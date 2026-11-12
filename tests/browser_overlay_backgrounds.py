"""Runtime Lounge backgrounds and HUD controls; run in a private PID namespace."""
from io import BytesIO
from pathlib import Path
import tempfile
import threading

from PIL import Image

from mkw_tracker.server import Application, make_server
from tests.test_overlay_profiles import payload


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        image = BytesIO()
        Image.new('RGB', (800, 200), '#16758a').save(image, format='PNG')
        upload = {'name': 'background.png', 'mimeType': 'image/png', 'buffer': image.getvalue()}
        app = Application(root / 'scores.sqlite', root)
        app.lounge.fetch = lambda name, *_: payload(name)
        for key in ('cards', 'other'):
            app.overlay_profiles.create(key, key.title(), app.settings)
            app.overlay_profiles.update(key, {
                'content': {block: block == 'lounge' for block in app.overlay_profiles.get(key)['content']},
                'overlay': {'width': 620, 'height': 140, 'show_header': False},
                'lounge': {'enabled': True, 'players': ['Example'], 'season': 3}})
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1280, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(base)
                page.locator('#auto-refresh').uncheck()
                page.wait_for_function('!refreshing')
                page.locator('#tab-overlays').click()
                page.locator('#overlay-source').select_option('cards')
                page.locator('summary').filter(has_text='Source content and Lounge cards').click()
                file = page.locator('#lounge-background-file')
                submit = page.locator('#lounge-background-upload')
                thumbnail = page.locator('#lounge-background-thumbnail')
                background = page.locator('#lounge-background')
                message = page.locator('#message')
                expect(thumbnail).to_be_hidden()
                file.set_input_files(upload)
                submit.click()
                expect(message).to_contain_text('Background uploaded for source cards')
                expect(background).to_have_value('custom')
                expect(thumbnail).to_be_visible()
                first_url = thumbnail.get_attribute('src')
                assert first_url.startswith('/api/overlay-profiles/cards/background.png?v=')
                assert page.request.get(base + first_url).ok
                # Image is saved immediately; appearance remains a separate unsaved draft.
                assert app.overlay_profiles.get('cards')['lounge']['appearance']['background'] == 'aurora'
                assert page.request.get(base + '/api/overlay-profiles/default/background.png').status == 404
                page.locator('#overlay-apply-preview').click()
                expect(message).to_contain_text('settings applied')
                live = page.frame_locator('#overlay-live-preview')
                expect(live.locator('.lounge-card')).to_have_attribute('data-background', 'custom')
                expect(live.locator('.lounge-card .lounge-background-image')).to_have_count(1)
                page.locator('#lounge-hud-preset').click()
                expect(background).to_have_value('hud')
                expect(page.locator('#lounge-border')).to_have_value('hud')
                expect(page.locator('#lounge-shape')).to_have_value('rounded')
                page.locator('#overlay-apply-preview').click()
                expect(message).to_contain_text('settings applied')
                expect(live.locator('.lounge-card')).to_have_attribute('data-background', 'hud')
                expect(thumbnail).to_have_attribute('src', first_url)
                # Uploaded background can combine with the HUD rim without deleting the image.
                background.select_option('custom')
                page.locator('#overlay-source-settings button[type=submit]').click()
                expect(message).to_contain_text('preferences saved')
                assert app.overlay_profiles.get('cards')['lounge']['appearance']['border'] == 'hud'

                # Invalid containers leave the saved image and appearance untouched.
                file.set_input_files({'name': 'bad.png', 'mimeType': 'image/png', 'buffer': b'not an image'})
                submit.click()
                expect(message).to_contain_text('Background was not changed')
                expect(thumbnail).to_have_attribute('src', first_url)
                page.locator('#overlay-source-reset').click()
                assert file.input_value() == ''

                # Reset while FileReader is pending cancels before any upload request.
                uploads = []
                page.on('request', lambda request: uploads.append(request.url)
                        if request.method == 'POST' and request.url.endswith('/background') else None)
                page.evaluate("""() => {
                  window.NativeFileReader = window.FileReader;
                  window.FileReader = class extends window.NativeFileReader {
                    readAsDataURL(file) { window.finishBackgroundRead = () => super.readAsDataURL(file); }
                  };
                }""")
                file.set_input_files(upload)
                submit.click()
                page.locator('#overlay-source-reset').click()
                page.evaluate('finishBackgroundRead()')
                expect(message).to_contain_text('Source or draft changed while reading')
                assert uploads == []
                page.evaluate('() => { window.FileReader = window.NativeFileReader; }')

                # New edits/new file during an upload response must survive the old completion.
                def newer_draft(route):
                    response = route.fetch()
                    background.select_option('carbon')
                    file.set_input_files({'name': 'next.png', 'mimeType': 'image/png', 'buffer': image.getvalue()})
                    route.fulfill(response=response)

                page.route('**/api/overlay-profiles/cards/background', newer_draft)
                file.set_input_files(upload)
                submit.click()
                expect(message).to_contain_text('Background uploaded')
                expect(background).to_have_value('carbon')
                assert file.input_value().endswith('next.png')
                page.unroute('**/api/overlay-profiles/cards/background', newer_draft)
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('#overlay-source').select_option('other')
                assert file.input_value() == ''
                expect(thumbnail).to_be_hidden()
                expect(background).to_have_value('aurora')
                # Discarded pending files cannot be accidentally uploaded to the new source.
                submit.click()
                expect(message).to_contain_text('Choose a PNG')
                page.locator('#overlay-source').select_option('cards')
                expect(thumbnail).to_be_visible()
                background.select_option('carbon')
                page.locator('#lounge-background-remove').click()
                expect(message).to_contain_text('Background removed for source cards')
                expect(thumbnail).to_be_hidden()
                expect(background).to_have_value('carbon')
                assert page.request.get(base + first_url).status == 404
                # Saving disables the real submit control, not the new HUD preset button.
                save = page.locator('#overlay-source-settings button[type=submit]')
                hud = page.locator('#lounge-hud-preset')

                def delayed_source_save(route):
                    response = route.fetch()
                    expect(save).to_be_disabled()
                    expect(hud).to_be_enabled()
                    hud.click()
                    route.fulfill(response=response)

                page.route('**/api/overlay-profiles/cards', delayed_source_save)
                save.click()
                expect(message).to_contain_text('preferences saved')
                expect(save).to_be_enabled()
                expect(background).to_have_value('hud')
                expect(page.locator('#lounge-border')).to_have_value('hud')
                assert app.overlay_profiles.get('cards')['lounge']['appearance']['background'] == 'carbon'
                page.unroute('**/api/overlay-profiles/cards', delayed_source_save)

                # Only selected source asset changed; no external images or horizontal overflow.
                assert page.request.get(base + '/api/overlay-profiles/other/background.png').status == 404
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                browser.close()
                print('PASS: upload/save/preview, HUD preset, custom + HUD rim, invalid-image fallback, draft/file fences, source isolation, removal and mobile layout.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
