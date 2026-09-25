"""Evidence contract: the soak forger's served line carries the seed-derived spec.

The soak forger (``serve-case --case-spec``) echoes the full spec on both the
``opcert_case_started`` and ``opcert_case_served`` evidence lines. This pins the
Python side of that contract: the existing ``served_hash_by_case`` still yields
the served header hash, and ``served_records_by_case`` exposes ``record["spec"]``
with ``seed``, ``base_case`` and ``params`` so a finding row is replayable.
"""
import json
from scripts.runtime_opcert_header_cases import served_hash_by_case, served_records_by_case

# A served evidence line exactly as the extended forger emits it (see
# app/Main.hs runCase: kind=opcert_case_served, header_hash, spec{...}).
_SERVED_LINE = json.dumps({
    "kind": "opcert_case_served",
    "case": "encoding-form-000118",
    "mutation": "NoMutation",
    "header_hash": "abc123",
    "slot": 4200,
    "pool": "pool1",
    "expected_verdict": "accept",
    "encoding_form": "trailing-bytes",
    "deviant_bytes": 1287,
    "spec": {"base_case": "valid-control", "seed": 267515629,
             "params": {"encoding_form": "trailing-bytes", "trailing_len": 3}},
})


def test_served_evidence_echoes_spec():
    # the hash is still recoverable via the existing helper
    assert served_hash_by_case([_SERVED_LINE]) == {"encoding-form-000118": "abc123"}
    # and the full record exposes the seed-derived spec for replay
    records = served_records_by_case([_SERVED_LINE])
    spec = records["encoding-form-000118"]["spec"]
    assert spec["seed"] == 267515629
    assert spec["base_case"] == "valid-control"
    assert spec["params"]["encoding_form"] == "trailing-bytes"


def test_started_line_without_hash_is_not_served():
    started = json.dumps({"kind": "opcert_case_started", "case": "encoding-form-000118",
                          "spec": {"base_case": "valid-control", "seed": 1, "params": {}}})
    assert served_hash_by_case([started]) == {}
    assert served_records_by_case([started]) == {}
