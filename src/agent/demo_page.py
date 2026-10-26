"""Local pages for the judge recording.

Overview, Voice, Camera, Home, Replay, Logs and Results are separate pages. The
session pages show the live transcript and the gate's latest decision; Logs
reads the worker trace for any session on this machine; the scored run from
4 October 2026 can be replayed from its own trace with original timestamps.

The pages live in ``agent/web`` and share partials from ``agent/web/partials``.
This module renders them, mints LiveKit tokens, streams the worker trace for
one room, lists the sessions in it, and serves the recorded run. Settings
edits the environment the workers start with, behind INTERRA_ADMIN_PASSWORD;
INTERRA_SITE_PASSWORD, when set, puts every page behind a browser password.

Start the voice worker with INTERRA_FDB_AGENT_NAME=interra-benchmark, the
camera worker and the home worker (``python -m agent.home_livekit dev``) in
other terminals, then this page:

    python -m agent.demo_page
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import functools
import json
import os
import re
import signal
import time
import uuid
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from livekit.api import AccessToken, RoomAgentDispatch, RoomConfiguration, VideoGrants

# Imported here, on the main thread: LiveKit plugins refuse to register from
# the request threads that would otherwise import this module first.
from agent.fdb_livekit import FdbConfig
from agent.smart_home import AIR_PURIFIER, SmartHome
from agent.settings import (
    SITE_PASSWORD,
    SUPERVISOR_PID,
    LoginThrottle,
    admin_password,
    apply_changes,
    basic_auth_password,
    describe,
    issue_session,
    load_overrides,
    same_password,
    save_overrides,
    settings_path,
    valid_session,
)

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)

PORT = 8780
VOICE_AGENT = "interra-benchmark"
CAMERA_AGENT = "interra-camera-troubleshooter"
HOME_AGENT = "interra-home"
AGENTS = {"voice": VOICE_AGENT, "camera": CAMERA_AGENT, "home": HOME_AGENT}
WEB_ROOT = Path(__file__).resolve().with_name("web")
PAGES = {
    "/": "index",
    "/voice": "voice",
    "/camera": "camera",
    "/home": "house",
    "/replay": "replay",
    "/logs": "logs",
    "/results": "results",
    "/settings": "settings",
}
PARTIALS = ("head", "sprite", "header", "footer")
STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
STATIC_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*\.(css|js|svg)$")
# Tokens are only minted for rooms this page names.
ROOM_NAME = re.compile(r"^demo-(voice|camera|home)-[a-z0-9]{6,16}$")
# Any room in the trace can be read back.
TRACE_ROOM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
LIVEKIT_NAMES = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
SESSION_COOKIE = "interra_admin"
SESSION_TTL = 12 * 3600
MAX_BODY = 64 * 1024
LOGIN_THROTTLE = LoginThrottle()

# The recording shown beside the live take: housing_11, a bedroom and price
# self-correction from the 4 October 2026 run, with its official strict result.
SCORED_RUN = ROOT / "docs" / "results" / "kaggle-20261004"
SCORED_TRACE = SCORED_RUN / "livekit-agent.jsonl"
SCORED_REPORT = SCORED_RUN / "interra_elevenlabs_pass_rate_report.json"
SCORED_ROOM = "eval-31fcf764"
SCORED_SCENARIO = "housing_11"


def _trace_path() -> Path:
    """The workers write here; INTERRA_FDB_TRACE_DIR moves it for both."""
    folder = Path(os.environ.get("INTERRA_FDB_TRACE_DIR", "artifacts/fdb_v3"))
    if not folder.is_absolute():
        folder = ROOT / folder
    return folder / "livekit-agent.jsonl"


TRACE_PATH = _trace_path()


def _shown_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def page_html(name: str = "index") -> str:
    """One page with its shared head, icons, header and footer filled in."""
    text = (WEB_ROOT / (name + ".html")).read_text(encoding="utf-8")
    for part in PARTIALS:
        text = text.replace("{{" + part + "}}", (WEB_ROOT / "partials" / (part + ".html")).read_text(encoding="utf-8"))
    return text


def static_file(name: str) -> tuple[bytes, str] | None:
    """A top-level stylesheet, script or icon from ``agent/web``; nothing else."""
    if not STATIC_NAME.match(name):
        return None
    path = WEB_ROOT / name
    if not path.is_file():
        return None
    return path.read_bytes(), STATIC_TYPES[path.suffix]


def mint_token(mode: str, room: str) -> str:
    if mode not in AGENTS or not ROOM_NAME.match(room) or not room.startswith("demo-" + mode + "-"):
        raise ValueError("mode and room do not match")
    url = os.environ.get("LIVEKIT_URL", "")
    key = os.environ.get("LIVEKIT_API_KEY", "")
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if not (url and key and secret):
        raise RuntimeError("missing LiveKit configuration")
    agent = AGENTS[mode]
    grants = VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
    dispatch = RoomConfiguration(agents=[RoomAgentDispatch(agent_name=agent)])
    return (
        AccessToken(key, secret)
        .with_identity(mode + "-" + uuid.uuid4().hex[:8])
        .with_name("Interra judge")
        .with_grants(grants)
        .with_room_config(dispatch)
        .with_ttl(datetime.timedelta(hours=1))
        .to_jwt()
    )


def config_payload() -> dict[str, Any]:
    """Names and settings the page shows. No secret leaves this process."""
    url = os.environ.get("LIVEKIT_URL", "")
    payload: dict[str, Any] = {
        "voice_agent": VOICE_AGENT,
        "camera_agent": CAMERA_AGENT,
        "home_agent": HOME_AGENT,
        "livekit_configured": all(os.environ.get(name) for name in LIVEKIT_NAMES),
        "livekit_host": urlparse(url).hostname or "",
        "trace": _shown_path(TRACE_PATH),
    }
    try:
        config = FdbConfig.from_env()
    except ValueError as exc:
        payload["config_error"] = str(exc)
        return payload
    payload.update(
        models={
            "stt": config.stt_model,
            "llm": config.llm_model,
            "llm_provider": config.llm_provider,
            "ollama_model": config.ollama_model,
            "tts": config.tts_model,
        },
        temperature=config.llm_temperature,
        commit_quiet_seconds=config.commit_quiet_seconds,
        unfinished_turn_hold_seconds=config.unfinished_turn_hold_seconds,
    )
    return payload


def home_payload() -> dict[str, Any]:
    """The home a new session starts with, and the device the page can add."""
    return {"devices": SmartHome().views(), "unseen_device": AIR_PURIFIER}


def home_walkthrough() -> dict[str, Any]:
    """The scripted home demo, run through the real controller with no LiveKit room."""
    from agent.home_demo import walkthrough

    _, events = asyncio.run(walkthrough())
    return {"events": events}


def _read_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


@functools.lru_cache(maxsize=1)
def scored_replay() -> dict[str, Any]:
    """The recorded room's own trace and the official strict result for it.

    Nothing here is re-run or re-scored. The result is the pinned evaluator's
    entry for this scenario, or ``None`` when the report does not hold exactly one.
    """
    records = [record for record in _read_records(SCORED_TRACE) if record.get("room") == SCORED_ROOM]
    report = json.loads(SCORED_REPORT.read_text(encoding="utf-8"))
    matches = [
        entry for entry in report.get("scenario_results", [])
        if entry.get("scenario_id") == SCORED_SCENARIO
    ]
    return {
        "room": SCORED_ROOM,
        "scenario": SCORED_SCENARIO,
        "source": _shown_path(SCORED_TRACE),
        "report": _shown_path(SCORED_REPORT),
        "records": records,
        "result": matches[0] if len(matches) == 1 else None,
    }


def _session_mode(room: str) -> str:
    if room.startswith("demo-voice-"):
        return "voice"
    if room.startswith("demo-camera-"):
        return "camera"
    if room.startswith("demo-home-"):
        return "home"
    return "benchmark"


def list_sessions(path: Path, *, now: float | None = None, limit: int = 60) -> list[dict[str, Any]]:
    """Rooms in the local trace, newest first, with what happened in each."""
    rooms: dict[str, dict[str, Any]] = {}
    if path.is_file():
        for record in _read_records(path):
            room = record.get("room")
            moment = record.get("time")
            if not isinstance(room, str) or not isinstance(moment, (int, float)):
                continue
            entry = rooms.setdefault(room, {
                "room": room, "mode": _session_mode(room), "first": moment, "last": moment,
                "events": 0, "executed": 0, "dropped": 0, "closed": False,
            })
            entry["first"] = min(entry["first"], moment)
            entry["last"] = max(entry["last"], moment)
            entry["events"] += 1
            kind = record.get("kind")
            if kind == "tool_call":
                entry["executed"] += 1
            elif kind == "tool_call_dropped":
                entry["dropped"] += 1
            elif kind in {"session_closed", "session_cleanup"}:
                entry["closed"] = True
    current = time.time() if now is None else now
    sessions = sorted(rooms.values(), key=lambda entry: entry["last"], reverse=True)[:limit]
    for entry in sessions:
        entry["live"] = not entry["closed"] and current - entry["last"] < 120
    return sessions


def sessions_payload() -> dict[str, Any]:
    recorded: list[dict[str, Any]] = []
    try:
        replay = scored_replay()
        records = replay["records"]
        if records:
            times = [record["time"] for record in records if isinstance(record.get("time"), (int, float))]
            recorded.append({
                "room": SCORED_ROOM, "mode": "recorded", "recorded": True, "scenario": SCORED_SCENARIO,
                "first": min(times), "last": max(times), "events": len(records),
                "executed": sum(1 for record in records if record.get("kind") == "tool_call"),
                "dropped": sum(1 for record in records if record.get("kind") == "tool_call_dropped"),
                "closed": True, "live": False,
            })
    except (OSError, ValueError):
        pass
    return {"trace": _shown_path(TRACE_PATH), "sessions": recorded + list_sessions(TRACE_PATH)}


def read_trace_since(path: Path, room: str, start: int) -> tuple[list[dict[str, Any]], int]:
    """Read one room's complete trace lines after a byte offset. An unfinished line stays for later."""
    if not path.is_file():
        return [], start
    found: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(start)
        while True:
            position = handle.tell()
            line = handle.readline()
            if not line or not line.endswith("\n"):
                return found, position
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("room") == room:
                found.append(record)


def resume_offset(last_event_id: str | None, size: int, since: str | None = None) -> int:
    """Where a stream starts: after the last delivered batch on a reconnect,
    at the start when the page asks for the whole room, otherwise at the end."""
    if last_event_id and last_event_id.isdigit() and int(last_event_id) <= size:
        return int(last_event_id)
    if since == "0":
        return 0
    return size


def _event(record: dict[str, Any], event_id: int | None = None) -> bytes:
    head = b"id: " + str(event_id).encode("ascii") + b"\n" if event_id is not None else b""
    return head + b"data: " + json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n\n"


def settings_payload() -> dict[str, Any]:
    """What the settings page shows: each variable, where its value comes from, and
    whether a save can restart the processes from here."""
    path = settings_path()
    payload: dict[str, Any] = {"file": _shown_path(path), "restart_available": supervisor_pid() is not None}
    try:
        overrides = load_overrides(path)
    except ValueError as exc:
        payload["file_error"] = str(exc)
        overrides = {}
    payload["settings"] = describe(os.environ, overrides)
    return payload


def save_settings(changes: Any) -> dict[str, str]:
    """Apply one edit to the saved overrides. A file that cannot be read is replaced."""
    path = settings_path()
    try:
        current = load_overrides(path)
    except ValueError:
        current = {}
    updated = apply_changes(current, changes)
    save_overrides(path, updated)
    return updated


def supervisor_pid() -> int | None:
    """The supervisor that started this page, if it did; never some other parent."""
    value = os.environ.get(SUPERVISOR_PID, "")
    if not value.isdigit() or int(value) != os.getppid() or not hasattr(signal, "SIGHUP"):
        return None
    return int(value)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path == "/api/health":
            self._json({"ok": True})
            return
        if not self._site_allowed():
            return
        if path in PAGES:
            self._send(200, page_html(PAGES[path]).encode("utf-8"), "text/html; charset=utf-8", "no-cache")
        elif path.startswith("/static/"):
            found = static_file(path[len("/static/"):])
            if found is None:
                self._text(404, "not found")
            else:
                self._send(200, found[0], found[1], "no-cache")
        elif path == "/favicon.svg":
            self._send(200, (WEB_ROOT / "logo.svg").read_bytes(), "image/svg+xml", "max-age=3600")
        elif path == "/api/token":
            self._token(query)
        elif path == "/api/config":
            self._json(config_payload())
        elif path == "/api/home":
            self._json(home_payload())
        elif path == "/api/home/walkthrough":
            self._json(home_walkthrough())
        elif path == "/api/sessions":
            self._json(sessions_payload())
        elif path == "/api/replay":
            try:
                self._json(scored_replay())
            except (OSError, ValueError) as exc:
                self._text(500, "the recorded run could not be read: " + str(exc))
        elif path == "/api/events":
            self._events(query)
        elif path == "/api/admin/status":
            password = admin_password()
            self._json({"enabled": password is not None, "signed_in": bool(password and self._signed_in(password))})
        elif path == "/api/admin/settings":
            if self._admin_password() is not None:
                self._json(settings_payload())
        else:
            self._text(404, "not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        if not self._site_allowed():
            return
        if path not in {"/api/admin/login", "/api/admin/logout", "/api/admin/settings"}:
            self._text(404, "not found")
            return
        body = self._json_body()
        if body is None:
            return
        if path == "/api/admin/login":
            self._login(body)
        elif path == "/api/admin/logout":
            self._send_with_cookie(self._session_cookie("", 0), {"signed_in": False})
        elif self._admin_password() is not None:
            self._save(body)

    # ----- access -----

    def _site_allowed(self) -> bool:
        """With INTERRA_SITE_PASSWORD set, every page and API needs it as a browser password."""
        expected = os.environ.get(SITE_PASSWORD, "")
        if not expected:
            return True
        given = basic_auth_password(self.headers.get("Authorization"))
        if given is not None and same_password(given, expected):
            return True
        body = b"password required"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Interra", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        return False

    def _signed_in(self, password: str) -> bool:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return False
        morsel = cookie.get(SESSION_COOKIE)
        return valid_session(morsel.value if morsel else None, password)

    def _admin_password(self) -> str | None:
        """The admin password when this request is signed in; otherwise answer and return None."""
        password = admin_password()
        if password is None:
            self._text(403, "set INTERRA_ADMIN_PASSWORD (8 characters or more) in the deployment to use settings")
            return None
        if not self._signed_in(password):
            self._text(401, "sign in first")
            return None
        return password

    def _session_cookie(self, value: str, max_age: int) -> str:
        parts = [f"{SESSION_COOKIE}={value}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Strict"]
        if self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https":
            parts.append("Secure")
        return "; ".join(parts)

    def _json_body(self) -> Any:
        """The request's JSON, or None after refusing it. Only same-origin JSON is read,
        which a cross-site form cannot send."""
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._text(415, "send application/json")
            return None
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self._text(403, "cross-origin request refused")
            return None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._text(413, "request too large")
            return None
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._text(400, "request is not JSON")
            return None

    def _login(self, body: Any) -> None:
        password = admin_password()
        if password is None:
            self._text(403, "set INTERRA_ADMIN_PASSWORD (8 characters or more) in the deployment to use settings")
            return
        if not LOGIN_THROTTLE.allowed():
            self._text(429, "too many wrong passwords; try again in a few minutes")
            return
        given = body.get("password") if isinstance(body, dict) else None
        if not isinstance(given, str) or not same_password(given, password):
            LOGIN_THROTTLE.failed()
            time.sleep(1.0)
            self._text(401, "wrong password")
            return
        print("settings: signed in", flush=True)
        self._send_with_cookie(self._session_cookie(issue_session(password, ttl=SESSION_TTL), SESSION_TTL), {"signed_in": True})

    def _save(self, body: Any) -> None:
        try:
            updated = save_settings(body)
        except ValueError as exc:
            self._text(400, str(exc))
            return
        except OSError as exc:
            self._text(500, "the settings could not be saved: " + str(exc))
            return
        pid = supervisor_pid()
        names = sorted(updated)
        print("settings: saved " + (", ".join(names) if names else "no overrides"), flush=True)
        self._json({"saved": True, "restarting": pid is not None, "overrides": names})
        if pid is not None:
            # The answer is already sent; the supervisor now restarts this page too.
            os.kill(pid, signal.SIGHUP)

    def _send_with_cookie(self, cookie: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send(self, status: int, body: bytes, content_type: str, cache: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict[str, Any]) -> None:
        self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json", "no-store")

    def _text(self, status: int, message: str) -> None:
        self._send(status, message.encode("utf-8"), "text/plain; charset=utf-8", "no-store")

    def _token(self, query: dict[str, list[str]]) -> None:
        mode = (query.get("mode") or [""])[0]
        room = (query.get("room") or [""])[0]
        try:
            issued = mint_token(mode, room)
        except (ValueError, RuntimeError) as exc:
            self._text(400, str(exc))
            return
        self._json({"url": os.environ["LIVEKIT_URL"], "token": issued})

    def _events(self, query: dict[str, list[str]]) -> None:
        room = (query.get("room") or [""])[0]
        if not TRACE_ROOM.match(room):
            self._text(400, "unknown room")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        exists = TRACE_PATH.is_file()
        resumed = self.headers.get("Last-Event-ID")
        since = (query.get("since") or [None])[0]
        offset = resume_offset(resumed, TRACE_PATH.stat().st_size if exists else 0, since)
        hello = {"trace": _shown_path(TRACE_PATH), "exists": exists, "resumed": bool(resumed)}
        quiet_since = time.monotonic()
        try:
            self.wfile.write(b"retry: 1500\nevent: hello\ndata: " + json.dumps(hello).encode("utf-8") + b"\n\n")
            self.wfile.flush()
            while True:
                if TRACE_PATH.is_file() and TRACE_PATH.stat().st_size < offset:
                    offset = 0  # the trace was replaced; read the new file from the start
                records, offset = read_trace_since(TRACE_PATH, room, offset)
                if records:
                    # The last event of a batch carries the byte offset, so a
                    # reconnect resumes after it instead of skipping records.
                    chunks = [_event(record) for record in records[:-1]]
                    chunks.append(_event(records[-1], offset))
                    self.wfile.write(b"".join(chunks))
                    self.wfile.flush()
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since > 2.0:
                    self.wfile.write(b": keep\n\n")
                    self.wfile.flush()
                    quiet_since = time.monotonic()
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def log_message(self, fmt: str, *args: object) -> None:
        request = str(args[0]) if args else ""
        if request.startswith("GET /api/token"):
            print("GET /api/token")
            return
        if request.startswith(("GET /api/events", "GET /static/", "GET /api/sessions", "GET /api/health")):
            return
        super().log_message(fmt, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Interra judge page.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    missing = [name for name in LIVEKIT_NAMES if not os.environ.get(name)]
    print(f"http://{args.host}:{args.port}/", flush=True)
    if missing:
        print("LiveKit is not configured (" + ", ".join(missing) + "); the recorded replay still works.", flush=True)
    if admin_password() is None:
        print("Settings are off; set INTERRA_ADMIN_PASSWORD (8 characters or more) to edit them from the page.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
