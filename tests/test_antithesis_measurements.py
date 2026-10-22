import json
from pathlib import Path
import pytest
import yaml
from test_antithesis_package import request


def test_mixed_taps_have_target_specific_trace_sources_and_collectors():
    from profile_manager.antithesis_package import preview_package
    value=request(scenario_id='cardano-amaru-miniprotocol-security-local',
        measurements={'profile_id':None,'tap_ids':['amaru-stock-network','cardano-stock-network']})
    value['versions']['policy']='latest-stable'
    result=preview_package(value)
    binding=result['measurement_binding']
    assert binding['status']=='applied'
    compose=yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    assert compose['services']['sm-amaru-victim']['environment']['AMARU_WITH_JSON_TRACES']=='true'
    assert 'dwarf-measurements' in compose['services']
    assert all('docker.sock' not in str(s) for s in compose['services'].values())
    assert not any(i['code']=='collector-not-implemented' for i in result['issues'])
    assert {r['service'] for r in binding['targets']}=={'sm-cardano-victim','sm-amaru-victim'}


def test_remote_runtime_uses_real_existing_collector(tmp_path):
    from scripts.antithesis_measurement_runtime import collect
    from profile_manager.data.catalog_definitions import load_definition
    tap='amaru-stock-network'
    definition=load_definition('measurements',tap).data
    trace=Path(__file__).parent/'fixtures/amaru-b159-stock-traces.ndjson'
    target={'service':'sm-amaru-victim','identity':{'implementation':'amaru','version':'test',
        'source_revision':'b159172f25a9c389f82f20bca4f15e3032791638','mode':'stock',
        'image_digest':'sha256:'+'a'*64}, 'entries':[{'id':tap,'enabled':True,
        'definition':definition,'parameters':{},'source':'antithesis','threshold_gate':{'enabled':False,'thresholds':[]}}],
        'trace_patterns':[str(trace)],'patched_patterns':[]}
    result=collect(target,tmp_path)
    assert result['input_files']
    assert not result['summary']['collector_errors']
    output=json.loads((tmp_path/'measurements/collectors'/tap/'result.json').read_text())
    assert output['export']['normalized_record_count'] > 0


def test_unsupported_remote_hook_is_explicit():
    from profile_manager.antithesis_package import preview_package
    value=request(scenario_id='cardano-amaru-miniprotocol-security-local',
        measurements={'profile_id':None,'tap_ids':['amaru-external-restart-readiness']})
    value['versions']['policy']='latest-stable'
    result=preview_package(value)
    assert result['measurement_binding']['status']=='partial'
    assert any(i['code']=='collector-not-implemented' for i in result['issues'])


def test_header_snapshot_reads_existing_records(tmp_path):
    from scripts.antithesis_measurement_runtime import collect
    from profile_manager.data.catalog_definitions import load_definition
    tap='amaru-stock-header-validation'
    trace=tmp_path/'trace.ndjson'
    trace.write_text(json.dumps({'fields':{'message':'chain.tip_accepted','outcome':'new_tip','header_hash':'cafe'}})+'\n')
    target={'service':'amaru','identity':{'implementation':'amaru','version':'test',
        'source_revision':'b159172f25a9c389f82f20bca4f15e3032791638','mode':'stock','image_digest':'sha256:'+'a'*64},
        'entries':[{'id':tap,'enabled':True,'definition':load_definition('measurements',tap).data,
                   'parameters':{},'source':'antithesis','threshold_gate':{'enabled':False,'thresholds':[]}}],
        'trace_patterns':[str(trace)],'patched_patterns':[]}
    collect(target,tmp_path/'out')
    body=json.loads((tmp_path/'out/measurements/collectors'/tap/'result.json').read_text())
    assert body['measurements']['header_validation']['status']=='available'


def test_patched_snapshot_reuses_patch_identity_and_existing_records(tmp_path):
    from scripts.antithesis_measurement_runtime import collect
    from profile_manager.data.catalog_definitions import load_definition
    from profile_manager.measurement_collectors.amaru_patched import AMARU_SOURCE_REVISION, AMARU_MEASUREMENT_PATCH_SHA256
    tap='amaru-patched-protocol-decode'
    trace=Path(__file__).parent/'fixtures/amaru-b159-patched-traces.ndjson'
    target={'service':'amaru','identity':{'implementation':'amaru','version':'test',
        'source_revision':AMARU_SOURCE_REVISION,'mode':'patched','image_digest':'sha256:'+'a'*64,
        'patch_set_sha256':AMARU_MEASUREMENT_PATCH_SHA256},
        'entries':[{'id':tap,'enabled':True,'definition':load_definition('measurements',tap).data,
                   'parameters':{},'source':'antithesis','threshold_gate':{'enabled':False,'thresholds':[]}}],
        'trace_patterns':[str(trace)],'patched_patterns':[]}
    result=collect(target,tmp_path/'out')
    assert not result['summary']['collector_errors']
    body=json.loads((tmp_path/'out/measurements/collectors'/tap/'result.json').read_text())
    assert body['patch_set_sha256']==AMARU_MEASUREMENT_PATCH_SHA256


def test_exact_patched_cardano_advanced_taps_keep_toolchain_identity(monkeypatch):
    from profile_manager import antithesis_artifacts as a
    from profile_manager.antithesis_package import preview_package
    root=Path(__file__).resolve().parents[1]/'dwarf/targets'
    rows={}
    for impl,recipe,revision,version in [('amaru','measurement-patches-nanoseconds-v4','aedfe797a5b8ef00d8b362be40b47a52c3b4a379','10.11.20260918'),('cardano-node','measurement-patches-nanoseconds-v5','fef83fed01d7926f3de83b3b917be5a4a48768b5','11.1.2')]:
        manifest=json.loads((root/impl/recipe/revision/'manifest.json').read_text())
        identifier=('a' if impl=='amaru' else 'b')*32
        rows[identifier]={'id':identifier,'state':'complete','implementation':impl,'version':version,
            'mode':'patched','source_revision':revision,'patch_set_sha256':manifest['patch_set_sha256'],
            'measurement_revision':manifest['measurement_revision'],'toolchain':manifest['toolchain'],
            'build_result_sha256':'sha256:'+'d'*64,'runtime_probe_log_sha256':'sha256:'+'e'*64,
            'image_id':'sha256:'+'c'*64,'reference':'dwarf-antithesis-fixture-'+impl+':prepared','runtime_interface':'extracted-binary' if impl=='amaru' else None}
    monkeypatch.setattr(a,'read',lambda identifier:rows[identifier])
    value=request(scenario_id='cardano-amaru-miniprotocol-security-local',measurements={'profile_id':None,
        'tap_ids':['amaru-patched-header-validation','cardano-patched-header-validation','cardano-patched-blockfetch-handler-queue','cardano-patched-txsubmission-residence']})
    value['versions'].update(policy='latest-stable',build_variant='instrumented',artifacts={'amaru':'a'*32,'cardano-node':'b'*32})
    result=preview_package(value)
    assert result['measurement_binding']['status']=='applied',result['measurement_binding']['issues']
    cardano=next(t for t in result['measurement_binding']['targets'] if t['identity']['implementation']=='cardano-node')
    assert cardano['identity']['toolchain']=={'ghc':'9.6.7','cabal':'3.16.0.0'}
    for target in result['measurement_binding']['targets']:
        assert target['identity']['build_result_sha256']=='sha256:'+'d'*64
        assert target['identity']['runtime_probe_log_sha256']=='sha256:'+'e'*64


def test_measured_generated_package_can_be_staged_without_weakening_secret_checks(tmp_path):
    from profile_manager.antithesis_preparation import prepare_package,PackageError
    value=request(scenario_id='cardano-amaru-miniprotocol-security-local',measurements={'profile_id':None,'tap_ids':['amaru-stock-network']})
    value['versions']['policy']='latest-stable'
    result=prepare_package(value,{},state_dir=tmp_path/'staged')
    assert result['receipt']['measurement_binding']['status']=='applied'
    supplied=tmp_path/'supplied';supplied.mkdir()
    (supplied/'docker-compose.yaml').write_text('services:\n  node:\n    image: example/node:experiment\n')
    (supplied/'github-pat.txt').write_text('not-a-real-secret')
    with pytest.raises(PackageError,match='secret'):
        prepare_package(request(package_source='existing'),{'compose_path':str(supplied),'image_references':[]},state_dir=tmp_path/'supplied-state')


def test_remote_window_keeps_newest_rotations_and_reports_dropped_history(tmp_path,monkeypatch):
    import os
    from scripts import antithesis_measurement_runtime as remote
    from profile_manager.data.catalog_definitions import load_definition
    for index in range(18):
        p=tmp_path/f'log-{index:02}.ndjson';p.write_text('x'*100+'\n'+'{}\n'*20)
        os.utime(p,(index+1,index+1))
    monkeypatch.setattr(remote,'MAX_BYTES',32)
    tap='amaru-stock-network'
    target={'service':'amaru','identity':{'implementation':'amaru','version':'test','source_revision':None,'mode':'stock','image_digest':'sha256:'+'a'*64},
        'entries':[{'id':tap,'enabled':True,'definition':load_definition('measurements',tap).data,'parameters':{},'source':'antithesis','threshold_gate':{'enabled':False,'thresholds':[]}}],
        'trace_patterns':[str(tmp_path/'*.ndjson')],'patched_patterns':[]}
    result=remote.collect(target,tmp_path/'out')
    assert str(tmp_path/'log-17.ndjson') in result['input_files']
    assert str(tmp_path/'log-00.ndjson') not in result['input_files']
    assert result['source_window']['omitted_file_count']==2
    assert result['source_window']['incomplete'] is True
    body=json.loads((tmp_path/'out/measurements/collectors'/tap/'result.json').read_text())
    assert body['export']['incomplete'] is True
    assert body['export']['adapter_source_window']['omitted_file_count']==2


def test_cardano_measurement_logging_is_enabled_only_in_victim_config():
    from profile_manager.antithesis_package import preview_package
    value=request(scenario_id='cardano-amaru-miniprotocol-security-local',measurements={'profile_id':None,'tap_ids':['cardano-stock-txsubmission-mempool']})
    value['versions']['policy']='latest-stable'
    result=preview_package(value)
    compose=yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    victim=compose['services']['sm-cardano-victim']
    assert 'measurement-config-sm-cardano-victim:/configs:ro' in victim['volumes']
    assert victim['depends_on']['measure-config-sm-cardano-victim']['condition']=='service_completed_successfully'
    assert 'p1-configs:/configs:ro' in compose['services']['p1']['volumes'] or 'p1-configs:/configs' in compose['services']['p1']['volumes']
    assert 'Mempool' in result['files']['environment/measurements/prepare-cardano.py']


def test_progress_log_does_not_repeat_definitions_or_raw_event_history():
    from scripts.antithesis_measurement_runtime import progress_summary
    result={'service':'victim','source_available':True,'source_window':{'incomplete':False},
        'summary':{'resolution':{'large-definition':'x'*100000},'collector_errors':[],
                   'collector_results':{'tap':{'events':['x'*1000]*100,'export':{'source_record_count':100},
                                    'measurements':{'activity':{'status':'available','value':100,'unit':'events'}}}}}}
    body=progress_summary(result)
    assert len(json.dumps(body))<1000
    assert body['collectors']['tap']['source_records']==100
    assert body['collectors']['tap']['measurements']['activity']['value']==100


def test_rotated_header_traces_preserve_latest_verdict(tmp_path):
    import os
    from scripts.antithesis_measurement_runtime import collect
    from profile_manager.data.catalog_definitions import load_definition
    for name,stamp,fields in [('old',1,{'outcome':'invalid_header','header_hash':'cafe'}),
                              ('new',2,{'message':'chain.tip_accepted','outcome':'new_tip','header_hash':'cafe'})]:
        path=tmp_path/(name+'.ndjson')
        path.write_text(json.dumps({'fields':fields})+'\n');os.utime(path,(stamp,stamp))
    tap='amaru-stock-header-validation'
    target={'service':'amaru','identity':{'implementation':'amaru','version':'test',
        'source_revision':None,'mode':'stock','image_digest':'sha256:'+'a'*64},
        'entries':[{'id':tap,'enabled':True,'definition':load_definition('measurements',tap).data,
                   'parameters':{},'source':'antithesis','threshold_gate':{'enabled':False,'thresholds':[]}}],
        'trace_patterns':[str(tmp_path/'*.ndjson')],'patched_patterns':[]}
    result=collect(target,tmp_path/'out')
    assert result['input_files']==[str(tmp_path/'old.ndjson'),str(tmp_path/'new.ndjson')]
    body=json.loads((tmp_path/'out/measurements/collectors'/tap/'result.json').read_text())
    assert body['measurements']['header_validation']['value']['accepted']==1
    assert body['measurements']['header_validation']['value']['rejected']==0


def test_corrected_cardano_contract_preserves_actual_identity_in_results(tmp_path):
    from profile_manager import antithesis_cardano_bridge as compat
    from profile_manager.data.catalog_definitions import load_definition
    from scripts.antithesis_measurement_runtime import collect
    tap='cardano-patched-header-validation'
    trace=tmp_path/'events.ndjson';trace.write_text('')
    identity=dict(implementation='cardano-node',version='11.1.2',mode='patched',engine='antithesis',
        source_revision=compat.SOURCE_REVISION,patch_set_sha256=compat.COMPOSITE_HASH,
        measurement_revision=compat.REVISION,compatibility=compat.contract(),
        toolchain={'ghc':'9.6.7','cabal':'3.16.0.0'})
    for field in ['image_digest','executable_digest','build_result_sha256','runtime_probe_log_sha256']:
        identity[field]='sha256:'+'a'*64
    target={'service':'cardano','identity':identity,'entries':[{'id':tap,'enabled':True,
        'definition':load_definition('measurements',tap).data,'parameters':{},'source':'antithesis',
        'threshold_gate':{'enabled':False,'thresholds':[]}}],'trace_patterns':[], 'patched_patterns':[str(trace)]}
    result=collect(target,tmp_path/'out')
    assert not result['summary']['collector_errors']
    body=json.loads((tmp_path/'out/measurements/collectors'/tap/'result.json').read_text())
    assert body['target']==identity
    assert body['patch_set_sha256']==compat.COMPOSITE_HASH
    assert body['measurement_revision']==compat.REVISION
    invalid={**identity,'compatibility':{**compat.contract(),'correction_sha256':'0'*64}}
    with pytest.raises(ValueError,match='compatibility'):
        compat.build_factories(json_trace_paths=[trace],target_identity=invalid)


@pytest.mark.parametrize('field,bad', [('source_revision','0'*40),('version','other'),('toolchain',{}),('patch_set_sha256','0'*64),('engine','local')])
def test_corrected_cardano_bridge_rejects_other_contracts(field,bad):
    from profile_manager import antithesis_cardano_bridge as bridge
    identity=dict(implementation='cardano-node',version='11.1.2',mode='patched',engine='antithesis',
        source_revision=bridge.SOURCE_REVISION,patch_set_sha256=bridge.COMPOSITE_HASH,
        measurement_revision=bridge.REVISION,compatibility=bridge.contract(),
        toolchain={'ghc':'9.6.7','cabal':'3.16.0.0'})
    identity[field]=bad
    with pytest.raises(ValueError,match='compatibility'):
        bridge.build_factories(json_trace_paths=[],target_identity=identity)


def test_corrected_cardano_bridge_restores_identity_on_prepare_failure():
    from profile_manager import antithesis_cardano_bridge as bridge
    from profile_manager.measurement_collectors import cardano_patched as shared
    identity=dict(implementation='cardano-node',version='11.1.2',mode='patched',engine='antithesis',
        source_revision=bridge.SOURCE_REVISION,patch_set_sha256=bridge.COMPOSITE_HASH,
        measurement_revision=bridge.REVISION,compatibility=bridge.contract(),
        toolchain={'ghc':'9.6.7','cabal':'3.16.0.0'},image_digest='sha256:'+'a'*64)
    assert not shared.supports_cardano_header_validation(identity)
    with pytest.raises(ValueError,match='patch-set'):
        shared._validate_identity(identity)
    tap='cardano-patched-header-validation'
    collector=bridge.build_factories(json_trace_paths=[],target_identity=identity)[tap]({'id':tap})
    with pytest.raises(ValueError,match='executable_digest'):
        collector.prepare(None)
    assert collector.target_identity==identity


def test_generated_patched_definition_stages_with_original_tap_id_and_bytes(tmp_path):
    import base64,io,zipfile
    from profile_manager.antithesis_preparation import prepare_package
    from profile_manager.data.catalog_definitions import load_definition
    tap='cardano-patched-protocol-decode'
    value=request(scenario_id='cardano-amaru-miniprotocol-security-local',measurements={'profile_id':None,'tap_ids':[tap]})
    value['versions']['policy']='latest-stable'
    result=prepare_package(value,{},state_dir=tmp_path/'state')
    assert result['receipt']['request']['measurements']['tap_ids']==[tap]
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64']))) as archive:
        name='provenance/preview/inputs/measurements/cardano-instrumented-protocol-decode.yaml'
        assert archive.read(name)==load_definition('measurements',tap).source
