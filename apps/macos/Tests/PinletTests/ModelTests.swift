import Foundation
import Testing
@testable import Pinlet

// swift-testing ships with the Command Line Tools, so `swift test` runs without Xcode.

@Test func topbarDecodesPythonJSON() throws {
    let json = """
    {"header": "Pinlet", "subtitle": "2 running · 1 working", "running": 2, "working": 1, "attention": false,
     "rows": [{"pid": 42, "name": "Claude Code", "state": "working", "title": "Claude Code",
               "detail": "Working · click", "color": "#0E7490", "shape": "spark", "own": false,
               "actions": [["Pause", "pause_toggle"], ["Stop", "stop"]]}],
     "all_actions": [["Pause all", "pause_all"]], "icon_state": "working", "icon_title": "2",
     "tooltip": "Pinlet: 2 running", "palette": {"ok": "#22A365"}}
    """
    let obj = try #require(try JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
    let view = try #require(TopbarView(json: obj))
    #expect(view.rows.first?.pid == 42)
    #expect(view.rows.first?.actions.map(\.key) == ["pause_toggle", "stop"])
    #expect(view.allActions.first?.label == "Pause all")
    #expect(view.iconState == "working")
    #expect(view.palette["ok"] == "#22A365")
}

@Test func engineConfig() throws {
    let data = Data(#"{"python": "/v/bin/python", "cwd": "/src", "env": {"PINLET_MODEL": "x", "N": 1}}"#.utf8)
    let cfg = try #require(EngineConfig(data: data))
    let env = cfg.environment(base: ["HOME": "/u", "PINLET_IDLE_EXIT": "5"])
    #expect(env["PINLET_MODEL"] == "x")
    #expect(env["N"] == nil)
    #expect(env["PINLET_IDLE_EXIT"] == "0")
    #expect(env["PINLET_OVERLAY_MENU_BAR"] == "0")
    #expect(env["HOME"] == "/u")
}

@Test func urlScheme() {
    #expect(PinletURL.action(for: URL(string: "pinlet://pause-all")!)?.key == "pause_all")
    #expect(PinletURL.action(for: URL(string: "pinlet://stop?pid=7")!)?.pid == 7)
    #expect(PinletURL.action(for: URL(string: "pinlet://stop")!) == nil)
    #expect(PinletURL.action(for: URL(string: "https://pause-all")!) == nil)
}

@Test func promptsDecodePythonJSON() throws {
    let raw: [[String: Any]] = [
        ["id": 3, "text": "Allow Pinlet to press Send?", "detail": "In Mail", "allow": "Allow", "deny": "Deny",
         "kind": "danger", "pid": 4242, "name": "Tibble · Claude Code", "secs_left": 24.5],
        ["id": 4, "text": "Let Pinlet use Notes?", "kind": "access", "pid": NSNull()],
        ["text": "no id"],
    ]
    let prompts = Prompt.list(raw)
    #expect(prompts.count == 2)
    #expect(prompts[0].pid == 4242 && prompts[0].secsLeft == 24.5 && !prompts[0].isAccess)
    #expect(prompts[1].isAccess && prompts[1].pid == nil && prompts[1].allow == "Allow" && prompts[1].secsLeft == 30)
}

@Test func menuTitlesUseTitleCase() {
    #expect(StatusMenu.menuTitle("Show 3 hidden") == "Show 3 Hidden")
    #expect(StatusMenu.menuTitle("Clear finished") == "Clear Finished")
    #expect(StatusMenu.menuTitle("Pause all") == "Pause All")
}

@Test func lookDefaultsToDarkAndPassesThemeToEngine() throws {
    #expect(AppSettings.appearanceName(for: "") == .darkAqua)
    #expect(AppSettings.appearanceName(for: "dark") == .darkAqua)
    #expect(AppSettings.appearanceName(for: "light") == .aqua)
    #expect(AppSettings.appearanceName(for: "auto") == nil)

    let defaults = try #require(UserDefaults(suiteName: "pinlet.tests.look"))
    defaults.removePersistentDomain(forName: "pinlet.tests.look")
    let settings = AppSettings(defaults: defaults)
    #expect(settings.engineEnvironment()["PINLET_OVERLAY_THEME"] == nil)
    defaults.set("light", forKey: "theme")
    #expect(settings.engineEnvironment()["PINLET_OVERLAY_THEME"] == "light")
    defaults.removePersistentDomain(forName: "pinlet.tests.look")
}

@Test func activityStepLineKeepsWhatPinletDoes() {
    #expect(ActivityModel.stepLine("Working · Click ‘Send’ · in Mail · step 7 · 0:42") == "Click ‘Send’ · in Mail")
    #expect(ActivityModel.stepLine("Working ⚡ · type · step 2 · 1h 05m · 1 note waiting") == "type")
    #expect(ActivityModel.stepLine("Sleeping") == "")
}

@Test func activityDefaultSelectionPrefersWhoNeedsYou() throws {
    let json = """
    {"header": "Pinlet", "rows": [
      {"pid": 1, "title": "Ash · Cursor", "state": "ready"},
      {"pid": 2, "title": "Tibble · Claude Code", "state": "working"},
      {"pid": 3, "title": "Nib · Codex", "state": "listening", "actions": [["Wake", "wake"]]}]}
    """
    let obj = try #require(try JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
    let view = try #require(TopbarView(json: obj))
    #expect(ActivityModel.defaultSelection(view.rows) == 3)
    #expect(ActivityModel.defaultSelection(Array(view.rows.prefix(2))) == 2)
    #expect(view.rows[1].firstName == "Tibble")
    #expect(ActivityRowView.symbol(view.rows[2].actions[0]) == "sun.max.fill")
}

@Test func menuShortensLongEngineMessages() {
    let note = "Could not start /Users/mkubavat/Silicon/active/pinlet/.venv/bin/python: The file “python” doesn’t exist."
    let short = StatusMenu.shortNote(note)
    #expect(short.hasPrefix("Could not start python: The file"))
    #expect(short.count <= 60)
    #expect(StatusMenu.isProblem(note))
    #expect(!StatusMenu.isProblem("Starting… (the first start loads the models)"))
    #expect(StatusMenu.shortNote("Connecting…") == "Connecting…")
}

@Test func engineResolveSkipsAPythonThatIsGone() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("pinlet-resolve-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: dir) }
    let app = dir.appendingPathComponent("app.json")
    try Data(#"{"python": "/gone/.venv/bin/python", "cwd": "/gone"}"#.utf8).write(to: app)
    let missing = dir.appendingPathComponent("runtime/bin/python")
    #expect(EngineConfig.resolve(env: [:], appConfig: app, runtimePython: missing) == nil)
    // Pinlet's own runtime takes over when the checkout's venv is gone.
    let managed = URL(fileURLWithPath: "/bin/sh")
    #expect(EngineConfig.resolve(env: [:], appConfig: app, runtimePython: managed)?.python == "/bin/sh")
    // Once installed, the own runtime wins over a live checkout venv in app.json.
    try Data(#"{"python": "/bin/ls", "cwd": "/gone"}"#.utf8).write(to: app)
    #expect(EngineConfig.resolve(env: [:], appConfig: app, runtimePython: managed)?.python == "/bin/sh")
    // Before that, a live app.json is used, and a cwd that is gone is dropped.
    try Data(#"{"python": "/bin/sh", "cwd": "/gone"}"#.utf8).write(to: app)
    let cfg = try #require(EngineConfig.resolve(env: [:], appConfig: app, runtimePython: missing))
    #expect(cfg.python == "/bin/sh" && cfg.cwd == nil)
    #expect(EngineConfig.resolve(env: ["PINLET_PYTHON": "/bin/ls"], appConfig: app, runtimePython: managed)?.python == "/bin/ls")
}

@Test func installerProgressLines() {
    let step = RuntimeInstaller.parseStep("::step 2/6 Getting the Pinlet source")
    #expect(step?.0 == 2 && step?.1 == 6 && step?.2 == "Getting the Pinlet source")
    #expect(RuntimeInstaller.parseStep("==> Getting uv") == nil)
    #expect(RuntimeInstaller.parseStep("::step x/6 nope") == nil)
}

@Test func aiToolsDecodeSetupJSON() {
    let raw: [[String: Any]] = [
        ["key": "cursor", "label": "Cursor", "installed": true, "state": "broken", "stable": false],
        ["key": "codex", "label": "OpenAI Codex", "installed": true, "state": "connected", "stable": true],
        ["key": "vscode", "label": "VS Code", "installed": false, "state": "not installed"],
    ]
    let tools = AITool.list(raw)
    #expect(tools.map(\.key) == ["cursor", "codex", "vscode"])
    #expect(tools[0].needsRepair && !tools[1].needsRepair && tools[1].connected)
    let summary = AIToolsModel.summary(
        ["changes": [["tool": "cursor", "status": "added"]], "restart": ["cursor", "codex"]],
        verb: "Connected", labels: ["cursor": "Cursor", "codex": "OpenAI Codex"])
    #expect(summary.hasPrefix("Connected Cursor and OpenAI Codex."))
}

@Test func backgroundModeReachesTheEngineOnlyWhenSwitchedOn() throws {
    let defaults = try #require(UserDefaults(suiteName: "pinlet.tests.background"))
    defaults.removePersistentDomain(forName: "pinlet.tests.background")
    let settings = AppSettings(defaults: defaults)
    #expect(settings.engineEnvironment()["PINLET_BACKGROUND"] == nil)
    defaults.set(true, forKey: "borrowWhenIdle")
    #expect(settings.engineEnvironment()["PINLET_BACKGROUND_POINTER"] == nil)
    defaults.set(true, forKey: "background")
    #expect(settings.engineEnvironment()["PINLET_BACKGROUND"] == "1")
    #expect(settings.engineEnvironment()["PINLET_BACKGROUND_POINTER"] == "idle")
    defaults.removePersistentDomain(forName: "pinlet.tests.background")
}
