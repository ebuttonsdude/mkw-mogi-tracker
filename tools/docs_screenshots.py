#!/usr/bin/env python3
"""Capture documentation images from fictional scores in a temporary database.

Run in a separate PID namespace (see docs/technical/contributing.md). Requires the
existing optional Playwright environment; never connects to OBS or a camera.
"""

import argparse
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mkw_tracker.server import Application, make_server  # noqa: E402


def review_example(app, ids, team_id):
    """Fictional reader output, not an OCR benchmark or real gameplay evidence."""
    app.store.update_player(ids[0], 'AApple', team_id)
    race = app.store.create_race('example-name-review', 'Mario Bros. Circuit')
    players = {player['id']: player['name'] for player in app.store.snapshot()['players']}
    return {
        'id': 'example-name-review', 'race_id': race['id'], 'course': race['course'],
        'rows': [{'position': index + 1, 'name': 'A Apple' if index == 0 else players[player_id],
                  'confidence_available': False} for index, player_id in enumerate(ids)],
        'reasons': ['Fictional example: select the existing AApple identity instead of creating a duplicate.'],
    }


def ww_example(app):
    """Two manually entered fictional sessions for cross-session course statistics."""
    app.switch_tracker_mode('ww', app.store.active_session()['id'])
    for session_index, name in enumerate(('Example practice', 'Example Worldwides')):
        app.store.create_session(name, app.store.active_session()['id'])
        app.store.set_player_name('Comet', app.store.active_session()['id'])
        before = 8000 + session_index * 100
        for index, (course, position, change) in enumerate((
                ('Boo Cinema', 2, 34), ('Crown City', 8, -12),
                ('Koopa Troopa Beach', 5, 18), ('Boo Cinema', 3, 27),
                ('Crown City', 7, -8), ('Koopa Troopa Beach', 4, 23))):
            race_id = f'ww-example-{session_index}-{index}'
            app.store.create_race(race_id, course)
            app.store.set_ww_result(race_id, position=position, vr_before=before,
                                    vr_after=before + change, delta=change)
            before += change
    app.update_settings({'overlay': {'title': 'Example Worldwides', 'height': 760,
                                     'scroll': False, 'ww_show_character': False}})


def main(argv=None):
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--only-ww', action='store_true', help='Generate only fictional WW screenshots.')
    selection.add_argument('--only-review', action='store_true', help='Generate only the fictional name-review example.')
    args = parser.parse_args(argv or [])
    output = ROOT / 'docs' / 'images'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mkw-docs-') as temporary:
        directory = Path(temporary)
        app = Application(directory / 'scores.sqlite', directory)
        server = None
        worker = None
        started = False
        try:
            folder = app.store.active_session()['folder_id']
            app.store.update_folder(folder, name='Example race night')
            names = [('Comet', 'Nova', 'Orion'), ('Aurora', 'Luna', 'Sol'),
                     ('Tide', 'Coral', 'Pearl'), ('Grove', 'Fern', 'Moss'),
                     ('Ember', 'Ash', 'Flint'), ('Cloud', 'Rain', 'Sky')]
            ids = []
            teams = []
            for team_name, first, second in names:
                team = app.store.create_team(team_name)
                teams.append(team['id'])
                for name in (first, second):
                    player = app.store.add_player(name)
                    app.store.update_player(player['id'], name, team['id'])
                    ids.append(player['id'])
            for index, order in enumerate((range(12), [4, 0, 7, 3, 9, 1, 5, 10, 2, 6, 11, 8],
                                           [2, 6, 1, 8, 0, 4, 11, 3, 7, 5, 9, 10])):
                race_id = f'example-{index + 1}'
                app.store.create_race(race_id, ['Mario Bros. Circuit', 'Whistlestop Summit', 'Rainbow Road'][index])
                app.store.submit_result(race_id, [ids[i] for i in order])
            app.update_settings({'overlay': {'title': 'Example race night', 'highlight_team_id': teams[0], 'height': 1100, 'scroll': False}})
            server = make_server(app, 0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            started = True
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page(viewport={'width': 1920, 'height': 1200}, device_scale_factor=1)
                    page.emulate_media(reduced_motion='reduce')
                    base = f'http://127.0.0.1:{server.server_address[1]}'
                    if not args.only_review and not args.only_ww:
                        page.goto(base)
                        expect(page.get_by_role('tab', name='Standings', exact=True)).to_be_visible()
                        expect(page.locator('#character-portrait')).to_be_hidden()
                        page.locator('#auto-refresh').uncheck()
                        # No machine paths, capture settings, real names, or runtime themes appear.
                        page.screenshot(path=str(output / 'standings.png'))
                        page.set_viewport_size({'width': 800, 'height': 1120})
                        page.goto(base + '/overlay')
                        expect(page.locator('.standing-card')).to_have_count(6)
                        expect(page.locator('.overlay-character')).to_have_count(0)
                        page.locator('#overlay').screenshot(path=str(output / 'overlay.png'), omit_background=True)

                    if not args.only_ww:
                        with app.lock:
                            candidate = review_example(app, ids, teams[0])
                        with patch.object(app.capture, 'snapshot', return_value={
                                'status': 'manual', 'message': 'Fictional documentation example', 'pending': [candidate]}):
                            page.set_viewport_size({'width': 1440, 'height': 1000})
                            page.goto(base)
                            page.locator('#auto-refresh').uncheck()
                            page.get_by_role('tab', name='Capture & review', exact=True).click()
                            row = page.locator('#capture-reviews .capture-placement').first
                            expect(row).to_contain_text('OCR: A Apple')
                            expect(row.get_by_label('Player identity')).to_have_value('')
                            row.get_by_label('Player identity').select_option(ids[0])
                            expect(row.get_by_label('Player identity')).to_have_value(ids[0])
                            expect(row.get_by_label('Player identity').locator('option:checked')).to_have_text('AApple')
                            expect(row.get_by_label('Name for new player')).to_have_value('A Apple')
                            row.screenshot(path=str(output / 'name-review.png'))
                            assert not next(race for race in app.store.snapshot()['races']
                                            if race['id'] == candidate['race_id'])['revision']

                    if not args.only_review:
                        with app.lock:
                            ww_example(app)
                        page.set_viewport_size({'width': 1440, 'height': 1200})
                        page.goto(base)
                        page.locator('#auto-refresh').uncheck()
                        page.get_by_role('tab', name='Standings', exact=True).click()
                        expect(page.locator('#ww-section')).to_be_visible()
                        page.locator('#ww-stats-scope').select_option('all')
                        expect(page.locator('#ww-courses tbody tr')).to_have_count(3)
                        expect(page.locator('#ww-courses tbody tr').first).to_contain_text('Boo Cinema')
                        page.locator('#ww-section').screenshot(path=str(output / 'ww-standings.png'))
                        page.set_viewport_size({'width': 800, 'height': 1120})
                        page.goto(base + '/overlay')
                        expect(page.locator('.ww-overlay-player')).to_have_text('Comet')
                        expect(page.locator('.overlay-character')).to_have_count(0)
                        expect(page.locator('.ww-current_vr')).to_contain_text('8182')
                        page.locator('#overlay').screenshot(path=str(output / 'ww-overlay.png'), omit_background=True)
                    print('Captured fictional documentation images: docs/images/')

                finally:
                    browser.close()
        finally:
            try:
                if server is not None:
                    try:
                        if started:
                            server.shutdown()
                    finally:
                        server.server_close()
            finally:
                try:
                    if started:
                        worker.join(timeout=5)
                finally:
                    app.close()


if __name__ == '__main__':
    main(sys.argv[1:])
