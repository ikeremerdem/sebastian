"""Sebastian: an obedient personal-productivity backend for the Hermes agent."""

import argparse
import secrets
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sebastian", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the server (migrates the database first)")
    sub.add_parser("migrate", help="apply database migrations and exit")
    sub.add_parser("genkey", help="print a random secret (API key / session secret)")
    sub.add_parser("hash-password", help="hash a UI password for SEBASTIAN_UI_PASSWORD_HASH")
    backup = sub.add_parser("backup", help="consistent SQLite backup, pruning old copies")
    backup.add_argument("--to", required=True, help="backup directory")
    backup.add_argument("--keep", type=int, default=14, help="how many backups to keep")
    args = parser.parse_args(argv)

    if args.cmd == "genkey":
        print(secrets.token_urlsafe(32))
        return 0
    if args.cmd == "hash-password":
        import getpass

        from argon2 import PasswordHasher

        pw = getpass.getpass("UI password: ")
        if pw != getpass.getpass("Repeat: "):
            print("passwords differ", file=sys.stderr)
            return 1
        print(PasswordHasher().hash(pw))
        return 0
    if args.cmd == "migrate":
        from .migrate import upgrade_to_head

        upgrade_to_head()
        return 0
    if args.cmd == "backup":
        from .backup import backup_database

        print(backup_database(args.to, args.keep))
        return 0

    import uvicorn

    from .config import get_config

    cfg = get_config()
    if not cfg.api_key or cfg.api_key == "change-me":
        print("SEBASTIAN_API_KEY is not set (generate one: sebastian genkey)", file=sys.stderr)
        return 1
    uvicorn.run("sebastian.main:app", host=cfg.host, port=cfg.port, log_level="info")
    return 0
