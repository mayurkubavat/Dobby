import CoreGraphics
import Foundation

/// What the floating bar shows right now, as the engine reports it (the
/// `stage` in the replies to the `topbar` and `companion` control ops). Rime
/// draws it in its thought cloud when it stands in for the bar.
struct CompanionStage: Equatable {
    var serverPid = 0
    /// confirm, paused, takeover, working or awake; "" when nothing is busy.
    var mode = ""
    var step = ""
    var steps = 0
    var elapsed = 0.0
    var app = ""
    var goal = ""
    var thought = ""
    /// idle, thinking, typing, keys, scrolling, dragging, waiting_user.
    var kind = "idle"
    /// When the task started (wall clock, seconds since 1970); 0 when none.
    var started = 0.0
    /// The agent pointer in global top-left points, while it is on screen.
    var pointer: CGPoint?
    var risk = "ok"
    var barHidden = false

    static let empty = CompanionStage()

    init() {}

    init(json: [String: Any]?) {
        guard let json else { return }
        serverPid = (json["server_pid"] as? NSNumber)?.intValue ?? 0
        mode = json["mode"] as? String ?? ""
        step = json["step"] as? String ?? ""
        steps = (json["steps"] as? NSNumber)?.intValue ?? 0
        elapsed = (json["elapsed"] as? NSNumber)?.doubleValue ?? 0
        app = json["app"] as? String ?? ""
        goal = json["goal"] as? String ?? ""
        thought = json["thought"] as? String ?? ""
        kind = json["kind"] as? String ?? "idle"
        started = (json["started"] as? NSNumber)?.doubleValue ?? 0
        if let p = json["pointer"] as? [Any], p.count == 2,
           let x = (p[0] as? NSNumber)?.doubleValue, let y = (p[1] as? NSNumber)?.doubleValue {
            pointer = CGPoint(x: x, y: y)
        }
        risk = json["risk"] as? String ?? "ok"
        barHidden = json["bar_hidden"] as? Bool ?? false
    }

    /// Seconds the task has run (counts on between polls).
    func seconds(now: Date = Date()) -> TimeInterval {
        started > 0 ? max(0, now.timeIntervalSince1970 - started) : elapsed
    }

    /// "0:42", "12:05", "1h 03m".
    static func clock(_ secs: TimeInterval) -> String {
        let s = max(0, Int(secs))
        return s < 3600 ? "\(s / 60):\(String(format: "%02d", s % 60))" : "\(s / 3600)h \(String(format: "%02d", s % 3600 / 60))m"
    }

    var risky: Bool { risk == "danger" || risk == "caution" }
}

/// The shape of Rime's bubble says the state before you read it, the way
/// manga lettering does.
enum BubbleKind: String, Equatable {
    /// Puffy, with trailing puffs: the AI is thinking or working.
    case cloud
    /// Round, with a tail: Rime is saying something.
    case speech
    /// Jagged: something keeps going wrong.
    case burst
    /// Ice-edged: frozen (paused).
    case ice
    /// Only the step and the timer ("Quieter cloud").
    case chip
}

/// The kind of step, for the small icon beside it.
enum StepKind: String, Equatable {
    case look, click, type, keys, scroll, drag, wait

    init(kind: String, step: String) {
        switch kind {
        case "typing": self = .type
        case "keys": self = .keys
        case "scrolling": self = .scroll
        case "dragging": self = .drag
        case "thinking": self = .look
        case "waiting_user": self = .wait
        default:
            let s = step.lowercased()
            if s.hasPrefix("type") { self = .type }
            else if s.hasPrefix("press") || s.hasPrefix("hotkey") { self = .keys }
            else if s.hasPrefix("scroll") { self = .scroll }
            else if s.hasPrefix("drag") { self = .drag }
            else if s.isEmpty { self = .look }
            else { self = .click }
        }
    }

    /// SF Symbol name.
    var symbol: String {
        switch self {
        case .look: return "eye"
        case .click: return "cursorarrow.click"
        case .type: return "keyboard"
        case .keys: return "command"
        case .scroll: return "arrow.up.and.down"
        case .drag: return "arrow.up.and.down.and.arrow.left.and.right"
        case .wait: return "hand.raised"
        }
    }
}

/// One bubble's content. Rime never draws a button in it that acts for the
/// AI: the only button is the mission log after a task.
struct CompanionBubble: Equatable {
    var kind: BubbleKind
    var who = ""
    var goal = ""
    var thought = ""
    var doing = ""
    var stepKind: StepKind?
    var meta = ""
    var showsLog = false

    /// What the bubble says, for VoiceOver.
    var spoken: String {
        [who, goal, thought, doing, meta].filter { !$0.isEmpty }.joined(separator: ". ")
    }

    /// What Rime shows for a mood. nil means no bubble.
    ///
    /// - Parameters:
    ///   - name: the AI Rime speaks for ("Bramble").
    ///   - row: that AI's menu row (its step when the engine has no stage).
    ///   - prompt: the open Allow/Deny question, if any.
    ///   - quiet: "Quieter cloud": only the step and the timer.
    ///   - log: the last finished task, for the done bubble.
    static func make(mood: CompanionMood, stage: CompanionStage, name: String, row: TopbarView.Row?,
                     prompt: Prompt?, quiet: Bool, log: CompanionLog.Task?, now: Date = Date()) -> CompanionBubble? {
        let who = name.isEmpty ? "Your AI" : name
        let secs = stage.seconds(now: now)
        let stepText = !stage.step.isEmpty ? stage.step : row.map { ActivityModel.stepLine($0.detail) } ?? ""
        let count = stage.steps > 0 ? "Step \(stage.steps)" : ""
        let meta = [count, secs >= 1 ? CompanionStage.clock(secs) : ""].filter { !$0.isEmpty }.joined(separator: " · ")
        switch mood {
        case .sleep, .lock, .awake:
            return nil
        case .ask:
            let question = prompt?.text ?? ""
            return CompanionBubble(kind: .speech, who: "\(who) · needs you", goal: question,
                                   thought: "Answer on the card. I can't approve for you.")
        case .pause:
            let takeover = row?.state == "takeover" || stage.mode == "takeover"
            return CompanionBubble(kind: .ice, who: takeover ? "You're in control" : "\(who) · frozen",
                                   goal: stage.goal,
                                   thought: takeover ? "\(who) waits until you let go." : "Stopped before its next step.",
                                   doing: stepText.isEmpty ? "" : "Last: \(stepText)",
                                   stepKind: stepText.isEmpty ? nil : StepKind(kind: stage.kind, step: stepText),
                                   meta: meta)
        case .think, .act:
            if quiet {
                return CompanionBubble(kind: .chip, who: who, meta: meta.isEmpty ? "Working" : meta)
            }
            let thinking = mood == .think
            var thought = stage.thought
            if thought.isEmpty && thinking && stepText.isEmpty { thought = "Looking at the screen." }
            return CompanionBubble(kind: .cloud, who: "\(who) · \(thinking ? "thinking" : "doing")",
                                   goal: stage.goal, thought: thought, doing: stepText,
                                   stepKind: stepText.isEmpty ? nil : StepKind(kind: stage.kind, step: stepText),
                                   meta: meta)
        case .error:
            let tries = row?.failures ?? 0
            return CompanionBubble(kind: .burst, who: who,
                                   thought: tries > 1 ? "That failed \(tries) times in a row. Want to take a look?"
                                                      : "That keeps failing. Want to take a look?",
                                   doing: stepText)
        case .done:
            if let log, !log.steps.isEmpty {
                let n = log.steps.count
                return CompanionBubble(kind: .speech, who: who,
                                       thought: "Done. \(n) step\(n == 1 ? "" : "s") in \(CompanionStage.clock(log.seconds)).",
                                       showsLog: true)
            }
            return CompanionBubble(kind: .speech, who: who, thought: "All done.")
        }
    }
}

/// The buttons that fan out from Rime when it is clicked.
enum RingItem: String, CaseIterable, Equatable {
    case resume, cancel, step, tell, view, quiet, loud, log, nap, wake

    var title: String {
        switch self {
        case .resume: return "Resume"
        case .cancel: return "Cancel task"
        case .step: return "One step"
        case .tell: return "Tell"
        case .view: return "Show what it sees"
        case .quiet: return "Quieter cloud"
        case .loud: return "Fuller cloud"
        case .log: return "Last task"
        case .nap: return "Nap"
        case .wake: return "Wake up"
        }
    }

    var help: String {
        switch self {
        case .resume: return "The AI carries on from where it froze."
        case .cancel: return "Hold for a moment: the AI stops this task for good."
        case .step: return "Lets exactly one action run, then freezes again."
        case .tell: return "The AI reads your note before its next step."
        case .view: return "Brackets on everything the AI found on its last look."
        case .quiet: return "Only the step count and the timer."
        case .loud: return "Thoughts and steps again."
        case .log: return "Every step of the last task, with times."
        case .nap: return "Rime sleeps until something happens."
        case .wake: return "Rime wakes up."
        }
    }

    var symbol: String {
        switch self {
        case .resume: return "play.fill"
        case .cancel: return "xmark"
        case .step: return "forward.frame.fill"
        case .tell: return "text.bubble.fill"
        case .view: return "viewfinder"
        case .quiet: return "minus"
        case .loud: return "plus"
        case .log: return "scroll.fill"
        case .nap: return "moon.fill"
        case .wake: return "sun.max.fill"
        }
    }

    /// Cancel throws work away, so it takes a short hold; everything else is one click.
    var needsHold: Bool { self == .cancel }

    /// What the ring offers. It never offers Allow: approvals stay on the card.
    static func items(mood: CompanionMood, frozen: Bool, hasTask: Bool, quiet: Bool, hasLog: Bool,
                      napping: Bool) -> [RingItem] {
        if frozen {
            return [.resume, .cancel, .step, .tell, .view, quiet ? .loud : .quiet]
        }
        if mood == .ask { return [.cancel, .tell, .view] }
        if hasTask { return [.cancel, .tell, .view, quiet ? .loud : .quiet] }
        var out: [RingItem] = [.tell]
        if hasLog { out.append(.log) }
        out.append(napping ? .wake : .nap)
        return out
    }
}

/// Which AIs a click on Rime froze, so Resume lets go of exactly those.
struct CompanionFreeze: Equatable {
    private(set) var pids: Set<Int> = []

    /// The rows a freeze stops: every AI that is working or about to.
    static func targets(_ rows: [TopbarView.Row]) -> [Int] {
        rows.filter { ["working", "awake", "confirm"].contains($0.state) }.map(\.pid)
    }

    mutating func froze(_ pids: [Int]) { self.pids.formUnion(pids) }

    /// The AIs to resume: the ones Rime froze that are still paused.
    mutating func thaw(_ rows: [TopbarView.Row]) -> [Int] {
        let paused = Set(rows.filter { $0.state == "paused" }.map(\.pid))
        let out = pids.intersection(paused)
        pids = []
        return out.sorted()
    }

    /// Forget AIs that are gone or no longer paused (resumed from the menu).
    mutating func prune(_ rows: [TopbarView.Row]) {
        let paused = Set(rows.filter { $0.state == "paused" || $0.state == "takeover" }.map(\.pid))
        pids.formIntersection(paused)
    }

    var any: Bool { !pids.isEmpty }
}

/// The steps Rime saw during a task, kept as the mission log.
struct CompanionLog: Equatable {
    struct Step: Equatable {
        var at: TimeInterval   // seconds into the task
        var text: String
    }

    struct Task: Equatable {
        var name: String
        var goal: String
        var steps: [Step]
        var seconds: TimeInterval
        var ended: Date
    }

    private(set) var current: Task?
    private(set) var last: Task?
    static let maxSteps = 200

    /// Feed every stage update: a new step text is a new line; the end of the work ends the task.
    mutating func observe(stage: CompanionStage, working: Bool, name: String, now: Date = Date()) {
        if working {
            if current == nil {
                current = Task(name: name, goal: stage.goal, steps: [], seconds: 0, ended: now)
            }
            guard var task = current else { return }
            let secs = stage.seconds(now: now)
            task.seconds = max(task.seconds, secs)
            if !stage.goal.isEmpty { task.goal = stage.goal }
            if !stage.step.isEmpty && task.steps.last?.text != stage.step && task.steps.count < CompanionLog.maxSteps {
                task.steps.append(Step(at: secs, text: stage.step))
            }
            current = task
        } else if var task = current {
            task.ended = now
            if !task.steps.isEmpty { last = task }
            current = nil
        }
    }
}

/// Where Rime goes to get out of the AI's way: straight up, or down when
/// there is no room above, or sideways as a last resort. All in Cocoa
/// coordinates (bottom-left origin).
enum CompanionDodge {
    static func origin(frame: CGRect, avoid: CGPoint, screen: CGRect, margin: CGFloat = 36) -> CGPoint {
        let up = CGPoint(x: frame.minX, y: avoid.y + margin)
        if up.y + frame.height <= screen.maxY { return up }
        let down = CGPoint(x: frame.minX, y: avoid.y - margin - frame.height)
        if down.y >= screen.minY { return down }
        let left = CGPoint(x: avoid.x - margin - frame.width, y: frame.minY)
        if left.x >= screen.minX { return left }
        return CGPoint(x: avoid.x + margin, y: frame.minY)
    }

    /// A global top-left point (Quartz, Accessibility) in Cocoa coordinates.
    static func cocoa(_ p: CGPoint, primaryHeight: CGFloat) -> CGPoint {
        CGPoint(x: p.x, y: primaryHeight - p.y)
    }

    /// A Cocoa rect as a global top-left [x, y, w, h].
    static func topLeft(_ r: CGRect, primaryHeight: CGFloat) -> [Double] {
        [Double(r.minX), Double(primaryHeight - r.maxY), Double(r.width), Double(r.height)]
    }
}

/// Rime tucked behind a screen edge with only its eyes showing, while idle.
enum CompanionPeek {
    /// How much of Rime stays visible when tucked.
    static let showing: CGFloat = 0.42
    static let snap: CGFloat = 14

    /// The tucked origin when `frame` was dropped against the left or right
    /// edge of `screen`; nil when it was not.
    static func tucked(frame: CGRect, screen: CGRect) -> CGPoint? {
        if frame.minX <= screen.minX + snap {
            return CGPoint(x: screen.minX - frame.width * (1 - showing), y: frame.minY)
        }
        if frame.maxX >= screen.maxX - snap {
            return CGPoint(x: screen.maxX - frame.width * showing, y: frame.minY)
        }
        return nil
    }

    /// Back fully on screen (for a task, or a click).
    static func out(frame: CGRect, screen: CGRect) -> CGPoint {
        CGPoint(x: min(max(frame.minX, screen.minX + 4), screen.maxX - frame.width - 4),
                y: min(max(frame.minY, screen.minY + 4), screen.maxY - frame.height - 4))
    }
}

/// Shake the pointer to summon Rime: quick left-right swings.
struct ShakeDetector {
    private var samples: [(t: TimeInterval, x: CGFloat)] = []
    private var lastFire: TimeInterval = -10
    static let window: TimeInterval = 0.8
    static let reversals = 4
    static let swing: CGFloat = 70

    /// Feed pointer positions; true once when a shake completes.
    mutating func feed(x: CGFloat, at t: TimeInterval) -> Bool {
        samples.append((t, x))
        samples.removeAll { t - $0.t > ShakeDetector.window }
        guard t - lastFire > 1.5, samples.count >= 6 else { return false }
        var turns = 0
        var direction: CGFloat = 0
        var anchor = samples[0].x
        for s in samples.dropFirst() {
            let d = s.x - anchor
            if abs(d) >= ShakeDetector.swing {
                let dir: CGFloat = d > 0 ? 1 : -1
                if direction != 0 && dir != direction { turns += 1 }
                direction = dir
                anchor = s.x
            }
        }
        if turns >= ShakeDetector.reversals - 1 {
            lastFire = t
            samples.removeAll()
            return true
        }
        return false
    }
}

/// "Rime, wait": the words that freeze the AI when voice is on.
enum VoicePause {
    static func matches(_ transcript: String) -> Bool {
        let words = transcript.lowercased()
            .components(separatedBy: CharacterSet.letters.inverted).filter { !$0.isEmpty }
        guard let last = words.suffix(4).last else { return false }
        let tail = Array(words.suffix(4))
        let named = tail.contains("rime") || tail.contains("rhyme") || tail.contains("pinlet")
        let stop = ["wait", "stop", "pause", "freeze", "hold"]
        return named && (stop.contains(last) || tail.contains(where: stop.contains))
    }
}
