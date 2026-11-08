import Foundation

/// Pinlet's own Python, which the app installs and keeps up to date (mirrors pinlet/runtime.py).
///
/// ~/Library/Application Support/Pinlet holds `runtime/` (the venv), `src/<id>/` (the source
/// it was installed from) and, when uv was missing, `bin/uv` and `python/`. AI tools run
/// ~/.pinlet/bin/pinlet, a launcher that finds whichever Python is current.
enum RuntimePaths {
    static func dataDir(env: [String: String] = ProcessInfo.processInfo.environment) -> URL {
        if let custom = env["PINLET_DATA_DIR"], !custom.isEmpty {
            return URL(fileURLWithPath: (custom as NSString).expandingTildeInPath)
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Pinlet")
    }

    static func python(env: [String: String] = ProcessInfo.processInfo.environment) -> URL {
        dataDir(env: env).appendingPathComponent("runtime/bin/python")
    }

    static func launcher(env: [String: String] = ProcessInfo.processInfo.environment) -> URL {
        if let custom = env["PINLET_LAUNCHER"], !custom.isEmpty {
            return URL(fileURLWithPath: (custom as NSString).expandingTildeInPath)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".pinlet/bin/pinlet")
    }

    static var installed: Bool { FileManager.default.isExecutableFile(atPath: python().path) }

    /// The id (`src/<id>`) of the source the runtime was last installed from.
    static var installedSourceID: String? {
        let url = dataDir().appendingPathComponent("runtime/.pinlet-source")
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        let last = (text.trimmingCharacters(in: .whitespacesAndNewlines) as NSString).lastPathComponent
        return last.isEmpty ? nil : last
    }

    /// What build.sh packed into the app: the installer, the source, and that source's id.
    static var bundledInstaller: URL? { resource("install.sh") }
    static var bundledSource: URL? { resource("pinlet-src.tar.gz") }
    static var bundledSourceID: String? {
        resource("pinlet-src.id").flatMap { try? String(contentsOf: $0, encoding: .utf8) }?
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// True when this app carries a newer Pinlet than the runtime has.
    static var updateAvailable: Bool {
        guard installed, let bundled = bundledSourceID, !bundled.isEmpty else { return false }
        return installedSourceID != bundled
    }

    private static func resource(_ name: String) -> URL? {
        guard let url = Bundle.main.resourceURL?.appendingPathComponent(name),
              FileManager.default.fileExists(atPath: url.path) else { return nil }
        return url
    }

    /// A PATH that also has the folders installers use, since apps start with a minimal one.
    static func toolPath(base: String? = ProcessInfo.processInfo.environment["PATH"]) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        var parts = ["\(home)/.pinlet/bin", "\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"]
        parts += (base ?? "").split(separator: ":").map(String.init)
        parts += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        var seen = Set<String>()
        return parts.filter { !$0.isEmpty && seen.insert($0).inserted }.joined(separator: ":")
    }
}

/// Runs the bundled installer (scripts/install.sh) and reports its progress.
final class RuntimeInstaller: ObservableObject {
    static let shared = RuntimeInstaller()

    enum Phase: Equatable {
        case idle
        case running(step: Int, total: Int, text: String)
        case failed(String)
        case done
    }

    @Published private(set) var phase: Phase = .idle
    /// Called on the main queue after a successful install, e.g. to restart the engine.
    var onFinished: [() -> Void] = []
    private var process: Process?
    private var buffer = ""

    var running: Bool { if case .running = phase { return true } else { return false } }

    static var logURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".pinlet/logs/install.log")
    }

    /// Installs or updates the runtime. `quiet` means an update in the background.
    func install() {
        guard !running else { return }
        guard let script = RuntimePaths.bundledInstaller else {
            phase = .failed("This Pinlet.app has no installer inside. Rebuild it with apps/macos/build.sh.")
            return
        }
        var args = [script.path, "--no-setup", "--progress"]
        if let source = RuntimePaths.bundledSource { args += ["--source", source.path] }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/sh")
        proc.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = RuntimePaths.toolPath()
        proc.environment = env
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        proc.standardInput = FileHandle.nullDevice
        let log = RuntimeInstaller.openLog()
        buffer = ""
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            log?.write(data)
            let text = String(decoding: data, as: UTF8.self)
            DispatchQueue.main.async { self?.consume(text) }
        }
        proc.terminationHandler = { [weak self] p in
            pipe.fileHandleForReading.readabilityHandler = nil
            try? log?.close()
            DispatchQueue.main.async { self?.finished(p.terminationStatus) }
        }
        phase = .running(step: 0, total: 6, text: "Starting…")
        do {
            try proc.run()
            process = proc
        } catch {
            phase = .failed("Could not run the installer: \(error.localizedDescription)")
        }
    }

    private func consume(_ text: String) {
        buffer += text
        while let nl = buffer.firstIndex(of: "\n") {
            let line = String(buffer[..<nl])
            buffer.removeSubrange(...nl)
            if let step = RuntimeInstaller.parseStep(line) {
                phase = .running(step: step.0, total: step.1, text: step.2)
            } else if line.hasPrefix("::error ") {
                phase = .failed(String(line.dropFirst("::error ".count)))
            }
        }
    }

    private func finished(_ status: Int32) {
        process = nil
        if status == 0 {
            phase = .done
            onFinished.forEach { $0() }
        } else if case .failed = phase {
            // keep the installer's own message
        } else {
            phase = .failed("The installer stopped (exit \(status)). The log has the details.")
        }
    }

    /// `::step 2/6 Getting the Pinlet source` → (2, 6, "Getting the Pinlet source").
    static func parseStep(_ line: String) -> (Int, Int, String)? {
        guard line.hasPrefix("::step ") else { return nil }
        let rest = line.dropFirst("::step ".count)
        guard let space = rest.firstIndex(of: " ") else { return nil }
        let nums = rest[..<space].split(separator: "/")
        guard nums.count == 2, let n = Int(nums[0]), let m = Int(nums[1]) else { return nil }
        return (n, m, String(rest[rest.index(after: space)...]))
    }

    private static func openLog() -> FileHandle? {
        let url = logURL
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: url.path, contents: nil)
        return try? FileHandle(forWritingTo: url)
    }
}

/// Runs the `pinlet` command (through the launcher, or the engine's Python) and returns its JSON.
enum PinletCommand {
    static func run(_ args: [String], completion: @escaping ([String: Any]?, String) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let proc = Process()
            let launcher = RuntimePaths.launcher()
            if FileManager.default.isExecutableFile(atPath: launcher.path) {
                proc.executableURL = launcher
                proc.arguments = args
            } else if let cfg = EngineConfig.resolve() {
                proc.executableURL = URL(fileURLWithPath: cfg.python)
                proc.arguments = ["-m", "pinlet.cli"] + args
            } else {
                DispatchQueue.main.async { completion(nil, "Pinlet is not installed yet.") }
                return
            }
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = RuntimePaths.toolPath()
            proc.environment = env
            proc.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
            let out = Pipe()
            let err = Pipe()
            proc.standardOutput = out
            proc.standardError = err
            proc.standardInput = FileHandle.nullDevice
            do { try proc.run() } catch {
                DispatchQueue.main.async { completion(nil, error.localizedDescription) }
                return
            }
            let data = out.fileHandleForReading.readDataToEndOfFile()
            let errText = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            proc.waitUntilExit()
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            DispatchQueue.main.async { completion(json, errText) }
        }
    }
}
