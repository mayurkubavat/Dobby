import AppKit
import SwiftUI

/// The companion: Rime, a small character in a corner of the screen.
///
/// With "Show progress with: Rime" (the default once the companion is on),
/// Rime stands in for the floating bar:
///
/// * its thought cloud shows the AI's goal, why it takes the step, the step
///   and the timer, in a bubble whose shape says the state (cloud thinking,
///   round saying, jagged trouble, icy frozen);
/// * the first click on Rime freezes the AI at once, frost creeps in from the
///   screen edges, and a ring of buttons fans out: Resume, Cancel task (hold),
///   One step, Tell, Show what it sees, Quieter cloud;
/// * it steps aside when the AI needs to click where it sits, and points at
///   the approval card. Allow and Deny stay on the card: nothing on Rime
///   approves anything.
///
/// It sends the engine a heartbeat four times a second. The floating bar
/// hides only while that heartbeat is fresh, so if Rime stops (hidden, the
/// app hangs or quits) the bar and its Pause come back by themselves.
final class CompanionPanel {
    /// (pid, text) → sends the note (control op "tell"); calls back with the reply or nil.
    var onTell: (_ pid: Int, _ text: String, _ done: @escaping ([String: Any]?) -> Void) -> Void = { _, _, done in done(nil) }
    /// A menu action for one AI: pause, resume, step, stop.
    var onAction: (_ key: String, _ pid: Int) -> Void = { _, _ in }
    /// The heartbeat (control op "companion"); the reply carries `avoid` and `stage`.
    var onBeat: (_ fields: [String: Any], _ done: @escaping ([String: Any]?) -> Void) -> Void = { _, done in done(nil) }
    /// What the AI found on its last look (control op "seen").
    var onSeen: (_ done: @escaping ([[String: Any]]) -> Void) -> Void = { done in done([]) }
    /// The approval card's frame while one is showing.
    var cardFrame: () -> NSRect? = { nil }

    private let state = CompanionState()
    private let rig = CompanionRigView()
    private let overlay = CompanionOverlay()
    private let voice = CompanionVoice()
    private var tracker = CompanionTracker()
    private var panel: CompanionWindow?
    private var view = TopbarView.empty
    private var prompts: [Prompt] = []
    private var reachable = false
    private var locked = false
    private var observers: [NSObjectProtocol] = []
    private var stage = CompanionStage.empty
    private var freeze = CompanionFreeze()
    private var log = CompanionLog()
    private var shake = ShakeDetector()
    private var mouseMonitor: Any?
    private var beatTimer: Timer?
    private var beating = false
    /// Rime froze the AI and the poll has not shown it paused yet.
    private var freezingUntil = Date.distantPast
    private var napping = false
    /// Out of the AI's way: where Rime's bottom-right corner is until it goes home.
    private var dodgeCorner: NSPoint?
    private var lastAvoid = Date.distantPast
    private var flashToken = 0

    static let flashSeconds: TimeInterval = 3.2
    static let beatSeconds: TimeInterval = 0.25

    var isShown: Bool { panel?.isVisible == true }

    init() {
        rig.onClick = { [weak self] in self?.clicked() }
        rig.onMoved = { [weak self] in self?.dropped() }
        voice.onPause = { [weak self] in self?.freezeNow(spoken: true) }
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

    // ------------------------------------------------------------- what is up
    private var rows: [TopbarView.Row] { view.rows }
    private var replacesBar: Bool { CompanionSettings.replacesBar() }
    /// An AI is on a task: working, asking, or paused in the middle of one.
    private var hasTask: Bool {
        rows.contains { ["working", "awake", "confirm", "paused", "takeover"].contains($0.state) }
    }
    private var working: Bool { rows.contains { $0.state == "working" || $0.state == "awake" } }
    private var frozen: Bool {
        Date() < freezingUntil || rows.contains { $0.state == "paused" || $0.state == "takeover" }
    }
    /// The AI Rime speaks for.
    private var speaking: TopbarView.Row? {
        ActivityModel.defaultSelection(rows).flatMap { pid in rows.first(where: { $0.pid == pid }) }
    }

    /// New content from the shared Pinlet (every poll). Also follows the Settings switches.
    func update(view: TopbarView, prompts: [Prompt], reachable: Bool, stage: CompanionStage? = nil) {
        self.view = view
        self.prompts = prompts
        self.reachable = reachable
        if let stage { self.stage = stage }
        guard CompanionSettings.enabled() else {
            if isShown { hide() }
            return
        }
        freeze.prune(view.rows)
        if Date() < freezingUntil, rows.contains(where: { $0.state == "paused" }) { freezingUntil = .distantPast }
        if napping && hasTask { napping = false }
        let size = CGFloat(CompanionSettings.size())
        if state.size != size { state.size = size }
        let target = speaking?.firstName ?? ""
        if state.target != target { state.target = target }
        if !isShown { show() }
        panel?.followRecordingSetting()
        overlay.followRecordingSetting()
        panel?.duringTask(hasTask)
        updateOrbs()
        voice.want(CompanionVoice.enabled() && working)
        startMouseMonitor(CompanionSettings.summon())
        refresh()
    }

    private func currentBubble(mood: CompanionMood) -> CompanionBubble? {
        CompanionBubble.make(mood: mood, stage: stage, name: speaking?.firstName ?? "", row: speaking,
                             prompt: prompts.first(where: { !$0.missed }), quiet: state.quiet, log: log.last)
    }

    /// Mood, bubble, frost, tether, ring: everything that follows the state.
    private func refresh() {
        var mood = tracker.update(view: view, prompts: prompts, reachable: reachable, locked: locked)
        if Date() < freezingUntil && mood != .lock { mood = .pause }
        if napping && !hasTask && mood != .lock { mood = .sleep }
        if state.mood != mood {
            state.mood = mood
            rig.mood = mood
            if mood == .lock {
                state.asking = false
                closeRing()
            }
        }
        rig.setAccessibilityLabel(frozen ? "Rime. The AI is frozen. Click for options."
                                  : working ? "Rime. Click to freeze \(speaking?.firstName ?? "the AI")."
                                  : "Pinlet companion, \(mood.spoken)")
        rig.risky = stage.risky && mood == .act
        rig.lookAt = mood == .ask ? cardFrame().map { NSPoint(x: $0.midX, y: $0.midY) } : nil
        log.observe(stage: stage, working: working || frozen, name: speaking?.firstName ?? "")
        state.lastTask = log.last
        let bubble = currentBubble(mood: mood)
        if state.bubble != bubble { state.bubble = bubble }
        // Frost only for a real pause, not while the person simply moves the mouse (take-over).
        let paused = Date() < freezingUntil || rows.contains { $0.state == "paused" }
        if paused != overlay.frosted { overlay.setFrost(paused && replacesBar) }
        if state.ringOpen { refreshRing() }
        updateTether(mood: mood)
        refit()
    }

    private func updateTether(mood: CompanionMood) {
        guard CompanionSettings.tether(), replacesBar, mood == .act || mood == .think,
              let pointer = stage.pointer, let panel, isShown else {
            overlay.setTether(from: nil, to: nil)
            return
        }
        let h = CompanionOverlay.primaryHeight
        let rigScreen = panel.convertToScreen(rig.convert(rig.bounds, to: nil))
        let crystal = NSPoint(x: rigScreen.midX, y: rigScreen.maxY - rigScreen.height * 0.18)
        overlay.setTether(from: crystal, to: CompanionDodge.cocoa(pointer, primaryHeight: h))
    }

    private func updateOrbs() {
        let speakingPid = speaking?.pid
        let others = rows.filter { $0.pid != speakingPid && $0.state != "stopped" && $0.state != "ready" }
        let orbs = others.prefix(5).map { row in
            CompanionOrb(pid: row.pid, initial: String(row.firstName.prefix(1)).uppercased(), name: row.firstName,
                         color: row.color, paused: row.state == "paused",
                         waiting: row.state == "waiting" || row.state == "listening")
        }
        if state.orbs != Array(orbs) { state.orbs = Array(orbs) }
    }

    private func flash(_ text: String) {
        state.flash = text
        flashToken += 1
        let token = flashToken
        refit()
        DispatchQueue.main.asyncAfter(deadline: .now() + CompanionPanel.flashSeconds) { [weak self] in
            guard let self, self.flashToken == token else { return }
            self.state.flash = ""
            self.refit()
        }
    }

    private func setLocked(_ value: Bool) {
        locked = value
        guard isShown else { return }
        refresh()
    }

    // ------------------------------------------------------------ heartbeat
    private func startBeat() {
        guard beatTimer == nil else { return }
        let t = Timer(timeInterval: CompanionPanel.beatSeconds, repeats: true) { [weak self] _ in self?.beat() }
        t.tolerance = 0.05
        RunLoop.main.add(t, forMode: .common)
        beatTimer = t
        beat()
    }

    private func stopBeat() {
        beatTimer?.invalidate()
        beatTimer = nil
    }

    private func beat() {
        guard !beating, let panel, isShown else { return }
        beating = true
        let h = CompanionOverlay.primaryHeight
        let fields: [String: Any] = [
            "visible": panel.isVisible && panel.isOnActiveSpace,
            "frame": CompanionDodge.topLeft(panel.frame, primaryHeight: h),
            "bar": replacesBar,
        ]
        onBeat(fields) { [weak self] reply in
            guard let self else { return }
            self.beating = false
            guard let reply, reply["ok"] as? Bool == true else { return }
            let next = CompanionStage(json: reply["stage"] as? [String: Any])
            let changed = next != self.stage
            self.stage = next
            if let p = reply["avoid"] as? [Any], p.count == 2,
               let x = (p[0] as? NSNumber)?.doubleValue, let y = (p[1] as? NSNumber)?.doubleValue {
                self.stepAside(from: CompanionDodge.cocoa(CGPoint(x: x, y: y), primaryHeight: h))
            } else if self.dodgeCorner != nil, Date().timeIntervalSince(self.lastAvoid) > 1.2 {
                self.goHome()
            }
            if changed { self.refresh() } else { self.refreshClock() }
        }
    }

    /// The timer in the bubble counts on between stage changes.
    private func refreshClock() {
        guard let bubble = state.bubble, !bubble.meta.isEmpty else { return }
        let next = currentBubble(mood: state.mood)
        if next != bubble { state.bubble = next }
        updateTether(mood: state.mood)
    }

    // ------------------------------------------------------------- step aside
    /// Flash step: blink out of the AI's way and reappear above (or below) the spot.
    private func stepAside(from point: CGPoint) {
        lastAvoid = Date()
        guard let panel, dodgeCorner == nil else { return }
        let screen = panel.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? .zero
        let origin = CompanionDodge.origin(frame: panel.frame, avoid: point, screen: screen)
        dodgeCorner = NSPoint(x: origin.x + panel.frame.width, y: origin.y)
        blinkTo(origin)
    }

    private func goHome() {
        dodgeCorner = nil
        guard let panel else { return }
        let size = panel.frame.size
        let corner = anchor(size: size)
        blinkTo(NSPoint(x: corner.x - size.width, y: corner.y))
    }

    private func blinkTo(_ origin: NSPoint) {
        guard let panel else { return }
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            panel.setFrameOrigin(origin)
            return
        }
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.08
            panel.animator().alphaValue = 0
        }, completionHandler: {
            panel.setFrameOrigin(origin)
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.12
                panel.animator().alphaValue = 1
            }
        })
    }

    // ------------------------------------------------------------- the click
    private func clicked() {
        if state.logOpen {
            state.logOpen = false
            refit()
            return
        }
        if napping && !hasTask {
            napping = false
            refresh()
            return
        }
        // The first click always freezes an AI at work. No menu to aim at first.
        if working && !frozen {
            freezeNow()
            return
        }
        if state.ringOpen { closeRing() } else { openRing() }
    }

    /// Freeze every AI at work before its next step (Rime's click, ⌃⌥P or "Rime, wait").
    func freezeNow(spoken: Bool = false) {
        let pids = CompanionFreeze.targets(rows)
        guard !pids.isEmpty else {
            if isShown && !spoken { openRing() }
            return
        }
        for pid in pids { onAction("pause", pid) }
        freeze.froze(pids)
        freezingUntil = Date().addingTimeInterval(2.5)
        state.asking = false
        refresh()
        guard isShown else { return }
        openRing()
        if spoken { flash("Frozen. I heard you.") }
    }

    private func openRing() {
        guard isShown, state.mood != .lock else { return }
        state.ringOpen = true
        state.asking = false
        state.caption = nil
        refreshRing()
        panel?.ignoresMouseEvents = false
        panel?.makeKey()
        refit()
    }

    private func closeRing() {
        guard state.ringOpen else { return }
        state.ringOpen = false
        state.caption = nil
        refit()
    }

    private func refreshRing() {
        let items = RingItem.items(mood: state.mood, frozen: frozen, hasTask: hasTask, quiet: state.quiet,
                                   hasLog: log.last != nil, napping: napping)
        if state.ring != items { state.ring = items }
    }

    private func perform(_ item: RingItem) {
        switch item {
        case .resume:
            var pids = freeze.thaw(rows)
            if pids.isEmpty { pids = rows.filter { $0.state == "paused" }.map(\.pid) }
            for pid in pids { onAction("resume", pid) }
            freezingUntil = .distantPast
            closeRing()
            overlay.setFrost(false)
            flash(pids.isEmpty ? "Nothing was frozen." : "Thawed. Carrying on.")
        case .cancel:
            var pids = Set(freeze.pids)
            pids.formUnion(rows.filter { ["working", "awake", "confirm", "paused", "takeover"].contains($0.state) }.map(\.pid))
            for pid in pids.sorted() { onAction("stop", pid) }
            _ = freeze.thaw([])
            freezingUntil = .distantPast
            closeRing()
            overlay.setFrost(false)
            flash(pids.isEmpty ? "No task to cancel." : "Cancelled. \(speaking?.firstName ?? "The AI") won't touch anything else.")
        case .step:
            guard let pid = rows.first(where: { $0.state == "paused" })?.pid ?? freeze.pids.first else {
                flash("Nothing is frozen right now.")
                return
            }
            onAction("step", pid)
            closeRing()
            flash("One step, then I freeze again.")
        case .tell:
            closeRing()
            toggleAsk()
        case .view:
            onSeen { [weak self] items in self?.showSeen(items) }
        case .quiet, .loud:
            state.quiet = item == .quiet
            UserDefaults.standard.set(state.quiet, forKey: "companionQuiet")
            refresh()
        case .log:
            closeRing()
            state.logOpen = log.last != nil
            refit()
        case .nap:
            napping = true
            closeRing()
            refresh()
        case .wake:
            napping = false
            closeRing()
            refresh()
        }
    }

    private func showSeen(_ items: [[String: Any]]) {
        let found: [(rect: CGRect, label: String)] = items.compactMap { item in
            guard let r = item["rect"] as? [Any], r.count == 4 else { return nil }
            let v = r.compactMap { ($0 as? NSNumber)?.doubleValue }
            guard v.count == 4 else { return nil }
            let words = [item["role"] as? String ?? "", item["label"] as? String ?? ""].filter { !$0.isEmpty }
            return (CGRect(x: v[0], y: v[1], width: v[2], height: v[3]), words.joined(separator: " · "))
        }
        if found.isEmpty {
            flash("Nothing to show yet: the AI hasn't looked at the screen lately.")
        } else {
            overlay.showBrackets(found)
        }
    }

    private func toggleOrb(_ orb: CompanionOrb) {
        onAction(orb.paused ? "resume" : "pause", orb.pid)
        let main = speaking?.firstName ?? "The others"
        flash(orb.paused ? "\(orb.name) is back in line." : "\(orb.name) paused. \(main) keeps going.")
    }

    // ------------------------------------------------------------------ ask
    /// Tell the AI something (ring's Tell, or Control-Option-Space).
    func toggleAsk() {
        guard isShown, state.mood != .lock else { return }
        state.asking.toggle()
        state.status = ""
        if state.asking {
            closeRing()
            panel?.ignoresMouseEvents = false
            panel?.makeKey()
            state.focusField += 1
        }
        refit()
    }

    private func submit() {
        let text = state.draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !state.sending else { return }
        guard let pid = ActivityModel.defaultSelection(rows) else {
            state.status = "No AI is connected. Ask your AI app to use Pinlet first."
            refit()
            return
        }
        let name = rows.first(where: { $0.pid == pid })?.firstName ?? "Pinlet"
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
            self.flash(woke ? "Woke \(name) with your note." : "Sent. \(name) reads it before its next step.")
        }
    }

    private func closeAll() {
        state.asking = false
        state.logOpen = false
        closeRing()
        refit()
    }

    // ---------------------------------------------------------- summon, peek
    private func startMouseMonitor(_ on: Bool) {
        if on, mouseMonitor == nil {
            mouseMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.mouseMoved]) { [weak self] event in
                guard let self else { return }
                if self.shake.feed(x: NSEvent.mouseLocation.x, at: event.timestamp) { self.summon() }
            }
        } else if !on, let monitor = mouseMonitor {
            NSEvent.removeMonitor(monitor)
            mouseMonitor = nil
        }
    }

    /// Shake the pointer: Rime hops over beside it.
    private func summon() {
        guard let panel, isShown, !rig.dragging else { return }
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first(where: { NSMouseInRect(mouse, $0.frame, false) })?.visibleFrame
            ?? panel.screen?.visibleFrame ?? .zero
        let size = panel.frame.size
        var corner = NSPoint(x: mouse.x + 40 + size.width * 0.3, y: mouse.y - 30 - size.height * 0.5)
        corner.x = min(max(corner.x, screen.minX + size.width + 4), screen.maxX - 4)
        corner.y = min(max(corner.y, screen.minY + 4), screen.maxY - size.height - 4)
        dodgeCorner = nil
        save(corner: corner)
        blinkTo(NSPoint(x: corner.x - size.width, y: corner.y))
        if CompanionSettings.speaks() && state.bubble == nil { flash("Here!") }
    }

    /// The person dropped Rime after a drag: keep it reachable, maybe tuck it behind an edge.
    private func dropped() {
        guard let panel else { return }
        dodgeCorner = nil
        let screen = panel.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? .zero
        var frame = panel.frame
        if hasTask || !CompanionSettings.peek() {
            frame.origin = CompanionPeek.out(frame: frame, screen: screen)  // never off screen mid-task
        } else if let tucked = CompanionPeek.tucked(frame: frame, screen: screen) {
            frame.origin = tucked
        }
        if frame.origin != panel.frame.origin { panel.setFrameOrigin(frame.origin) }
        save(corner: NSPoint(x: frame.maxX, y: frame.minY))
    }

    private func save(corner: NSPoint) {
        UserDefaults.standard.set("\(Double(corner.x)),\(Double(corner.y))", forKey: "companionCorner")
    }

    // --------------------------------------------------------------- window
    private func show() {
        let panel = self.panel ?? makePanel()
        self.panel = panel
        let appearance = AppSettings.currentAppearance()
        panel.appearance = appearance
        panel.contentView?.appearance = appearance
        state.quiet = CompanionSettings.quiet()
        fit()
        panel.orderFrontRegardless()
        rig.running = true
        startBeat()
    }

    func hide() {
        stopBeat()  // no heartbeat: the bar comes back
        rig.running = false
        state.asking = false
        state.ringOpen = false
        overlay.hideAll()
        voice.want(false)
        startMouseMonitor(false)
        panel?.orderOut(nil)
    }

    private func makePanel() -> CompanionWindow {
        let content = CompanionView(state: state, rig: rig,
                                    submit: { [weak self] in self?.submit() },
                                    close: { [weak self] in self?.closeAll() },
                                    perform: { [weak self] item in self?.perform(item) },
                                    toggleOrb: { [weak self] orb in self?.toggleOrb(orb) },
                                    openLog: { [weak self] in
                                        self?.state.logOpen = true
                                        self?.refit()
                                    })
        let panel = CompanionWindow()
        panel.contentView = NSHostingView(rootView: content)
        panel.onEscape = { [weak self] in self?.closeAll() }
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
        let corner = anchor(size: size)
        let frame = NSRect(x: corner.x - size.width, y: corner.y, width: size.width, height: size.height)
        if !NSEqualRects(frame, panel.frame) { panel.setFrame(frame, display: true) }
    }

    /// Where Rime's bottom-right corner goes: out of the AI's way, else its own
    /// corner, pulled fully on screen unless it is tucked away while idle.
    private func anchor(size: NSSize) -> NSPoint {
        if let dodgeCorner { return dodgeCorner }
        var corner = CompanionPanel.corner()
        let mayTuck = !hasTask && CompanionSettings.peek() && !state.ringOpen && !state.asking
            && state.bubble == nil && state.flash.isEmpty && !state.logOpen
        if !mayTuck {
            let probe = NSPoint(x: corner.x - 20, y: corner.y + 20)
            let screen = NSScreen.screens.first(where: { NSMouseInRect(probe, $0.frame, false) })?.visibleFrame
                ?? NSScreen.main?.visibleFrame ?? .zero
            corner.x = min(max(corner.x, screen.minX + size.width + 4), screen.maxX - 4)
            corner.y = min(max(corner.y, screen.minY + 4), max(screen.minY + 4, screen.maxY - size.height - 4))
        }
        return corner
    }

    /// The bottom right corner of the panel: where it was dragged to, else the
    /// bottom right of the main screen.
    static func corner(defaults: UserDefaults = .standard) -> NSPoint {
        if let stored = defaults.string(forKey: "companionCorner") {
            let parts = stored.split(separator: ",").compactMap { Double($0) }
            if parts.count == 2 {
                let point = NSPoint(x: parts[0], y: parts[1])
                // Two probes, so a corner tucked behind either edge still counts as on a screen.
                let probes = [NSPoint(x: point.x - 30, y: point.y + 30), NSPoint(x: point.x - 80, y: point.y + 30)]
                if NSScreen.screens.contains(where: { s in probes.contains { NSMouseInRect($0, s.frame, false) } }) {
                    return point
                }
            }
        }
        let frame = (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        return NSPoint(x: frame.maxX - 12, y: frame.minY + 8)
    }
}

/// What the companion's SwiftUI view shows.
final class CompanionState: ObservableObject {
    @Published var mood: CompanionMood = .sleep
    @Published var bubble: CompanionBubble?
    @Published var flash = ""
    @Published var asking = false
    @Published var draft = ""
    @Published var status = ""
    @Published var sending = false
    @Published var size: CGFloat = CGFloat(CompanionSettings.defaultSize)
    @Published var target = ""
    @Published var focusField = 0
    @Published var ringOpen = false
    @Published var ring: [RingItem] = []
    @Published var caption: RingItem?
    @Published var quiet = false
    @Published var orbs: [CompanionOrb] = []
    @Published var logOpen = false
    @Published var lastTask: CompanionLog.Task?
}

/// A clear panel that can take the keyboard for the ask field and the ring
/// but never activates Pinlet.app. Joins full-screen Spaces while an AI is on
/// a task, so Rime (and its freeze) is always within reach.
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

    /// On every Space, full-screen ones too, while an AI is on a task.
    func duringTask(_ on: Bool) {
        var wanted: NSWindow.CollectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        if on { wanted.insert(.fullScreenAuxiliary) }
        if collectionBehavior != wanted { collectionBehavior = wanted }
    }

    /// Left out of screenshots, recordings and screen sharing unless Settings >
    /// Appearance says to show the bar and companion there.
    func followRecordingSetting(defaults: UserDefaults = .standard) {
        let wanted: NSWindow.SharingType = defaults.bool(forKey: "barInRecordings") ? .readOnly : .none
        if sharingType != wanted { sharingType = wanted }
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
    let perform: (RingItem) -> Void
    let toggleOrb: (CompanionOrb) -> Void
    let openLog: () -> Void

    @FocusState private var fieldFocused: Bool
    private let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)

    private var dark: Bool {
        (AppSettings.currentAppearance() ?? NSApp.effectiveAppearance).bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
    }
    private var accent: Color { Color(nsColor: NSColor(hex: Palette.accent)) }
    private var rigSize: CGSize {
        CGSize(width: state.size, height: state.size * CompanionArt.box.height / CompanionArt.box.width)
    }
    private var ringRadius: CGFloat { max(128, state.size + 44) }

    var body: some View {
        VStack(alignment: .trailing, spacing: 6) {
            top
            ZStack(alignment: .bottomTrailing) {
                if state.ringOpen {
                    CompanionRingView(items: state.ring, rigSize: rigSize, radius: ringRadius,
                                      perform: perform, caption: $state.caption)
                }
                HStack(alignment: .bottom, spacing: 4) {
                    if !state.orbs.isEmpty && !state.ringOpen {
                        CompanionOrbsView(orbs: state.orbs, toggle: toggleOrb)
                            .padding(.bottom, rigSize.height * 0.25)
                    }
                    CompanionRigHost(rig: rig)
                        .frame(width: rigSize.width, height: rigSize.height)
                }
            }
        }
        .padding(8)
        .onChange(of: state.focusField) { _ in fieldFocused = true }
        .onExitCommand(perform: close)
    }

    @ViewBuilder private var top: some View {
        if state.logOpen, let task = state.lastTask {
            CompanionLogView(task: task, close: close).padding(.trailing, state.size * 0.3)
        } else if state.asking {
            ask
        } else if state.ringOpen {
            RingCaption(item: state.caption, napping: false).padding(.trailing, state.size * 0.3)
        } else if !state.flash.isEmpty {
            CompanionBubbleView(bubble: CompanionBubble(kind: .speech, who: "Rime", thought: state.flash),
                                dark: dark, openLog: openLog)
                .padding(.trailing, state.size * 0.3)
        } else if let bubble = state.bubble {
            CompanionBubbleView(bubble: bubble, dark: dark, openLog: openLog)
                .padding(.trailing, state.size * 0.3)
        }
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
    }
}

/// Hands the one rig view to SwiftUI.
struct CompanionRigHost: NSViewRepresentable {
    let rig: CompanionRigView

    func makeNSView(context: Context) -> CompanionRigView { rig }
    func updateNSView(_ view: CompanionRigView, context: Context) {}
}
