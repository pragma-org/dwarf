"""F134 (decision 2026-11-09, 2a): the generated devnet config pins every genesis hash.

cardano-node verifies a genesis file against its configured hash; DWARF's generated devnet
config named the files but pinned no hashes (bootstrap_assumptions_safe: genesis hashes not
pinned). Shelley/Alonzo/Conway hashes are blake2b-256 of the file bytes; Byron's is the
canonical-JSON hash from cardano-cli.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from scripts import runtime_compose_substrate as compose


def _env(tmp_path):
    for era in ("byron", "shelley", "alonzo", "conway"):
        (tmp_path / f"{era}-genesis.json").write_text(json.dumps({"era": era}) + "\n")
    (tmp_path / "configuration.yaml").write_text(json.dumps({
        "ByronGenesisFile": "byron-genesis.json", "ShelleyGenesisFile": "shelley-genesis.json",
        "AlonzoGenesisFile": "alonzo-genesis.json", "ConwayGenesisFile": "conway-genesis.json"}))
    return tmp_path / "configuration.yaml"


def test_every_genesis_hash_is_pinned(tmp_path):
    config = _env(tmp_path)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="ab" * 32 + "\n", stderr="")

    evidence = compose._pin_cardano_genesis_hashes(config_path=config, cardano_cli="cardano-cli", run=run)
    body = json.loads(config.read_text())
    for era in ("Shelley", "Alonzo", "Conway"):
        expected = hashlib.blake2b((tmp_path / f"{era.lower()}-genesis.json").read_bytes(), digest_size=32).hexdigest()
        assert body[f"{era}GenesisHash"] == expected
    assert body["ByronGenesisHash"] == "ab" * 32
    assert calls[0][:4] == ["cardano-cli", "byron", "genesis", "print-genesis-hash"]
    assert evidence["pinned"] == ["AlonzoGenesisHash", "ByronGenesisHash", "ConwayGenesisHash", "ShelleyGenesisHash"]


def test_a_byron_hash_failure_is_reported_not_guessed(tmp_path):
    config = _env(tmp_path)
    evidence = compose._pin_cardano_genesis_hashes(
        config_path=config, cardano_cli="cardano-cli",
        run=lambda argv, **k: SimpleNamespace(returncode=1, stdout="", stderr="unknown command"))
    body = json.loads(config.read_text())
    assert "ByronGenesisHash" not in body and "ShelleyGenesisHash" in body
    assert evidence["unpinned"] == {"ByronGenesisHash": "unknown command"}
