"""Engagement-readiness audits of e-commerce shops: performance, element availability, friction and
trust / persuasion / dark-pattern risk signals, scored into versioned indices.

The scores are readiness estimates from synthetic sessions, not measured user engagement. The public names below are
imported on first use, so importing the package stays cheap.
"""

from importlib import import_module

_EXPORTS = {
    "audit_shop": ".audit",
    "JourneyRunner": ".journey",
    "EngagementService": ".service",
    "score_run": ".scoring",
    "build_report": ".report",
    "RunStore": ".store",
    "EngagementSettings": ".settings",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted({*globals(), *__all__})
