import argparse
import sys

from .db import make_session
from .ingest import ingest_file
from .checks import run_checks, ALL_CHECKS
from .models import Library


def cmd_ingest(args):
    session = make_session(args.db)
    libraries = ingest_file(session, args.lib_file)
    for lib in libraries:
        print(f"ingested library '{lib.name}' (id={lib.id}) from {args.lib_file}")


def cmd_check(args):
    session = make_session(args.db)

    library_id = args.library_id
    if library_id is None and args.library_name:
        lib = session.query(Library).filter(Library.name == args.library_name).one()
        library_id = lib.id

    rule_ids = args.rules.split(",") if args.rules else None
    violations = run_checks(session, library_id=library_id, rule_ids=rule_ids)

    errors = [v for v in violations if v.severity == "error"]
    warnings = [v for v in violations if v.severity == "warning"]

    for v in violations:
        print(f"[{v.severity.upper()}] {v.rule_id} {v.object_label}: {v.message}")

    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    if errors:
        sys.exit(1)


def cmd_list_checks(args):
    for c in ALL_CHECKS:
        print(f"{c.rule_id}\t[{c.severity}]\t{c.description}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="liberty-lint")
    parser.add_argument("--db", default="sqlite:///liberty.db", help="SQLAlchemy DB URL")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Parse a .lib file and load it into the database")
    p_ingest.add_argument("lib_file")
    p_ingest.set_defaults(func=cmd_ingest)

    p_check = sub.add_parser("check", help="Run lint checks against ingested data")
    p_check.add_argument("--library-id", type=int, default=None)
    p_check.add_argument("--library-name", default=None)
    p_check.add_argument("--rules", default=None, help="Comma-separated rule IDs, e.g. CCS001,CCS002")
    p_check.set_defaults(func=cmd_check)

    p_list = sub.add_parser("list-checks", help="List all available check rules")
    p_list.set_defaults(func=cmd_list_checks)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
