"""The supervisor restarts children with saved settings and never leaves one running."""

import asyncio
import json
import os
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent import home_livekit
from agent.fdb_livekit import agent_server_options
from agent.serve import Child, Supervisor, child_environment, default_children, restart_delay
from agent.settings import save_overrides
from scripts.fdb_v3 import PINNED_COMMIT

ROOT = Path(__file__).resolve().parents[2]

# Writes its pid and one variable, then waits to be stopped.
REPORTER = (
    "import json, os, sys, time\n"
    "with open(sys.argv[1], 'a') as f: f.write(json.dumps([os.getpid(), os.environ.get('INTERRA_FDB_LLM_MODEL')]) + '\\n')\n"
    "time.sleep(60)\n"
)


def _lines(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _until(predicate, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ChildEnvironmentTests(unittest.TestCase):
    def test_overrides_beat_the_deployment_and_the_supervisor_owns_its_pid(self):
        env = child_environment(
            {"LIVEKIT_URL": "wss://deployed", "INTERRA_SUPERVISOR_PID": "1"},
            {"LIVEKIT_URL": "wss://dashboard"},
            4321,
        )
        self.assertEqual(env["LIVEKIT_URL"], "wss://dashboard")
        self.assertEqual(env["INTERRA_SUPERVISOR_PID"], "4321")
        self.assertEqual(env["INTERRA_FDB_AGENT_NAME"], "interra-benchmark")

    def test_a_deployed_or_saved_dispatch_name_is_kept(self):
        self.assertEqual(child_environment({"INTERRA_FDB_AGENT_NAME": ""}, {}, 1)["INTERRA_FDB_AGENT_NAME"], "")
        saved = child_environment({}, {"INTERRA_FDB_AGENT_NAME": "other"}, 1)
        self.assertEqual(saved["INTERRA_FDB_AGENT_NAME"], "other")

    def test_restart_delay_doubles_up_to_the_cap(self):
        self.assertEqual([restart_delay(n, 1.0, 5.0) for n in (0, 1, 2, 3, 4)], [1.0, 1.0, 2.0, 4.0, 5.0])

    def test_default_children_are_the_page_and_three_workers_on_separate_health_ports(self):
        children = default_children(9999)
        self.assertEqual([child.name for child in children], ["page", "voice", "camera", "home"])
        self.assertIn("9999", children[0].command)
        self.assertEqual(children[1].command[-2:], ("agent.fdb_livekit", "start"))
        self.assertEqual(children[2].command[-2:], ("agent.extension_livekit", "start"))
        self.assertEqual(children[3].command[-2:], ("agent.home_livekit", "start"))
        ports = [dict(child.env).get("INTERRA_AGENT_HTTP_PORT") for child in children[1:]]
        self.assertNotIn(None, ports)
        self.assertEqual(len(set(ports)), 3, ports)

    def test_the_home_worker_takes_its_health_port_from_the_environment(self):
        captured = {}

        class FakeServer:
            def __init__(self, **options):
                captured.update(options)

            def rtc_session(self, entrypoint, agent_name):
                captured["agent_name"] = agent_name

        with patch.dict(os.environ, {"INTERRA_AGENT_HTTP_PORT": "8083", "INTERRA_AGENT_IDLE_PROCESSES": "1",
                                     "INTERRA_AGENT_LOAD_THRESHOLD": ""}), \
                patch("livekit.agents.AgentServer", FakeServer), \
                patch("livekit.agents.cli.run_app"), \
                patch.object(home_livekit, "load_local_env"):
            home_livekit.main()
        self.assertEqual(captured, {"port": 8083, "num_idle_processes": 1, "agent_name": "interra-home"})

    def test_worker_options_come_from_the_environment_only_when_set(self):
        with patch.dict(os.environ, {"INTERRA_AGENT_HTTP_PORT": "", "INTERRA_AGENT_IDLE_PROCESSES": "",
                                     "INTERRA_AGENT_LOAD_THRESHOLD": ""}):
            self.assertEqual(agent_server_options(num_idle_processes=1), {"num_idle_processes": 1})
        with patch.dict(os.environ, {"INTERRA_AGENT_HTTP_PORT": "8082", "INTERRA_AGENT_IDLE_PROCESSES": "2",
                                     "INTERRA_AGENT_LOAD_THRESHOLD": "0.95"}):
            self.assertEqual(agent_server_options(num_idle_processes=1),
                             {"port": 8082, "num_idle_processes": 2, "load_threshold": 0.95})


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.settings = self.folder / "overrides.json"
        self.base = {**os.environ, "INTERRA_SETTINGS_FILE": str(self.settings), "INTERRA_FDB_LLM_MODEL": "deployed"}

    async def test_restart_applies_saved_settings_and_stops_the_old_children(self):
        out = self.folder / "reports.jsonl"
        supervisor = Supervisor([Child("reporter", (sys.executable, "-c", REPORTER, str(out)))], self.base, stop_timeout=5)
        task = asyncio.create_task(supervisor.run())
        try:
            await _until(lambda: len(_lines(out)) == 1)
            first_pid, first_value = _lines(out)[0]
            self.assertEqual(first_value, "deployed")

            save_overrides(self.settings, {"INTERRA_FDB_LLM_MODEL": "from-dashboard"})
            supervisor.request_restart()
            await _until(lambda: len(_lines(out)) == 2)
            second_pid, second_value = _lines(out)[1]
            self.assertEqual(second_value, "from-dashboard")
            self.assertNotEqual(first_pid, second_pid)
            self.assertFalse(_alive(first_pid))
            self.assertEqual(supervisor.generation, 2)
        finally:
            supervisor.request_stop()
            await asyncio.wait_for(task, 10)
        self.assertFalse(_alive(_lines(out)[-1][0]))

    async def test_a_child_that_exits_is_started_again_and_others_keep_running(self):
        out = self.folder / "reports.jsonl"
        crashes = self.folder / "crashes"
        crasher = (sys.executable, "-c", "import sys; open(sys.argv[1], 'a').write('x'); sys.exit(3)", str(crashes))
        supervisor = Supervisor(
            [Child("reporter", (sys.executable, "-c", REPORTER, str(out))), Child("crasher", crasher)],
            self.base,
            backoff=(0.05, 0.1),
        )
        task = asyncio.create_task(supervisor.run())
        try:
            await _until(lambda: crashes.is_file() and len(crashes.read_text()) >= 3)
            self.assertEqual(len(_lines(out)), 1)
            self.assertTrue(_alive(_lines(out)[0][0]))
        finally:
            supervisor.request_stop()
            await asyncio.wait_for(task, 10)

    async def test_a_childs_own_values_win_over_saved_settings(self):
        save_overrides(self.settings, {"INTERRA_FDB_LLM_MODEL": "from-dashboard"})
        out = self.folder / "reports.jsonl"
        child = Child("reporter", (sys.executable, "-c", REPORTER, str(out)), (("INTERRA_FDB_LLM_MODEL", "child"),))
        supervisor = Supervisor([child], self.base)
        task = asyncio.create_task(supervisor.run())
        try:
            await _until(lambda: len(_lines(out)) == 1)
            self.assertEqual(_lines(out)[0][1], "child")
        finally:
            supervisor.request_stop()
            await asyncio.wait_for(task, 10)

    async def test_an_unreadable_settings_file_starts_with_the_deployment_values(self):
        self.settings.write_text("{broken", encoding="utf-8")
        out = self.folder / "reports.jsonl"
        supervisor = Supervisor([Child("reporter", (sys.executable, "-c", REPORTER, str(out)))], self.base)
        task = asyncio.create_task(supervisor.run())
        try:
            await _until(lambda: len(_lines(out)) == 1)
            self.assertEqual(_lines(out)[0][1], "deployed")
        finally:
            supervisor.request_stop()
            await asyncio.wait_for(task, 10)

    async def test_a_child_that_ignores_sigterm_is_killed(self):
        out = self.folder / "reports.jsonl"
        stubborn = "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" + REPORTER
        supervisor = Supervisor([Child("stubborn", (sys.executable, "-c", stubborn, str(out)))], self.base, stop_timeout=0.3)
        task = asyncio.create_task(supervisor.run())
        await _until(lambda: len(_lines(out)) == 1)
        supervisor.request_stop()
        await asyncio.wait_for(task, 10)
        self.assertFalse(_alive(_lines(out)[0][0]))


class DeployImageTests(unittest.TestCase):
    def test_deploy_image_fetches_the_pinned_benchmark_and_runs_the_supervisor(self):
        dockerfile = (ROOT / "Dockerfile.deploy").read_text(encoding="utf-8")
        self.assertIn("ARG FDB_V3_COMMIT=" + PINNED_COMMIT, dockerfile)
        self.assertIn('CMD ["python", "-m", "agent.serve"]', dockerfile)
        self.assertRegex(dockerfile, r"pip install [^\n]*-e '\.\[voice\]'")
        railway = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))
        self.assertEqual(railway["build"]["dockerfilePath"], "Dockerfile.deploy")
        self.assertEqual(railway["deploy"]["healthcheckPath"], "/api/health")

    def test_submission_image_is_unchanged(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertTrue(re.search(r'CMD \["python", "-m", "agent.fdb_livekit", "start"\]', dockerfile))


if __name__ == "__main__":
    unittest.main()
