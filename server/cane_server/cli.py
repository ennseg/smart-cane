import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from cane_server.clock import SystemClock
from cane_server.config import Settings
from cane_server.services.binding import BindingService
from cane_server.services.devices import DeviceCredentials, DeviceRegistry, DeviceRegistryError
from cane_server.services.notifier import Notifier
from cane_server.services.texts import Texts
from cane_server.storage import repositories
from cane_server.storage.database import Database

TELEMETRY_PATH = "/api/v1/telemetry"


class Toolkit:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.clock = SystemClock()
        self.db = Database(settings.database_url)
        binding = BindingService(
            Notifier(),
            Texts(settings.display_tz, settings.map_url_template),
            max_failures=settings.bind_max_failures,
            block_min=settings.bind_block_min,
            invite_ttl_h=settings.invite_ttl_h,
        )
        self.registry = DeviceRegistry(binding)

    def default_server_url(self) -> str:
        return f"http://127.0.0.1:{self.settings.port}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cane_server.cli", description="Управление устройствами умной трости"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init-db", help="создать таблицы базы данных")

    add = commands.add_parser("add-device", help="зарегистрировать новую трость")
    add.add_argument("device_id", help="идентификатор, например cane-01")
    add.add_argument("--name", required=True, help="понятное имя, например «Трость Ивана»")
    add.add_argument("--period", type=int, default=None, help="период передачи, секунд")
    _add_output_options(add)

    code = commands.add_parser("new-code", help="выпустить новый код привязки для паспорта")
    code.add_argument("device_id")

    rotate = commands.add_parser("rotate-token", help="выдать новый токен и секрет HMAC")
    rotate.add_argument("device_id")
    _add_output_options(rotate)

    commands.add_parser("list-devices", help="список устройств")

    for name, help_text in (
        ("disable-device", "запретить приём данных"),
        ("enable-device", "разрешить приём данных"),
    ):
        toggle = commands.add_parser(name, help=help_text)
        toggle.add_argument("device_id")

    period = commands.add_parser("set-period", help="изменить период передачи устройства")
    period.add_argument("device_id")
    period.add_argument("seconds", type=int)
    return parser


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--server-url", default=None, help="адрес сервера для прошивки и симулятора"
    )
    parser.add_argument(
        "--sim-config", type=Path, default=None, help="куда сохранить конфиг симулятора"
    )


async def init_db(toolkit: Toolkit, _args: argparse.Namespace) -> None:
    print(f"База данных готова: {toolkit.settings.database_url}")


async def add_device(toolkit: Toolkit, args: argparse.Namespace) -> None:
    period = args.period or toolkit.settings.default_period_s
    async with toolkit.db.transaction() as session:
        registered = await toolkit.registry.register(
            session, args.device_id, args.name, period, toolkit.clock.now()
        )
    print(f"Устройство «{args.name}» ({args.device_id}) зарегистрировано.\n")
    print("КОД ПРИВЯЗКИ для паспорта изделия (одноразовый):")
    print(f"    {registered.bind_code}\n")
    _print_credentials(toolkit, registered.credentials, args)


async def new_code(toolkit: Toolkit, args: argparse.Namespace) -> None:
    async with toolkit.db.transaction() as session:
        code = await toolkit.registry.issue_passport_code(
            session, args.device_id, toolkit.clock.now()
        )
    print(f"Новый одноразовый код привязки для {args.device_id}: {code}")


async def rotate_token(toolkit: Toolkit, args: argparse.Namespace) -> None:
    async with toolkit.db.transaction() as session:
        credentials = await toolkit.registry.rotate_credentials(session, args.device_id)
    print("Старые токен и секрет больше не действуют. Прошейте устройство заново.\n")
    _print_credentials(toolkit, credentials, args)


async def list_devices(toolkit: Toolkit, _args: argparse.Namespace) -> None:
    async with toolkit.db.transaction() as session:
        devices = await repositories.list_devices(session)
        rows = [
            (device, await repositories.count_guardians(session, device.id)) for device in devices
        ]
    if not rows:
        print("Устройств пока нет. Добавьте: python -m cane_server.cli add-device ...")
        return
    texts = Texts(toolkit.settings.display_tz, toolkit.settings.map_url_template)
    now = toolkit.clock.now()
    for device, guardians in rows:
        seen = texts.moment(device.last_seen_at, now) if device.last_seen_at else "никогда"
        state = "включено" if device.enabled else "ОТКЛЮЧЕНО"
        print(
            f"{device.id:<16} «{device.name}» · {state} · период {device.period_s} с · "
            f"сопровождающих: {guardians} · последний контакт: {seen}"
        )


async def disable_device(toolkit: Toolkit, args: argparse.Namespace) -> None:
    await _set_enabled(toolkit, args.device_id, enabled=False)


async def enable_device(toolkit: Toolkit, args: argparse.Namespace) -> None:
    await _set_enabled(toolkit, args.device_id, enabled=True)


async def _set_enabled(toolkit: Toolkit, device_id: str, enabled: bool) -> None:
    async with toolkit.db.transaction() as session:
        await toolkit.registry.set_enabled(session, device_id, enabled)
    print(f"Устройство {device_id} {'включено' if enabled else 'отключено'}.")


async def set_period(toolkit: Toolkit, args: argparse.Namespace) -> None:
    async with toolkit.db.transaction() as session:
        await toolkit.registry.set_period(session, args.device_id, args.seconds)
    print(f"Период {args.device_id}: {args.seconds} с (устройство получит его в поле cfg).")


def _print_credentials(
    toolkit: Toolkit, credentials: DeviceCredentials, args: argparse.Namespace
) -> None:
    server_url = args.server_url or toolkit.default_server_url()
    print("Данные для прошивки (показываются ОДИН раз, сохраните их):")
    print(f"    X-Device-Token: {credentials.token}")
    print(f"    HMAC secret:    {credentials.hmac_secret}\n")
    print("Фрагмент secrets.h для прошивки ESP32:")
    print(firmware_header(credentials, server_url))
    if args.sim_config is not None:
        save_simulator_config(args.sim_config, credentials, server_url)
        print(f"\nКонфиг симулятора сохранён: {args.sim_config}")


def firmware_header(credentials: DeviceCredentials, server_url: str) -> str:
    return "\n".join(
        [
            "#pragma once",
            f'#define CANE_DEVICE_ID   "{credentials.device_id}"',
            f'#define CANE_TOKEN       "{credentials.token}"',
            f'#define CANE_HMAC_SECRET "{credentials.hmac_secret}"',
            f'#define CANE_SERVER_URL  "{server_url.rstrip("/")}{TELEMETRY_PATH}"',
        ]
    )


def save_simulator_config(path: Path, credentials: DeviceCredentials, server_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "url": server_url.rstrip("/"),
        "dev": credentials.device_id,
        "token": credentials.token,
        "secret": credentials.hmac_secret,
    }
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


COMMANDS: dict[str, Callable[[Toolkit, argparse.Namespace], Awaitable[None]]] = {
    "init-db": init_db,
    "add-device": add_device,
    "new-code": new_code,
    "rotate-token": rotate_token,
    "list-devices": list_devices,
    "disable-device": disable_device,
    "enable-device": enable_device,
    "set-period": set_period,
}


async def run(args: argparse.Namespace, settings: Settings) -> int:
    toolkit = Toolkit(settings)
    try:
        await toolkit.db.create_schema()
        await COMMANDS[args.command](toolkit, args)
    except DeviceRegistryError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    finally:
        await toolkit.db.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    sys.exit(main())
