import AppKit
import SwiftUI

/// Another connected AI, shown as a small orb beside Rime.
struct CompanionOrb: Equatable, Identifiable {
    var pid: Int
    var initial: String
    var name: String
    var color: String
    var paused: Bool
    var waiting: Bool
    var id: Int { pid }
}

// ------------------------------------------------------------------ bubble
/// The jagged "trouble" bubble.
struct BurstShape: Shape {
    func path(in r: CGRect) -> Path {
        let pts: [(CGFloat, CGFloat)] = [
            (0, 0.08), (0.06, 0), (0.2, 0.06), (0.34, 0), (0.5, 0.05), (0.66, 0), (0.8, 0.06), (0.94, 0),
            (1, 0.1), (0.97, 0.5), (1, 0.9), (0.94, 1), (0.8, 0.94), (0.66, 1), (0.5, 0.95), (0.34, 1),
            (0.2, 0.94), (0.06, 1), (0, 0.92), (0.03, 0.5),
        ]
        var p = Path()
        for (i, (x, y)) in pts.enumerated() {
            let point = CGPoint(x: r.minX + x * r.width, y: r.minY + y * r.height)
            if i == 0 { p.move(to: point) } else { p.addLine(to: point) }
        }
        p.closeSubpath()
        return p
    }
}

/// A round bubble with a small tail toward Rime (bottom right).
struct SpeechShape: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path(roundedRect: r, cornerRadius: 16, style: .continuous)
        p.move(to: CGPoint(x: r.maxX - 34, y: r.maxY - 1))
        p.addLine(to: CGPoint(x: r.maxX - 14, y: r.maxY + 9))
        p.addLine(to: CGPoint(x: r.maxX - 20, y: r.maxY - 1))
        p.closeSubpath()
        return p
    }
}

struct CompanionBubbleView: View {
    let bubble: CompanionBubble
    let dark: Bool
    let openLog: () -> Void

    private static let ice = Color(red: 184 / 255, green: 243 / 255, blue: 252 / 255)
    private static let cyan = Color(red: 111 / 255, green: 224 / 255, blue: 244 / 255)
    private static let amber = Color(nsColor: NSColor(hex: Palette.caution))

    private var shape: AnyShape {
        switch bubble.kind {
        case .cloud: return AnyShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        case .speech: return AnyShape(SpeechShape())
        case .burst: return AnyShape(BurstShape())
        case .ice: return AnyShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        case .chip: return AnyShape(Capsule())
        }
    }

    private var ink: Color { bubble.kind == .burst ? Color(white: 0.08) : .primary }

    var body: some View {
        VStack(alignment: .trailing, spacing: 3) {
            content
                .padding(.horizontal, bubble.kind == .burst ? 18 : (bubble.kind == .chip ? 11 : 13))
                .padding(.vertical, bubble.kind == .burst ? 14 : (bubble.kind == .chip ? 5 : 10))
                .frame(maxWidth: 280, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .background(background)
                .overlay(shape.stroke(rim, lineWidth: bubble.kind == .ice ? 1.5 : 1))
                .shadow(color: bubble.kind == .ice ? CompanionBubbleView.cyan.opacity(0.35) : .clear, radius: 9)
            if bubble.kind == .cloud {
                // The two little puffs that make it a thought.
                HStack(alignment: .top, spacing: 4) {
                    Circle().frame(width: 7, height: 7).offset(y: 9)
                    Circle().frame(width: 12, height: 12)
                }
                .foregroundStyle(.regularMaterial)
                .overlay(HStack(alignment: .top, spacing: 4) {
                    Circle().stroke(rim).frame(width: 7, height: 7).offset(y: 9)
                    Circle().stroke(rim).frame(width: 12, height: 12)
                })
                .padding(.trailing, 30)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(bubble.spoken)
    }

    @ViewBuilder private var background: some View {
        switch bubble.kind {
        case .burst:
            shape.fill(CompanionBubbleView.amber)
        case .ice:
            shape.fill(.regularMaterial)
                .overlay(shape.fill(LinearGradient(colors: [CompanionBubbleView.ice.opacity(0.32), .clear],
                                                   startPoint: .topLeading, endPoint: .bottomTrailing)))
        default:
            shape.fill(.regularMaterial)
        }
    }

    private var rim: Color {
        switch bubble.kind {
        case .ice: return CompanionBubbleView.ice
        case .burst: return CompanionBubbleView.amber
        default: return CompanionBubbleView.cyan.opacity(dark ? 0.35 : 0.5)
        }
    }

    @ViewBuilder private var content: some View {
        if bubble.kind == .chip {
            HStack(spacing: 8) {
                Text(bubble.who).font(.system(size: 11, weight: .semibold))
                Text(bubble.meta).font(.system(size: 11).monospacedDigit()).foregroundStyle(.secondary)
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                if !bubble.who.isEmpty {
                    HStack(spacing: 5) {
                        Circle().fill(bubble.kind == .burst ? Color(white: 0.1) : CompanionBubbleView.cyan)
                            .frame(width: 6, height: 6)
                        Text(bubble.who.uppercased())
                            .font(.system(size: 10, weight: .semibold)).kerning(0.6)
                            .foregroundStyle(bubble.kind == .burst ? Color(white: 0.1) : CompanionBubbleView.cyan)
                    }
                }
                if !bubble.goal.isEmpty {
                    Text(bubble.goal).font(.system(size: 12.5, weight: .semibold)).foregroundStyle(ink)
                }
                if !bubble.thought.isEmpty {
                    Text(bubble.thought).font(.system(size: 12).italic()).foregroundStyle(ink)
                }
                if !bubble.doing.isEmpty {
                    Divider().opacity(0.6)
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        if let kind = bubble.stepKind {
                            Image(systemName: kind.symbol).font(.system(size: 10.5, weight: .semibold))
                                .foregroundStyle(CompanionBubbleView.cyan)
                        }
                        Text(bubble.doing).font(.system(size: 12, weight: .medium)).foregroundStyle(ink)
                    }
                }
                if !bubble.meta.isEmpty {
                    Text(bubble.meta).font(.system(size: 11).monospacedDigit()).foregroundStyle(.secondary)
                }
                if bubble.showsLog {
                    Button(action: openLog) {
                        Label("Mission log", systemImage: "scroll").font(.system(size: 11, weight: .medium))
                    }
                    .buttonStyle(.link)
                    .accessibilityHint("Shows every step of the task that just ended.")
                }
            }
        }
    }
}

// -------------------------------------------------------------------- ring
/// The command ring that fans out from Rime. Laid out in a square box whose
/// bottom-right corner holds Rime.
struct CompanionRingView: View {
    let items: [RingItem]
    let rigSize: CGSize
    let radius: CGFloat
    let perform: (RingItem) -> Void
    @Binding var caption: RingItem?

    static let button: CGFloat = 40

    static func boxSize(rig: CGSize, radius: CGFloat) -> CGSize {
        CGSize(width: rig.width / 2 + radius + button / 2 + 6, height: rig.height / 2 + radius + button / 2 + 6)
    }

    /// Positions along a quarter arc from left to straight up.
    static func position(index: Int, count: Int, box: CGSize, rig: CGSize, radius: CGFloat) -> CGPoint {
        let center = CGPoint(x: box.width - rig.width / 2, y: box.height - rig.height / 2)
        let start = 178.0, end = 272.0
        let deg = count <= 1 ? 225 : start + (end - start) * Double(index) / Double(count - 1)
        let a = deg * .pi / 180
        return CGPoint(x: center.x + radius * CGFloat(cos(a)), y: center.y + radius * CGFloat(sin(a)))
    }

    @State private var shown = false

    var body: some View {
        let box = CompanionRingView.boxSize(rig: rigSize, radius: radius)
        ZStack(alignment: .topLeading) {
            Color.clear.frame(width: box.width, height: box.height)
            ForEach(Array(items.enumerated()), id: \.element) { index, item in
                RingButton(item: item, primary: index == 0, perform: perform, caption: $caption)
                    .position(CompanionRingView.position(index: index, count: items.count, box: box,
                                                         rig: rigSize, radius: radius))
                    .scaleEffect(shown ? 1 : 0.2)
                    .opacity(shown ? 1 : 0)
                    .animation(NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? nil
                               : .spring(response: 0.32, dampingFraction: 0.6).delay(Double(index) * 0.03), value: shown)
            }
        }
        .frame(width: box.width, height: box.height)
        .onAppear { shown = true }
    }
}

private struct RingButton: View {
    let item: RingItem
    let primary: Bool
    let perform: (RingItem) -> Void
    @Binding var caption: RingItem?
    @State private var holding = false
    @State private var held: CGFloat = 0

    private static let cyan = Color(red: 111 / 255, green: 224 / 255, blue: 244 / 255)
    private static let amber = Color(nsColor: NSColor(hex: Palette.caution))

    var body: some View {
        let face = ZStack {
            Circle().fill(.regularMaterial)
            Circle().stroke(primary ? RingButton.cyan : Color.primary.opacity(0.18), lineWidth: primary ? 2 : 1)
            if item.needsHold {
                Circle().trim(from: 0, to: held)
                    .stroke(RingButton.amber, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    .rotationEffect(.degrees(-90))
            }
            Image(systemName: item.symbol).font(.system(size: 15, weight: .semibold))
        }
        .frame(width: CompanionRingView.button, height: CompanionRingView.button)
        .contentShape(Circle())
        .onHover { inside in if inside { caption = item } }
        .help("\(item.title): \(item.help)")
        .accessibilityElement()
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel(item.title)
        .accessibilityHint(item.help)
        .accessibilityAction { perform(item) }

        if item.needsHold {
            face.onLongPressGesture(minimumDuration: 0.8, maximumDistance: 30, perform: {
                held = 0
                perform(item)
            }, onPressingChanged: { pressing in
                caption = item
                holding = pressing
                withAnimation(pressing ? .linear(duration: 0.8) : .easeOut(duration: 0.15)) { held = pressing ? 1 : 0 }
            })
        } else {
            face.onTapGesture { perform(item) }
        }
    }
}

/// The name and help of the ring button under the pointer.
struct RingCaption: View {
    let item: RingItem?
    let napping: Bool

    var body: some View {
        let item = self.item
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(item?.title ?? "Rime").font(.system(size: 12, weight: .semibold))
            Text(item?.help ?? (napping ? "Click a button, or Esc to close." : "Pick one, or Esc to close."))
                .font(.system(size: 12)).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 11)
        .padding(.vertical, 6)
        .frame(maxWidth: 280, alignment: .leading)
        .fixedSize(horizontal: false, vertical: true)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(.regularMaterial))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous)
            .stroke(Color(red: 111 / 255, green: 224 / 255, blue: 244 / 255), lineWidth: 1))
    }
}

// --------------------------------------------------------------------- log
struct CompanionLogView: View {
    let task: CompanionLog.Task
    let close: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label("Mission log", systemImage: "scroll").font(.system(size: 12, weight: .semibold))
                Spacer()
                Button(action: close) { Image(systemName: "xmark").font(.system(size: 10, weight: .bold)) }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Close the mission log")
            }
            Text(task.goal.isEmpty ? "\(task.name)'s last task" : task.goal)
                .font(.system(size: 12)).foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(task.steps.enumerated()), id: \.offset) { index, step in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(CompanionStage.clock(step.at)).font(.system(size: 11).monospacedDigit())
                                .foregroundStyle(.secondary).frame(width: 40, alignment: .trailing)
                            Text("\(index + 1). \(step.text)").font(.system(size: 12))
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 220)
            Text("\(task.steps.count) steps · \(CompanionStage.clock(task.seconds))")
                .font(.system(size: 11).monospacedDigit()).foregroundStyle(.secondary)
        }
        .padding(12)
        .frame(width: 300, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(.regularMaterial))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Color.primary.opacity(0.1)))
        .onExitCommand(perform: close)
    }
}

// -------------------------------------------------------------------- orbs
/// The other AIs, floating beside Rime. Click one to pause or resume only that AI.
struct CompanionOrbsView: View {
    let orbs: [CompanionOrb]
    let toggle: (CompanionOrb) -> Void

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 20, paused: NSWorkspace.shared.accessibilityDisplayShouldReduceMotion)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            VStack(spacing: 6) {
                ForEach(Array(orbs.enumerated()), id: \.element.id) { index, orb in
                    Button(action: { toggle(orb) }) {
                        ZStack {
                            Circle().fill(orb.paused ? Color.gray.opacity(0.6) : Color(nsColor: NSColor(hex: orb.color)))
                            Circle().stroke(Color.white.opacity(0.9), lineWidth: 1.5)
                            if orb.paused {
                                Image(systemName: "pause.fill").font(.system(size: 9, weight: .bold)).foregroundStyle(.white)
                            } else {
                                Text(orb.initial).font(.system(size: 11, weight: .bold)).foregroundStyle(Color(white: 0.08))
                            }
                        }
                        .frame(width: 24, height: 24)
                    }
                    .buttonStyle(.plain)
                    .offset(x: CGFloat(sin(t * (orb.waiting ? 0.9 : 1.6) + Double(index))) * 4,
                            y: CGFloat(cos(t * 1.1 + Double(index) * 1.7)) * 3)
                    .help(orb.paused ? "\(orb.name) is paused. Click to resume only \(orb.name)."
                                     : "\(orb.name)\(orb.waiting ? " waits its turn" : "") · click to pause only \(orb.name).")
                    .accessibilityLabel(orb.paused ? "Resume \(orb.name)" : "Pause \(orb.name)")
                }
            }
        }
    }
}
