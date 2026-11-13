import Darwin
import Foundation

/// The app's line-JSON connection to the shared Pinlet (see pinlet/hub/control.py).
///
/// All calls block, so run them on `queue`. One connection is kept open and
/// reopened after any error.
final class ControlClient {
    let path: String
    let queue = DispatchQueue(label: "dev.pinlet.control")
    private var fd: Int32 = -1
    private var buffer = Data()
    private var nextID = 1

    init(path: String = PinletPaths.socketPath()) {
        self.path = path
    }

    deinit { close() }

    /// Is something listening? (A plain connect, no hello.)
    static func isListening(path: String) -> Bool {
        let fd = ControlClient.openSocket(path: path)
        if fd < 0 { return false }
        Darwin.close(fd)
        return true
    }

    private static func openSocket(path: String) -> Int32 {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        if fd < 0 { return -1 }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8)
        let capacity = MemoryLayout.size(ofValue: addr.sun_path)
        guard bytes.count < capacity else { Darwin.close(fd); return -1 }
        withUnsafeMutableBytes(of: &addr.sun_path) { raw in
            raw.copyBytes(from: bytes)
            raw[bytes.count] = 0
        }
        addr.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        let ok = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        if ok != 0 { Darwin.close(fd); return -1 }
        var on: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
        var tv = timeval(tv_sec: 3, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        return fd
    }

    func close() {
        if fd >= 0 { Darwin.close(fd) }
        fd = -1
        buffer.removeAll()
    }

    private func send(_ object: [String: Any]) -> Bool {
        guard var data = try? JSONSerialization.data(withJSONObject: object) else { return false }
        data.append(0x0A)
        return data.withUnsafeBytes { raw -> Bool in
            var sent = 0
            while sent < raw.count {
                let n = Darwin.send(fd, raw.baseAddress!.advanced(by: sent), raw.count - sent, 0)
                if n <= 0 { return false }
                sent += n
            }
            return true
        }
    }

    private func readLine() -> [String: Any]? {
        while true {
            if let nl = buffer.firstIndex(of: 0x0A) {
                let line = buffer.subdata(in: buffer.startIndex..<nl)
                buffer.removeSubrange(buffer.startIndex...nl)
                return (try? JSONSerialization.jsonObject(with: line)) as? [String: Any]
            }
            var chunk = [UInt8](repeating: 0, count: 65536)
            let n = recv(fd, &chunk, chunk.count, 0)
            if n <= 0 { return nil }
            buffer.append(contentsOf: chunk[0..<n])
        }
    }

    private func ensureOpen() -> Bool {
        if fd >= 0 { return true }
        fd = ControlClient.openSocket(path: path)
        if fd < 0 { return false }
        guard send(["pinlet_control": 1]), let ready = readLine(), (ready["pinlet_ready"] as? NSNumber)?.intValue == 1 else {
            close()
            return false
        }
        // The engine signs saved rules with this key and honours no rule without it
        // (pinlet/overlay/rules.py). It is handed over on every new connection.
        if let key = rulesKey, send(["id": 0, "op": "rules_key", "key": key]) { _ = readLine() }
        return true
    }

    /// The rule-signing key from the Keychain, as hex (`RulesKey`).
    var rulesKey: String?

    /// One request, its reply (`nil` when the shared Pinlet is not reachable).
    func request(_ op: String, _ fields: [String: Any] = [:]) -> [String: Any]? {
        for _ in 0..<2 {  // one retry on a fresh connection
            guard ensureOpen() else { return nil }
            let id = nextID
            nextID += 1
            var msg = fields
            msg["id"] = id
            msg["op"] = op
            if send(msg) {
                while let reply = readLine() {
                    if (reply["id"] as? NSNumber)?.intValue == id { return reply }
                }
            }
            close()
        }
        return nil
    }

    func topbar() -> TopbarView? {
        poll()?.view
    }

    /// The menu and the Allow/Deny questions waiting for the app, in one request.
    func poll() -> (view: TopbarView, prompts: [Prompt])? {
        guard let reply = request("topbar"), reply["ok"] as? Bool == true,
              let raw = reply["view"] as? [String: Any], let view = TopbarView(json: raw) else { return nil }
        return (view, Prompt.list(reply["prompts"]))
    }

    /// The card's (or a notification's) answer. False when the question is gone.
    @discardableResult
    func answer(_ promptID: Int, _ answer: PromptAnswer) -> Bool {
        var fields = answer.fields
        fields["prompt_id"] = promptID
        return request("answer", fields)?["answered"] as? Bool == true
    }

    /// The saved "don't ask again" rules and "Always allow" apps (control op "rules").
    func permissions() -> (rules: [SavedRule], apps: [SavedApp])? {
        guard let reply = request("rules"), reply["ok"] as? Bool == true else { return nil }
        let rules = (reply["rules"] as? [[String: Any]] ?? []).compactMap(SavedRule.init(json:))
        let apps = (reply["apps"] as? [[String: Any]] ?? []).compactMap(SavedApp.init(json:))
        return (rules, apps)
    }

    /// Take back one rule, one app, or (with neither) everything.
    @discardableResult
    func revoke(rule: String? = nil, app: String? = nil) -> Bool {
        var fields: [String: Any] = [:]
        if let rule { fields["rule"] = rule } else if let app { fields["app"] = app } else { fields["all"] = true }
        return request("revoke", fields)?["ok"] as? Bool == true
    }

    /// Sends the person's instruction to the Pinlet with `pid` (control op "tell").
    /// The reply has "queued" and "woke", or nil when the shared Pinlet is not reachable.
    func tell(pid: Int, text: String) -> [String: Any]? {
        request("tell", ["pid": pid, "text": text])
    }

    /// The activity journal in sentences and the latest agent-to-agent messages
    /// (control op "activity"), or nil when the shared Pinlet is not reachable.
    func activity(limit: Int = 300) -> [String: Any]? {
        guard let reply = request("activity", ["limit": limit]), reply["ok"] as? Bool == true else { return nil }
        return reply
    }

    @discardableResult
    func action(_ key: String, pid: Int? = nil) -> Bool {
        var fields: [String: Any] = ["key": key]
        if let pid { fields["pid"] = pid }
        return request("action", fields)?["ok"] as? Bool == true
    }
}
