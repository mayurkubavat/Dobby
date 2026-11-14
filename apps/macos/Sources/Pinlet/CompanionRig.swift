import AppKit

/// The animated character. Springs from pose to pose and adds a little life
/// on top (breathing, blinks, a glance at the pointer). Draws only while
/// something moves: asleep it ticks slowly, paused or locked it stops.
final class CompanionRigView: NSView {
    var onClick: () -> Void = {}
    var onMoved: () -> Void = {}
    /// Where to look instead of the pointer (the approval card), global Cocoa. Rime's look stays as drawn.
    var lookAt: NSPoint?

    var mood: CompanionMood = .sleep {
        didSet { if mood != oldValue { moodChanged() } }
    }

    /// False while the panel is hidden or the displays sleep: no timer at all.
    var running = false {
        didSet {
            guard running != oldValue else { return }
            if running {
                last = CACurrentMediaTime()
                retime()
                needsDisplay = true
            } else {
                stopTimer()
            }
        }
    }

    private var dials: [CompanionSpring] = CompanionPose.target(.sleep).values.map { CompanionSpring(x: $0) }
    private var props: [CompanionSpring] = CompanionProp.allCases.map { CompanionSpring(x: $0 == .zzz ? 1 : 0) }
    private var timer: Timer?
    private var interval: TimeInterval = 0
    private var last = CACurrentMediaTime()
    private var since = CACurrentMediaTime()
    private var nextBlink = CACurrentMediaTime() + 2
    private var blinkEnd = 0.0

    override var isFlipped: Bool { true }
    override var isOpaque: Bool { false }

    override init(frame: NSRect) {
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityLabel("Pinlet companion, asleep")
        setAccessibilityHelp("Click to freeze the AI and see Rime's options. Drag to move.")
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    deinit { timer?.invalidate() }

    private var reduceMotion: Bool { NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }

    private func moodChanged() {
        since = CACurrentMediaTime()
        if reduceMotion {
            snap()
            return
        }
        if mood == .done { dials[CompanionDial.y.rawValue].v = -420 }  // one jump
        if running { retime() }
    }

    /// Straight to the pose: Reduce Motion, or the first frame.
    private func snap() {
        let target = CompanionPose.target(mood)
        for i in dials.indices { dials[i] = CompanionSpring(x: target.values[i]) }
        for prop in CompanionProp.allCases { props[prop.rawValue] = CompanionSpring(x: mood.prop == prop ? 1 : 0) }
        needsDisplay = true
    }

    // ---------------------------------------------------------------- timing
    private func stopTimer() {
        timer?.invalidate()
        timer = nil
        interval = 0
    }

    /// How often to draw: fast for a change or a lively mood, slow asleep, not at all when still.
    private func wantedInterval(settled: Bool) -> TimeInterval {
        if !running || reduceMotion { return 0 }
        if CACurrentMediaTime() - since < 1.2 { return 1.0 / 60 }
        switch mood {
        case .act, .done, .ask, .error, .think: return 1.0 / 60
        case .awake: return 1.0 / 30
        case .sleep: return 1.0 / 15
        case .pause, .lock: return settled ? 0 : 1.0 / 60
        }
    }

    private func retime(settled: Bool = false) {
        let wanted = wantedInterval(settled: settled)
        guard wanted != interval else { return }
        stopTimer()
        interval = wanted
        guard wanted > 0 else { return }
        let t = Timer(timeInterval: wanted, repeats: true) { [weak self] _ in self?.tick() }
        t.tolerance = wanted * 0.2
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    /// Where the eyes look to follow the pointer on screen (rig units).
    private func pointerLook() -> (Double, Double)? {
        guard let window else { return nil }
        let center = window.convertPoint(toScreen: convert(NSPoint(x: bounds.midX, y: bounds.midY), to: nil))
        let mouse = lookAt ?? NSEvent.mouseLocation
        let dx = Double(mouse.x - center.x), dy = Double(center.y - mouse.y)
        let distance = max(1, (dx * dx + dy * dy).squareRoot())
        let reach = min(1, distance / 300)
        return (dx / distance * 4.5 * reach, dy / distance * 4 * reach)
    }

    private func tick() {
        let now = CACurrentMediaTime()
        let dt = min(0.07, max(0.001, now - last))
        last = now
        let e = now - since
        var target = CompanionPose.target(mood)
        let base = target

        // Life on top of the pose.
        switch mood {
        case .awake, .ask:
            if let look = pointerLook() {
                target[.lookX] = look.0
                target[.lookY] = look.1 + (mood == .ask ? -1 : 0)
            }
            if mood == .ask {
                target[.armRight] = 140 + sin(now * 6) * 14
            }
        case .think:
            target[.lookX] = -3 + cos(now * 1.3) * 2
            target[.lookY] = -5 + sin(now * 1.7) * 1.2
            target[.glow] = 0.55 + sin(now * 4) * 0.3
        case .act:
            if let look = pointerLook() {
                target[.lookX] = look.0
                target[.lookY] = look.1
            }
            target[.y] = -abs(sin(now * 5.5)) * 6
            target[.squash] = 1 - max(0, cos(now * 11)) * 0.05
            target[.armLeft] = 18 + sin(now * 11) * 16
            target[.armRight] = 18 - sin(now * 11) * 16
        case .error:
            if e < 0.55 { target[.x] = sin(e * 42) * 5 * (1 - e / 0.55) }
        case .done:
            if e > 1.6 {
                target[.armLeft] = 20
                target[.armRight] = 20
                target[.mouthOpen] = 0.3
            }
        case .sleep:
            target[.tuft] = 26 + sin(now * 1.5) * 3
        case .pause, .lock:
            break
        }
        if mood != .pause && mood != .lock && mood != .act {
            let calm = mood == .sleep
            target[.squash] += sin(now * (calm ? 1.5 : 2.3)) * (calm ? 0.028 : 0.012)
        }
        if now > nextBlink {
            blinkEnd = now + 0.13
            nextBlink = now + 2.2 + Double.random(in: 0...3.2)
        }
        if now < blinkEnd && base[.eyeOpen] > 0.4 && mood != .pause {
            target[.eyeOpen] = 0.02
            target[.eyeCurve] = 0
        }

        var settled = true
        for i in dials.indices {
            dials[i].step(to: target.values[i], dt: dt)
            if !dials[i].settled(at: target.values[i]) { settled = false }
        }
        let y = CompanionDial.y.rawValue
        if dials[y].x > 0 {  // the ground
            dials[y].x = 0
            dials[y].v = 0
        }
        for prop in CompanionProp.allCases {
            let goal = mood.prop == prop ? 1.0 : 0.0
            props[prop.rawValue].step(to: goal, dt: dt)
            if !props[prop.rawValue].settled(at: goal) { settled = false }
        }
        needsDisplay = true
        retime(settled: settled)
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        snap()
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        ctx.saveGState()
        ctx.scaleBy(x: bounds.width / CompanionArt.box.width, y: bounds.height / CompanionArt.box.height)
        CompanionArt.draw(in: ctx, pose: CompanionPose(values: dials.map(\.x)), props: props.map(\.x),
                          time: CACurrentMediaTime())
        ctx.restoreGState()
    }

    // ----------------------------------------------------------------- mouse
    private var dragFrom: NSPoint?
    private var originFrom: NSPoint?
    private var dragged = false

    /// True while the person is moving the companion.
    var dragging: Bool { dragFrom != nil && dragged }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func mouseDown(with event: NSEvent) {
        dragFrom = NSEvent.mouseLocation
        originFrom = window?.frame.origin
        dragged = false
    }

    override func mouseDragged(with event: NSEvent) {
        guard let from = dragFrom, let origin = originFrom else { return }
        let now = NSEvent.mouseLocation
        let dx = now.x - from.x, dy = now.y - from.y
        if !dragged && (dx * dx + dy * dy).squareRoot() > 4 { dragged = true }
        if dragged { window?.setFrameOrigin(NSPoint(x: origin.x + dx, y: origin.y + dy)) }
    }

    override func mouseUp(with event: NSEvent) {
        if dragged { onMoved() } else { onClick() }
        dragFrom = nil
        originFrom = nil
        dragged = false
    }
}
