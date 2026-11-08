import AppKit

/// The menu bar item. It only draws the `TopbarView` JSON the shared Dobby
/// sends and sends the action keys back; the content itself (rows, wording,
/// colours) comes from dobby/overlay/topbar.py so the Linux tray matches.
final class StatusMenu: NSObject, NSMenuDelegate {
    struct Callbacks {
        var action: (_ key: String, _ pid: Int?) -> Void
        var restartEngine: () -> Void
        var openLog: () -> Void
        var quit: () -> Void
    }

    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()
    private let callbacks: Callbacks
    private var view = TopbarView.empty
    private var engineNote: String?

    init(callbacks: Callbacks) {
        self.callbacks = callbacks
        super.init()
        menu.delegate = self
        menu.autoenablesItems = false
        item.menu = menu
        item.button?.imagePosition = .imageLeading
        render()
    }

    /// New content from the shared Dobby; `engineNote` replaces the subtitle
    /// when the engine itself is not running ("Starting…", a crash, setup).
    func update(view: TopbarView, engineNote: String?) {
        guard view != self.view || engineNote != self.engineNote else { return }
        self.view = view
        self.engineNote = engineNote
        render()
    }

    private func render() {
        if let button = item.button {
            let state = engineNote == nil ? view.iconState : "attention"
            button.image = Icons.statusIcon(state: state, palette: view.palette)
            button.title = view.iconTitle.isEmpty ? "" : " \(view.iconTitle)"
            button.toolTip = engineNote.map { "Dobby: \($0)" } ?? view.tooltip
            button.setAccessibilityLabel(button.toolTip)
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
        menu.addItem(.separator())
        if view.rows.isEmpty && engineNote == nil {
            let empty = NSMenuItem(title: "Add `dobby` to Claude, Antigravity or Cursor to start.", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            menu.addItem(empty)
        }
        for row in view.rows {
            menu.addItem(rowItem(row))
        }
        if !view.allActions.isEmpty {
            menu.addItem(.separator())
            for action in view.allActions {
                menu.addItem(actionItem(action.label, key: action.key, pid: nil))
            }
        }
        menu.addItem(.separator())
        addPermissionItems()
        let login = NSMenuItem(title: "Open at Login", action: #selector(toggleLogin), keyEquivalent: "")
        login.target = self
        login.state = LoginItem.enabled ? .on : .off
        menu.addItem(login)
        let done = NSMenuItem(title: "Notify When Done", action: #selector(toggleNotifyDone), keyEquivalent: "")
        done.target = self
        done.state = Notifier.notifyWhenDone ? .on : .off
        menu.addItem(done)
        let log = NSMenuItem(title: "Show Log", action: #selector(openLog), keyEquivalent: "")
        log.target = self
        menu.addItem(log)
        let restart = NSMenuItem(title: "Restart Dobby", action: #selector(restartEngine), keyEquivalent: "")
        restart.target = self
        menu.addItem(restart)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit Dobby", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
    }

    private func header() -> NSMenuItem {
        let text = NSMutableAttributedString(
            string: view.header,
            attributes: [.font: NSFont.systemFont(ofSize: NSFont.systemFontSize, weight: .semibold)])
        let sub = engineNote ?? view.subtitle
        if !sub.isEmpty {
            text.append(NSAttributedString(
                string: "\n" + sub,
                attributes: [.font: NSFont.systemFont(ofSize: NSFont.smallSystemFontSize),
                             .foregroundColor: NSColor.secondaryLabelColor]))
        }
        let item = NSMenuItem(title: view.header, action: nil, keyEquivalent: "")
        item.attributedTitle = text
        item.isEnabled = false
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
                       "clear_finished": "checkmark.circle", "show_hidden": "eye"]
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

    @objc private func toggleLogin() { LoginItem.set(!LoginItem.enabled) }
    @objc private func toggleNotifyDone() { Notifier.notifyWhenDone.toggle() }
    @objc private func askScreen() { Permissions.askScreenRecording() }
    @objc private func askAccessibility() { Permissions.askAccessibility() }
    @objc private func openLog() { callbacks.openLog() }
    @objc private func restartEngine() { callbacks.restartEngine() }
    @objc private func quit() { callbacks.quit() }
}
