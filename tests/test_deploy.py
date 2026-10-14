"""Safe user-operated setup: no actual GitHub calls or credential input."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

spec=importlib.util.spec_from_file_location('deploy',Path(__file__).resolve().parents[1]/'scripts'/'deploy.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)


class DeployTest(unittest.TestCase):
    def test_existing_branch_preserved(self):
        with patch.object(d,'api',return_value={'object':{'sha':'a'*40}}) as api:
            with contextlib.redirect_stdout(io.StringIO()): d.initialize_state()
        self.assertEqual(api.call_count,1)
        self.assertIsNone(api.call_args.kwargs.get('body'))

    def test_recorded_missing_state_refuses_reset(self):
        with patch.object(d,'api',side_effect=[None,{'value':'true'}]) as api:
            with self.assertRaises(RuntimeError): d.initialize_state()
        self.assertEqual(api.call_count,2)

    def test_first_setup_creates_orphan_state_once_without_enabling(self):
        with patch.object(d,'api',side_effect=[None,None,{'sha':'tree'},{'sha':'commit'},{},{}]) as api:
            with contextlib.redirect_stdout(io.StringIO()): d.initialize_state()
        bodies=[c.kwargs.get('body') for c in api.call_args_list]
        self.assertEqual(bodies[3]['parents'],[])
        self.assertEqual(json.loads(bodies[2]['tree'][0]['content']),d.empty_state())
        self.assertEqual(bodies[4]['ref'],'refs/heads/monitor-state')
        self.assertEqual(bodies[5]['name'],'MONITOR_INITIALIZED')
        self.assertNotIn('MONITOR_ENABLED',json.dumps(bodies))

    def test_configuration_secrets_use_stdin_and_never_argv_or_output(self):
        values=['https://open.feishu.cn/open-apis/bot/v2/hook/'+'x'*24,'synthetic'+'-secret']
        completed=subprocess.CompletedProcess(['gh'],0,b'',b'')
        out=io.StringIO()
        with patch.object(d.getpass,'getpass',side_effect=values),patch.object(d.subprocess,'run',return_value=completed) as run:
            with contextlib.redirect_stdout(out): d.configure_secrets()
        self.assertEqual(run.call_count,2)
        for call,value in zip(run.call_args_list,values):
            self.assertNotIn(value,repr(call.args[0]))
            self.assertEqual(call.kwargs['input'],value.encode())
            self.assertNotIn(value,out.getvalue())

    def test_invalid_config_does_not_upload(self):
        with patch.object(d.getpass,'getpass',side_effect=['invalid','synthetic']),patch.object(d.subprocess,'run') as run:
            with self.assertRaises(d.MonitorError): d.configure_secrets()
        run.assert_not_called()

    def test_failed_secret_upload_has_no_sensitive_error(self):
        values=['https://open.feishu.cn/open-apis/bot/v2/hook/'+'x'*24,'synthetic'+'-secret']
        completed=subprocess.CompletedProcess(['gh'],1,b'',values[1].encode())
        with patch.object(d.getpass,'getpass',side_effect=values),patch.object(d.subprocess,'run',return_value=completed):
            with self.assertRaises(RuntimeError) as caught: d.configure_secrets()
        self.assertNotIn(values[1],str(caught.exception))

    def test_gh_http_auth_failure_does_not_look_like_missing_state(self):
        completed=subprocess.CompletedProcess(['gh'],1,b'',b'gh: Forbidden (HTTP 403)')
        with patch.object(d.subprocess,'run',return_value=completed),self.assertRaises(RuntimeError):
            d.api('git/ref/heads/monitor-state',missing=True)

    def test_enable_missing_secrets_does_not_set_gate(self):
        import base64
        state={'content':base64.b64encode(json.dumps(d.empty_state()).encode()).decode()}
        with patch.object(d,'api',side_effect=[state,{'secrets':[]}]),patch.object(d,'gh') as gh:
            with self.assertRaises(RuntimeError):d.enable()
        gh.assert_not_called()

    def test_enable_unresolved_does_not_set_gate(self):
        import base64
        state=d.empty_state()
        state['alerts']['forecast:'+'a'*40]={'status':'pending','attempted_at':'2026-10-14T12:00:00Z'}
        content={'content':base64.b64encode(json.dumps(state).encode()).decode()}
        with patch.object(d,'api',return_value=content),patch.object(d,'gh') as gh:
            with self.assertRaises(RuntimeError):d.enable()
        gh.assert_not_called()


if __name__=='__main__':unittest.main()
