"""Optional Chromium acceptance for endpoint configuration; outbound transport mocked."""

from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch

from mkw_tracker.external_vision import ExternalVisionReader
from mkw_tracker.server import Application, make_server


def main():
    from playwright.sync_api import expect, sync_playwright

    requests = []
    slow = threading.Event()

    def request(reader, png, deadline):
        requests.append(png)
        while slow.is_set():
            reader._check(deadline)
            time.sleep(.01)
        return {'name': 'V12ABCD', 'confidence': 0.0}

    with tempfile.TemporaryDirectory() as temporary, \
            patch.object(ExternalVisionReader, '_request', request), \
            patch('mkw_tracker.external_vision.secrets.token_hex', return_value='12abcd'):
        app = Application(Path(temporary) / 'scores.sqlite', temporary)
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.wait_for_function('() => state !== null && !refreshing')
                # Idle endpoint and backup controls must not freeze background standings.
                expect(page.locator('#backup-confirm')).to_be_disabled()
                expect(page.locator('#backup-export')).to_be_disabled()
                assert page.evaluate('canRedraw()')
                with app.lock:
                    app.store.add_player('Background initial')
                page.evaluate('refresh(true)')
                expect(page.locator('#players')).to_contain_text('Background initial')
                page.locator('#auto-refresh').uncheck()
                page.get_by_role('tab', name='Settings', exact=True).click()
                page.locator('#experimental-section > summary').click()
                form = page.locator('#external-settings')
                expect(page.locator('#external-test')).to_be_disabled()
                form.locator('[name=url]').fill('https://vision.example/v1')
                form.locator('[name=model]').fill('vision')
                form.locator('[name=api_key]').fill('private-browser-key')
                form.get_by_role('button', name='Save external endpoint').click()
                expect(page.locator('#external-status')).to_contain_text('Configuration saved')
                expect(form.locator('[name=url]')).to_have_value('https://vision.example/v1/chat/completions')
                expect(form.locator('[name=api_key]')).to_have_value('')
                expect(page.locator('#external-key-status')).to_contain_text('Saved privately')
                page.locator('#external-test').click()
                expect(page.locator('#external-status')).to_contain_text('Acknowledge')
                assert not requests
                form.locator('[name=acknowledged]').check()
                form.get_by_role('button', name='Save external endpoint').click()
                expect(page.locator('#external-test')).to_be_enabled()
                page.locator('#external-test').click()
                expect(page.locator('#external-status')).to_contain_text('Synthetic name read correctly')
                assert len(requests) == 1 and requests[0].startswith(b'\x89PNG')
                assert 'private-browser-key' not in page.locator('body').inner_text()
                assert 'private-browser-key' not in page.evaluate('JSON.stringify(state)')
                form.locator('[name=url]').fill('https://second.example/v1')
                expect(form.locator('[name=acknowledged]')).not_to_be_checked()
                expect(page.locator('#external-test')).to_be_disabled()
                form.get_by_role('button', name='Save external endpoint').click()
                expect(page.locator('#external-key-status')).to_have_text('No API key saved')
                form.locator('[name=acknowledged]').check()
                form.locator('[name=api_key]').fill('another-private-key')
                form.get_by_role('button', name='Save external endpoint').click()
                expect(page.locator('#external-test')).to_be_enabled()
                slow.set()
                page.locator('#external-test').click()
                expect(page.locator('#external-test-cancel')).to_be_enabled()
                page.locator('#external-test-cancel').click()
                expect(page.locator('#external-test')).to_be_enabled()
                expect(page.locator('#external-status')).to_contain_text('cancelled')
                slow.clear()
                page.locator('#external-forget').click()
                expect(page.locator('#external-key-status')).to_have_text('No API key saved')
                expect(form.locator('[name=acknowledged]')).not_to_be_checked()
                expect(form.locator('[name=api_key]')).to_have_value('')
                # Delay real API responses, not server mutations, to exercise late UI completions.
                page.evaluate("""() => {
                  const original = window.fetch;
                  window.fetch = async (...args) => {
                    const response = await original(...args);
                    const kind = args[0] === '/api/external-vision' && args[1]?.method === 'PATCH' ? 'Patch' :
                      args[0] === '/api/external-vision/test' ? 'Test' : null;
                    if (kind && window['hold' + kind]) {
                      window['ready' + kind] = true;
                      await new Promise(resolve => { window['release' + kind] = resolve; });
                    }
                    return response;
                  };
                }""")
                def hold(kind):
                    page.evaluate('(kind) => { window["hold" + kind] = true; window["ready" + kind] = false; }', kind)

                def release(kind):
                    page.evaluate('(kind) => { window["hold" + kind] = false; window["release" + kind](); }', kind)

                hold('Patch')
                form.locator('[name=model]').fill('delayed-model')
                form.get_by_role('button', name='Save external endpoint').click()
                page.wait_for_function('() => window.readyPatch')
                form.locator('[name=url]').fill('https://new-draft.example/v1')
                form.locator('[name=model]').fill('newer-draft-model')
                release('Patch')
                expect(form.get_by_role('button', name='Save external endpoint')).to_be_enabled()
                expect(form.locator('[name=url]')).to_have_value('https://new-draft.example/v1')
                expect(form.locator('[name=model]')).to_have_value('newer-draft-model')
                expect(page.locator('#external-status')).to_contain_text('newer unsaved changes')
                expect(page.locator('#external-test')).to_be_disabled()
                form.get_by_role('button', name='Save external endpoint').click()
                expect(page.locator('#external-status')).to_contain_text('Configuration saved')

                hold('Patch')
                form.locator('[name=api_key]').fill('delayed-private-key')
                form.get_by_role('button', name='Save external endpoint').click()
                page.wait_for_function('() => window.readyPatch')
                page.locator('#external-forget').click()
                expect(page.locator('#external-status')).to_contain_text('API key forgotten')
                release('Patch')
                expect(form.get_by_role('button', name='Save external endpoint')).to_be_enabled()
                expect(page.locator('#external-status')).to_contain_text('API key forgotten')
                expect(page.locator('#external-key-status')).to_have_text('No API key saved')
                expect(form.locator('[name=api_key]')).to_have_value('')
                assert not page.evaluate('state.external_vision.api_key_saved')

                form.locator('[name=acknowledged]').check()
                form.get_by_role('button', name='Save external endpoint').click()
                expect(page.locator('#external-status')).to_contain_text('Configuration saved')
                hold('Test')
                page.locator('#external-test').click()
                page.wait_for_function('() => window.readyTest')
                page.locator('#external-test-cancel').click()
                expect(page.locator('#external-status')).to_contain_text('Test cancelled')
                release('Test')
                expect(page.locator('#external-test')).to_be_enabled()
                expect(page.locator('#external-status')).to_contain_text('Test cancelled')
                hold('Test')
                page.locator('#external-test').click()
                page.wait_for_function('() => window.readyTest')
                form.locator('[name=url]').fill('https://edited-during-test.example/v1')
                release('Test')
                expect(page.locator('#external-test-cancel')).to_be_disabled()
                expect(page.locator('#external-status')).to_contain_text('Unsaved changes')
                expect(form.locator('[name=url]')).to_have_value('https://edited-during-test.example/v1')

                # A configured endpoint and idle Cancel must still permit real background updates.
                page.get_by_role('tab', name='Standings', exact=True).click()
                page.locator('#auto-refresh').check()
                page.locator('#auto-refresh').blur()
                assert page.evaluate('canRedraw()')
                with app.lock:
                    app.store.add_player('Background configured')
                page.evaluate('refresh(true)')
                expect(page.locator('#players')).to_contain_text('Background configured')
                page.locator('#auto-refresh').uncheck()
                page.get_by_role('tab', name='Capture & review', exact=True).click()
                page.locator('#camera-settings [name=name_engine]').select_option('external-vision')
                page.evaluate('refresh()')
                expect(page.locator('#camera-settings [name=name_engine]')).to_have_value('external-vision')
                page.get_by_role('tab', name='Videos', exact=True).click()
                page.locator('#import-video [name=name_engine]').select_option('external-vision')
                page.evaluate('refresh()')
                expect(page.locator('#import-video [name=name_engine]')).to_have_value('external-vision')
                assert not errors, errors
                browser.close()
            print('External endpoint Chromium acceptance passed (mocked transport).')
        finally:
            slow.clear()
            server.shutdown()
            server.server_close()
            thread.join()
            app.close()


if __name__ == '__main__':
    main()
