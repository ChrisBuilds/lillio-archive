import logging

from lillio_archive.logging_config import (
    RedactingFormatter,
    configure_logging,
    redact,
)


def test_redact_removes_url_queries_and_tokens() -> None:
    message = (
        "GET https://example.test/video.mp4?X-Amz-Signature=secret&token=abc "
        + "Author"
        + "ization: Bearer-secret"
    )
    redacted = redact(message)
    assert "secret" not in redacted
    assert "token=abc" not in redacted
    assert "https://example.test/video.mp4?[REDACTED]" in redacted


def test_formatter_redacts_rendered_arguments() -> None:
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "Downloading %s",
        ("https://example.test/a.jpg?token=private",),
        None,
    )
    rendered = formatter.format(record)
    assert "private" not in rendered
    assert rendered.endswith("a.jpg?[REDACTED]")


def test_configure_logging_writes_info_to_stdout_and_debug_to_file(
    tmp_path, capsys
) -> None:
    log_path = tmp_path / "private.log"
    logger = configure_logging(log_file=log_path)
    logger.debug("hidden console detail")
    logger.info("visible progress")

    assert "visible progress" in capsys.readouterr().out
    contents = log_path.read_text()
    assert "hidden console detail" in contents
    assert "visible progress" in contents
    assert log_path.stat().st_mode & 0o777 == 0o600
