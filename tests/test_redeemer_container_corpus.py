import hashlib
import json
from pathlib import Path

from scripts.build_redeemer_container_corpus import (
    build_redeemer_container_corpus,
    split_signed_transaction,
    witness_map_keys,
)


ROOT = Path(__file__).resolve().parents[1]


def _payload(path: Path) -> bytes:
    return bytes.fromhex(json.loads(path.read_text(encoding="utf-8"))["cborHex"])


def test_builder_preserves_signed_bodies_and_separates_decoder_controls(tmp_path):
    no_script = ROOT / "antithesis/cardano_amaru_adversarial/fixture/static/underfee.tx"
    valid_script = ROOT / "antithesis/cardano_amaru_adversarial/fixture/plutus/exu-zero.tx"
    destination = tmp_path / "corpus"

    manifest = build_redeemer_container_corpus(
        no_script_source=no_script,
        valid_script_source=valid_script,
        destination=destination,
    )

    assert [case["case_id"] for case in manifest["cases"]] == [
        "no-script-redeemers-omitted",
        "no-script-redeemers-empty-list",
        "no-script-redeemers-empty-map",
        "script-redeemer-valid-control",
        "script-redeemers-omitted",
    ]

    generated = {
        case["case_id"]: _payload(destination / case["tx_file"])
        for case in manifest["cases"]
    }
    no_script_body = split_signed_transaction(_payload(no_script))[0]
    valid_script_body = split_signed_transaction(_payload(valid_script))[0]

    for case_id in (
        "no-script-redeemers-omitted",
        "no-script-redeemers-empty-list",
        "no-script-redeemers-empty-map",
    ):
        assert split_signed_transaction(generated[case_id])[0] == no_script_body
    for case_id in ("script-redeemer-valid-control", "script-redeemers-omitted"):
        assert split_signed_transaction(generated[case_id])[0] == valid_script_body

    assert witness_map_keys(generated["no-script-redeemers-omitted"]) == (0,)
    assert witness_map_keys(generated["no-script-redeemers-empty-list"]) == (0, 5)
    assert witness_map_keys(generated["no-script-redeemers-empty-map"]) == (0, 5)
    assert set(witness_map_keys(generated["script-redeemer-valid-control"])) == {0, 5, 7}
    assert witness_map_keys(generated["script-redeemers-omitted"]) == (0, 7)

    assert split_signed_transaction(generated["no-script-redeemers-empty-list"])[1].endswith(
        b"\x05\x80"
    )
    assert split_signed_transaction(generated["no-script-redeemers-empty-map"])[1].endswith(
        b"\x05\xa0"
    )

    for case in manifest["cases"]:
        payload = generated[case["case_id"]]
        assert case["cbor_size"] == len(payload)
        assert case["cbor_sha256"] == hashlib.sha256(payload).hexdigest()


def test_builder_refuses_a_source_without_the_expected_redeemer_shape(tmp_path):
    no_script = ROOT / "antithesis/cardano_amaru_adversarial/fixture/static/underfee.tx"

    try:
        build_redeemer_container_corpus(
            no_script_source=no_script,
            valid_script_source=no_script,
            destination=tmp_path,
        )
    except ValueError as error:
        assert "valid script source" in str(error)
    else:
        raise AssertionError("builder accepted a script control without redeemers")


def test_redeemer_container_scenario_uses_the_shipped_runtime_corpus(tmp_path):
    scenario = json.loads(
        (
            ROOT
            / "dwarf/scenarios/ledger-submit-empty-redeemers-container-differential-amaru-cardano-node.yaml"
        ).read_text(encoding="utf-8")
    )
    blockers = " ".join(scenario["promotion_blockers"]).lower()
    assert "structural" in blockers
    assert "not ledger-success controls" in blockers
    corpus_reference = scenario["load"][0]["corpus_dir"]
    assert corpus_reference == "dwarf/corpora/redeemer-container"

    corpus_dir = ROOT / corpus_reference
    assert hashlib.sha256((corpus_dir / "corpus.json").read_bytes()).hexdigest() == (
        "7ff134bd5713b86a4cad28d6631f91d647a297672dee8f38fa12388b65917807"
    )
    manifest = json.loads((corpus_dir / "corpus.json").read_text(encoding="utf-8"))
    assert len(manifest["cases"]) == 5
    for case in manifest["cases"]:
        payload = _payload(corpus_dir / case["tx_file"])
        assert hashlib.sha256(payload).hexdigest() == case["cbor_sha256"]

    sources = {name: ROOT / path for name, path in manifest["sources"].items()}
    for source in sources.values():
        assert source.is_file()
        assert corpus_dir in source.parents

    rebuilt = build_redeemer_container_corpus(
        no_script_source=sources["no_script"],
        valid_script_source=sources["valid_script"],
        destination=tmp_path / "rebuilt",
    )
    assert [case["cbor_sha256"] for case in rebuilt["cases"]] == [
        case["cbor_sha256"] for case in manifest["cases"]
    ]
