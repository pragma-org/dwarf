import json
from pathlib import Path
from types import SimpleNamespace

from scripts import runtime_credential_ceremony as ceremony


def test_create_env_uses_current_cardano_testnet_cli_contract(tmp_path, monkeypatch):
    observed = []
    observed_env = []
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cardano_cli = bin_dir / "cardano-cli"
    cardano_cli.write_text("#!/bin/sh\n")
    cardano_cli.chmod(0o755)

    def run(command, **kwargs):
        observed.append(command)
        observed_env.append(kwargs["env"])
        output = Path(command[command.index("--output") + 1])
        pool = output / "pools-keys" / "pool1"
        pool.mkdir(parents=True)
        (pool / "vrf.vkey").write_text("vrf")
        (pool / "stake.vkey").write_text("stake")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ceremony.subprocess, "run", run)

    report_path = ceremony.run_credential_ceremony(
        {
            "output_dir": tmp_path,
            "pool_count": 1,
            "testnet_magic": 42,
            "kes_period_window": 12,
            "deterministic_seed": "0xC0EDC0DE",
        },
        env={"PATH": str(bin_dir)},
    )

    assert observed == [[
        "cardano-testnet",
        "create-env",
        "--output",
        str(tmp_path / "env"),
        "--num-pool-nodes",
        "1",
        "--testnet-magic",
        "42",
    ]]
    assert observed_env[0]["CARDANO_CLI"] == str(cardano_cli)
    assert json.loads(report_path.read_text())["keys_generated"] == 1
