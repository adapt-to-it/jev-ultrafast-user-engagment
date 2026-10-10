"""jev-engage: argument parsing, the typesafe-only journey rule, judge/score/report/list on a stored audit (scripted
judges, no `claude -p`; Jev through a stand-in for jev_ultrafast.model.post_json), an end-to-end audit of the fixture
shop and a SIGTERM/SIGHUP during one (headless Chromium; skipped without it)."""

import copy
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jev_ultrafast import model as typesafe
from jev_ultrafast.engagement import chrome, cli, judges, judgments, report
from jev_ultrafast.engagement.collectors import PageCollector
from jev_ultrafast.engagement.scoring import NAMES
from jev_ultrafast.engagement.store import RunStore, iso_now

GOLDEN = json.loads((Path(__file__).with_name("fixtures") / "runs" / "audit_complete.json").read_text(encoding="utf-8"))
SHOP = "https://shop.example/"
KEYS = ("TYPESAFE_API_KEY", "TYPESAFE_MODEL", "TEXT_MODEL_API_KEY", "TEXT_MODEL", "JEV_ENGAGEMENT_ENV",
        "JEV_JUDGE_MIN_P")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env of the repository
    monkeypatch.setenv("JEV_ENGAGEMENT_ARTIFACTS", str(tmp_path / "runs"))
    monkeypatch.setenv("JEV_ENGAGEMENT_CACHE", str(tmp_path / "cache"))
    for name in KEYS:  # no real key reaches a test (or the subprocesses that copy os.environ); restored afterwards
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


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
    assert parse(["judge", "r", "--backend", "jev"]).backend == "jev"
    assert parse(["audit", SHOP, "--judge", "jev"]).judge == "jev"
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


def test_an_audit_whose_jev_judge_failed_on_every_page_exits_1(capsys, monkeypatch):
    """audit --judge jev reads like judge --backend jev: when every Jev request failed and nothing was accepted the
    judge step could not be done (exit 1), while the audit is still printed and scored; once Jev answers, exit 0."""
    from jev_ultrafast.engagement import service

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(chrome, "sweep_stale_profiles", lambda root=None: [])
    run_id = stored_audit()
    monkeypatch.setattr(service.EngagementService, "audit_shop", lambda self, *a, **k: self.get_run(run_id))
    sent = []

    def unreachable(url, key, body):
        sent.append(body)
        raise RuntimeError("Model connection failed; no action executed.")
    monkeypatch.setattr(typesafe, "post_json", unreachable)
    assert cli.main(["audit", SHOP, "--judge", "jev"]) == 1
    out = capsys.readouterr().out
    assert sent and "0 verdetti accettati" in out and "Model connection failed" in out
    assert "Engagement readiness" in out and RunStore().load(run_id).get("scores")
    assert cli.main(["judge", run_id, "--backend", "jev"]) == 1  # the same step, the same exit status
    capsys.readouterr()

    def first_label(url, key, body):  # the rubric's first label (last in the reversed head) and the first snippet
        answers = {}
        for qid, question in body["questions"].items():
            ids = list(question["criteria"])
            pick = ids[-1] if qid.startswith("label_rev:") else ids[0]
            answers[qid] = {"choice": pick, "probabilities": {i: 0.9 if i == pick else 0.1 / (len(ids) - 1)
                                                              for i in ids}, "confidence": 0.85}
        return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 900}}
    monkeypatch.setattr(typesafe, "post_json", first_label)
    assert cli.main(["audit", SHOP, "--judge", "jev", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["judgments"]
    assert result["accepted"] > 0 and not result["errors"] and result["pages_left"] == 0


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
    for name in ("JEV_TEST_ADDED", "JEV_TEST_QUOTED", "JEV_TEST_EXPORTED"):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    (tmp_path / ".env").write_text("# comment\nJEV_TEST_KEPT=from .env\nJEV_TEST_ADDED = 42\n"
                                   "JEV_TEST_QUOTED=\"a b=c\"\nexport JEV_TEST_EXPORTED='x'\n", encoding="utf-8")
    cli.load_environment()
    assert os.environ["JEV_TEST_KEPT"] == "from the environment" and os.environ["JEV_TEST_ADDED"] == "42"
    assert os.environ["JEV_TEST_QUOTED"] == "a b=c" and os.environ["JEV_TEST_EXPORTED"] == "x"


def test_dotenv_inline_comments_and_empty_inherited_values(tmp_path, monkeypatch):
    """An inherited empty variable (TYPESAFE_API_KEY= in a shell profile) counts as unset, so the file's key loads; an
    unquoted value ends at " #" and a quoted one at its closing quote, so a comment never becomes part of a key (a 401
    later); a "#" inside quotes or inside a word stays."""
    names = ("JEV_TEST_EMPTY", "JEV_TEST_INLINE", "JEV_TEST_QUOTED", "JEV_TEST_HASH", "JEV_TEST_TAB", "JEV_TEST_KEEP",
             "JEV_TEST_NOTE")
    for name in names:
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    monkeypatch.setenv("JEV_TEST_EMPTY", "")
    monkeypatch.setenv("JEV_TEST_KEEP", "inherited")
    (tmp_path / ".env").write_text("JEV_TEST_EMPTY=file-key\nJEV_TEST_INLINE=file-key # la mia chiave\n"
                                   "JEV_TEST_QUOTED=\"a # b\"  # commento\nJEV_TEST_HASH=abc#def\n"
                                   "JEV_TEST_TAB=tab-key\t# commento\nJEV_TEST_KEEP=from-file\n"
                                   "JEV_TEST_NOTE= # solo un commento\n", encoding="utf-8")
    cli.load_environment()
    assert [os.environ[name] for name in names] == ["file-key", "file-key", "a # b", "abc#def", "tab-key",
                                                    "inherited", ""]


def test_dotenv_escapes_inside_double_quotes():
    """Inside double quotes \\" is a quote and \\\\ a backslash (the shell's and python-dotenv's reading), so a value
    with an escaped quote is not cut there; other backslashes and single-quoted text stay as written."""
    cases = {' "a\\"b" # nota': 'a"b', '"ends\\\\"': "ends\\", '"c:\\path"': "c:\\path",
             "'a\\\"b'": 'a\\"b', '"open': '"open', '""': ""}
    assert {raw: cli._env_value(raw) for raw in cases} == cases


def test_an_unreadable_dotenv_is_a_warning_not_a_traceback(monkeypatch, capsys):
    """./.env that cannot be read (permissions): one line naming the reason, never the content; the command goes on
    with the inherited environment."""
    def unreadable(path=None):
        raise PermissionError(13, "Permission denied", ".env")
    monkeypatch.setattr(cli, "load_environment", unreadable)
    monkeypatch.setattr(chrome, "sweep_stale_profiles", lambda root=None: [])
    assert cli.main(["list"]) == 0
    captured = capsys.readouterr()
    assert "avviso: ./.env non letto (Permission denied): valgono le variabili dell'ambiente" in captured.err
    assert "Traceback" not in captured.err and "Nessuna run" in captured.out


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


# ---------------------------------------------------------------- Jev as judge


def choice(ids, selected, p=0.9):
    ids = list(ids)
    rest = (1 - p) / (len(ids) - 1)
    return {"type": "choice", "choice": selected, "probabilities": {i: p if i == selected else rest for i in ids},
            "confidence": (p - 1 / len(ids)) / (1 - 1 / len(ids))}


def jev_answers(url, key, body):
    """systemone for the Jev judge: checks the request shape, then the first label and the first snippet; the label
    of trick_questions comes with p 0.55, below the acceptance floor (escalated to Claude)."""
    assert url == "https://api.typesafe.ai/v1/systemone" and key == "test-key"
    assert set(body) == {"model", "state", "questions"} and "https://" not in json.dumps(body)
    answers = {}
    for qid, question in body["questions"].items():
        assert question["type"] == "choice" and 2 <= len(question["criteria"]) <= 255
        kind, rubric = qid.split(":")[:2]
        labels = list(body["questions"][f"label:{':'.join(qid.split(':')[1:3])}"]["criteria"])
        p = 0.55 if rubric == "trick_questions" else 0.9
        answers[qid] = choice(question["criteria"], labels[0] if kind != "evidence" else
                              next(iter(question["criteria"])), p if kind != "evidence" else 0.8)
    return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 900, "output_tokens": 0}}


def test_jev_judges_from_the_cli_and_a_claude_backend_takes_the_rest(monkeypatch, capsys):
    """judge --backend jev: one TypeSafe request per page for the rubrics routed to Jev, one sample per task; the
    open tasks (perception rubrics, escalations) are named and judge --backend cli then finalizes every task."""
    run_id = stored_audit()
    assert cli.main(["judge", run_id, "--backend", "jev"]) == 1  # no TYPESAFE_API_KEY: refused, nothing judged
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err and RunStore().load(run_id)["judgments"]["verdicts"] == []
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    requests = []
    monkeypatch.setattr(typesafe, "post_json", lambda *args: requests.append(args[2]) or jev_answers(*args))
    assert cli.main(["judge", run_id, "--backend", "jev"]) == 0
    out = capsys.readouterr().out
    state = RunStore().load(run_id)["judgments"]
    rubrics = judgments.load_rubrics()
    jev = [t for t in state["tasks"] if judgments.routing(t, rubrics) == "jev"]
    escalated = [t for t in jev if t.get("escalation")]
    assert len(requests) == len({t["page_id"] for t in jev}) and escalated
    assert {t["rubric_id"] for t in escalated} == {"trick_questions"}
    accepted, passed = len(jev) - len(escalated), len(escalated)
    assert f"Giudizi (jev, jev-1.13.0, 1 campione): {accepted} verdetti accettati, {passed} task passati a Claude " \
           f"(low_probability {passed}), {len(requests)} richieste TypeSafe" in out
    open_tasks = len(state["tasks"]) - len(state["final"])
    assert f"{open_tasks} task restano aperti" in out and f"jev-engage judge {run_id} --backend cli, poi " \
           f"jev-engage score {run_id} [--journey <run_id del journey>] per aggiornare punteggi e rapporto" in out

    def scripted(self, tasks):
        return [{"task_id": t["task_id"], "judge_id": self.judge_id, "model": self.model,
                 "label": sorted(t["labels"])[0], "confidence": 0.8, "rationale": "Citazione sufficiente.",
                 "evidence": [{"snippet_id": t["snippets"][0]["snippet_id"], "quote": t["snippets"][0]["text"][:30]}]}
                for t in tasks]
    monkeypatch.setattr(judges.ClaudeCliJudge, "judge", scripted)
    assert cli.main(["judge", run_id, "--backend", "cli", "--model", "claude-sonnet-test", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["finalize"]["pending"] == 0 and result["accepted"] == 3 * open_tasks
    state = RunStore().load(run_id)["judgments"]
    assert len(state["final"]) == len(state["tasks"])
    assert sum(1 for f in state["final"] if f["models"] == ["jev-1.13.0"]) == len(jev) - len(escalated)
    assert cli.main(["score", run_id]) == 0 and "con giudizi LLM" in capsys.readouterr().out



def test_jev_errors_print_one_line_per_message(monkeypatch, capsys):
    """A refused key fails every page with the same message: one line with how many pages and tasks it hit, not one
    raw dict per task; the --json output keeps the per-task errors and the groups."""
    run_id = stored_audit()
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    requests = []

    def refused(url, key, body):
        requests.append(body)
        raise RuntimeError("Model provider returned HTTP 401; no action executed.")
    monkeypatch.setattr(typesafe, "post_json", refused)
    assert cli.main(["judge", run_id, "--backend", "jev"]) == 1  # nothing judged, every request failed
    out = capsys.readouterr().out
    state = RunStore().load(run_id)["judgments"]
    rubrics = judgments.load_rubrics()
    jev = [t for t in state["tasks"] if judgments.routing(t, rubrics) == "jev"]
    pages = len({t["page_id"] for t in jev})
    assert len(requests) == pages > 1
    lines = [line for line in out.splitlines() if "errore del giudice" in line]
    assert lines == [f"  errore del giudice su {pages} pagine ({len(jev)} task): Model provider returned HTTP 401; "
                     "no action executed."]
    assert "{'page_id'" not in out
    assert cli.main(["judge", run_id, "--backend", "jev", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error_groups"] == [{"error": "Model provider returned HTTP 401; no action executed.",
                                       "pages": pages, "tasks": len(jev)}]
    assert result["errors_total"] == len(jev) and len(result["errors"]) == min(10, len(jev))


def smoke_module():
    """scripts/smoke_engagement.py (not run by pytest: its main calls the paid API); its pure helpers are tested."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "smoke_engagement.py"
    spec = importlib.util.spec_from_file_location("smoke_engagement", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_smoke_journey_runs_on_the_audit_run_site(tmp_path, monkeypatch, capsys):
    """--audit-run: score_run merges the journey into that audit, so the journey runs on the audit's own site: --url
    on another host exits 2 before any work (unless the journey is skipped); without --url the journey starts from the
    audit's start page, and the fixture shop is served again iff the audit's smoke_summary.json records a fixture
    smoke (ruling 1: the record, not the URL's shape; the host has no port, so two loopback shops look alike). A
    later --audit-run smoke's own summary keeps that record (audit_mode)."""
    smoke = smoke_module()
    store = RunStore(tmp_path / "art")
    real = store.new_run("audit", "https://shop.example/", {})
    fixture = store.new_run("audit", "http://127.0.0.1:44617/shop/index.html", {})
    store.write_json(fixture, "smoke_summary.json", {"mode": "fixture", "audit_mode": "fixture"})
    unmarked = store.new_run("audit", "http://127.0.0.1:44618/shop/index.html", {})  # the CLI's, or a smoke cut short
    dev = store.new_run("audit", "http://127.0.0.1:8000/shop/index.html", {})  # a dev shop a smoke audited with --url
    store.write_json(dev, "smoke_summary.json", {"mode": "url", "audit_mode": "url"})
    rerun = store.new_run("audit", "http://127.0.0.1:44619/shop/index.html", {})  # an earlier smoke's, then --url
    store.write_json(rerun, "smoke_summary.json", {"mode": "url", "audit_mode": "fixture"})
    older = store.new_run("audit", "http://127.0.0.1:44620/shop/index.html", {})  # a summary without audit_mode
    store.write_json(older, "smoke_summary.json", {"mode": "fixture"})
    broken = store.new_run("audit", "http://127.0.0.1:44621/shop/index.html", {})
    (store.path(broken) / "smoke_summary.json").write_text("{not json", encoding="utf-8")
    journey = store.new_run("journey", "https://shop.example/", {})

    def site(*argv):
        args = smoke.parse_args([*argv, "--artifacts", str(tmp_path / "art")] if "--audit-run" in argv else argv)
        audit, problem = smoke.load_audit_run(args)
        return (problem,) if problem else smoke.journey_site(args, audit)
    assert site() == (True, None, None)
    assert site("--url", "https://other.example/") == (False, "https://other.example/", None)
    assert site("--audit-run", real) == (False, "https://shop.example/", None)
    assert site("--audit-run", real, "--url", "https://shop.example/p/1") == (False, "https://shop.example/p/1", None)
    assert site("--audit-run", fixture) == (True, None, None)
    assert site("--audit-run", unmarked) == (False, "http://127.0.0.1:44618/shop/index.html", None)
    assert site("--audit-run", dev) == (False, "http://127.0.0.1:8000/shop/index.html", None)
    assert site("--audit-run", rerun) == site("--audit-run", older) == (True, None, None)
    assert site("--audit-run", broken) == (False, "http://127.0.0.1:44621/shop/index.html", None)
    args = smoke.parse_args(["--audit-run", fixture, "--artifacts", str(tmp_path / "art")])
    assert smoke.audit_mode(args, fixture) == "fixture" and smoke.audit_mode(args, real) is None
    wrong = site("--audit-run", real, "--url", "http://127.0.0.1:8000/shop/index.html")
    assert wrong[:2] == (False, "http://127.0.0.1:8000/shop/index.html") and "not on shop.example" in wrong[2]
    assert "is a journey run" in site("--audit-run", journey)[0]

    monkeypatch.setattr(smoke.mcp_server, "load_keys", lambda: pytest.fail("refused before any key is read"))
    argv = ["--audit-run", real, "--artifacts", str(tmp_path / "art"), "--url", "http://127.0.0.1:8000/"]
    assert smoke.main(argv) == 2 and "another shop's audit" in capsys.readouterr().err
    monkeypatch.setattr(smoke.mcp_server, "load_keys", lambda: {"typesafe_key": False, "text_helper": None})
    assert smoke.main([*argv, "--skip", "journey"]) == 2  # the journey is skipped: only the missing key stops it
    assert "TYPESAFE_API_KEY is missing" in capsys.readouterr().err


def test_the_smoke_checks_count_the_run_and_refuse_a_foreign_journey():
    """jev_judge_accepted_a_verdict counts Jev's accepted verdicts in the run (an --audit-run whose Jev tasks were
    settled earlier asks nothing now), and a score that merged a journey of another host fails the smoke."""
    smoke = smoke_module()
    recorder = smoke.Recorder(None)
    judge = {"available": True, "accepted": 0, "accepted_in_run": 11, "requests": 0, "jev_tasks_open": []}
    score = {"warnings": ["journey runs on another host merged: 127.0.0.1"]}
    results = {c["name"]: c for c in smoke.checks({"judge": judge, "score": score}, recorder, None, True)}
    assert results["jev_judge_accepted_a_verdict"]["ok"] is True
    assert results["jev_judge_accepted_a_verdict"]["detail"] == "11 accepted in the run (0 by this call)"
    assert results["journey_on_the_audit_host"]["ok"] is False and results["journey_on_the_audit_host"]["required"]
    clean = {c["name"]: c for c in smoke.checks({"judge": {**judge, "accepted_in_run": 0}, "score": {"warnings": []}},
                                                 recorder, None, True)}
    assert clean["jev_judge_accepted_a_verdict"]["ok"] is False and clean["journey_on_the_audit_host"]["ok"] is True


def test_the_jev_pilot_line_counts_the_requests_that_brought_no_decision(capsys):
    """A refused key at the first decision: 0 decisions plus 1 failed request, as many as were sent."""
    journey = {"run_id": "r", "status": "error", "steps": 0, "policy": "typesafe", "text_helper": None,
               "verification": {"passed": None, "checks": {"not_assessable": "journey_error"}},
               "model_calls": {"choose": 0, "text": 0, "stale_or_refused": 0, "failed": 1},
               "timing_ms": {"decision": 0}, "friction": {}, "steps_path": "steps.jsonl"}
    cli._print_journey(journey)
    assert "pilota Jev: 0 decisioni (+1 richiesta TypeSafe senza decisione) in 0,00 s" in capsys.readouterr().out
    cli._print_journey({**journey, "model_calls": {"choose": 5, "text": 0, "failed": 0}})
    out = capsys.readouterr().out
    assert "pilota Jev: 5 decisioni in 0,00 s (escluse" in out and "testi scritti" not in out
    cli._print_journey({**journey, "model_calls": {"choose": 1, "text": 0, "failed": 0}})
    assert "pilota Jev: 1 decisione in 0,00 s (esclusa dal tempo del sito)" in capsys.readouterr().out
    cli._print_journey({**journey, "text_helper": "m", "model_calls": {"choose": 5, "text": 1, "text_failed": 2}})
    assert "aiuto testuale m, 1 testo scritto (+2 richieste senza testo)" in capsys.readouterr().out


def test_audit_with_the_jev_judge_needs_its_key_before_any_work(capsys, monkeypatch):
    monkeypatch.setattr(chrome, "sweep_stale_profiles", lambda root=None: pytest.fail("refused before any work"))
    assert cli.main(["audit", SHOP, "--judge", "jev"]) == 1
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err and RunStore().list_runs() == []


def test_report_kpis_say_not_applicable_and_name_units_in_italian(capsys):
    """--format kpis follows report.html: a KPI that does not apply is "non applicabile (...)", never "non
    valutabile"; units are the catalogue's Italian names (punteggio, conteggio, ...), never the registry's codes."""
    run_id = stored_audit()  # no journey and no judgments: their KPIs do not apply
    assert cli.main(["score", run_id]) == 0
    capsys.readouterr()
    assert cli.main(["report", run_id, "--format", "kpis", "--json"]) == 0
    rows = {r["id"]: r for r in json.loads(capsys.readouterr().out)["kpis"]}
    assert cli.main(["report", run_id, "--format", "kpis"]) == 0
    lines = {line.split()[0]: line for line in capsys.readouterr().out.splitlines() if line.startswith("  ")}
    assert set(lines) == set(rows)
    skipped = {kpi for kpi, row in rows.items() if row.get("applicable") is False}
    assert {"FAI.JOURNEY_SUCCESS", "TRI.RETURNS_CLARITY", "PTI.STRIKETHROUGH_LOWEST30"} <= skipped
    for kpi in skipped:
        assert "non applicabile" in lines[kpi] and "non valutabile" not in lines[kpi], lines[kpi]
    assert "non applicabile (nessun percorso dell'agente in questa run)" in lines["FAI.JOURNEY_SUCCESS"]
    assert "non applicabile (giudizi non richiesti)" in lines["TRI.RETURNS_CLARITY"]
    assert "non applicabile: nessun prezzo barrato" in lines["PTI.STRIKETHROUGH_LOWEST30"]  # not "(non applicabile"
    assert "non applicabile (" not in lines["PTI.STRIKETHROUGH_LOWEST30"]
    assert " punteggio " in lines["PERF.CLS"] and " conteggio " in lines["PERF.REQUESTS"]
    assert " rapporto " in lines["FAI.ACTIONS_RATIO"] and " etichetta " in lines["MPI.AUTHORITY"]
    assert " confidenza " in lines["DPR.COUNTDOWN_RESET"] and " sì/no " in lines["TRI.HTTPS"]
    assert " categoria " in lines["FAI.PLP_PAGINATION"] and "pulsante «carica altri»" in lines["FAI.PLP_PAGINATION"]
    lcp = report.fmt_value(rows["PERF.LCP"]["value"], "ms")  # "2 900 ms": the value names its unit, once
    assert f"{lcp}  " in lines["PERF.LCP"] and lines["PERF.LCP"].count(" ms") == 1
    for line in lines.values():
        assert not {"score", "count", "label", "confidence", "bool", "enum", "ratio"} & set(line.split()), line


def test_run_summary_shows_reasons_and_warnings_in_italian(capsys):
    cli._print_run({"run_id": "r", "status": "partial", "pages_total": 0, "pages": [],
                    "not_assessable": [{"stage": "pdp", "profile": "desktop", "kpi_id": None,
                                        "reason": "not_requested"},
                                       {"stage": None, "profile": None, "kpi_id": None, "reason": None}],
                    "warnings_total": 2,
                    "warnings": ["mobile: plp found through the Jev fallback: not reproducible across runs",
                                 "mobile: cart reached after a Jev pick (add_to_cart): not reproducible across runs"]})
    out = capsys.readouterr().out
    assert "  non valutabile: desktop · fase pagina prodotto: fase non richiesta\n" in out
    assert "  non valutabile: run\n" in out and "not_requested" not in out and "None" not in out
    assert ("avvisi: 2 (ultimo: mobile: fase carrello raggiunta dopo un elemento scelto da Jev con il fallback "
            "(aggiunta al carrello): non riproducibile tra run diverse)") in out


def test_score_summary_prints_the_llm_share_and_the_coverage_as_the_report_does(capsys):
    from jev_ultrafast.engagement.scoring import score_run
    from jev_ultrafast.engagement.service import EngagementService

    run = copy.deepcopy(GOLDEN)  # coverage 84.95 %: grade B, never "copertura 85 %"
    dropped = {"FAI.CHECKOUT_FIELDS", "FAI.GUEST_CHECKOUT", "FAI.FORCED_ACCOUNT", "FAI.AUTOCOMPLETE_ATTRS",
               "FAI.CART_EDITABLE", "FAI.BREADCRUMBS", "FAI.PLP_RESULT_COUNT", "PTI.SHIPPING_COST_PRE_CHECKOUT",
               "PTI.FUNNEL_PRICE_DELTA", "PTI.STRIKETHROUGH_LOWEST30"}
    for row in run["observations"]:
        if row["kpi_id"] in dropped:
            row.update(assessed=False, value=None, reason="timeout")
    summary = EngagementService._score_summary(run, score_run(run), {"report_html": "report.html"})
    cli._print_score(summary)
    out = capsys.readouterr().out
    share = summary["ers"]["llm_share"]
    assert 0 < share < 1 and f"Confidenza B (copertura 84 %) · quota LLM {report.fmt_share(share)}\n" in out
    assert report.fmt_share(share).endswith(" %") and "copertura 85 %" not in out and "quota LLM 0," not in out
