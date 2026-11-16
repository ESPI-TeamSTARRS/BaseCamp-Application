from collections.abc import Mapping
from typing import Any

from app.core.events import service_event_names as names
from app.core.events.business_event import BusinessEvent
from app.core.events.business_event_logger import BusinessEventLogger
from app.core.http.concurrency_limit_middleware import LIVENESS_PATHS
from app.core.http.overridable_send import Message, OverridableSend

TOO_LARGE = (413, "Payload too large")


class BodySizeLimitMiddleware:
    """Refuse (413) tout corps au-delà de `max_bytes`, annoncé ou diffusé par morceaux."""

    def __init__(
        self,
        app: Any,
        max_bytes: int,
        events: BusinessEventLogger,
        path_limits: Mapping[str, int] | None = None,
    ) -> None:
        self._app = app
        self._max_bytes = max_bytes
        self._events = events
        self._path_limits = dict(path_limits or {})

    async def __call__(self, scope: Message, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] in LIVENESS_PATHS:
            await self._app(scope, receive, send)
            return
        gate = OverridableSend(send)
        limit = self._path_limits.get(scope["path"], self._max_bytes)
        if self._declared_length(scope) > limit:
            self._refuse(gate, scope, limit)
            await gate.flush()
            return
        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message: Message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    self._refuse(gate, scope, limit)
                    return {"type": "http.disconnect"}
            return message

        await self._app(scope, bounded_receive, gate)
        await gate.flush()

    def _refuse(self, gate: OverridableSend, scope: Message, limit: int) -> None:
        if not gate.overridden:
            self._events.log(
                BusinessEvent.denied(
                    names.PAYLOAD_TOO_LARGE,
                    "payload_too_large",
                    attributes={"limit_bytes": limit, "method": scope["method"]},
                )
            )
        gate.override(*TOO_LARGE)

    @staticmethod
    def _declared_length(scope: Message) -> int:
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return 0
        return 0
