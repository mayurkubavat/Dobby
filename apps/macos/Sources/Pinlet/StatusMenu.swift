import AppKit

/// The menu bar item. It only draws the `TopbarView` JSON the shared Pinlet
/// sends and sends the action keys back; the content itself (rows, wording,
/// colours) comes from pinlet/overlay/topbar.py so the Linux tray matches.
final class StatusMenu: NSObject, NSMenuDelegate {
    struct Callbacks {
        var action: (_ key: String, _ pid: Int?) -> Void
        var answer: (_ promptID: Int, _ answer: PromptAnswer) -> Void
        var restartEngine: () -> Void
        var openLog: () -> Void
        var openSettings: () -> Void
        var openWelcome: () -> Void
        var openActivity: () -> Void
        var quit: () -> Void
    }

    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()
    private let callbacks: Callbacks
    private var view = TopbarView.empty
    private var engineNote: String?
    private var waiting: [Prompt] = []
    private var pulse: Timer?

    init(callbacks: Callbacks) {
        self.callbacks = callbacks
        super.init()
        menu.delegate = self
        menu.autoenablesItems = false
        item.menu = menu
        item.button?.imagePosition = .imageLeading
        render()
    }

    /// New content from the shared Pinlet; `engineNote` replaces the subtitle
    /// when the engine itself is not running ("Starting…", a crash, setup).
    func update(view: TopbarView, engineNote: String?, waiting: [Prompt] = []) {
        let ids = waiting.map(\.id)
        guard view != self.view || engineNote != self.engineNote || ids != self.waiting.map(\.id) else { return }
        self.view = view
        self.engineNote = engineNote
        self.waiting = waiting
        render()
    }

    private func render() {
        if let button = item.button {
            let state = engineNote == nil && waiting.isEmpty ? view.iconState : "attention"
            button.image = Icons.statusIcon(state: state, palette: view.palette)
            button.title = view.iconTitle.isEmpty ? "" : " \(view.iconTitle)"
            button.toolTip = engineNote.map { "Pinlet: \($0)" } ?? view.tooltip
            button.setAccessibilityLabel(button.toolTip)
            setPulsing(state == "working")
        }
        rebuild()
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        rebuild()
    }

    // ---------------------------------------------------------------- items
    private func rebuild() {
        menu.removeAllItems()
        menu.addItem(header())
        if let note = engineNote, note.contains("not installed") {
            let install = item("Install Pinlet…", #selector(openWelcome))
            install.image = NSImage(systemSymbolName: "arrow.down.circle", accessibilityDescription: nil)
            menu.addItem(install)
        } else if let note = engineNote, StatusMenu.isProblem(note) {
            let details = item("Show Details…", #selector(openLog))
            details.image = NSImage(systemSymbolName: "doc.text.magnifyingglass", accessibilityDescription: nil)
            details.toolTip = note
            menu.addItem(details)
        }
        menu.addItem(.separator())
        addWaitingPrompts()
        if view.rows.isEmpty && engineNote == nil && view.hidden == 0 {
            let empty = NSMenuItem(title: "Connect an AI app such as Claude or Cursor to start", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            menu.addItem(empty)
        }
        for row in view.rows {
            menu.addItem(rowItem(row))
        }
        if !view.allActions.isEmpty {
            menu.addItem(.separator())
            for action in view.allActions {
                menu.addItem(actionItem(StatusMenu.menuTitle(action.label), key: action.key, pid: nil))
            }
        }
        menu.addItem(.separator())
        let activity = item("Activity…", #selector(openActivity), key: " ")
        activity.keyEquivalentModifierMask = [.control, .option]  // the global ⌃⌥Space, shown here
        activity.image = NSImage(systemSymbolName: "text.bubble", accessibilityDescription: nil)
        menu.addItem(activity)
        menu.addItem(.separator())
        addPermissionItems()
        // The menu's title already says "Pinlet": the items don't repeat it.
        // Open at Login and Done notifications live in Settings.
        menu.addItem(item("Settings…", #selector(openSettings), key: ","))
        let troubleshoot = NSMenuItem(title: "Troubleshoot", action: nil, keyEquivalent: "")
        let sub = NSMenu(title: "Troubleshoot")
        sub.addItem(item("Setup and Permissions…", #selector(openWelcome)))
        sub.addItem(item("Show Log", #selector(openLog)))
        sub.addItem(.separator())
        sub.addItem(item("Relaunch", #selector(restartEngine)))
        troubleshoot.submenu = sub
        menu.addItem(troubleshoot)
        menu.addItem(.separator())
        menu.addItem(item("Quit", #selector(quit), key: "q"))
    }

    /// A slow breathing of the menu bar icon while a Pinlet works (none with Reduce Motion).
    private func setPulsing(_ on: Bool) {
        let wanted = on && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        guard wanted != (pulse != nil) else { return }
        pulse?.invalidate()
        pulse = nil
        guard let button = item.button else { return }
        guard wanted else {
            button.alphaValue = 1
            return
        }
        var dim = true
        let t = Timer(timeInterval: 1.1, repeats: true) { [weak button] _ in
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 1.0
                button?.animator().alphaValue = dim ? 0.55 : 1
            }
            dim.toggle()
        }
        RunLoop.main.add(t, forMode: .common)
        pulse = t
    }

    /// Questions whose card ran out: the question, then Allow / Deny (and Always Allow for an app).
    private func addWaitingPrompts() {
        guard !waiting.isEmpty else { return }
        for prompt in waiting {
            let head = NSMenuItem(title: prompt.text, action: nil, keyEquivalent: "")
            head.isEnabled = false
            head.image = NSImage(systemSymbolName: prompt.isAccess ? "hand.raised.fill" : "exclamationmark.triangle.fill",
                                 accessibilityDescription: nil)
            let who = prompt.name.isEmpty ? "Pinlet" : prompt.name
            let mins = Int(prompt.holdLeft / 60)
            head.toolTip = "\(who) is waiting for you" + (mins > 0 ? " (Deny in \(mins) min)" : "")
            menu.addItem(head)
            menu.addItem(promptItem(prompt.allow, id: prompt.id, ok: true, always: false))
            if prompt.isAccess && !prompt.app.isEmpty {
                menu.addItem(promptItem("Always Allow in \(prompt.app)…", id: prompt.id, ok: true, always: true))
            }
            menu.addItem(promptItem(prompt.deny, id: prompt.id, ok: false, always: false))
        }
        menu.addItem(.separator())
    }

    private func promptItem(_ title: String, id: Int, ok: Bool, always: Bool) -> NSMenuItem {
        let item = NSMenuItem(title: "    " + title, action: #selector(answerPrompt(_:)), keyEquivalent: "")
        item.target = self
        item.representedObject = ["id": id, "ok": ok, "always": always] as [String: Any]
        return item
    }

    @objc private func answerPrompt(_ sender: NSMenuItem) {
        guard let info = sender.representedObject as? [String: Any], let id = info["id"] as? Int else { return }
        callbacks.answer(id, PromptAnswer(ok: info["ok"] as? Bool ?? false, always: info["always"] as? Bool ?? false))
    }

    private func item(_ title: String, _ action: Selector, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        return item
    }

    /// Menu items use title case on macOS ("Show 3 Hidden"); the shared
    /// labels from pinlet/overlay/topbar.py are in sentence case for GNOME.
    static func menuTitle(_ label: String) -> String {
        label.split(separator: " ").map { word in
            word.prefix(1).uppercased() + word.dropFirst()
        }.joined(separator: " ")
    }

    /// A menu-width version of an engine message: paths become their last
    /// part ("/Users/me/…/.venv/bin/python" → "python"), one line, at most
    /// 60 characters. The full message is the header's tooltip and in the log.
    static func shortNote(_ note: String) -> String {
        let firstLine = note.split(whereSeparator: \.isNewline).first.map(String.init) ?? note
        let words = firstLine.split(separator: " ", omittingEmptySubsequences: false).map { word -> String in
            guard word.contains("/") else { return String(word) }
            let trail = word.hasSuffix(":") ? ":" : ""
            let bare = word.hasSuffix(":") ? word.dropLast() : word[...]
            let last = (String(bare) as NSString).lastPathComponent
            return (last.isEmpty ? String(bare) : last) + trail
        }
        let short = words.joined(separator: " ")
        return short.count <= 60 ? short : String(short.prefix(59)) + "…"
    }

    /// An engine message that is a failure (not "Starting…" or "Connecting…").
    static func isProblem(_ note: String) -> Bool {
        let lower = note.lowercased()
        return lower.contains("could not") || lower.contains("stopped (exit") || lower.contains("error")
            || lower.contains("not found") || lower.contains("failed")
    }

    private func header() -> NSMenuItem {
        let text = NSMutableAttributedString(
            string: view.header,
            attributes: [.font: NSFont.systemFont(ofSize: NSFont.systemFontSize, weight: .semibold)])
        let sub = engineNote.map(StatusMenu.shortNote) ?? view.subtitle
        if !sub.isEmpty {
            text.append(NSAttributedString(
                string: "\n" + sub,
                attributes: [.font: NSFont.systemFont(ofSize: NSFont.smallSystemFontSize),
                             .foregroundColor: NSColor.secondaryLabelColor]))
        }
        let item = NSMenuItem(title: view.header, action: nil, keyEquivalent: "")
        item.attributedTitle = text
        item.isEnabled = false
        item.toolTip = engineNote  // the whole message, paths and all
        return item
    }

    private func rowItem(_ row: TopbarView.Row) -> NSMenuItem {
        let item = NSMenuItem(title: row.title, action: nil, keyEquivalent: "")
        item.view = RowView(row: row) { [weak self] key in
            self?.callbacks.action(key, row.pid)
        }
        item.setAccessibilityLabel("\(row.title), \(row.detail)")
        return item
    }

    private func actionItem(_ title: String, key: String, pid: Int?) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: #selector(runAction(_:)), keyEquivalent: "")
        item.target = self
        item.representedObject = ActionRef(key: key, pid: pid)
        let symbols = ["pause_toggle": title == "Resume" ? "play.fill" : "pause.fill", "stop": "stop.fill",
                       "pause_all": "pause.fill", "resume_all": "play.fill", "stop_all": "stop.fill",
                       "clear_finished": "checkmark.circle", "show_hidden": "eye", "wake": "sun.max.fill"]
        if let name = symbols[key] {
            item.image = NSImage(systemSymbolName: name, accessibilityDescription: title)
        }
        return item
    }

    private func addPermissionItems() {
        var added = false
        if !Permissions.screenRecording {
            let item = NSMenuItem(title: "Allow Screen Recording…", action: #selector(askScreen), keyEquivalent: "")
            item.target = self
            item.image = NSImage(systemSymbolName: "exclamationmark.triangle.fill", accessibilityDescription: nil)
            menu.addItem(item)
            added = true
        }
        if !Permissions.accessibility {
            let item = NSMenuItem(title: "Allow Accessibility…", action: #selector(askAccessibility), keyEquivalent: "")
            item.target = self
            item.image = NSImage(systemSymbolName: "exclamationmark.triangle.fill", accessibilityDescription: nil)
            menu.addItem(item)
            added = true
        }
        if added { menu.addItem(.separator()) }
    }

    // -------------------------------------------------------------- actions
    private final class ActionRef: NSObject {
        let key: String
        let pid: Int?
        init(key: String, pid: Int?) { self.key = key; self.pid = pid }
    }

    @objc private func runAction(_ sender: NSMenuItem) {
        guard let ref = sender.representedObject as? ActionRef else { return }
        callbacks.action(ref.key, ref.pid)
    }

    @objc private func askScreen() { Permissions.askScreenRecording() }
    @objc private func askAccessibility() { Permissions.askAccessibility() }
    @objc private func openLog() { callbacks.openLog() }
    @objc private func openSettings() { callbacks.openSettings() }
    @objc private func openWelcome() { callbacks.openWelcome() }
    @objc private func openActivity() { callbacks.openActivity() }
    @objc private func restartEngine() { callbacks.restartEngine() }
    @objc private func quit() { callbacks.quit() }
}
