"""Verify historical and latest FDB-v3 evidence plus the 12-slide deck."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def evidence_sha256(data: bytes) -> str:
    """Normalize Windows checkout line endings before checking text evidence."""
    return sha256(data.replace(b"\r\n", b"\n"))


def main() -> None:
    results = ROOT / "docs/results"
    # Preserve verification of the historical main baseline and full archive.
    old_manifest = json.loads((results / "run-manifest.json").read_text(encoding="utf-8"))
    old_pass = json.loads((results / "interra_elevenlabs_pass_rate_report.json").read_text(encoding="utf-8"))
    old_tool = json.loads((results / "interra_elevenlabs_evaluation_report.json").read_text(encoding="utf-8"))
    if (old_manifest["recordings"], old_manifest["strict_passes"], old_manifest["turn_taken"]) != (
        old_pass["total_scenarios"], old_pass["passed"], old_tool["turn_taking"]["turn_taken"]
    ):
        raise SystemExit("Historical run manifest and reports disagree.")
    archive_path = results / "best-run-evidence.zip"
    if sha256(archive_path.read_bytes()) != old_manifest["archive_sha256"]:
        raise SystemExit("Historical evidence archive hash mismatch.")
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(old_manifest["archive_entries"]):
            raise SystemExit("Historical evidence archive is damaged or has unexpected entries.")
        for name, expected in old_manifest["archive_entries"].items():
            if sha256(archive.read(name)) != expected:
                raise SystemExit(f"Historical evidence entry hash mismatch: {name}")
        recordings = [name for name in archive.namelist() if name.startswith("recordings/")]
        if len(recordings) != old_manifest["recordings"]:
            raise SystemExit("Historical recording evidence count mismatch.")

    latest = results / "kaggle-20261004"
    manifest = json.loads((latest / "run-manifest.json").read_text(encoding="utf-8"))
    pass_report = json.loads((latest / "interra_elevenlabs_pass_rate_report.json").read_text(encoding="utf-8"))
    tool_report = json.loads((latest / "interra_elevenlabs_evaluation_report.json").read_text(encoding="utf-8"))
    if (manifest["result_summary"]["recordings"], manifest["result_summary"]["strict_passes"], manifest["result_summary"]["turn_taken"]) != (
        pass_report["total_scenarios"], pass_report["passed"], tool_report["turn_taking"]["turn_taken"]
    ):
        raise SystemExit("Latest run manifest and official reports disagree.")
    if (manifest["result_summary"]["strict_passes"], manifest["result_summary"]["turn_taken"]) <= (
        old_manifest["strict_passes"], old_manifest["turn_taken"]
    ):
        raise SystemExit("Latest run does not beat the historical main baseline.")
    for name, expected in manifest["sha256"].items():
        path = latest / name
        if not path.is_file():
            raise SystemExit(f"Latest run evidence missing: {name}")
        payload = path.read_bytes()
        if sha256(payload) != expected and evidence_sha256(payload) != expected:
            raise SystemExit(f"Latest run evidence hash mismatch: {name}")
    with zipfile.ZipFile(latest / "interra-fdb-results.zip") as results_archive:
        partial = [
            name for name in results_archive.namelist()
            if name.startswith("per-recording/") and name.endswith("result_interra_elevenlabs.json")
        ]
    if len(partial) != manifest["per_recording_result_jsons_retrieved"]:
        raise SystemExit("Retrieved per-recording file count disagrees with the run manifest.")

    with zipfile.ZipFile(ROOT / "docs/Interra_Theme05.pptx") as deck:
        slides = [name for name in deck.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
        if len(slides) != 12:
            raise SystemExit(f"User submission policy requires exactly 12 slides; found {len(slides)}.")
        slide_text = "\\n".join(deck.read(name).decode("utf-8") for name in slides)
        summary = manifest["result_summary"]
        if (f"{summary['strict_passes']}/100" not in slide_text
                or f"{summary['turn_taken']}/100" not in slide_text):
            raise SystemExit("Submission deck does not include the latest strict-pass and turn-take results.")
        for name in slides:
            ElementTree.fromstring(deck.read(name))
    print(f"Evidence verified: latest {manifest['result_summary']['recordings']} recordings, {pass_report['passed']} strict passes; prior baseline {old_pass['passed']}; {len(slides)} slides.")
    print(f"Per-recording outputs: {len(partial)}/{manifest['per_recording_result_jsons_expected']}; judge enabled: {manifest['organizer_llm_judge_enabled']}.")
    print("Outstanding: single-session repeat runs, clean-machine reproduction check, demo length check, and the submission form.")


if __name__ == "__main__":
    main()
