import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SimulatorConfig:
    url: str
    dev: str
    token: str
    secret: str

    @classmethod
    def load(cls, path: Path) -> "SimulatorConfig":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            url=data["url"].rstrip("/"), dev=data["dev"], token=data["token"], secret=data["secret"]
        )


class SequenceStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._next = self._load()

    def _load(self) -> int:
        if not self._path.exists():
            return 1
        return int(json.loads(self._path.read_text(encoding="utf-8"))["next_seq"])

    def take(self) -> int:
        value = self._next
        self._next += 1
        self._path.write_text(json.dumps({"next_seq": self._next}), encoding="utf-8")
        return value
