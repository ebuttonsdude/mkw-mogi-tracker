"""Custom theme acceptance using synthetic images and temporary application data."""
from io import BytesIO
from pathlib import Path
import tempfile
import threading

from mkw_tracker.server import Application, make_server


def main():
    from PIL import Image
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        app = Application(directory / 'scores.sqlite', directory)
        server = make_server(app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        source = BytesIO()
        Image.new('RGB', (64, 64), '#2060c0').save(source, format='PNG')
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('dialog', lambda dialog: dialog.accept())
                base = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(base)
                # Built-in theme controls are unavailable, not busy: polling must redraw.
                expect(page.locator('#tracker-mode')).to_have_value('mogi')
                expect(page.locator('#theme-update')).to_be_disabled()
                with app.lock:
                    players = [app.store.add_player(f'Player {index}')['id'] for index in range(12)]
                    app.store.create_race('background-race', 'Course')
                    app.store.submit_result('background-race', players)
                expect(page.locator('#players tbody tr')).to_have_count(12, timeout=10000)
                expect(page.locator('#room-points')).to_contain_text('82 / 984')
                with app.lock:
                    app.switch_tracker_mode('ww', app.store.active_session()['id'])
                expect(page.locator('body')).to_have_attribute('data-tracker-mode', 'ww', timeout=10000)
                with app.lock:
                    app.switch_tracker_mode('mogi', app.store.active_session()['id'])
                expect(page.locator('body')).to_have_attribute('data-tracker-mode', 'mogi', timeout=10000)
                expect(page.locator('#players tbody tr')).to_have_count(12)
                page.locator('#auto-refresh').uncheck()
                page.locator('#tab-settings').click()
                expect(page.locator('#character-theme option')).to_have_count(51)
                expect(page.locator('#theme-icon-note')).to_contain_text('No icon selected')
                expect(page.locator('#character-portrait')).to_be_hidden()
                page.locator('#custom-theme-editor summary').click()
                expect(page.locator('#theme-update')).to_be_disabled()
                page.locator('#custom-theme-name').fill('Ocean 日本')
                page.evaluate('''() => {
                    window.originalImageRead = FileReader.prototype.readAsDataURL;
                    FileReader.prototype.readAsDataURL = function(file) {
                        window.releaseThemeImage = () => window.originalImageRead.call(this, file);
                    };
                }''')
                page.locator('#custom-theme-image').set_input_files({'name': 'original.png', 'mimeType': 'image/png', 'buffer': source.getvalue()})
                expect(page.locator('#theme-save-copy')).to_be_disabled()
                assert not page.evaluate('document.activeElement.blur(); canRedraw()')
                page.locator('#custom-theme-name').fill('Ocean 日本')
                page.evaluate('FileReader.prototype.readAsDataURL = window.originalImageRead; window.releaseThemeImage()')
                expect(page.locator('#theme-editor-status')).to_contain_text('Image added')
                expect(page.locator('#custom-theme-icon')).to_be_visible()
                accent = page.locator('[data-token=accent]')
                old_accent = accent.input_value()
                page.locator('#theme-suggest').click()
                expect(page.locator('#theme-suggestions')).to_be_visible()
                expect(accent).to_have_value(old_accent)
                page.locator('#theme-apply-suggestions').click()
                assert accent.input_value() != old_accent
                accent.fill('#123456')
                page.locator('#theme-save-copy').click()
                expect(page.locator('#message')).to_contain_text('Custom theme saved')
                expect(page.locator('#character-portrait')).to_be_visible()
                custom_id = page.locator('#character-theme').input_value()
                assert custom_id != 'mario'
                expect(page.locator('#theme-update')).to_be_enabled()
                expect(page.locator('#theme-delete')).to_be_enabled()
                page.reload()
                expect(page.locator('#character-theme')).to_have_value(custom_id)
                expect(page.locator('#character-portrait')).to_be_visible()
                with app.lock:
                    theme = app.theme_library.resolve(custom_id)
                    assert theme['tokens']['accent'] == '#123456'
                    assert theme['name'] == 'Ocean 日本'
                    folder_id = app.store.active_session()['folder_id']
                image_response = page.request.get(base + f'/api/folders/{folder_id}/report.png')
                assert image_response.status == 200, image_response.text()
                assert image_response.body().startswith(b'\x89PNG')
                overlay = browser.new_page()
                overlay.goto(base + '/overlay')
                expect(overlay.locator('.overlay-character')).to_be_visible()
                expect(overlay.locator('.overlay-character')).to_have_attribute('alt', 'Ocean 日本 icon')
                page.locator('#custom-theme-editor summary').click()
                # Background state refresh must not replace an in-progress draft.
                page.locator('#custom-theme-name').fill('Ocean updated')
                page.locator('#refresh').click()
                expect(page.locator('#custom-theme-name')).to_have_value('Ocean updated')
                page.locator('#theme-update').click()
                expect(page.locator('#theme-editor-status')).to_contain_text('Theme and icon saved')
                expect(page.locator('#character-theme')).to_have_value(custom_id)
                with app.lock:
                    assert app.theme_library.resolve(custom_id)['name'] == 'Ocean updated'
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                # Explicit image removal retains the custom palette and is persisted.
                page.locator('#theme-remove-icon').click()
                page.locator('#theme-update').click()
                expect(page.locator('#character-portrait')).to_be_hidden()
                page.locator('#theme-delete').click()
                expect(page.locator('#theme-editor-status')).to_contain_text('Saved theme deleted')
                expect(page.locator('#character-theme')).to_have_value('mario')
                with app.lock:
                    assert not any(t['id'] == custom_id for t in app.theme_library.themes())
                # Delay a save while users submit twice and keep editing the draft.
                page.locator('#custom-theme-name').fill('Slow saved snapshot')
                page.evaluate("""() => {
                    window.originalThemeFetch = window.fetch;
                    window.themeSaveCalls = 0;
                    window.fetch = async (url, options) => {
                        if (url === '/api/themes' && options?.method === 'POST') {
                            window.themeSaveCalls++;
                            await new Promise(resolve => { window.releaseThemeSave = resolve; });
                        }
                        return window.originalThemeFetch(url, options);
                    };
                    const form = document.querySelector('#custom-theme-form');
                    form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
                    form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
                }""")
                expect(page.locator('#theme-save-copy')).to_be_disabled()
                assert not page.evaluate('document.activeElement.blur(); canRedraw()')
                expect(page.locator('#theme-update')).to_be_disabled()
                expect(page.locator('#theme-delete')).to_be_disabled()
                page.locator('#custom-theme-name').fill('Newer unsaved draft')
                page.locator('[data-token=accent]').fill('#abcdef')
                page.locator('#character-theme').select_option('luigi')
                page.evaluate('window.releaseThemeSave()')
                expect(page.locator('#theme-editor-status')).to_contain_text('newer draft')
                expect(page.locator('#custom-theme-name')).to_have_value('Newer unsaved draft')
                expect(page.locator('[data-token=accent]')).to_have_value('#abcdef')
                expect(page.locator('#character-theme')).to_have_value('luigi')
                assert page.evaluate('window.themeSaveCalls') == 1
                page.evaluate('window.fetch = window.originalThemeFetch')
                with app.lock:
                    slow = [theme for theme in app.theme_library.themes() if theme['name'] == 'Slow saved snapshot']
                    assert len(slow) == 1
                    assert app.settings['theme'] == 'mario'
                    app.theme_library.delete(slow[0]['id'])
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            app.close()
        restarted = Application(directory / 'scores.sqlite', directory)
        try:
            assert restarted.settings['theme'] == 'mario'
        finally:
            restarted.close()
    print('PASS: custom icon upload, editable suggestions, theme persistence/update/delete, overlay/PNG and mobile layout.')


if __name__ == '__main__':
    main()
