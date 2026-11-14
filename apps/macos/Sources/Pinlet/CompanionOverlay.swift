import AppKit

/// Rime's full-screen effects, drawn in clear click-through windows above
/// apps and below Rime: frost on the screen edges while the AI is frozen,
/// target-lock brackets on what the AI saw, and the ice tether from Rime's
/// crystal to the AI's pointer.
///
/// The windows never take a click and are left out of screenshots and
/// recordings (unless Settings > Appearance says to show them), so the AI
/// never sees them.
final class CompanionOverlay {
    struct Bracket: Equatable {
        var rect: CGRect     // global Cocoa coordinates
        var label: String
    }

    private var windows: [OverlayWindow] = []
    private var timer: Timer?
    private var screenObserver: NSObjectProtocol?

    private(set) var frosted = false
    private var brackets: [Bracket] = []
    private var bracketsUntil = Date.distantPast
    /// The tether: from Rime's crystal to the pointer (global Cocoa), springing after it.
    private var tetherFrom: CGPoint?
    private var tetherGoal: CGPoint?
    private var tetherNow: CGPoint?
    private var tetherVelocity = CGVector.zero
    private var phase: CGFloat = 0
    private var last = CACurrentMediaTime()

    init() {
        screenObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
            self?.rebuild()
        }
    }

    deinit {
        timer?.invalidate()
        if let screenObserver { NotificationCenter.default.removeObserver(screenObserver) }
    }

    private var reduceMotion: Bool { NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }

    // ------------------------------------------------------------- frost
    func setFrost(_ on: Bool) {
        guard on != frosted else { return }
        frosted = on
        ensureWindows()
        redraw()
        for w in windows {
            if reduceMotion {
                w.frostView.frostAmount = on ? 1 : 0
                w.frostView.needsDisplay = true
            } else {
                w.frostView.animateFrost(to: on ? 1 : 0)
            }
        }
        updateVisibility()
    }

    // ---------------------------------------------------------- brackets
    /// Lock-on brackets for a few seconds. `items` are global top-left rects.
    func showBrackets(_ items: [(rect: CGRect, label: String)], seconds: TimeInterval = 3.4) {
        let h = CompanionOverlay.primaryHeight
        brackets = items.map { Bracket(rect: CGRect(x: $0.rect.minX, y: h - $0.rect.maxY,
                                                    width: $0.rect.width, height: $0.rect.height),
                                       label: $0.label) }
        bracketsUntil = Date().addingTimeInterval(seconds)
        ensureWindows()
        for w in windows { w.frostView.bracketStart = CACurrentMediaTime() }
        redraw()
        updateVisibility()
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds + 0.05) { [weak self] in
            guard let self, Date() >= self.bracketsUntil else { return }
            self.brackets = []
            self.redraw()
            self.updateVisibility()
        }
    }

    // ------------------------------------------------------------ tether
    /// `from`: Rime's crystal; `to`: the agent pointer; both global Cocoa. nil hides it.
    func setTether(from: CGPoint?, to: CGPoint?) {
        tetherFrom = from
        tetherGoal = to
        if to == nil || from == nil {
            tetherNow = nil
        } else if tetherNow == nil || reduceMotion {
            tetherNow = to
        }
        ensureWindows()
        updateVisibility()
    }

    func hideAll() {
        brackets = []
        tetherFrom = nil
        tetherGoal = nil
        tetherNow = nil
        if frosted { setFrost(false) }
        updateVisibility()
    }

    // ------------------------------------------------------------ plumbing
    static var primaryHeight: CGFloat { NSScreen.screens.first?.frame.maxY ?? 900 }

    private var needsTimer: Bool { tetherNow != nil }

    private func updateVisibility() {
        let show = frosted || !brackets.isEmpty || tetherNow != nil
            || windows.contains(where: { $0.frostView.frostAmount > 0.001 })
        for w in windows {
            if show { w.orderFrontRegardless() } else { w.orderOut(nil) }
        }
        if needsTimer && timer == nil {
            last = CACurrentMediaTime()
            let t = Timer(timeInterval: 1.0 / 30, repeats: true) { [weak self] _ in self?.tick() }
            RunLoop.main.add(t, forMode: .common)
            timer = t
        } else if !needsTimer, let t = timer {
            t.invalidate()
            timer = nil
        }
        redraw()
    }

    private func tick() {
        let now = CACurrentMediaTime()
        let dt = CGFloat(min(0.07, max(0.001, now - last)))
        last = now
        if let goal = tetherGoal, var cur = tetherNow {
            // A soft spring: the thread follows the pointer like a thread would.
            tetherVelocity.dx += (goal.x - cur.x) * 120 * dt
            tetherVelocity.dy += (goal.y - cur.y) * 120 * dt
            tetherVelocity.dx *= exp(-14 * dt)
            tetherVelocity.dy *= exp(-14 * dt)
            cur.x += tetherVelocity.dx * dt
            cur.y += tetherVelocity.dy * dt
            tetherNow = cur
        }
        if !reduceMotion { phase -= 20 * dt }
        redraw()
    }

    private func redraw() {
        let tether: (CGPoint, CGPoint)? = {
            guard let a = tetherFrom, let b = tetherNow else { return nil }
            return (a, b)
        }()
        for w in windows {
            w.frostView.brackets = brackets
            w.frostView.tether = tether
            w.frostView.phase = phase
            w.frostView.needsDisplay = true
        }
    }

    private func ensureWindows() {
        if windows.count != NSScreen.screens.count { rebuild() }
    }

    private func rebuild() {
        let amount = frosted ? 1.0 : 0.0
        windows.forEach { $0.orderOut(nil) }
        windows = NSScreen.screens.map { screen in
            let w = OverlayWindow(screen: screen)
            w.frostView.frostAmount = amount
            return w
        }
        updateVisibility()
    }

    /// Follows Settings > Appearance > "Show the bar and companion in screenshots and recordings".
    func followRecordingSetting(defaults: UserDefaults = .standard) {
        let wanted: NSWindow.SharingType = defaults.bool(forKey: "barInRecordings") ? .readOnly : .none
        for w in windows where w.sharingType != wanted { w.sharingType = wanted }
    }
}

private final class OverlayWindow: NSPanel {
    let frostView: OverlayView

    init(screen: NSScreen) {
        frostView = OverlayView(frame: NSRect(origin: .zero, size: screen.frame.size))
        super.init(contentRect: screen.frame, styleMask: [.borderless, .nonactivatingPanel],
                   backing: .buffered, defer: false)
        setFrame(screen.frame, display: false)
        frostView.screenOrigin = screen.frame.origin
        contentView = frostView
        isFloatingPanel = true
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        ignoresMouseEvents = true
        hidesOnDeactivate = false
        isReleasedWhenClosed = false
        sharingType = .none
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

private final class OverlayView: NSView {
    var screenOrigin: CGPoint = .zero
    var frostAmount: CGFloat = 0
    var brackets: [CompanionOverlay.Bracket] = []
    var bracketStart: CFTimeInterval = 0
    var tether: (CGPoint, CGPoint)?
    var phase: CGFloat = 0
    private var frostTimer: Timer?

    override var isOpaque: Bool { false }

    /// Fades the frost in or out over 0.45 s.
    func animateFrost(to target: CGFloat) {
        frostTimer?.invalidate()
        let start = frostAmount, began = CACurrentMediaTime()
        let t = Timer(timeInterval: 1.0 / 60, repeats: true) { [weak self] timer in
            guard let self else { timer.invalidate(); return }
            let p = min(1, (CACurrentMediaTime() - began) / 0.45)
            self.frostAmount = start + (target - start) * CGFloat(p * p * (3 - 2 * p))
            self.needsDisplay = true
            if p >= 1 {
                timer.invalidate()
                if target == 0 { self.window?.orderOut(nil) }
            }
        }
        RunLoop.main.add(t, forMode: .common)
        frostTimer = t
    }

    private static let ice = NSColor(srgbRed: 184 / 255, green: 243 / 255, blue: 252 / 255, alpha: 1)
    private static let cyan = NSColor(srgbRed: 111 / 255, green: 224 / 255, blue: 244 / 255, alpha: 1)

    override func draw(_ dirtyRect: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        if frostAmount > 0.001 { drawFrost(ctx) }
        if !brackets.isEmpty { drawBrackets(ctx) }
        if let tether { drawTether(ctx, from: local(tether.0), to: local(tether.1)) }
    }

    private func local(_ p: CGPoint) -> CGPoint { CGPoint(x: p.x - screenOrigin.x, y: p.y - screenOrigin.y) }

    /// Frost creeping in from the edges, and a cool tint over everything.
    private func drawFrost(_ ctx: CGContext) {
        let a = frostAmount
        ctx.setFillColor(OverlayView.ice.withAlphaComponent(0.07 * a).cgColor)
        ctx.fill(bounds)
        let depth = min(bounds.width, bounds.height) * 0.12
        let space = CGColorSpaceCreateDeviceRGB()
        let colors = [OverlayView.ice.withAlphaComponent(0.55 * a).cgColor,
                      OverlayView.ice.withAlphaComponent(0).cgColor] as CFArray
        guard let g = CGGradient(colorsSpace: space, colors: colors, locations: [0, 1]) else { return }
        let b = bounds
        let edges: [(CGPoint, CGPoint, CGRect)] = [
            (CGPoint(x: b.minX, y: 0), CGPoint(x: b.minX + depth, y: 0), CGRect(x: b.minX, y: b.minY, width: depth, height: b.height)),
            (CGPoint(x: b.maxX, y: 0), CGPoint(x: b.maxX - depth, y: 0), CGRect(x: b.maxX - depth, y: b.minY, width: depth, height: b.height)),
            (CGPoint(x: 0, y: b.minY), CGPoint(x: 0, y: b.minY + depth), CGRect(x: b.minX, y: b.minY, width: b.width, height: depth)),
            (CGPoint(x: 0, y: b.maxY), CGPoint(x: 0, y: b.maxY - depth), CGRect(x: b.minX, y: b.maxY - depth, width: b.width, height: depth)),
        ]
        for (start, end, clip) in edges {
            ctx.saveGState()
            ctx.clip(to: clip)
            ctx.drawLinearGradient(g, start: start, end: end, options: [])
            ctx.restoreGState()
        }
        // A thin bright rim, like ice on glass.
        ctx.setStrokeColor(OverlayView.ice.withAlphaComponent(0.7 * a).cgColor)
        ctx.setLineWidth(2)
        ctx.stroke(b.insetBy(dx: 1, dy: 1))
        // A few crystals in the corners.
        ctx.setStrokeColor(UIColorWhite(alpha: 0.55 * a))
        ctx.setLineWidth(1.2)
        for corner in [CGPoint(x: b.minX + 26, y: b.minY + 26), CGPoint(x: b.maxX - 26, y: b.minY + 26),
                       CGPoint(x: b.minX + 26, y: b.maxY - 26), CGPoint(x: b.maxX - 26, y: b.maxY - 26)] {
            for i in 0..<6 {
                let angle = CGFloat(i) * .pi / 3
                ctx.move(to: corner)
                ctx.addLine(to: CGPoint(x: corner.x + cos(angle) * 14, y: corner.y + sin(angle) * 14))
            }
        }
        ctx.strokePath()
    }

    private func UIColorWhite(alpha: CGFloat) -> CGColor { NSColor.white.withAlphaComponent(alpha).cgColor }

    /// Corner brackets that snap onto each element, with its name under it.
    private func drawBrackets(_ ctx: CGContext) {
        let elapsed = CACurrentMediaTime() - bracketStart
        ctx.setStrokeColor(OverlayView.cyan.cgColor)
        ctx.setLineWidth(2)
        let font = NSFont.monospacedSystemFont(ofSize: 10.5, weight: .medium)
        for (i, item) in brackets.enumerated() {
            let shown = max(0, min(1, (elapsed - Double(i) * 0.06) / 0.25))
            if shown <= 0 { continue }
            let grow = CGFloat(1 - shown) * 10
            let r = CGRect(x: item.rect.minX - screenOrigin.x, y: item.rect.minY - screenOrigin.y,
                           width: item.rect.width, height: item.rect.height).insetBy(dx: -5 - grow, dy: -5 - grow)
            guard r.intersects(bounds) else { continue }
            let len = min(10, r.width / 3, r.height / 3)
            for (corner, dx, dy) in [(CGPoint(x: r.minX, y: r.minY), 1.0, 1.0), (CGPoint(x: r.maxX, y: r.minY), -1.0, 1.0),
                                     (CGPoint(x: r.minX, y: r.maxY), 1.0, -1.0), (CGPoint(x: r.maxX, y: r.maxY), -1.0, -1.0)] {
                ctx.move(to: CGPoint(x: corner.x + CGFloat(dx) * len, y: corner.y))
                ctx.addLine(to: corner)
                ctx.addLine(to: CGPoint(x: corner.x, y: corner.y + CGFloat(dy) * len))
            }
            ctx.strokePath()
            guard !item.label.isEmpty else { continue }
            let text = NSAttributedString(string: item.label, attributes: [
                .font: font, .foregroundColor: OverlayView.ice])
            let size = text.size()
            let pill = CGRect(x: r.minX, y: r.minY - size.height - 6, width: size.width + 10, height: size.height + 2)
            ctx.setFillColor(NSColor(srgbRed: 18 / 255, green: 36 / 255, blue: 52 / 255, alpha: 0.85).cgColor)
            ctx.addPath(CGPath(roundedRect: pill, cornerWidth: 4, cornerHeight: 4, transform: nil))
            ctx.fillPath()
            NSGraphicsContext.saveGraphicsState()
            text.draw(at: CGPoint(x: pill.minX + 5, y: pill.minY + 1))
            NSGraphicsContext.restoreGraphicsState()
        }
    }

    /// A faint flowing thread from Rime to the pointer.
    private func drawTether(_ ctx: CGContext, from a: CGPoint, to b: CGPoint) {
        let mid = CGPoint(x: (a.x + b.x) / 2, y: max(a.y, b.y) + 60)
        ctx.saveGState()
        ctx.setStrokeColor(OverlayView.ice.withAlphaComponent(0.75).cgColor)
        ctx.setLineWidth(1.6)
        ctx.setLineCap(.round)
        ctx.setLineDash(phase: phase, lengths: [3, 7])
        ctx.move(to: a)
        ctx.addQuadCurve(to: b, control: mid)
        ctx.strokePath()
        ctx.restoreGState()
    }
}
