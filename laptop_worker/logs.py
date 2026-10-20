import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def event(message, *args):
    logger = logging.getLogger("myl.worker")
    if not logger.handlers:
        directory = Path(__file__).resolve().parents[1] / ".web-demo" / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            directory / "worker.events.log",
            maxBytes=2_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    logger.info(message, *args)
