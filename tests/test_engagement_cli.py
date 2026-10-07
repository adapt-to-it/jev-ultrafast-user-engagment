"""jev-engage: argument parsing, the typesafe-only journey rule, judge/score/report/list on a stored audit (scripted
judges, no `claude -p`), an end-to-end audit of the fixture shop and a SIGTERM/SIGHUP during one (headless Chromium;
skipped without it)."""

import copy
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jev_ultrafast.engagement import chrome, cli, judges
from jev_ultrafast.engagement.collectors import PageCollector
from jev_ultrafast.engagement.scoring import NAMES
from jev_ultrafast.engagement.store import RunStore, iso_now

GOLDEN = json.loads((Path(__file__).with_name("fixtures") / "runs" / "audit_complete.json").read_text(encoding="utf-8"))
SHOP = "https://shop.example/"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env of the repository
    monkeypatch.setenv("JEV_ENGAGEMENT_ARTIFACTS", str(tmp_path / "runs"))
    monkeypatch.setenv("JEV_ENGAGEMENT_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


def stored_audit() -> str:
    """An audit run with the golden run's pages and observations, as audit_shop would have left it."""
    store = RunStore()
    run_id = store.new_run("audit", SHOP, copy.deepcopy(GOLDEN["settings"]))
    store.update(run_id, lambda run: run.update(
        {key: copy.deepcopy(GOLDEN[key]) for key in ("pages", "not_assessable", "deception", "observations")},
        status="partial", finished_at=iso_now()))
    return run_id


def test_the_parser_reads_every_command():
    parse = cli.build_parser().parse_args
    audit = parse(["audit", SHOP, "--profiles", "mobile", "--stages", "home,plp", "--judge", "cli", "--goal",
                   "Trova scarpe", "--oracle", "pdp_reached", "--oracle-param", "query=scarpe", "--oracle-param",
                   "max_price=50", "--repeats", "2", "--chrome-arg=--proxy-server=http://proxy:3128", "--json"])
    assert audit.profiles == ["mobile"] and audit.stages == ["home", "plp"] and audit.judge == "cli"
    assert audit.oracle_param == [("query", "scarpe"), ("max_price", "50")] and audit.policy == "typesafe"
    assert audit.chrome_arg == ["--proxy-server=http://proxy:3128"] and audit.repeats == 2 and audit.json
    assert parse(["journey", SHOP, "--goal", "g", "--oracle", "cart_not_empty", "--profile", "desktop"]).profile \
        == "desktop"
    assert parse(["judge", "r", "--backend", "api", "--samples", "1"]).backend == "api"
    assert parse(["score", "r", "--journey", "a", "--journey", "b"]).journey == ["a", "b"]
    assert parse(["report", "r", "--format", "kpis"]).format == "kpis"
    assert parse(["list", "--host", "shop.example", "--limit", "5"]).limit == 5


@pytest.mark.parametrize("argv", [
    [], ["audit"], ["audit", SHOP, "--policy", "host"], ["journey", SHOP, "--oracle", "made_up"],
    ["audit", SHOP, "--oracle-param", "no_value"], ["report", "r", "--format", "pdf"], ["judge", "r", "--backend", "x"],
    ["audit", SHOP, "--goal", "Trova scarpe"], ["audit", SHOP, "--profiles", "tablet"],
    ["audit", SHOP, "--stages", "home,basket"], ["audit", SHOP, "--stages", "home,home"],
    ["audit", SHOP, "--profiles", ","], ["audit", SHOP, "--repeats", "0"],
    ["audit", SHOP, "--judge", "cli", "--samples", "0"], ["judge", "r", "--samples", "9"], ["list", "--limit", "0"],
    ["journey", SHOP, "--goal", "g", "--oracle", "pdp_reached", "--max-steps", "x"],
    ["audit", "ftp://shop.example/"], ["audit", "shop.example"], ["audit", "https://stage:S3cret-pw@shop.example/"],
    ["journey", "file:///etc/passwd", "--goal", "g", "--oracle", "cart_not_empty"],
    ["audit", SHOP, "--browser", "harness", "--chrome-arg=--proxy-server=x"],
    ["journey", SHOP, "--browser", "cdp:http://127.0.0.1:9222", "--chrome-arg=--proxy-server=x", "--goal", "g",
     "--oracle", "cart_not_empty"],
    # oracle parameters are checked before the audit runs, whether TYPESAFE_API_KEY is set or not
    ["audit", SHOP, "--goal", "g", "--oracle", "cart_contains_item_under_price"],
    ["audit", SHOP, "--goal", "g", "--oracle", "cart_contains_item_under_price", "--oracle-param", "max_prize=50"],
    ["journey", SHOP, "--goal", "g", "--oracle", "cart_contains_item_under_price", "--oracle-param", "max_price=x"],
    ["journey", SHOP, "--goal", "g", "--oracle", "cart_not_empty", "--oracle-param", "query=scarpe"],
])
def test_invalid_arguments_exit_with_2(argv, capsys, monkeypatch):
    monkeypatch.setattr(chrome, "sweep_stale_profiles", lambda root=None: pytest.fail("refused before any work"))
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    assert stop.value.code == 2
    if argv and argv[0] in cli.COMMANDS and "-h" not in argv:  # the command's own usage line, not the top level's
        err = capsys.readouterr().err
        assert err.startswith(f"usage: jev-engage {argv[0]} ") and "S3cret-pw" not in err, argv


def test_cli_journeys_need_typesafe_and_say_that_host_journeys_are_mcp_only(capsys):
    assert cli.main(["journey", SHOP, "--goal", "Trova scarpe", "--oracle", "cart_not_empty"]) == 1
    assert cli.main(["audit", SHOP, "--goal", "Trova scarpe", "--oracle", "cart_not_empty"]) == 1
    err = capsys.readouterr().err
    assert err.count("TYPESAFE_API_KEY") == 2 and "MCP" in err
    assert RunStore().list_runs() == []  # refused before any audit or browser


def test_oracle_parameters_are_checked_before_the_audit_with_or_without_typesafe(capsys, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "not-used")
    monkeypatch.setattr(chrome, "sweep_stale_profiles", lambda root=None: pytest.fail("refused before any work"))
    with pytest.raises(SystemExit) as stop:
        cli.main(["audit", SHOP, "--goal", "g", "--oracle", "cart_contains_item_under_price"])
    assert stop.value.code == 2
    assert "--oracle-param: Oracle cart_contains_item_under_price needs max_price" in capsys.readouterr().err


def test_a_journey_that_cannot_start_leaves_the_audit_printed_and_scored(capsys, monkeypatch):
    from jev_ultrafast.engagement import service

    monkeypatch.setenv("TYPESAFE_API_KEY", "not-used")
    monkeypatch.setattr(chrome, "sweep_stale_profiles", lambda root=None: [])
    run_id = stored_audit()
    monkeypatch.setattr(service.EngagementService, "audit_shop", lambda self, *a, **k: self.get_run(run_id))
    started = []

    def no_journey(self, url, **options):
        started.append(options)
        raise RuntimeError("the journey could not start: TimeoutError: the start page did not load")
    monkeypatch.setattr(service.EngagementService, "run_journey", no_journey)
    code = cli.main(["audit", SHOP, "--goal", "Trova scarpe", "--oracle", "cart_contains_item_under_price",
                     "--oracle-param", "max_price=49,90"])
    out = capsys.readouterr().out
    assert code == 1 and started[0]["oracle_params"] == {"max_price": 49.9}
    assert out.startswith(f"Run {run_id}: ") and "Journey non avviato: the journey could not start" in out
    assert "Engagement readiness" in out and RunStore().load(run_id).get("scores")


def test_a_closed_pipe_ends_the_command_quietly(tmp_path):
    """`jev-engage list | head -1` with the reader gone: exit 141, no traceback, no "errore" line."""
    command = [sys.executable, "-m", "jev_ultrafast.engagement.cli", "list", "--json"]
    reader = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=tmp_path,
                              env={**os.environ, "JEV_ENGAGEMENT_ARTIFACTS": str(tmp_path / "runs")})
    reader.stdout.close()  # the reader leaves before the command writes
    _, err = reader.communicate(timeout=60)
    assert reader.returncode == 141 and b"Broken pipe" not in err and b"Traceback" not in err, err


def test_dotenv_values_never_override_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_TEST_KEPT", "from the environment")
    monkeypatch.setenv("JEV_TEST_ADDED", "placeholder")
    monkeypatch.delenv("JEV_TEST_ADDED")
    (tmp_path / ".env").write_text("# comment\nJEV_TEST_KEPT=from .env\nJEV_TEST_ADDED = 42\n", encoding="utf-8")
    cli.load_environment()
    assert os.environ["JEV_TEST_KEPT"] == "from the environment" and os.environ["JEV_TEST_ADDED"] == "42"


def test_list_and_report_of_nothing(capsys, monkeypatch):
    swept, handler = [], signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(chrome, "sweep_stale_profiles", lambda root=None: swept.append(root) or [])
    assert cli.main(["list"]) == 0
    assert "Nessuna run" in capsys.readouterr().out
    assert swept == [None]  # browsers left by a jev-engage that was SIGKILLed are reaped by the next call
    assert signal.getsignal(signal.SIGTERM) is handler  # the command's SIGTERM handler is gone again
    assert cli.main(["report", "20260101T000000000000Z_audit_nowhere.example"]) == 1
    assert "errore: Unknown run" in capsys.readouterr().err


def test_judge_score_report_and_list_on_a_stored_audit(monkeypatch, capsys):
    run_id = stored_audit()

    def scripted(self, tasks):
        return [{"task_id": t["task_id"], "judge_id": self.judge_id, "model": self.model,
                 "label": sorted(t["labels"])[0], "confidence": 0.8, "rationale": "Citazione sufficiente.",
                 "evidence": [{"snippet_id": t["snippets"][0]["snippet_id"], "quote": t["snippets"][0]["text"][:30]}]}
                for t in tasks]

    def no_subprocess(*args, **kwargs):
        raise AssertionError("tests never run claude -p")
    monkeypatch.setattr(judges.ClaudeCliJudge, "judge", scripted)
    monkeypatch.setattr(judges.subprocess, "run", no_subprocess)
    assert cli.main(["judge", run_id, "--backend", "cli", "--model", "claude-sonnet-test"]) == 0
    out = capsys.readouterr().out
    assert "Giudizi (cli, claude-sonnet-test, 3 campioni)" in out and "0 scartati" in out
    state = RunStore().load(run_id)["judgments"]
    assert {v["judge_id"] for v in state["verdicts"]} == {"j1", "j2", "j3"}
    assert len(state["final"]) == len(state["tasks"]) > 0

    assert cli.main(["score", run_id]) == 0
    out = capsys.readouterr().out
    assert "non engagement misurato" in out and "Confidenza" in out and "con giudizi LLM" in out
    assert "Grado " not in out and "violazion" not in out
    limiting = next(line for line in out.splitlines() if "fattore limitante:" in line)
    assert limiting.endswith(")") and limiting.split(": ")[1].split(" (")[0] in NAMES.values()  # name (code)
    html = Path(out.split("Rapporto: ")[-1].strip())
    assert html.is_file() and html.with_name("report.json").is_file()

    assert cli.main(["report", run_id, "--format", "paths", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["report_html"] == str(html)
    assert cli.main(["report", run_id]) == 0
    out = capsys.readouterr().out
    assert f"Run {run_id} (parziale)" in out and "Rapporto:" in out  # report.RUN_STATUS (stored_audit: partial)
    assert cli.main(["report", run_id, "--format", "kpis"]) == 0
    assert "TRI.HTTPS" in capsys.readouterr().out
    assert cli.main(["list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["runs"]
    assert [r["run_id"] for r in listed] == [run_id] and listed[0]["ers"] is not None


def test_a_judge_that_fails_everywhere_fails_the_command(monkeypatch, capsys):
    run_id = stored_audit()

    def broken(self, tasks):
        raise judges.JudgeError("claude -p exited with 1: not logged in")
    monkeypatch.setattr(judges.ClaudeCliJudge, "judge", broken)
    assert cli.main(["judge", run_id, "--samples", "1"]) == 1
    out = capsys.readouterr().out
    assert "not logged in" in out and " 1 campione)" in out


def test_cli_audit_of_the_fixture_shop_writes_the_report(chromium, shop_server, monkeypatch, capsys):
    monkeypatch.setattr(PageCollector, "net_quiet_s", 0.8)
    monkeypatch.setattr(PageCollector, "lcp_quiet_s", 0.8)
    since = len(shop_server.requests)
    code = cli.main(["audit", shop_server.url("shop/index.html"), "--profiles", "desktop", "--judge", "none",
                     "--browser", f"cdp:{chromium.ws_url}"])
    captured = capsys.readouterr()
    assert code == 0, captured
    out = captured.out
    assert ": completa (5 pagine)" in out and "Engagement readiness" in out and "senza giudizi LLM" in out
    assert "  … done: complete" in captured.err  # progress goes to stderr
    html = Path(out.split("Rapporto: ")[-1].strip())
    assert html.is_file() and html.with_name("report.json").is_file()
    report = json.loads(html.with_name("report.json").read_text(encoding="utf-8"))
    assert report["status"] == "complete" and report["headline"]["context"]["judged"] is False
    assert not [r for r in shop_server.requests[since:] if r["method"] != "GET" or r["path"].startswith("/pay")]


def _children(pid: int) -> list[int]:
    found = []
    for entry in os.listdir("/proc"):
        try:
            if entry.isdigit() and int(Path(f"/proc/{entry}/stat").read_text().rpartition(")")[2].split()[1]) == pid:
                found.append(int(entry))
        except OSError:
            continue
    return found


def _alive_in(group: int) -> list[int]:
    alive = []
    for entry in os.listdir("/proc"):
        try:
            fields = Path(f"/proc/{entry}/stat").read_text().rpartition(")")[2].split() if entry.isdigit() else None
        except OSError:
            continue
        if fields and int(fields[2]) == group and fields[0] != "Z":
            alive.append(int(entry))
    return alive


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_a_signal_during_an_audit_closes_the_browser_and_fails_the_run(signum, tmp_path, shop_server):
    """timeout, a CI cancel or a closed terminal: the launched Chromium runs in its own session, so the signal never
    reaches it. The command unwinds like Ctrl-C: browser closed, run failed and disowned, exit 128 + signal."""
    if chrome.find_chromium() is None or not os.path.isdir("/proc/self"):
        pytest.skip("needs Chromium and /proc")
    runs = tmp_path / "runs"
    env = {**os.environ, "JEV_ENGAGEMENT_ARTIFACTS": str(runs), "JEV_ENGAGEMENT_CACHE": str(tmp_path / "cache"),
           "JEV_CHROME_ARGS": "--proxy-server=http://127.0.0.1:9"}
    command = [sys.executable, "-m", "jev_ultrafast.engagement.cli", "audit", shop_server.url("shop/index.html"),
               "--profiles", "desktop", "--json"]
    process = subprocess.Popen(command, cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    browsers = []
    try:
        deadline = time.monotonic() + 30
        while not (browsers := _children(process.pid)) and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
        assert browsers, process.stderr.read().decode()[-2000:] if process.poll() is not None else "no browser"
        time.sleep(1.0)  # the audit is under way
        process.send_signal(signum)
        _, err = process.communicate(timeout=30)
        assert process.returncode == 128 + signum, err.decode()[-2000:]
        assert f"interrotto ({signal.Signals(signum).name})" in err.decode()
        deadline = time.monotonic() + 5
        while (left := [pid for group in browsers for pid in _alive_in(group)]) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not left, left
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for group in browsers:  # never leave a browser behind, even when the test failed
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
    (listed,) = RunStore(runs).list_runs()
    run = RunStore(runs).load(listed["run_id"])
    name = signal.Signals(signum).name
    assert run["status"] == "failed" and run["finished_at"], run["errors"]
    assert f"interrupted: the command received {name} while the run was in progress" in run["errors"]
    assert not list(runs.glob("*/*/owner.json"))
