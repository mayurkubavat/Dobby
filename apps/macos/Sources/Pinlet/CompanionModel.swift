import Foundation

/// What the companion shows: one mood at a time, read from the same poll the menu uses.
enum CompanionMood: String, CaseIterable {
    case sleep, awake, think, act, ask, pause, done, error, lock

    /// The small sign beside the character in this mood.
    var prop: CompanionProp? {
        switch self {
        case .sleep: return .zzz
        case .think: return .think
        case .act, .done: return .spark
        case .ask: return .ask
        case .pause: return .pause
        case .error: return .sweat
        case .lock: return .lock
        case .awake: return nil
        }
    }

    /// For VoiceOver.
    var spoken: String {
        switch self {
        case .sleep: return "asleep"
        case .awake: return "awake"
        case .think: return "thinking"
        case .act: return "working"
        case .ask: return "asking for your approval"
        case .pause: return "paused"
        case .done: return "done"
        case .error: return "having trouble"
        case .lock: return "waiting for the screen to unlock"
        }
    }
}

enum CompanionProp: Int, CaseIterable {
    case zzz, think, ask, pause, spark, sweat, lock
}

/// The rig's dials. A pose is one value per dial; the view springs from pose to pose.
enum CompanionDial: Int, CaseIterable {
    case y, x, squash, tilt
    case eyeOpen, eyeCurve, lookX, lookY
    case browTilt, browY, brow
    case mouthWidth, mouthCurve, mouthOpen
    case armLeft, armRight
    case tuft, glow, blush, dim
}

struct CompanionPose: Equatable {
    var values: [Double]

    subscript(_ dial: CompanionDial) -> Double {
        get { values[dial.rawValue] }
        set { values[dial.rawValue] = newValue }
    }

    /// Awake and at ease.
    static let base: CompanionPose = {
        var p = CompanionPose(values: [Double](repeating: 0, count: CompanionDial.allCases.count))
        p[.squash] = 1; p[.eyeOpen] = 1; p[.mouthWidth] = 7; p[.mouthCurve] = 4
        p[.glow] = 0.2; p[.blush] = 0.55; p[.dim] = 1
        return p
    }()

    static func target(_ mood: CompanionMood) -> CompanionPose {
        var p = base
        func set(_ pairs: [(CompanionDial, Double)]) { for (dial, value) in pairs { p[dial] = value } }
        switch mood {
        case .sleep:
            set([(.eyeOpen, 0), (.eyeCurve, -1), (.mouthWidth, 3), (.mouthCurve, 0), (.mouthOpen, 0.5),
                 (.tuft, 26), (.squash, 0.93), (.tilt, -4), (.glow, 0)])
        case .awake:
            set([(.mouthCurve, 5)])
        case .think:
            set([(.lookX, -3), (.lookY, -5), (.browTilt, -6), (.browY, -2), (.brow, 1), (.mouthWidth, 4),
                 (.mouthCurve, -1), (.armRight, -60), (.tilt, -5), (.glow, 0.6)])
        case .act:
            set([(.eyeOpen, 0.86), (.browTilt, 9), (.brow, 1), (.mouthWidth, 5), (.mouthCurve, 3), (.glow, 1),
                 (.armLeft, 18), (.armRight, 18)])
        case .ask:
            set([(.tilt, 7), (.armRight, 140), (.mouthWidth, 5), (.mouthCurve, 2), (.mouthOpen, 0.5), (.brow, 1),
                 (.browTilt, -9), (.browY, -3), (.glow, 0.5)])
        case .pause:
            set([(.eyeOpen, 0.48), (.mouthWidth, 6), (.mouthCurve, 0), (.tuft, 10), (.glow, 0.1)])
        case .done:
            set([(.eyeOpen, 0), (.eyeCurve, 1), (.mouthWidth, 9), (.mouthCurve, 8), (.mouthOpen, 0.7),
                 (.armLeft, 140), (.armRight, 140), (.blush, 1), (.glow, 0.8)])
        case .error:
            set([(.browTilt, -13), (.brow, 1), (.mouthWidth, 6), (.mouthCurve, -5), (.tuft, 18), (.glow, 0),
                 (.lookY, 1)])
        case .lock:
            set([(.eyeOpen, 0), (.eyeCurve, 0), (.mouthWidth, 5), (.mouthCurve, 0), (.squash, 0.95), (.tuft, 20),
                 (.glow, 0), (.dim, 0.55)])
        }
        return p
    }
}

/// A damped spring: a little overshoot, so a change of pose has life in it.
struct CompanionSpring {
    var x: Double
    var v = 0.0

    mutating func step(to target: Double, dt: Double, stiffness: Double = 170, damping: Double = 13) {
        v += (target - x) * stiffness * dt
        v *= exp(-damping * dt)
        x += v * dt
    }

    func settled(at target: Double) -> Bool { abs(target - x) < 0.01 && abs(v) < 0.05 }
}

/// Turns what Pinlet reports each second into the companion's mood, with a
/// short cheer when a Pinlet that was working goes back to rest.
struct CompanionTracker {
    private(set) var mood: CompanionMood = .sleep
    private var steady: CompanionMood = .sleep
    private var workingPid: Int?
    private var cheerUntil: Date?

    static let cheer: TimeInterval = 4
    /// Failed tool calls in a row before the companion looks worried.
    static let troubleAfter = 3

    static func steady(view: TopbarView, prompts: [Prompt], reachable: Bool, locked: Bool) -> CompanionMood {
        if locked { return .lock }
        guard reachable else { return .sleep }
        let rows = view.rows
        if prompts.contains(where: { !$0.missed }) || rows.contains(where: { $0.state == "confirm" }) { return .ask }
        if let row = rows.first(where: { $0.state == "working" }) {
            if row.failures >= troubleAfter { return .error }
            return ActivityModel.stepLine(row.detail).isEmpty ? .think : .act
        }
        if rows.contains(where: { $0.state == "awake" }) { return .think }
        if rows.contains(where: { $0.state == "paused" || $0.state == "takeover" }) { return .pause }
        if rows.contains(where: { $0.state == "listening" || $0.state == "waiting" }) { return .awake }
        return .sleep
    }

    @discardableResult
    mutating func update(view: TopbarView, prompts: [Prompt], reachable: Bool, locked: Bool,
                         now: Date = Date()) -> CompanionMood {
        let next = CompanionTracker.steady(view: view, prompts: prompts, reachable: reachable, locked: locked)
        let resting = next == .awake || next == .sleep
        if resting {
            // A cheer only for work that ended by itself: its Pinlet is still there and was not stopped.
            let wasWorking = steady == .act || steady == .think
            if wasWorking, let pid = workingPid,
               let row = view.rows.first(where: { $0.pid == pid }), row.state != "stopped" {
                cheerUntil = now.addingTimeInterval(CompanionTracker.cheer)
            }
        } else {
            cheerUntil = nil
        }
        if let row = view.rows.first(where: { $0.state == "working" || $0.state == "awake" }) {
            workingPid = row.pid
        } else if resting {
            workingPid = nil
        }
        steady = next
        if let until = cheerUntil, until > now {
            mood = .done
        } else {
            cheerUntil = nil
            mood = next
        }
        return mood
    }

    /// What the companion says in a mood ("" = nothing). Its own state only, never advice.
    static func line(for mood: CompanionMood, view: TopbarView) -> String {
        switch mood {
        case .act:
            let step = view.rows.first(where: { $0.state == "working" }).map { ActivityModel.stepLine($0.detail) } ?? ""
            return step.count > 70 ? String(step.prefix(69)) + "…" : step
        case .think: return "Thinking."
        case .ask: return "May I? The card has the buttons."
        case .pause: return "Paused. I'll wait right here."
        case .done: return "All done."
        case .error: return "That keeps failing. Want to take a look?"
        case .sleep, .awake, .lock: return ""
        }
    }
}

/// The companion's own settings (UserDefaults, with `PINLET_COMPANION` on top).
enum CompanionSettings {
    static let sizes = [56, 72, 96, 128]
    static let defaultSize = 72

    static func enabled(defaults: UserDefaults = .standard,
                        env: [String: String] = ProcessInfo.processInfo.environment) -> Bool {
        switch (env["PINLET_COMPANION"] ?? "").lowercased() {
        case "1", "on", "true", "yes": return true
        case "0", "off", "false", "no": return false
        default: return defaults.bool(forKey: "companion")
        }
    }

    static func size(defaults: UserDefaults = .standard) -> Int {
        let stored = defaults.integer(forKey: "companionSize")
        return sizes.contains(stored) ? stored : defaultSize
    }

    private static func flag(_ key: String, _ fallback: Bool, _ defaults: UserDefaults) -> Bool {
        defaults.object(forKey: key) as? Bool ?? fallback
    }

    /// "Show progress with: Rime": Rime's cloud and click stand in for the floating bar.
    static func replacesBar(defaults: UserDefaults = .standard) -> Bool { flag("companionReplacesBar", true, defaults) }
    /// The ice thread from Rime to the AI's pointer.
    static func tether(defaults: UserDefaults = .standard) -> Bool { flag("companionTether", true, defaults) }
    /// Shake the pointer to call Rime over.
    static func summon(defaults: UserDefaults = .standard) -> Bool { flag("companionSummon", true, defaults) }
    /// Tuck Rime behind a screen edge when idle.
    static func peek(defaults: UserDefaults = .standard) -> Bool { flag("companionPeek", true, defaults) }
    /// "Quieter cloud": only the step and the timer.
    static func quiet(defaults: UserDefaults = .standard) -> Bool { flag("companionQuiet", false, defaults) }

    /// Words in the bubble: off with Personality "Off" or "Minimal text".
    static func speaks(defaults: UserDefaults = .standard) -> Bool {
        defaults.string(forKey: "personality") != "off" && !defaults.bool(forKey: "minimalText")
    }
}
