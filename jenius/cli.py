"""Command-line interface: ``python -m jenius <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, config
from .config import Settings


def _settings(args: argparse.Namespace) -> Settings:
    return Settings(own_history_threshold_pct=args.own, peer_threshold_pct=args.peer)


def cmd_detect(args: argparse.Namespace) -> int:
    from .pipeline import run
    from .report import write_csv

    analysis = run(args.shipments, args.notes, _settings(args))
    write_csv(analysis.anomalies, args.output)
    anomalies = analysis.anomalies
    justified = sum(1 for f in anomalies if f.match.explained_by)
    print(f"Analysed {len(analysis.shipments)} shipments -> {len(analysis.route_weeks)} route-weeks")
    print(f"Anomalies: {len(anomalies)} ({justified} justified, {len(anomalies) - justified} need review)\n")
    for f in anomalies:
        row = f.csv_row()
        print(
            f"  {row['route']:<20} {row['week_of']}  ₹{row['cost_per_tonne_km']:>5}/t-km  "
            f"{row['flagged']:<15} {row['matched_note_id']}"
        )
    print(f"\nWrote {args.output}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from .pipeline import run
    from .report import knowledge_base, write_json

    write_json(knowledge_base(run(settings=_settings(args))), args.output)
    print(f"Wrote {args.output}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .assistant import Assistant
    from .pipeline import default_knowledge_base

    bot = Assistant(default_knowledge_base())
    if args.question:
        print(bot.answer(" ".join(args.question)))
        return 0
    print("Jenius assistant. Ask about routes, weeks or notes; 'exit' to quit.\n")
    while True:
        try:
            q = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if q.lower() in {"exit", "quit"}:
            return 0
        if q:
            print("\n" + bot.answer(q) + "\n")


def cmd_eval(args: argparse.Namespace) -> int:
    from .evaluation import evaluate

    report = evaluate()
    print(f"Reproducibility: {'PASS' if report.reproducible else 'FAIL'} ({report.hashes[0][:16]})")
    print(f"Correctness:     {report.passed}/{report.total}")
    for failure in report.failures:
        print(f"  {failure}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report.as_dict(), indent=2) + "\n")
        print(f"Wrote {args.output}")
    return 0 if report.ok else 1


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(args.host, args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jenius", description=__doc__)
    p.add_argument("--version", action="version", version=f"jenius {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def thresholds(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--own", type=float, default=15.0, help="%% above own trailing average")
        sp.add_argument("--peer", type=float, default=15.0, help="%% above similar routes")

    d = sub.add_parser("detect", help="detect anomalies and write the CSV report")
    d.add_argument("--shipments", type=Path, default=config.SHIPMENTS_FILE)
    d.add_argument("--notes", type=Path, default=config.NOTES_FILE)
    d.add_argument("-o", "--output", type=Path, default=config.OUT_DIR / "output_anomalies.csv")
    thresholds(d)
    d.set_defaults(func=cmd_detect)

    b = sub.add_parser("build", help="write the dashboard knowledge base (web/data/kb.json)")
    b.add_argument("-o", "--output", type=Path, default=config.WEB_DIR / "data" / "kb.json")
    thresholds(b)
    b.set_defaults(func=cmd_build)

    a = sub.add_parser("ask", help="ask the assistant (interactive if no question given)")
    a.add_argument("question", nargs="*")
    a.set_defaults(func=cmd_ask)

    e = sub.add_parser("eval", help="reproducibility + labelled-case evaluation")
    e.add_argument("-o", "--output", type=Path, default=config.OUT_DIR / "eval_report.json")
    e.set_defaults(func=cmd_eval)

    s = sub.add_parser("serve", help="run the API + dashboard server")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
