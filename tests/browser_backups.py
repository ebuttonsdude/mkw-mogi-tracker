"""Optional Chromium session-backup acceptance; run only in a separate PID namespace."""
from pathlib import Path
import tempfile
import threading

from mkw_tracker.server import Application, make_server
from tests.test_transfers import populate


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for mode in ('mogi', 'ffa', 'ww'):
            store = populate(root, mode)
            store.close()
        app = Application(root / 'tracker.sqlite3', root)
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_address[1]}')
                page.get_by_role('tab', name='Settings', exact=True).click()
                page.locator('#backup-section > summary').click()
                page.get_by_role('button', name='Choose sessions', exact=True).click()
                expect(page.locator('#backup-sessions input')).to_have_count(3)
                page.get_by_role('button', name='Select all listed sessions').click()
                with page.expect_download() as download:
                    page.get_by_role('button', name='Download selected sessions').click()
                archive = root / 'backup.zip'
                download.value.save_as(archive)
                page.locator('#backup-file').set_input_files(archive)
                expect(page.locator('#backup-confirm')).to_be_disabled()
                page.get_by_role('button', name='Preview import', exact=True).click()
                expect(page.locator('#backup-summary li')).to_have_count(3)
                expect(page.locator('#backup-confirm')).to_be_enabled()
                assert len(app.backups.catalog()['sessions']) == 3
                page.get_by_role('button', name='Confirm import as new sessions').click()
                expect(page.locator('#backup-status')).to_contain_text('Imported 3 sessions')
                assert len(app.backups.catalog()['sessions']) == 6
                expect(page.locator('#backup-confirm')).to_be_disabled()
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert errors == [], errors
                browser.close()
                print('PASS: selected three-mode ZIP download, preview without writes, explicit additive import, consumed token, mobile layout; no browser errors.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
