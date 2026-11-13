import AppKit
import SwiftUI

/// The companion: a small character in a corner of the screen that shows what
/// Pinlet is doing (asleep, thinking, working, asking, paused, done) and
/// takes a note for the AI when clicked.
///
/// It only mirrors state. Allow and Deny stay on the card (PromptPanel), so
/// the character is never a way to approve something. Off unless switched on
/// in Settings > Appearance or with `PINLET_COMPANION=1`.
///
/// Its window is a clear, non-activating panel sized to the character and its
/// bubble: clear pixels pass clicks through, and while an AI is working the
/// whole panel does, so it never sits between the AI and what it clicks.
final class CompanionPanel {
    /// (pid, text) → sends the note (control op "tell"); calls back with the reply or nil.
    var onTell: (_ pid: Int, _ text: String, _ done: @escaping ([String: Any]?) -> Void) -> Void = { _, _, done in done(nil) }

    private let state = CompanionState()
    private let rig = CompanionRigView()
    private var tracker = CompanionTracker()
    private var panel: CompanionWindow?
    private var view = TopbarView.empty
    private var prompts: [Prompt] = []
    private var reachable = false
    private var locked = false
    private var lineToken = 0
    private var observers: [NSObjectProtocol] = []

    static let lineSeconds: TimeInterval = 6

    var isShown: Bool { panel?.isVisible == true }

    init() {
        rig.onClick = { [weak self] in self?.toggleAsk() }
        rig.onMoved = { [weak self] in self?.rememberPlace() }
        let lock = DistributedNotificationCenter.default()
        observers.append(lock.addObserver(forName: .init("com.apple.screenIsLocked"), object: nil, queue: .main) {
            [weak self] _ in self?.setLocked(true)
        })
        observers.append(lock.addObserver(forName: .init("com.apple.screenIsUnlocked"), object: nil, queue: .main) {
            [weak self] _ in self?.setLocked(false)
        })
        let workspace = NSWorkspace.shared.notificationCenter
        observers.append(workspace.addObserver(forName: NSWorkspace.screensDidSleepNotification, object: nil, queue: .main) {
            [weak self] _ in self?.rig.running = false
        })
        observers.append(workspace.addObserver(forName: NSWorkspace.screensDidWakeNotification, object: nil, queue: .main) {
            [weak self] _ in self?.rig.running = self?.isShown == true
        })
    }

    /// New content from the shared Pinlet (every poll). Also follows the Settings switch.
    func update(view: TopbarView, prompts: [Prompt], reachable: Bool) {
        self.view = view
        self.prompts = prompts
        self.reachable = reachable
        guard CompanionSettings.enabled() else {
            if isShown { hide() }
            return
        }
        let mood = tracker.update(view: view, prompts: prompts, reachable: reachable, locked: locked)
        let size = CGFloat(CompanionSettings.size())
        if state.size != size { state.size = size }
        let target = ActivityModel.defaultSelection(view.rows).flatMap { pid in
            view.rows.first(where: { $0.pid == pid })?.firstName
        } ?? ""
        if state.target != target { state.target = target }
        if !isShown { show() }
        apply(mood)
        refit()
    }

    private func apply(_ mood: CompanionMood) {
        let line = CompanionSettings.speaks() ? CompanionTracker.line(for: mood, view: view) : ""
        let changed = mood != state.mood
        if changed {
            state.mood = mood
            rig.mood = mood
            rig.setAccessibilityLabel("Pinlet companion, \(mood.spoken)")
            if mood == .lock || mood == .ask { state.asking = false }
        }
        if changed || (mood == .act && line != state.lastLine) { say(line) }
        // An AI at work clicks through the companion, never on it.
        panel?.ignoresMouseEvents = !state.asking && (mood == .act || mood == .think || mood == .lock)
    }

    /// Show a line in the bubble for a few seconds ("" clears it).
    private func say(_ line: String, for seconds: TimeInterval = CompanionPanel.lineSeconds) {
        state.lastLine = line
        state.line = line
        lineToken += 1
        let token = lineToken
        guard !line.isEmpty else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { [weak self] in
            guard let self, self.lineToken == token else { return }
            self.state.line = ""
            self.refit()
        }
    }

    private func setLocked(_ value: Bool) {
        locked = value
        guard isShown else { return }
        apply(tracker.update(view: view, prompts: prompts, reachable: reachable, locked: value))
        refit()
    }

    // ------------------------------------------------------------------ ask
    /// Click the character or press Control-Option-Space: a field to tell the AI something.
    func toggleAsk() {
        guard isShown, state.mood != .lock else { return }
        state.asking.toggle()
        state.status = ""
        if state.asking {
            panel?.ignoresMouseEvents = false
            panel?.makeKey()
            state.focusField += 1
        }
        refit()
    }

    private func submit() {
        let text = state.draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !state.sending else { return }
        guard let pid = ActivityModel.defaultSelection(view.rows) else {
            state.status = "No AI is connected. Ask your AI app to use Pinlet first."
            refit()
            return
        }
        let name = view.rows.first(where: { $0.pid == pid })?.firstName ?? "Pinlet"
        state.sending = true
        onTell(pid, text) { [weak self] reply in
            guard let self else { return }
            self.state.sending = false
            guard let reply, reply["ok"] as? Bool == true else {
                self.state.status = "Couldn't reach Pinlet. Your note is still here."
                self.refit()
                return
            }
            self.state.draft = ""
            self.state.asking = false
            let woke = reply["woke"] as? Bool == true
            self.say(woke ? "Woke \(name) with your note." : "Sent. \(name) reads it at its next step.")
            self.refit()
        }
    }

    // --------------------------------------------------------------- window
    private func show() {
        let panel = self.panel ?? makePanel()
        self.panel = panel
        let appearance = AppSettings.currentAppearance()
        panel.appearance = appearance
        panel.contentView?.appearance = appearance
        fit()
        panel.orderFrontRegardless()
        rig.running = true
    }

    func hide() {
        rig.running = false
        state.asking = false
        panel?.orderOut(nil)
    }

    private func makePanel() -> CompanionWindow {
        let content = CompanionView(state: state, rig: rig,
                                    submit: { [weak self] in self?.submit() },
                                    close: { [weak self] in
                                        self?.state.asking = false
                                        self?.refit()
                                    })
        let panel = CompanionWindow()
        panel.contentView = NSHostingView(rootView: content)
        panel.onEscape = { [weak self] in
            self?.state.asking = false
            self?.refit()
        }
        return panel
    }

    /// After SwiftUI has laid out a change.
    private func refit() {
        DispatchQueue.main.async { [weak self] in self?.fit() }
    }

    /// Size to the content, keeping the bottom right corner (the character) where it is.
    private func fit() {
        guard let panel, let host = panel.contentView, !rig.dragging else { return }
        let size = host.fittingSize
        let corner = CompanionPanel.corner()
        let frame = NSRect(x: corner.x - size.width, y: corner.y, width: size.width, height: size.height)
        if !NSEqualRects(frame, panel.frame) { panel.setFrame(frame, display: true) }
    }

    /// The bottom right corner of the panel: where it was dragged to, else the
    /// bottom right of the main screen.
    static func corner(defaults: UserDefaults = .standard) -> NSPoint {
        if let stored = defaults.string(forKey: "companionCorner") {
            let parts = stored.split(separator: ",").compactMap { Double($0) }
            if parts.count == 2 {
                let point = NSPoint(x: parts[0], y: parts[1])
                let probe = NSPoint(x: point.x - 30, y: point.y + 30)
                if NSScreen.screens.contains(where: { NSMouseInRect(probe, $0.frame, false) }) { return point }
            }
        }
        let frame = (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        return NSPoint(x: frame.maxX - 12, y: frame.minY + 8)
    }

    private func rememberPlace() {
        guard let frame = panel?.frame else { return }
        UserDefaults.standard.set("\(Double(frame.maxX)),\(Double(frame.minY))", forKey: "companionCorner")
    }
}

/// What the companion's SwiftUI view shows.
final class CompanionState: ObservableObject {
    @Published var mood: CompanionMood = .sleep
    @Published var line = ""
    @Published var asking = false
    @Published var draft = ""
    @Published var status = ""
    @Published var sending = false
    @Published var size: CGFloat = CGFloat(CompanionSettings.defaultSize)
    @Published var target = ""
    @Published var focusField = 0
    var lastLine = ""
}

/// A clear panel that can take the keyboard for the ask field but never
/// activates Pinlet.app. Not on full-screen Spaces, and left out of screen sharing.
private final class CompanionWindow: NSPanel {
    var onEscape: () -> Void = {}

    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 120, height: 120),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: true)
        isFloatingPanel = true
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        hidesOnDeactivate = false
        isReleasedWhenClosed = false
        becomesKeyOnlyIfNeeded = true
        sharingType = .none
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    override func cancelOperation(_ sender: Any?) { onEscape() }
}

// -------------------------------------------------------------------- views
struct CompanionView: View {
    @ObservedObject var state: CompanionState
    let rig: CompanionRigView
    let submit: () -> Void
    let close: () -> Void

    @FocusState private var fieldFocused: Bool
    private let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)

    private var dark: Bool {
        (AppSettings.currentAppearance() ?? NSApp.effectiveAppearance).bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
    }
    private var accent: Color { Color(nsColor: NSColor(hex: Palette.accent)) }

    var body: some View {
        VStack(alignment: .trailing, spacing: 0) {
            if state.asking {
                ask
            } else if !state.line.isEmpty {
                Text(state.line)
                    .font(.system(size: CompanionView.lineFont.pointSize))
                    .frame(width: CompanionView.lineWidth(state.line), alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .modifier(CardBackground(shape: shape, dark: dark, rim: accent))
                    .padding(.trailing, state.size * 0.35)
                    .accessibilityLabel("Pinlet companion says: \(state.line)")
            }
            CompanionRigHost(rig: rig)
                .frame(width: state.size, height: state.size * CompanionArt.box.height / CompanionArt.box.width)
        }
        .padding(8)
        .onChange(of: state.focusField) { _ in fieldFocused = true }
    }

    static let lineFont = NSFont.systemFont(ofSize: 12)

    /// A short line keeps its own width; a long one wraps at 220.
    static func lineWidth(_ line: String) -> CGFloat {
        min(220, ((line as NSString).size(withAttributes: [.font: lineFont]).width + 2).rounded(.up))
    }

    private var placeholder: String {
        state.target.isEmpty ? "Tell your AI what to do…" : "Tell \(state.target) what to do…"
    }

    private var ask: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                TextField(placeholder, text: $state.draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .lineLimit(1...4)
                    .focused($fieldFocused)
                    .onSubmit(submit)
                    .disabled(state.sending)
                    .accessibilityLabel(placeholder)
                Button(action: submit) {
                    Image(systemName: "arrow.up").font(.system(size: 11, weight: .bold))
                }
                .modifier(GlassIconButton())
                .disabled(state.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || state.sending)
                .help("Send (Return)")
                .accessibilityLabel("Send")
            }
            Text(state.status.isEmpty ? "Return sends, Esc closes." : state.status)
                .font(.caption)
                .foregroundStyle(state.status.isEmpty ? HierarchicalShapeStyle.tertiary : HierarchicalShapeStyle.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(width: 260, alignment: .leading)
        .modifier(CardBackground(shape: shape, dark: dark, rim: accent))
        .padding(.trailing, state.size * 0.35)
        .onAppear { fieldFocused = true }
        .onExitCommand(perform: close)
    }
}

/// Hands the one rig view to SwiftUI.
struct CompanionRigHost: NSViewRepresentable {
    let rig: CompanionRigView

    func makeNSView(context: Context) -> CompanionRigView { rig }
    func updateNSView(_ view: CompanionRigView, context: Context) {}
}

/// The animated character. Springs from pose to pose and adds a little life
/// on top (breathing, blinks, a glance at the pointer). Draws only while
/// something moves: asleep it ticks slowly, paused or locked it stops.
final class CompanionRigView: NSView {
    var onClick: () -> Void = {}
    var onMoved: () -> Void = {}

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
        setAccessibilityHelp("Click to tell your AI something. Drag to move.")
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
        let mouse = NSEvent.mouseLocation
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
            if mood == .ask { target[.armRight] = 140 + sin(now * 6) * 14 }
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
