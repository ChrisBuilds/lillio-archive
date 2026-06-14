import logging
import re
import sys
from pathlib import Path

LOGGER_NAME = "lillio_archive"
URL_QUERY_PATTERN = re.compile(r"(https?://[^\s?]+)\?[^\s]+")
SECRET_PATTERN = re.compile(
    r"(?i)(authorization|cookie|password|token|x-amz-signature)"
    r"([\"'=:\s]+)([^\s,;]+)"
)

_package_logger = logging.getLogger(LOGGER_NAME)
_package_logger.addHandler(logging.NullHandler())
_package_logger.propagate = False


def redact(message: str) -> str:
    message = URL_QUERY_PATTERN.sub(r"\1?[REDACTED]", message)
    return SECRET_PATTERN.sub(r"\1\2[REDACTED]", message)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(
    *,
    verbose: bool = False,
    quiet: bool = False,
    log_file: Path | None = None,
) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = False

    console_level = (
        logging.WARNING if quiet else logging.DEBUG if verbose else logging.INFO
    )
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(console_level)
    console.setFormatter(RedactingFormatter("%(levelname)s: %(message)s"))
    logger.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        log_file.chmod(0o600)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            RedactingFormatter(
                "%(asctime)s %(levelname)-8s %(name)s "
                "%(filename)s:%(lineno)d %(message)s"
            )
        )
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    if name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
