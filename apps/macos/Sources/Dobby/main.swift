import AppKit

// `Dobby --icon <dir>` writes the app icon PNGs (used by build.sh).
let args = CommandLine.arguments
if args.count >= 3 && args[1] == "--icon" {
    IconExport.write(to: URL(fileURLWithPath: args[2]))
    exit(0)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // menu bar only, no Dock icon
app.run()
