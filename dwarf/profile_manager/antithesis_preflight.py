"""Bounded static checks of Antithesis assets, independent of Local /run."""
from __future__ import annotations

import hashlib
import posixpath
from pathlib import Path
import re
import tempfile

import yaml

from profile_manager import antithesis_preparation as prep
from profile_manager.antithesis_validation import moog_fault_exclusion_errors

MOOG_COMMIT = '2642c60b5598562294e553fb3491fc5228033c91'
INTERPOLATION = re.compile(r'\$(?:\{|[A-Za-z_])')
IMAGE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._/:@+-]{0,511}')
HOST = re.compile(r'(?=.{1,253}\Z)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?')


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        # Detect duplicate explicit keys before flattening Compose merge anchors.
        keys = set()
        for key, _ in node.value:
            if key.tag == 'tag:yaml.org,2002:merge':
                continue
            value = self.construct_object(key, deep=deep)
            if not isinstance(value, str) or value in keys:
                raise ValueError('Compose keys must be unique strings')
            keys.add(value)
        return super().construct_mapping(node, deep=deep)


def load_compose(data):
    if len(data) > 1024 * 1024:
        raise ValueError('Compose exceeds 1 MiB')
    data.decode('utf-8')
    prep._bound_compose(data)
    document = yaml.load(data, Loader=UniqueLoader)
    if not isinstance(document, dict) or not isinstance(document.get('services'), dict) or not document['services']:
        raise ValueError('Compose needs a non-empty services mapping')
    return document


def valid_image(value):
    if not IMAGE.fullmatch(value): return False
    if '@' in value:
        value, digest = value.rsplit('@', 1)
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest): return False
    parts = value.split('/')
    last = parts[-1]
    if ':' in last:
        last, tag = last.rsplit(':', 1)
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag): return False
        parts[-1] = last
    if len(parts)>1 and ('.' in parts[0] or ':' in parts[0] or parts[0]=='localhost'):
        if not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?',parts.pop(0)):return False
    return all(re.fullmatch(r'[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*',part) for part in parts)


def service_shape_errors(service):
    errors=[]
    for key in ('command','entrypoint'):
        value=service.get(key)
        if value is not None and not (isinstance(value,str) or isinstance(value,list) and all(isinstance(x,str) for x in value)):
            errors.append(key+' must be a string or string list.')
    labels=service.get('labels',{})
    if not isinstance(labels,(dict,list)) or isinstance(labels,list) and any(not isinstance(x,str) for x in labels):
        errors.append('labels must be a mapping or string list.')
    elif isinstance(labels,list):
        keys=[x.split('=',1)[0] for x in labels]
        if len(set(keys))!=len(keys):errors.append('Label keys must be unique.')
    dependencies=service.get('depends_on',{})
    if isinstance(dependencies,dict):
        for dependency in dependencies.values():
            if not isinstance(dependency,dict) or dependency.get('condition') not in ('service_started','service_healthy','service_completed_successfully'):
                errors.append('depends_on needs valid dependency conditions.')
    networks=service.get('networks',{})
    if isinstance(networks,dict) and any(x is not None and not isinstance(x,dict) for x in networks.values()):
        errors.append('Network attachment settings must be mappings.')
    mounts=service.get('volumes',[])
    if not isinstance(mounts,list):errors.append('volumes must be a list.')
    if isinstance(mounts,list):
        for mount in mounts:
            if isinstance(mount,str):
                parts=mount.split(':');target=parts[1] if len(parts)>1 else parts[0]
                if len(parts)>3 or not target.startswith('/') or len(parts)>1 and not parts[0]:errors.append('Volume needs a nonempty source and absolute target.')
            elif isinstance(mount,dict):
                target=mount.get('target');source=mount.get('source')
                if not isinstance(target,str) or not target.startswith('/') or (source is not None and (not isinstance(source,str) or not source)):
                    errors.append('Volume needs an absolute target and a string source.')
    return errors


def unresolved(value):
    return isinstance(value, str) and bool(INTERPOLATION.search(value.replace('$$', '').replace('${INTERNAL_NETWORK}', 'false')))


def add_check(report, contract, code, state, detail, location='docker-compose.yaml'):
    report['checks'].append(dict(contract=contract, code=code, state=state, location=location, detail=detail))


def finish(report):
    report['counts'] = {state: sum(row['state'] == state for row in report['checks'])
                        for state in ('pass', 'fail', 'warn', 'not_checked')}
    report['state'] = 'blocked' if report['counts']['fail'] else 'review'
    return report


def inspect_package(files):
    report = dict(schema_version=1, contract='antithesis/spec.md C01–C15', moog_source_commit=MOOG_COMMIT,
                  inventory_sha256=hashlib.sha256(prep._json(prep._inventory(files)).encode()).hexdigest(),
                  checks=[], images=[], execution_ready=False, submission_ready=False)
    def check(contract, code, state, detail, location='docker-compose.yaml'):
        add_check(report, contract, code, state, detail, location)
    for contract, code, detail in [
        ('C01','compose-schema','Full Docker Compose schema resolution is not performed by this bounded static checker.'),
        ('C02','binary-runtime','Actual binaries and offline dependencies require execution evidence.'),
        ('C08','semantic-readiness','Readiness conditions require a fresh execution; declarations alone are insufficient.'),
        ('C09','mount-permissions','Volume ownership and producer/consumer behavior require actual-user checks.'),
        ('C10','actual-faults','Fault labels do not prove the launcher applied the intended fault scope.'),
        ('C11','setup-event','Verify one readiness path emits setup_complete through the SDK after all prerequisites.'),
        ('C12','command-catalog','Inspect workload images for executable Test Composer commands and interpreters.'),
        ('C13','assertion-encounters','Execute commands and verify their exits and nonzero property encounters.'),
        ('C14','secret-content-review','Filename screening does not establish absence of credentials in file contents.'),
        ('C15','experiment-evidence','Review actual version/measurement bindings, immutable inputs and fresh-state evidence.'),
    ]:
        check(contract, code, 'not_checked', detail)
    for name in files:
        if any(part == '.DS_Store' or part.startswith('._') or part == '__pycache__' or part.endswith('.pyc') for part in name.split('/')):
            check('C14','package-metadata','fail','Remove host metadata or stale bytecode.', name)
    data = files.get('docker-compose.yaml', (None, 0))[0]
    if data is None:
        check('C01','compose-root','fail','MOOG requires docker-compose.yaml at the package root.')
        return finish(report)
    try:
        doc = load_compose(data)
    except (ValueError, TypeError, yaml.YAMLError, prep.PackageError, RecursionError):
        check('C01','compose-shape','fail','Compose is invalid, ambiguous, recursive or exceeds bounded structure limits.')
        return finish(report)
    check('C01','compose-root','pass','Root Compose parsed with bounded structure and unique keys.')
    # Check the complete value tree, including fields not individually interpreted below.
    pending = [doc]
    while pending:
        item = pending.pop()
        if isinstance(item, dict): pending.extend(item.values())
        elif isinstance(item, list): pending.extend(item)
        elif unresolved(item):
            check('C01','compose-interpolation','fail','Resolve host environment interpolation before packaging; escaped $$ container variables are preserved.')
            break
    if b'${INTERNAL_NETWORK}' in data:
        check('C05','moog-substitution','warn','Pinned MOOG replaces ${INTERNAL_NETWORK} with false; candidate readback must verify the result.')
    for feature in ('include',):
        if feature in doc: check('C01','compose-extension','fail','Resolve Compose '+feature+' into this package before final validation.')
    resources = {}
    for category in ('networks','volumes','configs','secrets'):
        entries = doc.get(category, {})
        if not isinstance(entries, dict):
            check('C01','compose-shape','fail',category+' must be a mapping.'); entries = {}
        resources[category] = entries
        for name, entry in entries.items():
            if entry is not None and not isinstance(entry, dict):
                check('C01','compose-shape','fail',category+'.'+name+' must be a mapping.'); continue
            entry = entry or {}
            if entry.get('external'):
                check('C01','external-resource','fail','Package relies on an external '+category+' resource.',category+'.'+name)
            if category == 'networks':
                internal = entry.get('internal', False)
                if internal not in (False, '${INTERNAL_NETWORK}') or isinstance(internal, (int,float)) and not isinstance(internal,bool):
                    check('C05','internal-network','fail','Submitted networks must not be internal.', 'networks.'+name)
    def dependency(value, location, require_file=False):
        if not isinstance(value,str) or not value or unresolved(value):
            check('C01','unresolved-input','fail','Provide an explicit packaged relative path.',location); return
        name = posixpath.normpath(value)
        if value.startswith(('/', '~')) or '\\' in value or ':' in value or name == '..' or name.startswith('../'):
            check('C01','external-input','fail','Input points outside the package.',location); return
        exists = name in files or name == '.' or any(p.startswith(name+'/') for p in files)
        if not exists or require_file and (name not in files or files[name][0] is None):
            check('C01','missing-input','fail','Referenced input is absent or not a file: '+name,location)
        else: check('C01','packaged-input','pass','Referenced input exists: '+name,location)
    for category in ('configs','secrets'):
        for name, entry in resources[category].items():
            if isinstance(entry,dict) and 'file' in entry: dependency(entry['file'],category+'.'+name,True)
    services = doc['services']
    for name, service in services.items():
        location = 'services.'+name
        if not isinstance(service,dict):
            check('C01','compose-shape','fail','Service must be a mapping.',location); continue
        def row(contract,code,state,detail): check(contract,code,state,detail,location)
        shape_errors=service_shape_errors(service)
        for detail in shape_errors: row('C01','compose-shape','fail',detail)
        if shape_errors:continue
        image = service.get('image')
        if not isinstance(image,str) or not valid_image(image) or unresolved(image):
            row('C03','image-reference','fail','Final service needs an explicit image reference.')
        else:
            report['images'].append(dict(service=name,reference=image))
            row('C03','image-reference','pass','Image reference recorded; access and contents require inspection.')
            if not re.search(r'@sha256:[a-f0-9]{64}$',image):
                row('C15','image-digest','warn','Mutable image reference; record its resolved digest for reproducibility. Experimental versions are allowed.')
        if 'build' in service: row('C03','image-build','fail','Materialize service images and remove build from the final MOOG package.')
        if service.get('platform') not in (None,'linux/amd64'): row('C02','platform','fail','Antithesis requires Linux/amd64; declaration selects a different platform.')
        if 'pull_policy' in service: row('C04','pull-policy','fail','Remove pull_policy from the submitted Compose.')
        logging = service.get('logging')
        if logging is not None and not isinstance(logging,dict): row('C01','compose-shape','fail','logging must be a mapping.')
        elif isinstance(logging,dict) and 'driver' in logging: row('C04','logging-driver','fail','Remove custom logging.driver so Antithesis can expose logs.')
        if service.get('container_name') != name: row('C06','container-name','fail','Set container_name equal to the service key for MOOG fault mapping.')
        else: row('C06','container-name','pass','Service key and container name match.')
        hostname = service.get('hostname',name)
        if not isinstance(hostname,str) or not HOST.fullmatch(hostname) or any(not p or len(p)>63 or p.startswith('-') or p.endswith('-') for p in hostname.split('.')):
            row('C06','hostname','fail','Use a DNS-valid hostname without underscores.')
        elif hostname != name: row('C06','hostname-map','warn','Hostname differs from service name; verify peer addresses and aliases.')
        row('C07','init','pass' if service.get('init') is True else 'warn',
            'init is enabled.' if service.get('init') is True else 'Enable init or review and justify the service exception.')
        if 'extends' in service: row('C01','compose-extension','fail','Resolve extends before final validation.')
        for category, code, targets in [('networks','network-reference',resources['networks']),('configs','config-reference',resources['configs']),
                                        ('secrets','secret-reference',resources['secrets']),('depends_on','dependency-reference',services)]:
            values = service.get(category,[])
            if not isinstance(values,(list,dict)):
                row('C01','compose-shape','fail',category+' must be a list or mapping.'); continue
            for value in values:
                key = value.get('source') if isinstance(value,dict) else value
                if not isinstance(key,str) or key not in targets:
                    row('C01',code,'fail','Reference needs a declared '+category+' entry.')
        env_files = service.get('env_file',[])
        if isinstance(env_files,(str,dict)): env_files=[env_files]
        if not isinstance(env_files,list): row('C01','compose-shape','fail','env_file must be a path or list.')
        else:
            for value in env_files: dependency(value.get('path') if isinstance(value,dict) else value,location+'.env_file',True)
        mounts = service.get('volumes',[])
        if not isinstance(mounts,list): row('C01','compose-shape','fail','volumes must be a list.'); continue
        for mount in mounts:
            if isinstance(mount,str):
                parts=mount.split(':'); source=parts[0] if len(parts)>1 else None
                kind='bind' if source and (source.startswith(('.', '/', '~')) or unresolved(source)) else 'volume'
            elif isinstance(mount,dict): source=mount.get('source');kind=mount.get('type')
            else: row('C01','compose-shape','fail','Volume entry must be a string or mapping.');continue
            if kind=='bind': dependency(source,location+'.volumes')
            elif kind=='volume' and source and (not isinstance(source,str) or source not in resources['volumes']):
                row('C09','volume-reference','fail','Named volume must be declared in top-level volumes.')
            elif kind not in ('volume','tmpfs'): row('C09','volume-type','not_checked','This mount type requires manual inspection.')
    with tempfile.TemporaryDirectory(prefix='dwarf-fault-label-') as directory:
        path=Path(directory)/'docker-compose.yaml'; path.write_bytes(data)
        errors=moog_fault_exclusion_errors(path)
    for error in errors: check('C10','fault-label','fail',error)
    if not errors: check('C10','fault-label','pass','Existing MOOG fault-label parser found no invalid classes.')
    for contract, code, detail in [('C04','compose-logging-pull','No forbidden logging driver or pull policy found.'),
                                   ('C05','compose-networks','No literal internal network found.')]:
        if not any(r['contract']==contract and r['state']=='fail' for r in report['checks']): check(contract,code,'pass',detail)
    return finish(report)
