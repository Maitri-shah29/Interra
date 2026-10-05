"""LiveKit voice-agent entry point for Full-Duplex-Bench v3.

The benchmark code and data remain external. Set ``INTERRA_FDB_V3_ROOT`` to the
official repository's ``v3`` directory so this module can load its mock backend.
No benchmark examples or expected answers are imported into the prompt.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from livekit.plugins import silero
from livekit.agents.types import APIConnectOptions

# Plugins register themselves with LiveKit Agents during import. LiveKit calls
# the process setup hook on a worker thread, so import optional providers while
# this module is being loaded on the process main thread instead.
if os.environ.get("INTERRA_FDB_LLM_PROVIDER", "livekit") == "ollama":
    from livekit.plugins import openai as _openai_plugin  # noqa: F401


def _model_fallbacks(name: str, default: str) -> tuple[str, ...]:
    configured = os.environ.get(name, default)
    return tuple(model.strip() for model in configured.split(",") if model.strip())


@dataclass(frozen=True)
class FdbConfig:
    fdb_v3_root: Path
    llm_provider: str = "livekit"
    llm_model: str = "openai/gpt-4.1-mini"
    ollama_model: str = "qwen3:8b"
    ollama_url: str = "http://localhost:11434/v1"
    stt_model: str = "deepgram/nova-3"
    stt_fallback_models: tuple[str, ...] = ("assemblyai/universal-3-5-pro",)
    tts_model: str = "cartesia/sonic-3"
    tts_fallback_models: tuple[str, ...] = ("deepgram/aura-2:athena",)
    voice_id: str = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
    latency_profile: str = "instant"
    trace_dir: Path = Path("artifacts/fdb_v3")
    session_cooldown_seconds: float = 3.0
    llm_temperature: float = 0.0
    endpointing_min_delay: float = 0.7
    endpointing_max_delay: float = 1.2
    unfinished_turn_hold_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.session_cooldown_seconds < 0:
            raise ValueError("session_cooldown_seconds must be non-negative")
        if not 0 <= self.endpointing_min_delay <= self.endpointing_max_delay:
            raise ValueError("endpointing delays must satisfy 0 <= min_delay <= max_delay")
        if self.unfinished_turn_hold_seconds < 0:
            raise ValueError("unfinished_turn_hold_seconds must be non-negative")

    @classmethod
    def from_env(cls) -> "FdbConfig":
        default_root = Path(".runtime/Full-Duplex-Bench/v3")
        return cls(
            fdb_v3_root=Path(os.environ.get("INTERRA_FDB_V3_ROOT", default_root)),
            llm_provider=os.environ.get("INTERRA_FDB_LLM_PROVIDER", "livekit"),
            llm_model=os.environ.get("INTERRA_FDB_LLM_MODEL", "openai/gpt-4.1-mini"),
            ollama_model=os.environ.get("INTERRA_OLLAMA_MODEL", "qwen3:8b"),
            ollama_url=os.environ.get("INTERRA_OLLAMA_OPENAI_URL", "http://localhost:11434/v1"),
            stt_model=os.environ.get("INTERRA_FDB_STT_MODEL", "deepgram/nova-3"),
            stt_fallback_models=_model_fallbacks(
                "INTERRA_FDB_STT_FALLBACK_MODELS", "assemblyai/universal-3-5-pro"
            ),
            tts_model=os.environ.get("INTERRA_FDB_TTS_MODEL", "cartesia/sonic-3"),
            tts_fallback_models=_model_fallbacks(
                "INTERRA_FDB_TTS_FALLBACK_MODELS", "deepgram/aura-2:athena"
            ),
            voice_id=os.environ.get("INTERRA_FDB_VOICE_ID", "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"),
            latency_profile=os.environ.get("INTERRA_FDB_LATENCY_PROFILE", "instant"),
            trace_dir=Path(os.environ.get("INTERRA_FDB_TRACE_DIR", "artifacts/fdb_v3")),
            session_cooldown_seconds=float(
                os.environ.get("INTERRA_FDB_SESSION_COOLDOWN_SECONDS", "3")
            ),
            llm_temperature=float(os.environ.get("INTERRA_FDB_LLM_TEMPERATURE", "0")),
            endpointing_min_delay=float(
                os.environ.get("INTERRA_FDB_ENDPOINTING_MIN_DELAY", "0.7")
            ),
            endpointing_max_delay=float(
                os.environ.get("INTERRA_FDB_ENDPOINTING_MAX_DELAY", "1.2")
            ),
            unfinished_turn_hold_seconds=float(
                os.environ.get("INTERRA_FDB_UNFINISHED_TURN_HOLD_SECONDS", "1.0")
            ),
        )

    def missing_requirements(self) -> list[str]:
        missing = [
            name
            for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
            if not os.environ.get(name)
        ]
        if not (self.fdb_v3_root / "mock_apis.py").is_file():
            missing.append(f"INTERRA_FDB_V3_ROOT ({self.fdb_v3_root / 'mock_apis.py'} not found)")
        return missing


def load_benchmark_module(fdb_v3_root: Path) -> ModuleType:
    path = fdb_v3_root / "mock_apis.py"
    if not path.is_file():
        raise FileNotFoundError(
            f"FDB-v3 mock backend not found at {path}. Set INTERRA_FDB_V3_ROOT "
            "to the official Full-Duplex-Bench/v3 directory."
        )
    spec = importlib.util.spec_from_file_location("interra_fdb_mock_apis", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load benchmark backend from {path}")
    module = importlib.util.module_from_spec(spec)
    sibling_path = str(fdb_v3_root.resolve())
    sys.path.insert(0, sibling_path)
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(sibling_path)
        except ValueError:
            pass
    return module


class TraceWriter:
    def __init__(self, trace_dir: Path):
        trace_dir.mkdir(parents=True, exist_ok=True)
        self.path = trace_dir / "livekit-agent.jsonl"
        self._lock = threading.Lock()

    def append(self, kind: str, **payload: Any) -> None:
        record = {"time": time.time(), "kind": kind, **payload}
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _call_key(name: str, arguments: dict[str, Any]) -> str:
    return json.dumps([name, arguments], sort_keys=True, ensure_ascii=False, default=str)


class ToolExecutor:
    """Run the official deterministic mock tools without blocking the event loop.

    One executor belongs to one room. A repeated call with identical arguments
    in the same room returns the first call's result instead of running and
    logging the tool again: a re-generated reply must not duplicate a side
    effect, and the backend is deterministic, so the answer is unchanged.
    Nothing is shared across rooms.
    """

    def __init__(
        self,
        registry: Any,
        room_name: str,
        trace: TraceWriter,
        telemetry_path: Path | None | str = "default",
    ):
        self.registry = registry
        self.room_name = room_name
        self.trace = trace
        # The benchmark scores this file. Offline replays pass None to skip it.
        self.telemetry_path: Path | None = (
            Path(tempfile.gettempdir()) / "agent_tool_calls.log"
            if telemetry_path == "default" else telemetry_path
        )
        self.calls: list[dict[str, Any]] = []
        self._completed: dict[str, asyncio.Future[str]] = {}

    async def call(self, name: str, **arguments: Any) -> str:
        key = _call_key(name, arguments)
        while (previous := self._completed.get(key)) is not None:
            try:
                result = await asyncio.shield(previous)
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if previous.cancelled() and not (task and task.cancelling()):
                    continue  # the first attempt was abandoned; run it ourselves
                raise
            self.trace.append(
                "tool_call_deduplicated", room=self.room_name,
                call={"function": name, "args": arguments},
            )
            return result
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._completed[key] = future
        try:
            result = await self._execute(name, arguments)
        except BaseException:
            # The call never reached the telemetry log, so a later identical
            # call must run instead of reusing this attempt.
            del self._completed[key]
            future.cancel()
            raise
        future.set_result(result)
        return result

    async def _execute(self, name: str, arguments: dict[str, Any]) -> str:
        started = time.time()
        status = "completed"
        try:
            result = await asyncio.to_thread(self.registry.call, name, **arguments)
        except Exception as exc:
            status = "failed"
            result = {"status": "error", "message": str(exc)}
        ended = time.time()
        call = {
            "function": name,
            "args": arguments,
            "timestamp_start": started,
            "timestamp_end": ended,
        }
        self.calls.append(call)
        if self.telemetry_path is not None:
            with self.telemetry_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"room": self.room_name, "call": call}) + "\n")
        self.trace.append("tool_call", room=self.room_name, status=status, call=call, result=result)
        return json.dumps(result, ensure_ascii=False)


# The official recorder keeps agent audio only until the wav ends plus 1.5s of
# trailing silence, then disconnects. A reply that starts after that window is
# scored as silence. These bounds keep the turn inside that window.
RECORDING_TAIL_S = 1.5


def benchmark_turn_handling(config: FdbConfig | None = None) -> dict[str, Any]:
    """Start the model during the utterance and commit the turn before the recorder leaves.

    ``turn_detection`` is left unset, so LiveKit Agents 1.8.3 uses its hosted
    semantic ``inference.TurnDetector``: ``min_delay`` applies when the turn
    sounds finished and ``max_delay`` when it does not. Preemptive generation
    only runs the LLM; LiveKit executes tools after the turn is committed.
    """
    config = config or FdbConfig(fdb_v3_root=Path("."))
    return {
        "endpointing": {
            "mode": "fixed",
            "min_delay": config.endpointing_min_delay,
            "max_delay": config.endpointing_max_delay,
        },
        "preemptive_generation": {
            "enabled": True,
            "preemptive_tts": True,
            "max_speech_duration": 180.0,
            "max_retries": 200,
        },
    }


def ollama_llm_kwargs(config: FdbConfig) -> dict[str, Any]:
    """Local Qwen must answer inside the recording window. Thinking is off only after a live probe."""
    kwargs: dict[str, Any] = {
        "model": config.ollama_model,
        "api_key": "ollama",
        "base_url": config.ollama_url,
        "temperature": config.llm_temperature,
        "parallel_tool_calls": False,
        "_strict_tool_schema": False,
    }
    if os.environ.get("INTERRA_OLLAMA_DISABLE_THINK") == "1":
        kwargs["extra_body"] = {"think": False}
    return kwargs


def livekit_speech_models(inference: Any, config: FdbConfig) -> tuple[Any, Any]:
    """Route speech through LiveKit with cross-provider failover and spaced retries."""
    retry_options = APIConnectOptions(max_retry=2, retry_interval=4.0, timeout=20.0)
    stt_options: dict[str, Any] = {
        "model": config.stt_model,
        "language": "en",
        "conn_options": retry_options,
    }
    if config.stt_fallback_models:
        stt_options["fallback"] = list(config.stt_fallback_models)
    tts_options: dict[str, Any] = {
        "model": config.tts_model,
        "voice": config.voice_id,
        "conn_options": retry_options,
    }
    if config.tts_fallback_models:
        tts_options["fallback"] = list(config.tts_fallback_models)
    return (
        inference.STT(**stt_options),
        inference.TTS(**tts_options),
    )


def livekit_llm_model(inference: Any, config: FdbConfig) -> Any:
    """Use a hosted tool-calling model under the same LiveKit project."""
    return inference.LLM(
        model=config.llm_model,
        extra_kwargs={"temperature": config.llm_temperature, "parallel_tool_calls": False},
    )


def _as_float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        raise TypeError(f"expected a number, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    normalized = str(value).strip().replace(",", "").replace("$", "")
    try:
        return float(normalized)
    except ValueError:
        pass
    units = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
        "fourteen": 14, "fifteen": 15, "sixteen": 16,
        "seventeen": 17, "eighteen": 18, "nineteen": 19,
        "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
        "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    }
    total = current = 0
    tokens = re.findall(r"[a-z]+", normalized.lower().replace("-", " "))
    if not tokens:
        raise ValueError(f"Invalid numeric value: {value!r}")
    for token in tokens:
        if token in {"and", "dollar", "dollars"}:
            continue
        if token in units:
            current += units[token]
        elif token == "hundred":
            current = max(current, 1) * 100
        elif token == "thousand":
            total += max(current, 1) * 1000
            current = 0
        else:
            raise ValueError(f"Invalid numeric value: {value!r}")
    return float(total + current)


def _as_int(value: Any) -> int:
    return int(_as_float(value))


_DIGIT_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_SPOKEN_SEPARATORS = {"dash", "hyphen", "dot", "point"}


def normalize_identifier(value: str) -> str:
    """Join explicitly spelled letters/digits without rewriting ordinary IDs.

    Speech arrives as ``"b o b one two"``, ``"p-5-2"`` or ``"k dash two"``.
    Hyphen- or dot-separated input is joined only when every part is a single
    character, so literal codes such as ``SKU-42`` keep their punctuation.
    Space-separated input also accepts short letter groups and digit runs
    (``"BOB 12"``), because a spoken identifier field never contains spaces.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    tokens = [token for token in re.split(r"[\s\-.]+", text) if token]
    spoken = [token for token in tokens if token.lower() not in _SPOKEN_SEPARATORS]
    if len(tokens) < 2 or not spoken:
        return text
    expanded: list[str] = []
    repeat = 1
    for token in spoken:
        lower = token.lower()
        if lower in {"double", "triple"}:
            repeat = 2 if lower == "double" else 3
            continue
        part = _DIGIT_WORDS.get(lower, token)
        expanded.append(part * repeat if len(part) == 1 else part)
        repeat = 1
    if repeat != 1 or not all(part.isalnum() for part in expanded):
        return text
    punctuated = bool(re.search(r"[-.]", text)) or len(spoken) != len(tokens)
    if punctuated:
        joinable = all(
            len(token) == 1 or token.lower() in _DIGIT_WORDS
            or token.lower() in {"double", "triple"}
            for token in spoken
        )
    else:
        joinable = all(len(part) <= 3 or part.isdigit() for part in expanded)
    return "".join(expanded).upper() if joinable else text


_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)
_MONTH_LOOKUP = {name.lower(): name for name in _MONTHS}
_MONTH_LOOKUP.update({name[:3].lower(): name for name in _MONTHS})
_MONTH_LOOKUP["sept"] = "September"
_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "thirtieth": 30,
}


def _day_number(text: str) -> int | None:
    """Parse ``20``, ``20th``, ``twentieth`` or ``twenty first`` as a day of month."""
    text = text.strip().lower().replace("-", " ")
    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?", text)
    if match:
        day = int(match.group(1))
        return day if 1 <= day <= 31 else None
    words = text.split()
    if len(words) == 1 and words[0] in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[words[0]]
    if len(words) == 2 and words[0] in {"twenty", "thirty"} and words[1] in _ORDINAL_WORDS:
        day = (20 if words[0] == "twenty" else 30) + _ORDINAL_WORDS[words[1]]
        return day if day <= 31 else None
    return None


def normalize_spoken_date(value: str) -> str:
    """Return a calendar date as the user says it: ``Month D`` with no year.

    The released expected arguments use this form (``"August 20"``), and the
    organizers' judge treats it as equal to ISO dates. Text that is not a
    single calendar date, such as ``"next Friday"``, is returned unchanged.
    """
    if not isinstance(value, str):
        return value
    text = re.sub(r"\s+", " ", value.strip())
    iso = re.fullmatch(r"(?:\d{4}-)?(\d{1,2})-(\d{1,2})", text)
    if iso:
        month, day = int(iso.group(1)), int(iso.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{_MONTHS[month - 1]} {day}"
        return text
    cleaned = re.sub(r",?\s*(?:in\s+)?\d{4}$", "", text).strip(" ,")
    cleaned = re.sub(r"^(?:on\s+)?(?:the\s+)?", "", cleaned, flags=re.IGNORECASE)
    month_first = re.fullmatch(r"([A-Za-z]+)\.?\s+(?:the\s+)?(.+)", cleaned)
    if month_first and month_first.group(1).lower() in _MONTH_LOOKUP:
        day = _day_number(month_first.group(2))
        if day is not None:
            return f"{_MONTH_LOOKUP[month_first.group(1).lower()]} {day}"
    day_first = re.fullmatch(r"(.+?)\s+(?:of\s+)?([A-Za-z]+)\.?", cleaned)
    if day_first and day_first.group(2).lower() in _MONTH_LOOKUP:
        day = _day_number(day_first.group(1))
        if day is not None:
            return f"{_MONTH_LOOKUP[day_first.group(2).lower()]} {day}"
    return text


_DOC_TYPES = ("passport", "driver_license", "id_card", "visa")
_DOC_TYPE_ALIASES = {
    "drivers_license": "driver_license",
    "driving_license": "driver_license",
    "driver_licence": "driver_license",
    "drivers_licence": "driver_license",
    "driving_licence": "driver_license",
    "identity_card": "id_card",
    "national_id": "id_card",
    "national_id_card": "id_card",
}


def normalize_doc_type(value: str) -> str:
    """Use the backend's snake_case document type (``driver_license``)."""
    if not isinstance(value, str):
        return value
    text = value.strip().lower().replace("’", "'").replace("'s", "s").replace("'", "")
    text = re.sub(r"[\s\-]+", "_", text).strip("_")
    text = re.sub(r"_(?:number|no|num|id_number)$", "", text)
    text = _DOC_TYPE_ALIASES.get(text, text)
    if text not in _DOC_TYPES and len(text) >= 3:
        candidates = [doc for doc in _DOC_TYPES if doc.startswith(text)]
        if len(candidates) == 1:
            return candidates[0]
    return text


_CURRENCY_NAMES = {
    "dollar": "USD", "us dollar": "USD", "u.s. dollar": "USD", "american dollar": "USD",
    "euro": "EUR", "pound": "GBP", "british pound": "GBP", "pound sterling": "GBP",
    "sterling": "GBP", "yen": "JPY", "japanese yen": "JPY",
    "canadian dollar": "CAD", "australian dollar": "AUD", "swiss franc": "CHF",
    "franc": "CHF", "yuan": "CNY", "renminbi": "CNY", "rupee": "INR",
    "indian rupee": "INR", "peso": "MXN", "mexican peso": "MXN", "won": "KRW",
    "korean won": "KRW",
}


def normalize_currency(value: str) -> str:
    """Return an ISO 4217 code for a spoken currency name or code."""
    if not isinstance(value, str):
        return value
    text = re.sub(r"\s+", " ", value.strip().lower())
    singular = re.sub(r"s$", "", text)
    named = _CURRENCY_NAMES.get(text) or _CURRENCY_NAMES.get(singular)
    if named:
        return named
    return text.upper() if re.fullmatch(r"[a-z]{3}", text) else value.strip()


def strip_trailing_noun(value: str, *nouns: str) -> str:
    """Drop a redundant trailing noun: ``savings account`` -> ``savings``."""
    if not isinstance(value, str):
        return value
    text = re.sub(r"\s+", " ", value.strip())
    for noun in nouns:
        stripped = re.sub(rf"\s+{noun}$", "", text, flags=re.IGNORECASE)
        if stripped and stripped != text:
            return stripped
    return text


def normalize_bill_type(value: str) -> str:
    """Use the backend's snake_case bill type (``credit_card``)."""
    text = strip_trailing_noun(value, "bill", "bills", "payment", "payments")
    if not isinstance(text, str):
        return text
    return re.sub(r"[\s\-]+", "_", text.lower())


_COMMUTE_MODES = {
    "driving": "driving", "drive": "driving", "car": "driving", "by car": "driving",
    "walking": "walking", "walk": "walking", "on foot": "walking", "foot": "walking",
    "biking": "biking", "bike": "biking", "bicycle": "biking", "cycling": "biking",
    "bicycling": "biking", "by bike": "biking",
    "transit": "transit", "public transit": "transit", "public transport": "transit",
    "public transportation": "transit", "bus": "transit", "train": "transit",
    "subway": "transit", "metro": "transit",
}


def normalize_commute_mode(value: str) -> str:
    """Map a spoken travel mode to driving, walking, biking or transit."""
    if not isinstance(value, str):
        return value
    text = re.sub(r"\s+", " ", value.strip().lower())
    return _COMMUTE_MODES.get(text, text)


_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}
_TEEN_WORDS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS_WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_WORDS = (
    set(_DIGIT_WORDS) | set(_TEEN_WORDS) | set(_TENS_WORDS) | {"hundred", "thousand"}
)


def spoken_number_run(words: list[str]) -> str:
    """Write a spoken house or street number as digits.

    ``"five hundred"`` is read as a quantity (500). Without a scale word each
    spoken group is a run of digits, as in addresses: ``"one zero one"`` is
    101 and ``"one twenty three"`` is 123.
    """
    lowered = [word.lower() for word in words]
    if {"hundred", "thousand"} & set(lowered):
        return str(int(_as_float(" ".join(lowered))))
    groups: list[str] = []
    index = 0
    while index < len(lowered):
        word = lowered[index]
        if word in _TENS_WORDS:
            value = _TENS_WORDS[word]
            following = lowered[index + 1] if index + 1 < len(lowered) else ""
            if following in _DIGIT_WORDS and following != "zero":
                value += int(_DIGIT_WORDS[following])
                index += 1
            groups.append(str(value))
        elif word in _TEEN_WORDS:
            groups.append(str(_TEEN_WORDS[word]))
        else:
            groups.append(_DIGIT_WORDS[word])
        index += 1
    return "".join(groups)


def normalize_place(value: str) -> str:
    """Keep the user's place wording; write spelled numbers and ordinals as digits.

    Only runs of two or more number words are rewritten, so a lone word such
    as "the one on Main" keeps its meaning.
    """
    if not isinstance(value, str):
        return value

    def digits(match: re.Match[str]) -> str:
        tens = _TENS_WORDS.get((match.group(1) or "").lower(), 0)
        number = tens + _ORDINAL_WORDS[match.group(2).lower()]
        suffix = "th" if 10 <= number % 100 <= 20 else _ORDINAL_SUFFIX.get(number % 10, "th")
        return f"{number}{suffix}"

    def number(match: re.Match[str]) -> str:
        return spoken_number_run(re.split(r"[\s\-]+", match.group(0)))

    text = re.sub(r"\s+", " ", value.strip())
    ordinal = r"\b(?:(" + "|".join(_TENS_WORDS) + r")[\s\-]+)?(" + "|".join(_ORDINAL_WORDS) + r")\b"
    text = re.sub(ordinal, digits, text, flags=re.IGNORECASE)
    word = r"(?:" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")"
    run = rf"\b{word}(?:[\s\-]+{word})+\b"
    return re.sub(run, number, text, flags=re.IGNORECASE)


_PRICE_WORDS = ("price", "rent", "budget", "cost", "monthly_rent", "rent_price", "monthly_budget")
_FILTER_ALIASES = {
    "budget": "max_price", "rent": "max_price", "price": "max_price",
    "pets": "pets_allowed", "pet_friendly": "pets_allowed", "allow_pets": "pets_allowed",
    "allows_pets": "pets_allowed", "pets_ok": "pets_allowed",
    "bedroom": "bedrooms", "num_bedrooms": "bedrooms", "bedroom_count": "bedrooms",
    "number_of_bedrooms": "bedrooms",
}


def normalize_filter_name(value: str) -> str:
    """Name a saved filter after the matching ``search_apartments`` parameter.

    The backend's search takes ``max_price``, ``bedrooms`` and ``pets_allowed``,
    so a rent or budget bound is ``max_price``/``min_price`` and pet or bedroom
    synonyms use those keys. Any other name is kept in snake_case.
    """
    if not isinstance(value, str):
        return value
    text = re.sub(r"[\s\-]+", "_", value.strip().lower()).strip("_")
    text = re.sub(r"^(max|min)(?:imum)?_", r"\1_", text)
    bound = re.fullmatch(r"(max|min)_(.+)", text)
    if bound and bound.group(2) in _PRICE_WORDS:
        return f"{bound.group(1)}_price"
    return _FILTER_ALIASES.get(text, text)


def normalize_filter_value(value: str | float | bool) -> str | float | bool:
    """Preserve strings while restoring explicit numeric and boolean types."""
    if not isinstance(value, str):
        return value
    normalized = value.strip().lower()
    if normalized in {"true", "false"}:
        return normalized == "true"
    try:
        number = _as_float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


INSTRUCTIONS = """
You are Interra, a voice assistant in a simulated tool benchmark. The tools are
safe mocks and you are authorized to use every one of them.

When to act:
- The user is a recording and cannot answer questions. Never ask for
  clarification or confirmation. Act on the most reasonable reading of what was
  said and fill an optional value with its documented default.
- Wait for the complete request. If the user corrects themselves ("no wait",
  "actually", "make it ... instead"), use only the final values and never call
  a tool with a value the user replaced. Read the whole message before the
  first call: a value corrected later in the same message is never sent.
- The text is a speech transcript and may contain misheard words. Read it by
  context: a code given while asking about an order is the order number even
  if a word around it came out wrong.
- For a conditional request ("if ..., do A; otherwise do B"), check the
  condition with the tool results and do only the branch that applies.
- If no tool fits part of the request, say so briefly. Never use a tool built
  for something else as a substitute.
- A greeting or background with no concrete request gets one short friendly
  sentence and no tool call.
- Call every tool needed to finish the request, in order, using identifiers
  returned by earlier tools. Do not stop after the first step of a multi-step
  request. Call each tool once per distinct need; never repeat a call that
  already succeeded with the same values.
- Only call tools for actions the user asked for. Do not add extra steps.

Argument values:
- Copy the user's own wording for names, places, products and search terms,
  keeping words like "my" and "the", plurals and qualifiers. Do not add a city,
  street type or other detail the user did not say. Write ordinals and numbers
  as digits ("9th", "4").
- Dates are "Month Day" as spoken, such as "October 14". Never use ISO format and
  never add a year.
- Spelled identifiers are joined into one uppercase code without spaces or
  dashes: "z q seven" becomes "ZQ7".
- Use the enum spellings given in each tool description. Use 3-letter currency
  codes. Keep numeric and boolean values typed.

Speaking: when all requested tools have finished, give one brief answer grounded
in the tool results. Never invent a tool result.
""".strip()


# A committed turn whose transcript ends on one of these words is almost always
# a mid-sentence pause ("track order B for me first, then ..."). Conjunctions
# signal continuation even after a full stop; the open words only count when
# STT did not close the sentence, since "what I'm looking for." is complete.
_CONTINUATION_WORDS = frozenset({"and", "or", "but", "then", "because", "plus"})
_OPEN_WORDS = _CONTINUATION_WORDS | frozenset({
    "so", "also", "first", "if", "that", "which", "when", "while",
    "a", "an", "the", "my", "your", "our", "their", "his", "her", "its", "some",
    "to", "for", "of", "at", "in", "on", "from", "with", "into", "under", "over",
    "about", "around", "between", "by", "near", "than", "until", "via",
    "um", "uh", "uhm", "erm", "hmm", "like", "well",
    "i", "i'm", "i'd", "i'll", "we", "we're", "let's", "is", "are", "was", "be",
})


def looks_unfinished(transcript: str) -> bool:
    """Whether a committed transcript stops in the middle of a sentence."""
    text = transcript.strip()
    if not text:
        return False
    if re.search(r"(?:,|-|—|–|…|\.\.\.)$", text):
        return True
    closed = text[-1] in ".?!"
    words = re.findall(r"[a-z']+", text.lower().replace("’", "'"))
    if not words:
        return False
    return words[-1] in (_CONTINUATION_WORDS if closed else _OPEN_WORDS)


class UnfinishedTurnHold:
    """Defer a reply to a turn that stops mid-sentence, without losing its words.

    LiveKit's endpointing can commit a turn during a pause. When the committed
    text looks unfinished, the hold keeps it and skips the reply. Speech that
    resumes is prepended with the held text, so the model sees one request. If
    the user stays silent for ``hold_seconds`` or leaves the room, the held text
    is released as a normal user turn, so a request is never dropped.
    """

    def __init__(
        self,
        release: Any,
        trace: TraceWriter,
        room_name: str,
        hold_seconds: float,
        *,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._release = release
        self._trace = trace
        self._room_name = room_name
        self._hold_seconds = hold_seconds
        self._sleep = sleep
        self._held = ""
        self._timer: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return self._hold_seconds > 0

    @property
    def held_text(self) -> str:
        return self._held

    def on_turn_completed(self, transcript: str) -> str | None:
        """Return the full text to answer now, or ``None`` while holding it."""
        self._cancel_timer()
        combined = " ".join(part for part in (self._held, transcript.strip()) if part)
        if self.enabled and looks_unfinished(combined):
            self._held = combined
            self._trace.append("turn_held", room=self._room_name, transcript=combined)
            self._timer = asyncio.create_task(self._release_after_silence(), name="interra-turn-hold")
            return None
        self._held = ""
        return combined

    def on_user_speaking(self) -> None:
        """The user resumed: keep the held text for the next committed turn."""
        self._cancel_timer()

    def flush(self, reason: str) -> None:
        """Release held text now, e.g. because the participant left."""
        self._cancel_timer()
        self._release_held(reason)

    async def aclose(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)

    def _cancel_timer(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _release_after_silence(self) -> None:
        await self._sleep(self._hold_seconds)
        self._timer = None
        self._release_held("silence")

    def _release_held(self, reason: str) -> None:
        held, self._held = self._held, ""
        if not held:
            return
        self._trace.append("turn_released", room=self._room_name, reason=reason, transcript=held)
        try:
            self._release(held)
        except Exception as exc:  # the session may already be closing
            self._trace.append(
                "turn_release_failed", room=self._room_name, reason=reason, error=str(exc)
            )


def prewarm(process: Any) -> None:
    """Load model code and weights before a recording enters the job event loop."""
    process.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.05, min_silence_duration=0.3
    )
    config = FdbConfig.from_env()
    process.userdata["benchmark"] = load_benchmark_module(config.fdb_v3_root)


async def run_session_lifecycle(
    ctx: Any,
    session: Any,
    agent: Any,
    room_options: Any,
    trace: TraceWriter,
    *,
    participant_identity: str,
    drain_timeout: float = 20.0,
    cooldown_seconds: float = 0.0,
    on_participant_left: Any = None,
) -> None:
    """Own the session until its participant leaves or the session fails.

    Drain pending speech after disconnect, then release STT and the worker job.
    ``on_participant_left`` runs before the drain so a held turn is answered.
    Other participants leaving must not end this participant's session.
    """
    disconnected = asyncio.Event()
    closed = asyncio.Event()

    def on_disconnect(participant: Any) -> None:
        if participant.identity == participant_identity and not disconnected.is_set():
            trace.append("participant_disconnected", room=ctx.room.name)
            disconnected.set()

    def on_close(event: Any) -> None:
        trace.append(
            "session_closed", room=ctx.room.name,
            reason=str(event.reason), failed=event.error is not None,
        )
        closed.set()

    ctx.room.on("participant_disconnected", on_disconnect)
    session.on("close", on_close)
    waiters: list[asyncio.Task[Any]] = []
    try:
        await session.start(room=ctx.room, agent=agent, room_options=room_options)
        waiters = [
            asyncio.create_task(disconnected.wait(), name="interra-participant-disconnect"),
            asyncio.create_task(closed.wait(), name="interra-session-close"),
        ]
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if disconnected.is_set() and not closed.is_set():
            if on_participant_left is not None:
                on_participant_left()
            trace.append("session_drain_started", room=ctx.room.name)
            try:
                await asyncio.wait_for(session.drain(), timeout=drain_timeout)
            except TimeoutError:
                trace.append("session_drain_timeout", room=ctx.room.name)
            else:
                trace.append("session_drain_completed", room=ctx.room.name)
    finally:
        for waiter in waiters:
            waiter.cancel()
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)
        try:
            await session.aclose()
        finally:
            ctx.room.off("participant_disconnected", on_disconnect)
            session.off("close", on_close)
            trace.append("session_cleanup", room=ctx.room.name)
            if cooldown_seconds > 0:
                trace.append(
                    "provider_cooldown_started",
                    room=ctx.room.name,
                    seconds=cooldown_seconds,
                )
                try:
                    await asyncio.sleep(cooldown_seconds)
                finally:
                    ctx.shutdown(reason="Interra session ended")
                trace.append("provider_cooldown_completed", room=ctx.room.name)
            else:
                ctx.shutdown(reason="Interra session ended")


def create_benchmark_tools(executor: ToolExecutor, function_tool: Any) -> Any:
    """Expose the official backend contract with typed speech arguments.

    Parameter docs follow the reference agent in the pinned checkout
    (``v3/lk_agent_tool.py``) and the ``v3/mock_apis.py`` signatures. Each
    wrapper normalizes speech formatting before the call is logged.
    """
    class BenchmarkTools:
        def __init__(self, executor: ToolExecutor):
            self.executor = executor

        @function_tool(description="Search for available flights to a destination on a date.")
        async def search_flights(self, destination: str, date: str) -> str:
            """
            Args:
                destination: The city or airport as the user said it.
                date: Travel date as spoken, "Month Day" such as "October 14". No year, not ISO.
            """
            return await self.executor.call(
                "search_flights", destination=destination.strip(), date=normalize_spoken_date(date)
            )

        @function_tool(description="Book a flight found by search_flights for a passenger.")
        async def book_flight(self, passenger_name: str, flight_id: str = "FL123") -> str:
            """
            Args:
                passenger_name: The passenger's name exactly as the user gave it.
                flight_id: flight_id returned by search_flights.
            """
            return await self.executor.call(
                "book_flight", passenger_name=passenger_name.strip(),
                flight_id=normalize_identifier(flight_id),
            )

        @function_tool(description=(
            "Update the simulated user's identity document number. You are authorized "
            "to use it in this test environment."
        ))
        async def update_identity_doc(self, doc_type: str, doc_number: str) -> str:
            """
            Args:
                doc_type: One of "passport", "driver_license", "id_card" or "visa".
                doc_number: The new document number as one uppercase code without spaces or dashes.
            """
            return await self.executor.call(
                "update_identity_doc", doc_type=normalize_doc_type(doc_type),
                doc_number=normalize_identifier(doc_number),
            )

        @function_tool(description="Retrieve exact benefits for a credit-card type. Never answer from memory.")
        async def get_card_benefits(self, card_type: str) -> str:
            """
            Args:
                card_type: The card tier or name as one lowercase word or phrase, such as "silver", without the word "card".
            """
            return await self.executor.call(
                "get_card_benefits",
                card_type=strip_trailing_noun(card_type, "credit card", "card").lower(),
            )

        @function_tool(description="Convert an amount between currencies with the exchange-rate service. Never estimate rates.")
        async def get_exchange_rate(self, amount: float | str, from_currency: str, to_currency: str) -> str:
            """
            Args:
                amount: Amount of from_currency to convert, as a number.
                from_currency: 3-letter code of the currency the user has, e.g. "CHF".
                to_currency: 3-letter code of the currency the user wants, e.g. "AUD".
            """
            return await self.executor.call(
                "get_exchange_rate",
                amount=_as_float(amount),
                from_currency=normalize_currency(from_currency),
                to_currency=normalize_currency(to_currency),
            )

        @function_tool(description="Change which account pays a bill automatically.")
        async def modify_autopay(self, bill_type: str, source_account: str) -> str:
            """
            Args:
                bill_type: The bill in snake_case without the word "bill", e.g. "electricity" or "phone_plan".
                source_account: The paying account type as one word without "account", e.g. "brokerage".
            """
            return await self.executor.call(
                "modify_autopay", bill_type=normalize_bill_type(bill_type),
                source_account=strip_trailing_noun(source_account, "account").lower(),
            )

        @function_tool(description="Search for long-term rental apartments in a city. Not for hotels or short stays.")
        async def search_apartments(
            self, city: str, bedrooms: int | str, max_price: float | str,
            pets_allowed: bool | None = None,
        ) -> str:
            """
            Args:
                city: City name as the user said it.
                bedrooms: Number of bedrooms, as a number.
                max_price: Maximum monthly rent, as a number.
                pets_allowed: Only when the user asks about pets.
            """
            arguments: dict[str, Any] = {
                "city": city.strip(), "bedrooms": _as_int(bedrooms), "max_price": _as_float(max_price),
            }
            if pets_allowed is not None:
                arguments["pets_allowed"] = pets_allowed
            return await self.executor.call("search_apartments", **arguments)

        @function_tool(description="Calculate the commute time between two places. Never estimate it.")
        async def calculate_commute(
            self, origin_address: str, destination_address: str, mode: str = "driving"
        ) -> str:
            """
            Args:
                origin_address: Starting place in the user's words, keeping "my"/"the", or an address returned by an earlier tool.
                destination_address: Destination in the user's words, keeping "my"/"the". Do not add a city.
                mode: One of "driving", "walking", "biking" or "transit". Defaults to "driving".
            """
            return await self.executor.call(
                "calculate_commute",
                origin_address=normalize_place(origin_address),
                destination_address=normalize_place(destination_address),
                mode=normalize_commute_mode(mode),
            )

        @function_tool(description=(
            "Update one saved apartment-search filter immediately. Call once per filter the user "
            "explicitly asks to update, change or set. Criteria given for a search (\"search for a "
            "one bedroom\") go to search_apartments and are not filter updates."
        ))
        async def update_search_filter(self, filter_name: str, value: str | float | bool) -> str:
            """
            Args:
                filter_name: snake_case filter key. Use the search_apartments parameter name when the filter is one of them ("max_price" for any rent or budget limit, "bedrooms", "pets_allowed"); otherwise the user's words with min_/max_ for bounds (e.g. "min_bathrooms") or a plain noun (e.g. "parking").
                value: The new value: a number for bounds and counts, true/false for yes/no filters, otherwise text.
            """
            return await self.executor.call(
                "update_search_filter",
                filter_name=normalize_filter_name(filter_name),
                value=normalize_filter_value(value),
            )

        @function_tool(description="Track a physical order using its order identifier.")
        async def track_order(self, order_id: str) -> str:
            """
            Args:
                order_id: The order code as one uppercase string without spaces or dashes.
            """
            return await self.executor.call("track_order", order_id=normalize_identifier(order_id))

        @function_tool(description="Search the product catalog. Never answer from memory.")
        async def search_products(
            self, query: str, max_price: float | str | None = None, category: str | None = None,
        ) -> str:
            """
            Args:
                query: The product words the user used, keeping plurals and qualifiers. If the user changed their mind, only the product they settled on.
                max_price: Maximum price as a number, only when the user gives one.
                category: Product category, only when the user names one.
            """
            arguments: dict[str, Any] = {"query": query.strip()}
            if max_price is not None:
                arguments["max_price"] = _as_float(max_price)
            if category:
                arguments["category"] = category.strip().lower()
            return await self.executor.call("search_products", **arguments)

        @function_tool(description="Add a product and quantity to the shopping cart.")
        async def add_to_cart(self, product_id: str, quantity: int | str = 1) -> str:
            """
            Args:
                product_id: The product code the user gave (e.g. "item b seven" is "B7"), used directly without searching; otherwise product_id from search_products.
                quantity: How many to add, as a number.
            """
            return await self.executor.call(
                "add_to_cart", product_id=normalize_identifier(product_id), quantity=_as_int(quantity)
            )

    return BenchmarkTools(executor)


def apply_turn_hold(hold: UnfinishedTurnHold, new_message: Any, stop_response: type[Exception]) -> None:
    """``Agent.on_user_turn_completed`` body: hold, merge, or pass the turn through."""
    transcript = new_message.text_content or ""
    answer = hold.on_turn_completed(transcript)
    if answer is None:
        raise stop_response()
    if answer != transcript.strip():
        new_message.content = [answer]


def trace_conversation_item(trace: TraceWriter, room_name: str, item: Any) -> None:
    """Record what each side said, so a clarification is distinguishable from an error reply."""
    role = getattr(item, "role", None)
    if role is None:
        return
    trace.append(
        "conversation_item", room=room_name, role=str(role),
        text=getattr(item, "text_content", None) or "",
        interrupted=bool(getattr(item, "interrupted", False)),
    )


def trace_session_error(trace: TraceWriter, room_name: str, event: Any) -> None:
    error = getattr(event, "error", event)
    trace.append(
        "session_error", room=room_name,
        source=type(getattr(event, "source", None)).__name__,
        error=str(getattr(error, "error", error)),
        recoverable=getattr(error, "recoverable", None),
    )


async def entrypoint(ctx: Any) -> None:
    """LiveKit job entrypoint. Must stay module-level so the worker process can import it."""
    config = FdbConfig.from_env()
    missing = config.missing_requirements()
    if missing:
        raise RuntimeError("Missing FDB-v3 configuration: " + ", ".join(missing))

    benchmark = ctx.proc.userdata["benchmark"]
    from livekit.agents import Agent, AgentSession, StopResponse, inference, llm

    function_tool = llm.function_tool if hasattr(llm, "function_tool") else llm.ai_callable
    session: Any = None

    def release_held_turn(text: str) -> None:
        session.generate_reply(user_input=text, input_modality="audio")

    trace = TraceWriter(config.trace_dir)
    hold = UnfinishedTurnHold(
        release_held_turn, trace, ctx.room.name, config.unfinished_turn_hold_seconds
    )

    class InterraVoiceAgent(Agent):
        def __init__(self) -> None:
            super().__init__(instructions=INSTRUCTIONS)

        async def on_user_turn_completed(self, turn_ctx: Any, new_message: Any) -> None:
            apply_turn_hold(hold, new_message, StopResponse)

    registry = benchmark.MockAPIRegistry(latency_profile=config.latency_profile)
    executor = ToolExecutor(registry, ctx.room.name, trace)
    tools = llm.find_function_tools(create_benchmark_tools(executor, function_tool))

    speech_stt, speech_tts = livekit_speech_models(inference, config)
    if config.llm_provider == "livekit":
        model = livekit_llm_model(inference, config)
    elif config.llm_provider == "ollama":
        from livekit.plugins import openai

        model = openai.LLM(**ollama_llm_kwargs(config))
    else:
        raise ValueError(f"Unsupported FDB LLM provider: {config.llm_provider}")
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=speech_stt,
        llm=model,
        tts=speech_tts,
        tools=tools,
        turn_handling=benchmark_turn_handling(config),
        max_tool_steps=6,
        user_away_timeout=None,
    )
    @session.on("user_state_changed")
    def on_user_state(event: Any) -> None:
        trace.append("user_state", room=ctx.room.name, state=str(event.new_state))
        if str(event.new_state) == "speaking":
            hold.on_user_speaking()

    @session.on("user_input_transcribed")
    def on_transcript(event: Any) -> None:
        trace.append(
            "transcript",
            room=ctx.room.name,
            transcript=event.transcript,
            is_final=event.is_final,
        )

    @session.on("agent_state_changed")
    def on_state(event: Any) -> None:
        trace.append("agent_state", room=ctx.room.name, state=str(event.new_state))

    @session.on("conversation_item_added")
    def on_item(event: Any) -> None:
        trace_conversation_item(trace, ctx.room.name, event.item)

    @session.on("error")
    def on_error(event: Any) -> None:
        trace_session_error(trace, ctx.room.name, event)

    trace.append("session_started", room=ctx.room.name)
    print(
        "Interra FDB speech:"
        f" stt={config.stt_model} llm={config.llm_provider}:{config.llm_model} tts={config.tts_model}"
        f" temperature={config.llm_temperature}"
        f" endpointing={config.endpointing_min_delay}-{config.endpointing_max_delay}s"
        f" unfinished_hold={config.unfinished_turn_hold_seconds}s",
        flush=True,
    )
    # Keep playout alive briefly after disconnect, then explicitly release the
    # session. Leaving close_on_disconnect=False without cleanup leaks STT jobs.
    from livekit.agents import room_io

    await ctx.connect()
    participant = await ctx.wait_for_participant()
    try:
        await run_session_lifecycle(
            ctx, session, InterraVoiceAgent(),
            room_io.RoomOptions(
                close_on_disconnect=False, participant_identity=participant.identity
            ),
            trace,
            participant_identity=participant.identity,
            cooldown_seconds=config.session_cooldown_seconds,
            on_participant_left=lambda: hold.flush("participant_left"),
        )
    finally:
        await hold.aclose()


def main() -> None:
    config = FdbConfig.from_env()
    missing = config.missing_requirements()
    if missing:
        raise SystemExit("Missing FDB-v3 configuration: " + ", ".join(missing))

    from livekit import agents
    from livekit.agents import AgentServer

    server = AgentServer(setup_fnc=prewarm, num_idle_processes=1, initialize_process_timeout=60.0)
    server.rtc_session(entrypoint)
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
