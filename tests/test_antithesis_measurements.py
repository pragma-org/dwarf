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


@pytest.mark.parametrize('field,bad', [('source_revision','0'*40),('version','other'),('toolchain',{}),('patch_set_sha256','0'*64),('engine','other')])
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


def test_cardano_config_program_is_self_contained_and_keeps_keepalive_debug(tmp_path):
    from profile_manager.antithesis_measurements import cardano_config_program

    program = cardano_config_program()
    namespace: dict = {}
    exec(compile(program, "cardano_config_program", "exec"), namespace)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"TraceOptions": {}}))
    namespace["_enable_cardano_measurement_traces"](path)
    options = json.loads(path.read_text())["TraceOptions"]
    assert options["KeepAlive.Remote"]["severity"] == "Debug"
    assert options["Mempool"]["severity"] == "Debug"   # TraceMempoolSynced is Debug-level
    assert "CARDANO_MEASUREMENT_TRACE_SEVERITIES=" in program
    # cardano-node 11.1.2 defaults to the legacy V1 inbound TxSubmission logic; the v5
    # residence contract instruments Inbound.V2, so measurement configs select it.
    assert json.loads(path.read_text())["TxSubmissionLogicVersion"] == "TxSubmissionLogicV2"
    assert "CARDANO_MEASUREMENT_NODE_SETTINGS=" in program


# --- patched identity from operator registrations (AT-1 Task 4) ------------

AMARU_V4_REV='aedfe797a5b8ef00d8b362be40b47a52c3b4a379'


def _amaru_registration(source_revision=AMARU_V4_REV, **build_changes):
    root=Path(__file__).resolve().parents[1]/'dwarf/targets'
    manifest=json.loads((root/'amaru/measurement-patches-nanoseconds-v4'/AMARU_V4_REV/'manifest.json').read_text())
    return {'registration_id':'registration-release-build-'+'1'*32+'-instrumented','implementation':'amaru',
        'version':'10.11.20260918','config_image_id':'sha256:'+'c'*64,'executable_digest':'sha256:'+'b'*64,
        'controller_installation_id':'test','runtime_interface':'extracted-binary',
        'provenance':{'source_revision':source_revision,'qualification':'unverified'},
        'source_build':{'variant':'instrumented','execution_id':'release-build-'+'1'*32,
            'build_result_sha256':'sha256:'+'d'*64,'patch_set_sha256':manifest['patch_set_sha256'],
            'measurement_revision':manifest['measurement_revision'],'instrumentation_evidence_sha256':'sha256:'+'e'*64,
            **build_changes}}


def _cardano_registration():
    from test_release_build_identity import cardano_registration
    reg=cardano_registration()
    reg.update(registration_id='registration-release-build-'+'2'*32+'-instrumented',runtime_interface=None,
               config_image_id='sha256:'+'f'*64)
    reg['source_build']['execution_id']='release-build-'+'2'*32
    return reg


def _rows_from(*registrations):
    from profile_manager import antithesis_artifacts as a
    rows={}
    for reg in registrations:
        identifier=a.artifact_id_for(reg['registration_id'])
        result={'status':'adapted','artifact_id':identifier,'reference':'dwarf-antithesis-node-'+identifier+':prepared',
            'image_id':reg['config_image_id'],'executable_digest':reg['executable_digest'],
            'runtime_interface':reg['runtime_interface'],'runtime_probe_log_sha256':'sha256:'+'e'*64,
            'provenance_check':{'state':'ok','label':reg['version'],'binary_commit':reg['provenance']['source_revision']},
            'preload_check':{'state':'not-applicable-static','dso_sha256':None,'log_sha256':None},
            'bootstrap_probe':'version-and-run-help' if reg['implementation']=='amaru' else None}
        rows[identifier]=a._row_from_registration(reg,result)
    return rows


def _instrumented_preview(monkeypatch,rows,tap_ids):
    from profile_manager import antithesis_artifacts as a
    from profile_manager.antithesis_package import preview_package
    monkeypatch.setattr(a,'read',lambda identifier:rows[identifier])
    value=request(scenario_id='cardano-amaru-miniprotocol-security-local',measurements={'profile_id':None,'tap_ids':tap_ids})
    artifacts={row['implementation']:row['id'] for row in rows.values()}
    value['versions'].update(policy='latest-stable',build_variant='instrumented',artifacts=artifacts)
    return preview_package(value)


def test_patched_identity_uses_instrumented_identity(monkeypatch):
    from profile_manager.release_build_identity import validate_collector_identity
    rows=_rows_from(_amaru_registration(),_cardano_registration())
    result=_instrumented_preview(monkeypatch,rows,['amaru-patched-header-validation','cardano-patched-header-validation'])
    binding=result['measurement_binding']
    assert binding['status']=='applied',binding['issues']
    assert {t['identity']['implementation'] for t in binding['targets']}=={'amaru','cardano-node'}
    for target in binding['targets']:
        identity=target['identity']
        assert identity['identity_kind']=='operator-local-config'
        assert identity['engine']=='antithesis' and identity['mode']=='patched'
        assert identity['image_config_id']==identity['image_digest']
        assert identity['verified_instrumentation']['controller_installation_id']=='test'
        assert identity['source_build']['variant']=='instrumented'
        assert 'experimental_instrumentation' not in identity
        assert validate_collector_identity(identity)


def test_auto_ported_amaru_registration_binds_patched_taps(tmp_path,monkeypatch):
    from profile_manager import measurement_patch_port as port_module
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT',str(tmp_path))
    root=Path(__file__).resolve().parents[1]/'dwarf/targets'
    base=json.loads((root/'amaru/measurement-patches-nanoseconds-v4'/AMARU_V4_REV/'manifest.json').read_text())
    derived=port_module.derive_manifest(base,source_revision='b'*40,release='x',
        preimages={p['path']:'ab'*32 for i in base['patches'] for p in i['preimages']},base_recipe_dir='d')
    port_module.save_derived_recipe('amaru','b'*40,derived,{})
    reg=_amaru_registration(source_revision='b'*40,patch_set_sha256=derived['patch_set_sha256'],
        measurement_revision=derived['measurement_revision'],auto_ported=True,ported_from_source_revision=AMARU_V4_REV)
    rows=_rows_from(reg,_cardano_registration())
    # header-validation is pinned to the exact 0918 source; a derived recipe
    # serves the ported taps, exactly as the local run does.
    result=_instrumented_preview(monkeypatch,rows,['amaru-patched-protocol-decode'])
    binding=result['measurement_binding']
    assert binding['status']=='applied',binding['issues']
    amaru=next(t for t in binding['targets'] if t['identity']['implementation']=='amaru')
    assert amaru['identity']['source_build']['auto_ported'] is True
    assert amaru['identity']['source_build']['ported_from_source_revision']==AMARU_V4_REV
    assert amaru['identity']['source_revision']=='b'*40
    assert [e['id'] for e in amaru['entries']]==['amaru-patched-protocol-decode']


def test_legacy_experimental_row_still_binds(monkeypatch):
    from profile_manager import antithesis_artifacts as a
    from profile_manager import antithesis_measurements as am
    from profile_manager.antithesis_package import preview_package
    candidate={'source_revision':'a'*40,'template_id':'nanoseconds-v3','measurement_revision':'nanoseconds-v3',
        'patch_set_sha256':'sha256:'+'1'*64,'evidence_sha256':'sha256:'+'2'*64,
        'controller_installation_id':'antithesis-artifact-'+'a'*32,'config_image_id':'sha256:'+'c'*64,'executable_digest':'sha256:'+'b'*64}
    row={'id':'a'*32,'state':'complete','implementation':'amaru','version':'10.11.20260918','mode':'patched',
        'source_revision':'a'*40,'patch_set_sha256':'sha256:'+'1'*64,'measurement_revision':'nanoseconds-v3',
        'image_id':'sha256:'+'c'*64,'reference':'dwarf-antithesis-fixture-amaru:prepared','runtime_interface':'extracted-binary',
        'experimental_instrumentation':candidate}
    cardano=_rows_from(_cardano_registration())
    rows={'a'*32:row,**cardano}
    seen=[]
    def capture(*,json_trace_paths,target_identity,**kw):
        seen.append(target_identity);return {'amaru-patched-header-validation'}
    monkeypatch.setattr(am,'build_amaru_patched_factories',capture)
    monkeypatch.setattr(a,'read',lambda identifier:rows[identifier])
    value=request(scenario_id='cardano-amaru-miniprotocol-security-local',measurements={'profile_id':None,'tap_ids':['amaru-patched-header-validation']})
    value['versions'].update(policy='latest-stable',build_variant='instrumented',artifacts={'amaru':'a'*32,'cardano-node':next(iter(cardano))})
    preview_package(value)
    assert seen and seen[0]['experimental_instrumentation']==candidate
    assert seen[0]['identity_kind']=='operator-local-config' and 'verified_instrumentation' not in seen[0]


def test_generated_rig_targets_use_their_own_evidence_volumes():
    from profile_manager import antithesis_generated_rig as rig
    from profile_manager.antithesis_measurements import bind_measurements
    from test_antithesis_generated_rig import body
    scenario=body(profile='profile-a-haskell-peersharing-disabled', target={'implementation':'cardano-node','version':'10.7.1'})
    rendered=rig.render_rig(rig.scenario_devnet(scenario), scenario.id)
    files=dict(rendered['files'])
    versions={'status':'applied','node_artifacts':{},'services':[
        {'service':name,'implementation':'cardano-node','requested_version':'10.7.1','source_revision':'f'*40,'artifact':{'digest':'sha256:'+'a'*64}}
        for name in ('node1','node2','node3')]}
    workload={'status':'applied','targets':['node1','node2','node3'],'evidence_volumes':{'node1':'node1-evidence','node2':'node2-evidence','node3':'node3-evidence'}}
    report=bind_measurements(files,{'profile_id':None,'tap_ids':['cardano-stock-blockfetch']},scenario,versions,workload)
    assert report['status']!='not-requested' and report['targets'], report['issues']
    assert {t['evidence_volume'] for t in report['targets']}=={'node1-evidence','node2-evidence','node3-evidence'}
    compose=yaml.safe_load(files['environment/docker-compose.yaml'])
    assert 'node1-evidence:/inputs/node1:ro' in compose['services']['dwarf-measurements']['volumes']
    assert 'sm-cardano-runtime-evidence:/inputs/node1:ro' not in compose['services']['dwarf-measurements']['volumes']


def test_taps_are_always_on_for_every_rig_node_unless_the_scenario_says_none(monkeypatch):
    """F178 (user rule 2026-11-11): the generator attaches taps like the local path — each node
    implementation's default profile (stock/patched by artifact mode) — when the request names
    none, and to every rig node with an evidence volume, not only the faultable targets. Before,
    the mixed chainhold package bound no tap (Amaru consumer is not a target) and a plain request
    carried no measurements at all."""
    from profile_manager.antithesis_package import preview_package
    from test_antithesis_package import _catalog
    _catalog(monkeypatch)
    versions={'policy':'exact','versions':{'cardano-node':'10.7.1','amaru':'10.11.20260918'},'build_variant':'stock','experimental_intent':False}
    result=preview_package(request(scenario_id='consensus-chainhold-upstream-differential',versions=versions,
                                   measurements={'profile_id':None,'tap_ids':[]}))
    binding=result['measurement_binding']
    bound={t['service']:{e['id'] for e in t['entries']} for t in binding['targets']}
    assert set(bound)=={'relay1','relay2','amaru-consumer'}, binding['issues']
    assert 'amaru-stock-network' in bound['amaru-consumer'] and 'cardano-stock-network' in bound['relay1']
    assert all(e['selection_source']=='implicit-default' for t in binding['targets'] for e in t['entries'])
    compose=yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    assert 'dwarf-measurements' in compose['services']
    # Taps the default profile names but no package collector exists for are a named gap, not silence.
    assert any(i['field']=='measurements.amaru-stock-resources' for i in binding['issues'])
    # A4: the workload reads each cardano node's tracer output (the node writes through its own tracer).
    assert 'measurement-tracer-relay1:/inputs/tracer-relay1:ro' in compose['services']['dwarf-workload']['volumes']
    runtime=json.loads(result['files']['environment/dwarf-workload/package/runtime.json'])
    assert {n['id']:n.get('log_path') for n in runtime['nodes']}['relay1']=='/inputs/tracer-relay1/logs'
    assert 'measurement-config-relay1:/inputs/config-relay1:ro' in compose['services']['dwarf-workload']['volumes']
    assert {n['id']:n.get('config_path') for n in runtime['nodes']}['relay1']=='/inputs/config-relay1/configs/config.json'
