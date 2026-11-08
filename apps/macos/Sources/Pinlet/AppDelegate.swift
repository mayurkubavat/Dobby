import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let engine = Engine()
    private let control = ControlClient()
    private var menu: StatusMenu!
    private let promptPanel = PromptPanel()
    private let notifier = Notifier()
    private let settingsWindow = SettingsWindowController()
    private let welcome = WelcomeWindowController()
    private var timer: Timer?
    private var polling = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        menu = StatusMenu(callbacks: .init(
            action: { [weak self] key, pid in self?.send(key, pid: pid) },
            restartEngine: { [weak self] in self?.engine.restart() },
            openLog: { NSWorkspace.shared.open(PinletPaths.daemonLog) },
            openSettings: { [weak self] in self?.settingsWindow.show() },
            openWelcome: { [weak self] in self?.welcome.show() },
            quit: { [weak self] in self?.quit() }))
        promptPanel.onAnswer = { [weak self] id, ok in self?.answer(id, ok: ok) }
        notifier.onAnswer = { [weak self] id, ok in self?.answer(id, ok: ok) }
        notifier.onOpenPrompt = { [weak self] in self?.promptPanel.focus() }
        notifier.start()
        settingsWindow.restartEngine = { [weak self] in self?.engine.restart() }
        welcome.showIfNeeded()
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
            if let (key, pid) = PinletURL.action(for: url) { send(key, pid: pid) }
        }
    }

    // --------------------------------------------------------------------
    private func note(for state: Engine.State) -> String? {
        switch state {
        case .notConfigured: return "Run `pinlet` from an AI client once so Pinlet knows its Python."
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
            let reply = self.control.poll()
            DispatchQueue.main.async {
                self.polling = false
                let view = reply?.view
                self.engine.tick(reachable: view != nil)
                // Reachable beats "starting": the socket answering means it is up.
                let note = view == nil ? (self.note(for: state) ?? "Connecting…") : nil
                self.menu.update(view: view ?? TopbarView.empty, engineNote: note)
                let prompts = reply?.prompts ?? []
                // One card at a time, the oldest question first; the rest wait their turn.
                self.promptPanel.update(prompts.first, palette: view?.palette ?? [:])
                self.notifier.update(prompts: prompts, rows: view?.rows ?? [])
            }
        }
    }

    private func answer(_ promptID: Int, ok: Bool) {
        if promptPanel.shown?.id == promptID { promptPanel.hide() }
        control.queue.async { [weak self] in
            self?.control.answer(promptID, ok: ok)
            DispatchQueue.main.async { self?.poll() }
        }
    }

    private func send(_ key: String, pid: Int?) {
        control.queue.async { [weak self] in
            self?.control.action(key, pid: pid)
            DispatchQueue.main.async { self?.poll() }
        }
    }

    private func quit() {
        // A shared Pinlet someone else started still gets stopped: Quit means "Pinlet off".
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
