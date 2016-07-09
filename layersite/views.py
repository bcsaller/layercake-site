import logging
import pkg_resources
from pathlib import Path

import aiohttp_jinja2
import yaml

from . import api
from . import auth
from .babel import BabelTransformer


log = logging.getLogger("layersite")


async def index(request):
    user = auth.get_current_user(request)
    return aiohttp_jinja2.render_template('index.html', request,
                                          {"user": user, })


def configure_access(app, cred_conf):
    p = Path(cred_conf)
    if not p.exists():
        raise ValueError(
            "Missing credentials config {}. Unable to authorize users".format(
                cred_conf))

    conf = yaml.load(p.open())
    # currently only github driver
    try:
        gh = conf['github']
        ac = auth.GithubAuth(
                client_id=gh["github_id"],
                client_secret=gh["github_secret"])
    except KeyError:
        logging.critical("Misconfigured Github Auth", exc_info=True)
        raise
    app['auth'] = ac
    app['admin_users'] = conf.get('site', {}).get("admin_users", [])
    auth.setup_auth(app)


def setup_routes(app, options):
    router = app.router
    router.add_route("GET", "/", index)
    transformer = BabelTransformer(pkg_resources.resource_filename(
                                   __name__, 'templates'))
    jsx = router.add_resource("/static/{filename:\w+\.jsx}")
    jsx.add_route("*", transformer.get)

    api.register_apis(app)

    # And static handling
    # XXX: cwd is not pacakgeable
    bower = Path.cwd() / "bower_components"
    resources = Path.cwd() / "static"
    router.add_static("/static", str(resources))
    router.add_static("/bower", str(bower))
    configure_access(app, options.credentials)
