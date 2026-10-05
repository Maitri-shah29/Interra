"""Offline FDB-v3 loops that need no audio, recorder or GPU.

``rescore``  Re-run the tool calls recorded in an archived agent trace through
             the current argument normalizers and duplicate protection, then
             score them with the official ``evaluate_pass_rate`` exact-match
             rules. Deterministic; needs no network.
``llm``      Replay the archived final transcripts through the current prompt,
             tools and an LLM, then score the calls the same way. Needs LiveKit
             Inference (``LIVEKIT_*``).
``outage-seed``
             Split an archived run at the first provider quota outage. Results
             of recordings that started earlier are written as seeds, so a new
             run re-records only the recordings the outage hit.

Both read the scenario labels from the pinned benchmark checkout. The labels are
used only for scoring; nothing from them reaches the prompt or the tools.

    python -m agent.fdb_offline rescore --run docs/results/kaggle-20261001
    python -m agent.fdb_offline llm --run docs/results/kaggle-20261001 --provider livekit
    python -m agent.fdb_offline outage-seed --run docs/results/kaggle-20261004 --output fdb-seed
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import shutil
import sys
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

from agent.fdb_livekit import (
    INSTRUCTIONS,
    ToolExecutor,
    create_benchmark_tools,
    load_benchmark_module,
    looks_unfinished,
)

DEFAULT_V3_ROOT = Path(".runtime/Full-Duplex-Bench/v3")


class NullTrace:
    def append(self, kind: str, **payload: Any) -> None:
        pass


@dataclass(frozen=True)
class RoomRun:
    """What the benchmark runner logged for one room."""

    scenario_id: str
    speaker: str
    batch_size: int
    recorder_ok: bool


@dataclass
class Recording:
    """One benchmark recording as it was served in an archived run."""

    room: str
    scenario_id: str
    speaker: str
    events: list[dict[str, Any]] = field(default_factory=list)
    recorder_ok: bool = True

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return [event["call"] for event in self.events if event.get("kind") == "tool_call"]


_ROOM_LINE = re.compile(
    r"\[\d+/(?P<total>\d+)\] Processing Speaker=(?P<speaker>\S+?)\.\.\. "
    r"Example=(?P<example>\S+?)\.\.\.\s*\n[^\n]*\n\s*🔗 Streaming via livekit_inference\.py "
    r"into room: (?P<room>eval-[0-9a-f]+)\s*\n\s*(?P<outcome>[^\n]*)"
)


def room_map_from_kernel_logs(
    logs: Iterable[dict[str, Any]] | str, *, batch_size: int | None = None,
) -> dict[str, RoomRun]:
    """Map each room to its recording from the benchmark runner's log lines.

    ``batch_size`` keeps only the rooms of an ``[i/N]`` batch of that size, so
    a one-recording smoke test before the full run is left out. A recording
    the runner retried after a recorder crash appears again in the same batch
    with a new room; only its last attempt is kept, as in the official results.
    """
    text = logs if isinstance(logs, str) else "".join(entry.get("data", "") for entry in logs)
    mapping: dict[str, RoomRun] = {}
    latest: dict[tuple[int, str, str], str] = {}
    for match in _ROOM_LINE.finditer(text):
        total = int(match.group("total"))
        if batch_size is not None and total != batch_size:
            continue
        key = (total, match.group("example"), match.group("speaker"))
        earlier = latest.get(key)
        if earlier is not None and earlier != match.group("room"):
            mapping.pop(earlier, None)
        latest[key] = match.group("room")
        mapping[match.group("room")] = RoomRun(
            scenario_id=match.group("example"),
            speaker=match.group("speaker"),
            batch_size=total,
            recorder_ok="finished successfully" in match.group("outcome"),
        )
    return mapping


def load_recordings(trace_paths: Path | Iterable[Path], room_map: dict[str, RoomRun]) -> list[Recording]:
    rooms: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for trace_path in [trace_paths] if isinstance(trace_paths, Path) else trace_paths:
        with trace_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    event = json.loads(line)
                    if event.get("room"):
                        rooms.setdefault(event["room"], []).append(event)
    recordings = []
    for room, run in room_map.items():
        recordings.append(Recording(
            room, run.scenario_id, run.speaker, rooms.get(room, []), run.recorder_ok
        ))
    return recordings


def load_official_scorer(v3_root: Path) -> ModuleType:
    path = v3_root / "evaluate_pass_rate.py"
    spec = importlib.util.spec_from_file_location("interra_fdb_pass_rate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Official scorer not found at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_scenarios(v3_root: Path) -> dict[str, dict[str, Any]]:
    data = json.loads((v3_root / "benchmark_data_v2.json").read_text(encoding="utf-8"))
    return {scenario["id"]: scenario for scenario in data["scenarios"]}


def _tools_for(registry: Any, room: str) -> tuple[ToolExecutor, Any]:
    executor = ToolExecutor(registry, room, NullTrace(), telemetry_path=None)
    tools = create_benchmark_tools(executor, lambda **kwargs: lambda fn: fn)
    return executor, tools


async def renormalize_calls(
    calls: list[dict[str, Any]], registry: Any, room: str
) -> list[dict[str, Any]]:
    """Send recorded calls through today's wrappers and duplicate protection."""
    executor, tools = _tools_for(registry, room)
    for call in calls:
        wrapper = getattr(tools, call["function"], None)
        if wrapper is None:
            continue
        try:
            await wrapper(**call.get("args", {}))
        except (TypeError, ValueError):
            continue  # the live agent would have returned a tool error here
    return [{"function": call["function"], "args": call["args"]} for call in executor.calls]


def score(scorer: ModuleType, scenario: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    result = scorer.evaluate_scenario_pass(scenario, calls, use_llm=False)
    return {"passed": bool(result["passed"]), "failure_reason": result.get("failure_reason", "")}


def summarize(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    passed = sum(1 for row in rows if row[key]["passed"])
    return {"passed": passed, "total": len(rows), "pass_rate": round(passed / len(rows), 3) if rows else 0.0}


RECORDER_FAILURES = {"inference_failed", "inference_error"}


def final_result_statuses(run_dir: Path) -> dict[str, str]:
    """Room name to result status from ``interra-fdb-results.zip``, if archived."""
    archive = run_dir / "interra-fdb-results.zip"
    if not archive.is_file():
        return {}
    statuses = {}
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if name.startswith("per-recording/") and name.endswith(RESULT_NAME):
                result = json.loads(bundle.read(name))
                if result.get("room_name"):
                    statuses[result["room_name"]] = str(result.get("status"))
    return statuses


def archived_recordings(run_dir: Path, batch_size: int) -> list[Recording]:
    """Load a run, including a ``-resume`` session that re-recorded part of it.

    The resume session's log comes after the original, so a re-recorded
    recording maps to its newest room, as in the merged official results.
    """
    logs: list[dict[str, Any]] = []
    for name in ("kaggle-kernel-logs.json", "kaggle-kernel-logs-resume.json"):
        if (run_dir / name).is_file():
            logs.extend(json.loads((run_dir / name).read_text(encoding="utf-8")))
    room_map = room_map_from_kernel_logs(logs, batch_size=batch_size)
    # Unbuffered logs interleave the recorder's outcome line with other output;
    # the final result files record the outcome for their rooms exactly.
    for room, status in final_result_statuses(run_dir).items():
        if room in room_map:
            room_map[room] = replace(room_map[room], recorder_ok=status not in RECORDER_FAILURES)
    traces = [run_dir / name for name in ("livekit-agent.jsonl", "livekit-agent-resume.jsonl")
              if (run_dir / name).is_file()]
    return load_recordings(traces, room_map)


async def rescore(run_dir: Path, v3_root: Path, *, batch_size: int = 100) -> dict[str, Any]:
    """Score archived calls as the official run did, then with today's wrappers.

    A recording whose recorder crashed has no result calls in the official run,
    so it fails in both columns; ``current_if_recorded`` shows what the agent's
    own calls would have scored had the recorder survived.
    """
    scorer = load_official_scorer(v3_root)
    scenarios = load_scenarios(v3_root)
    backend = load_benchmark_module(v3_root)
    rows = []
    for recording in archived_recordings(run_dir, batch_size):
        scenario = scenarios[recording.scenario_id]
        agent_calls = [{"function": c["function"], "args": c["args"]} for c in recording.tool_calls]
        current = await renormalize_calls(agent_calls, backend.MockAPIRegistry(), recording.room)
        recorded = recording.recorder_ok
        rows.append({
            "room": recording.room, "scenario_id": recording.scenario_id,
            "speaker": recording.speaker, "recorder_ok": recorded,
            "archived": score(scorer, scenario, agent_calls if recorded else []),
            "current": score(scorer, scenario, current if recorded else []),
            "current_if_recorded": score(scorer, scenario, current),
            "archived_calls": agent_calls, "current_calls": current,
        })
    return {
        "mode": "rescore",
        "archived": summarize(rows, "archived"),
        "current": summarize(rows, "current"),
        "current_if_recorded": summarize(rows, "current_if_recorded"),
        "recorder_failures": sum(1 for row in rows if not row["recorder_ok"]),
        "recordings": rows,
    }


# Provider errors that mean the account ran out of quota, not that the agent failed.
OUTAGE_MARKERS = ("inference_quota_exceeded", "MaxGatewayCredits")
RESULT_NAME = "result_interra_elevenlabs.json"


def outage_rooms(trace_path: Path, markers: Iterable[str] = OUTAGE_MARKERS) -> tuple[str | None, set[str]]:
    """Rooms that started at or after the first room that hit a quota outage.

    The cut is made by start time, never by score: every recording from the
    first quota error on is selected, whatever it scored.
    """
    started: dict[str, float] = {}
    first: tuple[float, str] | None = None
    with trace_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            room = event.get("room")
            if not room:
                continue
            if event.get("kind") == "session_started":
                started.setdefault(room, float(event["time"]))
            elif event.get("kind") == "session_error" and first is None:
                if any(marker in str(event.get("error", "")) for marker in markers):
                    first = (float(event["time"]), room)
    if first is None:
        return None, set()
    cutoff_room = first[1]
    cutoff = started.get(cutoff_room, first[0])
    return cutoff_room, {room for room, time in started.items() if time >= cutoff}


def build_outage_seed(run_dir: Path, destination: Path) -> dict[str, Any]:
    """Write the run's results that predate the outage as seeds for a re-run.

    ``interra-fdb-results.zip`` holds every final per-recording result. Seeds
    keep their original files; the official runner skips a recording whose
    result exists, so only the outage recordings are recorded again.
    """
    cutoff_room, affected = outage_rooms(run_dir / "livekit-agent.jsonl")
    if cutoff_room is None:
        raise ValueError(f"No provider quota outage found in {run_dir}")
    seeded: list[str] = []
    rerun: list[str] = []
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(run_dir / "interra-fdb-results.zip") as bundle:
        for name in sorted(bundle.namelist()):
            parts = Path(name).parts
            if len(parts) != 3 or parts[0] != "per-recording" or parts[2] != RESULT_NAME:
                continue
            folder = parts[1]
            data = bundle.read(name)
            if json.loads(data).get("room_name") in affected:
                rerun.append(folder)
                continue
            (destination / folder).mkdir()
            (destination / folder / RESULT_NAME).write_bytes(data)
            seeded.append(folder)
    manifest = {
        "source_run": run_dir.as_posix(),
        "rule": "re-record every recording whose room started at or after the "
                "first room with a provider quota error",
        "markers": list(OUTAGE_MARKERS),
        "cutoff_room": cutoff_room,
        "seeded": seeded,
        "rerun": rerun,
    }
    (destination / "seed-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def user_turns(events: list[dict[str, Any]], pause_s: float = 1.2) -> list[str]:
    """Split a room's final transcripts into the turns endpointing would commit.

    A turn ends when the user stays silent for ``pause_s`` (the endpointing
    ``max_delay``). Unfinished turns merge into the next one, as
    ``UnfinishedTurnHold`` does live.
    """
    segments: list[list[str]] = [[]]
    silent_since: float | None = None
    for event in events:
        kind = event.get("kind")
        if kind == "user_state":
            state = str(event.get("state"))
            if state == "listening":
                silent_since = event["time"]
            elif state == "speaking":
                if silent_since is not None and event["time"] - silent_since >= pause_s and segments[-1]:
                    segments.append([])
                silent_since = None
        elif kind == "transcript" and event.get("is_final") and event.get("transcript", "").strip():
            segments[-1].append(event["transcript"].strip())
    turns: list[str] = []
    held = ""
    for segment in segments:
        text = " ".join(part for part in [held, *segment] if part)
        if not text:
            continue
        if looks_unfinished(text):
            held = text
            continue
        turns.append(text)
        held = ""
    if held:
        turns.append(held)
    return turns


async def run_conversation(
    model: Any, turns: list[str], registry: Any, room: str, *, max_tool_steps: int = 6,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drive the agent's prompt and tools over text turns; return calls and replies."""
    from livekit.agents import llm

    executor, tool_obj = _tools_for(registry, room)
    tool_objects = llm.find_function_tools(
        create_benchmark_tools(executor, llm.function_tool)
    )
    tool_ctx = llm.ToolContext(tool_objects)
    chat_ctx = llm.ChatContext.empty()
    chat_ctx.add_message(role="system", content=INSTRUCTIONS)
    replies: list[str] = []
    for turn in turns:
        chat_ctx.add_message(role="user", content=turn)
        for _ in range(max_tool_steps + 1):
            text, calls = "", []
            async with model.chat(chat_ctx=chat_ctx, tools=tool_objects) as stream:
                async for chunk in stream:
                    if chunk.delta is None:
                        continue
                    text += chunk.delta.content or ""
                    calls.extend(chunk.delta.tool_calls or [])
            if text.strip():
                chat_ctx.add_message(role="assistant", content=text.strip())
                replies.append(text.strip())
            if not calls:
                break
            for call in calls:
                result = await llm.execute_function_call(call, tool_ctx)
                chat_ctx.items.append(result.fnc_call)
                if result.fnc_call_out is not None:
                    chat_ctx.items.append(result.fnc_call_out)
    return [{"function": c["function"], "args": c["args"]} for c in executor.calls], replies


def make_model(provider: str, model_name: str, temperature: float) -> Any:
    options = {"temperature": temperature, "parallel_tool_calls": False}
    if provider == "livekit":
        from livekit.agents import inference

        return inference.LLM(model=model_name, extra_kwargs=options)
    raise ValueError(f"Unsupported provider: {provider}")


async def replay_llm(
    run_dir: Path, v3_root: Path, model: Any, *, concurrency: int = 4,
    pause_s: float = 1.2, only: set[str] | None = None, batch_size: int = 100,
) -> dict[str, Any]:
    """Replay each recording's transcripts through the LLM; the recorder is assumed to survive."""
    scorer = load_official_scorer(v3_root)
    scenarios = load_scenarios(v3_root)
    backend = load_benchmark_module(v3_root)
    recordings = [
        recording for recording in archived_recordings(run_dir, batch_size)
        if not only or recording.scenario_id in only
    ]
    gate = asyncio.Semaphore(concurrency)

    async def one(recording: Recording) -> dict[str, Any]:
        turns = user_turns(recording.events, pause_s)
        async with gate:
            try:
                calls, replies = await run_conversation(
                    model, turns, backend.MockAPIRegistry(), recording.room
                )
                error = None
            except Exception as exc:  # report provider failures per recording
                calls, replies, error = [], [], f"{type(exc).__name__}: {exc}"
        scenario = scenarios[recording.scenario_id]
        archived = [{"function": c["function"], "args": c["args"]} for c in recording.tool_calls]
        return {
            "room": recording.room, "scenario_id": recording.scenario_id,
            "speaker": recording.speaker, "turns": turns, "replies": replies, "error": error,
            "archived": score(scorer, scenario, archived),
            "current": score(scorer, scenario, calls),
            "archived_calls": archived, "current_calls": calls,
        }

    rows = await asyncio.gather(*(one(recording) for recording in recordings))
    return {
        "mode": "llm",
        "archived": summarize(rows, "archived"),
        "current": summarize(rows, "current"),
        "errors": sum(1 for row in rows if row["error"]),
        "recordings": rows,
    }


def print_report(report: dict[str, Any]) -> None:
    before, after = report["archived"], report["current"]
    print(f"{report['mode']}: archived {before['passed']}/{before['total']}"
          f" -> current {after['passed']}/{after['total']} (exact-match strict pass)")
    if "current_if_recorded" in report:
        survived = report["current_if_recorded"]
        print(f"recorder crashed in {report['recorder_failures']} recordings;"
              f" with every recording captured: {survived['passed']}/{survived['total']}")
    if report.get("errors"):
        print(f"provider errors: {report['errors']}")
    for row in report["recordings"]:
        was, now = row["archived"]["passed"], row["current"]["passed"]
        if was != now:
            change = "FIXED " if now else "BROKEN"
            print(f"  {change} {row['scenario_id']:14} {row['room']}  "
                  f"{row['archived']['failure_reason'] or row['current']['failure_reason']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("rescore", "llm"):
        command = sub.add_parser(name)
        command.add_argument("--run", type=Path, required=True,
                             help="Archived run folder with livekit-agent.jsonl and kaggle-kernel-logs.json")
        command.add_argument("--v3-root", type=Path,
                             default=Path(os.environ.get("INTERRA_FDB_V3_ROOT", DEFAULT_V3_ROOT)))
        command.add_argument("--output", type=Path, help="Write the full JSON report here")
    llm_command = sub.choices["llm"]
    llm_command.add_argument("--provider", choices=("livekit",), default="livekit")
    llm_command.add_argument("--model", default=os.environ.get("INTERRA_FDB_LLM_MODEL", "openai/gpt-4.1-mini"))
    llm_command.add_argument("--temperature", type=float,
                             default=float(os.environ.get("INTERRA_FDB_LLM_TEMPERATURE", "0")))
    llm_command.add_argument("--concurrency", type=int, default=4)
    llm_command.add_argument("--pause", type=float, default=1.2,
                             help="Silence in seconds that ends a replayed turn")
    llm_command.add_argument("--scenario", action="append", help="Replay only these scenario IDs")
    seed_command = sub.add_parser("outage-seed")
    seed_command.add_argument("--run", type=Path, required=True,
                              help="Archived run folder with livekit-agent.jsonl and interra-fdb-results.zip")
    seed_command.add_argument("--output", type=Path, required=True, help="Seed folder to write")
    args = parser.parse_args(argv)

    if args.mode == "outage-seed":
        manifest = build_outage_seed(args.run, args.output)
        print(f"outage-seed: {len(manifest['seeded'])} seeded, {len(manifest['rerun'])} to re-record"
              f" (cut at {manifest['cutoff_room']})")
        return
    if args.mode == "rescore":
        report = asyncio.run(rescore(args.run, args.v3_root))
    else:
        async def run_llm() -> dict[str, Any]:
            model = make_model(args.provider, args.model, args.temperature)
            try:
                return await replay_llm(
                    args.run, args.v3_root, model, concurrency=args.concurrency,
                    pause_s=args.pause, only=set(args.scenario or []),
                )
            finally:
                await model.aclose()

        report = asyncio.run(run_llm())
    print_report(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"report: {args.output}")


if __name__ == "__main__":
    sys.exit(main())
