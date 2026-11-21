import AVFoundation
import SwiftUI
import WebKit

/// Full-screen WKWebView showing the TradeFlow PWA, with native AirPods motion bridged in:
///   window.__tfMotion(pitch, yaw, roll, tMillis)
///   window.__tfEars(connected)
///   window.__tfStem()
struct WebShell: UIViewRepresentable {
    let url: URL
    @ObservedObject var motion: HeadMotion

    func makeCoordinator() -> Coordinator { Coordinator(motion: motion) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        // The web app checks for this to switch from on-screen buttons to real gestures.
        config.applicationNameForUserAgent = "TradeFlowEars/1.0 Mobile/15E148 Safari/604.1"
        config.userContentController.add(context.coordinator, name: "tf")

        let web = WKWebView(frame: .zero, configuration: config)
        web.uiDelegate = context.coordinator
        web.navigationDelegate = context.coordinator
        web.isOpaque = false
        web.backgroundColor = UIColor(red: 0.05, green: 0.06, blue: 0.14, alpha: 1)
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.allowsBackForwardNavigationGestures = true
        #if DEBUG
        web.isInspectable = true // Safari > Develop > your iPhone
        #endif
        context.coordinator.web = web
        context.coordinator.wire()
        web.load(URLRequest(url: url))
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {}

    @MainActor
    final class Coordinator: NSObject, WKScriptMessageHandler, WKUIDelegate, WKNavigationDelegate {
        weak var web: WKWebView?
        let motion: HeadMotion

        init(motion: HeadMotion) {
            self.motion = motion
        }

        func wire() {
            // Play through the AirPods and keep the mic usable for live translation.
            try? AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .default, options: [.allowBluetoothA2DP, .mixWithOthers])
            try? AVAudioSession.sharedInstance().setActive(true)

            motion.onMotion = { [weak self] p, y, r, t in
                self?.js("window.__tfMotion&&window.__tfMotion(\(p),\(y),\(r),\(t))")
            }
            motion.onConnected = { [weak self] c in
                self?.js("window.__tfEars&&window.__tfEars(\(c))")
            }
            motion.onStem = { [weak self] in
                self?.js("window.__tfStem&&window.__tfStem()")
            }
            motion.start()
        }

        private func js(_ s: String) {
            web?.evaluateJavaScript(s, completionHandler: nil)
        }

        // The page says it is ready: tell it the current AirPods state.
        func userContentController(_ ucc: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
            if type == "ready" {
                js("window.__tfEars&&window.__tfEars(\(motion.connected))")
            } else if type == "log", let msg = body["msg"] as? String {
                print("[web]", msg)
            }
        }

        // Allow the page to use the mic without a second prompt.
        func webView(
            _ webView: WKWebView,
            requestMediaCapturePermissionFor origin: WKSecurityOrigin,
            initiatedByFrame frame: WKFrameInfo,
            type: WKMediaCaptureType,
            decisionHandler: @escaping (WKPermissionDecision) -> Void
        ) {
            decisionHandler(.grant)
        }

        // Demo on a local network: trust the self-signed certificate of the server the user typed in,
        // but only for that host and only when it's a private address or .local name.
        func webView(
            _ webView: WKWebView,
            didReceive challenge: URLAuthenticationChallenge,
            completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
        ) {
            let host = challenge.protectionSpace.host
            let local = host.hasPrefix("192.168.") || host.hasPrefix("10.") || host.hasPrefix("172.") || host.hasSuffix(".local")
            if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
               local, host == webView.url?.host ?? host,
               let trust = challenge.protectionSpace.serverTrust {
                completionHandler(.useCredential, URLCredential(trust: trust))
            } else {
                completionHandler(.performDefaultHandling, nil)
            }
        }

        // Never leave a blank screen: show what went wrong and a retry button.
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            showError(webView, error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            showError(webView, error)
        }

        private func showError(_ webView: WKWebView, _ error: Error) {
            let e = error as NSError
            if e.code == NSURLErrorCancelled { return }
            let target = (e.userInfo[NSURLErrorFailingURLStringErrorKey] as? String) ?? webView.url?.absoluteString ?? ""
            let msg = e.localizedDescription.replacingOccurrences(of: "<", with: "&lt;")
            let html = """
            <html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>
            <body style='background:#0d1024;color:#f2ecdf;font:16px -apple-system;padding:64px 24px'>
            <h2 style='font-family:Georgia;font-weight:400'>Can't reach TradeFlow</h2>
            <p style='opacity:.7'>\(msg)</p>
            <p style='opacity:.5;font-size:13px;word-break:break-all'>\(target)</p>
            <ul style='opacity:.7;font-size:14px;line-height:1.6'>
              <li>Is the iPhone on the same Wi-Fi as the laptop?</li>
              <li>Settings → Privacy &amp; Security → Local Network → TradeFlow is on?</li>
              <li>Is the server running?</li>
            </ul>
            <a href='\(target)' style='display:block;margin-top:24px;padding:14px;border-radius:14px;background:#f4b740;color:#0d1024;text-align:center;font-weight:600;text-decoration:none'>Retry</a>
            <p style='opacity:.5;font-size:13px;margin-top:16px'>Long-press anywhere to change the address.</p>
            </body></html>
            """
            webView.loadHTMLString(html, baseURL: nil)
        }

        // Payment links open in Safari, not inside the shell.
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = navigationAction.request.url { UIApplication.shared.open(url) }
            return nil
        }
    }
}
