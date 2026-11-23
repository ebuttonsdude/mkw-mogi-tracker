"""Local named OBS sources. Call under the application's lock; no network here."""
from copy import deepcopy
import json
import re
from uuid import uuid4

from .settings import DEFAULT_SETTINGS, merge_settings
from .overlay_backgrounds import OverlayBackgrounds

BLOCKS = ('header', 'standings', 'ww_player', 'ww_stats', 'ww_graph', 'lounge')
DEFAULT_CONTENT = {key: key != 'lounge' for key in BLOCKS}
LOUNGE_METRICS = ('username', 'division', 'mmr', 'leaderboard_rank', 'peak_mmr', 'events', 'positive_mmr_rate',
                  'average_score', 'last_ten_record', 'last_ten_mmr_change', 'last_ten_average_score', 'last_five_form',
                  'weekly_events', 'weekly_positive_mmr_rate', 'weekly_event_mmr_change', 'weekly_average_score')
LOUNGE_APPEARANCE = {'background': ('aurora', 'carbon', 'theme', 'transparent', 'custom', 'hud'),
                     'border': ('rainbow', 'division', 'accent', 'none', 'hud'), 'shape': ('pill', 'rounded')}
DEFAULT_LOUNGE = {'appearance': {key: values[0] for key, values in LOUNGE_APPEARANCE.items()},
                  'metrics': {key: key != 'last_five_form' for key in LOUNGE_METRICS},
                  'enabled': False, 'players': [], 'season': None, 'room_size': 12}


def text(value, limit, label):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f'Provide {label} (1–{limit} characters, no controls).')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError(f'Provide valid Unicode for {label}.') from error
    return value.strip()


def slug(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', value) or len(value) > 48:
        raise ValueError('Source slug must be 1–48 lowercase letters, digits or single hyphens.')
    return value


class OverlayProfiles:
    def __init__(self, directory, theme_resolver):
        self.path = directory / 'overlay-profiles.json'
        self.revision = 0
        self.instance = uuid4().hex
        self.resolve_theme = theme_resolver
        self.profiles = {'default': self._new('Default')}
        # Ephemeral visibility is deliberately not shared with the default source.
        self.visible = {}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict) or 'default' not in saved or not 1 <= len(saved) <= 16:
                raise ValueError('Invalid saved overlay profiles.')
            self.profiles = {slug(key): self._validate(self._new('Default'), value) for key, value in saved.items()}
        self.backgrounds = OverlayBackgrounds(directory)
        self.identities = {key: uuid4().hex for key in self.profiles}
        self.background_revisions = {key: 0 for key in self.profiles}

    @staticmethod
    def _new(name):
        return {'name': name, 'overlay': {}, 'theme': None, 'report_font': None,
                'content': deepcopy(DEFAULT_CONTENT), 'lounge': deepcopy(DEFAULT_LOUNGE)}

    def _validate(self, current, values):
        if not isinstance(values, dict) or not values or not set(values) <= set(current):
            raise ValueError('Unknown or empty source options.')
        result = deepcopy(current)
        for key, value in values.items():
            if key == 'name':
                result[key] = text(value, 80, 'source name')
            elif key == 'overlay':
                if value:
                    result[key] = merge_settings({'overlay': current[key]}, {'overlay': value})['overlay']
                elif value != {}:
                    raise ValueError('Provide overlay options.')
            elif key in ('theme', 'report_font'):
                if value is not None:
                    merge_settings(DEFAULT_SETTINGS, {key: value}, theme_resolver=self.resolve_theme)
                result[key] = value
            elif key == 'content':
                if not isinstance(value, dict) or not set(value) <= set(BLOCKS) or any(type(v) is not bool for v in value.values()):
                    raise ValueError('Content assignments must be boolean block options.')
                result[key].update(value)
            elif key == 'lounge':
                if not isinstance(value, dict) or not set(value) <= set(DEFAULT_LOUNGE):
                    raise ValueError('Unknown Lounge options.')
                lounge = dict(result[key], **value)
                appearance = lounge['appearance']
                if (not isinstance(appearance, dict) or not set(appearance) <= set(LOUNGE_APPEARANCE)
                        or any(value not in LOUNGE_APPEARANCE[key] for key, value in appearance.items())):
                    raise ValueError('Lounge appearance must use known background, border and shape choices.')
                lounge['appearance'] = dict(result[key]['appearance'], **appearance)
                metrics = lounge['metrics']
                if not isinstance(metrics, dict) or not set(metrics) <= set(LOUNGE_METRICS) or any(type(v) is not bool for v in metrics.values()):
                    raise ValueError('Lounge metrics must be known boolean options.')
                lounge['metrics'] = dict(result[key]['metrics'], **metrics)
                if type(lounge['enabled']) is not bool:
                    raise ValueError('Lounge enabled must be a boolean.')
                if lounge['season'] is not None and (type(lounge['season']) is not int or not 0 <= lounge['season'] <= 999):
                    raise ValueError('Choose a season number (0–999), or null for current season.')
                if type(lounge['room_size']) is not int or lounge['room_size'] not in (12, 24):
                    raise ValueError('Lounge room size must be 12 or 24.')
                players = lounge['players']
                if not isinstance(players, list) or len(players) > 8:
                    raise ValueError('Choose up to eight manual Lounge usernames.')
                players = [text(p, 64, 'Lounge username') for p in players]
                if len({p.casefold() for p in players}) != len(players):
                    raise ValueError('Lounge usernames must be distinct.')
                lounge['players'] = players
                result[key] = lounge
        return result

    def get(self, key):
        return deepcopy(self.profiles[slug(key)])

    def effective(self, key, settings):
        profile = self.get(key)
        resolved = deepcopy(settings)
        resolved['overlay'].update(profile['overlay'])
        for field in ('theme', 'report_font'):
            if profile[field] is not None:
                resolved[field] = profile[field]
        return profile, resolved

    def appearance(self, key):
        appearance = self.get(key)['lounge']['appearance']
        appearance['image_url'] = self.backgrounds.url(key)
        return appearance

    def _public_source(self, key):
        source = self.get(key)
        source['lounge']['appearance'] = self.appearance(key)
        return source

    def public(self, settings):
        return [dict(self._public_source(key), slug=key, url='/overlay' if key == 'default' else f'/overlay/{key}',
                     effective=self.effective(key, settings)[1]) for key in self.profiles]

    def _save(self, updated):
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(updated, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)
        self.profiles = updated
        self.revision += 1

    def create(self, key, name, settings):
        key = slug(key)
        if key in self.profiles or len(self.profiles) >= 16:
            raise ValueError('Choose an unused slug; at most sixteen sources are supported.')
        # A previously interrupted deletion must not attach artwork to a new source.
        self.backgrounds.remove(key)
        profile = self._new(text(name, 80, 'source name'))
        # Snapshot current options, but future options still inherit defaults.
        profile['overlay'] = deepcopy(settings['overlay'])
        self._save(dict(self.profiles, **{key: profile}))
        self.identities[key] = uuid4().hex
        self.background_revisions[key] = 0
        return self.get(key)

    def update(self, key, values):
        current = self.get(key)
        if key == 'default' and isinstance(values, dict) and 'overlay' in values:
            raise ValueError('Use /api/settings to update the default overlay display settings.')
        self._save(dict(self.profiles, **{key: self._validate(current, values)}))
        return self.get(key)

    def delete(self, key):
        self.get(key)
        if key == 'default':
            raise ValueError('The backward-compatible default source cannot be deleted.')
        updated = deepcopy(self.profiles)
        del updated[key]
        self._save(updated)
        self.visible.pop(key, None)
        self.identities.pop(key, None)
        self.background_revisions.pop(key, None)
        self.backgrounds.remove(key)

    def background_token(self, key):
        self.get(key)
        return self.identities[key], self.background_revisions[key]

    def save_background(self, key, png, token):
        if self.background_token(key) != token:
            raise ValueError('Source or background changed during upload; try again.')
        self.backgrounds.save_prepared(key, png)
        self.background_revisions[key] += 1
        self.revision += 1
        return {'image_url': self.backgrounds.url(key)}

    def remove_background(self, key):
        self.get(key)
        self.backgrounds.remove(key)
        self.background_revisions[key] += 1
        self.revision += 1
        return {'image_url': None}

    def move(self, block, target):
        self.get(target)
        if block not in BLOCKS:
            raise ValueError('Choose an existing overlay content block.')
        updated = deepcopy(self.profiles)
        for key, profile in updated.items():
            profile['content'][block] = key == target
        self._save(updated)
