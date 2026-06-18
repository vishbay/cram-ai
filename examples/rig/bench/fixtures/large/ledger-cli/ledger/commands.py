from ledger.store import Entry, Ledger


def cmd_add(ledger: Ledger, amount: float, description: str) -> str:
    ledger.add(Entry(description=description, amount=amount))
    return f"added {amount:+g} ({description})"


def balance(ledger: Ledger) -> float:
    # BUG: uses abs(), so debits and credits both add up instead of netting.
    return sum(abs(e.amount) for e in ledger.entries())


def cmd_balance(ledger: Ledger) -> str:
    return f"balance: {balance(ledger):+g}"
