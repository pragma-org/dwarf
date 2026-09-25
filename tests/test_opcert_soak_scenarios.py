from pathlib import Path


def test_scenario_count_is_293():
    assert len(list(Path("dwarf/scenarios").glob("*.yaml"))) == 293


def test_six_soak_scenarios_present():
    ids = {p.stem for p in Path("dwarf/scenarios").glob("opcert-soak-*.yaml")}
    assert ids == {
        "opcert-soak-encoding-mixed-1112-amaru-20260918",
        "opcert-soak-kes-period-mixed-1112-amaru-20260918",
        "opcert-soak-accept-boundary-cardano-1112",
        "opcert-soak-accept-boundary-amaru-20260918",
        "opcert-soak-restart-persistence-cardano-1112",
        "opcert-soak-restart-persistence-amaru-20260918",
    }
