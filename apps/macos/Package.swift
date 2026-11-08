// swift-tools-version:5.9
// Pinlet.app: the native macOS shell around the shared Pinlet (`pinlet serve`).
// Build the app bundle with ./build.sh (needs the Xcode command line tools).
import PackageDescription

let package = Package(
    name: "Pinlet",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "Pinlet",
            path: "Sources/Pinlet",
            linkerSettings: [.linkedFramework("AppKit"), .linkedFramework("ServiceManagement"),
                             .linkedFramework("SwiftUI"), .linkedFramework("UserNotifications"),
                             .linkedFramework("AppIntents"), .linkedFramework("Carbon")]
        ),
        .testTarget(name: "PinletTests", dependencies: ["Pinlet"], path: "Tests/PinletTests"),
    ]
)
