"""Dedicated Overlays workspace, captured saves and opt-in same-origin live preview.

Only run inside verified separate PID/private-proc isolation.
"""
import json
from pathlib import Path
import tempfile
import threading

from mkw_tracker.server import Application, make_server
from tests.test_overlay_profiles import payload


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = Application(root / 'scores.sqlite', root)
        lookups = []

        def fetch(name, *_):
            lookups.append(name)
            return payload(name, mmr=2742, rank='Bronze')

        app.lounge.fetch = fetch
        for key in ('cards', 'other'):
            app.overlay_profiles.create(key, key.title(), app.settings)
            app.overlay_profiles.update(key, {
                'content': {block: block == 'lounge' for block in app.overlay_profiles.get(key)['content']},
                'overlay': {'width': 520 if key == 'cards' else 700, 'height': 120, 'show_header': False},
                'lounge': {'enabled': key == 'cards', 'players': ['Saved Player'] if key == 'cards' else [],
                           'season': 3}})
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1280, 'height': 1000})
                page.on('pageerror', lambda error: errors.append(str(error)))
                requests = []
                page.on('request', lambda request: requests.append((request.method, request.url)))
                base = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(base)
                page.locator('#auto-refresh').uncheck()
                page.wait_for_function('!refreshing')
                expect(page.locator('[role=tab]')).to_have_count(7)
                expect(page.locator('#view-settings #overlay-settings')).to_have_count(0)
                expect(page.locator('#view-overlays #overlay-settings')).to_have_count(1)
                expect(page.locator('#view-settings #font-details')).to_have_count(1)
                frame = page.locator('#overlay-live-preview')
                live = page.frame_locator('#overlay-live-preview')
                apply = page.locator('#overlay-apply-preview')
                refresh = page.locator('#overlay-refresh-preview')
                width = page.locator('#overlay-settings [name=width]')
                height = page.locator('#overlay-settings [name=height]')

                def unloaded():
                    assert frame.get_attribute('src') is None
                    expect(page.locator('#overlay-preview-stage')).to_be_hidden()

                def refresh_state(background=False):
                    # refresh() deliberately coalesces concurrent polls. Wait for idle
                    # and start this request atomically, then await its actual response.
                    page.wait_for_function('''background => {
                        if (refreshing) return false;
                        window.workspaceRefresh = refresh(background);
                        return true;
                    }''', arg=background)
                    page.evaluate('async () => { await window.workspaceRefresh; delete window.workspaceRefresh; }')

                def preview_fits():
                    # ResizeObserver updates can land between separate bounding-box RPCs.
                    page.wait_for_function('''() => document.querySelector('#overlay-live-preview').getBoundingClientRect().width <=
                        document.querySelector('#overlay-preview-stage').getBoundingClientRect().width + 1''')

                def overlay_calls():
                    return sum('/api/overlay/' in url or url.endswith('/api/overlay') for _, url in requests)

                page.locator('#tab-overlays').click()
                expect(page.locator('#view-overlays')).to_be_visible()
                unloaded()
                assert lookups == [] and overlay_calls() == 0
                page.locator('#tab-overlays').focus()
                page.keyboard.press('ArrowRight')
                expect(page.locator('#tab-settings')).to_be_focused()
                page.keyboard.press('ArrowLeft')
                expect(page.locator('#tab-overlays')).to_be_focused()
                page.reload()
                expect(page.locator('#tab-overlays')).to_have_attribute('aria-selected', 'true')
                page.locator('#auto-refresh').uncheck()
                page.wait_for_function('!refreshing')
                unloaded()
                page.locator('#overlay-source').select_option('cards')
                page.locator('summary').filter(has_text='Source content and Lounge cards').click()
                page.locator('#lounge-usernames').fill('Applied Player')
                width.fill('620')
                height.fill('90')
                apply.click()
                expect(page.locator('#message')).to_contain_text('settings applied')
                expect(frame).to_have_attribute('src', '/overlay/cards')
                expect(live.locator('.lounge-name')).to_have_text('Applied Player', timeout=7000)
                assert app.overlay_profiles.get('cards')['overlay']['width'] == 620
                assert app.overlay_profiles.get('cards')['lounge']['players'] == ['Applied Player']
                assert app.settings['overlay']['width'] == 560
                assert live.locator('body').evaluate('() => innerWidth') == 620
                assert live.locator('body').evaluate('() => innerHeight') == 90

                # Applying a preview must not silently show a source the operator hid.
                page.locator('#overlay-hide').click()
                expect(live.locator('#overlay')).to_have_attribute('aria-hidden', 'true', timeout=6000)
                apply.click()
                expect(frame).to_have_attribute('src', '/overlay/cards')
                expect(live.locator('#overlay')).to_have_attribute('aria-hidden', 'true', timeout=6000)
                page.locator('#overlay-show').click()
                expect(live.locator('#overlay')).to_have_attribute('aria-hidden', 'false', timeout=6000)

                # Validation happens before either save, including a closed source disclosure.
                previous_patches = sum(method == 'PATCH' for method, _ in requests)
                page.locator('#overlay-source-name').fill('')
                page.locator('summary').filter(has_text='Source content and Lounge cards').click()
                apply.click()
                expect(page.locator('#overlay-source-name')).to_be_visible()
                assert sum(method == 'PATCH' for method, _ in requests) == previous_patches
                page.locator('#overlay-source-name').fill('Cards')
                height.fill('39')
                apply.click()
                assert sum(method == 'PATCH' for method, _ in requests) == previous_patches
                height.fill('1400')
                width.fill('1800')
                page.locator('#overlay-settings button').click()
                expect(frame).to_have_attribute('width', '1800')
                expect(frame).to_have_attribute('height', '1400')
                expect(live.locator('#overlay')).to_have_css('width', '1800px')
                assert live.locator('body').evaluate('() => innerWidth') == 1800
                stage = page.locator('#overlay-preview-stage')
                assert stage.bounding_box()['height'] <= 361
                assert abs(stage.bounding_box()['width'] / stage.bounding_box()['height'] - 1800 / 1400) < .01
                preview_fits()
                Path('.local').mkdir(exist_ok=True)
                page.locator('.overlay-preview-shell').screenshot(path='.local/overlay-workspace-desktop.png')
                page.set_viewport_size({'width': 390, 'height': 844})
                expect(live.locator('#overlay')).to_have_css('width', '1800px')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                preview_fits()
                page.locator('.overlay-preview-shell').screenshot(path='.local/overlay-workspace-mobile.png')
                page.set_viewport_size({'width': 1280, 'height': 1000})

                # Draft changes never enter the iframe or overwrite saved geometry.
                page.locator('#lounge-usernames').fill('Unsaved Player')
                width.fill('700')
                refresh.click()
                expect(live.locator('.lounge-name')).to_have_text('Applied Player', timeout=7000)
                expect(frame).to_have_attribute('width', '1800')
                expect(page.locator('#lounge-usernames')).to_have_value('Unsaved Player')
                expect(width).to_have_value('700')
                assert app.overlay_profiles.get('cards')['lounge']['players'] == ['Applied Player']
                page.locator('#overlay-source-settings button[type=submit]').click()
                expect(live.locator('.lounge-name')).to_have_text('Unsaved Player', timeout=7000)
                expect(width).to_have_value('700')
                page.locator('#overlay-settings button').click()
                expect(frame).to_have_attribute('width', '700')

                # Leaving the workspace unloads the browsing context and its polling timer.
                page.locator('#tab-settings').click()
                unloaded()
                page.wait_for_timeout(300)
                paused_calls = overlay_calls()
                page.wait_for_timeout(1800)
                assert overlay_calls() == paused_calls
                page.locator('#tab-overlays').click()
                unloaded()
                refresh.click()
                expect(frame).to_have_attribute('src', '/overlay/cards')

                # A second-save failure is explicit and retains the complete editable draft.
                def fail_display(route):
                    if route.request.method == 'PATCH' and 'overlay' in route.request.post_data_json:
                        route.fulfill(status=500, content_type='application/json', body=json.dumps({'error': 'Injected display failure'}))
                    else:
                        route.continue_()

                page.route('**/api/overlay-profiles/cards', fail_display)
                page.locator('#lounge-usernames').fill('Partial Saved')
                width.fill('720')
                apply.click()
                expect(page.locator('#message')).to_contain_text('Source content was saved, but display settings were not saved')
                expect(width).to_have_value('720')
                expect(page.locator('#lounge-usernames')).to_have_value('Partial Saved')
                assert app.overlay_profiles.get('cards')['overlay']['width'] == 700
                assert app.overlay_profiles.get('cards')['lounge']['players'] == ['Partial Saved']
                unloaded()
                page.unroute('**/api/overlay-profiles/cards', fail_display)
                apply.click()
                expect(frame).to_have_attribute('src', '/overlay/cards')
                expect(live.locator('.lounge-name')).to_have_text('Partial Saved', timeout=7000)

                # Source changes during Apply cannot retarget its captured writes or reopen old preview.
                held = []

                def hold_source(route):
                    if route.request.method == 'PATCH' and 'content' in route.request.post_data_json:
                        response = route.fetch()
                        held.append((route, response.body()))
                    else:
                        route.continue_()

                def wait_held():
                    for _ in range(80):
                        if held:
                            return
                        page.wait_for_timeout(25)
                    raise AssertionError('Request was not intercepted')

                def release():
                    route, body = held.pop()
                    route.fulfill(status=200, content_type='application/json', body=body)

                page.route('**/api/overlay-profiles/cards', hold_source)
                page.locator('#lounge-usernames').fill('Captured Draft')
                width.fill('740')
                apply.click()
                wait_held()
                page.locator('#lounge-usernames').fill('Newer Draft')
                width.fill('750')
                release()
                expect(apply).to_be_enabled()
                expect(page.locator('#message')).to_contain_text('Newer drafts are kept')
                assert app.overlay_profiles.get('cards')['lounge']['players'] == ['Captured Draft']
                assert app.overlay_profiles.get('cards')['overlay']['width'] == 740
                expect(page.locator('#lounge-usernames')).to_have_value('Newer Draft')
                expect(width).to_have_value('750')
                unloaded()
                page.locator('#overlay-source-reset').click()
                expect(page.locator('#lounge-usernames')).to_have_value('Captured Draft')
                expect(width).to_have_value('740')
                page.locator('#lounge-usernames').fill('Captured Player')
                width.fill('800')
                apply.click()
                wait_held()
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('#overlay-source').select_option('other')
                release()
                expect(apply).to_be_enabled()
                expect(page.locator('#overlay-source')).to_have_value('other')
                assert app.overlay_profiles.get('cards')['overlay']['width'] == 800
                assert app.overlay_profiles.get('other')['overlay']['width'] == 700
                unloaded()
                page.unroute('**/api/overlay-profiles/cards', hold_source)

                # Default Apply keeps the existing global-display settings endpoint semantics.
                page.locator('#overlay-source').select_option('default')
                width.fill('601')
                apply.click()
                expect(frame).to_have_attribute('src', '/overlay')
                assert app.settings['overlay']['width'] == 601
                assert 'width' not in app.overlay_profiles.get('default')['overlay']
                page.locator('#overlay-source').select_option('cards')
                unloaded()

                # Changing mode between the two saves aborts the second, even with a new draft.
                page.route('**/api/overlay-profiles/cards', hold_source)
                page.locator('#lounge-usernames').fill('Mode Draft')
                width.fill('810')
                apply.click()
                wait_held()
                with app.lock:
                    app.switch_tracker_mode('ffa', app.store.active_session()['id'])
                refresh_state()
                release()
                expect(page.locator('#message')).to_contain_text('Source content was saved, but display settings were not saved')
                assert app.overlay_profiles.get('cards')['overlay']['width'] == 800
                expect(width).to_have_value('810')
                unloaded()
                page.unroute('**/api/overlay-profiles/cards', hold_source)
                page.locator('#overlay-source-reset').click()
                refresh.click()
                expect(frame).to_have_attribute('src', '/overlay/cards')
                # Even a focused draft that prevents background redraw must pause on remote mode changes.
                page.locator('#auto-refresh').uncheck()
                page.wait_for_function('!refreshing')
                width.fill('820')
                width.focus()
                # An in-flight poll may already hold the previous mode. Reproduce
                # that ordering explicitly instead of depending on interval timing.
                state_intercepted = False

                def hold_state(route):
                    nonlocal state_intercepted
                    if state_intercepted:
                        route.continue_()
                        return
                    state_intercepted = True
                    response = route.fetch()
                    held.append((route, response.body()))

                page.route('**/api/state', hold_state)
                # No interval can take the intercepted route before this request:
                # enable polling and start it in the same browser task.
                page.evaluate('''() => {
                    if (refreshing) throw new Error('Unexpected refresh before controlled poll');
                    document.querySelector('#auto-refresh').checked = true;
                    window.workspaceRefresh = refresh(true);
                }''')
                wait_held()
                assert json.loads(held[0][1])['tracker_mode'] == 'ffa'
                with app.lock:
                    app.switch_tracker_mode('ww', app.store.active_session()['id'])
                # A competing interval/explicit refresh is coalesced, not awaited.
                page.evaluate('refresh(true)')
                assert page.evaluate('refreshing') and len(held) == 1
                release()
                # Keep the one-shot handler installed through the next request:
                # later polls must pass through rather than become held too.
                refresh_state(True)
                page.unroute('**/api/state', hold_state)
                unloaded()
                expect(width).to_have_value('820')
                refresh_state()
                expect(page.locator('#view-overlays')).to_be_visible()
                page.locator('#overlay-source-reset').click()
                refresh.click()
                expect(frame).to_have_attribute('src', '/overlay/cards')
                expect(live.locator('.lounge-card')).to_have_count(0)
                # External deletion is another pause edge, without destroying the focused draft.
                width.fill('825')
                width.focus()
                with app.lock:
                    app.overlay_profiles.delete('cards')
                refresh_state(True)
                unloaded()
                expect(width).to_have_value('825')
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('#overlay-source').select_option('other')
                refresh.click()
                expect(frame).to_have_attribute('src', '/overlay/other')
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('#overlay-source-delete').click()
                expect(page.locator('#overlay-source')).to_have_value('default')
                unloaded()
                assert 'cards' not in app.overlay_profiles.profiles
                assert 'other' not in app.overlay_profiles.profiles
                assert not errors, errors
                browser.close()
                print('PASS: dedicated accessible Overlays tab, captured Apply, live saved preview, draft isolation, native-size scaling, pause/source/mode/deletion fences and honest partial-save failures.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
