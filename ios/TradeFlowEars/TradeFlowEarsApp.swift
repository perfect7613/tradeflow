import SwiftUI

@main
struct TradeFlowEarsApp: App {
    init() {
        // `-serverURL <url>` at launch (xcrun devicectl ... -- -serverURL ...) is saved, so
        // reopening from the home screen keeps using it.
        if let url = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)["serverURL"] as? String {
            UserDefaults.standard.setPersistentDomain(
                (UserDefaults.standard.persistentDomain(forName: Bundle.main.bundleIdentifier ?? "") ?? [:]).merging(["serverURL": url]) { $1 },
                forName: Bundle.main.bundleIdentifier ?? ""
            )
        }
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .preferredColorScheme(.dark)
        }
    }
}

/// First run asks for the TradeFlow server URL (your ngrok HTTPS URL). After that the app
/// opens straight into the Ears screen. Long-press with two fingers anywhere to change it.
struct RootView: View {
    @AppStorage("serverURL") private var serverURL = ""
    @State private var draft = ""
    @State private var editing = false
    @StateObject private var motion = HeadMotion()

    var body: some View {
        Group {
            if let url = URL(string: serverURL), !serverURL.isEmpty, !editing {
                WebShell(url: url, motion: motion)
                    .ignoresSafeArea()
                    .id(serverURL)
                    .simultaneousGesture(
                        LongPressGesture(minimumDuration: 1.2).onEnded { _ in
                            draft = serverURL
                            editing = true
                        }
                    )
            } else {
                setup
            }
        }
    }

    private var setup: some View {
        ZStack {
            Color(red: 0.05, green: 0.06, blue: 0.14).ignoresSafeArea()
            VStack(alignment: .leading, spacing: 18) {
                Text("TradeFlow")
                    .font(.system(size: 40, weight: .regular, design: .serif))
                    .foregroundStyle(.white)
                Text("Approve payments with a nod. Paste the address of your TradeFlow server.")
                    .foregroundStyle(.white.opacity(0.6))
                TextField("https://your-tunnel.ngrok-free.app/ears", text: $draft)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .padding(14)
                    .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
                    .foregroundStyle(.white)
                Button {
                    var s = draft.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !s.hasPrefix("http") { s = "https://" + s }
                    if URL(string: s)?.path.isEmpty ?? true { s += "/ears" }
                    serverURL = s
                    editing = false
                } label: {
                    Text("Open").bold().frame(maxWidth: .infinity).padding(.vertical, 14)
                }
                .background(Color(red: 0.96, green: 0.72, blue: 0.25), in: RoundedRectangle(cornerRadius: 14))
                .foregroundStyle(Color(red: 0.05, green: 0.06, blue: 0.14))
                HStack(spacing: 8) {
                    Circle().fill(motion.available ? .green : .orange).frame(width: 8, height: 8)
                    Text(motion.available ? "Head tracking available" : "Connect AirPods Pro / Max / 3rd gen for head tracking")
                        .font(.footnote).foregroundStyle(.white.opacity(0.6))
                }
                Spacer()
            }
            .padding(24)
        }
        .onAppear {
            if draft.isEmpty { draft = serverURL }
            motion.start()
        }
    }
}
