"""`ffp push`: the signed POST of an Expected Points push to /api/expected-points (S11.3, ADR 0006).

Every request goes to a fake HTTP server on localhost; nothing here reaches the live site."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ffp import push
from ffp.cli import main

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "site" / "contracts" / "push-signature.json").read_text())
EXAMPLE = ROOT / "site" / "schema" / "examples" / "expected-points.json"
SECRET = "f" * 64


def test_signature_matches_the_contract_vector():
    v = CONTRACT["vector"]
    assert push.sign(v["secret"], v["timestamp"], v["body"].encode()) == v["signature"]


def test_header_names_come_from_the_contract():
    assert push.TIMESTAMP_HEADER == CONTRACT["headers"]["timestamp"]
    assert push.SIGNATURE_HEADER == CONTRACT["headers"]["signature"]
    assert push.MAX_BODY_BYTES == CONTRACT["maxBodyBytes"]


# --- the schema ------------------------------------------------------------------------------


def test_the_example_push_is_valid():
    assert push.schema_errors(json.loads(EXAMPLE.read_text())) == []


def test_schema_errors_name_the_field():
    payload = json.loads(EXAMPLE.read_text())
    payload["players"][0]["position"] = "LB"
    del payload["idMap"]
    errors = push.schema_errors(payload)
    assert any("players/0/position" in e for e in errors)
    assert any("idMap" in e for e in errors)


def test_a_date_time_that_is_not_a_date_is_an_error():
    payload = json.loads(EXAMPLE.read_text())
    payload["generatedAt"] = "not-a-timeZ"
    assert any("generatedAt" in e for e in push.schema_errors(payload))


# --- the secret ------------------------------------------------------------------------------


def test_secret_from_the_environment_first(tmp_path):
    (tmp_path / ".env.leaderboard").write_text(f"LEADERBOARD_PUSH_SECRET='{'a' * 64}'\n")
    assert push.load_secret({"LEADERBOARD_PUSH_SECRET": SECRET}, tmp_path / ".env.leaderboard") == SECRET


def test_secret_falls_back_to_the_env_file_as_the_wizard_writes_it(tmp_path):
    env_file = tmp_path / ".env.leaderboard"
    env_file.write_text(f"NTFY_TOPIC='ffp-abc'\nLEADERBOARD_PUSH_SECRET='{SECRET}'\n")
    assert push.load_secret({}, env_file) == SECRET


@pytest.mark.parametrize("line", [f"LEADERBOARD_PUSH_SECRET={SECRET}", f'LEADERBOARD_PUSH_SECRET="{SECRET}"',
                                  f"export LEADERBOARD_PUSH_SECRET='{SECRET}'"])
def test_secret_file_quoting(tmp_path, line):
    env_file = tmp_path / ".env.leaderboard"
    env_file.write_text(f"# comment\n\n{line}\n")
    assert push.load_secret({}, env_file) == SECRET


def test_no_secret_anywhere_is_a_clear_error(tmp_path):
    with pytest.raises(push.PushError, match="LEADERBOARD_PUSH_SECRET"):
        push.load_secret({}, tmp_path / "missing")


# --- the fake site ---------------------------------------------------------------------------


class FakeSite:
    """A localhost HTTP server that answers each POST with the next scripted (status, body)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server's name
                body = self.rfile.read(int(self.headers["Content-Length"]))
                site.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, text, *extra = site.replies.pop(0) if site.replies else (204, "")
                if status == "drop":  # close without answering: a network error for the client
                    self.close_connection = True
                    return
                data = text.encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in (extra[0] if extra else {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/expected-points"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site_factory():
    sites = []

    def make(*replies):
        sites.append(FakeSite(replies))
        return sites[-1]

    yield make
    for s in sites:
        s.close()


def _send(site, body=None, **kw):
    body = body if body is not None else EXAMPLE.read_bytes()
    kw.setdefault("sleep", lambda s: None)
    return push.send(body, site.url, SECRET, now=lambda: 1795370400, **kw)


def test_send_signs_the_exact_body_and_sets_the_headers(site_factory):
    site = site_factory((200, '{"ok":true,"players":3}'))
    _send(site)
    [req] = site.requests
    headers = {k.lower(): v for k, v in req["headers"].items()}
    assert req["path"] == "/api/expected-points"
    assert req["body"] == EXAMPLE.read_bytes()
    assert headers["x-ffp-timestamp"] == "1795370400"
    assert headers["x-ffp-signature"] == push.sign(SECRET, "1795370400", EXAMPLE.read_bytes())
    assert headers["content-type"] == "application/json"
    assert headers["user-agent"].startswith("ffp-push/")
    assert SECRET not in json.dumps(headers)


def test_send_retries_once_on_a_5xx(site_factory):
    site = site_factory((503, "busy"), (200, "{}"))
    waits = []
    _send(site, sleep=waits.append)
    assert len(site.requests) == 2 and waits == [push.RETRY_WAIT_SECONDS]


def test_send_retries_once_on_a_network_error(site_factory):
    site = site_factory(("drop", ""), (200, "{}"))
    _send(site)
    assert len(site.requests) == 2


def test_two_5xx_in_a_row_fail(site_factory):
    site = site_factory((500, "a"), (502, "b"), (200, "{}"))
    with pytest.raises(push.PushError, match="502"):
        _send(site)
    assert len(site.requests) == 2


def test_the_retry_is_signed_with_a_fresh_timestamp(site_factory):
    site = site_factory((500, "a"), (200, "{}"))
    clock = iter([1795370400, 1795370405])
    push.send(EXAMPLE.read_bytes(), site.url, SECRET, now=lambda: next(clock), sleep=lambda s: None)
    assert [{k.lower(): v for k, v in r["headers"].items()}["x-ffp-timestamp"] for r in site.requests] == ["1795370400", "1795370405"]


@pytest.mark.parametrize("status, words", [(401, "signature"), (400, "invalid"), (413, "too large"),
                                           (409, "older than the push the site holds")])
def test_client_errors_fail_at_once_with_a_clear_message(site_factory, status, words):
    site = site_factory((status, '{"error":"details from the site"}'))
    with pytest.raises(push.PushError, match=words) as raised:
        _send(site)
    assert len(site.requests) == 1
    assert "details from the site" in str(raised.value)
    assert SECRET not in str(raised.value)


def test_a_body_over_the_limit_is_refused_before_sending(site_factory):
    site = site_factory()
    with pytest.raises(push.PushError, match="too large"):
        _send(site, body=b" " * (push.MAX_BODY_BYTES + 1))
    assert site.requests == []


# --- the command -----------------------------------------------------------------------------


def test_cli_push_validates_signs_and_sends(site_factory, monkeypatch, capsys):
    site = site_factory((200, '{"ok":true}'))
    monkeypatch.setenv("LEADERBOARD_PUSH_SECRET", SECRET)
    assert main(["push", str(EXAMPLE), "--url", site.url]) == 0
    assert len(site.requests) == 1
    out = capsys.readouterr().out
    assert "3 players" in out and SECRET not in out


def test_cli_push_refuses_an_invalid_file_without_sending(site_factory, monkeypatch, tmp_path, capsys):
    site = site_factory()
    monkeypatch.setenv("LEADERBOARD_PUSH_SECRET", SECRET)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schemaVersion": 2}))
    assert main(["push", str(bad), "--url", site.url]) != 0
    assert site.requests == []
    assert "schema" in capsys.readouterr().err


def test_cli_push_refuses_a_file_that_is_not_json(site_factory, monkeypatch, tmp_path, capsys):
    site = site_factory()
    monkeypatch.setenv("LEADERBOARD_PUSH_SECRET", SECRET)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["push", str(bad), "--url", site.url]) != 0
    assert "not JSON" in capsys.readouterr().err


def test_cli_push_reports_a_rejected_signature(site_factory, monkeypatch, capsys):
    site = site_factory((401, '{"error":"bad signature"}'))
    monkeypatch.setenv("LEADERBOARD_PUSH_SECRET", SECRET)
    assert main(["push", str(EXAMPLE), "--url", site.url]) != 0
    err = capsys.readouterr().err
    assert "401" in err and SECRET not in err


def test_cli_push_without_a_secret(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("LEADERBOARD_PUSH_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env.leaderboard here
    assert main(["push", str(EXAMPLE), "--url", "http://127.0.0.1:9/x"]) != 0
    assert "LEADERBOARD_PUSH_SECRET" in capsys.readouterr().err


def test_default_url_is_the_live_site():
    assert push.DEFAULT_URL == "https://ffp-leaderboard.pages.dev/api/expected-points"


# --- the Function's answers (site/functions/api/expected-points.ts, S11.4) --------------------


def test_204_is_accepted(site_factory):
    site = site_factory((204, ""))
    assert _send(site).accepted


def test_a_retry_resends_the_same_body_so_a_lost_204_is_answered_204_again(site_factory):
    site = site_factory(("drop", ""), (204, ""))
    assert _send(site).accepted
    assert site.requests[0]["body"] == site.requests[1]["body"] == EXAMPLE.read_bytes()


def test_429_is_skipped_not_retried(site_factory):
    site = site_factory((429, '{"error":"under 5 minutes after the stored push"}', {"Retry-After": "120"}))
    result = _send(site)
    assert not result.accepted and result.skipped and len(site.requests) == 1
    assert "120" in result.note


def test_a_500_says_the_site_is_not_configured(site_factory):
    site = site_factory((500, '{"error":"not configured"}'), (500, '{"error":"not configured"}'))
    with pytest.raises(push.PushError, match="LEADERBOARD_PUSH_SECRET"):
        _send(site)
    assert len(site.requests) == 2


def test_cli_push_skipped_by_the_site_exits_0(site_factory, monkeypatch, capsys):
    site = site_factory((429, '{"error":"under 5 minutes"}', {"Retry-After": "90"}))
    monkeypatch.setenv("LEADERBOARD_PUSH_SECRET", SECRET)
    assert main(["push", str(EXAMPLE), "--url", site.url]) == 0
    assert "skipped" in capsys.readouterr().out.lower()


def test_cli_push_older_than_the_stored_one_fails(site_factory, monkeypatch, capsys):
    site = site_factory((409, '{"error":"older than the stored push (2026-11-22T18:00:00Z)"}'))
    monkeypatch.setenv("LEADERBOARD_PUSH_SECRET", SECRET)
    assert main(["push", str(EXAMPLE), "--url", site.url]) == 1
    assert "409" in capsys.readouterr().err and len(site.requests) == 1
