from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_every_antithesis_compose_asset_parses_as_yaml():
    compose_files = sorted(
        path
        for pattern in ("docker-compose.yaml", "docker-compose.yml")
        for path in (REPOSITORY_ROOT / "antithesis").rglob(pattern)
    )

    assert compose_files
    for compose_file in compose_files:
        parsed = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict), compose_file
        assert isinstance(parsed.get("services"), dict), compose_file


def test_cardano_amaru_dwarf_relay_two_waits_for_the_adversary():
    compose_file = (
        REPOSITORY_ROOT / "antithesis" / "cardano_amaru_dwarf" / "docker-compose.yaml"
    )
    parsed = yaml.safe_load(compose_file.read_text(encoding="utf-8"))

    relay_dependencies = parsed["services"]["amaru-relay-2"]["depends_on"]
    assert relay_dependencies["dwarf-adversary"]["condition"] == "service_started"
