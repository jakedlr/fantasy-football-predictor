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
    """Where DraftKings lists one Scoring Stat: category and subcategory names as the league response spells them."""

    slug: str  # short name used in payload keys: dk_<slug>_ladder, dk_<slug>_ou
    category: str
    ladder: str
    main_line: str | None  # None: the stat has no O/U, and there's no dk_<slug>_ou payload
    # More subcategories merged into dk_<slug>_ladder when the league lists them; never an error when it doesn't.
    extra_ladders: tuple[str, ...] = ()


DK_STATS = {
    "receiving_yards": DkStat("rec_yds", "Receiving Props", "Receiving Yards", "Rec Yards O/U"),
    "receptions": DkStat("receptions", "Receiving Props", "Receptions", "Receptions O/U"),
    # Anytime, 1st TD and 2+ are in "TD Scorer"; 3+ has its own subcategory. Only the anytime price is
    # needed to fit TDs, so a missing "3+ TDs" just means no 3+ Rungs.
    "rush_rec_tds": DkStat("tds", "TD Scorers", "TD Scorer", None, extra_ladders=("3+ TDs",)),
}


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
FD_TD_SCORER_TAB = "td-scorer-props"


def fd_tabs(stats: Iterable[str]) -> list[str]:
    """The event-page tabs that hold these stats' markets, each once, in first-asked order."""
    stats = list(stats)
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


def _get(http: Http, book: str, name: str, url: str, on_response: OnResponse | None) -> dict:
    payload = http.get_json(url)
    if on_response is not None:
        on_response(book, name, url, payload)
    return payload
