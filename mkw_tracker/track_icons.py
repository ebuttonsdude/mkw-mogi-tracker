"""Course labels remain available without bundled game thumbnails."""

from pathlib import Path

# Kept as an empty whitelist for the existing static-route boundary.
ICON_DIRECTORY = Path(__file__).parent / 'static' / 'track-icons'
COURSE_ICONS = {}


def icon_filename(course):
    return None


def icon_url(course):
    return None
