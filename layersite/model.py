import asyncio
import base64
import json
import logging
import yaml

from aiohttp import web
from pathlib import Path
from urllib.parse import urlparse

from .api import (RESTCollection, RESTResource, Metric, dump)
from . import auth
from .document import Document, loader

log = logging.getLogger("layersite")


# Document models
class Layer(Document):
    collection = "layers"
    schema = loader("layer.schema")
    pk = "id"
    default_sort = "name"


class Repo(Document):
    collection = "repos"
    schema = loader("repo.schema")
    pk = "id"
    default_sort = "id"


class SchemaAPI:
    def __init__(self, schema):
        self.schema = schema

    async def get(self, request):
        return web.Response(text=dump(self.schema),
                            headers={'Content-Type': 'application/json'})


class LayersAPI(RESTCollection):
    version = "v2"
    factory = Layer
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
            for repo in (await Repo.find(self.db, q)):
                if repo.id not in seen:
                    matched_repos.append(repo.id)
            for did in matched_repos:
                iface = await self.factory.find(self.db,
                                                **{self.factory.pk: did})
                response.append(iface[0])
        return web.Response(text=dump(response), headers=self.headers)


class LayerAPI(RESTResource):
    version = "v2"
    factory = Layer
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
    factory = Repo
    endpoint = "repos"
    WATCH_INTERVAL = 60 * 60 * 12

    async def bootstrap(self, app, db):
        await (super(RepoAPI, self).bootstrap(app, db))
        # and spawn a "cronjob" for inspecting repos
        self.watcher = app.loop.create_task(self.watch_repos(app, db))

    async def watch_repos(self, app, db):
        while True:
            # walk the collection of layers (yes, there is an encapsulation
            # break here) and update their repos when needed
            for layer in await Layer.find(db):
                await self.ingest_repo(app, layer)
            await asyncio.sleep(self.WATCH_INTERVAL)

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
        log.info("Ingesting %s for %s", repo_url, oid)
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
    factory = Metric
    endpoint = "metrics"

    async def get(self):
        user = self.get_current_user()
        if not user or user['login'] not in self.request.app['admin_users']:
            return web.Response(text="[]", headers=self.headers)
        return await super(MetricsAPI, self).get()


class MetaAPI(RESTCollection):
    async def get(self):
        apis = {}
        return web.Response(body=json.dumps(apis),
                            headers=self.headers)


async def register_api(app, api, base_uri):
    router = app.router
    db = app['db']
    await api.bootstrap(app, db)

    apiep = "/{}/{}/{}/".format(
            base_uri,
            api.version,
            api.endpoint)
    if isinstance(api, RESTCollection):
        route = router.add_resource(apiep)
    else:
        itemep = "%s{uid:[\w_-]+/?}" % (apiep)
        route = router.add_resource(itemep)
        # schema explicitly added for each item type
        router.add_route("*", "/{}/{}/schema/{}/".format(
            base_uri,
            api.version,
            api.factory.kind),
            SchemaAPI(api.factory.schema).get)
        # Editor support per item type
        router.add_route("GET", "/editor/%s/{oid}/" % (api.endpoint),
                api.editor_for)
    route.add_route("*", api)


async def register_apis(app, base_uri="api"):
    for api in [MetricsAPI(), LayersAPI(), LayerAPI(), RepoAPI()]:
        await register_api(app, api, base_uri)
