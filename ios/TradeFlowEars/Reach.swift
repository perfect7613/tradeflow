import Foundation

/// A native request to the server before the web view loads. On iOS this is what reliably
/// triggers the Local Network permission prompt for a LAN address, and it tells us early
/// whether the server is reachable at all.
final class Reach: NSObject, URLSessionDelegate {
    static let shared = Reach()

    func check(_ url: URL) async -> String {
        var comps = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        comps.path = "/api/state"
        comps.query = nil
        let session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)
        var req = URLRequest(url: comps.url!)
        req.timeoutInterval = 8
        do {
            let (_, resp) = try await session.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            print("[reach] \(comps.url!) -> \(code)")
            return code == 200 ? "ok" : "Server answered \(code)"
        } catch {
            print("[reach] \(comps.url!) failed: \(error)")
            return error.localizedDescription
        }
    }

    // Same rule as the web view: accept a self-signed certificate only on a private address.
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        let host = challenge.protectionSpace.host
        let local = host.hasPrefix("192.168.") || host.hasPrefix("10.") || host.hasPrefix("172.") || host.hasSuffix(".local")
        if local, let trust = challenge.protectionSpace.serverTrust {
            return (.useCredential, URLCredential(trust: trust))
        }
        return (.performDefaultHandling, nil)
    }
}
