"""Bootstrap, run, and evaluate Interra against Full-Duplex-Bench v3."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable


REPOSITORY = "https://github.com/DanielLin94144/Full-Duplex-Bench.git"
PINNED_COMMIT = "3e799c45a045256f47d5f1c9cda90157e2d2ec9e"
DATA_FILE_ID = "1SO_4MTazWQ_jvCx0dtmpQ-t40bdd07yz"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCH_ROOT = PROJECT_ROOT / ".runtime" / "Full-Duplex-Bench"
REPORT_ROOT = PROJECT_ROOT / "artifacts" / "fdb_v3"


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def benchmark_root() -> Path:
    configured = os.environ.get("INTERRA_FDB_V3_ROOT")
    return Path(configured).resolve().parent if configured else DEFAULT_BENCH_ROOT


def data_root(v3_root: Path) -> Path:
    return Path(os.environ.get("INTERRA_FDB_DATA_ROOT", v3_root / "fdb_v3_data_released")).resolve()


def bootstrap(download_data: bool) -> Path:
    root = benchmark_root()
    v3_root = root / "v3"
    if not (root / ".git").is_dir():
        root.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", REPOSITORY, str(root)], cwd=PROJECT_ROOT)
    run(["git", "fetch", "--depth", "1", "origin", PINNED_COMMIT], cwd=root)
    run(["git", "checkout", "--detach", PINNED_COMMIT], cwd=root)

    target = data_root(v3_root)
    if download_data and not target.is_dir():
        import gdown

        archive = root / "fdb_v3_data_released.zip"
        print(f"Downloading official FDB-v3 data to {archive}", flush=True)
        gdown.download(id=DATA_FILE_ID, output=str(archive), quiet=False)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(v3_root)
        archive.unlink()
        if not target.is_dir():
            raise RuntimeError(f"Downloaded archive did not produce {target}")
    return v3_root


def check(v3_root: Path, require_data: bool = True) -> None:
    missing = [
        name
        for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        if not os.environ.get(name)
    ]
    if not (v3_root / "mock_apis.py").is_file():
        missing.append(f"benchmark checkout ({v3_root / 'mock_apis.py'})")
    if require_data and not data_root(v3_root).is_dir():
        missing.append(f"benchmark data ({data_root(v3_root)})")
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg executable")
    if missing:
        raise SystemExit("FDB-v3 readiness failed; missing: " + ", ".join(missing))
    print("FDB-v3 configuration is ready.")


RESULT_NAME = "result_interra_elevenlabs.json"
OUTPUT_NAME = "output_interra_elevenlabs.wav"
RECORDER_FAILURES = {"inference_failed", "inference_error"}


def recorder_failures(results_dir: Path) -> list[Path]:
    """Result files the official runner wrote after its recorder subprocess crashed."""
    failed = []
    for path in sorted(results_dir.rglob(RESULT_NAME)):
        try:
            status = json.loads(path.read_text(encoding="utf-8")).get("status")
        except (OSError, json.JSONDecodeError):
            continue
        if status in RECORDER_FAILURES:
            failed.append(path)
    return failed


def discard_partial_outputs(results_dir: Path) -> list[str]:
    """Delete agent audio that has no result next to it.

    The runner reuses an existing output WAV without recording again, so audio
    left by a recorder that was stopped mid-recording must not survive.
    """
    removed = []
    for output in sorted(results_dir.glob(f"*/{OUTPUT_NAME}")):
        if not (output.parent / RESULT_NAME).exists():
            output.unlink()
            removed.append(output.parent.name)
    return removed


def _stop_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop the runner and the recorder subprocess it started."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def run_with_stall_guard(
    command: list[str], *, cwd: Path, env: dict[str, str], results_dir: Path,
    on_stall: Callable[[], None], stall_seconds: float, max_restarts: int,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 15.0,
) -> list[dict[str, object]]:
    """Run the official runner, restarting it when no result appears for too long.

    On 2026-10-04 the LiveKit worker logged "worker process is not responding"
    and the runner then waited on its recorder for over two hours. A stall
    stops the runner, discards the stuck recording's partial audio, calls
    ``on_stall`` (which restarts the agent) and resumes; finished recordings
    are skipped by the runner. Returns one entry per restart.
    """
    restarts: list[dict[str, object]] = []
    while True:
        print("+", " ".join(command), flush=True)
        process = popen(command, cwd=cwd, env=env, start_new_session=True)
        finished = len(list(results_dir.glob(f"*/{RESULT_NAME}")))
        progress_at = clock()
        stalled = False
        while process.poll() is None:
            sleep(poll_seconds)
            count = len(list(results_dir.glob(f"*/{RESULT_NAME}")))
            if count != finished:
                finished, progress_at = count, clock()
            elif clock() - progress_at > stall_seconds:
                stalled = True
                break
        if not stalled:
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command)
            return restarts
        _stop_tree(process)
        if len(restarts) >= max_restarts:
            raise RuntimeError(f"Benchmark runner made no progress for {stall_seconds:.0f} s "
                               f"after {max_restarts} restarts")
        discarded = discard_partial_outputs(results_dir)
        restarts.append({"restart": len(restarts) + 1, "results_before_stall": finished,
                         "discarded_partial_outputs": discarded})
        print(f"No new result for {stall_seconds:.0f} s; restarting the agent and resuming "
              f"(restart {len(restarts)}/{max_restarts}).", flush=True)
        on_stall()


def benchmark(
    v3_root: Path, force: bool, recorder_retries: int | None = None,
    on_stall: Callable[[], None] | None = None,
) -> None:
    """Run every released recording, then re-run recordings whose recorder crashed.

    In the 2026-10-01 run the pinned ``livekit_inference.py`` recorder aborted
    (exit -6) on 10 of 100 recordings before writing any result, so those
    recordings scored zero whatever the agent did. The runner skips finished
    recordings, so deleting only the crashed results re-runs just those in new
    rooms. Each retry is logged to ``recorder-retries.json``.
    """
    if recorder_retries is None:
        recorder_retries = int(os.environ.get("INTERRA_FDB_RECORDER_RETRIES", "1"))
    results_dir = data_root(v3_root)
    command = [
        sys.executable,
        "run_tool_benchmark_all_released.py",
        "--provider",
        "interra_elevenlabs",
        "--root_dir",
        str(results_dir),
    ]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    stall_restarts: list[dict[str, object]] = []

    def run_runner(arguments: list[str]) -> None:
        if on_stall is None:
            run(arguments, cwd=v3_root, env=env)
            return
        stall_restarts.extend(run_with_stall_guard(
            arguments, cwd=v3_root, env=env, results_dir=results_dir, on_stall=on_stall,
            stall_seconds=float(os.environ.get("INTERRA_FDB_STALL_SECONDS", "600")),
            max_restarts=int(os.environ.get("INTERRA_FDB_STALL_RESTARTS", "3")) - len(stall_restarts),
        ))

    run_runner(command + (["--force"] if force else []))
    retries: list[dict[str, object]] = []
    for attempt in range(1, recorder_retries + 1):
        failed = recorder_failures(results_dir)
        if not failed:
            break
        for path in failed:
            retries.append({"attempt": attempt, "recording": path.parent.name})
            path.unlink()
            (path.parent / OUTPUT_NAME).unlink(missing_ok=True)
        print(f"Re-running {len(failed)} recordings whose recorder crashed (attempt {attempt}).", flush=True)
        run_runner(command)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "recorder-retries.json").write_text(json.dumps({
        "retries_allowed": recorder_retries,
        "retried": retries,
        "still_failed": [path.parent.name for path in recorder_failures(results_dir)],
        "stall_restarts": stall_restarts,
    }, indent=2), encoding="utf-8")


def seed_results(v3_root: Path, seed_dir: Path) -> dict[str, object]:
    """Place results kept from an earlier run before the benchmark starts.

    The official runner skips a recording whose result file exists, so only
    the recordings without a seed are recorded. ``seed-manifest.json`` (from
    ``agent.fdb_offline outage-seed``) says why each recording was kept or
    re-run; it is copied to ``artifacts/fdb_v3/seeded-results.json``.
    """
    results_dir = data_root(v3_root)
    manifest_path = seed_dir / "seed-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    seeded = []
    for source in sorted(seed_dir.glob(f"*/{RESULT_NAME}")):
        target = results_dir / source.parent.name
        if not (target / "input.wav").is_file():
            raise RuntimeError(f"Seed {source.parent.name} has no released recording in {results_dir}")
        shutil.copy2(source, target / RESULT_NAME)
        seeded.append(source.parent.name)
    print(f"Seeded {len(seeded)} results from {seed_dir}; the rest will be recorded.", flush=True)
    report = {**manifest, "seeded_into_run": seeded}
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "seeded-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def smoke_benchmark(v3_root: Path) -> None:
    """Run one released recording and require recognized agent speech."""
    source = next(
        (folder for folder in sorted(data_root(v3_root).iterdir()) if (folder / "input.wav").is_file()),
        None,
    )
    if source is None:
        raise RuntimeError("FDB-v3 smoke test found no released input.wav")
    with tempfile.TemporaryDirectory(prefix="interra-fdb-smoke-") as temporary:
        folder = Path(temporary) / source.name
        folder.mkdir()
        shutil.copy2(source / "input.wav", folder / "input.wav")
        if (source / "metadata.json").is_file():
            shutil.copy2(source / "metadata.json", folder / "metadata.json")
        command = [
            sys.executable,
            "run_tool_benchmark_all_released.py",
            "--provider",
            "interra_elevenlabs",
            "--root_dir",
            temporary,
            "--force",
        ]
        print("Running one-recording FDB-v3 speech gate.", flush=True)
        subprocess.run(command, cwd=v3_root, env=os.environ.copy(), check=True, timeout=240)
        result_path = folder / "result_interra_elevenlabs.json"
        if not result_path.is_file():
            raise RuntimeError("FDB-v3 speech gate produced no result file")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("status") != "completed" or not str(result.get("transcript", "")).strip():
            raise RuntimeError(
                "FDB-v3 speech gate heard no agent reply; the full run was stopped."
            )
        print("FDB-v3 speech gate heard an agent reply.", flush=True)


def evaluate(v3_root: Path, use_llm: bool) -> None:
    if use_llm and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required for the optional self-reported LLM-judge evaluation.")
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    shared = [
        "--benchmark",
        str(v3_root / "benchmark_data_v2.json"),
        "--results-dir",
        str(data_root(v3_root)),
        "--provider",
        "interra_elevenlabs",
    ]
    for script, output in (
        ("evaluate_tool_calls.py", "interra_elevenlabs_evaluation_report.json"),
        ("evaluate_pass_rate.py", "interra_elevenlabs_pass_rate_report.json"),
    ):
        # The pinned evaluator can exit nonzero after writing its report.
        # Stage each invocation separately so an old report cannot mask failure.
        with tempfile.TemporaryDirectory(prefix="interra-evaluation-", dir=REPORT_ROOT) as temporary:
            report_path = Path(temporary) / output
            command = [sys.executable, script, *shared, "--output", str(report_path)]
            if use_llm:
                command.append("--use-llm")
            try:
                run(command, cwd=v3_root)
            except subprocess.CalledProcessError:
                if not report_path.is_file():
                    raise
                print(f"Official {script} exited after writing a fresh report.", flush=True)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict) or not isinstance(report.get("total_scenarios"), int):
                raise RuntimeError(f"Invalid official evaluation report: {output}")
            report_path.replace(REPORT_ROOT / output)
    if use_llm:
        run(
            [
                sys.executable,
                "analyze_tool_latency.py",
                "--results-dir",
                str(data_root(v3_root)),
                "--provider",
                "interra_elevenlabs",
                "--output",
                str(REPORT_ROOT / "interra_elevenlabs_latency_report.json"),
            ],
            cwd=v3_root,
        )
    else:
        print(
            "Skipped LLM-assisted latency post-analysis; pass --use-llm with "
            "OPENAI_API_KEY to generate it.",
            flush=True,
        )


class AgentProcess:
    """The LiveKit agent worker the benchmark rooms dispatch to; restartable."""

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "agent.fdb_livekit", "start"], cwd=PROJECT_ROOT, env=self.env
        )
        for _ in range(20):
            if self.process.poll() is not None:
                raise RuntimeError(f"LiveKit agent exited during startup with code {self.process.returncode}")
            time.sleep(0.5)

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process = None

    def restart(self) -> None:
        self.stop()
        self.start()


def all_steps(v3_root: Path, force: bool, use_llm: bool) -> None:
    env = os.environ.copy()
    env["INTERRA_FDB_V3_ROOT"] = str(v3_root)
    env["PYTHONUNBUFFERED"] = "1"  # a crashed worker's last log lines must reach the kernel log
    agent = AgentProcess(env)
    agent.start()
    try:
        smoke_benchmark(v3_root)
        seed = os.environ.get("INTERRA_FDB_SEED_RESULTS")
        if seed:
            seed_results(v3_root, (PROJECT_ROOT / seed).resolve())
            force = False  # --force would overwrite the seeded results
        benchmark(v3_root, force, on_stall=agent.restart)
    finally:
        agent.stop()
    evaluate(v3_root, use_llm)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("bootstrap", "check", "agent", "benchmark", "evaluate", "all"))
    parser.add_argument("--download-data", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--use-llm", action="store_true")
    args = parser.parse_args()

    if args.command == "bootstrap":
        print(bootstrap(args.download_data))
        return

    v3_root = Path(os.environ.get("INTERRA_FDB_V3_ROOT", DEFAULT_BENCH_ROOT / "v3")).resolve()
    if args.command == "check":
        check(v3_root)
    elif args.command == "agent":
        check(v3_root, require_data=False)
        run([sys.executable, "-m", "agent.fdb_livekit", "start"], cwd=PROJECT_ROOT)
    elif args.command == "benchmark":
        check(v3_root)
        benchmark(v3_root, args.force)
    elif args.command == "evaluate":
        check(v3_root)
        evaluate(v3_root, args.use_llm)
    else:
        check(v3_root)
        all_steps(v3_root, args.force, args.use_llm)


if __name__ == "__main__":
    main()
