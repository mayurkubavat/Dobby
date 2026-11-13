import AppKit
import SwiftUI

/// A small round glass button (macOS 26), a bordered one before.
struct GlassIconButton: ViewModifier {
    @ViewBuilder
    func body(content: Content) -> some View {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            content.buttonStyle(.glass).buttonBorderShape(.circle).controlSize(.small)
        } else {
            content.buttonStyle(.bordered).controlSize(.small)
        }
        #else
        content.buttonStyle(.bordered).controlSize(.small)
        #endif
    }
}

/// The one primary action: prominent glass in Pinlet's cyan (macOS 26).
struct GlassProminentButton: ViewModifier {
    let tint: Color

    @ViewBuilder
    func body(content: Content) -> some View {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            content.buttonStyle(.glassProminent).buttonBorderShape(.circle).tint(tint).controlSize(.large)
        } else {
            content.buttonStyle(.borderedProminent).tint(tint).controlSize(.large)
        }
        #else
        content.buttonStyle(.borderedProminent).tint(tint).controlSize(.large)
        #endif
    }
}
