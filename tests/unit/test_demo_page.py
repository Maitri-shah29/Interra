"""The judge pages show the gate decision and do not invent a score."""

import http.client
import json
import os
import re
import subprocess
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from agent import demo_page
from agent.demo_page import (
    CAMERA_AGENT,
    HOME_AGENT,
    PAGES,
    ROOT,
    SCORED_ROOM,
    VOICE_AGENT,
    WEB_ROOT,
    config_payload,
    list_sessions,
    mint_token,
    page_html,
    read_trace_since,
    resume_offset,
    scored_replay,
    static_file,
)
from agent.fdb_livekit import benchmark_dispatch_name, register_benchmark_session


class DemoPageTests(unittest.TestCase):
    def test_results_page_states_the_scored_run_and_the_gate(self):
        page = page_html("results")
        lower = page.lower()
        self.assertIn("70/100 strict", page)
        self.assertIn("100/100 turn-take", page)
        self.assertIn("4.452 s average", page)
        self.assertIn("organizer llm judge off", lower)
        self.assertIn("29 of 100 re-recorded", lower)
        self.assertIn("dropped before it is logged", lower)
        self.assertIn("interra-benchmark", lower)
        self.assertIn("interra-camera-troubleshooter", lower)
        self.assertIn("Strict check not passed", page)

    def test_overview_states_the_same_figures_and_the_gate(self):
        page = page_html("index")
        text = re.sub(r"<[^>]+>", "", page)
        for phrase in ("70/100", "100/100", "4.452 s", "Organizer LLM judge off", "dropped before it is logged"):
            self.assertIn(phrase, text)
        for route in ("/voice", "/camera", "/replay", "/results"):
            self.assertIn('href="' + route + '"', page)

    def test_results_table_matches_the_readme(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        table = readme[readme.index("| Metric | 2026-10-04 |"):]
        rows = [line for line in table.splitlines()[2:] if line.startswith("|")]
        self.assertGreater(len(rows), 8)
        page = page_html("results")
        for line in rows:
            cells = [cell.strip().strip("*") for cell in line.strip("|").split("|")]
            expected = "<tr><td>" + cells[0] + '</td><td class="now">' + "</td><td>".join(cells[1:]) + "</td></tr>"
            self.assertIn(expected, page, cells[0])

    def test_every_page_shares_the_shell_and_links_the_others(self):
        for route, name in PAGES.items():
            with self.subTest(page=name):
                page = page_html(name)
                self.assertNotIn("{{", page)
                self.assertIn('id="logo-mark"', page)
                for other in PAGES:
                    self.assertIn('href="' + other + '"', page)
                self.assertIn('/static/base.css', page)
                self.assertIn('data-page="', page)

    def test_pages_use_only_files_this_server_sends(self):
        for name in PAGES.values():
            page = page_html(name)
            for asset in re.findall(r'"/static/([^"]+)"', page):
                with self.subTest(page=name, asset=asset):
                    self.assertIsNotNone(static_file(asset))
        for script in WEB_ROOT.glob("*.js"):
            for imported in re.findall(r'from "\./([^"]+)"', script.read_text(encoding="utf-8")):
                self.assertTrue((WEB_ROOT / imported).is_file(), script.name + " imports " + imported)
        live = (WEB_ROOT / "live.js").read_text(encoding="utf-8")
        # Live captions come from the agent's own transcription streams.
        self.assertIn('registerTextStreamHandler("lk.transcription"', live)
        self.assertIn('"/api/events?room="', live)

    def test_static_files_stay_inside_the_web_folder(self):
        self.assertEqual(static_file("base.css")[1], "text/css; charset=utf-8")
        self.assertEqual(static_file("live.js")[1], "text/javascript; charset=utf-8")
        for name in ("../README.md", "partials", "head.html", "index.html", "missing.js", ".hidden.js"):
            self.assertIsNone(static_file(name), name)

    def test_scored_recording_is_shown_with_its_official_result(self):
        replay = scored_replay()
        records = replay["records"]
        self.assertGreater(len(records), 10)
        self.assertEqual({record["room"] for record in records}, {SCORED_ROOM})
        calls = [record["call"] for record in records if record["kind"] == "tool_call"]
        self.assertEqual(calls[0]["function"], "search_apartments")
        self.assertEqual((calls[0]["args"]["bedrooms"], calls[0]["args"]["max_price"]), (2, 1600.0))
        result = replay["result"]
        self.assertEqual(result["scenario_id"], "housing_11")
        # The pinned evaluator did not pass this recording; the pages must say so.
        self.assertFalse(result["passed"])
        self.assertIn("search_apartments", result["failure_reason"])

    def test_sessions_are_listed_newest_first_with_what_happened(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "livekit-agent.jsonl"
            path.write_text(
                '{"time": 100, "room": "demo-voice-aaaaaa", "kind": "session_started"}\n'
                '{"time": 101, "room": "demo-voice-aaaaaa", "kind": "tool_call_dropped"}\n'
                '{"time": 102, "room": "demo-voice-aaaaaa", "kind": "tool_call"}\n'
                '{"time": 103, "room": "demo-voice-aaaaaa", "kind": "session_cleanup"}\n'
                '{"time": 200, "room": "demo-camera-bbbbbb", "kind": "session_started"}\n'
                'not json\n',
                encoding="utf-8",
            )
            sessions = list_sessions(path, now=230)
        self.assertEqual([session["room"] for session in sessions], ["demo-camera-bbbbbb", "demo-voice-aaaaaa"])
        camera, voice = sessions
        self.assertEqual((camera["mode"], camera["live"]), ("camera", True))
        self.assertEqual((voice["mode"], voice["live"], voice["closed"]), ("voice", False, True))
        self.assertEqual((voice["events"], voice["executed"], voice["dropped"]), (4, 1, 1))
        self.assertEqual((voice["first"], voice["last"]), (100, 103))

    def test_config_names_the_project_and_no_secret(self):
        environment = {
            "LIVEKIT_URL": "wss://example.livekit.cloud",
            "LIVEKIT_API_KEY": "devkey",
            "LIVEKIT_API_SECRET": "topsecret",
        }
        with patch.dict(os.environ, environment, clear=False):
            payload = config_payload()
        self.assertTrue(payload["livekit_configured"])
        self.assertEqual(payload["livekit_host"], "example.livekit.cloud")
        self.assertEqual((payload["voice_agent"], payload["camera_agent"]), (VOICE_AGENT, CAMERA_AGENT))
        self.assertIn("commit_quiet_seconds", payload)
        text = json.dumps(payload)
        self.assertNotIn("devkey", text)
        self.assertNotIn("topsecret", text)

    def test_config_works_from_a_request_thread_in_a_fresh_process(self):
        # Regression: the first /config request imported the worker module from
        # a handler thread, and LiveKit refused to register plugins there.
        probe = (
            "import threading, agent.demo_page as page\n"
            "out = {}\n"
            "worker = threading.Thread(target=lambda: out.update(page.config_payload()))\n"
            "worker.start(); worker.join()\n"
            "assert out['models']['stt'], out\n"
        )
        done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])

    def test_voice_and_camera_tokens_name_different_workers(self):
        environment = {
            "LIVEKIT_URL": "wss://example.livekit.cloud",
            "LIVEKIT_API_KEY": "devkey",
            "LIVEKIT_API_SECRET": "secret",
        }
        with patch.dict(os.environ, environment, clear=False):
            voice = _payload(mint_token("voice", "demo-voice-abc123"))
            camera = _payload(mint_token("camera", "demo-camera-abc123"))
        self.assertEqual(voice["video"]["room"], "demo-voice-abc123")
        self.assertEqual(camera["video"]["room"], "demo-camera-abc123")
        self.assertEqual(voice["roomConfig"]["agents"][0]["agentName"], VOICE_AGENT)
        self.assertEqual(camera["roomConfig"]["agents"][0]["agentName"], CAMERA_AGENT)
        self.assertNotIn("secret", json.dumps(voice))

    def test_home_token_dispatches_the_home_worker(self):
        with patch.dict(os.environ, {"LIVEKIT_URL": "wss://x", "LIVEKIT_API_KEY": "k", "LIVEKIT_API_SECRET": "s"}):
            home = _payload(mint_token("home", "demo-home-abc123"))
            with self.assertRaises(ValueError):
                mint_token("home", "demo-voice-abc123")
        self.assertEqual(home["roomConfig"]["agents"][0]["agentName"], HOME_AGENT)
        self.assertEqual(config_payload()["home_agent"], HOME_AGENT)

    def test_home_page_shows_devices_and_offers_an_unseen_one(self):
        page = page_html("house")
        self.assertIn('data-mode="home"', page)
        self.assertIn("Add a new device", page)
        self.assertIn("Play walkthrough", page)
        self.assertIn("/static/house.js", page)
        self.assertEqual(PAGES["/home"], "house")

    def test_token_rejects_a_room_that_does_not_match_the_mode(self):
        with patch.dict(os.environ, {"LIVEKIT_URL": "wss://x", "LIVEKIT_API_KEY": "k", "LIVEKIT_API_SECRET": "s"}):
            with self.assertRaises(ValueError):
                mint_token("voice", "demo-camera-abc123")
            with self.assertRaises(ValueError):
                mint_token("voice", SCORED_ROOM)

    def test_trace_reader_keeps_one_room_and_an_unfinished_line(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "livekit-agent.jsonl"
            path.write_text(
                '{"room":"other","kind":"tool_call"}\n'
                '{"room":"demo-voice-abc123","kind":"tool_call_dropped","call":{"function":"search_flights"}}\n',
                encoding="utf-8",
            )
            found, offset = read_trace_since(path, "demo-voice-abc123", 0)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["kind"], "tool_call_dropped")
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"room":"demo-voice-abc123","kind":"transcript","transcript":"Aug')
            again, stuck = read_trace_since(path, "demo-voice-abc123", offset)
            self.assertEqual(again, [])
            self.assertEqual(stuck, offset)

    def test_a_stream_starts_where_it_should(self):
        self.assertEqual(resume_offset(None, 120), 120)
        self.assertEqual(resume_offset("64", 120), 64)
        self.assertEqual(resume_offset("500", 120), 120)
        self.assertEqual(resume_offset("-1", 120), 120)
        # The Logs page asks for the whole room; a reconnect still resumes.
        self.assertEqual(resume_offset(None, 120, "0"), 0)
        self.assertEqual(resume_offset("64", 120, "0"), 64)

    def test_unnamed_benchmark_worker_stays_auto_dispatched(self):
        server = Mock()
        with patch.dict(os.environ, {"INTERRA_FDB_AGENT_NAME": ""}, clear=False):
            self.assertEqual(benchmark_dispatch_name(), "")
            register_benchmark_session(server)
        server.rtc_session.assert_called_once()
        self.assertEqual(server.rtc_session.call_args.kwargs["agent_name"], "")

    def test_named_benchmark_worker_is_explicit(self):
        server = Mock()
        with patch.dict(os.environ, {"INTERRA_FDB_AGENT_NAME": "interra-benchmark"}, clear=False):
            register_benchmark_session(server)
        self.assertEqual(server.rtc_session.call_args.kwargs["agent_name"], "interra-benchmark")


class DemoServerTests(unittest.TestCase):
    """The real request handler on an ephemeral local port."""

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.trace = Path(folder.name) / "livekit-agent.jsonl"
        self.trace.write_text(
            '{"time": 1, "room":"other","kind":"tool_call"}\n'
            '{"time": 2, "room":"demo-voice-abc123","kind":"tool_call_pending","call":{"function":"search_flights","args":{}}}\n',
            encoding="utf-8",
        )
        patcher = patch.object(demo_page, "TRACE_PATH", self.trace)
        patcher.start()
        self.addCleanup(patcher.stop)
        quiet = patch.object(demo_page.Handler, "log_message", lambda *args: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), demo_page.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def open(self, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", path, headers=headers or {})
        return connection.getresponse()

    def test_pages_and_assets_are_served_with_their_types(self):
        for path in PAGES:
            response = self.open(path)
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200, path)
            self.assertTrue(response.getheader("Content-Type").startswith("text/html"), path)
            self.assertNotIn("{{", body)
        for path, kind in (
            ("/static/base.css", "text/css"),
            ("/static/live.js", "text/javascript"),
            ("/static/orb.js", "text/javascript"),
            ("/favicon.svg", "image/svg+xml"),
        ):
            response = self.open(path)
            self.assertTrue(response.read(), path)
            self.assertEqual(response.status, 200, path)
            self.assertTrue(response.getheader("Content-Type").startswith(kind), path)
        for path in ("/static/../README.md", "/static/partials/head.html", "/../pyproject.toml", "/token"):
            response = self.open(path)
            response.read()
            self.assertEqual(response.status, 404, path)

    def test_api_answers_json_without_secrets(self):
        with patch.dict(os.environ, {"LIVEKIT_API_SECRET": "topsecret"}, clear=False):
            config = self.open("/api/config")
            config_body = config.read().decode("utf-8")
        self.assertEqual(config.status, 200)
        self.assertNotIn("topsecret", config_body)
        replay = json.loads(self.open("/api/replay").read())
        self.assertEqual(replay["room"], SCORED_ROOM)
        self.assertFalse(replay["result"]["passed"])
        sessions = json.loads(self.open("/api/sessions").read())["sessions"]
        self.assertEqual(sessions[0]["room"], SCORED_ROOM)
        self.assertTrue(sessions[0]["recorded"])
        self.assertIn("demo-voice-abc123", [session["room"] for session in sessions])

    def test_home_api_starts_the_home_and_runs_the_walkthrough(self):
        home = json.loads(self.open("/api/home").read())
        self.assertEqual(len(home["devices"]), 8)
        self.assertEqual(home["unseen_device"]["id"], "bedroom-air-purifier")
        self.assertNotIn("bedroom-air-purifier", [device["id"] for device in home["devices"]])
        events = json.loads(self.open("/api/home/walkthrough").read())["events"]
        dropped = [e for e in events if e["kind"] == "tool_call_dropped"]
        self.assertEqual(dropped[0]["call"]["args"]["setpoint"], 22)
        executed = [e["call"]["args"].get("setpoint") for e in events if e["kind"] == "tool_call"]
        self.assertNotIn(22, executed)
        self.assertIn(24, executed)
        final = [e for e in events if e["kind"] == "home_devices"][-1]["devices"]
        self.assertEqual(next(d for d in final if d["id"] == "living-room-ac")["state"]["coolingSetpoint"], 24)
        self.assertEqual(next(d for d in final if d["id"] == "front-door")["state"]["lock"], "unlocked")
        self.assertIn("home_device_added", [e["kind"] for e in events])

    def test_token_without_livekit_settings_is_refused(self):
        with patch.dict(os.environ, {"LIVEKIT_URL": "", "LIVEKIT_API_KEY": "", "LIVEKIT_API_SECRET": ""}):
            response = self.open("/api/token?mode=voice&room=demo-voice-abc123")
            body = response.read().decode("utf-8")
        self.assertEqual(response.status, 400)
        self.assertIn("missing LiveKit configuration", body)

    def test_events_replay_one_room_from_a_resume_point(self):
        response = self.open("/api/events?room=demo-voice-abc123", {"Last-Event-ID": "0"})
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        hello, record = _read_events(response, 2)
        self.assertEqual(hello["event"], "hello")
        self.assertTrue(json.loads(hello["data"])["resumed"])
        self.assertEqual(json.loads(record["data"])["kind"], "tool_call_pending")
        self.assertEqual(int(record["id"]), self.trace.stat().st_size)

    def test_logs_page_reads_a_whole_room_then_follows_it(self):
        response = self.open("/api/events?room=demo-voice-abc123&since=0")
        hello, record = _read_events(response, 2)
        self.assertFalse(json.loads(hello["data"])["resumed"])
        self.assertEqual(json.loads(record["data"])["kind"], "tool_call_pending")
        with self.trace.open("a", encoding="utf-8") as handle:
            handle.write('{"time": 3, "room":"demo-voice-abc123","kind":"tool_call_dropped","call":{"function":"search_flights","args":{}}}\n')
        followed = _read_events(response, 1)[0]
        self.assertEqual(json.loads(followed["data"])["kind"], "tool_call_dropped")

    def test_events_start_at_the_end_and_follow_new_lines(self):
        response = self.open("/api/events?room=demo-voice-abc123")
        hello = _read_events(response, 1)[0]
        self.assertFalse(json.loads(hello["data"])["resumed"])
        with self.trace.open("a", encoding="utf-8") as handle:
            handle.write('{"room":"other","kind":"tool_call"}\n')
            handle.write('{"room":"demo-voice-abc123","kind":"tool_call_dropped","call":{"function":"search_flights","args":{}}}\n')
        record = _read_events(response, 1)[0]
        self.assertEqual(json.loads(record["data"])["kind"], "tool_call_dropped")

    def test_events_reject_a_room_name_that_is_not_a_room(self):
        for room in ("../trace", "a b", ""):
            response = self.open("/api/events?room=" + room.replace(" ", "%20"))
            response.read()
            self.assertEqual(response.status, 400, room)


def _read_events(response, count):
    """Parse server-sent events until ``count`` complete events arrive."""
    events, current = [], {}
    while len(events) < count:
        line = response.fp.readline().decode("utf-8")
        if not line:
            raise AssertionError("the event stream closed early")
        line = line.rstrip("\n")
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        current[field] = value[1:] if value.startswith(" ") else value
    return events


def _payload(token: str) -> dict:
    import base64
    body = token.split(".")[1]
    body += "=" * (-len(body) % 4)
    return json.loads(base64.urlsafe_b64decode(body))


if __name__ == "__main__":
    unittest.main()
