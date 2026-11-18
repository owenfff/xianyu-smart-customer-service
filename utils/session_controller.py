"""One coordinator per account/conversation; cancellation never authorizes a send."""
import asyncio
import contextvars
import time
from dataclasses import dataclass, field


class StaleReply(asyncio.CancelledError):
    """A superseded reply must bypass legacy ``except Exception`` handlers."""


reply_context = contextvars.ContextVar('reply_context', default=None)


@dataclass
class SessionController:
    account_id: str
    session_id: str
    buffer: list = field(default_factory=list)
    version: int = 0
    first_msg_time: float = 0.0
    last_msg_time: float = 0.0
    ai_task: object = None
    worker_task: object = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    sending: bool = False
    sent_version: int = -1

    def append(self, message):
        now = time.monotonic()
        message['arrived_at'] = now
        if not self.buffer:
            self.first_msg_time = now
        self.last_msg_time = now
        self.buffer.append(message)
        self.version += 1
        self.wake.set()
        # Sending is a commit point. A message arriving during ws.send belongs
        # to the next batch and cannot cancel a possibly delivered frame.
        if self.ai_task and not self.ai_task.done() and not self.sending:
            self.ai_task.cancel()


async def wait_for_batch(controller, debounce_sec=3.0, max_wait_sec=7.0):
    while controller.buffer:
        now = time.monotonic()
        delay = min(controller.last_msg_time + debounce_sec,
                    controller.first_msg_time + max_wait_sec) - now
        if delay <= 0:
            return
        controller.wake.clear()
        try:
            await asyncio.wait_for(controller.wake.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return
