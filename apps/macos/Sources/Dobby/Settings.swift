import AppKit
import SwiftUI

/// Dobby's settings, kept in the app's UserDefaults. The ones the engine
/// reads become `DOBBY_*` variables when the app starts `dobby serve`, so
/// people stop needing a shell to change them.
final class AppSettings: ObservableObject {
    static let shared = AppSettings()
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    // Engine settings (applied when Dobby restarts).
    @Published var personality: String = "" { didSet { store("personality", personality) } }  // "", off, subtle, elf
    @Published var glass: String = "" { didSet { store("glass", glass) } }                     // "", clear, regular, 0
    @Published var hideIdleMinutes: Int = -1 { didSet { store("hideIdleMinutes", hideIdleMinutes) } }  // -1 = default
    @Published var appPrompts: Bool = true { didSet { store("appPrompts", appPrompts) } }

    /// The values as `dobby serve` reads them. Only what was changed here.
    func engineEnvironment() -> [String: String] {
        load()
        var env: [String: String] = [:]
        if !personality.isEmpty { env["DOBBY_PERSONALITY"] = personality }
        if !glass.isEmpty { env["DOBBY_OVERLAY_GLASS"] = glass }
        if hideIdleMinutes >= 0 { env["DOBBY_MENU_HIDE_IDLE"] = String(hideIdleMinutes * 60) }
        if !appPrompts { env["DOBBY_APP_PROMPTS"] = "0" }
        return env
    }

    private var loading = false

    func load() {
        loading = true
        defer { loading = false }
        personality = defaults.string(forKey: "personality") ?? ""
        glass = defaults.string(forKey: "glass") ?? ""
        hideIdleMinutes = defaults.object(forKey: "hideIdleMinutes") as? Int ?? -1
        appPrompts = defaults.object(forKey: "appPrompts") as? Bool ?? true
    }

    private func store(_ key: String, _ value: Any) {
        if !loading { defaults.set(value, forKey: key) }
    }
}

/// The Settings window (Dobby menu → Settings…, or Cmd+, while it is open).
final class SettingsWindowController {
    var restartEngine: () -> Void = {}
    private var window: NSWindow?

    func show() {
        AppSettings.shared.load()
        if window == nil {
            let view = SettingsView(settings: AppSettings.shared, restart: { [weak self] in self?.restartEngine() })
            let w = NSWindow(contentViewController: NSHostingController(rootView: view))
            w.title = "Dobby Settings"
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
                Toggle("Open Dobby at login", isOn: $loginItem)
                    .onChange(of: loginItem) { LoginItem.set($0) }
                Picker("Personality", selection: $settings.personality) {
                    Text("Default (subtle)").tag("")
                    Text("Off").tag("off")
                    Text("Subtle").tag("subtle")
                    Text("Elf").tag("elf")
                }
                .onChange(of: settings.personality) { _ in changed = true }
                Picker("Bar glass", selection: $settings.glass) {
                    Text("Default (clear)").tag("")
                    Text("Clear").tag("clear")
                    Text("Regular").tag("regular")
                    Text("Frosted (no glass)").tag("0")
                }
                .onChange(of: settings.glass) { _ in changed = true }
            }
            .tabItem { Label("General", systemImage: "gearshape") }

            Form {
                Toggle("Ask Allow / Deny on a Dobby card", isOn: $settings.appPrompts)
                    .onChange(of: settings.appPrompts) { _ in changed = true }
                Text("Off: Dobby's own bar asks instead.").font(.caption).foregroundStyle(.secondary)
                Toggle("Notify when a Dobby is done", isOn: $notifyDone)
                    .onChange(of: notifyDone) { Notifier.notifyWhenDone = $0 }
            }
            .tabItem { Label("Notifications", systemImage: "bell.badge") }

            Form {
                Picker("Hide sleeping Dobbys after", selection: $settings.hideIdleMinutes) {
                    Text("Default (15 minutes)").tag(-1)
                    Text("5 minutes").tag(5)
                    Text("15 minutes").tag(15)
                    Text("1 hour").tag(60)
                    Text("Never").tag(0)
                }
                .onChange(of: settings.hideIdleMinutes) { _ in changed = true }
                Text("Stopped Dobbys always leave the list. Any Dobby comes back when it works again.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .tabItem { Label("Menu", systemImage: "list.bullet") }
        }
        .padding(20)
        .frame(width: 460)
        .safeAreaInset(edge: .bottom) {
            if changed {
                HStack {
                    Text("Some changes apply after Dobby restarts.").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Restart Dobby") { changed = false; restart() }
                }
                .padding([.horizontal, .bottom], 20)
            }
        }
    }
}
