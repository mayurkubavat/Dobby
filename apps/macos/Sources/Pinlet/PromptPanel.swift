import AppKit
import SwiftUI

/// Pinlet's Allow/Deny question as a native card near the top of the screen.
///
/// A non-activating panel: it takes the keyboard (Return / Esc) without
/// bringing Pinlet.app forward, so the app Pinlet works in stays the active one.
/// On macOS 26 the card is Liquid Glass (regular, since it carries text) with
/// Pinlet's dark tint; earlier systems get the regular material. It takes the
/// Look setting (dark by default, like the bar).
final class PromptPanel {
    var onAnswer: (_ promptID: Int, _ ok: Bool) -> Void = { _, _ in }

    private var panel: KeyPanel?
    private(set) var shown: Prompt?
    private var deadline = Date()

    /// Show `prompt` (or keep showing it), or hide the card when `nil`.
    func update(_ prompt: Prompt?, palette: [String: String]) {
        guard let prompt else { hide(); return }
        if let shown, shown.sameQuestion(as: prompt) { return }
        shown = prompt
        deadline = Date().addingTimeInterval(max(1, prompt.secsLeft))
        present(prompt, palette: palette)
    }

    /// Bring the card back to the front (a click on its notification).
    func focus() {
        guard let panel, panel.isVisible else { return }
        panel.makeKeyAndOrderFront(nil)
    }

    func hide() {
        shown = nil
        panel?.orderOut(nil)
    }

    private func present(_ prompt: Prompt, palette: [String: String]) {
        let appearance = AppSettings.currentAppearance()
        let dark = (appearance ?? NSApp.effectiveAppearance).bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        let card = PromptCard(prompt: prompt, deadline: deadline,
                              accent: Color(nsColor: Palette.color("fill", in: palette, fallback: Palette.fill)),
                              caution: Color(nsColor: Palette.color("caution", in: palette, fallback: Palette.caution)),
                              danger: Color(nsColor: Palette.color("danger", in: palette, fallback: Palette.danger)),
                              dark: dark) {
            [weak self] ok in
            guard let self, let shown = self.shown, shown.id == prompt.id else { return }
            self.hide()
            self.onAnswer(prompt.id, ok)
        }
        let host = NSHostingView(rootView: card)
        let size = host.fittingSize
        let panel = self.panel ?? KeyPanel()
        panel.appearance = appearance
        host.appearance = appearance
        panel.contentView = host
        let screen = NSScreen.screens.first(where: { NSMouseInRect(NSEvent.mouseLocation, $0.frame, false) })
            ?? NSScreen.main ?? NSScreen.screens.first
        if let frame = screen?.visibleFrame {
            panel.setFrame(NSRect(x: frame.midX - size.width / 2, y: frame.maxY - size.height - 10,
                                  width: size.width, height: size.height), display: true)
        }
        self.panel = panel
        panel.alphaValue = 0
        panel.makeKeyAndOrderFront(nil)
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? 0 : 0.18
            panel.animator().alphaValue = 1
        }
        NSAccessibility.post(element: panel, notification: .announcementRequested,
                             userInfo: [.announcement: "Pinlet asks: \(prompt.text)", .priority: NSAccessibilityPriorityLevel.high.rawValue])
    }
}

/// Borderless panels refuse key status by default; this one needs Return / Esc.
private final class KeyPanel: NSPanel {
    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 420, height: 120),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: true)
        isFloatingPanel = true
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        hidesOnDeactivate = false
        isMovableByWindowBackground = true
        isReleasedWhenClosed = false
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

struct PromptCard: View {
    let prompt: Prompt
    let deadline: Date
    let accent: Color
    var caution: Color = .orange
    var danger: Color = .red
    var dark: Bool = true
    let answer: (Bool) -> Void

    private let shape = RoundedRectangle(cornerRadius: 18, style: .continuous)

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: prompt.isAccess ? "hand.raised.fill" : "exclamationmark.triangle.fill")
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(prompt.isAccess ? accent : caution)
                .frame(width: 28)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                Text(prompt.name.isEmpty ? "Pinlet" : prompt.name)
                    .font(.caption).foregroundStyle(.secondary)
                Text(prompt.text)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                if !prompt.detail.isEmpty {
                    Text(prompt.detail)
                        .font(.subheadline).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                HStack(spacing: 8) {
                    Countdown(deadline: deadline)
                    Spacer(minLength: 12)
                    Button(prompt.deny) { answer(false) }
                        .keyboardShortcut(.cancelAction)
                        .controlSize(.large)
                    // A risky step never goes through on a stray Return: it takes Cmd+Return.
                    Button(prompt.allow) { answer(true) }
                        .keyboardShortcut(prompt.isAccess ? .defaultAction : KeyboardShortcut(.return, modifiers: .command))
                        .buttonStyle(.borderedProminent)
                        .tint(prompt.isAccess ? accent : danger)
                        .controlSize(.large)
                }
                .padding(.top, 4)
            }
        }
        .padding(16)
        .frame(width: 420, alignment: .leading)
        .modifier(CardBackground(shape: shape, dark: dark, rim: prompt.isAccess ? accent : danger))
        .padding(10)  // room for the shadow
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Pinlet asks: \(prompt.text)")
    }
}

/// "Denies in 0:24", ticking.
private struct Countdown: View {
    let deadline: Date

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let left = max(0, Int(deadline.timeIntervalSince(context.date).rounded(.up)))
            Text("Denies in \(left / 60):\(String(format: "%02d", left % 60))")
                .font(.caption).monospacedDigit().foregroundStyle(.tertiary)
        }
    }
}

/// Liquid Glass on macOS 26 (built with the macOS 26 SDK), the regular material before.
///
/// The glass carries the same palette tint as the bar's regular glass (dark
/// `#141A22` at 34%, light `#F7FAFC` at 18%) and a thin rim in the card's colour.
struct CardBackground: ViewModifier {
    let shape: RoundedRectangle
    var dark: Bool = true
    var rim: Color = .clear

    private var tint: Color {
        dark ? Color(red: 20 / 255, green: 26 / 255, blue: 34 / 255).opacity(0.34)
             : Color(red: 247 / 255, green: 250 / 255, blue: 252 / 255).opacity(0.18)
    }

    @ViewBuilder
    func body(content: Content) -> some View {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            content.glassEffect(.regular.tint(tint), in: shape)
                .overlay(shape.strokeBorder(rim.opacity(dark ? 0.35 : 0.28), lineWidth: 1))
        } else {
            content.background(.regularMaterial, in: shape)
                .overlay(shape.strokeBorder(Color.primary.opacity(0.08)))
        }
        #else
        content.background(.regularMaterial, in: shape)
            .overlay(shape.strokeBorder(Color.primary.opacity(0.08)))
        #endif
    }
}
