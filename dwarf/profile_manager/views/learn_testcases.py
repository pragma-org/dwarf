"""Learn reference for testcase lifecycle records and buckets."""

from __future__ import annotations

from profile_manager.data.operate_testcases import testcase_bucket_rows, testcase_catalog_rows
from profile_manager.templating import render


def render_learn_testcases() -> str:
    rows = testcase_catalog_rows()
    buckets = testcase_bucket_rows()
    return render(
        "learn/testcases.j2",
        page_title="Testcase lifecycle",
        density="reading",
        layout="wide",
        active="learn",
        active_sub="testcases",
        testcase_count=len(rows),
        bucket_count=len(buckets),
    )
