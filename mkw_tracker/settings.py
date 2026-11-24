"""Validate local display and experimental capture preferences."""

from copy import deepcopy
import math

from .themes import resolve_theme
from .report_fonts import valid_font_id

DEFAULT_SETTINGS = {
    'yolo_mode': False, 'discord_enabled': False, 'discord_application_id': '1574667683088171169',
    'standings_format': 'txt', 'theme': 'mario', 'report_font': '', 'mogi_png_graph': 'cumulative',
    'overlay': {'layout': 'teams', 'width': 560, 'height': 600, 'font_size': 22,
                'opacity': .85, 'show_header': True, 'show_players': True,
                'show_gaps': True, 'animate': True, 'scroll': True,
                'scroll_speed': 30, 'title': '', 'highlight_team_id': None, 'highlight_player_id': None,
                'alternating_backgrounds': True, 'slide_direction': 'left',
                'slide_duration': 400, 'ww_show_graph': True, 'ww_show_current': True,
                'auto_visibility': False, 'auto_visibility_during_races': False, 'transition_style': 'slide',
                'ww_show_peak': True, 'ww_show_lowest': True,
                'ww_show_last_10': True, 'ww_show_character': True},
}


def merge_settings(current, values, *, theme_resolver=resolve_theme):
    if not isinstance(values, dict) or not values or not set(values) <= set(DEFAULT_SETTINGS):
        raise ValueError('Provide valid display settings: standings_format, theme, report_font, '
                         'mogi_png_graph, overlay, discord_enabled, discord_application_id or yolo_mode.')
    merged = deepcopy(current)
    if 'discord_enabled' in values:
        if type(values['discord_enabled']) is not bool:
            raise ValueError('discord_enabled must be a boolean.')
        merged['discord_enabled'] = values['discord_enabled']
    if 'discord_application_id' in values:
        value = values['discord_application_id']
        if not isinstance(value, str) or (value and (not value.isascii() or not value.isdigit()
                                                     or not 17 <= len(value) <= 20)):
            raise ValueError('Discord Application ID must be 17–20 ASCII digits, or empty.')
        merged['discord_application_id'] = value
    if merged.get('discord_enabled') and not merged.get('discord_application_id'):
        raise ValueError('Enter a Discord Application ID before enabling Rich Presence.')
    if 'yolo_mode' in values:
        if type(values['yolo_mode']) is not bool:
            raise ValueError('yolo_mode must be a boolean.')
        merged['yolo_mode'] = values['yolo_mode']
    if 'standings_format' in values:
        if values['standings_format'] not in ('txt', 'md'):
            raise ValueError('standings_format must be txt or md')
        merged['standings_format'] = values['standings_format']
    if 'mogi_png_graph' in values:
        if values['mogi_png_graph'] not in ('cumulative', 'relative'):
            raise ValueError('mogi_png_graph must be cumulative or relative.')
        merged['mogi_png_graph'] = values['mogi_png_graph']
    if 'theme' in values:
        theme_resolver(values['theme'])
        merged['theme'] = values['theme']
    if 'report_font' in values:
        if not valid_font_id(values['report_font']):
            raise ValueError('Choose a font ID, not a path, or an empty string for default.')
        merged['report_font'] = values['report_font']
    if 'overlay' in values:
        overlay = values['overlay']
        if not isinstance(overlay, dict) or not overlay or not set(overlay) <= set(DEFAULT_SETTINGS['overlay']):
            raise ValueError('Provide valid OBS browser overlay options.')
        for key, value in overlay.items():
            if key in ('width', 'height', 'font_size', 'scroll_speed', 'slide_duration'):
                low, high = {'width': (240, 1920), 'height': (40, 2160),
                             'font_size': (12, 48), 'scroll_speed': (5, 120),
                             'slide_duration': (0, 5000)}[key]
                if type(value) is not int or not low <= value <= high:
                    raise ValueError(f'{key} must be a whole number from {low} to {high}.')
            elif key == 'opacity':
                if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
                    raise ValueError('opacity must be a number from 0 to 1.')
            elif key == 'layout':
                if value not in ('teams', 'players'):
                    raise ValueError('Overlay layout must be teams or players.')
            elif key == 'slide_direction':
                if value not in ('left', 'right'):
                    raise ValueError('Overlay slide direction must be left or right.')
            elif key == 'transition_style':
                if value not in ('slide', 'fade', 'checkered', 'dissolve', 'zoom', 'flip', 'iris', 'wipe'):
                    raise ValueError('Choose a supported overlay transition style.')
            elif key == 'title':
                if not isinstance(value, str) or len(value) > 100 or any(ord(char) < 32 for char in value):
                    raise ValueError('Overlay title must be at most 100 characters without controls.')
            elif key in ('highlight_team_id', 'highlight_player_id'):
                if value is not None and (not isinstance(value, str) or not value.strip()
                                          or len(value) > 200 or any(ord(c) < 32 for c in value)):
                    raise ValueError('Choose an identity to highlight or null for no highlight.')
            elif type(value) is not bool:
                raise ValueError(f'{key} must be a boolean.')
            if isinstance(value, str):
                try:
                    value.encode('utf-8')
                except UnicodeEncodeError as error:
                    raise ValueError(f'{key} must contain valid Unicode text.') from error
        merged['overlay'].update(overlay)
        after, during = 'auto_visibility', 'auto_visibility_during_races'
        if after in overlay or during in overlay:
            if overlay.get(after) and overlay.get(during):
                raise ValueError('Choose only one automatic overlay visibility direction.')
            # The two toggles are one preference. Older named sources that saved
            # only the original toggle must not inherit a conflicting direction.
            for key, opposite in ((after, during), (during, after)):
                if overlay.get(opposite):
                    merged['overlay'][key] = False
                else:
                    merged['overlay'].setdefault(key, False)
    return merged
