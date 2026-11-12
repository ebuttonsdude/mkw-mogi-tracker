"""Optional Chromium check for YOLO consent, ownership and provenance."""
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import numpy as np
from playwright.sync_api import expect, sync_playwright

from mkw_tracker.server import Application, make_server
from tests.test_yolo import reader


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = Application(Path(directory) / 'db.sqlite3', directory)
        server = make_server(app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(app.cameras, 'enumerator', return_value=[]), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(base)
                session_id = app.store.active_session()['id']
                assert page.request.patch(base + '/api/settings', data={
                    'yolo_mode': True, 'expected_session_id': session_id}).status == 400
                assert page.request.patch(base + '/api/settings', data={
                    'yolo_mode': True, 'confirm_yolo': True, 'expected_session_id': session_id,
                    'expected_tracker_mode': 'ffa'}).status == 400
                assert not app.settings['yolo_mode']
                page.locator('#auto-refresh').uncheck()
                page.get_by_role('tab', name='Settings', exact=True).click()
                page.locator('#experimental-section > summary').click()
                expect(page.locator('#yolo-mode')).not_to_be_checked()
                expect(page.locator('#font-details > summary')).to_be_visible()
                page.locator('#yolo-mode').click()
                expect(page.locator('#yolo-warning-text')).to_have_text(
                    'This mode is extremely likely to make mistakes. You will likely need to edit race history, teams, and names manually.')
                expect(page.get_by_role('button', name='Confirm - yolo', exact=True)).to_be_visible()
                page.set_viewport_size({'width': 380, 'height': 800})
                expect(page.locator('#yolo-warning')).to_be_in_viewport()
                expect(page.get_by_role('button', name='Cancel', exact=True)).to_be_in_viewport()
                Path('.local').mkdir(exist_ok=True)
                page.screenshot(path='.local/browser-yolo-warning.png')
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.get_by_role('button', name='Cancel', exact=True).click()
                expect(page.locator('#yolo-warning')).not_to_be_visible()
                expect(page.locator('#yolo-mode')).not_to_be_checked()
                assert app.settings['yolo_mode'] is False
                entry = page.evaluate('''() => {
                    const entry = {name: 'not-detected-fixme', player_id: '', manualIdentity: false};
                    matchIdentity(entry, [{id: 'existing-placeholder', name: 'not-detected-fixme'}]);
                    return entry;
                }''')
                assert entry['player_id'] == ''
                page.locator('#yolo-mode').click()
                page.keyboard.press('Escape')
                expect(page.locator('#yolo-mode')).not_to_be_checked()
                assert app.settings['yolo_mode'] is False
                # Modal consent owns its original session, even if another tab changes it.
                page.locator('#yolo-mode').click()
                with app.lock:
                    app.store.create_session('Other tab', app.store.active_session()['id'])
                page.get_by_role('button', name='Confirm - yolo', exact=True).click()
                expect(page.locator('#message')).to_contain_text('Session changed')
                expect(page.locator('#yolo-mode')).not_to_be_checked()
                page.locator('#yolo-mode').click()
                page.evaluate('''() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => url === '/api/settings'
                        ? new Promise(resolve => { window.releaseYolo = () => {
                            window.fetch = original; resolve(original(url, options));
                        }; }) : original(url, options);
                }''')
                page.get_by_role('button', name='Confirm - yolo', exact=True).click()
                expect(page.locator('#yolo-cancel')).to_be_disabled()
                page.keyboard.press('Escape')
                expect(page.locator('#yolo-warning')).to_be_visible()
                page.evaluate('window.releaseYolo()')
                expect(page.locator('#yolo-mode')).to_be_checked()
                expect(page.locator('#yolo-active')).to_be_visible()
                assert app.settings['yolo_mode'] is True
                app.capture.reader = reader()
                app.capture.observe(np.full((180, 320, 3), 100, dtype=np.uint8))
                page.evaluate('refresh()')
                page.get_by_role('tab', name='Race history', exact=True).click()
                page.locator('details.history-folder').evaluate_all('(nodes) => nodes.forEach(node => node.open = true)')
                expect(page.get_by_text('scored · YOLO auto-confirmed · revision 1', exact=True)).to_be_visible()
                race = app.store.snapshot()['races'][0]
                with app.lock:
                    app.store.correct_result(race['id'], list(reversed([p['player_id'] for p in race['placements']])))
                page.evaluate('refresh()')
                expect(page.get_by_text('scored · YOLO auto-confirmed · revision 2', exact=True)).to_be_visible()
                page.get_by_role('tab', name='Settings', exact=True).click()
                page.locator('#yolo-mode').click()
                expect(page.locator('#yolo-mode')).not_to_be_checked()
                expect(page.locator('#yolo-active')).not_to_be_visible()
                assert app.store.snapshot()['room']['points'] == 82
                # The setting persists, but WW never uses or displays it.
                page.locator('#yolo-mode').click()
                page.get_by_role('button', name='Confirm - yolo', exact=True).click()
                expect(page.locator('#yolo-mode')).to_be_checked()
                with app.lock:
                    app.switch_tracker_mode('ww', app.store.active_session()['id'])
                page.evaluate('refresh()')
                expect(page.locator('#yolo-mode')).not_to_be_visible()
                expect(page.locator('#yolo-active')).not_to_be_visible()
                assert not app.capture.yolo_policy[0]
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()
    print('YOLO Chromium consent, stale-session, history correction and WW checks passed')


if __name__ == '__main__':
    main()
