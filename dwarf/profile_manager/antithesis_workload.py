"""The ``dwarf-workload`` container entrypoint (BC1: lifecycle inside the package).

One process runs the whole scenario through ``run_service.execute_run`` with
``backend='antithesis'``. Its ``after_prepare`` hook waits until every mapped
node answers, emits ``setup_complete``, then blocks on ``/status/go`` written by
the Test Composer singleton driver. When the scenario ends, every assertion
becomes a fallback-SDK event and ``/status/exit`` + ``/status/done`` release the
driver with the scenario's exit code.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import time
from pathlib import Path

from profile_manager import antithesis_sdk as sdk

DEFAULT_PACKAGE = '/opt/dwarf/package'
DEFAULT_STATUS = '/status'
DEFAULT_RUNS = '/evidence/runs'
DEFAULT_STATE = '/evidence/state'
DEFAULT_READY_TIMEOUT = 900.0
EXIT_CODES = {'pass': 0, 'fail': 1, 'error': 2, 'precondition_failed': 3}
REVISION_MISMATCH = 4
_IDENT = re.compile(r'[^a-z0-9_]')
_VALUE_LIMIT = 512
_MEASUREMENT_LIMIT = 64
_GO_LOG_EVERY = 60.0


def property_id(primitive, prefix='dwarf_'):
    return prefix + _IDENT.sub('_', str(primitive).lower())


def installed_revision(environ=None):
    environ = os.environ if environ is None else environ
    revision = (environ.get('DWARF_SOURCE_REVISION') or '').strip()
    if revision:
        return revision
    identity = Path(__file__).resolve().parents[1] / 'release-identity.json'
    try:
        return str(json.loads(identity.read_text(encoding='utf-8')).get('release_revision') or 'unknown')
    except (OSError, ValueError):
        return 'unknown'


def _tcp_reachable(address, timeout=3.0):
    host, _, port = str(address).rpartition(':')
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _socket_tip(runtime):
    """Real-block probe over the mounted socket, the same query the local
    compose gate uses (``runtime_compose_substrate._wait_for_cardano_node_block``)."""
    from scripts.runtime_multi_node_observation import _query_tip_once
    cardano_cli = (runtime.get('support_binaries') or {}).get('cardano-cli') or 'cardano-cli'
    magic = int(runtime.get('network_magic') or 42)

    def tip(node):
        return _query_tip_once(cardano_cli=cardano_cli, socket_path=Path(node['socket_path']), network_magic=magic)
    return tip


def _tip_or_none(query, node):
    """A hung or garbled tip query means "not ready yet", never an aborted gate."""
    try:
        return query(node)
    except Exception:  # noqa: BLE001 - subprocess timeout, decode error, OSError
        return None


def _has_real_block(tip):
    from scripts.runtime_compose_substrate import _tip_has_real_chain_progress
    return _tip_has_real_chain_progress(tip)


def _amaru_progress(log_path):
    """The same intersect/advance/converge reading of the Amaru log that the
    local compose gate uses (``runtime_compose_substrate._wait_for_amaru_progress``)."""
    from scripts.runtime_compose_substrate import _parse_amaru_runtime_progress
    try:
        text = Path(log_path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        text = ''
    return _parse_amaru_runtime_progress(text)


def wait_ready(runtime, timeout, *, now=time.monotonic, sleep=time.sleep, reachable=_tcp_reachable, poll=5.0, tip=None):
    """Block until every node is ready, with local-run parity: TCP on its listen
    address; a Cardano node also needs its socket and a real block over it; an
    Amaru node also needs its bootstrap sentinel and, through its mounted log,
    to have intersected, advanced and converged on its upstream (a blocking
    signal such as a panic ends the probe). Returns the ready ids in order;
    raises on timeout."""
    nodes = list(runtime.get('nodes') or [])
    deadline = now() + float(timeout)
    query = tip if tip is not None else _socket_tip(runtime)
    while True:
        unready = []
        for node in nodes:
            ok = reachable(node.get('listen_address') or '')
            socket_path = node.get('socket_path')
            if ok and socket_path:
                ok = Path(socket_path).exists() and _has_real_block(_tip_or_none(query, node))
            sentinel = node.get('bootstrap_sentinel')
            if ok and sentinel:
                ok = Path(sentinel).exists()
            log_path = node.get('log_path')
            if ok and sentinel and log_path:
                progress = _amaru_progress(log_path)
                if progress.get('blocking_signals'):
                    raise RuntimeError('antithesis readiness probe: ' + str(node['id']) + ' shows blocking signals: '
                                       + ', '.join(progress['blocking_signals']))
                ok = bool(progress.get('ready'))
            if not ok:
                unready.append(node['id'])
        if not unready:
            return [node['id'] for node in nodes]
        if now() >= deadline:
            raise RuntimeError('antithesis readiness probe timed out: ' + ', '.join(unready))
        sleep(poll)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def after_prepare(handle, package, *, status_dir=DEFAULT_STATUS, environ=None, sleep=time.sleep):
    environ = os.environ if environ is None else environ
    package = Path(package)
    status_dir = Path(status_dir)
    runtime = json.loads((package / 'runtime.json').read_text(encoding='utf-8'))
    timeout = float(environ.get('DWARF_ANTITHESIS_READY_TIMEOUT') or DEFAULT_READY_TIMEOUT)
    ready = wait_ready(runtime, timeout, sleep=sleep)
    # The substrate lifecycle primitives merge this into the package's compose
    # report when they run, so health assertions see the probe's result.
    _write_json(Path(handle.run_dir) / 'outputs' / 'antithesis-readiness.json',
                {'ready': ready, 'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'timeout_seconds': timeout})
    handle.log(phase='framework', primitive='antithesis-workload', level='info', event='antithesis_ready',
               payload={'ready': ready, 'timeout_seconds': timeout})
    sdk.setup_complete(environ)
    go = status_dir / 'go'
    waited = 0.0
    while not go.exists():
        sleep(1.0)
        waited += 1.0
        if waited % _GO_LOG_EVERY == 0:
            handle.log(phase='framework', primitive='antithesis-workload', level='info', event='waiting_for_driver',
                       payload={'waited_seconds': waited})
    handle.log(phase='framework', primitive='antithesis-workload', level='info', event='driver_released', payload={})


def _clip(value):
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return text[:_VALUE_LIMIT]


def _measurement_rows(run_dir):
    path = Path(run_dir) / 'measurements' / 'compact-table.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    rows = value.get('rows') if isinstance(value, dict) else value
    if not isinstance(rows, list):
        return None
    return rows[:_MEASUREMENT_LIMIT]


def _last_phase_error(run_dir):
    """The last ``phase_error`` event of the run log, as SDK-sized details."""
    found = {}
    try:
        lines = (Path(run_dir) / 'log.ndjson').read_text(encoding='utf-8').splitlines()
    except OSError:
        return found
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('event') != 'phase_error':
            continue
        payload = event.get('payload') or {}
        found = {'error': (str(payload.get('type') or 'Error') + ': ' + str(payload.get('message') or ''))[:_VALUE_LIMIT],
                 'failed_primitive': str(event.get('primitive') or ''), 'failed_phase': str(event.get('phase') or '')}
    return found


def _assertion_semantics(primitive):
    """How a DWARF assertion maps onto Antithesis assertions (F174, measured-only Always)."""
    try:
        from profile_manager.antithesis_capabilities import load_matrix
        return (load_matrix()['primitives'].get(primitive) or {}).get('antithesis_assertion') or {}
    except (OSError, ValueError, KeyError):
        return {}


def _measured(value, rules):
    """True when every rule holds on the assertion's evaluated value: the check saw real evidence."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return False
    if not isinstance(value, dict):
        return False
    for rule in rules:
        got = value.get(rule.get('field'))
        if 'equals' in rule and got != rule['equals']:
            return False
        if 'gte_field' in rule:
            floor = value.get(rule['gte_field'])
            if not isinstance(got, (int, float)) or not isinstance(floor, (int, float)) or got < floor:
                return False
    return True


def _n2n_keepalive_answers(host, port):
    """A fresh N2N bearer gets a keepalive response: the target is alive and serving now."""
    from scripts.runtime_exposure_probe import _open_n2n_bearer, _control_keepalive
    try:
        sock, _ = _open_n2n_bearer({'host': host, 'port': int(port), 'pid': None}, duplex=False, timeout=5.0)
    except (OSError, EOFError, RuntimeError):
        return False
    try:
        return bool(_control_keepalive(sock, 1))
    except (OSError, EOFError):
        return False
    finally:
        sock.close()


def _target_answers_within(host, port, *, seconds=60.0, interval=3.0, sleep=None):
    """F189b: a target under a platform kill comes back ~20 s later; one keepalive at finish time
    (run ed09ffb2: killed at vtime 689.1, finish at 689.2) sees nothing. Poll for a grace period."""
    import time as _time
    sleep = sleep or _time.sleep
    waited = 0.0
    while True:
        if _n2n_keepalive_answers(host, port):
            return True
        if waited >= seconds:
            return False
        sleep(interval)
        waited += interval


# F189: how a failed safety assertion re-asks its target at finish time (matrix `recheck`).
RECHECKS = {'n2n-keepalive': _target_answers_within}


def _recheck_target(row, semantics):
    """F189. Run 5801a383: a probe measured during a platform pause of its target reported every
    sub-check false. If the target answers again at finish time it was not wedged by the probe's
    traffic; the window was a transient (pause, kill+restart), so the Always is unmeasured.
    Returns 'target-answers-now' to downgrade, None to keep the violation."""
    check = RECHECKS.get(semantics.get('recheck') or '')
    if check is None:
        return None
    points = row.get('data_points_used') or []
    result = (points[0].get('result') or {}) if points and isinstance(points[0], dict) else {}
    host, port = result.get('target_host'), result.get('target_port')
    if not host or not port:
        return None
    try:
        answers = check(host, port)
    except Exception:  # noqa: BLE001 - a recheck failure must not lose the original verdict
        return None
    return 'target-answers-now' if answers else None


def finish(run_dir, exit_status, *, status_dir=DEFAULT_STATUS, environ=None, error=None):
    """Turn the bundle's assertions into SDK events and release the driver."""
    environ = os.environ if environ is None else environ
    run_dir = Path(run_dir) if run_dir is not None else None
    rows = []
    if run_dir is not None:
        try:
            rows = json.loads((run_dir / 'assertions.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            rows = []
    if rows and exit_status == 'pass' and all(row.get('result') == 'skipped' for row in rows):
        # Nothing was evaluated: a pass here would be vacuous coverage.
        exit_status = 'precondition_failed'
        error = error or 'every assertion was skipped by the capability policy; the run evaluated nothing'
    for row in rows:
        primitive = row.get('primitive') or 'unknown'
        result = row.get('result')
        if result == 'skipped':
            sdk.emit_assert('reachability', property_id(primitive, 'dwarf_skipped_'), True,
                            details={'policy': row.get('note')}, environ=environ)
            continue
        details_row = {'result': result, 'evaluated_value': _clip(row.get('evaluated_value'))}
        semantics = _assertion_semantics(primitive)
        if semantics.get('kind') == 'liveness':
            # Under platform faults a liveness check holds only some of the time (F174).
            sdk.emit_assert('sometimes', property_id(primitive), result == 'pass', details=details_row, environ=environ)
            continue
        if semantics.get('kind') == 'safety':
            measured = _measured(row.get('evaluated_value'), semantics.get('measured_when') or [])
            recheck = _recheck_target(row, semantics) if measured and result != 'pass' else None
            if recheck:
                measured = False
                details_row = dict(details_row, recheck=recheck,
                                   note='the target answers a fresh keepalive now: the failing window was a transient (platform pause or restart), not a violation')
            sdk.emit_assert('sometimes', property_id(primitive) + '_measured', measured, details=details_row, environ=environ)
            if not measured:
                continue
        sdk.emit_assert('always', property_id(primitive), result == 'pass', details=details_row, environ=environ)
    details = {'exit_status': exit_status}
    if error:
        details['error'] = str(error)[:_VALUE_LIMIT]
    elif run_dir is not None and exit_status not in ('pass', 'fail'):
        # Say why in the platform report: the bundle may not be reachable afterwards.
        details.update(_last_phase_error(run_dir))
    sdk.emit_assert('sometimes', 'dwarf_scenario_completed', exit_status in ('pass', 'fail'), details=details, environ=environ)
    measurements = _measurement_rows(run_dir) if run_dir is not None else None
    if measurements is not None:
        sdk.emit_assert('reachability', 'dwarf_measurements_reported', True, details={'measurements': measurements}, environ=environ)
    code = EXIT_CODES.get(exit_status, 2)
    _release(status_dir, code)
    return code


def _release(status_dir, code):
    status_dir = Path(status_dir)
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / 'exit').write_text(str(code) + '\n', encoding='utf-8')
    (status_dir / 'done').write_text('done\n', encoding='utf-8')


def hooks_for(package, *, status_dir, environ):
    package = Path(package)
    try:
        capabilities = json.loads((package / 'capabilities.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        capabilities = {'primitives': []}
    from profile_manager.antithesis_capabilities import skip_policy
    policy = skip_policy(capabilities)

    def hooks(request):
        return {'after_prepare': lambda handle: after_prepare(handle, package, status_dir=status_dir, environ=environ),
                'primitive_policy': policy}
    return hooks


def hold(code, *, environ=None, sleep=time.sleep):
    """Keep the workload container up after the scenario (F172).

    Antithesis reports every container exit as unexpected, and an exit kills the Test Composer
    driver that waits on /status/done. The outcome is already in the SDK events and /status/exit.
    DWARF_ANTITHESIS_EXIT_AFTER_RUN=1 returns instead (local tooling)."""
    environ = os.environ if environ is None else environ
    if environ.get('DWARF_ANTITHESIS_EXIT_AFTER_RUN') == '1':
        return code
    while True:
        sleep(3600)


def _parser():
    parser = argparse.ArgumentParser(prog='cardano-profile antithesis workload')
    parser.add_argument('--package', default=None)
    parser.add_argument('--runs-dir', default=DEFAULT_RUNS)
    parser.add_argument('--state-dir', default=DEFAULT_STATE)
    parser.add_argument('--status-dir', default=DEFAULT_STATUS)
    return parser


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    args = _parser().parse_args(argv)
    package = Path(args.package or environ.get('DWARF_ANTITHESIS_PACKAGE_DIR') or DEFAULT_PACKAGE)
    status_dir = Path(args.status_dir)
    expected = (package / 'expected-revision').read_text(encoding='utf-8').strip() if (package / 'expected-revision').exists() else ''
    installed = installed_revision(environ)
    if not expected or expected != installed:
        _release(status_dir, REVISION_MISMATCH)
        return REVISION_MISMATCH
    from profile_manager import run_service
    run_service.ANTITHESIS_HOOKS = hooks_for(package, status_dir=status_dir, environ=environ)
    request = run_service.RunRequest(scenario_path=package / 'scenario.yaml', origin='antithesis', backend='antithesis',
                                     runs_dir=Path(args.runs_dir), state_dir=Path(args.state_dir))
    try:
        result = run_service.execute_run(request)
    except Exception as exc:  # noqa: BLE001 - the driver must be released with an honest outcome
        return finish(None, 'error', status_dir=status_dir, environ=environ, error=exc)
    run_dir = result.get('run_dir')
    exit_status = str((result.get('manifest') or {}).get('exit_status') or 'error')
    return finish(run_dir, exit_status, status_dir=status_dir, environ=environ)
