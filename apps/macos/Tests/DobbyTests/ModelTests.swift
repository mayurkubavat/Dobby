import Foundation
import Testing
@testable import Dobby

// swift-testing ships with the Command Line Tools, so `swift test` runs without Xcode.

@Test func topbarDecodesPythonJSON() throws {
    let json = """
    {"header": "Dobby", "subtitle": "2 running · 1 working", "running": 2, "working": 1, "attention": false,
     "rows": [{"pid": 42, "name": "Claude Code", "state": "working", "title": "Claude Code",
               "detail": "Working · click", "color": "#5B3FE0", "shape": "spark", "own": false,
               "actions": [["Pause", "pause_toggle"], ["Stop", "stop"]]}],
     "all_actions": [["Pause all", "pause_all"]], "icon_state": "working", "icon_title": "2",
     "tooltip": "Dobby: 2 running", "palette": {"ok": "#22A365"}}
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
    let data = Data(#"{"python": "/v/bin/python", "cwd": "/src", "env": {"DOBBY_MODEL": "x", "N": 1}}"#.utf8)
    let cfg = try #require(EngineConfig(data: data))
    let env = cfg.environment(base: ["HOME": "/u", "DOBBY_IDLE_EXIT": "5"])
    #expect(env["DOBBY_MODEL"] == "x")
    #expect(env["N"] == nil)
    #expect(env["DOBBY_IDLE_EXIT"] == "0")
    #expect(env["DOBBY_OVERLAY_MENU_BAR"] == "0")
    #expect(env["HOME"] == "/u")
}

@Test func urlScheme() {
    #expect(DobbyURL.action(for: URL(string: "dobby://pause-all")!)?.key == "pause_all")
    #expect(DobbyURL.action(for: URL(string: "dobby://stop?pid=7")!)?.pid == 7)
    #expect(DobbyURL.action(for: URL(string: "dobby://stop")!) == nil)
    #expect(DobbyURL.action(for: URL(string: "https://pause-all")!) == nil)
}

@Test func promptsDecodePythonJSON() throws {
    let raw: [[String: Any]] = [
        ["id": 3, "text": "Allow Dobby to press Send?", "detail": "In Mail", "allow": "Allow", "deny": "Deny",
         "kind": "danger", "pid": 4242, "name": "Tibble · Claude Code", "secs_left": 24.5],
        ["id": 4, "text": "Let Dobby use Notes?", "kind": "access", "pid": NSNull()],
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
