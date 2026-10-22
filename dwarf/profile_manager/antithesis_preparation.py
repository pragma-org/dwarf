"""Acquire and stage Antithesis assets; never build, execute, publish or submit."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import gzip
import http.client
import io
import ipaddress
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import tarfile
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit
import uuid
import zipfile
import zlib

import yaml

from profile_manager.antithesis_package import PackageError, _json, _sha, _shape, _validate, preview_package
from profile_manager.moog import validate_moog_asset

MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 2048
DOWNLOAD_TIMEOUT = 30
_DNS_SLOTS = threading.BoundedSemaphore(2)
_ID = re.compile(r'[0-9a-f]{32}')
_COMPOSE = ('docker-compose.yaml','docker-compose.yml','config/docker-compose.yaml','config/docker-compose.yml')


def state_directory():
    from profile_manager.config import storage_roots
    return storage_roots().state / 'antithesis' / 'preparations'


def _name(value):
    path = PurePosixPath(value)
    if (not value or len(value)>1024 or path.is_absolute() or '\\' in value or ':' in value
            or '\x00' in value or '..' in path.parts or len(path.parts) > 24
            or str(path) == '.'):
        raise PackageError('Unsafe package member path.', field='source', status=422)
    return str(path)


def _add(files, name, data, mode):
    name = _name(name)
    if name in files:
        raise PackageError('Duplicate package member: '+name, field='source', status=422)
    if len(files) >= MAX_FILES or sum(len(v[0] or b'') for v in files.values()) + len(data or b'') > MAX_BYTES:
        raise PackageError('Package exceeds the 32 MiB / 2048 member limit.', field='source', status=413)
    # Preserve executable bits, not special/setuid or broadly writable permissions.
    files[name] = (data, 0o755 if data is None or mode & 0o111 else 0o644)


def _resolve_public(host, port, timeout):
    # Bound resolver time without allowing unlimited abandoned resolver threads.
    if not _DNS_SLOTS.acquire(blocking=False):
        raise PackageError('Archive DNS lookup is busy. Retry later.', status=503)
    finished, result = threading.Event(), []
    def resolve():
        try:
            result.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except (OSError, UnicodeError, ValueError) as exc:
            result.append(exc)
        finally:
            _DNS_SLOTS.release()
            finished.set()
    threading.Thread(target=resolve, daemon=True).start()
    if not finished.wait(max(0, timeout)):
        raise PackageError('Package download timed out during DNS lookup.', field='source.url', status=504)
    if not result or isinstance(result[0], (UnicodeError, ValueError)):
        raise PackageError('Invalid archive hostname.', field='source.url', status=422)
    if isinstance(result[0], OSError):
        raise result[0]
    addresses = result[0]
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise PackageError('Package URLs must resolve to public HTTPS hosts.', field='source.url', status=422)
    return addresses[0][4][0]


def _download(url):
    """Bounded HTTPS download, pinning each connection to a public resolved IP."""
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT
    for _ in range(6):
        try:
            parsed = urlsplit(url)
            port = parsed.port or 443
        except ValueError as exc:
            raise PackageError('Invalid archive URL.', field='source.url', status=422) from exc
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise PackageError('Use a direct HTTPS archive URL without embedded credentials or fragments.', field='source.url', status=422)
        address = _resolve_public(parsed.hostname, port, deadline-time.monotonic())
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PackageError('Package download timed out.', field='source.url', status=504)
        connection = http.client.HTTPSConnection(parsed.hostname, port, timeout=min(10, remaining))
        def connect_pinned(target, timeout, source_address=None):
            connected = socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_STREAM)
            try:
                connected.settimeout(max(0.001, min(timeout, deadline-time.monotonic())))
                if source_address:
                    connected.bind(source_address)
                connected.connect((address, port))
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise PackageError('Package download timed out.', field='source.url', status=504)
                # HTTPSConnection performs TLS next; its handshake timeout must
                # reflect time already spent connecting, not the original budget.
                connected.settimeout(remaining)
                return connected
            except BaseException:
                connected.close()
                raise
        connection._create_connection = connect_pinned
        watchdog = None
        try:
            connection.connect()
            if time.monotonic() >= deadline:
                raise PackageError('Package download timed out.', field='source.url', status=504)
            # Close the actual socket at the absolute deadline, including while
            # parsing slowly delivered headers or reading a dripping body.
            connected_socket = connection.sock
            def abort():
                try:
                    connected_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connected_socket.close()
            watchdog = threading.Timer(max(0, deadline-time.monotonic()), abort)
            watchdog.daemon = True
            watchdog.start()
            connection.request('GET', (parsed.path or '/') + ('?'+parsed.query if parsed.query else ''),
                               headers={'User-Agent':'Dwarf-Antithesis-Preparation','Accept-Encoding':'identity'})
            response = connection.getresponse()
            if response.status in (301,302,303,307,308):
                location = response.getheader('Location')
                if not location:
                    break
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise PackageError('Archive server returned HTTP '+str(response.status)+'.', field='source.url', status=422)
            length = response.getheader('Content-Length')
            if length and int(length) > MAX_BYTES:
                raise PackageError('Archive download exceeds 32 MiB.', field='source.url', status=413)
            output = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PackageError('Package download timed out.', field='source.url', status=504)
                if connection.sock:
                    connection.sock.settimeout(min(10, remaining))
                block = response.read1(min(65536, MAX_BYTES + 1 - len(output)))
                if not block:
                    if time.monotonic() >= deadline:
                        raise PackageError('Package download timed out.', field='source.url', status=504)
                    return bytes(output)
                output.extend(block)
                if len(output) > MAX_BYTES:
                    raise PackageError('Archive download exceeds 32 MiB.', field='source.url', status=413)
        except (OSError, http.client.HTTPException) as exc:
            if time.monotonic() >= deadline:
                raise PackageError('Package download timed out.', field='source.url', status=504) from exc
            raise
        finally:
            if watchdog:
                watchdog.cancel()
            connection.close()
    raise PackageError('Archive redirect limit exceeded.', field='source.url', status=422)


def _unpack(data):
    if len(data) > MAX_BYTES:
        raise PackageError('Archive exceeds 32 MiB.', status=413)
    files = {}
    try:
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if len(archive.infolist()) > MAX_FILES:
                    raise PackageError('Archive has too many members.', status=413)
                for member in archive.infolist():
                    mode = member.external_attr >> 16
                    if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR) or member.flag_bits & 1:
                        raise PackageError('Archive links, special files and encryption are unsupported.', status=422)
                    if member.file_size > MAX_BYTES:
                        raise PackageError('Archive member is too large.', status=413)
                    content = None if member.is_dir() else archive.read(member)
                    _add(files, member.filename.rstrip('/'), content, mode)
        else:
            # Bound the entire expanded stream BEFORE tarfile parses PAX/GNU
            # metadata, which is otherwise consumed internally without yielding.
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                expanded = compressed.read(MAX_BYTES+1)
            if len(expanded)>MAX_BYTES:
                raise PackageError('Expanded tar archive exceeds 32 MiB.', status=413)
            with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
                for member in archive:
                    if not (member.isfile() or member.isdir()):
                        raise PackageError('Archive links and special files are unsupported.', status=422)
                    if member.size > MAX_BYTES:
                        raise PackageError('Archive member is too large.', status=413)
                    content = None if member.isdir() else archive.extractfile(member).read(MAX_BYTES+1)
                    _add(files, member.name.rstrip('/'), content, member.mode)
    except (zipfile.BadZipFile, tarfile.TarError, gzip.BadGzipFile, zlib.error, RuntimeError, EOFError) as exc:
        raise PackageError('URL must return a ZIP or tar.gz archive, not a repository web page.', field='source.url', status=422) from exc
    wrapper = None
    roots = {name.split('/')[0] for name in files}
    if len(roots) == 1:
        candidate = next(iter(roots))
        if any(name.startswith(candidate+'/') for name in files) and files.get(candidate, (None,0))[0] is None:
            wrapper = candidate
            files = {name[len(candidate)+1:]:entry for name,entry in files.items() if name != candidate}
    return files, wrapper


def _read_tree(root):
    if root.is_symlink():
        raise PackageError('Package links are unsupported.', field='source', status=422)
    root = root.resolve(strict=True)
    files = {}
    def unreadable(error):
        raise error
    for directory, folders, names in os.walk(root, followlinks=False, onerror=unreadable):
        for name in folders + names:
            path = Path(directory)/name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise PackageError('Package links and special files are unsupported: '+relative, field='source', status=422)
            if stat.S_ISDIR(info.st_mode):
                _add(files, relative, None, info.st_mode)
            else:
                if info.st_size > MAX_BYTES:
                    raise PackageError('Package file exceeds 32 MiB.', field='source', status=413)
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, 'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise PackageError('Package file changed while reading.', status=409)
                    _add(files, relative, stream.read(MAX_BYTES+1), info.st_mode)
    return files


def _write_tree(root, files):
    root.mkdir(parents=True, exist_ok=True)
    for name, (data, mode) in sorted(files.items()):
        path = root/name
        try:
            if data is None:
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise PackageError('Package paths conflict on this filesystem.', field='source', status=422) from exc
        path.chmod(mode)


def _inventory(files):
    return [{'path':name, 'kind':'directory' if data is None else 'file',
             'sha256':None if data is None else _sha(data), 'bytes':len(data or b''), 'mode':mode}
            for name,(data,mode) in sorted(files.items())]


def _bound_compose(data):
    """Bound alias-expanded node work before SafeLoader flattens merge keys."""
    try:
        node = yaml.compose(data, Loader=yaml.SafeLoader)
        pending, count = [(node,0)], 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if depth>24 or count>20000:
                raise PackageError('Compose structure is recursive or exceeds inspection complexity limits.', field='source', status=422)
            if isinstance(node,yaml.MappingNode):
                pending.extend((child,depth+1) for pair in node.value for child in pair)
            elif isinstance(node,yaml.SequenceNode):
                pending.extend((child,depth+1) for child in node.value)
    except (yaml.YAMLError, RecursionError) as exc:
        raise PackageError('Compose structure could not be parsed safely.', field='source', status=422) from exc


def _inspect(files, asset_root):
    issues, images, builds = [], [], []
    def issue(code, message):
        issues.append({'code':code, 'message':message})
    for name in _COMPOSE:
        if name in files and files[name][0] is not None:
            if len(files[name][0])>1024*1024:
                raise PackageError('Compose files must be at most 1 MiB.', field='source', status=413)
            _bound_compose(files[name][0])
    validation = validate_moog_asset(str(asset_root))
    if any(row['id']=='secret_files' and row['state']=='error' for row in validation['checks']):
        raise PackageError('Package contains secret-like filenames; remove them before staging.', field='source', status=422)
    for check in validation['checks']:
        if check['state'] in ('error','warn'):
            issue('asset-'+check['id'], check['detail'])
    compose_path = next((name for name in _COMPOSE if name in files and files[name][0] is not None), None)
    if not compose_path:
        issue('missing-compose', 'Images alone do not define the test. Supply its Compose file and referenced inputs.')
        return None, issues, images, builds
    if compose_path != 'docker-compose.yaml':
        issue('moog-root-layout', 'Pinned MOOG source expects root docker-compose.yaml. Supplied layout is preserved; publication layout needs resolution.')
    compose = yaml.safe_load(files[compose_path][0])
    pending, count = [(compose,0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth>24 or count>20000 or not isinstance(item,(dict,list,str,int,float,bool,type(None))):
            issue('invalid-compose', 'Compose structure is recursive, too complex, or contains unsupported scalar types.')
            return compose_path, issues, images, builds
        if isinstance(item,dict):
            if any(not isinstance(key,str) for key in item):
                issue('invalid-compose','Compose mapping keys must be strings.')
                return compose_path, issues, images, builds
            pending.extend((value,depth+1) for value in item.values())
        elif isinstance(item,list):
            pending.extend((value,depth+1) for value in item)
    services = compose.get('services') if isinstance(compose, dict) else None
    if not isinstance(services, dict) or not services:
        issue('invalid-compose', 'Compose must contain a non-empty services mapping.')
        return compose_path, issues, images, builds
    base = PurePosixPath(compose_path).parent

    def dependency(value, label):
        if not isinstance(value, str) or not value or '${' in value:
            issue('unresolved-input', label+' needs an explicit local path.')
            return
        name = posixpath.normpath(str(base / value))
        if value.startswith(('~','/')) or '\\' in value or ':' in value or name=='..' or name.startswith('../'):
            issue('external-input', label+' is outside the package.')
            return
        if name == '.':
            return
        if name not in files and not any(path.startswith(name+'/') for path in files):
            issue('missing-input', label+' is missing: '+name)

    for name, service in services.items():
        if not isinstance(service, dict):
            issue('invalid-compose', 'Service '+str(name)+' must be a mapping.')
            continue
        from profile_manager.antithesis_preflight import service_shape_errors
        shape_errors = service_shape_errors(service)
        if shape_errors:
            for detail in shape_errors:
                issue('invalid-compose', str(name)+': '+detail)
            continue
        image = service.get('image')
        if image:
            images.append({'service':name, 'reference':image, 'identity':'recorded-only'})
            if not isinstance(image, str) or '${' in image:
                issue('unresolved-image', 'Service '+str(name)+' has an unresolved image reference.')
        build = service.get('build')
        if build is not None:
            builds.append({'service':name, 'build':build})
            issue('image-build-required', 'Service '+str(name)+' includes a build; no image was built.')
            context = build.get('context','.') if isinstance(build, dict) else build
            if isinstance(context, str):
                dockerfile = build.get('dockerfile','Dockerfile') if isinstance(build,dict) else 'Dockerfile'
                dependency(str(PurePosixPath(context)/str(dockerfile)), str(name)+' build input')
            else:
                issue('unresolved-input', str(name)+' build context is not a path.')
        elif not image:
            issue('missing-image', 'Service '+str(name)+' has neither an image nor a build.')
        env_files = service.get('env_file', [])
        for entry in env_files if isinstance(env_files,list) else [env_files]:
            dependency(entry.get('path') if isinstance(entry,dict) else entry, str(name)+' env_file')
        for mount in service.get('volumes', []):
            if isinstance(mount,dict) and mount.get('type')=='bind':
                dependency(mount.get('source'), str(name)+' bind mount')
            elif isinstance(mount,str):
                source = mount.split(':',1)[0]
                if source.startswith(('.', '/', '~')) or '${' in source:
                    dependency(source, str(name)+' bind mount')
        if service.get('extends'):
            issue('unresolved-compose-extension', 'Service '+str(name)+' uses extends; its merged inputs are not resolved.')
    for category in ('configs','secrets'):
        entries = compose.get(category,{})
        if isinstance(entries,dict):
            for name, entry in entries.items():
                if isinstance(entry,dict) and 'file' in entry:
                    dependency(entry['file'], category+'.'+str(name))
    if compose.get('include'):
        issue('unresolved-compose-extension', 'Compose include inputs are preserved but not merged or resolved.')
    return compose_path, issues, images, builds


def _result(receipt, files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name,(data,mode) in sorted(files.items()):
            info = zipfile.ZipInfo(name+'/' if data is None else name, date_time=(1980,1,1,0,0,0))
            info.external_attr = ((stat.S_IFDIR if data is None else stat.S_IFREG) | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data or b'')
    return {'receipt':receipt, 'archive_base64':base64.b64encode(output.getvalue()).decode(),
            'archive_name':'antithesis-staged-'+receipt['id']+'.zip'}


def prepare_package(value, source, *, state_dir=None):
    _validate(value, preparation=True)
    kind = value['package_source']
    version_binding = {'status':'recorded', 'source':'supplied-package',
                       'detail':'Supplied package images are preserved; wizard version selections are intent only.'}
    _shape(source, {'build':set(), 'url':{'url'}, 'existing':{'compose_path','image_references'}}[kind], 'source')
    workload_binding = {'status':'recorded', 'source':'supplied-package', 'runtime_verified':False}
    measurement_binding = {'status':'supplied-package','targets':[],'issues':[]}
    description, issues = {'kind':kind}, []
    if kind == 'build':
        preview = preview_package(value)
        version_binding = preview['version_binding']
        workload_binding = preview['workload_binding']
        measurement_binding = preview['measurement_binding']
        # Rebase the generated environment and its relative inputs together.
        files = {}
        for name,text in preview['files'].items():
            if name.startswith('environment/'):
                _add(files, name.removeprefix('environment/'), text.encode(), 0o644)
            _add(files, 'provenance/preview/'+name, text.encode(), 0o644)
        issues.extend(preview['issues'])
        description['generator'] = ('existing-control-harness-preview' if preview['engine_handoff']['renderer']=='control-harness' else 'existing-native-preview')
        description['normalization'] = 'environment/ copied to package root; complete inspection snapshot retained under provenance/preview/'
    elif kind == 'url':
        url = source['url']
        if not isinstance(url,str) or len(url)>4096:
            raise PackageError('Provide an HTTPS archive URL.', field='source.url')
        files, wrapper = _unpack(_download(url))
        description.update(url=url, removed_wrapper=wrapper)
    else:
        path, references = source['compose_path'], source['image_references']
        if (not isinstance(path,str) or len(path)>4096 or not isinstance(references,list)
                or len(references)>128 or any(not isinstance(ref,str) or not ref or len(ref)>512 or any(c.isspace() for c in ref) for ref in references)):
            raise PackageError('Provide a Compose path and/or valid image references.', field='source')
        if not path and not references:
            raise PackageError('Provide a Compose path or image references.', field='source')
        files = {}
        if path:
            selected = Path(path).expanduser()
            if selected.is_symlink():
                raise PackageError('Package links are unsupported.', field='source', status=422)
            if selected.is_file():
                if selected.name not in ('docker-compose.yaml','docker-compose.yml'):
                    raise PackageError('Choose docker-compose.yaml, docker-compose.yml or their directory.', field='source', status=422)
                selected = selected.parent
            if not selected.is_dir():
                raise PackageError('Compose directory does not exist on the Dwarf host.', field='source', status=422)
            files = _read_tree(selected)
        description.update(compose_path=path, image_references=references)
    with tempfile.TemporaryDirectory(prefix='dwarf-antithesis-inspect-') as temporary:
        root = Path(temporary)
        _write_tree(root, files)
        # Include implicit directories so archive/readback inventories agree.
        materialized = _read_tree(root)
        if any(materialized.get(name)!=entry for name,entry in files.items()):
            raise PackageError('This filesystem cannot preserve all supplied package paths and bytes.', field='source', status=422)
        files = materialized
        compose_path, findings, images, builds = _inspect(files, root)
        issues.extend(findings)
    issues.append({'code':'publication-deferred','message':'Assets are staged only. Image publication and MOOG submission remain deferred.'})
    receipt = {'schema_version':1, 'id':uuid.uuid4().hex, 'created_at':datetime.now(timezone.utc).isoformat(),
               'state':'staged', 'execution_ready':False, 'submission_ready':False,
               'source':description, 'request':value, 'compose_path':compose_path,
               'version_binding':version_binding, 'workload_binding':workload_binding, 'measurement_binding':measurement_binding,
               'inventory':_inventory(files), 'images':images, 'build_requirements':builds,
               'issues':issues, 'compatibility':'not-evaluated',
               'package_contract':{'moog_source_commit':'2642c60b5598562294e553fb3491fc5228033c91','deployed_agent_verified':False}}
    directory = Path(state_dir) if state_dir is not None else state_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=directory) as temporary:
        staging = Path(temporary)
        _write_tree(staging/'package', files)
        with (staging/'receipt.json').open('w') as stream:
            stream.write(_json(receipt)); stream.flush(); os.fsync(stream.fileno())
        os.rename(staging, directory/receipt['id'])
    return _result(receipt, files)


def read_preparation(identifier, *, state_dir=None):
    if not isinstance(identifier,str) or not _ID.fullmatch(identifier):
        raise PackageError('Invalid preparation ID.', field='preparation_id')
    directory = (Path(state_dir) if state_dir is not None else state_directory())/identifier
    try:
        receipt = json.loads((directory/'receipt.json').read_text())
        files = _read_tree(directory/'package')
    except FileNotFoundError as exc:
        raise PackageError('Preparation was not found.', field='preparation_id', status=404) from exc
    if receipt['id'] != identifier or _inventory(files) != receipt['inventory']:
        raise PackageError('Stored preparation changed; acquire the source again.', field='preparation_id', status=409)
    return _result(receipt, files)
