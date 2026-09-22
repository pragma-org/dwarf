from pathlib import Path
import re

HTML_PATH = Path("docs/workbench/dwarf-measurement-program-technical-debrief.html")
TITLE = "DWARF Measurement Program — Implementation Overview and Technical Debrief"
RUN_IDS = {
    "20260920T235440Z-050046a4", "20260920T132629Z-ea000d37",
    "20260921T013953Z-565b77c3", "20260920T135958Z-362eedc7",
    "20260920T072858Z-2cc3bb0c", "20260920T073447Z-ab81bfb7",
    "20260921T035546Z-9747122c", "20260921T021935Z-3b58eafc",
    "20260921T045619Z-15e864a0", "20260921T045807Z-8e2bbb0e",
    "20260921T204537Z-ff5a800a", "20260921T202611Z-27eeadb5",
    "20260921T194749Z-26542a57", "20260921T195334Z-e713d0de",
}
SECTION_IDS = {
    "summary", "traceability", "architecture", "cards", "findings",
    "usage", "inventory", "coverage", "reproducibility", "health-repair",
    "full-metrics", "run-recipes", "next-work", "amaru-guide",
    "cardano-guide", "scenario-demos",
}


def source() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def test_debrief_has_approved_identity_and_information_architecture():
    html = source()
    assert f"<title>{TITLE}</title>" in html
    assert f"<h1>{TITLE}</h1>" in html
    for section_id in SECTION_IDS:
        assert f'id="{section_id}"' in html
        pattern = rf'<section[^>]+id="{section_id}"[\s\S]*?</section>\s*<aside class="child-explanation"'
        assert re.search(pattern, html), f"{section_id} needs a directly following child explanation"
    assert html.count('class="child-explanation"') >= len(SECTION_IDS)


def test_debrief_retains_exact_accepted_evidence_and_claim_limits():
    html = source()
    for run_id in RUN_IDS:
        assert run_id in html
    for value in (
        "b159172f25a9c389f82f20bca4f15e3032791638",
        "d3a6dafcced78f5809a96619e883cf04911d2bdc",
        "fef83fed01d7926f3de83b3b917be5a4a48768b5",
        "10.11.20260912", "11.1.2",
        "whole-microseconds-v1", "nanoseconds-v2", "nanoseconds-v3",
        "completed_with_security_finding", "64-byte",
        "on-chain", "same-height", "canonical progress",
    ):
        assert value in html
    for source_class in ("Stock", "External", "Patched", "Reserved"):
        assert f">{source_class}<" in html
    for status in ("proven", "partial", "unavailable", "deferred"):
        assert f'data-status="{status}"' in html
    assert "not an Amaru-versus-Cardano performance benchmark" in html
    assert "No mixed-node comparison" in html
    assert "No stable release threshold" in html


def test_debrief_documents_plutus_accounting_and_additive_card_six():
    html = source()
    for value in (
        "five frozen cards",
        "additive Card 06",
        "60 attempted",
        "30 accepted",
        "30 expected-invalid",
        "0 timed out",
        "51,300 bytes",
        "50,820 bytes",
        "60 duration samples",
        "35 attempted",
        "5 rejected",
        "35 duration samples",
        "signed simple payment",
        "submit-to-protocol-response",
        "mempool visibility",
        "chain adoption",
        "unavailable rather than zero",
        "not an automatic Amaru-versus-Cardano benchmark",
        "555c9c06…0e466",
        "318bc045…992e6",
        "3242de23…ee67",
        "feafaf46…128c1",
    ):
        assert value in html
    assert ">Five cards<" not in html
    assert "Five card contracts" not in html


def test_debrief_links_inventory_and_workbench_evidence():
    html = source()
    paths = (
        "dwarf/profiles/", "dwarf/scenarios/", "dwarf/primitives/",
        "dwarf/measurements/", "dwarf/targets/", "dwarf/spec/v1/",
        "dwarf/docs/client-examples/contracts/", "tests/",
        "dwarf/dashboard/", "/operate/runs/",
    )
    for path in paths:
        assert path in html
    for object_id in (
        "obj_8b15834b9a2047d69f9b661a",
        "obj_1463810836b54852b24b2ef2",
        "obj_48a4b0c0bc0441bfab10dab7",
        "obj_eb40eb62fdd24c539d85d818",
        "obj_110d143967b546d98179e595",
    ):
        assert object_id in html


def test_debrief_uses_canonical_dashboard_urls_without_loopback_hosts():
    html = source()
    lowered = html.lower()
    assert "127.0.0.1" not in lowered
    assert "localhost" not in lowered
    assert not re.search(r"https?://(?:\[?::1\]?|127(?:\.\d{1,3}){3})(?=[:/])", lowered)

    dashboard_hrefs = re.findall(
        r'href="([^"]+/(?:learn|operate)(?:/[^"#?]*)?)"', html
    )
    assert dashboard_hrefs
    assert all(
        href.startswith("https://dwarf.gainpalfam.com/")
        for href in dashboard_hrefs
    )


def test_debrief_has_accessible_implementation_filtering_contract():
    html = source()
    assert 'aria-label="Filter by node implementation"' in html
    assert 'data-implementation-filter="all"' in html
    assert 'data-implementation-filter="amaru"' in html
    assert 'data-implementation-filter="cardano-node"' in html
    assert 'id="implementation-status"' in html
    assert "implementation-unit" in html
    assert 'data-implementation="amaru"' in html
    assert 'data-implementation="cardano-node"' in html
    assert "activeImplementation" in html
    assert "implementationMatch" in html
    assert "aria-pressed" in html


def test_debrief_represents_all_thirty_measurement_definitions():
    html = source()
    measurement_ids = sorted(path.stem for path in Path("dwarf/measurements").glob("*.yaml"))
    measurement_profiles = sorted(
        path.stem for path in Path("dwarf/measurement-profiles").glob("*.yaml")
    )
    assert len(measurement_ids) == 30
    assert len(measurement_profiles) == 4
    for measurement_id in measurement_ids:
        assert f">{measurement_id}<" in html
        definition = Path("dwarf/measurements", f"{measurement_id}.yaml").read_text(
            encoding="utf-8"
        )
        title = next(
            line.removeprefix("title: ")
            for line in definition.splitlines()
            if line.startswith("title: ")
        )
        assert title.casefold() in html.casefold()
    for profile_id in measurement_profiles:
        assert f">{profile_id}<" in html
    assert html.count('class="tap-card') == 30
    assert "15 Amaru definitions" in html
    assert "15 Cardano-node definitions" in html
    assert "14 implemented and profile-selectable" in html
    assert "12 implemented and profile-selectable" in html
    assert "1 reserved or unprofiled" in html
    assert "3 reserved or unprofiled" in html
    for source_badge in ("Stock", "External", "Patched", "Reserved"):
        assert f'<span class="source-badge">{source_badge}</span>' in html
    for label in ("Measures", "Observation boundary", "Surface", "Exact limitation", "Evidence state"):
        assert f"<dt>{label}</dt>" in html
    for limitation in (
        "no authoritative Amaru protocol-response boundary",
        "no shared-clock Amaru chain-adoption duration",
        "mempool-visibility timing is unavailable",
        "A process-resource tap is not a mini-protocol measurement",
        "Configured is not exercised",
        "Unavailable is not zero",
    ):
        assert limitation in html


def test_debrief_classifies_live_demonstrations_and_exact_run_recipes():
    html = source()
    supported = {
        "client-example-cbor-decoding-amaru-d3a6dafc-regression",
        "client-example-cbor-decoding-cardano-patched",
        "client-example-plutus-vm-amaru-onchain-v2",
        "client-example-plutus-vm-cardano",
        "client-example-invalid-mini-protocol-amaru",
        "client-example-invalid-mini-protocol-cardano",
        "client-example-block-application-amaru-canonical-v3",
        "client-example-block-application-cardano-canonical-v2",
        "client-example-restart-recovery-sync-amaru",
        "client-example-restart-recovery-sync-cardano",
        "client-example-simple-transfer-amaru",
        "client-example-simple-transfer-cardano",
    }
    for scenario_id in supported:
        assert scenario_id in html
        assert f"https://dwarf.gainpalfam.com/operate/scenarios/{scenario_id}" in html
    for value in (
        "Ready on the currently active topology now",
        "No scenario is ready now",
        "Ready after an explicit supported profile redeployment through /run",
        "Do not use for the live demonstration",
        "profile-w-amaru-measurement-plutus-v2",
        "active_profile_readiness_failed",
        "Process, socket, or listener counts do not match the active profile.",
        "Best Amaru demo",
        "Best Cardano demo",
        "profile-y-amaru-block-application-nanoseconds-v3",
        "profile-v-cardano-measurement-nanoseconds-v2",
        "amaru-security-patched",
        "cardano-security-patched",
        "Scenario passed previously",
        "does not mean ready on this active deployment now",
    ):
        assert value in html
    for step in range(1, 11):
        assert html.count(f"Step {step:02d} —") >= 2
    assert html.count("Start local run") >= 2


def test_debrief_has_accessible_dependency_free_interactions():
    html = source()
    assert '<a class="skip-link" href="#main">' in html
    assert 'id="debrief-search"' in html
    assert 'aria-label="Filter evidence by status"' in html
    assert 'aria-live="polite"' in html
    assert 'id="expand-all"' in html and 'id="collapse-all"' in html
    assert "event.key === '/'" in html
    assert "event.key === 'Escape'" in html
    assert "event.altKey" in html and "Digit[1-4]" in html
    assert "URLSearchParams" not in html
    assert "<script src=" not in html
    assert "@media print" in html
    assert "@media(max-width:760px)" in html.replace(" ", "")
    assert "overflow-x:clip" in html.replace(" ", "")
    assert "overflow-x:auto" in html.replace(" ", "")
    assert "details[open]" in html
    assert "h1{overflow-wrap:anywhere" in html.replace(" ", "")
    assert "scroll-margin-top:190px" in html.replace(" ", "")


def test_debrief_documents_health_repair_metric_limits_and_exact_recipes():
    html = source()
    for value in (
        "amaru_relay_stalled",
        "c19ea75954f5e48206e49caec948c8dc6f1416e6",
        "sha256:929406cf125af6174022a90d0372b3a485d0eee74890f0aa00b404d24f469495",
        "profile-v-cardano-measurement-nanoseconds-v2",
        "8 of 14 configured collectors",
        "8 of 12 configured collectors",
        "all collectors configured",
        "all metrics exercised",
        "client-example-block-application-amaru-canonical-v3",
        "client-example-cbor-decoding-cardano-patched",
        "Start local run",
        "supported-unconfirmed",
        "Do not launch Antithesis",
    ):
        assert value in html


def test_full_metrics_audit_maps_every_implemented_measurement():
    audit = Path("docs/measurement-full-metrics-compatibility-audit.md").read_text(
        encoding="utf-8"
    )
    measurement_ids = {
        path.stem
        for path in Path("dwarf/measurements").glob("*.yaml")
    }
    assert len(measurement_ids) == 30
    for measurement_id in measurement_ids:
        assert f"`{measurement_id}`" in audit
    for value in (
        "Non-vacuous exercise rule",
        "Configured is not exercised",
        "20260921T035546Z-9747122c",
        "20260920T132629Z-ea000d37",
        "Step 1 — Scenario",
        "Step 10 — Run",
    ):
        assert value in audit
