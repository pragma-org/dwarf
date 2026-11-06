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
