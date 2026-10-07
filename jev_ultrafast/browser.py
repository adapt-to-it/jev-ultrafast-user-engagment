"""Observed actions over one CDP session (Browser Harness, or an explicit transport); no per-step subprocess."""

import hashlib
import json
import re
import sys
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
DEFAULT_METRICS = {"width": 1120, "height": 780, "deviceScaleFactor": 1, "mobile": False}
# The read-only ready poll tolerates what Chrome answers while a redirect swaps the document (CDP errors such as
# "Execution context was destroyed") until load_timeout. Fatal at once: the page's session is gone (SessionNotFound,
# -32001, or the daemon's "not_attached"), and any error Chrome did not send (a daemon or connection failure).
SESSION_GONE = re.compile(r"session with given id not found|not_attached|-32001", re.I)
NO_TARGET = re.compile(r"no target with given id", re.I)
CDP_CODE = re.compile(r"""\s*\{\s*['"]code['"]\s*:\s*(-?\d+)""")  # the daemon relays Chrome's error as str(dict)


def cdp_code(error):
    """The error code Chrome answered with (CdpError.code, or parsed from the daemon's message); None otherwise."""
    code = getattr(error, "code", None)
    if code is None and (match := CDP_CODE.match(str(error))):
        code = int(match[1])
    return code


def session_gone(error):
    return cdp_code(error) == -32001 or bool(SESSION_GONE.search(str(error)))


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


def send(transport, method, session_id=None, **params):
    """Browser Harness by default; an explicit transport (engagement audits) when one is given.

    On a transport, a `timeout` keyword is the client's reply wait in seconds (CdpTransport.call); on the default
    path it reaches CDP as a method parameter (e.g. Runtime.evaluate's evaluation timeout in milliseconds).
    """
    if transport is None:
        return cdp(method, session_id=session_id, **params)
    return transport.call(method, session_id, **params)


class Browser:
    transport = None
    navigation = None  # the Page.navigate result of the constructor's load (errorText when it did not commit)

    def __init__(self, url, *, transport=None, metrics=None, browser_context_id=None, background=True,
                 prepare=None, load_timeout=15):
        self.transport = transport
        if transport is None:
            ensure_daemon()
        target = {"url": "about:blank", "background": background}
        if browser_context_id:
            target["browserContextId"] = browser_context_id
        self.target = send(transport, "Target.createTarget", **target)["targetId"]
        try:
            self.session = send(transport, "Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
            if transport is not None:
                self.call("Page.enable")  # The transport answers native dialogs, which Chrome reports only now.
            self.call("Emulation.setDeviceMetricsOverride", **(metrics or DEFAULT_METRICS))
            # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
            self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            if prepare:
                prepare(self)
            self.navigation = self.navigate(url, load_timeout=load_timeout)
        except BaseException:
            try:
                self.close()  # The caller never gets this tab, so do not leak it.
            except Exception:
                pass
            raise

    def navigate(self, url, *, load_timeout=15):
        """Send Page.navigate once, then poll readyState read-only. Returns the Page.navigate result (errorText).

        On a transport each call waits until the deadline, at least 0.5 s. A page that never answered raises its last
        error (TimeoutError when its renderer stays blocked); one that did returns at the deadline, for collectors to
        settle. An error Chrome did not send, or a gone session, raises at once.
        """
        deadline = time.monotonic() + load_timeout
        result = self._within(deadline, "Page.navigate", url=url)
        seen_document, last_error = False, None
        while time.monotonic() < deadline:
            try:
                response = self._within(deadline, "Runtime.evaluate", expression="document.readyState",
                                        returnByValue=True)
                if response.get("exceptionDetails"):
                    raise StalePage("Document changed during evaluation")
                seen_document = True
                if response.get("result", {}).get("value") == "complete":
                    break
            except StalePage as exc:
                last_error = exc
            except TimeoutError as exc:
                last_error = exc  # The deadline passed while the renderer was busy.
                break
            except RuntimeError as exc:
                if session_gone(exc) or cdp_code(exc) is None:
                    raise  # A closed tab, a crashed renderer or a broken daemon is not a redirect.
                last_error = exc  # A redirect replaced the document during this read-only poll.
            time.sleep(0.02)
        if not seen_document and last_error is not None:
            raise last_error
        return result

    def call(self, method, **params):
        """One CDP call on this page's session (see send() for what `timeout` means on each path)."""
        return send(self.transport, method, self.session, **params)

    def _within(self, deadline, method, **params):
        """One call that waits no longer than the deadline (transports only; the daemon has its own IPC timeout)."""
        if self.transport is None:
            return self.call(method, **params)
        timeout = max(0.5, deadline - time.monotonic())
        return self.transport.call(method, self.session, timeout=timeout, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot}, self.transport
                )
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        result = browser_operation(
            {"operation": "act", "session": self.session, "action": action, "text": text}, self.transport
        )
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def close(self):
        target, self.target = getattr(self, "target", None), None
        if not target:
            return
        try:
            send(self.transport, "Target.closeTarget", targetId=target)
        except RuntimeError as exc:
            if not NO_TARGET.search(str(exc)):  # A tab that already closed itself is closed.
                raise
        finally:
            # Events still buffered for this page can no longer matter; drop them so the buffer stays small.
            if (discard := getattr(self.transport, "discard", None)) and getattr(self, "session", None):
                discard(self.session)


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def capture_screenshot(transport, session, **params):
    """Page.captureScreenshot. It is read-only, so on a transport a capture that stalls (seen now and then on
    background tabs of headless Chromium, while the page answers everything else) is asked for once more."""
    if transport is None:
        return send(None, "Page.captureScreenshot", session, **params)
    try:
        return transport.call("Page.captureScreenshot", session, timeout=5, **params)
    except TimeoutError:
        return transport.call("Page.captureScreenshot", session, timeout=25, **params)


def browser_operation(request, transport=None):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        return send(transport, method, session, **params)

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              if (!e.contains(document.elementFromPoint(x,y))) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {x,y};
            })(""" + json.dumps(action) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = capture_screenshot(transport, session, format="jpeg", quality=72)["data"]
    return info
