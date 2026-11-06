import json
import socket
import threading
from pathlib import Path

import pytest

from profile_manager import antithesis_workload as workload
from profile_manager import run_service


def _package(tmp_path, *, nodes, revision='a' * 40, level='partial', policy=None):
    package = tmp_path / 'package'
    package.mkdir()
    runtime = {'network_magic': 42, 'nodes': nodes, 'haskell_nodes': [n for n in nodes if n['impl'] == 'cardano-node'],
               'amaru_nodes': [n for n in nodes if n['impl'] == 'amaru'], 'topology': {'edges': []}}
    (package / 'runtime.json').write_text(json.dumps(runtime))
    (package / 'compose-report.json').write_text(json.dumps({'nodes': [{'id': n['id'], 'healthy': None} for n in nodes], 'healthy': None}))
    (package / 'install-report.json').write_text(json.dumps({'satisfied': True}))
    (package / 'scenario.yaml').write_text(json.dumps({'id': 'x'}))
    (package / 'expected-revision').write_text(revision + '\n')
    primitives = [{'phase': 'load', 'primitive': p, 'status': s, 'note': ''} for p, s in (policy or {}).items()]
    (package / 'capabilities.json').write_text(json.dumps({'level': level, 'reason': None, 'primitives': primitives, 'gaps': []}))
    return package


def _listener():
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(5)
    port = server.getsockname()[1]
    stop = threading.Event()

    def accept():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                conn.close()
            except socket.timeout:
                continue
    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    return port, stop, server


def _sdk_lines(path):
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def _fake_bundle(run_dir, *, exit_status='fail', skipped=True):
    run_dir.mkdir(parents=True)
    rows = [{'primitive': 'all_nodes_responsive', 'params': {}, 'evaluated_value': {'ok': True}, 'data_points_used': 2, 'result': 'pass'},
            {'primitive': 'mempool_failure_contained', 'params': {}, 'evaluated_value': 'x' * 900, 'data_points_used': 1, 'result': 'fail'}]
    if skipped:
        rows.append({'primitive': 'unknown_primitive_x', 'params': {}, 'evaluated_value': None, 'data_points_used': 0, 'result': 'skipped', 'note': 'unsupported'})
    (run_dir / 'assertions.json').write_text(json.dumps(rows))
    (run_dir / 'manifest.json').write_text(json.dumps({'exit_status': exit_status, 'run_id': run_dir.name}))


def test_finish_emits_outcomes_and_status(tmp_path):
    run_dir = tmp_path / 'runs' / 'run-1'
    _fake_bundle(run_dir)
    (run_dir / 'measurements').mkdir()
    (run_dir / 'measurements' / 'compact-table.json').write_text(json.dumps({'rows': [{'metric': 'm1', 'status': 'ok'}] * 70}))
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path / 'out')}
    code = workload.finish(run_dir, 'fail', status_dir=tmp_path / 'status', environ=env)
    assert code == 1
    rows = {r['antithesis_assert']['id']: r['antithesis_assert'] for r in _sdk_lines(tmp_path / 'out' / 'sdk.jsonl')}
    assert rows['dwarf_all_nodes_responsive']['assert_type'] == 'always' and rows['dwarf_all_nodes_responsive']['condition'] is True
    assert rows['dwarf_mempool_failure_contained']['condition'] is False
    assert len(rows['dwarf_mempool_failure_contained']['details']['evaluated_value']) == 512
    assert rows['dwarf_skipped_unknown_primitive_x']['assert_type'] == 'reachability'
    assert rows['dwarf_skipped_unknown_primitive_x']['details'] == {'policy': 'unsupported'}
    assert rows['dwarf_scenario_completed']['assert_type'] == 'sometimes' and rows['dwarf_scenario_completed']['condition'] is True
    assert rows['dwarf_scenario_completed']['details'] == {'exit_status': 'fail'}
    assert len(rows['dwarf_measurements_reported']['details']['measurements']) == 64
    assert (tmp_path / 'status' / 'exit').read_text().strip() == '1'
    assert (tmp_path / 'status' / 'done').exists()


@pytest.mark.parametrize('exit_status,code', [('pass', 0), ('fail', 1), ('error', 2), ('precondition_failed', 3), ('weird', 2)])
def test_exit_codes(tmp_path, exit_status, code):
    run_dir = tmp_path / 'runs' / 'r'
    _fake_bundle(run_dir, exit_status=exit_status, skipped=False)
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path / 'out')}
    assert workload.finish(run_dir, exit_status, status_dir=tmp_path / 'status', environ=env) == code
    completed = next(r['antithesis_assert'] for r in _sdk_lines(tmp_path / 'out' / 'sdk.jsonl') if r['antithesis_assert']['id'] == 'dwarf_scenario_completed')
    assert completed['condition'] is (exit_status in ('pass', 'fail'))


def test_wait_ready_waits_for_ports_and_sockets(tmp_path):
    port, stop, server = _listener()
    try:
        sock = tmp_path / 'node.socket'
        nodes = [{'id': 'node1', 'impl': 'cardano-node', 'listen_address': '127.0.0.1:%d' % port, 'socket_path': str(sock)},
                 {'id': 'node2', 'impl': 'amaru', 'listen_address': '127.0.0.1:%d' % port, 'socket_path': None}]
        clock = {'t': 0.0}
        def now():
            return clock['t']
        def sleep(seconds):
            clock['t'] += seconds
            if clock['t'] >= 10 and not sock.exists():
                sock.write_text('')
        ready = workload.wait_ready({'nodes': nodes}, timeout=60, now=now, sleep=sleep)
        assert ready == ['node1', 'node2']
    finally:
        stop.set(); server.close()


def test_wait_ready_times_out_with_unready_ids(tmp_path):
    nodes = [{'id': 'node1', 'impl': 'cardano-node', 'listen_address': '127.0.0.1:1', 'socket_path': str(tmp_path / 'missing')}]
    clock = {'t': 0.0}
    with pytest.raises(RuntimeError, match='antithesis readiness probe timed out: node1'):
        workload.wait_ready({'nodes': nodes}, timeout=5, now=lambda: clock['t'], sleep=lambda s: clock.__setitem__('t', clock['t'] + s))


def test_after_prepare_emits_setup_complete_only_after_ready_then_waits_for_go(tmp_path):
    port, stop, server = _listener()
    try:
        package = _package(tmp_path, nodes=[{'id': 'node1', 'impl': 'amaru', 'listen_address': '127.0.0.1:%d' % port, 'socket_path': None}])
        status = tmp_path / 'status'
        env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path / 'out'), 'DWARF_ANTITHESIS_READY_TIMEOUT': '30'}
        events = []

        class Handle:
            run_dir = tmp_path / 'run'
            def log(self, **payload):
                events.append(payload)
        Handle.run_dir.mkdir()
        polls = {'n': 0}
        def sleep(_seconds):
            polls['n'] += 1
            if polls['n'] == 3:
                assert _sdk_lines(tmp_path / 'out' / 'sdk.jsonl')[-1] == {'antithesis_setup': {'status': 'complete', 'details': {'message': 'ready to go'}}}
                status.mkdir(exist_ok=True); (status / 'go').write_text('go')
        workload.after_prepare(Handle(), package, status_dir=status, environ=env, sleep=sleep)
        lines = _sdk_lines(tmp_path / 'out' / 'sdk.jsonl')
        assert [l for l in lines if 'antithesis_setup' in l] == [{'antithesis_setup': {'status': 'complete', 'details': {'message': 'ready to go'}}}]
        report = json.loads((Handle.run_dir / 'outputs' / 'substrate-compose' / 'compose-report.json').read_text())
        assert report['healthy'] is True and report['nodes'][0]['healthy'] is True
        assert any(e.get('event') == 'antithesis_ready' for e in events)
    finally:
        stop.set(); server.close()


def test_after_prepare_timeout_emits_nothing(tmp_path):
    package = _package(tmp_path, nodes=[{'id': 'node1', 'impl': 'amaru', 'listen_address': '127.0.0.1:1', 'socket_path': None}])
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path / 'out'), 'DWARF_ANTITHESIS_READY_TIMEOUT': '1'}

    class Handle:
        run_dir = tmp_path / 'run'
        def log(self, **payload):
            pass
    Handle.run_dir.mkdir()
    with pytest.raises(RuntimeError, match='timed out'):
        workload.after_prepare(Handle(), package, status_dir=tmp_path / 'status', environ=env, sleep=lambda s: None)
    assert not (tmp_path / 'out' / 'sdk.jsonl').exists()


def test_main_runs_the_scenario_through_run_service_and_finishes(tmp_path, monkeypatch):
    package = _package(tmp_path, nodes=[], policy={'fault_node_freeze': 'delegated-fault', 'load_shell_command': 'runs-with-note'})
    monkeypatch.setattr(workload, 'installed_revision', lambda environ: 'a' * 40)
    seen = {}

    def fake_execute(request):
        seen['request'] = request
        seen['hooks'] = run_service.ANTITHESIS_HOOKS(request)
        run_dir = Path(request.runs_dir) / 'run-7'
        _fake_bundle(run_dir, exit_status='pass', skipped=False)
        return {'run_id': 'run-7', 'run_dir': str(run_dir), 'manifest': {'exit_status': 'pass'}}
    monkeypatch.setattr(run_service, 'execute_run', fake_execute)
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path / 'out')}
    code = workload.main(['--package', str(package), '--runs-dir', str(tmp_path / 'runs'), '--state-dir', str(tmp_path / 'state'),
                          '--status-dir', str(tmp_path / 'status')], environ=env)
    assert code == 0
    request = seen['request']
    assert request.backend == 'antithesis' and request.origin == 'antithesis'
    assert Path(request.scenario_path) == package / 'scenario.yaml'
    assert seen['hooks']['primitive_policy'] == {'fault_node_freeze': 'delegated-fault', 'load_shell_command': 'runs-with-note'}
    assert callable(seen['hooks']['after_prepare'])
    assert (tmp_path / 'status' / 'exit').read_text().strip() == '0'
    ids = [r['antithesis_assert']['id'] for r in _sdk_lines(tmp_path / 'out' / 'sdk.jsonl')]
    assert 'dwarf_scenario_completed' in ids


def test_main_refuses_revision_mismatch_without_setup_complete(tmp_path, monkeypatch):
    package = _package(tmp_path, nodes=[], revision='b' * 40)
    monkeypatch.setattr(workload, 'installed_revision', lambda environ: 'a' * 40)
    monkeypatch.setattr(run_service, 'execute_run', lambda request: pytest.fail('must not run'))
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path / 'out')}
    code = workload.main(['--package', str(package), '--runs-dir', str(tmp_path / 'runs'), '--state-dir', str(tmp_path / 'state'),
                          '--status-dir', str(tmp_path / 'status')], environ=env)
    assert code == 4
    assert (tmp_path / 'status' / 'exit').read_text().strip() == '4' and (tmp_path / 'status' / 'done').exists()
    assert not (tmp_path / 'out' / 'sdk.jsonl').exists()


def test_main_reports_run_service_failure_as_error(tmp_path, monkeypatch):
    package = _package(tmp_path, nodes=[])
    monkeypatch.setattr(workload, 'installed_revision', lambda environ: 'a' * 40)
    def boom(request):
        raise run_service.RunServiceError('scenario does not exist')
    monkeypatch.setattr(run_service, 'execute_run', boom)
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path / 'out')}
    code = workload.main(['--package', str(package), '--runs-dir', str(tmp_path / 'runs'), '--state-dir', str(tmp_path / 'state'),
                          '--status-dir', str(tmp_path / 'status')], environ=env)
    assert code == 2
    completed = next(r['antithesis_assert'] for r in _sdk_lines(tmp_path / 'out' / 'sdk.jsonl') if r['antithesis_assert']['id'] == 'dwarf_scenario_completed')
    assert completed['condition'] is False and 'scenario does not exist' in completed['details']['error']


def test_cli_wires_the_workload_subcommand(monkeypatch):
    from profile_manager import cli
    seen = {}
    monkeypatch.setattr(workload, 'main', lambda argv, environ=None: (seen.setdefault('argv', argv), 7)[1])
    assert cli.main(['antithesis', 'workload', '--package', '/p']) == 7
    assert seen['argv'] == ['--package', '/p']
