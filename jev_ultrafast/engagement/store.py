"""Run artifacts on disk: <root>/<host>/<run_id>/{run.json, steps.jsonl, snapshots/, shots/, report.*}."""

import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

from .schemas import SCHEMA_VERSION, RunRecord

try:
    import fcntl
except ImportError:  # Windows: writers are serialized within one process only.
    fcntl = None

RUN_ID_RE = r"^\d{8}T\d{6}\d{6}Z_(audit|journey)_[a-z0-9.-]+$"
RUN_KINDS = ("audit", "journey")
RESERVED = ("run.json", "steps.jsonl")  # written only under the run lock; dot files (.lock, temp files) as well
MAX_SLUG = 200  # run ids stay well below the 255-byte file name limit

_LOCKS: dict[str, list] = {}  # run dir -> [RLock, depth, open <run>/.lock holding the flock]
_LOCKS_GUARD = threading.Lock()


def artifacts_root(explicit: str | None = None) -> Path:
    root = explicit or os.environ.get("JEV_ENGAGEMENT_ARTIFACTS") or "artifacts/engagement"
    return Path(root).expanduser().resolve()


def iso_now(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _host_port(url_or_host: str) -> tuple[str, int | None]:
    parts = urlsplit(url_or_host if "//" in url_or_host else f"//{url_or_host}")
    host = parts.hostname or ""
    try:
        host = host.encode("idna").decode()
    except UnicodeError:
        pass
    try:
        return host, parts.port
    except ValueError:
        return host, None


def host_slug(url_or_host: str) -> str:
    """Directory-safe host: "https://Shop.example:8443/x" -> "shop.example-8443"."""
    host, port = _host_port(url_or_host)
    slug = re.sub(r"[^a-z0-9.-]+", "-", f"{host}-{port}" if port else host).strip(".-")
    return slug[:MAX_SLUG].strip(".-") or "unknown"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class RunStore:
    def __init__(self, root: str | Path | None = None):
        self.root = artifacts_root(str(root) if root is not None else None)

    def new_run(self, kind: str, start_url: str, settings: dict, *, now: datetime | None = None) -> str:
        if kind not in RUN_KINDS:
            raise ValueError(f"Run kind must be one of {RUN_KINDS}")
        slug = host_slug(start_url)
        now = (now or datetime.now(UTC)).astimezone(UTC)
        while True:  # Two runs in the same microsecond get consecutive ids.
            run_id = f"{now:%Y%m%dT%H%M%S%f}Z_{kind}_{slug}"
            try:
                (self.root / slug / run_id).mkdir(parents=True)
                break
            except FileExistsError:
                now += timedelta(microseconds=1)
        run: RunRecord = {
            "run_id": run_id,
            "schema_version": SCHEMA_VERSION,
            "kind": kind,
            "site": {"host": urlsplit(start_url).hostname or slug, "start_url": start_url},
            "created_at": iso_now(now),
            "finished_at": None,
            "settings": settings,
            "pages": [],
            "not_assessable": [],
            "deception": {},
            "journey": None,
            "observations": [],
            "judgments": {"tasks": [], "verdicts": [], "final": []},
            "scores": None,
            "status": "created",
            "warnings": [],
            "errors": [],
        }
        self.save(run_id, run)
        return run_id

    def path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not re.fullmatch(RUN_ID_RE, run_id, re.ASCII):
            raise ValueError(f"Invalid run id: {run_id!r}")
        slug = run_id.split("_", 2)[2]
        directory = (self.root / slug / run_id).resolve()
        if slug.startswith(".") or not directory.is_relative_to(self.root):
            raise ValueError(f"Invalid run id: {run_id!r}")
        return directory

    def _existing(self, run_id: str) -> Path:
        """The run's directory; writers never create a run that new_run did not."""
        directory = self.path(run_id)
        if not directory.is_dir():
            raise FileNotFoundError(f"Unknown run: {run_id}")
        return directory

    def _file(self, run_id: str, rel: str) -> Path:
        parts = PurePosixPath(rel).parts if isinstance(rel, str) else ()
        if (not parts or "\\" in rel or PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).drive
                or any(p in ("", ".", "..") or p.startswith(".") for p in parts) or parts[0].lower() in RESERVED):
            raise ValueError(f"Invalid artifact path: {rel!r}")
        directory = self._existing(run_id)
        target = (directory / rel).resolve()
        if not target.is_relative_to(directory):
            raise ValueError(f"Invalid artifact path: {rel!r}")
        return target

    @contextmanager
    def _lock(self, run_id: str):
        """Serialize writers of one run: threads with an RLock, processes (CLI next to the MCP server) with flock."""
        directory = self.path(run_id)
        with _LOCKS_GUARD:
            entry = _LOCKS.setdefault(str(directory), [threading.RLock(), 0, None])
        with entry[0]:
            if entry[1] == 0 and fcntl is not None and directory.is_dir():
                lock_file = open(directory / ".lock", "a+b")
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX)
                except BaseException:
                    lock_file.close()
                    raise
                entry[2] = lock_file
            entry[1] += 1
            try:
                yield
            finally:
                entry[1] -= 1
                if entry[1] == 0 and entry[2] is not None:
                    entry[2].close()  # Closing releases the flock.
                    entry[2] = None

    def load(self, run_id: str) -> RunRecord:
        path = self.path(run_id) / "run.json"
        if not path.exists():
            raise FileNotFoundError(f"Unknown run: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, run_id: str, run: RunRecord) -> None:
        data = json.dumps(run, ensure_ascii=False, indent=1).encode()
        with self._lock(run_id):
            _atomic_write(self._existing(run_id) / "run.json", data)

    def update(self, run_id: str, fn: Callable[[RunRecord], None]) -> RunRecord:
        with self._lock(run_id):
            run = self.load(run_id)
            fn(run)
            self.save(run_id, run)
            return run

    def append_step(self, run_id: str, step: dict) -> None:
        line = (json.dumps(step, ensure_ascii=False) + "\n").encode()
        with self._lock(run_id), open(self._existing(run_id) / "steps.jsonl", "a+b") as f:
            if end := f.seek(0, os.SEEK_END):
                f.seek(end - 1)
                if f.read(1) != b"\n":
                    line = b"\n" + line  # End a line torn by a crash, so this step stays readable.
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def read_steps(self, run_id: str) -> list[dict]:
        """Complete steps in order. A line torn by a crash mid-append is skipped."""
        path = self.path(run_id) / "steps.jsonl"
        if not path.exists():
            return []
        steps = []
        for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
            try:
                step = json.loads(line) if line.strip() else None
            except ValueError:
                continue
            if isinstance(step, dict):
                steps.append(step)
        return steps

    def write_json(self, run_id: str, rel: str, payload) -> str:
        _atomic_write(self._file(run_id, rel), json.dumps(payload, ensure_ascii=False).encode())
        return rel

    def write_bytes(self, run_id: str, rel: str, data: bytes) -> str:
        _atomic_write(self._file(run_id, rel), data)
        return rel

    def list_runs(self, host: str | None = None, limit: int = 20) -> list[dict]:
        """Newest first. `host` may be a host name or a URL; without a port it also matches its runs on any port."""
        hosts = [p for p in self.root.glob("*") if p.is_dir()] if self.root.is_dir() else []
        if host:
            slug, port = host_slug(host), _host_port(host)[1]
            pattern = re.escape(slug) + ("" if port else r"(-\d+)?")
            hosts = [p for p in hosts if re.fullmatch(pattern, p.name, re.ASCII)]
        run_dirs = [d for h in hosts if h.is_dir() for d in h.iterdir() if re.fullmatch(RUN_ID_RE, d.name, re.ASCII)]
        summaries = []
        for directory in sorted(run_dirs, key=lambda d: d.name.split("_", 1)[0], reverse=True)[:max(limit, 0)]:
            summary = {"run_id": directory.name, "path": str(directory)}
            try:
                run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                summaries.append({**summary, "status": "unreadable"})
                continue
            ers = ((run.get("scores") or {}).get("overall") or {}).get("ers") or {}
            summaries.append({
                **summary,
                "kind": run.get("kind"),
                "host": (run.get("site") or {}).get("host"),
                "start_url": (run.get("site") or {}).get("start_url"),
                "status": run.get("status"),
                "created_at": run.get("created_at"),
                "finished_at": run.get("finished_at"),
                "pages": len(run.get("pages") or []),
                "ers": ers.get("score"),
                "grade": ers.get("grade"),
            })
        return summaries
