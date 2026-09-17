"""Learn reference for generation structures and mutation-token sets."""

from __future__ import annotations

from profile_manager.data.learn_examples import grammar_management_examples
from profile_manager.data.operate_grammars import grammar_catalog_rows
from profile_manager.templating import render


def render_learn_grammars() -> str:
    rows = grammar_catalog_rows()
    return render(
        "learn/grammars.j2",
        page_title="Generation grammars",
        density="reading",
        layout="wide",
        active="learn",
        active_sub="grammars",
        rows=rows,
        token_count=sum(row["token_count"] for row in rows),
        examples=grammar_management_examples(),
    )
