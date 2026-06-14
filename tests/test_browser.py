from datetime import date

from lillio_archive.browser import (
    IGNORABLE_REQUEST_HOSTS,
    LOAD_MORE_PATTERN,
    LillioBrowser,
    authoritative_activity_date,
    fully_archived_page,
    parse_activity_date,
)
from lillio_archive.config import Config


def test_load_more_pattern_matches_known_feed_control() -> None:
    assert LOAD_MORE_PATTERN.search("Load More")
    assert LOAD_MORE_PATTERN.search("Show older activities")


def test_load_more_pattern_rejects_unrelated_controls() -> None:
    assert not LOAD_MORE_PATTERN.search("Message Center")


def test_parse_activity_date_with_explicit_year() -> None:
    assert parse_activity_date("Dec 5th, 2024") == "2024-12-05"


def test_parse_activity_date_infers_recent_year() -> None:
    assert parse_activity_date("Jun 9th", date(2026, 6, 10)) == "2026-06-09"
    assert parse_activity_date("Jun 23rd", date(2026, 6, 10)) == "2025-06-23"
    assert parse_activity_date("Dec 20th", date(2026, 1, 10)) == "2025-12-20"


def test_known_third_party_diagnostic_hosts_are_ignorable() -> None:
    assert "analytics.google.com" in IGNORABLE_REQUEST_HOSTS
    assert "api-iam.intercom.io" in IGNORABLE_REQUEST_HOSTS


def test_authoritative_activity_date_prefers_list_date() -> None:
    assert (
        authoritative_activity_date(
            {
                "id": 123,
                "list_date": "2025-12-23T07:20:00.000-05:00",
                "created_at": "2025-12-24T12:20:34.121-05:00",
            }
        )
        == "2025-12-23"
    )


def test_authoritative_activity_date_falls_back_to_created_at() -> None:
    assert (
        authoritative_activity_date(
            {
                "id": 123,
                "list_date": None,
                "created_at": "2025-06-23T09:48:04.267-04:00",
            }
        )
        == "2025-06-23"
    )


def test_archived_page_requires_every_media_item() -> None:
    archived = {"1:image", "2:video", "3:image"}
    assert fully_archived_page({"1:image", "2:video"}, archived)
    assert not fully_archived_page({"1:image", "4:image"}, archived)
    assert not fully_archived_page(set(), archived)


def test_record_journal_payload_maps_activity_ids_to_explicit_dates() -> None:
    browser = LillioBrowser(Config())
    count = browser._record_journal_payload(
        {
            "intervals": {
                "December, 2025": [
                    {
                        "activity": {
                            "id": 100101,
                            "list_date": "2025-12-23T07:20:00.000-05:00",
                            "created_at": "2025-12-23T12:20:34.121-05:00",
                        }
                    }
                ],
                "June, 2025": [
                    {
                        "activity": {
                            "id": 100102,
                            "list_date": None,
                            "created_at": "2025-06-23T09:48:04.267-04:00",
                        }
                    }
                ],
            }
        }
    )
    assert count == 2
    assert authoritative_activity_date(browser._activities["100101"]) == "2025-12-23"
    assert authoritative_activity_date(browser._activities["100102"]) == "2025-06-23"
