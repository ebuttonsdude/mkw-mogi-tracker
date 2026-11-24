from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.server import Application


class DiscordIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        factory = self.enterContext(patch('mkw_tracker.server.DiscordPresence'))
        self.rpc = factory.return_value
        self.rpc.status.return_value = 'Test status'
        self.app = Application(self.root / 'test.sqlite', self.root)
        self.addCleanup(self.app.close)

    def test_default_and_validation(self):
        self.assertFalse(self.app.settings['discord_enabled'])
        self.assertEqual(self.app.settings['discord_application_id'], '1574667683088171169')
        self.rpc.publish.assert_not_called()
        for values in ({'discord_enabled': 1}, {'discord_enabled': True, 'discord_application_id': ''},
                       {'discord_application_id': 123}, {'discord_application_id': '1' * 21},
                       {'discord_application_id': '١' * 18}, {'discord_application_id': 'token'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.app.update_settings(values)
        self.app.update_settings({'discord_enabled': True})
        self.rpc.configure.assert_called_with(True, '1574667683088171169')
        self.assertEqual(self.rpc.publish.call_args.args[0]['assets']['large_text'], 'Conkdor')

    def test_persistence_disable_and_modes(self):
        self.app.update_settings({'discord_enabled': True, 'discord_application_id': '123456789012345678'})
        self.rpc.configure.assert_called_with(True, '123456789012345678')
        for mode, label in [('ffa', 'Mogi FFA'), ('ww', 'Worldwides'), ('mogi', 'Mogi Teams')]:
            self.app.switch_tracker_mode(mode, self.app.store.active_session()['id'])
            self.assertIn(label, self.rpc.publish.call_args.args[0]['details'])
        self.app.close()
        self.app = Application(self.root / 'test.sqlite', self.root)
        self.addCleanup(self.app.close)
        self.assertTrue(self.app.settings['discord_enabled'])
        self.app.update_settings({'discord_enabled': False})
        self.rpc.configure.assert_called_with(False, '123456789012345678')
        self.rpc.publish.reset_mock()
        self.app.snapshot()
        self.rpc.publish.assert_not_called()
        self.app.close()
        self.app = Application(self.root / 'test.sqlite', self.root)
        self.addCleanup(self.app.close)
        self.assertFalse(self.app.settings['discord_enabled'])

    def test_stale_highlight_and_status(self):
        self.app.settings['overlay']['highlight_team_id'] = 'other-session-team'
        self.app.update_settings({'discord_enabled': True, 'discord_application_id': '123456789012345678'})
        self.assertEqual(self.rpc.publish.call_args.args[0]['state'], 'No team highlighted')
        self.assertEqual(self.app.snapshot()['discord_status'], 'Test status')
