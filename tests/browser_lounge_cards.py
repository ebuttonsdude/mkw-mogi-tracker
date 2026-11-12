"""Optional Chromium behavior checks for compact broadcast Lounge cards.

Use a verified separate PID namespace/private proc. All responses are synthetic;
no Lounge requests, real databases, capture devices or clipboard access.
"""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
import threading

from mkw_tracker.server import Application, make_server


METRICS = ('username', 'division', 'mmr', 'leaderboard_rank', 'peak_mmr', 'events',
           'positive_mmr_rate', 'average_score', 'last_ten_record',
           'last_ten_mmr_change', 'last_ten_average_score', 'weekly_events',
           'weekly_positive_mmr_rate', 'weekly_event_mmr_change', 'weekly_average_score', 'last_five_form')


def sample():
    data = {'player_id': 123, 'name': 'Example', 'season': 3, 'game': 'mkworld12p',
            'room_size': 12, 'division': 'Bronze', 'mmr': 2727, 'leaderboard_rank': 4819,
            'peak_mmr': 3739, 'events': 53, 'positive_mmr_rate': .528,
            'average_score': 78.6, 'last_ten_mmr_change': -579,
            'last_ten_average_score': 78.3, 'last_ten_wins': 3, 'last_ten_losses': 7,
            'last_five_form': ['W', 'L', 'L', 'W', 'W'],
            'fetched_at': '2026-11-09T05:35:32+00:00',
            'weekly': {'events': 14, 'positive_mmr_rate': .357,
                       'event_mmr_change': -547, 'average_score': 81.79,
                       'from': '2026-11-02T05:35:32+00:00',
                       'to': '2026-11-09T05:35:32+00:00'}}
    return {'tracker_mode': 'mogi', 'visible': True,
            'settings': {'width': 320, 'height': 900, 'font_size': 22,
                         'show_header': False, 'scroll': False, 'animate': False},
            'content': {'header': False, 'standings': False, 'lounge': True},
            'theme': {'tokens': {'panel': '#101827', 'text': '#ffffff',
                                 'muted': '#c5cbd5', 'accent': '#a0abff'}},
            'lounge': {'enabled': True,
                       'appearance': {'background': 'aurora', 'border': 'rainbow', 'shape': 'pill'},
                       'metrics': {key: key in ('username', 'division', 'mmr') for key in METRICS},
                       'players': [{'username': 'Example', 'status': 'ready', 'data': data}]}}


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = Application(root / 'scores.sqlite', root)
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 700, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                current = sample()
                failure = False

                def respond(route):
                    if failure:
                        route.fulfill(status=503, body='unavailable')
                    else:
                        route.fulfill(status=200, content_type='application/json', body=json.dumps(current))

                page.route('**/api/overlay', respond)
                url = f'http://127.0.0.1:{server.server_address[1]}/overlay'
                card = page.locator('.lounge-card').first

                def show(value, expected_cards=None):
                    nonlocal current
                    current = deepcopy(value)
                    page.goto(url)
                    expect(page.locator('#overlay')).to_have_class(re.compile(r'\bready\b'))
                    expect(page.locator('.lounge-card')).to_have_count(
                        len(current['lounge']['players']) if expected_cards is None else expected_cards)

                def metric(key):
                    return card.locator(f'.lounge-metric[data-metric="{key}"]')

                def no_overflow():
                    assert card.evaluate('''(node) => {
                        const box = node.getBoundingClientRect();
                        return [...node.querySelectorAll('*')].every(child => {
                            const bounds = child.getBoundingClientRect();
                            return bounds.left >= box.left - 1 && bounds.right <= box.right + 1;
                        });
                    }'''), card.evaluate('(node) => ({client: node.clientWidth, scroll: node.scrollWidth, width: node.getBoundingClientRect().width})')
                    assert card.evaluate('(node) => node.getBoundingClientRect().right <= innerWidth + 1')

                # Three chosen fields form a compact hero, not a report table.
                compact = sample()
                show(compact)
                expect(card.locator('.lounge-name')).to_have_text('Example')
                expect(card.locator('.lounge-mmr')).to_contain_text('2727')
                expect(card.locator('.lounge-division')).to_have_text(re.compile('Bronze', re.I))
                expect(card.locator('.lounge-metric')).to_have_count(0)
                for key, value in compact['lounge']['appearance'].items():
                    expect(card).to_have_attribute(f'data-{key}', value)
                compact_height = card.bounding_box()['height']
                assert compact_height < 150, compact_height
                rating_box = card.locator('.lounge-rating').bounding_box()
                card_box = card.bounding_box()
                assert card_box['x'] + card_box['width'] - rating_box['x'] - rating_box['width'] < 18
                no_overflow()
                screenshots = Path('.local')
                screenshots.mkdir(exist_ok=True)
                card.screenshot(path=str(screenshots / 'lounge-cards-compact.png'), omit_background=True)

                # Mixed windows share one line with identity/rating when there is room.
                dense = sample()
                dense['settings'].update(width=1000, height=200)
                dense['lounge']['players'][0]['data']['name'] = 'ExamplePlayer'
                dense['lounge']['metrics'].update(peak_mmr=True, events=True, positive_mmr_rate=True,
                                                  average_score=True, last_ten_record=True)
                page.set_viewport_size({'width': 1100, 'height': 1000})
                show(dense)
                expect(card.locator('.lounge-window')).to_have_count(0)
                boxes = [card.locator('.lounge-name').bounding_box(),
                         card.locator('.lounge-rating').bounding_box(),
                         *[item.bounding_box() for item in card.locator('.lounge-metric').all()]]
                assert max(box['y'] + box['height'] / 2 for box in boxes) - min(
                    box['y'] + box['height'] / 2 for box in boxes) < 3, boxes
                assert card.bounding_box()['height'] < 90
                assert metric('peak_mmr').bounding_box()['x'] > card.locator('.lounge-name').bounding_box()['x']
                assert metric('last_ten_record').bounding_box()['x'] < card.locator('.lounge-rating').bounding_box()['x']
                assert max(item.bounding_box()['width'] for item in card.locator('.lounge-metric').all()) < 165
                no_overflow()
                card.screenshot(path=str(screenshots / 'lounge-cards-inline.png'), omit_background=True)
                # More width is distributed among the existing bubbles, not fixed columns.
                smaller = deepcopy(dense)
                smaller['settings']['width'] = 900
                show(smaller)
                smaller_width = metric('peak_mmr').bounding_box()['width']
                show(dense)
                assert metric('peak_mmr').bounding_box()['width'] > smaller_width
                page.set_viewport_size({'width': 700, 'height': 1000})

                # Additional selected stats increase card height and retain honest scopes.
                expanded = sample()
                expanded['settings']['width'] = 460
                expanded['lounge']['metrics'] = dict.fromkeys(METRICS, True)
                show(expanded)
                assert card.bounding_box()['height'] > compact_height
                expect(metric('last_ten_record')).to_contain_text('30%')
                expect(metric('last_ten_record')).to_contain_text(re.compile(r'Win\s*%', re.I))
                expect(metric('last_ten_record').locator('.lounge-metric-scope')).to_have_text('L10')
                expect(metric('last_ten_record')).to_have_attribute('aria-label', re.compile(r'10.*events', re.I))
                expect(metric('last_ten_mmr_change')).to_contain_text('579')
                expect(metric('last_ten_mmr_change')).to_contain_text(re.compile('[▼↓↘]'))
                visible_text = card.inner_text()
                for diagnostic in ('2026-', 'Updated', 'verification times', 'Lounge #123', '3 / 7'):
                    assert diagnostic not in visible_text, (diagnostic, visible_text)
                assert 'races' not in metric('last_ten_record').inner_text().lower()
                no_overflow()
                card.screenshot(path=str(screenshots / 'lounge-cards-expanded.png'), omit_background=True)

                # Form carries literal outcomes as well as color, oldest to newest.
                expect(metric('last_five_form').locator('.lounge-form-mark')).to_have_text(['W', 'L', 'L', 'W', 'W'])
                expect(metric('last_five_form').locator('.lounge-form-mark').last).to_have_attribute('aria-label', 'Win, newest event')
                win_color = metric('last_five_form').locator('.lounge-form-win').first.evaluate('(node) => getComputedStyle(node).backgroundColor')
                loss_color = metric('last_five_form').locator('.lounge-form-loss').first.evaluate('(node) => getComputedStyle(node).backgroundColor')
                assert win_color != loss_color
                for form, expected in ((None, 'Unknown'), ([], 'No events'), (['W', 'X'], 'Unknown')):
                    missing_form = deepcopy(expanded)
                    missing_form['lounge']['players'][0]['data']['last_five_form'] = form
                    show(missing_form)
                    expect(metric('last_five_form')).to_contain_text(expected)
                    expect(metric('last_five_form').locator('.lounge-form-mark')).to_have_count(0)
                old_profile = deepcopy(compact)
                del old_profile['lounge']['metrics']['last_five_form']
                show(old_profile)
                expect(metric('last_five_form')).to_have_count(0)

                # Every switch removes its field, including identity-only configurations.
                selectors = {'username': '.lounge-name', 'mmr': '.lounge-mmr', 'division': '.lounge-division'}
                for key in METRICS:
                    selected = deepcopy(expanded)
                    selected['lounge']['metrics'][key] = False
                    show(selected)
                    locator = card.locator(selectors[key]) if key in selectors else metric(key)
                    expect(locator).to_have_count(0)

                hidden = deepcopy(compact)
                hidden['lounge']['metrics'] = dict.fromkeys(METRICS, False)
                show(hidden, expected_cards=0)

                # Legacy recent aggregates combine room sizes even without season tiles.
                legacy = deepcopy(expanded)
                legacy['lounge']['players'][0]['data'].update(game='mkworld', season=1)
                legacy['lounge']['metrics'] = {
                    key: key in ('last_ten_record', 'last_ten_mmr_change', 'last_ten_average_score')
                    for key in METRICS}
                show(legacy)
                expect(card.locator('.lounge-window')).to_have_count(0)
                expect(card.locator('.lounge-metric-scope')).to_have_text(['L10·ALL'] * 3)
                expect(metric('last_ten_record')).to_have_attribute('aria-label', re.compile(r'Last 10 events.*combined', re.I))
                expect(metric('last_ten_record')).to_contain_text('30%')
                expect(metric('events')).to_have_count(0)

                # Last-ten percentages divide by actual observations, not a fixed ten.
                fewer = deepcopy(expanded)
                data = fewer['lounge']['players'][0]['data']
                data.update(last_ten_wins=1, last_ten_losses=1)
                show(fewer)
                expect(metric('last_ten_record')).to_contain_text('50%')
                expect(metric('last_ten_record').locator('.lounge-metric-scope')).to_have_text('L2')
                data.update(last_ten_wins=0, last_ten_losses=0, weekly=None)
                show(fewer)
                expect(metric('last_ten_record')).to_contain_text(re.compile('Unknown|—', re.I))
                for key in ('weekly_events', 'weekly_positive_mmr_rate', 'weekly_event_mmr_change', 'weekly_average_score'):
                    expect(metric(key)).to_contain_text(re.compile('Unknown|—', re.I))

                # Direction is conveyed by a symbol as well as color, including a neutral zero.
                colors = []
                for change, symbol in ((-579, '[▼↓↘]'), (0, '[−—→↔]'), (579, '[▲↑↗]')):
                    delta = deepcopy(expanded)
                    delta['lounge']['players'][0]['data']['last_ten_mmr_change'] = change
                    show(delta)
                    expect(metric('last_ten_mmr_change')).to_contain_text(re.compile(symbol))
                    colors.append(metric('last_ten_mmr_change').locator('strong').evaluate('(node) => getComputedStyle(node).color'))
                assert len(set(colors)) == 3, colors

                # Appearance selections stay bounded and long Unicode names do not escape narrow sources.
                narrow = deepcopy(expanded)
                narrow['settings'].update(width=240, font_size=48)
                narrow['lounge']['players'][0]['data']['name'] = '名前 <img src=x onerror=alert(1)> ' + 'LongName' * 8
                for background, border, shape in (('carbon', 'division', 'rounded'), ('theme', 'accent', 'pill'), ('transparent', 'none', 'rounded')):
                    narrow['lounge']['appearance'] = {'background': background, 'border': border, 'shape': shape}
                    show(narrow)
                    for key, value in narrow['lounge']['appearance'].items():
                        expect(card).to_have_attribute(f'data-{key}', value)
                    expect(card.locator('.lounge-name')).to_have_text(narrow['lounge']['players'][0]['data']['name'])
                    expect(card.locator('img')).to_have_count(0)
                    no_overflow()

                # Counter housing is CSS-only and keeps mixed statistics compact.
                hud = deepcopy(dense)
                hud['lounge']['appearance'] = {'background': 'hud', 'border': 'hud', 'shape': 'rounded'}
                show(hud)
                expect(card).to_have_attribute('data-background', 'hud')
                expect(card).to_have_attribute('data-border', 'hud')
                assert 'linear-gradient' in card.evaluate('(node) => getComputedStyle(node).backgroundImage')
                assert card.evaluate('(node) => getComputedStyle(node, "::after").borderTopStyle') == 'solid'
                no_overflow()
                card.screenshot(path=str(screenshots / 'lounge-cards-hud.png'), omit_background=True)

                # Uploaded pixels are decorative, bounded to the card and use source opacity.
                from PIL import Image
                image = Image.new('RGB', (800, 200), '#46898b')
                buffer = BytesIO()
                image.save(buffer, format='PNG')
                image_url = '/api/overlay-profiles/default/background.png?v=0123456789abcdef'
                page.route('**' + image_url, lambda route: route.fulfill(content_type='image/png', body=buffer.getvalue()))
                custom = deepcopy(hud)
                custom['settings']['opacity'] = .6
                custom['lounge']['appearance'].update(background='custom', image_url=image_url)
                show(custom)
                expect(card).to_have_attribute('data-image-ready', 'true')
                expect(card.locator('img')).to_have_attribute('alt', '')
                expect(card.locator('img')).to_have_css('object-fit', 'cover')
                expect(card.locator('img')).to_have_css('opacity', '0.6')
                expect(card).to_have_css('background-color', 'rgba(0, 0, 0, 0)')
                no_overflow()
                card.screenshot(path=str(screenshots / 'lounge-cards-uploaded.png'), omit_background=True)
                # Malicious/non-owned URLs are never loaded; a missing image falls back.
                for bad_url in ('https://example.invalid/bg.png', 'data:image/svg+xml,<svg/>',
                                '/api/overlay-profiles/default/background.png?v=evil'):
                    custom['lounge']['appearance']['image_url'] = bad_url
                    show(custom)
                    expect(card.locator('img')).to_have_count(0)
                    assert card.evaluate('(node) => getComputedStyle(node).backgroundColor') != 'rgba(0, 0, 0, 0)'
                custom['lounge']['appearance']['image_url'] = image_url
                page.unroute('**' + image_url)
                page.route('**' + image_url, lambda route: route.fulfill(status=404, body='missing'))
                show(custom)
                expect(card.locator('img')).to_have_count(0)
                assert card.get_attribute('data-image-ready') is None
                # Large-font narrow HUD still wraps without widening the source.
                narrow['lounge']['appearance'] = hud['lounge']['appearance']
                show(narrow)
                no_overflow()

                # Status stays short without leaking raw transport diagnostics onto a stream.
                statuses = deepcopy(compact)
                statuses['settings'].update(animate=True, slide_duration=700)
                base_player = statuses['lounge']['players'][0]
                statuses['lounge']['players'] = [dict(deepcopy(base_player), status='stale', error='RAW DEBUG STACK ' * 30),
                                                {'username': 'Loading player', 'status': 'loading', 'data': None},
                                                {'username': 'Missing player', 'status': 'unavailable', 'data': None, 'error': 'RAW DEBUG STACK'}]
                show(statuses)
                expect(card.locator('.lounge-status')).to_contain_text(re.compile('stale', re.I))
                expect(page.locator('.lounge-card').nth(1).locator('.lounge-status')).to_contain_text(re.compile('loading', re.I))
                expect(page.locator('.lounge-card').nth(2).locator('.lounge-status')).to_contain_text(re.compile('unavailable', re.I))
                assert 'RAW DEBUG STACK' not in page.locator('#overlay').inner_text()
                expect(card.locator('.lounge-mmr')).to_contain_text('2727')

                # Failed polls retain existing cards; reduced motion disables card/source movement.
                expect(page.locator('#overlay')).to_have_css('transition-duration', '0.7s, 0s')
                page.emulate_media(reduced_motion='reduce')
                expect(page.locator('#overlay')).to_have_css('transition-duration', '0s')
                failure = True
                expect(page.locator('#connection-status')).to_contain_text('last update', timeout=6000)
                expect(page.locator('.lounge-card')).to_have_count(3)
                expect(card.locator('.lounge-mmr')).to_contain_text('2727')
                assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgba(0, 0, 0, 0)'
                assert not errors, errors
                browser.close()
                print('PASS: compact/expanded Lounge cards, optional metrics, honest win percentages, delta symbols/colors, blank unknowns, appearance, narrow Unicode, status and last-good reduced-motion rendering.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
