import json

from scripts.runtime_amaru_preview_proof import extract_latest_adopted_tip


def test_extract_latest_adopted_tip_accepts_pinned_amaru_tip_update_json():
    records = [
        {
            "timestamp": "2026-09-18T23:25:10.100000Z",
            "level": "DEBUG",
            "fields": {
                "block_height": 497,
                "header_hash": "a" * 64,
                "message": "tip.update",
                "slot": 2496,
            },
            "target": "amaru::ledger",
            "span": {"name": "state.roll_forward"},
        },
        {
            "timestamp": "2026-09-18T23:25:10.200000Z",
            "level": "DEBUG",
            "fields": {
                "block_height": 498,
                "header_hash": "b" * 64,
                "message": "tip.update",
                "slot": 2497,
            },
            "target": "amaru::ledger",
            "span": {"name": "state.roll_forward"},
        },
    ]
    log_text = "\n".join(json.dumps(record) for record in records)

    assert extract_latest_adopted_tip(log_text) == {
        "slot": 2497,
        "hash": "b" * 64,
        "block_hash": "b" * 64,
        "block_height": 498,
    }


def test_extract_latest_adopted_tip_keeps_legacy_text_compatibility():
    log_text = (
        "INFO adopted tip tip.slot=259288 tip.hash="
        + "c" * 64
        + " tip.block_height=2636 max_block_height=2636\n"
    )

    assert extract_latest_adopted_tip(log_text) == {
        "slot": 259288,
        "hash": "c" * 64,
        "block_hash": "c" * 64,
        "block_height": 2636,
    }


def test_extract_latest_adopted_tip_accepts_pinned_amaru_consensus_text():
    log_text = (
        "2026-10-10T13:24:42.004740Z DEBUG amaru::consensus: "
        "tip.adopt slot=7512 "
        'header_hash="4a0ef980d3610935d86dec3704432f55ece3da4e2b11fd4b58af573bffa68b19" '
        "block_height=1485 max_block_height=1485 suppressed=0\n"
    )

    assert extract_latest_adopted_tip(log_text) == {
        "slot": 7512,
        "hash": "4a0ef980d3610935d86dec3704432f55ece3da4e2b11fd4b58af573bffa68b19",
        "block_hash": "4a0ef980d3610935d86dec3704432f55ece3da4e2b11fd4b58af573bffa68b19",
        "block_height": 1485,
    }


def test_extract_latest_adopted_tip_rejects_malformed_consensus_text():
    malformed = (
        "INFO amaru::consensus: tip.adopt slot=7512 "
        'header_hash="not-a-hash" block_height=1485\n'
        "INFO amaru::consensus: tip.adopt slot=true "
        f'header_hash="{"a" * 64}" block_height=-1\n'
    )

    assert extract_latest_adopted_tip(malformed) is None


def test_extract_latest_adopted_tip_rejects_malformed_json_tip_values():
    malformed = json.dumps(
        {
            "fields": {
                "block_height": True,
                "header_hash": "not-a-hash",
                "message": "tip.update",
                "slot": "2497",
            }
        }
    )

    assert extract_latest_adopted_tip(malformed) is None
