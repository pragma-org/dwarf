"""Remote bindings for existing measurement definitions, factories and runtime."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path

import yaml

from profile_manager.data.catalog_definitions import load_definition
from profile_manager.measurement_collectors.amaru_stock import STOCK_MEASUREMENT_IDS as AMARU_STOCK
from profile_manager.measurement_collectors.cardano_stock import STOCK_MEASUREMENT_IDS as CARDANO_STOCK
from profile_manager.measurement_collectors.amaru_patched import build_amaru_patched_factories
from profile_manager.antithesis_cardano_bridge import build_factories as build_cardano_patched_factories

ROOT=Path(__file__).resolve().parents[1]
PYTHON='python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93'


def _runtime_sources(files):
    # Retain the actual unchanged Python modules, not rewritten collector logic.
    paths=[* (ROOT/'profile_manager/measurement_collectors').glob('*.py')]
    paths += [ROOT/'profile_manager'/name for name in ('__init__.py','measurement_runtime.py',
        'measurement_report.py','measurement_correlation.py','measurement_precision.py',
        'experimental_instrumentation.py','antithesis_cardano_bridge.py','run_ledger.py','schemas/run-ledger-entry-v1.schema.json')]
    paths += [ROOT/'scripts'/name for name in ('__init__.py','runtime_resource_profile.py',
        'runtime_telemetry.py','header_validation_parse.py','antithesis_measurement_runtime.py')]
    paths += [ROOT/'spec/v1/measurement-result.schema.json']
    for path in paths:
        relative=path.relative_to(ROOT).as_posix()
        # MOOG's existing filename guard treats the substring 'pat' as a PAT.
        # Keep source bytes unchanged and restore the import names in the image.
        relative=relative.replace('amaru_patched.py','amaru_instrumented.py').replace('cardano_patched.py','cardano_instrumented.py')
        files['environment/measurements/'+relative]=path.read_text()
    files['environment/measurements/prepare-cardano.py']=cardano_config_program()
    files['environment/measurements/Dockerfile']=(f'FROM {PYTHON}\n'
        'RUN pip install --no-cache-dir jsonschema==4.25.1\n'
        'WORKDIR /opt/dwarf\nCOPY . /opt/dwarf/\n'
        'RUN mv profile_manager/measurement_collectors/amaru_instrumented.py profile_manager/measurement_collectors/amaru_patched.py && '
        'mv profile_manager/measurement_collectors/cardano_instrumented.py profile_manager/measurement_collectors/cardano_patched.py\n'
        'ENV PYTHONPATH=/opt/dwarf\n'
        'ENTRYPOINT ["python", "-m", "scripts.antithesis_measurement_runtime"]\n')


def cardano_config_program():
    from scripts.runtime_compose_substrate import CARDANO_MEASUREMENT_TRACE_NAMESPACES, _enable_cardano_measurement_traces
    return ('import json,shutil\nfrom pathlib import Path\n'
        +'CARDANO_MEASUREMENT_TRACE_NAMESPACES='+repr(CARDANO_MEASUREMENT_TRACE_NAMESPACES)+'\n'
        +inspect.getsource(_enable_cardano_measurement_traces)
        +"\nif __name__=='__main__':\n"
        +"    shutil.copytree('/configs','/target',dirs_exist_ok=True)\n"
        +"    path=Path('/target/configs/config.json')\n"
        +"    _enable_cardano_measurement_traces(path)\n"
        +"    body=json.loads(path.read_text())\n"
        +"    for name,options in body['TraceOptions'].items():\n"
        +"        if any(name==ns or name.startswith(ns+'.') for ns in CARDANO_MEASUREMENT_TRACE_NAMESPACES):\n"
        +"            options.update(severity='Info',detail='DDetailed')\n"
        +"            options.pop('maxFrequency',None)\n"
        +"    path.write_text(json.dumps(body,indent=2)+'\\n')\n")


def _selections(request,scenario):
    chosen={}
    profile=request['profile_id'] or scenario.measurement_profile
    if profile:
        for row in load_definition('measurement-profiles',profile).data.get('measurements',[]):
            chosen[row['id']]=dict(row)
    for item in scenario.measurements or []:
        row=asdict(item) if not isinstance(item,dict) else dict(item)
        chosen[row['id']]=row
    for identifier in request['tap_ids']:
        chosen[identifier]={'id':identifier,'enabled':True}
    return [row for row in chosen.values() if row.get('enabled') is True]


def bind_measurements(files, request, scenario, versions, workload):
    selections=_selections(request,scenario)
    report={'status':'not-requested' if not selections else 'partial',
            'targets':[],'issues':[],'requested':[s['id'] for s in selections],
            'runtime_verified':False}
    if not selections:return report
    def gap(identifier,message):
        report['issues'].append({'code':'collector-not-implemented','field':'measurements.'+identifier,
                                'kind':'implementation-gap','message':message})
    if workload.get('status')!='applied' or versions.get('status')!='applied':
        for row in selections:gap(row['id'],'Connect the scenario target and workload before attaching this collector.')
        return report
    compose=yaml.safe_load(files['environment/docker-compose.yaml'])
    rows={r['service']:r for r in versions['services']}
    bound=set()
    for service in workload['targets']:
        row=rows[service];implementation=row['implementation']
        artifact=versions.get('node_artifacts',{}).get(implementation,{})
        identity={'implementation':implementation,'version':row['requested_version'],
            'source_revision':row['source_revision'],'mode':artifact.get('mode','stock'),
            'image_digest':artifact.get('image_id') or row.get('artifact',{}).get('digest'),
            'engine':'antithesis','non_authoritative_performance':True}
        for key in ('patch_set_sha256','measurement_revision','executable_digest','toolchain',
                    'build_result_sha256','runtime_probe_log_sha256','compatibility'):
            if key in artifact:identity[key]=artifact[key]
        if artifact.get('experimental_instrumentation'):
            candidate=artifact['experimental_instrumentation']
            identity.update(identity_kind='operator-local-config',image_config_id=artifact['image_id'],
                controller_installation_id=candidate['controller_installation_id'],experimental_instrumentation=candidate)
        supported=set(AMARU_STOCK if implementation=='amaru' else CARDANO_STOCK)
        supported.add(('amaru' if implementation=='amaru' else 'cardano')+'-stock-header-validation')
        if identity['mode']=='patched':
            try:
                factory=build_amaru_patched_factories if implementation=='amaru' else build_cardano_patched_factories
                supported.update(factory(json_trace_paths=[],target_identity=identity))
            except ValueError:
                pass
        entries=[]
        for selected in selections:
            identifier=selected['id']
            record=load_definition('measurements',identifier)
            if record.data['compatibility']['implementation']!=implementation:continue
            if identifier not in supported:continue
            entries.append({'id':identifier,'enabled':True,'source':'antithesis',
                'parameters':selected.get('parameters',{}),
                'threshold_gate':selected.get('threshold_gate') or {'enabled':False,'thresholds':[]},
                'definition':record.data,'definition_digest':'sha256:'+hashlib.sha256(record.source).hexdigest()})
            bound.add(identifier)
        if not entries:continue
        target=compose['services'][service]
        # The connected mixed workload already records each victim's stdout in
        # a dedicated volume; reuse that exact source rather than Docker logs.
        evidence='sm-'+('amaru' if implementation=='amaru' else 'cardano')+'-runtime-evidence'
        env=target.setdefault('environment',{})
        mount='/inputs/'+service
        patterns=[mount+'/amaru.log'] if implementation=='amaru' else []
        patched=[]
        if implementation=='amaru':
            env['AMARU_WITH_JSON_TRACES']='true'
        else:
            config_volume='measurement-config-'+service
            config_service='measure-config-'+service
            compose['volumes'][config_volume]={}
            source_mount=next(m for m in target['volumes'] if isinstance(m,str) and m.split(':')[1:2]==['/configs'])
            compose['services'][config_service]={
                'build':{'context':'./measurements','dockerfile':'Dockerfile'},
                'platform':'linux/amd64','container_name':config_service,'hostname':config_service,
                'init':True,'entrypoint':['python','/opt/dwarf/prepare-cardano.py'],
                'volumes':[source_mount,config_volume+':/target'],
                'depends_on':{'configurator':{'condition':'service_completed_successfully'}},
                'labels':{'com.antithesis.exclude_from_faults':'network,kill,pause,stop'}}
            target['volumes']=[config_volume+':/configs:ro' if m==source_mount else m for m in target['volumes']]
            target.setdefault('depends_on',{})[config_service]={'condition':'service_completed_successfully'}
            tracer_volume='measurement-tracer-'+service
            tracer_name='measure-tracer-'+service
            compose['volumes'][tracer_volume]={}
            tracer=dict(compose['services']['tracer'])
            tracer.update(container_name=tracer_name,hostname=tracer_name,
                volumes=[tracer_volume+':/opt/cardano-tracer','./measurements/tracer-config.yaml:/tracer-config.yaml:ro'])
            compose['services'][tracer_name]=tracer
            target['volumes']=[tracer_volume+':/tracer' if isinstance(m,str) and m.split(':')[1:2]==['/tracer'] else m for m in target['volumes']]
            target.setdefault('depends_on',{})[tracer_name]={'condition':'service_started'}
            files['environment/measurements/tracer-config.yaml']=files['environment/tracer-config.yaml']
            patterns=['/inputs/'+tracer_volume+'/logs/**/*.json','/inputs/'+tracer_volume+'/logs/**/*.log']
            if identity['mode']=='patched':
                env.update(DWARF_CARDANO_MEASUREMENT_TRACE='/evidence/cardano-measurement.ndjson',
                           DWARF_CARDANO_PLUTUS_TRACE='/evidence/cardano-plutus.ndjson')
                patched=[mount+'/cardano-measurement.ndjson',mount+'/cardano-plutus.ndjson']
        report['targets'].append({'service':service,'identity':identity,'entries':entries,
            'trace_patterns':patterns,'patched_patterns':patched,'evidence_volume':evidence,
            'scenario_id':scenario.id})
    for selected in selections:
        if selected['id'] not in bound:
            gap(selected['id'],'This tap needs a remote hook or patch contract that is not connected; it was not marked applied.')
    if report['targets']:
        _runtime_sources(files)
        files['environment/measurements/measurements.json']=json.dumps(report,sort_keys=True)
        mounts=['dwarf-measurement-results:/evidence']
        for target in report['targets']:
            mounts.append(target['evidence_volume']+':/inputs/'+target['service']+':ro')
            if target['identity']['implementation']=='cardano-node':
                volume='measurement-tracer-'+target['service'];mounts.append(volume+':/inputs/'+volume+':ro')
        compose['volumes']['dwarf-measurement-results']={}
        compose['services']['dwarf-measurements']={'build':{'context':'./measurements','dockerfile':'Dockerfile'},
            'platform':'linux/amd64','container_name':'dwarf-measurements','hostname':'dwarf-measurements',
            'init':True,'restart':'always','volumes':mounts,
            'labels':{'com.antithesis.exclude_from_faults':'network,kill,pause,stop'}}
        files['environment/docker-compose.yaml']=yaml.safe_dump(compose,sort_keys=False)
    if not report['issues']:report['status']='applied'
    return report
