import asyncio
import unittest

from utils import local_file, local_stream

from layersite import babel


class TestBabel(unittest.TestCase):
    def test_babel(self):
        b = babel.Babel()
        loop = asyncio.get_event_loop()
        output = loop.run_until_complete(b(local_file("test.jsx")))
        self.assertIn("createElement", output)

    def test_babel_stream(self):
        b = babel.Babel()
        loop = asyncio.get_event_loop()
        output = loop.run_until_complete(b(stream=local_stream("test.jsx")))
        self.assertIn("createElement", output)
