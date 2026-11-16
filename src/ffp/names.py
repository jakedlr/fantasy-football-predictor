"""Player-name normalization, so book names and nflverse display names compare equal."""

import re
import unicodedata

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_NOT_ALNUM_OR_SPACE = re.compile(r"[^a-z0-9\s]")


def normalize_name(name: str) -> str:
    """Matching key for a player name.

    ASCII-folds accents, lowercases, turns hyphens into spaces, drops periods,
    apostrophes and other punctuation, collapses whitespace and removes
    trailing generational suffixes (Jr, Sr, II, III, IV, V). So "A.J. Brown",
    "AJ Brown" and "aj brown" all give "aj brown", and "Amon-Ra St. Brown"
    gives "amon ra st brown". A one-word name is never emptied.
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = _NOT_ALNUM_OR_SPACE.sub("", text.replace("-", " "))
    words = text.split()
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)
