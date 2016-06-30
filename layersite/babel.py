import asyncio
import logging
import os
import subprocess
from io import BytesIO
from pathlib import Path

import jinja2
from aiohttp import web


log = logging.getLogger(__name__)


class Babel:
    """Manage a single process spawning Babel for React JSX transformation"""
    # requires:: npm install babel-cli babel-preset-es2015 babel-preset-react
    # to bootstrap babel-cli
    presets = ["es2015", "react"]

    async def __call__(self, sourcefile=None, stream=None, loop=None):
        if not (sourcefile or stream):
            raise ValueError("Must supply either a sourcefile or a stream to transform")

        if sourcefile:
            sourcefile = Path(sourcefile)
            if not sourcefile.exists():
                raise FileNotFoundError(sourcefile)

        if not loop:
            loop = asyncio.get_event_loop()
        try:
            cmd = ["babel", "--presets", ",".join(self.presets)]
            if sourcefile:
                cmd.append(str(sourcefile))

            p = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE if stream else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=dict(PATH=os.environ.get('PATH')),
                    loop=loop,
                    )
            stdout, stderr = await p.communicate(
                    stream.read() if stream else None)
            if stdout:
                output = stdout.decode('utf-8')
            if p.returncode is 0:
                return output
            else:
                logging.info("%s failed with %s", cmd, output)
        except FileNotFoundError:
            log.warn("babel or extensions: not on path")


class BabelTransformer(web.View):
    CONTENT = 0
    KIND = 1
    MTIME = 2

    MEMORY = 0
    FILE = 1

    def __init__(self, base_dir, autoupdate=True):
        self.cache = {}  # filename -> (contents, type=memory | file)
        self.base_dir = Path(base_dir)
        self.autoupdate = autoupdate
        self.b = Babel()

    async def get(self, request):
        filename = request.match_info['filename']
        source = self.base_dir / filename
        lm = source.lstat().st_mtime
        existing = self.cache.get(filename)
        if not existing or existing[self.MTIME] < lm:
            if not source.exists():
                raise FileNotFoundError(source)
            result = await self.b(source)
            self.cache[filename] = (result, self.MEMORY, lm)

        existing = self.cache.get(filename)
        if existing[self.KIND] == self.MEMORY:
            output = existing[self.CONTENT]
        else:
            output = existing[self.CONTENT].read_text()
        return web.Response(text=output)
