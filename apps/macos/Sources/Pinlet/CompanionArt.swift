import AppKit

/// Draws Rime, Pinlet's companion: a small frost sprite with a crystal tuft.
/// An original character in Pinlet's own Frost colours.
///
/// Everything is drawn in a 200 x 220 box with y down (the web prototype's
/// coordinates plus 20 of headroom for the jump), from a pose and the
/// opacity of each prop. No images: it stays sharp at any size.
enum CompanionArt {
    static let box = CGSize(width: 200, height: 220)

    private static func rgb(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
        CGColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
    }

    private static let body1: UInt32 = 0xFFFFFF
    private static let body2: UInt32 = 0xC9ECF6
    private static let edge: UInt32 = 0x58B4CC
    private static let ink: UInt32 = 0x13273A
    private static let ice: UInt32 = 0x6FE0F4
    private static let ice2: UInt32 = 0xB8F3FC
    private static let blush: UInt32 = 0xB3A4F2
    private static let amber: UInt32 = 0xFFD48A

    private static func oval(_ cx: Double, _ cy: Double, _ rx: Double, _ ry: Double) -> CGRect {
        CGRect(x: cx - rx, y: cy - ry, width: rx * 2, height: ry * 2)
    }

    private static func radial(_ ctx: CGContext, from inner: CGColor, to outer: CGColor,
                               center: CGPoint, radius: CGFloat) {
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let gradient = CGGradient(colorsSpace: space, colors: [inner, outer] as CFArray, locations: [0, 1])
        else { return }
        ctx.drawRadialGradient(gradient, startCenter: center, startRadius: 0, endCenter: center, endRadius: radius,
                               options: [.drawsAfterEndLocation])
    }

    private static func rotate(_ ctx: CGContext, degrees: Double, about p: CGPoint) {
        ctx.translateBy(x: p.x, y: p.y)
        ctx.rotate(by: CGFloat(degrees * .pi / 180))
        ctx.translateBy(x: -p.x, y: -p.y)
    }

    private static func polygon(_ ctx: CGContext, _ pts: [(Double, Double)], fill: UInt32) {
        ctx.beginPath()
        for (i, p) in pts.enumerated() {
            let pt = CGPoint(x: p.0, y: p.1)
            if i == 0 { ctx.move(to: pt) } else { ctx.addLine(to: pt) }
        }
        ctx.closePath()
        ctx.setFillColor(rgb(fill))
        ctx.setStrokeColor(rgb(edge))
        ctx.setLineWidth(1.5)
        ctx.setLineJoin(.round)
        ctx.drawPath(using: .fillStroke)
    }

    private static func limb(_ ctx: CGContext, _ rect: CGRect) {
        ctx.setFillColor(rgb(body2))
        ctx.setStrokeColor(rgb(edge))
        ctx.setLineWidth(2)
        ctx.addEllipse(in: rect)
        ctx.drawPath(using: .fillStroke)
    }

    /// `ctx` must already map the 200 x 220 box, y down, onto the target.
    static func draw(in ctx: CGContext, pose: CompanionPose, props: [Double], time: Double) {
        ctx.saveGState()
        ctx.translateBy(x: 0, y: 20)

        // Ground shadow: smaller while in the air.
        let lift = max(0, -pose[.y])
        ctx.setFillColor(rgb(0x000000, 0.22 * CGFloat(pose[.dim])))
        ctx.fillEllipse(in: oval(100, 180, 46 - lift * 0.35, 7))

        ctx.saveGState()
        ctx.translateBy(x: CGFloat(pose[.x]), y: CGFloat(pose[.y]))
        ctx.setAlpha(CGFloat(min(1, max(0, pose[.dim]))))
        ctx.beginTransparencyLayer(auxiliaryInfo: nil)
        body(ctx, pose)
        ctx.endTransparencyLayer()
        ctx.restoreGState()

        for prop in CompanionProp.allCases {
            let alpha = min(1, max(0, props[prop.rawValue]))
            guard alpha > 0.02 else { continue }
            ctx.saveGState()
            ctx.setAlpha(CGFloat(alpha))
            draw(prop, ctx, time)
            ctx.restoreGState()
        }
        ctx.restoreGState()
    }

    private static func body(_ ctx: CGContext, _ pose: CompanionPose) {
        let foot = CGPoint(x: 100, y: 176)
        let sy = pose[.squash], sx = 1 + (1 - pose[.squash]) * 0.8
        rotate(ctx, degrees: pose[.tilt], about: foot)
        ctx.translateBy(x: foot.x, y: foot.y)
        ctx.scaleBy(x: CGFloat(sx), y: CGFloat(sy))
        ctx.translateBy(x: -foot.x, y: -foot.y)

        // The tuft's glow: bright while working.
        let glow = CGFloat(min(1, max(0, pose[.glow])))
        if glow > 0.01 {
            radial(ctx, from: rgb(ice, 0.9 * glow), to: rgb(ice, 0), center: CGPoint(x: 100, y: 46), radius: 34)
        }

        ctx.saveGState()
        rotate(ctx, degrees: pose[.tuft], about: CGPoint(x: 100, y: 66))
        polygon(ctx, [(84, 42), (97, 64), (85, 66)], fill: ice2)
        polygon(ctx, [(117, 40), (115, 66), (103, 64)], fill: ice2)
        polygon(ctx, [(100, 26), (108, 66), (92, 66)], fill: ice)
        ctx.restoreGState()

        limb(ctx, oval(80, 174, 14, 7))
        limb(ctx, oval(120, 174, 14, 7))

        let torso = CGMutablePath()
        torso.move(to: CGPoint(x: 100, y: 60))
        torso.addCurve(to: CGPoint(x: 162, y: 126), control1: CGPoint(x: 142, y: 60), control2: CGPoint(x: 162, y: 96))
        torso.addCurve(to: CGPoint(x: 100, y: 176), control1: CGPoint(x: 162, y: 160), control2: CGPoint(x: 136, y: 176))
        torso.addCurve(to: CGPoint(x: 38, y: 126), control1: CGPoint(x: 64, y: 176), control2: CGPoint(x: 38, y: 160))
        torso.addCurve(to: CGPoint(x: 100, y: 60), control1: CGPoint(x: 38, y: 96), control2: CGPoint(x: 58, y: 60))
        torso.closeSubpath()
        ctx.saveGState()
        ctx.addPath(torso)
        ctx.clip()
        radial(ctx, from: rgb(body1), to: rgb(body2), center: CGPoint(x: 85, y: 92), radius: 105)
        ctx.restoreGState()
        ctx.addPath(torso)
        ctx.setStrokeColor(rgb(edge))
        ctx.setLineWidth(2)
        ctx.strokePath()

        ctx.setFillColor(rgb(body1, 0.7))
        ctx.fillEllipse(in: oval(100, 150, 30, 18))

        ctx.setFillColor(rgb(blush, CGFloat(min(1, max(0, pose[.blush] * 0.6)))))
        ctx.fillEllipse(in: oval(62, 134, 9, 5))
        ctx.fillEllipse(in: oval(138, 134, 9, 5))

        face(ctx, pose)

        ctx.saveGState()
        rotate(ctx, degrees: pose[.armLeft], about: CGPoint(x: 50, y: 122))
        limb(ctx, oval(44, 136, 9, 15))
        ctx.restoreGState()
        ctx.saveGState()
        rotate(ctx, degrees: -pose[.armRight], about: CGPoint(x: 150, y: 122))
        limb(ctx, oval(156, 136, 9, 15))
        ctx.restoreGState()
    }

    private static func face(_ ctx: CGContext, _ pose: CompanionPose) {
        let px = pose[.lookX], py = pose[.lookY]
        let open = min(1.08, max(0, pose[.eyeOpen]))
        ctx.setLineCap(.round)
        ctx.setLineJoin(.round)
        for cx0 in [78.0, 122.0] {
            let cx = cx0 + px, cy = 118 + py
            if open < 0.2 {
                // Shut: an arc that curves down asleep, up when happy.
                let curve = pose[.eyeCurve]
                ctx.beginPath()
                ctx.move(to: CGPoint(x: cx - 9, y: cy + curve * 3))
                ctx.addQuadCurve(to: CGPoint(x: cx + 9, y: cy + curve * 3), control: CGPoint(x: cx, y: cy - curve * 9))
                ctx.setStrokeColor(rgb(ink))
                ctx.setLineWidth(3)
                ctx.strokePath()
            } else {
                ctx.setFillColor(rgb(ink))
                ctx.fillEllipse(in: oval(cx, cy, 8.5, 12.5 * open))
                if open > 0.6 {
                    ctx.setFillColor(rgb(body1))
                    ctx.fillEllipse(in: oval(cx + 2.6, cy - 5 * open, 3.2, 3.2))
                    ctx.fillEllipse(in: oval(cx - 3, cy + 4.5 * open, 1.6, 1.6))
                }
            }
        }

        let brow = CGFloat(min(1, max(0, pose[.brow])))
        if brow > 0.02 {
            let by = 97 + pose[.browY] + py * 0.5, t = pose[.browTilt] * 0.32
            ctx.setStrokeColor(rgb(ink, brow))
            ctx.setLineWidth(3)
            ctx.beginPath()
            ctx.move(to: CGPoint(x: 70 + px, y: by - t))
            ctx.addLine(to: CGPoint(x: 86 + px, y: by + t))
            ctx.move(to: CGPoint(x: 130 + px, y: by - t))
            ctx.addLine(to: CGPoint(x: 114 + px, y: by + t))
            ctx.strokePath()
        }

        let mx = 100 + px * 0.6, my = 142 + py * 0.5
        let w = max(2, pose[.mouthWidth]), gap = max(0, pose[.mouthOpen]) * 9, curve = pose[.mouthCurve]
        ctx.beginPath()
        ctx.move(to: CGPoint(x: mx - w, y: my))
        ctx.addQuadCurve(to: CGPoint(x: mx + w, y: my), control: CGPoint(x: mx, y: my + curve))
        ctx.addQuadCurve(to: CGPoint(x: mx - w, y: my), control: CGPoint(x: mx, y: my + curve + gap))
        ctx.closePath()
        ctx.setFillColor(rgb(ink))
        ctx.setStrokeColor(rgb(ink))
        ctx.setLineWidth(3)
        ctx.drawPath(using: .fillStroke)
    }

    // ------------------------------------------------------------------ props
    private static func chip(_ ctx: CGContext, _ rect: CGRect, radius: CGFloat? = nil, fill: UInt32 = ice) {
        let path = CGPath(roundedRect: rect, cornerWidth: radius ?? rect.width / 2,
                          cornerHeight: radius ?? rect.height / 2, transform: nil)
        ctx.addPath(path)
        ctx.setFillColor(rgb(fill))
        ctx.setStrokeColor(rgb(body1))
        ctx.setLineWidth(2)
        ctx.drawPath(using: .fillStroke)
    }

    private static func star(_ ctx: CGContext, _ cx: Double, _ cy: Double, _ r: Double, phase: Double, time: Double) {
        let wave = 0.5 + 0.5 * sin(time * 5.7 + phase)
        let s = r * (0.4 + 0.6 * wave), k = s * 0.25
        ctx.saveGState()
        ctx.setAlpha(CGFloat(0.3 + 0.7 * wave))
        ctx.beginPath()
        let pts: [(Double, Double)] = [(0, -s), (k, -k), (s, 0), (k, k), (0, s), (-k, k), (-s, 0), (-k, -k)]
        for (i, p) in pts.enumerated() {
            let pt = CGPoint(x: cx + p.0, y: cy + p.1)
            if i == 0 { ctx.move(to: pt) } else { ctx.addLine(to: pt) }
        }
        ctx.closePath()
        ctx.setFillColor(rgb(ice2))
        ctx.fillPath()
        ctx.restoreGState()
    }

    private static func draw(_ prop: CompanionProp, _ ctx: CGContext, _ time: Double) {
        ctx.setLineCap(.round)
        ctx.setLineJoin(.round)
        switch prop {
        case .zzz:
            // Three z's drifting up and fading, one after another.
            let spots: [(Double, Double, Double)] = [(153, 56, 3.5), (166, 39, 4.5), (179, 20, 5.5)]
            for (i, spot) in spots.enumerated() {
                let p = (time / 2.8 + Double(i) / 3).truncatingRemainder(dividingBy: 1)
                let alpha = p < 0.3 ? p / 0.3 : (1 - p) / 0.7
                let x = spot.0 + 8 * p, y = spot.1 + 6 - 20 * p, h = spot.2
                ctx.saveGState()
                ctx.setAlpha(CGFloat(alpha))
                ctx.beginPath()
                ctx.move(to: CGPoint(x: x - h, y: y - h))
                ctx.addLine(to: CGPoint(x: x + h, y: y - h))
                ctx.addLine(to: CGPoint(x: x - h, y: y + h))
                ctx.addLine(to: CGPoint(x: x + h, y: y + h))
                ctx.setStrokeColor(rgb(ice2))
                ctx.setLineWidth(2.2)
                ctx.strokePath()
                ctx.restoreGState()
            }
        case .think:
            chip(ctx, oval(150, 62, 4, 4))
            chip(ctx, oval(159, 48, 6, 6))
            chip(ctx, CGRect(x: 150, y: 8, width: 46, height: 28), radius: 14)
            for (i, x) in [162.0, 173.0, 184.0].enumerated() {
                let wave = 0.5 + 0.5 * sin(time * 5.2 - Double(i) * 1.05)
                ctx.setFillColor(rgb(ink, CGFloat(0.35 + 0.65 * wave)))
                ctx.fillEllipse(in: oval(x, 22, 3, 3))
            }
        case .ask:
            chip(ctx, oval(170, 34, 17, 17))
            ctx.beginPath()
            ctx.move(to: CGPoint(x: 164, y: 30))
            ctx.addCurve(to: CGPoint(x: 176, y: 30), control1: CGPoint(x: 164, y: 22), control2: CGPoint(x: 176, y: 22))
            ctx.addCurve(to: CGPoint(x: 170, y: 38.5), control1: CGPoint(x: 176, y: 34.5), control2: CGPoint(x: 170, y: 34))
            ctx.setStrokeColor(rgb(ink))
            ctx.setLineWidth(3.2)
            ctx.strokePath()
            ctx.setFillColor(rgb(ink))
            ctx.fillEllipse(in: oval(170, 44.5, 2, 2))
        case .pause:
            chip(ctx, oval(170, 34, 17, 17))
            ctx.setFillColor(rgb(ink))
            for x in [162.0, 172.5] {
                ctx.addPath(CGPath(roundedRect: CGRect(x: x, y: 25, width: 5.5, height: 18), cornerWidth: 2,
                                   cornerHeight: 2, transform: nil))
            }
            ctx.fillPath()
        case .spark:
            star(ctx, 30, 52, 12, phase: 0, time: time)
            star(ctx, 172, 45, 15, phase: 2, time: time)
            star(ctx, 180, 105.5, 9.5, phase: 4, time: time)
        case .sweat:
            ctx.beginPath()
            ctx.move(to: CGPoint(x: 158, y: 62))
            ctx.addCurve(to: CGPoint(x: 166, y: 78), control1: CGPoint(x: 163, y: 70), control2: CGPoint(x: 166, y: 74))
            ctx.addCurve(to: CGPoint(x: 158, y: 86), control1: CGPoint(x: 166, y: 83), control2: CGPoint(x: 162, y: 86))
            ctx.addCurve(to: CGPoint(x: 150, y: 78), control1: CGPoint(x: 154, y: 86), control2: CGPoint(x: 150, y: 83))
            ctx.addCurve(to: CGPoint(x: 158, y: 62), control1: CGPoint(x: 150, y: 74), control2: CGPoint(x: 153, y: 70))
            ctx.closePath()
            ctx.setFillColor(rgb(ice))
            ctx.setStrokeColor(rgb(body1))
            ctx.setLineWidth(1.5)
            ctx.drawPath(using: .fillStroke)
        case .lock:
            chip(ctx, oval(170, 34, 17, 17), fill: amber)
            ctx.setFillColor(rgb(ink))
            ctx.addPath(CGPath(roundedRect: CGRect(x: 161, y: 32, width: 18, height: 13), cornerWidth: 3,
                               cornerHeight: 3, transform: nil))
            ctx.fillPath()
            ctx.beginPath()
            ctx.move(to: CGPoint(x: 164.5, y: 32))
            ctx.addLine(to: CGPoint(x: 164.5, y: 28))
            ctx.addArc(center: CGPoint(x: 170, y: 28), radius: 5.5, startAngle: .pi, endAngle: 2 * .pi, clockwise: false)
            ctx.addLine(to: CGPoint(x: 175.5, y: 32))
            ctx.setStrokeColor(rgb(ink))
            ctx.setLineWidth(3)
            ctx.strokePath()
        }
    }

    // ----------------------------------------------------------------- sheet
    /// Every mood side by side as a PNG (`Pinlet --companion-sheet out.png`), to check the art.
    static func sheet(scale: Int = 2) -> Data? {
        let moods = CompanionMood.allCases
        let cols = 3, rows = Int((Double(moods.count) / 3).rounded(.up))
        let w = Int(box.width) * cols * scale, h = Int(box.height) * rows * scale
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0, space: space,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        ctx.setFillColor(rgb(0x14283A))
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        ctx.translateBy(x: 0, y: CGFloat(h))
        ctx.scaleBy(x: CGFloat(scale), y: -CGFloat(scale))  // y down, like the view
        for (i, mood) in moods.enumerated() {
            var props = [Double](repeating: 0, count: CompanionProp.allCases.count)
            if let prop = mood.prop { props[prop.rawValue] = 1 }
            ctx.saveGState()
            ctx.translateBy(x: CGFloat(i % cols) * box.width, y: CGFloat(i / cols) * box.height)
            draw(in: ctx, pose: CompanionPose.target(mood), props: props, time: 0.9)
            ctx.restoreGState()
        }
        guard let image = ctx.makeImage() else { return nil }
        return NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])
    }
}
