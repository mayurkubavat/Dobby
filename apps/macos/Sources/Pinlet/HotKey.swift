import AppKit
import Carbon.HIToolbox

/// One system-wide shortcut through Carbon's RegisterEventHotKey: no
/// Accessibility permission needed, and the key press is not passed on.
final class HotKey {
    private var ref: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private let callback: () -> Void
    private static var byID: [UInt32: HotKey] = [:]
    private static var nextID: UInt32 = 1

    /// Control-Option-Space by default.
    init?(keyCode: UInt32 = UInt32(kVK_Space), modifiers: UInt32 = UInt32(controlKey | optionKey),
          callback: @escaping () -> Void) {
        self.callback = callback
        let id = HotKey.nextID
        HotKey.nextID += 1
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let status = InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
            var hk = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &hk)
            if let key = HotKey.byID[hk.id] {
                DispatchQueue.main.async { key.callback() }
            }
            return noErr
        }, 1, &spec, nil, &handler)
        guard status == noErr else { return nil }
        let hkID = EventHotKeyID(signature: OSType(0x50_4E_4C_54), id: id)  // "PNLT"
        guard RegisterEventHotKey(keyCode, modifiers, hkID, GetApplicationEventTarget(), 0, &ref) == noErr else {
            if let handler { RemoveEventHandler(handler) }
            return nil
        }
        HotKey.byID[id] = self
    }

    deinit {
        if let ref { UnregisterEventHotKey(ref) }
        if let handler { RemoveEventHandler(handler) }
    }
}
