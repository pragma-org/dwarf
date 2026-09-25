import pytest
from scripts import opcert_soak_families as F


def test_families_and_differential_sets():
    assert set(F.DIFFERENTIAL_FAMILIES) <= set(F.FAMILIES)
    # The three DWARF-owned differential families; peers may append more,
    # so assert presence + the FAMILIES subset invariant rather than equality.
    assert {"encoding-form", "kes-period-differential", "rules-differential"} <= set(F.DIFFERENTIAL_FAMILIES)


@pytest.mark.parametrize("family", F.FAMILIES)
def test_seed_iteration_is_deterministic(family):
    a = F.generate_case(family, seed=1234, iteration=17)
    b = F.generate_case(family, seed=1234, iteration=17)
    assert a == b


@pytest.mark.parametrize("family", F.FAMILIES)
def test_replay_reproduces_spec(family):
    a = F.generate_case(family, seed=99, iteration=3)
    b = F.generate_case(a["family"], a["seed"], a["iteration"])
    assert a == b


@pytest.mark.parametrize("family", F.FAMILIES)
def test_iterations_are_not_constant(family):
    specs = [F.generate_case(family, seed=5, iteration=i)["params"] for i in range(40)]
    assert len({repr(p) for p in specs}) > 1, "generator produced a constant case (no randomization)"


def test_encoding_form_covers_all_forms_over_many_iters():
    seen = {F.generate_case("encoding-form", 5, i)["params"]["encoding_form"] for i in range(400)}
    assert seen == set(F.ENCODING_FORMS)


def test_accept_boundary_counter_is_recorded_plus_one_and_kes_in_unit_interval():
    for i in range(50):
        p = F.generate_case("accept-boundary", 5, i)["params"]
        assert p["counter_delta"] == 1
        assert 0.0 <= p["kes_period_fraction"] < 1.0


def test_restart_persistence_replay_counter_below_rotate_target():
    for i in range(50):
        p = F.generate_case("restart-persistence", 5, i, restart_k=4)["params"]
        assert 1 <= p["rotate_to_counter"] <= 4
        assert 0 <= p["replay_counter"] < p["rotate_to_counter"]


def test_expected_verdict_per_family():
    assert F.generate_case("encoding-form", 5, 0)["expected_verdict"] == "accept"
    assert F.generate_case("accept-boundary", 5, 0)["expected_verdict"] == "accept"
    assert F.generate_case("restart-persistence", 5, 0)["expected_verdict"] == "reject"
    assert F.generate_case("kes-period-differential", 5, 0)["expected_verdict"] == "accept"


def test_unknown_family_raises():
    with pytest.raises(ValueError, match="unknown family"):
        F.generate_case("banana", 5, 0)
