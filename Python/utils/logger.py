import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from Python.config import LOG_FILE


class ConsoleFormatter(logging.Formatter):
    LEVEL_LABELS = {
        logging.DEBUG: "DBG",
        logging.INFO: "INF",
        logging.WARNING: "WRN",
        logging.ERROR: "ERR",
        logging.CRITICAL: "CRT",
    }

    def format(self, record):
        record.levelshort = self.LEVEL_LABELS.get(record.levelno, record.levelname[:3])
        return super().format(record)


def _get_log_level(env_name: str, default: int) -> int:
    value = os.getenv(env_name)
    if not value:
        return default
    return getattr(logging, value.upper(), default)


def setup_logger(name="rift_watcher", log_level=None):
    console_level = _get_log_level("RIFT_CONSOLE_LOG_LEVEL", logging.INFO)
    file_level = _get_log_level("RIFT_FILE_LOG_LEVEL", logging.DEBUG)
    logger_level = log_level or min(console_level, file_level)

    logger = logging.getLogger(name)
    logger.setLevel(logger_level)
    logger.propagate = False

    if logger.handlers:
        return logger

    console_formatter = ConsoleFormatter(
        "%(asctime)s %(levelshort)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    file_formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    try:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(file_level)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        logger.error(f"ログファイルの作成に失敗しました: {e}")

    return logger


logger = setup_logger()
