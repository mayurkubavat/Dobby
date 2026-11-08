import AppKit

extension NSColor {
    /// "#0E7490" → NSColor (sRGB). Bad input gives Pinlet cyan.
    convenience init(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        var v: UInt64 = 0x0EA5C6
        if s.count == 6, let parsed = UInt64(s, radix: 16) { v = parsed }
        self.init(srgbRed: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
                  blue: CGFloat(v & 0xFF) / 255, alpha: 1)
    }
}

/// Palette "Frost" (same values as pinlet/overlay/topbar.py).
enum Palette {
    static func color(_ key: String, in palette: [String: String], fallback: String) -> NSColor {
        NSColor(hex: palette[key] ?? fallback)
    }
    static let accent = "#0EA5C6"
    static let fill = "#0E7490"
    static let ok = "#22A365"
    static let caution = "#F5A524"
    static let danger = "#F04438"
}

enum Icons {
    // Pinlet's mark: a macOS pointer with a spark at its tip ("it uses your
    // computer, with a bit of magic"). Coordinates are in an 18 x 18 box,
    // y down, like the SVG in the icon study.
    static func pointer(scale k: CGFloat, dx: CGFloat = 0, dy: CGFloat = 0) -> NSBezierPath {
        let pts: [(CGFloat, CGFloat)] = [(3.6, 2.6), (3.6, 14.2), (6.5, 11.4), (8.6, 15.9), (10.7, 15.0), (8.7, 10.6), (12.8, 10.6)]
        let path = NSBezierPath()
        for (i, p) in pts.enumerated() {
            let pt = NSPoint(x: dx + p.0 * k, y: dy + p.1 * k)
            if i == 0 { path.move(to: pt) } else { path.line(to: pt) }
        }
        path.close()
        path.lineJoinStyle = .round
        return path
    }

    /// A four-point spark: centre (cx, cy), half-size r, pinched by quadratic curves.
    static func spark(cx: CGFloat, cy: CGFloat, r: CGFloat, pinch: CGFloat = 0.16) -> NSBezierPath {
        let q = r * pinch
        let tips = [NSPoint(x: cx, y: cy - r), NSPoint(x: cx + r, y: cy), NSPoint(x: cx, y: cy + r), NSPoint(x: cx - r, y: cy)]
        let ctrls = [NSPoint(x: cx + q, y: cy - q), NSPoint(x: cx + q, y: cy + q), NSPoint(x: cx - q, y: cy + q), NSPoint(x: cx - q, y: cy - q)]
        let path = NSBezierPath()
        path.move(to: tips[0])
        for i in 0..<4 {
            let p0 = tips[i], c = ctrls[i], p1 = tips[(i + 1) % 4]
            // quadratic -> cubic
            path.curve(to: p1,
                       controlPoint1: NSPoint(x: p0.x + 2 / 3 * (c.x - p0.x), y: p0.y + 2 / 3 * (c.y - p0.y)),
                       controlPoint2: NSPoint(x: p1.x + 2 / 3 * (c.x - p1.x), y: p1.y + 2 / 3 * (c.y - p1.y)))
        }
        path.close()
        return path
    }

    /// The menu bar icon. Idle is a template (macOS tints it for light and
    /// dark bars); working adds a green dot and attention an amber one.
    static func statusIcon(state: String, palette: [String: String]) -> NSImage {
        let size = NSSize(width: 18, height: 18)
        let dot: NSColor? = state == "working" ? Palette.color("ok", in: palette, fallback: Palette.ok)
            : state == "attention" ? Palette.color("caution", in: palette, fallback: Palette.caution) : nil
        let image = NSImage(size: size, flipped: true) { _ in
            guard let ctx = NSGraphicsContext.current else { return false }
            ctx.cgContext.beginTransparencyLayer(auxiliaryInfo: nil)
            let ink = dot == nil ? NSColor.black : NSColor.labelColor
            ink.setFill()
            ink.setStroke()
            let arrow = pointer(scale: 1)
            arrow.lineWidth = 0.9
            arrow.fill()
            arrow.stroke()
            spark(cx: 14, cy: 3.8, r: 2.9).fill()
            if let dot {
                let c = NSPoint(x: 14.6, y: 14.4), r: CGFloat = 3.0
                ctx.compositingOperation = .clear
                NSBezierPath(ovalIn: NSRect(x: c.x - r - 1.2, y: c.y - r - 1.2, width: (r + 1.2) * 2, height: (r + 1.2) * 2)).fill()
                ctx.compositingOperation = .sourceOver
                dot.setFill()
                NSBezierPath(ovalIn: NSRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)).fill()
            }
            ctx.cgContext.endTransparencyLayer()
            return true
        }
        image.isTemplate = dot == nil
        image.accessibilityDescription = "Pinlet"
        return image
    }

    /// Row icons: a rounded square in the row's colour with a white glyph (the
    /// squarer look of the Claude app, rather than Focus's circles).
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
            NSBezierPath(roundedRect: rect, xRadius: rect.width * 0.28, yRadius: rect.height * 0.28).fill()
            if let glyph {
                let g = glyph.size
                glyph.draw(in: NSRect(x: rect.midX - g.width / 2, y: rect.midY - g.height / 2, width: g.width, height: g.height))
            }
            return true
        }
        image.isTemplate = false
        return image
    }

    /// The app icon (Pinlet.icns is generated from this by build.sh), "Frost":
    /// the macOS icon grid (824 of 1024 with a soft shadow), a white frosted
    /// tile with a bright rim, a pearl pointer and a cyan star.
    static func appIcon(size: CGFloat) -> NSImage {
        NSImage(size: NSSize(width: size, height: size), flipped: true) { _ in
            guard let ctx = NSGraphicsContext.current else { return false }
            let u = size / 1024
            let tile = NSRect(x: 100 * u, y: 100 * u, width: 824 * u, height: 824 * u)
            let shape = NSBezierPath(roundedRect: tile, xRadius: 186 * u, yRadius: 186 * u)
            // tile with a drop shadow
            NSGraphicsContext.saveGraphicsState()
            let shadow = NSShadow()
            shadow.shadowColor = NSColor(hex: "#0B1220").withAlphaComponent(0.18)
            shadow.shadowBlurRadius = 24 * u
            shadow.shadowOffset = NSSize(width: 0, height: 10 * u)  // flipped: +y is down
            shadow.set()
            NSColor(hex: "#DCE5EE").setFill()
            shape.fill()
            NSGraphicsContext.restoreGraphicsState()
            NSGradient(starting: NSColor.white, ending: NSColor(hex: "#DCE5EE"))?.draw(in: shape, angle: 90)
            NSGradient(colors: [NSColor.white.withAlphaComponent(0.55), NSColor.white.withAlphaComponent(0)],
                       atLocations: [0, 0.45], colorSpace: .sRGB)?.draw(in: shape, angle: 90)
            // frosted-glass edge: a bright inner rim and a faint outer hairline
            let rim = NSBezierPath(roundedRect: tile.insetBy(dx: 4 * u, dy: 4 * u), xRadius: 182 * u, yRadius: 182 * u)
            rim.lineWidth = 8 * u
            NSColor.white.withAlphaComponent(0.9).setStroke()
            rim.stroke()
            shape.lineWidth = 2 * u
            NSColor(hex: "#0B1220").withAlphaComponent(0.10).setStroke()
            shape.stroke()
            // pointer and star, lifted with a soft cool shadow
            NSGraphicsContext.saveGraphicsState()
            let lift = NSShadow()
            lift.shadowColor = NSColor(hex: "#0B2A40").withAlphaComponent(0.28)
            lift.shadowBlurRadius = 28 * u
            lift.shadowOffset = NSSize(width: 0, height: 14 * u)
            lift.set()
            ctx.cgContext.beginTransparencyLayer(auxiliaryInfo: nil)
            let k = 1024 / 18 * u * 0.72          // the 18-pt pointer, scaled into the tile
            let arrow = pointer(scale: k, dx: 225 * u, dy: 156 * u)
            arrow.lineWidth = 50 * u                  // a thin cool edge around the pearl
            NSColor(hex: "#8EA6BA").setStroke()
            arrow.stroke()
            arrow.lineWidth = 34 * u                  // round the corners, as before
            NSColor(hex: "#F4F8FB").setStroke()
            arrow.stroke()
            NSGradient(starting: NSColor.white, ending: NSColor(hex: "#CFE0EE"))?.draw(in: arrow, angle: 90)
            let star = spark(cx: 722 * u, cy: 318 * u, r: 122 * u)
            NSGradient(starting: NSColor(hex: "#8BEAFB"), ending: NSColor(hex: "#0EA5C6"))?.draw(in: star, angle: 45)
            ctx.cgContext.endTransparencyLayer()
            NSGraphicsContext.restoreGraphicsState()
            return true
        }
    }
}
