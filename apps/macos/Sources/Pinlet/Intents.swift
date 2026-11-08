import AppIntents
import Foundation

// Pinlet's actions for Spotlight, Shortcuts and Siri. They run inside
// Pinlet.app and talk to the shared Pinlet over the control socket, like the
// menu does. (They only register when the app is built as an Xcode target;
// see project.yml and build.sh.)

enum PinletIntentError: Error, CustomLocalizedStringResourceConvertible {
    case notRunning

    var localizedStringResource: LocalizedStringResource {
        "Pinlet isn't running. Open Pinlet and try again."
    }
}

private func run(_ key: String) throws {
    let client = ControlClient()
    defer { client.close() }
    guard client.action(key) else { throw PinletIntentError.notRunning }
}

struct PauseAllPinletsIntent: AppIntent {
    static var title: LocalizedStringResource = "Pause All Pinlets"
    static var description = IntentDescription("Pauses every Pinlet before its next step.")

    func perform() async throws -> some IntentResult & ProvidesDialog {
        try run("pause_all")
        return .result(dialog: "Paused all Pinlets.")
    }
}

struct ResumeAllPinletsIntent: AppIntent {
    static var title: LocalizedStringResource = "Resume All Pinlets"
    static var description = IntentDescription("Lets every paused Pinlet carry on.")

    func perform() async throws -> some IntentResult & ProvidesDialog {
        try run("resume_all")
        return .result(dialog: "Pinlets are working again.")
    }
}

struct StopAllPinletsIntent: AppIntent {
    static var title: LocalizedStringResource = "Stop All Pinlets"
    static var description = IntentDescription("Stops every Pinlet and denies any question it is waiting on.")

    func perform() async throws -> some IntentResult & ProvidesDialog {
        try run("stop_all")
        return .result(dialog: "Stopped all Pinlets.")
    }
}

struct PinletStatusIntent: AppIntent {
    static var title: LocalizedStringResource = "What Is Pinlet Doing"
    static var description = IntentDescription("Says how many Pinlets are running and what they are doing.")

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let client = ControlClient()
        defer { client.close() }
        guard let view = client.topbar() else { throw PinletIntentError.notRunning }
        var lines = [view.subtitle]
        lines += view.rows.prefix(5).map { "\($0.title): \($0.detail)" }
        let text = lines.joined(separator: "\n")
        return .result(value: text, dialog: "\(text)")
    }
}

struct TellPinletIntent: AppIntent {
    static var title: LocalizedStringResource = "Tell Pinlet"
    static var description = IntentDescription(
        "Sends an instruction to the Pinlet that is working or waiting for you. Its AI reads it at its next step.")

    @Parameter(title: "Instruction", requestValueDialog: "What should Pinlet do?")
    var text: String

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let client = ControlClient()
        defer { client.close() }
        guard let view = client.topbar() else { throw PinletIntentError.notRunning }
        guard let pid = ActivityModel.defaultSelection(view.rows),
              let row = view.rows.first(where: { $0.pid == pid }) else {
            return .result(dialog: "No Pinlet is running. Ask your AI app to use Pinlet first.")
        }
        guard let reply = client.tell(pid: pid, text: text), reply["ok"] as? Bool == true else {
            throw PinletIntentError.notRunning
        }
        let woke = reply["woke"] as? Bool == true
        return .result(dialog: woke ? "Woke \(row.firstName) with your note." : "Sent to \(row.firstName).")
    }
}

struct ShowPinletActivityIntent: AppIntent {
    static var title: LocalizedStringResource = "Show What Pinlet Is Doing"
    static var description = IntentDescription("Opens Pinlet's activity panel, where you can also tell it what to do.")
    static var openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
        NotificationCenter.default.post(name: ActivityPanel.showRequest, object: nil)
        return .result()
    }
}

struct PinletShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: PauseAllPinletsIntent(), phrases: ["Pause \(.applicationName)", "Pause all \(.applicationName)s"])
        AppShortcut(intent: ResumeAllPinletsIntent(), phrases: ["Resume \(.applicationName)", "Resume all \(.applicationName)s"])
        AppShortcut(intent: StopAllPinletsIntent(), phrases: ["Stop \(.applicationName)", "Stop all \(.applicationName)s"])
        AppShortcut(intent: PinletStatusIntent(), phrases: ["What is \(.applicationName) doing", "\(.applicationName) status"])
        AppShortcut(intent: TellPinletIntent(), phrases: ["Tell \(.applicationName)", "Give \(.applicationName) an instruction"])
        AppShortcut(intent: ShowPinletActivityIntent(), phrases: ["Show \(.applicationName) activity", "Open \(.applicationName) activity"])
    }
}
