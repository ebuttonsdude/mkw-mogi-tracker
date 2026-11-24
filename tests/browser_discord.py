"""Optional Chromium Discord settings acceptance; mocked IPC only."""
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

from mkw_tracker.server import Application, make_server


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary, patch('mkw_tracker.server.DiscordPresence') as factory:
        factory.return_value.status.return_value = 'Mock Discord status'
        root = Path(temporary)
        app = Application(root / 'scores.sqlite', root)
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_address[1]}')
                page.locator('#tab-settings').click()
                page.locator('#discord-section > summary').click()
                expect(page.locator('#discord-enabled')).not_to_be_checked()
                page.get_by_text('Advanced: custom application', exact=True).click()
                page.locator('#discord-application-id').fill('123456789012345678')
                page.locator('#discord-enabled').check()
                page.locator('#refresh').click()
                page.evaluate('refresh()')
                expect(page.locator('#discord-application-id')).to_have_value('123456789012345678')
                expect(page.locator('#discord-enabled')).to_be_checked()
                page.locator('#discord-settings button').click()
                expect(page.locator('#message')).to_contain_text('Discord')
                assert app.settings['discord_enabled']
                for mode in ('ffa', 'ww', 'mogi'):
                    page.locator('#tracker-mode').select_option(mode)
                    expect(page.locator('#tracker-mode')).to_have_value(mode)
                    expect(page.locator('#discord-status')).to_have_text('Mock Discord status')
                page.locator('#discord-enabled').uncheck()
                page.locator('#discord-settings button').click()
                page.wait_for_function('() => state.settings.discord_enabled === false')
                page.reload()
                page.locator('#tab-settings').click()
                page.locator('#discord-section > summary').click()
                expect(page.locator('#discord-enabled')).not_to_be_checked()
                expect(page.locator('#discord-application-id')).to_have_value('123456789012345678')
                assert not errors, errors
                browser.close()
                print('PASS: Discord draft preservation, saves, modes and reload; mocked IPC only.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
