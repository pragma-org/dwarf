import sp1_closure
import sp1_merge_registry
import json
from pathlib import Path


def test_closure_classification_is_total(tmp_path, monkeypatch):
    # Invariant that holds before AND after the restore: every may->v4
    # delta scenario is classified exactly once, and cardano-node splits
    # cleanly into eligible + blocked. (Counts shrink as SP1/SP3 restore
    # scenarios into v4 — only the totality invariant is permanent.)
    may = tmp_path / "may/dwarf"
    current = tmp_path / "current/dwarf"
    (may / "scenarios").mkdir(parents=True)
    (may / "primitives").mkdir()
    (current / "scenarios").mkdir(parents=True)
    (may / "primitives/registry.json").write_text(
        json.dumps({"primitives": {"known": {"family": "load"}}})
    )
    (may / "scenarios/cardano.yaml").write_text(
        json.dumps({"target": {"implementation": "cardano-node"}, "load": [{"primitive": "known"}]})
    )
    (may / "scenarios/amaru.yaml").write_text(
        json.dumps({"target": {"implementation": "amaru"}, "load": [{"primitive": "known"}]})
    )
    monkeypatch.setattr(sp1_closure, "MAY", may)
    monkeypatch.setattr(sp1_closure, "V4", current)
    r = sp1_closure.compute()
    assert r["eligible"] == ["cardano.yaml"]
    assert r["deferred"] == ["amaru.yaml"]
    assert len(r["eligible"]) + len(r["blocked"]) + len(r["deferred"]) == len(r["delta"])
    assert len(r["cardano"]) == len(r["eligible"]) + len(r["blocked"])

    reg = set(json.loads((Path(sp1_closure.MAY) / "primitives" / "registry.json").read_text()).get("primitives", {}).keys())
    # eligible closure references only registered primitives;
    # blocked scenarios only ever reference unregistered ones.
    assert all(p in reg for p in r["primitives"])
    for prims in r["blocked"].values():
        assert all(p not in reg for p in prims)


def test_merge_is_additive_and_conflict_safe():
    v4 = {"primitives": {"keep": {"version": "0.1.0"}}}
    may = {"primitives": {"keep": {"version": "9.9.9"}, "new": {"version": "0.1.0"}}}
    merged, conflicts = sp1_merge_registry.merge_registry(v4, may, ["keep", "new", "missing"])
    assert merged["primitives"]["keep"]["version"] == "0.1.0"   # v4 kept, not clobbered
    assert merged["primitives"]["new"]["version"] == "0.1.0"    # new added
    assert any("keep" in c for c in conflicts)                  # conflict flagged
    assert any("missing" in c for c in conflicts)               # missing flagged
