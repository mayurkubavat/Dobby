import AppKit
import ApplicationServices
import CoreGraphics
import ServiceManagement

/// The two macOS permissions Dobby needs. The engine runs as this app's
/// child, so what is granted to Dobby.app is what the engine gets.
enum Permissions {
    static var accessibility: Bool { AXIsProcessTrusted() }
    static var screenRecording: Bool { CGPreflightScreenCaptureAccess() }
    static var allGranted: Bool { accessibility && screenRecording }

    static func askAccessibility() {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        _ = AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
        open("Privacy_Accessibility")
    }

    static func askScreenRecording() {
        if !CGRequestScreenCaptureAccess() {
            open("Privacy_ScreenCapture")
        }
    }

    static func open(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") {
            NSWorkspace.shared.open(url)
        }
    }
}

/// "Open at Login", through the system's own Login Items list.
enum LoginItem {
    static var enabled: Bool { SMAppService.mainApp.status == .enabled }

    static func set(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            NSLog("Dobby: login item change failed: \(error.localizedDescription)")
        }
    }
}
