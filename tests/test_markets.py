import pytest

from ffp.markets import MainLine


@pytest.mark.parametrize("line, threshold", [(71.5, 72), (5.5, 6), (0.5, 1), (37.5, 38)])
def test_over_a_half_point_line_is_y_at_least_the_next_integer(line, threshold):
    assert MainLine(line, -110, -110, "draftkings").over_threshold == threshold


# Sprint 2 review finding 10: one UTC time parser for the books, the schedule and --now.
@pytest.mark.parametrize(
    "text",
    [
        "2026-11-17T01:15:00.0000000Z",  # DraftKings, 7-digit fraction
        "2026-11-17T01:15:00.000Z",  # FanDuel
        "2026-11-17T01:15:00Z",  # our own kickoff format
        "2026-11-17T01:15:00",  # no offset: taken as UTC
        "2026-11-16T20:15:00-05:00",  # an offset: converted
    ],
)
def test_parse_utc_reads_every_kickoff_spelling(text):
    from datetime import datetime, timezone

    from ffp.markets import format_utc, parse_utc

    moment = parse_utc(text)
    assert moment == datetime(2026, 11, 17, 1, 15, tzinfo=timezone.utc)
    assert format_utc(moment) == "2026-11-17T01:15:00Z"
