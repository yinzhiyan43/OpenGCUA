import Foundation

/// Resolve MCP configuration from the running application's current preferences.
@MainActor enum MCPOperationConfiguration {
    static func load(preferences: Preferences, readKey: (String) throws -> String = CredentialStore.read) throws -> [String: Any] {
        var config = try preferences.taskConfiguration()
        let endpoint = config["llm_base_url"] as! String
        let fileKey = (config["api_key"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let key = fileKey.isEmpty
            ? (config["allow_empty_api_key"] as? Bool == true ? "" : try readKey(endpoint)) : fileKey
        guard !key.isEmpty || config["allow_empty_api_key"] as? Bool == true else {
            throw AppError.message(L("请在配置文件或此服务对应的钥匙串中保存 API Key。"))
        }
        config["api_key"] = key
        return config
    }
}
