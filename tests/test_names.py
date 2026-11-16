import pytest

from ffp.names import normalize_name


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Caleb Williams", "caleb williams"),
        ("Luka Dončić", "luka doncic"),
        ("Michael Pittman Jr.", "michael pittman"),
        ("Marvin Harrison Jr", "marvin harrison"),
        ("Kenneth Walker III", "kenneth walker"),
        ("D'Andre Swift", "dandre swift"),
        # ppopt gives "amonra st brown"; this project turns hyphens into spaces.
        ("Amon-Ra St. Brown", "amon ra st brown"),
        ("  T.J.   Hockenson ", "tj hockenson"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Marvin Harrison Jr.", "marvin harrison"),
        ("Kenneth Walker III", "kenneth walker"),
        ("Amon-Ra St. Brown", "amon ra st brown"),
        ("De'Von Achane", "devon achane"),
        ("DK Metcalf", "dk metcalf"),
        ("Ja'Marr Chase", "jamarr chase"),
        ("Jaxon Smith-Njigba", "jaxon smith njigba"),
        ("Deebo Samuel Sr.", "deebo samuel"),
        ("E.J. Williams Jr.", "ej williams"),
    ],
)
def test_normalize_name_nflverse_display_names(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("variant", ["A.J. Brown", "AJ Brown", "aj brown", "A.J.  Brown"])
def test_periods_in_initials_are_stripped(variant):
    assert normalize_name(variant) == "aj brown"


@pytest.mark.parametrize("apostrophe", ["'", "’", "`"])
def test_apostrophes_are_stripped(apostrophe):
    assert normalize_name(f"Ja{apostrophe}Marr Chase") == "jamarr chase"


def test_accents_are_ascii_folded():
    assert normalize_name("José Peña") == "jose pena"


@pytest.mark.parametrize("suffix", ["Jr", "Jr.", "Sr", "Sr.", "II", "III", "IV", "V", "jr."])
def test_generational_suffixes_are_dropped(suffix):
    assert normalize_name(f"Michael Carter {suffix}") == "michael carter"


def test_suffix_dropped_after_comma():
    assert normalize_name("Marvin Harrison, Jr.") == "marvin harrison"


def test_lone_suffix_like_word_is_kept():
    # A one-word name is never emptied.
    assert normalize_name("V") == "v"


def test_whitespace_is_collapsed():
    assert normalize_name("  Jaxon \t Smith -  Njigba  ") == "jaxon smith njigba"
