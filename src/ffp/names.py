"""Player-name normalization, so book names and nflverse names compare equal."""

import re
import unicodedata

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _clean(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return " ".join(text.split())


def normalize_name(name: str) -> str:
    """Player-name key: accents, punctuation and generational suffixes removed."""
    words = _clean(name).split()
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)
