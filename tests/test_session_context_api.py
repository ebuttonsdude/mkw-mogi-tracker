"""Creation requests carry the session displayed when the draft was started."""

import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mkw_tracker.server import Application, make_server


class SessionContextAPITests(unittest.TestCase):
    def test_missing_stale_and_current_creation_context(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(':memory:', directory)
            server = make_server(app, port=0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                old = app.store.snapshot()['session']
                current = app.store.create_session('Other tab', old['id'])
                base = f'http://127.0.0.1:{server.server_address[1]}'
                for path, fields in (('/api/players', {'name': 'Player'}),
                                     ('/api/teams', {'name': 'Team'}),
                                     ('/api/races', {'course': 'Course'})):
                    before = app.store.snapshot()
                    for context in ({}, {'expected_session_id': None},
                                    {'expected_session_id': old['id']}):
                        request = Request(base + path, data=json.dumps(fields | context).encode(),
                                          headers={'Content-Type': 'application/json'}, method='POST')
                        with self.assertRaises(HTTPError) as error:
                            urlopen(request)
                        with error.exception as response:
                            self.assertEqual(response.code, 400)
                            self.assertIn('session changed', json.load(response)['error'])
                        self.assertEqual(app.store.snapshot(), before)
                    request = Request(base + path, data=json.dumps(fields | {'expected_session_id': current['id']}).encode(),
                                      headers={'Content-Type': 'application/json'}, method='POST')
                    with urlopen(request) as response:
                        self.assertEqual(json.load(response)['session_id'], current['id'])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                app.close()
