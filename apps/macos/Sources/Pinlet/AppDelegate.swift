import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let engine = Engine()
    private let control = ControlClient()
    private var menu: StatusMenu!
    private let promptPanel = PromptPanel()
    private let notifier = Notifier()
    private let settingsWindow = SettingsWindowController()
    private let welcome = WelcomeWindowController()
    private let activity = ActivityPanel()
    private var hotKey: HotKey?
    private var timer: Timer?
    private var polling = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        menu = StatusMenu(callbacks: .init(
            action: { [weak self] key, pid in self?.send(key, pid: pid) },
            answer: { [weak self] id, ok, always in self?.answer(id, ok: ok, always: always) },
            restartEngine: { [weak self] in self?.engine.restart() },
            openLog: { NSWorkspace.shared.open(PinletPaths.daemonLog) },
            openSettings: { [weak self] in self?.settingsWindow.show() },
            openWelcome: { [weak self] in self?.welcome.show() },
            openActivity: { [weak self] in self?.activity.show() },
            quit: { [weak self] in self?.quit() }))
        activity.onAction = { [weak self] key, pid in self?.send(key, pid: pid) }
        activity.onTell = { [weak self] pid, text, done in self?.tell(pid, text, done: done) }
        activity.onFetchFeed = { [weak self] done in
            self?.control.queue.async { [weak self] in
                let reply = self?.control.activity()
                DispatchQueue.main.async { done(reply) }
            }
        }
        hotKey = HotKey { [weak self] in self?.activity.toggle() }
        NotificationCenter.default.addObserver(forName: ActivityPanel.showRequest, object: nil, queue: .main) {
            [weak self] _ in self?.activity.show()
        }
        promptPanel.onAnswer = { [weak self] id, ok, always, reason in
            self?.answer(id, ok: ok, always: always, reason: reason)
        }
        notifier.onAnswer = { [weak self] id, ok in self?.answer(id, ok: ok) }
        notifier.onOpenPrompt = { [weak self] in self?.promptPanel.focus() }
        notifier.start()
        settingsWindow.restartEngine = { [weak self] in self?.engine.restart() }
        installMenu()
        RuntimeInstaller.shared.onFinished.append { [weak self] in
            self?.engine.restart()
            AIToolsModel.shared.refresh()
        }
        welcome.showIfNeeded()
        // This app carries a newer Pinlet than its runtime: update it quietly, then restart.
        if RuntimePaths.updateAvailable, EngineConfig.resolve()?.isManaged == true {
            RuntimeInstaller.shared.install()
        }
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
            if let (key, pid) = PinletURL.action(for: url) {
                send(key, pid: pid)
                continue
            }
            switch PinletURL.window(for: url) {  // pinlet://settings, pinlet://welcome, pinlet://activity
            case "settings": settingsWindow.show()
            case "welcome": welcome.show()
            case "activity": activity.show()
            default: break
            }
        }
    }

    /// A main menu: menu bar apps have none, so Cmd+, and Cmd+V in text fields did nothing.
    private func installMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "Pinlet")
        let settings = NSMenuItem(title: "Settings…", action: #selector(openSettings), keyEquivalent: ",")
        settings.target = self
        appMenu.addItem(settings)
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w"))
        appMenu.addItem(NSMenuItem(title: "Quit Pinlet", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appItem.submenu = appMenu
        main.addItem(appItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(NSMenuItem(title: "Undo", action: Selector(("undo:")), keyEquivalent: "z"))
        let redo = NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(redo)
        edit.addItem(.separator())
        edit.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        edit.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        edit.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        edit.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        editItem.submenu = edit
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    @objc private func openSettings() { settingsWindow.show() }

    // --------------------------------------------------------------------
    private func note(for state: Engine.State) -> String? {
        switch state {
        case .notConfigured:
            return RuntimeInstaller.shared.running ? "Installing Pinlet…" : "Pinlet is not installed yet. Open Welcome to install it."
        case .starting: return "Starting… (the first start loads the models)"
        case .crashed(let why): return why
        case .stopped: return "Stopped"
        case .running, .external: return nil
        }
    }

    /// Questions whose "Always allow" is waiting for Touch ID.
    private var confirming = Set<Int>()
    /// The open questions as of the last poll (to name the app in the Touch ID sheet).
    private var lastPrompts: [Prompt] = []

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
                let prompts = reply?.prompts ?? []
                // A question whose card ran out keeps waiting in the menu, with Allow / Deny there.
                self.menu.update(view: view ?? TopbarView.empty, engineNote: note,
                                 waiting: prompts.filter(\.missed))
                self.activity.update(view ?? TopbarView.empty, reachable: view != nil)
                // One card at a time, the oldest question first; the rest wait their turn.
                self.lastPrompts = prompts
                self.confirming.formIntersection(prompts.map(\.id))
                self.promptPanel.update(prompts.first(where: { !$0.missed && !self.confirming.contains($0.id) }),
                                        palette: view?.palette ?? [:])
                self.notifier.update(prompts: prompts, rows: view?.rows ?? [])
            }
        }
    }

    private func answer(_ promptID: Int, ok: Bool, always: Bool = false, reason: String = "") {
        if ok && always && !confirming.contains(promptID) {
            // A standing permission takes proof of a person (Touch ID). Until macOS
            // answers, the question stays open and its card stays put away.
            let app = lastPrompts.first(where: { $0.id == promptID })?.app ?? ""
            confirming.insert(promptID)
            promptPanel.hide()
            PersonCheck.confirm("always allow Pinlet to use \(app.isEmpty ? "this app" : app)") { [weak self] passed in
                guard let self else { return }
                if passed {
                    self.answer(promptID, ok: true, always: true, reason: reason)
                } else {
                    // Cancelled or failed: nothing is saved and the question is asked again.
                    self.confirming.remove(promptID)
                    self.poll()
                }
            }
            return
        }
        confirming.remove(promptID)
        if promptPanel.shown?.id == promptID { promptPanel.hide() }
        control.queue.async { [weak self] in
            self?.control.answer(promptID, ok: ok, always: always, reason: reason)
            DispatchQueue.main.async { self?.poll() }
        }
    }

    private func tell(_ pid: Int, _ text: String, done: @escaping ([String: Any]?) -> Void) {
        control.queue.async { [weak self] in
            let reply = self?.control.tell(pid: pid, text: text)
            DispatchQueue.main.async {
                done(reply)
                self?.poll()
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
