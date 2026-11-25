import json
import logging
from logging.handlers import RotatingFileHandler

from .core import LOGS, now


class JsonFormatter(logging.Formatter):
    def format(self, record):
        value = {"time": now(), "level": record.levelname, "event": record.getMessage()}
        for key in ("job", "stage", "progress", "status", "seconds", "gpu_peak_system_mib"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value, ensure_ascii=False)


def configure():
    logger = logging.getLogger("soceth")
    if not logger.handlers:
        sink = RotatingFileHandler(LOGS / "studio.jsonl", maxBytes=8 * 1024**2, backupCount=4, encoding="utf-8")
        sink.setFormatter(JsonFormatter())
        logger.addHandler(sink)
        logger.setLevel(logging.INFO)

