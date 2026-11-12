"""Optional Chromium overlay acceptance, using temporary scores and real HTTP API.

PLAYWRIGHT_BROWSERS_PATH=.local/browsers .local/browser-venv/bin/python -m tests.browser_overlay
"""

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
        app.update_settings({'overlay': {'title': 'Mogi night · 2v2'}})
        ids = []
        team_ids = []
        for team_index in range(6):
            team = app.store.create_team(f'Team {team_index + 1}')
            team_ids.append(team['id'])
            for member_index in range(2):
                name = f'選手☆ {team_index + 1}-{member_index + 1}'
                player = app.store.add_player(name)
                app.store.update_player(player['id'], name, team['id'])
                ids.append(player['id'])
        app.store.create_race('first', 'Rainbow Road')
        app.store.submit_result('first', ids)
        server = make_server(app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 800, 'height': 700})
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(base + '/overlay')
                expect(page.locator('.standing-card')).to_have_count(6)
                expect(page.locator('.standing-member')).to_have_count(12)
                expect(page.locator('.rank-movement')).to_have_count(0)
                expect(page.locator('.standing-card').first.locator('.standing-gap')).to_have_count(0)
                expect(page.locator('.standing-card').nth(1).locator('.standing-gap')).to_have_text('-8 / -8')
                expect(page.locator('.standing-card').nth(2).locator('.standing-gap')).to_have_text('-4 / -12')
                expect(page.locator('.overlay-character')).to_have_count(0)
                expect(page.locator('.standing-score').first).to_contain_text('27')
                expect(page.locator('.member-name').first).to_have_text('選手☆ 1-1')
                assert 'Racers Delight' not in page.locator('.member-name').first.evaluate('(node) => getComputedStyle(node).fontFamily')
                assert 'Racers Delight' not in page.locator('.standing-score').first.evaluate('(node) => getComputedStyle(node).fontFamily')
                response = page.request.patch(base + '/api/teams/' + team_ids[0], data={'color': '#12abef'})
                assert response.ok, response.text()
                response = page.request.patch(base + '/api/settings', data={
                    'overlay': {'highlight_team_id': team_ids[0], 'alternating_backgrounds': True, 'opacity': .65}})
                assert response.ok, response.text()
                expect(page.locator('.highlighted-team')).to_have_count(1, timeout=5000)
                expect(page.locator('.highlight-badge')).to_have_text('★')
                expect(page.locator('.highlight-badge')).to_have_attribute('aria-label', 'Highlighted team')
                assert 'Highlighted team' not in page.locator('#standings').inner_text()
                expect(page.locator('.standing-card').first).to_have_css('border-left-color', 'rgb(18, 171, 239)')
                backgrounds = page.locator('.standing-card').evaluate_all('(rows) => rows.slice(0,2).map(row => getComputedStyle(row).backgroundColor)')
                assert backgrounds[0] != backgrounds[1], backgrounds
                highlighted = page.locator('.highlighted-team')
                assert highlighted.evaluate('(node) => getComputedStyle(node).backgroundColor') == highlighted.evaluate('(node) => node.style.getPropertyValue("--card-highlight")')
                assert highlighted.evaluate('(node) => node.style.getPropertyValue("--card-highlight")') not in page.locator('#overlay').evaluate('(node) => [node.style.getPropertyValue("--card-brighter"), node.style.getPropertyValue("--card-darker")]')
                assert all(color.endswith(', 0.65)') for color in backgrounds), backgrounds
                page.reload()
                expect(page.locator('.highlighted-team')).to_have_count(1)

                evidence = Path('.omx/evidence')
                evidence.mkdir(parents=True, exist_ok=True)
                screenshot = page.screenshot(path=str(evidence / 'themed-overlay.png'), omit_background=True)
                image = Image.open(BytesIO(screenshot)).convert('RGBA')
                assert image.getpixel((0, 0))[3] == 0
                assert image.getpixel((790, 690))[3] == 0
                # Identical polls keep DOM nodes and scroll position rather than flickering.
                page.locator('.standing-card').first.evaluate('(node) => node.dataset.kept = "yes"')
                page.wait_for_timeout(1700)
                assert page.locator('.standing-card').first.get_attribute('data-kept') == 'yes'
                # Whole-scoreboard controls preserve the current DOM and scroll.
                response = page.request.patch(base + '/api/settings', data={
                    'overlay': {'slide_duration': 600}})
                assert response.ok, response.text()
                expect(page.locator('#overlay')).to_have_css('transition-duration', '0.6s, 0s', timeout=5000)
                assert page.locator('.standing-card').first.get_attribute('data-kept') == 'yes'
                response = page.request.post(base + '/api/overlay/hide', data={})
                assert response.ok, response.text()
                expect(page.locator('#overlay')).to_have_attribute('aria-hidden', 'true', timeout=5000)
                expect(page.locator('#overlay')).to_have_css('visibility', 'hidden')
                assert page.locator('#overlay').evaluate('(node) => node.getBoundingClientRect().right <= 0')
                assert page.locator('.standing-card').first.get_attribute('data-kept') == 'yes'
                hidden_scroll = page.locator('#standings-viewport').evaluate('(node) => node.scrollTop')
                page.wait_for_timeout(1700)
                assert page.locator('#standings-viewport').evaluate('(node) => node.scrollTop') == hidden_scroll
                response = page.request.post(base + '/api/overlay/show', data={})
                assert response.ok, response.text()
                expect(page.locator('#overlay')).to_have_attribute('aria-hidden', 'false', timeout=5000)
                expect(page.locator('#overlay')).to_have_css('transform', 'none')
                assert page.locator('.standing-card').first.get_attribute('data-kept') == 'yes'
                response = page.request.patch(base + '/api/settings', data={
                    'overlay': {'slide_direction': 'right', 'slide_duration': 0}})
                assert response.ok, response.text()
                page.wait_for_function('() => document.querySelector("#overlay").classList.contains("slide-right")')
                response = page.request.post(base + '/api/overlay/toggle', data={})
                assert response.ok, response.text()
                expect(page.locator('#overlay')).to_have_css('visibility', 'hidden', timeout=5000)
                assert page.locator('#overlay').evaluate('(node) => node.getBoundingClientRect().left >= innerWidth')
                # Reload while hidden, including a delayed/unavailable first poll,
                # never exposes a visible scoreboard before the current state arrives.
                page.route('**/api/overlay', lambda route: route.fulfill(status=503, body='retry'))
                page.reload()
                expect(page.locator('#overlay')).to_have_css('visibility', 'hidden')
                page.unroute('**/api/overlay')
                expect(page.locator('#overlay')).to_have_attribute('aria-hidden', 'true', timeout=5000)
                expect(page.locator('#overlay')).to_have_css('visibility', 'hidden')
                response = page.request.patch(base + '/api/settings', data={
                    'overlay': {'slide_duration': 5000}})
                assert response.ok, response.text()
                expect(page.locator('#overlay')).to_have_css('transition-duration', '5s, 0s', timeout=5000)
                page.emulate_media(reduced_motion='reduce')
                response = page.request.post(base + '/api/overlay/show', data={})
                assert response.ok, response.text()
                expect(page.locator('#overlay')).to_have_attribute('aria-hidden', 'false', timeout=5000)
                expect(page.locator('#overlay')).to_have_css('transition-duration', '0s')
                expect(page.locator('#overlay')).to_have_css('transform', 'none')
                page.emulate_media(reduced_motion='no-preference')
                response = page.request.patch(base + '/api/settings', data={
                    'overlay': {'slide_direction': 'left', 'slide_duration': 400}})
                assert response.ok, response.text()
                # Builtin palette changes never invent or request character artwork.
                response = page.request.patch(base + '/api/settings', data={'theme': 'cow'})
                assert response.ok, response.text()
                expect(page.locator('.overlay-character')).to_have_count(0, timeout=5000)
                expect(page.locator('.overlay-title')).to_have_text('Mogi night · 2v2')
                screenshot = page.screenshot(path=str(evidence / 'themed-overlay-cow.png'), omit_background=True)
                image = Image.open(BytesIO(screenshot)).convert('RGBA')
                assert image.getpixel((0, 0))[3] == 0
                response = page.request.patch(base + '/api/settings', data={'theme': 'luigi'})
                assert response.ok, response.text()
                expect(page.locator('.overlay-character')).to_have_count(0, timeout=5000)
                app.store.create_race('second', 'Peach Stadium')
                app.store.submit_result('second', list(reversed(ids)))
                expect(page.locator('.standing-score').first).to_contain_text('30', timeout=5000)
                expect(page.locator('.rank-movement.rank-up')).to_have_count(3)
                expect(page.locator('.rank-movement.rank-down')).to_have_count(1)
                expect(page.get_by_role('img', name='Up 5 places since the last race', exact=True)).to_have_text('▲5')
                expect(page.get_by_role('img', name='Down 1 place since the last race', exact=True)).to_have_text('▼1')
                expect(page.locator('.rank-up').first).to_have_css('color', 'rgb(103, 239, 158)')
                expect(page.locator('.rank-down').first).to_have_css('color', 'rgb(255, 135, 152)')
                expect(page.locator('.standing-card').nth(1).locator('.standing-gap')).to_have_text('0 / 0')
                expect(page.locator('.standing-card').nth(2).locator('.standing-gap')).to_have_text('-4 / -4')
                expect(page.locator('.standing-card').nth(3).locator('.standing-gap')).to_have_text('0 / -4')
                # Movement derives from the latest race, not the last browser poll.
                page.locator('.rank-movement').first.evaluate('(node) => node.dataset.kept = "yes"')
                page.wait_for_timeout(1700)
                assert page.locator('.rank-movement').first.get_attribute('data-kept') == 'yes'
                page.reload()
                expect(page.get_by_role('img', name='Up 5 places since the last race', exact=True)).to_be_visible()
                page.screenshot(path=str(evidence / 'themed-overlay-movement.png'), omit_background=True)
                response = page.request.patch(base + '/api/settings', data={'overlay': {'show_gaps': False}})
                assert response.ok, response.text()
                expect(page.locator('.standing-gap')).to_have_count(0, timeout=5000)
                expect(page.locator('.rank-movement')).to_have_count(4)
                response = page.request.patch(base + '/api/settings', data={'overlay': {'show_gaps': True}})
                assert response.ok, response.text()
                expect(page.locator('.standing-gap')).to_have_count(5, timeout=5000)

                # The largest font setting must still fit the narrowest OBS source.
                response = page.request.patch(base + '/api/settings', data={
                    'overlay': {'width': 240, 'font_size': 48},
                })
                assert response.ok, response.text()
                expect(page.locator('#overlay')).to_have_css('width', '240px', timeout=5000)
                assert page.locator('.standing-card').evaluate_all('(cards) => cards.every(card => card.scrollWidth <= card.clientWidth)')
                expect(page.locator('.standing-gap')).to_have_count(5)
                expect(page.locator('.rank-movement')).to_have_count(4)
                response = page.request.patch(base + '/api/settings', data={
                    'overlay': {'width': 560, 'font_size': 48},
                })
                assert response.ok, response.text()
                expect(page.locator('#overlay')).to_have_css('font-size', '48px', timeout=5000)

                # Settings and themes are saved through the same endpoint as the dashboard.
                response = page.request.patch(base + '/api/settings', data={
                    'overlay': {'height': 240, 'scroll_speed': 120, 'scroll': True,
                                'font_size': 24, 'title': '<script>選手</script>'},
                })
                assert response.ok, response.text()
                expect(page.locator('.overlay-title')).to_have_text('<script>選手</script>', timeout=5000)
                assert page.locator('#overlay script').count() == 0
                page.wait_for_function('() => document.querySelector("#standings-viewport").scrollTop > 5', timeout=7000)
                page.emulate_media(reduced_motion='reduce')
                page.wait_for_function('() => !document.querySelector("#overlay").classList.contains("animate")')
                page.wait_for_function('() => document.querySelector("#standings-viewport").scrollTop === 0')
                response = page.request.patch(base + '/api/settings', data={
                    'overlay': {'layout': 'players', 'show_header': False, 'show_players': False,
                                'scroll': False},
                })
                assert response.ok, response.text()
                expect(page.locator('.standing-card')).to_have_count(12, timeout=5000)
                expect(page.locator('.rank-movement')).to_have_count(0)
                expect(page.locator('.standing-card').nth(1).locator('.standing-gap')).to_contain_text(' / ')
                expect(page.locator('.standing-member')).to_have_count(0)
                expect(page.locator('.standing-name.player-name')).to_have_count(12)
                assert 'Racers Delight' not in page.locator('.standing-name.player-name').first.evaluate('(node) => getComputedStyle(node).fontFamily')
                expect(page.locator('.highlighted-team')).to_have_count(2)
                response = page.request.patch(base + '/api/settings', data={'overlay': {'alternating_backgrounds': False, 'highlight_team_id': None}})
                assert response.ok, response.text()
                expect(page.locator('.highlighted-team')).to_have_count(0, timeout=5000)
                expect(page.locator('.row-brighter, .row-darker')).to_have_count(0)

                expect(page.locator('#overlay-header')).to_be_hidden()
                expect(page.locator('.overlay-character')).to_have_count(0)
                assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgba(0, 0, 0, 0)'
                page.screenshot(path=str(evidence / 'themed-overlay-players.png'), omit_background=True)
                # A failed poll retains the last successful data and recovers on its own.
                page.route('**/api/overlay', lambda route: route.fulfill(status=503, body='temporarily unavailable'))
                expect(page.locator('#connection-status')).to_contain_text('showing last update', timeout=5000)
                expect(page.locator('.standing-card')).to_have_count(12)
                page.unroute('**/api/overlay')
                expect(page.locator('#connection-status')).to_be_hidden(timeout=5000)
                assert not errors, errors
                browser.close()
                print('PASS: transparent OBS overlay, real scores, race-based movement, dual gaps, highlight, whole-scoreboard left/right controls, hidden reload, unchanged DOM, live settings, Unicode, overflow scrolling, reduced motion and failure recovery.')
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            app.close()


if __name__ == '__main__':
    main()
