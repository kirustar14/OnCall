"""Tracks the live frontend WebSocket connection for each open case so
backend services (STT, extraction, intervention) can push JSON messages to it."""

import asyncio
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

logger = logging.getLogger("servare.ws_manager")


class WSManager:
    def __init__(self) -> None:
        self._sockets: dict[str, WebSocket] = {}

    def register(self, case_id: str, ws: WebSocket) -> None:
        self._sockets[case_id] = ws

    def unregister(self, case_id: str) -> None:
        self._sockets.pop(case_id, None)

    async def send_json(self, case_id: str, payload: dict) -> None:
        ws = self._sockets.get(case_id)
        if ws is None:
            return

        # Extraction runs behind the audio, so a case that just closed will
        # still have writes landing after the browser is gone. That is expected,
        # not an error — logging a traceback for each one buries real failures.
        if ws.client_state is not WebSocketState.CONNECTED:
            self.unregister(case_id)
            return

        try:
            await ws.send_json(payload)
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("case %s disconnected before push", case_id)
            self.unregister(case_id)
        except Exception:
            logger.exception("failed to send to case %s", case_id)

    def send_json_threadsafe(self, loop: asyncio.AbstractEventLoop, case_id: str, payload: dict) -> None:
        """Call from a non-async callback (e.g. Deepgram's event thread)."""
        asyncio.run_coroutine_threadsafe(self.send_json(case_id, payload), loop)


ws_manager = WSManager()
