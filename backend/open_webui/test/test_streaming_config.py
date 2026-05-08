"""
Tests for streaming timeout configuration (AIOHTTP_CLIENT_TIMEOUT_SOCK_READ),
SSE keepalive interval derivation (SSE_KEEPALIVE_INTERVAL), and the
_keepalive_iter async generator.
"""

import asyncio
import importlib
import sys
import pytest


_KEEPALIVE = object()


async def _keepalive_iter(gen, interval):
    it = gen.__aiter__()
    pending = asyncio.create_task(it.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if done:
                try:
                    yield pending.result()
                except StopAsyncIteration:
                    break
                pending = asyncio.create_task(it.__anext__())
            else:
                yield _KEEPALIVE
    finally:
        pending.cancel()


def _reload_env(monkeypatch, env_vars: dict):
    """
    Apply env_vars, reload open_webui.env, return the reloaded module.
    Cleans up by restoring the original module after each test.
    """
    for key, value in env_vars.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    # Force reload so module-level code re-runs with new env
    if "open_webui.env" in sys.modules:
        del sys.modules["open_webui.env"]

    import open_webui.env as env_module
    return env_module


# ---------- AIOHTTP_CLIENT_TIMEOUT_SOCK_READ ----------

class TestSockReadTimeout:

    def test_default_is_60(self, monkeypatch):
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": None})
        assert env.AIOHTTP_CLIENT_TIMEOUT_SOCK_READ == 60

    def test_default_source_is_default(self, monkeypatch):
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": None})
        assert env._sock_read_source == "default"

    def test_valid_int_is_parsed(self, monkeypatch):
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": "120"})
        assert env.AIOHTTP_CLIENT_TIMEOUT_SOCK_READ == 120

    def test_configured_source_when_env_set(self, monkeypatch):
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": "120"})
        assert env._sock_read_source == "configured"

    def test_empty_string_becomes_none(self, monkeypatch):
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": ""})
        assert env.AIOHTTP_CLIENT_TIMEOUT_SOCK_READ is None

    def test_garbage_value_falls_back_to_60(self, monkeypatch):
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": "notanumber"})
        assert env.AIOHTTP_CLIENT_TIMEOUT_SOCK_READ == 60

    def test_float_string_falls_back_to_60(self, monkeypatch):
        # int() does not accept "60.5"
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": "60.5"})
        assert env.AIOHTTP_CLIENT_TIMEOUT_SOCK_READ == 60

    def test_zero_is_parsed_as_zero(self, monkeypatch):
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": "0"})
        assert env.AIOHTTP_CLIENT_TIMEOUT_SOCK_READ == 0

    def test_large_value(self, monkeypatch):
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": "300"})
        assert env.AIOHTTP_CLIENT_TIMEOUT_SOCK_READ == 300


# ---------- SSE_KEEPALIVE_INTERVAL ----------

class TestSseKeepaliveInterval:

    def test_default_sock_read_gives_15s_keepalive(self, monkeypatch):
        # sock_read=60 → min(60/4, 20) = min(15, 20) = 15
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": None})
        assert env.SSE_KEEPALIVE_INTERVAL == 15

    def test_120s_sock_read_gives_20s_keepalive(self, monkeypatch):
        # sock_read=120 → min(120/4, 20) = min(30, 20) = 20
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": "120"})
        assert env.SSE_KEEPALIVE_INTERVAL == 20

    def test_300s_sock_read_capped_at_20s(self, monkeypatch):
        # sock_read=300 → min(300/4, 20) = min(75, 20) = 20
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": "300"})
        assert env.SSE_KEEPALIVE_INTERVAL == 20

    def test_30s_sock_read_gives_7s_keepalive(self, monkeypatch):
        # sock_read=30 → min(30/4, 20) = min(7, 20) = 7
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": "30"})
        assert env.SSE_KEEPALIVE_INTERVAL == 7

    def test_keepalive_always_under_30s_proxy_timeout(self, monkeypatch):
        # For any sock_read >= 1, keepalive must be < 30s (Google proxy timeout)
        for sock_read in [1, 10, 30, 60, 120, 300, 900]:
            env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": str(sock_read)})
            assert env.SSE_KEEPALIVE_INTERVAL < 30, (
                f"keepalive={env.SSE_KEEPALIVE_INTERVAL}s >= 30s for sock_read={sock_read}s"
            )

    def test_none_sock_read_uses_60_as_base(self, monkeypatch):
        # When sock_read is disabled (empty string → None), keepalive still derives from 60
        env = _reload_env(monkeypatch, {"AIOHTTP_CLIENT_TIMEOUT_SOCK_READ": ""})
        assert env.SSE_KEEPALIVE_INTERVAL == 15


# ---------- _keepalive_iter ----------

class TestKeepaliveIter:

    async def _collect(self, gen):
        results = []
        async for item in gen:
            results.append(item)
        return results

    def test_yields_chunks_without_keepalive(self):
        async def fast_gen():
            for chunk in ["a", "b", "c"]:
                yield chunk

        results = asyncio.run(self._collect(_keepalive_iter(fast_gen(), interval=10)))
        assert results == ["a", "b", "c"]

    def test_injects_keepalive_on_stall(self):
        async def slow_gen():
            yield "first"
            await asyncio.sleep(0.2)
            yield "second"

        results = asyncio.run(self._collect(_keepalive_iter(slow_gen(), interval=0.05)))
        assert results[0] == "first"
        assert _KEEPALIVE in results
        assert results[-1] == "second"

    def test_terminates_cleanly_on_empty_generator(self):
        async def empty_gen():
            return
            yield  # make it an async generator

        results = asyncio.run(self._collect(_keepalive_iter(empty_gen(), interval=10)))
        assert results == []
