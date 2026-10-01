import asyncio
import threading

from . import asgi_backpressure, asgi_stream


def test_asgi_backpressure_is_installed():
    assert getattr(asgi_stream._enqueue, "_ai_workspace_backpressure", False) is True


def test_slow_client_backpressures_provider_thread_until_queue_drains():
    async def scenario():
        queue = asyncio.Queue()
        detached = threading.Event()
        for index in range(asgi_backpressure.MAX_PENDING_EVENTS):
            queue.put_nowait(("chunk", str(index)))

        task = asyncio.create_task(
            asyncio.to_thread(
                asgi_stream._enqueue,
                asyncio.get_running_loop(),
                queue,
                ("chunk", "next"),
                detached,
            )
        )
        await asyncio.sleep(0.12)
        assert task.done() is False
        assert queue.qsize() == asgi_backpressure.MAX_PENDING_EVENTS

        await queue.get()
        accepted = await asyncio.wait_for(task, timeout=1.5)
        assert accepted is True
        assert queue.qsize() == asgi_backpressure.MAX_PENDING_EVENTS

    asyncio.run(scenario())


def test_disconnect_releases_backpressured_provider_thread():
    async def scenario():
        queue = asyncio.Queue()
        detached = threading.Event()
        for index in range(asgi_backpressure.MAX_PENDING_EVENTS):
            queue.put_nowait(("chunk", str(index)))

        task = asyncio.create_task(
            asyncio.to_thread(
                asgi_stream._enqueue,
                asyncio.get_running_loop(),
                queue,
                ("chunk", "never-enqueued"),
                detached,
            )
        )
        await asyncio.sleep(0.12)
        detached.set()
        accepted = await asyncio.wait_for(task, timeout=1.5)
        assert accepted is False
        assert queue.qsize() == asgi_backpressure.MAX_PENDING_EVENTS

    asyncio.run(scenario())
