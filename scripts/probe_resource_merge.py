#!/usr/bin/env python3
"""Probe incremental MaaResource loading against the installed MaaCore.

This is an evidence-gathering script, not application code.  It never connects
to ADB or starts a task.  MaaCore's user directory, downloaded archive, and
extracted resource layer all live under a fresh system temporary directory.

Usage::

    .venv/bin/python scripts/probe_resource_merge.py
    .venv/bin/python scripts/probe_resource_merge.py --archive /path/to/main.zip
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


ARCHIVE_URL = (
    "https://github.com/MaaAssistantArknights/MaaResource/"
    "archive/refs/heads/main.zip"
)
LIB_NAMES = {
    "Darwin": "libMaaCore.dylib",
    "Linux": "libMaaCore.so",
    "Windows": "MaaCore.dll",
}


class MapLevelKey(ctypes.Structure):
    _fields_ = [
        ("stage_id", ctypes.c_char_p),
        ("code", ctypes.c_char_p),
        ("level_id", ctypes.c_char_p),
        ("name", ctypes.c_char_p),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--maa-path",
        type=Path,
        default=Path("resource/lib/maa") / platform.system(),
        help="installed MaaCore directory containing its resource/ tree",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        help="use this MaaResource main-branch zip instead of downloading it",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="GitHub archive download timeout in seconds",
    )
    parser.add_argument(
        "--expect-fallback",
        action="store_true",
        help=(
            "exit successfully when directly testable prerequisites pass but the "
            "remaining native merge/cache assumptions are unobservable, selecting "
            "the documented overlay-and-restart fallback"
        ),
    )
    return parser.parse_args()


def emit(check: str, status: str, conclusion: str, **evidence: Any) -> dict[str, Any]:
    result = {
        "check": check,
        "status": status,
        "conclusion": conclusion,
        **evidence,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return result


def _download_archive(destination: Path, timeout: float) -> None:
    request = urllib.request.Request(
        ARCHIVE_URL,
        headers={"User-Agent": "MAA-API-M7-resource-probe/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"GitHub returned HTTP {response.status}")
            with destination.open("wb") as out:
                shutil.copyfileobj(response, out)
    except Exception as exc:
        # Some macOS Python installations do not consult the system keychain,
        # while curl does.  Keep normal certificate verification and use curl's
        # system trust store as a fallback; never retry with verification off.
        if not isinstance(exc, OSError) and exc.__class__.__name__ != "URLError":
            raise
        try:
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    str(max(1, int(timeout))),
                    "--output",
                    str(destination),
                    ARCHIVE_URL,
                ],
                check=True,
                timeout=timeout + 5,
            )
        except (OSError, subprocess.SubprocessError) as curl_exc:
            raise RuntimeError(
                f"Python HTTPS failed ({exc}); verified curl fallback failed ({curl_exc})"
            ) from curl_exc


def _safe_extract_resource(archive: zipfile.ZipFile, destination: Path) -> int:
    """Extract only MaaResource-main/resource, rejecting traversal and symlinks."""
    prefix = PurePosixPath("MaaResource-main/resource")
    extracted = 0
    for member in archive.infolist():
        path = PurePosixPath(member.filename)
        if path.parts[:2] != prefix.parts or len(path.parts) < 2:
            continue
        relative = path.relative_to(prefix)
        if not relative.parts:
            continue
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise ValueError(f"unsafe archive member: {member.filename!r}")
        # Unix file type bits are stored in the upper 16 bits of external_attr.
        mode = (member.external_attr >> 16) & 0xFFFF
        if mode and (mode & 0o170000) == 0o120000:
            raise ValueError(f"symbolic link is not accepted: {member.filename!r}")
        target = destination.joinpath(*relative.parts)
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as out:
            shutil.copyfileobj(source, out)
        extracted += 1
    if extracted == 0 or not (destination / "version.json").is_file():
        raise ValueError("archive has no MaaResource-main/resource/version.json")
    return extracted


def _decode(value: bytes | None) -> str | None:
    return value.decode("utf-8", "replace") if value else None


def _map_key(lib: ctypes.CDLL, code: str) -> dict[str, str | None]:
    value = lib.AsstGetMapLevelKey(code.encode("utf-8"))
    return {
        "stage_id": _decode(value.stage_id),
        "code": _decode(value.code),
        "level_id": _decode(value.level_id),
        "name": _decode(value.name),
    }


def _read_tile_overview(resource: Path) -> dict[str, dict[str, Any]]:
    path = resource / "Arknights-Tile-Pos" / "overview.json"
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"expected object in {path}")
    return data


def _choose_repo_only_tile_code(base: Path, repo: Path) -> tuple[str, dict[str, Any]]:
    base_overview = _read_tile_overview(base)
    repo_overview = _read_tile_overview(repo)
    base_keys = set(base_overview)
    candidates = [
        item
        for key, item in repo_overview.items()
        if key not in base_keys and isinstance(item, dict) and item.get("code")
    ]
    if not candidates:
        raise ValueError("no MaaResource-only Arknights-Tile-Pos overview entry found")
    # Prefer a deterministic, ordinary stage code. Exclude special-mode suffixes
    # where possible because they are less likely to map through stage lookup.
    candidates.sort(key=lambda item: ("#" in str(item["code"]), str(item["code"])))
    return str(candidates[0]["code"]), candidates[0]


def _load_bindings(lib: ctypes.CDLL) -> None:
    lib.AsstSetUserDir.restype = ctypes.c_bool
    lib.AsstSetUserDir.argtypes = (ctypes.c_char_p,)
    lib.AsstLoadResource.restype = ctypes.c_bool
    lib.AsstLoadResource.argtypes = (ctypes.c_char_p,)
    lib.AsstGetVersion.restype = ctypes.c_char_p
    lib.AsstGetVersion.argtypes = ()
    if hasattr(lib, "AsstGetMapLevelKey"):
        lib.AsstGetMapLevelKey.restype = MapLevelKey
        lib.AsstGetMapLevelKey.argtypes = (ctypes.c_char_p,)
    if hasattr(lib, "AsstCreate") and hasattr(lib, "AsstAppendTask"):
        lib.AsstCreate.restype = ctypes.c_void_p
        lib.AsstCreate.argtypes = ()
        lib.AsstAppendTask.restype = ctypes.c_int32
        lib.AsstAppendTask.argtypes = (
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        )
        lib.AsstDestroy.restype = None
        lib.AsstDestroy.argtypes = (ctypes.c_void_p,)


def run() -> int:
    args = parse_args()
    checks: list[dict[str, Any]] = []
    system = platform.system()
    lib_path = args.maa_path / LIB_NAMES.get(system, "libMaaCore.dylib")
    base_resource = args.maa_path / "resource"
    archive_input = args.archive.resolve() if args.archive else None
    print(
        json.dumps(
            {
                "probe": "M7-01",
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "maa_path": str(args.maa_path.resolve()),
                "library": str(lib_path.resolve()),
                "archive_source": str(archive_input) if archive_input else ARCHIVE_URL,
                "device_connection": False,
                "user_dir_policy": "fresh system temporary directory",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )

    if not lib_path.is_file() or not base_resource.is_dir():
        checks.append(
            emit(
                "local_maacore_prerequisite",
                "FAIL",
                "MaaCore library or its base resource directory is missing",
                library_exists=lib_path.is_file(),
                base_resource_exists=base_resource.is_dir(),
            )
        )
        emit("probe_summary", "FAIL", "required local MaaCore input is unavailable")
        print("PROBE FAILED")
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix="maa-m7-resource-probe-") as temp_name:
            temp = Path(temp_name)
            archive_path = temp / "MaaResource-main.zip"
            repo_resource = temp / "repo" / "resource"
            user_dir = temp / "user"
            empty_layer = temp / "empty-layer"
            user_dir.mkdir()
            (empty_layer / "resource").mkdir(parents=True)

            if archive_input:
                shutil.copyfile(archive_input, archive_path)
            else:
                _download_archive(archive_path, args.timeout)
            with zipfile.ZipFile(archive_path) as archive:
                bad_member = archive.testzip()
                if bad_member is not None:
                    raise ValueError(f"zip CRC check failed at {bad_member}")
                names = [PurePosixPath(name).parts for name in archive.namelist()]
                if not any(parts[:2] == ("MaaResource-main", "resource") for parts in names):
                    raise ValueError("zip does not contain MaaResource-main/resource")
                file_count = _safe_extract_resource(archive, repo_resource)

            version_data = json.loads(
                (repo_resource / "version.json").read_text(encoding="utf-8")
            )
            checks.append(
                emit(
                    "archive_integrity",
                    "PASS",
                    "zip CRCs are valid and only its resource subtree was extracted",
                    archive_bytes=archive_path.stat().st_size,
                    extracted_files=file_count,
                    resource_version=version_data,
                )
            )

            lib = ctypes.CDLL(str(lib_path))
            _load_bindings(lib)
            if system == "Darwin":
                env = os.environ.get("DYLD_LIBRARY_PATH", "")
                if str(args.maa_path.resolve()) not in env.split(os.pathsep):
                    os.environ["DYLD_LIBRARY_PATH"] = os.pathsep.join(
                        value
                        for value in (str(args.maa_path.resolve()), env)
                        if value
                    )
            version = _decode(lib.AsstGetVersion())
            set_user_dir_ok = bool(lib.AsstSetUserDir(str(user_dir).encode()))
            checks.append(
                emit(
                    "isolated_user_dir",
                    "PASS" if set_user_dir_ok else "FAIL",
                    "MaaCore user data is redirected to the temporary probe directory"
                    if set_user_dir_ok
                    else "AsstSetUserDir rejected the temporary probe directory",
                    user_dir=str(user_dir),
                    maa_version=version,
                )
            )
            if not set_user_dir_ok:
                raise RuntimeError("AsstSetUserDir failed; do not continue outside temp dir")

            base_ok = bool(lib.AsstLoadResource(str(args.maa_path.resolve()).encode()))
            checks.append(
                emit(
                    "base_resource_load",
                    "PASS" if base_ok else "FAIL",
                    "installed MaaCore base resource load result",
                    result=base_ok,
                    maa_version=version,
                )
            )
            if not base_ok:
                raise RuntimeError("base AsstLoadResource returned false")

            missing_ok = bool(lib.AsstLoadResource(str(empty_layer).encode()))
            checks.append(
                emit(
                    "incremental_missing_files",
                    "PASS" if missing_ok else "FAIL",
                    "empty resource subtree is accepted as an incremental layer"
                    if missing_ok
                    else "empty resource subtree was rejected",
                    result=missing_ok,
                    layer=str(empty_layer),
                    evidence_limit=(
                        "AsstLoadResource exposes only a bool; silent omission is not "
                        "distinguishable from loading an empty layer"
                    ),
                )
            )
            if not missing_ok:
                raise RuntimeError("empty incremental layer returned false")

            if not hasattr(lib, "AsstGetMapLevelKey"):
                checks.append(
                    emit(
                        "repo_only_tile_stage_lookup",
                        "FAIL",
                        "AsstGetMapLevelKey is not exported by this MaaCore",
                    )
                )
                print("PROBE FAILED")
                return 1

            code, overview_entry = _choose_repo_only_tile_code(base_resource, repo_resource)
            before = _map_key(lib, code)
            layer_ok = bool(lib.AsstLoadResource(str(temp / "repo").encode()))
            after = _map_key(lib, code)
            lookup_ok = bool(layer_ok and any(after.values()) and after.get("code") == code)
            stage_lookup_ok = bool(
                layer_ok and any(after.values()) and after.get("code") == code
            )
            checks.append(
                emit(
                    "repo_only_tile_data_visibility",
                    "FAIL",
                    "repo-only TilePack coordinates are not directly enumerable by the C API",
                    code=code,
                    overview_entry=overview_entry,
                    before_overlay=before,
                    after_overlay=after,
                    incremental_load_result=layer_ok,
                    indirect_stage_metadata_lookup=(
                        "PASS" if stage_lookup_ok else "FAIL"
                    ),
                    evidence_limit=(
                        "AsstGetMapLevelKey resolves stage metadata for a code whose "
                        "overview entry is repo-only; it does not read or return the "
                        "Arknights-Tile-Pos coordinate record"
                    ),
                    observability="UNOBSERVABLE",
                )
            )

            repeated = []
            repeat_results = []
            for _ in range(2):
                repeat_results.append(
                    bool(lib.AsstLoadResource(str(temp / "repo").encode()))
                )
                repeated.append(_map_key(lib, code))
            stable = all(item == after for item in repeated)
            checks.append(
                emit(
                    "tilepack_duplicate_records",
                    "FAIL",
                    "public map-key lookup is stable after repeated loads, but cannot count TilePack rows",
                    code=code,
                    repeat_load_results=repeat_results,
                    repeat_lookup_results=repeated,
                    observable_invariant_passed=stable,
                    observability="UNOBSERVABLE",
                    reason=(
                        "The C API returns one AsstMapLevelKey and exposes no TilePack "
                        "entry count/path enumeration; identical lookup results cannot "
                        "rule out duplicate internal tile records."
                    ),
                )
            )

            # Show that the relevant base-only file is present and that its task
            # definition remains appendable, but do not confuse this with an actual
            # template match (that would need an image/device or a native test API).
            base_award_templates = sorted(
                (base_resource / "template" / "Award").rglob("*.png")
            )
            repo_award_templates = sorted(
                (repo_resource / "template" / "Award").rglob("*.png")
            )
            append_task_result: int | None = None
            instance: int | None = None
            if hasattr(lib, "AsstCreate"):
                instance = lib.AsstCreate()
                if instance:
                    append_task_result = int(
                        lib.AsstAppendTask(instance, b"Award", b"{}")
                    )
                    lib.AsstDestroy(instance)
            checks.append(
                emit(
                    "base_only_template_usable",
                    "FAIL",
                    "base-only Award template and task are present, but actual template use was not observed",
                    base_award_template_count=len(base_award_templates),
                    repo_award_template_count=len(repo_award_templates),
                    sample_base_template=(
                        str(base_award_templates[0].relative_to(base_resource))
                        if base_award_templates
                        else None
                    ),
                    award_append_task_result=append_task_result,
                    observability="UNOBSERVABLE",
                    reason=(
                        "AsstAppendTask only confirms the task definition is accepted; "
                        "AsstStart would require a live image/device to exercise template "
                        "matching, and MaaCore exposes no template lookup API."
                    ),
                )
            )

            checks.append(
                emit(
                    "template_cache_invalidation",
                    "FAIL",
                    "template matching cache revision is not exposed by the C API",
                    repeated_loads=3,
                    stable_map_lookup=stable,
                    observability="UNOBSERVABLE",
                    reason=(
                        "The exported interface has no cache revision getter or matcher "
                        "result tied to a supplied image; repeated resource loads and map "
                        "lookups do not prove matcher cache invalidation."
                    ),
                )
            )

            all_passed = all(item["status"] == "PASS" for item in checks)
            by_check = {item["check"]: item for item in checks}
            emit(
                "probe_summary",
                "PASS" if all_passed else "FAIL",
                "all directly observable checks passed"
                if all_passed
                else "one or more requirements failed or were not publicly observable",
                statuses={item["check"]: item["status"] for item in checks},
                user_dir=str(user_dir),
            )
            if all_passed:
                print("PROBE OK")
                return 0
            tile_visibility = by_check.get("repo_only_tile_data_visibility", {})
            duplicate_check = by_check.get("tilepack_duplicate_records", {})
            template_check = by_check.get("base_only_template_usable", {})
            cache_check = by_check.get("template_cache_invalidation", {})
            indirectly_verified = (
                by_check.get("archive_integrity", {}).get("status") == "PASS"
                and by_check.get("isolated_user_dir", {}).get("status") == "PASS"
                and by_check.get("base_resource_load", {}).get("status") == "PASS"
                and by_check.get("incremental_missing_files", {}).get("status") == "PASS"
                and tile_visibility.get("incremental_load_result") is True
                and tile_visibility.get("indirect_stage_metadata_lookup") == "PASS"
                and duplicate_check.get("observable_invariant_passed") is True
                and template_check.get("base_award_template_count", 0) > 0
                and template_check.get("award_append_task_result", 0) > 0
                and all(
                    item.get("observability") == "UNOBSERVABLE"
                    for item in (
                        tile_visibility,
                        duplicate_check,
                        template_check,
                        cache_check,
                    )
                )
            )
            if args.expect_fallback and indirectly_verified:
                emit(
                    "fallback_decision",
                    "PASS",
                    "native prerequisites and indirect checks pass; use the documented "
                    "resource overlay plus child-process restart because the remaining "
                    "TilePack/template-cache assumptions are not observable",
                    strategy="merge MaaResource into MaaCore resource/ atomically; "
                    "restart the child process after repo updates and reapply the overlay "
                    "after core updates",
                )
                print("PROBE FALLBACK REQUIRED")
                return 0
            print("PROBE FAILED")
            return 1
    except Exception as exc:
        emit(
            "probe_execution",
            "FAIL",
            "probe could not complete",
            error_type=type(exc).__name__,
            error=str(exc),
        )

    print("PROBE FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
