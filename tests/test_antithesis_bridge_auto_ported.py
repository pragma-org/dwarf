"""F25: the Antithesis composite bridge must serve an auto-ported composite build
(any revision ported from the correction base), not only the packaged 11.1.2."""
import copy, json
from pathlib import Path
import pytest
from profile_manager import antithesis_cardano_bridge as bridge
from profile_manager import measurement_patch_port as port_module
from profile_manager.measurement_collectors import cardano_patched as shared

ROOT = Path(__file__).resolve().parents[1] / 'dwarf'
NEW = '9' * 40


def _identity(**overrides):
    identity = {'implementation': 'cardano-node', 'mode': 'patched', 'version': '11.1.3', 'source_revision': NEW,
                'patch_set_sha256': bridge.COMPOSITE_HASH, 'measurement_revision': bridge.REVISION, 'compatibility': bridge.contract(),
                'toolchain': shared.CARDANO_HEADER_TOOLCHAIN, 'image_digest': 'sha256:' + 'a' * 64, 'image_config_id': 'sha256:' + 'a' * 64,
                'identity_kind': 'operator-local-config', 'controller_installation_id': 'install-test',
                'source_build': {'variant': 'instrumented', 'build_result_sha256': 'sha256:' + 'c' * 64, 'instrumentation_evidence_sha256': 'sha256:' + 'd' * 64,
                                 'patch_set_sha256': bridge.COMPOSITE_HASH, 'measurement_revision': bridge.REVISION,
                                 'auto_ported': True, 'ported_from_source_revision': bridge.SOURCE_REVISION},
                'verified_instrumentation': {'config_image_id': 'sha256:' + 'a' * 64, 'controller_installation_id': 'install-test', 'evidence_sha256': 'sha256:' + 'd' * 64}}
    identity.update(overrides)
    return identity


def test_base_identity_accepts_a_build_auto_ported_from_the_correction_base():
    base = bridge._base_identity(_identity())
    assert base['patch_set_sha256'] == bridge.BASE_HASH and base['measurement_revision'] == bridge.BASE_REVISION
    assert base['source_build']['patch_set_sha256'] == bridge.BASE_HASH and base['source_revision'] == NEW


def test_base_identity_still_refuses_other_bases_and_the_packaged_shape_still_works():
    with pytest.raises(ValueError, match='compatibility identity differs'):
        bridge._base_identity(_identity(source_build={**_identity()['source_build'], 'ported_from_source_revision': 'b' * 40}))
    with pytest.raises(ValueError, match='compatibility identity differs'):
        bridge._base_identity(_identity(source_build={k: v for k, v in _identity()['source_build'].items() if k not in ('auto_ported', 'ported_from_source_revision')}))
    packaged = _identity(version='11.1.2', source_revision=bridge.SOURCE_REVISION,
                         source_build={k: v for k, v in _identity()['source_build'].items() if k not in ('auto_ported', 'ported_from_source_revision')})
    assert bridge._base_identity(packaged)['patch_set_sha256'] == bridge.BASE_HASH


def test_build_factories_serve_an_auto_ported_composite_build(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    base = json.loads((ROOT / 'targets/cardano-node/measurement-patches-nanoseconds-v5/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json').read_text())
    derived = bridge.composite_manifest(port_module.derive_manifest(base, source_revision=NEW, release='11.1.3',
        preimages=[{'path': p['path'], 'preimages': {q['path']: q['sha256'] for q in p['preimages']}} for p in base['patches']], base_recipe_dir='d'))
    port_module.save_derived_recipe('cardano-node', NEW, derived, {})
    factories = bridge.build_factories(json_trace_paths=[], target_identity=_identity())
    assert factories and all(callable(f) for f in factories.values())
