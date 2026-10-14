"""Workflow-only, explicit first setup. Never resets an existing ledger."""
import json
import os
from installer.github_api import GitHubAPI, SetupError
from installer.core import validate_target
from monitor import empty_state, decode_json, validate_state


def prepare(api,repository,initialized=False):
    import base64
    base='/repos/'+validate_target(repository)
    try:
        response=api.request('GET',base+'/contents/state.json?ref=monitor-state')
    except SetupError as exc:
        if exc.code!='NOT_FOUND':raise
        if initialized:raise SetupError('STATE_MISSING_AFTER_SETUP','restore history; never create an empty replacement')
        # A branch with missing/corrupt state is not a first setup.
        try:api.request('GET',base+'/git/ref/heads/monitor-state')
        except SetupError as branch:
            if branch.code!='NOT_FOUND':raise
        else:raise SetupError('EXISTING_STATE_BRANCH_INVALID')
    else:
        try:validate_state(decode_json(base64.b64decode(response['content']),1048576))
        except Exception:raise SetupError('EXISTING_STATE_INVALID') from None
        return 'preserved'
    tree=api.request('POST',base+'/git/trees',{'tree':[{'path':'state.json','mode':'100644','type':'blob',
        'content':json.dumps(empty_state(),indent=2,sort_keys=True)+'\n'}]})
    commit=api.request('POST',base+'/git/commits',{'message':'Initialize non-secret delivery ledger','tree':tree['sha'],'parents':[]})
    api.request('POST',base+'/git/refs',{'ref':'refs/heads/monitor-state','sha':commit['sha']})
    return 'initialized'


def main():
    api=GitHubAPI(os.environ.get('GH_TOKEN',''))
    try:
        if os.environ.get('MONITOR_ENABLED')=='true':raise SetupError('MONITOR_ALREADY_ENABLED')
        print(json.dumps({'state':prepare(api,os.environ.get('GITHUB_REPOSITORY',''),
                         os.environ.get('MONITOR_INITIALIZED')=='true'),'notification_attempted':False}))
    except SetupError as exc:
        print(json.dumps({'error':exc.code}));return 4
    finally:api.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
