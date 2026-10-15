"""Read-only artifact acceptance against an explicit public source snapshot. No HTTP."""
import base64
import json
from pathlib import Path
from installer.core import SOURCE_REPOSITORY, verify_repository
from installer.github_api import SetupError


class SnapshotAPI:
    def __init__(self,snapshot,mode='paired'):
        self.snapshot=snapshot;self.mode=mode;self.calls=[]
    def request(self,method,path,payload=None):
        assert method=='GET' and payload is None,'Offline acceptance must stay read-only'
        self.calls.append(path)
        base='/repos/'+SOURCE_REPOSITORY
        if path==base:return {'full_name':SOURCE_REPOSITORY,'default_branch':'main','permissions':{'admin':True}}
        if path==base+'/git/ref/heads/main':return {'object':{'sha':self.snapshot['commit']}}
        if '/git/trees/' in path:
            names=list(self.snapshot['files'])
            if self.mode=='missing':names.remove('.github/workflows/monitor.yml')
            tree=[{'path':name,'mode':'100644'} for name in names]
            if self.mode=='unreviewed':tree.append({'path':'unreviewed.py','mode':'100644'})
            return {'truncated':False,'tree':tree}
        if '/contents/' in path:
            name=path.split('/contents/',1)[1].split('?',1)[0]
            content=self.snapshot['files'][name]
            if self.mode=='corrupt' and name=='project.json':content=base64.b64encode(b'changed').decode()
            return {'encoding':'base64','content':content}
        if '/actions/workflows/' in path:
            if self.mode=='workflow-unavailable':
                raise SetupError('NOT_FOUND',diagnostics={'phase':'github_api','operation':'workflow_ci','method':'GET','http_status':404,'request_id':'OFFLINE-SYNTHETIC'})
            return {'state':'disabled_fork' if self.mode=='fork-disabled' else 'active'}
        if path.endswith('/actions/secrets/public-key'):return {'key_id':'synthetic','key':'synthetic'}
        if '/actions/variables/' in path:raise SetupError('NOT_FOUND')
        raise AssertionError('Unrecognized offline operation')


def check(snapshot,manifest):
    results=[]
    cases={'paired':None,'fork-disabled':None,'missing':'PROJECT_FILES_MISSING',
           'corrupt':'PROJECT_FILES_MISMATCH','unreviewed':'UNREVIEWED_EXECUTABLE_FILES',
           'workflow-unavailable':'WORKFLOW_UNAVAILABLE'}
    for mode,expected in cases.items():
        api=SnapshotAPI(snapshot,mode);code=None;diagnostics={}
        try:assert verify_repository(api,SOURCE_REPOSITORY,manifest)==snapshot['commit']
        except SetupError as exc:code=exc.code;diagnostics=exc.diagnostics
        assert code==expected,(mode,code,expected)
        if mode=='missing':assert not any('/contents/' in path for path in api.calls)
        results.append({'case':mode,'passed':True,'error':code,'requests':len(api.calls),'diagnostics':diagnostics})
    return {'deployment_commit':snapshot['commit'],'oauth_requests':0,'http_requests':0,'writes':0,'cases':results}


def main(path):
    from installer.cli import manifest
    snapshot=json.loads(Path(path).read_text(encoding='utf-8'))
    print(json.dumps(check(snapshot,manifest()),ensure_ascii=False))
    return 0
