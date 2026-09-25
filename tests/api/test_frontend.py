"""FastAPI SPA hosting and packaged notification template coverage (M8-07)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import maa_api.main as main_module
from maa_api.settings import Settings, get_settings, set_settings


@pytest.fixture
def frontend_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build an isolated static output tree without relying on a Vite build."""
    frontend_dir = tmp_path / "static"
    assets_dir = frontend_dir / "assets"
    assets_dir.mkdir(parents=True)
    (frontend_dir / "index.html").write_text(
        "<!doctype html><html><body>frontend shell</body></html>", encoding="utf-8"
    )
    (frontend_dir / "assets" / "index-a1b2c3d4.js").write_text(
        "console.log('frontend asset')", encoding="utf-8"
    )
    (frontend_dir / "sw.js").write_text("self.skipWaiting()", encoding="utf-8")
    (frontend_dir / "manifest.webmanifest").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(main_module, "STATIC_PATH", frontend_dir)
    previous_settings = get_settings()
    set_settings(Settings(access_token="frontend-test-token"))
    client = TestClient(main_module.create_app())
    try:
        yield client
    finally:
        client.close()
        set_settings(previous_settings)


def test_frontend_is_served_without_auth_and_navigation_falls_back(
    frontend_client: TestClient,
) -> None:
    root = frontend_client.get("/")
    deep_link = frontend_client.get("/tasks/new", headers={"Accept": "text/html"})

    assert root.status_code == 200
    assert "frontend shell" in root.text
    assert deep_link.status_code == 200
    assert "frontend shell" in deep_link.text


def test_api_and_framework_docs_keep_priority_over_spa_fallback(
    frontend_client: TestClient,
) -> None:
    health = frontend_client.get(
        "/api/system/health", headers={"Accept": "text/html"}
    )
    docs = frontend_client.get("/docs", headers={"Accept": "text/html"})
    openapi = frontend_client.get(
        "/openapi.json", headers={"Accept": "text/html"}
    )

    assert health.status_code == 200
    assert health.headers["content-type"].startswith("application/json")
    assert health.json()["auth_enabled"] is True
    assert docs.status_code == 200 and "swagger-ui" in docs.text
    assert openapi.status_code == 200
    assert openapi.headers["content-type"].startswith("application/json")


def test_unknown_api_post_fallback_and_missing_asset_return_404(
    frontend_client: TestClient,
) -> None:
    unknown_api = frontend_client.get(
        "/api/not-a-route", headers={"Accept": "text/html"}
    )
    post_fallback = frontend_client.post(
        "/tasks/new", headers={"Accept": "text/html"}
    )
    missing_asset = frontend_client.get(
        "/assets/missing.js", headers={"Accept": "application/javascript"}
    )

    assert unknown_api.status_code == 404
    assert unknown_api.json()["error"]["code"] == "NOT_FOUND"
    assert post_fallback.status_code == 404
    assert missing_asset.status_code == 404
    assert not missing_asset.headers.get("content-type", "").startswith("text/html")


def test_static_cache_headers_follow_frontend_asset_policy(
    frontend_client: TestClient,
) -> None:
    hashed_asset = frontend_client.get("/assets/index-a1b2c3d4.js")
    root = frontend_client.get("/")
    spa_route = frontend_client.get("/tasks", headers={"Accept": "text/html"})
    service_worker = frontend_client.get("/sw.js")
    manifest = frontend_client.get("/manifest.webmanifest")

    assert hashed_asset.status_code == 200
    assert (
        hashed_asset.headers["cache-control"]
        == "public, max-age=31536000, immutable"
    )
    for response in (root, spa_route, service_worker, manifest):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"


def test_static_path_is_repository_anchored_and_app_build_does_not_need_assets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected_static_path = Path(main_module.__file__).resolve().parents[1] / "static"
    monkeypatch.chdir(tmp_path)

    assert main_module.STATIC_PATH == expected_static_path
    # ``check_dir=False`` allows app construction before Vite has generated static/.
    main_module.create_app()


def test_notification_template_renders_from_the_package_template_directory() -> None:
    from maa_api.model.core.executor import _render_email_template
    from maa_api.model.core.pipeline import TaskPipelineStatus

    rendered = _render_email_template(
        status=TaskPipelineStatus.COMPLETED,
        today=datetime(2026, 9, 24),
        logs=[],
    )

    assert "<title>MAA-API 任务报告</title>" in rendered
    assert "2026-09-24" in rendered
    assert "completed" in rendered


def test_dockerfile_builds_frontend_then_copies_only_output_to_python_runtime() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = (repo_root / "Dockerfile").read_text(encoding="utf-8")
    node_stage = dockerfile.index("FROM node:22-slim AS web")
    runtime_stage = dockerfile.index("FROM python:3.11.9-slim AS runtime")

    assert node_stage < runtime_stage
    assert "RUN corepack enable && pnpm install --frozen-lockfile" in dockerfile
    assert "RUN pnpm build" in dockerfile
    assert "COPY alembic.ini ./alembic.ini" in dockerfile
    assert "COPY config.template.yaml ./config.yaml" in dockerfile
    assert "COPY --from=web /static ./static" in dockerfile
    assert dockerfile.count("COPY --from=web ") == 1
    assert "node_modules" not in dockerfile[runtime_stage:]


def test_readme_documents_build_before_bare_metal_start_and_resource_only_mount() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "cd web && pnpm install --frozen-lockfile && pnpm build" in readme
    assert '-v "$(pwd)/resource:/app/resource"' in readme
    assert '-v "$(pwd):/app"' not in readme
    assert "Tailscale Serve 的 HTTPS 地址" in readme
