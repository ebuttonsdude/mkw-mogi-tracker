"""Synthetic Chromium checks for live race visibility and reversible overlay motion."""
from pathlib import Path
import tempfile
import threading
from unittest.mock import Mock

from mkw_tracker.server import Application, make_server


def main():
    import numpy as np
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = Application(root / 'scores.sqlite', root)
        app.store.create_team('Motion test')
        server = make_server(app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 800, 'height': 700})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(base + '/overlay')
                overlay = page.locator('#overlay')
                expect(page.locator('.standing-card')).to_have_count(1)
                page.locator('.standing-card').evaluate('(node) => node.dataset.kept = "yes"')

                def settings(**values):
                    response = page.request.patch(base + '/api/settings', data={'overlay': values})
                    assert response.ok, response.text()

                def visibility(action):
                    response = page.request.post(base + '/api/overlay/' + action, data={})
                    assert response.ok, response.text()
                    expect(overlay).to_have_attribute('aria-hidden', str(action == 'hide').lower(), timeout=5000)

                for effect in ('slide', 'fade', 'checkered', 'dissolve', 'zoom', 'flip', 'iris', 'wipe'):
                    settings(transition_style=effect, slide_duration=700)
                    expect(overlay).to_have_attribute('data-transition', effect, timeout=5000)
                    visibility('hide')
                    expect(overlay).to_have_css('visibility', 'hidden')
                    assert page.locator('.standing-card').get_attribute('data-kept') == 'yes', effect
                    visibility('show')
                    expect(overlay).to_have_css('visibility', 'visible')
                    page.wait_for_timeout(800)
                    expect(overlay).to_have_css('transform', 'none')
                    expect(overlay).to_have_css('opacity', '1')
                    assert overlay.evaluate('(node) => !node.style.clipPath'), effect
                    assert page.locator('.standing-card').get_attribute('data-kept') == 'yes', effect
                    # Reduced motion wins over every effect, including SVG tiles.
                    page.emulate_media(reduced_motion='reduce')
                    visibility('hide')
                    expect(overlay).to_have_css('visibility', 'hidden')
                    expect(overlay).to_have_css('transition-duration', '0s')
                    visibility('show')
                    expect(overlay).to_have_css('visibility', 'visible')
                    assert overlay.evaluate('(node) => !node.style.clipPath')
                    page.emulate_media(reduced_motion='no-preference')

                # Interrupted patterned animations settle to the most recent state.
                settings(transition_style='dissolve', slide_duration=5000)
                expect(overlay).to_have_attribute('data-transition', 'dissolve', timeout=5000)
                visibility('hide')
                assert 'overlay-reveal' in overlay.evaluate('(node) => node.style.clipPath')
                visibility('show')
                page.emulate_media(reduced_motion='reduce')
                expect(overlay).to_have_css('visibility', 'visible')
                page.wait_for_function('() => !document.querySelector("#overlay").style.clipPath')
                page.emulate_media(reduced_motion='no-preference')
                settings(slide_duration=0)
                visibility('hide')
                expect(overlay).to_have_css('visibility', 'hidden')
                visibility('show')
                expect(overlay).to_have_css('visibility', 'visible')

                settings(auto_visibility=True)
                reader = Mock()
                reader.read_prerace.return_value = {'course': 'Rainbow Road'}
                reader.is_prerace_layout.return_value = False
                reader.read.return_value = {'phase': 'unknown', 'rows': []}
                app.capture.reader = reader
                frame = np.full((100, 160, 3), 100, dtype=np.uint8)
                app.capture.observe(frame)
                expect(overlay).to_have_attribute('aria-hidden', 'true', timeout=5000)
                visibility('show')
                app.capture.observe(frame)  # Same intro cannot defeat manual show.
                page.wait_for_timeout(1700)
                expect(overlay).to_have_attribute('aria-hidden', 'false')
                visibility('hide')
                reader.read_prerace.return_value = None
                reader.read.return_value = {'phase': 'finishing_results', 'rows': [
                    {'position': position, 'name': f'Player {position}'} for position in range(1, 13)]}
                app.capture.observe(frame)
                expect(overlay).to_have_attribute('aria-hidden', 'false', timeout=5000)
                assert app.store.snapshot()['room']['points'] == 0
                # Persist controls through the real dashboard form.
                page.goto(base)
                page.locator('[data-tab="overlays"]').click()
                form = page.locator('#overlay-settings')
                during = form.get_by_label('Automatically show during races / hide after results', exact=True)
                after = form.get_by_label('Automatically hide during races / show after results', exact=True)
                expect(after).to_be_checked()
                during.check()
                expect(after).not_to_be_checked()
                during.uncheck()
                expect(after).not_to_be_checked()  # Both off leaves manual visibility.
                during.check()
                after.check()
                expect(during).not_to_be_checked()
                form.locator('[name="transition_style"]').select_option('checkered')
                form.locator('[name="slide_duration"]').fill('850')
                form.get_by_role('button', name='Save overlay settings').click()
                expect(page.locator('#message')).to_contain_text('saved')
                assert app.settings['overlay']['transition_style'] == 'checkered'
                assert app.settings['overlay']['auto_visibility'] is True
                assert app.settings['overlay']['slide_duration'] == 850
                # The same form configures named sources independently.
                response = page.request.post(base + '/api/overlay-profiles', data={
                    'slug': 'race-cards', 'name': 'Race cards'})
                assert response.ok, response.text()
                expect(page.locator('#overlay-source option[value="race-cards"]')).to_have_count(1)
                page.locator('#overlay-source').select_option('race-cards')
                form.locator('[name="transition_style"]').select_option('fade')
                during.check()
                expect(after).not_to_be_checked()
                form.locator('[name="slide_duration"]').fill('0')
                with page.expect_response(lambda response: response.url.endswith('/api/overlay-profiles/race-cards')
                                           and response.request.method == 'PATCH') as saved:
                    form.get_by_role('button', name='Save overlay settings').click()
                assert saved.value.ok
                source = app.overlay_profiles.get('race-cards')['overlay']
                assert source['auto_visibility_during_races'] and not source['auto_visibility']
                assert source['transition_style'] == 'fade'
                assert app.settings['overlay']['auto_visibility'] and not app.settings['overlay']['auto_visibility_during_races']
                assert app.settings['overlay']['transition_style'] == 'checkered'
                page.reload()
                page.locator('[data-tab="overlays"]').click()
                page.locator('#overlay-source').select_option('race-cards')
                expect(during).to_be_checked()
                expect(after).not_to_be_checked()

                response = page.request.post(base + '/api/overlay/race-cards/hide', data={})
                assert response.ok
                page.goto(base + '/overlay/race-cards')
                expect(overlay).to_have_attribute('data-transition', 'fade')
                expect(overlay).to_have_attribute('aria-hidden', 'true', timeout=5000)
                for results in (False, True):
                    with app.lock:
                        app.capture._emit_overlay_event(results, app.store.active_session()['id'])
                    expect(overlay).to_have_attribute('aria-hidden', str(results).lower(), timeout=5000)
                    assert page.request.get(base + '/api/overlay').json()['visible'] is results
                response = page.request.post(base + '/api/overlay/race-cards/show', data={})
                assert response.ok
                expect(overlay).to_have_attribute('aria-hidden', 'false', timeout=5000)
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            app.close()
    print('Overlay motion and live race visibility browser acceptance passed')


if __name__ == '__main__':
    main()
