import AppKit
import ApplicationServices
import Darwin

/// Read Dock accessibility inside the already-authorized, signed application.
/// Only OpenGCUA items are recorded; no desktop image or other app titles.
@MainActor final class MCPDockStatus {
    private var timer: Timer?

    func start() {
        record()
        let timer = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.record() }
        }
        self.timer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private func attribute(_ element: AXUIElement, _ name: String) -> (AXError, CFTypeRef?) {
        var value: CFTypeRef?
        let error = AXUIElementCopyAttributeValue(element, name as CFString, &value)
        return (error, value)
    }

    private func dock() -> [String: Any] {
        guard AXIsProcessTrusted() else {
            return ["status": "permission_denied", "count": NSNull()]
        }
        guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.dock").first else {
            return ["status": "not_running", "count": NSNull()]
        }
        let element = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(element, 0.5)
        let (error, value) = attribute(element, kAXChildrenAttribute)
        guard error == .success, let children = value as? [AXUIElement] else {
            return ["status": "unavailable", "ax_error": error.rawValue, "count": NSNull()]
        }
        var items: [[String: Any]] = []
        var lists = 0
        for child in children {
            guard attribute(child, kAXRoleAttribute).1 as? String == kAXListRole else { continue }
            lists += 1
            let (listError, listValue) = attribute(child, kAXChildrenAttribute)
            guard listError == .success, let entries = listValue as? [AXUIElement] else {
                return ["status": "unavailable", "ax_error": listError.rawValue, "count": NSNull()]
            }
            for entry in entries {
                let title = attribute(entry, kAXTitleAttribute).1 as? String ?? ""
                let url = attribute(entry, kAXURLAttribute).1 as? URL
                guard title == "OpenGCUA" || url?.standardizedFileURL == Bundle.main.bundleURL.standardizedFileURL else { continue }
                var item: [String: Any] = ["title": title]
                if let url { item["url"] = url.absoluteString }
                if let running = attribute(entry, "AXIsApplicationRunning").1 as? Bool {
                    item["running"] = running
                }
                if let position = attribute(entry, kAXPositionAttribute).1,
                   let size = attribute(entry, kAXSizeAttribute).1,
                   CFGetTypeID(position) == AXValueGetTypeID(),
                   CFGetTypeID(size) == AXValueGetTypeID() {
                    var point = CGPoint.zero
                    var dimensions = CGSize.zero
                    if AXValueGetValue(position as! AXValue, .cgPoint, &point),
                       AXValueGetValue(size as! AXValue, .cgSize, &dimensions) {
                        item["bounds"] = ["x": point.x, "y": point.y,
                                          "width": dimensions.width, "height": dimensions.height]
                    }
                }
                items.append(item)
            }
        }
        guard lists > 0 else { return ["status": "unavailable", "count": NSNull()] }
        return ["status": "ok", "count": items.count, "items": items, "source": "Dock AXChildren"]
    }

    private func record() {
        let directory = URL(fileURLWithPath: "/private/tmp/opengcua-service-\(getuid())", isDirectory: true)
        var info = stat()
        guard lstat(directory.path, &info) == 0, info.st_uid == getuid(),
              (info.st_mode & S_IFMT) == S_IFDIR, (info.st_mode & 0o777) == 0o700 else { return }
        let visible = NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier ?? "app.generalist.mac")
            .filter { $0.activationPolicy == .regular }.map { Int($0.processIdentifier) }
        let report: [String: Any] = [
            "host_pid": Int(getpid()), "captured_at": Date().timeIntervalSince1970,
            "accessibility_trusted": AXIsProcessTrusted(), "dock": dock(),
            "dock_visible_pids": visible
        ]
        let temporary = directory.appendingPathComponent(".dock-\(UUID().uuidString).tmp")
        defer { try? FileManager.default.removeItem(at: temporary) }
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
            try data.write(to: temporary, options: .withoutOverwriting)
            guard chmod(temporary.path, 0o600) == 0 else { return }
            _ = rename(temporary.path, directory.appendingPathComponent("dock-status.json").path)
        } catch {
            // Report absence/staleness through MCP, never fabricate a zero count.
        }
    }
}
