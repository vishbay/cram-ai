import sys

from ledger.commands import cmd_add, cmd_balance
from ledger.store import Ledger


def run(argv, ledger: Ledger) -> str:
    """Route one command. argv e.g. ['add', '100', 'paycheck'] or ['balance']."""
    if not argv:
        return "usage: add <amount> <description> | balance"
    cmd, rest = argv[0], argv[1:]
    if cmd == "add":
        amount = float(rest[0])
        description = " ".join(rest[1:])
        return cmd_add(ledger, amount, description)
    if cmd == "balance":
        return cmd_balance(ledger)
    return f"unknown command: {cmd}"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    print(run(argv, Ledger()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
