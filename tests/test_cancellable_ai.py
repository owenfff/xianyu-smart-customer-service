import asyncio
import unittest

from aiohttp import web

from utils.cancellable_ai import post, run_generation


class CancellableAITransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_generation_aborts_http_and_discards_old_result(self):
        started = asyncio.Event()
        stopped = asyncio.Event()
        saved = []

        async def handler(request):
            started.set()
            try:
                await asyncio.sleep(30)
                return web.json_response({'content': 'obsolete'})
            finally:
                stopped.set()

        app = web.Application()
        app.router.add_post('/reply', handler)
        runner = web.AppRunner(app, handler_cancellation=True)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        def generate():
            response = post(f'http://127.0.0.1:{port}/reply', json={}, timeout=30)
            saved.append(response.json())
            return saved[-1]

        try:
            task = asyncio.create_task(run_generation(generate))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            await asyncio.wait_for(stopped.wait(), 2)
            self.assertEqual(saved, [])
        finally:
            await runner.cleanup()

    async def test_success_response_keeps_legacy_response_interface(self):
        app = web.Application()
        app.router.add_post('/reply', lambda request: web.json_response({'content': 'hello'}))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            response = await run_generation(post, f'http://127.0.0.1:{port}/reply', json={}, timeout=1)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'content': 'hello'})
            response.raise_for_status()
        finally:
            await runner.cleanup()
