"""`ffp push FILE`: send an Expected Points push to the Leaderboard's `/api/expected-points` (S11.3).

The body is checked against `site/schema/expected-points.schema.json` here first, then signed as
`site/contracts/push-signature.json` says (ADR 0006): header X-FFP-Timestamp is Unix seconds, and
X-FFP-Signature is "sha256=" + hex HMAC-SHA256, under LEADERBOARD_PUSH_SECRET's UTF-8 bytes, of
timestamp + "." + body. The secret comes from the environment, else from `.env.leaderboard`
(written by scripts/setup_cloudflare.sh). It is never printed or logged.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path

DEFAULT_URL = "https://ffp-leaderboard.pages.dev/api/expected-points"
SECRET_NAME = "LEADERBOARD_PUSH_SECRET"
# The site refuses a generatedAt over 15 minutes before its clock (400). A push older than this is not
# sent: it leaves room for the send, its one retry (about 70 s) and a little clock skew.
SITE_MAX_AGE = timedelta(minutes=15)
MAX_AGE = timedelta(minutes=13)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_instant(text: str) -> datetime:
    """An RFC 3339 instant ending in Z, as the push's generatedAt."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))
DEFAULT_SECRET_FILE = Path(".env.leaderboard")
TIMESTAMP_HEADER = "X-FFP-Timestamp"
SIGNATURE_HEADER = "X-FFP-Signature"
MAX_BODY_BYTES = 1_048_576  # push-signature.json maxBodyBytes
USER_AGENT = "ffp-push/0.1"  # generic: no names or contact details
TIMEOUT_SECONDS = 30
RETRY_WAIT_SECONDS = 10

# The schema lives with the site (site/schema/), two levels above this package in a checkout or an
# editable install. FFP_SCHEMA overrides the path: the Docker image `pip install .`s the package (not
# editable, so that path doesn't exist there), copies the schema in and sets FFP_SCHEMA to it.
_REPO_SCHEMA = Path(__file__).resolve().parents[2] / "site" / "schema" / "expected-points.schema.json"


class PushError(Exception):
    """A push that can't be sent or was refused; the message says why, without the secret."""


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """The X-FFP-Signature value for `body` signed at `timestamp` (push-signature.json)."""
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


# --- the schema ----------------------------------------------------------------------------------


@cache
def _validator():
    import jsonschema

    path = Path(os.environ.get("FFP_SCHEMA", _REPO_SCHEMA))
    schema = json.loads(path.read_text())
    checker = jsonschema.FormatChecker(formats=())  # only the formats below, checked without extra packages
    checker.checks("date-time", raises=ValueError)(_is_date_time)
    return jsonschema.Draft202012Validator(schema, format_checker=checker)


def _is_date_time(value: object) -> bool:
    if not isinstance(value, str):
        return True  # the "type" keyword's business
    datetime.fromisoformat(value)  # ValueError: not a date-time
    return "T" in value


def schema_errors(payload: object) -> list[str]:
    """Each way `payload` breaks the Expected Points schema, as "path: message"; [] if it's valid."""
    errors = sorted(_validator().iter_errors(payload), key=lambda e: list(map(str, e.absolute_path)))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(top level)'}: {e.message}" for e in errors]


# --- the secret ----------------------------------------------------------------------------------


def load_secret(environ: Mapping[str, str] = os.environ, path: Path = DEFAULT_SECRET_FILE) -> str:
    """LEADERBOARD_PUSH_SECRET from `environ`, else from the KEY='value' lines of `path`."""
    if environ.get(SECRET_NAME):
        return environ[SECRET_NAME]
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() == SECRET_NAME:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            if value:
                return value
    raise PushError(f"no {SECRET_NAME}: set it in the environment or in {path} (scripts/setup_cloudflare.sh)")


# --- sending -------------------------------------------------------------------------------------

# The Function's answers (site/functions/api/expected-points.ts): 204 accepted, also for a repeat
# of the stored push (same generatedAt), which is why a retry resends the same body; 429 too soon
# after the stored push (skipped, not an error); the rest fail.
_REFUSED = {
    400: "the site says the push is invalid",
    401: "the site rejected the signature or timestamp (check LEADERBOARD_PUSH_SECRET and the clock)",
    409: "the site says this push is older than the push the site holds",
    413: "the site says the push is too large",
}
_SERVER = {500: "the site has no LEADERBOARD_PUSH_SECRET configured (re-run the deploy) or failed"}


@dataclass(frozen=True)
class Sent:
    """What the site did with a push: accepted (2xx), or skipped (429: too soon after the last one)."""

    accepted: bool
    note: str = ""

    @property
    def skipped(self) -> bool:
        return not self.accepted


def send(
    body: bytes,
    url: str,
    secret: str,
    *,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> Sent:
    """POST `body`, signed, to `url`. One retry of the same body, freshly signed, after a 5xx or a
    network error; a 429 is skipped and other 4xx fail, both at once. Raises PushError."""
    if len(body) > MAX_BODY_BYTES:
        raise PushError(f"push too large: {len(body)} bytes, the site takes at most {MAX_BODY_BYTES}")
    for attempt in (1, 2):
        try:
            _post(body, url, secret, str(int(now())))
            return Sent(accepted=True)
        except urllib.error.HTTPError as error:
            detail = _reply_text(error)
            if error.code == 429:
                wait = error.headers.get("Retry-After", "?")
                return Sent(accepted=False, note=f"under 5 minutes after the last accepted push "
                                                 f"(Retry-After {wait} s): {detail}")
            if error.code < 500:
                reason = _REFUSED.get(error.code, "the site refused the push")
                raise PushError(f"HTTP {error.code}: {reason}: {detail}") from None
            failure = f"HTTP {error.code}: {_SERVER.get(error.code, 'server error')}: {detail}"
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:  # refused, reset, DNS, timeout
            failure = f"network error: {getattr(error, 'reason', error)}"
        if attempt == 1:
            sleep(RETRY_WAIT_SECONDS)
    raise PushError(f"push failed twice; last: {failure}")


def _post(body: bytes, url: str, secret: str, timestamp: str) -> str:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign(secret, timestamp, body),
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", "replace")


def _reply_text(error: urllib.error.HTTPError, limit: int = 500) -> str:
    try:
        text = error.read().decode("utf-8", "replace").strip()
    except OSError:
        text = ""
    return (text[:limit] + "…" if len(text) > limit else text) or "(no body)"


# --- the command ---------------------------------------------------------------------------------


def add_parser(commands) -> None:
    """Register `ffp push` on the CLI's subcommands."""
    p = commands.add_parser("push", help="send an Expected Points push (ffp slate's output) to the Leaderboard")
    p.add_argument("file", type=Path, help="the push JSON, e.g. expected-points.json from ffp slate")
    p.add_argument("--url", default=DEFAULT_URL, help=f"where to POST it (default {DEFAULT_URL})")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    try:
        body = args.file.read_bytes()
    except OSError as error:
        return _fail(f"can't read {args.file}: {error.strerror}")
    try:
        payload = json.loads(body)
    except ValueError as error:
        return _fail(f"{args.file} is not JSON: {error}")
    errors = schema_errors(payload)
    if errors:
        shown = "\n  ".join(errors[:20]) + (f"\n  … and {len(errors) - 20} more" if len(errors) > 20 else "")
        return _fail(f"{args.file} fails site/schema/expected-points.schema.json, not sent:\n  {shown}")
    age = _now() - parse_instant(payload["generatedAt"])
    if age > MAX_AGE:
        return _fail(f"{args.file} was generated {age.total_seconds() / 60:.0f} minutes ago; the site refuses a push "
                     f"over {SITE_MAX_AGE.total_seconds() / 60:.0f} minutes old, so it was not sent")
    try:
        sent = send(body, args.url, load_secret())
    except PushError as error:
        return _fail(f"push to {args.url} failed: {error}")
    if sent.skipped:  # not a failure: the site already holds a push from the last 5 minutes
        print(f"Skipped by {args.url}: {sent.note}")
        return 0
    print(f"Pushed {len(payload['players'])} players (week {payload['week']}, "
          f"generated {payload['generatedAt']}) to {args.url}")
    return 0


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1
