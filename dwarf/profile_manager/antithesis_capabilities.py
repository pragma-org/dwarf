"""Classify a scenario's primitives against the committed Antithesis capability matrix.

The matrix (``data/antithesis-capabilities-v1.json``) says, per primitive, whether it
runs inside the ``dwarf-workload`` container, runs with a documented difference, is
delegated to the package or the platform, or is unsupported there. Anything not
listed is unsupported. The classification is computed at preview time (declared
gaps in the package manifest) and consumed again at run time (skip policy).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

STATUSES = ('runs', 'runs-with-note', 'delegated', 'delegated-fault', 'unsupported')
LEVELS = ('full', 'partial', 'none')
MATRIX_PATH = Path(__file__).resolve().parent / 'data' / 'antithesis-capabilities-v1.json'
_PHASES = ('setup', 'load', 'faults', 'probes', 'assertions', 'teardown')
_RUNNABLE = {'runs', 'runs-with-note'}
_GAP_CODES = {'unsupported': 'primitive-unsupported-on-antithesis',
              'delegated-fault': 'fault-delegated-to-platform'}
_SKIPPED = frozenset(_GAP_CODES)
_NO_SUT_RUNTIMES = {'library', 'single-node'}


@lru_cache(maxsize=1)
def _matrix_text():
    return MATRIX_PATH.read_text(encoding='utf-8')


def load_matrix():
    matrix = json.loads(_matrix_text())
    if matrix.get('schema_version') != 1 or matrix.get('default') not in STATUSES:
        raise ValueError('unsupported Antithesis capability matrix')
    for name, row in matrix['primitives'].items():
        if row.get('status') not in STATUSES:
            raise ValueError('unknown capability status for ' + name)
        producer = row.get('evidence_producer')
        if producer is not None and producer not in matrix['primitives']:
            raise ValueError('unknown evidence producer for ' + name)
    return matrix


def _steps(scenario):
    """Yield (phase label, primitive name) in execution order, phases flattened."""
    if scenario.phases:
        for phase in scenario.phases:
            for name in _PHASES:
                for ref in getattr(phase, name):
                    yield phase.id + ':' + name, name, ref.primitive, dict(ref.params or {})
        return
    for name in _PHASES:
        for ref in getattr(scenario, name):
            yield name, name, ref.primitive, dict(ref.params or {})


def _entry(matrix, primitive):
    return matrix['primitives'].get(primitive) or {'status': matrix['default'], 'note': 'Not listed in the capability matrix.'}


def classify(scenario):
    matrix = load_matrix()
    steps = list(_steps(scenario))
    # An assertion that only reads a proof written by another primitive follows
    # that producer: if the scenario's producer is skipped, the assertion is
    # skipped with a gap naming it, instead of failing for a file the package
    # can never write (AT-2b). A producer absent from the scenario is a scenario
    # defect, not a package gap, so the assertion keeps its own status.
    present = {primitive for _, _, primitive, _ in steps}
    skipped_producers = {primitive for _, _, primitive, _ in steps if _entry(matrix, primitive)['status'] in _SKIPPED}
    rows, gaps, runnable_load, degraded = [], [], False, False
    for label, phase, primitive, params in steps:
        entry = _entry(matrix, primitive)
        status, note, code = entry['status'], entry.get('note', ''), _GAP_CODES.get(entry['status'])
        producer = entry.get('evidence_producer')
        if producer in present and producer in skipped_producers and status in _RUNNABLE:
            status = 'unsupported'
            note = 'Its evidence producer ' + producer + ' is skipped inside the package; without that proof the assertion can only fail.'
            code = 'assertion-evidence-unsupported'
        rows.append({'phase': label, 'primitive': primitive, 'status': status, 'note': note})
        if phase == 'load' and status in _RUNNABLE:
            runnable_load = True
        if code:
            gaps.append({'kind': 'implementation-gap', 'code': code, 'field': 'scenario_id', 'primitive': primitive, 'phase': label,
                         'message': primitive + ': ' + (note or status)})
        # A primitive that runs can still be asked for parts that need process access the package lacks
        # (F168): it runs, reports those parts unavailable, and the scenario is partial with a named gap.
        unavailable = sorted(value for key, values in (entry.get('unavailable_params') or {}).items()
                             for value in values if value in (params.get(key) or []))
        if unavailable and status in _RUNNABLE:
            degraded = True
            gaps.append({'kind': 'implementation-gap', 'code': 'observation-unavailable-in-package', 'field': 'scenario_id',
                         'primitive': primitive, 'phase': label,
                         'message': primitive + ': ' + ', '.join(unavailable) + ' need process access; inside the package they are reported unavailable.'})
    reason = None
    if scenario.runtime in _NO_SUT_RUNTIMES:
        reason = 'no-faultable-sut'
    elif scenario.runtime == 'devnet':
        # The devnet itself can rule the scenario out before any primitive does:
        # a public network (no internet on the platform) or no target node.
        from profile_manager.antithesis_generated_rig import scenario_devnet
        codes = {issue['code'] for issue in scenario_devnet(scenario)['issues']}
        if 'public-network' in codes:
            reason = 'public-network'
        elif 'no-faultable-sut' in codes:
            reason = 'no-faultable-sut'
    if reason is None and not runnable_load:
        reason = 'no-runnable-load'
    assertions = [row for row in rows if row['phase'].endswith('assertions')]
    if reason is None and assertions and all(row['status'] in _SKIPPED for row in assertions):
        # Every verdict the scenario could produce is skipped: a run would
        # evaluate nothing, so there is nothing to submit (vacuous coverage).
        reason = 'no-evaluable-assertion'
    if reason:
        level = 'none'
    elif degraded or any(row['status'] == 'unsupported' for row in rows):
        level = 'partial'
    else:
        level = 'full'
    return {'schema_version': 1, 'matrix': 'antithesis-capabilities-v1', 'level': level, 'reason': reason,
            'runnable_load': runnable_load, 'primitives': rows, 'gaps': gaps}


def skip_policy(classification):
    """Primitive name -> status for every entry that does not simply run."""
    return {row['primitive']: row['status'] for row in classification['primitives'] if row['status'] != 'runs'}
