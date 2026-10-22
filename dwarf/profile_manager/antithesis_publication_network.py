"""Anonymous GHCR manifests/config and exact public GitHub subtree verification."""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import socket
import threading
import time
from urllib.parse import quote, urlencode, urljoin, urlsplit

from profile_manager import antithesis_preparation as prep
from profile_manager.antithesis_package import PackageError

MAX_RESPONSE = 8 * 1024 * 1024
TIMEOUT = 30
DIGEST = re.compile(r'sha256:[a-f0-9]{64}')
SHA = re.compile(r'[a-f0-9]{40}')
_COMPONENT = r'[a-z0-9]+(?:[._-][a-z0-9]+)*'
REPOSITORY = re.compile(_COMPONENT + r'(?:/' + _COMPONENT + r')+')
ACCEPT = ', '.join(['application/vnd.oci.image.index.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.v2+json'])


def _get(url, headers=None):
    """Bounded public HTTPS; no ambient credentials/proxies; strip auth on redirects."""
    deadline = time.monotonic()+TIMEOUT
    headers = dict(headers or {})
    for _ in range(6):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.port not in (None,443)
                or parsed.username or parsed.password or parsed.fragment):
            raise PackageError('Invalid public verification URL.', status=422)
        address = prep._resolve_public(parsed.hostname,443,deadline-time.monotonic())
        connection = http.client.HTTPSConnection(parsed.hostname,timeout=max(.001,deadline-time.monotonic()))
        def pinned(target,timeout,source_address=None):
            sock=socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET,socket.SOCK_STREAM)
            try:
                sock.settimeout(max(.001,deadline-time.monotonic()));sock.connect((address,443))
                sock.settimeout(max(.001,deadline-time.monotonic()));return sock
            except BaseException:
                sock.close();raise
        connection._create_connection=pinned
        timer=None
        try:
            connection.connect();sock=connection.sock
            def abort():
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                sock.close()
            timer=threading.Timer(max(0,deadline-time.monotonic()),abort);timer.daemon=True;timer.start()
            connection.request('GET',parsed.path+('?' + parsed.query if parsed.query else ''),
                               headers={'User-Agent':'Dwarf-Antithesis-Publication','Accept-Encoding':'identity',**headers})
            response=connection.getresponse()
            if response.status in (301,302,303,307,308):
                location=response.getheader('Location')
                if not location:break
                url=urljoin(url,location);headers.pop('Authorization',None);continue
            if response.status != 200:
                raise PackageError(f'Anonymous public read returned HTTP {response.status}. Check visibility, reference and provider rate limits.',status=422)
            if int(response.getheader('Content-Length') or 0)>MAX_RESPONSE:
                raise PackageError('Public verification response is too large.',status=413)
            data=bytearray()
            while True:
                if time.monotonic()>=deadline:raise TimeoutError()
                chunk=response.read1(min(65536,MAX_RESPONSE+1-len(data)))
                if not chunk:return bytes(data)
                data.extend(chunk)
                if len(data)>MAX_RESPONSE:raise PackageError('Public verification response is too large.',status=413)
        except (OSError,http.client.HTTPException) as exc:
            raise PackageError('Public verification connection failed or timed out. Retry the read-only check.',status=503) from exc
        finally:
            if timer:timer.cancel()
            connection.close()
    raise PackageError('Public verification redirect limit exceeded.',status=422)


def _digest(data):return 'sha256:'+hashlib.sha256(data).hexdigest()


def image_reference(reference):
    if not isinstance(reference,str) or len(reference)>512 or not reference.startswith('ghcr.io/'):
        raise PackageError('This publication checkpoint supports explicit GHCR image references.',status=422)
    path=reference.removeprefix('ghcr.io/')
    if '@' in path:
        repository,ref=path.rsplit('@',1)
        repository,separator,tag=repository.partition(':')
        valid=DIGEST.fullmatch(ref) and (not separator or re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}',tag))
    else:
        repository,sep,ref=path.rpartition(':')
        valid=sep and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}',ref)
    if not valid or not REPOSITORY.fullmatch(repository):
        raise PackageError('Use a GHCR repository with an explicit tag or SHA-256 digest.',status=422)
    return repository,ref


def verify_image(reference, expected_id=None):
    repository,ref=image_reference(reference)
    token=json.loads(_get('https://ghcr.io/token?'+urlencode({'service':'ghcr.io','scope':f'repository:{repository}:pull'}))).get('token')
    if not isinstance(token,str) or not token or len(token)>16384:
        raise PackageError('Anonymous registry pull token was unavailable.',status=422)
    headers={'Authorization':'Bearer '+token,'Accept':ACCEPT}
    def manifest(identifier):
        data=_get(f'https://ghcr.io/v2/{repository}/manifests/{identifier}',headers)
        digest=_digest(data)
        if identifier.startswith('sha256:') and identifier!=digest:
            raise PackageError('Public manifest digest does not match its bytes.',status=422)
        value=json.loads(data)
        if value.get('schemaVersion')!=2:raise PackageError('Unsupported public manifest schema.',status=422)
        return value,digest
    root,root_digest=manifest(ref)
    selected,selected_digest=root,root_digest
    if 'manifests' in root:
        matches=[row for row in root['manifests'] if row.get('platform',{}).get('os')=='linux' and row.get('platform',{}).get('architecture')=='amd64']
        if len(matches)!=1 or not DIGEST.fullmatch(matches[0].get('digest','')):
            raise PackageError('Public image must identify exactly one linux/amd64 variant.',status=422)
        selected,selected_digest=manifest(matches[0]['digest'])
    config_digest=selected.get('config',{}).get('digest','')
    if not DIGEST.fullmatch(config_digest):raise PackageError('Public image config digest is missing.',status=422)
    data=_get(f'https://ghcr.io/v2/{repository}/blobs/{config_digest}',headers)
    if _digest(data)!=config_digest:raise PackageError('Public image config digest does not match its bytes.',status=422)
    config=json.loads(data)
    if config.get('os')!='linux' or config.get('architecture')!='amd64':
        raise PackageError('Public image config is not linux/amd64.',status=422)
    identity_kind=None
    if expected_id:
        if expected_id==selected_digest:identity_kind='amd64-manifest'
        elif expected_id==config_digest:identity_kind='image-config'
        else:raise PackageError('Public image identity differs from the saved build.',status=422)
    return {'reference':f'ghcr.io/{repository}@{root_digest}','manifest_digest':root_digest,
            'amd64_manifest_digest':selected_digest,'config_digest':config_digest,'platform':'linux/amd64',
            'anonymous':True,'expected_identity_matched':bool(expected_id),'identity_kind':identity_kind,
            'scope':'Manifest and config readback; layer blobs and runtime behavior not exercised.'}


def validate_target(target):
    if not isinstance(target,dict) or set(target)!={'repository','commit','directory','requester','try_number'}:
        raise PackageError('Provide repository, commit, directory, requester and attempt.',status=400)
    if not isinstance(target['repository'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}',target['repository']):
        raise PackageError('Use a GitHub owner/repository.',status=422)
    if not isinstance(target['commit'],str) or not SHA.fullmatch(target['commit']):
        raise PackageError('Use the full immutable 40-character Git commit.',status=422)
    directory=target['directory']
    if not isinstance(directory,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*',directory) or prep._name(directory)!=directory:
        raise PackageError('Use a canonical repository-relative asset directory.',status=422)
    if any(p in ('.','..') for p in directory.split('/')):
        raise PackageError('Use a canonical asset directory.',status=422)
    if not isinstance(target['requester'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}',target['requester']):
        raise PackageError('Provide the GitHub requester name.',status=422)
    if type(target['try_number']) is not int or not 1<=target['try_number']<=9999:
        raise PackageError('Attempt must be a positive whole number below 10000.',status=422)


def verify_git(repository,commit,directory,files):
    validate_target(dict(repository=repository,commit=commit,directory=directory,requester='verification',try_number=1))
    base=f'https://api.github.com/repos/{repository}/git/'
    def read(path):return json.loads(_get(base+path,{'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}))
    obj=read('commits/'+commit)
    if obj.get('sha')!=commit:raise PackageError('Public Git commit identity changed.',status=422)
    sha=obj['tree']['sha']
    for part in directory.split('/'):
        if not SHA.fullmatch(sha):raise PackageError('Invalid Git tree identity.',status=422)
        tree=read('trees/'+sha)
        if tree.get('truncated'):raise PackageError('Public Git tree response was truncated.',status=422)
        matches=[r for r in tree['tree'] if r['path']==part and r['type']=='tree' and r['mode']=='040000']
        if len(matches)!=1:raise PackageError('Public Git asset directory was not found.',status=422)
        sha=matches[0]['sha']
    if not SHA.fullmatch(sha):raise PackageError('Invalid Git tree identity.',status=422)
    tree=read('trees/'+sha+'?recursive=1')
    if tree.get('truncated'):raise PackageError('Public Git tree response was truncated.',status=422)
    actual={}
    for row in tree['tree']:
        if row['type']=='tree' and row['mode']=='040000':continue
        if row['type']!='blob' or row['mode'] not in ('100644','100755') or row['path'] in actual:
            raise PackageError('Public Git assets contain unsupported entries.',status=422)
        actual[row['path']]=(row['sha'],row['mode'])
    expected={name:(hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest(),
                   '100755' if mode&0o111 else '100644') for name,(data,mode) in files.items() if data is not None}
    if actual!=expected:
        raise PackageError('Public Git files or executable modes differ from the exported package. Commit the exact ZIP contents in the selected directory.',status=422)
    return {'anonymous':True,'repository':repository,'commit':commit,'directory':directory,
            'tree_sha':sha,'file_count':len(expected),'verification':'Exact Git blob hashes and executable modes; no extra files.'}
