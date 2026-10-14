"""Real zipapp/PTY regression: no mocks of packaged resource loading."""
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest
from scripts.build_release import build

class ZipappStartupTest(unittest.TestCase):
    def test_real_zipapp_first_start_from_another_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);out=root/'build';build(out)
            working=root/'user-home';working.mkdir()
            tools=root/'bin';tools.mkdir();gh=tools/'gh';gh.write_text('#!/bin/sh\nexit 99\n');gh.chmod(0o700)
            master,slave=pty.openpty()
            process=None
            try:
                process=subprocess.Popen([sys.executable,str(out/'codex-reset-install.pyz')],cwd=working,
                    stdin=slave,stdout=slave,stderr=slave,env=dict(os.environ,PATH=str(tools)))
                os.close(slave);slave=None
                os.write(master,b'Y\ncancel\n')
                deadline=time.monotonic()+10;output=b''
                while time.monotonic()<deadline:
                    if select.select([master],[],[],0.1)[0]:
                        try:part=os.read(master,65536)
                        except OSError:break
                        if not part:break
                        output+=part
                    if process.poll() is not None:break
                process.wait(timeout=2)
                text=output.decode(errors='replace')
                self.assertIn('首次配置 GitHub App',text)
                self.assertIn('USER_CANCELLED',text)
                self.assertNotIn('UNEXPECTED_ERROR',text)
                self.assertFalse((working/'.codex-reset-setup').exists())
            finally:
                if process is not None and process.poll() is None:process.kill();process.wait()
                os.close(master)
                if slave is not None:os.close(slave)
    def test_resource_missing_and_present_in_real_zipapp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);build(root)
            code="import sys;sys.path.insert(0,sys.argv[1]);from installer.resources import read_bundled;assert read_bundled('public_app.json') is None;assert read_bundled('trusted_files.json')"
            done=subprocess.run([sys.executable,'-I','-S','-c',code,str(root/'codex-reset-install.pyz')],capture_output=True)
            self.assertEqual(done.returncode,0,done.stderr.decode())
