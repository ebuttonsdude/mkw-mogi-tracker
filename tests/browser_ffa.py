"""Optional Chromium Mogi FFA acceptance with temporary data, no live hardware.

PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python -m tests.browser_ffa
"""
from pathlib import Path
import tempfile
import threading
from unittest.mock import Mock, patch

from mkw_tracker.server import Application, make_server


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        app = Application(directory / 'scores.sqlite', directory)
        team = app.store.create_team('Existing team')
        team_session = app.store.active_session()['id']
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
                page.locator('#auto-refresh').uncheck()

                def switch_mode(mode):
                    # First use may initialize the mode's store and outputs. Wait
                    # for that mutation separately from the UI rendering assertion.
                    try:
                        with page.expect_response(lambda response: response.url == base + '/api/tracker-mode'
                                                  and response.request.method == 'POST') as pending:
                            page.locator('#tracker-mode').select_option(mode)
                        response = pending.value
                        assert response.status == 200, (mode, response.status, response.text())
                        expect(page.locator('body')).to_have_attribute('data-tracker-mode', mode)
                    except Exception:
                        print('Mode transition diagnostics:', mode, page.locator('#message').inner_text(),
                              page.evaluate('() => ({mode: state?.tracker_mode, refreshing, '
                                            'selected: document.querySelector("#tracker-mode").value})'))
                        raise

                # Simulate an owned, active CLI capture without starting hardware.
                with patch.object(app.capture, 'thread', Mock(is_alive=lambda: True)):
                    page.locator('#refresh').click()
                    blocked = page.locator('#blocked-tracker-mode')
                    expect(blocked).to_be_visible()
                    blocked.click(position={'x': 6, 'y': 6})
                    warning = page.locator('#mode-switch-warning')
                    expect(warning).to_have_text('Stop capture before switching tracker modes.')
                    expect(warning).to_be_visible()
                    button_box, notice_box = blocked.bounding_box(), warning.bounding_box()
                    assert abs(notice_box['x'] - button_box['x']) <= notice_box['width']
                    assert abs(notice_box['y'] - button_box['y']) < button_box['height']
                    expect(page.locator('body')).to_have_attribute('data-tracker-mode', 'mogi')
                    page.locator('h1').click()
                    expect(warning).to_be_hidden()
                    blocked.focus()
                    blocked.press('Enter')
                    expect(warning).to_be_visible()
                    blocked.press('Escape')
                    expect(warning).to_be_hidden()
                page.locator('#refresh').click()
                expect(page.locator('#tracker-mode')).to_be_enabled()
                switch_mode('ffa')
                page.locator('#tab-standings').click()
                expect(page.locator('#team-section')).to_be_hidden()
                expect(page.locator('#player-section')).to_be_visible()
                expect(page.locator('#player-help')).not_to_contain_text('Assign teams')
                with app.lock:
                    ffa_session = app.store.active_session()['id']
                    assert ffa_session != team_session
                    names = ['日本語', 'Élodie', 'Zeke'] + [f'Player {i}' for i in range(4, 13)]
                    players = [app.store.add_player(name)['id'] for name in names]
                    app.store.create_race('ffa-first', 'Rainbow Road')
                    app.store.submit_result('ffa-first', players)
                page.locator('#refresh').click()
                expect(page.locator('#players tbody tr')).to_have_count(12)
                expect(page.locator('#players select')).to_have_count(0)
                assert 'Team' not in page.locator('#players th').all_text_contents()
                expect(page.locator('#ffa-summary')).to_contain_text('82 / 984')
                # Server tie ordering is casefold-based, not browser locale collation.
                # Zeke sorts before Élodie; the first tied row owns the gap above.
                with app.lock:
                    tie_penalty = app.store.add_penalty('ffa-first', players[1], 2)
                page.locator('#refresh').click()
                rows = page.locator('#players tbody tr')
                expect(rows.locator('td.player-name')).to_have_text(
                    ['日本語', 'Zeke', 'Élodie'] + names[3:])
                expect(rows.locator('td.player-number')).to_have_text(
                    ['1', '2', '2'] + [str(i) for i in range(4, 13)])
                expect(rows.nth(1).locator('td').nth(5)).to_have_text('-5 / -5')
                expect(rows.nth(2).locator('td').nth(5)).to_have_text('0 / -5')
                with app.lock:
                    leader_penalty = app.store.add_penalty('ffa-first', players[0], 5)
                page.locator('#refresh').click()
                expect(rows.locator('td.player-name')).to_have_text(
                    ['Zeke', 'Élodie', '日本語'] + names[3:])
                expect(rows.locator('td.player-number')).to_have_text(
                    ['1', '1', '1'] + [str(i) for i in range(4, 13)])
                expect(rows.nth(0).locator('td').nth(5)).to_have_text('Leader')
                expect(rows.nth(1).locator('td').nth(5)).to_have_text('0 / 0')
                expect(rows.nth(2).locator('td').nth(5)).to_have_text('0 / 0')
                with app.lock:
                    app.store.remove_penalty(tie_penalty['id'])
                    app.store.remove_penalty(leader_penalty['id'])
                page.locator('#refresh').click()
                page.locator('#tab-overlays').click()
                expect(page.locator('#overlay-highlight-team')).to_be_hidden()
                expect(page.locator('#overlay-settings [name=layout]')).to_be_hidden()
                expect(page.locator('#overlay-settings [name=show_players]')).to_be_hidden()
                page.locator('#overlay-highlight-player').select_option(players[0])
                page.locator('#overlay-settings button').click()
                expect(page.locator('#message')).to_contain_text('overlay settings saved')
                overlay = browser.new_page()
                overlay.goto(base + '/overlay')
                expect(overlay.locator('.standing-card')).to_have_count(12)
                expect(overlay.locator('.standing-members')).to_have_count(0)
                expect(overlay.locator('.standing-subtitle')).to_have_count(0)
                expect(overlay.locator('[aria-label="Highlighted player"]')).to_have_count(1)
                expect(overlay.locator('.rank-movement')).to_have_count(0)
                with app.lock:
                    app.store.create_race('ffa-second', 'Crown City')
                    app.store.submit_result('ffa-second', list(reversed(players)))
                expect(overlay.locator('.rank-movement').first).to_be_visible(timeout=5000)
                page.locator('#tab-history').click()
                page.locator('#history').get_by_role('button', name='View folder PNG').click()
                expect(page.locator('#folder-png-team')).to_be_hidden()
                expect(page.locator('#folder-png-player')).to_be_visible()
                expect(page.locator('#folder-png-image')).to_be_visible(timeout=15000)
                page.locator('#folder-png-player').select_option(players[1])
                expect(page.locator('#folder-png-image')).to_be_visible(timeout=15000)
                page.locator('#folder-png-close').click()
                page.set_viewport_size({'width': 390, 'height': 844})
                page.locator('#tab-standings').click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                switch_mode('mogi')
                with app.lock:
                    assert app.store.active_session()['id'] == team_session
                    assert app.store.get_team(team['id'])['name'] == 'Existing team'
                switch_mode('ww')
                switch_mode('ffa')
                page.reload()
                expect(page.locator('#tracker-mode')).to_have_value('ffa')
                with app.lock:
                    assert app.store.active_session()['id'] == ffa_session
                    assert len(app.store.snapshot()['players']) == 12
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            app.close()
        restarted = Application(directory / 'scores.sqlite', directory)
        try:
            assert restarted.tracker_mode == 'ffa'
            assert restarted.store.active_session()['id'] == ffa_session
        finally:
            restarted.close()
    print('PASS: Mogi FFA mode isolation, team-free roster/settings, Unicode player overlay/highlight/movement, '
          'Unicode tie order/ranks/gaps, folder PNG player preview, mobile layout and restart persistence.')


if __name__ == '__main__':
    main()
