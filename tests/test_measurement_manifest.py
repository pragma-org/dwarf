import hashlib
import json

from profile_manager import forensic


def _measurement_context():
    definition = {
        "schema_version": "v1",
        "id": "amaru-external-workload-accounting",
        "collection_mode": "external",
        "threshold_gate": {"supported": True, "default_enabled": False},
    }
    resolved = {
        "id": definition["id"],
        "enabled": True,
        "source": "scenario-override",
        "parameters": {"window_seconds": 5},
        "threshold_gate": {
            "enabled": True,
            "thresholds": [
                {
                    "metric": "successful_submissions_per_second",
                    "operator": "gte",
                    "value": 1,
                    "unit": "tx/s",
                }
            ],
        },
        "definition_digest": "sha256:" + "d" * 64,
        "definition": definition,
    }
    return {
        "target_identity": {
            "implementation": "amaru",
            "version": "10.11.20260912",
            "source_revision": "b159172f25a9c389f82f20bca4f15e3032791638",
            "mode": "stock",
            "image_digest": "sha256:" + "4" * 64,
            "executable_digest": None,
        },
        "profile": {
            "id": "amaru-security-default",
            "source": "explicit-profile",
            "definition_digest": "sha256:" + "p" * 64,
        },
        "requested": [
            {"id": definition["id"], "enabled": True, "source": "scenario-override"}
        ],
        "resolved": [resolved],
        "skipped": [],
        "incompatible": [],
        "disabled": [],
    }


def test_run_manifest_retains_measurement_resolution_and_exact_target_identity(tmp_path):
    context = _measurement_context()
    handle = forensic.start_run(
        scenario_id="measurement-evidence",
        scenario_yaml=b'{"spec_version":"v1","id":"measurement-evidence"}\n',
        target={"implementation": "amaru", "version": "10.11.20260912"},
        runtime="library",
        profile_id=None,
        profile_resolved=None,
        measurement_context=context,
        framework_version="test",
        framework_commit="test",
        seed=1,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    handle.end(exit_status="pass")

    selection_path = handle.run_dir / "measurements" / "selection.json"
    retained = json.loads(selection_path.read_text(encoding="utf-8"))
    manifest = json.loads((handle.run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert retained["resolution"] == context
    assert retained["collector_states"] == {
        "amaru-external-workload-accounting": "selected"
    }
    assert retained["threshold_gates"]["amaru-external-workload-accounting"]["enabled"] is True
    assert retained["resolution"]["resolved"][0]["definition"]["collection_mode"] == "external"
    assert manifest["measurements"]["target_identity"] == context["target_identity"]
    assert manifest["measurements"]["profile"] == context["profile"]
    assert manifest["measurements"]["collector_states"] == retained["collector_states"]
    assert manifest["measurements"]["selection_path"] == "measurements/selection.json"
    assert manifest["measurements"]["selection_sha256"] == hashlib.sha256(
        selection_path.read_bytes()
    ).hexdigest()


def test_old_run_manifest_remains_measurement_optional(tmp_path):
    handle = forensic.start_run(
        scenario_id="legacy-evidence",
        scenario_yaml=b'{"spec_version":"v1","id":"legacy-evidence"}\n',
        target={"implementation": "amaru", "version": "test"},
        runtime="library",
        profile_id=None,
        profile_resolved=None,
        framework_version="test",
        framework_commit="test",
        seed=1,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    handle.end(exit_status="pass")

    manifest = json.loads((handle.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "measurements" not in manifest
    assert not (handle.run_dir / "measurements").exists()
