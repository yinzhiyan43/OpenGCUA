import Foundation
import Security

struct Preferences: Codable {
    var endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    var model = "qwen3.8-max"
    var llmProtocol = "openai"
    var reasoningEffort = "low"
    static let reasoningEfforts = ["none", "low", "medium", "high", "xhigh", "max"]
    var configFilePath = ""
    var appearance = "跟随系统"
    var language = "system"
    var sendWithEnter = true
    var showReasoning = false
    var showCode = false
    var allowEmptyKey = false
    var speechProvider = "Qwen 实时语音"
    var speechRegion = "北京"
    var speechModel = "qwen3.8-omni-flash-realtime"
    var speechWorkspaceID = ""
    var audioInputUID = ""
    init() {}
    enum CodingKeys: String, CodingKey { case endpoint, model, llmProtocol, reasoningEffort, configFilePath, appearance, language, sendWithEnter, showReasoning, showCode, allowEmptyKey, speechProvider, speechRegion, speechModel, speechWorkspaceID, audioInputUID }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        endpoint = try c.decodeIfPresent(String.self, forKey: .endpoint) ?? endpoint
        model = try c.decodeIfPresent(String.self, forKey: .model) ?? model
        llmProtocol = try c.decodeIfPresent(String.self, forKey: .llmProtocol) ?? llmProtocol
        reasoningEffort = try c.decodeIfPresent(String.self, forKey: .reasoningEffort) ?? reasoningEffort
        configFilePath = try c.decodeIfPresent(String.self, forKey: .configFilePath) ?? configFilePath
        appearance = try c.decodeIfPresent(String.self, forKey: .appearance) ?? appearance
        let savedLanguage = try c.decodeIfPresent(String.self, forKey: .language) ?? "system"
        language = AppLanguage.choices.contains(savedLanguage) ? savedLanguage : "system"
        sendWithEnter = try c.decodeIfPresent(Bool.self, forKey: .sendWithEnter) ?? sendWithEnter
        showReasoning = try c.decodeIfPresent(Bool.self, forKey: .showReasoning) ?? false
        showCode = try c.decodeIfPresent(Bool.self, forKey: .showCode) ?? false
        allowEmptyKey = try c.decodeIfPresent(Bool.self, forKey: .allowEmptyKey) ?? allowEmptyKey
        speechProvider = try c.decodeIfPresent(String.self, forKey: .speechProvider) ?? speechProvider
        speechRegion = try c.decodeIfPresent(String.self, forKey: .speechRegion) ?? speechRegion
        speechModel = try c.decodeIfPresent(String.self, forKey: .speechModel) ?? speechModel
        speechWorkspaceID = try c.decodeIfPresent(String.self, forKey: .speechWorkspaceID) ?? speechWorkspaceID
        audioInputUID = try c.decodeIfPresent(String.self, forKey: .audioInputUID) ?? audioInputUID
    }
    var speechHost: String { speechRegion == "新加坡" ? "dashscope-intl.aliyuncs.com" : "dashscope.aliyuncs.com" }
    var speechCredentialAccount: String { "https://" + speechHost + "/compatible-mode/v1" }
    var normalizedSpeechWorkspace: String { speechWorkspaceID.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
    var speechWorkspaceValid: Bool {
        normalizedSpeechWorkspace.range(of: "^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", options: .regularExpression) != nil
    }
    var speechURL: URL? {
        let workspace = normalizedSpeechWorkspace
        guard workspace.isEmpty || speechWorkspaceValid else { return nil }
        let host = workspace.isEmpty ? speechHost : workspace + (speechRegion == "新加坡" ? ".ap-southeast-1.maas.aliyuncs.com" : ".cn-beijing.maas.aliyuncs.com")
        var url = URLComponents(string: "wss://" + host + "/api-ws/v1/realtime")!
        url.queryItems = [URLQueryItem(name: "model", value: speechModel.trimmingCharacters(in: .whitespacesAndNewlines))]
        return url.url
    }
    func validatedSpeechURL() throws -> URL {
        guard let url = speechURL else {
            throw AppError.message(L("业务空间 ID 格式无效。可留空使用公共地址；如需专属地址，只填写 ID，不包含网址。"))
        }
        return url
    }
    var speechSession: [String: Any] {
        var session: [String: Any] = [
            "modalities": ["text", "audio"],
            "turn_detection": ["type": "semantic_vad", "threshold": 0.5, "silence_duration_ms": 700],
            "instructions": VoiceTaskTools.instructions + "\n" + L("用户偏好语言：%@。除非用户指定其他语言，否则使用此语言回复。", L10n.languageCode), "tools": VoiceTaskTools.definitions
        ]
        if speechModel.contains("omni") {
            // Omni 3.8 rejects both the gateway default Chelsie and the older Ethan voice.
            let voice = speechModel.hasPrefix("qwen3.8-") ? "Tina" : "Ethan"
            session["voice"] = voice
            session["input_audio_transcription"] = ["model": "qwen3-asr-flash-realtime"]
            session["audio"] = ["input": ["format": ["type": "pcm", "sample_rate": 16000,
                "sample_format": "s16le", "channels": 1, "packing": "interleaved", "channel_layout": "mono"]],
                "output": ["voice": voice, "format": ["type": "pcm", "sample_rate": 24000]]]
        } else { session["input_audio_format"] = "pcm" }
        return session
    }
    static func load() -> Self {
        guard let data = UserDefaults.standard.data(forKey: "preferences"),
              let value = try? JSONDecoder().decode(Self.self, from: data) else { return Self() }
        return value
    }
    func save() { if let data = try? JSONEncoder().encode(self) { UserDefaults.standard.set(data, forKey: "preferences") } }
    var normalizedEndpoint: String { endpoint.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/")) }
    var sendShortcutHint: String { sendWithEnter ? L("Enter 发送 · Shift+Enter 换行") : L("⌘Enter 发送 · Enter 换行") }
    func validateTaskCredentials(manualKey: String, readKey: (String) throws -> String = CredentialStore.read) throws {
        let config = try taskConfiguration()
        if config["allow_empty_api_key"] as? Bool == true { return }
        if let key = config["api_key"] as? String, !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return }
        let endpoint = config["llm_base_url"] as! String
        if endpoint == normalizedEndpoint && !manualKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return }
        guard !((try readKey(endpoint)).trimmingCharacters(in: .whitespacesAndNewlines)).isEmpty else {
            throw AppError.message(L("请填写 API Key、选择含有密钥的 JSON 配置，或开启免密选项。"))
        }
    }
    /// Re-read the chosen source-harness configuration for every new task.
    /// Persist only its path; file credentials never enter UserDefaults.
    func taskConfiguration() throws -> [String: Any] {
        var config: [String: Any] = ["llm_base_url": normalizedEndpoint, "model_name": model,
                                    "llm_protocol": llmProtocol, "reasoning_effort": reasoningEffort,
                                    "allow_empty_api_key": allowEmptyKey]
        if !configFilePath.isEmpty {
            let data: Data
            do { data = try Data(contentsOf: URL(fileURLWithPath: configFilePath)) }
            catch { throw AppError.message(L("无法读取所选配置文件，请重新选择文件或使用手动设置。")) }
            guard let object = try? JSONSerialization.jsonObject(with: data),
                  let values = object as? [String: Any] else {
                throw AppError.message(L("配置文件必须是 JSON 对象。"))
            }
            for (name, value) in values where !name.hasPrefix("_") { config[name] = value }
        }
        guard let endpoint = config["llm_base_url"] as? String,
              let model = config["model_name"] as? String,
              let wireProtocol = config["llm_protocol"] as? String,
              config["allow_empty_api_key"] is Bool,
              config["api_key"] == nil || config["api_key"] is String else {
            throw AppError.message(L("模型地址、模型名称、协议和 API Key 必须为字符串；免密选项必须为布尔值。"))
        }
        guard let effort = config["reasoning_effort"] as? String,
              Self.reasoningEfforts.contains(effort) else {
            throw AppError.message(L("Reasoning effort 必须是 none、low、medium、high、xhigh 或 max。"))
        }
        var effective = self
        effective.endpoint = endpoint; effective.model = model; effective.llmProtocol = wireProtocol
        effective.reasoningEffort = effort
        try effective.validate()
        config["llm_base_url"] = effective.normalizedEndpoint
        config["model_name"] = model.trimmingCharacters(in: .whitespacesAndNewlines)
        return config
    }
    func validate() throws {
        guard let u = URLComponents(string: normalizedEndpoint), let host = u.host,
              u.user == nil, u.password == nil, u.query == nil, u.fragment == nil,
              u.scheme == "https" || (u.scheme == "http" && ["localhost", "127.0.0.1", "::1", "[::1]"].contains(host)) else {
            throw AppError.message(L("模型地址必须使用 HTTPS；本机服务可以使用 HTTP。"))
        }
        guard !model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw AppError.message(L("请填写模型名称。")) }
        guard ["openai", "anthropic"].contains(llmProtocol) else { throw AppError.message(L("模型协议必须是 openai 或 anthropic。")) }
        guard Self.reasoningEfforts.contains(reasoningEffort) else { throw AppError.message(L("请选择有效的 Reasoning effort。")) }
        guard !speechModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw AppError.message(L("请填写语音模型名称。")) }
    }
}

/// Successful credentials stay only in this application's memory. Never cache
/// refusals or missing items, and invalidate before attempting any replacement.
final class CredentialSessionCache {
    private let lock = NSLock()
    private var keys: [String: String] = [:]

    func read(_ endpoint: String, load: () throws -> String) rethrows -> String {
        lock.lock(); defer { lock.unlock() }
        if let key = keys[endpoint] { return key }
        let key = try load()
        if !key.isEmpty { keys[endpoint] = key }
        return key
    }

    func save(_ key: String, endpoint: String, persist: () throws -> Void) rethrows {
        lock.lock(); defer { lock.unlock() }
        keys.removeValue(forKey: endpoint)
        try persist()
        if !key.isEmpty { keys[endpoint] = key }
    }
}

/// Durable ownership is separate from the process cache: tasks only read the
/// application's own item, while settings can import the legacy item once.
struct ApplicationCredentials {
    static let service = "app.generalist.mac.credentials.v2"
    static let legacyService = "app.generalist.model-api"
    var readItem: (_ service: String, _ account: String, _ interactive: Bool) throws -> String?
    var writeItem: (_ account: String, _ key: String) throws -> Void

    func read(_ account: String) throws -> String {
        try readItem(Self.service, account, false) ?? ""
    }

    func prepareForSettings(_ account: String) throws -> String {
        if let key = try readItem(Self.service, account, true) {
            try verify(key, account: account)
            return key
        }
        guard let legacy = try readItem(Self.legacyService, account, true) else { return "" }
        try save(legacy, account: account)
        return legacy
    }

    func save(_ key: String, account: String) throws {
        try writeItem(account, key)
        // Verify against the persistent store, never the memory cache. A saved
        // configuration must work with the same noninteractive policy as tasks.
        try verify(key, account: account)
    }

    private func verify(_ key: String, account: String) throws {
        guard try readItem(Self.service, account, false) == key else {
            throw AppError.message(L("凭据保存后验证失败，请在设置中重新保存 API Key。"))
        }
    }
}

enum CredentialStore {
    private static let sessionCache = CredentialSessionCache()
    private static let repository = ApplicationCredentials(readItem: readFromKeychain, writeItem: saveToKeychain)
    // File-based login Keychain ignores LAContext's no-UI flag. Scope the legacy
    // API's process-wide flag to serialized Security calls and restore it even on
    // failure. Never change the user's keychain lock settings or broad trust ACLs.
    private static let keychainLock = NSRecursiveLock()
    private static func withInteraction<T>(_ allowed: Bool, _ body: () throws -> T) throws -> T {
        keychainLock.lock(); defer { keychainLock.unlock() }
        var previous: DarwinBoolean = true
        try check(SecKeychainGetUserInteractionAllowed(&previous))
        try check(SecKeychainSetUserInteractionAllowed(allowed))
        defer { SecKeychainSetUserInteractionAllowed(previous.boolValue) }
        return try body()
    }
    private static func query(_ service: String, _ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }
    static func read(_ endpoint: String) throws -> String {
        try sessionCache.read(endpoint) { try repository.read(endpoint) }
    }
    /// Only the explicit settings flow may request migration/repair authorization.
    static func readForSettings(_ endpoint: String) throws -> String {
        try sessionCache.read(endpoint) { try repository.prepareForSettings(endpoint) }
    }
    private static func readFromKeychain(_ service: String, _ account: String, _ interactive: Bool) throws -> String? {
        try withInteraction(interactive) {
            var q = query(service, account)
            q[kSecReturnData as String] = true; q[kSecMatchLimit as String] = kSecMatchLimitOne
            var result: CFTypeRef?
            let status = SecItemCopyMatching(q as CFDictionary, &result)
            if status == errSecItemNotFound { return nil }
            try check(status)
            guard let data = result as? Data, let key = String(data: data, encoding: .utf8) else {
                throw AppError.message(L("凭据数据无效，请在设置中重新保存 API Key。"))
            }
            return key
        }
    }
    static func save(_ key: String, endpoint: String) throws {
        try sessionCache.save(key, endpoint: endpoint) { try repository.save(key, account: endpoint) }
    }
    private static func saveToKeychain(_ endpoint: String, _ key: String) throws {
        try withInteraction(true) {
            let q = query(ApplicationCredentials.service, endpoint)
            // An empty value clears the secret but retains a migration tombstone:
            // opening settings must never resurrect a deleted legacy credential.
            let attributes = [kSecValueData as String: Data(key.utf8)]
            var status = SecItemUpdate(q as CFDictionary, attributes as CFDictionary)
            if status == errSecItemNotFound {
                var access: SecAccess?
                // nil means ONLY the calling application, not all applications.
                try check(SecAccessCreate("OpenGCUA API Key" as CFString, nil, &access))
                guard let access else { throw AppError.message(L("无法创建应用凭据访问权限。")) }
                var item = q.merging(attributes) { _, new in new }
                item[kSecAttrAccess as String] = access
                status = SecItemAdd(item as CFDictionary, nil)
            }
            try check(status)
        }
    }
    private static func check(_ status: OSStatus) throws {
        guard status != errSecSuccess else { return }
        if status == errSecInteractionNotAllowed || status == errSecAuthFailed || status == errSecUserCanceled {
            throw AppError.message(L("凭据暂不可用。请解锁登录钥匙串，并在 OpenGCUA 设置中完成一次授权或重新保存 API Key；任务不会弹出系统授权框。"))
        }
        throw AppError.message(L("无法访问应用凭据（%@）。请在设置中重新保存 API Key。", String(describing: status)))
    }
}
