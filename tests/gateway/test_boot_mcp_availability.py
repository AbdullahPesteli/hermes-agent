"""Unavailable MCP servers must not hold the local gateway's startup gate closed."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway import run as gateway_run
from gateway.run_bootstrap import _start_gateway_run_runner


@pytest.mark.asyncio
async def test_platform_start_does_not_wait_for_mcp_discovery(monkeypatch):
    discovery_started = asyncio.Event()
    release_discovery = asyncio.Event()
    platform_started = asyncio.Event()

    async def discover(_config):
        discovery_started.set()
        await release_discovery.wait()

    async def start():
        platform_started.set()
        return True

    runner = SimpleNamespace(
        config=object(), start=start, should_exit_cleanly=False,
        _running=True, _background_tasks=set(),
    )
    monkeypatch.setattr(gateway_run, "_discover_gateway_mcp_tools", discover)
    boot = asyncio.create_task(_start_gateway_run_runner(runner, [False]))
    try:
        await asyncio.wait_for(discovery_started.wait(), timeout=5)
        await asyncio.wait_for(platform_started.wait(), timeout=5)
        assert await asyncio.wait_for(asyncio.shield(boot), timeout=5) is None
        assert runner._background_tasks, "discovery must belong to the runner's shutdown lifecycle"
        assert not release_discovery.is_set()
    finally:
        release_discovery.set()
        await asyncio.gather(boot, *runner._background_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_mcp_failure_is_reported_without_failing_platform_start(monkeypatch, caplog):
    discovery_finished = asyncio.Event()

    async def discover(_config):
        discovery_finished.set()
        raise ConnectionError("test MCP unavailable")

    runner = SimpleNamespace(
        config=object(), start=AsyncMock(return_value=True), should_exit_cleanly=False,
        _running=True, _background_tasks=set(),
    )
    monkeypatch.setattr(gateway_run, "_discover_gateway_mcp_tools", discover)
    assert await _start_gateway_run_runner(runner, [False]) is None
    await asyncio.wait_for(discovery_finished.wait(), timeout=5)
    await asyncio.gather(*runner._background_tasks)
    runner.start.assert_awaited_once()
    assert "MCP tool discovery failed" in caplog.text
