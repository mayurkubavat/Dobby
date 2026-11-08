import AppKit

/// The menu bar item. It only draws the `TopbarView` JSON the shared Pinlet
/// sends and sends the action keys back; the content itself (rows, wording,
/// colours) comes from pinlet/overlay/topbar.py so the Linux tray matches.
final class StatusMenu: NSObject, NSMenuDelegate {
    struct Callbacks {
        var action: (_ key: String, _ pid: Int?) -> Void
        var restartEngine: () -> Void
        var openLog: () -> Void
        var openSettings: () -> Void
        var openWelcome: () -> Void
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

    /// New content from the shared Pinlet; `engineNote` replaces the subtitle
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
            button.toolTip = engineNote.map { "Pinlet: \($0)" } ?? view.tooltip
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
        addPermissionItems()
        // The menu's title already says "Pinlet": the items don't repeat it.
        // Open at Login and Done notifications live in Settings.
        menu.addItem(item("Settings…", #selector(openSettings), key: ","))
        let troubleshoot = NSMenuItem(title: "Troubleshoot", action: nil, keyEquivalent: "")
        let sub = NSMenu(title: "Troubleshoot")
        sub.addItem(item("Check Permissions…", #selector(openWelcome)))
        sub.addItem(item("Show Log", #selector(openLog)))
        sub.addItem(.separator())
        sub.addItem(item("Relaunch", #selector(restartEngine)))
        troubleshoot.submenu = sub
        menu.addItem(troubleshoot)
        menu.addItem(.separator())
        menu.addItem(item("Quit", #selector(quit), key: "q"))
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

    @objc private func askScreen() { Permissions.askScreenRecording() }
    @objc private func askAccessibility() { Permissions.askAccessibility() }
    @objc private func openLog() { callbacks.openLog() }
    @objc private func openSettings() { callbacks.openSettings() }
    @objc private func openWelcome() { callbacks.openWelcome() }
    @objc private func restartEngine() { callbacks.restartEngine() }
    @objc private func quit() { callbacks.quit() }
}
