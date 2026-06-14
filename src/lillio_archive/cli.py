import argparse
import json
import logging
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Set

from . import __version__
from .archive import (
    archive_report,
    export_archive,
    reconcile_archive,
    verify_archive,
)
from .browser import LillioBrowser
from .config import Config, load_config
from .downloader import download_discovered, valid_archive_source_keys
from .logging_config import configure_logging
from .manifest import Manifest
from .repair import repair_future_archive_dates
from .results import RunResult


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Archive media from an authorized Lillio parent account."
    )
    result.add_argument(
        "command",
        choices=(
            "inspect",
            "download",
            "verify",
            "reconcile",
            "export",
            "report",
            "repair-dates",
        ),
    )
    result.add_argument("--config", type=Path, default=Path("lillio-archive.toml"))
    result.add_argument("--base-url")
    result.add_argument("--profile-dir", type=Path)
    result.add_argument("--download-dir", type=Path)
    result.add_argument("--artifact-dir", type=Path)
    result.add_argument("--export-dir", type=Path)
    result.add_argument("--max-expand-actions", type=int)
    result.add_argument("--retry-count", type=int)
    result.add_argument("--retry-delay", type=float)
    result.add_argument("--since", type=iso_date)
    result.add_argument("--until", type=iso_date)
    result.add_argument("--new", action="store_true")
    result.add_argument(
        "--full-scan",
        action="store_true",
        help="load all historical pages instead of stopping at an archived page",
    )
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--apply", action="store_true")
    result.add_argument(
        "--link-mode",
        choices=("auto", "hardlink", "copy"),
        default="auto",
    )
    result.add_argument("--include-sidecars", action="store_true")
    browser = result.add_mutually_exclusive_group()
    browser.add_argument("--headless", action="store_true")
    browser.add_argument("--visible", action="store_true")
    verbosity = result.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true")
    verbosity.add_argument("-q", "--quiet", action="store_true")
    result.add_argument("--log-file", type=Path)
    result.add_argument("--no-log-file", action="store_true")
    return result


def _with_browser(
    config: Config,
    operation: Callable[[LillioBrowser], RunResult | int],
    *,
    archived_source_keys: Optional[Set[str]] = None,
) -> RunResult | int:
    initial = (
        replace(config, browser_mode="headless")
        if config.browser_mode == "auto"
        else config
    )
    try:
        with LillioBrowser(initial) as browser:
            browser.open()
            browser.wait_for_authenticated_feed()
            browser.expand_feed(archived_source_keys=archived_source_keys)
            return operation(browser)
    except RuntimeError as error:
        if str(error) != "authentication_required" or config.browser_mode != "auto":
            raise
    logging.getLogger("lillio_archive.cli").info(
        "Saved session expired; reopening a visible browser for login"
    )
    with LillioBrowser(replace(config, browser_mode="visible")) as browser:
        browser.open()
        browser.wait_for_authenticated_feed()
        browser.expand_feed(archived_source_keys=archived_source_keys)
        return operation(browser)


def _record_result(config: Config, result: RunResult) -> None:
    if result.finished_at is None:
        result.finish()
    with Manifest(config.manifest_path) as manifest:
        manifest.record_run(
            command=result.command,
            started_at=result.started_at,
            finished_at=result.finished_at or result.started_at,
            filters_json=json.dumps(result.filters, sort_keys=True),
            totals_json=json.dumps(result.counts, sort_keys=True),
            bytes_count=sum(item.bytes for item in result.items),
            status="failed" if result.failed else "success",
        )


def run(args: argparse.Namespace, config: Config) -> int:
    logger = logging.getLogger("lillio_archive.cli")
    logger.info("Starting command: %s", args.command)
    result: Optional[RunResult] = None

    if args.command == "repair-dates":
        repaired = repair_future_archive_dates(config)
        result = RunResult(command="repair-dates")
        result.add(source_key="archive", status="repaired", message=str(repaired))
    if args.command == "verify":
        result = verify_archive(config)
    elif args.command == "report":
        result = archive_report(config)
    elif args.command == "export":
        result = export_archive(
            config,
            link_mode=args.link_mode,
            include_sidecars=args.include_sidecars,
        )
    elif args.command == "inspect":
        def inspect(browser: LillioBrowser) -> RunResult:
            path = browser.write_inspection(browser.inspect())
            inspection = RunResult(command="inspect")
            inspection.add(
                source_key="feed",
                status="inspected",
                filename=str(path),
            )
            return inspection

        result = _with_browser(config, inspect)
    elif args.command == "download":
        archived_source_keys = (
            None if args.full_scan else valid_archive_source_keys(config)
        )
        result = _with_browser(
            config,
            lambda browser: download_discovered(
                browser,
                config,
                since=args.since,
                until=args.until,
                new_only=args.new,
                dry_run=args.dry_run,
                full_scan=args.full_scan,
            ),
            archived_source_keys=archived_source_keys,
        )
    elif args.command == "reconcile":
        result = _with_browser(
            config,
            lambda browser: reconcile_archive(
                config, browser.discover_media(), apply=args.apply
            ),
        )

    assert isinstance(result, RunResult)
    json_path, csv_path = result.write(config.report_dir)
    if args.command != "download" or not args.dry_run:
        if args.command != "download":
            _record_result(config, result)
    logger.info("Reports: %s and %s", json_path, csv_path)
    logger.info("Summary: %s", result.counts)
    return 1 if result.failed else 0


def main() -> None:
    args = parser().parse_args()
    browser_mode = "headless" if args.headless else "visible" if args.visible else None
    config = load_config(
        path=args.config,
        overrides={
            "base_url": args.base_url,
            "profile_dir": args.profile_dir,
            "download_dir": args.download_dir,
            "artifact_dir": args.artifact_dir,
            "export_dir": args.export_dir,
            "browser_mode": browser_mode,
            "max_expand_actions": args.max_expand_actions,
            "retry_count": args.retry_count,
            "retry_delay": args.retry_delay,
        },
    )
    log_path = None if args.no_log_file else args.log_file or config.log_path
    logger = configure_logging(
        verbose=args.verbose, quiet=args.quiet, log_file=log_path
    )
    logger.info("Lillio Archive %s", __version__)
    try:
        raise SystemExit(run(args, config))
    except KeyboardInterrupt:
        logger.warning("Cancelled by user")
        raise SystemExit(130)
    except Exception as error:
        logger.error("Command failed: %s", error)
        logger.debug("Unhandled exception details", exc_info=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
