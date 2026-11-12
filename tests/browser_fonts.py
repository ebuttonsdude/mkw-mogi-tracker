"""Optional Chromium font acceptance with neutral temporary files and fake names."""
from io import BytesIO
from pathlib import Path
import tempfile
import threading

from PIL import Image, ImageFont

from mkw_tracker.server import Application, make_server


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fonts = root / 'fonts'
        fonts.mkdir()
        selected = fonts / 'Neutral Preview.ttf'
        selected.write_bytes(ImageFont.load_default(size=20).path.getvalue())
        app = Application(root / 'scores.sqlite', root)
        name = 'Latin 日本語 Ω ☆ ♥\ufe0f A\u200dB 👩\u200d💻 \U0010ffff'
        team = app.store.create_team('Team 日本語 ☆')
        player = app.store.add_player(name)
        app.store.update_player(player['id'], name, team['id'])
        server = make_server(app, 0)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                dashboard = browser.new_page(viewport={'width': 1280, 'height': 1000})
                overlay = browser.new_page(viewport={'width': 560, 'height': 600})
                for page in (dashboard, overlay):
                    page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_address[1]}'
                dashboard.goto(base)
                dashboard.locator('#tab-settings').click()
                dashboard.locator('#font-details > summary').click()
                expect(dashboard.locator('#report-font option', has_text='Neutral Preview')).to_have_count(1)
                dashboard.select_option('#report-font', selected.name)
                dashboard.fill('#font-preview-text', name)
                expect(dashboard.locator('#font-preview')).to_have_text(name)
                dashboard.evaluate('document.fonts.ready')
                assert dashboard.locator('#font-preview').evaluate('(node) => getComputedStyle(node).fontFamily').startswith('TrackerFont')
                dashboard.locator('#font-settings button', has_text='Save font').click()
                expect(dashboard.locator('#message')).to_contain_text('font saved')
                overlay.goto(base + '/overlay')
                expect(overlay.locator('.member-name')).to_have_text(name)
                overlay.evaluate('document.fonts.ready')
                family = overlay.locator('.member-name').evaluate('(node) => getComputedStyle(node).fontFamily')
                assert family.startswith('TrackerFont'), family
                assert overlay.locator('.standing-score').first.evaluate('(node) => getComputedStyle(node).fontFamily') == family
                assert overlay.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgba(0, 0, 0, 0)'
                # CDP reports actual glyph sources, not just a CSS declaration.
                cdp = overlay.context.new_cdp_session(overlay)
                cdp.send('DOM.enable')
                cdp.send('CSS.enable')
                document = cdp.send('DOM.getDocument')
                node = cdp.send('DOM.querySelector', {'nodeId': document['root']['nodeId'], 'selector': '.member-name'})
                used = cdp.send('CSS.getPlatformFontsForNode', {'nodeId': node['nodeId']})['fonts']
                assert any(font['isCustomFont'] and font['glyphCount'] for font in used), used
                assert len({font['familyName'] for font in used if font['glyphCount']}) >= 2, used
                dashboard.reload()
                dashboard.locator('#font-details > summary').click()
                expect(dashboard.locator('#report-font')).to_have_value(selected.name)
                folder = app.store.snapshot()['session']['folder_id']
                response = dashboard.request.get(base + f'/api/folders/{folder}/preview.png')
                assert response.status == 200
                with Image.open(BytesIO(response.body())) as image:
                    image.verify()
                # A disappeared file does not blank names or break PNGs. Keep
                # its portable preference so reinstallation can restore it.
                selected.unlink()
                overlay.reload()
                expect(overlay.locator('.member-name')).to_have_text(name)
                overlay.evaluate('document.fonts.ready')
                assert overlay.locator('.member-name').bounding_box()['width'] > 0
                assert dashboard.request.get(base + f'/api/folders/{folder}/report.png').status == 200
                dashboard.locator('#font-reload').click()
                expect(dashboard.locator('#font-status')).to_contain_text('unavailable')
                expect(dashboard.locator('#report-font')).to_have_value(selected.name)
                dashboard.locator('#font-reset').click()
                expect(dashboard.locator('#report-font')).to_have_value('')
                assert app.settings['report_font'] == ''
                # Failed requests leave text visible, even with a saved entry.
                selected.write_bytes(ImageFont.load_default(size=20).path.getvalue())
                app.font_library.refresh()
                app.update_settings({'report_font': selected.name})
                overlay.route('**/api/fonts/*', lambda route: route.fulfill(status=404, body='missing'))
                overlay.reload()
                expect(overlay.locator('.member-name')).to_have_text(name)
                overlay.evaluate('document.fonts.ready')
                assert overlay.locator('.member-name').bounding_box()['height'] > 0
                overlay.unroute('**/api/fonts/*')
                overlay.wait_for_function('() => Array.from(document.fonts).some(font => font.family === "TrackerFont0" && font.status === "loaded")')
                # No preference changes, score loss, or opaque page background.
                assert app.settings['report_font'] == selected.name
                assert overlay.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgba(0, 0, 0, 0)'
                assert not errors, errors
                browser.close()
                print('PASS: font selector/preview/reset/persistence, actual Latin + Unicode browser fallback, PNG export, missing font/404 recovery and transparent overlay. Not real OBS/CEF validation.')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
            app.close()


if __name__ == '__main__':
    main()
