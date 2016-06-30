import base64
import json

import aiohttp
from aiohttp import web
from aioauth_client import GithubClient


class GithubAPI:
    def __init__(self, access_token):
        self.endpoint = "https://api.github.com"
        self.token = access_token
        self._timeout = 10
        self._headers = {
            'Authorization': 'token {}'.format(access_token),
            'User-Agent': 'aiohttp',
            }
        self._client = aiohttp.ClientSession()

    async def get(self, url):
        url = url[1:] if url.startswith("/") else url
        url = self.endpoint + "/" + url
        with aiohttp.Timeout(self._timeout):
            async with self._client.get(
                    url, headers=self._headers) as response:
                if response.status >= 400:
                    return response
                return await response.json()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._client.close()


class GithubAuth:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.__client_secret = client_secret
        self.client = GithubClient(client_id, client_secret)

    def auth_url(self):
        # user:email might be enough here
        return self.client.get_authorize_url(scope="user")

    async def get_token(self, code):
        return await self.client.get_access_token(code)

    def api(self, token):
        return GithubAPI(token)


async def auth_callback(request):
    github = request.app['auth']
    if 'code' not in request.GET:
        return web.HTTPFound(github.get_authorize_url(scope='user'))

    # Get access token
    code = request.GET['code']
    token, _ = await github.get_token(code)
    assert token

    # Resolve user info
    with github.api(token) as api:
        user = await api.get("/user")
        # Redirect with cookie
        resp = web.HTTPFound("/")
        authcookie = json.dumps(user, ensure_ascii=False).encode("utf-8")
        # The decode here is a workaround for py.http which seems broken to me
        resp.set_cookie("u", base64.b64encode(authcookie).decode("utf-8"))
    return resp


def setup_auth(app):
    app.router.add_route("GET", "/oauth_callback/github", auth_callback)


def get_current_user(request):
    user = request.cookies.get("u")
    if user:
        return json.loads(base64.b64decode(user).decode('utf-8'))
    return None
