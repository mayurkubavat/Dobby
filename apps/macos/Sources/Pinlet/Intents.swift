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

struct PinletShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: PauseAllPinletsIntent(), phrases: ["Pause \(.applicationName)", "Pause all \(.applicationName)s"])
        AppShortcut(intent: ResumeAllPinletsIntent(), phrases: ["Resume \(.applicationName)", "Resume all \(.applicationName)s"])
        AppShortcut(intent: StopAllPinletsIntent(), phrases: ["Stop \(.applicationName)", "Stop all \(.applicationName)s"])
        AppShortcut(intent: PinletStatusIntent(), phrases: ["What is \(.applicationName) doing", "\(.applicationName) status"])
    }
}
