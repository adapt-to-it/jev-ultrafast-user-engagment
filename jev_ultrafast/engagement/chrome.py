"""Find and launch a Chromium we own: headless-new (pages stay "visible"), temporary profile, free DevTools port."""

import atexit
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

PATH_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")
PLAYWRIGHT_ROOTS = ("/opt/pw-browsers", "~/.cache/ms-playwright")
APP_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)
PROFILE_PREFIX = "jev-chrome-"
STALE_UNOWNED_S = 600  # a profile without owner.json is still being launched, unless it is this old
# Headless Chromium reports no pointer and no hover; a desktop shop must see the mouse a desktop user has, so
# hover menus and `(pointer: fine)` code paths render. Touch emulation (mobile profile) overrides both per page.
HEADLESS_MOUSE = ("--blink-settings=primaryHoverType=2,availableHoverTypes=2,"
                  "primaryPointerType=4,availablePointerTypes=4")  # hover: hover, pointer: fine

_OPEN: list["LaunchedChrome"] = []  # browsers this process launched and has not closed yet
_OPEN_LOCK = threading.Lock()


def _executable(path):
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _build_order(path):
    """Playwright builds, newest first: chromium-1194 before chromium-1181, plain builds before tip-of-tree."""
    name = Path(path).parents[1].name
    numbers = re.findall(r"\d+", name)
    return bool(re.fullmatch(r"chromium-\d+", name)), int(numbers[-1]) if numbers else 0


def find_chromium() -> str | None:
    for key in ("JEV_CHROME_PATH", "CHROME_PATH", "BH_CHROME_PATH"):
        if not (value := os.environ.get(key)):
            continue
        if _executable(path := os.path.expanduser(value)):
            return path
        warnings.warn(f"{key}={value!r} is not an executable file; looking for Chromium elsewhere", stacklevel=2)
    for name in PATH_NAMES:
        if path := shutil.which(name):
            return path
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), *PLAYWRIGHT_ROOTS]
    for root in filter(None, roots):
        builds = glob.glob(os.path.join(os.path.expanduser(root), "chromium-*", "chrome-linux", "chrome"))
        for path in sorted(builds, key=_build_order, reverse=True):
            if _executable(path):
                return path
    return next((path for path in APP_PATHS if _executable(path)), None)


@dataclass
class LaunchedChrome:
    ws_url: str
    process: subprocess.Popen
    user_data_dir: str

    def close(self) -> None:
        with _OPEN_LOCK:
            _OPEN[:] = [chrome for chrome in _OPEN if chrome is not self]
        if self.process.poll() is None:
            self._signal(signal.SIGTERM)
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
                self.process.wait(5)
        try:  # A signal skips Chromium's own cleanup of its singleton dir when that lives outside the profile.
            singleton = Path(os.readlink(Path(self.user_data_dir) / "SingletonSocket")).parent
            if singleton.name.startswith((".org.chromium.", ".com.google.Chrome.")):
                shutil.rmtree(singleton, ignore_errors=True)
        except OSError:
            pass
        shutil.rmtree(self.user_data_dir, ignore_errors=True)

    def _signal(self, sig):
        # The browser runs in its own process group, so renderers and helpers stop with it.
        try:
            if os.name == "posix":
                os.killpg(self.process.pid, sig)
            elif sig == signal.SIGTERM:
                self.process.terminate()
            else:
                self.process.kill()
        except (ProcessLookupError, PermissionError):
            pass


def close_all() -> None:
    """Close every browser this process launched (registered with atexit; also safe to call directly)."""
    with _OPEN_LOCK:
        browsers = list(_OPEN)
    for chrome in browsers:
        try:
            chrome.close()
        except Exception:
            pass


atexit.register(close_all)


def _alive(pid) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Alive, owned by another user.
    except OSError:
        return False
    return True


def _uses_profile(pid, profile) -> bool | None:
    """Whether /proc shows `profile` in pid's command line; None when there is no /proc to ask."""
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
    except FileNotFoundError:
        return None if not os.path.isdir("/proc/self") else False
    except OSError:
        return False
    return any(arg in (profile, f"--user-data-dir={profile}") for arg in args)


def _profile_in_use(profile) -> bool:
    if not os.path.isdir("/proc/self"):
        return False
    return any(entry.isdigit() and _uses_profile(int(entry), profile) for entry in os.listdir("/proc"))


def sweep_stale_profiles(root: str | None = None) -> list[str]:
    """Remove profiles (and stop browsers) left by processes that died without closing them. Never raises.

    A profile is stale when its owner.json names a dead owner, or when it has no owner.json, is older than
    STALE_UNOWNED_S and no process uses it. Its browser is killed only when /proc shows that exact profile in the
    browser's command line (a reused pid is never signalled); without /proc a live browser pid is left alone.
    """
    removed = []
    try:
        candidates = glob.glob(os.path.join(root or tempfile.gettempdir(), PROFILE_PREFIX + "*"))
    except Exception:
        return removed
    for profile in candidates:
        try:
            if not os.path.isdir(profile) or os.path.islink(profile):
                continue
            owner_file = Path(profile) / "owner.json"
            try:
                owner = json.loads(owner_file.read_text())
            except FileNotFoundError:
                owner = None
            except ValueError:
                owner = {}  # Torn while being written by a process that then died: judged by its age.
            if owner is None or not isinstance(owner, dict) or "owner_pid" not in owner:
                if time.time() - os.path.getmtime(profile) < STALE_UNOWNED_S or _profile_in_use(profile):
                    continue
                owner = {}
            elif _alive(owner["owner_pid"]):
                continue
            chrome_pid = owner.get("chrome_pid")
            if _alive(chrome_pid):
                uses = _uses_profile(chrome_pid, profile)
                if uses is None:
                    continue  # No /proc: this browser cannot be told from a reused pid, so leave both alone.
                if uses:
                    try:
                        os.killpg(chrome_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            shutil.rmtree(profile, ignore_errors=True)
            removed.append(profile)
        except Exception:
            continue
    return removed


def launch_chromium(*, path=None, headless=True, window=(1366, 768), lang="it-IT", extra_args=()) -> LaunchedChrome:
    path = path or find_chromium()
    if not path:
        raise FileNotFoundError("No Chromium found. Set JEV_CHROME_PATH to a Chrome or Chromium executable.")
    sweep_stale_profiles()
    profile = tempfile.mkdtemp(prefix=PROFILE_PREFIX)
    # Chromium's own temp files (the ProcessSingleton socket dir among them) live in the profile, so closing the
    # browser with a signal, which skips Chromium's cleanup, leaves nothing in the system temp dir. Only where the
    # socket path stays within the 104-byte sun_path limit (not under macOS's long temp dir; close() covers that).
    # Linux verified, macOS by inspection.
    temp = Path(profile) / "tmp"
    # The crash database follows the default user data dir, not --user-data-dir: CHROME_CONFIG_HOME (Linux) keeps
    # it out of ~/.config.
    env = {**os.environ, "LANGUAGE": lang.replace("-", "_"), "CHROME_CONFIG_HOME": str(Path(profile) / "config")}
    if os.name == "posix" and len(str(temp)) + len("/.org.chromium.Chromium.XXXXXX/SingletonSocket") < 104:
        env["TMPDIR"] = str(temp)
    # A download link or an attachment response saves into the profile, never into the user's Downloads folder.
    prefs = {"download": {"default_directory": str(Path(profile) / "downloads"), "prompt_for_download": False}}
    args = [
        path,
        *(["--headless=new", HEADLESS_MOUSE] if headless else []),
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-dev-shm-usage",
        # Quiet the browser's own traffic (sync, component and variations updates, metrics) during audits.
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--disable-default-apps",
        "--metrics-recording-only",
        "--password-store=basic",
        "--use-mock-keychain",
        f"--window-size={window[0]},{window[1]}",
        f"--lang={lang}",
        *(["--no-sandbox"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []),
        *shlex.split(os.environ.get("JEV_CHROME_ARGS", "")),
        *extra_args,
        "about:blank",
    ]
    log_path = Path(profile) / "chrome-stderr.log"
    try:
        temp.mkdir()
        (Path(profile) / "Default").mkdir()
        (Path(profile) / "Default" / "Preferences").write_text(json.dumps(prefs))
        with open(log_path, "wb") as log:
            # Linux Chromium ignores --lang; its UI locale and default Accept-Language come from LANGUAGE.
            process = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, env=env,
                **({"start_new_session": True} if os.name == "posix" else {}),
            )
    except BaseException:
        shutil.rmtree(profile, ignore_errors=True)
        raise
    chrome = LaunchedChrome("", process, profile)
    with _OPEN_LOCK:
        _OPEN.append(chrome)
    try:
        owner = {"owner_pid": os.getpid(), "chrome_pid": process.pid, "created": time.time()}
        (Path(profile) / "owner.json").write_text(json.dumps(owner))
        port_file = Path(profile) / "DevToolsActivePort"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            lines = port_file.read_text().split() if port_file.exists() else []
            if len(lines) >= 2:
                chrome.ws_url = f"ws://127.0.0.1:{lines[0]}{lines[1]}"
                return chrome
            if process.poll() is not None:
                break
            time.sleep(0.05)
        code = process.poll()
        state = f"exited with code {code}" if code is not None else "did not open DevTools within 15 s"
        detail = log_path.read_text(errors="replace")[-1500:]
        raise RuntimeError(f"Chromium {state}: {path}\n{detail}".rstrip())
    except BaseException:
        chrome.close()
        raise
