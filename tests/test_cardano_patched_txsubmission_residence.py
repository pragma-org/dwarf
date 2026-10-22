"""Exact source contract checks; no compiler or node execution claims."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from profile_manager.measurement_collectors import cardano_patched as patched
from profile_manager.measurement_runtime import CollectorContext

REVISION = patched.CARDANO_SOURCE_REVISION
TAP = 'cardano-patched-txsubmission-residence'
ROOT = Path('dwarf/targets/cardano-node/measurement-patches-nanoseconds-v5') / REVISION
OLD = Path(str(ROOT).replace('nanoseconds-v5', 'nanoseconds-v4'))


def manifest():
    return json.loads((ROOT/'manifest.json').read_text())


def identity():
    return dict(implementation='cardano-node', version='11.1.2', mode='patched',
        source_revision=REVISION, patch_set_sha256=manifest()['patch_set_sha256'],
        toolchain={'ghc':'9.6.7','cabal':'3.16.0.0'},
        image_digest='sha256:'+'1'*64, executable_digest='sha256:'+'2'*64,
        build_result_sha256='sha256:'+'3'*64, runtime_probe_log_sha256='sha256:'+'4'*64)


def event(phase, **changes):
    start,end={'request':(100,120),'body':(200,210),'terminal':(220,250)}[phase]
    row=dict(schema_version='v1', target='cardano-node::measurement',
        event='txsubmission_residence',boundary='inbound-v2-request-body-handling',
        phase=phase,trace_session='c'*32,peer_session=1,peer_id='peer-1',request_id=1,tx_index=1,request_tx_count=1,tx_id='TxId '+ 'a'*64,
        outcome='pending' if phase=='request' else 'body-returned' if phase=='body' else 'body-handled',
        pending_depth=0 if phase!='terminal' else 1,inflight_depth=1 if phase!='terminal' else 0,
        started_monotonic_ns=start,ended_monotonic_ns=end)
    row.update(changes)
    return row


def records():
    return [event('request'),event('body'),event('terminal')]


def collect(tmp_path,rows,selected=None,max_bytes=32*1024*1024):
    path=tmp_path/'trace.ndjson';path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    ctx=CollectorContext(run_dir=tmp_path,collector_dir=tmp_path/'measurements'/TAP,
        measurement_id=TAP,definition={},parameters={})
    c=patched.CardanoPatchedCollector({'id':TAP},json_trace_paths=[path],
        target_identity=selected or identity(),include_existing=True,max_source_bytes=max_bytes)
    c.prepare(ctx);c.start(ctx)
    return c.finalize(ctx)


def test_v5_preserves_v4_and_applies_exact_preimages(tmp_path):
    from scripts import build_cardano_measurement_target as builder
    m=manifest();old=json.loads((OLD/'manifest.json').read_text())
    assert m['measurement_revision']=='nanoseconds-v5'
    assert m['patches'][:6]==old['patches']
    for patch in m['patches'][:6]:
        assert (ROOT/patch['path']).read_bytes()==(OLD/patch['path']).read_bytes()
    assert builder.verify_patch_set(ROOT,m)['patch_set_sha256']==m['patch_set_sha256']
    patch=m['patches'][-1]
    for item in patch['preimages']:
        data=(Path('tests/fixtures/cardano-txsubmission-v5')/item['path']).read_bytes()
        assert hashlib.sha256(data).hexdigest()==item['sha256']
        path=tmp_path/item['path'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    builder.verify_preimages(tmp_path,patch['preimages'])
    subprocess.run(['git','apply','--check','--whitespace=error-all',str((ROOT/patch['path']).resolve())],cwd=tmp_path,check=True)
    subprocess.run(['git','apply','--whitespace=error-all',str((ROOT/patch['path']).resolve())],cwd=tmp_path,check=True)
    assert not (ROOT/'build-evidence.json').exists()


def test_continuous_residence_bounds_and_depth(tmp_path):
    result=collect(tmp_path,records())
    m=result['measurements']
    assert m['request_to_terminal_lower_bound']['mean']==.1
    assert m['request_to_terminal_upper_bound']['mean']==.15
    assert m['request_to_body_lower_bound']['mean']==.08
    assert m['body_to_terminal_upper_bound']['mean']==.05
    assert m['pending_body_depth']['maximum']==1
    assert m['inflight_body_depth']['maximum']==1
    assert 'mempool_residence' not in m
    assert result['exercise']['status']=='pending-control-evidence'
    assert result['exercise']['measurement_complete'] is False
    assert result['exercise']['campaign_adapter']=='implemented-joined-projection'
    joined=json.loads((tmp_path/result['artifacts']['txsubmission_correlations']).read_text())
    assert joined['transactions'][0]['request_bracket_ns']==[100,120]


@pytest.mark.parametrize('field,value',[('trace_session','d'*32),('peer_session',2),('request_id',2),('tx_id','b'*64)])
def test_distinct_identity_never_joins(tmp_path,field,value):
    rows=records();rows[1][field]=value
    result=collect(tmp_path,rows)
    assert result['measurements']['request_to_terminal_upper_bound']['sample_count']==0
    assert result['exercise']['status']=='incomplete'


@pytest.mark.parametrize('changes',[{'peer_session':True},{'request_id':0},{'request_id':2**64},
    {'tx_id':'x'*257},{'tx_id':'bad\n'},{'phase':[]},{'pending_depth':65537},
    {'inflight_depth':-1},{'started_monotonic_ns':True},{'ended_monotonic_ns':2**64},
    {'outcome':'accepted'},{'boundary':'mempool'},{'extra':'field'}])
def test_malformed_records_fail_closed(tmp_path,changes):
    rows=records();rows[2].update(changes)
    result=collect(tmp_path,rows)
    assert result['export']['rejected_record_count']==1
    assert result['exercise']['status']=='incomplete'
    assert result['measurements']['request_to_terminal_upper_bound']['sample_count']==0


def test_duplicates_conflicts_missing_misordering_and_omissions(tmp_path):
    result=collect(tmp_path,records()+records())
    assert result['exercise']['duplicate_event_count']==3
    assert result['measurements']['request_to_terminal_upper_bound']['sample_count']==1
    for rows in [[],records()[:2],records()+[event('terminal',ended_monotonic_ns=251)],
                 [event('request'),event('body',started_monotonic_ns=90),event('terminal')]]:
        result=collect(tmp_path,rows)
        assert result['exercise']['status']=='incomplete'
        assert result['measurements']['request_to_terminal_upper_bound']['sample_count']==0
    result=collect(tmp_path,[event('request'),event('body',outcome='body-omitted'),event('terminal',outcome='body-omitted')])
    assert result['exercise']['terminal_outcomes']=={'body-omitted':1}
    assert result['exercise']['status']=='pending-control-evidence'


def test_exact_identity_and_no_v4_promotion(tmp_path):
    selected=identity()
    assert patched.supports_cardano_txsubmission_residence(selected)
    assert TAP in patched.build_cardano_patched_factories(json_trace_paths=[],target_identity=selected)
    for field,value in [('patch_set_sha256',patched.CARDANO_BLOCKFETCH_PATCH_SHA256),
                        ('source_revision','0'*40),('toolchain',{}),('mode','stock'),('version','10.7.1')]:
        bad={**selected,field:value}
        assert not patched.supports_cardano_txsubmission_residence(bad)
        assert TAP not in patched.build_cardano_patched_factories(json_trace_paths=[],target_identity=bad)
    for field in ['image_digest','executable_digest','build_result_sha256','runtime_probe_log_sha256']:
        bad=dict(selected);del bad[field]
        with pytest.raises(ValueError,match='immutable'):
            collect(tmp_path,records(),bad)


def test_unavailable_truncation_and_cross_phase_outcome_conflict(tmp_path):
    fallback={key:value for key,value in event('request').items() if key in {'schema_version','target','event','boundary'}}
    result=collect(tmp_path,records()+[{**fallback,'phase':'unavailable'}])
    assert result['exercise']['status']=='incomplete'
    result=collect(tmp_path,records(),max_bytes=20)
    assert result['exercise']['status']=='incomplete'
    result=collect(tmp_path,[event('request'),event('body'),event('terminal',outcome='body-omitted')])
    assert result['exercise']['status']=='incomplete'


def test_definition_suite_keeps_runtime_gates_and_rejects_unreconciled_campaign_records():
    definition=yaml.safe_load(Path(f'dwarf/measurements/{TAP}.yaml').read_text())
    assert 'bounds' in definition['description']
    assert definition['required_capabilities']==['cardano-patched-txsubmission-residence','cardano-patch-identity']
    suite=yaml.safe_load(Path('dwarf/qualification-suites/node-measurements-v1.yaml').read_text())
    row=next(r for r in suite['exercises'] if r['measurement_id']==TAP)
    assert row['runtime']['status']=='implemented'
    assert row['fallback']['status']=='blocked'
    assert row['rule']['required_event']=='txsubmission_residence'
    assert 'outcomes' not in row['rule']
    from profile_manager.data.measurement_coverage import _source
    assert _source(definition)=='Patched'
    preset=yaml.safe_load(Path('dwarf/measurement-profiles/cardano-security-patched.yaml').read_text())
    assert not any(r['id']==TAP and r['enabled'] for r in preset['measurements'])
    from profile_manager.campaign_runtime_evidence import _normalized_signals
    assert _normalized_signals(TAP,records(),{'status':'collected','sample_count':1,'records':[]})[0]==[]


def test_multi_transaction_batch_requires_full_cardinality_and_stable_snapshots(tmp_path):
    rows=[event(phase,tx_index=index,request_tx_count=2,inflight_depth=0 if phase=='terminal' else 2,tx_id='a'*64 if index==1 else 'b'*64)
          for phase in ('request','body','terminal') for index in (1,2)]
    result=collect(tmp_path,rows)
    assert result['exercise']['coherent_transaction_count']==2
    assert result['measurements']['request_to_terminal_upper_bound']['sample_count']==2
    assert result['measurements']['pending_body_depth']['sample_count']==3
    for bad in [rows[:-1], [r for r in rows if r['tx_index']==1],
                [{**r,'tx_id':'a'*64} for r in rows],
                [{**r,'pending_depth':2} if r['tx_index']==2 else r for r in rows]]:
        result=collect(tmp_path,bad)
        assert result['exercise']['status']=='incomplete'
        assert result['measurements']['request_to_terminal_upper_bound']['sample_count']==0
    result=collect(tmp_path,list(reversed(rows)))
    assert result['exercise']['coherent_transaction_count']==2


def test_peer_session_alias_and_huge_missing_cardinality_fail_closed(tmp_path):
    result=collect(tmp_path,records()+[event('request',peer_id='other-peer',request_id=2)])
    assert result['exercise']['status']=='incomplete'
    assert result['measurements']['request_to_terminal_upper_bound']['sample_count']==0
    for change in ({'request_tx_count':65536},{'request_tx_count':2**64},
                   {'tx_index':True},{'request_tx_count':True},{'peer_id':'x'*257}):
        result=collect(tmp_path,[{**r,**change} for r in records()])
        assert result['exercise']['status']=='incomplete'


def test_native_actions_and_protocol_errors_remain_outside_telemetry_guards(tmp_path):
    m=manifest();patch=m['patches'][-1]
    for item in patch['preimages']:
        path=tmp_path/item['path'];path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((Path('tests/fixtures/cardano-txsubmission-v5')/item['path']).read_bytes())
    subprocess.run(['git','apply',str((ROOT/patch['path']).resolve())],cwd=tmp_path,check=True)
    source=(tmp_path/'lib/Ouroboros/Network/TxSubmission/Inbound/V2.hs').read_text()
    import re
    assert not re.search(r'\b(catch|try)\b',source)
    request=source.split('requestTxBodies n txKeys =',1)[1].split('-- Continue processing',1)[0]
    assert 'if dwarfBlockFetchEnabled\n        then requestCount `seq` pure ()\n        else pure ()' in request
    assert request.index('txsToRequest <- resolveTxRequest') < request.index('requestCount = Map.size txsToRequest') < request.index('requestCount `seq` pure ()') < request.index('requestEnded <- dwarfQueueStamp')
    body=source.split('CollectTxs requested txs -> do',1)[1]
    assert body.index('throwIO ProtocolErrorTxNotRequested') < body.index('emit "body"')
    assert body.index('throwIO protocolError') < body.index('emit "body"')
    assert "applyReceivedTxs now [ (txId tx, tx) | tx <- txs ] peerStateDwarf\n        handlingEnded <- dwarfQueueStamp" in body
    helper=(tmp_path/'api/lib/Ouroboros/Network/DwarfMeasurement.hs').read_text()
    sink=helper.split('emitDwarfTxSubmission phase',1)[1]
    assert sink.index('emitted <- tryHeaderInstrumentation') < sink.index('let boundedTxId') < sink.index('LBS.toStrict $ encode')
    assert 'SomeAsyncException' in helper and 'Just _ -> throwIO failure' in helper
    assert 'peerDwarfBodyRequests peerState StrictSeq.|> serial' in source
    registry=(tmp_path/'lib/Ouroboros/Network/TxSubmission/Inbound/V2/Registry.hs').read_text()
    assert registry.count('(_, peers) <- readTVar registry')==2
    assert 'next == 0 || next == maxBound = 0' in registry




def test_depths_must_match_native_outstanding_batch_lifecycle(tmp_path):
    for rows in [[event('request',inflight_depth=0),event('body'),event('terminal')],
                 [event('request'),event('body',inflight_depth=0),event('terminal')],
                 [event('request'),event('body'),event('terminal',inflight_depth=1)]]:
        result=collect(tmp_path,rows)
        assert result['exercise']['status']=='incomplete'
        assert result['measurements']['inflight_body_depth']['sample_count']==0


def test_real_collector_campaign_join_requires_reconciled_metrics(tmp_path):
    from profile_manager.campaign_runtime_evidence import _normalized_signals
    result=collect(tmp_path,records())
    rows=[json.loads(line) for line in (tmp_path/result['artifacts']['normalized']).read_text().splitlines()]
    metrics=result['measurements']
    summary={'status':'collected','sample_count':max(r['sample_count'] for r in metrics.values()),
             'records':[{'name':name,**metric} for name,metric in metrics.items()]}
    samples,native=_normalized_signals(TAP,rows,summary,collector=result)
    assert len(samples)==3
    assert [sample["value"] for sample in samples]==[1,1,0]
    assert all(sample["metric"]=="inflight_body_depth" for sample in samples)
    assert native==rows
    summary["sample_count"]+=1
    assert _normalized_signals(TAP,rows,summary,collector=result)[0]==[]
    assert patched._measurements(TAP,rows)=={}
