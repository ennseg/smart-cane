import json
from pathlib import Path

import pytest

from cane_server import cli
from cane_server.security import sha256_hex, sign_body, signature_matches, token_matches
from cane_server.services.texts import format_ago, format_duration
from simulator.device import encode, sign
from simulator.physics import Walker, battery_percent


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "только что"),
        (59, "только что"),
        (60, "1 мин назад"),
        (59 * 60, "59 мин назад"),
        (3600, "1 ч назад"),
        (3600 + 25 * 60, "1 ч 25 мин назад"),
        (3 * 86400, "3 дн назад"),
        (-10, "только что"),
    ],
)
def test_format_ago(seconds: int, expected: str) -> None:
    assert format_ago(seconds) == expected


def test_format_duration() -> None:
    assert format_duration(42) == "42 с"
    assert format_duration(600) == "10 мин"
    assert format_duration(7260) == "2 ч 1 мин"


def test_token_and_signature_checks() -> None:
    assert token_matches(sha256_hex("secret-token"), "secret-token")
    assert not token_matches(sha256_hex("secret-token"), "secret-tokeN")
    body = b'{"seq":1}'
    signature = sign_body("key", body)
    assert signature_matches("key", body, signature)
    assert signature_matches("key", body, f"  {signature.upper()} ")
    assert not signature_matches("other", body, signature)
    assert not signature_matches("key", body, "кириллица")


def test_simulator_signs_exactly_like_server_expects() -> None:
    packet = {"dev": "cane-01", "seq": 7, "type": "pos", "lat": 59.9}
    body = encode(packet)
    assert sign("s3cret", body) == sign_body("s3cret", body)
    assert json.loads(body) == packet


@pytest.mark.parametrize(
    ("voltage", "percent"),
    [(4.25, 100), (4.20, 100), (4.00, 80), (3.85, 54), (3.60, 15), (3.50, 8), (3.20, 0)],
)
def test_simulator_battery_table_matches_architecture(voltage: float, percent: int) -> None:
    assert battery_percent(voltage) == percent


def test_walker_moves_roughly_requested_distance() -> None:
    walker = Walker()
    start = (walker.lat, walker.lon)
    walker.walk(100)
    moved_lat_m = (walker.lat - start[0]) * 111_320
    assert abs(moved_lat_m) <= 101


def run_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *args: str) -> int:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path.as_posix()}/cli.db")
    monkeypatch.chdir(tmp_path)
    return cli.main(list(args))


def test_cli_registers_device_and_writes_simulator_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "sim" / "cane-01.json"
    code = run_cli(
        monkeypatch,
        tmp_path,
        "add-device",
        "cane-01",
        "--name",
        "Трость Ивана",
        "--period",
        "10",
        "--sim-config",
        str(config_path),
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "КОД ПРИВЯЗКИ" in output
    assert '#define CANE_SERVER_URL  "http://127.0.0.1:8000/api/v1/telemetry"' in output
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["dev"] == "cane-01"
    assert config["token"] in output
    assert config["secret"] in output

    assert run_cli(monkeypatch, tmp_path, "list-devices") == 0
    assert "период 10 с" in capsys.readouterr().out


def test_cli_rejects_duplicate_and_invalid_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(monkeypatch, tmp_path, "add-device", "cane-01", "--name", "A") == 0
    assert run_cli(monkeypatch, tmp_path, "add-device", "cane-01", "--name", "B") == 1
    assert run_cli(monkeypatch, tmp_path, "add-device", "bad id!", "--name", "C") == 1
    assert "уже зарегистрировано" in capsys.readouterr().err


def test_cli_new_code_and_rotation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_cli(monkeypatch, tmp_path, "add-device", "cane-01", "--name", "A")
    capsys.readouterr()
    assert run_cli(monkeypatch, tmp_path, "new-code", "cane-01") == 0
    assert "Новый одноразовый код" in capsys.readouterr().out
    assert run_cli(monkeypatch, tmp_path, "rotate-token", "cane-01") == 0
    assert "Старые токен и секрет больше не действуют" in capsys.readouterr().out
    assert run_cli(monkeypatch, tmp_path, "new-code", "cane-77") == 1
