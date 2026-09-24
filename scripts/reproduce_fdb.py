"""Pinned FDB-v3 setup/evaluation. Run with Python 3.11; see docs/FDB_SETUP.md."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid
import venv

ROOT = Path(__file__).resolve().parents[1]


def validate_results(data, provider):
    inputs = sorted(data.glob("*/input.wav"))
    if not inputs:
        raise ValueError("No dataset inputs found")
    for audio in inputs:
        result = json.loads((audio.parent / f"result_{provider}.json").read_text())
        if result.get("status") != "completed" or not result.get("transcript", "").strip():
            raise ValueError(f"Incomplete inference or empty response: {audio.parent.name}")
    return len(inputs)


def pin_judge(source, model, expected):
    needle = 'model="gpt-4o"'
    if source.count(needle) != expected:
        raise ValueError("Evaluator source changed; review judge pin before running")
    return source.replace(needle, "model=" + json.dumps(model))


def strict_errors(source):
    """Keep successful scoring intact; never silently substitute failed judge calls."""
    class FailClosed(ast.NodeTransformer):
        def visit_ExceptHandler(self, node):
            node.body = [ast.Raise()]
            return node
    return ast.unparse(ast.fix_missing_locations(FailClosed().visit(ast.parse(source)))) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only prerequisite check")
    parser.add_argument("--prepare-only", action="store_true", help="Install voice dependencies and pinned source")
    parser.add_argument("--data-dir", type=Path, help="Extracted fdb_v3_data_released directory")
    args = parser.parse_args()
    config = json.loads((ROOT / "config/fdb.json").read_text())
    if args.check:
        print("Python 3.11:", sys.version_info[:2] == (3, 11))
        for name in ("git", "ffmpeg", "nvidia-smi"):
            print(name + ":", bool(shutil.which(name)))
        print("Local credential file:", (ROOT / ".env.local").exists())
        print("Credentials are verified after setup; never printed. Full evaluation requires Linux/CUDA.")
        return
    if sys.version_info[:2] != (3, 11):
        parser.error("Use Python 3.11")
    if not args.prepare_only and (sys.platform != "linux" or not shutil.which("nvidia-smi")):
        parser.error("Full upstream evaluation needs Linux with an NVIDIA CUDA GPU. Use --prepare-only here.")
    if not args.prepare_only and not args.data_dir:
        parser.error("Supply --data-dir containing the released input.wav folders; see docs/FDB_SETUP.md")
    envdir = ROOT / ".venv-fdb"
    python = envdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        venv.create(envdir, with_pip=True)
    lock = ("fdb.lock" if os.name == "nt" else "fdb-linux.lock") if args.prepare_only else "fdb-evaluation.lock"
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements" / lock), "-e", str(ROOT)], check=True)
    upstream = ROOT / "artifacts/fdb-upstream"
    upstream.parent.mkdir(parents=True, exist_ok=True)
    if not upstream.exists():
        subprocess.run(["git", "clone", config["repository"], str(upstream)], check=True)
        subprocess.run(["git", "checkout", config["revision"]], cwd=upstream, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=upstream, text=True).strip()
    if head != config["revision"] or dirty:
        raise RuntimeError("Upstream checkout differs from pinned clean source; preserve it and review manually")
    if args.prepare_only:
        print("Voice environment ready. Follow docs/FDB_SETUP.md to configure credentials and test the microphone.")
        return
    # Load dotenv in the prepared interpreter; secrets go only into child environments.
    subprocess.run([str(python), str(Path(__file__).resolve()), "--check"], check=True)
    run = ROOT / "artifacts/fdb-runs" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    run.mkdir(parents=True)
    env = os.environ.copy()
    # Parse dotenv using the installed library, in-process after adding its site packages.
    site = subprocess.check_output([str(python), "-c", "import site; print(site.getsitepackages()[-1])"], text=True).strip()
    sys.path.insert(0, site)
    from dotenv import dotenv_values
    env = {**{k: v for k, v in dotenv_values(ROOT / ".env.local").items() if v is not None}, **env}
    required = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "OPENAI_API_KEY")
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise RuntimeError("Missing settings: " + ", ".join(missing) + ". See docs/FDB_SETUP.md")
    env.update(INTERRA_FDB_ROOT=str(upstream / "v3"), INTERRA_TRACE_DIR=str(run / "traces"),
               INTERRA_USE_CASE="benchmark", INTERRA_FDB_CONFIG=str(ROOT / "config/fdb.json"),
               INTERRA_FDB_TOOL_LOG="/tmp/agent_tool_calls.log",
               PYTHONHASHSEED=str(config["seed"]), PYTHONUNBUFFERED="1")
    data = run / "data"
    hashes = {}
    for audio in sorted(args.data_dir.resolve().glob("*/input.wav")):
        target = data / audio.parent.name
        target.mkdir(parents=True)
        for name in ("input.wav", "metadata.json"):
            source = audio.parent / name
            shutil.copy2(source, target / name)
            hashes[f"{audio.parent.name}/{name}"] = hashlib.sha256(source.read_bytes()).hexdigest()
    if not hashes:
        raise ValueError("No released dataset inputs found")
    metadata = {"config": config, "input_sha256": hashes, "status": "started", "commands": [],
                "evaluator_changes": "Pin judge model; exception handlers re-raise instead of fallback/skip. Successful scoring unchanged.",
                "planner": env.get("INTERRA_PLANNER", config["planner"]),
                "planner_model": env.get("INTERRA_MODEL", config["planner_model"]),
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    def command(argv, label, cwd=upstream / "v3"):
        print(f"Running {label}; log: {run / (label + '.log')}", flush=True)
        metadata["commands"].append(argv)
        with (run / f"{label}.log").open("w", encoding="utf-8") as log:
            subprocess.run(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    worker = None
    try:
        command([str(python), "-m", "pip", "freeze"], "dependencies")
        command([str(python), "-m", "agent.fdb.livekit_agent", "download-files"], "models", ROOT)
        with (run / "worker.log").open("w", encoding="utf-8") as log:
            worker = subprocess.Popen([str(python), "-m", "agent.fdb.livekit_agent", "start"], cwd=ROOT,
                env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 120
            while "registered worker" not in (run / "worker.log").read_text(encoding="utf-8", errors="replace"):
                if worker.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Worker did not register; see worker.log")
                time.sleep(0.25)
            command([str(python), "run_tool_benchmark_all_released.py", "--root_dir", str(data),
                     "--provider", config["provider"], "--force"], "inference")
            metadata["completed_inputs"] = validate_results(data, config["provider"])
            for name, count in (("evaluate_tool_calls", 2), ("evaluate_pass_rate", 1), ("analyze_tool_latency", 1)):
                source = (upstream / "v3" / f"{name}.py").read_text(encoding="utf-8")
                script = run / f"{name}.py"
                script.write_text(strict_errors(pin_judge(source, config["judge_model"], count)), encoding="utf-8")
                argv = [str(python), str(script), "--results-dir", str(data), "--provider", config["provider"],
                        "--output", str(run / f"{name}.json")]
                if name != "analyze_tool_latency":
                    argv += ["--benchmark", str(upstream / "v3/benchmark_data_v2.json"), "--use-llm"]
                env["PYTHONPATH"] = str(upstream / "v3")
                command(argv, name)
                report = json.loads((run / f"{name}.json").read_text())
                evaluated = len(report["per_scenario"]) if name == "analyze_tool_latency" else report["total_scenarios"]
                if evaluated != metadata["completed_inputs"]:
                    raise RuntimeError(f"Incomplete evaluator report: {name}")
            metadata["status"] = "completed"
    finally:
        if worker is not None:
            try:
                os.killpg(worker.pid, signal.SIGTERM)
                worker.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid, signal.SIGKILL)
                worker.wait()
            except ProcessLookupError:
                worker.wait()
        if metadata["status"] != "completed":
            metadata["status"] = "failed"
        (run / "run.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        print("Run evidence:", run)


if __name__ == "__main__":
    main()
