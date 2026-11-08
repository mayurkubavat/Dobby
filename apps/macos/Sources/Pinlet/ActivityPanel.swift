import AppKit
import Carbon.HIToolbox
import SwiftUI

/// The activity panel: what each Pinlet is doing right now, its recent steps,
/// Wake / Pause / Stop, and a field to tell it what to do.
///
/// Open it with Control-Option-Space, from the menu ("Activity…"), or the
/// "Show What Pinlet Is Doing" intent. What you type goes to the shared Pinlet
/// with the control op "tell" and reaches that Pinlet's AI with its next tool
/// result (see pinlet/control/inbox.py); a Pinlet waiting for a wake is woken.
///
/// It is a non-activating Liquid Glass panel (regular glass, since it carries
/// text): it takes the keyboard for the text field without bringing Pinlet.app
/// forward, so the app Pinlet works in stays the active one. Esc closes it.
final class ActivityPanel {
    /// Posted (on the main queue) to open the panel, e.g. by an App Intent.
    static let showRequest = Notification.Name("dev.pinlet.showActivity")

    /// (pid, text) → sends the instruction; calls back with the reply or nil.
    var onTell: (_ pid: Int, _ text: String, _ done: @escaping ([String: Any]?) -> Void) -> Void = { _, _, done in done(nil) }
    var onAction: (_ key: String, _ pid: Int?) -> Void = { _, _ in }

    private let model = ActivityModel()
    private var panel: ActivityWindow?

    var isShown: Bool { panel?.isVisible == true }

    /// New content from the shared Pinlet (every poll; cheap when closed).
    func update(_ view: TopbarView, reachable: Bool) {
        model.absorb(view, reachable: reachable)
        guard isShown else { return }
        DispatchQueue.main.async { [weak self] in self?.fit() }  // after SwiftUI lays out the change
    }

    /// Grow or shrink with the content, keeping the top edge where it is.
    private func fit() {
        guard let panel, let host = panel.contentView else { return }
        let size = host.fittingSize
        let old = panel.frame
        guard abs(size.height - old.height) > 0.5 || abs(size.width - old.width) > 0.5 else { return }
        panel.setFrame(NSRect(x: old.maxX - size.width, y: old.maxY - size.height,
                              width: size.width, height: size.height), display: true)
    }

    func toggle() {
        isShown ? hide() : show()
    }

    func show(selecting pid: Int? = nil) {
        if let pid { model.selected = pid }
        let panel = self.panel ?? makePanel()
        self.panel = panel
        let appearance = AppSettings.currentAppearance()
        panel.appearance = appearance
        panel.contentView?.appearance = appearance
        if !panel.isVisible {
            place(panel)
            panel.alphaValue = 0
            panel.makeKeyAndOrderFront(nil)
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? 0 : 0.18
                panel.animator().alphaValue = 1
            }
        } else {
            panel.makeKeyAndOrderFront(nil)
        }
        model.focusField += 1
    }

    func hide() {
        panel?.orderOut(nil)
    }

    private func makePanel() -> ActivityWindow {
        let content = ActivityView(
            model: model,
            send: { [weak self] pid, text in self?.send(pid, text) },
            action: { [weak self] key, pid in self?.onAction(key, pid) },
            close: { [weak self] in self?.hide() })
        let host = NSHostingView(rootView: content)
        let panel = ActivityWindow()
        panel.contentView = host
        panel.onEscape = { [weak self] in self?.hide() }
        return panel
    }

    private func place(_ panel: NSPanel) {
        guard let host = panel.contentView else { return }
        let size = host.fittingSize
        let screen = NSScreen.screens.first(where: { NSMouseInRect(NSEvent.mouseLocation, $0.frame, false) })
            ?? NSScreen.main ?? NSScreen.screens.first
        guard let frame = screen?.visibleFrame else { return }
        // Top right, under the menu bar item, where the Allow/Deny card is not.
        panel.setFrame(NSRect(x: frame.maxX - size.width - 12, y: frame.maxY - size.height - 8,
                              width: size.width, height: size.height), display: true)
    }

    private func send(_ pid: Int, _ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let name = model.view.rows.first(where: { $0.pid == pid })?.firstName ?? "Pinlet"
        model.sending = true
        onTell(pid, trimmed) { [weak self] reply in
            guard let self else { return }
            self.model.sending = false
            guard let reply, reply["ok"] as? Bool == true else {
                let why = (reply?["error"] as? String).map { ": \($0)" } ?? ""
                self.model.status = .failed("Couldn't reach Pinlet\(why). Your note is still in the field.")
                return
            }
            self.model.draft = ""
            self.model.remember(pid: pid, line: "You: \(trimmed)")
            let woke = reply["woke"] as? Bool == true
            self.model.status = .sent(woke ? "Woke \(name) with your note."
                                           : "Sent. \(name) reads it at its next step.")
            if let panel = self.panel {
                NSAccessibility.post(element: panel, notification: .announcementRequested,
                                     userInfo: [.announcement: woke ? "Woke \(name)" : "Sent to \(name)"])
            }
        }
    }
}

/// A borderless glass panel that can take the keyboard (the text field) but
/// never activates Pinlet.app.
private final class ActivityWindow: NSPanel {
    var onEscape: () -> Void = {}

    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 440, height: 300),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: true)
        isFloatingPanel = true
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false  // the glass draws its own; a window shadow outlines the padding
        hidesOnDeactivate = false
        isMovableByWindowBackground = true
        isReleasedWhenClosed = false
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    override func cancelOperation(_ sender: Any?) { onEscape() }
}

// ------------------------------------------------------------------- model
final class ActivityModel: ObservableObject {
    enum Status: Equatable {
        case none
        case sent(String)
        case failed(String)
    }

    @Published var view = TopbarView.empty
    @Published var reachable = false
    @Published var selected: Int?
    @Published var draft = ""
    @Published var sending = false
    @Published var status: Status = .none
    @Published var focusField = 0
    /// Recent distinct activity lines per Pinlet, newest last (built from what
    /// the menu shows each second, plus your own notes).
    @Published private(set) var recent: [Int: [String]] = [:]

    static let recentLimit = 6

    func absorb(_ view: TopbarView, reachable: Bool) {
        if view != self.view { self.view = view }
        if reachable != self.reachable { self.reachable = reachable }
        for row in view.rows where row.state == "working" {
            remember(pid: row.pid, line: ActivityModel.stepLine(row.detail))
        }
        let pids = Set(view.rows.map(\.pid))
        if let sel = selected, !pids.contains(sel) { selected = nil }
        if selected == nil { selected = ActivityModel.defaultSelection(view.rows) }
        recent = recent.filter { pids.contains($0.key) }
    }

    func remember(pid: Int, line: String) {
        guard !line.isEmpty else { return }
        var lines = recent[pid] ?? []
        if lines.last == line { return }
        lines.append(line)
        if lines.count > ActivityModel.recentLimit { lines.removeFirst(lines.count - ActivityModel.recentLimit) }
        recent[pid] = lines
    }

    /// "Working · Click 'Send' · in Mail · step 7 · 0:42" → "Click 'Send' · in Mail".
    static func stepLine(_ detail: String) -> String {
        let parts = detail.components(separatedBy: " · ")
        let kept = parts.dropFirst().filter { part in
            let clock = part.range(of: #"^\d+(:\d\d|h \d\dm)$"#, options: .regularExpression) != nil
            return !clock && !part.hasPrefix("step ") && !part.hasSuffix(" waiting")
        }
        return kept.joined(separator: " · ")
    }

    /// The Pinlet a note goes to by default: one that needs you or waits for
    /// you, else one that is working, else the first.
    static func defaultSelection(_ rows: [TopbarView.Row]) -> Int? {
        for states in [["confirm", "listening"], ["working", "awake"], ["paused", "takeover"]] {
            if let row = rows.first(where: { states.contains($0.state) }) { return row.pid }
        }
        return rows.first?.pid
    }

    var selectedRow: TopbarView.Row? {
        view.rows.first(where: { $0.pid == selected })
    }
}

extension TopbarView.Row {
    /// "Tibble · Claude Code" → "Tibble".
    var firstName: String {
        title.components(separatedBy: " · ").first.map { $0.isEmpty ? "Pinlet" : $0 } ?? "Pinlet"
    }
}

// -------------------------------------------------------------------- views
struct ActivityView: View {
    @ObservedObject var model: ActivityModel
    let send: (Int, String) -> Void
    let action: (String, Int?) -> Void
    let close: () -> Void

    @FocusState private var fieldFocused: Bool
    private let shape = RoundedRectangle(cornerRadius: 18, style: .continuous)

    private var accent: Color { Color(nsColor: Palette.color("fill", in: model.view.palette, fallback: Palette.fill)) }
    private var dark: Bool {
        (AppSettings.currentAppearance() ?? NSApp.effectiveAppearance).bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if model.view.rows.isEmpty {
                Text(model.reachable ? "Nothing running. Ask your AI app to use Pinlet."
                                     : "Pinlet isn't running yet.")
                    .font(.callout).foregroundStyle(.secondary)
                    .padding(.vertical, 6)
            } else {
                VStack(spacing: 4) {
                    ForEach(model.view.rows, id: \.pid) { row in
                        ActivityRowView(row: row, selected: row.pid == model.selected, accent: accent,
                                        select: { model.selected = row.pid },
                                        action: { key in action(key, row.pid) })
                    }
                }
                if let row = model.selectedRow, let lines = model.recent[row.pid], !lines.isEmpty {
                    recentSteps(lines)
                }
                field
                statusLine
            }
        }
        .padding(16)
        .frame(width: 440, alignment: .leading)
        .modifier(CardBackground(shape: shape, dark: dark, rim: accent))
        .padding(10)  // room for the shadow
        .onChange(of: model.focusField) { _ in fieldFocused = true }
        .onAppear { fieldFocused = true }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Pinlet activity")
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Pinlet").font(.headline)
                Text(model.view.subtitle).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text("⌃⌥Space").font(.caption2).foregroundStyle(.tertiary)
                .accessibilityHidden(true)
            Button(action: close) {
                Image(systemName: "xmark").font(.system(size: 11, weight: .bold))
            }
            .modifier(GlassIconButton())
            .help("Close (Esc)")
            .accessibilityLabel("Close")
        }
    }

    private func recentSteps(_ lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text("Recent").font(.caption).foregroundStyle(.secondary)
            ForEach(Array(lines.enumerated().reversed()), id: \.offset) { item in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: item.element.hasPrefix("You: ") ? "text.bubble" : "circle.fill")
                        .font(.system(size: item.element.hasPrefix("You: ") ? 10 : 4))
                        .foregroundStyle(item.offset == lines.count - 1 ? accent : Color.secondary)
                        .frame(width: 12)
                    Text(item.element)
                        .font(.callout)
                        .foregroundStyle(item.offset == lines.count - 1 ? .primary : .secondary)
                        .lineLimit(2)
                }
            }
        }
        .padding(.horizontal, 4)
    }

    private var placeholder: String {
        guard let row = model.selectedRow else { return "Tell Pinlet what to do…" }
        return "Tell \(row.firstName) what to do…"
    }

    private var field: some View {
        HStack(spacing: 8) {
            TextField(placeholder, text: $model.draft, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...4)
                .focused($fieldFocused)
                .onSubmit(submit)
                .disabled(model.sending)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(Color.primary.opacity(0.07), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .accessibilityLabel(placeholder)
            Button(action: submit) {
                Image(systemName: "arrow.up").font(.system(size: 13, weight: .bold))
            }
            .modifier(GlassProminentButton(tint: accent))
            .disabled(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.sending
                      || model.selected == nil)
            .help("Send (Return)")
            .accessibilityLabel("Send")
        }
    }

    @ViewBuilder
    private var statusLine: some View {
        switch model.status {
        case .none:
            Text("It reaches the AI at its next step. Return sends, Esc closes.")
                .font(.caption).foregroundStyle(.tertiary)
        case .sent(let text):
            Label(text, systemImage: "checkmark.circle.fill")
                .font(.caption).foregroundStyle(.secondary)
        case .failed(let text):
            Label(text, systemImage: "exclamationmark.triangle.fill")
                .font(.caption).foregroundStyle(Color(nsColor: NSColor(hex: Palette.caution)))
        }
    }

    private func submit() {
        guard let pid = model.selected else { return }
        send(pid, model.draft)
    }
}

/// One Pinlet: its colour dot, name, what it is doing, and its actions.
struct ActivityRowView: View {
    let row: TopbarView.Row
    let selected: Bool
    let accent: Color
    let select: () -> Void
    let action: (String) -> Void

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(Color(nsColor: NSColor(hex: row.color)))
                .frame(width: 10, height: 10)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(row.title).font(.callout.weight(.semibold)).lineLimit(1)
                Text(row.detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer(minLength: 8)
            ForEach(row.actions, id: \.key) { act in
                Button { action(act.key) } label: {
                    Image(systemName: ActivityRowView.symbol(act))
                        .font(.system(size: 10, weight: .bold))
                }
                .modifier(GlassIconButton())
                .help(act.label)
                .accessibilityLabel("\(act.label) \(row.firstName)")
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(selected ? accent.opacity(0.16) : Color.clear,
                    in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .contentShape(Rectangle())
        .onTapGesture(perform: select)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(row.title), \(row.detail)")
        .accessibilityAddTraits(selected ? [.isSelected] : [])
    }

    static func symbol(_ action: TopbarView.Action) -> String {
        if action.key.hasPrefix("stop") { return "stop.fill" }
        if action.key == "wake" { return "sun.max.fill" }
        return action.label.lowercased().hasPrefix("resume") ? "play.fill" : "pause.fill"
    }
}

/// A small round glass button (macOS 26), a bordered one before.
struct GlassIconButton: ViewModifier {
    @ViewBuilder
    func body(content: Content) -> some View {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            content.buttonStyle(.glass).buttonBorderShape(.circle).controlSize(.small)
        } else {
            content.buttonStyle(.bordered).controlSize(.small)
        }
        #else
        content.buttonStyle(.bordered).controlSize(.small)
        #endif
    }
}

/// The one primary action: prominent glass in Pinlet's cyan (macOS 26).
struct GlassProminentButton: ViewModifier {
    let tint: Color

    @ViewBuilder
    func body(content: Content) -> some View {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            content.buttonStyle(.glassProminent).buttonBorderShape(.circle).tint(tint).controlSize(.large)
        } else {
            content.buttonStyle(.borderedProminent).tint(tint).controlSize(.large)
        }
        #else
        content.buttonStyle(.borderedProminent).tint(tint).controlSize(.large)
        #endif
    }
}

// ----------------------------------------------------------- global hotkey
/// One system-wide shortcut through Carbon's RegisterEventHotKey: no
/// Accessibility permission needed, and the key press is not passed on.
final class HotKey {
    private var ref: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private let callback: () -> Void
    private static var byID: [UInt32: HotKey] = [:]
    private static var nextID: UInt32 = 1

    /// Control-Option-Space by default.
    init?(keyCode: UInt32 = UInt32(kVK_Space), modifiers: UInt32 = UInt32(controlKey | optionKey),
          callback: @escaping () -> Void) {
        self.callback = callback
        let id = HotKey.nextID
        HotKey.nextID += 1
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let status = InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
            var hk = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &hk)
            if let key = HotKey.byID[hk.id] {
                DispatchQueue.main.async { key.callback() }
            }
            return noErr
        }, 1, &spec, nil, &handler)
        guard status == noErr else { return nil }
        let hkID = EventHotKeyID(signature: OSType(0x50_4E_4C_54), id: id)  // "PNLT"
        guard RegisterEventHotKey(keyCode, modifiers, hkID, GetApplicationEventTarget(), 0, &ref) == noErr else {
            if let handler { RemoveEventHandler(handler) }
            return nil
        }
        HotKey.byID[id] = self
    }

    deinit {
        if let ref { UnregisterEventHotKey(ref) }
        if let handler { RemoveEventHandler(handler) }
    }
}
