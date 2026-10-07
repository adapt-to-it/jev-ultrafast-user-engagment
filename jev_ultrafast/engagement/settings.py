"""Settings of one engagement-readiness run: plain data, validated once.

to_dict() is this input shape. run.json stores the schema shape instead (RunRecord.settings: browser as
{mode, product, headless}, no url); from_dict() reads both.
"""

import math
import re
from dataclasses import asdict, dataclass, field, fields
from urllib.parse import urlsplit

from .profiles import DEVICE_PROFILES
from .schemas import STAGES

CONSENT_POLICIES = ("auto", "reject", "accept", "none")
LANGS = {"it": "it-IT", "en": "en-US"}
LOCALE_RE = r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*"  # "it", "en", "de-DE"
# A URL with user:password@ would carry them into the run, its progress messages and the shared report.
CREDENTIALS = ("the shop URL carries credentials (user:password@host): remove them, they would be stored in the run, "
               "its progress and its report (a shop behind HTTP authentication cannot be audited)")
TYPES = {
    "url": str, "profiles": (list, tuple), "stages": (list, tuple), "browser": str, "headless": bool, "locale": str,
    "consent": str, "repeats": int, "max_pages": int, "settle_timeout_s": (int, float),
    "artifacts_dir": (str, type(None)), "screenshots": bool,
}


def has_credentials(url: str) -> bool:
    """True when the URL has a userinfo part (user:password@ or a bare @) before its host."""
    return "@" in urlsplit(url).netloc


@dataclass
class EngagementSettings:
    url: str
    profiles: list[str] = field(default_factory=lambda: ["mobile", "desktop"])
    stages: list[str] = field(default_factory=lambda: list(STAGES))
    browser: str = "auto"  # auto | launch | harness | cdp:<ws-or-http-url>
    headless: bool = True
    locale: str = "it"
    consent: str = "auto"  # auto | reject | accept | none
    repeats: int = 1
    max_pages: int = 12
    settle_timeout_s: float = 20.0
    artifacts_dir: str | None = None
    screenshots: bool = True

    def __post_init__(self):
        for name, kind in TYPES.items():
            value = getattr(self, name)
            if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
                raise ValueError(f"{name} has the wrong type: {value!r}")
        self.profiles, self.stages = list(self.profiles), list(self.stages)
        parts = urlsplit(self.url)
        if has_credentials(self.url):  # before any message that would echo the URL
            raise ValueError(CREDENTIALS)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"Expected an http(s) shop URL, got {self.url!r}")
        for name, values, allowed in (("profiles", self.profiles, DEVICE_PROFILES), ("stages", self.stages, STAGES)):
            unknown = [v for v in values if not isinstance(v, str) or v not in allowed]
            if unknown or not values or len(set(values)) != len(values):
                raise ValueError(f"{name} must be distinct values of {list(allowed)}, got {values!r}")
        if not re.fullmatch(LOCALE_RE, self.locale):
            raise ValueError(f"locale must be a language tag such as 'it' or 'de-DE', got {self.locale!r}")
        if self.consent not in CONSENT_POLICIES:
            raise ValueError(f"consent must be one of {CONSENT_POLICIES}")
        endpoint = urlsplit(self.browser[4:]) if self.browser.startswith("cdp:") else None
        if self.browser not in ("auto", "launch", "harness") and not (
                endpoint and endpoint.scheme in ("ws", "wss", "http", "https") and endpoint.hostname):
            raise ValueError("browser must be auto, launch, harness or cdp:<ws(s) or http(s) DevTools URL>")
        if self.repeats < 1 or self.max_pages < 1 or not (math.isfinite(self.settle_timeout_s)
                                                          and self.settle_timeout_s > 0):
            raise ValueError("repeats and max_pages must be >= 1 and settle_timeout_s a finite number > 0")

    @property
    def lang(self) -> str:
        """BCP 47 tag for Chromium --lang and Accept-Language, e.g. "it" -> "it-IT"."""
        return LANGS.get(self.locale, self.locale)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict, *, url: str | None = None) -> "EngagementSettings":
        """Read to_dict() output, or RunRecord.settings with url=run["site"]["start_url"]. Unknown keys are ignored."""
        names = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in names}
        if isinstance(browser := data.get("browser"), dict):
            data["browser"] = browser.get("mode", "auto")
            data.setdefault("headless", browser.get("headless", True))
        if url is not None:
            data.setdefault("url", url)
        if "url" not in data:
            raise ValueError("Settings have no url; pass url=run['site']['start_url']")
        return cls(**data)
