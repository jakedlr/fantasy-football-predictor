import math

import pytest

from ffp.odds import (
    DEVIG_METHODS,
    american_to_implied,
    apply_logit_shift,
    calibrate_logit_shift,
    devig,
    devig_main_line,
)


def test_american_to_implied_favourite():
    assert american_to_implied(-140) == pytest.approx(140 / 240)


def test_american_to_implied_underdog():
    assert american_to_implied(110) == pytest.approx(100 / 210)


def test_american_to_implied_even():
    assert american_to_implied(100) == pytest.approx(0.5)
    assert american_to_implied(-100) == pytest.approx(0.5)


@pytest.mark.parametrize("bad", [0, 50, -50, 99, -99])
def test_american_to_implied_rejects_invalid(bad):
    with pytest.raises(ValueError):
        american_to_implied(bad)


@pytest.mark.parametrize("method", DEVIG_METHODS)
def test_devig_sums_to_one(method):
    p_over, p_under = devig(-140, 110, method)
    assert p_over + p_under == pytest.approx(1.0)


@pytest.mark.parametrize("method", DEVIG_METHODS)
def test_devig_favourite_stays_favourite(method):
    p_over, p_under = devig(-140, 110, method)
    assert p_over > 0.5 > p_under


@pytest.mark.parametrize("method", DEVIG_METHODS)
def test_devig_symmetric_line_is_fifty_fifty(method):
    p_over, p_under = devig(-110, -110, method)
    assert p_over == pytest.approx(0.5)
    assert p_under == pytest.approx(0.5)


def test_multiplicative_value():
    q1, q2 = 140 / 240, 100 / 210
    p_over, _ = devig(-140, 110, "multiplicative")
    assert p_over == pytest.approx(q1 / (q1 + q2))


def test_additive_value():
    q1, q2 = 140 / 240, 100 / 210
    p_over, _ = devig(-140, 110, "additive")
    assert p_over == pytest.approx(q1 - (q1 + q2 - 1) / 2)


def test_power_value():
    q1, q2 = 140 / 240, 100 / 210
    p_over, p_under = devig(-140, 110, "power")
    k = __import__("math").log(p_over) / __import__("math").log(q1)
    assert q2 ** k == pytest.approx(p_under)


def test_methods_shade_longshot_differently():
    # Power and Shin remove more vig from the underdog than multiplicative does.
    mult = devig(-300, 240, "multiplicative")[0]
    power = devig(-300, 240, "power")[0]
    shin = devig(-300, 240, "shin")[0]
    assert power > mult
    assert shin > mult


def test_unknown_method():
    with pytest.raises(ValueError):
        devig(-110, -110, "magic")


@pytest.mark.parametrize(
    "price, expected",
    [
        ("−260", 260 / 360),  # DraftKings sends U+2212 MINUS SIGN
        ("-260", 260 / 360),
        (-260, 260 / 360),
        ("+150", 100 / 250),
        ("150", 100 / 250),
        (150, 100 / 250),
        (" −800 ", 800 / 900),
    ],
)
def test_american_to_implied_parses_strings(price, expected):
    assert american_to_implied(price) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["", "abc", "−50"])
def test_american_to_implied_rejects_bad_strings(bad):
    with pytest.raises(ValueError):
        american_to_implied(bad)


# --- Logit-shift (odds-ratio) vig model: p = sigma(logit q - c) -------------


def test_apply_logit_shift_zero_c_is_identity():
    assert apply_logit_shift(0.7, 0.0) == pytest.approx(0.7)


def test_apply_logit_shift_value():
    q, c = 0.7, 0.2
    expected = 1 / (1 + math.exp(-(math.log(q / (1 - q)) - c)))
    assert apply_logit_shift(q, c) == pytest.approx(expected)


def _add_vig(p: float, c: float) -> float:
    """Inverse of apply_logit_shift: the book's q for a Fair Probability p."""
    return 1 / (1 + math.exp(-(math.log(p / (1 - p)) + c)))


def test_calibrate_logit_shift_fair_market_is_zero():
    assert calibrate_logit_shift(0.5, 0.5) == pytest.approx(0.0, abs=1e-12)
    assert calibrate_logit_shift(0.7, 0.3) == pytest.approx(0.0, abs=1e-12)


def test_calibrate_logit_shift_positive_with_overround():
    q_over, q_under = american_to_implied("−110"), american_to_implied("−110")
    c = calibrate_logit_shift(q_over, q_under)
    assert c > 0
    assert apply_logit_shift(q_over, c) + apply_logit_shift(q_under, c) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("p_over, c", [(0.5, 0.05), (0.62, 0.09), (0.15, 0.2), (0.93, 0.12)])
def test_calibrate_logit_shift_recovers_known_vig(p_over, c):
    q_over, q_under = _add_vig(p_over, c), _add_vig(1 - p_over, c)
    c_hat = calibrate_logit_shift(q_over, q_under)
    assert c_hat == pytest.approx(c, abs=1e-9)
    assert apply_logit_shift(q_over, c_hat) == pytest.approx(p_over, abs=1e-9)
    assert apply_logit_shift(q_under, c_hat) == pytest.approx(1 - p_over, abs=1e-9)


def test_calibrate_logit_shift_negative_when_underround():
    # A market summing below 1 (e.g. a promo) gives c < 0.
    assert calibrate_logit_shift(0.45, 0.45) < 0


@pytest.mark.parametrize(
    "over, under",
    [("−110", "−110"), ("−140", "+110"), (-140, 110), ("−800", "+500"), ("+150", "−190")],
)
def test_devig_main_line_sums_to_one(over, under):
    p_over, p_under = devig_main_line(over, under)
    assert abs(p_over + p_under - 1) < 1e-9


def test_devig_main_line_matches_calibrated_shift():
    q_over, q_under = american_to_implied(-140), american_to_implied(110)
    c = calibrate_logit_shift(q_over, q_under)
    p_over, p_under = devig_main_line(-140, 110)
    assert p_over == pytest.approx(apply_logit_shift(q_over, c))
    assert p_under == pytest.approx(apply_logit_shift(q_under, c))


def test_devig_main_line_recovers_synthetic_fair_values():
    # Fair 0.62 / 0.38, vig added with known c, then priced as the book would.
    p_over, c = 0.62, 0.09
    q_over, q_under = _add_vig(p_over, c), _add_vig(1 - p_over, c)
    over = -100 * q_over / (1 - q_over)  # q > 0.5 -> negative American odds
    under = 100 * (1 - q_under) / q_under  # q < 0.5 -> positive American odds
    got_over, got_under = devig_main_line(over, under)
    assert got_over == pytest.approx(p_over, abs=1e-9)
    assert got_under == pytest.approx(1 - p_over, abs=1e-9)


def test_devig_main_line_heavily_one_sided():
    p_over, p_under = devig_main_line("−800", "+500")
    assert abs(p_over + p_under - 1) < 1e-9
    assert american_to_implied("+500") > p_under > 0
    assert american_to_implied("−800") > p_over > 0.85


def test_calibrated_c_lowers_a_rung():
    # c from the Main Line, applied to a one-sided "X+" Rung at −260.
    q_over, q_under = american_to_implied("−115"), american_to_implied("−105")
    c = calibrate_logit_shift(q_over, q_under)
    q_rung = american_to_implied("−260")
    assert apply_logit_shift(q_rung, c) < q_rung
