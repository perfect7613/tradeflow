import CoreMotion
import Foundation
import MediaPlayer

/// Reads AirPods head attitude with CMHeadphoneMotionManager and reports the stem press.
/// Gesture detection itself happens in the web app (web/src/lib/gestures.ts), so the same
/// detector is unit-tested and shared with the browser simulator.
@MainActor
final class HeadMotion: NSObject, ObservableObject, CMHeadphoneMotionManagerDelegate {
    @Published var connected = false
    @Published var available = false
    @Published var authorized = true

    var onMotion: ((Double, Double, Double, Double) -> Void)?
    var onConnected: ((Bool) -> Void)?
    var onStem: (() -> Void)?

    private let manager = CMHeadphoneMotionManager()
    private var lastSent: TimeInterval = 0

    func start() {
        available = manager.isDeviceMotionAvailable
        authorized = CMHeadphoneMotionManager.authorizationStatus() != .denied
        manager.delegate = self
        guard available else { return }
        manager.startDeviceMotionUpdates(to: .main) { [weak self] motion, _ in
            guard let self, let m = motion else { return }
            let now = Date().timeIntervalSince1970
            if now - self.lastSent < 0.035 { return } // ~25 Hz is plenty for nods
            self.lastSent = now
            if !self.connected { self.setConnected(true) }
            let a = m.attitude
            self.onMotion?(a.pitch, a.yaw, a.roll, now * 1000)
        }
        setupStem()
    }

    func stop() {
        manager.stopDeviceMotionUpdates()
    }

    private func setConnected(_ c: Bool) {
        connected = c
        onConnected?(c)
    }

    // Called when AirPods connect, and when they stop streaming motion (taken out of the ears).
    nonisolated func headphoneMotionManagerDidConnect(_ manager: CMHeadphoneMotionManager) {
        Task { @MainActor in self.setConnected(true) }
    }

    nonisolated func headphoneMotionManagerDidDisconnect(_ manager: CMHeadphoneMotionManager) {
        Task { @MainActor in self.setConnected(false) }
    }

    /// A stem press arrives as a play/pause remote command while we are the now-playing app.
    private func setupStem() {
        let center = MPRemoteCommandCenter.shared()
        for cmd in [center.togglePlayPauseCommand, center.pauseCommand, center.playCommand] {
            cmd.isEnabled = true
            cmd.addTarget { [weak self] _ in
                Task { @MainActor in self?.onStem?() }
                return .success
            }
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = [
            MPMediaItemPropertyTitle: "TradeFlow",
            MPMediaItemPropertyArtist: "Listening for payments",
        ]
    }
}
