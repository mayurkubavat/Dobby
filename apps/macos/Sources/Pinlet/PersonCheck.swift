import Foundation
import LocalAuthentication

/// Proof that a person is at the Mac: Touch ID (or Apple Watch), with the
/// login password as the fallback macOS offers.
///
/// Pinlet asks for it before it saves an "Always allow", so a standing
/// permission cannot be given by a stray key press or by synthetic input: the
/// system draws the sheet and software cannot answer Touch ID.
enum PersonCheck {
    /// Ask the person to confirm. `reason` completes "Pinlet is trying to …".
    /// `done` runs on the main queue: true only when macOS says the owner confirmed.
    static func confirm(_ reason: String, done: @escaping (Bool) -> Void) {
        let context = LAContext()
        context.localizedCancelTitle = "Cancel"
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            // No Touch ID and no password to fall back on: nothing can prove a person, so nothing is saved.
            DispatchQueue.main.async { done(false) }
            return
        }
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { ok, _ in
            DispatchQueue.main.async { done(ok) }
        }
    }
}
