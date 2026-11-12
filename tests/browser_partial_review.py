"""Optional Chromium coverage for sparse capture review and draft upgrades.

Run only in a verified separate PID namespace with private proc.
Uses temporary stores and synthetic observations; no capture hardware or live services.
"""
from pathlib import Path
import tempfile
import threading

from mkw_tracker.server import Application, make_server


def main():
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = Application(root / 'scores.sqlite', root)
        players = [app.store.add_player(f'Player {position}') for position in range(1, 13)]
        app.store.create_race('partial-review', 'Synthetic course')

        def rows(positions):
            return [{'position': position, 'name': f'Player {position}', 'confidence': .9}
                    for position in positions]

        candidate = {'id': 'partial-review', 'race_id': 'partial-review',
                     'course': 'Synthetic course', 'partial': True, 'rows': rows(range(9, 12)),
                     'reasons': ['Partial screenshot']}
        candidate['rows'][1]['name'] = 'Player 10?'
        app.capture.state['pending'].append(candidate)
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_address[1]}')
                page.locator('#auto-refresh').uncheck()
                page.wait_for_function('!refreshing')
                page.locator('#tab-capture').click()
                groups = page.locator('.capture-card .capture-placement')
                expect(groups).to_have_count(12)

                def name(position):
                    return groups.nth(position - 1).locator('input')

                def identity(position):
                    return groups.nth(position - 1).locator('select')

                expect(page.locator('.partial-review-note')).to_contain_text('3 of 12 positions observed')
                for position in [*range(1, 9), 12]:
                    expect(name(position)).to_have_value('')
                    expect(identity(position)).to_have_value('')
                for position in (9, 11):
                    expect(name(position)).to_have_value(f'Player {position}')
                    expect(identity(position)).to_have_value(players[position - 1]['id'])
                expect(name(10)).to_have_value('Player 10?')
                expect(identity(10)).to_have_value('')  # No approximate-name inference.
                confirm = page.get_by_role('button', name='Confirm identities and count points')
                assert not page.locator('.capture-card form').evaluate('(form) => form.checkValidity()')
                confirm.click()
                assert app.store.snapshot()['room']['points'] == 0
                assert app.store.get_race('partial-review')['revision'] == 0
                expect(groups).to_have_count(12)

                name(9).fill('Typed Nine')
                identity(10).select_option(players[9]['id'])
                identity(11).select_option('')  # Explicitly reject an exact suggestion.
                name(2).fill('Temporary edit')
                name(2).fill('')  # An explicit clear also belongs to the operator.
                with app.lock:
                    candidate['rows'] = rows(range(2, 12))
                    for row in candidate['rows']:
                        if row['position'] >= 9:
                            row['name'] = 'Changed OCR name'
                page.evaluate('refresh()')
                expect(page.locator('.partial-review-note')).to_contain_text('10 of 12 positions observed')
                for position in range(3, 9):
                    expect(name(position)).to_have_value(f'Player {position}')
                    expect(identity(position)).to_have_value(players[position - 1]['id'])
                for position in (1, 2, 12):
                    expect(name(position)).to_have_value('')
                expect(name(9)).to_have_value('Typed Nine')
                expect(identity(9)).to_have_value('')
                expect(name(10)).to_have_value('Player 10?')
                expect(identity(10)).to_have_value(players[9]['id'])
                expect(name(11)).to_have_value('Player 11')
                expect(identity(11)).to_have_value('')

                with app.lock:
                    candidate.update(rows=rows(range(1, 13)), partial=False, reasons=[])
                    candidate['rows'][2]['name'] = 'Corrected OCR Three'
                page.evaluate('refresh()')
                expect(page.locator('.partial-review-note')).to_have_count(0)
                for position in (1, 12):
                    expect(name(position)).to_have_value(f'Player {position}')
                    expect(identity(position)).to_have_value(players[position - 1]['id'])
                expect(name(2)).to_have_value('')
                expect(name(3)).to_have_value('Corrected OCR Three')
                expect(identity(3)).to_have_value('')
                expect(name(9)).to_have_value('Typed Nine')
                expect(identity(10)).to_have_value(players[9]['id'])
                expect(identity(11)).to_have_value('')
                assert app.store.snapshot()['room']['points'] == 0

                # Only the operator's explicit twelve-identity review counts points.
                for position in range(1, 13):
                    identity(position).select_option(players[position - 1]['id'])
                with page.expect_response(lambda response: '/api/capture/review' in response.url) as response:
                    confirm.click()
                assert response.value.ok, response.value.text()
                expect(page.locator('.capture-card')).to_have_count(0)
                assert app.store.get_race('partial-review')['revision'] == 1
                assert app.store.snapshot()['room']['points'] == 82
                assert not errors, errors
                browser.close()
                print('PASS: sparse positions, blank unknowns, exact-only suggestions, protected edits, '
                      'fuller observation upgrades, and explicit 82-point confirmation.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
