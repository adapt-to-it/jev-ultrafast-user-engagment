"""Versioned device profiles and the CDP calls that apply them to one page session.

Mobile follows Lighthouse-style throttling: 150 ms RTT, 1.6 Mbps down (209,715 B/s), 750 Kbps up (96,000 B/s) and a
4x CPU slowdown. User agents are ordinary Chrome strings; the Chrome major version follows the running browser.

The UA string and its client hints (Sec-CH-UA*, navigator.userAgentData) are one identity surface, so both are set
together; otherwise a "mobile" profile would receive the desktop or headless variant of a shop. That is the whole
override. We do NOT touch navigator.webdriver, plugins, WebGL, canvas or fonts, pass
--disable-blink-features=AutomationControlled, inject stealth scripts or handle CAPTCHAs: a challenge page stays
"not assessable".

Input: mobile emulates touch (coarse pointer, no hover, 5 touch points). Desktop keeps the browser's mouse: headless
Chromium launched by chrome.launch_chromium reports a fine pointer with hover, like a real desktop.
"""

import copy
import re

PROFILES_VERSION = "profiles.v1"

DEVICE_PROFILES: dict[str, dict] = {
    "mobile": {
        "metrics": {"width": 390, "height": 844, "deviceScaleFactor": 3, "mobile": True},
        "network": {"latency": 150, "downloadThroughput": 209715, "uploadThroughput": 96000},
        "cpu_rate": 4,
        "touch": True,
        "user_agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Mobile Safari/537.36",
        "platform": "Linux armv81",
        "ua_metadata": {"platform": "Android", "platformVersion": "10.0.0", "architecture": "", "model": "K",
                        "mobile": True},
    },
    "desktop": {
        "metrics": {"width": 1366, "height": 768, "deviceScaleFactor": 1, "mobile": False, "screenWidth": 1366,
                    "screenHeight": 768},
        "network": None,
        "cpu_rate": 1,
        "touch": False,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36",
        "platform": "Win32",
        "ua_metadata": {"platform": "Windows", "platformVersion": "15.0.0", "architecture": "x86", "model": "",
                        "mobile": False, "bitness": "64"},
    },
}

NO_THROTTLING = {"latency": 0, "downloadThroughput": -1, "uploadThroughput": -1}


def accept_language(lang: str) -> str:
    """Chrome adds the q-values itself: "it-IT" -> "it-IT,it,en" is sent as "it-IT,it;q=0.9,en;q=0.8"."""
    base = lang.split("-")[0]
    return ",".join([lang, *([base] if base != lang else []), *(["en"] if base != "en" else [])])


def new_context(transport) -> str:
    return transport.call("Target.createBrowserContext", disposeOnDetach=True)["browserContextId"]


def close_context(transport, context_id) -> None:
    try:
        transport.call("Target.disposeBrowserContext", browserContextId=context_id)
    except RuntimeError:
        pass  # Already disposed, or the browser is gone.


def _major(text: str) -> str | None:
    match = re.search(r"Chrome/(\d+)", text or "")
    return match[1] if match else None


def _browser_major(transport) -> str | None:
    try:
        version = transport.call("Browser.getVersion")
    except RuntimeError:
        return None
    return _major(version.get("userAgent") or version.get("product"))


def apply_profile(transport, session_id, profile: str | dict, *, cache_disabled=True, lang="it-IT") -> dict:
    """Apply a named profile, or a dict overriding the desktop profile, to one fresh page session (before navigation).

    A fresh target has touch emulation off, so a profile without touch leaves it alone: turning it "off" explicitly
    resets headless Chromium's pointer and hover to none.
    """
    if isinstance(profile, str):
        name, p = profile, copy.deepcopy(DEVICE_PROFILES[profile])
    else:
        name, p = profile.get("name", "custom"), {**copy.deepcopy(DEVICE_PROFILES["desktop"]), **profile}
    major = _browser_major(transport) or _major(p["user_agent"]) or "141"
    user_agent = re.sub(r"Chrome/[\d.]+", f"Chrome/{major}.0.0.0", p["user_agent"])
    brands = [{"brand": "Google Chrome", "version": major}, {"brand": "Chromium", "version": major},
              {"brand": "Not?A_Brand", "version": "24"}]
    metadata = {"brands": brands, "fullVersionList": [{**b, "version": f"{b['version']}.0.0.0"} for b in brands],
                "fullVersion": f"{major}.0.0.0", **p.get("ua_metadata", {})}
    touch = bool(p.get("touch"))
    network = p.get("network") or NO_THROTTLING
    language = accept_language(lang)

    def call(method, **params):
        return transport.call(method, session_id, **params)

    call("Network.enable")
    call("Network.setCacheDisabled", cacheDisabled=cache_disabled)
    call("Network.emulateNetworkConditions", offline=False, **network)
    call("Emulation.setDeviceMetricsOverride", **p["metrics"])
    call("Emulation.setUserAgentOverride", userAgent=user_agent, acceptLanguage=language,
         platform=p.get("platform", ""), userAgentMetadata=metadata)
    if touch:
        call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
    call("Emulation.setCPUThrottlingRate", rate=p.get("cpu_rate", 1))
    call("Emulation.setFocusEmulationEnabled", enabled=True)
    return {
        "name": name,
        "version": PROFILES_VERSION,
        "metrics": p["metrics"],
        "network": p.get("network"),
        "cpu_rate": p.get("cpu_rate", 1),
        "touch": touch,
        "user_agent": user_agent,
        "ua_metadata": metadata,
        "accept_language": language,
        "cache_disabled": cache_disabled,
    }
