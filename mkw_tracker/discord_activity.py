"""Pure, bounded Discord status text from the active scoring snapshot."""


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

    Discord's two text fields cannot fit arbitrarily large teams. Member names
    are shortened first and any remaining overflow is explicitly counted.
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
            details = f'Mogi Teams | {count} races scored'
            state = 'No team highlighted'
            if team:
                details += f" | {_text(team['name'], 35)} #{_rank(team, teams)}"
                members = [p for p in players if p.get('team_id') == team['id']]
                parts = [f"{_text(p['name'], 16)} {p['points']}pts #{_rank(p, players)}" for p in members]
                state = 'No team members'
                for visible in range(len(parts), 0, -1):
                    candidate = ' | '.join(parts[:visible])
                    if visible < len(parts):
                        candidate += f' | +{len(parts) - visible} members'
                    if len(candidate.encode('utf-8')) <= 128:
                        state = candidate
                        break
    return {'details': _text(details), 'state': _text(state)}
