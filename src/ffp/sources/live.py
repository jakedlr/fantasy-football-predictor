"""Live sportsbook requests: what a run needs → the few requests that answer it → raw payloads.

Nothing here parses prop prices: `draftkings.parse_prop_markets` and the
FanDuel parser do that with what this returns. Endpoints, ids and the public
FanDuel app key are from research/sportsbook-markets.md (re-checked
2026-11-15). DraftKings subcategory ids change (Interceptions O/U 18958 came
and went), so they're looked up by name in the league response, never
hard-coded.

`on_response(book, name, url, payload)`, when given, is called once for every
successful response, with the full URL (app key included: the snapshots
module strips it).
"""

from collections.abc import Callable, Iterable
from typing import NamedTuple

from . import draftkings, fanduel
from .http import Http

OnResponse = Callable[[str, str, str, dict], None]

# --- DraftKings ----------------------------------------------------------------------

DK_BOOK = draftkings.BOOK
DK_LEAGUE_ID = 88808  # NFL
DK_STATE = "dkusnj"  # New Jersey
DK_LEAGUE_URL = f"https://sportsbook-nash.draftkings.com/api/sportscontent/{DK_STATE}/v1/leagues/{DK_LEAGUE_ID}"
DK_SUBCATEGORY_URL = DK_LEAGUE_URL + "/categories/{category_id}/subcategories/{subcategory_id}"


class DkStat(NamedTuple):
    """Where DraftKings lists one Scoring Stat or Usage Stat: category and subcategory names as the league response spells them."""

    slug: str  # short name used in payload keys: dk_<slug>_ladder, dk_<slug>_ou
    category: str
    ladder: str
    main_line: str | None  # None: the stat has no O/U, and there's no dk_<slug>_ou payload
    # More subcategories merged into dk_<slug>_ladder when the league lists them; never an error when it doesn't.
    extra_ladders: tuple[str, ...] = ()


DK_STATS = {
    "receiving_yards": DkStat("rec_yds", "Receiving Props", "Receiving Yards", "Rec Yards O/U"),
    "receptions": DkStat("receptions", "Receiving Props", "Receptions", "Receptions O/U"),
    "passing_yards": DkStat("pass_yds", "Passing Props", "Pass Yards", "Pass Yards O/U"),
    "passing_tds": DkStat("pass_tds", "Passing Props", "Pass TDs", "Pass TDs O/U"),
    "passing_interceptions": DkStat("ints", "Passing Props", "Interceptions", "Interceptions O/U"),
    "rushing_yards": DkStat("rush_yds", "Rushing Props", "Rush Yards", "Rush Yards O/U"),
    "carries": DkStat("rush_att", "Rushing Props", "Rush Attempts", "Rush Attempts O/U"),  # a Usage Stat
    # Anytime, 1st TD and 2+ are in "TD Scorer"; 3+ has its own subcategory. Only the anytime price is
    # needed to fit TDs, so a missing "3+ TDs" just means no 3+ Rungs.
    "rush_rec_tds": DkStat("tds", "TD Scorers", "TD Scorer", None, extra_ladders=("3+ TDs",)),
}


class DkSubcategory(NamedTuple):
    """A DraftKings subcategory by its category and subcategory names, and its payload name."""

    payload: str  # the payload key: dk_<payload>
    category: str
    subcategory: str


# Sprint 6, kickers and D/ST: team totals and the TD scorer (D/ST TDs) feed the D/ST model; the
# kicker subcategories are fetched only so the Snapshot keeps them.
DK_TEAM_TOTALS = DkSubcategory("team_totals", "Team Props", "Team Totals")
DK_TD_SCORER = DkSubcategory("td_scorer", "TD Scorers", "TD Scorer")
DK_KICKER_SUBCATEGORIES = (
    DkSubcategory("fg_made_ladder", "Special Teams Props", "FG Made"),
    DkSubcategory("pat_made_ladder", "Special Teams Props", "PAT Made"),
    DkSubcategory("kicking_pts_ladder", "Special Teams Props", "Kicking Pts"),
    DkSubcategory("kicking_pts_ou", "Special Teams Props", "Kicking Pts O/U"),
)


class SubcategoryNotFound(LookupError):
    """The DraftKings league response no longer lists a category or subcategory by the name we look for."""


def dk_subcategory_ids(league: dict, category: str, subcategory: str) -> list[tuple[int, int]]:
    """Every (category id, subcategory id) the league lists under these names. Names are matched with surrounding
    whitespace ignored ("Passing Props " has a trailing space). Raises SubcategoryNotFound naming what's missing."""
    category_ids = [c["id"] for c in league.get("categories", []) if c.get("name", "").strip() == category]
    if not category_ids:
        raise SubcategoryNotFound(f'DraftKings league {DK_LEAGUE_ID} has no category "{category}" (renamed?)')
    in_category = [s for s in league.get("subcategories", []) if s.get("categoryId") in category_ids]
    ids = [(s["categoryId"], s["id"]) for s in in_category if s.get("name", "").strip() == subcategory]
    if not ids:
        listed = ", ".join(f'"{s.get("name", "").strip()}"' for s in in_category)
        raise SubcategoryNotFound(
            f'DraftKings league {DK_LEAGUE_ID} has no subcategory "{subcategory}" in category "{category}" '
            f"(renamed, or not offered now?); it lists {listed or 'none'}")
    return ids


def fetch_draftkings(http: Http, stats: Iterable[str], on_response: OnResponse | None = None) -> dict[str, dict]:
    """The league response, then each stat's Ladder and Main Line (O/U) subcategory responses, as
    {"dk_league": ..., "dk_<slug>_ladder": ..., "dk_<slug>_ou": ...} in request order. A name the league lists
    under several subcategory ids is fetched from each, merged into one payload, and so are a stat's
    extra_ladders. A stat without an O/U (rush_rec_tds) has no "dk_<slug>_ou": parse it with
    main_line_json None."""
    wanted = list(stats)
    unknown = [s for s in wanted if s not in DK_STATS]
    if unknown:
        raise ValueError(f"no DraftKings markets known for {', '.join(unknown)} (known: {', '.join(DK_STATS)})")

    league = _get(http, DK_BOOK, "dk_league", DK_LEAGUE_URL, on_response)
    # Look every name up before fetching any of them: a renamed subcategory fails without wasted requests.
    plan = []
    for stat in wanted:
        dk = DK_STATS[stat]
        ladder_ids = dk_subcategory_ids(league, dk.category, dk.ladder)
        for extra in dk.extra_ladders:
            try:
                ladder_ids += dk_subcategory_ids(league, dk.category, extra)
            except SubcategoryNotFound:
                pass
        plan.append((f"dk_{dk.slug}_ladder", ladder_ids))
        if dk.main_line is not None:
            plan.append((f"dk_{dk.slug}_ou", dk_subcategory_ids(league, dk.category, dk.main_line)))

    payloads = {"dk_league": league}
    for name, ids in plan:
        parts = [
            _get(http, DK_BOOK, name, DK_SUBCATEGORY_URL.format(category_id=c, subcategory_id=s), on_response)
            for c, s in ids
        ]
        payloads[name] = parts[0] if len(parts) == 1 else _merge(parts)
    return payloads


def dk_stat_requests(league: dict, stat: str) -> list[tuple[str, list[str]]]:
    """A stat's (payload name, subcategory URLs) for its Ladder and Main Line, looked up in `league`:
    SubcategoryNotFound when the league doesn't list it (yet)."""
    dk = DK_STATS[stat]
    ladder_ids = dk_subcategory_ids(league, dk.category, dk.ladder)
    for extra in dk.extra_ladders:
        try:
            ladder_ids += dk_subcategory_ids(league, dk.category, extra)
        except SubcategoryNotFound:
            pass
    out = [(f"dk_{dk.slug}_ladder", _subcategory_urls(ladder_ids))]
    if dk.main_line is not None:
        out.append((f"dk_{dk.slug}_ou", _subcategory_urls(dk_subcategory_ids(league, dk.category, dk.main_line))))
    return out


def dk_subcategory_request(league: dict, sub: DkSubcategory) -> tuple[str, list[str]]:
    """A named subcategory's (payload name, URLs), looked up in `league`."""
    return f"dk_{sub.payload}", _subcategory_urls(dk_subcategory_ids(league, sub.category, sub.subcategory))


def _subcategory_urls(ids: list[tuple[int, int]]) -> list[str]:
    return [DK_SUBCATEGORY_URL.format(category_id=c, subcategory_id=s) for c, s in ids]


def fetch_dk_batch(http, requests: Iterable[tuple[str, list[str]]],
                   on_response: OnResponse | None = None) -> dict[str, dict | Exception]:
    """Every request's payload by name, fetched as one batch (each URL once): a name listed under several
    subcategories is merged into one payload, and a name whose any part failed is answered by its error."""
    requests = list(requests)
    urls = list(dict.fromkeys(u for _, parts in requests for u in parts))
    got = dict(zip(urls, get_many(http, urls)))
    out = {}
    for name, parts in requests:
        answers = [got[u] for u in parts]
        error = next((a for a in answers if isinstance(a, Exception)), None)
        if error is not None:
            out[name] = error
            continue
        if on_response is not None:
            for url, payload in zip(parts, answers):
                on_response(DK_BOOK, name, url, payload)
        out[name] = answers[0] if len(answers) == 1 else _merge(answers)
    return out


def get_many(http, urls: list[str]) -> list:
    """Each URL's JSON in order, or the FetchError that request failed with: as one batch when `http` is a
    Book's view (several in flight, ffp.sources.http.BookHttp), else one at a time. A Book left out
    (PushedBack) or a stopped run (Cancelled) is raised, not answered."""
    from ffp.sources.http import FetchError, PushedBack

    if hasattr(http, "get_many"):
        return http.get_many(urls)
    out = []
    for url in urls:
        try:
            out.append(http.get_json(url))
        except PushedBack:
            raise
        except FetchError as error:
            out.append(error)
    return out


def fetch_dk_league(http: Http, on_response: OnResponse | None = None) -> dict:
    """The DraftKings league response: its Games, their main spread, total and moneyline, and the
    category catalogue."""
    return _get(http, DK_BOOK, "dk_league", DK_LEAGUE_URL, on_response)


def fetch_dk_subcategory(http: Http, league: dict, sub: DkSubcategory, on_response: OnResponse | None = None) -> dict:
    """One subcategory's response, looked up by name in `league` (merged when listed under several ids)."""
    parts = [_get(http, DK_BOOK, f"dk_{sub.payload}", DK_SUBCATEGORY_URL.format(category_id=c, subcategory_id=s),
                  on_response)
             for c, s in dk_subcategory_ids(league, sub.category, sub.subcategory)]
    return parts[0] if len(parts) == 1 else _merge(parts)


def _merge(responses: list[dict]) -> dict:
    """Several subcategory responses as one: their lists concatenated, each event (by id) listed once."""
    merged = {key: list(value) if isinstance(value, list) else value for key, value in responses[0].items()}
    for response in responses[1:]:
        for key, value in response.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
    if "events" in merged:
        events: dict = {}
        for event in merged["events"]:
            events.setdefault(event["id"], event)
        merged["events"] = list(events.values())
    return merged


# --- FanDuel -------------------------------------------------------------------------

FD_BOOK = fanduel.BOOK
FD_APP_KEY = "FhMFpcPWXMeyZxOx"  # the website's public app key (research/sportsbook-markets.md)
FD_HOST = "https://sbapi.nj.sportsbook.fanduel.com"
FD_LEAGUE_URL = (f"{FD_HOST}/api/content-managed-page?page=CUSTOM&customPageId=nfl&_ak={FD_APP_KEY}"
                 "&timezone=America/New_York")
FD_EVENT_URL = FD_HOST + "/api/event-page?_ak=" + FD_APP_KEY + "&eventId={event_id}&tab={tab}"

FD_PASSING_TAB = "passing-props"
FD_RECEIVING_TAB = fanduel.TABS["receiving_yards"]  # tabs the parser reads live in fanduel.TABS
FD_RUSHING_TAB = "rushing-props"
FD_TD_SCORER_TAB = fanduel.TABS[fanduel.TD_STAT]


def fd_tabs(stats: Iterable[str]) -> list[str]:
    """The event-page tabs that hold these stats' markets, each once, in first-asked order.

    A stat FanDuel doesn't offer (fanduel.NOT_OFFERED) needs no tab and is skipped.
    """
    stats = [s for s in stats if s not in fanduel.NOT_OFFERED]
    unknown = [s for s in stats if s not in fanduel.TABS]
    if unknown:
        raise ValueError(f"no FanDuel tab known for {', '.join(unknown)} (known: {', '.join(fanduel.TABS)})")
    return list(dict.fromkeys(fanduel.TABS[s] for s in stats))


def fetch_fanduel_league(http: Http, on_response: OnResponse | None = None) -> dict:
    """The content-managed NFL page: the event list, with FanDuel event ids at attachments.events{}."""
    return _get(http, FD_BOOK, "fd_league", FD_LEAGUE_URL, on_response)


def fetch_fanduel_events(
    http: Http,
    event_ids: Iterable[str | int],
    tabs: Iterable[str] = (FD_RECEIVING_TAB,),
    on_response: OnResponse | None = None,
) -> dict[tuple[str, str, str], dict]:
    """Each event's page for each tab, as {("fd_event", event id, tab): payload}, event by event in order."""
    tabs = list(tabs)
    payloads = {}
    for event_id in map(str, event_ids):
        for tab in tabs:
            url = FD_EVENT_URL.format(event_id=event_id, tab=tab)
            payloads[("fd_event", event_id, tab)] = _get(http, FD_BOOK, f"fd_event_{event_id}_{tab}", url,
                                                         on_response)
    return payloads


def fd_event_url(event_id: str | int, tab: str) -> str:
    return FD_EVENT_URL.format(event_id=event_id, tab=tab)


def _get(http: Http, book: str, name: str, url: str, on_response: OnResponse | None) -> dict:
    payload = http.get_json(url)
    if on_response is not None:
        on_response(book, name, url, payload)
    return payload
