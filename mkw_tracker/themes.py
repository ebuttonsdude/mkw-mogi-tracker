"""Local character-inspired palettes shared by the dashboard, PNG and OBS."""

import colorsys
import re
from copy import deepcopy

# Original color palettes, not downloaded character artwork.
CHARACTERS = (
    ('mario', 'Mario', '#ff6871'), ('luigi', 'Luigi', '#78df79'),
    ('peach', 'Peach', '#ff9ccc'), ('daisy', 'Daisy', '#ffbc58'),
    ('yoshi', 'Yoshi', '#8ae373'), ('donkey-kong', 'Donkey Kong', '#dfad79'),
    ('bowser', 'Bowser', '#f3b65a'), ('bowser-jr', 'Bowser Jr.', '#a2dc64'),
    ('koopa-troopa', 'Koopa Troopa', '#9bdf83'), ('toad', 'Toad', '#ff7d89'),
    ('toadette', 'Toadette', '#f68fce'), ('lakitu', 'Lakitu', '#f1d76b'),
    ('king-boo', 'King Boo', '#c0a4f5'), ('shy-guy', 'Shy Guy', '#f7838f'),
    ('wario', 'Wario', '#f9dc63'), ('waluigi', 'Waluigi', '#b695f5'),
    ('birdo', 'Birdo', '#f587b8'), ('pauline', 'Pauline', '#f87580'),
    ('rosalina', 'Rosalina', '#87e3dc'), ('baby-mario', 'Baby Mario', '#ff9a91'),
    ('baby-luigi', 'Baby Luigi', '#a7e79c'), ('baby-peach', 'Baby Peach', '#f7b8d9'),
    ('baby-daisy', 'Baby Daisy', '#f7cd84'), ('baby-rosalina', 'Baby Rosalina', '#b1e9e6'),
    ('nabbit', 'Nabbit', '#b4a0ee'), ('goomba', 'Goomba', '#d8b18a'),
    ('dry-bones', 'Dry Bones', '#d3d1c7'), ('piranha-plant', 'Piranha Plant', '#f68483'),
    ('spike', 'Spike', '#9cd77c'), ('wiggler', 'Wiggler', '#f2cd69'),
    ('hammer-bro', 'Hammer Bro', '#95dca6'), ('sidestepper', 'Sidestepper', '#f58c7e'),
    ('cataquack', 'Cataquack', '#80c9f3'), ('monty-mole', 'Monty Mole', '#cdb18b'),
    ('cheep-cheep', 'Cheep Cheep', '#fa9a88'), ('pianta', 'Pianta', '#84d4c8'),
    ('rocky-wrench', 'Rocky Wrench', '#d7b196'), ('conkdor', 'Conkdor', '#d5a8df'),
    ('swoop', 'Swoop', '#a4a3ed'), ('pokey', 'Pokey', '#eccd78'),
    ('peepa', 'Peepa', '#b9c5ed'), ('stingby', 'Stingby', '#ead06c'),
    ('fish-bone', 'Fish Bone', '#d1d6dd'), ('coin-coffer', 'Coin Coffer', '#e6cc7d'),
    ('cow', 'Cow', '#dfc5a8'), ('snowman', 'Snowman', '#c1e6f5'),
    ('penguin', 'Penguin', '#91b9ee'), ('dolphin', 'Dolphin', '#83d0ef'),
    ('para-biddybud', 'Para-Biddybud', '#f3a2ac'), ("chargin-chuck", "Chargin' Chuck", '#91cfb7'),
)


def _mix(first, second, amount):
    a = tuple(int(first[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(second[i:i + 2], 16) for i in (1, 3, 5))
    return '#' + ''.join(f'{round(x * (1 - amount) + y * amount):02x}' for x, y in zip(a, b))


def _palette(identifier, name, accent):
    rgb = [int(accent[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    hue = colorsys.rgb_to_hsv(*rgb)[0]
    colors = [accent]
    for index in range(1, 12):
        channels = colorsys.hsv_to_rgb((hue + index * .61803398875) % 1, .53, .98)
        colors.append('#' + ''.join(f'{round(value * 255):02x}' for value in channels))
    return {'id': identifier, 'name': name, 'team_colors': colors,
            'tokens': {'bg': _mix('#0a101d', accent, .06),
                       'panel': _mix('#152031', accent, .08),
                       'input': _mix('#0c1524', accent, .04),
                       'text': '#f3f6fc', 'muted': '#c0ccdc',
                       'line': _mix('#354354', accent, .2),
                       'accent': accent, 'accent_text': '#111926',
                       'danger': '#ffb5b5', 'warning': '#ffdb91'}}


_THEMES = {identifier: _palette(identifier, name, accent) for identifier, name, accent in CHARACTERS}
def character_icon_path(filename):
    """The derived project ships no character artwork."""
    return None


def resolve_theme(identifier='mario'):
    # Renderer callers may pass a locally resolved custom theme, never an HTTP path.
    if isinstance(identifier, dict):
        required = {'id', 'name', 'tokens', 'team_colors', 'custom', 'icon_url', 'icon_name', 'icon_fallback'}
        if not required <= set(identifier) or set(identifier) - required - {'icon_png', 'overridden'}:
            raise ValueError('Provide a complete locally resolved theme.')
        theme_id, name = identifier['id'], identifier['name']
        if not isinstance(theme_id, str) or not (theme_id in _THEMES or re.fullmatch(r'custom-[0-9a-f]{32}', theme_id)):
            raise ValueError('Invalid resolved theme identifier.')
        if (not isinstance(name, str) or not name.strip() or len(name) > 80
                or any(ord(c) < 32 for c in name)):
            raise ValueError('Invalid resolved theme name.')
        name.encode('utf-8')
        tokens, colors = identifier['tokens'], identifier['team_colors']
        if (not isinstance(tokens, dict) or set(tokens) != set(_THEMES['mario']['tokens'])
                or not isinstance(colors, list) or len(colors) != 12
                or any(not isinstance(c, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', c)
                       for c in [*tokens.values(), *colors])):
            raise ValueError('Invalid resolved theme colors.')
        overridden = identifier.get('overridden', False)
        if type(overridden) is not bool or ('overridden' in identifier and (not overridden or theme_id not in _THEMES)):
            raise ValueError('Invalid resolved theme override.')
        if type(identifier['custom']) is not bool or identifier['custom'] != (theme_id.startswith('custom-') or overridden):
            raise ValueError('Invalid resolved theme kind.')
        if identifier['icon_fallback'] is not False:
            raise ValueError('Resolved themes do not have bundled icon fallbacks.')
        url, icon_name = identifier['icon_url'], identifier['icon_name']
        if url is not None and (not identifier['custom'] or not isinstance(url, str)
                                or not re.fullmatch(r'/api/themes/' + re.escape(theme_id) + r'/icon\.png\?v=[0-9a-f]{16}', url)):
            raise ValueError('Invalid local theme icon URL.')
        if (url is None and icon_name is not None) or (url is not None and icon_name != name):
            raise ValueError('Invalid resolved theme icon name.')
        if 'icon_png' in identifier:
            image = identifier['icon_png']
            if url is None or not isinstance(image, bytes) or len(image) > 4 * 1024 * 1024 or not image.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Invalid resolved theme PNG.')
        return deepcopy(identifier)
    if not isinstance(identifier, str) or identifier not in _THEMES:
        raise ValueError('Choose an available theme.')
    theme = deepcopy(_THEMES[identifier])
    theme.update(custom=False, icon_url=None, icon_name=None, icon_fallback=False)
    return theme


def all_themes():
    return [resolve_theme(identifier) for identifier in _THEMES]


def team_color_map(snapshot, session_id, theme):
    """Keep identities colored alike across exports, including zero-score teams."""
    teams = {team['id']: team for team in snapshot['teams'] + snapshot.get('archived_teams', [])
             if team.get('session_id') == session_id}
    ordered = sorted(teams.values(), key=lambda team: (team['name'].casefold(), team['id']))
    palette = theme['team_colors']
    colors = {team['id']: (team['color'] if isinstance(team.get('color'), str)
                          and re.fullmatch(r'#[0-9a-fA-F]{6}', team['color'])
                          else palette[index % len(palette)]) for index, team in enumerate(ordered)}
    colors[None] = palette[len(ordered) % len(palette)]
    return colors
