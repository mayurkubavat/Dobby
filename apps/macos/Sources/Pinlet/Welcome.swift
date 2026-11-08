import AppKit
import Combine
import SwiftUI

/// First-run window: install Pinlet's engine, grant the two permissions it
/// needs (each with a live checkmark and a button to the right System Settings
/// pane), connect the AI tools found on this Mac, and Open at Login. Shown at
/// launch while the engine is not installed, or until both permissions are
/// granted (or the person closes it with "Later"); always available from the menu.
final class WelcomeWindowController {
    static let dismissedKey = "welcomeDismissed"
    private var window: NSWindow?

    func showIfNeeded() {
        if !RuntimePaths.installed && EngineConfig.resolve() == nil {
            show()
        } else if !Permissions.allGranted && !UserDefaults.standard.bool(forKey: WelcomeWindowController.dismissedKey) {
            show()
        }
    }

    func show() {
        if window == nil {
            let view = WelcomeView(close: { [weak self] in
                UserDefaults.standard.set(true, forKey: WelcomeWindowController.dismissedKey)
                self?.window?.close()
            })
            let w = NSWindow(contentViewController: NSHostingController(rootView: view))
            w.title = "Welcome to Pinlet"
            w.styleMask = [.titled, .closable, .fullSizeContentView]
            w.titlebarAppearsTransparent = true
            w.isReleasedWhenClosed = false
            w.center()
            window = w
        }
        bringForward(window)
    }
}

/// Shows a window of this menu bar app in front of the app the person is using.
/// `activate` alone can leave it behind the frontmost app.
func bringForward(_ window: NSWindow?) {
    if #available(macOS 14.0, *) { NSApp.activate() } else { NSApp.activate(ignoringOtherApps: true) }
    window?.makeKeyAndOrderFront(nil)
    window?.orderFrontRegardless()
}

struct WelcomeView: View {
    let close: () -> Void
    @State private var accessibility = Permissions.accessibility
    @State private var screen = Permissions.screenRecording
    @State private var loginItem = LoginItem.enabled
    @State private var installed = EngineConfig.resolve() != nil
    @ObservedObject private var installer = RuntimeInstaller.shared
    @ObservedObject private var tools = AIToolsModel.shared
    private let tick = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 14) {
                Image(nsImage: Icons.appIcon(size: 56))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Welcome to Pinlet").font(.title2.weight(.semibold))
                    Text("A few steps let Pinlet see and use your Mac for your AI.")
                        .foregroundStyle(.secondary)
                }
            }
            step(done: installed, title: "Install Pinlet",
                 detail: installer.running ? "Downloading Python and Pinlet's packages, about a minute the first time."
                                           : "Pinlet's engine and its own Python, kept in Application Support.",
                 action: installer.running ? "" : "Install") { installer.install() }
            if !installed { InstallStatus(installer: installer).padding(.leading, 32) }
            step(done: accessibility, title: "Accessibility",
                 detail: "To click, type and read buttons in your apps.",
                 action: "Open Settings…") { Permissions.askAccessibility() }
            step(done: screen, title: "Screen Recording",
                 detail: "To take the screenshots your AI looks at. macOS may ask you to reopen Pinlet.",
                 action: "Allow…") { Permissions.askScreenRecording() }
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: toolsIcon).font(.system(size: 20)).foregroundStyle(toolsColor)
                VStack(alignment: .leading, spacing: 6) {
                    Text("Connect your AI tools").font(.headline)
                    if installed {
                        AIToolsList(model: tools, compact: true)
                    } else {
                        Text("After the install, pick the AI tools that may use Pinlet.")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                }
            }
            Toggle("Open at login", isOn: $loginItem)
                .onChange(of: loginItem) { LoginItem.set($0) }
            HStack {
                Spacer()
                Button(installed && accessibility && screen ? "Done" : "Later") { close() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 480)
        .onReceive(tick) { _ in
            accessibility = Permissions.accessibility
            screen = Permissions.screenRecording
            let now = EngineConfig.resolve() != nil
            if now && !installed { tools.refresh() }
            installed = now
        }
    }

    private var toolsReady: Bool {
        !tools.found.isEmpty && tools.found.allSatisfy { $0.connected && !$0.needsRepair }
    }
    private var toolsIcon: String {
        tools.found.contains(where: \.needsRepair) ? "exclamationmark.triangle.fill"
            : (toolsReady ? "checkmark.circle.fill" : "circle")
    }
    private var toolsColor: Color {
        tools.found.contains(where: \.needsRepair) ? .orange : (toolsReady ? .green : .secondary)
    }

    private func step(done: Bool, title: String, detail: String, action: String,
                      run: @escaping () -> Void) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: done ? "checkmark.circle.fill" : "circle")
                .font(.system(size: 20))
                .foregroundStyle(done ? Color.green : Color.secondary)
                .accessibilityLabel(done ? "Granted" : "Not granted")
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.headline)
                Text(detail).font(.subheadline).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if !done && !action.isEmpty {
                Button(action, action: run)
            }
        }
    }
}
