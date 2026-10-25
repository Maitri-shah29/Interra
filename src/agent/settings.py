"""Settings saved from the dashboard, applied over the deployment's environment.

The overrides live in one JSON file, on the persistent volume when deployed.
The supervisor (``agent.serve``) reads it each time it starts the page and the
workers; the settings page writes it and asks the supervisor to restart them.
Values are stored as plain text, so the volume must stay private.

The admin password is ``INTERRA_ADMIN_PASSWORD`` in the deployment itself. It
cannot be changed from the page, so a leaked session cannot lock the owner out.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_FILE = Path("artifacts/settings/overrides.json")

ADMIN_PASSWORD = "INTERRA_ADMIN_PASSWORD"
SITE_PASSWORD = "INTERRA_SITE_PASSWORD"
SETTINGS_FILE = "INTERRA_SETTINGS_FILE"
SUPERVISOR_PID = "INTERRA_SUPERVISOR_PID"
MIN_PASSWORD_LENGTH = 8

NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_VALUE_LENGTH = 4096
# Names that control access, process start-up or the interpreter itself.
PROTECTED_NAMES = frozenset({
    ADMIN_PASSWORD, SITE_PASSWORD, SETTINGS_FILE, SUPERVISOR_PID,
    "PATH", "HOME", "PORT", "SHELL", "USER", "TMPDIR",
})
PROTECTED_PREFIXES = ("LD_", "PYTHON", "RAILWAY_")
SECRET_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD")

# The names the code reads, with what each one changes. Any other valid name
# can still be added from the page.
KNOWN_SETTINGS: tuple[tuple[str, str], ...] = (
    ("LIVEKIT_URL", "LiveKit project URL (wss://…)"),
    ("LIVEKIT_API_KEY", "LiveKit API key"),
    ("LIVEKIT_API_SECRET", "LiveKit API secret"),
    ("INTERRA_FDB_AGENT_NAME", "Dispatch name of the voice worker; the Voice page needs interra-benchmark"),
    ("INTERRA_FDB_LLM_PROVIDER", "livekit or ollama"),
    ("INTERRA_FDB_LLM_MODEL", "LLM through LiveKit Inference"),
    ("INTERRA_FDB_LLM_TEMPERATURE", "LLM temperature"),
    ("INTERRA_FDB_STT_MODEL", "Speech-to-text model"),
    ("INTERRA_FDB_STT_FALLBACK_MODELS", "Comma-separated speech-to-text fallbacks"),
    ("INTERRA_FDB_TTS_MODEL", "Text-to-speech model"),
    ("INTERRA_FDB_TTS_FALLBACK_MODELS", "Comma-separated text-to-speech fallbacks"),
    ("INTERRA_FDB_VOICE_ID", "Text-to-speech voice"),
    ("INTERRA_FDB_LATENCY_PROFILE", "Latency profile of the mock tools"),
    ("INTERRA_FDB_COMMIT_GATE", "auto, on or off; auto gates only a named worker such as the judge page's"),
    ("INTERRA_FDB_COMMIT_QUIET_SECONDS", "Quiet window before a tool call commits"),
    ("INTERRA_FDB_UNFINISHED_TURN_HOLD_SECONDS", "Hold after an unfinished turn"),
    ("INTERRA_FDB_ENDPOINTING_MIN_DELAY", "Minimum end-of-turn delay"),
    ("INTERRA_FDB_ENDPOINTING_MAX_DELAY", "Maximum end-of-turn delay"),
    ("INTERRA_FDB_SESSION_COOLDOWN_SECONDS", "Pause between benchmark sessions"),
    ("INTERRA_AGENT_LOAD_THRESHOLD", "CPU load above which a worker stops taking rooms (LiveKit default 0.7)"),
    ("INTERRA_AGENT_IDLE_PROCESSES", "Warm job processes per worker"),
    ("INTERRA_OLLAMA_MODEL", "Ollama model when the provider is ollama"),
    ("INTERRA_OLLAMA_OPENAI_URL", "Ollama OpenAI-compatible URL"),
)


def settings_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    path = Path(env.get(SETTINGS_FILE) or DEFAULT_SETTINGS_FILE)
    return path if path.is_absolute() else ROOT / path


def check_name(name: str) -> str:
    """The name itself, or ValueError saying why it cannot be set from the page."""
    if not isinstance(name, str) or not NAME.match(name):
        raise ValueError(f"{name!r} is not a variable name (A-Z, 0-9 and _, starting with a letter)")
    if name in PROTECTED_NAMES or name.startswith(PROTECTED_PREFIXES):
        raise ValueError(f"{name} can only be set in the deployment")
    return name


def check_value(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if len(value) > MAX_VALUE_LENGTH:
        raise ValueError(f"{name} is longer than {MAX_VALUE_LENGTH} characters")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be one line")
    return value


def load_overrides(path: Path) -> dict[str, str]:
    """Saved overrides. A missing file is none; an unreadable one raises ValueError."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ValueError(f"{path} could not be read: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold an object")
    overrides: dict[str, str] = {}
    for name, value in data.items():
        overrides[check_name(name)] = check_value(name, value)
    return overrides


def save_overrides(path: Path, overrides: Mapping[str, str]) -> None:
    """Write the whole file at once, readable only by this user."""
    checked = {check_name(name): check_value(name, value) for name, value in overrides.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(sorted(checked.items())), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_changes(current: Mapping[str, str], changes: Any) -> dict[str, str]:
    """``{"set": {name: value}, "unset": [name]}`` applied to the saved overrides."""
    if not isinstance(changes, dict):
        raise ValueError("send an object with set and unset")
    to_set = changes.get("set", {})
    to_unset = changes.get("unset", [])
    if not isinstance(to_set, dict) or not isinstance(to_unset, list):
        raise ValueError("set must be an object and unset a list")
    updated = dict(current)
    for name in to_unset:
        updated.pop(check_name(name), None)
    for name, value in to_set.items():
        updated[check_name(name)] = check_value(name, value)
    return updated


def is_secret(name: str) -> bool:
    return any(word in name for word in SECRET_WORDS)


def masked(value: str) -> str:
    if not value:
        return ""
    return "••••" + value[-4:] if len(value) >= 12 else "••••"


def describe(environ: Mapping[str, str], overrides: Mapping[str, str]) -> list[dict[str, Any]]:
    """One row per known or overridden name. Secret values never leave masked."""
    known = dict(KNOWN_SETTINGS)
    names = list(known) + sorted(name for name in overrides if name not in known)
    rows = []
    for name in names:
        if name in overrides:
            source, value = "dashboard", overrides[name]
        elif name in environ:
            source, value = "deployment", environ[name]
        else:
            source, value = "unset", ""
        secret = is_secret(name)
        rows.append({
            "name": name,
            "description": known.get(name, ""),
            "secret": secret,
            "value": masked(value) if secret else value,
            "source": source,
            # Saved but not yet in this process: the restart has not happened.
            "pending": name in overrides and environ.get(name) != overrides[name],
        })
    return rows


def admin_password(environ: Mapping[str, str] | None = None) -> str | None:
    """The admin password, or None when the settings page is switched off."""
    env = os.environ if environ is None else environ
    password = env.get(ADMIN_PASSWORD, "")
    return password if len(password) >= MIN_PASSWORD_LENGTH else None


def same_password(given: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(given.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def _session_signature(password: str, expires: int) -> str:
    key = hashlib.sha256(b"interra-admin-session\x00" + password.encode("utf-8")).digest()
    return hmac.new(key, str(expires).encode("ascii"), hashlib.sha256).hexdigest()


def issue_session(password: str, *, now: float | None = None, ttl: int = 12 * 3600) -> str:
    """A signed expiry. It outlives restarts and dies when the password changes."""
    expires = int(time.time() if now is None else now) + ttl
    return f"{expires}.{_session_signature(password, expires)}"


def valid_session(cookie: str | None, password: str, *, now: float | None = None) -> bool:
    if not cookie or "." not in cookie:
        return False
    expires_text, signature = cookie.split(".", 1)
    if not expires_text.isdigit():
        return False
    expires = int(expires_text)
    if expires < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(signature, _session_signature(password, expires))


def basic_auth_password(header: str | None) -> str | None:
    """The password from an ``Authorization: Basic`` header; the user name is ignored."""
    if not header or not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return decoded.split(":", 1)[1] if ":" in decoded else None


class LoginThrottle:
    """Refuse sign-ins after too many failures in a window, from anyone.

    The page serves each request on its own thread, hence the lock.
    """

    def __init__(self, limit: int = 10, window: float = 900.0):
        self.limit = limit
        self.window = window
        self._failures: deque[float] = deque()
        self._lock = threading.Lock()

    def _trim(self, now: float) -> None:
        while self._failures and now - self._failures[0] > self.window:
            self._failures.popleft()

    def allowed(self, now: float | None = None) -> bool:
        with self._lock:
            self._trim(time.monotonic() if now is None else now)
            return len(self._failures) < self.limit

    def failed(self, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            self._trim(moment)
            self._failures.append(moment)
