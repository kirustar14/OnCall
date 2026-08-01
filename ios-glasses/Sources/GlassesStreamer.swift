//  GlassesStreamer.swift
//
//  Streams the Meta Ray-Ban Display world-facing camera to the phone in real
//  time, and posts one small JPEG per second to the OnCall backend.
//
//  The glasses camera is the only sensor a web app cannot reach — Web Apps on
//  Ray-Ban Display get display, sensors, Neural Band input and network, while
//  camera and microphone belong to the native Device Access Toolkit. So this is
//  a thin native relay and nothing more: glasses -> phone over Bluetooth, phone
//  -> backend over HTTP. All reasoning stays server-side.
//
//  Frames arrive continuously; only one a second is posted. The backend keeps a
//  short ring buffer and looks up the frame nearest a moment that already
//  mattered, so a whole shift of video never reaches a model.
//
//  API usage follows Meta Wearables DAT for iOS v0.7.0. Two behaviours in that
//  SDK are non-obvious and cost real time if missed; both are called out below.

import Foundation
import CoreMedia
import CoreImage
import MWDATCore
import MWDATCamera
import os

@MainActor
@Observable
final class GlassesStreamer {

    // MARK: - Configuration

    /// Where the OnCall backend is reachable from the phone. This is a LAN
    /// address, not localhost — the phone is a different machine to the laptop.
    var backendBaseURL: String = "http://192.168.1.10:8000"

    /// Which case the frames belong to. Copy it from the web app's URL.
    var caseID: String = ""

    /// Post rate. The camera delivers 15 fps; one a second is plenty to land
    /// within a second of any moment, and keeps this to ~40 KB/s.
    private let postInterval: TimeInterval = 1.0

    // MARK: - Observable state

    /// Where start() actually got to. Every step reports, because the SDK's
    /// failure modes are silent: an unregistered device simply never emits,
    /// and the session call that follows would wait forever.
    private(set) var step: String = "idle"
    private(set) var registration: String = "unknown"
    private(set) var streamState: String = "stopped"
    private(set) var framesReceived: Int = 0
    private(set) var framesPosted: Int = 0
    private(set) var lastError: String?
    /// Most recent frame, for the on-phone preview — this is what proves the
    /// glasses link is live and real time.
    private(set) var previewImage: CGImage?

    // MARK: - Internals

    private let log = Logger(subsystem: "app.oncall.glasses", category: "stream")
    private let wearables: any WearablesInterface
    private let ciContext = CIContext()
    private let session = URLSession(configuration: .ephemeral)

    private var deviceSession: DeviceSession?
    private var stream: MWDATCamera.Stream?
    private var tokens: [any AnyListenerToken] = []

    /// The AutoDeviceSelector MUST be held strongly for the lifetime of the
    /// session. If it is created inline and allowed to deallocate,
    /// createSession() throws noEligibleDevice even when the glasses are
    /// connected and report the capability. (facebook/meta-wearables-dat-ios#148)
    private var deviceSelector: AutoDeviceSelector?

    private var lastPostAt: Date = .distantPast
    private var posting = false

    init(wearables: any WearablesInterface, configureError: String? = nil) {
        self.wearables = wearables
        if let configureError {
            // Without this the app launches, looks fine, and nothing ever works.
            self.lastError = "Wearables.configure() failed: \(configureError)"
            self.step = "SDK not configured"
        }
        observeRegistration()
    }

    // MARK: - Registration

    private func observeRegistration() {
        Task { [weak self] in
            guard let self else { return }
            for await state in self.wearables.registrationStateStream() {
                self.registration = Self.label(state)
            }
        }
    }

    /// Hands off to the Meta AI app, which performs the actual pairing and
    /// returns via the app's URL scheme.
    func register() async {
        do {
            try await wearables.startRegistration()
        } catch {
            lastError = "registration failed: \(error)"
        }
    }

    // MARK: - Streaming

    func start() async {
        lastError = nil
        step = "creating selector"
        do {
            // Hold the selector — see the note on the property.
            // Filter on supportsDisplay(), matching the working RARE-Insight
            // integration. There is no supportsCamera() predicate — camera
            // access is an extension on DeviceSession — and on Ray-Ban Display
            // the camera shares the same session the display owns, so the
            // display capability is the right proxy for "this is the device".
            // Selecting with no predicate at all picks up whatever is paired,
            // including devices that cannot serve a camera stream.
            let selector = AutoDeviceSelector(
                wearables: wearables,
                filter: { $0.supportsDisplay() }
            )
            deviceSelector = selector

            // Wait for the selector to resolve a device before creating the
            // session: createSession() called before the first
            // activeDeviceStream emission throws noEligibleDevice even when a
            // suitable device is connected.
            //
            // Bounded, because if the glasses are not registered the stream
            // simply never emits — and an unbounded await here looks exactly
            // like a button that does nothing.
            step = "waiting for a device"
            let resolved = await withTaskGroup(of: Bool.self) { group -> Bool in
                group.addTask {
                    for await activeID in selector.activeDeviceStream()
                    where activeID != nil { return true }
                    return false
                }
                group.addTask {
                    try? await Task.sleep(for: .seconds(12))
                    return false
                }
                let first = await group.next() ?? false
                group.cancelAll()
                return first
            }

            guard resolved else {
                step = "no device"
                lastError = "No glasses became active in 12s. Registration is "
                    + "\(registration) — pair first, and check they are connected in Meta AI."
                return
            }

            // createSession is synchronous-throwing in v0.7.0.
            step = "creating session"
            let newSession = try wearables.createSession(deviceSelector: selector)
            deviceSession = newSession
            step = "starting session"
            try await newSession.start()

            // .raw so frames arrive as decoded CVPixelBuffers. .hvc1 delivers
            // encoded samples whose image buffer is nil, which would need a
            // VideoToolbox decode before anything could be made of them.
            let config = StreamConfiguration(
                videoCodec: .raw,
                resolution: .low,      // 360x640 — ample for a look-back frame
                frameRate: 15
            )
            step = "adding camera stream"
            guard let newStream = try newSession.addStream(config: config) else {
                step = "no stream"
                lastError = "addStream returned nil — the device session has no camera capability."
                return
            }
            stream = newStream

            // Every listen() token must be retained or the listener is dropped.
            tokens.append(newStream.statePublisher.listen { [weak self] (state: StreamState) in
                Task { @MainActor in self?.streamState = String(describing: state) }
            })
            tokens.append(newStream.errorPublisher.listen { [weak self] (error: StreamError) in
                Task { @MainActor in self?.lastError = String(describing: error) }
            })
            tokens.append(newStream.videoFramePublisher.listen { [weak self] (frame: VideoFrame) in
                Task { @MainActor in self?.handle(frame) }
            })

            step = "starting stream"
            await newStream.start()
            streamState = "starting"
        } catch {
            step = "failed"
            lastError = "\(step) failed: \(error)"
            log.error("start failed: \(String(describing: error))")
        }
    }

    func stop() async {
        await stream?.stop()
        for token in tokens { await token.cancel() }
        tokens.removeAll()
        stream = nil
        await deviceSession?.stop()
        deviceSession = nil
        deviceSelector = nil
        streamState = "stopped"
    }

    // MARK: - Frames

    private func handle(_ frame: VideoFrame) {
        framesReceived += 1

        guard let pixelBuffer = CMSampleBufferGetImageBuffer(frame.sampleBuffer) else { return }
        let image = CIImage(cvPixelBuffer: pixelBuffer)

        // Preview every frame — this is the real-time evidence that the link is
        // live. Posting is throttled separately.
        if let cgImage = ciContext.createCGImage(image, from: image.extent) {
            previewImage = cgImage
        }

        let now = Date()
        guard !posting,
              now.timeIntervalSince(lastPostAt) >= postInterval,
              !caseID.isEmpty
        else { return }

        lastPostAt = now
        guard let jpeg = ciContext.jpegRepresentation(
            of: image,
            colorSpace: CGColorSpaceCreateDeviceRGB(),
            options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.6]
        ) else { return }

        posting = true
        Task { await post(jpeg: jpeg, capturedAt: now) }
    }

    private func post(jpeg: Data, capturedAt: Date) async {
        defer { posting = false }

        guard let url = URL(string: backendBaseURL.trimmingCharacters(in: .whitespaces) + "/api/frame") else {
            lastError = "bad backend URL"
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 4
        request.httpBody = try? JSONSerialization.data(withJSONObject: [
            "case_id": caseID,
            "image_b64": jpeg.base64EncodedString(),
            "media_type": "image/jpeg",
            // Epoch seconds, matching the backend's clock domain. The whole
            // look-back mechanism turns on this being honest.
            "captured_at": capturedAt.timeIntervalSince1970,
            "source": "glasses",
        ])

        do {
            let (_, response) = try await session.data(for: request)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                lastError = "backend returned \(http.statusCode)"
                return
            }
            framesPosted += 1
            lastError = nil
        } catch {
            // A dropped frame is not worth interrupting a resuscitation over.
            // Surface it, keep streaming.
            lastError = "post failed: \(error.localizedDescription)"
        }
    }

    // MARK: - Helpers

    private static func label(_ state: RegistrationState) -> String {
        switch state {
        case .unavailable: return "unavailable"
        case .available:   return "available"
        case .registering: return "registering…"
        case .registered:  return "registered"
        @unknown default:  return "unknown"
        }
    }
}
