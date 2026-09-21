import json
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "dwarf/spec/v1/client-example-acceptance-card.schema.json"
CARDS_DIR = ROOT / "dwarf/docs/client-examples/contracts"

EXPECTED_IDS = {
    "client-example-01-cbor-decoding",
    "client-example-02-plutus-vm",
    "client-example-03-invalid-mini-protocol",
    "client-example-04-block-application",
    "client-example-05-restart-recovery-sync",
}

V1_CARD_ID = "client-example-03-invalid-mini-protocol"
V1_PROFILES = {
    "amaru": "profile-q-amaru-measurement-patched",
    "cardano-node": "profile-t-cardano-measurement-patched",
}
V2_PROFILES = {
    "amaru": "profile-u-amaru-measurement-nanoseconds-v2",
    "cardano-node": "profile-v-cardano-measurement-nanoseconds-v2",
}
V2_TARGETS = {
    "amaru": {
        "measurement_revision": "nanoseconds-v2",
        "patch_set_sha256": "4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0",
        "patched_executable_digest": "sha256:05233bac96c1914a232a2d9c5a704f08401aff0b20356c015e848f295b919b78",
        "patched_image_digest": "sha256:c3f139e87b4ada079a4dc5c656a2ca06c6dc30ea55719d54bedb772c836de862",
        "patch_manifest_sha256": "sha256:1012e64ad3a3f5a9abd1aea061d5036fcc8e936392f82d5829e3df7733ab6b99",
    },
    "cardano-node": {
        "measurement_revision": "nanoseconds-v2",
        "patch_set_sha256": "1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c",
        "patched_executable_digest": "sha256:3fb83f12ac1152e96c884c756a505484a39d9200da6c59b24678ac61218220eb",
        "patched_image_digest": "sha256:956ae21cf9141a7149692392453ea31ece00e33960c2cca0f79548beeacf7370",
        "patch_manifest_sha256": "sha256:06405007512eb8040898fbe53984b43bb5419100e5c7c64954588ee618ad7071",
    },
}
V3_AMARU_BLOCK_TARGET = {
    "measurement_revision": "nanoseconds-v3",
    "patch_set_sha256": "042f6b1840bc6a30e65d77ce702e1be9967b77564ecfb9c77c1e5c25520aad00",
    "patched_executable_digest": "sha256:6c33df932f50601166a0107ed9a47742ebc501218be5bc59f99a69f5fcddc94c",
    "patched_image_digest": "sha256:d120f9515d5bcc6aa68629e0370fa7bdf5a5612e5d35005aa231d32ab2b7169a",
    "patch_manifest_sha256": "sha256:05f4baf227395c75467d716fb6f59a8344d3ebbae726011e19173cbd74385fe9",
}

EXPECTED_TARGETS = {
    "amaru": {
        "version": "10.11.20260912",
        "source_revision": "b159172f25a9c389f82f20bca4f15e3032791638",
        "stock_image_digest": "sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e",
        "patched_image_digest": "sha256:dacb2351e69ab1d0bbfbc569b222d1bde40d9a158e79c556b30df554375addcc",
    },
    "cardano-node": {
        "version": "11.1.2",
        "source_revision": "fef83fed01d7926f3de83b3b917be5a4a48768b5",
        "stock_image_digest": "sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f",
        "patched_image_digest": "sha256:c74c3deafac54ed4d30da21952fce579c3293919993d31081e18787605757b8c",
    },
}


def _cards():
    return [
        yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(CARDS_DIR.glob("*.yaml"))
    ]


def test_exactly_five_cards_validate_against_the_contract_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    cards = _cards()
    assert {card["id"] for card in cards} == EXPECTED_IDS
    validator = jsonschema.Draft202012Validator(schema)
    for card in cards:
        validator.validate(card)


def test_every_card_pins_both_real_node_targets_and_modes():
    for card in _cards():
        assert card["scope"]["real_node_required"] is True
        assert card["scope"]["simulation_allowed"] is False
        assert set(card["targets"]) == set(EXPECTED_TARGETS)
        for implementation, expected in EXPECTED_TARGETS.items():
            target = card["targets"][implementation]
            for key, value in expected.items():
                if key != "patched_image_digest" or card["id"] == V1_CARD_ID:
                    assert target[key] == value
            assert target["stock_mode"] == "stock"
            assert target["patched_mode"] == "patched"


def test_card03_keeps_v1_while_future_cards_pin_nanoseconds_v2():
    for card in _cards():
        profiles = {
            leg["implementation"]: leg["profile"]
            for leg in card["scenario_legs"]
        }
        if card["id"] == V1_CARD_ID:
            assert profiles == V1_PROFILES
            assert all(
                "measurement_revision" not in target
                for target in card["targets"].values()
            )
            continue
        if card["id"] == "client-example-04-block-application":
            assert profiles == {
                "amaru": "profile-y-amaru-block-application-nanoseconds-v3",
                "cardano-node": "profile-v-cardano-measurement-nanoseconds-v2",
            }
        else:
            assert profiles == V2_PROFILES
        for implementation, expected in V2_TARGETS.items():
            if (
                card["id"] == "client-example-04-block-application"
                and implementation == "amaru"
            ):
                expected = V3_AMARU_BLOCK_TARGET
            for key, value in expected.items():
                assert card["targets"][implementation][key] == value


def test_every_card_defines_non_vacuous_security_and_measurement_gates():
    for card in _cards():
        assert card["functional_requirement"]["expected_result"]
        assert card["security_assertions"]
        assert card["measurements"]
        assert all(metric["boundary"] for metric in card["measurements"])
        assert all(metric["minimum_sample_floor"] >= 1 for metric in card["measurements"])
        assert card["evidence_quality_rules"]["very_small_sample"] == "n < 5"
        assert card["evidence_quality_rules"]["small_sample"] == "5 <= n < 30"
        assert card["evidence_quality_rules"]["useful_sample"] == "n >= 30"
        assert card["required_collectors"]
        assert card["retained_artifacts"]
        assert card["report_routes"]
        assert card["legitimate_claims"]
        assert card["explicit_non_claims"]


def test_cards_keep_measurements_non_gating_and_walkthrough_deferred():
    for card in _cards():
        assert card["verdict_policy"]["security_assertions_authoritative"] is True
        assert card["verdict_policy"]["measurements_gate_by_default"] is False
        assert card["verdict_policy"]["threshold_gate"] == "explicit-opt-in-only"
        assert card["deferred_scope"]["presentation_walkthrough"] is True
        assert card["deferred_scope"]["mixed_node_comparison"] is True
        assert card["deferred_scope"]["weekly_automation"] is True
        assert card["deferred_scope"]["stable_release_thresholds"] is True


def test_cards_pin_workload_identity_and_stop_conditions():
    for card in _cards():
        workload = card["workload"]
        assert workload["seed"].startswith("0x")
        assert workload["digest"].startswith("sha256:")
        assert workload["warm_up_seconds"] >= 0
        assert workload["duration_seconds"] > 0
        assert workload["stop_conditions"]
        assert card["environment"]["hardware_fingerprint"] == "cardano-box-2026-09-19"


def test_invalid_protocol_card_names_the_three_exact_live_amaru_boundaries():
    card = next(
        item
        for item in _cards()
        if item["id"] == "client-example-03-invalid-mini-protocol"
    )

    assert {
        metric["boundary"]
        for metric in card["measurements"]
        if metric["name"].startswith("handshake_")
    } == {
        "mux-cbor-item",
        "mini-protocol-decode",
        "handshake-negotiation",
    }


def _card(card_id):
    return next(item for item in _cards() if item["id"] == card_id)


def _scenario(scenario_id):
    return json.loads(
        (ROOT / "dwarf" / "scenarios" / f"{scenario_id}.yaml").read_text(
            encoding="utf-8"
        )
    )


def test_card01_records_completed_finding_and_exact_fixed_revision_regression():
    resolution = _card("client-example-01-cbor-decoding")["approved_resolution"]

    assert resolution["kind"] == "completed-security-finding-and-regression"
    assert resolution["historical_finding"] == {
        "run_id": "20260920T135054Z-28289dcd",
        "source_revision": "b159172f25a9c389f82f20bca4f15e3032791638",
        "failed_assertion": "cbor_conformance_clean",
        "finding_id": "amaru-plutus-data-byte-string-bound",
        "execution_classification": "completed_with_security_finding",
        "security_verdict": "fail",
    }
    assert resolution["regression"] == {
        "scenario_id": "client-example-cbor-decoding-amaru-d3a6dafc-regression",
        "profile": "profile-x-amaru-cbor-fix-regression-nanoseconds-v2",
        "source_revision": "d3a6dafcced78f5809a96619e883cf04911d2bdc",
        "upstream_fix_revision": "d3a6dafcced78f5809a96619e883cf04911d2bdc",
        "measurement_revision": "nanoseconds-v2",
        "corpus_digest": "sha256:8f5b409f5c2b25b31e392365bab0e9a703a526e776a4f34dfbc3204f9b022dd9",
        "required_result": "pass",
        "accepted_run_id": "20260920T235440Z-050046a4",
        "manifest_sha256": "3e7412574ddcda16aaa788b4c27a4bc74f9728dd1fcc381fd5d822611fb65871",
        "assertions_sha256": "545bb3ab6164782ba3aae56a95b9a9391f6170b1e0e80573873c96904c5f73ae",
        "codec_result_sha256": "89ac73f932ec9dc1cca0b78a0ec52714b3579f5289d5aad4227a4a4c354cf733",
        "bundle_sha256": "6fd2d11f9dcb17d181edc06e81983c3ff18968cfbc484fc20887ddbfc4513440",
    }


def test_card02_requires_additive_topology_with_live_on_chain_plutus_v2():
    resolution = _card("client-example-02-plutus-vm")["approved_resolution"]

    assert resolution["kind"] == "on-chain-plutus-v2-topology"
    assert resolution["scenario_id"] == "client-example-plutus-vm-amaru-onchain-v2"
    assert resolution["profile"] == "profile-w-amaru-measurement-plutus-v2"
    assert resolution["preserve_existing_topologies"] is True
    assert resolution["requirements"] == [
        "generated-genesis-digests-retained",
        "live-protocol-parameters-digest-retained",
        "live-plutus-v2-cost-model-matches-pinned-model",
        "30-valid-transactions-included",
        "30-expected-invalid-transactions-included",
        "transaction-hashes-and-identifiers-retained",
        "continued-amaru-chain-progress",
    ]

    scenario = _scenario("client-example-plutus-vm-amaru-onchain-v2")
    assert scenario["profile"] == "profile-w-amaru-measurement-plutus-v2"
    assert scenario["target"]["source_revision"] == (
        "b159172f25a9c389f82f20bca4f15e3032791638"
    )
    controlled = next(
        step
        for step in scenario["load"]
        if step["primitive"] == "runtime_controlled_plutus_transactions"
    )
    assert controlled["profile_id"] == "profile-w-amaru-measurement-plutus-v2"
    assert resolution["accepted_evidence"] == {
        "run_id": "20260921T013953Z-565b77c3",
        "measurement_revision": "nanoseconds-v2",
        "framework_commit": "05e757016838afd0594aeeea5b7019698d797e73",
        "scenario_sha256": "3dc7db5b24e3e6396cf6e25304db3c5a96469863be94d540ca2c3b468ed49b01",
        "manifest_sha256": "436232f557906fbb58661b528db1ac6d37c73ac21506e7daef9dce9ad2956927",
        "assertions_sha256": "98e71e58ab5f7696b121ae1c498155d24524c35146f86af2926effe41f304c06",
        "measurement_report_sha256": "86c3dc58e43a3571243693a26de6622f983d926a3cd9d3c046f4678bd5d2c525",
        "transaction_result_sha256": "9403d75f28e7ad4125aef0d774a40e340d12fcee9d1d892437cd20a1f158708c",
        "health_progress_sha256": "eec8329a68e6358ae5fef76b369e19ef85ee402a2dd6b799ea4fc2ed8e677f64",
        "bundle_sha256": "bf5604de608889cabc4ea30242a2236aaccf12cd06c07999c14ee26a8a3c7ed0",
        "bundle_file_count": 575,
    }


def test_card04_replaces_raw_monotonicity_with_canonical_progress_v2():
    card = _card("client-example-04-block-application")
    resolution = card["approved_resolution"]

    assert card["status"] == "accepted"
    assert {leg["status"] for leg in card["scenario_legs"]} == {"accepted"}
    assert resolution["kind"] == "canonical-progress-v2"
    assert resolution["preserve_v1_runs"] is True
    assert resolution["scenario_ids"] == {
        "amaru": "client-example-block-application-amaru-canonical-v3",
        "cardano-node": "client-example-block-application-cardano-canonical-v2",
    }
    assert resolution["measurement_revisions"] == {
        "amaru": "nanoseconds-v3",
        "cardano-node": "nanoseconds-v2",
    }
    assert resolution["raw_evidence"] == [
        "adopted-block-events",
        "same-height-hash-switches",
        "rollback-and-fork-events",
        "application-timings",
    ]
    assert resolution["pass_conditions"] == [
        "bounded-canonical-progress",
        "final-convergence",
        "complete-required-correlations",
        "no-fatal-health-signal",
    ]
    assert resolution["fail_conditions"] == [
        "no-progress",
        "non-convergence",
        "excessive-or-continuing-oscillation",
        "missing-correlations",
        "fatal-health-signal",
    ]
    assert resolution["same_height_switch_alone_fails"] is False
    assert resolution["accepted_evidence"]["cardano-node"]["run_id"] == (
        "20260921T021935Z-3b58eafc"
    )
    assert resolution["accepted_evidence"]["cardano-node"]["measurement_revision"] == (
        "nanoseconds-v2"
    )
    assert resolution["accepted_evidence"]["amaru"]["run_id"] == (
        "20260921T035546Z-9747122c"
    )
    assert resolution["accepted_evidence"]["amaru"]["measurement_revision"] == (
        "nanoseconds-v3"
    )
    assert resolution["accepted_evidence"]["amaru"]["bundle_sha256"] == (
        "f17b4892ca45d003a94b7c175d510cbd0890420bdf79420a46ed4dff79c0c808"
    )


def test_card05_accepts_both_run_relative_restart_and_sync_legs():
    card = _card("client-example-05-restart-recovery-sync")
    resolution = card["approved_resolution"]

    assert card["status"] == "accepted"
    assert {leg["status"] for leg in card["scenario_legs"]} == {"accepted"}
    assert resolution["kind"] == "real-restart-controlled-sync-v1"
    assert resolution["clock_contract"] == "run-relative-monotonic-seconds"
    assert resolution["measurement_revisions"] == {
        "amaru": "nanoseconds-v2",
        "cardano-node": "nanoseconds-v2",
    }
    assert resolution["accepted_evidence"] == {
        "amaru": {
            "run_id": "20260921T045619Z-15e864a0",
            "framework_commit": "b009507629c02b3437d28981c77bc6641b2d290f",
            "scenario_sha256": "41b89b052a2ed9ad2b7621f102c7e5edaff785d30d435593f07c8dd9437960f8",
            "manifest_sha256": "e54c9f1434e5b59b9d18a5cb49dd88012a6d4fb7fd2053d052a8129b9eaceac3",
            "assertions_sha256": "a6d56c4368cf23cf312cb3852667c5f70ce8a72fecb890178f3859f288df7470",
            "measurement_report_sha256": "d3f5b0230bc422cf971bdf374161214e6f2ef578ac13bede9e0048498c7f650a",
            "restart_proof_sha256": "2bd8b4b3dfcf970907dfd47b022dfb70d5ea34dc512d2a27b03225d7cd96d25f",
            "sync_proof_sha256": "9bc2d79939afaa0b4767d62ee87e9c1dda7d8aa2594e28f30abc6562e84a5d5f",
            "health_progress_sha256": "a35a0f94c799e8032bcbfa6ee6ec9999b08828615fab4c17baa1f0554cf6d1a0",
            "resource_result_sha256": "b309293c8987eeb08d6f3040d8e820ecce27407fe321fa39c735209998a26a92",
            "restart_window_resource_samples": 40,
            "bundle_sha256": "d0b0bce35254094636c83e59d688bf7be894c9bc0da9fae99f1eda2089e22217",
            "bundle_file_count": 85,
        },
        "cardano-node": {
            "run_id": "20260921T045807Z-8e2bbb0e",
            "framework_commit": "b009507629c02b3437d28981c77bc6641b2d290f",
            "scenario_sha256": "76dfeba29356d08484bbcc2f91a2a4aff0598d63d86eae3e839bafa4dea1d2b1",
            "manifest_sha256": "b21f5535993133b331f6e1d15b40c6c73a746ca2d9fe18d9caf28d241d771943",
            "assertions_sha256": "311e25ae02bb746aeba106cc503a90a1d454259de334442616da606e06db7bbd",
            "measurement_report_sha256": "c48f5781be30f54bf9e39e318238b5b73fb978abf2f2ccaa2b51001eb0cf4e23",
            "restart_proof_sha256": "970558063400b4ad6a804e09e4d271b98c63d419b88d896882f17c6e5c04d2fd",
            "sync_proof_sha256": "643260973cd015d83a0250a8a497ceb5a43b9b2ee12ae9dd622b01af34f8950c",
            "health_progress_sha256": "d44e324116f08db6ca7b008de7d2583519d9cab4a01d685b64f0b43997c86dec",
            "resource_result_sha256": "8e0265418aa3e077dc0e17e766b76ab2eb9d19c69d57d48968bb329bb0291e54",
            "restart_window_resource_samples": 173,
            "bundle_sha256": "0cf0d4e2d6eeb49a82c3b3053c2b2a5b0eb39e37c8489301df76b06a2a48cfce",
            "bundle_file_count": 70,
        },
    }
    assert card["implementation_gaps"] == [
        {
            "id": "GAP-REAL-RESTART-GATES",
            "status": "closed",
            "required_change": "The real target restart retains ordered restart, listener, chain-progress, and peer-role events in the run-relative monotonic clock domain.",
        },
        {
            "id": "GAP-CONTROLLED-SYNC-RANGE",
            "status": "closed",
            "required_change": "The controlled range binds exact start and end heights, hashes, run-relative timestamps, and the implementation-specific peer policy.",
        },
    ]
