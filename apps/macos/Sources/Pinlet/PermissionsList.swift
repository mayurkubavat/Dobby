import SwiftUI

/// What the person has told Pinlet not to ask about again: the "don't ask
/// again" rules and the "Always allow" apps, for Settings > Permissions.
/// (Permissions.swift is the other thing: what macOS allows Pinlet.)
final class PermissionsModel: ObservableObject {
    static let shared = PermissionsModel()

    @Published private(set) var rules: [SavedRule] = []
    @Published private(set) var apps: [SavedApp] = []
    /// False while the shared Pinlet cannot be reached.
    @Published private(set) var reachable = true

    /// Set by the app delegate: reads the lists off the main thread.
    var fetch: ((_ done: @escaping ([SavedRule]?, [SavedApp]?) -> Void) -> Void)?
    /// Takes back one rule, one app, or (with neither) everything.
    var revoke: ((_ rule: String?, _ app: String?, _ done: @escaping () -> Void) -> Void)?

    var isEmpty: Bool { rules.isEmpty && apps.isEmpty }

    func refresh() {
        guard let fetch else { return }
        fetch { [weak self] rules, apps in
            guard let self else { return }
            self.reachable = rules != nil
            self.rules = rules ?? []
            self.apps = apps ?? []
        }
    }

    func forget(rule: SavedRule) {
        rules.removeAll { $0.id == rule.id }
        revoke?(rule.id, nil) { [weak self] in self?.refresh() }
    }

    func forget(app: SavedApp) {
        apps.removeAll { $0.id == app.id }
        revoke?(nil, app.app) { [weak self] in self?.refresh() }
    }

    func forgetAll() {
        rules = []
        apps = []
        revoke?(nil, nil) { [weak self] in self?.refresh() }
    }
}

struct PermissionsTab: View {
    @ObservedObject var model: PermissionsModel
    @State private var confirmAll = false

    var body: some View {
        Form {
            Section {
                if model.rules.isEmpty {
                    Text(model.reachable ? "Nothing saved. Pinlet asks before each risky step."
                                         : "Pinlet is not running.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(model.rules) { rule in
                        PermissionRow(title: rule.what,
                                      detail: Self.detail(rule),
                                      symbol: Self.symbol(rule.scope)) { model.forget(rule: rule) }
                    }
                }
            } header: {
                Text("Don't Ask Again")
            } footer: {
                SettingsFooter("Each one covers a single kind of step, in one app, for one AI. Passwords, payments, macOS security prompts and Pinlet's own windows are never covered.")
            }

            Section {
                if model.apps.isEmpty {
                    Text("No apps.").foregroundStyle(.secondary)
                } else {
                    ForEach(model.apps) { app in
                        PermissionRow(title: app.app,
                                      detail: Self.until(app.expires),
                                      symbol: "app.badge.checkmark") { model.forget(app: app) }
                    }
                }
            } header: {
                Text("Always Allowed Apps")
            } footer: {
                SettingsFooter("Saved with Touch ID or your password. Each lapses a month after you saved it, and Pinlet asks again.")
            }

            Section {
                HStack {
                    Button("Ask About Everything Again…", role: .destructive) { confirmAll = true }
                        .disabled(model.isEmpty)
                    Spacer()
                    Button { model.refresh() } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                        .labelStyle(.iconOnly)
                        .help("Refresh")
                }
            }
        }
        .modifier(SettingsForm())
        .onAppear { model.refresh() }
        .confirmationDialog("Ask about everything again?", isPresented: $confirmAll) {
            Button("Forget All Permissions", role: .destructive) { model.forgetAll() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Pinlet forgets every saved rule and every always-allowed app, and asks before each risky step.")
        }
    }

    static func detail(_ rule: SavedRule) -> String {
        var parts: [String] = []
        if !rule.agent.isEmpty { parts.append(rule.agent) }
        parts.append(rule.until())
        if rule.usesToday > 0 { parts.append("used \(rule.usesToday)× today") }
        return parts.joined(separator: " · ")
    }

    static func until(_ date: Date?) -> String {
        guard let date else { return "No end date (saved before the monthly reset)" }
        let f = DateFormatter()
        f.setLocalizedDateFormatFromTemplate("MMMd")
        return "Until " + f.string(from: date)
    }

    static func symbol(_ scope: String) -> String {
        switch scope {
        case "always": return "checkmark.seal"
        case "day": return "calendar"
        case "hour": return "clock"
        default: return "bolt"
        }
    }
}

private struct PermissionRow: View {
    let title: String
    let detail: String
    let symbol: String
    let remove: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: symbol)
                .foregroundStyle(.secondary)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button("Remove", action: remove)
                .controlSize(.small)
        }
        .accessibilityElement(children: .combine)
    }
}
