//  OnCallGlassesApp.swift
//
//  Standalone iOS app: Meta Ray-Ban Display camera -> phone -> OnCall backend.
//
//  Deliberately small. It registers the glasses, opens one camera stream, shows
//  the live frames so you can see the link is real, and posts one JPEG a second
//  to the backend. No inference, no storage, no UI beyond what proves it works.
//
//  Everything clinical happens server-side, exactly as it does when the frame
//  comes from a laptop webcam — the backend cannot tell the difference and does
//  not need to.

import SwiftUI
import MWDATCore

@main
struct OnCallGlassesApp: App {

    @State private var streamer: GlassesStreamer

    init() {
        // configure() also validates the bundle identifier and display name
        // against what was registered in the Meta developer console — a
        // mismatch fails here rather than at pairing time.
        var configureError: String?
        do {
            try Wearables.configure()
        } catch {
            configureError = String(describing: error)
            NSLog("[OnCall] Wearables.configure() failed: \(error)")
        }
        _streamer = State(initialValue: GlassesStreamer(wearables: Wearables.shared,
                                                        configureError: configureError))
    }

    var body: some Scene {
        WindowGroup {
            ContentView(streamer: streamer)
                // Meta AI returns here after pairing. Without this the
                // registration flow leaves and never comes back.
                .onOpenURL { url in
                    // handleUrl, not handleURL — and it is async throws in
                    // DAT 0.8.0, where the registration/permission URL handlers
                    // were consolidated onto Wearables itself.
                    Task { _ = try? await Wearables.shared.handleUrl(url) }
                }
        }
    }
}

struct ContentView: View {
    @Bindable var streamer: GlassesStreamer

    var body: some View {
        NavigationStack {
            Form {
                Section("Backend") {
                    TextField("http://192.168.1.10:8000", text: $streamer.backendBaseURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("case id", text: $streamer.caseID)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Text("The phone and the laptop must be on the same network. "
                         + "localhost is the phone, not the laptop.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section("Glasses") {
                    LabeledContent("Step", value: streamer.step)
                    LabeledContent("Registration", value: streamer.registration)
                    LabeledContent("Stream", value: streamer.streamState)
                    LabeledContent("Frames received", value: "\(streamer.framesReceived)")
                    LabeledContent("Frames posted", value: "\(streamer.framesPosted)")

                    if let error = streamer.lastError {
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                }

                Section("Live from the glasses") {
                    // The point of this view: if frames are moving here, the
                    // Bluetooth link is genuinely live and real time.
                    if let cgImage = streamer.previewImage {
                        Image(decorative: cgImage, scale: 1.0, orientation: .up)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                    } else {
                        Text("No frames yet.")
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, minHeight: 120)
                    }
                }

                Section {
                    Button("Pair glasses") {
                        Task { await streamer.register() }
                    }
                    Button("Start streaming") {
                        Task { await streamer.start() }
                    }
                    Button("Stop", role: .destructive) {
                        Task { await streamer.stop() }
                    }
                }
            }
            .navigationTitle("OnCall — Glasses")
        }
    }
}
