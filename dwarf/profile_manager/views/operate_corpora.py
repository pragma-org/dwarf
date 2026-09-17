"""Operate views for shipped and runtime fuzz corpora."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

from profile_manager.data.asset_catalog import is_safe_asset_id
from profile_manager.data.operate_corpora import corpus_catalog_rows, corpus_detail
from profile_manager.templating import render


def render_operate_corpora() -> str:
    rows = corpus_catalog_rows()
    return render(
        "operate/corpora.j2",
        page_title="Fuzz corpora",
        density="dense",
        layout="wide",
        active="operate",
        active_sub="corpora",
        rows=rows,
        input_count=sum(row["input_count"] for row in rows),
        size_bytes=sum(row["size_bytes"] for row in rows),
    )


def render_operate_corpus_detail(corpus_id: str, *, token: str | None = None) -> str | None:
    detail = corpus_detail(corpus_id)
    if detail is None:
        return None
    return render(
        "operate/corpus_detail.j2",
        page_title=detail["label"],
        density="reading",
        layout="wide",
        active="operate",
        active_sub="corpora",
        corpus=detail,
        control_token=token or "",
    )


def dispatch_corpus_request(path: str, *, token: str | None = None) -> str | None:
    parts = [unquote(part) for part in urlsplit(path).path.strip("/").split("/")]
    if len(parts) not in {2, 3} or parts[:2] != ["operate", "corpora"]:
        return None
    if len(parts) == 2:
        return render_operate_corpora()
    if not is_safe_asset_id(parts[2]):
        return None
    return render_operate_corpus_detail(parts[2], token=token)
