"""Who may do what on the observer, and the guard every request passes (slice H).

Two roles hold a token:

- the **owner**, whose token the observer prints for its own window (or reads from
  `SOVEREIGN_WORLD_OBSERVER_TOKEN`), may also pause and resume a runner the observer started;
- a **viewer**, whose token exists only on a public observer (`observe --public`) and travels in
  the shared link, may only look.

The owner's token is refused, like a wrong token, on any request that came through a proxy (one
carrying `X-Forwarded-For`, `Cf-Connecting-Ip` or `Cf-Ray`), so whoever holds it can still act
only from the observer's own machine. Both tokens are always compared, in constant time.

The guard, an ASGI middleware in front of every route and file, refuses request bodies over a
few kilobytes (413) or without a declared length (411), limits how often each client and all
clients together may ask (429, with `Retry-After`; the owner on its own machine is exempt), and
puts the security headers on every answer: a strict Content-Security-Policy, no sniffing, no
referrer, no framing.
"""

from __future__ import annotations

import json
import math
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

MIN_TOKEN_CHARS = 32
"""The shortest viewer token accepted (a fresh one is 43 characters)."""
OUTSIDE_HEADERS = ("x-forwarded-for", "cf-connecting-ip", "cf-ray")
"""Headers that mark a request as having come through a proxy or tunnel."""
MAX_CLIENTS = 10_000
"""Clients whose request allowance is remembered; the least recent is forgotten first."""

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:;"
    " connect-src 'self'; worker-src blob:; font-src 'self'; base-uri 'none';"
    " form-action 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("content-security-policy", CSP),
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "no-referrer"),
    ("x-frame-options", "DENY"),
    (
        "permissions-policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
    ),
    ("cross-origin-opener-policy", "same-origin"),
    ("cross-origin-resource-policy", "same-origin"),
)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
App = Callable[[Scope, Receive, Send], Awaitable[None]]


class Role(StrEnum):
    OWNER = "owner"
    VIEWER = "viewer"


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    for key, value in headers:
        if key.lower() == name:
            return value
    return None


def from_outside(headers: list[tuple[bytes, bytes]]) -> bool:
    """Whether a request came through a proxy or tunnel."""
    names = {key.lower() for key, _ in headers}
    return any(name.encode() in names for name in OUTSIDE_HEADERS)


@dataclass(frozen=True, slots=True)
class Tokens:
    """The owner's token, and on a public observer the viewers' token."""

    owner: str
    viewer: str | None = None

    def __post_init__(self) -> None:
        if not self.owner:
            raise ValueError("the observer needs a token")
        if self.viewer is not None:
            if len(self.viewer) < MIN_TOKEN_CHARS:
                raise ValueError(
                    f"the viewer token must be at least {MIN_TOKEN_CHARS} characters long"
                )
            if secrets.compare_digest(self.viewer.encode(), self.owner.encode()):
                raise ValueError("the viewer token must differ from the observer's own token")

    def role_of(self, headers: list[tuple[bytes, bytes]]) -> Role | None:
        """The role a request's `Authorization: Bearer` token gives it, or None."""
        given = _header(headers, b"authorization") or b""
        owner = secrets.compare_digest(given, f"Bearer {self.owner}".encode())
        # Compared even when there is no viewer token, so the time taken says nothing.
        viewer_token = self.viewer if self.viewer is not None else secrets.token_urlsafe(32)
        viewer = secrets.compare_digest(given, f"Bearer {viewer_token}".encode())
        if owner and not from_outside(headers):
            return Role.OWNER
        if viewer and self.viewer is not None:
            return Role.VIEWER
        return None


def make_tokens(owner: str, viewer: str | None, *, public: bool) -> Tokens:
    """The observer's tokens: a viewer token only when it is public, from `viewer` (its
    environment) or fresh."""
    if not public:
        return Tokens(owner)
    return Tokens(owner, viewer or secrets.token_urlsafe(32))


@dataclass(frozen=True, slots=True)
class Limits:
    """How often clients may ask, and how large a request body may be."""

    client_burst: float = 600
    client_rate: float = 20
    """Requests a second each client regains, up to its burst."""
    global_burst: float = 3000
    global_rate: float = 150
    body_bytes: int = 4096
    clock: Callable[[], float] = field(default=time.monotonic, compare=False)


@dataclass(slots=True)
class _Bucket:
    tokens: float
    at: float

    def fill(self, now: float, burst: float, rate: float) -> None:
        self.tokens = min(burst, self.tokens + (now - self.at) * rate)
        self.at = now


class RateLimiter:
    """Token buckets per client and for all clients together."""

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self._lock = threading.Lock()
        now = limits.clock()
        self._all = _Bucket(limits.global_burst, now)
        self._clients: OrderedDict[str, _Bucket] = OrderedDict()

    def take(self, client: str) -> float:
        """Take one request from the client's and everyone's allowance: 0 when allowed, else
        the seconds until it would be."""
        limits = self.limits
        with self._lock:
            now = limits.clock()
            bucket = self._clients.pop(client, None) or _Bucket(limits.client_burst, now)
            self._clients[client] = bucket
            while len(self._clients) > MAX_CLIENTS:
                self._clients.popitem(last=False)
            bucket.fill(now, limits.client_burst, limits.client_rate)
            self._all.fill(now, limits.global_burst, limits.global_rate)
            if bucket.tokens >= 1 and self._all.tokens >= 1:
                bucket.tokens -= 1
                self._all.tokens -= 1
                return 0.0
            waits = [
                (1 - held.tokens) / rate
                for held, rate in ((bucket, limits.client_rate), (self._all, limits.global_rate))
                if held.tokens < 1
            ]
            return max(waits)


class Guard:
    """The ASGI middleware in front of the observer: body limits, request limits, and the
    security headers on every answer."""

    def __init__(self, app: App, tokens: Tokens, limits: Limits) -> None:
        self.app = app
        self.tokens = tokens
        self.limits = limits
        self.limiter = RateLimiter(limits)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {key.lower() for key, _ in message.get("headers", [])}
                message["headers"] = [
                    *message.get("headers", []),
                    *(
                        (name.encode(), value.encode())
                        for name, value in SECURITY_HEADERS
                        if name.encode() not in present
                    ),
                ]
            await send(message)

        headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
        length = _header(headers, b"content-length")
        chunked = b"chunked" in (_header(headers, b"transfer-encoding") or b"").lower()
        if chunked:
            await _refuse(with_headers, 411, "a request body needs its length")
            return
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                declared = -1
            if declared < 0:
                await _refuse(with_headers, 400, "a malformed Content-Length")
                return
            if declared > self.limits.body_bytes:
                await _refuse(with_headers, 413, "the request body is too large")
                return
        if self.tokens.role_of(headers) is not Role.OWNER:
            client = scope.get("client")
            wait = self.limiter.take(str(client[0]) if client else "unknown")
            if wait > 0:
                seconds = max(1, math.ceil(wait))
                await _refuse(
                    with_headers,
                    429,
                    f"too many requests; try again in {seconds} s",
                    (("retry-after", str(seconds)),),
                )
                return

        received = 0
        limit = self.limits.body_bytes

        async def bounded() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, bounded, with_headers)


async def _refuse(
    send: Send, status: int, detail: str, extra: tuple[tuple[str, str], ...] = ()
) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
                *((name.encode(), value.encode()) for name, value in extra),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
