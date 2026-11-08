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

    /// The values as `pinlet serve` reads them. Only what was changed here.
    func engineEnvironment() -> [String: String] {
        load()
        var env: [String: String] = [:]
        if !personality.isEmpty { env["PINLET_PERSONALITY"] = personality }
        if !glass.isEmpty { env["PINLET_OVERLAY_GLASS"] = glass }
        if !theme.isEmpty { env["PINLET_OVERLAY_THEME"] = theme }
        if hideIdleMinutes >= 0 { env["PINLET_MENU_HIDE_IDLE"] = String(hideIdleMinutes * 60) }
        if !appPrompts { env["PINLET_APP_PROMPTS"] = "0" }
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
    }

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

    func show() {
        AppSettings.shared.load()
        if window == nil {
            let view = SettingsView(settings: AppSettings.shared, restart: { [weak self] in self?.restartEngine() })
            let w = NSWindow(contentViewController: NSHostingController(rootView: view))
            w.title = "Pinlet Settings"
            w.styleMask = [.titled, .closable]
            w.isReleasedWhenClosed = false
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

struct SettingsView: View {
    @ObservedObject var settings: AppSettings
    let restart: () -> Void
    @State private var loginItem = LoginItem.enabled
    @State private var notifyDone = Notifier.notifyWhenDone
    @State private var changed = false

    var body: some View {
        TabView {
            Form {
                Toggle("Open at login", isOn: $loginItem)
                    .onChange(of: loginItem) { LoginItem.set($0) }
                Picker("Personality", selection: $settings.personality) {
                    Text("Default (subtle)").tag("")
                    Text("Off").tag("off")
                    Text("Subtle").tag("subtle")
                    Text("Lively").tag("lively")
                }
                .onChange(of: settings.personality) { _ in changed = true }
                Picker("Look", selection: $settings.theme) {
                    Text("Default (dark)").tag("")
                    Text("Dark").tag("dark")
                    Text("Light").tag("light")
                    Text("Match macOS").tag("auto")
                }
                .onChange(of: settings.theme) { _ in changed = true }
                Picker("Bar glass", selection: $settings.glass) {
                    Text("Default (clear at rest, regular with text)").tag("")
                    Text("Always clear").tag("clear")
                    Text("Always regular").tag("regular")
                    Text("Frosted (no glass)").tag("0")
                }
                .onChange(of: settings.glass) { _ in changed = true }
            }
            .tabItem { Label("General", systemImage: "gearshape") }

            Form {
                Toggle("Ask Allow / Deny on a Pinlet card", isOn: $settings.appPrompts)
                    .onChange(of: settings.appPrompts) { _ in changed = true }
                Text("Off: Pinlet's own bar asks instead.").font(.caption).foregroundStyle(.secondary)
                Toggle("Notify when a Pinlet is done", isOn: $notifyDone)
                    .onChange(of: notifyDone) { Notifier.notifyWhenDone = $0 }
            }
            .tabItem { Label("Notifications", systemImage: "bell.badge") }

            Form {
                Picker("Hide sleeping Pinlets after", selection: $settings.hideIdleMinutes) {
                    Text("Default (15 minutes)").tag(-1)
                    Text("5 minutes").tag(5)
                    Text("15 minutes").tag(15)
                    Text("1 hour").tag(60)
                    Text("Never").tag(0)
                }
                .onChange(of: settings.hideIdleMinutes) { _ in changed = true }
                Text("Stopped Pinlets always leave the list. Any Pinlet comes back when it works again.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .tabItem { Label("Menu", systemImage: "list.bullet") }
        }
        .padding(20)
        .frame(width: 460)
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
