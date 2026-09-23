import io
import json
import re
import tarfile
import threading
from html import unescape
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from jsonschema import Draft202012Validator

from profile_manager import dashboard
from profile_manager.data.definition_schemas import scenario_editor_descriptor


def _write_catalog_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    scenarios = tmp_path / "scenarios"
    targets = tmp_path / "targets" / "manifests"
    profiles = tmp_path / "profiles"
    scenarios.mkdir(parents=True)
    targets.mkdir(parents=True)
    (profiles / "profile-demo").mkdir(parents=True)

    scenario = {
        "spec_version": "v1",
        "id": "scenario-demo",
        "title": "Scenario demo",
        "target": {"implementation": "amaru", "version": "any"},
        "runtime": "library",
        "setup": [],
        "load": [],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [],
    }
    target = {
        "id": "target-demo",
        "binary": "dwarf/targets/demo",
        "input_format": "stdin_bytes",
        "implementation": "amaru",
        "language": "rust",
        "upstream_commit": "demo-revision",
        "decoder_type": "CBOR codec",
        "invariants": ["no panic on bounded input"],
        "expected_outcomes": {"ok": "exit 0", "clean_error": "exit 1", "crash": "other"},
    }
    profile = {
        "id": "profile-demo",
        "label": "Profile demo",
        "node_type": "mixed",
        "node_count": 1,
        "amaru_node_count": 1,
        "network_magic": 42,
        "peer_sharing": False,
        "remote_runtime_root": "/opt/dwarf/profiles/profile-demo",
        "compose_project": "dwarf-profile-demo",
        "topology_pattern": "local-mesh",
        "shared_genesis": True,
    }
    (scenarios / "scenario-demo.yaml").write_text(json.dumps(scenario, indent=2) + "\n")
    (targets / "target-demo.yaml").write_text(json.dumps(target, indent=2) + "\n")
    (profiles / "profile-demo" / "profile.yaml").write_text(json.dumps(profile, indent=2) + "\n")
    (scenarios / "pending").mkdir()
    (scenarios / "pending" / "not-live.yaml").write_text("{}\n")
    (scenarios / "._scenario-demo.yaml").write_text("finder metadata\n")
    (targets / ".DS_Store").write_text("cache\n")

    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(scenarios))
    monkeypatch.setenv("ADA2_DWARF_MANIFESTS_DIR", str(targets))
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(profiles))
    return {"scenarios": scenarios, "targets": targets, "profiles": profiles}


@pytest.mark.parametrize(
    ("catalog", "definition_id", "expected_name", "expected_text"),
    [
        ("scenarios", "scenario-demo", "scenario-demo.yaml", '"title": "Scenario demo"'),
        ("targets", "target-demo", "target-demo.yaml", '"binary": "dwarf/targets/demo"'),
        ("profiles", "profile-demo", "profile.yaml", '"label": "Profile demo"'),
    ],
)
def test_individual_definition_download(
    tmp_path, monkeypatch, catalog, definition_id, expected_name, expected_text
):
    _write_catalog_fixture(tmp_path, monkeypatch)

    response = dashboard.dispatch_api_request(
        f"/api/catalog/{catalog}/{definition_id}/download"
    )

    assert response is not None
    status, content_type, body, headers = response
    assert status == 200
    assert content_type == "application/yaml; charset=utf-8"
    assert headers["Content-Disposition"] == f'attachment; filename="{expected_name}"'
    assert expected_text in body.decode("utf-8")


@pytest.mark.parametrize("bad_id", ["../config", "a/b", "%2e%2e%2fconfig", ".DS_Store", "._profile"])
def test_definition_download_rejects_unsafe_ids(tmp_path, monkeypatch, bad_id):
    _write_catalog_fixture(tmp_path, monkeypatch)

    response = dashboard.dispatch_api_request(
        f"/api/catalog/scenarios/{bad_id}/download"
    )

    assert response is not None
    assert response[0] == 400


@pytest.mark.parametrize(
    ("catalog", "expected_member"),
    [
        ("scenarios", "dwarf/scenarios/scenario-demo.yaml"),
        ("targets", "dwarf/targets/manifests/target-demo.yaml"),
        ("profiles", "dwarf/profiles/profile-demo/profile.yaml"),
    ],
)
def test_bulk_export_is_deterministic_and_contains_only_definitions(
    tmp_path, monkeypatch, catalog, expected_member
):
    _write_catalog_fixture(tmp_path, monkeypatch)

    first = dashboard.dispatch_api_request(f"/api/catalog/{catalog}/export")
    second = dashboard.dispatch_api_request(f"/api/catalog/{catalog}/export")

    assert first is not None and second is not None
    assert first[0] == 200
    assert first[1] == "application/gzip"
    assert first[2] == second[2]
    with tarfile.open(fileobj=io.BytesIO(first[2]), mode="r:gz") as archive:
        names = archive.getnames()
    assert names == [expected_member]
    assert not any("pending" in name or "._" in name or ".DS_Store" in name for name in names)


@pytest.mark.parametrize("catalog", ["scenarios", "targets", "profiles"])
def test_definition_detail_page_has_complete_source_and_actions(tmp_path, monkeypatch, catalog):
    _write_catalog_fixture(tmp_path, monkeypatch)
    definition_id = {
        "scenarios": "scenario-demo",
        "targets": "target-demo",
        "profiles": "profile-demo",
    }[catalog]

    html = dashboard.render_route_html(f"/operate/{catalog}/{definition_id}")

    assert html is not None
    assert definition_id in html
    assert "Complete definition" in html
    assert f"/operate/{catalog}/{definition_id}/edit" in html
    assert f"/api/catalog/{catalog}/{definition_id}/download" in html


def test_unknown_catalog_and_missing_definition_are_not_found(tmp_path, monkeypatch):
    _write_catalog_fixture(tmp_path, monkeypatch)

    unknown = dashboard.dispatch_api_request("/api/catalog/packages/example/download")
    missing = dashboard.dispatch_api_request("/api/catalog/scenarios/missing/download")

    assert unknown is not None and unknown[0] == 404
    assert missing is not None and missing[0] == 404


def test_live_missing_definition_ids_render_themed_controlled_404_pages(
    tmp_path, monkeypatch
):
    _write_catalog_fixture(tmp_path, monkeypatch)
    handler = dashboard.serve_dashboard_handler_factory("secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        for catalog in ("scenarios", "targets", "profiles"):
            try:
                urlopen(
                    f"http://127.0.0.1:{port}/operate/{catalog}/missing-record"
                )
                raise AssertionError(
                    f"{catalog} missing record unexpectedly returned 200"
                )
            except HTTPError as exc:
                body = exc.read().decode("utf-8")
                assert exc.code == 404
                assert exc.headers.get_content_type() == "text/html"
                assert "<h1>" in body and "not found</h1>" in body
                assert f'href="/operate/{catalog}"' in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_profile_schema_covers_every_bundled_profile_field():
    schema = json.loads(
        Path("dwarf/spec/v1/profile.schema.json").read_text(encoding="utf-8")
    )
    expected = {
        "id", "label", "node_type", "node_count", "amaru_node_count",
        "haskell_count", "amaru_count", "network_magic", "peer_sharing",
        "remote_runtime_root", "compose_project", "topology_pattern",
        "shared_genesis", "amaru_network", "upstream_peer_address",
        "listen_address", "config_source_dir", "public_network", "testbed",
        "version_policy", "cardano_version", "amaru_version",
        "compatibility_pair",
        "measurement_target_mode", "measurement_patch_revision",
        "measurement_patch_set_sha256", "amaru_json_traces",
        "plutus_v2_genesis", "plutus_v2_cost_model_path",
        "plutus_v2_cost_model_sha256", "cardano_experimental_protocols",
    }
    assert set(schema["properties"]) == expected
    assert schema["properties"]["node_type"]["enum"] == ["cardano-node", "amaru", "mixed"]
    assert schema["properties"]["topology_pattern"]["enum"] == ["local-mesh"]


@pytest.mark.parametrize(
    "schema_path",
    [
        "dwarf/spec/v1/profile.schema.json",
        "dwarf/spec/v1/target-manifest.schema.json",
    ],
)
def test_definition_schema_documents_every_field_and_enum_value(schema_path):
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))

    for name, details in schema["properties"].items():
        assert details.get("description"), f"{name} has no field description"
        if details.get("enum"):
            option_help = details.get("x-ui-option-descriptions") or {}
            assert set(option_help) == {str(value) for value in details["enum"]}
            assert all(option_help.values())


def test_scenario_descriptor_supplies_help_for_every_field_and_primitive_parameter():
    descriptor = scenario_editor_descriptor()

    for name, details in descriptor["scenario_schema"]["properties"].items():
        assert details.get("x-ui-help"), f"scenario field {name} has no UI help"
        if details.get("enum"):
            assert set(details.get("x-ui-option-descriptions") or {}) == {
                str(value) for value in details["enum"]
            }
    for primitive, entry in descriptor["primitives"].items():
        for name, details in entry["params_schema"].get("properties", {}).items():
            assert details.get("x-ui-help"), f"{primitive}.{name} has no UI help"
            if details.get("enum"):
                assert set(details.get("x-ui-option-descriptions") or {}) == {
                    str(value) for value in details["enum"]
                }


@pytest.mark.parametrize("catalog", ["profiles", "targets"])
def test_definition_editor_exposes_accessible_hover_help_and_selected_option_help(catalog):
    html = dashboard.render_route_html(f"/operate/{catalog}/new")

    assert html is not None
    assert 'class="definition-field__name"' in html
    assert 'data-field-help=' in html
    assert 'aria-describedby=' in html
    assert 'data-option-help=' in html


def test_definition_field_help_is_visible_for_touch_focus():
    css = Path("dwarf/dashboard/static/css/base.css").read_text(encoding="utf-8")

    assert ".definition-field__name:focus::after" in css


def test_builder_help_uses_one_themed_visual_surface_without_native_tooltips():
    template = Path("dwarf/dashboard/templates/operate/definition_editor.j2").read_text(encoding="utf-8")
    definition_script = Path("dwarf/dashboard/static/js/definition-editor.js").read_text(encoding="utf-8")
    scenario_script = Path("dwarf/dashboard/static/js/scenario-editor.js").read_text(encoding="utf-8")
    css = Path("dwarf/dashboard/static/css/base.css").read_text(encoding="utf-8")

    assert 'title="{{ field.description }}"' not in template
    assert 'title="{{ option.description }}"' not in template
    assert "select.title =" not in definition_script
    assert "select.title =" not in scenario_script
    assert "name.title =" not in scenario_script
    assert "option.title =" not in scenario_script
    assert "ensureSelectedOptionTooltip" in definition_script
    assert "ensureSelectedOptionTooltip" in scenario_script
    assert ".definition-select-tooltip" in css
    assert "visually-hidden definition-field__description" in template


@pytest.mark.parametrize(
    "script_path",
    [
        "dwarf/dashboard/static/js/definition-editor.js",
        "dwarf/dashboard/static/js/scenario-editor.js",
    ],
)
def test_raw_create_validation_refreshes_document_identity_before_save(script_path):
    script = Path(script_path).read_text(encoding="utf-8")

    assert "const validationId = create ? '' : originalId;" in script
    assert "if (action === 'validate' && payload.ok) model = payload.data;" in script


def test_scenario_editor_bootstraps_schema_help_for_static_and_dynamic_fields():
    html = dashboard.render_route_html("/operate/scenarios/new")
    script = Path("dwarf/dashboard/static/js/scenario-editor.js").read_text(encoding="utf-8")

    assert html is not None
    assert 'data-scenario-field="runtime"' in html
    assert "decorateScenarioFields" in script
    assert "updateSelectedOptionHelp" in script
    assert "x-ui-help" in script


def test_profile_editor_contains_all_fields_raw_source_and_immutable_id(tmp_path, monkeypatch):
    _write_catalog_fixture(tmp_path, monkeypatch)

    html = dashboard.render_route_html("/operate/profiles/profile-demo/edit")

    assert html is not None
    for field in ("label", "node_type", "node_count", "amaru_node_count", "network_magic", "peer_sharing"):
        assert f'data-field="{field}"' in html
    assert 'data-field="id" data-field-type="string" readonly aria-readonly="true"' in html
    assert "Advanced raw JSON/YAML" in html
    assert '"label": "Profile demo"' in html


def test_new_profile_opens_builder_with_templates():
    html = dashboard.render_route_html("/operate/profiles/new")

    assert html is not None
    assert "Create profile" in html
    assert "Template" in html
    assert 'data-editor-template' in html
    assert 'data-editor-save disabled' in html


def test_profile_builder_has_structured_version_authority_and_live_exact_preview():
    html = dashboard.render_route_html("/operate/profiles/new")

    assert html is not None
    assert 'data-profile-version-panel' in html
    assert "Node versions" in html
    assert "Recommended" in html and "latest-confirmed" in html
    assert "Advanced" in html and "exact" in html
    assert "Experimental" in html and "latest-stable" in html
    assert 'data-profile-version-resolution' in html
    assert 'data-profile-version-descriptor' in html
    assert 'data-profile-version-fields' in html
    assert 'href="/operate/versions"' in html
    assert 'href="/learn/versions"' in html
    assert 'data-version-field="cardano_version"' in html
    assert 'data-version-field="amaru_version"' in html
    assert 'data-version-field="compatibility_pair"' in html
    script = Path("dwarf/dashboard/static/js/definition-editor.js").read_text(encoding="utf-8")
    assert "versionFieldSlot.append(field)" in script


def test_scenario_builder_discloses_profile_controlled_exact_runtime_versions(
    tmp_path, monkeypatch
):
    _write_catalog_fixture(tmp_path, monkeypatch)

    html = dashboard.render_route_html("/operate/scenarios/new")

    assert html is not None
    assert 'data-scenario-profile-resolution' in html
    assert "Cardano-node 10.7.1" in html
    assert "Amaru 10.11.0" in html
    assert "Runtime versions come from the selected deployment profile" in html
    assert "descriptive target metadata—not the deployment pin" in html
    assert 'href="/operate/profiles"' in html
    assert 'href="/operate/versions"' in html


def test_target_builder_explains_profile_version_authority():
    html = dashboard.render_route_html("/operate/targets/new")

    assert html is not None
    assert 'data-target-version-boundary' in html
    assert "Targets identify a test surface" in html
    assert "Deployment profiles select the exact runtime node artifacts" in html
    assert 'href="/operate/profiles"' in html
    assert 'href="/learn/versions"' in html


def test_profile_validation_accepts_yaml_and_returns_normalized_data(tmp_path, monkeypatch):
    _write_catalog_fixture(tmp_path, monkeypatch)
    body = b"""id: profile-yaml\nlabel: YAML profile\nhaskell_count: 1\namaru_count: 1\nnetwork_magic: 42\npeer_sharing: false\ncustom_extension:\n  retained: true\n"""

    response = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/profiles/validate?token=secret&id=profile-yaml",
        body=body,
        expected_token="secret",
    )

    assert response is not None and response[0] == 200
    payload = json.loads(response[2])
    assert payload["ok"] is True
    assert payload["data"]["custom_extension"] == {"retained": True}


def test_profile_save_is_token_gated_atomic_and_preserves_unknown_fields(tmp_path, monkeypatch):
    roots = _write_catalog_fixture(tmp_path, monkeypatch)
    source_path = roots["profiles"] / "profile-demo" / "profile.yaml"
    before = source_path.read_bytes()
    changed = json.loads(before)
    changed["label"] = "Changed safely"
    changed["custom_extension"] = {"retained": True}
    body = (json.dumps(changed) + "\n").encode()

    denied = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/profiles/profile-demo/save?token=wrong&create=0",
        body=body,
        expected_token="secret",
    )
    assert denied is not None and denied[0] == 403
    assert source_path.read_bytes() == before

    saved = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/profiles/profile-demo/save?token=secret&create=0",
        body=body,
        expected_token="secret",
    )
    assert saved is not None and saved[0] == 200
    persisted = json.loads(source_path.read_text())
    assert persisted["label"] == "Changed safely"
    assert persisted["custom_extension"] == {"retained": True}
    assert not list(source_path.parent.glob(".*.tmp"))


def test_profile_save_rejects_id_change_and_invalid_body_without_modifying_file(tmp_path, monkeypatch):
    roots = _write_catalog_fixture(tmp_path, monkeypatch)
    source_path = roots["profiles"] / "profile-demo" / "profile.yaml"
    before = source_path.read_bytes()
    changed_id = json.loads(before)
    changed_id["id"] = "renamed-profile"

    mismatch = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/profiles/profile-demo/save?token=secret&create=0",
        body=json.dumps(changed_id).encode(),
        expected_token="secret",
    )
    invalid = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/profiles/profile-demo/save?token=secret&create=0",
        body=b'{"id":"profile-demo","label":"bad","node_count":0,"amaru_node_count":0,"network_magic":42,"peer_sharing":false}',
        expected_token="secret",
    )

    assert mismatch is not None and mismatch[0] == 422
    assert invalid is not None and invalid[0] == 422
    assert source_path.read_bytes() == before


def test_profile_create_refuses_overwrite_and_creates_new_definition(tmp_path, monkeypatch):
    roots = _write_catalog_fixture(tmp_path, monkeypatch)
    new_profile = {
        "id": "profile-created", "label": "Created", "haskell_count": 2,
        "amaru_count": 1, "network_magic": 42, "peer_sharing": False,
        "topology_pattern": "local-mesh", "shared_genesis": True,
    }
    body = json.dumps(new_profile).encode()

    created = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/profiles/profile-created/save?token=secret&create=1",
        body=body,
        expected_token="secret",
    )
    duplicate = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/profiles/profile-created/save?token=secret&create=1",
        body=body,
        expected_token="secret",
    )

    assert created is not None and created[0] == 200
    assert duplicate is not None and duplicate[0] == 409
    assert (roots["profiles"] / "profile-created" / "profile.yaml").is_file()


def test_profiles_catalog_is_compact_searchable_and_keeps_deploy(monkeypatch, tmp_path):
    roots = _write_catalog_fixture(tmp_path, monkeypatch)
    from profile_manager import profiles

    monkeypatch.setattr(profiles, "PROFILE_ROOT", roots["profiles"])
    html = dashboard.render_route_html("/operate/profiles")

    assert html is not None
    assert 'data-profile-filter' in html
    assert 'class="profile-catalog"' in html
    assert "Deploy" in html or "Copy deploy" in html
    assert "profile-entry__fields" not in html


def test_target_schema_covers_every_bundled_manifest_field():
    schema = json.loads(
        Path("dwarf/spec/v1/target-manifest.schema.json").read_text(encoding="utf-8")
    )
    assert set(schema["properties"]) == {
        "id", "binary", "harness", "implementation", "language",
        "input_format", "decoder_type", "status", "upstream_commit",
        "invariants", "expected_outcomes", "complements", "source_caveat",
    }
    assert schema["properties"]["implementation"]["enum"] == ["amaru", "cardano-node"]
    assert "file_arg" in schema["properties"]["input_format"]["enum"]


def test_definition_schemas_accept_every_bundled_profile_and_target():
    for schema_path, pattern in (
        (Path("dwarf/spec/v1/profile.schema.json"), "dwarf/profiles/*/profile.yaml"),
        (Path("dwarf/spec/v1/target-manifest.schema.json"), "dwarf/targets/manifests/*.yaml"),
    ):
        validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
        for path in sorted(Path(".").glob(pattern)):
            errors = list(validator.iter_errors(json.loads(path.read_text(encoding="utf-8"))))
            assert not errors, f"{path}: {[error.message for error in errors]}"


def test_target_editor_has_complete_structured_and_raw_controls(tmp_path, monkeypatch):
    _write_catalog_fixture(tmp_path, monkeypatch)

    html = dashboard.render_route_html("/operate/targets/target-demo/edit")

    assert html is not None
    for field in (
        "binary", "harness", "implementation", "language", "input_format",
        "decoder_type", "status", "upstream_commit", "invariants",
        "expected_outcomes", "complements", "source_caveat",
    ):
        assert f'data-field="{field}"' in html
    assert 'data-field="id" data-field-type="string" readonly' in html
    assert 'data-field-type="array"' in html
    assert 'data-field-type="object"' in html
    assert "Advanced raw JSON/YAML" in html


def test_new_target_opens_shared_builder():
    html = dashboard.render_route_html("/operate/targets/new")

    assert html is not None
    assert "Create target" in html
    assert 'data-field="implementation"' in html
    assert 'data-field="input_format"' in html


@pytest.mark.parametrize(
    ("path", "expected_href"),
    [
        ("/operate/scenarios/new", "/learn/overview#dsl"),
        ("/operate/profiles/new", "/learn/developer-onboarding#authoring-profiles"),
        ("/operate/targets/new", "/learn/developer-onboarding#authoring-targets"),
    ],
)
def test_definition_builders_link_to_contextual_authoring_guidance(path, expected_href):
    html = dashboard.render_route_html(path)

    assert html is not None
    pattern = (
        rf'<a[^>]*href="{re.escape(expected_href)}"[^>]*'
        rf'target="_blank"[^>]*rel="noopener noreferrer"[^>]*>'
        rf'Authoring guide <span aria-hidden="true">↗</span></a>'
    )
    assert re.search(pattern, html)


def test_target_schema_exposes_complete_decoder_taxonomy():
    schema = json.loads(
        Path("dwarf/spec/v1/target-manifest.schema.json").read_text(encoding="utf-8")
    )
    decoder = schema["properties"]["decoder_type"]
    expected = [
        "CBOR codec",
        "Mini-protocol decoder",
        "Coverage-guided AFL++ harness (native SanitizerCoverage)",
    ]

    assert decoder["enum"] == expected
    assert set(decoder["x-ui-option-descriptions"]) == set(expected)
    assert all(decoder["x-ui-option-descriptions"].values())


def test_new_target_requires_an_authored_upstream_revision():
    html = dashboard.render_route_html("/operate/targets/new")

    assert html is not None
    assert '"upstream_commit": ""' in html
    assert "pin-required" not in html
    assert 'data-field="upstream_commit"' in html
    assert 'placeholder="Full upstream Git commit SHA"' in html


def test_learn_page_has_contextual_profile_and_target_authoring_guidance():
    html = dashboard.render_route_html("/learn/developer-onboarding")

    assert html is not None
    assert 'id="authoring-profiles"' in html
    assert 'id="authoring-targets"' in html
    assert "Variants are not a target manifest field" in html
    assert "Coverage-guided AFL++ harness (native SanitizerCoverage)" in html


def test_target_create_edit_and_validation_preserve_optional_extensions(tmp_path, monkeypatch):
    roots = _write_catalog_fixture(tmp_path, monkeypatch)
    target = {
        "id": "target-created", "binary": "dwarf/targets/created",
        "input_format": "file_arg", "implementation": "cardano-node",
        "language": "haskell", "upstream_commit": "abc123",
        "decoder_type": "Mini-protocol decoder", "invariants": ["no host panic"],
        "harness": {"runner": "dwarf-decode-any"},
        "expected_outcomes": {"ok": "exit 0", "crash": "signal"},
        "complements": ["target-demo"], "source_caveat": "proof fixture",
        "custom_extension": {"retained": True},
    }
    created = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/targets/target-created/save?token=secret&create=1",
        body=json.dumps(target).encode(),
        expected_token="secret",
    )
    assert created is not None and created[0] == 200
    path = roots["targets"] / "target-created.yaml"
    assert json.loads(path.read_text())["custom_extension"] == {"retained": True}

    target["decoder_type"] = "CBOR codec"
    edited = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/targets/target-created/save?token=secret&create=0",
        body=json.dumps(target).encode(),
        expected_token="secret",
    )
    assert edited is not None and edited[0] == 200
    assert json.loads(path.read_text())["decoder_type"] == "CBOR codec"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("implementation", "simulator"),
        ("input_format", "socket"),
        ("decoder_type", "invented target surface"),
        ("invariants", "not-a-list"),
        ("harness", []),
    ],
)
def test_target_validation_rejects_unsupported_shapes_without_writing(
    tmp_path, monkeypatch, field, value
):
    roots = _write_catalog_fixture(tmp_path, monkeypatch)
    path = roots["targets"] / "target-demo.yaml"
    before = path.read_bytes()
    target = json.loads(before)
    target[field] = value

    response = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/targets/target-demo/save?token=secret&create=0",
        body=json.dumps(target).encode(),
        expected_token="secret",
    )

    assert response is not None and response[0] == 422
    assert path.read_bytes() == before


def test_scenario_editor_descriptor_covers_every_registered_primitive_schema():
    from profile_manager.data.definition_schemas import scenario_editor_descriptor

    descriptor = scenario_editor_descriptor()
    registry = json.loads(Path("dwarf/primitives/registry.json").read_text())["primitives"]
    primitives = descriptor["primitives"]

    assert set(primitives) == set(registry)
    assert descriptor["primitive_count"] == len(registry)
    assert descriptor["family_counts"] == {
        "setup": 7,
            "load": 132,
        "fault": 5,
        "probe": 3,
            "assertion": 93,
        "teardown": 1,
    }
    for name, entry in primitives.items():
        assert entry["family"] == registry[name]["family"]
        assert entry["runtimes"] == registry[name]["runtimes"]
        assert entry["supports"] == registry[name]["supports"]
        assert entry["params_schema"]["type"] == "object"
        assert isinstance(entry["params_schema"].get("properties", {}), dict)


def _embedded_json(html: str, attribute: str):
    match = re.search(
        rf'<script type="application/json" {attribute}>(.*?)</script>', html, re.DOTALL
    )
    assert match is not None
    return json.loads(unescape(match.group(1)))


def test_new_scenario_opens_registry_driven_structured_builder(tmp_path, monkeypatch):
    _write_catalog_fixture(tmp_path, monkeypatch)

    html = dashboard.render_route_html("/operate/scenarios/new")
    registry = _embedded_json(html, "data-scenario-registry")

    assert html is not None
    assert "Create scenario" in html
    assert "scenario-builder" in html
    assert 'data-scenario-field="target.implementation"' in html
    assert 'data-scenario-field="runtime"' in html
    assert set(registry["primitives"]) == set(
        json.loads(Path("dwarf/primitives/registry.json").read_text())["primitives"]
    )
    for phase in ("setup", "load", "faults", "probes", "assertions", "teardown"):
        assert f'data-scenario-phase="{phase}"' in html
    assert f'{registry["primitive_count"]} registered primitives' in html


def test_scenario_builder_clones_existing_definition_without_losing_fields(
    tmp_path, monkeypatch
):
    roots = _write_catalog_fixture(tmp_path, monkeypatch)
    original = json.loads((roots["scenarios"] / "scenario-demo.yaml").read_text())
    original["m1_trace"] = {"threat_ids": ["T-001"]}
    original["invariants"] = {"tip": "monotonic"}
    (roots["scenarios"] / "scenario-demo.yaml").write_text(json.dumps(original))

    edit_html = dashboard.render_route_html("/operate/scenarios/scenario-demo/edit")
    edit_model = _embedded_json(edit_html, "data-editor-initial")
    clone_html = dashboard.render_route_html("/operate/scenarios/new?template=scenario-demo")
    clone_model = _embedded_json(clone_html, "data-editor-initial")

    assert edit_model == original
    assert clone_model["id"] == "new-scenario"
    assert clone_model["title"] == "New scenario"
    assert clone_model["m1_trace"] == original["m1_trace"]
    assert clone_model["invariants"] == original["invariants"]


def test_scenario_builder_exposes_schema_driven_parameter_controls():
    js = Path("dwarf/dashboard/static/js/scenario-editor.js").read_text()

    assert "params_schema" in js
    assert "data-param-mode" in js
    assert "FUZZ" in js
    assert "minimum" in js
    assert "enum" in js
    assert "runtimes" in js
    assert "supports" in js


def test_scenario_builder_preserves_object_parameters_behind_local_schema_refs():
    js = Path("dwarf/dashboard/static/js/scenario-editor.js").read_text()

    assert "function resolveLocalSchema" in js
    assert "resolveLocalSchema(paramSchema, schema)" in js
    assert "if (value && typeof value === 'object') return 'json';" in js


def test_scenario_parameter_mode_choices_are_supported_and_persist_after_change():
    js = Path("dwarf/dashboard/static/js/scenario-editor.js").read_text()

    assert "function makeModeSelect(mode, schema)" in js
    assert "option.disabled = true" in js
    assert "function renderParam(row, name, schema, value, required, forcedMode)" in js
    assert "forcedMode || paramMode(value, schema)" in js
    assert "modeSelect.value" in js


def test_scenario_builder_uses_the_dashboard_runtime_token(tmp_path, monkeypatch):
    _write_catalog_fixture(tmp_path, monkeypatch)

    html = dashboard.render_route_html(
        "/operate/scenarios/scenario-demo/edit", token="runtime-token"
    )

    assert 'data-token="runtime-token"' in html


def test_scenario_save_round_trip_and_invalid_primitive_fail_closed(tmp_path, monkeypatch):
    roots = _write_catalog_fixture(tmp_path, monkeypatch)
    path = roots["scenarios"] / "scenario-demo.yaml"
    scenario = json.loads(path.read_text())
    scenario["title"] = "Edited without losing traceability"
    scenario["m1_trace"] = {"threat_ids": ["T-001"], "gap_ids": ["G-002"]}

    edited = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/scenarios/scenario-demo/save?token=secret&create=0",
        body=json.dumps(scenario).encode(),
        expected_token="secret",
    )
    assert edited is not None and edited[0] == 200
    assert json.loads(path.read_text()) == scenario

    before = path.read_bytes()
    scenario["load"] = [{"primitive": "not_registered"}]
    rejected = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/scenarios/scenario-demo/save?token=secret&create=0",
        body=json.dumps(scenario).encode(),
        expected_token="secret",
    )
    assert rejected is not None and rejected[0] == 422
    assert path.read_bytes() == before


def test_preexisting_parameter_schema_drift_is_visible_but_new_drift_is_rejected(
    tmp_path, monkeypatch
):
    roots = _write_catalog_fixture(tmp_path, monkeypatch)
    path = roots["scenarios"] / "scenario-demo.yaml"
    scenario = json.loads(path.read_text())
    scenario["target"]["implementation"] = "cardano-node"
    scenario["assertions"] = [
        {"primitive": "aflpp_smoke_exit_clean", "min_cycles_done": 0}
    ]
    path.write_text(json.dumps(scenario))

    assert dashboard.render_route_html("/operate/scenarios/scenario-demo") is not None
    scenario["title"] = "Unrelated legacy edit"
    unchanged_drift = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/scenarios/scenario-demo/save?token=secret&create=0",
        body=json.dumps(scenario).encode(),
        expected_token="secret",
    )
    assert unchanged_drift is not None and unchanged_drift[0] == 200

    before = path.read_bytes()
    scenario["assertions"][0]["min_cycles_done"] = -1
    worsened = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/scenarios/scenario-demo/save?token=secret&create=0",
        body=json.dumps(scenario).encode(),
        expected_token="secret",
    )
    assert worsened is not None and worsened[0] == 422
    assert path.read_bytes() == before

    scenario["id"] = "scenario-new"
    created = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path="/api/catalog/scenarios/scenario-new/save?token=secret&create=1",
        body=json.dumps(scenario).encode(),
        expected_token="secret",
    )
    assert created is not None and created[0] == 422
    assert not (roots["scenarios"] / "scenario-new.yaml").exists()


@pytest.mark.parametrize(
    "scenario_name",
    [
        "amaru-cbor-block-fuzz",
        "cardano-node-mini-protocol-chainsync-fuzz",
        "cardano-amaru-miniprotocol-security-local",
    ],
)
def test_representative_scenarios_round_trip_through_catalog_validation(scenario_name):
    from profile_manager.data.catalog_definitions import (
        load_definition,
        parse_and_validate_definition,
        serialize_definition,
    )

    record = load_definition("scenarios", scenario_name)
    reparsed = parse_and_validate_definition(
        "scenarios", serialize_definition(record.data), expected_id=scenario_name
    )

    assert reparsed == record.data


def test_operator_docs_describe_catalog_builders_and_gui_execution():
    getting_started = Path("dwarf/dashboard/templates/learn/getting_started.j2").read_text()
    operator_runbook = Path("dwarf/dashboard/templates/learn/operator_runbook.j2").read_text()
    scenarios_page = Path("dwarf/dashboard/templates/operate/scenarios.j2").read_text()

    for source in (getting_started, operator_runbook):
        assert "/operate/scenarios/new" in source
        assert "structured builder" in source.lower()
        assert "Advanced raw JSON/YAML" in source
        assert "Download all" in source
    assert "+ New scenario</a>" in scenarios_page
