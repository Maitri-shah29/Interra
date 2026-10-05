"""Build a self-contained Kaggle worker from tracked runtime files."""

from __future__ import annotations

import base64
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_KERNEL_ID = "adityavardhankochar/interra-fdb-v3-benchmark"
BENCHMARK_TITLE = "interra fdb v3 benchmark"


SEED_FOLDER = "fdb-seed"


def _source_archive(seed: Path | None = None) -> bytes:
    """Zip the working tree, so an uncommitted agent fix is what Kaggle runs.

    ``seed`` adds a folder from ``agent.fdb_offline outage-seed`` as
    ``fdb-seed/``; the worker places those results before the benchmark.
    """
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative in ("src", "scripts", "pyproject.toml"):
            path = ROOT / relative
            files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
            for item in files:
                if "__pycache__" in item.parts or item.suffix == ".pyc":
                    continue
                bundle.write(item, item.relative_to(ROOT).as_posix())
        if seed is not None:
            if not (seed / "seed-manifest.json").is_file():
                raise FileNotFoundError(f"{seed} has no seed-manifest.json")
            for item in sorted(path for path in seed.rglob("*") if path.is_file()):
                bundle.write(item, (Path(SEED_FOLDER) / item.relative_to(seed)).as_posix())
    return buffer.getvalue()


def parse_settings(pairs: list[str]) -> dict[str, str]:
    """Parse ``NAME=VALUE`` experiment settings; only non-secret INTERRA_* names."""
    settings: dict[str, str] = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name.startswith("INTERRA_"):
            raise ValueError(f"Expected INTERRA_NAME=VALUE, got {pair!r}")
        settings[name] = value
    return settings


def _worker_source(settings: dict[str, str] | None = None, seed: Path | None = None) -> str:
    archive = _source_archive(seed)
    payload = base64.b64encode(archive).decode("ascii")
    source = (ROOT / "scripts" / "kaggle_setup.py").read_text(encoding="utf-8")
    source = source.replace("RUN_FULL_BENCHMARK = False", "RUN_FULL_BENCHMARK = True", 1)
    source = source.replace('EMBEDDED_SOURCE_B64 = ""', f'EMBEDDED_SOURCE_B64 = "{payload}"', 1)
    environment = f"RUN_ENVIRONMENT: dict[str, str] = {json.dumps(settings or {}, sort_keys=True)}"
    source = source.replace("RUN_ENVIRONMENT: dict[str, str] = {}", environment, 1)
    if (
        "RUN_FULL_BENCHMARK = True" not in source
        or f'EMBEDDED_SOURCE_B64 = "{payload}"' not in source
        or environment not in source
    ):
        raise RuntimeError("Kaggle setup source has changed; packaging substitutions failed")
    return source


def notebook_document(source: str) -> dict[str, object]:
    """Wrap the worker in a real ipynb so Kaggle's notebook converter can parse it."""
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "# Interra FDB-v3 Kaggle worker\n",
                    "\n",
                    "Do **not** attach the private source dataset. This notebook embeds the runtime.\n",
                    "Attach Secrets named `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.\n",
                    "Enable GPU (T4) and Internet, then use **Save Version → Save & Run All**.\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "id": "interra-fdb-v3-worker",
                "metadata": {},
                "outputs": [],
                "source": source,
            },
        ],
    }


def package(
    destination: Path, *, kernel_id: str = BENCHMARK_KERNEL_ID, title: str = BENCHMARK_TITLE,
    settings: dict[str, str] | None = None, seed: Path | None = None,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        settings = {**(settings or {}), "INTERRA_FDB_SEED_RESULTS": SEED_FOLDER}
    source = _worker_source(settings, seed)
    (destination / "interra_setup.py").write_text(source, encoding="utf-8")
    notebook = notebook_document(source)
    (destination / "interra_setup.ipynb").write_text(
        json.dumps(notebook, indent=1) + "\n",
        encoding="utf-8",
    )
    metadata = json.loads((ROOT / "kaggle" / "kernel-metadata.json").read_text(encoding="utf-8"))
    metadata["id"] = kernel_id
    metadata["title"] = title
    metadata["code_file"] = "interra_setup.ipynb"
    metadata["kernel_type"] = "notebook"
    metadata["dataset_sources"] = []
    (destination / "kernel-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--kernel-id", default=BENCHMARK_KERNEL_ID)
    parser.add_argument("--title", default=BENCHMARK_TITLE)
    parser.add_argument("--set", dest="settings", action="append", default=[],
                        metavar="INTERRA_NAME=VALUE", help="Non-secret setting exported for this run")
    parser.add_argument("--seed", type=Path,
                        help="Results folder from `agent.fdb_offline outage-seed`; only the other recordings run")
    args = parser.parse_args()
    package(args.destination, kernel_id=args.kernel_id, title=args.title,
            settings=parse_settings(args.settings), seed=args.seed)
