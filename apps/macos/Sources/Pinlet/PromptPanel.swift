import AppKit
import SwiftUI

/// Pinlet's Allow/Deny question as a native card near the top of the screen.
///
/// A non-activating panel that does not take the keyboard when it appears: a
/// Return the person was typing elsewhere (or a key an AI was about to press)
/// must never answer it. A click on the card gives it the keyboard; then Esc
/// denies and Return / Cmd+Return allow. Allow is also held back for a moment
/// after the card appears, and input that Pinlet itself injected is dropped.
/// On macOS 26 the card is Liquid Glass (regular, since it carries text) with
/// Pinlet's dark tint; earlier systems get the regular material. It takes the
/// Look setting (dark by default, like the bar).
final class PromptPanel {
    var onAnswer: (_ promptID: Int, _ answer: PromptAnswer) -> Void = { _, _ in }

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
                              dark: dark,
                              resized: { [weak self] in self?.fitToContent() }) {
            [weak self] answer in
            guard let self, let shown = self.shown, shown.id == prompt.id else { return }
            self.hide()
            self.onAnswer(prompt.id, answer)
        }
        let host = FirstClickHost(rootView: card)
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
        panel.orderFrontRegardless()  // in front, but the keyboard stays where it was
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? 0 : 0.18
            panel.animator().alphaValue = 1
        }
        NSAccessibility.post(element: panel, notification: .announcementRequested,
                             userInfo: [.announcement: "Pinlet asks: \(prompt.text)", .priority: NSAccessibilityPriorityLevel.high.rawValue])
    }
}

extension PromptPanel {
    /// The card grew (the reason field opened): resize the panel, keeping its top edge.
    fileprivate func fitToContent() {
        guard let panel, let host = panel.contentView else { return }
        let size = host.fittingSize
        var frame = panel.frame
        frame.origin.y += frame.height - size.height
        frame.size = size
        panel.setFrame(frame, display: true, animate: false)
    }
}

/// The card is not the key window when it appears, and Pinlet.app is not the
/// active app: without this the first click would only focus the card.
private final class FirstClickHost<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// Borderless panels refuse key status by default; this one needs Return / Esc.
private final class KeyPanel: NSPanel {
    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 440, height: 120),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: true)
        isFloatingPanel = true
        // Above Pinlet's own drawing layer (screen saver level), whose glow around the
        // window being worked in would otherwise run across the card's text.
        level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.screenSaverWindow)) + 1)
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

    /// The mark Pinlet's engine puts on every mouse and key event it injects
    /// (`PINLET_EVENT_TAG` in pinlet/hal/macos.py).
    static let injectedTag: Int64 = 0x444F_4242

    /// Clicks and keys an AI sent through Pinlet never reach the card.
    override func sendEvent(_ event: NSEvent) {
        switch event.type {
        case .leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp,
             .keyDown, .keyUp:
            if let cg = event.cgEvent {
                if cg.getIntegerValueField(.eventSourceUserData) == KeyPanel.injectedTag {
                    NSLog("Pinlet: ignored injected input on the Allow/Deny card")
                    return
                }
                if event.type == .leftMouseDown || event.type == .keyDown {
                    // 0 is the keyboard or mouse itself; anything else is a process that posted the event.
                    let source = cg.getIntegerValueField(.eventSourceUnixProcessID)
                    if source != 0 { NSLog("Pinlet: card input posted by process %lld", source) }
                }
            }
        default: break
        }
        super.sendEvent(event)
    }
}

struct PromptCard: View {
    let prompt: Prompt
    let deadline: Date
    let accent: Color
    var caution: Color = .orange
    var danger: Color = .red
    var dark: Bool = true
    var resized: () -> Void = {}
    let answer: (PromptAnswer) -> Void
    @State private var always = false
    /// A risky step: "Don't ask again", and for how long.
    @State private var remember = false
    @State private var scope = "task"
    /// "Tell the AI why": the person's words go back to the AI with the Deny.
    @State private var explaining = false
    @State private var reason = ""
    @FocusState private var reasonFocused: Bool
    /// Allow wakes up a moment after the card appears, so a click or key already on its way cannot land on it.
    @State private var armed = false

    private let shape = RoundedRectangle(cornerRadius: 18, style: .continuous)

    /// The card can offer "Don't ask again" for this question.
    private var canRemember: Bool { !prompt.isAccess && !prompt.action.isEmpty }
    private var needsTouchID: Bool { (prompt.isAccess && always) || (remember && scope == "always") }
    private var target: NSImage? {
        guard !prompt.image.isEmpty, let data = Data(base64Encoded: prompt.image) else { return nil }
        return NSImage(data: data)
    }

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
                if let target {
                    // The control itself, so the person sees what is about to be pressed.
                    Image(nsImage: target)
                        .resizable().interpolation(.high).aspectRatio(contentMode: .fit)
                        .frame(maxWidth: .infinity, maxHeight: 84, alignment: .leading)
                        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .strokeBorder(Color.primary.opacity(0.12)))
                        .padding(.vertical, 2)
                        .accessibilityLabel("Picture of what Pinlet is about to press")
                }
                if !prompt.detail.isEmpty {
                    Text(prompt.detail)
                        .font(.subheadline).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if prompt.isAccess && !prompt.app.isEmpty && !explaining {
                    Toggle("Always allow in \(prompt.app)", isOn: $always)
                        .toggleStyle(.checkbox)
                        .font(.subheadline)
                        .onChange(of: always) { _ in grow() }
                }
                if canRemember && !explaining {
                    HStack(spacing: 6) {
                        Toggle(rememberTitle, isOn: $remember)
                            .toggleStyle(.checkbox)
                            .font(.subheadline)
                            .onChange(of: remember) { _ in grow() }
                        if remember {
                            Picker("How long", selection: $scope) {
                                Text("for this task").tag("task")
                                if prompt.canSave {
                                    Text("for 1 hour").tag("hour")
                                    Text("today").tag("day")
                                    Text("always").tag("always")
                                }
                            }
                            .labelsHidden()
                            .pickerStyle(.menu)
                            .controlSize(.small)
                            .fixedSize()
                            .onChange(of: scope) { _ in grow() }
                        }
                    }
                }
                if needsTouchID && !explaining {
                    Label("Needs Touch ID or your password. Pinlet asks again after a month.",
                          systemImage: "touchid")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if explaining {
                    // Return here denies with the reason; it never allows.
                    TextField("Tell the AI why, or what to do instead", text: $reason)
                        .textFieldStyle(.roundedBorder)
                        .focused($reasonFocused)
                        .onSubmit { answer(PromptAnswer(ok: false, reason: reason)) }
                        .onAppear { reasonFocused = true }
                }
                HStack(spacing: 8) {
                    Spacer(minLength: 12)
                    Button(prompt.deny) { answer(PromptAnswer(ok: false, reason: reason)) }
                        .keyboardShortcut(.cancelAction)
                        .controlSize(.large)
                    // A risky step never goes through on a stray Return: it takes Cmd+Return.
                    // While the person types a reason, Return belongs to the text field.
                    Button(prompt.allow) {
                        answer(PromptAnswer(ok: true, always: prompt.isAccess && always,
                                            remember: canRemember && remember ? scope : ""))
                    }
                        .keyboardShortcut(prompt.isAccess && !explaining ? .defaultAction : KeyboardShortcut(.return, modifiers: .command))
                        .buttonStyle(.borderedProminent)
                        .tint(prompt.isAccess ? accent : danger)
                        .controlSize(.large)
                        .disabled(!armed)
                }
                .padding(.top, 4)
                // Its own line, so neither the hint nor the link is ever cut short.
                HStack(spacing: 8) {
                    Countdown(deadline: deadline)
                        .fixedSize()
                    Spacer(minLength: 12)
                    if !explaining {
                        Button("Deny and say why…") {
                            explaining = true
                            grow()
                        }
                            .buttonStyle(.link)
                            .font(.subheadline)
                            .fixedSize()
                    }
                }
                if canRemember && !explaining {
                    Divider().opacity(0.5)
                    // Every risky step by this AI until its task ends or Stop. Never payments,
                    // passwords or security prompts: those are refused before any rule is read.
                    Button {
                        answer(PromptAnswer(ok: true, wide: true))
                    } label: {
                        Label("Allow, and run this task without asking…", systemImage: "forward.end.fill")
                            .font(.subheadline)
                    }
                    .buttonStyle(.link)
                    .disabled(!armed)
                    .help("Every risky step by \(prompt.client.isEmpty ? "this AI" : prompt.client) goes ahead until its task ends or you press Stop. Payments, passwords and macOS security prompts are never included. Takes Touch ID.")
                }
            }
        }
        .padding(16)
        .frame(width: 440, alignment: .leading)
        .modifier(CardBackground(shape: shape, dark: dark, rim: prompt.isAccess ? accent : danger))
        .padding(10)  // room for the shadow
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Pinlet asks: \(prompt.text)")
        .onAppear {
            DispatchQueue.main.asyncAfter(deadline: .now() + PromptCard.armDelay) { armed = true }
        }
    }

    static let armDelay: TimeInterval = 0.6

    /// "Don't ask again for Send in Mail"
    private var rememberTitle: String {
        let what = prompt.actionLabel.isEmpty ? "this" : "“\(prompt.actionLabel)”"
        return "Don’t ask again for \(what)" + (prompt.app.isEmpty ? "" : " in \(prompt.app)")
    }

    private func grow() { DispatchQueue.main.async { resized() } }
}

/// "Denies in 0:24", ticking.
private struct Countdown: View {
    let deadline: Date

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let left = max(0, Int(deadline.timeIntervalSince(context.date).rounded(.up)))
            Text("Moves to the menu in \(left / 60):\(String(format: "%02d", left % 60))")
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
