import json

from profile_manager.primitives import OpcertSoakInvariantHolds, OpcertSoakVerdictsAgree


class _Handle:
    def __init__(self, run_dir):
        self.run_dir = run_dir


def _write(tmp, body, name="opcert-soak"):
    p = tmp / "outputs" / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "result.json").write_text(json.dumps(body))
    return f"outputs/{name}/result.json"


def test_invariant_holds_passes_on_clean_conclusive(tmp_path):
    rel = _write(tmp_path, {"conclusive": 400, "pass": True, "mismatches": [], "disagreements": []})
    res = OpcertSoakInvariantHolds(params={"report_path": rel}).evaluate(_Handle(tmp_path))
    assert res["result"] == "pass"


def test_invariant_fails_on_zero_conclusive(tmp_path):
    rel = _write(tmp_path, {"conclusive": 0, "pass": False, "mismatches": [], "disagreements": []})
    res = OpcertSoakInvariantHolds(params={"report_path": rel}).evaluate(_Handle(tmp_path))
    assert res["result"] == "fail"


def test_invariant_fails_on_mismatch(tmp_path):
    rel = _write(tmp_path, {"conclusive": 400, "pass": False,
                            "mismatches": [{"iteration": 3, "spec": {}}], "disagreements": []})
    res = OpcertSoakInvariantHolds(params={"report_path": rel}).evaluate(_Handle(tmp_path))
    assert res["result"] == "fail"


def test_agree_passes_on_clean_conclusive(tmp_path):
    rel = _write(tmp_path, {"conclusive": 400, "pass": True, "mismatches": [], "disagreements": []})
    res = OpcertSoakVerdictsAgree(params={"report_path": rel}).evaluate(_Handle(tmp_path))
    assert res["result"] == "pass"


def test_agree_fails_on_disagreement(tmp_path):
    rel = _write(tmp_path, {"conclusive": 400, "pass": False, "mismatches": [],
                            "disagreements": [{"iteration": 7, "verdicts": {"node1": "accepted", "amaru-relay-1": "rejected"}}]})
    res = OpcertSoakVerdictsAgree(params={"report_path": rel}).evaluate(_Handle(tmp_path))
    assert res["result"] == "fail"


def test_agree_unavailable_report_fails_closed(tmp_path):
    res = OpcertSoakVerdictsAgree(params={"report_path": "outputs/missing/result.json"}).evaluate(_Handle(tmp_path))
    assert res["result"] == "fail"
