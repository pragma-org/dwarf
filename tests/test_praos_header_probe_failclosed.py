from scripts import runtime_hardening_probe as hp


def test_praos_header_probe_is_unavailable_not_fabricated():
    out = hp.apply_hardening_mode(metadata={}, mode="praos_header_assertion_probe", config={})
    result = out["result"]
    assert result.get("status") == "unavailable"
    assert "header_rejected" not in result
    assert result.get("reason")


def test_assertion_reports_unavailable_as_failclosed_not_pass():
    from profile_manager.primitives import PraosHeaderAssertionRejected

    payload = {"outcome": "ok", "result": {"status": "unavailable", "reason": "superseded by opcert scenarios"}}

    class Handle:
        run_dir = "/tmp"

    def _latest(handle, **kwargs):
        return 1, payload

    import profile_manager.primitives as prim
    orig = prim._latest_completed_payload
    prim._latest_completed_payload = _latest
    try:
        out = PraosHeaderAssertionRejected(params={}).evaluate(Handle())
    finally:
        prim._latest_completed_payload = orig
    assert out["result"] == "fail"
    assert "superseded" in out["note"]
    assert out["evaluated_value"].get("status") == "unavailable"
