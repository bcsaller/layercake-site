from aiohttp import web

from bson.json_util import dumps
from strict_rfc3339 import now_to_rfc3339_utcoffset
import aiohttp_jinja2

from . import auth
from . import document


def dump(obj):
    return dumps(obj, indent=2)


# One built in Document kind we manage
class Metric(document.Document):
    collection = "metrics"
    schema = document.loader("metrics.schema")
    pk = None
    default_sort = None


class RESTBase:
    headers = {'Content-Type': 'application/json', }

    @classmethod
    def from_request(cls, request):
        i = cls()
        i.request = request
        return i

    def default_route(self, base_url="api", obj=None):
        url = "/{}/{}/{}/".format(base_url, self.version, self.endpoint)
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
        if user and user['login'] in self.request.app['admin_users']:
            return True

        if document is not None:
            # TODO: assign a list of roles to a user
            # TODO: resolve roles to a perm set relative to an obj
            # TODO: apply
            owners = document.get("owner", [])
            if user and user["login"] in owners:
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
        if hasattr(self, "factory") and "kind" not in data:
            data['kind'] = self.factory.get_kind()
        obj = Metric()
        obj.update(data)
        await obj.save(self.db, w=0)


class RESTResource(RESTBase):
    async def get(self, uid):
        uid = uid.rstrip("/")
        result = await self.factory.find(self.db, id=uid)
        return web.Response(text=dump(result[0] if result else []),
                            headers=self.headers)

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
        return web.Response(text="OK")

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

    async def post(self):
        body = await self.request.json()
        if not isinstance(body, list):
            body = [body]
        # validate the user can modify each record before changing any
        documents = []
        for item in body:
            oid = item['id']
            document = await self.factory.load(self.db, oid)
            if not (await self.verify_write_permissions(document)):
                raise web.HTTPUnauthorized(reason="Github user not authorized")
            document.update(item)
            documents.append(document)

        for document in documents:
            await document.save(self.db)
            await self.add_metric({"action": "update",
                                   "item": document['id']})
        return web.Response(status=200)
