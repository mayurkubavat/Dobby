import Foundation
import Security

/// The key the engine signs saved "don't ask again" rules with.
///
/// It lives in the login Keychain, where macOS lets only this app read it, so
/// a rule written into `~/.pinlet/rules.json` by anything else carries no
/// valid signature and is ignored. If the Keychain cannot be used, a key made
/// for this launch is used instead: saved rules then last until Pinlet.app quits.
enum RulesKey {
    private static let service = "dev.pinlet.rules-key"
    private static let account = "signing"

    /// 32 random bytes as hex, the same on every launch when the Keychain works.
    static func hex() -> String {
        if let data = read() ?? create() { return data.map { String(format: "%02x", $0) }.joined() }
        NSLog("Pinlet: the Keychain is not available; saved permissions will last until Pinlet quits")
        return random().map { String(format: "%02x", $0) }.joined()
    }

    private static func random() -> Data {
        var bytes = [UInt8](repeating: 0, count: 32)
        if SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) != errSecSuccess {
            for i in bytes.indices { bytes[i] = UInt8.random(in: 0...255) }
        }
        return Data(bytes)
    }

    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private static func read() -> Data? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data,
              data.count >= 16 else { return nil }
        return data
    }

    private static func create() -> Data? {
        let data = random()
        var q = query
        q[kSecValueData as String] = data
        q[kSecAttrLabel as String] = "Pinlet permissions key"
        let status = SecItemAdd(q as CFDictionary, nil)
        if status == errSecSuccess { return data }
        if status == errSecDuplicateItem { return read() }
        return nil
    }
}
