"""jev-engage: argument parsing, the typesafe-only journey rule, judge/score/report/list on a stored audit (scripted
judges, no `claude -p`), and an end-to-end audit of the fixture shop (headless Chromium; skipped without it)."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jev_ultrafast.engagement import cli, judges
from jev_ultrafast.engagement.collectors import PageCollector
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
])
def test_invalid_arguments_exit_with_2(argv, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    assert stop.value.code == 2


def test_cli_journeys_need_typesafe_and_say_that_host_journeys_are_mcp_only(capsys):
    assert cli.main(["journey", SHOP, "--goal", "Trova scarpe", "--oracle", "cart_not_empty"]) == 1
    assert cli.main(["audit", SHOP, "--goal", "Trova scarpe", "--oracle", "cart_not_empty"]) == 1
    err = capsys.readouterr().err
    assert err.count("TYPESAFE_API_KEY") == 2 and "MCP" in err
    assert RunStore().list_runs() == []  # refused before any audit or browser


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


def test_list_and_report_of_nothing(capsys):
    assert cli.main(["list"]) == 0
    assert "Nessuna run" in capsys.readouterr().out
    assert cli.main(["report", "20260101T000000000000Z_audit_nowhere.example"]) == 1
    assert "errore: Unknown run" in capsys.readouterr().err
    assert cli.main(["audit", SHOP, "--browser", "harness", "--chrome-arg=--proxy-server=x"]) == 1
    assert "--chrome-arg" in capsys.readouterr().err


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
    html = Path(out.split("Rapporto: ")[-1].strip())
    assert html.is_file() and html.with_name("report.json").is_file()

    assert cli.main(["report", run_id, "--format", "paths", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["report_html"] == str(html)
    assert cli.main(["report", run_id]) == 0
    assert "Rapporto:" in capsys.readouterr().out
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
    assert "not logged in" in capsys.readouterr().out


def test_cli_audit_of_the_fixture_shop_writes_the_report(chromium, shop_server, monkeypatch, capsys):
    monkeypatch.setattr(PageCollector, "net_quiet_s", 0.8)
    monkeypatch.setattr(PageCollector, "lcp_quiet_s", 0.8)
    since = len(shop_server.requests)
    code = cli.main(["audit", shop_server.url("shop/index.html"), "--profiles", "desktop", "--judge", "none",
                     "--browser", f"cdp:{chromium.ws_url}"])
    captured = capsys.readouterr()
    assert code == 0, captured
    out = captured.out
    assert ": completo (5 pagine)" in out and "Engagement readiness" in out and "senza giudizi LLM" in out
    assert "  … done: complete" in captured.err  # progress goes to stderr
    html = Path(out.split("Rapporto: ")[-1].strip())
    assert html.is_file() and html.with_name("report.json").is_file()
    report = json.loads(html.with_name("report.json").read_text(encoding="utf-8"))
    assert report["status"] == "complete" and report["headline"]["context"]["judged"] is False
    assert not [r for r in shop_server.requests[since:] if r["method"] != "GET" or r["path"].startswith("/pay")]
