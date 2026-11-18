"""Bounded authentication and account health, independent of the HTTP loop."""
import asyncio
import contextvars
import functools
import time


class RiskBlocked(asyncio.CancelledError):
    """Do not let legacy recovery handlers swallow a hard platform block."""


auth_deadline = contextvars.ContextVar('auth_deadline', default=None)


def bounded_auth(function):
    @functools.wraps(function)
    async def wrapped(self, *args, **kwargs):
        for name in ('_auth_sliders', '_auth_browsers', '_auth_playwrights'):
            if not hasattr(self, name):
                setattr(self, name, set())
        if getattr(self, 'runtime_state', '') == 'risk_blocked':
            raise RiskBlocked('账号需人工验证')
        parent = auth_deadline.get()
        deadline = parent if parent is not None else time.monotonic() + 90.0
        token = auth_deadline.set(deadline)
        try:
            return await asyncio.wait_for(function(self, *args, **kwargs),
                                          timeout=max(0.001, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            self.last_token_refresh_status = 'timeout'
            self.last_token_refresh_error_message = '认证恢复超过90秒，退出并退避重连'
            await self._cleanup_auth_resources()
            self._set_runtime_state('reconnecting')
            ws = getattr(self, 'ws', None)
            if ws:
                try:
                    await asyncio.wait_for(ws.close(), timeout=3.0)
                except Exception:
                    pass
            raise
        except RiskBlocked:
            await self._cleanup_auth_resources()
            ws = getattr(self, 'ws', None)
            if ws:
                try:
                    await asyncio.wait_for(ws.close(), timeout=3.0)
                except Exception:
                    pass
            raise
        except asyncio.CancelledError:
            await self._cleanup_auth_resources()
            raise
        finally:
            auth_deadline.reset(token)
    return wrapped


def account_health(enabled, live, task, now=None):
    now = time.time() if now is None else now
    state = getattr(live, 'runtime_state', 'offline') if live else 'offline'
    alive = bool(task and not task.done())
    ws = getattr(live, 'ws', None)
    ws_open = bool(ws and not getattr(ws, 'closed', True))
    heartbeat = getattr(live, 'last_heartbeat_response', 0) or 0
    connected_at = getattr(live, 'last_successful_connection', 0) or 0
    age = now - (heartbeat or connected_at) if heartbeat or connected_at else None
    healthy = not enabled or (alive and state == 'connected' and ws_open
                              and age is not None and age <= 90.0)
    return dict(enabled=enabled, state=state, task_alive=alive, websocket_open=ws_open,
                last_heartbeat=heartbeat or None, heartbeat_age_seconds=age,
                last_msg_time=getattr(live, 'last_message_received_time', 0) or None,
                state_since=getattr(live, 'runtime_state_since', now),
                healthy=healthy, needs_manual_verification=state == 'risk_blocked')
