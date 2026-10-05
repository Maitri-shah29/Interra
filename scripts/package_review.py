"""Assemble an FDB-v3 review archive; never tag, push, sign, or submit it."""

from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "submission"
REPORT_DIR = ROOT / "docs" / "results"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", type=Path, help="The actual three-to-five-minute demo video.")
    parser.add_argument("--allow-missing-demo", action="store_true", help="Build an explicitly incomplete review archive.")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    names = subprocess.check_output(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
    ).decode("utf-8").split("\0")
    selected = {
        name: ROOT / name for name in names if name and (ROOT / name).is_file()
        and (not name.lower().endswith(".pptx") or name == "docs/Interra_Theme05_submission.pptx")
    }

    required = [
        REPORT_DIR / "interra_elevenlabs_evaluation_report.json",
        REPORT_DIR / "interra_elevenlabs_pass_rate_report.json",
        REPORT_DIR / "run-manifest.json",
        REPORT_DIR / "best-run-evidence.zip",
        REPORT_DIR / "kaggle-20261004" / "interra_elevenlabs_evaluation_report.json",
        REPORT_DIR / "kaggle-20261004" / "interra_elevenlabs_pass_rate_report.json",
        REPORT_DIR / "kaggle-20261004" / "run-manifest.json",
        REPORT_DIR / "kaggle-20261004" / "livekit-agent.jsonl",
        REPORT_DIR / "kaggle-20261004" / "tool-calls.jsonl",
        REPORT_DIR / "kaggle-20261004" / "summary.md",
        ROOT / "docs" / "Interra_Theme05_submission.pptx",
    ]
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"Missing FDB-v3 review material: {path}")
        selected[path.relative_to(ROOT).as_posix()] = path

    if args.demo:
        if not args.demo.is_file():
            parser.error("The supplied demo video does not exist.")
        selected["demo/" + args.demo.name] = args.demo.resolve()
    else:
        selected["docs/DEMO.md"] = ROOT / "docs/DEMO.md"

    manifest = {
        "status": "submission package; completion confirmed by user on 2026-10-05",
        "benchmark": "Full-Duplex-Bench v3",
        "provider": "interra_elevenlabs",
        "base_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "branch": subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=ROOT, text=True
        ).strip(),
        "includes_uncommitted_work": True,
        "demo_included": args.demo is not None,
        "demo_url": "https://cursor.com/artifacts/v/art-65efdeb4-9b6f-4416-839a-875b82833f5a",
        "remaining": [],
        "sha256": {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in sorted(selected.items())
        },
    }
    destination = OUTPUT / "Interra-Theme5-FDB-v3-review.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(selected.items()):
            archive.write(path, name)
        archive.writestr("release-manifest.json", json.dumps(manifest, indent=2))
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
    print(destination)


if __name__ == "__main__":
    main()
