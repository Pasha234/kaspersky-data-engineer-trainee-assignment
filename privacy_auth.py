import hmac
import logging
import time
import asyncio
from pathlib import Path

AUDIT_ENDPOINTS = {
    "/v1/chat/completions": "chat_completions",
    "/v1/completions": "completions",
    "/v1/embeddings": "embeddings",
    "/v1/models": "models",
}

logger = logging.getLogger("vllm.audit")

class FileTokenAuthMiddleware:
    def __init__(self, app, secret_path: str = "/run/secrets/vllm_api_key"):
        self.app = app
        # Strip only trailing CR/LF newlines that editors or `echo` might add
        self.api_key = Path(secret_path).read_bytes().rstrip(b"\r\n")
        if not self.api_key:
            raise RuntimeError("API key file is empty")

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        endpoint = AUDIT_ENDPOINTS.get(path, "unknown")
        method = scope.get("method", "")

        # Allow Kubernetes startup/readiness/liveness probes without auth
        if scope["type"] == "http" and path == "/health" and method == "GET":
            return await self.app(scope, receive, send)

        authorization_values = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"authorization"
        ]

        authorized = False

        # Принимаем ровно один заголовок Authorization.
        if len(authorization_values) == 1:
            scheme, separator, token = authorization_values[0].partition(b" ")

            # Допускаем несколько пробелов между схемой и токеном.
            token = token.lstrip(b" ")

            authorized = (
                bool(separator)
                and scheme.lower() == b"bearer"
                and bool(token)
                and hmac.compare_digest(token, self.api_key)
            )

        if not authorized:
            logger.warning(
                "event=auth_rejected endpoint=%s",
                endpoint,
            )
            if scope["type"] == "websocket":
                await send({
                    "type": "websocket.close",
                    "code": 1008,
                })
            else:
                body = b'{"error":{"message":"Unauthorized","type":"auth_error"}}'
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"www-authenticate", b"Bearer"),
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": body,
                })
            return

        # Этот аудит описывает HTTP-запросы.
        # Авторизованные WebSocket-соединения передаём приложению отдельно.
        if scope["type"] == "websocket":
            return await self.app(scope, receive, send)

        start_time = time.monotonic()
        status_code = None
        response_complete = False
        outcome = "error"

        async def send_wrapper(message):
            nonlocal status_code, response_complete

            await send(message)

            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                if not message.get("more_body", False):
                    response_complete = True

        try:
            await self.app(scope, receive, send_wrapper)
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        else:
            if not response_complete or status_code is None:
                outcome = "incomplete"
            elif status_code >= 400:
                outcome = "http_error"
            else:
                outcome = "success"
        finally:
            elapsed_ms = (time.monotonic() - start_time) * 1000.0

            logger.info(
                "event=request_finished endpoint=%s "
                "status=%s outcome=%s duration_ms=%.2f",
                endpoint,
                status_code if status_code is not None else "unknown",
                outcome,
                elapsed_ms,
            )