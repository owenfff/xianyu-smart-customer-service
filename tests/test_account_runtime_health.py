import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from utils.account_runtime import account_health, bounded_auth, RiskBlocked, auth_deadline
from cookie_manager import CookieManager
from XianyuAutoAsync import XianyuLive


class AccountRuntimeHealthTest(unittest.IsolatedAsyncioTestCase):
    def live(self, state='connected', heartbeat_age=1):
        return SimpleNamespace(runtime_state=state, runtime_state_since=time.time(),
                               ws=SimpleNamespace(closed=False),
                               last_heartbeat_response=time.time() - heartbeat_age,
                               last_successful_connection=time.time() - 100,
                               last_message_received_time=time.time() - 5)

    def test_enabled_is_not_online(self):
        result = account_health(True, self.live(), MagicMock(done=lambda: True))
        self.assertFalse(result['healthy'])
        result = account_health(True, self.live(heartbeat_age=91), MagicMock(done=lambda: False))
        self.assertFalse(result['healthy'])
        result = account_health(False, None, None)
        self.assertTrue(result['healthy'])

    def test_connected_healthy_and_risk_blocked_visible(self):
        task = MagicMock(done=lambda: False)
        self.assertTrue(account_health(True, self.live(), task)['healthy'])
        result = account_health(True, self.live('risk_blocked'), task)
        self.assertFalse(result['healthy'])
        self.assertTrue(result['needs_manual_verification'])

    async def test_auth_timeout_cleans_resources_and_closes_socket(self):
        live = self.live('initializing')
        live.ws.close = AsyncMock()
        live._cleanup_auth_resources = AsyncMock()
        live._set_runtime_state = MagicMock()
        @bounded_auth
        async def blocked(self):
            await asyncio.sleep(100)
        token = auth_deadline.set(time.monotonic() + .02)
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await blocked(live)
        finally:
            auth_deadline.reset(token)
        live._cleanup_auth_resources.assert_awaited_once()
        live.ws.close.assert_awaited_once()

    async def test_risk_blocked_never_enters_authentication(self):
        live = self.live('risk_blocked')
        callback = AsyncMock()
        wrapped = bounded_auth(callback)
        with self.assertRaises(RiskBlocked):
            await wrapped(live)
        callback.assert_not_awaited()

    async def test_slider_hard_block_cancels_without_running_fallback(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = 'test'
        live._mark_risk_blocked = MagicMock()
        live.last_token_refresh_error_message = 'hard block'
        live._cleanup_auth_resources = AsyncMock()
        slider = SimpleNamespace(last_verification_feedback={'status': 'hard_block'})
        with patch('utils.slider_orchestrator.run_slider_async_with_fallback', new_callable=AsyncMock) as run:
            with self.assertRaises(RiskBlocked):
                await live._run_account_slider(slider, 'https://test.invalid')
        self.assertFalse(run.call_args.kwargs['fallback_enabled'])
        live._mark_risk_blocked.assert_called_once()
        live._cleanup_auth_resources.assert_awaited_once()

    async def test_watchdog_restarts_only_failed_enabled_account(self):
        manager = CookieManager.__new__(CookieManager)
        manager.loop = asyncio.get_running_loop()
        manager.cookies = {'failed': 'cookie', 'blocked': 'cookie', 'disabled': 'cookie'}
        manager.cookie_status = {'failed': True, 'blocked': True, 'disabled': False}
        manager.tasks = {}
        manager.live_instances = {'blocked': self.live('risk_blocked')}
        manager._risk_blocked_accounts = set()
        manager._task_locks = {}
        manager._last_watchdog_restart = {}
        manager._run_xianyu = AsyncMock()
        with patch('cookie_manager.db_manager.get_cookie_details', return_value={'user_id': 1}):
            await manager._watchdog_once()
        await asyncio.gather(*manager.tasks.values())
        manager._run_xianyu.assert_awaited_once_with('failed', 'cookie', 1)
        self.assertEqual(set(manager.tasks), {'failed'})

    def test_backoff_is_bounded_exponential(self):
        live = XianyuLive.__new__(XianyuLive)
        live.init_auth_failures = 0
        values = []
        with patch('XianyuAutoAsync.random.randint', return_value=5):
            for i in range(1, 9):
                live.connection_failures = i
                values.append(live._calculate_retry_delay('timeout'))
        self.assertEqual(values, [20, 35, 65, 125, 245, 305, 305, 305])

    def test_retry_jitter_bounds_and_auth_failure_counter(self):
        live = XianyuLive.__new__(XianyuLive)
        live.connection_failures = 0
        live.init_auth_failures = 3
        with patch('XianyuAutoAsync.random.randint', return_value=15) as jitter:
            self.assertEqual(live._calculate_retry_delay('token failed'), 75)
            jitter.assert_called_once_with(5, 15)
        live.init_auth_failures = 10000
        for _ in range(50):
            self.assertTrue(305 <= live._calculate_retry_delay('timeout') <= 315)

    async def test_handshake_and_token_share_one_total_deadline(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = 'test'
        live.ws = None
        live.runtime_state = 'initializing'
        live._cleanup_auth_resources = AsyncMock()
        context = MagicMock()
        socket = MagicMock(closed=False)
        socket.close = AsyncMock()
        context.__aenter__ = AsyncMock(return_value=socket)
        context.__aexit__ = AsyncMock()
        live._create_websocket_connection = AsyncMock(return_value=context)
        live.init = AsyncMock(side_effect=lambda ws: None)
        async def stalled_init(ws):
            await asyncio.sleep(100)
        live.init.side_effect = stalled_init
        token = auth_deadline.set(time.monotonic() + .02)
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await live._connect_and_initialize({})
        finally:
            auth_deadline.reset(token)
        context.__aexit__.assert_awaited_once()
        self.assertIsNone(live.ws)
        self.assertEqual(live.runtime_state, 'reconnecting')

    async def test_manual_resume_clears_risk_after_old_task_cleanup(self):
        manager = CookieManager.__new__(CookieManager)
        manager.loop = asyncio.get_running_loop()
        manager.cookies = {'test': 'cookie'}
        manager.cookie_status = {'test': True}
        manager.keywords = {}
        manager.live_instances = {}
        manager._risk_blocked_accounts = {'test'}
        manager._manual_risk_resets = {'test'}
        manager._task_locks = {}
        started = asyncio.Event()
        async def old():
            try:
                started.set()
                await asyncio.sleep(100)
            finally:
                manager._risk_blocked_accounts.add('test')
        old_task = asyncio.create_task(old())
        manager.tasks = {'test': old_task}
        await started.wait()
        manager._run_xianyu = AsyncMock()
        with patch('cookie_manager.db_manager.get_cookie_details', return_value={'user_id': 1}), patch(
                'cookie_manager.db_manager.update_cookie_status_note'):
            await manager.update_cookie('test', 'cookie', save_to_db=False)
        await asyncio.gather(*manager.tasks.values())
        self.assertNotIn('test', manager._risk_blocked_accounts)
        manager._run_xianyu.assert_awaited_once()
