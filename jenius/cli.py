"""Command line: run the server, set up the database, and the few admin jobs that need a shell.

  python -m jenius init-db                 create/upgrade the tables and load the demo dataset
  python -m jenius serve [--port 8000]     run the web app
  python -m jenius make-admin EMAIL        let an account review payments and see usage
  python -m jenius payments [--all]        list UPI payments waiting to be checked
  python -m jenius approve ID | reject ID  decide a payment
  python -m jenius usage                   Claude calls and cost per day
  python -m jenius ask "question"          ask about the demo dataset from the terminal
"""
import argparse
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parent.parent


def _db():
    from .config import get_settings
    from .db import Database

    return Database(get_settings().database_url)


def cmd_init_db(_args) -> None:
    from alembic import command
    from alembic.config import Config

    from .demo import ensure_demo

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    with _db().sessions() as db:
        dataset = ensure_demo(db)
    print(f"Database ready. Demo dataset: {dataset.name if dataset else 'not found (case-study CSVs missing)'}")


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("jenius.api.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload,
                proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))


def cmd_make_admin(args) -> None:
    from .models import User

    with _db().sessions() as db:
        user = db.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            sys.exit(f"No account with email {args.email}. Sign up on the website first.")
        user.is_admin = not args.remove
        db.commit()
        print(f"{user.email} is {'no longer ' if args.remove else ''}an admin.")


def cmd_payments(args) -> None:
    from .models import Payment, User

    with _db().sessions() as db:
        q = select(Payment, User.email).join(User, User.id == Payment.user_id).order_by(Payment.created_at)
        if not args.all:
            q = q.where(Payment.status == "pending")
        rows = db.execute(q).all()
        if not rows:
            print("No payments to check." if not args.all else "No payments yet.")
        for p, email in rows:
            print(f"{p.id}  {p.created_at:%Y-%m-%d %H:%M}  {email:30s}  ₹{p.amount_inr:<6} {p.cycle:8s} "
                  f"UTR {p.upi_reference:20s} {p.status}")


def _review(args, approve: bool) -> None:
    from fastapi import HTTPException

    from .api.routes.admin import review_payment
    from .models import User

    with _db().sessions() as db:
        admin = db.scalar(select(User).where(User.is_admin.is_(True)).order_by(User.created_at))
        if admin is None:
            sys.exit("Make an admin first: python -m jenius make-admin you@example.com")
        try:
            payment = review_payment(db, uuid.UUID(args.payment_id), admin, approve, args.note)
        except HTTPException as err:
            sys.exit(str(err.detail))
        except ValueError:
            sys.exit("That isn't a payment id. Copy it from `python -m jenius payments`.")
        print(f"Payment {payment.id} {payment.status}.")


def cmd_usage(_args) -> None:
    from .models import LlmCall

    with _db().sessions() as db:
        day = func.date(LlmCall.created_at)
        rows = db.execute(select(day, func.count(), func.sum(LlmCall.input_tokens), func.sum(LlmCall.output_tokens),
                                 func.sum(LlmCall.cost_usd)).group_by(day).order_by(day.desc()).limit(30)).all()
        if not rows:
            print("No Claude calls yet.")
        for d, n, i, o, c in rows:
            print(f"{d}  {n:5d} calls  {i or 0:>10,} in  {o or 0:>9,} out  ${float(c or 0):.4f}")


def cmd_ask(args) -> None:
    from .api.routes.chat import dataset_brief, offline_reply
    from .assistant.tools import ToolBox
    from .config import get_settings
    from .demo import ensure_demo

    settings = get_settings()
    with _db().sessions() as db:
        dataset = ensure_demo(db)
        if dataset is None:
            sys.exit("The demo dataset isn't available.")
        if not settings.llm_enabled or args.offline:
            print(offline_reply(db, dataset, args.question))
            return
        from .assistant.agent import ClaudeAgent

        result = ClaudeAgent(settings).run_turn(
            history=[], user_text=args.question, toolbox=ToolBox(db, dataset.id),
            dataset_brief=dataset_brief(dataset),
            on_call=lambda c: print(f"  [{c.model}] {c.input_tokens} in / {c.output_tokens} out, ${c.cost_usd:.4f}",
                                    file=sys.stderr))
        print(result.reply)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m jenius", description="Jenius freight cost assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create/upgrade tables and load the demo").set_defaults(fn=cmd_init_db)

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    serve.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    serve.set_defaults(fn=cmd_serve)

    admin = sub.add_parser("make-admin", help="give an account admin rights")
    admin.add_argument("email")
    admin.add_argument("--remove", action="store_true")
    admin.set_defaults(fn=cmd_make_admin)

    payments = sub.add_parser("payments", help="list UPI payments waiting to be checked")
    payments.add_argument("--all", action="store_true")
    payments.set_defaults(fn=cmd_payments)

    for name, approve in (("approve", True), ("reject", False)):
        p = sub.add_parser(name, help=f"{name} a UPI payment")
        p.add_argument("payment_id")
        p.add_argument("--note", default="")
        p.set_defaults(fn=lambda a, approve=approve: _review(a, approve))

    sub.add_parser("usage", help="Claude cost per day").set_defaults(fn=cmd_usage)

    ask = sub.add_parser("ask", help="ask about the demo dataset")
    ask.add_argument("question")
    ask.add_argument("--offline", action="store_true", help="use the free built-in engine")
    ask.set_defaults(fn=cmd_ask)

    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
