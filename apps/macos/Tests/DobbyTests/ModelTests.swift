import XCTest
@testable import Dobby

final class ModelTests: XCTestCase {
    func testTopbarDecodesPythonJSON() throws {
        let json = """
        {"header": "Dobby", "subtitle": "2 running · 1 working", "running": 2, "working": 1, "attention": false,
         "rows": [{"pid": 42, "name": "Claude Code", "state": "working", "title": "Claude Code",
                   "detail": "Working · click", "color": "#5B3FE0", "shape": "spark", "own": false,
                   "actions": [["Pause", "pause_toggle"], ["Stop", "stop"]]}],
         "all_actions": [["Pause all", "pause_all"]], "icon_state": "working", "icon_title": "2",
         "tooltip": "Dobby: 2 running", "palette": {"ok": "#22A365"}}
        """
        let obj = try JSONSerialization.jsonObject(with: Data(json.utf8)) as! [String: Any]
        let view = try XCTUnwrap(TopbarView(json: obj))
        XCTAssertEqual(view.rows.first?.pid, 42)
        XCTAssertEqual(view.rows.first?.actions.map(\.key), ["pause_toggle", "stop"])
        XCTAssertEqual(view.allActions.first?.label, "Pause all")
        XCTAssertEqual(view.iconState, "working")
        XCTAssertEqual(view.palette["ok"], "#22A365")
    }

    func testEngineConfig() throws {
        let data = Data(#"{"python": "/v/bin/python", "cwd": "/src", "env": {"DOBBY_MODEL": "x", "N": 1}}"#.utf8)
        let cfg = try XCTUnwrap(EngineConfig(data: data))
        let env = cfg.environment(base: ["HOME": "/u", "DOBBY_IDLE_EXIT": "5"])
        XCTAssertEqual(env["DOBBY_MODEL"], "x")
        XCTAssertNil(env["N"])
        XCTAssertEqual(env["DOBBY_IDLE_EXIT"], "0")
        XCTAssertEqual(env["DOBBY_OVERLAY_MENU_BAR"], "0")
        XCTAssertEqual(env["HOME"], "/u")
    }

    func testURLScheme() {
        XCTAssertEqual(DobbyURL.action(for: URL(string: "dobby://pause-all")!)?.key, "pause_all")
        XCTAssertEqual(DobbyURL.action(for: URL(string: "dobby://stop?pid=7")!)?.pid, 7)
        XCTAssertNil(DobbyURL.action(for: URL(string: "dobby://stop")!))
        XCTAssertNil(DobbyURL.action(for: URL(string: "https://pause-all")!))
    }
}
