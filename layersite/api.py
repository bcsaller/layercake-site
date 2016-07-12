from aiohttp import web
import base64
from functools import wraps, lru_cache
from pathlib import Path
from urllib.parse import urlparse

from bson.json_util import dumps
from strict_rfc3339 import now_to_rfc3339_utcoffset
import aiohttp_jinja2
import yaml

from . import auth
from . import document


def dump(obj):
    return dumps(obj, indent=2)


def permission(perm):
    def decorator(f):
        @wraps(f)
        def wrapped(f):
            p = f.__annotations__.setdefault('permissions', set())
            p.add(perm)
        return wrapped
    return decorator


class RESTBase:
    headers = {'Content-Type': 'application/json', }

    @classmethod
    def from_request(cls, request):
        i = cls()
        i.request = request
        return i

    def default_route(self, base_url="api", obj=None):
        url =  "/{}/{}/{}/".format(base_url, self.version, self.endpoint)
        if obj:
            if isinstance(obj, document.Document):
                oid = obj.id
            else:
                oid = obj
            url = "{}{}/".format(url, oid)
        return url

    def get_current_user(self):
        return auth.get_current_user(self.request)

    @property
    def app(self):
        return self.request.app

    @property
    def db(self):
        return self.app['db']

    @property
    def metrics(self):
        return getattr(self.app['db'], "metrics")

    async def verify_write_permissions(self, document, user=None):
        return await self.verify_permissions(required_perms=("owner"),
                                             document=document, user=user)

    async def verify_permissions(self, method=None, user=None,
                                 required_perms=None, document=None):
        if required_perms is None and method:
            required_perms = method.__annotations__.get('permissions', set())

        if not required_perms:
            return True

        if user is None:
            user = self.get_current_user()
        if user['login'] in self.request.app['admin_users']:
            return True

        if document is not None:
            # TODO: assign a list of roles to a user
            # TODO: resolve roles to a perm set relative to an obj
            # TODO: apply
            owners = document.get("owner", [])
            if user["login"] in owners:
                return True
        return False

    async def __call__(self, request):
        """Main Resource Entry point

        Handles user auth
        Method level ACL
        """
        mn = request.method.lower()
        if hasattr(self, mn):
            ins = self.from_request(request)
            m = getattr(ins, request.method.lower(), None)
        if not m:
            raise web.HTTPMethodNotAllowed(request)
        # perm checks
        await self.verify_permissions()
        return await m(**dict(request.match_info))

    async def add_metric(self, data):
        data['timestamp'] = now_to_rfc3339_utcoffset()
        peername = self.request.transport.get_extra_info('peername')
        if peername is not None:
                host, port = peername
                data['remote_address'] = host
        data["username"] = self.get_current_user()["login"]
        obj = document.Metric()
        obj.update(data)
        await obj.save(self.db, w=0)


class RESTResource(RESTBase):
    @lru_cache()
    async def get(self, uid):
        uid = uid.rstrip("/")
        result = await self.factory.find(self.db, id=uid)
        return web.Response(text=dump(result[0] if result else []), headers=self.headers)

    async def post(self, uid):
        body = await self.request.json()
        uid = uid.rstrip("/")
        document = await self.factory.load(self.db, uid)
        if not (await self.verify_write_permissions(document)):
            raise web.HTTPUnauthorized(reason="Github user not authorized")
        document.update(body)
        await document.save(self.db, user=self.get_current_user()['login'])
        await self.add_metric({"action": "update",
                               "item": document['id']})
        # clear the get cache
        self.get.cache_clear()
        return web.Response(status=200)

    async def delete(self, uid):
        uid = uid.rstrip("/")
        document = await self.factory.load(self.db, uid)
        if not (await self.verify_write_permissions(document)):
            raise web.HTTPUnauthorized(reason="Github user not authorized")
        if document:
            await  document.remove(self.db)
            await self.add_metric({"action": "delete",
                                   "item": document['id']})
        return web.HTTPFound("/")

    async def editor_for(self, request):
        klass = self.factory
        schema = self.factory.schema
        db = request.app['db']
        oid = request.match_info.get("oid", None)
        if oid == "+":
            # "+" is out token for "add new"
            obj = klass()
        else:
            obj = await klass.load(db, oid)

        return aiohttp_jinja2.render_template(
                "editor.html",
                request,
                dict(
                    schema=schema,
                    entity=obj,
                    endpoint=self.default_route(obj=oid),
                    kind=obj.kind,
                    user=auth.get_current_user(request),
                    ))


class RESTCollection(RESTBase):
    def parse_search_query(self):
        result = {}
        q = self.request.GET.getall("q", [])
        for query in q:
            if ":" in query:
                k, v = query.split(":", 1)
                result[k] = v
            else:
                if isinstance(q, list):
                    q = " ".join(q)
                result["$text"] = {"$search": q}
        return result

    async def get(self):
        q = self.parse_search_query()
        response = []
        for iface in (await self.factory.find(self.db, q)):
            response.append(iface)
        return web.Response(text=dump(response), headers=self.headers)


class SchemaAPI:
    def __init__(self, schema):
        self.schema = schema

    async def get(self, request):
        return web.Response(text=dump(self.schema),
                            headers={'Content-Type': 'application/json'})


class LayersAPI(RESTCollection):
    version = "v2"
    factory = document.Layer
    endpoint = "layers"

    async def get(self):
        q = self.parse_search_query()
        response = []
        seen = set()
        for iface in (await self.factory.find(self.db, q)):
            seen.add(iface.id)
            response.append(iface)

        # we always do a fts of the layer, when repotext is set
        # we include its text index as well
        repotext = self.request.GET.get('repotext', False)
        if repotext:
            # Fall back to a full text search
            matched_repos = []
            for repo in (await document.Repo.find(self.db, q)):
                if repo.id not in seen:
                    matched_repos.append(repo.id)
            for did in matched_repos:
                iface = await self.factory.find(self.db,
                                                **{self.factory.pk: did})
                response.append(iface[0])
        return web.Response(text=dump(response), headers=self.headers)


class LayerAPI(RESTResource):
    version = "v2"
    factory = document.Layer
    repo_factory = document.Repo
    endpoint = "layers"

    async def post(self, uid):
        result = await super(LayerAPI, self).post(uid)
        # and pull any repo updates

        uid = uid.rstrip("/")
        doc = await self.factory.find(self.db, {'id': uid})
        doc = doc[0]

        repo = RepoAPI()
        self.request.app.loop.create_task(repo.ingest_repo(self.app, doc))
        return result


class RepoAPI(RESTResource):
    version = "v2"
    factory = document.Repo
    endpoint = "repos"

    def decode_content_from_response(self, response):
        content = base64.b64decode(response['content'])
        content = content.decode("utf-8")
        return content

    async def get_readme(self, repo_url, ghclient):
        url = urlparse(repo_url)
        rpath = url.path
        response = await ghclient.get("/repos{}/readme".format(rpath))
        return self.decode_content_from_response(response)

    async def get_content(self, url, ghclient):
        response = await ghclient.get(url)
        content = self.decode_content_from_response(response)
        response['content'] = content
        return response

    async def walk_content(self, repo_url, ghclient):
        url = urlparse(repo_url)
        rpath = url.path
        repo_dir = await ghclient.get("/repos{}/contents".format(rpath))
        rules = []
        schemas = []
        for item in repo_dir:
            if item['type'] != "file":
                continue
            path = Path(item['path'])
            if path.match("*.rules"):
                rules.append(
                        await self.get_content(
                            item['url'], ghclient))
            elif path.match("*.schema"):
                schemas.append(
                    await self.get_content(
                        item['url'], ghclient))

        for rule in rules:
            rule['content'] = yaml.load(rule['content'])
        for schema in schemas:
            schema['content'] = yaml.load(schema['content'])
        return rules, schemas

    async def ingest_repo(self, app, layer_doc):
        oid = layer_doc['id']
        repo_url = layer_doc['repo']
        gh = auth.get_github_client()
        if not gh:
            raise ValueError("Unable to obtains github client")
        readme = await self.get_readme(repo_url, gh)
        rules, schemas = await self.walk_content(repo_url, gh)
        obj = self.factory()
        obj.update({"id": oid,
                    "readme": readme,
                    "rules": rules,
                    "schema": schemas})
        await obj.save(app['db'])
        gh.close()


class MetricsAPI(RESTCollection):
    version = "v2"
    factory = document.Metric
    endpoint = "metrics"

    async def get(self):
        if not (await self.verify_permissions(document, group="admin")):
            raise web.HTTPUnauthorized(reason="Github user not authorized")
        return await super(MetricsAPI, self).get()


async def register_apis(app, base_uri="api"):
    router = app.router
    db = app['db']
    # Metrics
    metrics = MetricsAPI()
    metrics_ep = router.add_resource("/{}/{}/{}/".format(
                    base_uri,
                    metrics.version,
                    metrics.endpoint))

    metrics_ep.add_route("*", metrics)

    for collection, item in [(LayersAPI(), LayerAPI())]:
        # collection
        await collection.factory.prepare(db)

        apiep = "/{}/{}/{}/".format(
                base_uri,
                collection.version,
                collection.endpoint)
        col = router.add_resource(apiep)
        col.add_route("*", collection)
        # items
        itemep = "%s{uid:[\w_-]+/?}" % (apiep)
        repos = router.add_resource("/%s/%s/repos/{uid}/" % (
                    base_uri, collection.version))
        repoapi = RepoAPI()
        repos.add_route("*", repoapi)
        await repoapi.factory.prepare(db)

        items = router.add_resource(itemep)
        items.add_route("*", item)

        # Editor support
        router.add_route("GET", "/editor/%s/{oid}/" % (item.endpoint),
                         item.editor_for)

        # schema
        router.add_route("*", "/{}/{}/schema/{}/".format(
            base_uri,
            item.version,
            collection.factory.kind),
            SchemaAPI(collection.factory.schema).get)
