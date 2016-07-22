import argparse
import json
import logging

from aiohttp import web
import aiohttp_jinja2
import asyncio
import jinja2

from motor import motor_asyncio as motor


from . import model
from . import views


log = logging.getLogger("layersite")


async def init(options, loop):
    mclient = motor.AsyncIOMotorClient(options.mongo_uri)
    db = getattr(mclient, options.mongo_db)
    app = web.Application(loop=loop)
    app.update(dict(options=options,
                    db=db))
    loader = jinja2.PackageLoader("layersite", "templates")
    env = aiohttp_jinja2.setup(app, loader=loader)
    env.filters['jsonify'] = json.dumps
    views.setup_routes(app, options)
    await model.register_apis(app)
    return app


def configure_logging(level):
    logging.basicConfig(level=level,
                        format="%(name)s:%(lineno)d:%(funcName)s: %(message)s")
    logging.getLogger("asyncio").setLevel(logging.WARN)


def setup():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--mongo-db", default="layers")

    parser.add_argument("-c", "--credentials", default="credentials.yaml")
    parser.add_argument("-l", "--log-level", default=logging.INFO)

    options = parser.parse_args()
    return options


def main():
    options = setup()
    configure_logging(options.log_level)
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    app = loop.run_until_complete(init(options, loop))
    try:
        web.run_app(app)
    finally:
        loop.close()


if __name__ == '__main__':
    main()
