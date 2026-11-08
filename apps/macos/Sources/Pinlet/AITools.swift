import AppKit
import SwiftUI

/// One AI tool as `pinlet setup --list --json` reports it.
struct AITool: Identifiable, Equatable {
    var id: String { key }
    var key: String
    var label: String
    var installed: Bool
    var state: String       // "connected", "broken", "not set up", "not installed"
    var stable: Bool
    var note: String

    init?(json: [String: Any]) {
        guard let key = json["key"] as? String, let label = json["label"] as? String else { return nil }
        self.key = key
        self.label = label
        installed = json["installed"] as? Bool ?? false
        state = json["state"] as? String ?? "not installed"
        stable = json["stable"] as? Bool ?? false
        note = json["note"] as? String ?? ""
    }

    var connected: Bool { state == "connected" }
    var needsRepair: Bool { state == "broken" || (connected && !stable) }

    static func list(_ raw: Any?) -> [AITool] {
        (raw as? [[String: Any]] ?? []).compactMap(AITool.init(json:))
    }
}

/// The AI tools on this Mac and their Pinlet entries, shared by Welcome and Settings.
final class AIToolsModel: ObservableObject {
    static let shared = AIToolsModel()

    @Published private(set) var tools: [AITool] = []
    @Published private(set) var busy = false
    @Published private(set) var loaded = false
    /// What the last Connect / Remove / Repair did, in a sentence.
    @Published private(set) var message: String?

    var found: [AITool] { tools.filter(\.installed) }

    func refresh() {
        PinletCommand.run(["setup", "--list", "--json"]) { [weak self] json, err in
            guard let self else { return }
            self.loaded = true
            if let json { self.tools = AITool.list(json["tools"]) } else if !err.isEmpty { self.message = err }
        }
    }

    func connect(_ keys: [String]) { change(["setup", "--only", keys.joined(separator: ",")], verb: "Connected") }
    func remove(_ keys: [String]) { change(["setup", "--remove", "--only", keys.joined(separator: ",")], verb: "Removed Pinlet from") }
    func repair() { change(["setup", "--repair"], verb: "Repaired") }

    private func change(_ args: [String], verb: String) {
        guard !busy, !(args.last ?? "").isEmpty else { return }
        busy = true
        message = nil
        PinletCommand.run(args + ["--json"]) { [weak self] json, err in
            guard let self else { return }
            self.busy = false
            guard let json else {
                self.message = err.isEmpty ? "Pinlet setup did not answer." : err
                return
            }
            self.tools = AITool.list(json["tools"])
            self.message = AIToolsModel.summary(json, verb: verb, labels: self.labels)
        }
    }

    private var labels: [String: String] { Dictionary(uniqueKeysWithValues: tools.map { ($0.key, $0.label) }) }

    /// "Connected Cursor and Codex. Restart them to pick Pinlet up." from setup's JSON.
    static func summary(_ json: [String: Any], verb: String, labels: [String: String]) -> String {
        let changes = json["changes"] as? [[String: Any]] ?? []
        let errors = changes.filter { ($0["status"] as? String) == "error" }
        if let first = errors.first {
            let tool = labels[first["tool"] as? String ?? ""] ?? (first["tool"] as? String ?? "")
            return "\(tool): \(first["detail"] as? String ?? "failed")"
        }
        let restart = (json["restart"] as? [String] ?? []).map { labels[$0] ?? $0 }
        if restart.isEmpty { return "Nothing to change: everything was already set up." }
        let names = ListFormatter.localizedString(byJoining: restart)
        let notes = (json["notes"] as? [String: String] ?? [:]).values.sorted()
        let tail = verb == "Removed Pinlet from" ? "" : " Restart \(restart.count == 1 ? "it" : "them") to pick Pinlet up."
        return "\(verb) \(names).\(tail)" + (notes.isEmpty ? "" : " " + notes.joined(separator: " "))
    }

    /// The MCP entry for a tool Pinlet does not know, ready to paste.
    static var snippet: String {
        """
        {
          "mcpServers": {
            "Pinlet": { "command": "\(RuntimePaths.launcher().path)" }
          }
        }
        """
    }
}

/// The list of AI tools with a toggle or a state for each (Welcome and Settings).
struct AIToolsList: View {
    @ObservedObject var model: AIToolsModel
    /// Welcome: toggles plus one Connect button. Settings: per-row buttons.
    var compact: Bool
    @State private var chosen: Set<String> = []
    @State private var seeded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !model.loaded {
                ProgressView().controlSize(.small)
            } else if model.found.isEmpty {
                Text("No AI tools found. Install one (Claude, Cursor, Codex, Gemini CLI…) and come back, or copy the config below into it.")
                    .font(.subheadline).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(model.found) { tool in
                HStack(spacing: 10) {
                    if compact && !tool.connected {
                        Toggle(tool.label, isOn: binding(tool.key))
                    } else {
                        Image(systemName: icon(tool)).foregroundStyle(color(tool))
                            .accessibilityLabel(stateText(tool))
                        Text(tool.label)
                    }
                    Spacer()
                    Text(stateText(tool)).font(.caption).foregroundStyle(.secondary)
                    if !compact {
                        if tool.connected && !tool.needsRepair {
                            Button("Remove") { model.remove([tool.key]) }.disabled(model.busy)
                        } else if tool.needsRepair {
                            Button("Repair") { model.repair() }.disabled(model.busy)
                        } else {
                            Button("Connect") { model.connect([tool.key]) }.disabled(model.busy)
                        }
                    }
                }
            }
            if compact && model.found.contains(where: { !$0.connected }) {
                HStack {
                    Spacer()
                    if model.busy { ProgressView().controlSize(.small) }
                    Button("Connect") { model.connect(Array(chosen)) }
                        .disabled(model.busy || chosen.isEmpty)
                }
            }
            if let message = model.message {
                Text(message).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .onAppear { if !model.loaded { model.refresh() } }
        .onChange(of: model.tools) { tools in
            if !seeded && !tools.isEmpty {
                chosen = Set(tools.filter { $0.installed && !$0.connected }.map(\.key))
                seeded = true
            }
        }
    }

    private func binding(_ key: String) -> Binding<Bool> {
        Binding(get: { chosen.contains(key) },
                set: { on in if on { chosen.insert(key) } else { chosen.remove(key) } })
    }

    private func stateText(_ tool: AITool) -> String {
        switch tool.state {
        case "connected": return tool.stable ? "Connected" : "Connected (old path)"
        case "broken": return "Broken path"
        default: return "Not connected"
        }
    }

    private func icon(_ tool: AITool) -> String {
        tool.needsRepair ? "exclamationmark.triangle.fill" : (tool.connected ? "checkmark.circle.fill" : "circle")
    }

    private func color(_ tool: AITool) -> Color {
        tool.needsRepair ? .orange : (tool.connected ? .green : .secondary)
    }
}

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
