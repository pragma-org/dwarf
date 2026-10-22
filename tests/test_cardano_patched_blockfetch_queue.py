"""Offline contract checks only; these do not establish compile/runtime evidence."""
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from profile_manager.measurement_collectors import cardano_patched as patched
from profile_manager.measurement_runtime import CollectorContext
from profile_manager.measurement_targets import MeasurementTargetError, resolve_patched_cardano_target
from scripts import build_cardano_measurement_target as builder

REVISION = patched.CARDANO_SOURCE_REVISION
TAP = 'cardano-patched-blockfetch-handler-queue'
ROOT = Path('dwarf/targets/cardano-node/measurement-patches-nanoseconds-v4') / REVISION
OLD = Path(str(ROOT).replace('nanoseconds-v4', 'nanoseconds-v3'))


def manifest():
    return json.loads((ROOT / 'manifest.json').read_text())


def identity(**changes):
    return dict(implementation='cardano-node', version='11.1.2', mode='patched',
                source_revision=REVISION, patch_set_sha256=manifest()['patch_set_sha256'],
                toolchain={'ghc': '9.6.7', 'cabal': '3.16.0.0'},
                image_digest='sha256:' + '1'*64, executable_digest='sha256:' + '2'*64,
                build_result_sha256='sha256:' + '3'*64,
                runtime_probe_log_sha256='sha256:' + '4'*64, **changes)


def event(phase, **changes):
    times = {'enqueue': (100, 120), 'dequeue': (80, 150), 'service': (160, 220)}
    start, end = times[phase]
    row = dict(schema_version='v1', target='cardano-node::measurement',
                event='blockfetch_handler_queue', boundary='client-merged-request', phase=phase,
                trace_session='c'*32, client_id=1, request_id=1, update_index=1,
                queue_depth=0 if phase != 'enqueue' else 2,
                started_monotonic_ns=start, ended_monotonic_ns=end,
                block_hash='a'*64 if phase == 'service' else '')
    row.update(changes)
    return row


def records():
    return [event('enqueue'), event('dequeue'), event('service')]


def collect(tmp_path, rows, selected=None, max_bytes=32*1024*1024):
    trace = tmp_path / 'trace.ndjson'
    trace.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    ctx = CollectorContext(run_dir=tmp_path, collector_dir=tmp_path/'measurements'/TAP,
                           measurement_id=TAP, definition={}, parameters={})
    c = patched.CardanoPatchedCollector({'id': TAP}, json_trace_paths=[trace],
            target_identity=selected or identity(), include_existing=True, max_source_bytes=max_bytes)
    c.prepare(ctx)
    c.start(ctx)
    return c.finalize(ctx)


def test_manifest_inherits_v3_and_patches_exact_retained_preimages(tmp_path):
    m = manifest()
    old = json.loads((OLD/'manifest.json').read_text())
    assert m['measurement_revision'] == 'nanoseconds-v4'
    assert m['patches'][:5] == old['patches']
    assert builder.verify_patch_set(ROOT, m)['patch_set_sha256'] == m['patch_set_sha256']
    patch = m['patches'][-1]
    for preimage in patch['preimages']:
        fixture = Path('tests/fixtures/cardano-blockfetch-v4') / preimage['path']
        data = fixture.read_bytes()
        assert hashlib.sha256(data).hexdigest() == preimage['sha256']
        path = tmp_path / preimage['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    builder.verify_preimages(tmp_path, patch['preimages'])
    subprocess.run(['git', 'apply', '--check', '--whitespace=error-all', str((ROOT/patch['path']).resolve())], cwd=tmp_path, check=True)
    subprocess.run(['git', 'apply', '--whitespace=error-all', str((ROOT/patch['path']).resolve())], cwd=tmp_path, check=True)
    helper = (tmp_path/'api/lib/Ouroboros/Network/DwarfMeasurement.hs').read_text()
    assert 'SomeAsyncException' in helper
    assert 'dwarfQueueStamp' in helper
    assert 'native <- action' in helper
    assert helper.index('native <- action') < helper.index('ended <- dwarfQueueStamp')
    state = (tmp_path/'lib/Ouroboros/Network/BlockFetch/ClientState.hs').read_text()
    assert 'fetchClientDwarfQueueVar' in state
    assert 'unsafePerformIO' not in state
    assert 'takeTFetchRequestVar fetchClientRequestVar' in state
    assert not (ROOT/'build-evidence.json').exists()


def test_exact_pair_only_and_no_build_registry(tmp_path):
    assert patched.supports_cardano_blockfetch_queue(identity())
    assert TAP in patched.build_cardano_patched_factories(json_trace_paths=[], target_identity=identity())
    for field, value in [('patch_set_sha256', patched.CARDANO_HEADER_PATCH_SHA256),
                         ('source_revision', '0'*40), ('mode','stock'), ('toolchain',{})]:
        selected = identity(); selected[field] = value
        assert not patched.supports_cardano_blockfetch_queue(selected)
        assert TAP not in patched.build_cardano_patched_factories(json_trace_paths=[], target_identity=selected)
    profile = SimpleNamespace(cardano_version='11.1.2', measurement_patch_revision=REVISION,
                              measurement_patch_set_sha256=manifest()['patch_set_sha256'])
    with pytest.raises(MeasurementTargetError, match='is not built'):
        resolve_patched_cardano_target(profile, registry_root=tmp_path)


def test_residence_bounds_service_depth_and_raw_correlation(tmp_path):
    result = collect(tmp_path, records())
    metrics = result['measurements']
    assert metrics['queue_residence_lower_bound']['mean'] == 0
    assert metrics['queue_residence_upper_bound']['mean'] == .05
    assert metrics['handler_service']['mean'] == .06
    assert metrics['pending_range_depth']['maximum'] == 2
    assert 'queue_residence' not in metrics
    assert result['exercise']['status'] == 'pending-control-evidence'
    assert result['exercise']['measurement_complete'] is False
    correlation = json.loads((tmp_path/result['artifacts']['queue_correlations']).read_text())
    assert correlation['requests'][0]['block_hashes'] == ['a'*64]
    assert correlation['requests'][0]['enqueue_brackets_ns'] == [[100,120]]
    assert correlation['requests'][0]['dequeue_bracket_ns'] == [80,150]


def test_merged_queue_uses_first_enqueue_and_requires_all_updates(tmp_path):
    rows = [event('enqueue'), event('enqueue', update_index=2, started_monotonic_ns=125,
              ended_monotonic_ns=130, queue_depth=3), event('dequeue', update_index=2),
            event('service', update_index=2)]
    result = collect(tmp_path, rows)
    assert result['measurements']['queue_residence_upper_bound']['sample_count'] == 1
    assert result['measurements']['pending_range_depth']['sample_count'] == 3
    assert result['exercise']['merged_request_count'] == 1
    result = collect(tmp_path, rows[1:])
    assert result['exercise']['status'] == 'incomplete'
    assert result['measurements']['queue_residence_upper_bound']['sample_count'] == 0


@pytest.mark.parametrize('rows', [[], [event('enqueue')], [event('dequeue')],
    [event('service')], [event('enqueue'),event('dequeue')],
    [event('enqueue'),event('dequeue', ended_monotonic_ns=90),event('service')],
    [event('enqueue'),event('dequeue'),event('service', started_monotonic_ns=140)]])
def test_no_unpaired_or_misordered_evidence_counts_as_exercise(tmp_path, rows):
    result = collect(tmp_path, rows)
    assert result['exercise']['status'] == 'incomplete'
    assert result['export']['incomplete'] is True
    assert result['exercise']['measurement_complete'] is False


@pytest.mark.parametrize('changes', [{'client_id': True}, {'request_id':0}, {'update_index':2**64},
    {'phase':[]}, {'queue_depth':-1}, {'queue_depth':65537}, {'started_monotonic_ns':True},
    {'ended_monotonic_ns':2**64}, {'block_hash':'bad'}, {'boundary':'other'}, {'schema_version':'v2'}])
def test_malformed_records_cannot_supply_queue_samples(tmp_path, changes):
    row=event('service'); row.update(changes)
    result = collect(tmp_path, [event('enqueue'),event('dequeue'),row])
    assert result['export']['rejected_record_count'] == 1
    assert result['measurements']['handler_service']['sample_count'] == 0
    assert result['exercise']['status'] == 'incomplete'


def test_duplicates_do_not_inflate_and_conflicts_poison_request(tmp_path):
    result = collect(tmp_path, records()+records())
    assert result['exercise']['duplicate_event_count'] == 3
    assert result['measurements']['handler_service']['sample_count'] == 1
    result = collect(tmp_path, records()+[event('service', ended_monotonic_ns=221)])
    assert result['exercise']['conflicting_event_count'] == 1
    assert result['measurements']['handler_service']['sample_count'] == 0
    assert result['exercise']['status'] == 'incomplete'


def test_unavailable_and_truncation_stay_incomplete(tmp_path):
    unavailable = dict(schema_version='v1',target='cardano-node::measurement',
        event='blockfetch_handler_queue',boundary='client-merged-request',phase='unavailable')
    result=collect(tmp_path, records()+[unavailable])
    assert result['exercise']['unavailable_event_count'] == 1
    assert result['exercise']['status'] == 'incomplete'
    result=collect(tmp_path, records(), max_bytes=20)
    assert result['export']['incomplete'] is True
    assert result['exercise']['status'] == 'incomplete'


@pytest.mark.parametrize('field',['image_digest','executable_digest','build_result_sha256','runtime_probe_log_sha256'])
def test_immutable_provenance_required(tmp_path,field):
    selected=identity(); del selected[field]
    with pytest.raises(ValueError, match='immutable'):
        collect(tmp_path,records(),selected)


def test_definition_catalog_no_preset_or_fallback_promotion():
    definition=yaml.safe_load(Path(f'dwarf/measurements/{TAP}.yaml').read_text())
    assert definition['required_capabilities']==['cardano-patched-blockfetch-queue','cardano-patch-identity']
    assert 'bounds' in definition['description']
    suite=yaml.safe_load(Path('dwarf/qualification-suites/node-measurements-v1.yaml').read_text())
    row=next(r for r in suite['exercises'] if r['measurement_id']==TAP)
    assert row['runtime']['status']=='implemented'
    assert 'compile' in row['runtime']['reason']
    assert row['fallback']['status']=='blocked'
    preset=yaml.safe_load(Path('dwarf/measurement-profiles/cardano-security-patched.yaml').read_text())
    assert not any(r['id']==TAP and r['enabled'] for r in preset['measurements'])




def test_positive_lower_bound_and_distinct_clients_sessions_do_not_join(tmp_path):
    result=collect(tmp_path,[event('enqueue'),event('dequeue',started_monotonic_ns=140),event('service')])
    assert result['measurements']['queue_residence_lower_bound']['mean']==.02
    for field,value in [('client_id',2),('trace_session','d'*32),('request_id',2)]:
        row=event('dequeue');row[field]=value
        result=collect(tmp_path,[event('enqueue'),row,event('service')])
        assert result['measurements']['queue_residence_upper_bound']['sample_count']==0
        assert result['exercise']['status']=='incomplete'


def test_missing_merge_huge_update_counter_does_not_allocate_unbounded_range(tmp_path):
    result=collect(tmp_path,[event('enqueue'),event('dequeue',update_index=2**64-1),event('service')])
    assert result['exercise']['status']=='incomplete'
    assert result['measurements']['queue_residence_upper_bound']['sample_count']==0


def test_source_contains_cancellation_guard_before_fallback_and_native_action_outside_it():
    patch=(ROOT/manifest()['patches'][-1]['path']).read_text()
    added='\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
    clock=added.split('dwarfQueueStamp =',1)[1].split('-- | Only bounded',1)[0]
    assert 'fromException failure :: Maybe SomeAsyncException' in clock
    assert 'Just _ -> MC.throwIO (failure :: SomeException)' in clock
    assert 'Nothing -> pure Nothing' in clock
    emit=added.split('emitDwarfBlockFetch phase',1)[1].split('measureDwarfFetchedBlock ::',1)[0]
    for expr in ['take 65 blockHash','LBS.toStrict $ encode','withMVar lock']:
        assert expr in emit
    assert emit.index('emitted <- tryHeaderInstrumentation') < emit.index('LBS.toStrict $ encode')
    service=added.split('measureDwarfFetchedBlock clientId',1)[1]
    assert 'native <- action\n  ended <- dwarfQueueStamp' in service
    assert 'MC.catch' not in service and 'evaluate native' not in service


def test_v4_helper_preimage_is_exact_v3_output(tmp_path):
    source='api/lib/Ouroboros/Network/DwarfMeasurement.hs'
    path=tmp_path/source;path.parent.mkdir(parents=True)
    path.write_bytes(Path('tests/fixtures/cardano-network-v2-DwarfMeasurement.hs').read_bytes())
    old=json.loads((OLD/'manifest.json').read_text())
    subprocess.run(['git','apply',str((OLD/old['patches'][3]['path']).resolve())],cwd=tmp_path,check=True)
    assert path.read_bytes()==(Path('tests/fixtures/cardano-blockfetch-v4')/source).read_bytes()
