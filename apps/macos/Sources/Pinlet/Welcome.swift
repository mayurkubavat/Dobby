import AppKit
import Combine
import SwiftUI

/// First-run window: the two permissions Pinlet needs, each with a live
/// checkmark and a button to the right System Settings pane, plus Open at
/// Login. Shown at launch until both are granted (or the person closes it
/// with "Later"); always available from the menu.
final class WelcomeWindowController {
    static let dismissedKey = "welcomeDismissed"
    private var window: NSWindow?

    func showIfNeeded() {
        if !Permissions.allGranted && !UserDefaults.standard.bool(forKey: WelcomeWindowController.dismissedKey) {
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
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

struct WelcomeView: View {
    let close: () -> Void
    @State private var accessibility = Permissions.accessibility
    @State private var screen = Permissions.screenRecording
    @State private var loginItem = LoginItem.enabled
    private let tick = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 14) {
                Image(nsImage: Icons.appIcon(size: 56))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Welcome to Pinlet").font(.title2.weight(.semibold))
                    Text("Two permissions let Pinlet see and use your Mac for your AI.")
                        .foregroundStyle(.secondary)
                }
            }
            step(done: accessibility, title: "Accessibility",
                 detail: "To click, type and read buttons in your apps.",
                 action: "Open Settings…") { Permissions.askAccessibility() }
            step(done: screen, title: "Screen Recording",
                 detail: "To take the screenshots your AI looks at. macOS may ask you to reopen Pinlet.",
                 action: "Allow…") { Permissions.askScreenRecording() }
            Toggle("Open at login", isOn: $loginItem)
                .onChange(of: loginItem) { LoginItem.set($0) }
            HStack {
                Spacer()
                Button(accessibility && screen ? "Done" : "Later") { close() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 480)
        .onReceive(tick) { _ in
            accessibility = Permissions.accessibility
            screen = Permissions.screenRecording
        }
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
            if !done {
                Button(action, action: run)
            }
        }
    }
}
