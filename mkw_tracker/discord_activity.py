"""Pure, bounded Discord status text from the active scoring snapshot."""


# Remote reference only; no artwork or user-uploaded theme images are distributed.
# Source and third-party hosting boundary are documented in ATTRIBUTION.md.
CONKDOR_IMAGE_URL = 'https://mario.wiki.gallery/images/5/5d/MK_World_map_Conkdor.png'


def _text(value, limit=128):
    text = ' '.join(str(value).split())
    encoded = text.encode('utf-8', errors='replace')
    if len(encoded) <= limit:
        return text
    return encoded[:max(0, limit - 3)].decode('utf-8', errors='ignore') + '…'


def _rank(row, rows):
    return 1 + sum(other['points'] > row['points'] for other in rows)


def _change(value):
    return 'unknown' if value is None else f'{value:+d}'


def build_activity(mode, snapshot, overlay_settings, *, current_course=None, lounge_rank=None):
    """No I/O or inferred identity; ranks are competition ranks of net points.

    Teams use a compact net-score summary rather than a crowded member list.
    """
    session_id = snapshot['session']['id']
    races = [r for r in snapshot['races'] if r['session_id'] == session_id]
    if mode == 'ww':
        summary = snapshot['ww']
        recorded = [r for r in races if r.get('ww_result') is not None]
        last = recorded[-1]['ww_result']['delta'] if recorded else None
        details = f"Worldwides | Last 10 VR: {_change(summary['last_10_change'])} | Last: {_change(last)}"
        state = f"Race {len(recorded) + 1} | {_text(current_course, 80) if current_course else 'Current course unknown'}"
    else:
        players = [p for p in snapshot['players'] if p.get('session_id', session_id) == session_id]
        teams = [t for t in snapshot['teams'] if t.get('session_id', session_id) == session_id]
        count = sum(bool(r.get('revision')) for r in races)
        if mode == 'ffa':
            player = next((p for p in players if p['id'] == overlay_settings.get('highlight_player_id')), None)
            details = f'Mogi FFA | {count} races scored'
            state = 'No player highlighted'
            if player:
                state = f"{_text(player['name'], 40)}: {player['points']} pts (#{_rank(player, players)})"
                if lounge_rank:
                    state += f' | Lounge: {_text(lounge_rank, 30)}'
        else:
            team = next((t for t in teams if t['id'] == overlay_settings.get('highlight_team_id')), None)
            details = f"Mogi Teams · {count} {'race' if count == 1 else 'races'}"
            state = 'No team highlighted'
            if team:
                above = [t for t in teams if t['points'] > team['points']]
                if above:
                    previous = min(above, key=lambda t: t['points'])
                    gap = f"{previous['points'] - team['points']} pts to #{_rank(previous, teams)}"
                else:
                    others = [t for t in teams if t['id'] != team['id']]
                    if not others:
                        gap = 'No other teams'
                    else:
                        runner_up = max(others, key=lambda t: t['points'])
                        lead = team['points'] - runner_up['points']
                        gap = (f'{lead} pts ahead of #2' if lead
                               else 'Tied for #1 · 0 pts gap')
                state = f"Team Position #{_rank(team, teams)} · {team['points']} pts · {gap}"
    return {'details': _text(details), 'state': _text(state),
            'assets': {'large_image': CONKDOR_IMAGE_URL, 'large_text': 'Conkdor'}}
