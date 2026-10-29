import os
from pathlib import Path
import uvicorn

if __name__ == "__main__":
    logs = Path(
        os.environ.get(
            "MYL_LOG_DIR", Path(__file__).resolve().parents[3] / ".web-demo" / "logs"
        )
    )
    logs.mkdir(parents=True, exist_ok=True)
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(message)s"}},
        "handlers": {
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(logs / "server.log"),
                "maxBytes": 2_000_000,
                "backupCount": 2,
                "encoding": "utf-8",
                "formatter": "plain",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {
                "handlers": ["file"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }
    uvicorn.run(
        "demo_server.app:create_app",
        factory=True,
        host=os.environ.get("MYL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYL_PORT", "8000")),
        workers=1,
        access_log=False,
        log_config=log_config,
    )
