"""AT-4: Antithesis results back into DWARF's one output.

Fixtures under tests/fixtures/antithesis_proxy/ are sanitised copies of the
MOOG proxy output for the 2026-10-20 run on cardano-box (no links, no report
URL, synthetic testRunId, two synthetic DWARF SDK properties)."""
import json
from pathlib import Path

import pytest

from profile_manager import remote
from profile_manager.config import DeploymentConfig
from profile_manager.moog import normalize_moog_config

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'antithesis_proxy'
TEST_RUN_ID = '1' * 64
RUN_ID = '788998d60b09b96953e830e9f9ae2c7b-62-14'


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def config():
    cfg = DeploymentConfig.from_dict({'moog': {'enabled': True, 'token_id': 'token', 'moog_binary': '/bin/moog', 'secrets_root': '/secrets'}})
    return cfg


def moog_config():
    return normalize_moog_config({'enabled': True, 'token_id': 'token', 'moog_binary': '/bin/moog', 'secrets_root': '/secrets'})


def fake_ssh(monkeypatch, responses):
    """responses: list of (returncode, stdout, stderr) consumed in order; records commands."""
    seen = []
    def ssh(config, command, timeout=None, dry_run=False, verb=None):
        seen.append(command)
        rc, out, err = responses.pop(0)
        return remote.CommandResult(rc, out, err, command)
    monkeypatch.setattr(remote, 'ssh_command', ssh)
    return seen


# ---------------------------------------------------------------------------
# Task 1: proxy readers

def test_fetch_antithesis_builds_the_proxy_command_without_moog_env(config, monkeypatch):
    seen = fake_ssh(monkeypatch, [(0, '{"data":[],"next_cursor":null}', '')])
    out = remote.fetch_antithesis(config, moog_config(), 'runs', limit=50, cursor='Mg==')
    assert out == {'ok': True, 'payload': {'data': [], 'next_cursor': None}, 'exit_code': 0, 'error': ''}
    assert seen == ['/bin/moog antithesis runs --limit 50 --cursor Mg==']
    assert 'MOOG_' not in seen[0]


def test_fetch_antithesis_validates_every_value_before_building_a_command(config, monkeypatch):
    seen = fake_ssh(monkeypatch, [])
    for kwargs in ({'run_id': 'abc; rm -rf /'}, {'run_id': 'not-a-run-id'}, {'run_id': RUN_ID, 'query': 'a b'}, {'cursor': 'x y'}, {'limit': 'ten'}):
        with pytest.raises(ValueError):
            remote.fetch_antithesis(config, moog_config(), 'runs' if 'cursor' in kwargs or 'limit' in kwargs else ('events' if 'query' in kwargs else 'run'), **kwargs)
    assert seen == []


@pytest.mark.parametrize('rc,stdout,stderr,error', [
    (2, '', 'sso', 'authorization or SSO failure (exit 2)'),
    (3, '', 'down', 'proxy or network failure (exit 3)'),
    (4, 'garbage', '', 'invalid JSON from proxy (exit 4)'),
    (0, 'nope', '', 'invalid JSON: Expecting value'),
])
def test_fetch_antithesis_reports_exit_codes_and_bad_json(config, monkeypatch, rc, stdout, stderr, error):
    fake_ssh(monkeypatch, [(rc, stdout, stderr)])
    out = remote.fetch_antithesis(config, moog_config(), 'properties', run_id=RUN_ID)
    assert out['ok'] is False and out['payload'] is None and out['exit_code'] == rc
    assert out['error'].startswith(error)
    assert len(out['error']) <= 200


def test_fetch_antithesis_parses_ndjson_for_events(config, monkeypatch):
    seen = fake_ssh(monkeypatch, [(0, '{"a":1}\n\n{"b":2}\n', '')])
    out = remote.fetch_antithesis(config, moog_config(), 'events', run_id=RUN_ID, query='dwarf_measurements')
    assert out['ok'] is True and out['payload'] == [{'a': 1}, {'b': 2}]
    assert seen == ['/bin/moog antithesis events --run-id ' + RUN_ID + ' --q dwarf_measurements']
    fake_ssh(monkeypatch, [(0, '{"a":1}\nnot json\n', '')])
    bad = remote.fetch_antithesis(config, moog_config(), 'logs', run_id=RUN_ID)
    assert bad['ok'] is False and bad['payload'] is None and bad['error'].startswith('invalid NDJSON')


def test_fetch_antithesis_rejects_unknown_subcommands(config, monkeypatch):
    fake_ssh(monkeypatch, [])
    with pytest.raises(ValueError):
        remote.fetch_antithesis(config, moog_config(), 'delete', run_id=RUN_ID)


def test_find_antithesis_run_joins_on_the_description_test_run_id(config, monkeypatch):
    seen = fake_ssh(monkeypatch, [(0, json.dumps(fixture('runs-page-1.json')), ''), (0, json.dumps(fixture('runs-page-2.json')), '')])
    found = remote.find_antithesis_run(config, moog_config(), TEST_RUN_ID)
    assert found['status'] == 'found' and found['matches'] == 1 and found['pages'] == 2
    assert found['run']['run_id'] == fixture('run-completed.json')['run_id']
    assert seen == ['/bin/moog antithesis runs --limit 100', '/bin/moog antithesis runs --limit 100 --cursor cGFnZTI=']


def test_find_antithesis_run_is_ambiguous_when_two_rows_match(config, monkeypatch):
    page = fixture('runs-page-1.json')
    fake_ssh(monkeypatch, [(0, json.dumps(page), '')])
    found = remote.find_antithesis_run(config, moog_config(), '2' * 64)
    assert found['status'] == 'ambiguous' and found['run'] is None and found['matches'] == 2


def test_find_antithesis_run_stops_at_max_pages_and_reports_not_found(config, monkeypatch):
    page = fixture('runs-page-1.json')
    fake_ssh(monkeypatch, [(0, json.dumps(page), '')] * 3)
    found = remote.find_antithesis_run(config, moog_config(), '9' * 64, max_pages=2)
    assert found['status'] == 'not_found' and found['run'] is None and found['matches'] == 0 and found['pages'] == 2 and 'newest 2 runs' in found['error']


def test_find_antithesis_run_skips_rows_without_a_json_description(config, monkeypatch):
    page = fixture('runs-page-2.json')
    page['data'].insert(0, {'run_id': 'odd-1-1', 'status': 'completed', 'description': 'free text'})
    page['data'].insert(0, {'run_id': 'odd-2-1', 'status': 'completed'})
    fake_ssh(monkeypatch, [(0, json.dumps(page), '')])
    assert remote.find_antithesis_run(config, moog_config(), TEST_RUN_ID)['status'] == 'found'


def test_find_antithesis_run_propagates_proxy_errors(config, monkeypatch):
    fake_ssh(monkeypatch, [(3, '', 'connection refused')])
    found = remote.find_antithesis_run(config, moog_config(), TEST_RUN_ID)
    assert found['status'] == 'error' and found['run'] is None and found['error'].startswith('proxy or network failure')


# ---------------------------------------------------------------------------
# Task 2: normaliser and verdict rules

def results_module():
    from profile_manager import antithesis_results
    return antithesis_results


@pytest.mark.parametrize('name,kind', [
    ('Always: victim keeps serving', 'always'), ('Unreachable: panic path', 'always'),
    ('Sometimes: adversary reached block-fetch', 'sometimes'), ('Reachable: handshake completes', 'sometimes'),
    ('Always: Peak memory usage', 'always'), ('No Antithesis errors', 'platform'),
    ('container: amaru-relay-1, exit code: 1', 'platform'), ('always: lowercase is not the SDK prefix', 'platform'),
    ('Always:no space', 'platform'), ('', 'platform'), (None, 'platform'),
])
def test_property_kind_prefixes(name, kind):
    assert results_module().property_kind(name) == kind


@pytest.mark.parametrize('name,source', [
    ('Always: mixed_sm_victim_keeps_serving', 'dwarf'), ('Sometimes: mixed_sm_adversary_reached_block_fetch', 'dwarf'),
    ('Reachable: mixed_sm_workload_command_invoked', 'dwarf'), ('Always: a', 'dwarf'),
    ('Always: Peak memory usage', 'antithesis'), ('Always: 9starts_with_digit', 'antithesis'), ('Always: Mixed_Case', 'antithesis'),
    ('No Antithesis errors', 'antithesis'), ('container: amaru-relay-1, exit code: 1', 'antithesis'),
    # F173: real names from run dc774cde… carry the display type a second time.
    ('Always: Missed Always: dwarf_all_nodes_responsive', 'dwarf'),
    ('Always: Missed Always: dwarf_txsubmission_window_enforced', 'dwarf'),
    ('Sometimes: Sometimes: dwarf_nodes_reachable', 'dwarf'), ('Sometimes: Sometimes: dwarf_scenario_completed', 'dwarf'),
    ('Always: Commands finish with zero exit code: dwarf/anytime_health.py', 'antithesis'),
    ('Always: Missed Always: Peak memory usage', 'antithesis'),
])
def test_property_source_requires_the_sdk_prefix_and_a_snake_case_dwarf_message(name, source):
    assert results_module().property_source(name) == source


def test_normalise_result_keeps_only_the_sanitised_fields():
    m = results_module()
    run = fixture('run-completed.json'); run['links'] = {'triage_report': 'https://example.invalid/report?auth=SECRET'}
    result = m.normalise_result(run, fixture('properties.json'), test_run_id=TEST_RUN_ID)
    assert result['schema_version'] == m.RESULT_SCHEMA
    assert result['test_run_id'] == TEST_RUN_ID and result['antithesis_run_id'] == run['run_id']
    assert result['run_status'] == 'completed' and result['created_at'] == run['created_at'] and result['completed_at'] == run['completed_at']
    assert result['environment'] == {'antithesis_version': 'v62-14', 'images': [{'name': i['name'], 'tag': i['tag']} for i in run['results']['environment']['images']]}
    assert [p['kind'] for p in result['properties']] == ['platform', 'platform', 'platform', 'always', 'platform', 'always', 'sometimes']
    assert result['properties'][0] == {'id': fixture('properties.json')['data'][0]['id'], 'name': 'No Antithesis errors', 'kind': 'platform', 'source': 'antithesis',
                                       'status': 'Failing', 'passed': False, 'example_count': 0, 'counterexample_count': 2}
    assert [p['source'] for p in result['properties']] == ['antithesis'] * 5 + ['dwarf', 'dwarf'], 'Always: Peak memory usage is a platform property'
    assert result['properties_truncated'] is False and result['dwarf_property_count'] == 2
    assert result['failing'] == ['No Antithesis errors', 'container: amaru-relay-1, exit code: 1']
    text = json.dumps(result)
    for forbidden in ('auth=', 'links', 'description', 'parameters', 'counterexamples', 'examples', 'moment'):
        assert forbidden not in text, forbidden


def test_normalise_result_marks_truncated_property_pages_and_missing_tables():
    m = results_module()
    props = fixture('properties.json'); props['next_cursor'] = 'MgAAAAAAAAA='
    assert m.normalise_result(fixture('run-completed.json'), props, test_run_id=TEST_RUN_ID)['properties_truncated'] is True
    none = m.normalise_result(fixture('run-in-progress.json'), None, test_run_id='2' * 64)
    assert none['properties'] == [] and none['dwarf_property_count'] == 0 and none['run_status'] == 'in_progress' and none['completed_at'] is None


def _result(status='completed', rows=None, properties=True):
    m = results_module()
    run = fixture('run-completed.json'); run['status'] = status
    props = fixture('properties.json')
    if rows is not None:
        props['data'] = rows
    return m.normalise_result(run, props if properties else None, test_run_id=TEST_RUN_ID)


def _truncated(rows):
    m = results_module()
    props = fixture('properties.json'); props['data'] = rows; props['next_cursor'] = 'MgAAAAAAAAA='
    return m.normalise_result(fixture('run-completed.json'), props, test_run_id=TEST_RUN_ID)


def _row(name, status='Passing', examples=1):
    return {'id': 'f' * 32, 'name': name, 'status': status, 'is_event': False, 'is_group': False,
            'example_count': examples, 'examples': [], 'counterexample_count': 0 if status == 'Passing' else 1, 'counterexamples': []}


@pytest.mark.parametrize('result,expected', [
    (lambda: _result(status='in_progress'), ('inconclusive', 'antithesis_run_not_terminal')),
    (lambda: _result(properties=False), ('inconclusive', 'properties_unavailable')),
    (lambda: _result(rows=[]), ('inconclusive', 'properties_unavailable')),
    (lambda: _result(rows=[_row('No Antithesis errors')]), ('inconclusive', 'no_dwarf_assertions')),
    (lambda: _result(rows=[_row('No Antithesis errors'), _row('Always: a', 'Failing')]), ('fail', 'assertions_failed')),
    (lambda: _result(rows=[_row('No Antithesis errors', 'Failing'), _row('Always: a')]), ('fail', 'platform_property_failed')),
    (lambda: _result(rows=[_row('No Antithesis errors'), _row('Always: a'), _row('Sometimes: b', examples=0)]), ('fail', 'sometimes_not_encountered')),
    (lambda: _result(rows=[_row('No Antithesis errors'), _row('Always: a'), _row('Sometimes: b', examples=3)]), ('pass', 'verified_antithesis_report')),
    (lambda: _result(), ('fail', 'platform_property_failed')),   # the real 2026-10-20 table: DWARF rows pass, platform rows fail
    (lambda: _truncated([_row('No Antithesis errors'), _row('Always: a'), _row('Sometimes: b', examples=3)]), ('inconclusive', 'properties_truncated')),
    (lambda: _truncated([_row('No Antithesis errors'), _row('Always: a', 'Failing')]), ('fail', 'assertions_failed')),
])
def test_result_verdict_rules(result, expected):
    assert results_module().result_verdict(result()) == expected


def test_result_assertions_mark_unencountered_sometimes_as_fail():
    rows = [_row('No Antithesis errors', 'Failing'), _row('Always: a'), _row('Sometimes: b', examples=0), _row('Sometimes: c', examples=2)]
    assertions = results_module().result_assertions(_result(rows=rows))
    assert [(a['name'], a['kind'], a['source'], a['result']) for a in assertions] == [
        ('No Antithesis errors', 'platform', 'antithesis', 'fail'), ('Always: a', 'always', 'dwarf', 'pass'),
        ('Sometimes: b', 'sometimes', 'dwarf', 'fail'), ('Sometimes: c', 'sometimes', 'dwarf', 'pass')]
    assert all(set(a) == {'id', 'name', 'kind', 'source', 'result'} for a in assertions)


# ---------------------------------------------------------------------------
# Task 3: bundle writer

def _entry(tmp_path):
    run_dir = tmp_path / 'runs' / ('external-antithesis-' + 'a' * 24)
    return run_dir, {'run_id': run_dir.name, 'kind': 'external', 'backend': 'antithesis', 'evidence_path': str(run_dir),
                     'scenario': {'id': 'cardano-amaru-miniprotocol-security-local', 'sha256': 'b' * 64},
                     'provenance': {'external_test_run_id': TEST_RUN_ID}}


def _write(tmp_path, m, *, run=None, props=None, measurements=None, scenario_source=b'id: x\n'):
    run_dir, entry = _entry(tmp_path)
    run = run or fixture('run-completed.json'); props = fixture('properties.json') if props is None else props
    result = m.normalise_result(run, props, test_run_id=TEST_RUN_ID)
    path = m.write_result_bundle(run_dir, entry=entry, result=result, raw={'run': run, 'properties': props},
                                 scenario_source=scenario_source, measurements=measurements)
    return run_dir, entry, result, path


def test_write_result_bundle_lays_out_local_shape_and_binds_the_chain(tmp_path):
    import hashlib
    m = results_module()
    run = fixture('run-completed.json'); run['links'] = {'triage_report': 'https://example.invalid/report?auth=SECRET'}
    run_dir, entry, result, manifest_path = _write(tmp_path, m, run=run)
    assert manifest_path == run_dir / 'manifest.json'
    for rel in ('external/result.json', 'external/report/run.json', 'external/report/properties.json', 'assertions.json', 'chain.json', 'scenario.yaml', 'gaps.json'):
        assert (run_dir / rel).is_file(), rel
    assert (run_dir / 'external' / 'report').stat().st_mode & 0o777 == 0o700
    assert (run_dir / 'external' / 'report' / 'run.json').stat().st_mode & 0o777 == 0o600
    manifest = json.loads(manifest_path.read_text()); chain = json.loads((run_dir / 'chain.json').read_text())
    from profile_manager.data.measurement_compare import _evidence_state
    assert _evidence_state(run_dir.name, manifest, chain) == ('bound', chain['manifest_hash'])
    assert chain['run_id'] == run_dir.name and chain['source'] == 'antithesis-report'
    assert manifest['schema_version'] == 'v1' and manifest['run_id'] == run_dir.name
    assert manifest['runtime'] == 'antithesis' and manifest['kind'] == 'external' and manifest['backend'] == 'antithesis'
    assert manifest['scenario'] == entry['scenario']
    assert manifest['external'] == {'backend': 'antithesis', 'test_run_id': TEST_RUN_ID, 'antithesis_run_id': run['run_id'], 'run_status': 'completed'}
    assert manifest['exit_status'] == 'pass' and manifest['started_at'] == run['created_at'] and manifest['ended_at'] == run['completed_at']
    assert manifest['assertion_summary'] == {'total': 7, 'pass': 5, 'fail': 2}
    assert manifest['execution']['security_verdict'] == 'fail'
    assert manifest['result'] == {'source': 'antithesis-report', 'sha256': hashlib.sha256((run_dir / 'external' / 'result.json').read_bytes()).hexdigest()}
    assert 'measurements' not in manifest
    assert json.loads((run_dir / 'assertions.json').read_text()) == m.result_assertions(result)
    assert (run_dir / 'scenario.yaml').read_bytes() == b'id: x\n'
    gaps = json.loads((run_dir / 'gaps.json').read_text())
    assert gaps['schema_version'] == 'v1' and gaps['runtime'] == 'antithesis'
    assert {g['path'] for g in gaps['missing']} >= {'probes/', 'metrics/', 'launch/', 'measurements/report.json'}
    assert all(g['reason'] for g in gaps['missing'])
    everything = ''.join(p.read_text() for p in run_dir.rglob('*') if p.is_file())
    assert 'auth=' not in everything and 'triage_report' not in everything, 'the report link never enters the bundle, not even the raw run row'


def test_write_result_bundle_refuses_to_overwrite_a_complete_bundle(tmp_path):
    m = results_module()
    run_dir, entry, result, path = _write(tmp_path, m)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='already complete'):
        m.write_result_bundle(run_dir, entry=entry, result=result, raw={'run': fixture('run-completed.json'), 'properties': fixture('properties.json')},
                              scenario_source=None, measurements=None)
    assert path.read_bytes() == before


@pytest.mark.parametrize('rows,status,exit_status,security', [
    ([_row('No Antithesis errors'), _row('Always: a'), _row('Sometimes: b', examples=2)], 'completed', 'pass', 'pass'),
    ([_row('No Antithesis errors'), _row('Always: a', 'Failing')], 'completed', 'pass', 'fail'),
    ([_row('No Antithesis errors'), _row('Always: a')], 'in_progress', 'fail', 'fail'),
])
def test_bundle_manifest_verdict_agrees_with_result_verdict(tmp_path, rows, status, exit_status, security):
    m = results_module()
    run = fixture('run-completed.json'); run['status'] = status
    props = fixture('properties.json'); props['data'] = rows
    run_dir, entry, result, path = _write(tmp_path, m, run=run, props=props)
    manifest = json.loads(path.read_text())
    assert manifest['exit_status'] == exit_status and manifest['execution']['security_verdict'] == security
    verdict, _reason = m.result_verdict(result)
    assert verdict == security or verdict == 'inconclusive'


def test_write_result_bundle_includes_measurements_when_given(tmp_path):
    m = results_module()
    measurements = {'schema_version': 'v1', 'scenario': 'x', 'source': 'antithesis-events', 'complete': False, 'measurements': {'block_application': {'status': 'available', 'value': 1.5, 'unit': 'us'}}}
    run_dir, entry, result, path = _write(tmp_path, m, measurements=measurements, scenario_source=None)
    manifest = json.loads(path.read_text())
    assert manifest['measurements'] == measurements
    assert json.loads((run_dir / 'measurements' / 'report.json').read_text()) == measurements
    assert 'measurements/report.json' not in {g['path'] for g in json.loads((run_dir / 'gaps.json').read_text())['missing']}
    assert not (run_dir / 'scenario.yaml').exists()


# ---------------------------------------------------------------------------
# Task 8a: one builder for the proxy commands (dashboard and control shim)

def test_moog_proxy_command_builders_validate_and_render():
    from profile_manager import moog as m
    cfg = moog_config()
    assert m.build_moog_antithesis_command(cfg, 'run', run_id=RUN_ID) == '/bin/moog antithesis run --run-id ' + RUN_ID
    assert m.build_moog_antithesis_command(cfg, 'runs', limit=50, cursor='cGFnZTI=') == '/bin/moog antithesis runs --limit 50 --cursor cGFnZTI='
    assert m.build_moog_antithesis_command(cfg, 'events', run_id=RUN_ID, query='dwarf_measurements') == '/bin/moog antithesis events --run-id ' + RUN_ID + ' --q dwarf_measurements'
    assert m.build_moog_test_runs_command(cfg, 'J-GainSec') == "MOOG_MPFS_HOST=https://mpfs.plutimus.com MOOG_TOKEN_ID=token /bin/moog facts test-runs --whose J-GainSec --no-pretty"
    assert m.build_moog_test_run_command(cfg, 'a' * 64) == "MOOG_MPFS_HOST=https://mpfs.plutimus.com MOOG_TOKEN_ID=token /bin/moog facts test-runs --test-run-id " + 'a' * 64
    for call in (lambda: m.build_moog_antithesis_command(cfg, 'run', run_id='x; rm -rf /'), lambda: m.build_moog_antithesis_command(cfg, 'runs', cursor='a b'),
                 lambda: m.build_moog_antithesis_command(cfg, 'events', run_id=RUN_ID, query='x y'), lambda: m.build_moog_antithesis_command(cfg, 'drop', run_id=RUN_ID),
                 lambda: m.build_moog_antithesis_command(cfg, 'run'), lambda: m.build_moog_test_runs_command(cfg, 'bad user!'), lambda: m.build_moog_test_run_command(cfg, 'zz')):
        with pytest.raises(ValueError):
            call()


def test_requester_probe_builder_embeds_paths_and_reads_no_config_file():
    from profile_manager import moog as m
    cfg = {'moog_binary': '/bin/moog', 'secrets_root': '/srv/secrets', 'requester_wallet_file': '/srv/secrets/requester/requester.json', 'github_pat': 'x'}
    script = m.build_moog_requester_probe_command(cfg)
    assert script.startswith("python3 - <<'PY'\n") and script.rstrip().endswith('PY')
    for needle in ("'/srv/secrets/requester/wallet.passphrase'", "'/srv/secrets/requester/requester.json'", "'/bin/moog'", 'credentials_configured=True', "'wallet','info'"):
        assert needle in script, needle
    assert 'dwarf-v4' not in script and 'config.yaml' not in script and 'x' not in script.split('credentials_configured=True')[0][-2:]
    assert 'credentials_configured=False' in m.build_moog_requester_probe_command({**cfg, 'github_pat': ''})
    with pytest.raises(ValueError, match='bad-secrets-root'):
        m.build_moog_requester_probe_command({**cfg, 'secrets_root': 'rel/path'})


def test_find_antithesis_run_stops_when_the_proxy_repeats_a_page(config, monkeypatch):
    """F36: moog v0.5.1.3's proxy ignores --cursor (every page is the first page)
    and caps a page at 100 rows. The lookup must notice the repeat, stop, and say
    how far it could see instead of burning the page budget."""
    page = fixture('runs-page-1.json'); page['next_cursor'] = 'c2FtZQ=='
    seen = fake_ssh(monkeypatch, [(0, json.dumps(page), '')] * 5)
    found = remote.find_antithesis_run(config, moog_config(), '9' * 64)
    assert found['status'] == 'not_found' and found['pages'] == 2 and found['matches'] == 0
    assert 'newest 2 runs' in found['error'] and 'paging' in found['error']
    assert seen == ['/bin/moog antithesis runs --limit 100', '/bin/moog antithesis runs --limit 100 --cursor c2FtZQ==']


def test_write_result_bundle_drops_nested_links_everywhere(tmp_path):
    """Review #4: links can sit on property rows and counterexamples too."""
    m = results_module()
    run = fixture('run-completed.json'); run['links'] = {'triage_report': 'https://x/?auth=TOP'}
    props = fixture('properties.json')
    props['data'][0]['links'] = {'self': 'https://x/p?auth=TOP'}
    props['data'][0]['counterexamples'] = [{'moment': {'vtime': '1'}, 'links': {'logs': 'https://x/l?auth=TOP'}}]
    run_dir, entry, result, _ = _write(tmp_path, m, run=run, props=props)
    everything = ''.join(p.read_text() for p in run_dir.rglob('*') if p.is_file())
    assert 'auth=' not in everything and '"links"' not in everything


def test_proxy_argument_values_cannot_start_with_a_dash():
    """Review #6: a value beginning with '-' would reach the moog CLI as a flag."""
    from profile_manager import moog as m
    for call in (lambda: m.build_moog_antithesis_command(moog_config(), 'runs', cursor='--help'), lambda: m.build_moog_antithesis_command(moog_config(), 'runs', cursor='-'),
                 lambda: m.build_moog_antithesis_command(moog_config(), 'events', run_id=RUN_ID, query='--help'), lambda: m.build_moog_antithesis_command(moog_config(), 'events', run_id=RUN_ID, query='-x')):
        with pytest.raises(ValueError):
            call()
    assert m.build_moog_antithesis_command(moog_config(), 'logs', run_id=RUN_ID, input_hash='-254310451031', vtime='12.5').endswith('--input-hash -254310451031 --vtime 12.5')


def test_rebuild_measurements_keeps_one_service_per_report():
    """Review #7: the mixed package has one dwarf_measurements service per node; rows of
    other services must not overwrite the chosen implementation's values."""
    import json
    from profile_manager.antithesis_results import antithesis_target_identity, rebuild_measurements
    line = lambda service, value: {"output_text": json.dumps({"dwarf_measurements": service, "collectors": {"c": {"measurements": {"block_application": {"status": "available", "value": value, "unit": "us"}}}}})}
    events = [line("sm-cardano-victim", 1.0), line("sm-amaru-adversary", 9.0), line("sm-cardano-victim", 2.0)]
    identity = antithesis_target_identity(events)
    report = rebuild_measurements(events, scenario="s", target_identity=identity)
    assert identity["implementation"] == "cardano-node" and report["service"] == "sm-cardano-victim"
    assert report["services"] == ["sm-amaru-adversary", "sm-cardano-victim"]
    assert report["measurements"]["block_application"]["value"] == 2.0


def test_write_result_bundle_carries_capability_gaps_into_gaps_json(tmp_path):
    from profile_manager import antithesis_results as m
    run_dir, entry = _entry(tmp_path)
    entry['provenance']['antithesis_capabilities'] = {'level': 'partial', 'reason': None, 'gaps': [
        {'kind': 'implementation-gap', 'code': 'primitive-unsupported-on-antithesis', 'primitive': 'runtime_tracer_capture',
         'phase': 'load', 'message': 'runtime_tracer_capture: docker exec'},
        {'kind': 'implementation-gap', 'code': 'fault-delegated-to-platform', 'primitive': 'fault_node_freeze',
         'phase': 'faults', 'message': 'fault_node_freeze: platform faults'}]}
    run = fixture('run-completed.json'); props = fixture('properties.json')
    result = m.normalise_result(run, props, test_run_id=TEST_RUN_ID)
    m.write_result_bundle(run_dir, entry=entry, result=result, raw={'run': run, 'properties': props}, scenario_source=None, measurements=None)
    gaps = json.loads((run_dir / 'gaps.json').read_text())
    capability = [g for g in gaps['missing'] if g.get('kind') == 'implementation-gap']
    assert [(g['code'], g['primitive'], g['path']) for g in capability] == [
        ('primitive-unsupported-on-antithesis', 'runtime_tracer_capture', 'primitive:runtime_tracer_capture'),
        ('fault-delegated-to-platform', 'fault_node_freeze', 'primitive:fault_node_freeze')]
    assert any(g['path'] == 'measurements/report.json' for g in gaps['missing'])
    manifest = json.loads((run_dir / 'manifest.json').read_text())
    assert manifest['capabilities'] == {'level': 'partial', 'reason': None, 'gap_count': 2}
