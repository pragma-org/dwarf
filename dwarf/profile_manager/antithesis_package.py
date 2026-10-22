"""In-memory Antithesis package previews; no execution, builds or publication."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import yaml

from profile_manager import antithesis_generator as native
from profile_manager import antithesis_topology as topology
from profile_manager import antithesis_harness as harness
from profile_manager import antithesis_workload_binding as workload
from profile_manager.antithesis_version_binding import bind_native_versions
from profile_manager.data.catalog_definitions import CatalogError, load_definition
from profile_manager.scenario import ScenarioValidationError, scenario_from_body

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,255}')
_SHA = re.compile(r'sha256:[0-9a-f]{64}')
_BASE_SERVICES = {'configurator', 'tracer', 'p1', 'p2', 'p3', 'relay1', 'relay2',
                  'tracer-sidecar', 'log-tailer', 'dwarf-adversary'}


class PackageError(ValueError):
    def __init__(self, message, *, field='request', status=400):
        super().__init__(message)
        self.field, self.status = field, status


def _json(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n'


def _sha(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def _shape(value, keys, field):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise PackageError('Unsupported fields for ' + field + '.', field=field)


def _identifier(value, field, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise PackageError('Choose a valid ' + field + '.', field=field)


def _validate(value, *, preparation=False):
    _shape(value, {'package_source','topology','topology_digest','versions','scenario_id',
                   'measurements','duration_hours','faults'}, 'request')
    if value['package_source'] not in (('build','url','existing') if preparation else ('build',)):
        raise PackageError('Unsupported package source.' if preparation else
                           'Use Prepare package for package URL or existing-asset import.', field='package_source', status=422)
    _identifier(value['scenario_id'], 'scenario_id', optional=preparation and value['package_source'] != 'build')
    if type(value['duration_hours']) is not int or not 1 <= value['duration_hours'] <= 1000000:
        raise PackageError('Requested duration must be a positive whole number of hours.', field='duration_hours')
    if value['faults'] not in ('on','off'):
        raise PackageError('Choose on or off for faults.', field='faults')
    v = value['versions']
    _shape(v, {'policy','versions','build_variant','experimental_intent'} | ({'artifacts'} if isinstance(v,dict) and 'artifacts' in v else set()), 'versions')
    if 'artifacts' in v:
        from profile_manager.antithesis_artifacts import selected_artifacts
        try:
            selected_artifacts(v['artifacts'],v['build_variant'])
        except (ValueError,OSError) as exc:
            raise PackageError(str(exc),field='versions.artifacts',status=422) from exc
    if v['policy'] not in ('profile-default','latest-stable','latest-confirmed','exact') or v['build_variant'] not in ('stock','instrumented') or type(v['experimental_intent']) is not bool:
        raise PackageError('Invalid version intent.', field='versions')
    if not isinstance(v['versions'], dict) or not set(v['versions']) <= {'cardano-node','amaru'}:
        raise PackageError('Invalid version selections.', field='versions.versions')
    for version in v['versions'].values():
        _identifier(version, 'version')
    if v['policy'] != 'exact' and v['versions']:
        raise PackageError('Exact version selections require the exact policy.', field='versions')
    m = value['measurements']
    _shape(m, {'profile_id','tap_ids'}, 'measurements')
    _identifier(m['profile_id'], 'measurement_profile', optional=True)
    if not isinstance(m['tap_ids'], list) or len(m['tap_ids']) > 128:
        raise PackageError('Select at most 128 measurement taps.', field='measurements.tap_ids')
    for tap in m['tap_ids']:
        _identifier(tap, 'measurement_tap')
    if len(set(m['tap_ids'])) != len(m['tap_ids']):
        raise PackageError('Measurement taps must be unique.', field='measurements.tap_ids')
    t = value['topology']
    if not isinstance(t, dict) or any(type(item) not in (str,int) for item in t.values()):
        raise PackageError('Invalid topology request.', field='topology')
    digest = value['topology_digest']
    if digest is not None and (not isinstance(digest,str) or not _SHA.fullmatch(digest)):
        raise PackageError('Invalid topology digest.', field='topology_digest')


def _engine_handoff(files, *, renderer_invoked, version_binding, workload_binding=None):
    """Describe this preview's output, without resolving or executing more work."""
    compose_path = 'environment/docker-compose.yaml'
    environment_emitted = compose_path in files
    compose = yaml.safe_load(files[compose_path]) if environment_emitted else {}
    services = compose.get('services', {})
    command = services.get('dwarf-adversary', {}).get('command', [])
    # Read the emitted command, not a second mapping of the selected scenario.
    def argument(flag):
        if isinstance(command, list) and flag in command:
            index = command.index(flag) + 1
            if index < len(command):
                return command[index]
        return None

    control = version_binding.get('source') == 'control-harness'
    inputs = []
    def row(field, status, detail, evidence=()):
        inputs.append({'field':field, 'status':status, 'detail':detail, 'evidence':list(evidence)})

    row('package_source', 'applied',
        'Build with Dwarf selects file generation for inspection. No images are built and no package is prepared.',
        ['package-manifest.json'])
    row('scenario_id', 'partial' if environment_emitted else 'recorded',
        'The emitted adversary uses part of this scenario; complete operations and observed assertions are missing.'
        if environment_emitted else 'The scenario is retained; no scenario environment was emitted.',
        ['inputs/scenario.yaml'] + ([compose_path] if environment_emitted else []))
    row('topology', 'applied' if environment_emitted else 'recorded',
        'Scenario default uses the native generator environment shown below.'
        if environment_emitted else 'No scenario topology was applied. Any topology-preview files are a separate, unmerged rig.',
        [compose_path] if environment_emitted else ['package-manifest.json'])
    bound = version_binding['status'] in ('applied','partial')
    row('versions', version_binding['status'],
        'The selected stock release is bound to relay2 in the emitted Compose. Supporting images are unchanged; runtime identity and compatibility remain unverified.'
        if bound else 'Requested node versions were not applied. Inspect version_binding and the listed gaps; template images remain unchanged.',
        ['package-manifest.json'] + ([compose_path] if bound else []))
    row('build_variant', 'applied' if bound else 'recorded',
        'Stock selected the declared OCI release artifact for the target. No image build was performed.'
        if bound else 'The stock/instrumented choice has not selected or produced a build.',
        ['package-manifest.json'])
    row('experimental_intent', 'recorded',
        'Experimental intent is preserved; identity and compatibility are not evaluated here.',
        ['package-manifest.json'])
    measurement_files = sorted(name for name in files if name.startswith('inputs/measurement'))
    row('measurements', 'recorded',
        'Measurement selections and scenario defaults are retained; no requested tap collectors are installed.',
        ['inputs/scenario.yaml', 'package-manifest.json'] + measurement_files)
    row('duration_hours', 'recorded',
        'Duration is submission intent only; no MOOG request has been made.',
        ['package-manifest.json'])
    row('faults', 'recorded',
        'Fault on/off is submission intent only; existing service fault-exclusion labels are unchanged.',
        ['package-manifest.json'])
    if control:
        rows = {r['field']:r for r in inputs}
        rows['scenario_id'].update(status='recorded', detail='The control harness is prepared; the selected scenario workload and observing assertions are not connected.')
        rows['topology'].update(status='applied', detail='The existing Amaru/mixed control harness supplies the environment. It is not a completed scenario translation.')
        rows['versions']['detail'] = ('Selected versions are bound to the declared harness targets. Amaru reports its target binary separately from the fixed bootstrap wrapper and support tools; runtime behavior remains unverified.'
            if bound else 'Requested versions were not applied. The control harness retains its baseline images; inspect the binding gaps.')
        rows['build_variant']['detail'] = ('Stock selects the declared target artifact through the existing executable adapter. Bootstrap support tools remain pinned; no measurement patches or image builds were applied.'
            if bound else 'The requested stock/instrumented selection has no applied target binding.')
    if workload_binding and workload_binding['status'] == 'applied':
        rows['scenario_id'].update(status='partial', detail='The existing paired mini-protocol workload, observer and test-command build sources are connected. Local probe orchestration remains untranslated; execution is unverified.', evidence=[compose_path, 'environment/miniprotocol/workload/Dockerfile', 'package-manifest.json'])
        rows['topology']['detail'] = 'The mixed control harness includes dedicated mini-protocol victims, paired fuzzers and the existing observer inputs.'
    return {
        'workload_binding':workload_binding,
        'renderer':'control-harness' if control else 'native-scenario' if renderer_invoked else None,
        'renderer_invoked':renderer_invoked,
        'environment_emitted':environment_emitted,
        'adversary':{'protocol':argument('--protocol'), 'shape':argument('--cbor-shape')}
            if 'dwarf-adversary' in services else None,
        'services':[{'name':name, 'image':service.get('image'), 'command':service.get('command')}
                    for name,service in sorted(services.items())],
        'config_files':sorted(name for name in files if name.startswith('environment/') and name != compose_path),
        'inputs':inputs,
    }


def preview_package(value):
    _validate(value)
    files, issues, images = {}, [], []
    renderer_invoked = False

    def gap(code, field, message):
        issues.append({'code':code, 'field':field, 'kind':'implementation-gap', 'message':message})

    def definition(catalog, identifier, filename):
        try:
            record = load_definition(catalog, identifier)
            if len(record.source) > 1024*1024:
                raise PackageError('Definition is too large to preview.', field=catalog)
            files[filename] = record.source.decode('utf-8')
            return record
        except (CatalogError, UnicodeError, OSError) as exc:
            raise PackageError('Selected ' + catalog + ' definition is unavailable: ' + identifier, field=catalog, status=422) from exc

    record = definition('scenarios',value['scenario_id'],'inputs/scenario.yaml')
    try:
        scenario = scenario_from_body(record.source)
    except ScenarioValidationError as exc:
        raise PackageError('Scenario could not be parsed.', field='scenario_id', status=422) from exc
    try:
        resolved = topology.resolve_topology(value['topology'])
    except topology.TopologyError as exc:
        raise PackageError(str(exc),field='topology.'+exc.field,status=exc.status) from exc

    control = None
    if value['topology']['source'] == 'scenario-default':
        control = harness.resolve_default(scenario)
    elif resolved['state'] == 'resolved' and resolved['request']['template_id'] in {'amaru-control-v1','mixed-control-v1'}:
        if resolved['topology_digest'] != value['topology_digest']:
            raise PackageError('Topology changed. Resolve it again before generating the package.', field='topology_digest', status=409)
        control = resolved

    if control:
        renderer_invoked = True
        try:
            harness.emit(files, control)
        except topology.TopologyError as exc:
            raise PackageError(str(exc), field='topology', status=exc.status) from exc
    elif value['topology']['source'] != 'scenario-default':
        if resolved['state'] == 'resolved':
            if resolved['topology_digest'] != value['topology_digest']:
                raise PackageError('Topology changed. Resolve it again before generating the package.', field='topology_digest',status=409)
            files['topology-preview/docker-compose.yaml'] = yaml.safe_dump(resolved['compose'],sort_keys=False)
            images.extend({'service':name, 'reference':service.get('image'),
                'source':'topology-preview/docker-compose.yaml','identity':'recorded-only','built':False}
                for name,service in resolved['compose']['services'].items())
            for name, digest in resolved['input_digests'].items():
                content = (topology.ASSET_ROOT/name).read_bytes()
                if _sha(content) != digest:
                    raise PackageError('Topology inputs changed during generation. Retry.',field='topology',status=409)
                files['topology-preview/'+name] = content.decode()
            # Keep the compiled projection; the source docker-compose input is provenance only.
            files['topology-preview/source-docker-compose.yaml'] = files.pop('topology-preview/docker-compose.yaml')
            files['topology-preview/docker-compose.yaml'] = yaml.safe_dump(resolved['compose'],sort_keys=False)
        gap('topology-scenario-merge-not-implemented','topology','The requested topology and scenario are not connected by this generator. Any topology-preview files are a separate control rig, not the requested test.')
    else:
        try:
            renderer_invoked = True
            generated = native.render_bundle(scenario,registry='')
            compose = yaml.safe_load(generated.files['config/docker-compose.yaml'])
            if not _BASE_SERVICES <= set(compose['services']):
                raise PackageError('The native service inventory changed; the package adapter needs an update.',status=503)
            compose = {key:compose[key] for key in ('services','volumes','networks') if key in compose}
            compose['services'] = {name:service for name,service in compose['services'].items() if name in _BASE_SERVICES}
            for network in compose.get('networks',{}).values():
                if isinstance(network,dict) and network.get('internal') == '${INTERNAL_NETWORK}':
                    network['internal'] = False
            # Only generated configuration inputs are reused. Do not ship the old
            # unconditional condition:true composer as an implemented assertion.
            for name in ('testnet.yaml','tracer-config.yaml','relay-dwarf-topology.json','relay-eclipse-topology.json'):
                if name in generated.files:
                    files['environment/'+name] = generated.files[name]
            files['environment/relay-topology.json'] = (native.ARCHETYPE_COMPOSE.parent/'relay-topology.json').read_text()
            for name, service in compose['services'].items():
                for mount in service.get('volumes',[]):
                    origin = mount.split(':',1)[0]
                    if origin.startswith('./') and 'environment/'+origin[2:] not in files:
                        raise PackageError('Native Compose needs an input that this adapter does not package.',field='scenario_id',status=503)
                if not set(service.get('depends_on',{})) <= set(compose['services']):
                    raise PackageError('Native Compose has an unrepresented service dependency.',status=503)
                images.append({'service':name, 'reference':service.get('image'),
                    'source':'environment/docker-compose.yaml','identity':'recorded-only', 'built':False})
            files['environment/docker-compose.yaml'] = yaml.safe_dump(compose,sort_keys=False)
            files['workload/generator-metadata.json'] = _json({'status':'planning-only', 'native_generator':json.loads(generated.files['dwarf-manifest.json'])})
            gap('assertion-observation-not-implemented','scenario_id','The adversary is included, but result-observing scenario assertions and test-command packaging are not implemented. The legacy unconditional assertion driver was omitted.')
            gap('scenario-operation-mapping-incomplete','scenario_id','The native adversary is a partial translation. The complete scenario phases, probes, parameters and requested assertion semantics have not been implemented; inputs/scenario.yaml preserves them.')
        except native.GeneratorError as exc:
            gap('scenario-generator-not-implemented','scenario_id','No package adapter implements this selected scenario: '+str(exc))

    v = value['versions']
    version_binding = (harness.bind_versions(files, v, control,
        scenario_default=scenario.target.get('version') if value['topology']['source']=='scenario-default' else None)
        if control else bind_native_versions(files, v, scenario))
    workload_binding = workload.bind_workload(files, scenario, control, version_binding)
    issues.extend(workload_binding['issues'])
    if control and workload_binding['status'] != 'applied':
        gap('scenario-harness-workload-not-implemented', 'scenario_id', 'The control harness is prepared, but this scenario workload and its observing assertions are not connected.')
    issues.extend(version_binding['issues'])
    if version_binding['status'] == 'recorded' and v['policy'] != 'profile-default':
        gap('version-binding-not-implemented','versions','This requested version intent could not be applied. See the specific binding gaps; no substitute release was chosen.')
    if v['build_variant'] == 'instrumented' and version_binding['status'] != 'applied' and not any(i['code']=='patched-build-not-implemented' for i in issues):
        gap('patched-build-not-implemented','versions.build_variant','The requested patched build has not been generated.')
    from profile_manager.antithesis_measurements import bind_measurements
    measurement_binding=bind_measurements(files,value['measurements'],scenario,version_binding,workload_binding)
    issues.extend(measurement_binding['issues'])
    if 'environment/docker-compose.yaml' in files:
        effective = yaml.safe_load(files['environment/docker-compose.yaml'])['services']
        images = [image for image in images if image['source'] != 'environment/docker-compose.yaml']
        images.extend({'service':name, 'reference':service.get('image'),
            'source':'environment/docker-compose.yaml', 'identity':'recorded-only', 'built':False}
            for name,service in effective.items())
    m = value['measurements']
    if m['profile_id']:
        definition('measurement-profiles',m['profile_id'],'inputs/measurement-profile.yaml')
    definition_names = {}
    for tap in m['tap_ids']:
        # MOOG's existing filename guard matches the substring 'pat'. Keep
        # definition IDs/content unchanged while staging a safe snapshot name.
        filename = 'inputs/measurements/'+tap.replace('-patched-','-instrumented-')+'.yaml'
        if filename in definition_names and definition_names[filename] != tap:
            raise PackageError('Measurement snapshot filenames collide.',field='measurements.tap_ids')
        definition_names[filename] = tap
        definition('measurements',tap,filename)


    gap('image-build-publication-deferred','package_source','Images have not been built or published. The public GitHub publication trigger and submission will be connected later.')
    handoff = _engine_handoff(files, renderer_invoked=renderer_invoked, version_binding=version_binding, workload_binding=workload_binding)
    for item in handoff['inputs']:
        if item['field']=='measurements' and measurement_binding['status']=='applied':
            item.update(status='applied',detail='Existing DWARF collectors are bound to target-specific traces; periodic results are retained and logged. Runtime data availability remains unverified.')
    files['README.md'] = ('# Antithesis package preview — INCOMPLETE\n\n'
        'Generated for review only. This is not a prepared or runnable test receipt.\n'
        'Read package-manifest.json for requested intent, engine_handoff and missing implementations.\n'
        'Applied means present in preview output, not runtime verified. Recorded means intent only.\n'
        'environment/ contains the emitted native environment or control harness, identified in engine_handoff; topology-preview/ '
        'is an unmerged control rig when an override was requested.\n'
        'inputs/ preserves definitions. workload/ metadata is planning data, not observed assertions.\n'
        'Image references are recorded for traceability, not checked for compatibility or eligibility.\n'
        'Duration and fault settings are submission intent only. No containers were started.\n')
    manifest = {'schema_version':1,'backend':'antithesis','state':'incomplete','execution_ready':False,
        'request':value,'issues':issues,'images':images,'compatibility':'not-evaluated',
        'engine_handoff':handoff, 'version_binding':version_binding, 'workload_binding':workload_binding, 'measurement_binding':measurement_binding,
        'generator_source_digest':_sha(Path(harness.__file__ if control else native.__file__).read_bytes()),
        'files':{name:_sha(text.encode()) for name,text in sorted(files.items())}}
    files['package-manifest.json'] = _json(manifest)
    inventory = [{'path':name,'sha256':_sha(text.encode()),'bytes':len(text.encode())} for name,text in sorted(files.items())]
    if sum(item['bytes'] for item in inventory) > 4*1024*1024:
        raise PackageError('Generated preview exceeds the package size limit.',status=413)
    digest = _sha(_json(inventory).encode())
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,text in sorted(files.items()):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644 << 16
            archive.writestr(info,text)
    return {'state':'incomplete','execution_ready':False,'issues':issues,'files':files,
        'inventory':inventory,'package_digest':digest,'images':images,'engine_handoff':handoff,
        'version_binding':version_binding, 'workload_binding':workload_binding, 'measurement_binding':measurement_binding,
        'archive_base64':base64.b64encode(output.getvalue()).decode(),
        'archive_name':'antithesis-preview-'+digest.split(':')[1][:12]+'.zip'}
