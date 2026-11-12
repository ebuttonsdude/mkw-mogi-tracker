"""Optional course-picture Chromium acceptance; temporary data and synthetic colors only."""
from io import BytesIO
from pathlib import Path
import tempfile
import threading

from mkw_tracker.server import Application, make_server
from mkw_tracker.migrate import migrate
from tests.test_course_migration import LEGACY_COURSE_FILES


def main():
    from PIL import Image
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary) / 'target'
        legacy = Path(temporary) / 'legacy'
        static = legacy / 'mkw_tracker/static/track-icons'
        static.mkdir(parents=True)
        (legacy / '.local/tracker').mkdir(parents=True)
        retained = directory / 'imported-assets/track-icons'
        retained.mkdir(parents=True)
        source = BytesIO()
        Image.new('RGB', (48, 32), '#3478ab').save(source, format='PNG')
        for filename in LEGACY_COURSE_FILES:
            (static / filename).write_bytes(source.getvalue())
            (retained / filename).write_bytes(source.getvalue())
        repaired = migrate(legacy, directory, categories={'pictures'}, apply=True,
                           conflict='skip', theme_conflict='skip')
        assert repaired['course_images'] == 40 and not repaired['warnings'], repaired
        app = Application(directory / 'scores.sqlite', directory)
        app.store.create_race('course-test', 'Rainbow Road')
        server = make_server(app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(base)
                page.locator('#tab-settings').click()
                expect(page.locator('#course-images-details')).not_to_have_attribute('open', '')
                page.locator('#course-images-details summary').click()
                expect(page.locator('#course-image-library img')).to_have_count(40)
                page.locator('#course-image-reload').click()
                expect(page.locator('#course-image-library img')).to_have_count(40)
                page.wait_for_function('''() => [...document.querySelectorAll('#course-image-library img')]
                    .length === 40 && [...document.querySelectorAll('#course-image-library img')]
                    .every(image => image.complete && image.naturalWidth > 0)''')
                saved = page.request.get(base + '/api/course-images').json()['images']
                assert {item['course'] for item in saved} == set(LEGACY_COURSE_FILES.values())
                page.locator('#tab-history').click()
                expect(page.locator('#history .course-icon')).to_have_count(1)
                expect(page.locator('#history')).to_contain_text('Rainbow Road')
                page.locator('#tab-settings').click()
                # Clear only this disposable synthetic library for the existing upload scenarios.
                for item in saved:
                    response = page.request.delete(base + '/api/course-images/' + item['id'],
                                                   data={}, headers={'Origin': base})
                    assert response.status == 200, response.text()
                page.locator('#course-image-reload').click()
                expect(page.locator('#course-image-library')).to_contain_text('No saved course pictures')
                files = [{'name': name, 'mimeType': 'image/png', 'buffer': source.getvalue()}
                         for name in ('track_084RR.png', 'unmatched-local.png')]
                page.locator('#course-image-files').set_input_files(files)
                expect(page.locator('#course-image-status')).to_contain_text('Review associations')
                expect(page.get_by_label('Course for track_084RR.png')).to_have_value('Rainbow Road')
                expect(page.get_by_label('Course for unmatched-local.png')).to_have_value('')
                # An unrelated state poll does not clear a draft or close the disclosure.
                page.locator('#course-image-import').click()
                expect(page.locator('#course-image-status')).to_have_text('1 imported · 0 skipped · 0 failed · 1 pending')
                expect(page.locator('#course-image-drafts')).to_contain_text('Pending: choose a course')
                page.get_by_label('Course for unmatched-local.png').fill('My custom course')
                page.locator('#refresh').click()
                expect(page.get_by_label('Course for unmatched-local.png')).to_have_value('My custom course')
                page.evaluate('async () => { document.activeElement.blur(); await refresh(true); }')
                expect(page.get_by_label('Course for unmatched-local.png')).to_have_value('My custom course')
                expect(page.locator('#course-images-details')).to_have_attribute('open', '')
                page.locator('#course-image-import').click()
                expect(page.locator('#course-image-status')).to_have_text('1 imported · 1 skipped · 0 failed · 0 pending')
                expect(page.locator('#course-image-library img')).to_have_count(2)
                page.locator('#tab-history').click()
                expect(page.locator('#history .course-icon')).to_have_count(1)
                expect(page.locator('#history')).to_contain_text('Rainbow Road')
                page.locator('#tab-capture').click()
                page.evaluate('''() => { state.capture.current_race = state.races[0]; renderCurrentRace([]); }''')
                expect(page.locator('#current-capture-race .course-icon')).to_have_count(1)
                expect(page.locator('#current-capture-race')).to_contain_text('Rainbow Road')
                assert page.evaluate('''() => ['/static/track-icons/old.png', 'https://example.com/art.png', '/api/course-images/x.png?v=aa'].every(
                    icon_url => !courseTitle({course: 'Safe label', icon_url}).querySelector('img'))''')
                page.locator('#tab-settings').click()
                page.locator('#course-image-files').set_input_files(files[0])
                expect(page.locator('#course-image-status')).to_contain_text('Review associations')
                page.once('dialog', lambda dialog: dialog.dismiss())
                page.locator('#course-image-import').click()
                expect(page.locator('#course-image-status')).to_contain_text('0 imported · 1 skipped')
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('#course-image-import').click()
                expect(page.locator('#course-image-status')).to_contain_text('1 imported · 0 skipped')
                page.locator('#course-image-clear').click()
                expect(page.locator('#course-image-drafts tr')).to_have_count(0)
                page.once('dialog', lambda dialog: dialog.dismiss())
                page.get_by_role('button', name='Delete picture for Rainbow Road', exact=True).click()
                expect(page.locator('#course-image-library img')).to_have_count(2)
                held_deletes = []

                def hold_delete(route):
                    if route.request.method == 'DELETE':
                        held_deletes.append(route)
                    else:
                        route.continue_()

                page.route('**/api/course-images/*', hold_delete)
                page.once('dialog', lambda dialog: dialog.accept())
                page.get_by_role('button', name='Delete picture for Rainbow Road', exact=True).click()
                expect(page.locator('#course-image-import')).to_be_disabled()
                expect(page.locator('#course-image-files')).to_be_disabled()
                expect(page.get_by_role('button', name='Delete picture for My custom course', exact=True)).to_be_disabled()
                uploads = []
                page.on('request', lambda request: uploads.append(request.url)
                        if request.method == 'POST' and request.url.endswith('/api/course-images') else None)
                # Even a synthetic click cannot bypass the shared mutation gate.
                page.locator('#course-image-import').dispatch_event('click')
                assert not uploads
                assert held_deletes
                held_deletes.pop().continue_()
                expect(page.locator('#course-image-library img')).to_have_count(1)
                expect(page.locator('#course-image-import')).to_be_enabled()
                page.unroute('**/api/course-images/*', hold_delete)
                page.locator('#tab-history').click()
                expect(page.locator('#history .course-icon')).to_have_count(0)
                expect(page.locator('#history')).to_contain_text('Rainbow Road')
                page.locator('#tab-settings').click()
                page.locator('#course-image-files').set_input_files(files)
                expect(page.locator('#course-image-status')).to_contain_text('Review associations')
                page.get_by_label('Course for unmatched-local.png').fill('Another local course')

                def fail_one_upload(route):
                    if route.request.method == 'POST' and route.request.post_data_json.get('course') == 'Rainbow Road':
                        route.fulfill(status=500, content_type='application/json', body='{"error":"Synthetic failure"}')
                    else:
                        route.continue_()

                page.route('**/api/course-images', fail_one_upload)
                page.locator('#course-image-import').click()
                expect(page.locator('#course-image-status')).to_have_text('1 imported · 0 skipped · 1 failed · 0 pending')
                expect(page.locator('#course-image-drafts')).to_contain_text('Failed: Synthetic failure')
                page.unroute('**/api/course-images', fail_one_upload)
                page.locator('#course-image-clear').click()
                # Filename matching is fenced against both manual edits and discarded batches.
                held_matches = []
                page.route('**/api/course-images/match', lambda route: held_matches.append(route))
                page.locator('#course-image-files').set_input_files(files[1])
                page.wait_for_function('() => document.querySelectorAll("#course-image-drafts tbody tr").length === 1')
                page.get_by_label('Course for unmatched-local.png').fill('Manual choice wins')
                assert held_matches
                held_matches.pop().fulfill(status=200, content_type='application/json',
                                           body='{"matches":[{"filename":"unmatched-local.png","course":"Rainbow Road"}]}')
                expect(page.locator('#course-image-status')).to_contain_text('Review associations')
                expect(page.get_by_label('Course for unmatched-local.png')).to_have_value('Manual choice wins')
                page.locator('#course-image-files').set_input_files(files[1])
                page.wait_for_function('() => document.querySelector("#course-image-status").textContent.startsWith("Matching")')
                page.locator('#course-image-clear').click()
                assert held_matches
                held_matches.pop().fulfill(status=200, content_type='application/json',
                                           body='{"matches":[{"filename":"unmatched-local.png","course":"Rainbow Road"}]}')
                expect(page.locator('#course-image-drafts tr')).to_have_count(0)
                page.unroute('**/api/course-images/match')
                page.locator('#course-image-files').set_input_files(
                    {'name': 'oversized.png', 'mimeType': 'image/png', 'buffer': bytes(4 * 1024 * 1024 + 1)})
                expect(page.locator('#course-image-drafts')).to_contain_text('Too large: limit is 4 MiB')
                page.locator('#course-image-clear').click()
                page.evaluate('''() => {
                    const files = new DataTransfer();
                    for (let i = 0; i < 257; i++) files.items.add(new File(['x'], `file${i}.png`, {type: 'image/png'}));
                    const input = document.querySelector('#course-image-files'); input.files = files.files;
                    input.dispatchEvent(new Event('change'));
                }''')
                expect(page.locator('#course-image-status')).to_have_text('Choose at most 256 files per batch.')
                # Optional pictures are shared across tracker modes, labels never disappear.
                for mode in ('ffa', 'ww'):
                    page.locator('#tracker-mode').select_option(mode)
                    expect(page.locator('body')).to_have_attribute('data-tracker-mode', mode)
                    with app.lock:
                        app.store.create_race(f'{mode}-course', 'My custom course')
                        if mode == 'ww':
                            app.store.set_ww_result(f'{mode}-course', position=3, vr_after=9000)
                    page.locator('#refresh').click()
                    expect(page.locator('#history .course-icon')).to_have_count(1)
                    expect(page.locator('#history')).to_contain_text('My custom course')
                    page.locator('#tab-capture').click()
                    page.evaluate('''() => {
                        state.capture.current_race = state.races.find(race => race.course === 'My custom course');
                        if (state.tracker_mode === 'ww') renderWWCapture(); else renderCurrentRace([]);
                    }''')
                    expect(page.locator('#current-capture-race .course-icon')).to_have_count(1)
                    expect(page.locator('#current-capture-race')).to_contain_text('My custom course')
                    page.locator('#tab-history').click()
                    if mode == 'ww':
                        page.locator('#tab-standings').click()
                        expect(page.locator('#ww-courses .course-icon')).to_have_count(1)
                        page.locator('#tab-history').click()
                # A failed image is removed, leaving the label intact.
                page.route('**/api/course-images/*.png?*', lambda route: route.abort())
                page.reload()
                expect(page.locator('#history')).to_contain_text('My custom course')
                expect(page.locator('#history .course-icon')).to_have_count(0)
                page.locator('#tab-settings').click()
                expect(page.locator('#course-images-details')).not_to_have_attribute('open', '')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.locator('#course-images-details summary').click()
                expect(page.locator('#course-image-files')).to_be_visible()
                assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                page.locator('#course-image-clear').click()
                page.locator('#course-images-details summary').click()
                page.evaluate('() => document.activeElement.blur()')
                assert page.evaluate('() => canRedraw()')
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.locator('#course-images-details summary').click()

                def alias_batch(courses, expected, confirm):
                    expect(page.locator('#course-image-files')).to_be_enabled()
                    batch = [{'name': f'alias-{index}.png', 'mimeType': 'image/png', 'buffer': source.getvalue()}
                             for index in range(len(courses))]
                    page.locator('#course-image-files').set_input_files(batch)
                    expect(page.locator('#course-image-status')).to_contain_text('Review associations')
                    for index, course in enumerate(courses):
                        page.get_by_label(f'Course for alias-{index}.png').fill(course)
                    dialogs = []

                    def decide(dialog):
                        dialogs.append(dialog.message)
                        if confirm:
                            dialog.accept()
                        else:
                            dialog.dismiss()

                    page.on('dialog', decide)
                    page.locator('#course-image-import').click()
                    expect(page.locator('#course-image-status')).to_have_text(expected)
                    page.remove_listener('dialog', decide)
                    return dialogs

                # Resolve canonical aliases against saved pictures, not raw UI strings.
                assert not alias_batch(['Mario Circuit'], '1 imported · 0 skipped · 0 failed · 0 pending', False)
                assert len(alias_batch(['mario_circuit'], '0 imported · 1 skipped · 0 failed · 0 pending', False)) == 1
                assert len(alias_batch(['mario_circuit'], '1 imported · 0 skipped · 0 failed · 0 pending', True)) == 1
                # Every later file resolves again, so duplicates inside one batch also need consent.
                assert len(alias_batch(['Koopa Troopa Beach', 'koopa_troopa_beach'],
                                       '1 imported · 1 skipped · 0 failed · 0 pending', False)) == 1
                assert len(alias_batch(['Batch custom Alpha', 'batch_custom_alpha'],
                                       '2 imported · 0 skipped · 0 failed · 0 pending', True)) == 1
                assert len(alias_batch(['BATCH CUSTOM ALPHA'],
                                       '0 imported · 1 skipped · 0 failed · 0 pending', False)) == 1
                page.locator('#course-image-clear').click()
                browser.close()
                assert not errors, errors
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            app.close()
    print('Course pictures browser acceptance passed')


if __name__ == '__main__':
    main()
