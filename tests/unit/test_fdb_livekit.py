import inspect
import os
import pickle
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.fdb_livekit import (
    FdbConfig,
    INSTRUCTIONS,
    RECORDING_TAIL_S,
    _as_float,
    _as_int,
    benchmark_turn_handling,
    entrypoint,
    livekit_llm_model,
    livekit_speech_models,
    load_benchmark_module,
    ollama_llm_kwargs,
    prewarm,
)


class FdbLiveKitTests(unittest.TestCase):
    def test_config_reports_missing_keys_and_benchmark(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"INTERRA_FDB_V3_ROOT": str(Path(folder) / "missing-v3")}, clear=True
        ):
            config = FdbConfig.from_env()
            missing = config.missing_requirements()
        self.assertIn("LIVEKIT_URL", missing)
        self.assertNotIn("ELEVEN_API_KEY", missing)
        self.assertTrue(any(item.startswith("INTERRA_FDB_V3_ROOT") for item in missing))
        self.assertEqual(config.stt_fallback_models, ("assemblyai/universal-3-5-pro",))
        self.assertEqual(config.tts_fallback_models, ("deepgram/aura-2:athena",))
        self.assertEqual(config.session_cooldown_seconds, 3.0)

    def test_provider_fallbacks_and_cooldown_are_configurable(self):
        with patch.dict(
            os.environ,
            {
                "INTERRA_FDB_STT_FALLBACK_MODELS": "deepgram/flux-general-en, assemblyai/universal-streaming",
                "INTERRA_FDB_TTS_FALLBACK_MODELS": "deepgram/aura-2:athena",
                "INTERRA_FDB_SESSION_COOLDOWN_SECONDS": "6",
            },
            clear=True,
        ):
            config = FdbConfig.from_env()
        self.assertEqual(
            config.stt_fallback_models,
            ("deepgram/flux-general-en", "assemblyai/universal-streaming"),
        )
        self.assertEqual(config.tts_fallback_models, ("deepgram/aura-2:athena",))
        self.assertEqual(config.session_cooldown_seconds, 6.0)

    def test_loads_official_backend_by_path_without_import_side_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "latency_injector.py").write_text(
                "MARKER = 'sibling-loaded'\n", encoding="utf-8"
            )
            (root / "mock_apis.py").write_text(
                "from latency_injector import MARKER\n"
                "class MockAPIRegistry:\n"
                "    def __init__(self, latency_profile='instant'): self.profile = latency_profile\n"
                "    def call(self, name, **kwargs): return {'name': name, **kwargs}\n",
                encoding="utf-8",
            )
            module = load_benchmark_module(root)
            registry = module.MockAPIRegistry()
            self.assertEqual(module.MARKER, "sibling-loaded")
            self.assertEqual(registry.call("track_order", order_id="A1")["order_id"], "A1")

    def test_entrypoint_can_be_imported_by_a_worker_process(self):
        self.assertEqual(entrypoint.__qualname__, "entrypoint")
        self.assertNotIn("<locals>", entrypoint.__qualname__)
        pickle.dumps(entrypoint)

    def test_prewarm_uses_silero_imported_before_worker_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "mock_apis.py").write_text(
                "class MockAPIRegistry:\n"
                "    def __init__(self, latency_profile='instant'): pass\n",
                encoding="utf-8",
            )
            process = Mock(userdata={})
            with patch.dict(os.environ, {"INTERRA_FDB_V3_ROOT": folder}, clear=False):
                with patch("agent.fdb_livekit.silero.VAD.load", return_value="vad") as load:
                    prewarm(process)
            load.assert_called_once_with(min_speech_duration=0.05, min_silence_duration=0.3)
            self.assertEqual(process.userdata["vad"], "vad")
            self.assertIn("benchmark", process.userdata)

    def test_session_stays_open_after_benchmark_wav_disconnects(self):
        source = inspect.getsource(entrypoint)
        self.assertIn("close_on_disconnect=False", source)
        self.assertNotIn("session.say(", source)

    def test_reply_is_scheduled_inside_the_recording_window(self):
        handling = benchmark_turn_handling()
        self.assertLess(handling["endpointing"]["max_delay"], RECORDING_TAIL_S)
        preemptive = handling["preemptive_generation"]
        self.assertTrue(preemptive["enabled"])
        self.assertTrue(preemptive["preemptive_tts"])
        self.assertGreaterEqual(preemptive["max_speech_duration"], 120)
        self.assertGreaterEqual(preemptive["max_retries"], 20)
        self.assertIn("every tool needed", INSTRUCTIONS)

    def test_numeric_tool_arguments_accept_spoken_number_text(self):
        self.assertEqual(_as_int("2"), 2)
        self.assertEqual(_as_float("$1,200"), 1200.0)
        self.assertEqual(_as_int("two"), 2)
        self.assertEqual(_as_float("fifteen hundred dollars"), 1500.0)

    def test_thinking_flag_is_sent_only_after_the_ollama_probe(self):
        config = FdbConfig(fdb_v3_root=Path("."))
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("extra_body", ollama_llm_kwargs(config))
        with patch.dict(os.environ, {"INTERRA_OLLAMA_DISABLE_THINK": "1"}, clear=True):
            self.assertEqual(ollama_llm_kwargs(config)["extra_body"], {"think": False})

    def test_speech_uses_livekit_inference(self):
        inference = Mock()
        config = FdbConfig(fdb_v3_root=Path("."))
        speech_stt, speech_tts = livekit_speech_models(inference, config)
        stt_options = inference.STT.call_args.kwargs
        self.assertEqual(stt_options["model"], "deepgram/nova-3")
        self.assertEqual(stt_options["language"], "en")
        self.assertEqual(stt_options["fallback"], ["assemblyai/universal-3-5-pro"])
        self.assertEqual(stt_options["conn_options"].retry_interval, 4.0)
        tts_options = inference.TTS.call_args.kwargs
        self.assertEqual(tts_options["model"], "cartesia/sonic-3")
        self.assertEqual(tts_options["voice"], config.voice_id)
        self.assertEqual(tts_options["fallback"], ["deepgram/aura-2:athena"])
        self.assertEqual(tts_options["conn_options"].retry_interval, 4.0)
        self.assertIs(speech_stt, inference.STT.return_value)
        self.assertIs(speech_tts, inference.TTS.return_value)

    def test_hosted_llm_uses_existing_livekit_credentials(self):
        inference = Mock()
        config = FdbConfig(fdb_v3_root=Path("."))
        model = livekit_llm_model(inference, config)
        inference.LLM.assert_called_once_with(
            model="openai/gpt-4.1-mini",
            extra_kwargs={"temperature": 0.0, "parallel_tool_calls": False},
        )
        self.assertIs(model, inference.LLM.return_value)


if __name__ == "__main__":
    unittest.main()
