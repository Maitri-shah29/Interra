"""Run the judge page and both LiveKit workers in one container.

The page tails the trace the workers write, so all three must share a disk.
Each start reads the overrides saved from the settings page and lays them over
the deployment's environment. After a save the page sends SIGHUP and every
child restarts with the new values. A child that exits on its own is started
again after a back-off, so a bad setting cannot take the settings page down.

    python -m agent.serve --port 8780
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from dataclasses import dataclass
from typing import Mapping, Sequence

from agent.settings import SUPERVISOR_PID, load_overrides, settings_path

DEFAULT_PORT = 8780
VOICE_DISPATCH_NAME = "interra-benchmark"


@dataclass(frozen=True)
class Child:
    name: str
    command: tuple[str, ...]
    # Set after the saved overrides: values this child must have to share the host.
    env: tuple[tuple[str, str], ...] = ()


def default_children(port: int, host: str = "0.0.0.0") -> tuple[Child, ...]:
    python = sys.executable
    return (
        Child("page", (python, "-m", "agent.demo_page", "--host", host, "--port", str(port))),
        # Both workers would otherwise bind LiveKit's health port 8081.
        Child("voice", (python, "-m", "agent.fdb_livekit", "start"), (("INTERRA_AGENT_HTTP_PORT", "8081"),)),
        Child(
            "camera",
            (python, "-m", "agent.extension_livekit", "start"),
            # LiveKit keeps six idle job processes in production; one is enough here.
            (("INTERRA_AGENT_HTTP_PORT", "8082"), ("INTERRA_AGENT_IDLE_PROCESSES", "1")),
        ),
    )


def child_environment(base: Mapping[str, str], overrides: Mapping[str, str], supervisor_pid: int) -> dict[str, str]:
    """Deployment values, then dashboard overrides, then what the supervisor owns."""
    env = dict(base)
    # The Voice page dispatches to a named worker; a deployment may still name another.
    env.setdefault("INTERRA_FDB_AGENT_NAME", VOICE_DISPATCH_NAME)
    env.update(overrides)
    env[SUPERVISOR_PID] = str(supervisor_pid)
    return env


def restart_delay(consecutive_failures: int, low: float, high: float) -> float:
    return min(high, low * (2 ** max(0, consecutive_failures - 1)))


def log(message: str) -> None:
    print("[serve] " + message, file=sys.stderr, flush=True)


class Supervisor:
    """Owns one generation of children at a time; a restart ends the generation."""

    def __init__(
        self,
        children: Sequence[Child],
        base_env: Mapping[str, str],
        *,
        stop_timeout: float = 10.0,
        backoff: tuple[float, float] = (1.0, 30.0),
        healthy_after: float = 60.0,
    ):
        self.children = tuple(children)
        self.base_env = dict(base_env)
        self.stop_timeout = stop_timeout
        self.backoff = backoff
        self.healthy_after = healthy_after
        self.generation = 0
        self._restart = asyncio.Event()
        self._stop = asyncio.Event()

    def request_restart(self) -> None:
        self._restart.set()

    def request_stop(self) -> None:
        self._stop.set()

    def environment(self) -> dict[str, str]:
        path = settings_path(self.base_env)
        try:
            overrides = load_overrides(path)
        except ValueError as exc:
            # Starting without the overrides keeps the settings page reachable to fix them.
            log(f"ignoring saved settings: {exc}")
            overrides = {}
        if overrides:
            log(f"applying {len(overrides)} saved setting(s): " + ", ".join(sorted(overrides)))
        return child_environment(self.base_env, overrides, os.getpid())

    async def run(self) -> None:
        while not self._stop.is_set():
            self._restart.clear()
            self.generation += 1
            await self._run_generation(self.environment())

    async def _run_generation(self, env: dict[str, str]) -> None:
        keepers = [asyncio.create_task(self._keep(child, env), name="keep-" + child.name) for child in self.children]
        restart = asyncio.create_task(self._restart.wait(), name="restart-requested")
        stop = asyncio.create_task(self._stop.wait(), name="stop-requested")
        try:
            await asyncio.wait([restart, stop], return_when=asyncio.FIRST_COMPLETED)
            if restart.done() and not self._stop.is_set():
                log("restarting with the saved settings")
        finally:
            for task in (*keepers, restart, stop):
                task.cancel()
            await asyncio.gather(*keepers, restart, stop, return_exceptions=True)

    async def _keep(self, child: Child, env: dict[str, str]) -> None:
        """Run one child until cancelled, starting it again whenever it exits."""
        loop = asyncio.get_running_loop()
        failures = 0
        while True:
            started = loop.time()
            # Its own process group, so job processes the workers fork stop with them.
            process = await asyncio.create_subprocess_exec(
                *child.command, env={**env, **dict(child.env)}, start_new_session=True
            )
            log(f"{child.name} started (pid {process.pid})")
            try:
                code = await process.wait()
            finally:
                if process.returncode is None:
                    await self._terminate(child, process)
            # The leader is gone; anything it left in its group is orphaned.
            self._signal_group(process, signal.SIGKILL)
            failures = 0 if loop.time() - started >= self.healthy_after else failures + 1
            delay = restart_delay(failures, *self.backoff)
            log(f"{child.name} exited with {code}; starting it again in {delay:g} s")
            await asyncio.sleep(delay)

    async def _terminate(self, child: Child, process: asyncio.subprocess.Process) -> None:
        self._signal_group(process, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), self.stop_timeout)
        except asyncio.TimeoutError:
            log(f"{child.name} did not stop in {self.stop_timeout:g} s; killing it")
            self._signal_group(process, signal.SIGKILL)
            await process.wait()
        else:
            log(f"{child.name} stopped")

    @staticmethod
    def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass


async def serve(children: Sequence[Child]) -> None:
    supervisor = Supervisor(children, os.environ)
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGHUP, supervisor.request_restart)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, supervisor.request_stop)
    log(f"supervising {', '.join(child.name for child in children)}; settings at {settings_path()}")
    await supervisor.run()
    log("stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Interra judge page and both LiveKit workers.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or DEFAULT_PORT))
    args = parser.parse_args()
    asyncio.run(serve(default_children(args.port, args.host)))


if __name__ == "__main__":
    main()
