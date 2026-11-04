import AppKit

/// One Dobby in the menu: its icon, name and what it is doing, with round
/// Pause/Resume and Stop buttons on the right, so there is no submenu.
final class RowView: NSView {
    static let width: CGFloat = 320
    static let height: CGFloat = 46

    private let onAction: (String) -> Void

    init(row: TopbarView.Row, onAction: @escaping (String) -> Void) {
        self.onAction = onAction
        super.init(frame: NSRect(x: 0, y: 0, width: RowView.width, height: RowView.height))
        autoresizingMask = [.width]

        let icon = NSImageView(image: Icons.rowIcon(color: row.color, shape: row.shape))
        icon.setAccessibilityElement(false)

        let title = NSTextField(labelWithString: row.title)
        title.font = NSFont.menuFont(ofSize: 0)
        title.textColor = .labelColor
        title.lineBreakMode = .byTruncatingTail
        let detail = NSTextField(labelWithString: row.detail)
        detail.font = NSFont.systemFont(ofSize: NSFont.smallSystemFontSize)
        detail.textColor = .secondaryLabelColor
        detail.lineBreakMode = .byTruncatingTail
        let text = NSStackView(views: [title, detail])
        text.orientation = .vertical
        text.alignment = .leading
        text.spacing = 1
        text.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

        var buttons: [NSView] = []
        for action in row.actions {
            buttons.append(CircleButton(action: action) { [weak self] key in self?.onAction(key) })
        }
        let controls = NSStackView(views: buttons)
        controls.orientation = .horizontal
        controls.spacing = 6

        let line = NSStackView(views: [icon, text, controls])
        line.orientation = .horizontal
        line.alignment = .centerY
        line.spacing = 10
        line.edgeInsets = NSEdgeInsets(top: 0, left: 14, bottom: 0, right: 12)
        line.setHuggingPriority(.defaultLow, for: .horizontal)
        line.translatesAutoresizingMaskIntoConstraints = false
        addSubview(line)
        NSLayoutConstraint.activate([
            line.leadingAnchor.constraint(equalTo: leadingAnchor),
            line.trailingAnchor.constraint(equalTo: trailingAnchor),
            line.topAnchor.constraint(equalTo: topAnchor),
            line.bottomAnchor.constraint(equalTo: bottomAnchor),
            icon.widthAnchor.constraint(equalToConstant: 26),
            icon.heightAnchor.constraint(equalToConstant: 26),
        ])
    }

    required init?(coder: NSCoder) { fatalError("not used") }
}

/// A 26 pt round button with an SF Symbol: Pause/Resume, or Stop (red on hover).
final class CircleButton: NSButton {
    private let key: String
    private let handler: (String) -> Void
    private var hovering = false { didSet { needsDisplay = true } }
    private var isStop: Bool { key.hasPrefix("stop") }

    init(action: TopbarView.Action, handler: @escaping (String) -> Void) {
        key = action.key
        self.handler = handler
        super.init(frame: NSRect(x: 0, y: 0, width: 26, height: 26))
        let resume = action.label.lowercased().hasPrefix("resume")
        let symbol = isStop ? "stop.fill" : (resume ? "play.fill" : "pause.fill")
        image = NSImage(systemSymbolName: symbol, accessibilityDescription: action.label)?
            .withSymbolConfiguration(.init(pointSize: 10, weight: .bold))
        imagePosition = .imageOnly
        isBordered = false
        title = ""
        toolTip = action.label
        setAccessibilityLabel(action.label)
        target = self
        self.action = #selector(fire)
        translatesAutoresizingMaskIntoConstraints = false
        widthAnchor.constraint(equalToConstant: 26).isActive = true
        heightAnchor.constraint(equalToConstant: 26).isActive = true
        addTrackingArea(NSTrackingArea(rect: .zero, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                       owner: self, userInfo: nil))
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func mouseEntered(with event: NSEvent) { hovering = true }
    override func mouseExited(with event: NSEvent) { hovering = false }

    override func draw(_ dirtyRect: NSRect) {
        let circle = NSBezierPath(ovalIn: bounds.insetBy(dx: 0.5, dy: 0.5))
        let fill: NSColor
        if hovering {
            fill = isStop ? NSColor(hex: Palette.danger) : NSColor(hex: Palette.fill)
        } else {
            fill = NSColor.labelColor.withAlphaComponent(0.09)
        }
        fill.setFill()
        circle.fill()
        guard let image else { return }
        let ink: NSColor = hovering ? .white : .labelColor
        let glyph = image.withSymbolConfiguration(.init(paletteColors: [ink])) ?? image
        let s = glyph.size
        glyph.draw(in: NSRect(x: bounds.midX - s.width / 2, y: bounds.midY - s.height / 2, width: s.width, height: s.height))
    }

    @objc private func fire() { handler(key) }
}
