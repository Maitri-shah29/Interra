"""Prepare a reproducible Interra FDB-v3 worker on Kaggle.

Upload this as a Kaggle *notebook* or *script* kernel. Do not attach the
private source dataset; the packaged worker embeds a path-validated archive
when `EMBEDDED_SOURCE_B64` is set. Read provider credentials only from
Kaggle Secrets and write no credentials into the notebook output.
"""

from __future__ import annotations

import json
import base64
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile


WORK = Path("/kaggle/working")
SCRATCH = Path("/kaggle/temp")
INPUT_ROOT = Path("/kaggle/input/interra-fdb-v3-private-source")
PROJECT = WORK / "Interra"
VENV = SCRATCH / "interra-fdb-venv"
REPORT = WORK / "interra-setup-report.json"
RESULTS_ARCHIVE = WORK / "interra-fdb-results.zip"
RUN_FULL_BENCHMARK = False
EMBEDDED_SOURCE_B64 = ""
# Non-secret INTERRA_* settings for one experiment run, set by kaggle_package.
RUN_ENVIRONMENT: dict[str, str] = {}


def run(command: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def venv_python() -> str:
    return str(VENV / "bin" / "python")


def read_kaggle_secrets() -> list[str]:
    missing: list[str] = []
    try:
        from kaggle_secrets import UserSecretsClient

        client = UserSecretsClient()
        for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
            try:
                os.environ[name] = client.get_secret(name)
            except Exception:
                missing.append(name)
    except Exception:
        missing = [
            "LIVEKIT_URL",
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
        ]
    return sorted(set(missing))


def source_root() -> Path:
    archive = INPUT_ROOT / "interra-source.zip"
    if archive.is_file() or EMBEDDED_SOURCE_B64:
        destination = WORK / "Interra-source"
        # An attached historical dataset must not shadow the notebook's update.
        source = io.BytesIO(base64.b64decode(EMBEDDED_SOURCE_B64)) if EMBEDDED_SOURCE_B64 else archive
        with zipfile.ZipFile(source) as bundle:
            for member in bundle.infolist():
                name = member.filename
                if Path(name).is_absolute() or "\\" in name or ".." in Path(name).parts:
                    raise ValueError(f"Unsafe source archive member: {name}")
            bundle.extractall(destination)
        if not (destination / "pyproject.toml").is_file():
            raise FileNotFoundError("Source archive has no pyproject.toml")
        return destination
    if (INPUT_ROOT / "pyproject.toml").is_file():
        return INPUT_ROOT
    candidates = list(Path("/kaggle/input").rglob("pyproject.toml"))
    if len(candidates) != 1:
        raise FileNotFoundError(
            "Could not uniquely locate the private Interra source dataset; "
            f"candidates={candidates}"
        )
    return candidates[0].parent


def enable_fast_qwen_if_supported() -> None:
    """Ask Ollama to skip Qwen3 thinking. Keep going with the /no_think prompt if it refuses."""
    probe = subprocess.run(
        [
            "curl",
            "-fsS",
            "--max-time",
            "30",
            "http://127.0.0.1:11434/v1/chat/completions",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(
                {
                    "model": "qwen3:8b",
                    "messages": [{"role": "user", "content": "/no_think Reply with only READY."}],
                    "think": False,
                    "stream": False,
                }
            ),
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        os.environ["INTERRA_OLLAMA_DISABLE_THINK"] = "1"
        print("Ollama accepted think:false for qwen3:8b.", flush=True)
        return
    print("Ollama did not accept think:false; the prompt uses /no_think.", flush=True)


def start_ollama() -> subprocess.Popen[bytes]:
    with (WORK / "ollama.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(["ollama", "serve"], stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            probe = subprocess.run(
                ["curl", "-fsS", "http://127.0.0.1:11434/api/tags"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if probe.returncode == 0:
                return server
            if server.poll() is not None:
                raise RuntimeError(f"Ollama exited during startup: {server.returncode}")
            time.sleep(1)
        raise RuntimeError("Ollama did not become ready within 60 seconds")
    except BaseException:
        stop_ollama(server)
        raise


def stop_ollama(server: subprocess.Popen[bytes]) -> None:
    if server.poll() is not None:
        return
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


def require_livekit_inference_agent(project: Path) -> None:
    """Stop a notebook that still embeds the retired ElevenLabs ack path."""
    agent = project / "src" / "agent" / "fdb_livekit.py"
    if not agent.is_file():
        return
    source = agent.read_text(encoding="utf-8")
    if "plugins.elevenlabs" in source or 'say("Okay' in source or "say('Okay" in source:
        raise RuntimeError(
            "This notebook still has the ElevenLabs spoken-ack agent. "
            "Upload the LiveKit Inference worker before running."
        )
    if "deepgram/nova-3" not in source or "cartesia/sonic-3" not in source:
        raise RuntimeError("FDB agent is missing the LiveKit Inference speech models.")
    print(
        "Interra worker speech path: LiveKit Inference Deepgram Nova-3, "
        "GPT-4.1 mini, Cartesia Sonic-3.",
        flush=True,
    )


def archive_results(project: Path, destination: Path, tool_log: Path | None = None) -> int:
    """Bundle every per-recording result and run report into one output file.

    Kaggle's output listing returned only 15 of 100 per-recording JSONs from
    the 2026-10-01 run, so the evidence is shipped as a single archive. Audio
    is left out. Returns the number of per-recording results archived.
    """
    tool_log = tool_log or Path("/tmp/agent_tool_calls.log")
    data = project / ".runtime" / "Full-Duplex-Bench" / "v3" / "fdb_v3_data_released"
    reports = project / "artifacts" / "fdb_v3"
    results = sorted(data.rglob("result_*.json")) if data.is_dir() else []
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in results:
            bundle.write(path, Path("per-recording") / path.relative_to(data))
        if reports.is_dir():
            for path in sorted(reports.iterdir()):
                if path.is_file() and path.suffix in {".json", ".jsonl"}:
                    bundle.write(path, Path("reports") / path.name)
        if tool_log.is_file():
            bundle.write(tool_log, Path("reports") / "agent_tool_calls.log")
    print(f"Archived {len(results)} per-recording results to {destination}", flush=True)
    return len(results)


def apply_run_environment(settings: dict[str, str]) -> None:
    """Export packaged experiment settings before the benchmark starts."""
    for name, value in settings.items():
        if not name.startswith("INTERRA_"):
            raise ValueError(f"Only INTERRA_* settings can be packaged: {name}")
        os.environ[name] = value
        print(f"Run setting {name}={value}", flush=True)


def main() -> None:
    apply_run_environment(RUN_ENVIRONMENT)
    shutil.copytree(source_root(), PROJECT, dirs_exist_ok=True)
    require_livekit_inference_agent(PROJECT)
    missing = read_kaggle_secrets()
    if RUN_FULL_BENCHMARK:
        if missing:
            raise RuntimeError("Benchmark Secrets unavailable: " + ", ".join(missing))
    run(["apt-get", "update"])
    run(
        [
            "apt-get",
            "install",
            "-y",
            f"python{sys.version_info.major}.{sys.version_info.minor}-venv",
            "zstd",
        ]
    )
    run([sys.executable, "-m", "venv", str(VENV)])
    python = venv_python()
    run([python, "-m", "pip", "install", "--upgrade", "pip"])
    run([python, "-m", "pip", "install", "-e", ".[fdb]"], cwd=PROJECT)

    llm_provider = os.environ.get("INTERRA_FDB_LLM_PROVIDER", "livekit")
    if llm_provider not in {"livekit", "ollama"}:
        raise ValueError(f"Unsupported FDB LLM provider: {llm_provider}")
    server = None
    try:
        if llm_provider == "ollama":
            run(["bash", "-lc", "curl -fsSL https://ollama.com/install.sh | sh"])
            server = start_ollama()
            model = os.environ.get("INTERRA_OLLAMA_MODEL", "qwen3:8b")
            run(["ollama", "pull", model])
            run(["ollama", "run", model, "/no_think Reply with only READY."])
            enable_fast_qwen_if_supported()
        run(["nvidia-smi"])

        run([python, "scripts/fdb_v3.py", "bootstrap", "--download-data"], cwd=PROJECT)
        if not missing:
            run([python, "scripts/fdb_v3.py", "check"], cwd=PROJECT)
            if RUN_FULL_BENCHMARK:
                try:
                    run([python, "scripts/fdb_v3.py", "all", "--force"], cwd=PROJECT)
                finally:
                    archive_results(PROJECT, RESULTS_ARCHIVE)
        else:
            print("Benchmark deferred; add Kaggle Secrets: " + ", ".join(missing), flush=True)
    finally:
        if server is not None:
            stop_ollama(server)

    report = {
        "llm_provider": llm_provider,
        "llm_model": (
            os.environ.get("INTERRA_FDB_LLM_MODEL", "openai/gpt-4.1-mini")
            if llm_provider == "livekit"
            else os.environ.get("INTERRA_OLLAMA_MODEL", "qwen3:8b")
        ),
        "project": str(PROJECT),
        "venv": str(VENV),
        "livekit_ready": not missing,
        "missing_secret_names": missing,
        "full_benchmark_run": RUN_FULL_BENCHMARK,
        "run_environment": RUN_ENVIRONMENT,
        "next_command": "python scripts/fdb_v3.py all --force",
    }
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
