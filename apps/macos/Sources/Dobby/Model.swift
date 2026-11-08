import Foundation

/// The menu's content, exactly as the shared Dobby sends it
/// (`dobby.overlay.topbar.View.to_dict()`, via the control op "topbar").
struct TopbarView: Equatable {
    struct Row: Equatable {
        var pid: Int
        var name: String
        var state: String
        var title: String
        var detail: String
        var color: String
        var shape: String
        var actions: [Action]
    }

    struct Action: Equatable {
        var label: String
        var key: String
    }

    var header: String
    var subtitle: String
    var rows: [Row]
    var running: Int
    var working: Int
    var attention: Bool
    var allActions: [Action]
    var iconState: String
    var iconTitle: String
    var tooltip: String
    var palette: [String: String]

    static let empty = TopbarView(
        header: "Dobby", subtitle: "Starting…", rows: [], running: 0, working: 0, attention: false,
        allActions: [], iconState: "idle", iconTitle: "", tooltip: "Dobby", palette: [:])

    static func actions(_ raw: Any?) -> [Action] {
        guard let list = raw as? [Any] else { return [] }
        return list.compactMap { item in
            if let pair = item as? [Any], pair.count >= 2, let label = pair[0] as? String, let key = pair[1] as? String {
                return Action(label: label, key: key)
            }
            if let dict = item as? [String: Any], let label = dict["label"] as? String, let key = dict["key"] as? String {
                return Action(label: label, key: key)
            }
            return nil
        }
    }

    /// Lenient: a field the server leaves out falls back to a sensible default.
    init?(json: [String: Any]) {
        guard let header = json["header"] as? String else { return nil }
        self.header = header
        subtitle = json["subtitle"] as? String ?? ""
        rows = (json["rows"] as? [[String: Any]] ?? []).map { r in
            Row(pid: (r["pid"] as? NSNumber)?.intValue ?? 0,
                name: r["name"] as? String ?? "",
                state: r["state"] as? String ?? "ready",
                title: r["title"] as? String ?? (r["name"] as? String ?? "Dobby"),
                detail: r["detail"] as? String ?? "",
                color: r["color"] as? String ?? "#7C5CFF",
                shape: r["shape"] as? String ?? "moon",
                actions: TopbarView.actions(r["actions"]))
        }
        running = (json["running"] as? NSNumber)?.intValue ?? rows.count
        working = (json["working"] as? NSNumber)?.intValue ?? 0
        attention = json["attention"] as? Bool ?? false
        allActions = TopbarView.actions(json["all_actions"])
        iconState = json["icon_state"] as? String ?? "idle"
        iconTitle = json["icon_title"] as? String ?? ""
        tooltip = json["tooltip"] as? String ?? "Dobby"
        palette = json["palette"] as? [String: String] ?? [:]
    }

    init(header: String, subtitle: String, rows: [Row], running: Int, working: Int, attention: Bool,
         allActions: [Action], iconState: String, iconTitle: String, tooltip: String, palette: [String: String]) {
        self.header = header; self.subtitle = subtitle; self.rows = rows; self.running = running
        self.working = working; self.attention = attention; self.allActions = allActions
        self.iconState = iconState; self.iconTitle = iconTitle; self.tooltip = tooltip; self.palette = palette
    }
}

/// How to start `dobby serve`, written by the `dobby` connector to ~/.dobby/app.json.
struct EngineConfig: Equatable {
    var python: String
    var cwd: String?
    var env: [String: String]

    static var path: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".dobby/app.json")
    }

    init(python: String, cwd: String?, env: [String: String]) {
        self.python = python; self.cwd = cwd; self.env = env
    }

    init?(data: Data) {
        guard let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let python = json["python"] as? String, !python.isEmpty else { return nil }
        self.python = python
        cwd = json["cwd"] as? String
        env = (json["env"] as? [String: Any] ?? [:]).compactMapValues { $0 as? String }
    }

    static func load(from url: URL = EngineConfig.path) -> EngineConfig? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return EngineConfig(data: data)
    }

    /// The daemon's environment: the app's own, the client's Dobby settings,
    /// then what only makes sense when the app owns the engine.
    func environment(base: [String: String]) -> [String: String] {
        var out = base
        for (k, v) in env { out[k] = v }
        out["DOBBY_IDLE_EXIT"] = "0"            // the app keeps it alive; Quit stops it
        out["DOBBY_OVERLAY_MENU_BAR"] = "0"     // the app draws the menu bar item
        out["DOBBY_APP_OWNED"] = "1"
        out["PYTHONUNBUFFERED"] = "1"
        return out
    }
}

/// Where the shared Dobby listens (mirrors dobby.hub.paths.socket_path).
enum DobbyPaths {
    static func socketPath(env: [String: String] = ProcessInfo.processInfo.environment) -> String {
        if let custom = env["DOBBY_SOCKET"], !custom.isEmpty {
            return (custom as NSString).expandingTildeInPath
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return home + "/Library/Application Support/Dobby/dobby.sock"
    }

    static var daemonLog: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".dobby/logs/daemon.log")
    }
}

/// `dobby://pause-all`, `dobby://stop?pid=4242`… for Shortcuts, scripts and Raycast.
enum DobbyURL {
    static func action(for url: URL) -> (key: String, pid: Int?)? {
        guard url.scheme?.lowercased() == "dobby" else { return nil }
        let name = (url.host ?? url.path).trimmingCharacters(in: CharacterSet(charactersIn: "/")).lowercased()
        let pid = URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?.first(where: { $0.name == "pid" })?.value.flatMap(Int.init)
        let map = ["pause-all": "pause_all", "resume-all": "resume_all", "stop-all": "stop_all",
                   "pause": "pause", "resume": "resume", "stop": "stop", "toggle": "pause_toggle"]
        guard let key = map[name] else { return nil }
        if !key.hasSuffix("_all") && pid == nil { return nil }
        return (key, pid)
    }
}

/// An Allow/Deny question waiting for the app's card (dobby/hub/prompts.py).
struct Prompt: Equatable, Identifiable {
    var id: Int
    var text: String
    var detail: String
    var allow: String
    var deny: String
    var kind: String          // "danger" (a risky step) or "access" (may Dobby use an app?)
    var pid: Int?
    var name: String
    var secsLeft: Double

    var isAccess: Bool { kind == "access" }

    init?(json: [String: Any]) {
        guard let id = (json["id"] as? NSNumber)?.intValue, let text = json["text"] as? String else { return nil }
        self.id = id
        self.text = text
        detail = json["detail"] as? String ?? ""
        allow = (json["allow"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "Allow"
        deny = (json["deny"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "Deny"
        kind = json["kind"] as? String ?? "danger"
        pid = (json["pid"] as? NSNumber)?.intValue
        name = json["name"] as? String ?? ""
        secsLeft = (json["secs_left"] as? NSNumber)?.doubleValue ?? 30
    }

    static func list(_ raw: Any?) -> [Prompt] {
        (raw as? [[String: Any]] ?? []).compactMap(Prompt.init(json:))
    }

    /// Same question (the countdown aside).
    func sameQuestion(as other: Prompt) -> Bool { id == other.id }
}
