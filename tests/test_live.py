"""Live sources (S2.1/S2.7): what we need → sportsbook requests → raw payloads, served here from fixtures."""

import copy
import json
from pathlib import Path

import pytest

from ffp.sources import draftkings, live
from ffp.sources.http import Http

FIX = Path(__file__).resolve().parents[1] / "research" / "fixtures"
DK_SUB = "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusnj/v1/leagues/88808/categories/{}/subcategories/{}"
FD_EVENT = "https://sbapi.nj.sportsbook.fanduel.com/api/event-page?_ak={}&eventId={}&tab={}"
MNF, THURSDAY = "36275768", "36305232"


def load(name):
    return json.loads((FIX / name).read_text())


def site():
    """The fixture each endpoint URL stands in for."""
    return {
        live.DK_LEAGUE_URL: load("dk_nfl_league.json"),
        DK_SUB.format(1342, 16570): load("dk_nfl_rec_yds_ladder.json"),
        DK_SUB.format(1342, 14114): load("dk_nfl_rec_yds_ou.json"),
        live.FD_LEAGUE_URL: load("fd_nfl_league.json"),
        FD_EVENT.format(live.FD_APP_KEY, MNF, "receiving-props"): load("fd_nfl_event_receiving_tab.json"),
        FD_EVENT.format(live.FD_APP_KEY, MNF, "rushing-props"): load("fd_nfl_event_rushing_tab.json"),
        FD_EVENT.format(live.FD_APP_KEY, THURSDAY, "receiving-props"): load(
            "fd_nfl_event_receiving_tab_not_posted.json"),
    }


class Site:
    """A fake fetch serving the committed fixtures by URL, recording every request."""

    def __init__(self, pages=None):
        self.pages = site() if pages is None else pages
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.pages:
            return 404, b"{}", {}
        return 200, json.dumps(self.pages[url]).encode(), {}


@pytest.fixture
def fake(tmp_path):
    fetch = Site()
    return fetch, Http(tmp_path, fetch=fetch, sleep=lambda s: None)


def test_the_endpoints_are_the_researched_ones():
    assert live.DK_LEAGUE_URL == "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusnj/v1/leagues/88808"
    assert live.FD_LEAGUE_URL == ("https://sbapi.nj.sportsbook.fanduel.com/api/content-managed-page"
                                  f"?page=CUSTOM&customPageId=nfl&_ak={live.FD_APP_KEY}&timezone=America/New_York")
    assert live.FD_RECEIVING_TAB == "receiving-props"


# --- DraftKings -------------------------------------------------------------------


def test_draftkings_receiving_yards_is_the_league_then_its_ladder_then_its_main_line(fake):
    fetch, http = fake
    payloads = live.fetch_draftkings(http, ["receiving_yards"])
    assert fetch.calls == [live.DK_LEAGUE_URL, DK_SUB.format(1342, 16570), DK_SUB.format(1342, 14114)]
    assert list(payloads) == ["dk_league", "dk_rec_yds_ladder", "dk_rec_yds_ou"]
    assert payloads["dk_rec_yds_ladder"] == load("dk_nfl_rec_yds_ladder.json")


def test_draftkings_payloads_feed_the_draftkings_parser(fake):
    payloads = live.fetch_draftkings(fake[1], ["receiving_yards"])
    markets = draftkings.parse_prop_markets(payloads["dk_rec_yds_ladder"], payloads["dk_rec_yds_ou"],
                                            "receiving_yards")
    zay = next(m for m in markets if m.player == "Zay Flowers")
    assert zay.main_line is not None and zay.ladder


def test_draftkings_subcategory_ids_come_from_the_league_by_name(tmp_path):
    pages = site()
    league = pages[live.DK_LEAGUE_URL]
    for sub in league["subcategories"]:
        if sub["name"] == "Receiving Yards":
            sub["id"] = 99999  # DK renumbered it
    pages[DK_SUB.format(1342, 99999)] = pages.pop(DK_SUB.format(1342, 16570))
    fetch = Site(pages)
    live.fetch_draftkings(Http(tmp_path, fetch=fetch, sleep=lambda s: None), ["receiving_yards"])
    assert DK_SUB.format(1342, 99999) in fetch.calls


def test_draftkings_category_names_match_despite_stray_whitespace():
    assert live.dk_subcategory_ids(load("dk_nfl_league.json"), "Passing Props", "Pass Yards") == [(1000, 16569)]


def test_a_name_held_by_several_subcategories_fetches_each_and_merges_them(tmp_path):
    pages = site()
    league = pages[live.DK_LEAGUE_URL]
    league["subcategories"].append({"id": 18958, "categoryId": 1342, "name": "Rec Yards O/U"})
    other = copy.deepcopy(pages[DK_SUB.format(1342, 14114)])
    for market in other["markets"]:
        market["id"] = f"x{market['id']}"
    for selection in other["selections"]:
        selection["marketId"] = f"x{selection['marketId']}"
    pages[DK_SUB.format(1342, 18958)] = other
    seen = []
    payloads = live.fetch_draftkings(Http(tmp_path, fetch=Site(pages), sleep=lambda s: None), ["receiving_yards"],
                                     on_response=lambda book, name, url, payload: seen.append((name, url)))
    merged, first = payloads["dk_rec_yds_ou"], pages[DK_SUB.format(1342, 14114)]
    assert len(merged["markets"]) == 2 * len(first["markets"])
    assert len(merged["selections"]) == 2 * len(first["selections"])
    assert [e["id"] for e in merged["events"]] == [e["id"] for e in first["events"]]  # an event is listed once
    assert [n for n, _ in seen].count("dk_rec_yds_ou") == 2  # each raw response is still seen


@pytest.mark.parametrize("rename, missing", [
    (("subcategories", "Rec Yards O/U"), '"Rec Yards O/U"'),
    (("subcategories", "Receiving Yards"), '"Receiving Yards"'),
    (("categories", "Receiving Props"), '"Receiving Props"'),
])
def test_a_renamed_subcategory_or_category_is_an_error_naming_it(tmp_path, rename, missing):
    pages = site()
    kind, name = rename
    for entry in pages[live.DK_LEAGUE_URL][kind]:
        if entry["name"].strip() == name:
            entry["name"] = name + " (new)"
    fetch = Site(pages)
    with pytest.raises(live.SubcategoryNotFound, match=missing):
        live.fetch_draftkings(Http(tmp_path, fetch=fetch, sleep=lambda s: None), ["receiving_yards"])
    assert fetch.calls == [live.DK_LEAGUE_URL]  # nothing fetched on a guess


def test_an_unknown_stat_is_rejected_before_any_request(fake):
    fetch, http = fake
    with pytest.raises(ValueError, match="rushing_tds"):
        live.fetch_draftkings(http, ["rushing_tds"])
    assert fetch.calls == []


def test_the_league_response_is_reused_from_the_cache(fake):
    fetch, http = fake
    live.fetch_draftkings(http, ["receiving_yards"])
    live.fetch_draftkings(http, ["receiving_yards"])
    assert len(fetch.calls) == 3


# --- FanDuel ----------------------------------------------------------------------


def test_fanduel_league_is_the_content_managed_nfl_page(fake):
    fetch, http = fake
    assert live.fetch_fanduel_league(http) == load("fd_nfl_league.json")
    assert fetch.calls == [live.FD_LEAGUE_URL]


def test_fanduel_events_fetch_each_requested_event_tab_in_order(fake):
    fetch, http = fake
    payloads = live.fetch_fanduel_events(http, [MNF, int(THURSDAY)])
    assert fetch.calls == [FD_EVENT.format(live.FD_APP_KEY, MNF, "receiving-props"),
                           FD_EVENT.format(live.FD_APP_KEY, THURSDAY, "receiving-props")]
    assert list(payloads) == [("fd_event", MNF, "receiving-props"), ("fd_event", THURSDAY, "receiving-props")]
    assert payloads[("fd_event", MNF, "receiving-props")] == load("fd_nfl_event_receiving_tab.json")


def test_fanduel_tabs_are_a_parameter(fake):
    fetch, http = fake
    payloads = live.fetch_fanduel_events(http, [MNF], tabs=["receiving-props", "rushing-props"])
    assert list(payloads) == [("fd_event", MNF, "receiving-props"), ("fd_event", MNF, "rushing-props")]
    assert len(fetch.calls) == 2


def test_no_events_means_no_requests(fake):
    fetch, http = fake
    assert live.fetch_fanduel_events(http, []) == {}
    assert fetch.calls == []


# --- on_response ------------------------------------------------------------------


def test_on_response_sees_every_response_with_its_book_name_and_full_url(fake):
    fetch, http = fake
    seen = []

    def on_response(book, name, url, payload):
        seen.append((book, name, url, payload))

    dk = live.fetch_draftkings(http, ["receiving_yards"], on_response=on_response)
    fd_league = live.fetch_fanduel_league(http, on_response=on_response)
    fd = live.fetch_fanduel_events(http, [MNF], on_response=on_response)
    assert [(b, n) for b, n, _, _ in seen] == [
        ("draftkings", "dk_league"), ("draftkings", "dk_rec_yds_ladder"), ("draftkings", "dk_rec_yds_ou"),
        ("fanduel", "fd_league"), ("fanduel", f"fd_event_{MNF}_receiving-props"),
    ]
    assert [u for _, _, u, _ in seen] == fetch.calls  # the full URL, app key included: snapshots strip it
    assert [p for _, _, _, p in seen] == [*dk.values(), fd_league, *fd.values()]


def test_a_failed_request_is_not_handed_on(tmp_path):
    from ffp.sources.http import FetchError

    seen = []
    http = Http(tmp_path, fetch=Site({}), sleep=lambda s: None)
    with pytest.raises(FetchError):
        live.fetch_fanduel_events(http, ["1"], on_response=lambda *a: seen.append(a))
    assert seen == []
