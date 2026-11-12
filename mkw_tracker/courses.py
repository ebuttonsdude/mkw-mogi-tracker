"""User-supplied course vocabulary and conservative OCR matching."""

from difflib import SequenceMatcher
import re

COURSES = (
    "Acorn Heights", "Airship Fortress", "Boo Cinema", "Bowser's Castle",
    "Cheep Cheep Falls", "Choco Mountain", "Crown City", "Dandelion Depths",
    "Desert Hills", "Dino Dino Jungle", "DK Pass", "DK Spaceport",
    "Dry Bones Burnout", "Faraway Oasis", "Great ? Block Ruins", "Koopa Troopa Beach",
    "Mario Bros. Circuit", "Mario Circuit", "Moo Moo Meadows", "Peach Beach",
    "Peach Stadium", "Rainbow Road", "Salty Salty Speedway", "Shy Guy Bazaar",
    "Sky-High Sundae", "Starview Peak", "Toad's Factory", "Wario Stadium",
    "Wario's Shipyard", "Whistlestop Summit", "SNES Mario Circuit 1",
    "SNES Mario Circuit 2", "SNES Mario Circuit 3", "SNES Ghost Valley 1",
    "SNES Ghost Valley 2", "SNES Ghost Valley 3", "SNES Choco Island 1",
    "SNES Choco Island 2", "SNES Vanilla Lake 1", "SNES Koopa Beach 1",
)


def _key(text):
    return re.sub(r'[^a-z0-9]', '', text.casefold())


def match_course(observed):
    """Return a canonical name only when the read distinguishes one course."""
    key = _key(observed)
    if not key:
        return None
    indexed = [(name, _key(name)) for name in COURSES]
    exact = [name for name, canonical in indexed if canonical == key]
    if exact:
        return exact[0]
    # Preserve visible track numbers. Missing numbers are safe only for a unique course.
    numbers = re.findall(r'\d+', key)
    candidates = [(name, canonical) for name, canonical in indexed
                  if (not numbers or re.findall(r'\d+', canonical) == numbers)
                  and (not key.startswith('snes') or canonical.startswith('snes'))]
    partial = [name for name, canonical in candidates if len(key) >= 6 and key in canonical
               and len(key) / len(canonical) >= .45]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        return None
    ranked = sorted(((SequenceMatcher(None, key, canonical).ratio(), name)
                     for name, canonical in candidates), reverse=True)
    if ranked and ranked[0][0] >= .82 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= .12):
        return ranked[0][1]
    return None
