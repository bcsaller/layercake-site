from bson.json_util import loads, dumps
import datetime
import logging
import pkg_resources

import jsonschema
import yaml

log = logging.getLogger(__name__)


def loader(filename):
    fn = pkg_resources.resource_filename(__name__, filename)
    return yaml.load(open(fn).read())


class Document(dict):
    def __init__(self, data=None):
        self.update(self.empty())
        self.update(data)

    def __str__(self):
        return self.bson()

    def bson(self):
        return dumps(self)

    def validate(self):
        jsonschema.validate(self, self.schema)

    def __setitem__(self, key, value):
        self[key] = value
        self.validate()

    def update(self, data=None, **kwargs):
        if data:
            if isinstance(data, str):
                data = loads(data)
            super(Document, self).update(data)
        super(Document, self).update(kwargs)

    @classmethod
    async def load(cls, db, key, update=True):
        document = await db.find_one({cls.pk: key})
        if document:
            document = cls(document)
        else:
            document = cls({cls.pk: key})
        return document

    @classmethod
    def query_from_schema(cls, key, value):
        spec = cls.schema['properties'].get(key)
        if not spec:
            return {"$eq": value}
        stype = spec.get("type", "string")
        if stype == "number":
            return {"$eq": int(value)}
        return {"$regex": value, "$options": "i"}

    @classmethod
    def empty(cls):
        """Return a dict populated with default (or empty)
        values from schema"""
        result = {}
        for k, v in cls.schema['properties'].items():
            value = v.get("default", None)
            if value is None:
                if v.get('type', 'string') == "string":
                    value = ""
            result[k] = value
        return result

    @classmethod
    async def find(cls, db, sort=True, **kwargs):
        query = {}
        for k, v in kwargs.items():
            query[k] = cls.query_from_schema(k, v)
        if not query:
            query = {cls.pk: {"$exists": True}}

        query = {"$query": query}
        if sort:
            query["$orderby"] = {cls.default_sort: 1}

        result = []
        async for doc in db.find(query):
            result.append(cls(doc))
        return result

    async def save(self, db, upsert=True, user=None):
        # XXX: user should be Org in an github I think
        pk = self[self.pk]
        self.validate()
        dict.__setitem__(self, 'lastmodified',
                         datetime.datetime.utcnow())
        owners = self.get("owner", [])
        if user and not owners:
            dict.__setitem__(self, 'owner', [user])
        await  db.update({self.pk: pk}, {'$set': self},
                         upsert=upsert)

    async def remove(self, db):
        await db.remove({self.pk: self[self.pk]})


class Layer(Document):
    collection = "layers"
    schema = loader("layer.schema")
    pk = "id"
    default_sort = "name"


class Repo(Document):
    collection = "repos"
    schema = loader("repo.schema")
    pk = "id"
