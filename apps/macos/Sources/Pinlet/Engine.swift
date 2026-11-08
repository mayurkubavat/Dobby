import Foundation

/// Runs the shared Pinlet (`python -m pinlet serve`) as this app's child.
///
/// Being the parent matters on macOS: the engine and its overlay helper count
/// as part of Pinlet.app, so Screen Recording and Accessibility are granted to
/// "Pinlet" once, not to whichever terminal or AI client started Python.
final class Engine {
    enum State: Equatable {
        case notConfigured      // no Python to start: Pinlet is not installed yet (Welcome installs it)
        case starting
        case running(pid: Int32)
        case external           // a shared Pinlet we did not start is already listening
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
    private let queue = DispatchQueue(label: "dev.pinlet.engine")

    init(socketPath: String = PinletPaths.socketPath()) {
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
        if ControlClient.isListening(path: socketPath) && !replaceLeftoverEngine() {
            state = .external
            return
        }
        guard let config = EngineConfig.resolve() else {
            state = .notConfigured
            return
        }
        state = .starting
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: config.python)
        proc.arguments = ["-m", "pinlet", "serve"]
        // No checkout to run in: start in the home folder, never wherever the app happens to be.
        proc.currentDirectoryURL = URL(fileURLWithPath: config.cwd ?? NSHomeDirectory())
        var env = config.environment(base: ProcessInfo.processInfo.environment)
        for (k, v) in AppSettings.shared.engineEnvironment() { env[k] = v }  // the Settings window wins
        proc.environment = env
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

    /// A shared Pinlet an earlier Pinlet.app started (one that outlived it, or an old
    /// engine after an update) is asked to quit, so this app runs the current engine.
    /// One an AI client or terminal started itself (`app_owned: false`) is left alone.
    private func replaceLeftoverEngine() -> Bool {
        let client = ControlClient(path: socketPath)
        defer { client.close() }
        guard let status = client.request("status"), (status["ok"] as? Bool) != false else { return false }
        if (status["app_owned"] as? Bool) == false { return false }  // older engines lack the key
        NSLog("Pinlet: replacing the leftover engine (pid \(status["server_pid"] ?? "?"))")
        _ = client.request("quit")
        client.close()
        for _ in 0..<100 {  // up to 10 s for it to let go of the socket
            if !ControlClient.isListening(path: socketPath) { return true }
            usleep(100_000)
        }
        return false
    }

    private func exited(_ proc: Process) {
        process = nil
        if quitting {
            state = .stopped
            return
        }
        // Lost a race to another `pinlet serve`? Then that one is ours to show.
        if ControlClient.isListening(path: socketPath) {
            state = .external
            return
        }
        restarts += 1
        state = .crashed("Pinlet stopped (exit \(proc.terminationStatus)). Restarting…")
        let delay = min(60.0, pow(2.0, Double(min(restarts, 6))))
        queue.asyncAfter(deadline: .now() + delay) { self.launch() }
    }

    /// Called every poll: notice a config that appeared, or an external Pinlet that went away.
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
        queue.async { [weak self] in
            guard let self else { return }
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
        let url = PinletPaths.daemonLog
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: url) else { return nil }
        handle.seekToEndOfFile()
        return handle
    }
}
