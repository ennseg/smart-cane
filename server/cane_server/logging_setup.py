import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, stream=sys.stdout, force=True)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
