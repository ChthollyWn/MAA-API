"""Offline smoke test for the built SPA and FastAPI static hosting path."""

from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = REPO_ROOT / "static"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.references.append(value)


def main() -> int:
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        print(f"[{'PASS' if condition else 'FAIL'}] {message}")
        if not condition:
            failures.append(message)

    index_path = STATIC_ROOT / "index.html"
    manifest_path = STATIC_ROOT / "manifest.webmanifest"
    sw_path = STATIC_ROOT / "sw.js"
    if not index_path.is_file():
        check(False, "Vite build output exists; run `cd web && pnpm build` first")
        print("SMOKE FAIL")
        return 1

    html = index_path.read_text(encoding="utf-8")
    parser = AssetParser()
    parser.feed(html)
    external = [
        value for value in parser.references if value.startswith(("http://", "https://", "//"))
    ]
    check(not external, "built HTML references only same-origin resources")
    check(manifest_path.is_file(), "PWA manifest is present")
    check(sw_path.is_file(), "PWA service worker is present")

    missing_assets: list[str] = []
    for reference in parser.references:
        if reference.startswith(("data:", "#")):
            continue
        if not reference.startswith("/"):
            continue
        asset_path = (STATIC_ROOT / reference.lstrip("/")).resolve()
        if not asset_path.is_relative_to(STATIC_ROOT.resolve()) or not asset_path.is_file():
            missing_assets.append(reference)
    check(not missing_assets, f"all local entry assets exist: {missing_assets}")

    manifest: dict[str, object] = {}
    if manifest_path.is_file():
        try:
            parsed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(parsed_manifest, dict):
                manifest = parsed_manifest
        except (json.JSONDecodeError, OSError):
            pass
    check(manifest.get("start_url") == "/", "PWA starts at the SPA root")
    icons = manifest.get("icons")
    icon_paths = [
        item.get("src")
        for item in icons
        if isinstance(item, dict) and isinstance(item.get("src"), str)
    ] if isinstance(icons, list) else []
    check(bool(icon_paths), "PWA manifest declares install icons")
    missing_icons = [
        value for value in icon_paths
        if not value.startswith("/") or not (STATIC_ROOT / value.lstrip("/")).is_file()
    ]
    check(not missing_icons, f"manifest icons exist in the build output: {missing_icons}")

    sw_text = sw_path.read_text(encoding="utf-8") if sw_path.is_file() else ""
    check(
        all(re.search(pattern, sw_text) for pattern in (r"api", r"docs", r"redoc", r"openapi")),
        "service worker excludes API and API documentation navigations",
    )

    try:
        from fastapi.testclient import TestClient

        from maa_api.main import app

        # Do not enter the context manager: acceptance must not start MaaCore, ADB, or DB jobs.
        client = TestClient(app, raise_server_exceptions=False)
        root = client.get("/", headers={"Accept": "text/html"})
        deep_link = client.get("/logs/task", headers={"Accept": "text/html"})
        unknown_api = client.get("/api/m8-smoke-not-a-route", headers={"Accept": "text/html"})
        missing_asset = client.get(
            "/assets/m8-smoke-not-a-file.js", headers={"Accept": "application/javascript"}
        )
        post_fallback = client.post("/logs/task", headers={"Accept": "text/html"})
        docs = client.get("/docs", headers={"Accept": "text/html"})

        check(root.status_code == 200 and "<div id=\"root\"></div>" in root.text, "root serves the SPA entry")
        check(
            deep_link.status_code == 200 and deep_link.content == root.content,
            "deep SPA navigations fall back to the same entry document",
        )
        check(
            unknown_api.status_code == 404
            and unknown_api.headers.get("content-type", "").startswith("application/json")
            and unknown_api.json().get("error", {}).get("code") == "NOT_FOUND",
            "unknown API paths stay JSON 404 responses",
        )
        check(
            missing_asset.status_code == 404
            and not missing_asset.headers.get("content-type", "").startswith("text/html"),
            "missing static assets stay 404, never the SPA document",
        )
        check(post_fallback.status_code == 404, "non-GET SPA requests stay 404")
        check(docs.status_code == 200 and "swagger-ui" in docs.text, "FastAPI docs keep priority")
        check(root.headers.get("cache-control") == "no-cache", "SPA entry uses no-cache")
        check(
            deep_link.headers.get("cache-control") == "no-cache",
            "deep navigation document uses no-cache",
        )

        asset_refs = [
            value for value in parser.references
            if value.startswith("/assets/") and Path(value).suffix in {".js", ".css"}
        ]
        asset_responses = [client.get(value) for value in asset_refs]
        check(bool(asset_responses), "built entry references hashed JS/CSS assets")
        check(
            all(
                response.status_code == 200
                and response.headers.get("cache-control")
                == "public, max-age=31536000, immutable"
                for response in asset_responses
            ),
            "hashed JS/CSS assets are served with immutable caching",
        )
        service_worker = client.get("/sw.js")
        manifest_response = client.get("/manifest.webmanifest")
        check(
            service_worker.status_code == 200
            and service_worker.headers.get("cache-control") == "no-cache",
            "service worker is served without a long-lived cache",
        )
        check(
            manifest_response.status_code == 200
            and manifest_response.headers.get("cache-control") == "no-cache",
            "manifest is served without a long-lived cache",
        )
    except Exception as exc:  # noqa: BLE001 - turn any infrastructure error into a readable smoke failure
        check(False, f"FastAPI TestClient smoke completed: {type(exc).__name__}: {exc}")

    if failures:
        print(f"SMOKE FAIL ({len(failures)} checks)")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
