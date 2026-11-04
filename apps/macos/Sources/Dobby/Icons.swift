import AppKit

extension NSColor {
    /// "#5B3FE0" → NSColor (sRGB). Bad input gives Dobby violet.
    convenience init(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        var v: UInt64 = 0x7C5CFF
        if s.count == 6, let parsed = UInt64(s, radix: 16) { v = parsed }
        self.init(srgbRed: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
                  blue: CGFloat(v & 0xFF) / 255, alpha: 1)
    }
}

/// Palette A, "Elf Violet, tuned" (same values as dobby/overlay/topbar.py).
enum Palette {
    static func color(_ key: String, in palette: [String: String], fallback: String) -> NSColor {
        NSColor(hex: palette[key] ?? fallback)
    }
    static let accent = "#7C5CFF"
    static let fill = "#5B3FE0"
    static let ok = "#22A365"
    static let caution = "#F5A524"
    static let danger = "#F04438"
}

enum Icons {
    /// Dobby's mark: a rounded head with two long pointed ears, eyes cut out.
    static func mark(in rect: NSRect) -> NSBezierPath {
        let cx = rect.midX, s = min(rect.width, rect.height)
        // AppKit is y-up: the Python drawing's y grows downward, so flip it.
        let cy = rect.midY - s * 0.05
        let r = s * 0.27
        let path = NSBezierPath()
        func p(_ dx: CGFloat, _ dy: CGFloat) -> NSPoint { NSPoint(x: cx + dx * r, y: cy - dy * r) }
        for side: CGFloat in [-1, 1] {
            path.move(to: p(side * 0.55, -0.25))
            path.line(to: p(side * 1.55, -0.75))
            path.line(to: p(side * 0.75, 0.35))
            path.close()
        }
        path.appendOval(in: NSRect(x: cx - r * 0.82, y: cy - r * 0.95, width: r * 1.64, height: r * 1.85))
        return path
    }

    static func eyes(in rect: NSRect) -> NSBezierPath {
        let s = min(rect.width, rect.height)
        let er = s * 0.05
        let y = rect.midY
        let path = NSBezierPath()
        for x in [rect.minX + s * 0.42, rect.minX + s * 0.58] {
            path.appendOval(in: NSRect(x: x - er, y: y - er, width: er * 2, height: er * 2))
        }
        return path
    }

    /// The menu bar icon. Idle is a template (macOS tints it); working adds a
    /// green dot and attention an amber one.
    static func statusIcon(state: String, palette: [String: String]) -> NSImage {
        let size = NSSize(width: 20, height: 18)
        let dot: NSColor? = state == "working" ? Palette.color("ok", in: palette, fallback: Palette.ok)
            : state == "attention" ? Palette.color("caution", in: palette, fallback: Palette.caution) : nil
        let image = NSImage(size: size, flipped: false) { rect in
            let body = NSRect(x: 0, y: 0, width: rect.height, height: rect.height)
            NSGraphicsContext.current?.cgContext.beginTransparencyLayer(auxiliaryInfo: nil)
            (dot == nil ? NSColor.black : NSColor.labelColor).setFill()
            mark(in: body).fill()
            NSGraphicsContext.current?.compositingOperation = .clear
            eyes(in: body).fill()
            if let dot {
                let r: CGFloat = 3.6
                let c = NSPoint(x: body.maxX - 2.5, y: body.minY + 4)
                NSBezierPath(ovalIn: NSRect(x: c.x - r - 1.2, y: c.y - r - 1.2, width: (r + 1.2) * 2, height: (r + 1.2) * 2)).fill()
                NSGraphicsContext.current?.compositingOperation = .sourceOver
                dot.setFill()
                NSBezierPath(ovalIn: NSRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)).fill()
            }
            NSGraphicsContext.current?.cgContext.endTransparencyLayer()
            return true
        }
        image.isTemplate = dot == nil
        image.accessibilityDescription = "Dobby"
        return image
    }

    /// Row icons: a filled circle in the row's colour with a white glyph, like
    /// the Focus menu.
    static let glyphs: [String: String] = [
        "spark": "sparkles", "moon": "moon.fill", "pause": "pause.fill", "alert": "exclamationmark",
        "hand": "hand.raised.fill", "stop": "stop.fill", "clock": "clock.fill",
    ]

    static func rowIcon(color: String, shape: String, diameter: CGFloat = 26) -> NSImage {
        let size = NSSize(width: diameter, height: diameter)
        let fill = NSColor(hex: color)
        let symbolName = glyphs[shape] ?? "circle.fill"
        let config = NSImage.SymbolConfiguration(pointSize: diameter * 0.46, weight: .semibold)
            .applying(.init(paletteColors: [.white]))
        let glyph = NSImage(systemSymbolName: symbolName, accessibilityDescription: shape)?
            .withSymbolConfiguration(config)
        let image = NSImage(size: size, flipped: false) { rect in
            fill.setFill()
            NSBezierPath(ovalIn: rect).fill()
            if let glyph {
                let g = glyph.size
                glyph.draw(in: NSRect(x: rect.midX - g.width / 2, y: rect.midY - g.height / 2, width: g.width, height: g.height))
            }
            return true
        }
        image.isTemplate = false
        return image
    }

    /// The app icon (Dobby.icns is generated from this by build.sh).
    static func appIcon(size: CGFloat) -> NSImage {
        NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
            let inset = rect.insetBy(dx: size * 0.1, dy: size * 0.1)
            let bg = NSBezierPath(roundedRect: inset, xRadius: size * 0.2, yRadius: size * 0.2)
            NSGradient(starting: NSColor(hex: "#7C5CFF"), ending: NSColor(hex: "#4A2FC8"))?.draw(in: bg, angle: -90)
            NSGraphicsContext.current?.cgContext.beginTransparencyLayer(auxiliaryInfo: nil)
            NSColor.white.setFill()
            let markRect = inset.insetBy(dx: size * 0.08, dy: size * 0.08)
            mark(in: markRect).fill()
            NSGraphicsContext.current?.compositingOperation = .clear
            eyes(in: markRect).fill()
            NSGraphicsContext.current?.cgContext.endTransparencyLayer()
            return true
        }
    }
}
