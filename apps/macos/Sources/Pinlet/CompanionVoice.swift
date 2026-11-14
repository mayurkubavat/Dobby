import AVFoundation
import Foundation
import Speech

/// "Rime, wait": a spoken freeze, off unless switched on in Settings > Companion.
///
/// Listens only while an AI is working and only with on-device recognition,
/// so nothing you say leaves the Mac. macOS asks once for the microphone and
/// for speech recognition. Recognition runs in short sessions that restart,
/// because a single session stops after about a minute.
final class CompanionVoice {
    /// Called on the main queue when the person says the words.
    var onPause: () -> Void = {}

    private let engine = AVAudioEngine()
    private var recognizer: SFSpeechRecognizer?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var restart: DispatchWorkItem?
    private(set) var listening = false
    private var wanted = false
    private var authorized = false

    static let sessionSeconds: TimeInterval = 50

    /// Whether the Settings switch is on.
    static func enabled(defaults: UserDefaults = .standard) -> Bool {
        defaults.bool(forKey: "companionVoice")
    }

    /// Listen while `on` (voice switched on and an AI at work), else stop.
    func want(_ on: Bool) {
        guard on != wanted else { return }
        wanted = on
        if on { start() } else { stop() }
    }

    private func start() {
        guard wanted, !listening else { return }
        if !authorized {
            SFSpeechRecognizer.requestAuthorization { [weak self] status in
                DispatchQueue.main.async {
                    guard let self else { return }
                    self.authorized = status == .authorized
                    if self.authorized { self.start() }
                }
            }
            return
        }
        let recognizer = self.recognizer ?? SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
        self.recognizer = recognizer
        guard let recognizer, recognizer.isAvailable, recognizer.supportsOnDeviceRecognition else { return }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        request.contextualStrings = ["Rime, wait", "Rime, stop", "Rime, pause"]
        self.request = request
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 else { return }
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak request] buffer, _ in
            request?.append(buffer)
        }
        engine.prepare()
        do { try engine.start() } catch {
            input.removeTap(onBus: 0)
            return
        }
        listening = true
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let text = result?.bestTranscription.formattedString, VoicePause.matches(text) {
                DispatchQueue.main.async {
                    self.onPause()
                    self.cycle()  // start fresh so the same words do not fire twice
                }
                return
            }
            if error != nil || result?.isFinal == true {
                DispatchQueue.main.async { self.cycle() }
            }
        }
        let again = DispatchWorkItem { [weak self] in self?.cycle() }
        restart = again
        DispatchQueue.main.asyncAfter(deadline: .now() + CompanionVoice.sessionSeconds, execute: again)
    }

    private func cycle() {
        stop(keepWanted: true)
        if wanted { DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.start() } }
    }

    private func stop(keepWanted: Bool = false) {
        restart?.cancel()
        restart = nil
        task?.cancel()
        task = nil
        request?.endAudio()
        request = nil
        if engine.isRunning { engine.stop() }
        engine.inputNode.removeTap(onBus: 0)
        listening = false
        if !keepWanted { wanted = false }
    }
}
