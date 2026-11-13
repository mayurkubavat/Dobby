import AppKit
import SwiftUI

/// `pinlet doctor --json`, shown as a checklist (Settings → Diagnostics).
final class DiagnosticsModel: ObservableObject {
    struct Item: Identifiable, Equatable {
        var id: String { name }
        var name: String
        var status: String  // ok, warn, fail
        var detail: String
        var fix: String
    }

    @Published private(set) var items: [Item] = []
    @Published private(set) var running = false
    @Published private(set) var error: String?

    func run() {
        guard !running else { return }
        running = true
        PinletCommand.run(["doctor", "--json"]) { [weak self] json, err in
            guard let self else { return }
            self.running = false
            guard let json else {
                self.items = []
                self.error = err.isEmpty ? "Pinlet is not installed yet." : err
                return
            }
            self.error = nil
            self.items = (json["checks"] as? [[String: Any]] ?? []).map {
                Item(name: $0["name"] as? String ?? "", status: $0["status"] as? String ?? "warn",
                     detail: $0["detail"] as? String ?? "", fix: $0["fix"] as? String ?? "")
            }
        }
    }

    /// Plain text for a bug report.
    var report: String {
        var lines = ["Pinlet.app \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"), macOS \(ProcessInfo.processInfo.operatingSystemVersionString)"]
        lines.append("Accessibility: \(Permissions.accessibility ? "granted" : "not granted"), Screen Recording: \(Permissions.screenRecording ? "granted" : "not granted")")
        for item in items {
            lines.append("\(item.status.uppercased())  \(item.name): \(item.detail)" + (item.fix.isEmpty ? "" : "  (fix: \(item.fix))"))
        }
        if let error { lines.append("error: \(error)") }
        return lines.joined(separator: "\n")
    }
}

struct DiagnosticsView: View {
    @ObservedObject var model: DiagnosticsModel
    @ObservedObject var installer = RuntimeInstaller.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    if let error = model.error {
                        Text(error).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(model.items) { item in
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: icon(item.status)).foregroundStyle(color(item.status))
                                .accessibilityLabel(item.status)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.name).font(.headline)
                                Text(item.detail).font(.caption).foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                                if !item.fix.isEmpty {
                                    Text("Fix: \(item.fix)").font(.caption).textSelection(.enabled)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            InstallStatus(installer: installer)
            HStack {
                Button("Check Again") { model.run() }.disabled(model.running)
                if model.running { ProgressView().controlSize(.small) }
                Spacer()
                Button("Reinstall Engine") { installer.install() }.disabled(installer.running)
                Button("Copy Report") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(model.report, forType: .string)
                }
            }
        }
        .padding(20)
        .frame(minHeight: 300, alignment: .top)
        .onAppear { model.run() }
        .onChange(of: installer.phase) { phase in if phase == .done { model.run() } }
    }

    private func icon(_ status: String) -> String {
        switch status {
        case "ok": return "checkmark.circle.fill"
        case "fail": return "xmark.octagon.fill"
        default: return "exclamationmark.triangle.fill"
        }
    }

    private func color(_ status: String) -> Color {
        switch status {
        case "ok": return .green
        case "fail": return .red
        default: return .orange
        }
    }
}

/// The installer's progress or failure, in one line (Welcome and Diagnostics).
struct InstallStatus: View {
    @ObservedObject var installer: RuntimeInstaller

    var body: some View {
        switch installer.phase {
        case .idle, .done:
            EmptyView()
        case .running(let step, let total, let text):
            VStack(alignment: .leading, spacing: 4) {
                ProgressView(value: Double(max(step, 0)), total: Double(max(total, 1)))
                Text(text).font(.caption).foregroundStyle(.secondary)
            }
        case .failed(let why):
            HStack(alignment: .top) {
                Text(why).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button("Show Log") { NSWorkspace.shared.open(RuntimeInstaller.logURL) }
            }
        }
    }
}
