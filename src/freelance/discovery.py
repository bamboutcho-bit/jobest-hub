"""Multi-source freelance lead discovery engine.

Scrapes public freelance/gig sources for project leads from individuals,
founders, startups, and small businesses looking for developers. Sources
include Hacker News threads, Reddit hiring subreddits, and remote contract
feeds. Intentionally read-only: never bypasses authentication or CAPTCHAs.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

UA = "FreelanceLeadDiscovery/1.0 (+local personal job-search tool)"
TIMEOUT = 20
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Keywords that signal a real client looking for a developer
HIRING_SIGNALS = [
    "looking for", "seeking", "need a developer", "need a freelancer",
    "hiring", "[hiring]", "want to build", "build an app", "build a website",
    "build a platform", "mvp", "saas", "web app", "mobile app",
    "freelance developer", "contract developer", "need help building",
    "startup looking", "cofounder", "technical cofounder",
    "java", "spring", "react", "fullstack", "full stack", "backend",
    "frontend", "api", "rest api", "python", "node",
]

# Skill match keywords (our candidate's core stack)
SKILL_KEYWORDS = [
    "java", "spring boot", "spring", "react", "reactjs", "next.js", "nextjs",
    "python", "fastapi", "flask", "postgresql", "docker", "kubernetes",
    "microservices", "rest api", "graphql", "fullstack", "full stack",
    "backend", "frontend", "web app", "web application", "mvp",
]


def _clean_html(text: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = html.unescape(str(text or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _dedup_hash(source: str, unique_id: str) -> str:
    """Generate a deduplication hash from source + unique identifier."""
    return hashlib.md5(f"{source}:{unique_id}".encode()).hexdigest()


def _extract_email(text: str) -> str | None:
    """Extract the first plausible email from text."""
    emails = EMAIL_RE.findall(text or "")
    for email in emails:
        local = email.split("@")[0].lower()
        if local not in ("noreply", "no-reply", "donotreply", "notifications"):
            return email.lower()
    return None


def _has_hiring_signal(text: str) -> bool:
    """Check if text contains signals that someone is hiring/seeking a developer."""
    lower = text.lower()
    return any(signal in lower for signal in HIRING_SIGNALS)


def _skill_match_count(text: str) -> int:
    """Count how many of the candidate's skill keywords appear in the text."""
    lower = text.lower()
    skills = set(kw.lower() for kw in SKILL_KEYWORDS)
    try:
        from src.candidate.profile_manager import get_active_profile
        profile = get_active_profile()
        custom_skills = [s.lower() for s in (profile.get("core_stack") or []) + (profile.get("keywords") or []) if s]
        skills.update(custom_skills)
    except Exception:
        pass
    return sum(1 for kw in skills if kw in lower)


def _lead(*, title: str, client_name: str, client_type: str, contact_email: str | None,
          contact_url: str | None, source_platform: str, source_url: str,
          raw_description: str, dedup_id: str) -> dict[str, Any]:
    """Normalize a discovered lead into the standard dict format."""
    return {
        "dedup_hash": _dedup_hash(source_platform, dedup_id),
        "title": (title or "Untitled Project")[:500],
        "client_name": (client_name or "Unknown")[:255],
        "client_type": (client_type or "individual")[:30],
        "contact_email": contact_email,
        "contact_url": contact_url,
        "source_platform": source_platform,
        "source_url": source_url,
        "raw_description": raw_description or "",
    }


# ---------------------------------------------------------------------------
# Source: Hacker News (Algolia API)
# ---------------------------------------------------------------------------

def fetch_hackernews(limit: int = 50) -> list[dict[str, Any]]:
    """Search HN for freelance/hiring threads via the Algolia search API."""
    leads: list[dict[str, Any]] = []
    queries = [
        "freelancer seeking developer",
        "looking for developer MVP",
        "hiring freelance developer",
        "need developer build",
    ]
    seen_ids: set[str] = set()

    for query in queries:
        if len(leads) >= limit:
            break
        try:
            resp = requests.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": query, "tags": "(story,comment)", "hitsPerPage": 30},
                headers={"User-Agent": UA},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("HN Algolia search failed for '%s': %s", query, exc)
            continue

        for hit in data.get("hits", []):
            object_id = hit.get("objectID", "")
            if object_id in seen_ids:
                continue
            seen_ids.add(object_id)

            title = hit.get("title") or hit.get("story_title") or ""
            text = _clean_html(hit.get("comment_text") or hit.get("story_text") or "")
            combined = f"{title} {text}"

            if not _has_hiring_signal(combined):
                continue
            if _skill_match_count(combined) < 1:
                continue

            author = hit.get("author") or "HN User"
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"

            leads.append(_lead(
                title=title[:200] or f"HN Project ({author})",
                client_name=author,
                client_type="individual",
                contact_email=_extract_email(text),
                contact_url=url,
                source_platform="hackernews",
                source_url=url,
                raw_description=combined[:5000],
                dedup_id=object_id,
            ))
            if len(leads) >= limit:
                break
        time.sleep(0.3)

    logger.info("HN discovery: found %d leads", len(leads))
    return leads


# ---------------------------------------------------------------------------
# Source: Reddit (public JSON feeds)
# ---------------------------------------------------------------------------

def fetch_reddit(limit: int = 50) -> list[dict[str, Any]]:
    """Fetch hiring posts from r/forhire and r/freelance_forhire."""
    leads: list[dict[str, Any]] = []
    subreddits = [
        ("forhire", "https://www.reddit.com/r/forhire/new.json?limit=50"),
        ("freelance_forhire", "https://www.reddit.com/r/freelance_forhire/new.json?limit=50"),
    ]
    seen_ids: set[str] = set()

    for sub_name, url in subreddits:
        if len(leads) >= limit:
            break
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 403:
                logger.info("Reddit r/%s returned 403 (blocked/requires OAuth); skipping", sub_name)
            else:
                logger.warning("Reddit r/%s fetch failed: %s", sub_name, exc)
            continue
        except Exception as exc:
            logger.warning("Reddit r/%s fetch failed: %s", sub_name, exc)
            continue

        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            post_id = post.get("id", "")
            if post_id in seen_ids:
                continue
            seen_ids.add(post_id)

            title = post.get("title", "")
            selftext = post.get("selftext", "")
            combined = f"{title} {selftext}"

            # Only include [Hiring] tagged posts (people looking for devs)
            is_hiring = "[hiring]" in title.lower() or "hiring" in (post.get("link_flair_text") or "").lower()
            if not is_hiring and not _has_hiring_signal(combined):
                continue
            # Skip [For Hire] posts (other freelancers advertising themselves)
            if "[for hire]" in title.lower():
                continue
            if _skill_match_count(combined) < 1:
                continue

            author = post.get("author") or "Reddit User"
            permalink = f"https://www.reddit.com{post.get('permalink', '')}"

            leads.append(_lead(
                title=title[:200],
                client_name=author,
                client_type="individual",
                contact_email=_extract_email(selftext),
                contact_url=permalink,
                source_platform=f"reddit/{sub_name}",
                source_url=permalink,
                raw_description=combined[:5000],
                dedup_id=post_id,
            ))
            if len(leads) >= limit:
                break
        time.sleep(0.5)

    logger.info("Reddit discovery: found %d leads", len(leads))
    return leads


# ---------------------------------------------------------------------------
# Source: Remote Contract Feeds (reuse existing public_sources patterns)
# ---------------------------------------------------------------------------

def fetch_remoteok_contracts(limit: int = 50) -> list[dict[str, Any]]:
    """Fetch contract/freelance gigs from RemoteOK."""
    leads: list[dict[str, Any]] = []
    try:
        resp = requests.get("https://remoteok.com/api", headers={"User-Agent": UA}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("RemoteOK fetch failed: %s", exc)
        return leads

    for item in (data if isinstance(data, list) else []):
        if not isinstance(item, dict) or not item.get("position"):
            continue
        tags = " ".join(map(str, item.get("tags") or []))
        text = f"{item.get('position', '')} {item.get('description', '')} {tags}".lower()
        # Look for contract/freelance markers OR relevant tech
        is_contract = any(kw in text for kw in ("contract", "freelance", "b2b", "contractor"))
        is_relevant = _skill_match_count(text) >= 2
        if not (is_contract or is_relevant):
            continue

        slug = item.get("slug") or item.get("id") or item.get("position", "")
        leads.append(_lead(
            title=item.get("position", "")[:200],
            client_name=item.get("company") or "Unknown",
            client_type="company",
            contact_email=_extract_email(item.get("description") or ""),
            contact_url=item.get("apply_url") or item.get("url") or "https://remoteok.com",
            source_platform="remoteok",
            source_url=item.get("url") or "https://remoteok.com",
            raw_description=_clean_html(item.get("description") or "")[:5000],
            dedup_id=f"rok-{slug}",
        ))
        if len(leads) >= limit:
            break

    logger.info("RemoteOK contracts: found %d leads", len(leads))
    return leads


def fetch_jobicy_contracts(limit: int = 50) -> list[dict[str, Any]]:
    """Fetch contract/freelance gigs from Jobicy."""
    leads: list[dict[str, Any]] = []
    try:
        resp = requests.get(
            "https://jobicy.com/api/v2/remote-jobs",
            params={"count": min(limit * 2, 200)},
            headers={"User-Agent": UA},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Jobicy fetch failed: %s", exc)
        return leads

    for item in data.get("jobs", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        text = f"{item.get('jobTitle', '')} {item.get('jobDescription', '')} {item.get('jobIndustry', '')}".lower()
        is_contract = any(kw in text for kw in ("contract", "freelance", "b2b"))
        is_relevant = _skill_match_count(text) >= 2
        if not (is_contract or is_relevant):
            continue

        job_id = item.get("id") or item.get("jobTitle", "")
        leads.append(_lead(
            title=item.get("jobTitle", "")[:200],
            client_name=item.get("companyName") or "Unknown",
            client_type="company",
            contact_email=None,
            contact_url=item.get("url") or item.get("jobUrl") or "",
            source_platform="jobicy",
            source_url=item.get("url") or item.get("jobUrl") or "",
            raw_description=_clean_html(item.get("jobDescription") or "")[:5000],
            dedup_id=f"jobicy-{job_id}",
        ))
        if len(leads) >= limit:
            break

    logger.info("Jobicy contracts: found %d leads", len(leads))
    return leads


# ---------------------------------------------------------------------------
# Main discovery dispatcher
# ---------------------------------------------------------------------------

SOURCE_FETCHERS = {
    "hackernews": fetch_hackernews,
    "reddit": fetch_reddit,
    "remoteok": fetch_remoteok_contracts,
    "jobicy": fetch_jobicy_contracts,
}


def run_freelance_discovery(*, sources: list[str] | None = None,
                             limit: int | None = None) -> list[dict[str, Any]]:
    """Run discovery across all configured freelance lead sources.

    Returns a flat list of normalized lead dicts ready for dedup + DB insertion.
    """
    if not settings.freelance_enabled:
        logger.info("Freelance engine is disabled; skipping discovery.")
        return []

    active_sources = sources or [s.strip() for s in settings.freelance_discovery_sources.split(",") if s.strip()]
    max_leads = limit or settings.freelance_max_leads_per_scan

    all_leads: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []

    for source_name in active_sources:
        fetcher = SOURCE_FETCHERS.get(source_name)
        if not fetcher:
            logger.debug("Unknown freelance source '%s'; skipping.", source_name)
            continue

        start = time.time()
        try:
            leads = fetcher(limit=max(10, max_leads // max(1, len(active_sources))))
            all_leads.extend(leads)
            source_results.append({
                "source": source_name,
                "leads": len(leads),
                "status": "ok",
                "duration_seconds": round(time.time() - start, 1),
            })
        except Exception as exc:
            logger.exception("Freelance discovery failed for %s", source_name)
            source_results.append({
                "source": source_name,
                "leads": 0,
                "status": "error",
                "error": str(exc)[:200],
                "duration_seconds": round(time.time() - start, 1),
            })

    logger.info("Freelance discovery complete: %d leads from %d sources", len(all_leads), len(active_sources))
    return all_leads[:max_leads]
