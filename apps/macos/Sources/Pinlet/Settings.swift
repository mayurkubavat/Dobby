import AppKit
import SwiftUI

/// Pinlet's settings, kept in the app's UserDefaults. The ones the engine
/// reads become `PINLET_*` variables when the app starts `pinlet serve`, so
/// people stop needing a shell to change them.
final class AppSettings: ObservableObject {
    static let shared = AppSettings()
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    // Engine settings (applied when Pinlet restarts).
    @Published var personality: String = "" { didSet { store("personality", personality) } }  // "", off, subtle, lively
    @Published var glass: String = "" { didSet { store("glass", glass) } }                     // "", auto, clear, regular, 0
    @Published var theme: String = "" { didSet { store("theme", theme) } }                     // "", dark, light, auto
    @Published var hideIdleMinutes: Int = -1 { didSet { store("hideIdleMinutes", hideIdleMinutes) } }  // -1 = default
    @Published var appPrompts: Bool = true { didSet { store("appPrompts", appPrompts) } }
    @Published var restingBar: Bool = false { didSet { store("restingBar", restingBar) } }  // keep the bar while resting
    /// Let screenshots and screen recordings show the bar (for demos and for checking its look).
    @Published var barInRecordings: Bool = false { didSet { store("barInRecordings", barInRecordings) } }
    @Published var barAtBottom: Bool = false { didSet { store("barAtBottom", barAtBottom) } }  // above the Dock
    /// The Settings tab in front ("general", "appearance", "permissions"…). Not saved.
    @Published var tab: String = "general"
    static let tabs = ["general", "appearance", "companion", "notifications", "menu", "ai-tools", "permissions", "diagnostics"]
    @Published var background: Bool = false { didSet { store("background", background) } }  // work in one window, leave the screen alone
    @Published var borrowWhenIdle: Bool = false { didSet { store("borrowWhenIdle", borrowWhenIdle) } }
    /// "" (human: steps at a speed a person can follow and stop) or "fast".
    @Published var pace: String = "" { didSet { store("pace", pace) } }
    /// "" (ask before risky steps), "turbo" (ask nothing) or "secure" (ask everything).
    @Published var defaultLevel: String = "" { didSet { store("defaultLevel", defaultLevel) } }
    /// Look: "" (each AI its own colour) or one colour for all, as "#rrggbb".
    @Published var color: String = "" { didSet { store("color", color) } }
    @Published var minimalText: Bool = false { didSet { store("minimalText", minimalText) } }
    @Published var clickTrail: Bool = true { didSet { store("clickTrail", clickTrail) } }
    // The companion (read live by CompanionPanel, no relaunch).
    @Published var companion: Bool = false { didSet { store("companion", companion) } }
    @Published var companionSize: Int = CompanionSettings.defaultSize { didSet { store("companionSize", companionSize) } }

    /// The values as `pinlet serve` reads them. Only what was changed here.
    func engineEnvironment() -> [String: String] {
        load()
        var env: [String: String] = [:]
        if !personality.isEmpty { env["PINLET_PERSONALITY"] = personality }
        if !glass.isEmpty { env["PINLET_OVERLAY_GLASS"] = glass }
        if !theme.isEmpty { env["PINLET_OVERLAY_THEME"] = theme }
        if hideIdleMinutes >= 0 { env["PINLET_MENU_HIDE_IDLE"] = String(hideIdleMinutes * 60) }
        if !appPrompts { env["PINLET_APP_PROMPTS"] = "0" }
        if restingBar { env["PINLET_OVERLAY_PILL"] = "always" }
        if barInRecordings { env["PINLET_OVERLAY_PILL_RECORD"] = "1" }
        if barAtBottom { env["PINLET_OVERLAY_PILL_POS"] = "bottom" }
        if !pace.isEmpty { env["PINLET_PACE"] = pace }
        if !defaultLevel.isEmpty { env["PINLET_DEFAULT_MODE"] = defaultLevel }
        if !color.isEmpty { env["PINLET_OVERLAY_COLOR"] = color }
        if minimalText { env["PINLET_OVERLAY_NARRATE"] = "0" }
        if !clickTrail { env["PINLET_OVERLAY_TRAIL"] = "0" }
        if background {
            env["PINLET_BACKGROUND"] = "1"
            if borrowWhenIdle { env["PINLET_BACKGROUND_POINTER"] = "idle" }
        }
        return env
    }

    private var loading = false

    func load() {
        loading = true
        defer { loading = false }
        personality = defaults.string(forKey: "personality") ?? ""
        glass = defaults.string(forKey: "glass") ?? ""
        theme = defaults.string(forKey: "theme") ?? ""
        hideIdleMinutes = defaults.object(forKey: "hideIdleMinutes") as? Int ?? -1
        appPrompts = defaults.object(forKey: "appPrompts") as? Bool ?? true
        restingBar = defaults.object(forKey: "restingBar") as? Bool ?? false
        barInRecordings = defaults.object(forKey: "barInRecordings") as? Bool ?? false
        barAtBottom = defaults.object(forKey: "barAtBottom") as? Bool ?? false
        background = defaults.object(forKey: "background") as? Bool ?? false
        borrowWhenIdle = defaults.object(forKey: "borrowWhenIdle") as? Bool ?? false
        pace = defaults.string(forKey: "pace") ?? ""
        defaultLevel = defaults.string(forKey: "defaultLevel") ?? ""
        color = defaults.string(forKey: "color") ?? ""
        minimalText = defaults.object(forKey: "minimalText") as? Bool ?? false
        clickTrail = defaults.object(forKey: "clickTrail") as? Bool ?? true
        companion = defaults.object(forKey: "companion") as? Bool ?? false
        companionSize = CompanionSettings.size(defaults: defaults)
    }

    /// One colour for every AI, in macOS's own palette. Frost is Pinlet's icon colour.
    struct Swatch: Identifiable {
        let name: String
        let hex: String
        var id: String { hex }
    }

    static let colors: [Swatch] = [
        Swatch(name: "Frost", hex: "#0ea5c6"), Swatch(name: "Blue", hex: "#0a84ff"),
        Swatch(name: "Indigo", hex: "#5e5ce6"), Swatch(name: "Mint", hex: "#63e6e2"),
        Swatch(name: "Green", hex: "#30d158"), Swatch(name: "Orange", hex: "#ff9f0a"),
        Swatch(name: "Pink", hex: "#ff375f"), Swatch(name: "Graphite", hex: "#8e8e93"),
    ]

    /// The look of Pinlet's own windows (the Allow/Deny card): dark unless the
    /// theme says light, or nil to follow macOS ("auto").
    static func appearanceName(for theme: String) -> NSAppearance.Name? {
        switch theme {
        case "light": return .aqua
        case "auto": return nil
        default: return .darkAqua
        }
    }

    /// The current theme's appearance, read straight from the defaults.
    static func currentAppearance(defaults: UserDefaults = .standard) -> NSAppearance? {
        appearanceName(for: defaults.string(forKey: "theme") ?? "").flatMap { NSAppearance(named: $0) }
    }

    private func store(_ key: String, _ value: Any) {
        if !loading { defaults.set(value, forKey: key) }
    }
}

/// The Settings window (Pinlet menu → Settings…, or Cmd+, while it is open).
final class SettingsWindowController {
    var restartEngine: () -> Void = {}
    private var window: NSWindow?

    /// `tab`: one of `AppSettings.tabs` to bring to the front (`pinlet://settings/permissions`).
    func show(tab: String? = nil) {
        AppSettings.shared.load()
        if let tab, AppSettings.tabs.contains(tab) { AppSettings.shared.tab = tab }
        if window == nil {
            let view = SettingsView(settings: AppSettings.shared, restart: { [weak self] in self?.restartEngine() })
            let w = NSWindow(contentViewController: NSHostingController(rootView: view))
            w.title = "Pinlet Settings"
            w.styleMask = [.titled, .closable]
            w.isReleasedWhenClosed = false
            w.center()
            window = w
        }
        bringForward(window)
    }
}

struct SettingsView: View {
    @ObservedObject var settings: AppSettings
    let restart: () -> Void
    @State private var loginItem = LoginItem.enabled
    @State private var notifyDone = Notifier.notifyWhenDone
    @State private var notifyTrouble = Notifier.notifyTrouble
    @State private var finishSound = Notifier.finishSound
    @State private var haptics = Notifier.haptics
    @State private var announce = Notifier.announce
    @State private var changed = false
    @StateObject private var diagnostics = DiagnosticsModel()

    var body: some View {
        TabView(selection: $settings.tab) {
            Form {
                Section {
                    Toggle("Open at login", isOn: $loginItem)
                        .onChange(of: loginItem) { LoginItem.set($0) }
                    Picker("Personality", selection: $settings.personality) {
                        Text("Default (subtle)").tag("")
                        Text("Off").tag("off")
                        Text("Subtle").tag("subtle")
                        Text("Lively").tag("lively")
                    }
                    .onChange(of: settings.personality) { _ in changed = true }
                }
                Section {
                    Picker("Pace", selection: $settings.pace) {
                        Text("Human (default)").tag("")
                        Text("Fast").tag("fast")
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: settings.pace) { _ in changed = true }
                } footer: {
                    SettingsFooter("Human: each step waits a moment, the pointer rests on its target before a click and text is typed at a readable speed, so you can follow along and press Stop (⌃⌥⎋) in time. Pinlet never acts while the screen is locked.")
                }
                Section {
                    Toggle("Work in the background", isOn: $settings.background)
                        .onChange(of: settings.background) { _ in changed = true }
                    Toggle("Borrow the pointer when I am idle", isOn: $settings.borrowWhenIdle)
                        .onChange(of: settings.borrowWhenIdle) { _ in changed = true }
                        .disabled(!settings.background)
                } footer: {
                    SettingsFooter("In the background, each AI works in one window through accessibility and leaves your pointer, keyboard and front app alone. Keep its apps on another Desktop. Steps that need a real click are refused, or done while you are idle.")
                }
            }
            .modifier(SettingsForm())
            .tabItem { Label("General", systemImage: "gearshape") }
            .tag("general")

            Form {
                Section {
                    Picker("Look", selection: $settings.theme) {
                        Text("Default (dark)").tag("")
                        Text("Dark").tag("dark")
                        Text("Light").tag("light")
                        Text("Match macOS").tag("auto")
                    }
                    .onChange(of: settings.theme) { _ in changed = true }
                    Picker("Bar glass", selection: $settings.glass) {
                        Text("Default").tag("")
                        Text("Always clear").tag("clear")
                        Text("Always regular").tag("regular")
                        Text("Frosted (no glass)").tag("0")
                    }
                    .onChange(of: settings.glass) { _ in changed = true }
                    Toggle("Show the bar while Pinlet rests", isOn: $settings.restingBar)
                        .onChange(of: settings.restingBar) { _ in changed = true }
                    Toggle("Show the bar in screenshots and recordings", isOn: $settings.barInRecordings)
                        .onChange(of: settings.barInRecordings) { _ in changed = true }
                    Toggle("Keep the bar at the bottom of the screen", isOn: $settings.barAtBottom)
                        .onChange(of: settings.barAtBottom) { _ in changed = true }
                } footer: {
                    SettingsFooter("Default glass is clear at rest and regular with text. With the bar off at rest, wake Pinlet from the menu or with ⌃⌥Space.")
                }
                Section {
                    Picker("Colour", selection: $settings.color) {
                        Text("One per AI (default)").tag("")
                        ForEach(AppSettings.colors) { c in
                            Label {
                                Text(c.name)
                            } icon: {
                                Image(systemName: "circle.fill").foregroundStyle(Color(hex: c.hex))
                            }
                            .tag(c.hex)
                        }
                    }
                    .onChange(of: settings.color) { _ in changed = true }
                    Toggle("Minimal text", isOn: $settings.minimalText)
                        .onChange(of: settings.minimalText) { _ in changed = true }
                    Toggle("Leave a trail of clicks", isOn: $settings.clickTrail)
                        .onChange(of: settings.clickTrail) { _ in changed = true }
                } footer: {
                    SettingsFooter("Colour tints the pointer, its label, the glow and the bar. Minimal text drops “Pinlet is working” and shows each action only briefly. On macOS 26 and later the bar and Pinlet's cards are Liquid Glass.")
                }
            }
            .modifier(SettingsForm())
            .tabItem { Label("Appearance", systemImage: "paintbrush") }
            .tag("appearance")

            Form {
                Section {
                    Toggle("Show the companion", isOn: $settings.companion)
                    Picker("Size", selection: $settings.companionSize) {
                        Text("Small").tag(56)
                        Text("Medium").tag(72)
                        Text("Large").tag(96)
                        Text("Extra large").tag(128)
                    }
                    .disabled(!settings.companion)
                } footer: {
                    SettingsFooter("Rime sits in a corner and shows what Pinlet is doing: asleep, thinking, working, asking, paused or done. Click it or press ⌃⌥Space to tell your AI something, and drag it anywhere. Allow and Deny stay on the card. Applies right away, with no relaunch.")
                }
            }
            .modifier(SettingsForm())
            .tabItem { Label("Companion", systemImage: "face.smiling") }
            .tag("companion")

            Form {
                Section {
                    Toggle("Ask Allow / Deny on a card", isOn: $settings.appPrompts)
                        .onChange(of: settings.appPrompts) { _ in changed = true }
                } footer: {
                    SettingsFooter("Off: the bar asks instead.")
                }
                Section {
                    Toggle("Notify when a task is done", isOn: $notifyDone)
                        .onChange(of: notifyDone) { Notifier.notifyWhenDone = $0 }
                    Toggle("Notify when a Pinlet keeps failing", isOn: $notifyTrouble)
                        .onChange(of: notifyTrouble) { Notifier.notifyTrouble = $0 }
                    Toggle("Play a sound when a task is done", isOn: $finishSound)
                        .onChange(of: finishSound) { Notifier.finishSound = $0 }
                    Toggle("Tap the trackpad when Pinlet asks", isOn: $haptics)
                        .onChange(of: haptics) { Notifier.haptics = $0 }
                }
                Section {
                    Toggle("Announce progress with VoiceOver", isOn: $announce)
                        .onChange(of: announce) { Notifier.announce = $0 }
                } footer: {
                    SettingsFooter("Only while VoiceOver is on: says when a Pinlet starts, needs you, pauses or is done.")
                }
            }
            .modifier(SettingsForm())
            .tabItem { Label("Notifications", systemImage: "bell.badge") }
            .tag("notifications")

            Form {
                Section {
                    Picker("Hide sleeping rows after", selection: $settings.hideIdleMinutes) {
                        Text("15 minutes (default)").tag(-1)
                        Text("5 minutes").tag(5)
                        Text("15 minutes").tag(15)
                        Text("1 hour").tag(60)
                        Text("Never").tag(0)
                    }
                    .onChange(of: settings.hideIdleMinutes) { _ in changed = true }
                } footer: {
                    SettingsFooter("Stopped rows always leave the menu. A row comes back when that Pinlet works again.")
                }
            }
            .modifier(SettingsForm())
            .tabItem { Label("Menu", systemImage: "list.bullet") }
            .tag("menu")

            Form {
                Section {
                    AIToolsList(model: AIToolsModel.shared, compact: false)
                } footer: {
                    SettingsFooter("Every AI tool runs \(RuntimePaths.launcher().path), so moving Pinlet never breaks them. Restart a tool after a change.")
                }
                Section {
                    HStack {
                        Button("Refresh") { AIToolsModel.shared.refresh() }
                        Spacer()
                        Button("Copy Config for Another Tool") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(AIToolsModel.snippet, forType: .string)
                        }
                    }
                }
            }
            .modifier(SettingsForm())
            .tabItem { Label("AI Tools", systemImage: "sparkles") }
            .tag("ai-tools")

            PermissionsTab(model: PermissionsModel.shared, settings: settings) { changed = true }
                .tabItem { Label("Permissions", systemImage: "hand.raised") }
            .tag("permissions")

            DiagnosticsView(model: diagnostics)
                .tabItem { Label("Diagnostics", systemImage: "stethoscope") }
            .tag("diagnostics")
        }
        .frame(width: 600)
        .safeAreaInset(edge: .bottom) {
            if changed {
                HStack {
                    Text("Some changes apply after a relaunch.").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Relaunch Now") { changed = false; restart() }
                }
                .padding([.horizontal, .bottom], 20)
            }
        }
    }
}

/// One look for every tab: grouped rows (macOS 13+) at a fixed height, so no
/// tab jumps or squeezes its text.
struct SettingsForm: ViewModifier {
    func body(content: Content) -> some View {
        content
            .formStyle(.grouped)
            .frame(minHeight: 460, alignment: .top)
    }
}

/// A section's explanation: secondary, wrapping onto as many lines as it needs.
struct SettingsFooter: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension Color {
    /// "#rrggbb" as a colour (grey when it is not one).
    init(hex: String) {
        let digits = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        let value = UInt32(digits, radix: 16) ?? 0x8e8e93
        self.init(red: Double((value >> 16) & 0xff) / 255, green: Double((value >> 8) & 0xff) / 255,
                  blue: Double(value & 0xff) / 255)
    }
}
