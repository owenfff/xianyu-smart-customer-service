"""Cancellable HTTP bridge for the legacy synchronous reply engine.

Keep its public synchronous API, while async callers can abort the actual HTTP
request and prevent obsolete generations from being saved as assistant history.
"""
import asyncio
import concurrent.futures
import contextvars
import threading

import aiohttp
import requests


class GenerationCancelled(BaseException):
    pass


generation_context = contextvars.ContextVar('generation_context', default=None)


class Generation:
    def __init__(self, loop):
        self.loop = loop
        self.cancelled = threading.Event()
        self.future = None

    def check(self):
        if self.cancelled.is_set():
            raise GenerationCancelled()

    def cancel(self):
        self.cancelled.set()
        if self.future is not None:
            self.future.cancel()


def check_generation():
    generation = generation_context.get()
    if generation is not None:
        generation.check()


def post(*args, **kwargs):
    generation = generation_context.get()
    if generation is None:
        return requests.post(*args, **kwargs)
    generation.check()

    async def request():
        options = dict(kwargs)
        timeout = options.pop('timeout', 30)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(*args, **options) as response:
                result = requests.Response()
                result.status_code = response.status
                result._content = await response.read()
                result.headers.update(response.headers)
                result.url = str(response.url)
                result.encoding = response.charset or 'utf-8'
                return result

    future = asyncio.run_coroutine_threadsafe(request(), generation.loop)
    generation.future = future
    if generation.cancelled.is_set():
        future.cancel()
    try:
        result = future.result()
        generation.check()
        return result
    except concurrent.futures.CancelledError:
        raise GenerationCancelled() from None
    finally:
        generation.future = None


async def run_generation(function, *args, **kwargs):
    generation = Generation(asyncio.get_running_loop())
    token = generation_context.set(generation)
    try:
        worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    finally:
        generation_context.reset(token)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        generation.cancel()
        # Let the synchronous frame release its chat lock before the next
        # generation can start. The HTTP future above is actually cancelled.
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=5.0)
        except (GenerationCancelled, asyncio.CancelledError, asyncio.TimeoutError):
            pass
        raise
    except GenerationCancelled:
        raise asyncio.CancelledError() from None
