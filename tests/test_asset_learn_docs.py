"""Reconciliation contract for the asset catalogs and their Learn guidance."""

from __future__ import annotations

from pathlib import Path
import re

from profile_manager import dashboard
from profile_manager.data import learn_api, learn_examples, sub_nav
from profile_manager.data.operate_corpora import corpus_catalog_rows
from profile_manager.data.operate_grammars import grammar_catalog_rows
from profile_manager.data.operate_plugins import plugin_catalog_payload
from profile_manager.data.operate_primitives import primitive_catalog_rows
from profile_manager.data.operate_profile_templates import profile_template_catalog_rows
from profile_manager.data.operate_risk_packages import risk_package_catalog_rows
from profile_manager.data.operate_testcases import (
    testcase_bucket_rows as load_testcase_buckets,
    testcase_catalog_rows as load_testcases,
)
from profile_manager.views.operate_primitives_new import render_operate_primitives_new


ROOT = Path(__file__).resolve().parents[1]

CATALOGS = {
    "primitives": ("/operate/primitives", "/operate/primitives/<id>", "/learn/primitives"),
    "profile-templates": (
        "/operate/profile-templates",
        "/operate/profile-templates/<id>",
        "/learn/profile-templates",
    ),
    "testcases": ("/operate/testcases", "/operate/testcases/<id>", "/learn/testcases"),
    "testcase-buckets": (
        "/operate/testcase-buckets",
        "/operate/testcase-buckets/<id>",
        "/learn/testcases",
    ),
    "corpora": ("/operate/corpora", "/operate/corpora/<id>", "/learn/corpora"),
    "grammars": ("/operate/grammars", "/operate/grammars/<id>", "/learn/grammars"),
    "risk-packages": (
        "/operate/risk-packages",
        "/operate/risk-packages/<id>",
        "/learn/risk-packages",
    ),
    "plugins": ("/operate/plugins", "/operate/plugins/<id>", "/learn/plugin-authoring"),
}


def test_every_asset_catalog_is_in_api_reference_and_primary_catalogs_are_in_navigation():
    routes = {route for group in learn_api.html_route_groups() for route in group["routes"]}
    operate_nav = {entry["url"] for entry in sub_nav.OPERATE_SUB_NAV}
    learn_nav = {entry["url"] for entry in sub_nav.LEARN_SUB_NAV}

    compatibility_only = {"testcases", "testcase-buckets"}
    for catalog, (operate, detail, learn) in CATALOGS.items():
        assert {operate, detail, learn} <= routes
        if catalog in compatibility_only:
            assert operate not in operate_nav
            assert learn not in learn_nav
        else:
            assert operate in operate_nav
            assert learn in learn_nav


def test_every_asset_catalog_has_a_complete_concrete_example():
    examples = {entry["catalog"]: entry for entry in learn_examples.asset_catalog_examples()}

    assert set(examples) == set(CATALOGS)
    for catalog, entry in examples.items():
        assert entry["object"]
        assert entry["purpose"]
        assert entry["schema"]
        assert entry["lifecycle"]
        assert entry["source_of_truth"]
        assert (ROOT / entry["example_source"]).is_file()
        assert entry["mutability"]
        assert entry["relationships"]
        assert entry["example"]
        assert entry["operate_url"] == CATALOGS[catalog][0]
        assert entry["learn_url"] == CATALOGS[catalog][2]


def test_learn_landing_counts_reconcile_to_live_catalogs():
    html = dashboard.render_route_html("/learn")
    corpora = corpus_catalog_rows()
    grammars = grammar_catalog_rows()
    risks = risk_package_catalog_rows()

    expected = (
        f"{len(primitive_catalog_rows())} registered operations",
        f"{len(profile_template_catalog_rows())} shipped topology scaffolds",
        f"{len(corpora)} corpus units · {sum(row['input_count'] for row in corpora)} inputs",
        f"{len(grammars)} structure/token sets · {sum(row['token_count'] for row in grammars)} parsed tokens",
        f"{len(risks)} work packages",
        f"{len(plugin_catalog_payload()['plugins'])} discovered plugins",
    )
    for label in expected:
        assert label in html
    assert f"{len(load_testcases())} records · {len(load_testcase_buckets())} buckets" not in html


def test_contextual_authoring_links_resolve_to_documented_learn_pages():
    expected_links = {
        "/operate/scenarios/new": "/learn/overview#dsl",
        "/operate/profiles/new": "/learn/developer-onboarding#authoring-profiles",
        "/operate/targets/new": "/learn/developer-onboarding#authoring-targets",
        "/operate/primitives/new": "/learn/primitives#authoring",
        "/operate/profile-templates": "/learn/profile-templates",
        "/operate/testcases": "/learn/testcases",
        "/operate/testcase-buckets": "/learn/testcases",
        "/operate/corpora": "/learn/corpora",
        "/operate/grammars": "/learn/grammars",
        "/operate/risk-packages": "/learn/risk-packages",
        "/operate/plugins": "/learn/plugin-authoring",
    }
    documented = {route for group in learn_api.html_route_groups() for route in group["routes"]}

    for operate, learn in expected_links.items():
        html = (
            render_operate_primitives_new(token="test")
            if operate == "/operate/primitives/new"
            else dashboard.render_route_html(operate)
        )
        assert html is not None
        assert f'href="{learn}"' in html
        assert learn.split("#", 1)[0] in documented
        assert dashboard.render_route_html(learn.split("#", 1)[0]) is not None


def test_reconciled_docs_do_not_repeat_obsolete_inventory_claims():
    paths = (
        "dwarf/profile_manager/data/concepts.py",
        "dwarf/dashboard/templates/learn/developer_onboarding.j2",
        "dwarf/dashboard/templates/learn/operator_runbook.j2",
        "dwarf/README.md",
        "dwarf/docs/spec-reference.md",
        "dwarf/docs/primitives-reference.md",
        "docs/presentation/2026-09-17/deployment-and-feature-audit.md",
        "docs/presentation/2026-09-17/walkthrough-guide.html",
    )
    source = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in paths)

    for stale in (
        "the five current profiles",
        "Nine templates ship",
        "no primitive catalog route exists",
        "| `/operate/primitives` | — | Unavailable |",
        "242 scenario definitions, 13 profiles, 38 target manifests",
        "The primitive registry currently exposes 206 entries",
        "Only scenario template path provides",
    ):
        assert stale.lower() not in source.lower()


def test_asset_guidance_states_boundaries_and_export_provenance():
    readme = (ROOT / "dwarf/README.md").read_text(encoding="utf-8")
    runbook = (ROOT / "dwarf/dashboard/templates/learn/operator_runbook.j2").read_text(
        encoding="utf-8"
    )
    examples = (ROOT / "dwarf/dashboard/templates/learn/examples.j2").read_text(
        encoding="utf-8"
    )

    for source in (readme, runbook):
        assert "DWARF-EXPORT-MANIFEST.json" in source
        assert re.search(r"source\s+revision", source, flags=re.IGNORECASE)
        assert re.search(r"source\s+of\s+truth", source, flags=re.IGNORECASE)
    assert "asset_examples" in examples
    assert "Purpose" in examples
    assert "Schema" in examples
    assert "Lifecycle" in examples
    assert "Mutability" in examples
    assert "Relationships" in examples
