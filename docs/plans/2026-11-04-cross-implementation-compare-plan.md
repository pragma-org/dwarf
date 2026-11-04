# Cross-implementation Measurement Compare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the offline Pages viewer compare picked runs from different workloads on concept-keyed rows (Amaru and cardano-node on one card, one bar per build), with the raw metric id on hover, from a complete catalog that is the single source for both the live cards and the embedded viewer index.

**Architecture:** The catalog `presentation-v1.json` gains direction, unit, `compare_as`/equivalence, boundary fields and the unit/label tables; `measurement_presentation.py` validates and uses them for live cards; `measurement_compare._catalog_index` emits a viewer index with concept bindings; a generator embeds that index into both viewer HTML copies. In the viewer, the `portable-store.js` block gets a `runs` filter, mode-aware build groups, a unit-aware `describe` and a `catalogConcept` hook that keys rows by concept when runs are picked; the `portable-compare.js` block gets the Pick-runs control, pick strip and hover tooltip.

**Tech Stack:** Python 3 (stdlib, pytest), vanilla ES5-style JavaScript inlined in `docs/index.html`, Node `vm` tests, Playwright render test.

**Spec:** `docs/plans/2026-11-04-cross-implementation-compare-design.md`

## Global Constraints

- Repo `/Users/nigel/dwarf-project/dwarf-fable/V10-FRESH`, branch `feat/cross-implementation-compare`. Commit author `JG <forky@1timeuse.com>` (set `GIT_AUTHOR_*`/`GIT_COMMITTER_*` on every commit). No push.
- Python tests: `cd <repo> && PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider <files>`.
- Node tests: `cd <repo> && node tests/browser/<file>.cjs`. Playwright: `NODE_PATH=/Users/nigel/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules` (playwright 1.62.1); browsers via `DWARF_BROWSER_CHANNEL=chrome` when `/Applications/Google Chrome.app` exists, else `node $NODE_PATH/playwright/cli.js install chromium-headless-shell` once.
- `docs/index.html` is the only viewer source (no build step). After every edit to it, regenerate `/index.html` with the exact transform of `tests/test_campaign_pages_workflow.py::test_root_viewer_matches_docs_viewer_with_relative_asset_paths` (three `replace` calls: `src="./antithesis-import.js"`→`src="./docs/antithesis-import.js"`, `src="./portable-defaults.js"`→`src="./docs/portable-defaults.js"`, `data-dwarf-default-runs="./evidence/default-runs.json"`→`…"./docs/evidence/default-runs.json"`), and run that test.
- Viewer text from evidence is set with `textContent` only, never HTML.
- Live run-page card titles and concept ids must not change (design §2, §5.4). No "different clock" wording anywhere.
- Group key for bars: `implementation|version|mode|source_revision|image_digest`; labels show the mode.
- Companion ids: regex `_by_[a-z_]*outcome$`. Approximate rows: `comparison_direction` neutral, no preferred highlight.
- Run archives for fixtures: `/private/tmp/claude-501/-Users-nigel-dwarf-project-dwarf-v4/0d1bdaff-3ff1-4cb0-acec-ed645746aea6/scratchpad/exports-workload/workload-run-metric-ids.json` (ids and units of the four 2026-11-04 reports; regenerate from `_x/<run>/**/measurements/report.json` if missing).

## Review Focus

1. A pick that names a run id no longer in the store (removed import, excluded evidence): the comparison must render zero rows and the summary "0 runs picked", not throw. Test in Task 5.
2. The same raw id with different units across builds (`header_validation` count on stock, `…patched-header-validation.header_validation` us on patched): two rows, never one. Test in Task 5.
3. A verdict dict with a non-numeric member (`{"accepted": 3, "detail": {...}}`): not summed, cell stays unavailable. Test in Task 1.
4. Antithesis records mixed into a pick: the Antithesis hook still owns their rows (keyed with scenario and mode), so they never merge with devnet rows. Test in Task 5.
5. Hand-edited embedded catalog: `render_viewer_presentation_catalog.py --check` must fail with a non-zero exit naming the file. Test in Task 4.

---

### Task 1: Catalog schema fields, unit tables, verdict sums, companion rule

**Files:**
- Modify: `dwarf/measurements/presentation-v1.json` (top-level keys; every concept gains `comparison_direction`)
- Modify: `dwarf/profile_manager/measurement_presentation.py:19-51` (validation), `:163-181` (`_primary`), `:282-300` (`_fallback_card`), `:334-381` (`build_measurement_presentation`)
- Test: `tests/test_measurement_presentation.py`

**Interfaces:**
- Produces: catalog top-level `implementation_labels: dict`, `unit_short: dict`, `unit_full: dict` (moved verbatim from the embedded payload in `docs/index.html` line ~9830); concept optional fields `comparison_direction ∈ {lower,higher,neutral}`, `unit: str`, `compare_as: str`, `equivalence ∈ {exact,approximate}`; binding optional fields `boundary: str`, `unit_equivalent: bool`.
- Produces: `is_companion_metric(metric_id: str) -> bool` (regex `_by_[a-z_]*outcome$`), `canonical_unit(unit: str, catalog: dict) -> str` (lower-cased `unit_short` lookup, else the input), `sum_verdict_dict(value) -> float | None` (sum when `value` is a non-empty dict whose values are all `int|float` and not `bool`, else `None`); all public in `measurement_presentation.py`.
- Produces: `_primary` returns the verdict sum for `count` and `scalar` metrics whose raw value is a verdict dict; `_fallback_card` does the same; `build_measurement_presentation` skips companions of consumed ids via `is_companion_metric` and filters a binding's present ids by `concept["unit"]` through `canonical_unit` when the concept declares a unit.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_measurement_presentation.py`)

```python
import copy, json, pytest
from pathlib import Path
from profile_manager.measurement_presentation import (
    canonical_unit, is_companion_metric, sum_verdict_dict,
)
CATALOG_PATH = Path(__file__).resolve().parents[1] / "dwarf/measurements/presentation-v1.json"

def _catalog_with(mutate):
    catalog = json.loads(CATALOG_PATH.read_text())
    mutate(catalog)
    path = Path(tempfile.mkdtemp()) / "catalog.json"
    path.write_text(json.dumps(catalog)); return path

def test_catalog_carries_unit_and_label_tables_and_directions():
    catalog = load_measurement_presentation_catalog()
    assert catalog["implementation_labels"] == {"amaru": "Amaru", "cardano-node": "Cardano"}
    assert catalog["unit_short"]["microseconds"] == "us" and catalog["unit_full"]["us"] == "microseconds"
    directions = {c["id"]: c.get("comparison_direction", "neutral") for c in catalog["concepts"]}
    assert directions["block-application-time"] == "lower"
    assert directions["chain-sync-speed"] == "higher"
    assert directions["mempool-transaction-count"] == "neutral"

@pytest.mark.parametrize("mutate,message", [
    (lambda c: c["concepts"][0].__setitem__("comparison_direction", "best"), "comparison_direction"),
    (lambda c: c["concepts"][0].__setitem__("equivalence", "close"), "equivalence"),
    (lambda c: c["concepts"][0].__setitem__("unit", 3), "unit"),
    (lambda c: c["concepts"][0].__setitem__("compare_as", "no-such-concept"), "compare_as"),
    (lambda c: (c["concepts"][0].__setitem__("compare_as", c["concepts"][1]["id"]),
                c["concepts"][1].__setitem__("compare_as", c["concepts"][2]["id"])), "compare_as"),
    (lambda c: c["concepts"][0]["bindings"][0].__setitem__("unit_equivalent", "yes"), "unit_equivalent"),
    (lambda c: c.pop("unit_short"), "unit_short"),
])
def test_catalog_rejects_invalid_new_fields(mutate, message):
    with pytest.raises(ValueError, match=message):
        load_measurement_presentation_catalog(_catalog_with(mutate))

def test_companion_rule_and_unit_canonicalisation():
    assert is_companion_metric("block_application_by_outcome")
    assert is_companion_metric("protocol_decode_by_decode_outcome")
    assert is_companion_metric("handshake_state_by_outcome")
    assert not is_companion_metric("block_application")
    catalog = load_measurement_presentation_catalog()
    assert canonical_unit("microseconds", catalog) == "us"
    assert canonical_unit("us", catalog) == "us"
    assert canonical_unit("requests", catalog) == "requests"

def test_verdict_dict_sums_only_when_all_members_are_numbers():
    assert sum_verdict_dict({"accepted": 205, "rejected": 0}) == 205
    assert sum_verdict_dict({"accepted": 3, "detail": {"x": 1}}) is None
    assert sum_verdict_dict({}) is None
    assert sum_verdict_dict(7) is None

def test_header_validation_verdict_count_card_reads_the_sum():
    result = build_measurement_presentation(
        {"header_validation": {"status": "available", "unit": "count", "value": {"accepted": 205, "rejected": 0}}},
        _target("amaru", "stock"))
    card = result["cards"][0]
    assert card["primary_value"] == 205
    assert card["raw_metric_ids"] == ["header_validation"]
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_measurement_presentation.py -k "tables_and_directions or rejects_invalid or companion_rule or verdict_dict or verdict_count_card"`
Expected: FAIL / ERROR with `ImportError: cannot import name 'canonical_unit'`.

- [ ] **Step 3: Move the tables and seed directions in `presentation-v1.json`**

One-off script (not committed): load `docs/index.html`, parse the `data-portable-viewer-payload` JSON, copy `presentation_catalog.implementation_labels`, `unit_short`, `unit_full` to top level of the catalog; for each concept set `comparison_direction` to the embedded `by_metric[<first binding id>].comparison_direction` (every concept resolves to exactly one value today; `block-selection-time` → `neutral`). Write with `json.dumps(indent=2)` + trailing newline, preserving concept order.

- [ ] **Step 4: Implement validation and helpers in `measurement_presentation.py`**

`_DIRECTIONS = {"lower","higher","neutral"}`, `_EQUIVALENCES = {"exact","approximate"}`, `_COMPANION = re.compile(r"_by_[a-z_]*outcome$")`. In `load_measurement_presentation_catalog`: require `implementation_labels`, `unit_short`, `unit_full` to be `dict[str,str]`; validate the concept/binding optional fields by type and enum; validate `compare_as` names an existing concept which itself has no `compare_as` (message contains `compare_as`). Add `is_companion_metric`, `canonical_unit`, `sum_verdict_dict`. In `_primary` and `_fallback_card`: when the chosen value is a dict, replace with `sum_verdict_dict(value)`. In `build_measurement_presentation`: `present = [m for m in binding ids if m in metrics and _unit_matches(concept, metrics[m], catalog)]` where `_unit_matches` is true when the concept has no `unit`, or `canonical_unit(raw unit) == concept["unit"]`, or `binding.get("unit_equivalent")`; the companion skip becomes `is_companion_metric(metric_id) and _companion_parent_consumed(metric_id, consumed)` (parent = id with the `_by_…outcome` suffix removed, or the id itself listed in a consumed binding's `metric_ids`).

- [ ] **Step 5: Run the file's tests**

Run: `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_measurement_presentation.py`
Expected: all pass (13 existing + 5 new parametrised groups).

- [ ] **Step 6: Commit**

```bash
git add dwarf/measurements/presentation-v1.json dwarf/profile_manager/measurement_presentation.py tests/test_measurement_presentation.py
git commit -m "feat(catalog): directions, unit tables, compare_as fields and verdict sums"
```

---

### Task 2: Vocabulary — bind every id of the four workload runs

**Files:**
- Modify: `dwarf/measurements/presentation-v1.json`
- Create: `tests/fixtures/workload_run_metric_ids.json` (copy of the scratchpad file named in Global Constraints)
- Test: `tests/test_measurement_presentation.py`

**Interfaces:**
- Consumes: Task 1 fields and helpers.
- Produces: the concepts and bindings of design §5.1–§5.3 exactly (ids, units, directions, `compare_as` targets `chain-advance-count` and `mempool-intake-count` with no bindings, `equivalence: approximate` on `chain-tip-update-count`, `adopted-block-count`, `mempool-received-count`, `transaction-outcome-count`; `unit_equivalent: true` on the cardano bindings of `adopted-block-count` and `transaction-outcome-count`; reserved cardano bindings' `modes` = `["stock","patched"]`; `handler-queue-residence` and `txsubmission-residence` `metric_type` → `distribution`, `primary_statistic` → `median`). Every new binding has a one-sentence `boundary`.

- [ ] **Step 1: Write the failing tests**

```python
FIXTURE = json.loads((Path(__file__).parent / "fixtures/workload_run_metric_ids.json").read_text())

@pytest.mark.parametrize("run_id", sorted(FIXTURE))
def test_every_workload_run_metric_binds_to_a_concept(run_id):
    run = FIXTURE[run_id]
    metrics = {mid: {"status": "available", "unit": run["units"].get(mid) or "events", "value": 1}
               for mid in run["metric_ids"]}
    result = build_measurement_presentation(metrics, _target(run["implementation"], run["mode"]))
    concept_ids = {c["id"] for c in load_measurement_presentation_catalog()["concepts"]}
    fallback = [c for c in result["cards"] if c["concept_id"] not in concept_ids]   # _fallback_card derives its id from the raw id
    assert fallback == [], [c["raw_metric_ids"] for c in fallback]
    retained = {raw for c in result["cards"] for raw in c["raw_metric_ids"]}
    assert retained == set(run["metric_ids"])   # companions must be listed in a binding, not merely skipped

def test_depth_gauges_route_by_unit():
    metrics = {"pending_depth": {"status": "available", "unit": "requests", "median": 0, "sample_count": 9},
               "amaru-patched-txsubmission-residence.pending_depth": {"status": "available", "unit": "transactions", "median": 0, "sample_count": 4}}
    result = build_measurement_presentation(metrics, _target("amaru", "patched"))
    assert _card(result, "blockfetch-pending-depth")["raw_metric_ids"] == ["pending_depth"]
    assert _card(result, "txsubmission-pending-depth")["raw_metric_ids"] == ["amaru-patched-txsubmission-residence.pending_depth"]

def test_approximate_pairs_keep_live_titles_and_point_at_compare_targets():
    catalog = load_measurement_presentation_catalog()
    concepts = {c["id"]: c for c in catalog["concepts"]}
    assert concepts["adopted-block-count"]["title"] == "Blocks adopted"
    assert concepts["adopted-block-count"]["compare_as"] == "chain-advance-count"
    assert concepts["adopted-block-count"]["equivalence"] == "approximate"
    assert concepts["transaction-outcome-count"]["compare_as"] == "mempool-intake-count"
    assert concepts["chain-advance-count"]["bindings"] == [] and concepts["chain-advance-count"]["unit"] == "events"
    assert concepts["mempool-intake-count"]["comparison_direction"] == "neutral"
    for cid in ("txsubmission-pending-depth", "blockfetch-inflight-depth"):
        assert concepts[cid]["comparison_direction"] == "neutral"
    assert concepts["protocol-decode-time"]["bindings"][0]["implementation"] in {"amaru", "cardano-node"}
    assert {b["implementation"] for b in concepts["protocol-decode-time"]["bindings"]} == {"amaru", "cardano-node"}
    assert {b["implementation"] for b in concepts["header-validation-verdicts"]["bindings"]} == {"amaru", "cardano-node"}
    assert all(b.get("boundary") for c in catalog["concepts"] for b in c["bindings"] if c["id"] in {"header-validation-verdicts", "protocol-decode-time", "blockfetch-handler-time"})
```
Also extend `test_acceptance_run_metric_inventory_has_canonical_client_metadata` with a third row `("amaru", "patched", amaru_ids)`, and extend `test_cardano_stock_counts_and_reserved_internal_boundaries_keep_true_sources` with a `_target("cardano-node", "stock")` call asserting both reserved cards still have `status == "reserved"`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_measurement_presentation.py -k "workload_run_metric or route_by_unit or approximate_pairs or inventory or reserved_internal"`
Expected: FAIL; the fixture-driven test lists unbound ids such as `submit_to_mempool_visibility`, `handler`, `header_validation`.

- [ ] **Step 3: Add the concepts and bindings to `presentation-v1.json`** per design §5.1, §5.2, §5.3 (ids, units, directions, modes as tabulated; `*` bindings use `modes: ["stock","patched"]`, source `external`; new patched bindings source `patched`; `header-validation-verdicts` source `stock`, `unit: "count"`, `metric_type: count`; `header-validation-time` `unit: "us"`). Write the `boundary` sentences from the collector facts (e.g. amaru `handler`: "Amaru BlockFetch handler service time per completed fetch event"; cardano `handler_service`: "cardano-node addFetchedBlock service time inside a merged BlockFetch request").

- [ ] **Step 4: Run the file's tests**

Run: `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_measurement_presentation.py`
Expected: all pass, four fixture runs green.

- [ ] **Step 5: Commit**

```bash
git add dwarf/measurements/presentation-v1.json tests/fixtures/workload_run_metric_ids.json tests/test_measurement_presentation.py
git commit -m "feat(catalog): bind every operator-workload metric; cross-implementation pairs"
```

---

### Task 3: Python compare model parity

**Files:**
- Modify: `dwarf/profile_manager/data/measurement_compare.py:36-70, 100-160, 404-431, 574-590, 641-800`
- Test: `tests/test_measurement_compare.py` (new)

**Interfaces:**
- Consumes: Task 1 helpers (`is_companion_metric`, `canonical_unit`, `sum_verdict_dict`).
- Produces: `GROUP_IDENTITY_FIELDS = IDENTITY_FIELDS + ("mode",)` used by `group_key` and `_group_label` (label `"<impl> <version> · <mode> · <rev12> · <digest19>"`); `IDENTITY_FIELDS`/`SCENARIO_TARGET_IDENTITY_FIELDS` unchanged. `FILTER_KEYS` gains `"runs"` (comma-separated run ids). `_catalog_index(catalog) -> {by_metric, categories, order, concepts, bindings, implementation_labels, unit_short, unit_full}` with `concepts[id] = {title, category, comparison_direction, unit, metric_type, compare_as, equivalence}` and `bindings[impl][raw_id] = [{concept, modes, boundary, unit_equivalent, primary}]`. `describe_metric(index, record, metric_id, row) -> {title, category, comparison_direction}` (unit-aware). `catalog_concept(index, record, metric_id, row) -> dict | None` with keys `id, title, category, comparison_direction, boundary, approximate, raw_unit, source_concept, key` (`key = json.dumps(["catalog-concept-v1", row_concept, unit], separators=(",",":"))`). In `build_measurement_comparison`: when `filters["runs"]`, records outside the list are dropped, the `scenario` predicate is ignored, `focus_required` is False, rows key by `catalog_concept(...)["key"]` when it returns a mapping (`concept_grouped: True`, `approximate`, `fingerprint_parts = {"concept", "runtime", "unit"}`), baseline deltas apply to them, disagreement detection skips approximate rows only.

- [ ] **Step 1: Write the failing tests** (`tests/test_measurement_compare.py`)

```python
import json
from profile_manager.data.measurement_compare import (
    _catalog_index, _metric_cell, build_measurement_comparison, group_key, _group_label,
    catalog_concept, describe_metric, FILTER_KEYS,
)

def _record(run_id, impl, mode, scenario, metrics, **extra):
    base = {"run_id": run_id, "implementation": impl, "version": "v1", "source_revision": "a" * 40,
            "image_digest": "sha256:" + ("c" if mode == "patched" else "d") * 64, "mode": mode,
            "scenario_id": scenario, "scenario_behavior_sha256": "s" * 64, "runtime": "devnet",
            "measurement_profile_id": f"{impl}-{mode}", "measurement_profile_digest": "p" * 64,
            "collector_digests": ["k" * 64], "collector_missing": [], "profile_id": "profile-x", "profile_sha256": "q" * 64,
            "report_schema_version": "v1", "provenance_state": "complete", "identity_missing": [], "identity_source": "report",
            "exit_status": "pass", "verdict": "pass", "ended_at": "2026-11-04T01:00:00Z", "evidence_state": "verified",
            "sources": [], "session_order": 0, "metrics": {k: _metric_cell(k, v) for k, v in metrics.items()}}
    base.update(extra); return base

US = lambda median: {"status": "available", "unit": "us", "median": median, "sample_count": 10}

def test_metric_cell_sums_verdict_dicts():
    cell = _metric_cell("header_validation", {"status": "available", "unit": "count", "value": {"accepted": 205, "rejected": 0}})
    assert cell["primary_value"] == 205 and cell["positive_evidence"]

def test_group_identity_includes_mode():
    stock = _record("r1", "amaru", "stock", "w", {}); patched = _record("r2", "amaru", "patched", "w", {}, image_digest=stock["image_digest"])
    assert group_key(stock) != group_key(patched)
    assert " · stock" in _group_label(stock, "exact") and " · patched" in _group_label(patched, "exact")

def test_catalog_index_emits_concepts_and_binding_lists():
    index = _catalog_index()
    assert index["concepts"]["keepalive-round-trip-time"]["comparison_direction"] == "lower"
    assert [b["concept"] for b in index["bindings"]["cardano-node"]["keepalive_rtt"]] == ["keepalive-round-trip-time"]
    assert {b["concept"] for b in index["bindings"]["amaru"]["pending_depth"]} == {"blockfetch-pending-depth", "txsubmission-pending-depth"}
    assert index["unit_short"]["microseconds"] == "us" and index["implementation_labels"]["amaru"] == "Amaru"

def test_describe_is_unit_aware():
    index = _catalog_index(); rec = _record("r", "amaru", "patched", "w", {})
    assert describe_metric(index, rec, "pending_depth", {"unit": "requests"})["title"] == "BlockFetch pending depth"
    assert describe_metric(index, rec, "pending_depth", {"unit": "transactions"})["title"] == "TxSubmission pending depth"

def test_runs_filter_merges_implementations_on_concept_rows():
    amaru = _record("a1", "amaru", "stock", "amaru-measurement-workload-stock-portable", {"keepalive_round_trip": US(585), "chain_tip_updates": {"status": "available", "unit": "events", "value": 160}})
    cardano = _record("c1", "cardano-node", "stock", "cardano-measurement-workload-stock-portable", {"keepalive_rtt": US(569.5), "adopted_blocks": {"status": "available", "unit": "blocks", "value": 173}}, session_order=1)
    gated = build_measurement_comparison([amaru, cardano], filters={})
    assert gated["focus_required"] is True and gated["rows"] == []
    picked = build_measurement_comparison([amaru, cardano], filters={"runs": "a1,c1"})
    assert picked["focus_required"] is False and len(picked["groups"]) == 2
    rows = {r["metric_id"]: r for r in picked["rows"]}
    ka = rows["keepalive-round-trip-time"]
    assert ka["concept_grouped"] and ka["comparison_direction"] == "lower" and not ka["approximate"]
    assert sorted(c["value"] for c in ka["cells"].values()) == [569.5, 585]
    assert {c["metric_id"] for cell in ka["cells"].values() for c in cell["contributors"]} == {"keepalive_round_trip", "keepalive_rtt"}
    adv = rows["chain-advance-count"]
    assert adv["approximate"] and adv["comparison_direction"] == "neutral" and adv["unit"] == "events"
    assert sorted(c["value"] for c in adv["cells"].values()) == [160, 173]
    assert "runs" in FILTER_KEYS
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_measurement_compare.py`
Expected: `ImportError: cannot import name 'catalog_concept'`.

- [ ] **Step 3: Implement** in `measurement_compare.py`: `GROUP_IDENTITY_FIELDS`; `group_key` joins `GROUP_IDENTITY_FIELDS`; `_group_label` inserts `record["mode"]` after the version; `FILTER_KEYS += ("runs",)`; `_metric_cell` applies `sum_verdict_dict` when `value` is a dict (before `_number`); `_catalog_index` builds the extended index (`primary` = first non-companion id of the binding); `describe_metric`, `catalog_concept` per the Interfaces block; `_record_matches` honours `runs` (split on `,`, strip) and skips `scenario` when `runs` is set; `build_measurement_comparison` sets `focus_required = not filters["scenario"] and not filters["runs"] and len(scenario_links) > 1`, keys rows by concept when `filters["runs"]` and `catalog_concept` returns a mapping, adds `approximate` to rows, and lets baseline deltas run on concept rows while skipping disagreement detection only when `row["approximate"]`.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_measurement_compare.py tests/test_measurement_presentation.py tests/test_operate_primitives.py tests/test_version_pinned_cbor_primitives.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/data/measurement_compare.py tests/test_measurement_compare.py
git commit -m "feat(compare): runs filter, concept-keyed rows and mode-aware build groups in the Python model"
```

---

### Task 4: Generator for the embedded viewer catalog

**Files:**
- Create: `dwarf/scripts/render_viewer_presentation_catalog.py`
- Modify: `docs/index.html` (payload JSON only), `index.html` (same)
- Test: `tests/test_render_viewer_presentation_catalog.py` (new)

**Interfaces:**
- Consumes: `measurement_compare._catalog_index`.
- Produces: `render_payload_catalog(index: dict) -> dict` (the `presentation_catalog` value: `by_metric`, `categories`, `concepts`, `bindings`, `implementation_labels`, `unit_short`, `unit_full`); `rewrite_viewer(path: Path, catalog: dict, *, now: str) -> bool` (replaces `presentation_catalog` and `generated_at_utc` inside the `<script type="application/json" data-portable-viewer-payload>…</script>` block, keeps every other payload key, returns whether the file changed; JSON written with `json.dumps(payload, separators=(", ", ": "))` on one line as today); CLI `python3 dwarf/scripts/render_viewer_presentation_catalog.py [--check] [--root PATH]` acting on `docs/index.html` and `index.html`, exit 1 on `--check` drift printing the path.

- [ ] **Step 1: Write the failing tests**

```python
import json, re, shutil, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "dwarf/scripts/render_viewer_presentation_catalog.py"
PAYLOAD = re.compile(r'<script type="application/json" data-portable-viewer-payload>(.*?)</script>', re.S)

def _payload(path): return json.loads(PAYLOAD.search(path.read_text()).group(1))

def _copy(tmp_path):
    for name in ("docs/index.html", "index.html"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True); shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path

def test_generator_is_idempotent_and_preserves_other_payload_keys(tmp_path):
    root = _copy(tmp_path); before = _payload(root / "docs/index.html")
    subprocess.run([sys.executable, SCRIPT, "--root", root], check=True, env={"PYTHONPATH": str(ROOT / "dwarf")})
    after = _payload(root / "docs/index.html")
    assert set(after) == set(before) and after["runs"] == before["runs"] and after["filters"] == before["filters"]
    assert after["presentation_catalog"]["bindings"]["cardano-node"]["keepalive_rtt"][0]["concept"] == "keepalive-round-trip-time"
    assert after["presentation_catalog"]["concepts"]["chain-advance-count"]["compare_as"] is None
    first = (root / "docs/index.html").read_text()
    subprocess.run([sys.executable, SCRIPT, "--root", root], check=True, env={"PYTHONPATH": str(ROOT / "dwarf")})
    assert (root / "docs/index.html").read_text().replace(after["generated_at_utc"], "") == first.replace(after["generated_at_utc"], "")
    assert _payload(root / "index.html")["presentation_catalog"] == after["presentation_catalog"]

def test_check_mode_detects_hand_edits(tmp_path):
    root = _copy(tmp_path)
    subprocess.run([sys.executable, SCRIPT, "--root", root], check=True, env={"PYTHONPATH": str(ROOT / "dwarf")})
    text = (root / "docs/index.html").read_text().replace('"keepalive-round-trip-time"', '"keepalive-round-trip-tim"', 1)
    (root / "docs/index.html").write_text(text)
    result = subprocess.run([sys.executable, SCRIPT, "--root", root, "--check"], capture_output=True, text=True, env={"PYTHONPATH": str(ROOT / "dwarf")})
    assert result.returncode == 1 and "docs/index.html" in result.stdout + result.stderr

def test_committed_viewers_match_the_catalog():
    result = subprocess.run([sys.executable, SCRIPT, "--check"], capture_output=True, text=True, env={"PYTHONPATH": str(ROOT / "dwarf")})
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_render_viewer_presentation_catalog.py`
Expected: FAIL (script missing → `FileNotFoundError`/non-zero).

- [ ] **Step 3: Implement the script** per the Interfaces block (stdlib only; `--check` compares the would-be output's `presentation_catalog` with the current one, ignoring `generated_at_utc`).

- [ ] **Step 4: Run the generator on the real viewers, then the tests**

Run: `PYTHONPATH=dwarf python3 dwarf/scripts/render_viewer_presentation_catalog.py && PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_render_viewer_presentation_catalog.py tests/test_campaign_pages_workflow.py && node tests/browser/test_antithesis_import.cjs`
Expected: all pass; the Node test still finds `header_validation_time` concept rows (Antithesis path untouched).

- [ ] **Step 5: Commit**

```bash
git add dwarf/scripts/render_viewer_presentation_catalog.py tests/test_render_viewer_presentation_catalog.py docs/index.html index.html
git commit -m "feat(pages): generate the embedded viewer catalog from presentation-v1.json"
```

---

### Task 5: Viewer compare model — runs filter, concept keying, mode groups

**Files:**
- Modify: `docs/index.html` `portable-store.js` block (lines ~11571–12164: `IDENTITY` 11578, `metricCell` 11610, `groupKey` 11809, `FILTER_KEYS` 11877, `recordMatches` 11881, `buildComparison` 11911–12062), then regenerate `index.html`
- Test: `tests/browser/test_portable_compare_concepts.cjs` (new)

**Interfaces:**
- Consumes: the embedded `presentation_catalog` from Task 4 (`concepts`, `bindings`, `unit_short`, `implementation_labels`).
- Produces (all inside the `DwarfPortableStore` closure, exported on `api` where named): `GROUP_IDENTITY = IDENTITY.concat(["mode"])`; `groupKey(record, rollup)` joins `GROUP_IDENTITY`; group `label` and `label_short` include ` · <mode>`; group object gains `mode`. `FILTER_KEYS` gains `"runs"` (comma-separated). `recordMatches(record, filters)`: `runs` predicate, scenario ignored when `runs` set. `canonicalUnit(unit, catalog) -> string`; `sumVerdictDict(value) -> number|null` used in `metricCell`; `describe(record, id, metric) -> {title, category, comparison_direction}` unit-aware; `api.catalogConcept(record, id, metric, catalog) -> null | {id, title, category, comparison_direction, boundary, approximate, raw_unit, source_concept, key}` with `key = JSON.stringify(["catalog-concept-v1", rowConcept, unit])`. In `buildComparison`: `mapped = antithesisConcept || (filters.runs ? catalogConcept : null)`; concept rows carry `concept_grouped: true`, `approximate`, `comparison_direction` from the concept, contributors carry `metric_id`, `raw_unit`, `boundary`, `approximate`, `fingerprint`; cells with contributors of differing fingerprints get `mixed_provenance: true`; `focus_required = !filters.scenario && !filters.runs && scenarioLinks.length > 1`; baseline deltas apply to catalog concept rows; disagreements skip rows where `approximate` is true or (as today) Antithesis `concept_grouped` rows; result gains `picked_run_count` and `picked_scenario_count`.

- [ ] **Step 1: Write the failing test** (`tests/browser/test_portable_compare_concepts.cjs`; load `portable-archive`, `portable-detail`, `portable-store` blocks and `docs/antithesis-import.js` as `test_antithesis_import.cjs` does; build records with a `record(runId, impl, mode, scenario, metrics, extra)` helper mirroring Task 3's `_record`, metrics via `storeApi.metricCell` if exported, else literal cells `{status, primary_value, unit, sample_count, positive_evidence}`)

Assertions:
```js
const catalog = JSON.parse(html.match(/data-portable-viewer-payload>([\s\S]*?)<\/script>/)[1]).presentation_catalog;
// gate
let r = storeApi.buildComparison([amaru, cardano], {}, catalog);
assert.equal(r.focus_required, true); assert.equal(r.rows.length, 0);
// picks
r = storeApi.buildComparison([amaru, cardano, amaruPatched], {runs: 'a1,c1,a2', show_unmeasured: '1'}, catalog);
assert.equal(r.focus_required, false); assert.equal(r.groups.length, 3);
assert.deepEqual(r.groups.map(g => g.label_short).sort(), ['Amaru v1 · patched', 'Amaru v1 · stock', 'Cardano v1 · stock']);
const ka = r.rows.find(row => row.metric_id === 'keepalive-round-trip-time');
assert.equal(ka.concept_grouped, true); assert.equal(ka.comparison_direction, 'lower'); assert.equal(ka.approximate, false);
assert.deepEqual(Object.values(ka.cells).map(c => c.value).filter(v => v !== null).sort(), [569.5, 585]);
assert.deepEqual([...new Set(Object.values(ka.cells).flatMap(c => c.contributors.map(x => x.metric_id)))].sort(), ['keepalive_round_trip', 'keepalive_rtt']);
const verdicts = r.rows.find(row => row.metric_id === 'header-validation-verdicts');
assert.deepEqual(Object.values(verdicts.cells).map(c => c.value).filter(v => v !== null).sort(), [127, 205]);
assert.equal(r.rows.filter(row => /header-validation/.test(row.metric_id)).length, 2, 'count and us header rows stay apart');
const adv = r.rows.find(row => row.metric_id === 'chain-advance-count');
assert.equal(adv.approximate, true); assert.equal(adv.comparison_direction, 'neutral'); assert.equal(adv.unit, 'events');
const depth = r.rows.filter(row => row.metric_id === 'txsubmission-pending-depth');
assert.equal(depth.length, 1); assert.equal(depth[0].unit, 'transactions');
assert.ok(r.rows.some(row => row.metric_id === 'blockfetch-pending-depth' && row.unit === 'requests'));
// unit-aware describe without picks
const plain = storeApi.buildComparison([amaruPatched], {scenario: amaruPatched.scenario_id, show_unmeasured: '1'}, catalog);
assert.ok(plain.rows.some(row => row.metric_id === 'pending_depth' && row.title === 'BlockFetch pending depth'));
// unknown pick
const none = storeApi.buildComparison([amaru, cardano], {runs: 'nope'}, catalog);
assert.equal(none.rows.length, 0); assert.equal(none.picked_run_count, 0);
// antithesis record keeps its own hook
const anti = record('x1', 'amaru', 'patched', 'anti', {keepalive_round_trip: us(5)}, {runtime: 'antithesis', derived_import_identity: 'f'.repeat(64)});
const mixed = storeApi.buildComparison([amaru, anti], {runs: 'a1,x1'}, catalog);
assert.equal(mixed.rows.filter(row => /keepalive/.test(row.metric_id)).length, 2);
```
Fixture metrics: amaru stock `keepalive_round_trip` 585 us, `header_validation` count `{accepted:205,rejected:0}`, `chain_tip_updates` 160 events; cardano stock `keepalive_rtt` 569.5 us, `header_validation` count `{accepted:127,rejected:0}`, `adopted_blocks` 173 blocks; amaru patched `pending_depth` 0 requests, `amaru-patched-txsubmission-residence.pending_depth` 0 transactions (positive evidence via `sample_count`), `amaru-patched-header-validation.header_validation` 40 us.

- [ ] **Step 2: Run to verify failure**

Run: `node tests/browser/test_portable_compare_concepts.cjs`
Expected: AssertionError on `focus_required`/`groups.length` or `TypeError` for `catalogConcept`.

- [ ] **Step 3: Implement in the `portable-store.js` block** per the Interfaces block. Keep `IDENTITY` unchanged for `coherentIdentity`. `describe` signature change: update the two callers in `buildComparison` (options loop and rows loop). Export `metricCell`, `catalogConcept`, `canonicalUnit` on `api`.

- [ ] **Step 4: Regenerate `index.html`, run tests**

Run: `python3 - <<'EOF'` (apply the three replacements from Global Constraints to produce `index.html`) `EOF` then `node tests/browser/test_portable_compare_concepts.cjs && node tests/browser/test_antithesis_import.cjs && PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_campaign_pages_workflow.py tests/test_render_viewer_presentation_catalog.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add docs/index.html index.html tests/browser/test_portable_compare_concepts.cjs
git commit -m "feat(viewer): concept-keyed cross-implementation rows behind a runs filter"
```

---

### Task 6: Viewer UI — Pick runs, pick strip, mode pills, hover source

**Files:**
- Modify: `docs/index.html` `portable-compare.js` block (lines ~12523–12964: `buildLabel` 12810, `renderCoverage` 12792, `renderOverview` 12815, `buildResult` 12887, `refresh` 12915, controls 12947), CSS near lines 9552–9660 (`.mc-*`), then regenerate `index.html`
- Test: `tests/browser/test_portable_compare_picks_browser.cjs` (new, Playwright)

**Interfaces:**
- Consumes: Task 5 result fields (`picked_run_count`, `picked_scenario_count`, `row.approximate`, group `mode`, contributor `metric_id`/`raw_unit`/`boundary`/`approximate`), `api.scenarioLabel` if exported (else the local `scenarioLabel`).
- Produces: `filters.runs` driven by a `<details class="mc-disclosure mc-pick-runs" data-compare-pick-runs>` in `controls` right after the Workload select: summary "Pick runs", body = per-workload `<fieldset>` with `<legend>` = workload label, one `<label><input type="checkbox" data-compare-pick="<run_id>"> <impl label> <version> <mode> · <run_id></label>` per included eligible run, and a "Clear picks" button (`data-compare-pick-clear`). Changing a checkbox sets `filters.runs` (comma-joined, session order), resets `page`/`groupPage`, calls `recompute(true)`. While `filters.runs` is set: `filterNodes.scenario.disabled = true` with placeholder text "Picked runs"; the Workloads disclosure shows a pick strip (`.mc-picks`, one `.pill.mc-pick` per run with a "×" button `data-compare-unpick`) instead of workload pills; a `<p class="mc-note" data-compare-pick-summary>` reads `N runs picked across M workloads · concept rows`. `buildLabel(group)` appends `<span class="mc-pill mc-pill--mode">stock|patched</span>`. `preferredGroups(row, groups)` returns an empty set when `row.approximate`. Every `li.mc-bar` and every coverage `td` gets `tabindex="0"` and a `<span class="mc-bar__source" role="tooltip">` whose text is `<Impl label> · <raw id>[, <raw id>] · <raw unit>[ · <boundary>]`, prefixed `≈ approximate pair · ` when the contributor is approximate; CSS hides it (`display:none`) except on `.mc-bar:hover > .mc-bar__source, .mc-bar:focus-within > .mc-bar__source` (and the `td` equivalents), positioned absolute above the bar, `max-width: 60ch`, `z-index: 6`, theme colours from existing `--dk-*` tokens.

- [ ] **Step 1: Write the failing Playwright test** (`tests/browser/test_portable_compare_picks_browser.cjs`): serve `docs/` with `python3 -m http.server` on a free port as `test_antithesis_import_browser.cjs` does, open `/index.html#compare`, wait for `[data-compare-pick-runs]`; import the two fixture archives built in-test with the `tar()`/`file()` helpers (amaru `amaru-measurement-workload-stock-portable` with `keepalive_round_trip`, `header_validation`; cardano `cardano-measurement-workload-stock-portable` with `keepalive_rtt`, `header_validation`) via the Import drop input (`[data-import-tarball]` `setInputFiles`), then:

```js
assert.equal(await page.locator('.mc-metric').count(), 0, 'two workloads, none chosen → focus required');   // after clearing the auto-selected workload? (see note)
await page.locator('[data-compare-pick-runs] > summary').click();
assert.equal(await page.locator('[data-compare-pick-runs] fieldset').count(), 2);
await page.locator('[data-compare-pick="a1"]').check(); await page.locator('[data-compare-pick="c1"]').check();
await expect(page.locator('[data-compare-pick-summary]')).toHaveText(/2 runs picked across 2 workloads/);
const card = page.locator('.mc-metric[data-metric="keepalive-round-trip-time"]');
assert.equal(await card.locator('.mc-bar').count(), 2);
await expect(card.locator('.mc-bar__label .mc-pill--mode').first()).toHaveText('stock');
const tip = card.locator('.mc-bar').first().locator('.mc-bar__source');
assert.equal(await tip.isVisible(), false);
await card.locator('.mc-bar').first().hover();
assert.equal(await tip.isVisible(), true);
assert.match(await tip.textContent(), /Amaru · keepalive_round_trip · us/);
assert.equal(await page.locator('[data-compare-filter="scenario"]').isDisabled(), true);
await page.locator('[data-compare-pick-clear]').click();
assert.equal(await page.locator('[data-compare-filter="scenario"]').isDisabled(), false);
assert.equal(await page.locator('.mc-picks').count(), 0);
```
Note: the default view auto-selects the first workload, so the first assertion checks instead that only one workload's rows render (`.mc-metric[data-metric="keepalive_round_trip"]` present, `keepalive_rtt` absent). Use `page.waitForFunction` rather than `expect` if `@playwright/test` is not on `NODE_PATH`.

- [ ] **Step 2: Run to verify failure**

Run: `NODE_PATH=/Users/nigel/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules DWARF_BROWSER_CHANNEL=chrome node tests/browser/test_portable_compare_picks_browser.cjs`
Expected: fails waiting for `[data-compare-pick-runs]` (timeout) or on the pill assertion. If the browser does not launch, run `node $NODE_PATH/playwright/cli.js install chromium-headless-shell` once and drop the channel variable.

- [ ] **Step 3: Implement the UI and CSS** in the `portable-compare.js` block and the `.mc-*` CSS region per the Interfaces block. Hook `renderPickRuns()` into `refresh()` (rebuild the checkbox list from `store.snapshot()`, keeping checked state from `filters.runs`, dropping ids no longer present and updating `filters.runs` accordingly).

- [ ] **Step 4: Regenerate `index.html`, run all viewer tests**

Run: the Task 5 Step 4 regeneration, then `NODE_PATH=… DWARF_BROWSER_CHANNEL=chrome node tests/browser/test_portable_compare_picks_browser.cjs && node tests/browser/test_portable_compare_concepts.cjs && node tests/browser/test_antithesis_import.cjs && PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_campaign_pages_workflow.py tests/test_render_viewer_presentation_catalog.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add docs/index.html index.html tests/browser/test_portable_compare_picks_browser.cjs
git commit -m "feat(viewer): pick runs across workloads, mode pills and hover source on compare bars"
```

---

### Task 7: Acceptance on the real default data and wrap-up

**Files:**
- Modify: `docs/plans/2026-11-04-cross-implementation-compare-design.md` (Status line → "implemented; acceptance run <date>"), memory note (outside repo)
- Evidence: screenshot(s) in the scratchpad

- [ ] **Step 1: Serve the repo root and load the real Pages set**

Run: `cd <repo> && python3 -m http.server 8765 --bind 127.0.0.1 >/dev/null 2>&1 &` then open `http://127.0.0.1:8765/index.html#compare` with Playwright (script in the scratchpad, same NODE_PATH): wait until `[data-session-inventory] input` count ≥ 10 (default archives loaded), pick runs `20261104T013138Z-572908c9`, `20261104T004457Z-8901e42d`, `20261104T022354Z-c6fd2046`, `20261104T024915Z-b1a2f8cb`.
Expected: `.mc-metric[data-metric="block-application-time"]`, `keepalive-round-trip-time`, `restart-readiness`, `plutus-execution-time`, `header-validation-verdicts`, `protocol-decode-time` each have 4 `.mc-bar` (fewer only where a build has no tap; record the count per card); hovering the Amaru keepalive bar shows `keepalive_round_trip`, the cardano bar `keepalive_rtt`; `chain-advance-count` bar tooltip starts with `≈`; no `.mc-category__title` reads "Other metrics".

- [ ] **Step 2: Screenshot the picked view and the hover** to `<scratchpad>/compare-acceptance-*.png`; stop the server.

- [ ] **Step 3: Full test run**

Run: `PYTHONPATH=dwarf python3 -m pytest -q -p no:cacheprovider tests/test_measurement_presentation.py tests/test_measurement_compare.py tests/test_render_viewer_presentation_catalog.py tests/test_campaign_pages_workflow.py tests/test_ci_stage_pages_campaigns.py && node tests/browser/test_antithesis_import.cjs && node tests/browser/test_portable_compare_concepts.cjs && NODE_PATH=… node tests/browser/test_portable_compare_picks_browser.cjs`
Expected: all pass. Then run the box suite subset via the scratchpad `boxtest.sh` only if the Python changes touch anything the box deploys (they do: `measurement_presentation.py`, `measurement_compare.py`) — expected baseline failures are the 25 pre-existing ones listed in the memory note; no new failures.

- [ ] **Step 4: Update the design status and commit**

```bash
git add docs/plans/2026-11-04-cross-implementation-compare-design.md
git commit -m "docs: cross-implementation compare implemented; acceptance on the Pages default set"
```
