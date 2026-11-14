import Foundation
import Testing
@testable import Pinlet

private func row(_ pid: Int, _ state: String, _ detail: String = "", failures: Int = 0) -> TopbarView.Row {
    TopbarView.Row(pid: pid, name: "Bramble · Claude Code", state: state, title: "Bramble", detail: detail,
                   color: "#0E7490", shape: "spark", actions: [], failures: failures)
}

@Test func stageDecodesEngineJSON() {
    let stage = CompanionStage(json: [
        "server_pid": 9, "mode": "working", "step": "Click ‘Send’", "steps": 3, "goal": "Send the notes",
        "thought": "to attach them", "kind": "typing", "started": 100.0, "pointer": [10.5, 20.0],
        "risk": "danger", "bar_hidden": true,
    ])
    #expect(stage.serverPid == 9 && stage.mode == "working" && stage.steps == 3)
    #expect(stage.pointer == CGPoint(x: 10.5, y: 20))
    #expect(stage.risky && stage.barHidden)
    #expect(stage.seconds(now: Date(timeIntervalSince1970: 142)) == 42)
    #expect(CompanionStage(json: nil) == .empty)
    #expect(CompanionStage.clock(42) == "0:42" && CompanionStage.clock(3725) == "1h 02m")
}

@Test func bubbleShapeFollowsTheMood() {
    var stage = CompanionStage()
    stage.goal = "Send the Q3 notes"
    stage.step = "Click ‘New Message’"
    stage.steps = 2
    stage.thought = "starting a fresh message"
    let working = row(1, "working", "Working · Click ‘New Message’")
    let act = CompanionBubble.make(mood: .act, stage: stage, name: "Bramble", row: working, prompt: nil, quiet: false, log: nil)
    #expect(act?.kind == .cloud && act?.who == "Bramble · doing" && act?.goal == "Send the Q3 notes")
    #expect(act?.stepKind == .click && act?.meta.hasPrefix("Step 2") == true)
    let quiet = CompanionBubble.make(mood: .act, stage: stage, name: "Bramble", row: working, prompt: nil, quiet: true, log: nil)
    #expect(quiet?.kind == .chip)
    let paused = CompanionBubble.make(mood: .pause, stage: stage, name: "Bramble", row: row(1, "paused"), prompt: nil, quiet: false, log: nil)
    #expect(paused?.kind == .ice && paused?.who == "Bramble · frozen")
    let takeover = CompanionBubble.make(mood: .pause, stage: stage, name: "Bramble", row: row(1, "takeover"), prompt: nil, quiet: false, log: nil)
    #expect(takeover?.who == "You're in control")
    let error = CompanionBubble.make(mood: .error, stage: stage, name: "Bramble", row: row(1, "working", failures: 4), prompt: nil, quiet: false, log: nil)
    #expect(error?.kind == .burst && error?.thought.contains("4 times") == true)
    #expect(CompanionBubble.make(mood: .sleep, stage: stage, name: "Bramble", row: nil, prompt: nil, quiet: false, log: nil) == nil)
}

@Test func askBubbleNeverApproves() throws {
    let prompt = try #require(Prompt(json: ["id": 1, "text": "Send this email?"]))
    let ask = try #require(CompanionBubble.make(mood: .ask, stage: CompanionStage(), name: "Bramble", row: nil,
                                                prompt: prompt, quiet: false, log: nil))
    #expect(ask.kind == .speech && ask.goal == "Send this email?")
    #expect(ask.thought.contains("card"))
    #expect(!ask.showsLog)
}

@Test func ringNeverOffersAllow() {
    let frozen = RingItem.items(mood: .pause, frozen: true, hasTask: true, quiet: false, hasLog: false, napping: false)
    #expect(frozen.first == .resume)
    #expect(frozen.contains(.cancel) && frozen.contains(.step) && frozen.contains(.quiet))
    let asking = RingItem.items(mood: .ask, frozen: false, hasTask: true, quiet: true, hasLog: false, napping: false)
    #expect(asking == [.cancel, .tell, .view])
    let idle = RingItem.items(mood: .sleep, frozen: false, hasTask: false, quiet: false, hasLog: true, napping: true)
    #expect(idle == [.tell, .log, .wake])
    #expect(RingItem.cancel.needsHold && !RingItem.resume.needsHold)
    #expect(!RingItem.allCases.map(\.title).contains { $0.lowercased().contains("allow") })
}

@Test func freezeResumesOnlyWhatRimeFroze() {
    var freeze = CompanionFreeze()
    let rows = [row(1, "working"), row(2, "paused"), row(3, "confirm"), row(4, "ready")]
    let targets = CompanionFreeze.targets(rows)
    #expect(targets == [1, 3])
    freeze.froze(targets)
    let after = [row(1, "paused"), row(2, "paused"), row(3, "paused"), row(4, "ready")]
    #expect(freeze.thaw(after) == [1, 3])
    #expect(!freeze.any)
    freeze.froze([1])
    freeze.prune([row(1, "working")])  // resumed from the menu
    #expect(!freeze.any)
}

@Test func missionLogKeepsTheSteps() {
    var log = CompanionLog()
    var stage = CompanionStage()
    stage.goal = "Send notes"
    stage.started = 1000
    let t0 = Date(timeIntervalSince1970: 1001)
    stage.step = "Click ‘New’"
    log.observe(stage: stage, working: true, name: "Bramble", now: t0)
    log.observe(stage: stage, working: true, name: "Bramble", now: t0.addingTimeInterval(1))
    stage.step = "Type ‘hi’"
    log.observe(stage: stage, working: true, name: "Bramble", now: t0.addingTimeInterval(4))
    #expect(log.last == nil)
    log.observe(stage: stage, working: false, name: "Bramble", now: t0.addingTimeInterval(6))
    let task = log.last
    #expect(task?.steps.map(\.text) == ["Click ‘New’", "Type ‘hi’"])
    #expect(task?.goal == "Send notes" && task?.seconds == 5)
    let done = CompanionBubble.make(mood: .done, stage: CompanionStage(), name: "Bramble", row: nil, prompt: nil,
                                    quiet: false, log: task)
    #expect(done?.showsLog == true && done?.thought.contains("2 steps") == true)
}

@Test func dodgeGoesUpThenDownThenSideways() {
    let screen = CGRect(x: 0, y: 0, width: 1000, height: 800)
    let frame = CGRect(x: 900, y: 10, width: 90, height: 100)
    let up = CompanionDodge.origin(frame: frame, avoid: CGPoint(x: 940, y: 50), screen: screen)
    #expect(up.y > 50 && up.x == 900)
    let high = CGRect(x: 900, y: 690, width: 90, height: 100)
    let down = CompanionDodge.origin(frame: high, avoid: CGPoint(x: 940, y: 740), screen: screen)
    #expect(down.y + high.height < 740)
    #expect(CompanionDodge.cocoa(CGPoint(x: 5, y: 100), primaryHeight: 800) == CGPoint(x: 5, y: 700))
    #expect(CompanionDodge.topLeft(CGRect(x: 10, y: 700, width: 50, height: 60), primaryHeight: 800) == [10, 40, 50, 60])
}

@Test func peekTucksOnlyAtAnEdge() {
    let screen = CGRect(x: 0, y: 0, width: 1000, height: 800)
    #expect(CompanionPeek.tucked(frame: CGRect(x: 500, y: 10, width: 90, height: 100), screen: screen) == nil)
    let right = CompanionPeek.tucked(frame: CGRect(x: 905, y: 10, width: 90, height: 100), screen: screen)
    #expect(right != nil && right!.x > 1000 - 90)
    let left = CompanionPeek.tucked(frame: CGRect(x: 3, y: 10, width: 90, height: 100), screen: screen)
    #expect(left != nil && left!.x < 0)
    let out = CompanionPeek.out(frame: CGRect(x: 960, y: -40, width: 90, height: 100), screen: screen)
    #expect(out.x + 90 <= 1000 && out.y >= 0)
}

@Test func shakeSummonsOnce() {
    var shake = ShakeDetector()
    var fired = 0
    var t = 0.0
    for i in 0..<16 {
        let x: CGFloat = i % 2 == 0 ? 100 : 300
        if shake.feed(x: x, at: t) { fired += 1 }
        t += 0.05
    }
    #expect(fired == 1)
    var slow = ShakeDetector()
    var any = false
    for i in 0..<10 { any = slow.feed(x: CGFloat(i) * 10, at: Double(i) * 0.05) || any }
    #expect(!any)
}

@Test func voiceWordsNeedTheName() {
    #expect(VoicePause.matches("Rime, wait"))
    #expect(VoicePause.matches("okay rime stop"))
    #expect(VoicePause.matches("Rhyme pause"))
    #expect(!VoicePause.matches("wait a moment"))
    #expect(!VoicePause.matches("the rime of the ancient mariner"))
}

@Test func stepKindFromState() {
    #expect(StepKind(kind: "typing", step: "") == .type)
    #expect(StepKind(kind: "idle", step: "Scroll down") == .scroll)
    #expect(StepKind(kind: "idle", step: "Click ‘Send’") == .click)
    #expect(StepKind(kind: "idle", step: "") == .look)
}
