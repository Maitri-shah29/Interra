"""Kaggle worker setup sequencing and credential boundary."""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, Mock, patch
import zipfile

from scripts import kaggle_package, kaggle_setup


class KaggleSetupTests(unittest.TestCase):
    def test_embedded_update_takes_precedence_over_old_dataset(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "input"
            inputs.mkdir()
            with zipfile.ZipFile(inputs / "interra-source.zip", "w") as bundle:
                bundle.writestr("pyproject.toml", "old dataset")
            embedded = io.BytesIO()
            with zipfile.ZipFile(embedded, "w") as bundle:
                bundle.writestr("pyproject.toml", "updated notebook")
            with (
                patch.object(kaggle_setup, "INPUT_ROOT", inputs),
                patch.object(kaggle_setup, "WORK", root / "work"),
                patch.object(kaggle_setup, "EMBEDDED_SOURCE_B64", base64.b64encode(embedded.getvalue()).decode()),
            ):
                source = kaggle_setup.source_root()
            self.assertEqual((source / "pyproject.toml").read_text(), "updated notebook")

    def test_package_can_target_existing_notebook_without_changing_privacy(self) -> None:
        with TemporaryDirectory() as directory:
            destination = kaggle_package.package(Path(directory), kernel_id="owner/current", title="Current notebook")
            metadata = json.loads((destination / "kernel-metadata.json").read_text())
            self.assertEqual(metadata["id"], "owner/current")
            self.assertTrue(metadata["is_private"])
            self.assertEqual(metadata["dataset_sources"], [])

    def test_full_run_stops_before_install_when_livekit_secrets_missing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(kaggle_setup, "RUN_FULL_BENCHMARK", True),
                patch.object(kaggle_setup, "PROJECT", root / "project"),
                patch.object(kaggle_setup, "source_root", return_value=root),
                patch.object(kaggle_setup.shutil, "copytree"),
                patch.object(kaggle_setup, "read_kaggle_secrets", return_value=["LIVEKIT_API_KEY"]),
                patch.object(kaggle_setup, "run") as run,
            ):
                with self.assertRaisesRegex(RuntimeError, "LIVEKIT_API_KEY"):
                    kaggle_setup.main()
            run.assert_not_called()

    def test_results_archive_holds_every_recording_and_report_without_audio(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "project" / ".runtime" / "Full-Duplex-Bench" / "v3" / "fdb_v3_data_released"
            for index in range(3):
                folder = data / f"example_{index}"
                folder.mkdir(parents=True)
                (folder / "result_interra_elevenlabs.json").write_text("{}")
                (folder / "output_interra_elevenlabs.wav").write_bytes(b"RIFF")
            reports = root / "project" / "artifacts" / "fdb_v3"
            reports.mkdir(parents=True)
            (reports / "livekit-agent.jsonl").write_text("{}\n")
            (reports / "interra_elevenlabs_pass_rate_report.json").write_text("{}")
            tool_log = root / "agent_tool_calls.log"
            tool_log.write_text("{}\n")
            archive = root / "results.zip"
            count = kaggle_setup.archive_results(root / "project", archive, tool_log)
            with zipfile.ZipFile(archive) as bundle:
                names = sorted(bundle.namelist())
        self.assertEqual(count, 3)
        self.assertEqual(len([name for name in names if name.startswith("per-recording/")]), 3)
        self.assertIn("reports/livekit-agent.jsonl", names)
        self.assertIn("reports/agent_tool_calls.log", names)
        self.assertFalse(any(name.endswith(".wav") for name in names))

    def test_stops_running_ollama_process(self) -> None:
        server = Mock()
        server.poll.return_value = None
        kaggle_setup.stop_ollama(server)
        server.terminate.assert_called_once_with()
        server.wait.assert_called_once_with(timeout=10)

    def test_virtual_environment_is_outside_saved_outputs(self) -> None:
        self.assertFalse(kaggle_setup.VENV.is_relative_to(kaggle_setup.WORK))

    def test_extracts_explicit_source_archive(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "input"
            inputs.mkdir()
            with zipfile.ZipFile(inputs / "interra-source.zip", "w") as bundle:
                bundle.writestr("pyproject.toml", "[project]\nname='interra-runtime'\n")
                bundle.writestr("scripts/fdb_v3.py", "# setup entry point\n")
            with (
                patch.object(kaggle_setup, "INPUT_ROOT", inputs),
                patch.object(kaggle_setup, "WORK", root / "work"),
            ):
                source = kaggle_setup.source_root()
            self.assertTrue((source / "pyproject.toml").is_file())
            self.assertTrue((source / "scripts" / "fdb_v3.py").is_file())

    def test_extracts_embedded_source_when_dataset_is_unmounted(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("pyproject.toml", "[project]\nname='interra-runtime'\n")
            payload = base64.b64encode(archive.read_bytes()).decode("ascii")
            with (
                patch.object(kaggle_setup, "INPUT_ROOT", root / "unmounted"),
                patch.object(kaggle_setup, "WORK", root / "work"),
                patch.object(kaggle_setup, "EMBEDDED_SOURCE_B64", payload),
            ):
                source = kaggle_setup.source_root()
            self.assertTrue((source / "pyproject.toml").is_file())

    def test_rejects_archive_path_traversal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "input"
            inputs.mkdir()
            with zipfile.ZipFile(inputs / "interra-source.zip", "w") as bundle:
                bundle.writestr("../escape.txt", "unsafe")
            with (
                patch.object(kaggle_setup, "INPUT_ROOT", inputs),
                patch.object(kaggle_setup, "WORK", root / "work"),
            ):
                with self.assertRaises(ValueError):
                    kaggle_setup.source_root()
            self.assertFalse((root / "escape.txt").exists())

    def test_installs_venv_package_before_creating_environment(self) -> None:
        commands: list[list[str]] = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(kaggle_setup, "PROJECT", root / "project"),
                patch.object(kaggle_setup, "VENV", root / "venv"),
                patch.object(kaggle_setup, "REPORT", root / "report.json"),
                patch.object(kaggle_setup, "source_root", return_value=root),
                patch.object(kaggle_setup.shutil, "copytree"),
                patch.object(kaggle_setup, "run", side_effect=lambda command, **_: commands.append(command)),
                patch.object(kaggle_setup, "start_ollama") as start_ollama,
                patch.object(kaggle_setup, "stop_ollama") as stop_ollama,
                patch.object(kaggle_setup, "enable_fast_qwen_if_supported"),  # probes Ollama with curl
                patch.object(kaggle_setup, "read_kaggle_secrets", return_value=["LIVEKIT_API_KEY"]),
                patch.dict(os.environ, {"INTERRA_FDB_LLM_PROVIDER": "ollama"}),
            ):
                kaggle_setup.main()
            stop_ollama.assert_called_once_with(start_ollama.return_value)

            install = next(i for i, command in enumerate(commands) if command[:3] == ["apt-get", "install", "-y"])
            create = next(i for i, command in enumerate(commands) if command[1:3] == ["-m", "venv"])
            self.assertLess(install, create)
            self.assertIn(
                f"python{kaggle_setup.sys.version_info.major}.{kaggle_setup.sys.version_info.minor}-venv",
                commands[install],
            )
            self.assertFalse(json.loads((root / "report.json").read_text())["livekit_ready"])
            self.assertFalse(any("fdb_v3.py" in " ".join(command) and "all" in command for command in commands))

    def test_stops_ollama_when_setup_fails_after_start(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            def fail_on_model_pull(command: list[str], **_: object) -> None:
                if command[:2] == ["ollama", "pull"]:
                    raise RuntimeError("model pull failed")

            with (
                patch.object(kaggle_setup, "PROJECT", root / "project"),
                patch.object(kaggle_setup, "VENV", root / "venv"),
                patch.object(kaggle_setup, "source_root", return_value=root),
                patch.object(kaggle_setup.shutil, "copytree"),
                patch.object(kaggle_setup, "run", side_effect=fail_on_model_pull),
                patch.object(kaggle_setup, "start_ollama") as start_ollama,
                patch.object(kaggle_setup, "stop_ollama") as stop_ollama,
                patch.dict(os.environ, {"INTERRA_FDB_LLM_PROVIDER": "ollama"}),
            ):
                with self.assertRaisesRegex(RuntimeError, "model pull failed"):
                    kaggle_setup.main()
            stop_ollama.assert_called_once_with(start_ollama.return_value)

    def test_hosted_llm_skips_local_model_setup(self) -> None:
        commands: list[list[str]] = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(kaggle_setup, "PROJECT", root / "project"),
                patch.object(kaggle_setup, "VENV", root / "venv"),
                patch.object(kaggle_setup, "REPORT", root / "report.json"),
                patch.object(kaggle_setup, "source_root", return_value=root),
                patch.object(kaggle_setup.shutil, "copytree"),
                patch.object(kaggle_setup, "run", side_effect=lambda command, **_: commands.append(command)),
                patch.object(kaggle_setup, "start_ollama") as start_ollama,
                patch.object(kaggle_setup, "read_kaggle_secrets", return_value=[]),
                patch.dict(os.environ, {"INTERRA_FDB_LLM_PROVIDER": "livekit"}),
            ):
                kaggle_setup.main()
            start_ollama.assert_not_called()
            self.assertFalse(any("ollama" in " ".join(command) for command in commands))
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(report["llm_model"], "openai/gpt-4.1-mini")

    def test_package_emits_parseable_notebook_without_dataset_source(self) -> None:
        with TemporaryDirectory() as directory:
            destination = kaggle_package.package(Path(directory) / "upload")
            notebook = json.loads((destination / "interra_setup.ipynb").read_text(encoding="utf-8"))
            metadata = json.loads((destination / "kernel-metadata.json").read_text(encoding="utf-8"))
            cell_source = notebook["cells"][1]["source"]
            source = cell_source if isinstance(cell_source, str) else "".join(cell_source)
            self.assertEqual(notebook["nbformat"], 4)
            self.assertEqual(metadata["kernel_type"], "notebook")
            self.assertEqual(metadata["code_file"], "interra_setup.ipynb")
            self.assertEqual(metadata["dataset_sources"], [])
            self.assertEqual(metadata["id"], kaggle_package.BENCHMARK_KERNEL_ID)
            self.assertIn("RUN_FULL_BENCHMARK = True", source)
            self.assertNotIn('EMBEDDED_SOURCE_B64 = ""', source)
            self.assertIn("EMBEDDED_SOURCE_B64 = ", source)
            json.loads(json.dumps(notebook))
            payload = source.split('EMBEDDED_SOURCE_B64 = "', 1)[1].split('"', 1)[0]
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload))) as bundle:
                agent = bundle.read("src/agent/fdb_livekit.py").decode("utf-8")
            self.assertIn("\nasync def entrypoint(", agent)
            self.assertNotIn("main.<locals>.entrypoint", agent)
            self.assertIn("close_on_disconnect=False", agent)
            self.assertIn('"preemptive_tts": True', agent)
            self.assertIn("RUN_ENVIRONMENT: dict[str, str] = {}", source)

    def test_package_embeds_experiment_settings(self) -> None:
        settings = kaggle_package.parse_settings(["INTERRA_FDB_ENDPOINTING_MAX_DELAY=1.8"])
        with TemporaryDirectory() as directory:
            destination = kaggle_package.package(Path(directory), settings=settings)
            source = (destination / "interra_setup.py").read_text(encoding="utf-8")
        self.assertIn(
            'RUN_ENVIRONMENT: dict[str, str] = {"INTERRA_FDB_ENDPOINTING_MAX_DELAY": "1.8"}', source
        )

    def test_package_embeds_seed_results_and_points_the_worker_at_them(self) -> None:
        with TemporaryDirectory() as directory:
            seed = Path(directory) / "seed"
            (seed / "shop_01_a").mkdir(parents=True)
            (seed / "shop_01_a" / "result_interra_elevenlabs.json").write_text("{}")
            (seed / "seed-manifest.json").write_text("{}")
            destination = kaggle_package.package(Path(directory) / "upload", seed=seed)
            source = (destination / "interra_setup.py").read_text(encoding="utf-8")
        self.assertIn('"INTERRA_FDB_SEED_RESULTS": "fdb-seed"', source)
        payload = source.split('EMBEDDED_SOURCE_B64 = "', 1)[1].split('"', 1)[0]
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload))) as bundle:
            names = bundle.namelist()
        self.assertIn("fdb-seed/shop_01_a/result_interra_elevenlabs.json", names)
        self.assertIn("fdb-seed/seed-manifest.json", names)

    def test_package_rejects_non_interra_settings(self) -> None:
        for pair in ("LIVEKIT_API_SECRET=x", "INTERRA_FDB_ENDPOINTING_MAX_DELAY"):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                kaggle_package.parse_settings([pair])

    def test_run_environment_exports_only_interra_settings(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            kaggle_setup.apply_run_environment({"INTERRA_FDB_ENDPOINTING_MAX_DELAY": "1.8"})
            self.assertEqual(os.environ["INTERRA_FDB_ENDPOINTING_MAX_DELAY"], "1.8")
            with self.assertRaises(ValueError):
                kaggle_setup.apply_run_environment({"LIVEKIT_URL": "wss://example.invalid"})


if __name__ == "__main__":
    unittest.main()
