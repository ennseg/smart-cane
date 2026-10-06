# Умная трость — сервер и Telegram-бот

Серверная часть учебного проекта «Умная трость для незрячих с GPS-трекингом и уведомлениями в Telegram».
Принимает от ESP32 телеметрию и тревоги (JSON по HTTP с токеном и подписью HMAC), хранит их в SQLite
и рассылает сопровождающим через Telegram-бота. Бот отвечает на `/where`, `/status`, `/history`.

**Стек:** Python 3.11+, FastAPI, aiogram 3, SQLAlchemy 2 (async) + SQLite, pydantic, uvicorn, pytest.

## Быстрый старт без Telegram и без устройства

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env            # Linux/macOS: cp .env.example .env   (там DRY_RUN=true)

python -m cane_server.cli add-device cane-01 --name "Трость Ивана" --period 5 --sim-config sim/cane-01.json
python -m cane_server                                              # сервер на :8000
python -m cane_server.devchat 1001 "/bind КОД-ИЗ-ВЫВОДА"           # во втором окне
python -m simulator --config sim/cane-01.json --scenario all       # в третьем окне
```

Сообщения бота в режиме DRY_RUN печатаются в лог сервера строками `[TG → 1001] ...`.

## Проверки

```bash
pytest            # 143 автотеста, без интернета и без токена Telegram
ruff check .      # линтер
ruff format --check .
```

## Документация

- [МЕТОДИЧКА.md](МЕТОДИЧКА.md) — пошаговая инструкция: установка, бот, `.env`, регистрация устройства,
  запуск, симулятор, команды бота, деплой, **контракт API для разработчика прошивки**, частые проблемы.
- [ОБЪЯСНЕНИЕ.md](ОБЪЯСНЕНИЕ.md) — подробно о том, как всё устроено (с аналогиями из Java/Spring Boot).
- [reports/](reports/) — логи сквозного прогона сервера в DRY_RUN с симулятором.
- [docs/](docs/) — техническое задание и описание архитектуры системы.

## Структура

```
cane_server/   сервер: api/ (HTTP для устройства), bot/ (aiogram), services/ (логика), storage/ (БД)
simulator/     симулятор прошивки ESP32 со сценариями
tests/         автотесты pytest
```
