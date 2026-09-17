"""Operate views for generation structures and mutation tokens."""

from __future__ import annotations

import json
from urllib.parse import unquote, urlsplit

from profile_manager.data.asset_catalog import is_safe_asset_id
from profile_manager.data.operate_grammars import grammar_catalog_rows, grammar_detail
from profile_manager.templating import render


def render_operate_grammars() -> str:
    rows = grammar_catalog_rows()
    return render(
        "operate/grammars.j2",
        page_title="Generation grammars",
        density="dense",
        layout="wide",
        active="operate",
        active_sub="grammars",
        rows=rows,
        token_count=sum(row["token_count"] for row in rows),
        ready_count=sum(row["status"] == "ready" for row in rows),
    )


def render_operate_grammar_detail(
    grammar_id: str, *, token: str | None = None
) -> str | None:
    detail = grammar_detail(grammar_id)
    if detail is None:
        return None
    structure = detail.get("structure") or {}
    return render(
        "operate/grammar_detail.j2",
        page_title=detail["label"],
        density="reading",
        layout="wide",
        active="operate",
        active_sub="grammars",
        grammar=detail,
        control_token=token or "",
        shape_json=json.dumps(structure.get("shape", {}), indent=2),
        major_types_json=json.dumps(structure.get("major_types", {}), indent=2),
        cbor_tokens_json=json.dumps(structure.get("cbor_tokens", {}), indent=2),
        sample_sources_text="\n".join(structure.get("sample_sources", [])),
    )


def dispatch_grammar_request(path: str, *, token: str | None = None) -> str | None:
    parts = [unquote(part) for part in urlsplit(path).path.strip("/").split("/")]
    if len(parts) not in {2, 3} or parts[:2] != ["operate", "grammars"]:
        return None
    if len(parts) == 2:
        return render_operate_grammars()
    if not is_safe_asset_id(parts[2]):
        return None
    return render_operate_grammar_detail(parts[2], token=token)
