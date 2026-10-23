import AppKit
import SwiftUI

/// Both MCP modes enter before the UI/runtime/keychain are initialized.
enum MCPCommand {
    static func runIfRequested() -> Int32? {
        let args = Array(CommandLine.arguments.dropFirst())
        guard let command = args.first,
              ["--mcp", "--mcp-host", "--install-codex-mcp"].contains(command) else { return nil }
        guard let resources = Bundle.main.resourceURL,
              (command == "--mcp-host" ? args.count == 2 : args.count == 1) else { return 64 }
        let child = Process()
        child.executableURL = resources.appendingPathComponent("python/bin/python3")
        child.arguments = ["-I", "-B", "-u", "-m", "opengcua_mcp.launcher"]
        if command == "--mcp-host" { child.arguments! += ["--host", args[1]] }
        if command == "--install-codex-mcp" { child.arguments! += ["--install-codex"] }
        var environment = ProcessInfo.processInfo.environment
        for key in ["PYTHONPATH", "PYTHONHOME", "OPENGCUA_EMBEDDED_HOST", "OPENGCUA_DRIVER_APP"] {
            environment.removeValue(forKey: key)
        }
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
                Text(L("通过 MCP 使用 OpenGCUA 的查看、点击、输入和滚动能力。无需额外模型密钥。"))
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
