"""Free public job feeds used to widen discovery beyond job-board scraping.

These feeds are intentionally read-only: the system stores public listing URLs and
never attempts to bypass authentication, CAPTCHAs, paywalls, or access controls.
"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

UA = "JobSearchAutomation/1.0 (+local personal job-search tool)"
TIMEOUT = 20


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(url, params=params or {}, headers={"User-Agent": UA}, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


# XML 1.0 forbids most control characters; some feeds (e.g. WeWorkRemotely's RSS)
# include raw control bytes or bare "&" characters that aren't valid entity
# references, which makes Python's strict ElementTree parser raise
# "not well-formed (invalid token)". Sanitize before parsing instead of letting
# one bad feed byte kill the whole source.
_ILLEGAL_XML_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_BARE_AMPERSAND_RE = re.compile(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[a-zA-Z][a-zA-Z0-9]*;)")


def _get_xml(url: str) -> ET.Element:
    response = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    text = response.content.decode(response.encoding or "utf-8", errors="replace").strip()
    if "text/html" in content_type or text.lower().startswith("<!doctype html") or text.lower().startswith("<html"):
        raise ValueError(f"Endpoint {url} returned HTML instead of XML/RSS")
    text = _ILLEGAL_XML_CHARS_RE.sub("", text)
    text = _BARE_AMPERSAND_RE.sub("&amp;", text)
    return ET.fromstring(text)


def _job(title: str, company: str, location: str, description: str, url: str,
         source: str, *, date_posted: str | None = None, is_remote: bool = True,
         application_url: str | None = None, company_url: str | None = None,
         extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "site": source,
        "title": title or "Software Engineer",
        "company": company or "Unknown company",
        "location": location or "Remote",
        "is_remote": bool(is_remote),
        "job_url": url,
        "job_url_direct": application_url or url,
        "application_url": application_url or url,
        "company_url": company_url,
        "description": description,
        "date_posted": date_posted,
        "emails": None,
        **(extra or {}),
    }


def fetch_remoteok(limit: int = 200) -> list[dict[str, Any]]:
    data = _get_json("https://remoteok.com/api")
    rows: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not item.get("position"):
            continue
        tags = " ".join(map(str, item.get("tags") or []))
        text = f"{item.get('position','')} {item.get('description','')} {tags}".lower()
        if not any(x in text for x in ("java", "spring", "backend", "full stack", "fullstack", "software engineer")):
            continue
        rows.append(_job(
            item.get("position"), item.get("company"), item.get("location") or "Worldwide remote",
            item.get("description"), item.get("url") or "https://remoteok.com",
            "remoteok", date_posted=item.get("date"), is_remote=True,
            application_url=item.get("apply_url") or item.get("url"),
            company_url=item.get("company_url"),
        ))
        if len(rows) >= limit:
            break
    return rows


def fetch_jobicy(limit: int = 200) -> list[dict[str, Any]]:
    data = _get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": min(limit, 200)})
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        text = f"{item.get('jobTitle','')} {item.get('jobDescription','')} {item.get('jobIndustry','')}".lower()
        if not any(x in text for x in ("java", "spring", "backend", "full stack", "fullstack", "software engineer")):
            continue
        rows.append(_job(
            item.get("jobTitle"), item.get("companyName"), item.get("jobGeo") or "Remote",
            item.get("jobDescription") or item.get("jobExcerpt"), item.get("url") or item.get("jobUrl"),
            "jobicy", date_posted=item.get("pubDate") or item.get("date"), is_remote=True,
            application_url=item.get("url") or item.get("jobUrl"),
            company_url=item.get("companyUrl"),
        ))
    return rows[:limit]


def _arbeitnow_rows(url: str, source: str, limit: int) -> list[dict[str, Any]]:
    data = _get_json(url)
    jobs = data.get("data", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        text = f"{item.get('title','')} {item.get('description','')}".lower()
        if not any(x in text for x in ("java", "spring", "backend", "full stack", "fullstack", "software engineer")):
            continue
        rows.append(_job(
            item.get("title"), item.get("company_name"), item.get("location") or "Europe",
            item.get("description"), item.get("url") or "",
            source, date_posted=str(item.get("created_at") or ""), is_remote=bool(item.get("remote")),
            application_url=item.get("url"), company_url=item.get("company_url"),
        ))
        if len(rows) >= limit:
            break
    return rows


def fetch_arbeitnow(limit: int = 150) -> list[dict[str, Any]]:
    return _arbeitnow_rows("https://www.arbeitnow.com/api/job-board-api", "arbeitnow", limit)


def fetch_arbeitnow_uk(limit: int = 100) -> list[dict[str, Any]]:
    return _arbeitnow_rows("https://www.arbeitnow.co.uk/api/job-board-api", "arbeitnow_uk", limit)


def fetch_himalayas(limit: int = 100, terms: tuple[str, ...] = ("java", "spring boot", "backend engineer", "full stack")) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in terms:
        data = _get_json("https://himalayas.app/jobs/api/search", params={"q": term, "sort": "recent", "page": 1})
        for item in (data.get("jobs", []) if isinstance(data, dict) else []):
            if not isinstance(item, dict):
                continue
            url = item.get("applicationLink") or item.get("url") or item.get("guid") or ""
            key = str(item.get("guid") or url)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(_job(
                item.get("title"), item.get("companyName"), item.get("location") or item.get("timezone") or "Remote",
                item.get("description") or item.get("excerpt"), url,
                "himalayas", date_posted=item.get("createdAt") or item.get("postedAt"), is_remote=True,
                application_url=item.get("applicationLink") or url,
                company_url=item.get("companyWebsite"),
            ))
            if len(rows) >= limit:
                return rows
    return rows


def fetch_rss(url: str, source: str, limit: int = 100) -> list[dict[str, Any]]:
    root = _get_xml(url)
    rows: list[dict[str, Any]] = []
    for item in root.findall(".//item")[:limit]:
        title = _text(item.findtext("title"))
        link = _text(item.findtext("link"))
        description = _text(item.findtext("description") or item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded"))
        guid = _text(item.findtext("guid"))
        pub_date = _text(item.findtext("pubDate"))
        if not title or not link:
            continue
        text = f"{title} {description}".lower()
        if not any(x in text for x in ("java", "spring", "backend", "full stack", "fullstack", "software engineer")):
            continue
        rows.append(_job(
            title, "", "Remote", description, link, source,
            date_posted=pub_date, is_remote=True, application_url=link,
            extra={"guid": guid},
        ))
    return rows


def fetch_wwr(limit: int = 150) -> list[dict[str, Any]]:
    return fetch_rss("https://weworkremotely.com/categories/remote-programming-jobs.rss", "weworkremotely", limit)


def fetch_himalayas_rss(limit: int = 100) -> list[dict[str, Any]]:
    return fetch_rss("https://himalayas.app/jobs/rss", "himalayas_rss", limit)


PUBLIC_SOURCE_RUNNERS = {
    "remoteok": fetch_remoteok,
    "jobicy": fetch_jobicy,
    "arbeitnow": fetch_arbeitnow,
    "arbeitnow_uk": fetch_arbeitnow_uk,
    "himalayas": fetch_himalayas,
    "weworkremotely": fetch_wwr,
    "himalayas_rss": fetch_himalayas_rss,
}


def run_public_source(source: str, limit: int) -> tuple[list[dict[str, Any]], str | None, float]:
    started = datetime.now(timezone.utc)
    try:
        rows = PUBLIC_SOURCE_RUNNERS[source](limit=limit)
        return rows, None, (datetime.now(timezone.utc) - started).total_seconds()
    except Exception as exc:
        logger.warning("Public source %s failed: %s", source, exc)
        return [], str(exc)[:1000], (datetime.now(timezone.utc) - started).total_seconds()
