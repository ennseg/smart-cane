import argparse
import sys

import httpx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cane_server.devchat",
        description="Написать боту от имени сопровождающего (сервер в режиме DRY_RUN)",
    )
    parser.add_argument("chat_id", type=int, help="условный chat_id, например 1001")
    parser.add_argument("text", nargs="?", help="текст сообщения, например /where")
    parser.add_argument("--button", help="нажать inline-кнопку с этими callback_data")
    parser.add_argument("--name", default="Тестовый", help="имя сопровождающего")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="адрес сервера")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if (args.text is None) == (args.button is None):
        print("Укажите текст сообщения или --button", file=sys.stderr)
        return 2
    payload = {"chat_id": args.chat_id, "first_name": args.name}
    if args.text is not None:
        payload["text"] = args.text
    else:
        payload["callback_data"] = args.button
    try:
        response = httpx.post(f"{args.url.rstrip('/')}/dev/bot", json=payload, timeout=30)
    except httpx.HTTPError as error:
        print(f"Сервер недоступен: {error}", file=sys.stderr)
        return 1
    if response.status_code != 200:
        print(f"Ошибка {response.status_code}: {response.text}", file=sys.stderr)
        return 1
    print_replies(response.json()["replies"])
    return 0


def print_replies(replies: list[dict[str, object]]) -> None:
    if not replies:
        print("(бот ничего не ответил)")
    for reply in replies:
        print(f"── {reply['summary']}")


if __name__ == "__main__":
    sys.exit(main())
