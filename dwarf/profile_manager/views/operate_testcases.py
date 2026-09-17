"""Operate pages and artifact downloads for testcase lifecycle state."""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from profile_manager.data.asset_catalog import is_safe_asset_id
from profile_manager.data.operate_testcases import (
    testcase_artifact,
    testcase_bucket_detail,
    testcase_bucket_rows,
    testcase_catalog_rows,
    testcase_detail,
)
from profile_manager.templating import render


def render_operate_testcases() -> str:
    rows = testcase_catalog_rows()
    return render(
        "operate/testcases.j2",
        page_title="Testcases",
        density="dense",
        layout="wide",
        active="operate",
        active_sub="testcases",
        rows=rows,
        malformed_count=sum(row["status"] == "malformed" for row in rows),
    )


def render_operate_testcase_detail(case_id: str) -> str | None:
    detail = testcase_detail(case_id)
    if detail is None:
        return None
    return render(
        "operate/testcase_detail.j2",
        page_title=case_id,
        density="reading",
        layout="wide",
        active="operate",
        active_sub="testcases",
        testcase=detail,
    )


def render_operate_testcase_buckets() -> str:
    rows = testcase_bucket_rows()
    return render(
        "operate/testcase_buckets.j2",
        page_title="Testcase buckets",
        density="dense",
        layout="wide",
        active="operate",
        active_sub="testcase-buckets",
        rows=rows,
        case_count=sum(row["case_count"] for row in rows),
    )


def render_operate_testcase_bucket_detail(bucket_id: str) -> str | None:
    detail = testcase_bucket_detail(bucket_id)
    if detail is None:
        return None
    return render(
        "operate/testcase_bucket_detail.j2",
        page_title=bucket_id,
        density="reading",
        layout="wide",
        active="operate",
        active_sub="testcase-buckets",
        bucket=detail,
    )


def dispatch_testcase_request(path: str) -> str | None:
    parts = [unquote(part) for part in urlsplit(path).path.strip("/").split("/")]
    if len(parts) not in {2, 3} or parts[0] != "operate":
        return None
    if parts[1] == "testcases":
        if len(parts) == 2:
            return render_operate_testcases()
        return render_operate_testcase_detail(parts[2]) if is_safe_asset_id(parts[2]) else None
    if parts[1] == "testcase-buckets":
        if len(parts) == 2:
            return render_operate_testcase_buckets()
        return render_operate_testcase_bucket_detail(parts[2]) if is_safe_asset_id(parts[2]) else None
    return None


def dispatch_testcase_artifact_request(path: str):
    parts = [unquote(part) for part in urlsplit(path).path.strip("/").split("/")]
    if len(parts) != 5 or parts[:3] != ["api", "assets", "testcases"] or parts[4] != "artifact":
        return None
    case_id = parts[3]
    if not is_safe_asset_id(case_id):
        return (400, "text/plain; charset=utf-8", b"invalid asset id\n")
    state, artifact = testcase_artifact(case_id)
    if state == "unsafe-path":
        return (400, "text/plain; charset=utf-8", b"unsafe artifact path\n")
    if state != "recorded" or artifact is None:
        return (404, "text/plain; charset=utf-8", b"artifact not found\n")
    filename = PurePosixPath(artifact.name).name.replace('"', "") or f"{case_id}.bin"
    return (
        200,
        "application/octet-stream",
        artifact.read_bytes(),
        {"Content-Disposition": f'attachment; filename="{filename}"'},
    )
