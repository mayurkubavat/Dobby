import AppKit
import UserNotifications

/// Notification Center banners for the two moments that matter:
///
/// * "Pinlet needs you": an Allow/Deny question nobody answered on the card
///   within a few seconds, with Allow and Deny right on the banner;
/// * "Done": a Pinlet that worked for a while went back to sleep.
///
/// Banners need a real, signed app bundle; under `swift run` this is a no-op.
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    var onAnswer: (_ promptID: Int, _ ok: Bool) -> Void = { _, _ in }
    var onOpenPrompt: () -> Void = {}

    static let doneKey = "notifyWhenDone"
    static var notifyWhenDone: Bool {
        get { UserDefaults.standard.object(forKey: doneKey) as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: doneKey) }
    }

    /// A card left unanswered this long also goes to Notification Center.
    var promptGrace: TimeInterval = 5
    /// Work shorter than this is not worth a "Done".
    var minWork: TimeInterval = 20

    private var center: UNUserNotificationCenter?
    private var promptSeen: [Int: Date] = [:]
    private var promptPosted = Set<Int>()
    private var workingSince: [Int: Date] = [:]

    private enum ID {
        static let promptCategory = "pinlet.prompt"
        static let doneCategory = "pinlet.done"
        static let allow = "allow"
        static let deny = "deny"
    }

    func start() {
        guard Bundle.main.bundleIdentifier != nil else { return }
        let c = UNUserNotificationCenter.current()
        c.delegate = self
        let allow = UNNotificationAction(identifier: ID.allow, title: "Allow", options: [.authenticationRequired])
        let deny = UNNotificationAction(identifier: ID.deny, title: "Deny", options: [])
        c.setNotificationCategories([
            UNNotificationCategory(identifier: ID.promptCategory, actions: [allow, deny], intentIdentifiers: [], options: []),
            UNNotificationCategory(identifier: ID.doneCategory, actions: [], intentIdentifiers: [], options: []),
        ])
        c.requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
        center = c
    }

    /// Called on every poll (main thread).
    func update(prompts: [Prompt], rows: [TopbarView.Row], now: Date = Date()) {
        updatePrompts(prompts, now: now)
        updateRows(rows, now: now)
    }

    private func updatePrompts(_ prompts: [Prompt], now: Date) {
        let live = Set(prompts.map(\.id))
        for gone in Set(promptSeen.keys).subtracting(live) {
            promptSeen[gone] = nil
            if promptPosted.remove(gone) != nil {
                center?.removeDeliveredNotifications(withIdentifiers: ["prompt-\(gone)"])
            }
        }
        for p in prompts {
            let first = promptSeen[p.id] ?? now
            promptSeen[p.id] = first
            guard !promptPosted.contains(p.id), now.timeIntervalSince(first) >= promptGrace else { continue }
            promptPosted.insert(p.id)
            let content = UNMutableNotificationContent()
            content.title = "\(p.name.isEmpty ? "Pinlet" : p.name) needs you"
            content.body = p.detail.isEmpty ? p.text : "\(p.text)\n\(p.detail)"
            content.categoryIdentifier = ID.promptCategory
            content.threadIdentifier = "pinlet-\(p.pid ?? 0)"
            content.userInfo = ["prompt": p.id]
            content.sound = .default
            if #available(macOS 12.0, *) { content.interruptionLevel = .timeSensitive }
            post("prompt-\(p.id)", content)
        }
    }

    private func updateRows(_ rows: [TopbarView.Row], now: Date) {
        var seen = Set<Int>()
        for row in rows {
            seen.insert(row.pid)
            let busy = row.state == "working" || row.state == "awake"
            if busy {
                if workingSince[row.pid] == nil { workingSince[row.pid] = now }
            } else if let since = workingSince.removeValue(forKey: row.pid), row.state == "ready" {
                finished(row, worked: now.timeIntervalSince(since))
            }
        }
        for pid in Set(workingSince.keys).subtracting(seen) { workingSince[pid] = nil }  // stopped or hidden
    }

    private func finished(_ row: TopbarView.Row, worked: TimeInterval) {
        guard Notifier.notifyWhenDone, worked >= minWork else { return }
        let content = UNMutableNotificationContent()
        content.title = "\(row.title) is done"
        let secs = Int(worked)
        content.body = "Worked for \(secs / 60):\(String(format: "%02d", secs % 60))."
        content.categoryIdentifier = ID.doneCategory
        content.threadIdentifier = "pinlet-\(row.pid)"
        if #available(macOS 12.0, *) { content.interruptionLevel = .active }
        post("done-\(row.pid)-\(Int(Date().timeIntervalSince1970))", content)
    }

    private func post(_ id: String, _ content: UNNotificationContent) {
        center?.add(UNNotificationRequest(identifier: id, content: content, trigger: nil))
    }

    // ------------------------------------------------- UNUserNotificationCenterDelegate
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        let promptID = (info["prompt"] as? NSNumber)?.intValue
        let action = response.actionIdentifier
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if let promptID, action == ID.allow || action == ID.deny {
                self.onAnswer(promptID, action == ID.allow)
            } else if promptID != nil {
                self.onOpenPrompt()
            }
        }
        completionHandler()
    }
}
