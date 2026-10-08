"""Watching from outside (slice H): viewers may look and never steer, the owner acts only from
its own machine, and every answer carries the limits and security headers."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from format_one import FIXTURE
from typer.testing import CliRunner

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from observer.test_runner_link import FakeRunner
from observer.test_server import _paths

from sovereign_world.cli import app as cli_app
from sovereign_world.observer.access import (
    CSP,
    MIN_TOKEN_CHARS,
    SECURITY_HEADERS,
    Limits,
    RateLimiter,
    Role,
    Tokens,
    make_tokens,
)
from sovereign_world.observer.server import UI_ROOT, build_app, check_public
from sovereign_world.observer.service import RunService

OWNER = "owner-token-" + "o" * 32
VIEWER = "viewer-token-" + "v" * 32
AS_OWNER = {"Authorization": f"Bearer {OWNER}"}
AS_VIEWER = {"Authorization": f"Bearer {VIEWER}"}
OUTSIDE = (
    {"X-Forwarded-For": "203.0.113.7"},
    {"Cf-Connecting-Ip": "203.0.113.7"},
    {"Cf-Ray": "8a1b2c3d4e5f-DXB"},
)
FILL = {"day": "1", "civ": "0", "path": "manifest.json", "rest": "anything"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def old_run(tmp_path: Path) -> Path:
    root = tmp_path / "old"
    shutil.copytree(FIXTURE, root)
    return root


@pytest.fixture
def service(old_run: Path) -> RunService:
    walked = RunService(old_run)
    walked.walk_all()
    return walked


def _client(
    service: RunService, *, public: bool = False, limits: Limits | None = None
) -> TestClient:
    return TestClient(build_app(service, OWNER, viewer_token=VIEWER, public=public, limits=limits))


def _api_urls(service: RunService) -> list[tuple[str, str]]:
    app = build_app(service, OWNER, viewer_token=VIEWER)
    urls = []
    for method, path in _paths(app.routes):
        if not path.startswith("/api"):
            continue
        url = path.replace(":path", "")
        for name, value in FILL.items():
            url = url.replace("{" + name + "}", value)
        urls.append((method, url))
    return urls


def test_a_viewer_may_look_at_everything_but_control_nothing(service: RunService) -> None:
    service.attach(FakeRunner())
    client = _client(service)
    urls = _api_urls(service)
    assert ("POST", "/api/control") in urls and ("GET", "/api/control") in urls
    for method, url in urls:
        answer = client.request(method, url, headers=AS_VIEWER, json={})
        if url == "/api/control":
            assert answer.status_code == 403, (method, url)
            assert "viewers may not" in answer.json()["detail"]
        else:
            assert answer.status_code not in (401, 403), (method, url)
        owner = client.request(method, url, headers=AS_OWNER, json={})
        assert owner.status_code not in (401, 403), (method, url)
    status = client.get("/api/status", headers=AS_VIEWER).json()
    assert status["role"] == "viewer" and status["public"] is False
    assert client.get("/api/status", headers=AS_OWNER).json()["role"] == "owner"
    # The viewer's attempt changed nothing the runner was asked.
    paused = client.post("/api/control", headers=AS_VIEWER, json={"paused": False})
    assert paused.status_code == 403
    assert client.get("/api/control", headers=AS_OWNER).json()["control"]["paused"] is True


def test_a_public_observer_lets_nobody_steer(service: RunService) -> None:
    service.attach(FakeRunner())
    client = _client(service, public=True)
    for headers in (AS_OWNER, AS_VIEWER):
        assert client.get("/api/control", headers=headers).status_code == 403
        answer = client.post("/api/control", headers=headers, json={"paused": False})
        assert answer.status_code == 403
        assert "viewing only" in answer.json()["detail"]
        assert client.get("/api/status", headers=headers).json()["public"] is True
    # Without a token it is still 401: the token check comes first.
    assert client.post("/api/control", json={"paused": False}).status_code == 401


def test_the_owners_token_works_only_on_the_observers_own_machine(service: RunService) -> None:
    client = _client(service)
    for outside in OUTSIDE:
        refused = client.get("/api/status", headers={**AS_OWNER, **outside})
        assert refused.status_code == 401, outside
        assert refused.headers["www-authenticate"] == "Bearer"
        viewer = client.get("/api/status", headers={**AS_VIEWER, **outside})
        assert viewer.status_code == 200 and viewer.json()["role"] == "viewer"
    assert client.get("/api/status", headers=AS_OWNER).status_code == 200


def test_tokens_are_checked_and_both_always_compared(
    service: RunService, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="at least"):
        Tokens(OWNER, "v" * (MIN_TOKEN_CHARS - 1))
    with pytest.raises(ValueError, match="differ"):
        Tokens(OWNER, OWNER)
    with pytest.raises(ValueError):
        Tokens("")
    with pytest.raises(ValueError):
        build_app(service, OWNER, viewer_token="short")
    tokens = Tokens(OWNER, VIEWER)
    headers = [(b"authorization", f"Bearer {VIEWER}".encode())]
    assert tokens.role_of(headers) is Role.VIEWER
    assert tokens.role_of([(b"authorization", f"Bearer {OWNER}".encode())]) is Role.OWNER
    assert tokens.role_of([(b"authorization", VIEWER.encode())]) is None
    assert tokens.role_of([]) is None
    # Without a viewer token, no bearer is a viewer (an empty one included).
    assert Tokens(OWNER).role_of([(b"authorization", b"Bearer ")]) is None
    compared: list[bytes] = []
    import sovereign_world.observer.access as access

    real = access.secrets.compare_digest

    def counting(given: bytes, expected: bytes) -> bool:
        compared.append(expected)
        return real(given, expected)

    monkeypatch.setattr(access.secrets, "compare_digest", counting)
    for token in (OWNER, VIEWER, "wrong"):
        compared.clear()
        tokens.role_of([(b"authorization", f"Bearer {token}".encode())])
        assert len(compared) == 2, token
        compared.clear()
        Tokens(OWNER).role_of([(b"authorization", f"Bearer {token}".encode())])
        assert len(compared) == 2, token
    # A viewer token exists only on a public observer.
    assert make_tokens(OWNER, VIEWER, public=False).viewer is None
    assert make_tokens(OWNER, VIEWER, public=True).viewer == VIEWER
    fresh = make_tokens(OWNER, None, public=True).viewer
    assert fresh is not None and len(fresh) >= MIN_TOKEN_CHARS


def test_requests_are_limited_per_client_and_in_all(service: RunService) -> None:
    clock = Clock()
    limits = Limits(
        client_burst=5, client_rate=1, global_burst=8, global_rate=2, body_bytes=4096, clock=clock
    )
    app = build_app(service, OWNER, viewer_token=VIEWER, limits=limits)
    first = TestClient(app, client=("198.51.100.1", 5000))
    second = TestClient(app, client=("198.51.100.2", 5000))
    for _ in range(5):
        assert first.get("/api/status", headers=AS_VIEWER).status_code == 200
    refused = first.get("/api/status", headers=AS_VIEWER)
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "1"
    assert "too many requests" in refused.json()["detail"]
    # Pages and wrong tokens count too.
    assert first.get("/").status_code == 429
    # Another client has its own allowance, until all clients together run out.
    for _ in range(3):
        assert second.get("/api/status", headers=AS_VIEWER).status_code == 200
    everyone = second.get("/api/status", headers=AS_VIEWER)
    assert everyone.status_code == 429 and everyone.headers["retry-after"] == "1"
    # The owner on its own machine is never limited; from outside it is a stranger.
    for _ in range(20):
        assert first.get("/api/status", headers=AS_OWNER).status_code == 200
    outside = first.get("/api/status", headers={**AS_OWNER, **OUTSIDE[2]})
    assert outside.status_code == 429
    clock.now += 1
    assert first.get("/api/status", headers=AS_VIEWER).status_code == 200


def test_the_limiter_forgets_and_waits() -> None:
    clock = Clock()
    limiter = RateLimiter(
        Limits(client_burst=2, client_rate=0.5, global_burst=100, global_rate=100, clock=clock)
    )
    assert limiter.take("a") == 0 and limiter.take("a") == 0
    assert limiter.take("a") == pytest.approx(2.0)
    clock.now += 1
    assert limiter.take("a") == pytest.approx(1.0)
    clock.now += 2
    assert limiter.take("a") == 0
    assert Limits() == Limits(client_burst=600, client_rate=20, global_burst=3000)


def test_large_or_unmeasured_bodies_are_refused(service: RunService) -> None:
    service.attach(FakeRunner())
    client = _client(service)
    big = json.dumps({"shown": 1, "pad": "x" * 5000})
    answer = client.post(
        "/api/control",
        headers={**AS_OWNER, "Content-Type": "application/json"},
        content=big,
    )
    assert answer.status_code == 413
    assert answer.headers["content-security-policy"] == CSP

    def chunks() -> object:
        yield b'{"shown": 1}'

    chunked = client.post(
        "/api/control",
        headers={**AS_OWNER, "Content-Type": "application/json"},
        content=chunks(),  # type: ignore[arg-type]
    )
    assert chunked.status_code == 411
    small = client.post("/api/control", headers=AS_OWNER, json={"shown": 1})
    assert small.status_code == 200


def test_every_answer_carries_the_security_headers(service: RunService) -> None:
    service.attach(FakeRunner())
    clock = Clock()
    client = _client(
        service, limits=Limits(client_burst=3, client_rate=1, global_burst=100, clock=clock)
    )
    answers = [
        client.get("/", headers=AS_OWNER),
        client.get("/src/main.js", headers=AS_OWNER),
        client.get("/nothing.js", headers=AS_OWNER),
        client.get("/api/status", headers=AS_OWNER),
        client.get("/api/run/days/999", headers=AS_OWNER),
        client.get("/api/status"),
        client.get("/api/control", headers=AS_VIEWER),
    ]
    for _ in range(4):
        answers.append(client.get("/api/status", headers=AS_VIEWER))
    statuses = {answer.status_code for answer in answers}
    assert statuses == {200, 401, 403, 404, 429}
    for answer in answers:
        for name, value in SECURITY_HEADERS:
            assert answer.headers.get(name) == value, (answer.status_code, name)
        assert "server" not in answer.headers or "uvicorn" not in answer.headers["server"]


def test_a_viewer_sees_no_path_from_this_machine(service: RunService, old_run: Path) -> None:
    client = _client(service, public=True)
    hidden = [str(old_run).encode(), str(UI_ROOT).encode(), str(Path.home()).encode()]
    seen = 0
    for method, url in _api_urls(service):
        if method != "GET" or url.endswith("/people"):
            continue
        answer = client.get(url, headers=AS_VIEWER)
        for path in hidden:
            assert path not in answer.content, (url, path)
        seen += 1
    assert seen >= 10


def test_a_public_observer_runs_no_world_and_stays_on_this_machine(old_run: Path) -> None:
    check_public(host="127.0.0.1", run_days=None)
    check_public(host="localhost", run_days=None)
    check_public(host="::1", run_days=None)
    with pytest.raises(ValueError, match="runs no world"):
        check_public(host="127.0.0.1", run_days=3)
    for host in ("0.0.0.0", "192.168.1.10", "example.com"):
        with pytest.raises(ValueError, match="only on this machine"):
            check_public(host=host, run_days=None)
    runner = CliRunner()
    for extra in (["--run-days", "3"], ["--host", "0.0.0.0"]):
        result = runner.invoke(cli_app, ["observe", str(old_run), "--public", *extra])
        assert result.exit_code == 2, result.output


def test_the_page_needs_nothing_the_security_policy_refuses() -> None:
    """The CSP refuses inline styles, inline scripts and event-handler attributes: the page's
    own code uses none (styles are set through the CSSOM)."""
    files = [UI_ROOT / "index.html", *sorted((UI_ROOT / "src").rglob("*.js"))]
    patterns = {
        "a style attribute": re.compile(r"""\sstyle\s*=\s*["'$]"""),
        "an inline script": re.compile(r"<script(?![^>]*\bsrc=)[^>]*>"),
        "an event-handler attribute": re.compile(r"""<[a-z][^>]*\son[a-z]+\s*=\s*["']"""),
        "a javascript: address": re.compile(r"javascript:"),
    }
    found = [
        f"{path.relative_to(UI_ROOT)}: {what}"
        for path in files
        for what, pattern in patterns.items()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert found == []
    assert len(files) > 50
