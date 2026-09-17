"""Learner-side dashboard landing — editorial entry point.

Slice 20 CP2: tile grid linking concepts, walkthroughs, architecture,
coverage, and status. Reading-mode density preserves the editorial
tone while the brand chrome carries the forensic-noir HUD aesthetic.

Anti-fabrication rails: every count is sourced from a real catalog
(CONCEPTS, walkthrough_entries, scenarios, fuzz targets, profile node
types). Nothing synthesized.
"""
from __future__ import annotations

from profile_manager.data.concepts import CONCEPTS
from profile_manager.data.learn_docs import GLOSSARY
from profile_manager.data.learn_examples import asset_catalog_examples, list_examples
from profile_manager.data.operate_targets import operate_target_rows
from profile_manager.data.operate_runs import (
    operate_run_rows,
    status_pill_inventory,
)
from profile_manager.data.profiles import _profile_rows
from profile_manager.data.operate_profile_templates import profile_template_catalog_rows
from profile_manager.data.operate_corpora import corpus_catalog_rows
from profile_manager.data.operate_grammars import grammar_catalog_rows
from profile_manager.data.operate_risk_packages import risk_package_catalog_rows
from profile_manager.data.operate_plugins import plugin_catalog_payload
from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.data.status import deployed_source_summary
from profile_manager.data.walkthroughs import walkthrough_entries
from profile_manager.templating import render


def render_learn_landing() -> str:
    impls = sorted({row["node_type"] for row in _profile_rows() if row.get("node_type")})

    runs = operate_run_rows(limit=200)
    pills = status_pill_inventory(runs)
    pass_count = next((p["count"] for p in pills if p["slug"] == "pass"), 0)
    pass_rate_pct = round(100 * pass_count / len(runs)) if runs else None
    corpora = corpus_catalog_rows()
    grammars = grammar_catalog_rows()
    risk_packages = risk_package_catalog_rows()

    return render(
        "learn/landing.j2",
        page_title="Learn",
        density="reading",        active="learn",
        active_sub="overview",
        implementations=impls,
        scenario_count=len(_list_scenarios_for_compare()),
        primitive_count=deployed_source_summary()["primitive_count"],
        profile_template_count=len(profile_template_catalog_rows()),
        corpus_count=len(corpora),
        corpus_input_count=sum(row["input_count"] for row in corpora),
        grammar_count=len(grammars),
        grammar_token_count=sum(row["token_count"] for row in grammars),
        risk_package_count=len(risk_packages),
        risk_candidate_count=len({candidate for row in risk_packages for candidate in row["candidate_ids"]}),
        plugin_count=len(plugin_catalog_payload()["plugins"]),
        fuzz_target_count=len(operate_target_rows()),
        concept_count=len(CONCEPTS),
        glossary_count=len(GLOSSARY),
        example_count=len(list_examples()),
        asset_example_count=len(asset_catalog_examples()),
        walkthrough_count=len(walkthrough_entries()),
        runs_total=len(runs),
        pass_rate_pct=pass_rate_pct,
    )
