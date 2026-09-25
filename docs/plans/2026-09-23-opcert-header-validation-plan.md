# Opcert Header Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live-devnet tests that deliver headers with exactly one broken operational-certificate rule to real Amaru and Cardano-node targets (Amaru-only, Cardano-only, mixed) and record each node's verdict, reason, and cross-node agreement with full DWARF evidence.

**Architecture:** A Haskell opcert header peer (extension of `dwarf-kes-adversary`) serves one mutated header per case, preceded by a valid control, to one isolated target. A Python primitive runs cases from a data table, joins the peer's ground truth with verdicts from new header-validation measurement taps, and writes `result.json`; two assertions judge it. The `runtime_praos_header_assertion_probe` stub becomes fail-closed and TM-013 points to the new scenarios.

**Tech Stack:** Python 3 (DWARF framework, pytest), Haskell (ouroboros-consensus / gen-header, cabal, Docker image), DWARF profiles on cardano-box.

**Spec:** `docs/plans/2026-09-23-opcert-header-validation-design.md`

## Global Constraints

- Topologies/profiles: `profile-z-amaru-20260918-nanoseconds-v3` (Amaru 10.11.20260918), `profile-v-cardano-measurement-nanoseconds-v2` (Cardano-node 11.1.2), `profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3` (both targets).
- Reference mutations (ouroboros-consensus `gen-header`, 1:1 with case ids): `NoMutation`, `MutateColdKey`, `MutateCounterUnder`, `MutateCounterOver1`, `MutateKESPeriodBefore`, `MutateKESPeriod`, `MutateKESKey`.
- Every case keeps the header well-formed and validly signed; exactly one opcert rule differs. A `valid-control` header precedes each case on the same path.
- Fail-closed everywhere: a missing precondition (no leadership, control not adopted, header not delivered, verdict not observed) yields `inconclusive`, never `pass`; a tap that cannot extract a verdict returns `unavailable` with a reason, never a synthesized value.
- Reuse existing systems (scenario/registry/schema/profile/measurement/collector/assertion/SARIF/Learn). No new parallel tooling.
- Commit style: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` then `Claude-Session: https://claude.ai/code/session_01HczuZyRsz3iW3RpXAGBa6q`. Branch `feat/opcert-header-validation`.
- Sync/run on cardano-box: push Mac clone to `box` remote, `git merge --ff-only sync/from-mac` in `/home/nigel/dwarf-pragma`, run with `ADA2_PROFILE_MANAGER_CONFIG=/home/nigel/.local/share/dwarf/state/config.yaml ADA2_DWARF_STATE_DIR=/home/nigel/.local/share/dwarf/state ADA2_DWARF_RUNS_DIR=/home/nigel/.local/share/dwarf/runs`.
- Tests: `PYTHONPATH=dwarf python3 -m pytest -q tests/...`; scenario gate `python3 dwarf/scripts/validate_scenarios.py --strict`; docs `python3 dwarf/scripts/gen_reference.py dwarf dwarf/docs`.

## Review Focus

- **Case not delivered but scored pass** — if the peer never served the mutated header (target on a different fork, no leader slot in window), the case must be `inconclusive`, not `pass`. (Task 3)
- **Control not adopted** — if the target rejected or ignored the `valid-control`, no rejection is attributable; the whole case set for that target is `inconclusive`. (Task 3, Task 8)
- **Verdict reason unmatched** — a node rejects for a *different* reason than the case targets (e.g. VRF instead of opcert counter); the join must record the actual reason and the assertion must treat a wrong-reason rejection as a mismatch, not a silent pass. (Task 6, Task 7)
- **Tap sees no line** — the header hash never appears in the target's logs within the window; the tap returns `unavailable`, and the case is `inconclusive`. (Task 5)
- **Mixed disagreement** — one node accepts a case the other rejects; `opcert_verdicts_agree` must fail and classify a finding, not average the two. (Task 7)

---

## Task 1: Opcert case table (data + loader + validation)

**Files:**
- Create: `dwarf/corpora/opcert/opcert-header-cases-v1.json`
- Create: `dwarf/scripts/opcert_cases.py`
- Test: `tests/test_opcert_cases.py`

**Interfaces:**
- Produces: `opcert_cases.load_cases(path=None) -> list[dict]`; each case `{"id": str, "family": "rule"|"boundary", "mutation": str, "rule": str, "expected_verdict": "accept"|"reject", "expected_reason": {"cardano-node": str|None, "amaru": str|None}}`. `opcert_cases.MUTATIONS: frozenset[str]`. `opcert_cases.validate_cases(cases) -> None` (raises `ValueError`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opcert_cases.py
import json
from pathlib import Path
import pytest
from scripts import opcert_cases

CASES = Path("dwarf/corpora/opcert/opcert-header-cases-v1.json")

def test_case_table_loads_and_validates():
    cases = opcert_cases.load_cases()
    ids = [c["id"] for c in cases]
    assert ids[0] == "valid-control"
    assert len(ids) == len(set(ids)), "case ids must be unique"
    for name in ("cold-key-unauthorized", "counter-behind", "counter-jump",
                 "counter-plus-one", "kes-before-window", "kes-after-window",
                 "hot-key-mismatch"):
        assert name in ids, name
    for c in cases:
        assert c["mutation"] in opcert_cases.MUTATIONS
        assert c["expected_verdict"] in ("accept", "reject")
        assert set(c["expected_reason"]) == {"cardano-node", "amaru"}
    # valid cases carry no reason; reject cases name a reason for each node
    for c in cases:
        if c["expected_verdict"] == "accept":
            assert c["expected_reason"] == {"cardano-node": None, "amaru": None}
        else:
            assert c["expected_reason"]["cardano-node"] and c["expected_reason"]["amaru"]

def test_validate_rejects_unknown_mutation():
    with pytest.raises(ValueError, match="unknown mutation"):
        opcert_cases.validate_cases([{"id": "x", "family": "rule", "mutation": "Nope",
                                      "rule": "r", "expected_verdict": "reject",
                                      "expected_reason": {"cardano-node": "a", "amaru": "b"}}])

def test_validate_rejects_duplicate_ids():
    row = {"id": "dup", "family": "rule", "mutation": "NoMutation", "rule": "r",
           "expected_verdict": "accept", "expected_reason": {"cardano-node": None, "amaru": None}}
    with pytest.raises(ValueError, match="duplicate"):
        opcert_cases.validate_cases([row, dict(row)])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=dwarf python3 -m pytest -q tests/test_opcert_cases.py -v`
Expected: FAIL (module and data file absent).

- [ ] **Step 3: Create the data file**

`dwarf/corpora/opcert/opcert-header-cases-v1.json` — a JSON array. Rule + boundary rows. Expected reasons use the real error names (cardano-node `OCERT`/KES errors, Amaru `header.rs` variants):

```json
[
  {"id": "valid-control", "family": "rule", "mutation": "NoMutation", "rule": "none",
   "expected_verdict": "accept", "expected_reason": {"cardano-node": null, "amaru": null}},
  {"id": "cold-key-unauthorized", "family": "rule", "mutation": "MutateColdKey", "rule": "cold-key-authorization",
   "expected_verdict": "reject", "expected_reason": {"cardano-node": "InvalidSignatureOCERT", "amaru": "InvalidSignature"}},
  {"id": "counter-behind", "family": "rule", "mutation": "MutateCounterUnder", "rule": "counter-monotonicity",
   "expected_verdict": "reject", "expected_reason": {"cardano-node": "CounterTooSmallOCERT", "amaru": "SequenceNumberTooSmall"}},
  {"id": "counter-jump", "family": "rule", "mutation": "MutateCounterOver1", "rule": "counter-monotonicity",
   "expected_verdict": "reject", "expected_reason": {"cardano-node": "CounterOverIncrementedOCERT", "amaru": "SequenceNumberTooFarAhead"}},
  {"id": "counter-plus-one", "family": "boundary", "mutation": "NoMutation", "rule": "counter-monotonicity",
   "expected_verdict": "accept", "expected_reason": {"cardano-node": null, "amaru": null}},
  {"id": "kes-before-window", "family": "rule", "mutation": "MutateKESPeriod", "rule": "kes-window",
   "expected_verdict": "reject", "expected_reason": {"cardano-node": "KESBeforeStartOCERT", "amaru": "OpCertKesPeriodTooOld"}},
  {"id": "kes-after-window", "family": "rule", "mutation": "MutateKESPeriodBefore", "rule": "kes-window",
   "expected_verdict": "reject", "expected_reason": {"cardano-node": "KESAfterEndOCERT", "amaru": "OpCertKesPeriodTooLarge"}},
  {"id": "hot-key-mismatch", "family": "rule", "mutation": "MutateKESKey", "rule": "kes-hot-key-binding",
   "expected_verdict": "reject", "expected_reason": {"cardano-node": "InvalidKesSignatureOCERT", "amaru": "InvalidKesSignature"}}
]
```

Note: `counter-plus-one` documents the accepted boundary (counter incremented by exactly one) and is served as a valid header; the peer applies the +1 counter without a rule break. Confirm each Amaru variant name against `~/codebases/amaru` `crates/amaru-ouroboros/src/praos/header.rs` on the box during Task 3; fix any that differ and re-run this test.

- [ ] **Step 4: Write the loader**

```python
# dwarf/scripts/opcert_cases.py
from __future__ import annotations
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
DEFAULT_PATH = DWARF_ROOT / "corpora" / "opcert" / "opcert-header-cases-v1.json"
MUTATIONS = frozenset({
    "NoMutation", "MutateColdKey", "MutateCounterUnder", "MutateCounterOver1",
    "MutateKESPeriodBefore", "MutateKESPeriod", "MutateKESKey",
})

def load_cases(path=None) -> list[dict]:
    cases = json.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    validate_cases(cases)
    return cases

def validate_cases(cases: list[dict]) -> None:
    seen: set[str] = set()
    for c in cases:
        if c["id"] in seen:
            raise ValueError(f"duplicate case id: {c['id']}")
        seen.add(c["id"])
        if c["mutation"] not in MUTATIONS:
            raise ValueError(f"unknown mutation: {c['mutation']}")
        if c["expected_verdict"] not in ("accept", "reject"):
            raise ValueError(f"bad verdict: {c['expected_verdict']}")
        if set(c["expected_reason"]) != {"cardano-node", "amaru"}:
            raise ValueError(f"expected_reason must key both nodes: {c['id']}")
        if c["expected_verdict"] == "accept" and any(c["expected_reason"].values()):
            raise ValueError(f"accept case must have null reasons: {c['id']}")
        if c["expected_verdict"] == "reject" and not all(c["expected_reason"].values()):
            raise ValueError(f"reject case must name a reason per node: {c['id']}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=dwarf python3 -m pytest -q tests/test_opcert_cases.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dwarf/corpora/opcert/opcert-header-cases-v1.json dwarf/scripts/opcert_cases.py tests/test_opcert_cases.py
git commit -m "feat: opcert header case table + loader (rule + boundary)"
```

---

## Task 2: Header-validation tap parsers (verdict + reason from logs)

**Files:**
- Create: `dwarf/scripts/header_validation_parse.py`
- Test: `tests/test_header_validation_parse.py`

**Interfaces:**
- Produces: `parse_cardano_header_events(lines: Iterable[str]) -> list[dict]` and `parse_amaru_header_events(lines: Iterable[str]) -> list[dict]`; each event `{"header_hash": str, "verdict": "accepted"|"rejected", "reason": str|None, "at": str|None}`. `verdict_by_hash(events) -> dict[str, dict]` (last event per hash wins).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_header_validation_parse.py
from scripts import header_validation_parse as hv

def test_cardano_rejected_header_reason():
    line = ('{"at":"2026-09-23T00:00:01Z","ns":"ChainDB.AddBlockEvent.AddBlockValidation.InvalidBlock",'
            '"data":{"block":{"hash":"abcd"},"error":"...CounterTooSmallOCERT..."}}')
    events = hv.parse_cardano_header_events([line])
    assert events == [{"header_hash": "abcd", "verdict": "rejected",
                       "reason": "CounterTooSmallOCERT", "at": "2026-09-23T00:00:01Z"}]

def test_cardano_accepted_header():
    line = ('{"at":"2026-09-23T00:00:02Z","ns":"ChainDB.AddBlockEvent.AddedToCurrentChain",'
            '"data":{"newtip":"ef01"}}')
    assert hv.parse_cardano_header_events([line]) == [
        {"header_hash": "ef01", "verdict": "accepted", "reason": None, "at": "2026-09-23T00:00:02Z"}]

def test_amaru_rejected_header_reason():
    line = ('{"timestamp":"2026-09-23T00:00:03Z","level":"ERROR","fields":{'
            '"error":"header validation failed: SequenceNumberTooSmall","header_hash":"beef",'
            '"outcome":"invalid_header","message":"chain.header_rejected"},"target":"amaru::consensus"}')
    assert hv.parse_amaru_header_events([line]) == [
        {"header_hash": "beef", "verdict": "rejected",
         "reason": "SequenceNumberTooSmall", "at": "2026-09-23T00:00:03Z"}]

def test_amaru_accepted_header():
    line = ('{"timestamp":"2026-09-23T00:00:04Z","level":"INFO","fields":{'
            '"message":"chain.tip_accepted","outcome":"new_tip","header_hash":"cafe"},"target":"amaru::consensus"}')
    assert hv.parse_amaru_header_events([line]) == [
        {"header_hash": "cafe", "verdict": "accepted", "reason": None, "at": "2026-09-23T00:00:04Z"}]

def test_verdict_by_hash_last_wins():
    events = [{"header_hash": "aa", "verdict": "rejected", "reason": "R", "at": "1"},
              {"header_hash": "aa", "verdict": "accepted", "reason": None, "at": "2"}]
    assert hv.verdict_by_hash(events)["aa"]["verdict"] == "accepted"

def test_unparseable_lines_are_skipped():
    assert hv.parse_cardano_header_events(["not json", ""]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=dwarf python3 -m pytest -q tests/test_header_validation_parse.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the parser**

```python
# dwarf/scripts/header_validation_parse.py
from __future__ import annotations
import json, re
from typing import Iterable

_OCERT = re.compile(r"(KESBeforeStartOCERT|KESAfterEndOCERT|CounterTooSmallOCERT|"
                    r"CounterOverIncrementedOCERT|InvalidKesSignatureOCERT|InvalidSignatureOCERT)")
_AMARU = re.compile(r"(OpCertKesPeriodTooLarge|OpCertKesPeriodTooOld|InvalidKesSignature|"
                    r"SequenceNumberTooSmall|SequenceNumberTooFarAhead|InvalidSignature)")

def _loads(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None

def parse_cardano_header_events(lines: Iterable[str]) -> list[dict]:
    out = []
    for line in lines:
        d = _loads(line)
        if not isinstance(d, dict):
            continue
        ns = str(d.get("ns", "")); data = d.get("data") or {}
        if ns.endswith("AddedToCurrentChain") or ns.endswith("SwitchedToAFork"):
            h = data.get("newtip") or (data.get("block") or {}).get("hash")
            if h:
                out.append({"header_hash": str(h), "verdict": "accepted", "reason": None, "at": d.get("at")})
        elif "InvalidBlock" in ns:
            h = (data.get("block") or {}).get("hash") or data.get("block")
            m = _OCERT.search(json.dumps(data))
            if h:
                out.append({"header_hash": str(h), "verdict": "rejected",
                            "reason": m.group(1) if m else None, "at": d.get("at")})
    return out

def parse_amaru_header_events(lines: Iterable[str]) -> list[dict]:
    out = []
    for line in lines:
        d = _loads(line)
        if not isinstance(d, dict):
            continue
        f = d.get("fields") or {}
        msg = str(f.get("message", "")); h = f.get("header_hash")
        if not h:
            continue
        if f.get("outcome") == "new_tip" or msg == "chain.tip_accepted":
            out.append({"header_hash": str(h), "verdict": "accepted", "reason": None, "at": d.get("timestamp")})
        elif f.get("outcome") == "invalid_header" or "header_rejected" in msg or "validation failed" in str(f.get("error", "")):
            m = _AMARU.search(str(f.get("error", "")))
            out.append({"header_hash": str(h), "verdict": "rejected",
                        "reason": m.group(1) if m else None, "at": d.get("timestamp")})
    return out

def verdict_by_hash(events: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for e in events:
        result[e["header_hash"]] = e
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=dwarf python3 -m pytest -q tests/test_header_validation_parse.py -v`
Expected: PASS.

Note: the exact cardano-node `ns` and Amaru `message`/`outcome` strings must be confirmed against real logs in Task 8; if they differ, update the regexes and these fixtures together.

- [ ] **Step 5: Commit**

```bash
git add dwarf/scripts/header_validation_parse.py tests/test_header_validation_parse.py
git commit -m "feat: header-validation verdict/reason log parsers (cardano-node + amaru)"
```

---

## Task 3: Opcert header peer (Haskell) — case application

**Files:**
- Create: `antithesis/components/dwarf-opcert-adversary/` (cabal package: `src/DwarfOpcertAdversary.hs`, `app/Main.hs`, `test/Main.hs`, `dwarf-opcert-adversary.cabal`, `cabal.project`, `Dockerfile`, `build-image.sh`) — copy the layout of `antithesis/components/dwarf-kes-adversary/`.
- Reference: `antithesis/components/dwarf-kes-adversary/src/DwarfKesAdversary.hs` (header decode/encode helpers, `advancingChainSyncServer`).

**Interfaces:**
- Produces: an executable `dwarf-opcert-adversary` with `serve --upstream HOST:PORT --listen-port PORT --pool-dir DIR --case CASE_ID --min-slot SLOT --evidence FILE`. It follows the upstream honest chain, at the pool's leader slot builds a header extending the tip, applies the case's mutation (re-signing with the pool KES/cold keys in `--pool-dir`), serves it to the one downstream, and appends a JSON ground-truth line per served header to `--evidence`: `{"kind":"opcert_case_served","case":ID,"mutation":M,"header_hash":H,"parent":{"slot":S,"hash":PH},"slot":SLOT,"pool":POOLID,"expected_verdict":V}`.
- Consumes: the case ids and mutation names from Task 1 (kept in sync by string).

- [ ] **Step 1: Write the failing Haskell test**

```haskell
-- test/Main.hs — pure test of the mutation dispatch and evidence shape
import DwarfOpcertAdversary (Case(..), parseCase, applyMutation, MutationResult(..))
import qualified Data.ByteString as BS
main :: IO ()
main = do
  -- parseCase maps every table id to a known mutation
  mapM_ (\i -> maybe (error ("unmapped case " <> i)) (const (pure ())) (parseCase i))
        ["valid-control","cold-key-unauthorized","counter-behind","counter-jump",
         "counter-plus-one","kes-before-window","kes-after-window","hot-key-mismatch"]
  -- valid-control must not alter the header bytes
  let hdr = BS.replicate 512 0x01
  case applyMutation (maybe (error "no case") id (parseCase "valid-control")) hdr of
    Unchanged out -> if out == hdr then pure () else error "control changed bytes"
    _ -> error "control must be Unchanged"
  putStrLn "ok"
```

- [ ] **Step 2: Build and run to verify it fails**

Run on cardano-box (the Haskell toolchain + ouroboros deps live there; see `dwarf-kes-adversary/build-image.sh`):
```bash
cd antithesis/components/dwarf-opcert-adversary && cabal test 2>&1 | tail -20
```
Expected: FAIL (module/functions absent).

- [ ] **Step 3: Implement the library**

Extend the KES adversary's header codec. Add `data Case`, `parseCase :: String -> Maybe Case` (the 8 ids → 7 mutations), and `applyMutation :: Case -> ... -> MutationResult` which, for each non-control case, edits the decoded opcert field (cold-key sig / counter / KES-period / KES-verification-key) and **re-signs** the header with the pool KES key from `--pool-dir`, then re-encodes. `valid-control` and `counter-plus-one` produce a validly signed header (the latter with counter+1). Reuse `decHeader`/`encHeader` from `DwarfKesAdversary`. Keep `-Wall -Werror`.

Detailed opcert field editing uses the ouroboros-consensus `OCert` accessors; mirror the mutations in `~/codebases/*/ouroboros-consensus` `gen-header` (the same generator dwarf-v4 used). Confirm the Amaru reason strings from `~/codebases/amaru/crates/amaru-ouroboros/src/praos/header.rs` and reconcile with Task 1.

- [ ] **Step 4: Build and run to verify it passes**

Run: `cabal test 2>&1 | tail -5`  Expected: `ok`.

- [ ] **Step 5: Build the image**

```bash
bash antithesis/components/dwarf-opcert-adversary/build-image.sh
```
Expected: image `dwarf/opcert-adversary:<tag>` built; record the digest.

- [ ] **Step 6: Commit**

```bash
git add antithesis/components/dwarf-opcert-adversary
git commit -m "feat: opcert header peer applies one opcert mutation per case and re-signs"
```

---

## Task 4: `runtime_opcert_header_cases` primitive + script

**Files:**
- Create: `dwarf/scripts/runtime_opcert_header_cases.py`
- Modify: `dwarf/profile_manager/primitives.py` (add `RuntimeOpcertHeaderCases(LoadPrimitive)` near the calibration primitives ~line 13466)
- Create: `dwarf/primitives/load/runtime_opcert_header_cases.schema.json`
- Modify: `dwarf/primitives/registry.json` (add the entry; keep file formatting — insert alphabetically by hand, do not reserialize)
- Test: `tests/test_runtime_opcert_header_cases.py`

**Interfaces:**
- Consumes: `opcert_cases.load_cases` (Task 1), `header_validation_parse.verdict_by_hash` (Task 2), the peer container (Task 3), the profile `runtime.json` (`compose_project`, `actual_topology`).
- Produces: `result.json` `{schema_version, target, case_set, cases:[{case,expected_verdict,expected_reason,served_hash,observed_verdict,observed_reason,status}], attempts.ndjson}` where `status ∈ {matched, mismatch, inconclusive}`. `run_opcert_header_cases(runtime_root, target_node, cases_path, output_dir, ...) -> dict`.

- [ ] **Step 1: Write the failing test** (pure join logic, no devnet)

```python
# tests/test_runtime_opcert_header_cases.py
from scripts import runtime_opcert_header_cases as ohc

def _case(cid, verdict, reason):
    return {"id": cid, "expected_verdict": verdict,
            "expected_reason": {"cardano-node": reason, "amaru": reason}}

def test_join_matches_reject_with_right_reason():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    served = {"counter-behind": "h1"}
    observed = {"h1": {"verdict": "rejected", "reason": "CounterTooSmallOCERT"}}
    rows = ohc.join_cases(cases, served, observed, implementation="cardano-node")
    assert rows[0]["status"] == "matched"

def test_wrong_reason_is_mismatch_not_pass():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    served = {"counter-behind": "h1"}
    observed = {"h1": {"verdict": "rejected", "reason": "InvalidKesSignatureOCERT"}}
    rows = ohc.join_cases(cases, served, observed, implementation="cardano-node")
    assert rows[0]["status"] == "mismatch"

def test_accepted_bad_case_is_mismatch():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    served = {"counter-behind": "h1"}
    observed = {"h1": {"verdict": "accepted", "reason": None}}
    assert ohc.join_cases(cases, served, observed, "cardano-node")[0]["status"] == "mismatch"

def test_unserved_case_is_inconclusive():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    assert ohc.join_cases(cases, {}, {}, "cardano-node")[0]["status"] == "inconclusive"

def test_served_but_unobserved_is_inconclusive():
    cases = [_case("counter-behind", "reject", "CounterTooSmallOCERT")]
    rows = ohc.join_cases(cases, {"counter-behind": "h1"}, {}, "cardano-node")
    assert rows[0]["status"] == "inconclusive"

def test_valid_control_accepted_is_matched():
    cases = [_case("valid-control", "accept", None)]
    rows = ohc.join_cases(cases, {"valid-control": "h0"},
                          {"h0": {"verdict": "accepted", "reason": None}}, "amaru")
    assert rows[0]["status"] == "matched"
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=dwarf python3 -m pytest -q tests/test_runtime_opcert_header_cases.py -v`  Expected: FAIL.

- [ ] **Step 3: Implement `join_cases` and the script skeleton**

```python
# dwarf/scripts/runtime_opcert_header_cases.py  (join_cases shown; full script wires the peer + taps)
def join_cases(cases, served_hash_by_case, observed_by_hash, implementation):
    rows = []
    for c in cases:
        served = served_hash_by_case.get(c["id"])
        obs = observed_by_hash.get(served) if served else None
        expected_v = c["expected_verdict"]
        expected_r = c["expected_reason"][implementation]
        if served is None or obs is None:
            status = "inconclusive"
        elif expected_v == "accept":
            status = "matched" if obs["verdict"] == "accepted" else "mismatch"
        else:  # reject
            status = "matched" if (obs["verdict"] == "rejected" and obs.get("reason") == expected_r) else "mismatch"
        rows.append({"case": c["id"], "expected_verdict": expected_v, "expected_reason": expected_r,
                     "served_hash": served, "observed_verdict": (obs or {}).get("verdict"),
                     "observed_reason": (obs or {}).get("reason"), "status": status})
    return rows
```
The `run_opcert_header_cases` driver: resolve the target container/port from `runtime.json`; for each case, invoke the peer container (Task 3 image) with `--case` and `--pool-dir` (devnet pool keys), capture the served evidence line, capture the target's logs over the window (`docker logs --since`), parse them with Task 2, then `join_cases`. Write `result.json` + `attempts.ndjson`. Follow the container/log patterns in `dwarf/scripts/runtime_amaru_measurement_calibration.py`.

- [ ] **Step 4: Run to verify pass**  Run: same pytest. Expected: PASS.

- [ ] **Step 5: Register primitive + schema** — add `RuntimeOpcertHeaderCases(LoadPrimitive)` in `primitives.py` (build the peer command, run it via `subprocess.run(..., cwd=DWARF_ROOT, env=telemetry env)`, log started/completed like `RuntimeControlledPlutusTransactions`); add strict `additionalProperties:false` schema (`profile_id` XOR `runtime_root`, `target_node`, `cases_path`, `case_ids` optional, `output_dir`, `timeout_seconds`, `expect_exit`); add the registry entry `{module: profile_manager.primitives, class: RuntimeOpcertHeaderCases, family: load, runtimes:[devnet], supports:[cardano-node,amaru], version:0.1.0}`.

- [ ] **Step 6: Commit**

```bash
git add dwarf/scripts/runtime_opcert_header_cases.py dwarf/profile_manager/primitives.py \
        dwarf/primitives/load/runtime_opcert_header_cases.schema.json dwarf/primitives/registry.json \
        tests/test_runtime_opcert_header_cases.py
git commit -m "feat: runtime_opcert_header_cases primitive + verdict join"
```

---

## Task 5: Header-validation measurement taps (stock, both nodes) + patched scaffolds

**Files:**
- Create: `dwarf/measurements/amaru-stock-header-validation.yaml`, `dwarf/measurements/cardano-stock-header-validation.yaml`
- Create (scaffold only): `dwarf/measurements/amaru-patched-header-validation.yaml`, `dwarf/measurements/cardano-patched-header-validation.yaml`
- Modify: `dwarf/profile_manager/measurement_collectors/amaru_stock.py`, `cardano_stock.py` (add a header-validation collector using Task 2 parsers), and the factories (`amaru_factory.py`, `cardano_factory.py`) to register the new ids
- Modify: `dwarf/measurement-coverage/v1.yaml` (map header-validation rule → new scenarios; mark patched taps as scaffolded/unavailable)
- Test: `tests/test_header_validation_collector.py`, and extend `tests/test_measurement_catalog.py`

**Interfaces:**
- Produces: collector id `amaru-stock-header-validation` / `cardano-stock-header-validation` whose `result.json` carries `{schema_version, measurement_id, source_revision, verdicts:{accepted:int,rejected:int}, by_reason:{reason:count}, events:[...]}`; patched collectors return `{status:"unavailable", reason:"patched header-validation instrumentation not built"}`.

- [ ] **Step 1: Write the failing collector test** — feed the collector a fixture ndjson of real-shaped log lines (reuse Task 2 fixtures) and assert it finalizes with the verdict counts and never emits a zero for a missing signal (missing → `unavailable`). Also assert the patched collector returns `status == "unavailable"` with a reason.

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** the stock header-validation collectors (call `header_validation_parse`), register them in the factories, write the four measurement YAMLs (stock real; patched `target_modes:[patched]`, `collection_mode: patched-node`, `default_enabled:false`, description states it is scaffolded). Add all four to `compatibility.versions` for the current Amaru (0912/0918) and Cardano (11.1.2) targets, matching the existing tap YAMLs.

- [ ] **Step 4: Run to verify pass**, and update `tests/test_measurement_catalog.py` expected id sets (+4) and per-tap version lists.

- [ ] **Step 5: Commit**

```bash
git add dwarf/measurements/*header-validation.yaml dwarf/profile_manager/measurement_collectors/ \
        dwarf/measurement-coverage/v1.yaml tests/test_header_validation_collector.py tests/test_measurement_catalog.py
git commit -m "feat: stock header-validation taps (both nodes) + patched scaffolds"
```

---

## Task 6: Assertions `opcert_case_verdicts_match_expected` and `opcert_verdicts_agree`

**Files:**
- Modify: `dwarf/profile_manager/primitives.py` (two `AssertionPrimitive` classes near the calibration assertions)
- Create: `dwarf/primitives/assertion/opcert_case_verdicts_match_expected.schema.json`, `dwarf/primitives/assertion/opcert_verdicts_agree.schema.json`
- Modify: `dwarf/primitives/registry.json`, `dwarf/scripts/gen_reference.py` (DESC entries)
- Test: `tests/test_opcert_assertions.py`

**Interfaces:**
- Consumes: the primitive's `result.json` (Task 4). `opcert_case_verdicts_match_expected` passes iff every case row is `matched` (fail on any `mismatch` or `inconclusive`). `opcert_verdicts_agree` (mixed) reads two per-node result files and passes iff every case has the same verdict on both nodes.

- [ ] **Step 1: Write failing tests** — `matched`-only → pass; one `mismatch` → fail with the case named; one `inconclusive` → fail (fail-closed); agree with equal verdicts → pass; agree with one node accepting where the other rejects → fail and flag the case.

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** both assertions (dict result `{primitive, params, evaluated_value, data_points_used, result}` like `AmaruMeasurementBoundaryProven`).

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Register + docs** — registry entries (family assertion, supports both nodes), schemas, DESC lines; `python3 dwarf/scripts/gen_reference.py dwarf dwarf/docs`.

- [ ] **Step 6: Commit**

```bash
git add dwarf/profile_manager/primitives.py dwarf/primitives/assertion/opcert_*.schema.json \
        dwarf/primitives/registry.json dwarf/scripts/gen_reference.py dwarf/docs/primitives-reference.* \
        tests/test_opcert_assertions.py
git commit -m "feat: opcert verdict-match and cross-node agreement assertions"
```

---

## Task 7: Fail-close the praos header stub + repoint TM-013

**Files:**
- Modify: `dwarf/scripts/runtime_hardening_probe.py:651` (`praos_header_assertion_probe` branch)
- Modify: the coverage data mapping TM-013/RR-013 to scenarios (`dwarf/profile_manager/data/coverage.py` or `measurement-coverage/v1.yaml` — grep `TM-013`)
- Test: `tests/test_praos_header_probe_failclosed.py`, and adjust any test asserting the old stub value

**Interfaces:**
- Produces: the `praos_header_assertion_probe` mode returns `{"status": "unavailable", "reason": "superseded by runtime_opcert_header_cases; header-rejection is proven by the opcert scenarios"}` instead of `{"header_rejected": True, ...}`.

- [ ] **Step 1: Write failing test** — assert the probe result has `status == "unavailable"` and no `header_rejected: True`.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Edit line 651** to the unavailable result; repoint TM-013/RR-013 to `opcert-header-validation-cases-*`.
- [ ] **Step 4: Run to verify pass**; grep tests for the old value and update.
- [ ] **Step 5: Commit** `fix: fail-close praos header stub; TM-013 points to opcert scenarios`.

---

## Task 8: Scenarios + profile wiring + one real run per topology

**Files:**
- Create: `dwarf/scenarios/opcert-header-validation-cases-amaru-20260918.yaml`, `...-cardano-1112.yaml`, `...-mixed-1112-amaru.yaml`, `...-mixed-1112-cardano.yaml`
- Modify: the amaru-control / compose substrate to attach the peer container to a target only (grep `runtime_amaru_control_substrate.py` for how extra services are added; add the opcert peer like the KES bundle adds its proxy)
- Test: extend `tests/test_profile_version_resolution.py` only if a profile field is added (avoid if possible); scenario gate

**Interfaces:**
- Consumes: Tasks 4–6. Each scenario: `runtime` devnet, the topology profile, `measurement_profile` the matching security profile, `setup` `runtime_verify_exact_target`, `load` `runtime_opcert_header_cases` (+ the header-validation taps auto-attach), `assertions` `opcert_case_verdicts_match_expected` (+ `opcert_verdicts_agree` and `target_progress_continues` on mixed), `evidence_intent: finding-validation`.

- [ ] **Step 1** Write the four scenarios; `python3 dwarf/cardano-profile scenario validate --semantic <each>` → OK.
- [ ] **Step 2** `python3 dwarf/scripts/validate_scenarios.py --strict` → only pre-existing warnings.
- [ ] **Step 3** Wire the peer container into the substrate; unit-test the compose transform if one exists.
- [ ] **Step 4** Push to box; deploy each profile; run each scenario. Confirm: valid-control accepted; each rule case rejected with the expected reason on each node; mixed agreement; target keeps following the chain; 14/13 taps finalized incl. the new header-validation tap; SARIF written. Capture run ids.
- [ ] **Step 5** Confirm the exact Amaru reason strings from the live logs match Task 1; fix the table + parser regex if any differ; re-run the affected scenario.
- [ ] **Step 6** Commit the scenarios + substrate wiring + any reason-string corrections.

---

## Task 9: Coverage, docs, full-suite gate, deliver

- [ ] **Step 1** Regenerate docs (`gen_reference.py`); update pinned counts (scenarios +4, primitives +1, assertions +2) in the tests that pin them (`test_operate_primitives.py`, `test_dashboard_freshness.py`, `test_measurement_catalog.py`, `test_landing_run_wizard.py`, `test_measurement_coverage_learn.py`, `test_definition_catalogs.py`).
- [ ] **Step 2** Full suite on branch vs `main`; only the 5 pre-existing failures allowed.
- [ ] **Step 3** Write `dwarf/docs/finding-*.md` + `reports/*-evidence/` only if a real run shows a divergence or an accepted bad case; otherwise write a short coverage note under `dwarf/docs/` describing the new opcert coverage and its evidence runs.
- [ ] **Step 4** Push branch to internal `origin`; open a merge request; rebuild+deploy the dashboard image (per the session's deploy method) so the Learn/coverage pages reflect the new TM-013 evidence.
- [ ] **Step 5** Final commit + MR description.
