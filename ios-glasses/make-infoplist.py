#!/usr/bin/env python3
"""Generate Sources/Info.plist.

The MWDAT block carries a ClientToken, so the plist is gitignored and rebuilt
from the registered RARE-Insight app instead of being committed. Run after
cloning, and any time project.yml changes.
"""
import plistlib, sys, pathlib

SRC = pathlib.Path("/Users/karansoin/Desktop/rare-insight-mvp/"
                   "IOS+VisionOS+WatchOS/Backend/Backend-Info.plist")
OUT = pathlib.Path(__file__).parent / "Sources" / "Info.plist"

mwdat = plistlib.loads(SRC.read_bytes()).get("MWDAT")
if not mwdat:
    sys.exit(f"no MWDAT dict in {SRC}")

plist = {
    "CFBundleDevelopmentRegion": "en",
    "CFBundleExecutable": "$(EXECUTABLE_NAME)",
    "CFBundleIdentifier": "$(PRODUCT_BUNDLE_IDENTIFIER)",
    "CFBundleInfoDictionaryVersion": "6.0",
    "CFBundleName": "$(PRODUCT_NAME)",
    "CFBundleDisplayName": "OnCall Glasses",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "1.0",
    "CFBundleVersion": "1",
    "UILaunchScreen": {},
    "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait"],

    # Credentials for the Device Access Toolkit. TeamID is $(DEVELOPMENT_TEAM),
    # so signing team is whatever Xcode uses — it is not pinned here.
    "MWDAT": {**mwdat, "TeamID": "R865YU98DC"},

    # Meta AI returns through this scheme after pairing. Without it the flow
    # leaves the app and never comes back.
    "CFBundleURLTypes": [{
        "CFBundleURLName": "com.cortexon.rareinsight",
        "CFBundleURLSchemes": [mwdat.get("AppLinkURLScheme", "").rstrip(":/")],
    }],

    "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    "NSLocalNetworkUsageDescription":
        "Sends point-of-view frames to the OnCall backend on this network.",
    "NSBluetoothAlwaysUsageDescription":
        "Connects to Meta Ray-Ban Display glasses to receive the camera stream.",
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_bytes(plistlib.dumps(plist))
print(f"wrote {OUT}")
print("  MWDAT keys:", sorted(mwdat))
print("  URL scheme:", plist["CFBundleURLTypes"][0]["CFBundleURLSchemes"])
