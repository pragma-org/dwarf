# Opcert Derived Randomized Differential Soaks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the deterministic opcert header-case runs into randomized 3-hour differential soaks — one generic soak mode, four seed-deterministic per-family generators, a per-iteration family invariant, two fail-closed assertions, and a six-cell run matrix — reusing the existing opcert forger, parser, and substrate detection.

**Architecture:** A new `LoadPrimitive` `runtime_opcert_header_soak` drives a seeded time-budget loop. Each iteration a Python family generator (`opcert_soak_families.py`) produces a `SoakCaseSpec`; the existing `dwarf-opcert-adversary-latest serve-case` peer — extended with a deterministic `--case-spec` override and one new `encoding-form` CBOR re-encoder — serves it to the target(s)'s isolated consumer; the existing `header_validation_parse` parser yields the verdict; a per-family invariant is evaluated; every iteration is appended to `attempts.ndjson`; `result.json` carries counters, `mismatches[]` and `disagreements[]` with the seed-derived spec to reproduce each. Two assertions (`opcert_soak_invariant_holds`, `opcert_soak_verdicts_agree`) consume `result.json`; `target_progress_continues` is reused.

**Tech Stack:** Python 3 (DWARF framework, pytest), Haskell (ouroboros-consensus / cabal / Docker image, extending `antithesis/components/dwarf-opcert-adversary-latest`), DWARF profiles on cardano-box.

**Spec:** `docs/plans/2026-09-24-opcert-derived-soaks-design.md`

## Global Constraints

- **Extend, never reinvent.** The soak driver imports and reuses
  `scripts.runtime_opcert_header_cases` (`_load_runtime`, the amaru-control
  branch helpers, `_docker`, `_container_state`, `_cardano_tip`,
  `_consumer_topology`, `_amaru_project_ip`, `_amaru_extract_keys`,
  `_host_env_dir`, `_container_image`) and `scripts.header_validation_parse`
  (`parse_cardano_header_events`, `parse_amaru_header_events`, `verdict_by_hash`).
  It reuses `join_cases` accept/reject/reason semantics. No parallel tool; one
  forger binary, extended in place.
- **Fail-closed everywhere.** No leader slot / undelivered header within the
  per-iteration timeout ⇒ `inconclusive`, tracked separately, never `pass`,
  never `agree`/`disagree`. Zero conclusive iterations ⇒ run FAILS. A tap that
  cannot extract a verdict ⇒ `unavailable`, never synthesized.
- **Replayable.** `seed + iteration` fully determines a `SoakCaseSpec`, including
  every byte of an `encoding-form` re-encoding (the forger seeds its own RNG from
  the spec `seed`). Every `mismatches[]`/`disagreements[]` row carries the full
  spec.
- **Profiles reused:** `profile-v-cardano-measurement-nanoseconds-v2`,
  `profile-z-amaru-20260918-nanoseconds-v3`,
  `profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3`. No new profile.
- **Counts + gate green.** `dwarf/scenarios/*.yaml` count goes 287 → **293**
  (+6). `python3 dwarf/scripts/validate_scenarios.py --strict` stays green with
  only the two pre-existing semantic warnings. `python3
  dwarf/scripts/gen_reference.py dwarf dwarf/docs` regenerated (primitive +
  assertion counts, evidence-based `verified` column).
- **Box is source of truth.** Implement on `feat/opcert-header-validation`;
  `git push origin feat/opcert-header-validation` from the box.
- **Commit trailer:**
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` then
  `Claude-Session: https://claude.ai/code/session_01HczuZyRsz3iW3RpXAGBa6q`.
- **Tests:** `PYTHONPATH=dwarf python3 -m pytest -q tests/…`. Run environment:
  `ADA2_PROFILE_MANAGER_CONFIG=/home/nigel/.local/share/dwarf/state/config.yaml
  ADA2_DWARF_STATE_DIR=/home/nigel/.local/share/dwarf/state
  ADA2_DWARF_RUNS_DIR=/home/nigel/.local/share/dwarf/runs`.

## Review Focus

- **Silent-constant generator** — a generator that returns the *same* case every
  iteration (no real randomization) would make a 3h soak vacuous while looking
  green. Pinned to **Task 2** (`test_iterations_are_not_constant`,
  `test_seed_iteration_is_deterministic`, `test_replay_reproduces_spec`).
- **Inconclusive flood masquerading as pass** — a pool that is rarely leader
  yields all-inconclusive iterations; the run must FAIL, not pass on zero
  conclusive. Pinned to **Task 1** (`test_zero_conclusive_fails`) and **Task 4**
  (`test_budget_loop_all_inconclusive_fails`).
- **False agreement (one node never receives the header)** — a differential
  iterations where one side is inconclusive must be excluded from agree/disagree,
  not scored `agree`. Pinned to **Task 1** (`test_differential_one_side_missing_is_inconclusive`)
  and **Task 4** (`test_differential_missing_side_not_counted_agree`).
- **Time budget not bounding the run** — the loop must stop on the monotonic
  clock, not run forever. Pinned to **Task 4** (`test_budget_bounds_loop`).
- **Mismatch without repro data** — a recorded `mismatches[]`/`disagreements[]`
  row that lacks the seed-derived spec cannot be replayed. Pinned to **Task 1**
  (`test_mismatch_row_carries_full_spec`) and **Task 3**
  (`test_served_evidence_echoes_spec`).

---

## Task 1: Soak result model + decision helpers (pure Python)

**Files:**
- Create: `dwarf/scripts/opcert_soak_result.py`
- Test: `tests/test_opcert_soak_result.py`

**Interfaces:**
- `classify_iteration(spec: dict, served_hash: str | None, observed: dict | None, implementation: str) -> str` → `"pass" | "mismatch" | "inconclusive"`. Reuses `join_cases` accept/reject/reason semantics: accept-spec ⇒ pass iff `observed["verdict"] == "accepted"`; reject-spec ⇒ pass iff `rejected` and the reason for `implementation` matches; never-served or never-observed ⇒ `inconclusive`. The spec's `expected_reason` is the per-node dict `{"cardano-node": …, "amaru": …}` the generator emits, so `implementation` selects the expected token (identical to `join_cases`).
- `classify_differential(verdict_a: str | None, verdict_b: str | None) -> str` → `"agree" | "disagree" | "inconclusive"` (either side `None` ⇒ inconclusive).
- `build_soak_result(*, family, seed, differential, target_nodes, records, duration_seconds, runtime_root, compose_project, target_health) -> dict` — assembles the `result.json` body (counters, `iterations`, `conclusive`, `inconclusive`, `pass`, `mismatches[]`, `disagreements[]`). `records` is the per-iteration list produced by the driver.
- `evaluate_soak_invariant(result: dict) -> dict` → `{"result": "pass"|"fail", "mismatches": [...], "conclusive": int}`; PASS iff `mismatches == []` and `conclusive > 0`.
- `evaluate_soak_agree(result: dict) -> dict` → `{"result": "pass"|"fail", "disagreements": [...], "conclusive": int}`; PASS iff `disagreements == []` and `conclusive > 0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opcert_soak_result.py
from scripts import opcert_soak_result as R

def _accept_spec(it=0):
    return {"family": "encoding-form", "seed": 7, "iteration": it,
            "base_case": "valid-control", "expected_verdict": "accept",
            "case_id": f"encoding-form-{it:06d}", "params": {"encoding_form": "trailing-bytes"}}

def _reject_spec(it=0):
    return {"family": "restart-persistence", "seed": 7, "iteration": it,
            "base_case": "counter-behind", "expected_verdict": "reject",
            "expected_reason": {"cardano-node": "CounterTooSmallOCERT", "amaru": "SequenceNumberTooSmall"},
            "case_id": f"restart-persistence-{it:06d}", "params": {"replay_counter": 0}}

def test_accept_spec_accepted_is_pass():
    assert R.classify_iteration(_accept_spec(), "h1", {"verdict": "accepted", "reason": None}, "cardano-node") == "pass"

def test_accept_spec_rejected_is_mismatch():
    assert R.classify_iteration(_accept_spec(), "h1", {"verdict": "rejected", "reason": "x"}, "cardano-node") == "mismatch"

def test_reject_spec_wrong_reason_is_mismatch():
    assert R.classify_iteration(_reject_spec(), "h1", {"verdict": "rejected", "reason": "InvalidKesSignatureOCERT"}, "cardano-node") == "mismatch"

def test_reject_spec_right_reason_per_impl_is_pass():
    assert R.classify_iteration(_reject_spec(), "h1", {"verdict": "rejected", "reason": "CounterTooSmallOCERT"}, "cardano-node") == "pass"
    assert R.classify_iteration(_reject_spec(), "h1", {"verdict": "rejected", "reason": "SequenceNumberTooSmall"}, "amaru") == "pass"

def test_unserved_is_inconclusive():
    assert R.classify_iteration(_accept_spec(), None, None, "cardano-node") == "inconclusive"

def test_served_but_unobserved_is_inconclusive():
    assert R.classify_iteration(_accept_spec(), "h1", None, "cardano-node") == "inconclusive"

def test_differential_one_side_missing_is_inconclusive():
    assert R.classify_differential("accepted", None) == "inconclusive"
    assert R.classify_differential(None, "rejected") == "inconclusive"

def test_differential_agree_and_disagree():
    assert R.classify_differential("accepted", "accepted") == "agree"
    assert R.classify_differential("accepted", "rejected") == "disagree"

def test_zero_conclusive_fails():
    records = [{"outcome": "inconclusive", "spec": _accept_spec(i)} for i in range(5)]
    result = R.build_soak_result(family="encoding-form", seed=7, differential=True,
                                 target_nodes=["node1", "amaru-relay-1"], records=records,
                                 duration_seconds=10.0, runtime_root="", compose_project="",
                                 target_health={})
    assert result["conclusive"] == 0
    assert result["pass"] is False
    assert R.evaluate_soak_invariant(result)["result"] == "fail"

def test_mismatch_row_carries_full_spec():
    spec = _accept_spec(3)
    records = [{"outcome": "mismatch", "spec": spec, "served_hash": "h1",
                "observed_verdict": "rejected", "observed_reason": None}]
    result = R.build_soak_result(family="encoding-form", seed=7, differential=False,
                                 target_nodes=["node1"], records=records, duration_seconds=1.0,
                                 runtime_root="", compose_project="", target_health={})
    row = result["mismatches"][0]
    assert row["spec"] == spec and row["iteration"] == 3 and row["served_hash"] == "h1"

def test_agree_disagree_counters_and_rows():
    specs = [_accept_spec(i) for i in range(3)]
    records = [
        {"outcome": "pass", "differential": "agree", "spec": specs[0]},
        {"outcome": "mismatch", "differential": "disagree", "spec": specs[1],
         "verdicts": {"node1": "accepted", "amaru-relay-1": "rejected"}},
        {"outcome": "inconclusive", "differential": "inconclusive", "spec": specs[2]},
    ]
    result = R.build_soak_result(family="encoding-form", seed=7, differential=True,
                                 target_nodes=["node1", "amaru-relay-1"], records=records,
                                 duration_seconds=5.0, runtime_root="", compose_project="",
                                 target_health={})
    assert result["counters"]["agree"] == 1 and result["counters"]["disagree"] == 1
    assert result["conclusive"] == 2
    assert result["disagreements"][0]["iteration"] == 1
    assert R.evaluate_soak_agree(result)["result"] == "fail"
```

- [ ] **Step 2: Run the test to verify it fails** — `PYTHONPATH=dwarf python3 -m pytest -q tests/test_opcert_soak_result.py`
- [ ] **Step 3: Implement `opcert_soak_result.py`** to green. `classify_iteration` delegates to the same accept/reject/reason logic as `runtime_opcert_header_cases.join_cases`.
- [ ] **Step 4: Verify green.**
- [ ] **Step 5: Commit** — `feat(opcert-soak): soak result model + fail-closed decision helpers`.

**Deliverable:** pure result/decision module with green unit tests; zero-conclusive and one-side-missing both fail closed.

---

## Task 2: Seed-deterministic family generators

**Files:**
- Create: `dwarf/scripts/opcert_soak_families.py`
- Test: `tests/test_opcert_soak_families.py`

**Interfaces:**
- `FAMILIES: tuple[str, ...] = ("encoding-form", "accept-boundary", "restart-persistence", "kes-period-differential")`
- `DIFFERENTIAL_FAMILIES: frozenset[str] = frozenset({"encoding-form", "kes-period-differential"})`
- `ENCODING_FORMS: tuple[str, ...] = ("noncanonical-int", "definite-array", "indefinite-array", "extra-map-key", "duplicate-map-key", "missing-optional-key", "trailing-bytes")`
- `iter_rng(family: str, seed: int, iteration: int) -> random.Random` — `random.Random(f"{family}:{seed}:{iteration}")`.
- `generate_case(family: str, seed: int, iteration: int, *, restart_k: int = 4) -> dict` — a JSON-serialisable `SoakCaseSpec` dict: `{"family", "seed", "iteration", "case_id", "base_case", "expected_verdict", "expected_reason"|None, "params"}`. Family params:
  - `encoding-form`: `{"encoding_form": <one of ENCODING_FORMS>, "trailing_len": 1..8 (trailing-bytes only), "byte_seed": int}`; `base_case="valid-control"`, verdict `accept`.
  - `accept-boundary`: `{"counter_delta": 1, "kes_period_fraction": [0,1)}`; `base_case="counter-plus-one"`, verdict `accept`.
  - `restart-persistence`: `{"rotate_to_counter": 1..restart_k, "replay_counter": 0..rotate_to_counter-1}`; `base_case="counter-behind"`, verdict `reject`, `expected_reason` = `{"cardano-node": "CounterTooSmallOCERT", "amaru": "SequenceNumberTooSmall"}`.
  - `kes-period-differential`: `{"slot_offset_fraction": [0,1)}`; `base_case="valid-control"`, verdict `accept`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opcert_soak_families.py
import pytest
from scripts import opcert_soak_families as F

def test_families_and_differential_sets():
    assert set(F.DIFFERENTIAL_FAMILIES) <= set(F.FAMILIES)
    assert F.DIFFERENTIAL_FAMILIES == {"encoding-form", "kes-period-differential"}

@pytest.mark.parametrize("family", F.FAMILIES)
def test_seed_iteration_is_deterministic(family):
    a = F.generate_case(family, seed=1234, iteration=17)
    b = F.generate_case(family, seed=1234, iteration=17)
    assert a == b  # replayable

@pytest.mark.parametrize("family", F.FAMILIES)
def test_replay_reproduces_spec(family):
    # a persisted spec re-generates byte-for-byte from (family, seed, iteration)
    a = F.generate_case(family, seed=99, iteration=3)
    b = F.generate_case(a["family"], a["seed"], a["iteration"])
    assert a == b

@pytest.mark.parametrize("family", F.FAMILIES)
def test_iterations_are_not_constant(family):
    specs = [F.generate_case(family, seed=5, iteration=i)["params"] for i in range(40)]
    assert len({repr(p) for p in specs}) > 1, "generator produced a constant case (no randomization)"

def test_encoding_form_covers_all_forms_over_many_iters():
    seen = {F.generate_case("encoding-form", 5, i)["params"]["encoding_form"] for i in range(400)}
    assert seen == set(F.ENCODING_FORMS)

def test_accept_boundary_counter_is_recorded_plus_one_and_kes_in_unit_interval():
    for i in range(50):
        p = F.generate_case("accept-boundary", 5, i)["params"]
        assert p["counter_delta"] == 1
        assert 0.0 <= p["kes_period_fraction"] < 1.0

def test_restart_persistence_replay_counter_below_rotate_target():
    for i in range(50):
        p = F.generate_case("restart-persistence", 5, i, restart_k=4)["params"]
        assert 1 <= p["rotate_to_counter"] <= 4
        assert 0 <= p["replay_counter"] < p["rotate_to_counter"]

def test_expected_verdict_per_family():
    assert F.generate_case("encoding-form", 5, 0)["expected_verdict"] == "accept"
    assert F.generate_case("accept-boundary", 5, 0)["expected_verdict"] == "accept"
    assert F.generate_case("restart-persistence", 5, 0)["expected_verdict"] == "reject"
    assert F.generate_case("kes-period-differential", 5, 0)["expected_verdict"] == "accept"

def test_unknown_family_raises():
    with pytest.raises(ValueError, match="unknown family"):
        F.generate_case("banana", 5, 0)
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement `opcert_soak_families.py`** using `iter_rng`. `case_id = f"{family}-{iteration:06d}"`.
- [ ] **Step 4: Verify green.**
- [ ] **Step 5: Commit** — `feat(opcert-soak): seed-deterministic per-family case generators`.

**Deliverable:** four generators; determinism, replay, non-constant, and per-family invariants proven.

---

## Task 3: Forger `--case-spec` override + `encoding-form` re-encoder (Haskell)

**Files:**
- Modify: `antithesis/components/dwarf-opcert-adversary-latest/app/Main.hs` (accept `--case-spec`)
- Modify: `antithesis/components/dwarf-opcert-adversary-latest/src/DwarfOpcertAdversary.hs` (`applyCaseSpec`, `reEncodeOpcert`)
- Test: `antithesis/components/dwarf-opcert-adversary-latest/test/OpcertSoakSpec.hs` (add to the existing test suite)

**Interfaces (Haskell):**
```haskell
-- A seed-derived spec parsed from --case-spec JSON.
data CaseSpec = CaseSpec
  { csBaseCase        :: !String
  , csSeed            :: !Int
  , csEncodingForm    :: !(Maybe String)   -- "trailing-bytes" | "noncanonical-int" | ...
  , csTrailingLen     :: !(Maybe Int)
  , csCounterDelta    :: !(Maybe Integer)
  , csKesPeriodFrac   :: !(Maybe Double)   -- accept-boundary: mapped with live genesis
  , csReplayCounter   :: !(Maybe Word)
  , csSlotOffsetFrac  :: !(Maybe Double)
  }

parseCaseSpec  :: LBS.ByteString -> Either String CaseSpec
-- Deterministic override of applyCase; falls back to caseId when spec is Nothing.
applyCaseSpec  :: KeySet -> Maybe CaseSpec -> String -> Header -> Either String CaseResult
-- Structural CBOR re-encoding of an otherwise-valid opcert header body; seeded.
reEncodeOpcert :: Int -> String -> Maybe Int -> Praos.HeaderBody StandardCrypto
               -> Praos.HeaderBody StandardCrypto
```
`kes_period_fraction` maps at serve time to `start + floor(frac * (maxKESEvolutions + 1))` from the live genesis (`ksMaxKESEvo`). Evidence: each `opcert_case_served` line echoes `"spec"` (base_case, params, seed).

- [ ] **Step 1: Write the failing Haskell test** (alongside the existing adversary test):

```haskell
-- test/OpcertSoakSpec.hs
module Main (main) where

import DwarfOpcertAdversary
import Test.Hspec

main :: IO ()
main = hspec $ do
  describe "parseCaseSpec" $ do
    it "parses an encoding-form trailing-bytes spec" $ do
      let js = "{\"base_case\":\"valid-control\",\"seed\":91827,\
               \\"params\":{\"encoding_form\":\"trailing-bytes\",\"trailing_len\":3}}"
      parseCaseSpec js `shouldSatisfy` \case
        Right s -> csBaseCase s == "valid-control"
                     && csEncodingForm s == Just "trailing-bytes"
                     && csTrailingLen s == Just 3
        _ -> False
    it "rejects an unknown encoding form" $
      parseCaseSpec "{\"base_case\":\"valid-control\",\"seed\":1,\
        \\"params\":{\"encoding_form\":\"raw-bytes\"}}" `shouldSatisfy` isLeft

  describe "reEncodeOpcert" $ do
    it "trailing-bytes re-encoding still decodes to the same opcert (reaches decoder)" $
      -- the re-encoded body round-trips to the same logical opcert fields;
      -- only the serialization deviates, so the header reaches header validation.
      reEncodesToSameOpcert "trailing-bytes" 3 sampleBody `shouldBe` True
    it "is deterministic in the seed (same seed => same bytes)" $
      encodedBytes (reEncodeOpcert 42 "trailing-bytes" (Just 3) sampleBody)
        `shouldBe` encodedBytes (reEncodeOpcert 42 "trailing-bytes" (Just 3) sampleBody)

  describe "applyCaseSpec" $
    it "falls back to caseId behavior when spec is Nothing" $
      fmap crExpectedVerdict (applyCaseSpec sampleKeySet Nothing "valid-control" sampleHeader)
        `shouldBe` Right "accept"
```
(`sampleBody`, `sampleHeader`, `sampleKeySet`, `reEncodesToSameOpcert`, `encodedBytes`, `isLeft` are test fixtures/helpers defined in the test module from a captured devnet header, mirroring the existing `verify-resign` fixture.)

- [ ] **Step 2: Build the test, verify red** — `cabal test` in the component dir.
- [ ] **Step 3: Implement** `parseCaseSpec`, `applyCaseSpec`, `reEncodeOpcert`; thread `--case-spec` through `Main.hs serve-case`; echo `spec` in the `opcert_case_served` evidence line.
- [ ] **Step 4: Add a Python evidence contract test** `tests/test_opcert_soak_forger_evidence.py::test_served_evidence_echoes_spec` — a served evidence line parsed by `served_hash_by_case` still yields the hash **and** exposes `record["spec"]` with `seed`, `base_case`, `params`.
- [ ] **Step 5: Rebuild the binary; record the new `dist-newstyle` executable path/digest in the task notes.**
- [ ] **Step 6: Verify green** (Haskell + Python).
- [ ] **Step 7: Commit** — `feat(opcert-soak): forger --case-spec override + encoding-form re-encoder`.

**Deliverable:** one extended forger that deterministically applies a seed-derived spec, re-encodes valid opcerts structurally, and echoes the spec in evidence; deterministic scenarios unchanged when `--case-spec` is absent.

---

## Task 4: Soak driver (time-budget loop, reusing the deterministic driver)

**Files:**
- Create: `dwarf/scripts/runtime_opcert_header_soak.py`
- Test: `tests/test_runtime_opcert_header_soak.py`

**Interfaces:**
- `run_opcert_header_soak(runtime_root, family, seed, output_dir, *, target_node=None, target_nodes=None, time_budget_seconds=10800, peer_bin=None, per_iteration_timeout=240, restart_k=4, clock=time.monotonic) -> dict` — writes `attempts.ndjson` + `result.json`, returns the result body. `clock` is injectable for tests. Reuses `runtime_opcert_header_cases._load_runtime` and the substrate-specific consumer wiring; for a differential family it serves each iteration's spec to both `target_nodes` and compares verdicts via `classify_differential`.
- `_serve_one(spec, *, consumer, forger_ctx) -> tuple[str | None, dict | None]` — writes `case-spec-<n>.json`, invokes `serve-case --case-spec`, returns `(served_hash, observed)`; on no leader / undelivered within `per_iteration_timeout` returns `(served_hash_or_None, None)`.
- `main(argv=None)` — argparse CLI mirroring the primitive params; exit 0 iff `result["pass"]`.

- [ ] **Step 1: Write the failing test** (loop logic tested with the substrate + forger stubbed; no Docker):

```python
# tests/test_runtime_opcert_header_soak.py
import json
from scripts import runtime_opcert_header_soak as soak

class FakeClock:
    def __init__(self, budget, step=1.0):
        self.t = 0.0; self.budget = budget; self.step = step
    def __call__(self):
        v = self.t; self.t += self.step; return v

def _patch_substrate(monkeypatch, served):
    # served: list of (served_hash|None, observed|None) per iteration, per node
    monkeypatch.setattr(soak, "_open_consumers", lambda *a, **k: {"node1": object(), "amaru-relay-1": object()})
    monkeypatch.setattr(soak, "_close_consumers", lambda *a, **k: None)
    monkeypatch.setattr(soak, "_load_runtime_root", lambda root: ({"compose_project": "p"}, "cardano-node"))

def test_budget_bounds_loop(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch, served=None)
    calls = {"n": 0}
    def fake_serve(spec, **k):
        calls["n"] += 1
        return ("h%d" % spec["iteration"], {"verdict": "accepted", "reason": None})
    monkeypatch.setattr(soak, "_serve_one", fake_serve)
    r = soak.run_opcert_header_soak(str(tmp_path), "accept-boundary", 5, str(tmp_path/"o"),
                                    target_node="node1", time_budget_seconds=10,
                                    clock=FakeClock(budget=10, step=1.0))
    assert calls["n"] <= 11          # loop stopped on the clock, not unbounded
    assert r["iterations"] == calls["n"]

def test_budget_loop_all_inconclusive_fails(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch, served=None)
    monkeypatch.setattr(soak, "_serve_one", lambda spec, **k: (None, None))
    r = soak.run_opcert_header_soak(str(tmp_path), "accept-boundary", 5, str(tmp_path/"o"),
                                    target_node="node1", time_budget_seconds=5,
                                    clock=FakeClock(budget=5))
    assert r["conclusive"] == 0 and r["pass"] is False

def test_differential_missing_side_not_counted_agree(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch, served=None)
    def fake_serve_node(spec, *, node, **k):
        return ("h", {"verdict": "accepted", "reason": None}) if node == "node1" else (None, None)
    monkeypatch.setattr(soak, "_serve_one_node", fake_serve_node)
    r = soak.run_opcert_header_soak(str(tmp_path), "encoding-form", 5, str(tmp_path/"o"),
                                    target_nodes=["node1", "amaru-relay-1"], time_budget_seconds=3,
                                    clock=FakeClock(budget=3))
    assert r["counters"]["agree"] == 0 and r["conclusive"] == 0 and r["pass"] is False

def test_every_iteration_appended_to_attempts(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch, served=None)
    monkeypatch.setattr(soak, "_serve_one", lambda spec, **k: ("h", {"verdict": "accepted", "reason": None}))
    out = tmp_path / "o"
    soak.run_opcert_header_soak(str(tmp_path), "accept-boundary", 5, str(out),
                                target_node="node1", time_budget_seconds=4, clock=FakeClock(budget=4))
    lines = (out / "attempts.ndjson").read_text().splitlines()
    assert len(lines) >= 1 and all(json.loads(l)["spec"]["family"] == "accept-boundary" for l in lines)

def test_result_written_with_seed_and_duration(monkeypatch, tmp_path):
    _patch_substrate(monkeypatch, served=None)
    monkeypatch.setattr(soak, "_serve_one", lambda spec, **k: ("h", {"verdict": "accepted", "reason": None}))
    out = tmp_path / "o"
    r = soak.run_opcert_header_soak(str(tmp_path), "accept-boundary", 5, str(out),
                                    target_node="node1", time_budget_seconds=4, clock=FakeClock(budget=4))
    disk = json.loads((out / "result.json").read_text())
    assert disk["seed"] == 5 and disk["family"] == "accept-boundary" and "duration_seconds" in disk
    assert disk == r
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement `runtime_opcert_header_soak.py`.** Import the deterministic driver's helpers; factor the isolated-consumer setup so a soak opens the consumer(s) **once** and serves N iterations against them (`_open_consumers` / `_serve_one` / `_close_consumers`). For family C (`restart-persistence`) `_serve_one` performs the rotation-then-restart sequence, reusing the aged-profile opcert-rotation machinery, before serving the replay counter. Use `opcert_soak_families.generate_case` and `opcert_soak_result.build_soak_result`.
- [ ] **Step 4: Verify green.**
- [ ] **Step 5: Commit** — `feat(opcert-soak): time-budget soak driver over the reused opcert substrate`.

**Deliverable:** a seeded soak driver whose loop is clock-bounded, appends every iteration, fails closed on zero conclusive, and never scores a one-sided differential iteration as agreement.

---

## Task 5: `runtime_opcert_header_soak` primitive + registry + schema

**Files:**
- Modify: `dwarf/profile_manager/primitives.py` (`class RuntimeOpcertHeaderSoak(LoadPrimitive)`)
- Modify: `dwarf/primitives/registry.json`
- Create: `dwarf/primitives/load/runtime_opcert_header_soak.schema.json`
- Test: `tests/test_opcert_soak_primitive.py`

**Interfaces:**
- Registry entry `runtime_opcert_header_soak` → `{class: RuntimeOpcertHeaderSoak, family: load, module: profile_manager.primitives, params_schema: primitives/load/runtime_opcert_header_soak.schema.json, runtimes: [devnet], supports: [cardano-node, amaru], version: 0.1.0}`.
- Schema params: `family` (enum of the four), `seed` (integer), `time_budget_seconds` (number, default 10800), `profile_id`/`runtime_root` (oneOf, mirroring the deterministic schema), `target_node` (string) **xor** `target_nodes` (array, length 2), `output_dir`, `peer_bin`, `per_iteration_timeout`, `restart_k`, `python_bin`, `timeout_seconds`, `expect_exit`. `additionalProperties: false`. A schema `oneOf` requires `target_nodes` for the differential families and `target_node` otherwise.
- `RuntimeOpcertHeaderSoak.run` builds the `runtime_opcert_header_soak.py` command (mirroring `RuntimeOpcertHeaderCases.run`: `PYTHONPATH`, `timeout_seconds`, `expect_exit`, logs `opcert_soak_summary`).

- [ ] **Step 1: Write the failing test:**

```python
# tests/test_opcert_soak_primitive.py
import json
from pathlib import Path
from profile_manager import registry

def test_soak_primitive_registered():
    entry = registry.load()["primitives"]["runtime_opcert_header_soak"]
    assert entry["class"] == "RuntimeOpcertHeaderSoak"
    assert entry["family"] == "load"
    assert set(entry["supports"]) == {"cardano-node", "amaru"}

def test_soak_schema_rejects_both_target_shapes():
    from scripts import validate_scenarios as V   # reuse the project schema validator
    schema = json.loads(Path("dwarf/primitives/load/runtime_opcert_header_soak.schema.json").read_text())
    ok = {"family": "accept-boundary", "seed": 5, "profile_id": "p", "target_node": "node1",
          "output_dir": "o"}
    bad = {"family": "accept-boundary", "seed": 5, "profile_id": "p",
           "target_node": "n", "target_nodes": ["a", "b"], "output_dir": "o"}
    assert V.validate_params(schema, ok) == []
    assert V.validate_params(schema, bad) != []

def test_soak_command_build(monkeypatch, tmp_path):
    from profile_manager.primitives import RuntimeOpcertHeaderSoak
    prim = RuntimeOpcertHeaderSoak({"family": "encoding-form", "seed": 5,
                                    "profile_id": "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3",
                                    "target_nodes": ["node1", "amaru-relay-1"],
                                    "time_budget_seconds": 10800, "output_dir": "outputs/soak"})
    cmd = prim._build_command(runtime_root=str(tmp_path), output_dir=tmp_path/"o")
    assert "--family" in cmd and "encoding-form" in cmd
    assert "--time-budget-seconds" in cmd and "10800" in cmd
    assert "--target-nodes" in cmd
```
(If `registry`/`validate_scenarios` expose different helper names, adapt the test to the real names discovered in Task 5 Step 1 — the assertions on behavior stay.)

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** the primitive, schema, and registry entry.
- [ ] **Step 4: Verify green** + `python3 dwarf/scripts/validate_scenarios.py --strict` (still 287, no new scenarios yet).
- [ ] **Step 5: Commit** — `feat(opcert-soak): runtime_opcert_header_soak load primitive + schema`.

**Deliverable:** a registered soak primitive whose schema enforces the single-vs-differential target shape.

---

## Task 6: Assertions `opcert_soak_invariant_holds` + `opcert_soak_verdicts_agree`

**Files:**
- Modify: `dwarf/profile_manager/primitives.py` (`OpcertSoakInvariantHolds`, `OpcertSoakVerdictsAgree`)
- Modify: `dwarf/primitives/registry.json`
- Create: `dwarf/primitives/assertion/opcert_soak_invariant_holds.schema.json`, `dwarf/primitives/assertion/opcert_soak_verdicts_agree.schema.json`
- Test: `tests/test_opcert_soak_assertions.py`

**Interfaces:**
- `opcert_soak_invariant_holds` params: `{report_path: string}` (default `outputs/opcert-soak/result.json`). Delegates to `opcert_soak_result.evaluate_soak_invariant`. PASS iff `mismatches == []` and `conclusive > 0`; a missing/unreadable report ⇒ fail with `unavailable`.
- `opcert_soak_verdicts_agree` params: `{report_path: string}`. Delegates to `evaluate_soak_agree`. PASS iff `disagreements == []` and `conclusive > 0`.

- [ ] **Step 1: Write the failing test:**

```python
# tests/test_opcert_soak_assertions.py
import json
from pathlib import Path
from tests.helpers import make_handle   # existing test helper used by test_opcert_assertions.py
from profile_manager.primitives import OpcertSoakInvariantHolds, OpcertSoakVerdictsAgree

def _write(tmp, body, name="opcert-soak"):
    p = tmp / "outputs" / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "result.json").write_text(json.dumps(body))
    return f"outputs/{name}/result.json"

def test_invariant_holds_passes_on_clean_conclusive(tmp_path):
    rel = _write(tmp_path, {"conclusive": 400, "pass": True, "mismatches": [], "disagreements": []})
    res = OpcertSoakInvariantHolds({"report_path": rel}).evaluate(make_handle(tmp_path))
    assert res["passed"] is True

def test_invariant_fails_on_zero_conclusive(tmp_path):
    rel = _write(tmp_path, {"conclusive": 0, "pass": False, "mismatches": [], "disagreements": []})
    res = OpcertSoakInvariantHolds({"report_path": rel}).evaluate(make_handle(tmp_path))
    assert res["passed"] is False

def test_invariant_fails_on_mismatch(tmp_path):
    rel = _write(tmp_path, {"conclusive": 400, "pass": False,
                            "mismatches": [{"iteration": 3, "spec": {}}], "disagreements": []})
    res = OpcertSoakInvariantHolds({"report_path": rel}).evaluate(make_handle(tmp_path))
    assert res["passed"] is False

def test_agree_fails_on_disagreement(tmp_path):
    rel = _write(tmp_path, {"conclusive": 400, "pass": False, "mismatches": [],
                            "disagreements": [{"iteration": 7, "verdicts": {"node1": "accepted", "amaru-relay-1": "rejected"}}]})
    res = OpcertSoakVerdictsAgree({"report_path": rel}).evaluate(make_handle(tmp_path))
    assert res["passed"] is False

def test_agree_unavailable_report_fails_closed(tmp_path):
    res = OpcertSoakVerdictsAgree({"report_path": "outputs/missing/result.json"}).evaluate(make_handle(tmp_path))
    assert res["passed"] is False
```
(Use the same handle/`_read_client_proof` helper the existing `tests/test_opcert_assertions.py` uses; discover its exact name in Step 1 and adapt the import.)

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** both assertions + schemas + registry entries (`supports: [cardano-node, amaru]`, `runtimes: [devnet]`).
- [ ] **Step 4: Verify green** + `validate_scenarios --strict`.
- [ ] **Step 5: Commit** — `feat(opcert-soak): soak invariant + verdicts-agree assertions`.

**Deliverable:** two fail-closed assertions consuming the soak `result.json`.

---

## Task 7: Six soak scenarios + strict gate + count pin

**Files:**
- Create: `dwarf/scenarios/opcert-soak-encoding-mixed-1112-amaru-20260918.yaml`
- Create: `dwarf/scenarios/opcert-soak-kes-period-mixed-1112-amaru-20260918.yaml`
- Create: `dwarf/scenarios/opcert-soak-accept-boundary-cardano-1112.yaml`
- Create: `dwarf/scenarios/opcert-soak-accept-boundary-amaru-20260918.yaml`
- Create: `dwarf/scenarios/opcert-soak-restart-persistence-cardano-1112.yaml`
- Create: `dwarf/scenarios/opcert-soak-restart-persistence-amaru-20260918.yaml`

**Interfaces / conventions:** mirror the existing `opcert-header-validation-cases-*.yaml` scenarios (same `setup: runtime_verify_exact_target`, `measurement_profile`, `probes: runtime_target_health_and_progress` reading the soak `result.json`). Each `load` step is `runtime_opcert_header_soak` with `time_budget_seconds: 10800`, `timeout_seconds: 11700`, `expect_exit: 0`, `evidence_intent: finding-validation`. Differential scenarios (A, D) carry `target_nodes: ["node1", "amaru-relay-1"]` and both assertions; single-target scenarios (B, C) carry `target_node` and only `opcert_soak_invariant_holds`. All carry `target_progress_continues`.

Representative differential scenario (family A, mixed):
```json
{
  "spec_version": "v1",
  "id": "opcert-soak-encoding-mixed-1112-amaru-20260918",
  "title": "Opcert encoding-form soak (3h differential) — mixed Cardano-node 11.1.2 + Amaru 10.11.20260918",
  "authors": ["dwarf"],
  "tags": ["opcert", "header-validation", "soak", "encoding-form", "differential", "mixed", "finding-validation"],
  "evidence_intent": "finding-validation",
  "target": {"implementation": "amaru", "version": "10.11.20260918", "source_revision": "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"},
  "runtime": "devnet",
  "profile": "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3",
  "measurement_profile": "amaru-security-patched",
  "seed": "0x50AC0A24",
  "setup": [{"primitive": "runtime_verify_exact_target", "implementation": "amaru", "version": "10.11.20260918", "source_revision": "aedfe797a5b8ef00d8b362be40b47a52c3b4a379", "mode": "patched", "image_digest": "sha256:b3f6c0cec64c62e0500f012367d1661872efede9b683c8a56dd870a42c8fb3fd", "executable_digest": "sha256:45c96358f1033629e6f0b7d91f306176fde07e35a9f23d2bd4001dc8b841d17a", "patch_set_sha256": "47787f152bc7bdcd645d5d32125f6658847812c447fd375ede44d31b546620f6"}],
  "load": [{
    "primitive": "runtime_opcert_header_soak",
    "profile_id": "profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3",
    "family": "encoding-form",
    "seed": 1353486372,
    "target_nodes": ["node1", "amaru-relay-1"],
    "time_budget_seconds": 10800,
    "output_dir": "outputs/opcert-soak",
    "timeout_seconds": 11700,
    "expect_exit": 0
  }],
  "faults": [],
  "probes": [{"primitive": "runtime_target_health_and_progress", "report_path": "outputs/opcert-soak/result.json", "progress_reference": "load-start"}],
  "assertions": [
    {"primitive": "opcert_soak_invariant_holds", "report_path": "outputs/opcert-soak/result.json"},
    {"primitive": "opcert_soak_verdicts_agree", "report_path": "outputs/opcert-soak/result.json"},
    {"primitive": "target_progress_continues"}
  ],
  "teardown": []
}
```
(Copy the exact digests/`source_revision`/`measurement_profile` from the matching `opcert-header-validation-cases-*` scenario for each cell; cardano cells use `profile-v`, amaru cells use `profile-z`, and the digests already in those scenarios.)

- [ ] **Step 1: Write a failing count-pin test** `tests/test_opcert_soak_scenarios.py`:
```python
from pathlib import Path
def test_scenario_count_is_293():
    assert len(list(Path("dwarf/scenarios").glob("*.yaml"))) == 293
def test_six_soak_scenarios_present():
    ids = {p.stem for p in Path("dwarf/scenarios").glob("opcert-soak-*.yaml")}
    assert ids == {
        "opcert-soak-encoding-mixed-1112-amaru-20260918",
        "opcert-soak-kes-period-mixed-1112-amaru-20260918",
        "opcert-soak-accept-boundary-cardano-1112",
        "opcert-soak-accept-boundary-amaru-20260918",
        "opcert-soak-restart-persistence-cardano-1112",
        "opcert-soak-restart-persistence-amaru-20260918",
    }
```
- [ ] **Step 2: Verify red** (count is 287).
- [ ] **Step 3: Author the six scenarios.**
- [ ] **Step 4:** `python3 dwarf/scripts/validate_scenarios.py --strict` → green, exactly the two pre-existing semantic warnings, and the count-pin test passes.
- [ ] **Step 5: Commit** — `feat(opcert-soak): six 3h soak scenarios (matrix A/D mixed, B/C cardano+amaru)`.

**Deliverable:** the six scenarios pass the strict gate; scenario count pinned at 293.

---

## Task 8: Reference docs + full-suite verification

**Files:**
- Modify (regenerated): `dwarf/docs/*` via `gen_reference.py`
- Modify: any pinned primitive/assertion count constant surfaced by the reference build.

- [ ] **Step 1:** `python3 dwarf/scripts/gen_reference.py dwarf dwarf/docs`; confirm the two new assertions and the new load primitive appear, and the evidence-based `verified` column reflects the new scenarios (no fabricated support).
- [ ] **Step 2:** Run the whole opcert + soak test set:
  `PYTHONPATH=dwarf python3 -m pytest -q tests/test_opcert_cases.py tests/test_runtime_opcert_header_cases.py tests/test_opcert_assertions.py tests/test_opcert_soak_result.py tests/test_opcert_soak_families.py tests/test_runtime_opcert_header_soak.py tests/test_opcert_soak_primitive.py tests/test_opcert_soak_assertions.py tests/test_opcert_soak_scenarios.py`.
- [ ] **Step 3:** Full suite vs `main`: only the pre-existing failures allowed; `validate_scenarios.py --strict` green with only the two pre-existing warnings.
- [ ] **Step 4: Commit** — `docs(opcert-soak): regenerate reference for soak primitive + assertions`.
- [ ] **Step 5:** `git push origin feat/opcert-header-validation` from the box.

**Deliverable:** regenerated reference, whole suite green (modulo pre-existing), branch pushed.

---

## Live-run note (out of plan scope, for the operator)

The six scenarios are 3h each. Run them after the code lands, per the run-environment vars above, and treat any `mismatches[]`/`disagreements[]` row as a finding to replay from its recorded seed+iteration. Per the stop-conditions: if Amaru cannot be driven for family B or C on its substrate, degrade that cell to cardano-only and report it — do not force it.
