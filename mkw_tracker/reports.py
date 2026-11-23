"""Local, review-aware folder reports and the OBS plaintext scoreboard."""

from collections import Counter
from io import BytesIO
from math import ceil, floor, log10
import os
from pathlib import Path
from struct import error as StructError
import sys

from .store import POINTS
from .themes import resolve_theme, team_color_map
from .report_fonts import (draw_text as _draw_text, _font_codepoints, FontLibrary, FontStack, text_clusters)

MEMBERSHIP_NOTE = 'Teams use roster assignments at export time, not historical membership.'


def _standings(rows):
    ordered = sorted(rows, key=lambda row: (-row['points'], row['name'].casefold(), row['id'] or ''))
    previous = None
    rank = 0
    for index, row in enumerate(ordered, 1):
        if previous is None or row['points'] != previous:
            rank = index
        row['rank'] = rank
        row['gap_above'] = None if previous is None else previous - row['points']
        previous = row['points']
    return ordered


def folder_report(snapshot, folder_id):
    """Latest placement revisions in the folder's saved race order."""
    folder = next((f for f in snapshot['folders'] if f['id'] == folder_id), None)
    if folder is None:
        raise KeyError(folder_id)
    is_ffa = folder.get('format', snapshot.get('format', snapshot.get('session', {}).get('format'))) == 'ffa'
    players = {p['id']: p for p in snapshot['players'] + snapshot.get('archived_players', [])
               if p['active'] and p['session_id'] == folder['session_id']}
    teams = {t['id']: t for t in snapshot['teams'] + snapshot.get('archived_teams', [])
             if t['active'] and t['session_id'] == folder['session_id']}
    races = [r for r in snapshot['races'] if r['folder_id'] == folder_id]
    player_rows = {}
    team_rows = {team_id: {'id': team_id, 'name': team['name'], 'points': 0,
                          'gross_points': 0, 'penalty_points': 0, 'races': [None] * len(races)}
                 for team_id, team in teams.items()}
    race_rows = []

    def identities(player_id):
        player = players[player_id]
        team_id = player['team_id'] if not is_ffa and player['team_id'] in teams else None
        if player_id not in player_rows:
            player_rows[player_id] = {'id': player_id, 'name': player['name'],
                'team_id': team_id, 'team_name': None if is_ffa else teams[team_id]['name'] if team_id else 'Unassigned',
                'points': 0, 'gross_points': 0, 'penalty_points': 0, 'races': [None] * len(races)}
        if team_id not in team_rows:
            team_rows[team_id] = {'id': team_id, 'name': teams[team_id]['name'] if team_id else 'Unassigned',
                'points': 0, 'gross_points': 0, 'penalty_points': 0, 'races': [None] * len(races)}
        return player_rows[player_id], team_rows[team_id]

    for player_id in players:
        identities(player_id)

    for index, race in enumerate(races):
        race_rows.append({k: race.get(k) for k in ('id', 'course', 'created_at', 'status',
                                                  'revision', 'offset_ms', 'icon_url')})
        race_rows[-1]['number'] = index + 1
        race_rows[-1]['penalties'] = [dict(p) for p in race.get('penalties', [])]
        for entry in race['placements']:
            if entry['player_id'] not in players:
                continue
            row, team = identities(entry['player_id'])
            row['races'][index] = {'position': entry['position'], 'points': entry['points'],
                                 'penalty_points': 0, 'net_points': entry['points']}
            row['gross_points'] += entry['points']
            team['gross_points'] += entry['points']
            team['races'][index] = (team['races'][index] or 0) + entry['points']
        for penalty in race.get('penalties', []):
            if penalty['player_id'] not in players:
                continue
            row, team = identities(penalty['player_id'])
            row['penalty_points'] += penalty['points']
            team['penalty_points'] += penalty['points']
            if row['races'][index] is not None:
                row['races'][index]['penalty_points'] += penalty['points']
                row['races'][index]['net_points'] -= penalty['points']
            team['races'][index] = (team['races'][index] or 0) - penalty['points']
    for row in list(player_rows.values()) + list(team_rows.values()):
        row['net_points'] = row['points'] = row['gross_points'] - row['penalty_points']
    # Zero is known for absent teams in a confirmed race, never for pending races.
    for team in team_rows.values():
        for index, race in enumerate(races):
            if race['revision'] and team['races'][index] is None:
                team['races'][index] = 0
    # Room allocation includes historical identities even after roster removal.
    gross = sum(entry['points'] for race in races for entry in race['placements'])
    penalties = sum(penalty['points'] for race in races for penalty in race.get('penalties', []))
    return {'folder': dict(folder), 'format': 'ffa' if is_ffa else 'teams',
            'membership_note': None if is_ffa else MEMBERSHIP_NOTE,
            'races': race_rows, 'players': _standings(list(player_rows.values())),
            'teams': [] if is_ffa else _standings(list(team_rows.values())),
            'points': gross - penalties, 'net_points': gross - penalties,
            'gross_points': gross, 'penalty_points': penalties,
            'scored_races': sum(bool(r['revision']) for r in races),
            'pending_races': sum(not r['revision'] for r in races)}


def _gap(row):
    value = row['gap_above']
    return '—' if value is None else str(-value) if value else '0'


def _room_line(snapshot):
    room = snapshot['room']
    line = (f"Room: {room['points']}/{room['maximum_points']} pts | "
            f"{room['scored_races']}/{room['expected_races']} races")
    if room.get('penalty_points'):
        line += f" | Net {room['net_points']} (−{room['penalty_points']} deductions)"
    return line


def _team_rows(snapshot):
    return _standings([dict(team, points=team.get('net_points', team['points']))
                       for team in snapshot['teams']])


def team_standings_text(snapshot):
    if snapshot.get('format', snapshot.get('session', {}).get('format')) == 'ffa':
        return player_standings_text(snapshot)
    lines = ['TEAM STANDINGS']
    for team in _team_rows(snapshot):
        gap = '' if team['gap_above'] is None else f" | {_gap(team)}"
        lines.append(f"{team['rank']}. {team['name']} | {team['points']} pts{gap}")
    if len(lines) == 1:
        lines.append('No teams')
    lines.append(_room_line(snapshot))
    return '\n'.join(lines) + '\n'


def _markdown(value):
    return (str(value).replace('\\', '\\\\').replace('\n', ' ').replace('\r', ' ')
            .replace('|', '\\|').replace('`', '\\`').replace('<', '&lt;').replace('>', '&gt;')
            .replace('*', '\\*').replace('_', '\\_').replace('[', '\\[').replace(']', '\\]'))


def team_standings_markdown(snapshot):
    if snapshot.get('format', snapshot.get('session', {}).get('format')) == 'ffa':
        return player_standings_markdown(snapshot)
    lines = ['# Team standings', '', '| Rank | Team | Points | Gap |', '| --- | --- | ---: | ---: |']
    for team in _team_rows(snapshot):
        lines.append(f"| {team['rank']} | {_markdown(team['name'])} | {team['points']} | {_gap(team)} |")
    if not snapshot['teams']:
        lines.append('| — | No teams | — | — |')
    lines.extend(['', _markdown(_room_line(snapshot))])
    return '\n'.join(lines) + '\n'


def player_standings_text(snapshot):
    lines = ['FFA PLAYER STANDINGS']
    for player in _standings([dict(p, points=p.get('net_points', p['points'])) for p in snapshot['players']]):
        gap = '' if player['gap_above'] is None else f" | {_gap(player)}"
        lines.append(f"{player['rank']}. {player['name']} | {player['points']} pts{gap}")
    if len(lines) == 1:
        lines.append('No players')
    lines.append(_room_line(snapshot))
    return '\n'.join(lines) + '\n'


def player_standings_markdown(snapshot):
    lines = ['# FFA player standings', '', '| Rank | Player | Points | Gap |', '| --- | --- | ---: | ---: |']
    for player in _standings([dict(p, points=p.get('net_points', p['points'])) for p in snapshot['players']]):
        lines.append(f"| {player['rank']} | {_markdown(player['name'])} | {player['points']} | {_gap(player)} |")
    if not snapshot['players']:
        lines.append('| — | No players | — | — |')
    lines.extend(['', _markdown(_room_line(snapshot))])
    return '\n'.join(lines) + '\n'


def _check_font_text(font, text):
    if isinstance(font, FontStack):
        return  # Each cluster is resolved through the shared fallback stack.
    missing = sorted({ord(character) for character in str(text)
                      if ord(character) not in font._report_codepoints
                      # Shaping modifiers do not require standalone glyphs.
                      and not (0xfe00 <= ord(character) <= 0xfe0f
                               or 0xe0100 <= ord(character) <= 0xe01ef
                               or ord(character) in (0x200c, 0x200d))})
    if missing:
        characters = ', '.join(f'U+{value:04X}' for value in missing[:8])
        raise RuntimeError(f'PNG font {font.path} lacks glyphs for {characters}. '
                           'Set MKW_REPORT_FONT to a local font covering these characters; '
                           'fonts are never downloaded during export.')


def _font(ImageFont, size):
    # Local Unicode fonts only. Do not silently render Japanese as missing boxes.
    roots = [Path('/usr/share/fonts'), Path.home() / '.local/share/fonts',
             Path.home() / '.fonts', Path('/usr/local/share/fonts')]
    if sys.platform == 'win32':
        roots.extend([Path(os.environ.get('SystemRoot') or os.environ.get('WINDIR') or 'C:/Windows') / 'Fonts',
                      Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
                      / 'Microsoft/Windows/Fonts'])
    explicit = os.environ.get('MKW_REPORT_FONT')
    candidates = ([Path(explicit).expanduser()] if explicit else
                  (candidate for filename in ('NotoSansCJK-Regular.ttc', 'NotoSansJP-Regular.ttf',
                                              'NotoSansCJKjp-Regular.otf', 'TakaoGothic.ttf',
                                              'YuGothR.ttc', 'meiryo.ttc', 'msgothic.ttc')
                   for root in roots if root.is_dir()
                   for candidate in sorted(root.rglob(filename))))
    for candidate in candidates:
        try:
            candidate = candidate.resolve(strict=True)
            font = ImageFont.truetype(str(candidate), size)
            stat = candidate.stat()
            font._report_codepoints = _font_codepoints(str(candidate), font.index, stat.st_mtime_ns, stat.st_size)
            _check_font_text(font, 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 '
                             '\u00e9\u65e5\u672c\u8a9e\u3042\u30a2\u0391\u03a9\u03b1\u03c9\u2606')
            return font
        except (OSError, ValueError, RuntimeError, StructError, StopIteration):
            continue
    remediation = ('On Windows, install Japanese supplemental fonts in Settings > Apps > '
                   'Optional features, or install a static Noto Sans CJK JP font in the system '
                   'or per-user Fonts folder.' if sys.platform == 'win32' else
                   'Install Noto Sans CJK (noto-fonts-cjk on Arch), or place '
                   'NotoSansCJKjp-Regular.otf in ~/.local/share/fonts.')
    detail = f' MKW_REPORT_FONT could not load a supported font at {explicit}.' if explicit else ''
    raise RuntimeError('PNG export needs a local Japanese-capable font with Latin, Greek and symbol glyphs. '
                       f'{remediation} Set MKW_REPORT_FONT to an explicit local .ttf, .otf or .ttc path.'
                       f'{detail} Fonts are never downloaded during export.')


def _average_pace(races, players):
    return races * sum(POINTS) * players / len(POINTS)


def cumulative_team_series(report, *, relative=False):
    """Net team prefix sums; a pending race makes its entire later prefix unknown.

    A team's absence in a confirmed race contributes zero. Deductions have already
    been applied to team race cells by folder_report, at their linked race.
    Relative values subtract the current roster's share of gross room points.
    """
    team_sizes = Counter(player['team_id'] for player in report['players']) if relative else {}
    series = []
    for team in report['teams']:
        running, blocked, values = 0, False, []
        for index, race in enumerate(report['races']):
            if blocked or not race['revision']:
                blocked = True
                values.append(None)
            else:
                running += team['races'][index] or 0
                if relative:
                    expected = _average_pace(index + 1, team_sizes[team['id']])
                    values.append(running - expected)
                else:
                    values.append(running)
        series.append({'id': team['id'], 'name': team['name'], 'values': values})
    return series


def cumulative_player_series(report, *, relative=False):
    """Net player prefixes, optionally relative to one player's gross room share."""
    series = []
    for player in report['players']:
        running, blocked, values = 0, False, []
        for index, race in enumerate(report['races']):
            if blocked or not race['revision']:
                blocked = True
                values.append(None)
            else:
                cell = player['races'][index]
                running += cell['net_points'] if cell else 0
                values.append(running - _average_pace(index + 1, 1) if relative else running)
        series.append({'id': player['id'], 'name': player['name'], 'values': values})
    return series


def scaled_bounds(values, *, center_flat=False):
    """Return inclusive zero-aware (lower, upper, ticks) with nice integer steps."""
    known = [value for value in values if value is not None]
    low, high = min([0, *known]), max([0, *known])
    if low == high:
        if center_flat:
            return -1, 1, [-1, 0, 1]
        return 0, 5, list(range(6))
    padding = (high - low) * .05
    padded_low = low - padding if low < 0 else 0
    padded_high = high + padding if high > 0 else 0
    desired = (padded_high - padded_low) / 6
    magnitude = 10 ** floor(log10(desired))
    step = next(multiplier * magnitude for multiplier in (1, 2, 2.5, 5, 10)
                if multiplier * magnitude >= desired)
    step = max(1, ceil(step))
    lower = floor(padded_low / step) * step
    upper = ceil(padded_high / step) * step
    return lower, upper, list(range(lower, upper + step, step))


def render_folder_png(snapshot, folder_id, theme='mario', *, highlight_team_id=None,
                      highlight_session_id=None, highlight_player_id=None, font_library=None, report_font="",
                      course_images=None, mogi_png_graph='cumulative'):
    """Themed net-point chart and standings for the folder's saved format."""
    try:
        from PIL import Image, ImageColor, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("PNG export requires Pillow; install the project's reports extra.") from exc
    report = folder_report(snapshot, folder_id)
    course_images = course_images or {}
    is_ffa = report['format'] == 'ffa'
    rows = report['players'] if is_ffa else report['teams']
    selected_theme = resolve_theme(theme)
    tokens = selected_theme['tokens']
    colors = team_color_map(snapshot, report['folder']['session_id'], selected_theme)
    if is_ffa:
        roster = [p for p in snapshot['players'] + snapshot.get('archived_players', [])
                  if p.get('active', True) and p['session_id'] == report['folder']['session_id']]
        palette = selected_theme['team_colors']
        colors = {p['id']: palette[index % len(palette)] for index, p in enumerate(roster)}
    count = len(report['races'])
    width = max(1100, 84 + count * 108)
    if width > 16000 or len(report['players']) > 500 or len(report['teams']) > 500:
        raise ValueError('Folder is too large for one PNG; split it into smaller folders.')
    fonts = font_library or FontLibrary()
    def font_at(size):
        return fonts.font(ImageFont, size, report_font)

    player_font = font_at(17)
    player_heading = font_at(25) if is_ffa else player_font
    font = player_font
    small = font_at(13)
    heading = font_at(25)
    large = font_at(33)
    operations = []

    def text(value, x, top, available, selected=font, color=None):
        _check_font_text(selected, value)
        line = ''
        for char in text_clusters(str(value)):
            if selected.getlength(line + char) > available and line:
                if ' ' in line.rstrip():
                    finished, line = line.rsplit(' ', 1)
                else:
                    finished, line = line, ''
                operations.append(('text', (x, top), finished, selected, color or tokens['text']))
                top += selected.size + 5
            line += char
        operations.append(('text', (x, top), line, selected, color or tokens['text']))
        return top + selected.size + 7

    def rectangle(bounds, color, radius=0):
        operations.append(('rect', bounds, color, radius))

    def tint(color, amount=.14):
        foreground, background = ImageColor.getrgb(color), ImageColor.getrgb(tokens['panel'])
        return tuple(round(a * amount + b * (1 - amount)) for a, b in zip(foreground, background))

    # A generous title bar keeps long Unicode room names clear of the points badge.
    title = report['folder']['name']
    if report['folder'].get('lounge_id'):
        title += ' | Lounge ID: ' + report['folder']['lounge_id']
    portrait_png = selected_theme.get('icon_png')
    title_x = 108 if portrait_png else 24
    title_end = text(title, title_x, 17, width - title_x - 276, heading, tokens['accent_text'])
    text(f"{report['gross_points']} / 984 pts", width - 252, 20, 228, heading, tokens['accent_text'])
    bar_height = max(96 if portrait_png else 72, title_end + 16)
    operations.insert(0, ('rect', (0, 0, width, bar_height), tokens['accent'], 0))
    if portrait_png:
        operations.append(('portrait', (20, 12), portrait_png))
    # Small finish-line checks are original geometry, not game artwork.
    for column in range(ceil(width / 12)):
        for row in range(2):
            if (column + row) % 2 == 0:
                rectangle((column * 12, bar_height + row * 6,
                           column * 12 + 12, bar_height + row * 6 + 6), tokens['line'])
    relative = mogi_png_graph == 'relative'
    if is_ffa:
        chart_title = 'Player points above/below average' if relative else 'Cumulative player net points'
        series = cumulative_player_series(report, relative=relative)
    else:
        chart_title = 'Team points above/below average' if relative else 'Cumulative team net points'
        series = cumulative_team_series(report, relative=relative)
    y = text(chart_title, 24, bar_height + 24, width - 48, heading)
    if report['pending_races']:
        y = text('Pending race stops all later cumulative totals.', 24, y, width - 48, small, tokens['warning'])
    if report['penalty_points']:
        y = text(f"Net {report['net_points']} pts after {report['penalty_points']} pts in deductions.",
                 24, y, width - 48, small, tokens['muted'])
    lower, upper, ticks = scaled_bounds((value for team in series for value in team['values']),
                                        center_flat=relative)
    tick_labels = ([f'{tick:+d}' if tick else '0 · average' for tick in ticks]
                   if relative else [str(tick) for tick in ticks])
    left = max(56, ceil(max(small.getlength(label) for label in tick_labels)) + 32)
    right, top, bottom = width - 28, y + 12, y + 266
    spacing = (right - left) / max(count, 1)

    def point(index, value):
        return (left + spacing * (index + .5), bottom - (value - lower) * (bottom - top) / (upper - lower))

    for tick, label in zip(ticks, tick_labels):
        ordinate = point(0, tick)[1]
        baseline = relative and tick == 0
        operations.append(('line', [(left, ordinate), (right, ordinate)],
                           tokens['muted'] if baseline else tokens['line'], 2 if baseline else 1))
        text(label, 12, ordinate - 9, left - 20, small, tokens['muted'])
    for team in series:
        color = colors[team['id']]
        coords = [point(race_index, value) for race_index, value in enumerate(team['values'])
                  if value is not None]
        if len(coords) > 1:
            operations.append(('line', coords, color, 3))
        for coordinate in coords:
            operations.append(('dot', coordinate, color))
    icon_band = 50 if any(course_images.get(race['course']) for race in report['races']) else 0
    labels_bottom = bottom + 12 + icon_band
    for index, race in enumerate(report['races']):
        center = point(index, 1)[0]
        png = course_images.get(race['course'])
        if png:
            operations.append(('course-icon', (int(center - 40), int(bottom + 12)), (80, 44), png))
        label = f"R{index + 1} · {race['course']}" + (' (pending)' if not race['revision'] else '')
        labels_bottom = max(labels_bottom, text(label, center - spacing / 2 + 4,
                           bottom + 12 + icon_band, spacing - 8, small))
    y = labels_bottom + 15
    if not count:
        y = text('No races in this folder.', 24, y, width - 48)
    standings_title = ('FFA player standings · Net points' if is_ffa else
                       'Team standings · Players ranked overall · Net points')
    y = text(standings_title, 24, y, width - 48, heading)
    y = text('Gap: above / leader', 24, y, width - 48, small, tokens['muted'])
    card_width = (width - 60) / 2
    members = {team['id']: [player for player in report['players'] if player['team_id'] == team['id']]
               for team in report['teams']}
    for start in range(0, len(rows), 2):
        bottoms = []
        for column, team in enumerate(rows[start:start + 2]):
            color = colors[team['id']]
            x = 24 + column * (card_width + 12)
            background_index = len(operations)
            rank_width = max(58, large.getlength(f"#{team['rank']}") + 12)
            rank_bottom = text(f"#{team['rank']}", x + 12, y + 10, rank_width, large, color)
            total = f"{team['net_points']} pts"
            score_width = min(card_width * .4, max(116, heading.getlength(total) + 4))
            score_x = x + card_width - score_width - 12
            name_bottom = text(team['name'], x + rank_width + 14, y + 13,
                               card_width - rank_width - score_width - 42, player_heading if is_ffa else font, color)
            score_bottom = text(total, score_x, y + 10, score_width, heading, color)
            if team['gap_above'] is not None:
                leader_gap = team['points'] - rows[0]['points']
                score_bottom = text(f"{_gap(team)} / {leader_gap}", score_x, score_bottom,
                                    score_width, small, tokens['muted'])
            member_top = max(y + 66, rank_bottom + 10, name_bottom + 10, score_bottom + 10)
            team_members = [] if is_ffa else members[team['id']]
            columns = 2 if len(team_members) > 4 else 1
            member_width = (card_width - 28) / columns
            for offset in range(0, len(team_members), columns):
                row_bottom = member_top
                for member_column, player in enumerate(team_members[offset:offset + columns]):
                    member_x = x + 14 + member_column * member_width
                    rank_label = f"#{player['rank']}"
                    player_rank_width = max(30, small.getlength(rank_label) + 8)
                    points_label = f"{player['net_points']} pts"
                    points_width = min(member_width * .45, max(66, font.getlength(points_label) + 4))
                    rank_bottom = text(rank_label, member_x, member_top + 2, player_rank_width, small, color)
                    name_bottom = text(player['name'], member_x + player_rank_width,
                        member_top, member_width - player_rank_width - points_width - 10, player_font)
                    points_bottom = text(points_label, member_x + member_width - points_width - 4,
                                         member_top, points_width, font, color)
                    row_bottom = max(row_bottom, rank_bottom, name_bottom, points_bottom)
                member_top = row_bottom + 2
            card_bottom = member_top + 10
            operations.insert(background_index, ('rect', (x, y, x + card_width, card_bottom), tint(color), 14))
            operations.insert(background_index + 1, ('rect', (x, y + 12, x + 5, card_bottom - 12), color, 2))
            bottoms.append(card_bottom)
        y = max(bottoms) + 12
    if not rows:
        y = text('No confirmed results.', 24, y, width - 48)
    session_id = report['folder']['session_id']
    highlighted = next((team for team in snapshot['teams'] + snapshot.get('archived_teams', [])
                        if team['id'] == highlight_team_id and team.get('session_id') == session_id
                        and team.get('active', True)), None)
    if is_ffa:
        highlighted = next((player for player in report['players']
                            if player['id'] == highlight_player_id), None)
    if highlighted and highlight_session_id == session_id:
        color = colors[highlighted['id']]
        y = text(f"{highlighted['name']} · Race finishing positions", 24, y + 12,
                 width - 48, player_heading if is_ffa else heading, color)
        roster = sorted((player for player in snapshot['players'] + snapshot.get('archived_players', [])
                         if player.get('session_id') == session_id
                         and player['team_id'] == highlighted['id'] and player.get('active', True)),
                        key=lambda player: (player['name'].casefold(), player['id']))
        if is_ffa:
            roster = [highlighted]
        placements = {player['id']: player['races'] for player in report['players']}
        if not roster:
            y = text('No players assigned to this team.', 24, y, width - 48, small, tokens['muted'])
        elif not count:
            y = text('No races in this folder.', 24, y, width - 48, small, tokens['muted'])
        else:
            columns = min(3, count)
            tile_width = (width - 48 - (columns - 1) * 12) / columns
            placement_font = font_at(15)
            for start in range(0, count, columns):
                bottoms = []
                for column, race in enumerate(report['races'][start:start + columns]):
                    index = start + column
                    x = 24 + column * (tile_width + 12)
                    background_index = len(operations)
                    png = course_images.get(race['course'])
                    icon_width = 84 if png else 0
                    if png:
                        operations.append(('course-icon', (int(x + 12), int(y + 12)), (72, 44), png))
                    header_bottom = text(f"R{race['number']} · {race['course']}", x + 12 + icon_width,
                                         y + 12, tile_width - 24 - icon_width, font, color)
                    if png:
                        header_bottom = max(header_bottom, y + 56)
                    member_top = header_bottom + 6
                    for player in roster:
                        cell = placements.get(player['id'], [None] * count)[index]
                        place = ('Pending' if not race['revision'] else
                                 f"Place {cell['position']}" if cell else 'Unknown')
                        place_width = max(82, small.getlength(place) + 4)
                        name_bottom = text(player['name'], x + 12, member_top,
                                           tile_width - place_width - 36, placement_font)
                        place_bottom = text(place, x + tile_width - place_width - 12,
                                            member_top + 2, place_width, small,
                                            tokens['muted'] if cell and race['revision'] else tokens['warning'])
                        member_top = max(name_bottom, place_bottom) + 2
                    card_bottom = member_top + 8
                    operations.insert(background_index, ('rect', (x, y, x + tile_width, card_bottom),
                                                         tint(color), 12))
                    bottoms.append(card_bottom)
                y = max(bottoms) + 12
    height = int(y + 12)
    if height > 30000 or width * height > 80_000_000:
        raise ValueError('Folder report exceeds the 80-megapixel PNG limit; split the folder into smaller reports.')
    image = Image.new('RGB', (width, height), tokens['bg'])
    draw = ImageDraw.Draw(image)
    for operation in operations:
        kind, *args = operation
        if kind == 'text':
            point_, label, selected, color = args
            _draw_text(draw, point_, label, font=selected, fill=color)
        elif kind == 'rect':
            bounds, color, radius = args
            draw.rounded_rectangle(bounds, radius=radius, fill=color)
        elif kind == 'line':
            coords, color, line_width = args
            draw.line(coords, fill=color, width=line_width)
        elif kind == 'dot':
            (x, y_), color = args
            draw.ellipse((x - 4, y_ - 4, x + 4, y_ + 4), fill=color)
        elif kind == 'course-icon':
            (x, top_), (box_width, box_height), png = args
            with Image.open(BytesIO(png)) as original:
                icon = original.convert('RGBA')
                icon.thumbnail((box_width, box_height))
            image.paste(icon, (x + (box_width - icon.width) // 2,
                               top_ + (box_height - icon.height) // 2), icon)
        elif kind == 'portrait':
            point_, png = args
            with Image.open(BytesIO(png)) as original:
                icon = original.convert('RGBA')
                icon.thumbnail((72, 72))
                position = (point_[0] + (72 - icon.width) // 2,
                            point_[1] + (72 - icon.height) // 2)
                image.paste(icon, position, icon)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()
