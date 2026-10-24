import Foundation

enum AppError: Error { case message(String) }

@main enum ConfigContracts {
    @MainActor static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("local.json")
        var preferences = try JSONDecoder().decode(Preferences.self, from: Data(#"{"model":"old"}"#.utf8))
        precondition(preferences.llmProtocol == "openai" && preferences.configFilePath.isEmpty)
        precondition(preferences.reasoningEffort == "low")
        precondition(preferences.sendWithEnter)
        precondition(Preferences().model == "qwen3.8-max")
        precondition(!preferences.showReasoning && !preferences.showCode)
        let nullDisplay = try JSONDecoder().decode(Preferences.self, from: Data(#"{"showReasoning":null,"showCode":null,"foreground_allowed":true,"ask_user_enabled":false}"#.utf8))
        precondition(!nullDisplay.showReasoning && !nullDisplay.showCode)
        var display = preferences; display.showReasoning = true; display.showCode = true
        let displayRoundtrip = try JSONDecoder().decode(Preferences.self, from: JSONEncoder().encode(display))
        precondition(displayRoundtrip.showReasoning && displayRoundtrip.showCode)
        let encodedDisplay = String(decoding: try JSONEncoder().encode(display), as: UTF8.self)
        precondition(!encodedDisplay.contains("foreground_allowed") && !encodedDisplay.contains("ask_user_enabled"))
        let suite = "Generalist-setup-test-" + UUID().uuidString
        let defaultsStore = UserDefaults(suiteName: suite)!
        defer { defaultsStore.removePersistentDomain(forName: suite) }
        precondition(FirstLaunch.needsSetup(in: defaultsStore))
        precondition(FirstLaunch.progress(in: defaultsStore) == .welcome)
        FirstLaunch.saveProgress(.permissions, in: defaultsStore)
        precondition(FirstLaunch.progress(in: UserDefaults(suiteName: suite)!) == .permissions)
        // Saving model preferences alone must never mark setup as complete.
        defaultsStore.set(Data(), forKey: "preferences")
        precondition(FirstLaunch.needsSetup(in: defaultsStore))
        FirstLaunch.complete(in: defaultsStore)
        precondition(!FirstLaunch.needsSetup(in: UserDefaults(suiteName: suite)!))
        precondition(FirstLaunch.progress(in: defaultsStore) == .welcome)
        do { try preferences.validateTaskCredentials(manualKey: "", readKey: { _ in "" }); preconditionFailure("Missing key accepted") }
        catch {}
        try preferences.validateTaskCredentials(manualKey: "fixture", readKey: { _ in preconditionFailure("Unnecessary key lookup") })
        try preferences.validateTaskCredentials(manualKey: "", readKey: { _ in "fixture" })
        preferences.allowEmptyKey = true
        try preferences.validateTaskCredentials(manualKey: "", readKey: { _ in preconditionFailure("Unnecessary key lookup") })
        let noAuth = try MCPOperationConfiguration.load(preferences: preferences,
            readKey: { _ in preconditionFailure("No-auth MCP must not open Keychain") })
        precondition(noAuth["api_key"] as? String == "")
        preferences.allowEmptyKey = false
        let cache = CredentialSessionCache()
        var reads = 0
        let readKey: (String) throws -> String = { endpoint in
            cache.read(endpoint) { reads += 1; return "fixture-" + endpoint }
        }
        let taskOne = try MCPOperationConfiguration.load(preferences: preferences, readKey: readKey)
        let taskTwo = try MCPOperationConfiguration.load(preferences: preferences, readKey: readKey)
        precondition(reads == 1 && taskOne["api_key"] as? String == taskTwo["api_key"] as? String,
                     "Repeated tasks must reuse one authorized read")
        let account = preferences.normalizedEndpoint
        _ = try readKey("https://other.example/v1")
        precondition(reads == 2, "Credentials must remain isolated by endpoint")
        cache.save("updated", endpoint: account) {}
        let updatedKey = try readKey(account)
        precondition(updatedKey == "updated" && reads == 2)
        cache.save("", endpoint: account) {}
        _ = try readKey(account)
        precondition(reads == 3, "Deleting a key must discard the cached credential")
        do {
            try cache.save("bad", endpoint: account) { throw AppError.message("save refused") }
            preconditionFailure("Failed save accepted")
        } catch {}
        _ = try readKey(account)
        precondition(reads == 4, "Failed replacement must not retain the previous credential")
        let retryCache = CredentialSessionCache()
        do {
            _ = try retryCache.read(account) { throw AppError.message("read refused") }
            preconditionFailure("Failed read accepted")
        } catch {}
        precondition(retryCache.read(account) { "" } == "")
        precondition(retryCache.read(account) { "authorized" } == "authorized",
                     "Refused and missing credentials must not be cached")
        precondition(CredentialSessionCache().read(account) { "fresh session" } == "fresh session")
        // A persistent store must work with a completely fresh process cache.
        // Runtime reads never visit the legacy service or allow interactive UI.
        var persisted = [ApplicationCredentials.legacyService: [account: "legacy-fixture"]]
        var accesses: [(String, Bool)] = []
        var denyRead = false
        var denyWrite = false
        let durable = ApplicationCredentials(readItem: { service, endpoint, interactive in
            accesses.append((service, interactive))
            if denyRead { throw AppError.message("locked") }
            return persisted[service]?[endpoint]
        }, writeItem: { endpoint, key in
            if denyWrite { throw AppError.message("save refused") }
            persisted[ApplicationCredentials.service, default: [:]][endpoint] = key
        })
        let beforeMigration = try durable.read(account)
        precondition(beforeMigration.isEmpty && accesses.count == 1 && !accesses[0].1)
        precondition(accesses[0].0 == ApplicationCredentials.service)
        let migrated = try durable.prepareForSettings(account)
        precondition(migrated == "legacy-fixture")
        precondition(persisted[ApplicationCredentials.legacyService]?[account] == "legacy-fixture",
                     "Migration must preserve the old item for recovery")
        precondition(accesses.last?.1 == false, "Migration must prove a noninteractive persistent read")
        accesses.removeAll()
        let coldRead = try CredentialSessionCache().read(account) { try durable.read(account) }
        precondition(coldRead == "legacy-fixture" && accesses.count == 1 && !accesses[0].1)
        try durable.save("replaced", account: account)
        let coldReplaced = try CredentialSessionCache().read(account) { try durable.read(account) }
        precondition(coldReplaced == "replaced")
        try durable.save("", account: account)
        accesses.removeAll()
        let cleared = try durable.prepareForSettings(account)
        precondition(cleared.isEmpty && accesses.allSatisfy { $0.0 == ApplicationCredentials.service },
                     "Cleared keys must not be resurrected from the legacy item")
        denyRead = true; accesses.removeAll()
        do { _ = try durable.read(account); preconditionFailure("Locked store accepted") } catch {}
        precondition(accesses.count == 1 && !accesses[0].1, "A task must fail without interactive retry")
        denyRead = false; denyWrite = true
        let otherAccount = "https://migration.example/v1"
        persisted[ApplicationCredentials.legacyService, default: [:]][otherAccount] = "recoverable"
        do { _ = try durable.prepareForSettings(otherAccount); preconditionFailure("Failed migration accepted") } catch {}
        precondition(persisted[ApplicationCredentials.service]?[otherAccount] == nil)
        precondition(persisted[ApplicationCredentials.legacyService]?[otherAccount] == "recoverable")
        denyWrite = false; accesses.removeAll()
        let isolated = try durable.read(otherAccount)
        precondition(isolated.isEmpty && accesses.count == 1 && !accesses[0].1)
        let unreadable = ApplicationCredentials(readItem: { _, _, interactive in
            if !interactive { throw AppError.message("authorization required") }
            return "fixture"
        }, writeItem: { _, _ in })
        do { try unreadable.save("fixture", account: account); preconditionFailure("Save without silent readback accepted") } catch {}
        do { _ = try unreadable.prepareForSettings(account); preconditionFailure("Unusable authorization cached") } catch {}
        let defaults = try preferences.taskConfiguration()
        precondition(defaults["reasoning_effort"] as? String == "low")
        preferences.reasoningEffort = "medium"
        preferences.sendWithEnter = false
        preferences.configFilePath = file.path
        try Data(#"{"llm_base_url":"https://first.example/v1/","model_name":"first","llm_protocol":"anthropic","reasoning_effort":"high","api_key":"fixture-secret","max_tokens":4096,"_note":"ignored"}"#.utf8).write(to: file)
        let first = try preferences.taskConfiguration()
        precondition(first["llm_base_url"] as? String == "https://first.example/v1")
        precondition(first["llm_protocol"] as? String == "anthropic")
        precondition(first["reasoning_effort"] as? String == "high")
        precondition(first["max_tokens"] as? Int == 4096 && first["_note"] == nil)
        precondition(first["model_name"] as? String == "first")
        for name in ["", " \n\t "] {
            var emptyModel = Preferences(); emptyModel.model = name
            do { try emptyModel.validate(); preconditionFailure("手工模型名称必须非空") } catch {}
            do { _ = try emptyModel.taskConfiguration(); preconditionFailure("任务模型名称必须非空") } catch {}
        }
        let displayConfiguration = try display.taskConfiguration()
        precondition(displayConfiguration["show_reasoning"] == nil && displayConfiguration["show_code"] == nil)
        try preferences.validateTaskCredentials(manualKey: "", readKey: { _ in preconditionFailure("File key should take precedence") })
        let saved = try JSONEncoder().encode(preferences)
        precondition(!String(decoding: saved, as: UTF8.self).contains("fixture-secret"))
        let restored = try JSONDecoder().decode(Preferences.self, from: saved)
        precondition(restored.configFilePath == file.path)
        precondition(restored.reasoningEffort == "medium")
        precondition(!restored.sendWithEnter)
        // A second task must use changed file contents without restarting the app.
        try Data(#"{"llm_base_url":"https://second.example/v1","model_name":"second","llm_protocol":"openai"}"#.utf8).write(to: file)
        let second = try restored.taskConfiguration()
        precondition(second["model_name"] as? String == "second")
        precondition(second["llm_base_url"] as? String == "https://second.example/v1")
        precondition(second["api_key"] == nil)
        precondition(second["reasoning_effort"] as? String == "medium")
        do {
            try restored.validateTaskCredentials(manualKey: "wrong-endpoint", readKey: { endpoint in
                precondition(endpoint == "https://second.example/v1"); return ""
            })
            preconditionFailure("Manual key reused for a different file endpoint")
        } catch {}
        for invalid in ["[]", "not json", #"{"llm_protocol":"unsupported"}"#, #"{"api_key":17}"#,
                        #"{"reasoning_effort":"unsupported"}"#, #"{"reasoning_effort":17}"#,
                        #"{"reasoning_effort":null}"#, #"{"model_name":""}"#, #"{"model_name":"  "}"#] {
            try Data(invalid.utf8).write(to: file)
            do { _ = try restored.taskConfiguration(); preconditionFailure("Invalid config accepted") }
            catch {}
        }
        try FileManager.default.removeItem(at: file)
        do { _ = try restored.taskConfiguration(); preconditionFailure("Missing file accepted") }
        catch {}
        preferences.configFilePath = ""
        preferences.llmProtocol = "anthropic"
        let manual = try preferences.taskConfiguration()
        precondition(manual["llm_protocol"] as? String == "anthropic")
        for effort in Preferences.reasoningEfforts {
            preferences.reasoningEffort = effort
            let config = try preferences.taskConfiguration()
            precondition(config["reasoning_effort"] as? String == effort)
        }
        print("Configuration reload, override, validation and credential persistence contracts passed")
    }
}
