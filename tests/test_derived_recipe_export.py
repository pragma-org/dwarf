import hashlib, io, json, tarfile
from pathlib import Path
from urllib.parse import urlencode
import pytest
from profile_manager import dashboard
from profile_manager import measurement_patch_port as port

ROOT = Path(__file__).resolve().parents[1]
V4_DIR = 'measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379'
NEW = 'b' * 40

@pytest.fixture
def persisted(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    base = json.loads((ROOT / 'dwarf/targets/amaru' / V4_DIR / 'manifest.json').read_text())
    derived = port.derive_manifest(base, source_revision=NEW, release='x', preimages={p['path']: 'ab' * 32 for i in base['patches'] for p in i['preimages']}, base_recipe_dir=V4_DIR)
    port.save_derived_recipe('amaru', NEW, derived, {'status': 'ported'})
    return base, derived

def test_export_bundles_manifest_patches_and_destination(persisted):
    base, derived = persisted
    result = port.export_derived_recipe('amaru', NEW)
    assert result['destination'] == f'dwarf/targets/amaru/measurement-patches-{base["measurement_revision"]}/{NEW}/'
    assert Path(result['path']).name == f'derived-recipe-amaru-{NEW[:12]}.tar.gz'
    assert result['sha256'] == 'sha256:' + hashlib.sha256(Path(result['path']).read_bytes()).hexdigest()
    with tarfile.open(result['path']) as tar:
        names = sorted(tar.getnames())
        assert names == sorted(['manifest.json', 'port-result.json', 'DESTINATION.txt', 'README.txt'] + ['patches/' + p['path'] for p in base['patches']])
        assert json.loads(tar.extractfile('manifest.json').read()) == derived
        assert tar.extractfile('DESTINATION.txt').read().decode() == result['destination'] + '\n'
        assert tar.extractfile('README.txt').read().decode().startswith('Auto-ported DWARF measurement patch set — review before committing.')
        patch = base['patches'][0]
        assert hashlib.sha256(tar.extractfile('patches/' + patch['path']).read()).hexdigest() == patch['sha256']
    assert sorted(result['members']) == names

def test_export_refuses_missing_recipe(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    with pytest.raises(port.PortError, match='No auto-ported recipe'):
        port.export_derived_recipe('amaru', 'c' * 40)

def test_export_endpoint_authentication_fields_and_success(persisted, monkeypatch):
    path = '/api/versions/derived-recipe/export'
    assert dashboard.dispatch_derived_recipe_export_request(method='GET', path=path, expected_token='s')[0] == 405
    assert dashboard.dispatch_derived_recipe_export_request(method='POST', path=path, expected_token='s', body=b'')[0] == 401
    bad = urlencode({'token': 's', 'implementation': 'amaru', 'source_revision': 'zz'}).encode()
    assert dashboard.dispatch_derived_recipe_export_request(method='POST', path=path, expected_token='s', body=bad)[0] == 409
    extra = urlencode({'token': 's', 'implementation': 'amaru', 'source_revision': NEW, 'command': 'sh'}).encode()
    assert dashboard.dispatch_derived_recipe_export_request(method='POST', path=path, expected_token='s', body=extra)[0] == 409
    assert dashboard.dispatch_derived_recipe_export_request(method='POST', path='/api/versions/other', expected_token='s') is None
    good = urlencode({'token': 's', 'implementation': 'amaru', 'source_revision': NEW}).encode()
    status, ctype, payload = dashboard.dispatch_derived_recipe_export_request(method='POST', path=path, expected_token='s', body=good)
    body = json.loads(payload)
    assert status == 200 and body['ok'] is True and body['destination'].startswith('dwarf/targets/amaru/')

def test_versions_ui_labels_auto_ported_builds():
    js = (ROOT / 'dwarf/dashboard/static/js/release-preparations.js').read_text()
    for needle in ("' · auto-ported from '", 'Provisional: patch set machine-ported from', 'Export for review', '/api/versions/derived-recipe/export', 'port_attempt'):
        assert needle in js, needle
    learn = (ROOT / 'dwarf/dashboard/templates/learn/versions.j2').read_text()
    assert 'Auto-ported instrumented builds' in learn and 'Export for review' in learn
