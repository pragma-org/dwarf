import json
from pathlib import Path

from jsonschema import Draft202012Validator


SCHEMA = (
    Path(__file__).parents[1]
    / "dwarf"
    / "spec"
    / "v1"
    / "patched-timing-event.schema.json"
)


def _validator():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_schema_accepts_legacy_and_nanosecond_amaru_events():
    validator = _validator()
    legacy = {
        "target": "amaru::protocols",
        "fields": {
            "message": "measurement.protocol_ingress",
            "elapsed_micros": 2,
        },
    }
    precise = {
        "target": "amaru::protocols",
        "fields": {
            "message": "measurement.protocol_ingress",
            "elapsed_nanos": 2184,
            "elapsed_micros": 2,
        },
    }

    validator.validate(legacy)
    validator.validate(precise)


def test_schema_accepts_legacy_and_nanosecond_cardano_events():
    validator = _validator()
    legacy = {
        "schema_version": "v1",
        "target": "cardano-node::measurement",
        "event": "protocol_receive_decode",
        "duration_us": 2,
    }
    precise = {
        "schema_version": "v2",
        "target": "cardano-node::measurement",
        "event": "protocol_receive_decode",
        "elapsed_nanos": 2184,
        "duration_us": 2,
    }

    validator.validate(legacy)
    validator.validate(precise)


def test_schema_rejects_invalid_nanosecond_values():
    validator = _validator()
    base = {
        "schema_version": "v2",
        "target": "cardano-node::measurement",
        "event": "protocol_receive_decode",
        "duration_us": 2,
    }

    for invalid in (-1, True, 2.184):
        event = {**base, "elapsed_nanos": invalid}
        errors = list(validator.iter_errors(event))
        assert errors, f"elapsed_nanos={invalid!r} must be rejected"
