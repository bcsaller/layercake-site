from aiohttp import web

from bson.json_util import dumps
import aiohttp_jinja2

from . import auth
from . import document


def dump(obj):
    return dumps(obj, indent=2)


class RESTBase:
    @classmethod
    def from_request(cls, request):
        i = cls()
        i.request = request
        return i

    def get_current_user(self):
        return auth.get_current_user(self.request)

    @property
    def app(self):
        return self.request.app

    @property
    def db(self):
        return getattr(self.app['db'], self.collection)

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")

    def parse_search_query(self):
        result = {}
        q = self.request.GET.getall("q", [])
        for query in q:
            if ":" in query:
                k, v = query.split(":", 1)
                result[k] = v
            else:
                result[self.factory.pk] = query
        return result

    async def verify_write_permissions(self, document, user=None):
        if user is None:
            user = self.get_current_user()["login"]
        if user in self.request.app['admin_users']:
            return True

        import pdb; pdb.set_trace()
        owners = document.get("owner", [])
        if not owners:
            # XXX: backwards compat
            return True
        users = [o for o in owners if not o.startswith("~")]
        if user in users:
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
        return await m(**dict(request.match_info))


class RESTResource(RESTBase):
    async def get(self, uid):
        uid = uid.rstrip("/")
        result = await self.factory.find(self.db, {"id": uid})
        return web.Response(text=dump(result[0]))

    async def post(self, uid):
        body = await self.request.json()
        uid = uid.rstrip("/")
        document = await self.factory.load(self.db, uid)
        if not (await self.verify_write_permissions(document)):
            raise web.HTTPUnauthorized(reason="Github user not authorized")
        document.update(body)
        await document.save(self.db, user=self.get_current_user()['login'])
#        await self.add_metric({"kind": self.collection,
#                               "action": "update",
#                               "item": document['id']})
        return web.Response(status=200)

    async def delete(self, uid):
        uid = uid.rstrip("/")
        document = await self.factory.load(self.db, uid)
        if not (await self.verify_write_permissions(document)):
            raise web.HTTPUnauthorized(reason="Github user not authorized")
        if document:
            await  document.remove(self.db)
            #await self.add_metric({"kind": self.collection,
            #                       "action": "delete",
            #                       "item": document['id']})
        return web.HTTPFound("/")

    async def editor_for(self, request):
        klass = self.factory
        schema = self.factory.schema
        db = getattr(request.app['db'], self.factory.collection)
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
                    kind=self.collection,
                    user=auth.get_current_user(request),
                    ))


class RESTCollection(RESTBase):
    async def get(self):
        q = self.parse_search_query()
        response = []
        for iface in (await self.factory.find(self.db, **q)):
            response.append(iface)
        # Iteration complete
        return web.Response(text=dump(response))

    async def post(self):
        body = await self.request.json()
        if not isinstance(body, list):
            body = [body]
        # XXX: ACL
        #user = self.get_current_user()["username"]
        # validate the user can modify each record before changing any
        # XXX: this could be a race (vs out of band modification)
        # but this will be redone with proper database acls
        documents = []
        for item in body:
            id = item['id']
            document = await self.factory.load(self.db, id)
            #if not (await self.verify_write_permissions(document, user)):
            #    return web.Response(401, "User not authorized")
            document.update(item)
            documents.append(document)
        for document in documents:
            await document.save(self.db, user=user)
            # XXX: metrics


class SchemaAPI:
    def __init__(self, schema):
        self.schema = schema

    async def get(self, request):
        return web.Response(text=dump(self.schema))


class LayersAPI(RESTCollection):
    version = "v2"
    factory = document.Layer
    collection = "layers"


class LayerAPI(RESTResource):
    version = "v2"
    factory = document.Layer
    repo_factory = document.Repo
    collection = "layers"

    async def get_readme(self, request):
        # Readmes are stored in their own document and fetched out of band from
        # the other metadata, thus the specifiedecial handling
        uid = self.request.match_info['uid'].rstrip("/")
        result = await self.repo_factory.find(self.db, {"id": uid})
        return web.Response(text=dump(result[0]))


def register_api(app, collection, item, base_uri="api"):
    router = app.router
    # collection
    apiep = "/{}/{}/{}/".format(
            base_uri,
            collection.version,
            collection.collection)
    col = router.add_resource(apiep)
    col.add_route("*", collection)
    # items
    itemep = "%s{uid:[\w_-]+/?}" % (apiep)
    # r(router.add_route("GET", itemep + "/readme/"))

    items = router.add_resource(itemep)
    items.add_route("*", item)
    # Editor support
    router.add_route("GET", "/editor/%s/{oid}/" % (item.collection),
                     item.editor_for)

    # schema
    router.add_route("*", "/{}/{}/schema/{}/".format(
        base_uri,
        item.version,
        collection.factory.__name__.lower()),
        SchemaAPI(collection.factory.schema).get)
