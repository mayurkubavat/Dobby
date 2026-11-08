import AppIntents
import Foundation

// Dobby's actions for Spotlight, Shortcuts and Siri. They run inside
// Dobby.app and talk to the shared Dobby over the control socket, like the
// menu does. (They only register when the app is built as an Xcode target;
// see project.yml and build.sh.)

enum DobbyIntentError: Error, CustomLocalizedStringResourceConvertible {
    case notRunning

    var localizedStringResource: LocalizedStringResource {
        "Dobby isn't running. Open Dobby and try again."
    }
}

private func run(_ key: String) throws {
    let client = ControlClient()
    defer { client.close() }
    guard client.action(key) else { throw DobbyIntentError.notRunning }
}

struct PauseAllDobbysIntent: AppIntent {
    static var title: LocalizedStringResource = "Pause All Dobbys"
    static var description = IntentDescription("Pauses every Dobby before its next step.")

    func perform() async throws -> some IntentResult & ProvidesDialog {
        try run("pause_all")
        return .result(dialog: "Paused all Dobbys.")
    }
}

struct ResumeAllDobbysIntent: AppIntent {
    static var title: LocalizedStringResource = "Resume All Dobbys"
    static var description = IntentDescription("Lets every paused Dobby carry on.")

    func perform() async throws -> some IntentResult & ProvidesDialog {
        try run("resume_all")
        return .result(dialog: "Dobbys are working again.")
    }
}

struct StopAllDobbysIntent: AppIntent {
    static var title: LocalizedStringResource = "Stop All Dobbys"
    static var description = IntentDescription("Stops every Dobby and denies any question it is waiting on.")

    func perform() async throws -> some IntentResult & ProvidesDialog {
        try run("stop_all")
        return .result(dialog: "Stopped all Dobbys.")
    }
}

struct DobbyStatusIntent: AppIntent {
    static var title: LocalizedStringResource = "What Is Dobby Doing"
    static var description = IntentDescription("Says how many Dobbys are running and what they are doing.")

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let client = ControlClient()
        defer { client.close() }
        guard let view = client.topbar() else { throw DobbyIntentError.notRunning }
        var lines = [view.subtitle]
        lines += view.rows.prefix(5).map { "\($0.title): \($0.detail)" }
        let text = lines.joined(separator: "\n")
        return .result(value: text, dialog: "\(text)")
    }
}

struct DobbyShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: PauseAllDobbysIntent(), phrases: ["Pause \(.applicationName)", "Pause all \(.applicationName)s"])
        AppShortcut(intent: ResumeAllDobbysIntent(), phrases: ["Resume \(.applicationName)", "Resume all \(.applicationName)s"])
        AppShortcut(intent: StopAllDobbysIntent(), phrases: ["Stop \(.applicationName)", "Stop all \(.applicationName)s"])
        AppShortcut(intent: DobbyStatusIntent(), phrases: ["What is \(.applicationName) doing", "\(.applicationName) status"])
    }
}
