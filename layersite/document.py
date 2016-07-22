from bson.json_util import loads, dumps
import datetime
import logging
import pkg_resources


import jsonschema
import motor
import yaml

log = logging.getLogger(__name__)


def loader(filename):
    fn = pkg_resources.resource_filename(__name__, filename)
    return yaml.load(open(fn).read())


class DocumentBase(dict):
    def __init__(self, data=None):
        self.update(self.empty())
        self.update(data)

    def __str__(self):
        return self.bson()

    @property
    def id(self):
        return self.get(self.pk)

    @property
    def kind(self):
        return self.schema.get("name", self.__class__.__name__).lower()

    @classmethod
    def get_kind(cls):
        return cls.schema.get("name", cls.__name__).lower()

    def bson(self):
        return dumps(self)

    def validate(self):
        jsonschema.validate(self, self.schema,
                            format_checker=jsonschema.FormatChecker())

    def __setitem__(self, key, value):
        self[key] = value
        self.validate()

    def update(self, data=None, **kwargs):
        if data:
            if isinstance(data, str):
                data = loads(data)
            super(DocumentBase, self).update(data)
        super(DocumentBase, self).update(kwargs)

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
    def properties(cls):
        return cls.schema['properties'].keys()

    @classmethod
    def get_property(cls, name):
        return cls.schema['properties'][name]


class Document(DocumentBase):
    @classmethod
    async def prepare(cls, db):
        await cls.create_text_index(db)

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
    async def load(cls, db, key, update=True):
        db = getattr(db, cls.collection)
        document = await db.find_one({cls.pk: key})
        if document:
            document = cls(document)
        else:
            document = cls({cls.pk: key})
        return document

    @classmethod
    async def find(cls, db, query=None, sort=True, **kwargs):
        db = getattr(db, cls.collection)
        if query is None:
            query = {}
            for k, v in kwargs.items():
                query[k] = cls.query_from_schema(k, v)
            query = {"$query": query}
        result = []

        # query omitting the internal fields
        cursor = db.find(query, {"_id": 0,
                                 "lastmodified": 0})
        if sort and cls.default_sort:
            cursor.sort(cls.default_sort, 1)

        async for doc in cursor:
            result.append(cls(doc))
        return result

    async def save(self, db, upsert=True, user=None, **kw):
        db = getattr(db, self.collection)
        # XXX: user should be Org in an github I think
        self.validate()
        dict.__setitem__(self, 'lastmodified',
                         datetime.datetime.utcnow())
        owners = self.get("owner", [])
        if user and not owners:
            dict.__setitem__(self, 'owner', [user])
        if not self.pk:
            await db.insert(dict(self), **kw)
        else:
            await db.update({self.pk: self.id}, {'$set': self},
                            upsert=upsert, **kw)

    async def remove(self, db):
        db = getattr(db, self.collection)
        await db.remove({self.pk: self.id})

    @classmethod
    def text_fields(cls):
        return [f for f in cls.properties() if
                cls.get_property(f)['type'] == "string"]

    @classmethod
    async def create_text_index(cls, db, drop=False):
        db = getattr(db, cls.collection)
        if drop:
            await db.drop_index("fts")

        fields = cls.text_fields()
        spec = []
        for field in fields:
            spec.append((field, motor.pymongo.TEXT))
        await db.ensure_index(spec, name="fts")
