import argparse
import sys
import time
from pathlib import Path

import httpx

from simulator.config import SequenceStore, SimulatorConfig
from simulator.device import VirtualCane
from simulator.scenarios import ScenarioRunner

SCENARIOS = ("walk", "sos", "still", "battery", "gps_loss", "offline", "errors", "serve", "all")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator", description="Симулятор прошивки умной трости"
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="JSON из cli add-device --sim-config"
    )
    parser.add_argument("--scenario", choices=SCENARIOS, default="all")
    parser.add_argument(
        "--period", type=float, default=None, help="свой период, с (иначе из cfg сервера)"
    )
    parser.add_argument("--shuffle", action="store_true", help="досылать накопленное вперемешку")
    parser.add_argument("--steps", type=int, default=6, help="число шагов в сценарии walk")
    parser.add_argument(
        "--crit-sleep", type=float, default=None, help="сколько молчать после batt_crit, с"
    )
    return parser


def log(message: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"{stamp} {message}" if message.strip() else message, flush=True)


def run(args: argparse.Namespace) -> None:
    config = SimulatorConfig.load(args.config)
    sequence = SequenceStore(args.config.with_suffix(".seq.json"))
    with httpx.Client() as client:
        cane = VirtualCane(config, sequence, client, args.period, log)
        runner = ScenarioRunner(cane, log, crit_sleep_s=args.crit_sleep)
        log(f"Симулятор {config.dev} → {config.url}")
        dispatch(runner, args)
        log("Сценарий завершён")


def dispatch(runner: ScenarioRunner, args: argparse.Namespace) -> None:
    match args.scenario:
        case "walk":
            runner.walk(args.steps)
        case "offline":
            runner.offline(shuffle=args.shuffle)
        case "all":
            runner.run_all(shuffle=args.shuffle)
        case name:
            getattr(runner, name)()


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except KeyboardInterrupt:
        log("Остановлено пользователем")
    except FileNotFoundError as error:
        print(f"Не найден файл конфигурации: {error.filename}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
