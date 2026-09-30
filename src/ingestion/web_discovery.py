"""Polite public-web expansion for job discovery.

This module discovers additional public job postings from employer career sites and
public ATS-hosted pages starting from domains already observed in other sources.
It never bypasses authentication, CAPTCHAs, robots.txt, paywalls, or access controls.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.robotparser
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse, urldefrag

import requests

logger = logging.getLogger(__name__)

UA = "JobSearchAutomation/2.0 (+public career-site discovery; personal tool)"
TIMEOUT = 15
JOB_PATH_HINTS = (
    "/career", "/careers", "/jobs", "/job", "/vacancies", "/positions",
    "/opportunities", "/recruit", "/recruitment", "/join-us", "/joinus",
    "/work-with-us", "/work-with-us", "/emploi", "/offres", "/stages",
)
JOB_WORDS = (
    "software engineer", "backend", "back-end", "java", "spring", "full stack",
    "fullstack", "developer", "software development", "intern", "internship",
    "stage", "stagiaire", "graduate", "junior", "apprentice", "alternance",
)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _same_domain(a: str, b: str) -> bool:
    ha, hb = _host(a), _host(b)
    return bool(ha and hb and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)))


def _looks_like_board(url: str) -> bool:
    host = _host(url)
    return any(x in host for x in (
        "linkedin.", "indeed.", "glassdoor.", "ziprecruiter.", "bayt.",
        "remoteok.", "jobicy.", "arbeitnow.", "himalayas.", "weworkremotely.",
    ))


def _url_score(url: str, text: str = "") -> int:
    low = (url + " " + text).lower()
    score = 0
    if any(h in low for h in JOB_PATH_HINTS):
        score += 6
    if any(w in low for w in JOB_WORDS):
        score += 3
    if low.count("/") <= 4:
        score += 1
    return score


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.text_parts: list[str] = []
        self._href = ""
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            data = dict(attrs)
            self._href = data.get("href", "") or ""
            self._anchor_text = []

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor_text)))
            self._href = ""
            self._anchor_text = []

    def handle_data(self, data):
        if self._href:
            self._anchor_text.append(data)
        self.text_parts.append(data)


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "title":
            self._in_title = True
        if t == "meta":
            data = dict(attrs)
            key = (data.get("property") or data.get("name") or "").strip().lower()
            value = (data.get("content") or "").strip()
            if key and value:
                self.meta[key] = value

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _json_ld_objects(html: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S):
        raw = unescape(raw).strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except Exception:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                values.extend([x for x in item["@graph"] if isinstance(x, dict)])
            elif isinstance(item, dict):
                objects.append(item)
    return objects


def _extract_email_addresses(text: str) -> list[str]:
    return sorted(set(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, flags=re.I)))


def _canonical_job(item: dict[str, Any], page_url: str, source: str) -> dict[str, Any] | None:
    types = item.get("@type")
    types = types if isinstance(types, list) else [types]
    if not any(str(t).lower() == "jobposting" for t in types if t):
        return None
    title = _clean(item.get("title"))
    description = _clean(item.get("description"))
    if not title or not description:
        return None
    text = f"{title} {description}".lower()
    if not any(x in text for x in JOB_WORDS):
        return None
    org = item.get("hiringOrganization") or {}
    company = _clean(org.get("name") if isinstance(org, dict) else org)
    company_url = _clean(org.get("sameAs") or org.get("url")) if isinstance(org, dict) else ""
    loc = item.get("jobLocation") or item.get("applicantLocationRequirements") or ""
    location = "Remote / Worldwide"
    is_remote = False
    if isinstance(loc, list):
        loc = loc[0] if loc else ""
    if isinstance(loc, dict):
        addr = loc.get("address") or loc
        bits = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
        location = ", ".join(_clean(x) for x in bits if _clean(x)) or location
        if str(loc.get("@type", "")).lower() == "joblocationtype" or "remote" in location.lower():
            is_remote = True
    elif isinstance(loc, str) and loc:
        location = _clean(loc)
        is_remote = "remote" in location.lower()
    remote_flag = str(item.get("workHours") or item.get("employmentType") or "")
    if "remote" in (description + " " + remote_flag).lower():
        is_remote = True
    applicant_url = _clean(item.get("url") or item.get("directApply") or page_url)
    direct_apply = item.get("directApply")
    if isinstance(direct_apply, bool):
        direct_apply = applicant_url if direct_apply else ""
    return {
        "site": source,
        "title": title,
        "company": company or _host(page_url),
        "location": location,
        "is_remote": is_remote,
        "job_url": applicant_url,
        "job_url_direct": applicant_url,
        "application_url": _clean(direct_apply) or applicant_url,
        "company_url": company_url or f"{urlparse(page_url).scheme}://{_host(page_url)}",
        "description": description,
        "date_posted": _clean(item.get("datePosted")),
        "emails": _extract_email_addresses(description),
        "_web_discovered": True,
    }


def _robots_allowed(root_url: str, target_url: str, rp_cache: dict[str, urllib.robotparser.RobotFileParser]) -> bool:
    parsed = urlparse(root_url)
    base = f"{parsed.scheme}://{_host(root_url)}"
    rp = rp_cache.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(urljoin(base + "/", "/robots.txt"))
        try:
            rp.read()
        except Exception:
            # If robots cannot be read, fail safe for this domain.
            rp_cache[base] = rp
            return False
        rp_cache[base] = rp
    try:
        return rp.can_fetch(UA, target_url)
    except Exception:
        return False


def _fetch(session: requests.Session, url: str) -> tuple[int, str, str]:
    response = session.get(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}, timeout=TIMEOUT, allow_redirects=True)
    content_type = response.headers.get("Content-Type", "")
    if response.status_code >= 400 or "text/html" not in content_type.lower():
        return response.status_code, response.url, ""
    return response.status_code, response.url, response.text[:2_000_000]


def _sitemap_urls(session: requests.Session, root: str, robots: dict[str, urllib.robotparser.RobotFileParser], limit: int) -> list[str]:
    candidates = [urljoin(root, "/sitemap.xml"), urljoin(root, "/sitemap_index.xml")]
    found: list[str] = []
    seen: set[str] = set()
    queue = deque(candidates)
    while queue and len(found) < limit:
        sm = queue.popleft()
        if sm in seen or not _robots_allowed(root, sm, robots):
            continue
        seen.add(sm)
        try:
            r = session.get(sm, headers={"User-Agent": UA}, timeout=TIMEOUT)
            if r.status_code >= 400:
                continue
            text = r.text
        except Exception:
            continue
        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, flags=re.I | re.S)
        for loc in locs:
            loc = unescape(loc.strip())
            if not _same_domain(root, loc):
                continue
            if loc.lower().endswith((".xml", "/sitemap.xml")) and "sitemap" in loc.lower():
                queue.append(loc)
                continue
            if _url_score(loc) >= 6:
                found.append(urldefrag(loc)[0])
                if len(found) >= limit:
                    break
    return list(dict.fromkeys(found))[:limit]


def crawl_domain(root_url: str, *, max_pages: int, sitemap_limit: int, delay_seconds: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = root_url if urlparse(root_url).scheme else "https://" + root_url
    root = f"{urlparse(root).scheme}://{_host(root)}/"
    if _looks_like_board(root):
        return [], {"root": root, "pages": 0, "jobs": 0, "blocked": "known_board"}
    session = requests.Session()
    robots: dict[str, urllib.robotparser.RobotFileParser] = {}
    if not _robots_allowed(root, root, robots):
        return [], {"root": root, "pages": 0, "jobs": 0, "blocked": "robots"}

    queue: deque[str] = deque([root])
    for url in _sitemap_urls(session, root, robots, limit=sitemap_limit):
        queue.append(url)
    seen: set[str] = set()
    jobs: list[dict[str, Any]] = []
    pages = 0

    while queue and pages < max_pages:
        url = urldefrag(queue.popleft())[0]
        if not url or url in seen or not _same_domain(root, url):
            continue
        if not _robots_allowed(root, url, robots):
            continue
        seen.add(url)
        try:
            status, final_url, html = _fetch(session, url)
        except Exception:
            continue
        pages += 1
        if status in {401, 403, 429}:
            return jobs, {"root": root, "pages": pages, "jobs": len(jobs), "blocked": str(status)}
        if not html:
            continue
        page_emails = _extract_email_addresses(html)
        for obj in _json_ld_objects(html):
            job = _canonical_job(obj, final_url, "public_web")
            if job:
                if page_emails:
                    job["emails"] = sorted(set(job.get("emails") or []) | set(page_emails))
                jobs.append(job)
        parser = _LinkParser()
        try:
            parser.feed(html)
        except Exception:
            parser.links = []
        for href, anchor in parser.links:
            target = urljoin(final_url, href)
            target = urldefrag(target)[0]
            if not _same_domain(root, target) or target.startswith(("mailto:", "javascript:")):
                continue
            if _url_score(target, anchor) >= 5:
                queue.append(target)
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    unique: dict[str, dict[str, Any]] = {}
    for job in jobs:
        unique[job["job_url"]] = job
    return list(unique.values()), {"root": root, "pages": pages, "jobs": len(unique), "blocked": None}


def expand_from_seed_jobs(rows: list[dict[str, Any]], *, max_domains: int, max_pages_per_domain: int,
                          sitemap_limit: int, max_total_pages: int, workers: int,
                          delay_seconds: float, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    domains: list[str] = []
    seen: set[str] = set()
    for row in rows:
        candidate = row.get("company_url") or row.get("companyWebsite") or row.get("job_url") or row.get("job_url_direct")
        if not candidate:
            continue
        try:
            host = _host(str(candidate))
            scheme = urlparse(str(candidate)).scheme or "https"
            root = f"{scheme}://{host}/"
        except Exception:
            continue
        if not host or _looks_like_board(root) or host in seen:
            continue
        seen.add(host)
        domains.append(root)
        if len(domains) >= max_domains:
            break
    if not domains:
        return [], []
    jobs: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    workers = max(1, min(workers, len(domains)))
    per_domain_pages = max(2, min(max_pages_per_domain, max_total_pages))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="webcrawl") as pool:
        futures = {
            pool.submit(crawl_domain, root, max_pages=per_domain_pages, sitemap_limit=sitemap_limit, delay_seconds=delay_seconds): root
            for root in domains
        }
        for future in as_completed(futures):
            root = futures[future]
            try:
                domain_jobs, info = future.result()
            except Exception as exc:
                domain_jobs, info = [], {"root": root, "pages": 0, "jobs": 0, "error": str(exc)[:500]}
            jobs.extend(domain_jobs)
            results.append(info)
            if progress_callback:
                progress_callback({
                    "stage": "web_expansion",
                    "status": "ok" if not info.get("error") and not info.get("blocked") else "limited",
                    "domain": root,
                    "domains_total": len(domains),
                    "domains_completed": len(results),
                    "pages": info.get("pages", 0),
                    "jobs": info.get("jobs", 0),
                    "message": f"Expanded {root}: {info.get('jobs', 0)} job postings",
                })
    return jobs, results
