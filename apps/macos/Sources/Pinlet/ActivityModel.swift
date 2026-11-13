import AppKit
import SwiftUI

/// What the activity panel shows and sends: the Pinlets, their recent steps,
/// and the instruction being typed.
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
    static let feedLimit = 5

    /// One line of the shared history: a journal sentence or an agent's message.
    struct FeedItem: Equatable, Identifiable {
        var id: String
        var time: Date?
        var text: String
        var message: Bool
    }

    /// The activity journal and agent-to-agent messages, newest first.
    @Published private(set) var feed: [FeedItem] = []
    /// Open and claimed tasks on the agents' shared board, one line each.
    @Published private(set) var tasks: [String] = []

    func absorbFeed(_ reply: [String: Any]) {
        var items: [FeedItem] = []
        for line in reply["lines"] as? [[String: Any]] ?? [] {
            guard let text = line["text"] as? String, !text.isEmpty else { continue }
            let end = line["end"] as? String ?? line["start"] as? String ?? ""
            items.append(FeedItem(id: "j\(end)\(text)", time: ActivityModel.parseTime(end),
                                  text: text, message: false))
        }
        for msg in reply["messages"] as? [[String: Any]] ?? [] {
            guard let text = msg["text"] as? String, !text.isEmpty else { continue }
            let from = (msg["from"] as? String ?? "An agent").components(separatedBy: " · ").first ?? "An agent"
            let to = (msg["to"] as? String ?? "an agent").components(separatedBy: " · ").first ?? "an agent"
            let verb = (msg["kind"] as? String) == "handoff" ? "handed control to" : "to"
            let t = (msg["t"] as? NSNumber)?.doubleValue
            items.append(FeedItem(id: "m\(t ?? 0)\(text)", time: t.map { Date(timeIntervalSince1970: $0) },
                                  text: "\(from) \(verb) \(to): \(text)", message: true))
        }
        items.sort { ($0.time ?? .distantPast) > ($1.time ?? .distantPast) }
        let kept = Array(items.prefix(ActivityModel.feedLimit))
        if kept != feed { feed = kept }
        let board = (reply["tasks"] as? [[String: Any]] ?? []).compactMap { $0["line"] as? String }
        if board != tasks { tasks = board }
    }

    private static let isoFull: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoPlain = ISO8601DateFormatter()

    static func parseTime(_ text: String) -> Date? {
        isoFull.date(from: text) ?? isoPlain.date(from: text)
    }

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
