// swift-tools-version:5.9
// Dobby.app: the native macOS shell around the shared Dobby (`dobby serve`).
// Build the app bundle with ./build.sh (needs the Xcode command line tools).
import PackageDescription

let package = Package(
    name: "Dobby",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "Dobby",
            path: "Sources/Dobby",
            linkerSettings: [.linkedFramework("AppKit"), .linkedFramework("ServiceManagement"),
                             .linkedFramework("SwiftUI"), .linkedFramework("UserNotifications"),
                             .linkedFramework("AppIntents")]
        ),
        .testTarget(name: "DobbyTests", dependencies: ["Dobby"], path: "Tests/DobbyTests"),
    ]
)
