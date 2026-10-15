using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace CodexReset;

internal sealed class WorkerSession
{
    private Process? process;
    private readonly string cancelFile;
    private readonly Action<JsonElement> receive;
    public bool Running => process is { HasExited: false };
    public bool Forced { get; private set; }
    public WorkerSession(string cancelFile, Action<JsonElement> receive)
    { this.cancelFile = cancelFile; this.receive = receive; }

    public async Task<int> RunAsync(string? offline)
    {
        string root = AppContext.BaseDirectory;
        var info = new ProcessStartInfo(Path.Combine(root, "worker", "reset-worker.exe")) {
            WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false, true),
            StandardOutputEncoding = new UTF8Encoding(false, true), StandardErrorEncoding = new UTF8Encoding(false, true)
        };
        var inherited = new Dictionary<string, string?>();
        foreach (string name in new[] { "SystemRoot", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "USERPROFILE" })
            inherited[name] = Environment.GetEnvironmentVariable(name);
        info.Environment.Clear();
        foreach (var item in inherited) if (item.Value != null) info.Environment[item.Key] = item.Value;
        info.Environment["PATH"] = Path.Combine(root, "tools") + ";" + Environment.GetFolderPath(Environment.SpecialFolder.System);
        info.Environment["PYTHONUTF8"] = "1"; info.Environment["PYTHONUNBUFFERED"] = "1";
        info.Environment["PYTHONIOENCODING"] = "utf-8";
        info.ArgumentList.Add("--cancel-file"); info.ArgumentList.Add(cancelFile);
        if (offline != null) { info.ArgumentList.Add("--offline-e2e"); info.ArgumentList.Add(offline); }
        process = new Process { StartInfo = info };
        if (!process.Start()) throw new IOException("WORKER_START_FAILED");
        process.StandardInput.AutoFlush = true; process.StandardInput.NewLine = "\n";
        // Drain and discard stderr; it is never shown, logged or saved.
        Task errors = Task.Run(async () => { char[] data = new char[4096]; while (await process.StandardError.ReadAsync(data) > 0) { } });
        var buffer = new StringBuilder(); char[] chunk = new char[2048];
        int count;
        while ((count = await process.StandardOutput.ReadAsync(chunk)) > 0)
        {
            for (int index = 0; index < count; index++)
            {
                char value = chunk[index];
                if (value == '\n')
                {
                    using var message = JsonDocument.Parse(buffer.ToString()); buffer.Clear();
                    receive(message.RootElement.Clone());
                }
                else { buffer.Append(value); if (buffer.Length > 131072) throw new InvalidDataException("UI_RESPONSE_LIMIT"); }
            }
        }
        if (buffer.Length != 0) throw new InvalidDataException("UI_RESPONSE_TRUNCATED");
        await process.WaitForExitAsync(); await errors;
        return process.ExitCode;
    }

    public async Task RespondAsync(int id, string value)
    {
        string line = JsonSerializer.Serialize(new { id, value });
        if (Encoding.UTF8.GetByteCount(line) > 8191) throw new InvalidDataException("UI_INPUT_LIMIT");
        if (process == null || process.HasExited) throw new IOException("WORKER_ENDED");
        await process.StandardInput.WriteLineAsync(line);
    }

    public async Task PauseAsync()
    {
        if (!Running) return;
        Directory.CreateDirectory(Path.GetDirectoryName(cancelFile)!);
        using (File.Create(cancelFile)) { }
        try { process!.StandardInput.Close(); } catch (IOException) { }
        if (await Task.WhenAny(process!.WaitForExitAsync(), Task.Delay(5000)) is Task winner && !process.HasExited)
        { Forced = true; process.Kill(entireProcessTree: true); await process.WaitForExitAsync(); }
    }
}

internal sealed class MainWindow : Window
{
    private readonly StackPanel content = new() { Margin = new Thickness(36, 24, 36, 24) };
    private readonly string? testMode;
    private readonly string? testOutput;
    private readonly List<string> stages = new();
    private WorkerSession? session;
    private int? prompt;
    private bool stopping, finished, done, closing, repaired, sawResume, sawCode, failed;
    private int doneCode = -1;
    private string title = "设置 Codex 提醒";
    private string detail = "连接 GitHub 和飞书。设置好后，每小时检查一次，符合条件才提醒你。";
    private string userCode = "";
    private string error = "";
    private string initial = "";
    private string placeholder = "";
    private bool back;
    private List<(string Label, string Value)> choices = new();
    private List<Uri> links = new();
    private Button? startButton, continueButton;
    private readonly List<Button> choiceButtons = new();
    private TextBox? field;
    public MainWindow(string? testMode, string? testOutput)
    {
        this.testMode = testMode; this.testOutput = testOutput;
        Title = "Codex 提醒设置 · Windows 测试版"; Width = 760; Height = 650; MinWidth = 540; MinHeight = 470;
        Background = new SolidColorBrush(Color.FromRgb(247, 249, 253));
        WindowStartupLocation = WindowStartupLocation.CenterScreen;
        Content = new ScrollViewer { Content = content, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        var logo = Path.Combine(AppContext.BaseDirectory, "ProjectLogo.png");
        if (File.Exists(logo)) Icon = new BitmapImage(new Uri(logo));
        Render();
        Loaded += async (_, _) => {
            if (testMode == null) return;
            Capture("welcome");
            startButton!.RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Button.ClickEvent));
            await Task.Delay(TimeSpan.FromSeconds(50));
            if (!closing) { failed = true; await Pause(); FinishTest("timeout"); }
        };
        Closing += (_, eventArgs) => {
            if (closing || session is not { Running: true }) return;
            eventArgs.Cancel = true;
            _ = CloseSafely();
        };
    }
    private async Task CloseSafely() { await Pause(); closing = true; Close(); }
    private TextBlock Text(string value, double size = 16, Brush? color = null) => new() {
        Text = value, FontSize = size, TextWrapping = TextWrapping.Wrap, LineHeight = size * 1.65,
        Foreground = color ?? new SolidColorBrush(Color.FromRgb(25, 38, 58)), Margin = new Thickness(0, 0, 0, 14)
    };
    private Button Button(string label, Func<Task> action, bool primary = false)
    {
        var button = new Button { Content = label, Padding = new Thickness(18, 10, 18, 10),
            FontSize = 15, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 10, 10),
            Background = primary ? new SolidColorBrush(Color.FromRgb(39, 97, 230)) : Brushes.White,
            Foreground = primary ? Brushes.White : Brushes.DarkSlateGray, BorderThickness = new Thickness(1) };
        button.Click += async (_, _) => { try { await action(); } catch { Fail("连接中断了。请关闭后重新打开。问题标识：UI_CONNECTION_FAILED"); await Pause(); } };
        return button;
    }
    private void Render()
    {
        content.Children.Clear(); choiceButtons.Clear(); field = null;
        var header = new StackPanel { Orientation = Orientation.Horizontal };
        string logo = Path.Combine(AppContext.BaseDirectory, "ProjectLogo.png");
        if (File.Exists(logo)) header.Children.Add(new Image { Source = new BitmapImage(new Uri(logo)), Width = 42, Height = 42, Margin = new Thickness(0, 0, 14, 12) });
        header.Children.Add(Text("Codex 提醒", 19)); content.Children.Add(header);
        content.Children.Add(Text(testMode == null ? "Windows 测试版 · 仅在你确认后开启" : "离线自动测试 · 使用模拟服务，未连接账号", 12, Brushes.SlateGray));
        content.Children.Add(Text(title, 28));
        // Selectable copy for issue identifiers and instructions.
        content.Children.Add(new TextBox { Text = detail, IsReadOnly = true, BorderThickness = new Thickness(0),
            Background = Brushes.Transparent, FontSize = 16, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 18) });
        if (userCode != "") {
            content.Children.Add(Text(userCode, 28));
            content.Children.Add(Button("复制授权码", () => { Clipboard.SetText(userCode); return Task.CompletedTask; }, true));
        }
        foreach (Uri url in links) content.Children.Add(Button(LinkLabel(url), () => {
            if (testMode == null) Process.Start(new ProcessStartInfo(url.AbsoluteUri) { UseShellExecute = true });
            return Task.CompletedTask;
        }));
        if (prompt != null && choices.Count == 0) {
            if (error != "") content.Children.Add(Text(error, 14, Brushes.Firebrick));
            if (placeholder != "") content.Children.Add(Text(placeholder, 14, Brushes.SlateGray));
            field = new TextBox { Text = initial, FontSize = 17, Padding = new Thickness(10), Margin = new Thickness(0, 0, 0, 14) };
            field.KeyDown += async (_, e) => { if (e.Key == System.Windows.Input.Key.Enter && field.Text.Trim() != "") await Respond(field.Text); };
            content.Children.Add(field);
            if (back) content.Children.Add(Button("返回上一步", () => Respond("__back__")));
            continueButton = Button("继续", () => field.Text.Trim() != "" ? Respond(field.Text) : Task.CompletedTask, true);
            content.Children.Add(continueButton); field.Focus();
        }
        foreach (var choice in choices) {
            var button = Button(choice.Label, () => Respond(choice.Value), choiceButtons.Count == 0);
            choiceButtons.Add(button); content.Children.Add(button);
        }
        if (session == null) { startButton = Button("开始设置", Start, true); content.Children.Add(startButton); }
        else if (!done && !finished && !failed) {
            if (prompt == null) content.Children.Add(Text(stopping ? "正在结束本地连接并保留进度…" : "请稍等…", 14, Brushes.SlateGray));
            var pause = Button("暂停设置，稍后继续", Pause); pause.IsEnabled = !stopping; content.Children.Add(pause);
        }
        if (done || finished || failed) {
            content.Children.Add(Button("关闭", () => { closing = true; Close(); return Task.CompletedTask; }));
            foreach (string address in new[] { "https://github.com/settings/installations", "https://github.com/settings/apps/authorizations" }) {
                var uri = new Uri(address); content.Children.Add(Button(address.EndsWith("authorizations") ? "撤销安装工具的用户授权" : "撤销安装工具的仓库安装", () => {
                    if (testMode == null) Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); return Task.CompletedTask;
                }));
            }
            content.Children.Add(Text("暂停不会撤回已提交的操作。如果上次提交结果不明确，下次会先请你核对。撤销安装工具权限后，已配置的提醒仍保留。", 12, Brushes.SlateGray));
        }
        content.Children.Add(Text("概率来自第三方，不代表 OpenAI 官方保证。连接和测试都需要你本人确认。", 12, Brushes.SlateGray));
    }
    private static string LinkLabel(Uri url) => url.Host == "open.feishu.cn" ? "打开飞书官方页面" : url.AbsolutePath switch {
        "/login/device" => "打开 GitHub，输入代码", "/settings/installations" => "核对仓库安装", _ when url.AbsolutePath.EndsWith("/fork") => "复制到我的 GitHub", _ when url.AbsolutePath.EndsWith("/permissions") => "核对应用权限", _ => "打开 GitHub 官方页面"
    };
    private async Task Start()
    {
        if (session != null) return;
        string local = Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (local == "") { Fail("无法找到本机进度目录。问题标识：LOCAL_APP_DATA_REQUIRED"); return; }
        string cancel = Path.Combine(local, "Codex Reset Installer", "sessions", Guid.NewGuid().ToString("N") + ".cancel");
        session = new WorkerSession(cancel, Receive); title = "正在准备"; detail = "检查安装文件…"; Render();
        int exit;
        try { exit = await session.RunAsync(testMode is "full" or "cancel" or "resume" ? testMode : null); }
        catch { exit = -1; if (!stopping) Fail("无法启动安装文件，请关闭后重新打开下载的 EXE。问题标识：WORKER_CONNECTION_FAILED"); await session.PauseAsync(); }
        try { File.Delete(cancel); } catch (IOException) { }
        if (stopping) { done = true; title = "设置已暂停"; detail = "进度已保留。重新打开后可以继续；已提交的操作不会撤回，请先核对不明确的结果。"; }
        else if (!done) Fail("安装连接已结束。请重新打开继续。问题标识：WORKER_ENDED");
        Render();
        if (testMode != null) {
            bool pass = testMode switch {
                "full" => exit == 0 && doneCode == 0 && finished && sawCode && repaired,
                "cancel" => stopping && exit == 130 && !session.Forced,
                "resume" => exit == 0 && doneCode == 0 && finished && sawResume,
                "startup" => stopping && exit is 2 or 130 && !session.Forced,
                _ => false
            };
            failed |= !pass; FinishTest(pass ? "passed" : "failed");
        }
    }
    private void Receive(JsonElement message)
    {
        string type = message.GetProperty("type").GetString()!;
        if (stopping) return;
        userCode = "";
        if (type == "prompt") {
            links.RemoveAll(u => u.AbsolutePath is "/login/device" or "/page/cli");
            title = message.GetProperty("title").GetString()!; detail = message.GetProperty("text").GetString()!;
            prompt = message.GetProperty("id").GetInt32(); initial = message.GetProperty("initial").GetString()!;
            placeholder = message.GetProperty("placeholder").GetString()!;
            error = message.GetProperty("error").GetString()!; back = message.GetProperty("back").GetBoolean();
            choices = message.GetProperty("choices").EnumerateArray().Select(c => (c.GetProperty("label").GetString()!, c.GetProperty("value").GetString()!)).ToList();
            if (title == "上次保存中断了") sawResume = true;
        }
        else if (type == "notice") {
            string stage = message.GetProperty("stage").GetString()!; stages.Add(stage);
            if (message.TryGetProperty("links", out var urls) && urls.GetArrayLength() != 0)
                links = urls.EnumerateArray().Select(u => Uri.TryCreate(u.GetString(), UriKind.Absolute, out var uri) ? uri : null)
                    .Where(u => u is { Scheme: "https", IsDefaultPort: true, UserInfo: "" } && u.Host is "github.com" or "open.feishu.cn").Select(u => u!).ToList();
            if (stage == "github" && message.TryGetProperty("user_code", out var code) && Regex.IsMatch(code.GetString() ?? "", "\\A[A-Z0-9]{4}-[A-Z0-9]{4}\\z") && links.Any(u => u.AbsoluteUri == "https://github.com/login/device")) {
                userCode = code.GetString()!; sawCode = true;
                if (testMode != null) { Clipboard.SetText(userCode); if (Clipboard.GetText() != userCode) failed = true; Clipboard.Clear(); }
            }
            string text = message.GetProperty("text").GetString()!;
            if (text != "") { title = message.GetProperty("title").GetString()!; detail = text; }
            if (stage == "enabled") { finished = true; links.Clear(); }
            if (stage == "stopped") { prompt = null; choices.Clear(); links.Clear(); }
        }
        else if (type == "done") { done = true; doneCode = message.GetProperty("code").GetInt32(); prompt = null; choices.Clear(); links.Clear(); }
        else throw new InvalidDataException("UI_RESPONSE_TYPE");
        Render();
        if (testMode != null && userCode != "") Capture("device");
        if (testMode != null) _ = AdvanceTest(type, message.Clone());
    }
    private async Task Respond(string value)
    {
        if (prompt is not int id || session == null) return;
        prompt = null; choices.Clear(); initial = ""; userCode = ""; error = ""; Render();
        await session.RespondAsync(id, value);
    }
    private async Task Pause()
    {
        if (stopping || session == null) return;
        stopping = true; prompt = null; choices.Clear(); links.Clear(); userCode = ""; title = "正在暂停"; Render();
        await session.PauseAsync();
    }
    private void Fail(string text) { failed = true; title = "暂时停在这里"; detail = text; prompt = null; choices.Clear(); links.Clear(); userCode = ""; Render(); }
    private async Task AdvanceTest(string type, JsonElement message)
    {
        await Dispatcher.Yield(DispatcherPriority.ApplicationIdle);
        try {
            if (type == "notice" && message.GetProperty("stage").GetString() == "offline-cancel-ready") { Capture("before-pause"); await Pause(); return; }
            if (type == "notice" && userCode != "") Capture("device");
            if (type != "prompt" || stopping) return;
            if (testMode == "startup") { Capture("first-prompt"); await Pause(); return; }
            if (prompt != message.GetProperty("id").GetInt32()) return;
            Capture("prompt-" + prompt);
            if (choices.Count > 0) {
                int index = title == "准备存放设置的地方" || (testMode == "resume" && choices.Any(c => c.Value == "reuse")) ? choices.FindIndex(c => c.Value == "reuse") : 0;
                choiceButtons[index].RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Button.ClickEvent));
            }
            else {
                if (title == "设置放在哪里？" && !repaired && testMode == "full") { field!.Text = "https://github.com/ExampleUser/codex-reset-monitor"; repaired = true; }
                else field!.Text = title == "给提醒机器人起个名字" ? "我的 Codex 小助手" : title == "填写机器人编号" ? "cli_xxxxxxxxxxxxxxxx" : "ExampleUser/codex-reset-monitor";
                continueButton!.RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Button.ClickEvent));
            }
        } catch { failed = true; await Pause(); }
    }
    private void Capture(string name)
    {
        if (testOutput == null) return;
        Directory.CreateDirectory(testOutput); UpdateLayout();
        var bitmap = new RenderTargetBitmap((int)ActualWidth, (int)ActualHeight, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(this); var png = new PngBitmapEncoder(); png.Frames.Add(BitmapFrame.Create(bitmap));
        using var output = File.Create(Path.Combine(testOutput, name + ".png")); png.Save(output);
    }
    private void FinishTest(string result)
    {
        if (closing) return;
        Capture("final");
        if (testOutput != null) File.WriteAllText(Path.Combine(testOutput, "RESULT.json"), JsonSerializer.Serialize(new { mode = testMode, result, passed = !failed, doneCode, finished, sawResume, sawCode, repaired, forced = session?.Forced, stages, services = "synthetic offline fixtures; no live account E2E" }), new UTF8Encoding(false));
        closing = true; Application.Current.Shutdown(failed ? 1 : 0);
    }
}

internal static class Program
{
    [STAThread]
    public static int Main(string[] args)
    {
        string? mode = null, output = null;
        for (int index = 0; index < args.Length; index++) {
            if (args[index] == "--offline-ui-test" && index + 1 < args.Length) mode = args[++index];
            else if (args[index] == "--test-output" && index + 1 < args.Length) output = Path.GetFullPath(args[++index]);
            else return 2;
        }
        if (mode != null && (output == null || mode is not ("full" or "cancel" or "resume" or "startup"))) return 2;
        using var mutex = new Mutex(true, "Local\\CodexResetInstaller", out bool acquired);
        if (!acquired) { MessageBox.Show("设置窗口已经打开，请回到原来的窗口继续。", "Codex 提醒"); return 2; }
        try { var app = new Application(); return app.Run(new MainWindow(mode, output)); }
        finally { mutex.ReleaseMutex(); }
    }
}
