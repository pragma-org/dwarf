import hashlib
import json
import pytest
from profile_manager.antithesis_package import PackageError


def module():
    from profile_manager import antithesis_publication_network
    return antithesis_publication_network


def encode(value):return json.dumps(value).encode()
def digest(data):return 'sha256:'+hashlib.sha256(data).hexdigest()


def fixture(monkeypatch,arch='amd64'):
    config=encode({'architecture':arch,'os':'linux'})
    child=encode({'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json','config':{'digest':digest(config)},'layers':[]})
    index=encode({'schemaVersion':2,'mediaType':'application/vnd.oci.image.index.v1+json','manifests':[{'digest':digest(child),'platform':{'os':'linux','architecture':'amd64'}}]})
    seen=[]
    def get(url,headers=None):
        seen.append((url,headers))
        if '/token?' in url:
            assert not headers or 'Authorization' not in headers;return encode({'token':'anonymous-only'})
        assert headers['Authorization']=='Bearer anonymous-only'
        if url.endswith('/manifests/test'):return index
        if url.endswith('/manifests/'+digest(child)):return child
        if url.endswith('/blobs/'+digest(config)):return config
        raise AssertionError(url)
    monkeypatch.setattr(module(),'_get',get)
    return config,child,index,seen


def test_anonymous_manifest_and_config_identity(monkeypatch):
    config,child,index,seen=fixture(monkeypatch)
    for expected in [digest(child),digest(config)]:
        value=module().verify_image('ghcr.io/example/test:test',expected)
        assert value['manifest_digest']==digest(index)
        assert value['reference']=='ghcr.io/example/test@'+digest(index)
        assert value['expected_identity_matched'] is True
        assert value['platform']=='linux/amd64'
    assert all(url.startswith('https://ghcr.io/') for url,_ in seen)


def test_index_platform_lie_rejected(monkeypatch):
    fixture(monkeypatch,arch='arm64')
    with pytest.raises(PackageError,match='amd64'):module().verify_image('ghcr.io/example/test:test')


def test_identity_and_digest_mismatch(monkeypatch):
    fixture(monkeypatch)
    with pytest.raises(PackageError,match='identity'):module().verify_image('ghcr.io/example/test:test','sha256:'+'f'*64)
    original=module()._get
    def changed(url,headers=None):
        value=original(url,headers)
        return value+b' ' if '/manifests/sha256:' in url else value
    monkeypatch.setattr(module(),'_get',changed)
    with pytest.raises(PackageError,match='digest'):module().verify_image('ghcr.io/example/test:test')


@pytest.mark.parametrize('ref',['localhost/test:tag','ghcr.io/x/../test:tag','ghcr.io/x/y','ghcr.io/x/y@sha256:no','ghcr.io/x/y:foo?x=1','https://ghcr.io/x/y:foo'])
def test_invalid_image_ref(ref):
    with pytest.raises(PackageError):module().verify_image(ref)


def git_fixture(monkeypatch,change=None):
    files={'docker-compose.yaml':(b'services: {}',0o644),'test/run.py':(b'print(1)',0o755)}
    rows=[{'path':n,'mode':'100755' if m&0o111 else '100644','type':'blob','sha':hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()} for n,(b,m) in files.items()]
    result={'tree':rows,'truncated':False,'sha':'2'*40}
    if change:change(result)
    def get(url,headers=None):
        assert not headers or 'Authorization' not in headers
        if '/git/commits/' in url:return encode({'sha':'a'*40,'tree':{'sha':'0'*40}})
        if url.endswith('/git/trees/'+'0'*40):return encode({'tree':[{'path':'antithesis','mode':'040000','type':'tree','sha':'1'*40}]})
        if url.endswith('/git/trees/'+'1'*40):return encode({'tree':[{'path':'example','mode':'040000','type':'tree','sha':'2'*40}]})
        if url.endswith('/git/trees/'+'2'*40+'?recursive=1'):return encode(result)
        raise AssertionError(url)
    monkeypatch.setattr(module(),'_get',get)
    return files


def test_git_bytes_and_modes(monkeypatch):
    files=git_fixture(monkeypatch)
    result=module().verify_git('Example/dwarf','a'*40,'antithesis/example',files)
    assert result['anonymous'] is True and result['file_count']==2


@pytest.mark.parametrize('change',[
 lambda r:r.update(truncated=True),
 lambda r:r['tree'][1].update(mode='100644'),
 lambda r:r['tree'][0].update(sha='f'*40),
 lambda r:r['tree'].append({'path':'extra','mode':'100644','type':'blob','sha':'b'*40}),
 lambda r:r['tree'][1].update(mode='120000'),
])
def test_git_drift_rejected(monkeypatch,change):
    files=git_fixture(monkeypatch,change)
    with pytest.raises(PackageError):module().verify_git('Example/dwarf','a'*40,'antithesis/example',files)


def test_anonymous_http_redirect_strips_pull_token_and_checks_public_destination(monkeypatch):
    net=module();calls=[];resolved=[]
    class Sock:
        def shutdown(self,*a):pass
        def close(self):pass
    class Response:
        status=307
        def getheader(self,key):return 'https://blob.example/object?signature=temporary' if key=='Location' else None
    class Body:
        status=200
        def __init__(self):self.done=False
        def getheader(self,key):return '2' if key=='Content-Length' else None
        def read1(self,n):
            if self.done:return b''
            self.done=True;return b'{}'
    responses=iter([Response(),Body()])
    class Connection:
        def __init__(self,host,**kwargs):self.host=host;self.sock=Sock()
        def connect(self):pass
        def request(self,method,path,headers):calls.append((self.host,path,headers))
        def getresponse(self):return next(responses)
        def close(self):pass
    def resolve(host,*args):resolved.append(host);return '1.1.1.1'
    monkeypatch.setattr(net.prep,'_resolve_public',resolve)
    monkeypatch.setattr(net.http.client,'HTTPSConnection',Connection)
    assert net._get('https://ghcr.io/v2/test/blob',{'Authorization':'Bearer anonymous-token'})==b'{}'
    assert calls[0][2]['Authorization']=='Bearer anonymous-token'
    assert 'Authorization' not in calls[1][2]
    assert resolved==['ghcr.io','blob.example']


def test_public_read_rejects_private_destination_before_connect(monkeypatch):
    net=module()
    monkeypatch.setattr(net.prep,'_resolve_public',lambda *a:(_ for _ in ()).throw(PackageError('Public hosts only')))
    monkeypatch.setattr(net.http.client,'HTTPSConnection',lambda *a,**k:pytest.fail('connected'))
    with pytest.raises(PackageError,match='Public'):net._get('https://127.0.0.1/file')


def test_malformed_tag_before_digest_rejected_before_network(monkeypatch):
    monkeypatch.setattr(module(),'_get',lambda *a:pytest.fail('invalid reference reached network'))
    with pytest.raises(PackageError):module().verify_image('ghcr.io/example/test:bad?tag@sha256:'+'a'*64)
