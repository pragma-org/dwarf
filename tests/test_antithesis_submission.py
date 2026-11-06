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


def test_readiness_checks_carry_group_and_label(ready_env):
    row=check(ready_env)
    assert row['state']=='ready'
    by_id={c['id']:c for c in row['checks']}
    assert by_id['package']['group']=='Package' and by_id['package']['label']
    assert by_id['public_git']['group']=='Public Git'
    assert by_id['public_image']['group']=='Public images'
    assert by_id['requester']['group']=='MOOG requester (external)' and by_id['requester']['external'] is True
    assert by_id['duplicate']['group']=='Duplicate request'
    assert all({'id','state','detail','group','label'}<=set(c) for c in row['checks'])


def test_public_image_checks_are_reported_per_reference(ready_env,staged,monkeypatch):
    m=module()
    path=Path(staged['receipt']['source']['compose_path'])/'docker-compose.yaml'
    path.write_text(path.read_text()+'  second:\n    image: ghcr.io/example/other:tag\n    container_name: second\n    init: true\n')
    updated=m.prep.prepare_package(staged['receipt']['request'],{'compose_path':str(path.parent),'image_references':[]})
    ready_env['source']['id']=updated['receipt']['id']
    def verify(reference,expected_id=None):
        if reference.endswith('other:tag'):
            raise PackageError('Anonymous public read returned HTTP 404. Check visibility, reference and provider rate limits.',status=422)
        return {'anonymous':True,'platform':'linux/amd64'}
    monkeypatch.setattr(m,'verify_image',verify)
    row=check(ready_env,'c'*32)
    assert row['state']=='blocked'
    images=[c for c in row['checks'] if c['id']=='public_image']
    assert len(images)==2
    states={c['detail'].split(' ')[0] if False else (c['state'],'other:tag' in c['detail']) for c in images}
    assert states=={('ok',False),('error',True)}
    private=next(c for c in images if c['state']=='error')
    assert private['urls']['user'].endswith('/packages/container/other/settings')


# ---------------------------------------------------------------------------
# AT-4 Task 5: scenario binding at reservation and result collection on refresh

AT4=Path(__file__).resolve().parent/'fixtures'/'antithesis_proxy'


def at4(name):return json.loads((AT4/name).read_text())


def _submitted(ready_env,monkeypatch):
    m=module();row=check(ready_env)
    monkeypatch.setattr(m.remote,'run_moog_create_test',lambda *a,**k:m.remote.CommandResult(0,json.dumps({'test_run_id':'f'*64}),'',''))
    return wait(m.submit(row['id'])['id'])


def _proxy(monkeypatch,*,find=None,run=None,props=None):
    m=module();calls=[]
    run=run or at4('run-completed.json');props=at4('properties.json') if props is None else props
    def find_run(config,moog_config,test_run_id,**kw):
        calls.append(('find',test_run_id));return find or {'status':'found','run':run,'matches':1,'pages':1,'error':''}
    def fetch(config,moog_config,subcommand,**kw):
        calls.append((subcommand,kw.get('run_id')))
        if subcommand=='run':return {'ok':True,'payload':run,'exit_code':0,'error':''}
        if subcommand=='properties':return {'ok':True,'payload':props,'exit_code':0,'error':''}
        if subcommand=='events':return {'ok':True,'payload':[],'exit_code':0,'error':''}
        raise AssertionError(subcommand)
    monkeypatch.setattr(m.remote,'find_antithesis_run',find_run);monkeypatch.setattr(m.remote,'fetch_antithesis',fetch)
    return calls


def _entry(m,row):
    from profile_manager.run_ledger import RunLedger
    return RunLedger(m.storage_roots().state).get(row['dwarf_run_id'])


def test_reserve_binds_the_package_scenario(ready_env,monkeypatch):
    m=module();row=_submitted(ready_env,monkeypatch)
    entry=_entry(m,row)
    assert entry['scenario']['id']=='cardano-node-cbor-block-header-fuzz' and len(entry['scenario']['sha256'])==64


def test_refresh_collects_the_result_and_records_a_verdict(ready_env,monkeypatch):
    m=module();row=_submitted(ready_env,monkeypatch)
    run=at4('run-completed.json');run['links']={'triage_report':'https://secret/report?auth=TOP_SECRET'}
    calls=_proxy(monkeypatch,run=run)
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env,'finished','failure')])
    row=wait(m.refresh(row['id'])['id'])
    assert row['state']=='completed',row
    assert row['result']['state']=='verified' and row['result']['verdict']=='fail' and row['result']['reason']=='platform_property_failed',row['result']
    assert row['result']['antithesis_run_id']==run['run_id'] and row['result']['dwarf_property_count']==2
    assert row['result']['run_page']=='/operate/runs/'+row['dwarf_run_id'] and 'No Antithesis errors' in row['result']['failing']
    assert calls==[('find','f'*64),('run',run['run_id']),('properties',run['run_id']),('events',run['run_id'])]
    assert 'verdict fail' in row['message']
    entry=_entry(m,row)
    assert entry['verdict']=='fail' and entry['provenance']['verdict_reason']=='verified_antithesis_report'
    run_dir=Path(entry['evidence_path']);link=run_dir/'external'/'report-link.json'
    assert link.stat().st_mode&0o777==0o600 and 'TOP_SECRET' in link.read_text()
    assert (run_dir/'manifest.json').is_file() and (run_dir/'external'/'result.json').is_file()
    everything=''.join(p.read_text() for p in run_dir.rglob('*') if p.is_file() and p!=link)
    assert 'TOP_SECRET' not in everything and 'TOP_SECRET' not in json.dumps(row)


def test_refresh_keeps_inconclusive_when_the_proxy_fails_and_retries_next_time(ready_env,monkeypatch):
    m=module();row=_submitted(ready_env,monkeypatch)
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env,'finished','success')])
    _proxy(monkeypatch,find={'status':'error','run':None,'matches':0,'pages':1,'error':'proxy or network failure (exit 3)'})
    row=wait(m.refresh(row['id'])['id'])
    assert row['state']=='completed'
    assert row['result']=={'state':'unavailable','reason':'report_fetch_failed','detail':'proxy or network failure (exit 3)'}
    entry=_entry(m,row)
    assert entry['verdict']=='inconclusive' and entry['provenance']['verdict_reason']=='no_verified_result_bundle'
    assert not (Path(entry['evidence_path'])/'manifest.json').exists()
    passing=[r for r in at4('properties.json')['data'] if r['status']=='Passing']
    _proxy(monkeypatch,props={'data':passing,'next_cursor':None})
    row=wait(m.refresh(row['id'])['id'])
    assert row['result']['state']=='verified' and row['result']['verdict']=='pass',row['result']
    assert _entry(m,row)['verdict']=='pass'


def test_refresh_does_not_rewrite_a_complete_bundle(ready_env,monkeypatch):
    m=module();row=_submitted(ready_env,monkeypatch)
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env,'finished','failure')])
    _proxy(monkeypatch);row=wait(m.refresh(row['id'])['id']);assert row['result']['state']=='verified'
    manifest=Path(_entry(m,row)['evidence_path'])/'manifest.json';before=manifest.read_bytes()
    calls=_proxy(monkeypatch,find={'status':'error','run':None,'matches':0,'pages':1,'error':'down'})
    row=wait(m.refresh(row['id'])['id'])
    assert row['result']['state']=='verified' and row['result']['verdict']=='fail' and calls==[] and manifest.read_bytes()==before


@pytest.mark.parametrize('find,reason',[
    ({'status':'ambiguous','run':None,'matches':2,'pages':1,'error':''},'antithesis_run_ambiguous'),
    ({'status':'not_found','run':None,'matches':0,'pages':2,'error':'searched the newest 100 runs; the proxy ignores paging cursors, older runs are unreachable'},'antithesis_run_not_found'),
])
def test_unresolved_antithesis_runs_leave_the_verdict_inconclusive(ready_env,monkeypatch,find,reason):
    m=module();row=_submitted(ready_env,monkeypatch)
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env,'finished','failure')])
    _proxy(monkeypatch,find=find);row=wait(m.refresh(row['id'])['id'])
    assert row['result']['state']=='unavailable' and row['result']['reason']==reason
    assert row['result']['detail']==find['error']
    assert _entry(m,row)['verdict']=='inconclusive'


def test_result_collection_failure_never_breaks_the_status_refresh(ready_env,monkeypatch):
    m=module();row=_submitted(ready_env,monkeypatch)
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env,'finished','failure')])
    def boom(*a,**k):raise RuntimeError('TOP_SECRET')
    monkeypatch.setattr(m.remote,'find_antithesis_run',boom)
    row=wait(m.refresh(row['id'])['id'])
    assert row['state']=='completed' and row['result']['state']=='unavailable' and row['result']['reason']=='report_fetch_failed'
    assert 'TOP_SECRET' not in json.dumps(row)


def test_git_mismatch_still_records_an_existing_attempt(ready_env,monkeypatch):
    """F34: a package that differs from the public commit blocks submission, but
    the exact MOOG attempt may already exist; readiness must still observe it so
    Refresh can link and judge the retained run."""
    m=module()
    monkeypatch.setattr(m,'verify_git',lambda *a:(_ for _ in ()).throw(PackageError('Public Git files or executable modes differ from the exported package.',status=422)))
    monkeypatch.setattr(m,'query_requester',lambda *a:{'checks':[{'id':'requester','state':'ok','detail':'Verified'}],'facts':[fact(ready_env,'finished','failure')]})
    row=check(ready_env)
    assert row['state']=='blocked' and row['existing_test_run_id']=='f'*64
    assert any(c['id']=='public_git' and c['state']=='error' and 'differ' in c['detail'] for c in row['checks'])
    assert 'TOP_SECRET' not in json.dumps(row)
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env,'finished','failure')])
    _proxy(monkeypatch)
    row=wait(m.refresh(row['id'])['id'])
    # Review #8: the run executed the public commit, not this package — no scenario identity is bound, so no verdict is attributed to one.
    assert row['state']=='completed' and row['result']['state']=='inconclusive' and row['result']['reason']=='result_bundle_scenario_unbound'
    assert not _entry(m,row).get('scenario')


def test_query_requester_uses_the_probe_verb_in_shim_mode(ready_env,monkeypatch):
    """F35: in shim mode the requester probe travels as a fixed verb instead of being refused."""
    m=module();captured=[]
    monkeypatch.setattr(m.remote,'control_shim_enabled',lambda:True)
    def ssh(config,command,timeout=None,dry_run=False,verb=None):
        captured.append((verb,command));return m.remote.CommandResult(0,'{"credentials":true,"public_key":"vkey-selected"}','','')
    monkeypatch.setattr(m.remote,'ssh_command',ssh)
    required=['requester_wallet_metadata','requester_vkey','requester_wallet_funded','github_profile_vkey','github_codeowners','moog_user_registered','moog_role_registered','moog_repo_whitelisted','facts_users','facts_roles','facts_white_list']
    monkeypatch.setattr(m,'_query_readiness',lambda *a,**k:{'checks':[{'id':i,'state':'ok','detail':'ok'} for i in required],'requester_public_key':'vkey-selected'})
    monkeypatch.setattr(m,'query_facts',lambda *a:[])
    result=ready_env['query_requester'](ready_env['config'],ready_env['target'])
    assert captured and captured[0][0]==('moog-requester-probe',) and "'wallet','info'" in captured[0][1]
    rows={c['id']:c['state'] for c in result['checks']}
    assert rows['transport_credentials']=='ok' and rows['selected_wallet']=='ok'


def test_non_terminal_or_tableless_results_are_not_frozen_into_a_bundle(ready_env,monkeypatch):
    """Review #1: MOOG says finished while the Antithesis row is still in progress (or the
    table is empty): nothing is written, the reason is retryable, and the next refresh collects."""
    m=module();row=_submitted(ready_env,monkeypatch)
    monkeypatch.setattr(m,'query_facts',lambda *a:[fact(ready_env,'finished','failure')])
    _proxy(monkeypatch,run=at4('run-in-progress.json'))
    row=wait(m.refresh(row['id'])['id'])
    assert row['result']=={'state':'unavailable','reason':'antithesis_run_not_terminal','detail':''}
    run_dir=Path(_entry(m,row)['evidence_path'])
    assert not (run_dir/'manifest.json').exists() and not (run_dir/'external'/'result.json').exists()
    _proxy(monkeypatch,props={'data':[],'next_cursor':None})
    row=wait(m.refresh(row['id'])['id'])
    assert row['result']['reason']=='properties_unavailable' and not (run_dir/'manifest.json').exists()
    _proxy(monkeypatch)
    row=wait(m.refresh(row['id'])['id'])
    assert row['result']['state']=='verified' and row['result']['verdict']=='fail'


def test_refresh_evidence_carries_the_matched_fact_without_the_report_link(ready_env,monkeypatch):
    """Review #3: the run page cross-checks the status and link receipts against the
    matched MOOG fact; the wizard's receipts carry it with value.url removed."""
    from profile_manager.data.operate_run import operate_run_detail
    m=module();row=_submitted(ready_env,monkeypatch)
    f=fact(ready_env,'finished','failure');f['value'].update(duration=1,**{'from':{'from':{'duration':1,'faults_enabled':False,'phase':'pending'},'phase':'accepted'}})
    monkeypatch.setattr(m,'query_facts',lambda *a:[f])
    _proxy(monkeypatch);row=wait(m.refresh(row['id'])['id'])
    entry=_entry(m,row);run_dir=Path(entry['evidence_path'])
    status=json.loads((run_dir/'external'/'status.json').read_text())
    assert [f['id'] for f in status['facts']]==['f'*64] and 'url' not in status['facts'][0]['value'] and status['report_available'] is True
    detail=operate_run_detail(row['dwarf_run_id'],runs_dir=run_dir.parent)
    assert detail['external']['receipt_errors']==[],detail['external']['receipt_errors']
    assert detail['external']['receipt_integrity']=='verified' and detail['external']['outcome']=='failure'
    assert 'TOP_SECRET' not in json.dumps(detail)


def test_plain_key_requester_probe_sees_the_saved_pat(ready_env,monkeypatch):
    """Review #5: load_settings() strips secrets; the non-shim probe must take the
    credential presence from the full config, not the stripped one."""
    from profile_manager.config import DeploymentConfig
    m=module();captured=[]
    monkeypatch.setattr(m.remote,'control_shim_enabled',lambda:False)
    full=DeploymentConfig.from_dict({'moog':{**ready_env['config'].moog,'github_pat':'ghp_saved'}})
    monkeypatch.setattr(m,'load_config',lambda:full)
    def ssh(config,command,timeout=None,dry_run=False,verb=None):
        captured.append(command);return m.remote.CommandResult(0,'{"credentials":true,"public_key":"vkey-selected"}','','')
    monkeypatch.setattr(m.remote,'ssh_command',ssh)
    monkeypatch.setattr(m,'_query_readiness',lambda *a,**k:{'checks':[],'requester_public_key':'vkey-selected'})
    monkeypatch.setattr(m,'query_facts',lambda *a:[])
    ready_env['query_requester'](ready_env['config'],ready_env['target'])
    assert 'credentials_configured=True' in captured[0] and 'ghp_saved' not in captured[0]


def _generated_receipt(staged, level, *, gaps=()):
    receipt=staged['receipt'];request=receipt['request'];request['package_source']='build'
    receipt['version_binding']={'status':'applied'}
    receipt['workload_binding']={'status':'applied' if level!='none' else 'recorded','source':'dwarf-workload'}
    primitives=[{'phase':'load','primitive':'runtime_multi_node_observation','status':'runs-with-note','note':''},
                {'phase':'faults','primitive':'fault_node_freeze','status':'delegated-fault','note':''}]
    if level=='partial':primitives.append({'phase':'probes','primitive':'runtime_target_health_and_progress','status':'unsupported','note':'docker exec'})
    receipt['capabilities']={'level':level,'reason':'no-runnable-load' if level=='none' else None,'runnable_load':level!='none',
                             'primitives':primitives,'gaps':list(gaps)}
    receipt['issues'].extend(gaps)
    return receipt


def test_generated_workload_with_declared_gaps_passes_generated_selections(ready_env,staged,monkeypatch):
    m=module()
    gaps=[{'code':'primitive-unsupported-on-antithesis','field':'scenario_id','kind':'implementation-gap','primitive':'runtime_target_health_and_progress','phase':'probes','message':'x'}]
    _generated_receipt(staged,'partial',gaps=gaps)
    monkeypatch.setattr(m,'read_source',lambda source:staged)
    row=check(ready_env);checks={c['id']:c for c in row['checks']}
    assert 'generated_selections' not in checks or checks['generated_selections']['state']!='error'
    assert checks['scenario_capabilities']['state']=='warn'
    assert checks['scenario_capabilities']['group']=='Package' and checks['scenario_capabilities']['label']=='Antithesis support'
    assert '1 unsupported' in checks['scenario_capabilities']['detail'] and '1 fault' in checks['scenario_capabilities']['detail']
    assert row['state']=='ready'


def test_generated_workload_full_support_is_ok(ready_env,staged,monkeypatch):
    m=module();_generated_receipt(staged,'full')
    monkeypatch.setattr(m,'read_source',lambda source:staged)
    row=check(ready_env);checks={c['id']:c for c in row['checks']}
    assert checks['scenario_capabilities']['state']=='ok' and row['state']=='ready'


def test_generated_workload_without_runnable_load_is_blocked(ready_env,staged,monkeypatch):
    m=module();_generated_receipt(staged,'none')
    monkeypatch.setattr(m,'read_source',lambda source:staged)
    row=check(ready_env);checks={c['id']:c for c in row['checks']}
    assert checks['scenario_capabilities']['state']=='error'
    assert 'No runnable load on Antithesis' in checks['scenario_capabilities']['detail']
    assert row['state']=='blocked'


def test_library_scenario_is_blocked_with_no_faultable_sut(ready_env,staged,monkeypatch):
    m=module();receipt=_generated_receipt(staged,'none');receipt['capabilities']['reason']='no-faultable-sut'
    receipt['workload_binding']={'status':'recorded','source':'none'}
    monkeypatch.setattr(m,'read_source',lambda source:staged)
    row=check(ready_env);checks={c['id']:c for c in row['checks']}
    assert 'No faultable SUT' in checks['scenario_capabilities']['detail'] and row['state']=='blocked'


def test_reserve_records_the_capability_classification(ready_env,staged,monkeypatch):
    m=module();receipt=staged['receipt']
    receipt['capabilities']={'level':'partial','reason':None,'runnable_load':True,'primitives':[],
                             'gaps':[{'kind':'implementation-gap','code':'primitive-unsupported-on-antithesis','field':'scenario_id',
                                      'primitive':'runtime_tracer_capture','phase':'load','message':'runtime_tracer_capture: docker'}]}
    monkeypatch.setattr(m,'read_source',lambda source:staged)
    row=_submitted(ready_env,monkeypatch)
    entry=_entry(m,row)
    recorded=entry['provenance']['antithesis_capabilities']
    assert recorded['level']=='partial' and recorded['reason'] is None
    assert [g['primitive'] for g in recorded['gaps']]==['runtime_tracer_capture']
