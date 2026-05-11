import asyncio

# Sentinel yielded by _keepalive_iter when the upstream generator stalls.
# Callers must check `data is _KEEPALIVE` — do not compare by value.
_KEEPALIVE = object()


async def _keepalive_iter(gen, interval: float):
    """
    Wraps an async generator, yielding _KEEPALIVE whenever `interval` seconds
    pass without a chunk. Callers translate _KEEPALIVE into an SSE comment
    (': keepalive\\n\\n') to keep proxies from dropping idle connections.

    Uses asyncio.wait instead of asyncio.wait_for so the underlying task is
    not cancelled on timeout — the generator resumes normally after each ping.
    """
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
