"""Learn reference for fuzz-corpus roles and safe management."""

from __future__ import annotations

from profile_manager.data.learn_examples import corpus_management_examples
from profile_manager.data.operate_corpora import corpus_catalog_rows
from profile_manager.templating import render


def render_learn_corpora() -> str:
    rows = corpus_catalog_rows()
    return render(
        "learn/corpora.j2",
        page_title="Fuzz corpora",
        density="reading",
        layout="wide",
        active="learn",
        active_sub="corpora",
        corpus_count=len(rows),
        input_count=sum(row["input_count"] for row in rows),
        examples=corpus_management_examples(),
    )
