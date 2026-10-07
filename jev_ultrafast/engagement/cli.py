"""jev-engage: engagement-readiness audits from the command line.

Human output is Italian; --json prints the service's JSON instead. Journeys from the CLI use policy "typesafe"
(TYPESAFE_API_KEY, plus TEXT_MODEL_API_KEY for text fields): host-driven journeys exist only through the MCP server.
Exit status: 0 on success, 1 when the run failed or a step could not be done, 2 for invalid arguments, 141 when
the reader of the output went away (| head). Runs of a jev-engage or MCP server process that died are marked
failed at start.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from .profiles import DEVICE_PROFILES
from .report import fmt_confidence
from .schemas import STAGES

ORACLES = ("cart_contains_item_under_price", "cart_not_empty", "pdp_reached", "search_results_shown")
STATUS = {"complete": "completo", "partial": "parziale", "failed": "fallito", "running": "in corso",
          "created": "creato", "done": "concluso (DONE)", "blocked": "bloccato",
          "stopped_at_checkout_boundary": "fermato al primo step del checkout", "budget_exhausted": "passi esauriti",
          "error": "errore", "abandoned": "abbandonato"}
NO_TYPESAFE = ("Il journey da CLI usa la policy typesafe e richiede TYPESAFE_API_KEY (più TEXT_MODEL_API_KEY per i "
               "campi di testo), in .env o nell'ambiente. I journey guidati dall'host (Claude Code) sono disponibili "
               "solo tramite il server MCP del plugin jev-engagement.")


class Failure(Exception):
    """A step that could not be done; main() prints it and exits with 1."""


def load_environment(path: Path | None = None) -> None:
    """KEY=value lines of ./.env into the environment, without overriding what is already set."""
    path = path or Path.cwd() / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def _choices(allowed):
    """Comma-separated distinct values of `allowed` (an invalid list is an argument error: exit 2)."""
    def parse(value: str) -> list[str]:
        items = [item.strip() for item in value.split(",") if item.strip()]
        unknown = [item for item in items if item not in allowed]
        if unknown or not items or len(set(items)) != len(items):
            raise argparse.ArgumentTypeError(f"valori distinti fra {', '.join(allowed)}, ricevuto {value!r}")
        return items
    return parse


def _integer(low: int, high: int | None = None):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            number = None
        if number is None or number < low or (high is not None and number > high):
            limits = f"da {low} a {high}" if high is not None else f"almeno {low}"
            raise argparse.ArgumentTypeError(f"atteso un intero {limits}, ricevuto {value!r}")
        return number
    return parse


def _pair(value: str) -> tuple[str, str]:
    key, sep, text = value.partition("=")
    if not sep or not key.strip():
        raise argparse.ArgumentTypeError(f"atteso chiave=valore, ricevuto {value!r}")
    return key.strip(), text.strip()


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="stampa il risultato in JSON")
    common.add_argument("--artifacts", metavar="DIR",
                        help="cartella delle run (predefinita: $JEV_ENGAGEMENT_ARTIFACTS o ./artifacts/engagement)")
    browser = argparse.ArgumentParser(add_help=False)
    browser.add_argument("--browser", default="auto", help="auto | launch | harness | cdp:<URL DevTools>")
    browser.add_argument("--locale", default="it", help="lingua del lessico e Accept-Language (it, en, ...)")
    browser.add_argument("--headed", action="store_true", help="Chromium con finestra (solo browser lanciati)")
    browser.add_argument("--chrome-arg", action="append", default=[], metavar="ARG",
                         help="argomento extra per il Chromium lanciato, ripetibile "
                              "(es. --chrome-arg=--proxy-server=…)")
    journey = argparse.ArgumentParser(add_help=False)
    journey.add_argument("--goal", help="obiettivo in linguaggio naturale")
    journey.add_argument("--oracle", choices=ORACLES, help="verifica indipendente dell'esito")
    journey.add_argument("--oracle-param", action="append", default=[], type=_pair, metavar="K=V",
                         help="parametro dell'oracolo, ripetibile (es. max_price=50)")
    journey.add_argument("--policy", choices=("typesafe",), default="typesafe",
                         help="da CLI solo typesafe; la policy host è disponibile via MCP")
    journey.add_argument("--max-steps", type=_integer(1, 60), default=40)
    journey.add_argument("--optimal-steps", type=_integer(1), help="interazioni minime per l'obiettivo, se note")
    journey.add_argument("--optimal-pages", type=_integer(1),
                         help="pagine distinte minime, pagina iniziale inclusa, se note")

    parser = argparse.ArgumentParser(
        prog="jev-engage",
        description="Audit di engagement readiness di negozi online: stima da sessioni sintetiche (attrito previsto e "
                    "segnali di rischio), non engagement misurato. Non effettua mai ordini.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMANDO")

    audit = sub.add_parser("audit", parents=[common, browser, journey],
                           help="audit deterministico (e journey opzionale)")
    audit.add_argument("url")
    audit.add_argument("--profiles", type=_choices(list(DEVICE_PROFILES)), default=list(DEVICE_PROFILES),
                       help="es. mobile,desktop")
    audit.add_argument("--stages", type=_choices(STAGES), default=list(STAGES), help=f"es. {','.join(STAGES)}")
    audit.add_argument("--consent", choices=("auto", "reject", "accept", "none"), default="auto")
    audit.add_argument("--repeats", type=_integer(1, 5), default=1, help="caricamenti per pagina (mediane), 1-5")
    audit.add_argument("--max-pages", type=_integer(1))
    audit.add_argument("--judge", choices=("none", "cli", "api", "openai"), default="none",
                       help="giudizi LLM: cli = claude -p con l'abbonamento Claude")
    audit.add_argument("--samples", type=_integer(1, 5), default=3, help="campioni per task di giudizio, 1-5")
    audit.add_argument("--judge-model", help="modello dei giudici")
    audit.add_argument("--journey-profile", choices=list(DEVICE_PROFILES), default="mobile")

    run = sub.add_parser("journey", parents=[common, browser, journey], help="journey con policy typesafe")
    run.add_argument("url")
    run.add_argument("--profile", choices=list(DEVICE_PROFILES), default="mobile")

    judge = sub.add_parser("judge", parents=[common], help="giudizi LLM sui task di una run di audit")
    judge.add_argument("run_id")
    judge.add_argument("--backend", choices=("cli", "api", "openai"), default="cli")
    judge.add_argument("--samples", type=_integer(1, 5), default=3, help="campioni per task di giudizio, 1-5")
    judge.add_argument("--model")

    score = sub.add_parser("score", parents=[common], help="calcola i punteggi e scrive il rapporto")
    score.add_argument("run_id")
    score.add_argument("--journey", action="append", default=[], metavar="RUN_ID", help="run di journey da unire")

    report = sub.add_parser("report", parents=[common], help="mostra il rapporto di una run")
    report.add_argument("run_id")
    report.add_argument("--format", choices=("summary", "kpis", "paths"), default="summary")

    runs = sub.add_parser("list", parents=[common], help="elenca le run")
    runs.add_argument("--host")
    runs.add_argument("--limit", type=_integer(1, 100), default=20)
    return parser


def _transport_factory(args):
    """A launched Chromium with the --chrome-arg extras (None: the service opens the browser per --browser)."""
    if not getattr(args, "chrome_arg", None):
        return None
    if args.browser not in ("auto", "launch"):
        raise Failure("--chrome-arg vale solo per un Chromium lanciato (--browser auto o launch)")

    def factory():
        from .chrome import find_chromium, launch_chromium
        from .settings import LANGS
        from .transport import DirectTransport

        path = find_chromium()
        if path is None:
            raise RuntimeError("Chromium non trovato: imposta JEV_CHROME_PATH")
        chrome = launch_chromium(path=path, headless=not args.headed, lang=LANGS.get(args.locale, args.locale),
                                 extra_args=tuple(args.chrome_arg))
        try:
            return DirectTransport(chrome.ws_url), chrome
        except BaseException:
            chrome.close()
            raise
    return factory


def _service(args):
    from .service import default_service
    service = default_service(args.artifacts, transport_factory=_transport_factory(args))
    service.recover_orphans()  # runs of a process that died while running them are marked failed
    return service


def _say(message: str) -> None:
    print(f"  … {message}", file=sys.stderr, flush=True)


def _journey_options(args, parser) -> dict | None:
    if not args.goal and not args.oracle:
        return None
    if not (args.goal and args.oracle):
        parser.error("--goal e --oracle vanno indicati insieme")
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise Failure(NO_TYPESAFE)
    return {"goal": args.goal, "oracle": args.oracle, "oracle_params": dict(args.oracle_param), "policy": args.policy,
            "max_steps": args.max_steps, "optimal_steps": args.optimal_steps, "optimal_pages": args.optimal_pages}


# ---------------------------------------------------------------- human output (Italian)


def _num(value, digits=1) -> str:
    if value is None:
        return "n/d"
    if isinstance(value, bool):
        return "sì" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}".replace(".", ",")
    return str(value)


def _print_run(summary: dict) -> None:
    status = STATUS.get(summary.get("status"), summary.get("status"))
    print(f"Run {summary['run_id']}: {status} ({summary.get('pages_total', 0)} pagine)")
    for page in summary.get("pages") or []:
        print(f"  {page.get('profile') or '-':8} {page.get('stage') or '-':15} {page.get('type') or '-':9} "
              f"LCP {_num(page.get('lcp_ms'), 0)} ms · CLS {_num(page.get('cls'), 3)} · {_num(page.get('kb'), 0)} KB")
    for item in summary.get("not_assessable") or []:
        where = " ".join(str(v) for v in (item.get("profile"), item.get("stage"), item.get("kpi_id")) if v)
        print(f"  non valutabile: {where or 'run'} ({item.get('reason')})")
    if summary.get("journey"):
        journey = summary["journey"]
        passed = (journey.get("verification") or {}).get("passed")
        print(f"  journey: {STATUS.get(journey.get('status'), journey.get('status'))}, verifica "
              f"{'superata' if passed else 'non superata' if passed is False else 'non valutabile'}")
    if summary.get("warnings_total"):
        print(f"  avvisi: {summary['warnings_total']} (ultimo: {summary['warnings'][-1]})")
    for error in summary.get("errors") or []:
        print(f"  errore: {error}")


def _print_score(summary: dict) -> None:
    ers = summary.get("ers") or {}
    print("Engagement readiness (stima da sessioni sintetiche, non engagement misurato)")
    if ers.get("published"):
        print(f"  ERS {_num(ers.get('score'))} · {summary.get('confidence')} · "
              f"quota LLM {_num(ers.get('llm_share'), 2)}")
    else:
        print(f"  ERS non pubblicato: {ers.get('reason') or 'copertura insufficiente'}")
    print(f"  {summary.get('scope')}")
    for name, sub in (summary.get("sub_indices") or {}).items():
        confidence = fmt_confidence(sub.get("grade"), sub.get("coverage"))
        print(f"  {sub.get('name', name):30} {_num(sub.get('score')):>6}  Confidenza {confidence}"
              + (f"  limitanti: {', '.join(sub['limiting_kpis'])}" if sub.get("limiting_kpis") else ""))
    dpr = summary.get("dpr") or {}
    print(f"  {dpr.get('name', 'DPR')}: {dpr.get('text')}")
    for signal in summary.get("top_risk_signals") or []:
        print(f"    - segnale di rischio {signal['kpi_id']}: {signal.get('description')} "
              f"(confidenza {_num(signal.get('confidence'), 2)})")
    if ers.get("limiting_factor"):
        print(f"  fattore limitante: {ers['limiting_factor']}")
    print(f"Rapporto: {(summary.get('report') or {}).get('report_html')}")


def _print_report(result: dict, fmt: str) -> None:
    if fmt == "paths":
        print(f"Rapporto HTML: {result['report_html']}\nRapporto JSON: {result['report_json']}")
        return
    if fmt == "kpis":
        for row in result.get("kpis") or []:
            value = _num(row.get("value"), 2) if row.get("assessed") else f"non valutabile ({row.get('reason')})"
            print(f"  {row['id']:32} {value:>12}  {row.get('unit') or ''}  punteggio {_num(row.get('normalized'))}")
        print(f"Rapporto: {result['report_html']}")
        return
    headline = result.get("headline") or {}
    print(f"Run {result['run_id']} ({STATUS.get(result.get('status'), result.get('status'))})")
    if headline.get("published"):
        print(f"  ERS {_num(headline.get('ers'))} · {result.get('confidence')}")
    else:
        print(f"  ERS non pubblicato: {headline.get('reason') or 'copertura insufficiente'}")
    print(f"  {result.get('scope')}")
    for signal in result.get("risk_signals") or []:
        print(f"    - segnale di rischio {signal['kpi_id']}: {signal.get('description')}")
    for reason, count in (result.get("not_assessable_reasons") or {}).items():
        print(f"  non valutabile ×{count}: {reason}")
    print(f"Rapporto: {result['report_html']}")


def _emit(args, result, printer) -> None:
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    else:
        printer(result)


# ---------------------------------------------------------------- commands


def _audit(args, parser) -> int:
    journey = _journey_options(args, parser)
    service = _service(args)
    try:
        audited = service.audit_shop(args.url, args.profiles, args.stages, args.browser, args.locale, args.consent,
                                     args.repeats, wait=True, progress=None if args.json else _say,
                                     headless=not args.headed, max_pages=args.max_pages)
        result = {"audit": audited}
        journey_ids = []
        if journey and audited["status"] != "failed":
            done = service.run_journey(args.url, profile=args.journey_profile, browser=args.browser,
                                       locale=args.locale, wait=True, headless=not args.headed, **journey)
            result["journey"] = done
            journey_ids.append(done["run_id"])
        if args.judge != "none" and audited["status"] != "failed":
            result["judgments"] = service.judge_with(audited["run_id"], args.judge, args.samples,
                                                     model=args.judge_model)
        if audited["status"] != "failed":
            result["score"] = service.score_run(audited["run_id"], journey_ids)
    finally:
        service.shutdown()

    def show(result):
        _print_run(result["audit"])
        if result.get("journey"):
            _print_journey(result["journey"])
        if result.get("judgments"):
            _print_judgments(result["judgments"])
        if result.get("score"):
            _print_score(result["score"])
    _emit(args, result, show)
    return 1 if audited["status"] == "failed" else 0


def _print_journey(result: dict) -> None:
    verification = result.get("verification") or {}
    passed = verification.get("passed")
    outcome = "superata" if passed else "non superata" if passed is False else "non valutabile"
    print(f"Journey {result['run_id']}: {STATUS.get(result.get('status'), result.get('status'))}, "
          f"{result.get('steps')} passi, verifica indipendente {outcome}")
    for kpi, value in (result.get("friction") or {}).items():
        print(f"  {kpi:28} {_num(value, 2)}")
    print(f"  passi: {result.get('steps_path')}")


def _print_judgments(result: dict) -> None:
    final = result.get("finalize") or {}
    print(f"Giudizi ({result['backend']}, {result['model']}, {result['samples']} campioni): "
          f"{result['accepted']} verdetti accettati, {result['rejected_total']} scartati, "
          f"{final.get('decided', 0)} task decisi, {final.get('uncertain', 0)} incerti")
    for error in result.get("errors") or []:
        print(f"  errore del giudice: {error}")


def _journey(args, parser) -> int:
    journey = _journey_options(args, parser)
    if journey is None:
        parser.error("indica --goal e --oracle")
    service = _service(args)
    try:
        result = service.run_journey(args.url, profile=args.profile, browser=args.browser, locale=args.locale,
                                     wait=True, headless=not args.headed, **journey)
    finally:
        service.shutdown()
    _emit(args, result, _print_journey)
    return 1 if result.get("run_status") == "failed" or "verification" not in result else 0


def _judge(args, parser) -> int:
    service = _service(args)
    result = service.judge_with(args.run_id, args.backend, args.samples, model=args.model)
    _emit(args, result, _print_judgments)
    return 1 if result["errors"] and not result["accepted"] and not result["reused"] else 0


def _score(args, parser) -> int:
    result = _service(args).score_run(args.run_id, args.journey)
    _emit(args, result, _print_score)
    return 0


def _report(args, parser) -> int:
    result = _service(args).get_report(args.run_id, args.format)
    _emit(args, result, lambda r: _print_report(r, args.format))
    return 0


def _list(args, parser) -> int:
    result = _service(args).list_runs(args.host, args.limit)

    def show(result):
        if not result["runs"]:
            print(f"Nessuna run in {result['root']}")
        for run in result["runs"]:
            ers = f"ERS {_num(run.get('ers'))} ({run.get('grade') or 'n/d'})" if run.get("ers") is not None else ""
            print(f"{run['run_id']}  {run.get('kind') or '-':7} {STATUS.get(run.get('status'), run.get('status'))}"
                  f"  {ers}")
    _emit(args, result, show)
    return 0


COMMANDS = {"audit": _audit, "journey": _journey, "judge": _judge, "score": _score, "report": _report, "list": _list}


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_environment()
    try:
        code = COMMANDS[args.command](args, parser)
        sys.stdout.flush()  # a closed pipe (| head) surfaces here, not at interpreter exit
        return code
    except KeyboardInterrupt:
        print("interrotto", file=sys.stderr)
        return 130
    except BrokenPipeError:  # the reader went away (e.g. | head): nothing left to say to it
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return 141
    except (Failure, ValueError, LookupError, OSError, RuntimeError) as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
