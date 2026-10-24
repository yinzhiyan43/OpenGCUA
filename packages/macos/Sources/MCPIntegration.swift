import AppKit
import SwiftUI
import Darwin

/// MCP starts without desktop UI; model configuration is read only for operations.
enum MCPCommand {
    @MainActor static func runIfRequested() -> Int32? {
        let args = Array(CommandLine.arguments.dropFirst())
        guard let command = args.first,
              ["--mcp", "--mcp-host", "--install-codex-mcp"].contains(command) else { return nil }
        guard let resources = Bundle.main.resourceURL, args.count == 1 else { return 64 }
        let python = resources.appendingPathComponent("python/bin/python3")
        var environment = ProcessInfo.processInfo.environment
        for key in ["PYTHONPATH", "PYTHONHOME", "OPENGCUA_EMBEDDED_HOST", "OPENGCUA_DRIVER_APP", "API_KEY", "DASHSCOPE_API_KEY", "MAC_AGENT_CONFIG", "GENERALIST_DRIVER_SOCKET"] {
            environment.removeValue(forKey: key)
            unsetenv(key)
        }
        if command == "--mcp" {
            // A connection needs only a stdio channel, not another native app
            // waiting for Python. Device access stays in the single signed host.
            let arguments = [python.path, "-I", "-B", "-u", "-m", "opengcua_mcp.launcher"]
            var argv = arguments.map { strdup($0) } + [nil]
            defer { for argument in argv { free(argument) } }
            argv.withUnsafeMutableBufferPointer { _ = execv(python.path, $0.baseAddress!) }
            return 1
        }
        // Legacy launch requests enter the same normal application lifecycle.
        if command == "--mcp-host" { return nil }
        NSApplication.shared.setActivationPolicy(.prohibited)
        let child = Process()
        child.executableURL = python
        child.arguments = ["-I", "-B", "-u", "-m", "opengcua_mcp.launcher", "--install-codex"]
        child.environment = environment
        child.standardInput = FileHandle.standardInput
        child.standardOutput = FileHandle.standardOutput
        child.standardError = FileHandle.standardError
        do {
            try child.run()
            child.waitUntilExit()
            return child.terminationStatus
        } catch {
            try? FileHandle.standardError.write(contentsOf: Data("OpenGCUA MCP: \(error.localizedDescription)\n".utf8))
            return 1
        }
    }
}

@MainActor struct MCPIntegrationView: View {
    @State private var busy = false
    @State private var message = ""
    @State private var failed = false

    var body: some View {
        Form {
            Section("Codex") {
                Text(L("通过 MCP 调用已配置的模型，完成有明确边界的界面操作。当前聊天负责整体决策；底层工具保留作诊断。"))
                Button(busy ? L("正在连接…") : L("连接到 Codex")) { connect() }
                    .disabled(busy)
                if !message.isEmpty {
                    Text(message).font(.callout).foregroundStyle(failed ? .orange : .secondary)
                        .textSelection(.enabled)
                }
            }
            Section(L("共用安装与权限")) {
                Text(L("MCP 已内置于本应用，与桌面功能共用 OpenGCUA 的辅助功能和屏幕录制权限。更新应用时一并更新。"))
                Text(L("Codex 的工具操作确认由 Codex 管理。完成任务后释放会话，以便其他任务使用电脑。"))
                    .font(.callout).foregroundStyle(.secondary)
            }
        }.formStyle(.grouped)
    }

    private func connect() {
        busy = true; message = ""
        let executable = Bundle.main.executableURL!
        Task {
            let result = await Task.detached { () -> (Bool, String) in
                let process = Process(), output = Pipe()
                process.executableURL = executable
                process.arguments = ["--install-codex-mcp"]
                process.standardOutput = output; process.standardError = output
                do {
                    try process.run()
                    let data = output.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()
                    return (process.terminationStatus == 0, String(data: data, encoding: .utf8) ?? "")
                } catch { return (false, error.localizedDescription) }
            }.value
            busy = false; failed = !result.0
            message = result.0 ? L("已接入 Codex。重新打开聊天或重启 Codex 以加载工具。") : result.1
        }
    }
}
