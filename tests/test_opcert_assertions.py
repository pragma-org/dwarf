import json
from pathlib import Path
from profile_manager.primitives import OpcertCaseVerdictsMatchExpected, OpcertVerdictsAgree


def _write(run_dir, subdir, cases, target=None):
    d = run_dir / subdir
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({
        "schema_version": 1, "case_set": "opcert-header-cases-v1",
        "target": target or {"implementation": "cardano-node", "version": "11.1.2"},
        "cases": cases}), encoding="utf-8")


class _Handle:
    def __init__(self, run_dir):
        self.run_dir = run_dir


def test_match_passes_when_all_matched(tmp_path):
    _write(tmp_path, "outputs/opcert-header-cases",
           [{"case": "valid-control", "status": "matched", "observed_verdict": "accepted"},
            {"case": "counter-behind", "status": "matched", "observed_verdict": "rejected"}])
    out = OpcertCaseVerdictsMatchExpected(params={}).evaluate(_Handle(tmp_path))
    assert out["result"] == "pass"


def test_match_fails_on_mismatch(tmp_path):
    _write(tmp_path, "outputs/opcert-header-cases",
           [{"case": "counter-behind", "status": "mismatch", "observed_verdict": "accepted"}])
    out = OpcertCaseVerdictsMatchExpected(params={}).evaluate(_Handle(tmp_path))
    assert out["result"] == "fail" and "counter-behind" in out["evaluated_value"]["mismatched"]


def test_match_fails_closed_when_missing(tmp_path):
    out = OpcertCaseVerdictsMatchExpected(params={}).evaluate(_Handle(tmp_path))
    assert out["result"] == "fail"


def test_agree_passes_when_verdicts_equal(tmp_path):
    rows = [{"case": "counter-behind", "status": "matched", "observed_verdict": "rejected"}]
    _write(tmp_path, "outputs/opcert-header-cases-cardano", rows,
           {"implementation": "cardano-node", "version": "11.1.2"})
    _write(tmp_path, "outputs/opcert-header-cases-amaru", rows,
           {"implementation": "amaru", "version": "10.11.20260918"})
    out = OpcertVerdictsAgree(params={}).evaluate(_Handle(tmp_path))
    assert out["result"] == "pass"


def test_agree_fails_on_disagreement(tmp_path):
    _write(tmp_path, "outputs/opcert-header-cases-cardano",
           [{"case": "counter-behind", "status": "matched", "observed_verdict": "rejected"}],
           {"implementation": "cardano-node", "version": "11.1.2"})
    _write(tmp_path, "outputs/opcert-header-cases-amaru",
           [{"case": "counter-behind", "status": "mismatch", "observed_verdict": "accepted"}],
           {"implementation": "amaru", "version": "10.11.20260918"})
    out = OpcertVerdictsAgree(params={}).evaluate(_Handle(tmp_path))
    assert out["result"] == "fail" and "counter-behind" in out["evaluated_value"]["disagreements"]
