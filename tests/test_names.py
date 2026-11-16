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
        ("Amon-Ra St. Brown", "amonra st brown"),
        ("  T.J.   Hockenson ", "tj hockenson"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected
