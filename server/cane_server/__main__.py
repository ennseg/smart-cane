import uvicorn

from cane_server.app import create_app
from cane_server.config import Settings
from cane_server.logging_setup import configure_logging


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
