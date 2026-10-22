import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from test_antithesis_package_validation import staged
from test_antithesis_publication import target
from profile_manager.config import DeploymentConfig
from profile_manager.antithesis_package import PackageError


def module():
    from profile_manager import antithesis_submission
    return antithesis_submission


def wait(identifier):
    for _ in range(500):
        row=module().read_job(identifier)
        if row['state'] not in ('queued','checking','submitting','refreshing'):
            return row
        time.sleep(.01)
    pytest.fail('job did not finish')


@pytest.fixture
def ready_env(staged, monkeypatch):
    m=module()
    path=Path(staged['receipt']['source']['compose_path'])/'docker-compose.yaml'
    path.write_text(path.read_text().replace('example/node:experimental','ghcr.io/example/node:experimental'))
    updated=m.prep.prepare_package(staged['receipt']['request'],{'compose_path':str(path.parent),'image_references':[]})
    staged.clear();staged.update(updated)
    config=DeploymentConfig.from_dict({'moog':{'enabled':True,'token_id':'token','moog_binary':'/bin/moog','secrets_root':'/secrets'}})
    config=replace(config,moog=m.moog.normalize_moog_config(config.moog))
    monkeypatch.setattr(m,'load_settings',lambda:config)
    monkeypatch.setattr(m,'verify_git',lambda *a: {'anonymous':True})
    monkeypatch.setattr(m,'verify_image',lambda *a: {'anonymous':True,'platform':'linux/amd64'})
    real_query=m.query_requester
    monkeypatch.setattr(m,'query_requester',lambda *a: {'checks':[{'id':'requester','state':'ok','detail':'Verified'}], 'facts':[]})
    return {'source':{'kind':'preparation','id':staged['receipt']['id']},'target':target(),'config':config,'query_requester':real_query}


def check(env,identifier='a'*32):
    return wait(module().start_readiness(env['source'],env['target'],identifier)['id'])


def test_readiness_saves_exact_inputs_without_submitting(ready_env,monkeypatch):
    m=module()
    monkeypatch.setattr(m.remote,'run_moog_create_test',lambda *a,**k:pytest.fail('readiness submitted'))
    row=check(ready_env)
    assert row['state']=='ready',row
    assert row['source']==ready_env['source'] and row['target']==ready_env['target']
    assert row['submission_attempted'] is False
    assert row['request']['duration_hours']>=1
    assert row['checks'] and row['inventory']
    assert m.start_readiness(ready_env['source'],ready_env['target'],row['id'])==row


@pytest.mark.parametrize('kind',['preparation','build','publication'])
def test_all_source_paths_use_same_readiness(ready_env,staged,monkeypatch,kind):
    m=module()
    monkeypatch.setattr(m,'read_source',lambda source:staged)
    ready_env['source']['kind']=kind
    assert check(ready_env)['state']=='ready'


def test_missing_settings_and_provider_failure_are_not_ready(ready_env,monkeypatch):
    m=module()
    monkeypatch.setattr(m,'load_settings',lambda:DeploymentConfig.from_dict({}))
    assert check(ready_env)['state']=='blocked'
    monkeypatch.setattr(m,'load_settings',lambda:ready_env['config'])
    monkeypatch.setattr(m,'query_requester',lambda *a: (_ for _ in ()).throw(RuntimeError('TOP_SECRET')))
    row=check(ready_env,'b'*32)
    assert row['state']=='blocked'
    assert 'TOP_SECRET' not in json.dumps(row)


def fact(env,phase='pending',outcome=None):
    t=env['target']
    return {'id':'f'*64,'key':{'type':'test-run','platform':'github','repository':dict(zip(['organization','repo'],t['repository'].split('/'))),
             'requester':t['requester'],'commitId':t['commit'],'directory':t['directory'],'try':t['try_number']},
            'value':{'phase':phase,'outcome':outcome,'url':'https://secret/?auth=TOP_SECRET'}}


def test_existing_exact_attempt_blocks_without_incrementing(ready_env,monkeypatch):
    m=module()
    monkeypatch.setattr(m,'query_requester',lambda *a:{'checks':[],'facts':[fact(ready_env)]})
    row=check(ready_env)
    assert row['state']=='blocked' and row['existing_test_run_id']=='f'*64
    assert row['target']['try_number']==1 and 'TOP_SECRET' not in json.dumps(row)


def test_changed_source_and_request_id_collision(ready_env):
    m=module();row=check(ready_env)
    with pytest.raises(PackageError):m.start_readiness(ready_env['source'],dict(ready_env['target'],try_number=2),row['id'])
    path=m.prep.state_directory()/ready_env['source']['id']/'package/docker-compose.yaml'
    path.write_text('changed')
    row=check(ready_env,'b'*32)
    assert row['state']=='blocked'


def test_api_requires_auth_and_exact_shape(ready_env,monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request as dispatch
    m=module();monkeypatch.setattr(m,'start_readiness',lambda *a:pytest.fail('unauthorized'))
    payload={'token':'bad','source':ready_env['source'],'target':ready_env['target'],'request_id':'a'*32}
    call=lambda:dispatch(method='POST',path='/api/antithesis-run/package/readiness',body=json.dumps(payload).encode(),expected_token='good')
    assert call()[0]==403
    payload['token']='good';payload['extra']='bad';assert call()[0]==400

@pytest.mark.parametrize('facts',[None,{},[{}],[{'id':'f'*64,'key':{},'value':{}}]])
def test_malformed_facts_cannot_establish_no_duplicate(ready_env,monkeypatch,facts):
    m=module();monkeypatch.setattr(m,'query_requester',lambda *a:{'checks':[],'facts':facts})
    assert check(ready_env)['state']=='blocked'


def test_query_requester_never_exposes_provider_errors(ready_env,monkeypatch):
    m=module()
    monkeypatch.setattr(m.remote,'control_shim_enabled',lambda:False)
    monkeypatch.setattr(m.remote,'ssh_command',lambda *a,**k:m.remote.CommandResult(0,'{"credentials":true,"public_key":"vkey-selected"}','',''))
    monkeypatch.setattr(m,'_query_readiness',lambda *a,**k:{'checks':[{'id':'facts_users','state':'error','detail':'TOP_SECRET'}]})
    monkeypatch.setattr(m,'query_facts',lambda *a:[])
    # Restore the real query function hidden by ready_env's network seam.
    real=ready_env['query_requester']
    result=real(ready_env['config'],ready_env['target'])
    assert any(c['state']=='error' for c in result['checks'])
    assert 'TOP_SECRET' not in json.dumps(result)

@pytest.mark.parametrize('change', ['tap','profile','patched','version','workload','topology'])
def test_generated_unsupported_intent_is_blocked(ready_env,staged,monkeypatch,change):
    m=module();receipt=staged['receipt'];request=receipt['request'];request['package_source']='build'
    receipt['version_binding']={'status':'applied'};receipt['workload_binding']={'status':'applied'}
    if change=='tap':request['measurements']['tap_ids']=['example']
    if change=='profile':request['measurements']['profile_id']='example'
    if change=='patched':request['versions']['build_variant']='instrumented'
    if change=='version':receipt['version_binding']['status']='recorded'
    if change=='workload':receipt['workload_binding']['status']='recorded'
    if change=='topology':receipt['issues'].append({'code':'topology-scenario-merge-not-implemented'})
    monkeypatch.setattr(m,'read_source',lambda source:staged)
    assert check(ready_env)['state']=='blocked'


def test_selected_wallet_must_match_cached_registered_identity(ready_env,monkeypatch):
    m=module();config=ready_env['config']
    monkeypatch.setattr(m.remote,'control_shim_enabled',lambda:False)
    calls=[]
    def ssh(config,command,**kwargs):
        calls.append(command)
        return m.remote.CommandResult(0,'{"credentials":true,"public_key":"different-key"}','','')
    monkeypatch.setattr(m.remote,'ssh_command',ssh)
    monkeypatch.setattr(m,'_query_readiness',lambda *a,**k:{'requester_public_key':'cached-key','checks':[]})
    monkeypatch.setattr(m,'query_facts',lambda *a:[])
    row=ready_env['query_requester'](config,ready_env['target'])
    assert any(c['id']=='selected_wallet' and c['state']=='error' for c in row['checks'])
    assert 'wallet' in calls[0] and 'info' in calls[0] and 'MOOG_WALLET_PASSPHRASE' in calls[0]


def test_explicit_submission_uses_existing_transport_once_and_ledger(ready_env,monkeypatch):
    m=module();row=check(ready_env);calls=[]
    def submit(*a,**kw):
        calls.append(kw)
        return m.remote.CommandResult(0,json.dumps({'test_run_id':'f'*64}),'TOP_SECRET','TOP_SECRET')
    monkeypatch.setattr(m.remote,'run_moog_create_test',submit)
    row=wait(m.submit(row['id'])['id'])
    assert row['state']=='submitted' and row['test_run_id']=='f'*64
    assert row['dwarf_run_id'].startswith('external-antithesis-')
    assert m.submit(row['id'])==row and len(calls)==1
    assert calls[0]['commit']==ready_env['target']['commit']
    assert 'TOP_SECRET' not in json.dumps(row)


@pytest.mark.parametrize('pretty', [False, True])
def test_release_wrapped_acknowledgement_is_retained_without_replay(ready_env, monkeypatch, pretty):
    m=module(); row=check(ready_env); calls=[]
    # Release 0.5.1.3's actual successful shape: WithTxHash NewTestRunCreated.
    payload={'txHash':'a'*64,'value':{'testRunId':'f'*64,
             'state':{'phase':'pending','duration':1,'faults_enabled':True}}}
    def submit(*args, **kwargs):
        calls.append(kwargs)
        return m.remote.CommandResult(0,json.dumps(payload,indent=2 if pretty else None),'TOP_SECRET','')
    monkeypatch.setattr(m.remote,'run_moog_create_test',submit)
    row=wait(m.submit(row['id'])['id'])
    assert row['state']=='submitted' and row['test_run_id']=='f'*64
    assert m.submit(row['id'])==row and len(calls)==1
    assert 'TOP_SECRET' not in json.dumps(row)


def test_uncertain_transport_diagnostics_are_private_not_exposed(ready_env, monkeypatch):
    m=module(); row=check(ready_env)
    monkeypatch.setattr(m.remote,'run_moog_create_test',lambda *args,**kwargs:
        m.remote.CommandResult(1,'private output','TOP_SECRET','DO_NOT_RETAIN_COMMAND'))
    row=wait(m.submit(row['id'])['id'])
    assert row['state']=='uncertain' and row['transport_exit_code']==1
    path=m._path(row['id'])/'transport.json'
    diagnostic=json.loads(path.read_text())
    assert diagnostic=={'returncode':1,'stdout':'private output','stderr':'TOP_SECRET'}
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'TOP_SECRET' not in json.dumps(row)


@pytest.mark.parametrize('output', [
    json.dumps({'testRunId':'a'*64})+'\n'+json.dumps({'testRunId':'b'*64}),
    'test-run-id: '+'a'*64+'\ntest-run-id: '+'b'*64,
    json.dumps({'txHash':'c'*64,'value':{'testRunId':'a'*64}})+'\n'+json.dumps({'testRunId':'b'*64}),
    json.dumps({'txHash':'c'*64,'value':{'testRunId':'a'*64}})+'\n'+json.dumps({'txHash':'c'*64,'value':{'testRunId':'b'*64}}),
])
def test_conflicting_acknowledgements_are_uncertain(output):
    assert module()._acknowledged_test_id(output) is None


@pytest.mark.parametrize('mode',['timeout','no-id','nonzero'])
def test_uncertain_submission_never_replays(ready_env,monkeypatch,mode):
    m=module();row=check(ready_env);calls=[]
    def submit(*a,**kw):
        calls.append(kw)
        if mode=='timeout':raise TimeoutError('TOP_SECRET')
        return m.remote.CommandResult(1 if mode=='nonzero' else 0,'submitted','TOP_SECRET','TOP_SECRET')
    monkeypatch.setattr(m.remote,'run_moog_create_test',submit)
    row=wait(m.submit(row['id'])['id'])
    assert row['state']=='uncertain' and row['submission_attempted']
    assert m.submit(row['id'])['state']=='uncertain' and len(calls)==1
    assert 'TOP_SECRET' not in json.dumps(row)
    # Matching chain fact can recover an ambiguous response without another submission.
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env)])
    row=wait(m.refresh(row['id'])['id'])
    assert row['test_run_id']=='f'*64 and row['state']=='pending' and len(calls)==1


def test_changed_config_and_stale_readiness_prevent_submission(ready_env,monkeypatch):
    m=module();row=check(ready_env)
    monkeypatch.setattr(m.remote,'run_moog_create_test',lambda *a,**k:pytest.fail('changed config submitted'))
    changed=replace(ready_env['config'],host='other-host')
    monkeypatch.setattr(m,'load_settings',lambda:changed)
    row=wait(m.submit(row['id'])['id'])
    assert row['state']=='blocked' and not row['submission_attempted']
    monkeypatch.setattr(m,'load_settings',lambda:ready_env['config'])
    row=check(ready_env,'b'*32);row['checked_at']=0
    m.build._save(m._path(row['id'])/'status.json',row)
    with pytest.raises(PackageError):m.submit(row['id'])


def test_finished_failure_does_not_claim_findings_or_execution_success(ready_env,monkeypatch):
    m=module();row=check(ready_env)
    monkeypatch.setattr(m.remote,'run_moog_create_test',lambda *a,**k:m.remote.CommandResult(0,json.dumps({'test_run_id':'f'*64}),'',''))
    row=wait(m.submit(row['id'])['id'])
    receipt=m.storage_roots().runs/row['dwarf_run_id']/'external/submission.json'
    original=receipt.read_bytes()
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env,'finished','failure')])
    row=wait(m.refresh(row['id'])['id'])
    assert row['state']=='completed' and row['outcome']=='failure'
    assert 'cause is not classified' in row['message']
    assert 'reported findings' not in row['message'] and 'TOP_SECRET' not in json.dumps(row)
    from profile_manager.run_ledger import RunLedger
    from profile_manager.config import storage_roots
    ledger=RunLedger(storage_roots().state).get(row['dwarf_run_id'])
    assert ledger['state']=='completed' and ledger['verdict']=='inconclusive'
    assert receipt.read_bytes()==original


def test_second_check_same_attempt_cannot_submit_twice(ready_env,monkeypatch):
    m=module();a=check(ready_env);b=check(ready_env,'b'*32);calls=[]
    def submit(*args,**kwargs):
        calls.append(kwargs);return m.remote.CommandResult(0,json.dumps({'test_run_id':'f'*64}),'','')
    monkeypatch.setattr(m.remote,'run_moog_create_test',submit)
    wait(m.submit(a['id'])['id'])
    row=wait(m.submit(b['id'])['id'])
    assert len(calls)==1 and row['state'] in ('blocked','uncertain')


from test_antithesis_image_build import prepared, catalog, storage
from test_antithesis_publication import built


def test_actual_generated_public_export_reaches_readiness(built,ready_env,monkeypatch):
    from test_antithesis_publication import create, public_images, run
    m=module();handoff=create(built);public_images(monkeypatch)
    assert run(handoff,'verify-images')['state']=='succeeded'
    ready_env['source']={'kind':'publication','id':handoff['id']}
    row=check(ready_env)
    assert row['state']=='ready',row
    assert row['request']['scenario_id']=='cardano-amaru-miniprotocol-security-local'


def test_double_click_concurrency_and_worker_restart_never_resubmits(ready_env,monkeypatch):
    import threading
    m=module();row=check(ready_env);entered=threading.Event();release=threading.Event();calls=[]
    def submit(*a,**k):
        calls.append(k);entered.set();assert release.wait(5)
        return m.remote.CommandResult(0,json.dumps({'test_run_id':'f'*64}),'','')
    monkeypatch.setattr(m.remote,'run_moog_create_test',submit)
    first=m.submit(row['id'])
    try:
        assert entered.wait(5)
        assert m.submit(row['id'])['id']==first['id']
        assert len(calls)==1
    finally:release.set()
    row=wait(row['id']);row['state']='submitting'
    m.build._save(m._path(row['id'])/'status.json',row)
    assert m.read_job(row['id'])['state']=='uncertain'
    assert m.submit(row['id'])['state']=='uncertain' and len(calls)==1


def test_status_errors_and_stale_facts_do_not_erase_completion(ready_env,monkeypatch):
    m=module();row=check(ready_env)
    monkeypatch.setattr(m.remote,'run_moog_create_test',lambda *a,**k:m.remote.CommandResult(0,json.dumps({'test_run_id':'f'*64}),'',''))
    row=wait(m.submit(row['id'])['id'])
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env,'finished','failure')])
    row=wait(m.refresh(row['id'])['id']);assert row['state']=='completed'
    for results in ([],[fact(ready_env,'running')],[fact(ready_env,'failed')]):
        monkeypatch.setattr(m,'query_facts',lambda *a:results)
        row=wait(m.refresh(row['id'])['id'])
        assert row['state']=='completed' and row['outcome']=='failure'
    def fail(*a):raise RuntimeError('TOP_SECRET')
    monkeypatch.setattr(m,'query_facts',fail)
    row=wait(m.refresh(row['id'])['id'])
    assert row['state']=='completed' and 'TOP_SECRET' not in json.dumps(row)


def test_crash_between_claim_and_ledger_can_reconcile_without_replay(ready_env,monkeypatch):
    m=module();row=check(ready_env)
    reserve=m._reserve
    monkeypatch.setattr(m,'_reserve',lambda *a:(_ for _ in ()).throw(OSError('disk failed')))
    monkeypatch.setattr(m.remote,'run_moog_create_test',lambda *a,**k:pytest.fail('crashed reservation submitted'))
    row=wait(m.submit(row['id'])['id'])
    assert row['submission_attempted'] and row['state']=='uncertain'
    assert m.submit(row['id'])['state']=='uncertain'
    monkeypatch.setattr(m,'_reserve',reserve)
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env)])
    assert wait(m.refresh(row['id'])['id'])['state']=='pending'


def test_cli_reservation_same_attempt_different_parameters_blocks(ready_env,monkeypatch):
    m=module();row=check(ready_env)
    launch=m._launch(row);launch.update(repository=launch['repository'].upper(),github_user=launch['github_user'].upper(),duration_hours=99,faults=not launch['faults'])
    m.run_service.reserve_external_run(m.run_service.ExternalRunRequest(backend='antithesis',launch=launch))
    calls=[]
    monkeypatch.setattr(m.remote,'run_moog_create_test',lambda *a,**k:(calls.append(k) or m.remote.CommandResult(0,'','','')))
    result=wait(m.submit(row['id'])['id'])
    assert result['state'] in ('blocked','uncertain') and not calls


def test_existing_fact_import_does_not_invent_submission(ready_env,monkeypatch):
    m=module();existing=fact(ready_env,'finished','failure');existing['value']['duration']=5
    monkeypatch.setattr(m,'query_requester',lambda *a:{'checks':[],'facts':[existing]})
    row=check(ready_env);assert row['state']=='blocked'
    monkeypatch.setattr(m,'query_facts',lambda *a:[existing])
    row=wait(m.refresh(row['id'])['id']);assert row['state']=='completed'
    from profile_manager.config import storage_roots
    directory=storage_roots().runs/row['dwarf_run_id']/'external'
    assert not (directory/'submission.json').exists()
    assert (directory/'link.json').exists()
    assert json.loads((directory/'launch.json').read_text())['duration_hours']==5


def test_interrupted_refresh_preserves_terminal_observation(ready_env):
    m=module();row=check(ready_env)
    row.update(state='refreshing',previous_state='completed',submission_attempted=True,
               outcome='failure',message='Run completed with reported findings.')
    m.build._save(m._path(row['id'])/'status.json',row)
    recovered=m.read_job(row['id'])
    assert recovered['state']=='completed' and recovered['outcome']=='failure'
    assert 'interrupted' in recovered['refresh_error']


def test_acknowledgement_waits_for_ledger_before_publishing_terminal_state(ready_env,monkeypatch):
    import threading
    m=module();row=check(ready_env);entered=threading.Event();release=threading.Event()
    original=m._record_submission
    def record(*a):
        entered.set();assert release.wait(5);return original(*a)
    monkeypatch.setattr(m,'_record_submission',record)
    monkeypatch.setattr(m.remote,'run_moog_create_test',lambda *a,**k:m.remote.CommandResult(0,json.dumps({'test_run_id':'f'*64}),'',''))
    m.submit(row['id'])
    try:
        assert entered.wait(5)
        assert m.read_job(row['id'])['state']=='submitting'
    finally:
        release.set();wait(row['id'])


@pytest.mark.parametrize('repository,platform,kind,expected',[
    ({'organization':'example','repo':'DWARF'},'github','white-list-repo','ok'),
    ({'organization':'example','project':'dwarf'},'github','white-list-repo','ok'),
    ({'organization':'example','repo':'dwarf','project':'other'},'github','white-list-repo','error'),
    ({'organization':'example','project':'dwarf'},'gitlab','white-list-repo','error'),
    ({'organization':'example','project':'dwarf'},'github','role','error'),
    ({'organization':'different','repo':'dwarf'},'github','white-list-repo','error'),
    (None,'github','white-list-repo','error'),
])
def test_requester_accepts_live_release_whitelist_shape_only_for_matching_github_repo(ready_env,monkeypatch,repository,platform,kind,expected):
    import copy
    m=module()
    facts={'white_list':[{'id':'42b603572feb3fd5684f33e3112c1a914a09f39c4aaf60a4a243794e170568fd',
        'key':{'type':kind,'platform':platform,'repository':repository},'value':None}],
        '_checks':[{'id':'facts_white_list','state':'ok','detail':'1'}]}
    original=copy.deepcopy(facts)
    monkeypatch.setattr(m.remote,'control_shim_enabled',lambda:False)
    monkeypatch.setattr(m.remote,'ssh_command',lambda *a,**k:m.remote.CommandResult(0,'{"credentials":true,"public_key":"vkey"}','',''))
    monkeypatch.setattr(m.moog,'query_moog_health',lambda *a,**k:{})
    monkeypatch.setattr(m.moog,'query_moog_facts',lambda *a,**k:facts)
    monkeypatch.setattr(m.moog,'query_moog_github_artifacts',lambda *a,**k:{})
    monkeypatch.setattr(m.moog,'wallet_statuses',lambda *a,**k:[])
    monkeypatch.setattr(m,'query_facts',lambda *a:[])
    result=ready_env['query_requester'](ready_env['config'],ready_env['target'])
    assert next(c['state'] for c in result['checks'] if c['id']=='moog_repo_whitelisted')==expected
    assert facts==original
