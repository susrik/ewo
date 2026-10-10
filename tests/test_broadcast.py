"""BroadcastHub: in-process pub/sub carrying the live tail of job logs."""

from __future__ import annotations

import asyncio

from ewo.jobs.broadcast import BroadcastHub, close, publish, set_hub


async def _collect_until_done(
    hub: BroadcastHub, run_id: int, after_id: int = 0, wait: float = 0.05
) -> list[dict]:
    """Subscribe, let the stream drain, then close and gather what arrived."""

    received: list[dict] = []

    async def consume() -> None:
        async for event in hub.subscribe(run_id, after_id=after_id):
            received.append(event)
            if event.get("_done"):
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(wait)
    hub.close(run_id)
    await asyncio.wait_for(task, timeout=1)
    return received


def test_subscribe_drains_buffer_then_live() -> None:
    async def scenario() -> None:
        hub = BroadcastHub()
        hub.attach(asyncio.get_running_loop())
        hub.publish(1, {"id": 1, "n": "buffered"})  # lands before subscribe

        received: list[dict] = []

        async def consume() -> None:
            async for event in hub.subscribe(1, after_id=0):
                received.append(event)
                if event.get("_done"):
                    return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # let it drain the buffer and start waiting
        hub.publish(1, {"id": 2, "n": "live"})
        await asyncio.sleep(0.05)
        hub.close(1)
        await asyncio.wait_for(task, timeout=1)

        assert [e["id"] for e in received if "id" in e] == [1, 2]
        assert any("_done" in e for e in received)

    asyncio.run(scenario())


def test_subscribe_after_id_skips_older_buffered() -> None:
    async def scenario() -> None:
        hub = BroadcastHub()
        hub.attach(asyncio.get_running_loop())
        for n in (1, 2, 3):
            hub.publish(1, {"id": n})

        received = await _collect_until_done(hub, 1, after_id=1)
        assert [e["id"] for e in received if "id" in e] == [2, 3]

    asyncio.run(scenario())


def test_publish_before_attach_does_not_crash() -> None:
    """Buffered events are retained even before the loop is attached."""
    hub = BroadcastHub()
    hub.publish(1, {"id": 1})
    hub.close(1)  # no subscribers yet — no-op


def test_module_level_publish_forwards_to_hub() -> None:
    async def scenario() -> None:
        hub = BroadcastHub()
        hub.attach(asyncio.get_running_loop())
        set_hub(hub)

        received: list[dict] = []

        async def consume() -> None:
            async for event in hub.subscribe(1, after_id=0):
                received.append(event)
                if event.get("_done"):
                    return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        publish(1, {"id": 7})
        await asyncio.sleep(0.05)
        close(1)
        await asyncio.wait_for(task, timeout=1)

        assert [e["id"] for e in received if "id" in e] == [7]

    asyncio.run(scenario())
    set_hub(None)


def test_module_level_publish_noop_without_hub() -> None:
    set_hub(None)
    publish(1, {"id": 1})  # no hub → silently ignored
    close(1)
