import SwiftUI
import AppKit

struct Choice: Identifiable { let id = UUID(); let label: String; let value: String }
@MainActor final class Wizard: ObservableObject {
    @Published var title = "设置 Codex 提醒"
    @Published var detail = "连接 GitHub 和飞书。设置好后，每小时检查一次，符合条件才提醒你。"
    @Published var choices: [Choice] = []
    @Published var field = ""
    @Published var placeholder = ""
    @Published var links: [URL] = []
    @Published var active = false
    @Published var waiting = false
    @Published var finished = false
    @Published var preview = false
    @Published var problem = ""
    @Published var stopping = false
    @Published var fieldError = ""
    @Published var canGoBack = false
    var promptID: Int?
    var process: Process?
    var input: FileHandle?
    var buffer = Data()

    init() {
        if let index = CommandLine.arguments.firstIndex(of: "--show-page"), CommandLine.arguments.count > index + 1 {
            preview = true; active = true
            switch CommandLine.arguments[index + 1] {
            case "github": title = "连接 GitHub"; detail = "在 GitHub 官方页面，只选择存放提醒设置的仓库。\n完成连接后，再点下面的按钮。"; choices = [Choice(label: "已经连接，继续", value: "")]
            case "feishu": title = "连接飞书"; detail = "我们会打开飞书官方页面。\n按页面提示，连接你自己的提醒机器人。"; choices = [Choice(label: "打开飞书授权", value: "")]
            case "test": title = "发一条测试"; detail = "先给你发送一条写着“安装测试”的消息。\n收到后再开启提醒。"; choices = [Choice(label: "发一条测试", value: ""),Choice(label:"先不开启",value:"")]
            case "done": title = "提醒已开启"; detail = "设置完成。每小时检查一次，符合条件时会通知你。\n第三方估计不保证个人额度恢复。"; finished = true
            default: active = false
            }
        }
    }
    func start() {
        guard !preview else { return }
        guard let resources = Bundle.main.resourceURL else { fail("找不到安装文件，请重新下载完整安装包。"); return }
        let executable = resources.appendingPathComponent("worker/reset-worker")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else { fail("安装包不完整，请重新下载。"); return }
        let task = Process(); let incoming = Pipe(); let outgoing = Pipe(); let errors = Pipe()
        task.executableURL = executable
        task.currentDirectoryURL = resources
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = resources.appendingPathComponent("tools").path + ":/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PYTHONUNBUFFERED"] = "1"
        task.environment = environment
        task.standardInput = incoming; task.standardOutput = outgoing; task.standardError = errors
        input = incoming.fileHandleForWriting; process = task
        outgoing.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            Task { @MainActor in self.consume(data) }
        }
        errors.fileHandleForReading.readabilityHandler = { handle in
            if handle.availableData.isEmpty { handle.readabilityHandler = nil }
        }
        task.terminationHandler = { task in
            Task { @MainActor in
                self.waiting = false; self.promptID = nil; self.choices = []; self.links = []
                if self.stopping { self.title = "设置已暂停"; self.detail = "进度已保留。重新打开后可以继续；如果上次提交结果不明确，会先请你核对。"; self.problem = self.detail }
                if self.promptID == nil && !self.finished && self.problem.isEmpty {
                    self.title = task.terminationStatus == 0 ? "设置已结束" : "暂时没有完成"
                    self.detail = "下次打开可以继续。暂停不会撤回已经提交的操作。"
                }
            }
        }
        do { try task.run(); active = true; waiting = true; title = "正在准备"; detail = "检查安装文件…" }
        catch { fail("无法启动安装文件。问题标识：WORKER_START_FAILED") }
    }
    func consume(_ data: Data) {
        buffer.append(data)
        if buffer.count > 131072 { fail("安装过程返回了无效数据。问题标识：UI_RESPONSE_LIMIT"); process?.terminate(); return }
        while let end = buffer.firstIndex(of: 10) {
            let line = buffer.prefix(upTo: end); buffer.removeSubrange(...end)
            guard let message = try? JSONSerialization.jsonObject(with: line) as? [String: Any], let type = message["type"] as? String else {
                fail("安装过程返回了无效数据。问题标识：UI_RESPONSE_INVALID"); process?.terminate(); return
            }
            if type == "prompt" {
                title = message["title"] as? String ?? "继续设置"; detail = message["text"] as? String ?? ""
                fieldError = message["error"] as? String ?? ""; canGoBack = message["back"] as? Bool ?? false
                promptID = message["id"] as? Int; field = message["initial"] as? String ?? ""; placeholder = message["placeholder"] as? String ?? ""
                choices = (message["choices"] as? [[String: String]] ?? []).compactMap { item in
                    guard let label = item["label"], let value = item["value"] else { return nil }; return Choice(label: label,value: value)
                }; waiting = false
                // Safe native smoke hooks stop at the first local, unconfigured page.
                if title == "准备连接 GitHub" && promptID == 1 {
                    if CommandLine.arguments.contains("--smoke-cancel") { respond("cancel") }
                    if CommandLine.arguments.contains("--smoke-pause") { stop() }
                }
            } else if type == "notice" {
                if finished && message["stage"] as? String == "cleanup" { continue }
                if let urls = message["links"] as? [String], !urls.isEmpty {
                    links = urls.compactMap { value in
                        guard let url = URL(string:value), url.scheme == "https", ["github.com","open.feishu.cn"].contains(url.host ?? "") else { return nil }; return url
                    }
                }
                if let text = message["text"] as? String, !text.isEmpty {
                    detail = text; title = message["title"] as? String ?? "继续设置"
                }
                if message["stage"] as? String == "enabled" { finished = true; links = [] }
                if message["stage"] as? String == "stopped" { problem = detail; promptID = nil; choices = []; links = [] }
            } else if type == "done" {
                waiting = false; promptID = nil; choices = []; links = []
                if !finished && problem.isEmpty { title = "设置已结束"; detail = "下次打开可以继续设置。暂停不会撤回已经提交的操作。"; problem = detail }
            }
        }
    }
    func respond(_ value: String) {
        guard !preview, let id = promptID, let input = input else { return }
        guard let raw = try? JSONSerialization.data(withJSONObject: ["id":id,"value":value]), raw.count <= 8191 else { return }
        do { try input.write(contentsOf: raw + Data([10])); promptID = nil; choices = []; field = ""; waiting = true }
        catch { fail("连接中断了。请关闭后重新打开，继续设置。") }
    }
    func fail(_ text: String) { problem = text; detail = text; title = "暂时停在这里"; waiting = false }
    func stop() {
        guard let process = process, process.isRunning else { return }
        stopping = true; promptID = nil; choices = []; links = []; waiting = true
        title = "正在暂停"; detail = "正在结束本地连接并保留进度…"
        process.interrupt()
        try? input?.close()
    }
    func linkLabel(_ url: URL) -> String {
        if url.host == "open.feishu.cn" { return "打开飞书官方页面" }
        if url.path.hasSuffix("/permissions") { return "核对应用权限" }
        if url.path == "/settings/installations" { return "核对仓库安装" }
        if url.path.hasSuffix("/fork") { return "复制到我的 GitHub" }
        if url.path == "/login/device" { return "打开 GitHub，输入代码" }
        if url.path == "/settings/apps/new" { return "打开 GitHub 创建页" }
        return "打开 GitHub 官方页面"
    }
}

struct ContentView: View {
    @StateObject private var wizard = Wizard()
    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack {
                Image(systemName: wizard.finished ? "checkmark.circle.fill" : "bell.badge.fill")
                    .font(.system(size:28)).foregroundStyle(wizard.finished ? Color.green : Color.blue)
                Text("Codex 提醒").font(.headline)
                Spacer()
                Text(wizard.preview ? "页面预览 · 未连接账号" : "仅在你确认后开启").font(.caption).foregroundStyle(.secondary)
            }
            Divider()
            ScrollView { VStack(alignment: .leading, spacing: 18) {
            Text(wizard.title).font(.system(size:29,weight:.semibold))
            Text(wizard.detail).font(.system(size:15)).lineSpacing(7).fixedSize(horizontal:false,vertical:true).textSelection(.enabled)
            if wizard.waiting { HStack { ProgressView().controlSize(.small); Text("请稍等…").foregroundStyle(.secondary) } }
            ForEach(wizard.links, id: \.absoluteString) { url in
                Button(wizard.linkLabel(url)) { NSWorkspace.shared.open(url) }
                    .disabled(wizard.preview)
            }
            if wizard.promptID != nil && wizard.choices.isEmpty {
                if !wizard.fieldError.isEmpty { Text(wizard.fieldError).foregroundStyle(.red).font(.body) }
                TextField(wizard.placeholder, text:$wizard.field).textFieldStyle(.roundedBorder).font(.body)
                    .onSubmit { if !wizard.field.trimmingCharacters(in:.whitespaces).isEmpty { wizard.respond(wizard.field) } }
                if wizard.canGoBack { Button("返回上一步") { wizard.respond("__back__") } }
                Button("继续") { wizard.respond(wizard.field) }.buttonStyle(.borderedProminent).disabled(wizard.field.trimmingCharacters(in:.whitespaces).isEmpty)
            }
            HStack(spacing:12) {
                if !wizard.active {
                    Button("开始设置") { wizard.start() }.buttonStyle(.borderedProminent).controlSize(.large)
                }
                ForEach(wizard.choices) { choice in
                    Button(choice.label) { wizard.respond(choice.value) }.controlSize(.large).disabled(wizard.preview)
                }
                if wizard.finished || !wizard.problem.isEmpty { Button("关闭") { NSApplication.shared.terminate(nil) }.controlSize(.large) }
            }
            if wizard.active && !wizard.finished && wizard.problem.isEmpty {
                Button("暂停设置，稍后继续") { wizard.stop() }.disabled(wizard.stopping)
            }
            if wizard.finished || !wizard.problem.isEmpty {
                Link("撤销安装工具的 GitHub 安装", destination: URL(string:"https://github.com/settings/installations")!)
                Link("撤销安装工具的 GitHub 用户授权", destination: URL(string:"https://github.com/settings/apps/authorizations")!)
                Text("仅撤销安装工具的权限；已配置的提醒仍保留。暂停不会撤回已经提交的操作。").font(.caption).foregroundStyle(.secondary)
            }
            }.frame(maxWidth:.infinity,alignment:.leading) }
            Spacer(minLength:0)
            Divider()
            Text("概率来自第三方，不代表 OpenAI 官方保证。连接和测试都需要你本人确认。")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(32).frame(width:660,height:490).background(Color(NSColor.windowBackgroundColor))
        .onAppear {
            if CommandLine.arguments.contains("--smoke-start") { wizard.start() }
            if let index = CommandLine.arguments.firstIndex(of: "--capture"), CommandLine.arguments.count > index + 1 {
                let destination = CommandLine.arguments[index + 1]
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                    if let view = NSApplication.shared.windows.first(where: { $0.isVisible })?.contentView,
                       let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
                        view.cacheDisplay(in: view.bounds, to: bitmap)
                        if let data = bitmap.representation(using: .png, properties: [:]) { try? data.write(to: URL(fileURLWithPath: destination)) }
                    }
                    if CommandLine.arguments.contains("--quit-after-capture") { NSApplication.shared.terminate(nil) }
                }
            }
        }
        .onDisappear { wizard.stop() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in wizard.stop() }
    }
}
@main struct InstallerApp: App {
    var body: some Scene {
        WindowGroup("Codex 提醒设置") { ContentView() }
            .windowResizability(.contentSize)
    }
}
