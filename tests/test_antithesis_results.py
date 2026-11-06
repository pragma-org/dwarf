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


def test_fetch_antithesis_quotes_every_value(config, monkeypatch):
    seen = fake_ssh(monkeypatch, [(0, '{"run_id":"x"}', '')])
    remote.fetch_antithesis(config, moog_config(), 'run', run_id='abc; rm -rf /')
    assert seen == ["/bin/moog antithesis run --run-id 'abc; rm -rf /'"]


@pytest.mark.parametrize('rc,stdout,stderr,error', [
    (2, '', 'sso', 'authorization or SSO failure (exit 2)'),
    (3, '', 'down', 'proxy or network failure (exit 3)'),
    (4, 'garbage', '', 'invalid JSON from proxy (exit 4)'),
    (0, 'nope', '', 'invalid JSON: Expecting value'),
])
def test_fetch_antithesis_reports_exit_codes_and_bad_json(config, monkeypatch, rc, stdout, stderr, error):
    fake_ssh(monkeypatch, [(rc, stdout, stderr)])
    out = remote.fetch_antithesis(config, moog_config(), 'properties', run_id='r-1-1')
    assert out['ok'] is False and out['payload'] is None and out['exit_code'] == rc
    assert out['error'].startswith(error)
    assert len(out['error']) <= 200


def test_fetch_antithesis_parses_ndjson_for_events(config, monkeypatch):
    seen = fake_ssh(monkeypatch, [(0, '{"a":1}\n\n{"b":2}\n', '')])
    out = remote.fetch_antithesis(config, moog_config(), 'events', run_id='r-1-1', query='dwarf_measurements')
    assert out['ok'] is True and out['payload'] == [{'a': 1}, {'b': 2}]
    assert seen == ['/bin/moog antithesis events --run-id r-1-1 --q dwarf_measurements']
    fake_ssh(monkeypatch, [(0, '{"a":1}\nnot json\n', '')])
    bad = remote.fetch_antithesis(config, moog_config(), 'logs', run_id='r-1-1')
    assert bad['ok'] is False and bad['payload'] is None and bad['error'].startswith('invalid NDJSON')


def test_fetch_antithesis_rejects_unknown_subcommands(config, monkeypatch):
    fake_ssh(monkeypatch, [])
    with pytest.raises(ValueError):
        remote.fetch_antithesis(config, moog_config(), 'delete', run_id='r')


def test_find_antithesis_run_joins_on_the_description_test_run_id(config, monkeypatch):
    seen = fake_ssh(monkeypatch, [(0, json.dumps(fixture('runs-page-1.json')), ''), (0, json.dumps(fixture('runs-page-2.json')), '')])
    found = remote.find_antithesis_run(config, moog_config(), TEST_RUN_ID)
    assert found['status'] == 'found' and found['matches'] == 1 and found['pages'] == 2
    assert found['run']['run_id'] == fixture('run-completed.json')['run_id']
    assert seen == ['/bin/moog antithesis runs --limit 50', '/bin/moog antithesis runs --limit 50 --cursor cGFnZTI=']


def test_find_antithesis_run_is_ambiguous_when_two_rows_match(config, monkeypatch):
    page = fixture('runs-page-1.json')
    fake_ssh(monkeypatch, [(0, json.dumps(page), '')])
    found = remote.find_antithesis_run(config, moog_config(), '2' * 64)
    assert found['status'] == 'ambiguous' and found['run'] is None and found['matches'] == 2


def test_find_antithesis_run_stops_at_max_pages_and_reports_not_found(config, monkeypatch):
    page = fixture('runs-page-1.json')
    fake_ssh(monkeypatch, [(0, json.dumps(page), '')] * 3)
    found = remote.find_antithesis_run(config, moog_config(), '9' * 64, max_pages=2)
    assert found == {'status': 'not_found', 'run': None, 'matches': 0, 'pages': 2, 'error': ''}


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
