from ledger.commands import balance, cmd_add
from ledger.store import Ledger
from ledger.cli import run


def _ledger_with(*amounts):
    ledger = Ledger()
    for amt in amounts:
        cmd_add(ledger, amt, "entry")
    return ledger


def test_balance_nets_credits_and_debits():
    # +100 credit, -30 debit  ->  net 70
    assert balance(_ledger_with(100, -30)) == 70


def test_balance_all_debits():
    assert balance(_ledger_with(-10, -5)) == -15


def test_cli_balance_routes_and_nets():
    ledger = Ledger()
    run(["add", "100", "paycheck"], ledger)
    run(["add", "-40", "rent"], ledger)
    assert run(["balance"], ledger) == "balance: +60"
