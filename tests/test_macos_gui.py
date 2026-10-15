"""Native model regression, synthetic codes and a private pasteboard only."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swiftc'),'requires macOS Swift/AppKit')
class NativeGUIModelTest(unittest.TestCase):
    def test_fragmented_device_code_copy_and_lifecycle(self):
        source=(Path(__file__).resolve().parents[1]/'macos/InstallerApp.swift').read_text()
        model=source.split('\nstruct ContentView: View {',1)[0]
        harness=r'''
@main struct Checks {
    @MainActor static func main() throws {
        let wizard = Wizard()
        func send(_ message: [String:Any]) throws {
            let data = try JSONSerialization.data(withJSONObject:message) + Data([10])
            // Simulate arbitrary pipe chunks, including splits within UTF-8.
            for byte in data { wizard.consume(Data([byte])) }
        }
        func device() throws {
            try send(["type":"notice","stage":"github","title":"连接 GitHub","text":"仅本人使用",
                      "links":["https://github.com/login/device"],"user_code":"ABCD-1234"])
        }
        try device()
        precondition(wizard.userCode == "ABCD-1234")
        let pasteboard = NSPasteboard.withUniqueName()
        defer { pasteboard.releaseGlobally() }
        wizard.copyUserCode(to:pasteboard)
        precondition(pasteboard.string(forType:.string) == "ABCD-1234" && wizard.codeCopied)
        try send(["type":"prompt","id":1,"title":"这是你的账号吗？","text":"ExampleUser",
                  "choices":[["label":"是我的账号","value":"ExampleUser"]]])
        precondition(wizard.userCode.isEmpty && !wizard.codeCopied && !wizard.waiting)
        precondition(!wizard.links.contains { $0.path == "/login/device" })
        // A stale copy action cannot overwrite anything after leaving the code page.
        pasteboard.clearContents(); pasteboard.setString("keep this",forType:.string)
        wizard.copyUserCode(to:pasteboard)
        precondition(pasteboard.string(forType:.string) == "keep this")
        try device()
        try send(["type":"notice","stage":"feishu","text":"等待飞书",
                  "links":["https://open.feishu.cn/page/cli?user_code=SYNTHETIC"]])
        precondition(wizard.userCode.isEmpty)
        try send(["type":"prompt","id":2,"title":"机器人准备好了吗？","text":"请核对","choices":[]])
        precondition(wizard.links.isEmpty)
        try device(); try send(["type":"done","code":0]); precondition(wizard.userCode.isEmpty)
        let invalid = Wizard()
        for code in ["bad", "ABCD-1234\n", "ABCD-1234-extra"] {
            let data = try JSONSerialization.data(withJSONObject:["type":"notice","stage":"github","links":["https://github.com/login/device"],"user_code":code]) + Data([10])
            invalid.consume(data); precondition(invalid.userCode.isEmpty)
        }
        try device(); wizard.fail("safe synthetic error"); precondition(wizard.userCode.isEmpty)
        print("Native copy / fragmented IPC / stale-code checks passed")
    }
}
'''
        with tempfile.TemporaryDirectory(prefix='codex-gui-test-') as directory:
            path=Path(directory);swift=path/'Checks.swift';swift.write_text(model+harness)
            executable=path/'checks'
            subprocess.run(['swiftc','-parse-as-library','-module-cache-path',str(path/'module-cache'),
                            str(swift),'-o',str(executable)],check=True,capture_output=True,text=True,timeout=90)
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('checks passed',result.stdout)
