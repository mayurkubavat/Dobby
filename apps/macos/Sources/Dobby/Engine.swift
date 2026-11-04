import Foundation

/// Runs the shared Dobby (`python -m dobby serve`) as this app's child.
///
/// Being the parent matters on macOS: the engine and its overlay helper count
/// as part of Dobby.app, so Screen Recording and Accessibility are granted to
/// "Dobby" once, not to whichever terminal or AI client started Python.
final class Engine {
    enum State: Equatable {
        case notConfigured      // no ~/.dobby/app.json yet: run `dobby` from an AI client once
        case starting
        case running(pid: Int32)
        case external           // a shared Dobby we did not start is already listening
        case crashed(String)
        case stopped
    }

    private(set) var state: State = .stopped {
        didSet { if state != oldValue { DispatchQueue.main.async { self.onChange?(self.state) } } }
    }
    var onChange: ((State) -> Void)?

    private var process: Process?
    private var quitting = false
    private var restarts = 0
    private let socketPath: String
    private let queue = DispatchQueue(label: "dev.dobby.engine")

    init(socketPath: String = DobbyPaths.socketPath()) {
        self.socketPath = socketPath
    }

    var childPID: Int32? {
        if case .running(let pid) = state { return pid }
        return nil
    }

    func start() {
        queue.async { self.launch() }
    }

    private func launch() {
        guard !quitting, process == nil else { return }
        if ControlClient.isListening(path: socketPath) {
            state = .external
            return
        }
        guard let config = EngineConfig.load() else {
            state = .notConfigured
            return
        }
        state = .starting
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: config.python)
        proc.arguments = ["-m", "dobby", "serve"]
        if let cwd = config.cwd { proc.currentDirectoryURL = URL(fileURLWithPath: cwd) }
        proc.environment = config.environment(base: ProcessInfo.processInfo.environment)
        if let log = Engine.openLog() {
            proc.standardOutput = log
            proc.standardError = log
        }
        proc.standardInput = FileHandle.nullDevice
        proc.terminationHandler = { [weak self] p in
            self?.queue.async { self?.exited(p) }
        }
        do {
            try proc.run()
            process = proc
            state = .running(pid: proc.processIdentifier)
        } catch {
            state = .crashed("Could not start \(config.python): \(error.localizedDescription)")
        }
    }

    private func exited(_ proc: Process) {
        process = nil
        if quitting {
            state = .stopped
            return
        }
        // Lost a race to another `dobby serve`? Then that one is ours to show.
        if ControlClient.isListening(path: socketPath) {
            state = .external
            return
        }
        restarts += 1
        state = .crashed("Dobby stopped (exit \(proc.terminationStatus)). Restarting…")
        let delay = min(60.0, pow(2.0, Double(min(restarts, 6))))
        queue.asyncAfter(deadline: .now() + delay) { self.launch() }
    }

    /// Called every poll: notice a config that appeared, or an external Dobby that went away.
    func tick(reachable: Bool) {
        queue.async {
            if reachable { self.restarts = 0 }
            switch self.state {
            case .notConfigured, .stopped:
                if !self.quitting { self.launch() }
            case .external where !reachable:
                self.state = .stopped
                self.launch()
            default:
                break
            }
        }
    }

    func restart() {
        queue.async {
            self.restarts = 0
            if let p = self.process {
                p.terminationHandler = { [weak self] _ in
                    self?.queue.async { self?.process = nil; self?.launch() }
                }
                p.terminate()
            } else {
                self.state = .stopped
                self.launch()
            }
        }
    }

    /// Stop the engine we own (SIGTERM, then SIGKILL after 3 s).
    func stop(completion: @escaping () -> Void) {
        queue.async {
            self.quitting = true
            guard let p = self.process, p.isRunning else {
                DispatchQueue.main.async(execute: completion)
                return
            }
            p.terminate()
            let deadline = Date().addingTimeInterval(3)
            while p.isRunning && Date() < deadline { usleep(50_000) }
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
            DispatchQueue.main.async(execute: completion)
        }
    }

    static func openLog() -> FileHandle? {
        let url = DobbyPaths.daemonLog
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: url) else { return nil }
        handle.seekToEndOfFile()
        return handle
    }
}
