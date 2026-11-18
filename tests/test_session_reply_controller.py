import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from XianyuAutoAsync import XianyuLive
from utils.session_controller import wait_for_batch, reply_context, SessionController, StaleReply


class SessionReplyControllerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.live = XianyuLive.__new__(XianyuLive)
        self.live.cookie_id = 'test-account'
        self.live.myid = 'seller'
        self.live.user_id = 1
        self.live.ws = MagicMock(closed=False)
        self.live.ws.send = AsyncMock()
        self.live.runtime_state = 'connected'
        self.live.session_controllers = {}
        self.live.message_debounce_tasks = {}
        self.live.message_debounce_lock = asyncio.Lock()
        self.live._create_tracked_task = asyncio.create_task
        self.live._reserve_message_reply = AsyncMock(return_value=True)
        self.live._finalize_message_reply = AsyncMock()
        self.live._release_message_reply = AsyncMock()
        self.live._publish_reply_state = MagicMock()
        self.taken_over = False
        self.patch_takeover = patch('XianyuAutoAsync.db_manager.is_chat_taken_over',
                                    side_effect=lambda *a: self.taken_over)
        self.patch_takeover.start()
        self.patch_wait = patch('XianyuAutoAsync.wait_for_batch',
                                side_effect=lambda c: wait_for_batch(c, .03, .07))
        self.patch_wait.start()

    async def asyncTearDown(self):
        tasks = [c.worker_task for c in self.live.session_controllers.values() if c.worker_task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.patch_wait.stop()
        self.patch_takeover.stop()

    async def message(self, value, chat='chat'):
        await self.live._schedule_debounced_reply(
            chat, {}, self.live.ws, 'buyer', 'buyer', value, 'item', 'now', dedupe_message_id=value)

    async def wait_idle(self):
        async def finished():
            while self.live.session_controllers:
                await asyncio.sleep(.005)
        await asyncio.wait_for(finished(), 2)

    async def test_short_burst_merges_into_one_send(self):
        async def process(data, ws, name, buyer, text, item, chat, stamp):
            await self.live.send_msg(ws, chat, buyer, text)
        self.live._process_chat_message_reply = AsyncMock(side_effect=process)
        await self.message('date')
        await self.message('room')
        await self.wait_idle()
        self.live.ws.send.assert_awaited_once()
        self.assertEqual(self.live._process_chat_message_reply.await_args.args[4], 'date\nroom')
        self.assertEqual(self.live._finalize_message_reply.await_count, 2)
        self.assertFalse(self.live._publish_reply_state.call_args.args[1])

    async def test_max_wait_starts_generation_during_continuous_messages(self):
        started = asyncio.Event()
        async def process(*args):
            started.set()
        self.live._process_chat_message_reply = AsyncMock(side_effect=process)
        await self.message('0')
        async def producer():
            for i in range(1, 10):
                await asyncio.sleep(.02)
                await self.message(str(i))
        producer_task = asyncio.create_task(producer())
        await asyncio.wait_for(started.wait(), .15)
        self.assertFalse(producer_task.done())
        await producer_task
        await self.wait_idle()

    async def test_new_message_cancels_generation_without_parallel_worker(self):
        started = asyncio.Event()
        active = 0
        peak = 0
        cancelled = asyncio.Event()
        texts = []
        async def process(data, ws, name, buyer, text, item, chat, stamp):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                texts.append(text)
                if len(texts) == 1:
                    started.set()
                    try:
                        await asyncio.sleep(10)
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise
                await self.live.send_msg(ws, chat, buyer, text)
            finally:
                active -= 1
        self.live._process_chat_message_reply = AsyncMock(side_effect=process)
        await self.message('first')
        await started.wait()
        worker = self.live.session_controllers[('test-account', 'chat')].worker_task
        await self.message('second')
        self.assertIs(worker, self.live.session_controllers[('test-account', 'chat')].worker_task)
        await self.wait_idle()
        self.assertTrue(cancelled.is_set())
        self.assertEqual(peak, 1)
        self.assertEqual(texts, ['first', 'first\nsecond'])
        self.live.ws.send.assert_awaited_once()

    async def test_suppressed_cancel_cannot_send_stale_reply(self):
        started = asyncio.Event()
        async def process(data, ws, name, buyer, text, item, chat, stamp):
            if text == 'first':
                started.set()
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    pass
            await self.live.send_msg(ws, chat, buyer, text)
        self.live._process_chat_message_reply = AsyncMock(side_effect=process)
        await self.message('first')
        await started.wait()
        await self.message('second')
        await self.wait_idle()
        self.live.ws.send.assert_awaited_once()

    async def test_new_message_during_send_is_preserved_for_next_batch(self):
        sending = asyncio.Event()
        release = asyncio.Event()
        count = 0
        async def send(frame):
            nonlocal count
            count += 1
            if count == 1:
                sending.set()
                await release.wait()
        self.live.ws.send.side_effect = send
        async def process(data, ws, name, buyer, text, item, chat, stamp):
            await self.live.send_msg(ws, chat, buyer, text)
        self.live._process_chat_message_reply = AsyncMock(side_effect=process)
        await self.message('first')
        await sending.wait()
        await self.message('second')
        release.set()
        await self.wait_idle()
        self.assertEqual([c.args[4] for c in self.live._process_chat_message_reply.await_args_list],
                         ['first', 'second'])

    async def test_takeover_and_offline_block_actual_socket_send(self):
        controller = SessionController('test-account', 'chat')
        controller.version = 1
        token = reply_context.set((controller, 1))
        try:
            self.taken_over = True
            with self.assertRaises(StaleReply):
                await self.live.send_msg(self.live.ws, 'chat', 'buyer', 'old')
            self.taken_over = False
            self.live.runtime_state = 'offline'
            with self.assertRaises(ConnectionError):
                await self.live.send_msg(self.live.ws, 'chat', 'buyer', 'old')
        finally:
            reply_context.reset(token)
        self.live.ws.send.assert_not_awaited()

    async def test_worker_error_always_resets_ui_and_releases_pending_ids(self):
        self.live._process_chat_message_reply = AsyncMock(side_effect=RuntimeError('model failed'))
        await self.message('first')
        await self.wait_idle()
        self.assertFalse(self.live._publish_reply_state.call_args.args[1])
        self.live._release_message_reply.assert_awaited_once()

    async def test_separate_sessions_have_independent_workers(self):
        self.live._process_chat_message_reply = AsyncMock()
        await self.message('one', 'chat1')
        await self.message('two', 'chat2')
        self.assertEqual(len(self.live.session_controllers), 2)
        await self.wait_idle()
