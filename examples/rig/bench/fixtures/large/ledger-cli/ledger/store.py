import dataclasses


@dataclasses.dataclass
class Entry:
    description: str
    amount: float  # positive = credit, negative = debit


class Ledger:
    def __init__(self):
        self._entries: list[Entry] = []

    def add(self, entry: Entry) -> None:
        self._entries.append(entry)

    def entries(self) -> list[Entry]:
        return list(self._entries)
