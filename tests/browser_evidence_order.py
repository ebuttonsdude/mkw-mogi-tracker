"""Result-entry evidence order using synthetic pictures and a disposable store.

Run only in a verified separate PID namespace with private proc.
"""
from pathlib import Path
import tempfile
import threading
from uuid import NAMESPACE_URL, uuid5

from PIL import Image

from mkw_tracker.server import Application, make_server


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = Application(root / 'scores.sqlite', root)
        race = app.store.create_race('image-order', 'Example course')
        directory = root / 'evidence'
        directory.mkdir(exist_ok=True)
        urls = {}
        for label, kind, color in [('map.png', 'prerace', '#3355aa'),
                                      ('suspected.png', 'suspected_results', '#aa7733'),
                                      ('result-old.png', 'results', '#337755'),
                                      ('result-new.png', 'results', '#55aa77')]:
            filename = f'{uuid5(NAMESPACE_URL, label)}.png'
            urls[label] = f'/evidence/{filename}'
            Image.new('RGB', (160, 90), color).save(directory / filename)
            app.store.add_evidence(race['id'], filename, kind=kind, label=label)
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_address[1]}')
                page.wait_for_function("state?.races?.some(race => race.id === 'image-order')")
                page.evaluate("openEditor('image-order')")
                images = page.locator('#editor-evidence img')
                expect(images).to_have_count(4)
                assert images.evaluate_all('(nodes) => nodes.map(node => node.getAttribute("src"))') == [
                    urls['map.png'], urls['suspected.png'],
                    urls['result-old.png'], urls['result-new.png']]
                expect(page.locator('#editor-evidence')).to_contain_text('Finishing order is unverified')
                expect(page.locator('#placements select')).to_have_count(12)
                page.wait_for_function("Array.from(document.querySelectorAll('#editor-evidence img')).every(img => img.complete && img.naturalWidth)")
                last = images.last.bounding_box()
                fields = page.locator('#results').bounding_box()
                assert last['y'] + last['height'] <= fields['y'], (last, fields)
                # The all-evidence gallery keeps its existing results-first order.
                page.evaluate("openEvidence(state.races.find(race => race.id === 'image-order'))")
                expect(page.locator('#history-evidence img')).to_have_count(4)
                expect(page.locator('#history-evidence img').first).to_have_attribute('src', urls['result-old.png'])
                expect(page.locator('#history-evidence img').last).to_have_attribute('src', urls['map.png'])
                page.set_viewport_size({'width': 360, 'height': 740})
                page.evaluate("openEditor('image-order')")
                expect(images.last).to_have_attribute('src', urls['result-new.png'])
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert app.store.snapshot()['room']['points'] == 0
                assert not errors, errors
                browser.close()
                print('PASS: all evidence retained, map first and newest results nearest entry, unchanged gallery, narrow layout and no score mutation.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
