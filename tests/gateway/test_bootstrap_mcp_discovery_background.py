"""MCP discovery never delays platform start (#16856).

``discover_mcp_tools()`` waits up to 120s per configured server. Awaiting it before
``runner.start()`` keeps every platform (Discord shard, Telegram polling) offline for that whole
wait, so discovery runs as a task registered on the runner — which the shutdown drain cancels —
and a failure stays non-fatal.
"""
from __future__ import annotations

import asyncio
import threading

from gateway import run as gateway_run
from gateway import run_bootstrap


class _FakeRunner:
    def __init__(self, config=None):
        self.config = config or object()
        self.started = False
        self.should_exit_cleanly = False
        self.exit_reason = None
        self.exit_code = None
        self._running = True

    async def start(self):
        self.started = True
        return True


def test_platforms_start_before_mcp_discovery_finishes(monkeypatch):
    release = threading.Event()
    entered = asyncio.Event()

    async def slow_discovery(config):
        entered.set()
        await asyncio.to_thread(release.wait, 5)

    monkeypatch.setattr(gateway_run, "_discover_gateway_mcp_tools", slow_discovery)

    async def scenario():
        runner = _FakeRunner()
        verdict = await run_bootstrap._start_gateway_run_runner(runner, [])

        assert verdict is None
        assert runner.started is True           # platforms are up while discovery is still blocked
        assert release.is_set() is False
        pending = list(runner._background_tasks)
        assert [task.get_name() for task in pending] == ["gateway-mcp-discovery"]

        await asyncio.wait_for(entered.wait(), 5)
        release.set()
        await asyncio.gather(*pending, return_exceptions=True)
        assert runner._background_tasks == set()  # drained from the registry once it finished

    asyncio.run(scenario())


def test_a_failing_discovery_still_starts_the_platforms(monkeypatch):
    async def unreachable(config):
        raise RuntimeError("MCP server unreachable")

    monkeypatch.setattr(gateway_run, "_discover_gateway_mcp_tools", unreachable)

    async def scenario():
        runner = _FakeRunner()
        verdict = await run_bootstrap._start_gateway_run_runner(runner, [])

        assert verdict is None
        assert runner.started is True
        await asyncio.gather(*list(runner._background_tasks), return_exceptions=True)

    asyncio.run(scenario())


def test_an_existing_background_task_set_is_reused(monkeypatch):
    """A runner that already tracks background work keeps its own set (no shadow registry)."""
    async def discovery(config):
        return None

    monkeypatch.setattr(gateway_run, "_discover_gateway_mcp_tools", discovery)

    async def scenario():
        runner = _FakeRunner()
        runner._background_tasks = set()
        existing = runner._background_tasks

        await run_bootstrap._start_gateway_run_runner(runner, [])
        await asyncio.gather(*list(existing), return_exceptions=True)

        assert runner._background_tasks is existing

    asyncio.run(scenario())
