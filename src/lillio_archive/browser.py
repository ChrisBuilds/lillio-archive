import json
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from .config import Config
from .logging_config import get_logger


logger = get_logger(__name__)


LOAD_MORE_PATTERN = re.compile(
    r"(load|show|view)\s+(more|older)|older\s+(posts|activities|updates)",
    re.IGNORECASE,
)
ACTIVITY_ID_PATTERN = re.compile(r"^activity-(\d+)-modal$")
DATE_PATTERN = re.compile(
    r"\b("
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?"
    r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
    re.IGNORECASE,
)
IGNORABLE_REQUEST_HOSTS = {
    "analytics.google.com",
    "api-iam.intercom.io",
}


@dataclass(frozen=True)
class MediaCandidate:
    activity_id: str
    media_type: str
    source_url: str
    activity_date: Optional[str]
    activity_date_source: str
    title: Optional[str]
    description: Optional[str]
    list_date: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def source_key(self) -> str:
        return f"{self.activity_id}:{self.media_type}"

    @property
    def metadata_fingerprint(self) -> str:
        payload = json.dumps(
            {
                "activity_date": self.activity_date,
                "created_at": self.created_at,
                "description": self.description,
                "list_date": self.list_date,
                "media_type": self.media_type,
                "title": self.title,
                "updated_at": self.updated_at,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def parse_activity_date(text: str, today: Optional[date] = None) -> Optional[str]:
    match = DATE_PATTERN.search(text)
    if not match:
        return None

    reference = today or date.today()
    month = datetime.strptime(match.group(1)[:3].title(), "%b").month
    year = int(match.group(3)) if match.group(3) else reference.year
    parsed = date(year, month, int(match.group(2)))
    if not match.group(3) and parsed > reference:
        parsed = parsed.replace(year=year - 1)
    return parsed.isoformat()


def authoritative_activity_date(activity: Dict[str, Any]) -> Optional[str]:
    for field in ("list_date", "created_at"):
        value = activity.get(field)
        if not value:
            continue
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            logger.debug(
                "Ignoring malformed authoritative date activity=%s field=%s value=%r",
                activity.get("id"),
                field,
                value,
            )
    return None


def fully_archived_page(
    media_keys: Set[str],
    archived_source_keys: Set[str],
) -> bool:
    return bool(media_keys) and media_keys <= archived_source_keys


class LillioBrowser:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._playwright: Playwright
        self.context: BrowserContext
        self.page: Page
        self._activities: Dict[str, Dict[str, Any]] = {}

    def __enter__(self) -> "LillioBrowser":
        logger.info(
            "Starting Chromium (headless=%s, profile=%s)",
            self.config.headless,
            self.config.profile_dir,
        )
        self.config.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.profile_dir.chmod(0o700)
        self._playwright = sync_playwright().start()
        self.context = self._playwright.chromium.launch_persistent_context(
            str(self.config.profile_dir),
            headless=self.config.headless,
            accept_downloads=True,
            viewport={"width": 1440, "height": 1000},
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.on("console", self._log_console_message)
        self.page.on("pageerror", lambda error: logger.error("Browser page error: %s", error))
        self.page.on("requestfailed", self._log_failed_request)
        self.page.on("response", self._handle_response)
        logger.debug("Chromium started with %d existing page(s)", len(self.context.pages))
        return self

    @staticmethod
    def _log_console_message(message: Any) -> None:
        if message.type not in {"error", "warning"}:
            return
        if message.text.startswith("Failed to load resource:"):
            logger.debug("Browser console %s: %s", message.type, message.text)
            return
        logger.warning("Browser console %s: %s", message.type, message.text)

    @staticmethod
    def _log_failed_request(request: Any) -> None:
        host = urlparse(request.url).hostname
        is_thumbnail = host == "himama.s3.amazonaws.com" and "/thumb_" in request.url
        log = (
            logger.debug
            if host in IGNORABLE_REQUEST_HOSTS or is_thumbnail
            else logger.warning
        )
        log(
            "Browser request failed: %s %s (%s)",
            request.method,
            request.url,
            request.failure,
        )

    @staticmethod
    def _log_error_response(response: Any) -> None:
        if response.status < 400:
            return
        host = urlparse(response.url).hostname
        log = logger.debug if host in IGNORABLE_REQUEST_HOSTS else logger.warning
        log("Browser HTTP %d: %s", response.status, response.url)

    def _handle_response(self, response: Any) -> None:
        self._log_error_response(response)
        path = urlparse(response.url).path
        if response.status >= 400 or not path.endswith(
            ("all_activities_api", "journal_api")
        ):
            return
        try:
            self._record_journal_payload(response.json())
        except Exception as error:
            logger.warning(
                "Could not read authoritative dates from %s: %s",
                response.url,
                error,
            )

    def _record_journal_payload(self, payload: Dict[str, Any]) -> int:
        recorded = 0
        for activities in payload.get("intervals", {}).values():
            for wrapper in activities:
                activity = wrapper.get("activity", {})
                activity_id = activity.get("id")
                activity_date = authoritative_activity_date(activity)
                if activity_id is None or activity_date is None:
                    continue
                key = str(activity_id)
                if self._activities.get(key) != activity:
                    self._activities[key] = activity
                    recorded += 1
        if recorded:
            logger.debug(
                "Captured %d authoritative activity date(s); %d total",
                recorded,
                len(self._activities),
            )
        return recorded

    def __exit__(self, *_args: object) -> None:
        logger.debug("Closing browser context")
        self.context.close()
        self._playwright.stop()
        logger.info("Browser closed")

    def open(self) -> None:
        logger.info("Opening Lillio")
        started = time.monotonic()
        self.page.goto(self.config.base_url, wait_until="domcontentloaded")
        logger.info(
            "Page loaded in %.2fs: %s (%s)",
            time.monotonic() - started,
            self.page.title(),
            self.page.url,
        )

    def dismiss_app_reminder(self) -> bool:
        reminder = self.page.get_by_role("link", name="Remind me later")
        if reminder.count() == 1 and reminder.is_visible():
            logger.info("Dismissing the parent-app reminder")
            reminder.click()
            return True
        logger.debug("Parent-app reminder is not visible")
        return False

    def wait_for_authenticated_feed(self) -> None:
        if "/login" in self.page.url:
            if self.config.headless:
                raise RuntimeError("authentication_required")
            logger.info(
                "Waiting for interactive sign-in; credentials stay in the browser"
            )
            print(
                "\nSign in to Lillio and navigate to the activity/feed page in "
                "the browser window."
            )
            input("Press Enter here when the feed is visible...")
            if "/login" in self.page.url:
                raise RuntimeError("The browser is still on the login page.")
        self.dismiss_app_reminder()
        self.page.locator(".activity-modal").first.wait_for(
            state="attached", timeout=15_000
        )
        logger.info(
            "Authenticated feed ready with %d initial activities",
            self.page.locator(".activity-modal").count(),
        )

    def loaded_media_source_keys(self) -> Set[str]:
        values = self.page.evaluate(
            """
            () => [...document.querySelectorAll('.activity-modal')].flatMap(el => {
              const match = /^activity-(\\d+)-modal$/.exec(el.id);
              const type = (el.dataset.type || '').toLowerCase();
              if (!match || !['image', 'video'].includes(type)) return [];
              return [`${match[1]}:${type}`];
            })
            """
        )
        return set(values)

    def expand_feed(
        self,
        *,
        archived_source_keys: Optional[Set[str]] = None,
    ) -> int:
        actions = 0
        control = self.page.locator(".more-images-btn")
        stop_keys = archived_source_keys or set()
        logger.info(
            "Expanding feed (maximum %d pagination actions)",
            self.config.max_expand_actions,
        )
        initial_keys = self.loaded_media_source_keys()
        if fully_archived_page(initial_keys, stop_keys):
            logger.info(
                "Initial feed page is fully archived (%d media item(s)); "
                "skipping older pages",
                len(initial_keys),
            )
            return 0
        while actions < self.config.max_expand_actions:
            if control.count() != 1 or not control.is_visible():
                logger.info("No visible Load More control remains")
                break
            label = control.inner_text().strip()
            if not LOAD_MORE_PATTERN.search(label):
                logger.error("Unexpected pagination control label: %r", label)
                raise RuntimeError(f"Unexpected pagination control: {label!r}")

            previous_count = self.page.locator(".activity-modal").count()
            previous_keys = self.loaded_media_source_keys()
            page_number = control.get_attribute("data-page") or "unknown"
            logger.info(
                "Loading feed page %s (%d activities currently loaded)",
                page_number,
                previous_count,
            )
            started = time.monotonic()
            control.click()
            actions += 1
            self.page.wait_for_function(
                """
                previousCount => {
                  const noMore = document.querySelector(
                    '#no-more-thumbnails'
                  );
                  return document.querySelectorAll('.activity-modal').length >
                    previousCount || (noMore && noMore.offsetParent !== null);
                }
                """,
                arg=previous_count,
                timeout=15_000,
            )
            current_count = self.page.locator(".activity-modal").count()
            logger.info(
                "Pagination action %d completed in %.2fs: %d new, %d total",
                actions,
                time.monotonic() - started,
                current_count - previous_count,
                current_count,
            )
            new_keys = self.loaded_media_source_keys() - previous_keys
            if fully_archived_page(new_keys, stop_keys):
                logger.info(
                    "Loaded page is fully archived (%d media item(s)); "
                    "stopping before older pages",
                    len(new_keys),
                )
                break

        if actions == self.config.max_expand_actions and control.is_visible():
            logger.warning(
                "Stopped at the configured pagination limit; older media may remain"
            )
        logger.info("Feed expansion finished after %d action(s)", actions)
        return actions

    def inspect(self) -> Dict[str, Any]:
        return self.page.evaluate(
            """
            () => {
              const attrs = (el) => {
                const result = {};
                for (const name of [
                  'aria-label', 'data-testid', 'href', 'role', 'type'
                ]) {
                  const value = el.getAttribute(name);
                  if (value) result[name] = value;
                }
                return result;
              };
              const compact = (el) => ({
                tag: el.tagName.toLowerCase(),
                text: (el.innerText || el.getAttribute('aria-label') || '')
                  .trim().replace(/\\s+/g, ' ').slice(0, 120),
                attrs: attrs(el)
              });
              return {
                url: location.origin + location.pathname,
                title: document.title,
                buttons: [...document.querySelectorAll('button')]
                  .filter(el => el.offsetParent !== null).slice(0, 100).map(compact),
                links: [...document.querySelectorAll('a[href]')]
                  .filter(el => el.offsetParent !== null).slice(0, 150).map(compact),
                media: [...document.querySelectorAll('img, video, source')]
                  .filter(el => el.offsetParent !== null).slice(0, 200).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    srcOrigin: (() => {
                      try { return new URL(el.currentSrc || el.src).origin; }
                      catch (_) { return null; }
                    })(),
                    attrs: attrs(el)
                  }))
              };
            }
            """
        )

    def write_inspection(self, report: Dict[str, Any]) -> Path:
        self.config.artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.artifact_dir.chmod(0o700)
        path = self.config.artifact_dir / "inspection.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        path.chmod(0o600)
        logger.info("Wrote redacted inspection report: %s", path)
        return path

    def discover_media(self) -> List[MediaCandidate]:
        logger.info("Discovering downloadable media in loaded activities")
        values: List[Dict[str, str]] = self.page.evaluate(
            """
            () => [...document.querySelectorAll('.activity-modal')].map(el => ({
              id: el.id,
              type: el.dataset.type || '',
              date: (el.querySelector('.activity-date')?.textContent || '').trim(),
              title: (el.querySelector('h4')?.textContent || '').trim(),
              description: (
                el.querySelector('.activity-description')?.textContent || ''
              ).trim().replace(/\\s+/g, ' ')
            }))
            """
        )
        candidates = []
        ignored = 0
        for value in values:
            match = ACTIVITY_ID_PATTERN.match(value["id"])
            media_type = value["type"].lower()
            if not match or media_type not in {"image", "video"}:
                ignored += 1
                continue
            activity_id = match.group(1)
            authoritative = self._activities.get(activity_id, {})
            authoritative_date = authoritative_activity_date(authoritative)
            activity_date = authoritative_date or parse_activity_date(value["date"])
            date_source = "journal_api" if authoritative_date else "dom_inference"
            if authoritative_date is None:
                logger.warning(
                    "Activity %s has no journal API date; inferred %s from %r",
                    activity_id,
                    activity_date or "unknown",
                    value["date"],
                )
            candidates.append(
                MediaCandidate(
                    activity_id=activity_id,
                    media_type=media_type,
                    source_url=f"{self.config.base_url.rstrip('/')}/activities/"
                    f"{activity_id}.{media_type}",
                    activity_date=activity_date,
                    activity_date_source=date_source,
                    title=(authoritative.get("title") or value["title"])[:500] or None,
                    description=(
                        authoritative.get("description") or value["description"]
                    )[:4000]
                    or None,
                    list_date=authoritative.get("list_date"),
                    created_at=authoritative.get("created_at"),
                    updated_at=authoritative.get("updated_at"),
                )
            )
        image_count = sum(item.media_type == "image" for item in candidates)
        video_count = sum(item.media_type == "video" for item in candidates)
        missing_dates = sum(item.activity_date is None for item in candidates)
        logger.info(
            "Discovered %d media item(s): %d image(s), %d video(s)",
            len(candidates),
            image_count,
            video_count,
        )
        if ignored:
            logger.debug("Ignored %d unsupported or malformed activity modal(s)", ignored)
        if missing_dates:
            logger.warning("%d media item(s) have no parseable activity date", missing_dates)
        for candidate in candidates:
            logger.debug(
                "Candidate activity=%s type=%s date=%s title_length=%d "
                "date_source=%s description_length=%d endpoint=%s",
                candidate.activity_id,
                candidate.media_type,
                candidate.activity_date or "unknown",
                len(candidate.title or ""),
                candidate.activity_date_source,
                len(candidate.description or ""),
                candidate.source_url,
            )
        return candidates
