import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let engine = Engine()
    private let control = ControlClient()
    private var menu: StatusMenu!
    private var timer: Timer?
    private var polling = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        menu = StatusMenu(callbacks: .init(
            action: { [weak self] key, pid in self?.send(key, pid: pid) },
            restartEngine: { [weak self] in self?.engine.restart() },
            openLog: { NSWorkspace.shared.open(DobbyPaths.daemonLog) },
            quit: { [weak self] in self?.quit() }))
        engine.onChange = { [weak self] _ in self?.poll() }
        engine.start()
        let t = Timer(timeInterval: 1.0, repeats: true) { [weak self] _ in self?.poll() }
        RunLoop.main.add(t, forMode: .common)  // keeps updating while the menu is open
        timer = t
        poll()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        timer?.invalidate()
        engine.stop { NSApp.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls {
            if let (key, pid) = DobbyURL.action(for: url) { send(key, pid: pid) }
        }
    }

    // --------------------------------------------------------------------
    private func note(for state: Engine.State) -> String? {
        switch state {
        case .notConfigured: return "Run `dobby` from an AI client once so Dobby knows its Python."
        case .starting: return "Starting… (the first start loads the models)"
        case .crashed(let why): return why
        case .stopped: return "Stopped"
        case .running, .external: return nil
        }
    }

    private func poll() {
        guard !polling else { return }
        polling = true
        let state = engine.state
        control.queue.async { [weak self] in
            guard let self else { return }
            let view = self.control.topbar()
            DispatchQueue.main.async {
                self.polling = false
                self.engine.tick(reachable: view != nil)
                // Reachable beats "starting": the socket answering means it is up.
                let note = view == nil ? (self.note(for: state) ?? "Connecting…") : nil
                self.menu.update(view: view ?? TopbarView.empty, engineNote: note)
            }
        }
    }

    private func send(_ key: String, pid: Int?) {
        control.queue.async { [weak self] in
            self?.control.action(key, pid: pid)
            DispatchQueue.main.async { self?.poll() }
        }
    }

    private func quit() {
        // A shared Dobby someone else started still gets stopped: Quit means "Dobby off".
        if case .external = engine.state {
            control.queue.async { [weak self] in
                _ = self?.control.request("quit")
                DispatchQueue.main.async { NSApp.terminate(nil) }
            }
        } else {
            NSApp.terminate(nil)
        }
    }
}
