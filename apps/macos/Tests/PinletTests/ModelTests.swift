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
    #expect(PinletURL.window(for: URL(string: "pinlet://settings/permissions")!) == "settings")
    #expect(PinletURL.settingsTab(for: URL(string: "pinlet://settings/permissions")!) == "permissions")
    #expect(PinletURL.settingsTab(for: URL(string: "pinlet://settings")!) == nil)
    #expect(PinletURL.settingsTab(for: URL(string: "pinlet://activity/permissions")!) == nil)
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

// ------------------------------------------------------------- the companion
private func companionView(_ rows: [(Int, String, String, Int)]) -> TopbarView {
    TopbarView(header: "Pinlet", subtitle: "", rows: rows.map { r in
        TopbarView.Row(pid: r.0, name: "Claude Code", state: r.1, title: "Tibble · Claude Code", detail: r.2,
                       color: "#0E7490", shape: "spark", actions: [], failures: r.3)
    }, running: rows.count, working: 0, attention: false, allActions: [], iconState: "idle", iconTitle: "",
    tooltip: "", palette: [:])
}

@Test func companionMoodFollowsPinlet() throws {
    func mood(_ rows: [(Int, String, String, Int)], prompts: [Prompt] = [], reachable: Bool = true,
              locked: Bool = false) -> CompanionMood {
        CompanionTracker.steady(view: companionView(rows), prompts: prompts, reachable: reachable, locked: locked)
    }
    #expect(mood([]) == .sleep)
    #expect(mood([(1, "ready", "Sleeping", 0)]) == .sleep)
    #expect(mood([(1, "listening", "Waiting for you", 0)]) == .awake)
    #expect(mood([(1, "working", "Working", 0)]) == .think)
    #expect(mood([(1, "working", "Working · Click ‘Send’ · in Mail · step 7 · 0:42", 0)]) == .act)
    #expect(mood([(1, "working", "Working · Click ‘Send’", 3)]) == .error)
    #expect(mood([(1, "awake", "Awake", 0)]) == .think)
    #expect(mood([(1, "paused", "Paused", 0)]) == .pause)
    #expect(mood([(1, "takeover", "You're in control", 0)]) == .pause)
    #expect(mood([(1, "confirm", "Needs you", 0)]) == .ask)
    #expect(mood([(1, "working", "Working · Click", 0)], reachable: false) == .sleep)
    #expect(mood([(1, "working", "Working · Click", 0)], locked: true) == .lock)
    let prompt = try #require(Prompt(json: ["id": 1, "text": "Send?"]))
    #expect(mood([(1, "working", "Working · Click", 0)], prompts: [prompt]) == .ask)
    let missed = try #require(Prompt(json: ["id": 2, "text": "Send?", "missed": true]))
    #expect(mood([(1, "listening", "", 0)], prompts: [missed]) == .awake)
}

@Test func companionCheersOnlyWhenWorkEndsByItself() {
    let t0 = Date(timeIntervalSince1970: 1000)
    let working = companionView([(1, "working", "Working · Click ‘Send’", 0)])
    var tracker = CompanionTracker()
    #expect(tracker.update(view: working, prompts: [], reachable: true, locked: false, now: t0) == .act)
    let resting = companionView([(1, "listening", "", 0)])
    #expect(tracker.update(view: resting, prompts: [], reachable: true, locked: false, now: t0 + 1) == .done)
    #expect(tracker.update(view: resting, prompts: [], reachable: true, locked: false, now: t0 + 3) == .done)
    #expect(tracker.update(view: resting, prompts: [], reachable: true, locked: false, now: t0 + 6) == .awake)

    // Stopped by the person, or its row gone: no cheer.
    var stopped = CompanionTracker()
    stopped.update(view: working, prompts: [], reachable: true, locked: false, now: t0)
    #expect(stopped.update(view: companionView([(1, "stopped", "Stopped", 0)]), prompts: [], reachable: true,
                           locked: false, now: t0 + 1) == .sleep)
    var gone = CompanionTracker()
    gone.update(view: working, prompts: [], reachable: true, locked: false, now: t0)
    #expect(gone.update(view: companionView([]), prompts: [], reachable: true, locked: false, now: t0 + 1) == .sleep)
    // Paused in between: resting afterwards is not "done".
    var paused = CompanionTracker()
    paused.update(view: working, prompts: [], reachable: true, locked: false, now: t0)
    #expect(paused.update(view: companionView([(1, "paused", "Paused", 0)]), prompts: [], reachable: true,
                          locked: false, now: t0 + 1) == .pause)
    #expect(paused.update(view: resting, prompts: [], reachable: true, locked: false, now: t0 + 2) == .awake)
}

@Test func companionWordsAndSettings() throws {
    let working = companionView([(1, "working", "Working · Click ‘Send’ · in Mail · step 7 · 0:42", 0)])
    #expect(CompanionTracker.line(for: .act, view: working) == "Click ‘Send’ · in Mail")
    #expect(CompanionTracker.line(for: .sleep, view: working).isEmpty)
    #expect(CompanionTracker.line(for: .lock, view: working).isEmpty)
    #expect(!CompanionTracker.line(for: .ask, view: working).isEmpty)

    let suite = "pinlet.tests.companion"
    let defaults = try #require(UserDefaults(suiteName: suite))
    defaults.removePersistentDomain(forName: suite)
    #expect(!CompanionSettings.enabled(defaults: defaults, env: [:]))          // off unless switched on
    #expect(CompanionSettings.enabled(defaults: defaults, env: ["PINLET_COMPANION": "1"]))
    defaults.set(true, forKey: "companion")
    #expect(CompanionSettings.enabled(defaults: defaults, env: [:]))
    #expect(!CompanionSettings.enabled(defaults: defaults, env: ["PINLET_COMPANION": "off"]))
    #expect(CompanionSettings.size(defaults: defaults) == 72)
    defaults.set(96, forKey: "companionSize")
    #expect(CompanionSettings.size(defaults: defaults) == 96)
    defaults.set(77, forKey: "companionSize")
    #expect(CompanionSettings.size(defaults: defaults) == 72)
    #expect(CompanionSettings.speaks(defaults: defaults))
    defaults.set("off", forKey: "personality")
    #expect(!CompanionSettings.speaks(defaults: defaults))
    defaults.removePersistentDomain(forName: suite)
}

@Test func companionPosesAndSpring() throws {
    // Every mood has its own pose, and each pose has every dial.
    let poses = CompanionMood.allCases.map { CompanionPose.target($0) }
    #expect(Set(poses.map { $0.values.map { String($0) }.joined(separator: ",") }).count == CompanionMood.allCases.count)
    #expect(poses.allSatisfy { $0.values.count == CompanionDial.allCases.count })
    #expect(CompanionPose.target(.sleep)[.eyeOpen] == 0)
    #expect(CompanionPose.target(.lock)[.dim] < 1)

    var spring = CompanionSpring(x: 0)
    var peak = 0.0
    for _ in 0..<180 {
        spring.step(to: 1, dt: 1.0 / 60)
        peak = max(peak, spring.x)
    }
    #expect(spring.settled(at: 1))
    #expect(peak > 1 && peak < 1.3)   // a little overshoot, not a wobble

    // The art draws: a PNG with all nine moods.
    let png = try #require(CompanionArt.sheet(scale: 1))
    #expect(png.count > 2000)
}
