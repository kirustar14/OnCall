# OnCall — Glasses relay (iOS)

Streams the Meta Ray-Ban Display world-facing camera to the phone in real time,
and posts one JPEG a second to the OnCall backend.

This exists because the glasses camera is the one sensor a web app cannot reach.
Web Apps on Ray-Ban Display get display, sensors, Neural Band input and network;
camera and microphone belong to the native Device Access Toolkit. So this is a
thin relay and nothing else — glasses to phone over Bluetooth, phone to backend
over HTTP. No inference, no storage, no clinical logic on the phone.

The backend cannot tell a glasses frame from a webcam frame, and does not need
to. Both post the same JSON to `/api/frame`, and exactly one frame is ever
looked at: the one nearest a moment that already mattered.

## Two files

| | |
|---|---|
| `OnCallGlassesApp.swift` | `@main`, SDK configure, the Meta AI return URL, and a status screen |
| `GlassesStreamer.swift` | registration, device session, camera stream, JPEG, POST |

## Build

Needs a **physical iPhone** — the Device Access Toolkit does not work in the
simulator — with the Meta AI app installed and the glasses already paired to it.

1. **New Xcode project** → iOS → App → SwiftUI. Delete the generated
   `ContentView.swift` and `…App.swift`, then drag in the two files here.

2. **Add the SDK.** File → Add Package Dependencies →
   `https://github.com/facebook/meta-wearables-dat-ios` (v0.7.0 or later).
   Add `MWDATCore` and `MWDATCamera` to the target.

3. **Register the app** in the Meta developer console. The **bundle identifier
   and display name must match exactly** — `Wearables.configure()` validates
   both and fails at launch on a mismatch, not at pairing time.

4. **URL scheme.** Target → Info → URL Types → add the scheme from the console.
   Without it, Meta AI completes pairing and never returns to the app, which
   looks exactly like registration silently failing.

5. **Info.plist — two entries that are easy to miss:**

   ```xml
   <key>NSLocalNetworkUsageDescription</key>
   <string>Sends point-of-view frames to the OnCall backend on this network.</string>

   <!-- The backend runs over plain HTTP on the LAN during a demo. -->
   <key>NSAppTransportSecurity</key>
   <dict><key>NSAllowsLocalNetworking</key><true/></dict>
   ```

   Missing the first gives a silent permission prompt the first time you post;
   missing the second makes every POST fail with a TLS error.

6. **Run on the device**, then in the app:
   - **Backend** — your laptop's LAN address, e.g. `http://192.168.1.10:8000`.
     Not `localhost`: on the phone that means the phone.
   - **case id** — copy it from the web app's URL.
   - **Pair glasses** → hands off to Meta AI → returns here as `registered`.
   - **Start streaming** → frames appear in the preview.

## Verifying it actually works

The live preview is the point. If images are moving there, the Bluetooth link is
genuinely live and real time — that is the thing worth checking before anything
else.

Then confirm the backend is receiving:

```sh
curl -s localhost:8000/api/frame -H 'Content-Type: application/json' \
  -d '{"case_id":"<case>","image_b64":"","media_type":"image/jpeg","captured_at":0,"source":"test"}'
```

A real case returns `{"buffered": N}`; an unknown one returns an error. Once the
phone is posting, `N` should climb by roughly one a second.

## Notes from the SDK that cost real time

Both of these come from the working RARE-Insight integration against DAT v0.7.0:

- **The `AutoDeviceSelector` must be held strongly.** Created inline and allowed
  to deallocate, `createSession()` throws `noEligibleDevice` even with the
  glasses connected and reporting the capability.
  (facebook/meta-wearables-dat-ios#148)

- **Wait for the first `activeDeviceStream` emission before `createSession()`.**
  Calling earlier reproduces the same `noEligibleDevice` for a different reason.

- **Use `.raw`, not `.hvc1`.** `.hvc1` delivers encoded samples whose image
  buffer is `nil`, so there is nothing to turn into a JPEG without a
  VideoToolbox decode step first.

- Every `listen()` token must be retained, or the listener is silently dropped.

## What this is not

No frames are stored, and none are analysed on the phone. Streaming is
continuous; **sending is not** — one frame a second is posted, and the backend
only ever describes the single frame nearest an alert. A shift of video never
reaches a model.
