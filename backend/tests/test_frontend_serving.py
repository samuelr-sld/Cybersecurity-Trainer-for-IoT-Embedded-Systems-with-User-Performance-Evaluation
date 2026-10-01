"""Single-origin deployment: the backend serves the prebuilt production frontend.

`app/frontend.py` mounts a build directory at `/`. These tests pin the two
halves of that promise:

  * the frontend is actually served (`/`, hashed `/assets/...`), and
  * NOTHING that existed before it changed — `/health`, `/api/*`, `/ws/*`, and
    the exact shape of "not found" the frontend's API client depends on.

They run against `create_app(frontend_dist=<tmp dir>)`, i.e. the REAL routers
and middleware with a throwaway build directory, so there is no process-wide
state to undo and no dependency on a developer's real `dist/`. (The shipped
bundle itself is verified by hand on the Raspberry Pi.)
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import config
from app.main import app, create_app

INDEX_HTML = (
    '<!doctype html><html><head><title>TRAINER-FRONTEND-SENTINEL</title>'
    '<script type="module" src="/assets/index-abc123.js"></script></head>'
    '<body><div id="root"></div></body></html>'
)
ASSET_JS = "console.log('hashed-asset-sentinel')"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A minimal production build: index.html, a hashed asset, a root file."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (tmp_path / "assets" / "index-abc123.js").write_text(ASSET_JS, encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(dist: Path) -> TestClient:
    return TestClient(create_app(frontend_dist=str(dist)))


# --- the frontend is served ---------------------------------------------------


def test_root_serves_the_production_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "TRAINER-FRONTEND-SENTINEL" in response.text


def test_hashed_assets_are_served_from_the_site_root(client: TestClient) -> None:
    # The build's index.html uses ABSOLUTE /assets/... paths, so assets must
    # resolve at the root, not under a prefix.
    response = client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.text == ASSET_JS


def test_other_root_files_of_the_build_are_served(client: TestClient) -> None:
    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg")


def test_a_missing_asset_is_a_404_not_the_index(client: TestClient) -> None:
    # No SPA history fallback: the app has no client-side router, and a
    # missing hashed asset must fail loudly rather than return HTML.
    response = client.get("/assets/index-does-not-exist.js")
    assert response.status_code == 404


# --- nothing that existed before changed --------------------------------------


def test_health_is_unchanged_and_not_shadowed(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == TestClient(create_app(frontend_dist="")).get("/health").json()
    assert response.json()["status"] == "ok"


def test_api_routes_are_unchanged_and_not_shadowed(client: TestClient) -> None:
    participants = client.get("/api/participants")
    assert participants.status_code == 200
    assert participants.headers["content-type"].startswith("application/json")
    assert participants.json() == {"participants": []}

    status = client.get("/api/sessions/hack/no-such-session")
    assert status.status_code == 200
    assert status.json() == {"live": False}


def test_an_unknown_api_path_keeps_fastapis_own_404_body(client: TestClient) -> None:
    # The frontend's API client recognises an outdated backend by EXACTLY this
    # body (`UNROUTED_DETAIL` in src/api/trainerApi.js). A catch-all static
    # mount must not turn it into plain text or HTML.
    response = client.get("/api/this-route-does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_openapi_is_still_served(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 200


def test_the_hack_websocket_still_routes(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as websocket:
        assert websocket.receive_json()["type"] == "session"


def test_the_build_websocket_still_routes(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as websocket:
        assert websocket.receive_json()["type"] == "session"


def test_an_unknown_websocket_path_is_still_refused_not_crashed(dist: Path) -> None:
    # StaticFiles asserts an HTTP scope; without FrontendFiles' guard a
    # WebSocket reaching the `/` mount would raise AssertionError. It must be
    # the same clean refusal the router gives with no frontend configured.
    with_frontend = TestClient(create_app(frontend_dist=str(dist)))
    without_frontend = TestClient(create_app(frontend_dist=""))
    codes = []
    for candidate in (with_frontend, without_frontend):
        with pytest.raises(WebSocketDisconnect) as refused:
            with candidate.websocket_connect("/ws/no-such-endpoint"):
                pass
        codes.append(refused.value.code)
    assert codes[0] == codes[1]


# --- the mount cannot be used to read outside the build -----------------------


@pytest.mark.parametrize(
    "path",
    ["/..%2fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt", "/assets/../../secret.txt"],
)
def test_path_traversal_cannot_leave_the_build_directory(dist: Path, path: str) -> None:
    (dist.parent / "secret.txt").write_text("OUTSIDE-THE-BUILD", encoding="utf-8")
    response = TestClient(create_app(frontend_dist=str(dist))).get(path)
    assert "OUTSIDE-THE-BUILD" not in response.text


# --- configuration -------------------------------------------------------------


def test_unconfigured_means_api_only_exactly_as_before() -> None:
    client = TestClient(create_app(frontend_dist=""))
    root = client.get("/")
    assert root.status_code == 404
    assert root.json() == {"detail": "Not Found"}
    assert client.get("/health").status_code == 200


def test_the_default_application_serves_no_frontend_unless_configured() -> None:
    # `app` is the process-wide instance; with TRAINER_FRONTEND_DIST unset
    # (the development and test default) it must remain API-only.
    if config.FRONTEND_DIST_DIR:
        pytest.skip("TRAINER_FRONTEND_DIST is set in this environment")
    assert TestClient(app).get("/").status_code == 404


def test_the_directory_comes_from_configuration_not_from_code(
    dist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FRONTEND_DIST_DIR", str(dist))
    response = TestClient(create_app()).get("/")
    assert response.status_code == 200
    assert "TRAINER-FRONTEND-SENTINEL" in response.text


def test_a_directory_without_an_index_leaves_the_api_up_and_says_why(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    empty = tmp_path / "not-a-build"
    empty.mkdir()
    with caplog.at_level(logging.WARNING, logger="app.frontend"):
        client = TestClient(create_app(frontend_dist=str(empty)))
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 404
    assert any("index.html" in record.getMessage() for record in caplog.records)


def test_a_nonexistent_directory_does_not_prevent_startup(tmp_path: Path) -> None:
    client = TestClient(create_app(frontend_dist=str(tmp_path / "missing")))
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 404
