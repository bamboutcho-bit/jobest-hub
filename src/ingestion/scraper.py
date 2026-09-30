"""Fast, observable multi-source ingestion.

JobSpy covers major job boards; free public feeds widen discovery beyond any
single platform. Every source runs concurrently and emits progress telemetry.
"""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from config.candidate_profile import SEARCH_MATRIX, DEFAULT_DISCOVERY_SITES
from config.settings import settings
from src.ingestion.public_sources import run_public_source
from src.ingestion.web_discovery import expand_from_seed_jobs

logger = logging.getLogger(__name__)

try:
    from jobspy import scrape_jobs
    import jobspy.model
    if hasattr(jobspy.model, "Country") and hasattr(jobspy.model.Country, "from_string"):
        _orig_country_from_string = jobspy.model.Country.from_string

        @classmethod
        def _safe_country_from_string(cls, country_str: str):
            try:
                return _orig_country_from_string(country_str)
            except Exception:
                return getattr(cls, "WORLDWIDE", getattr(cls, "USA", None))

        jobspy.model.Country.from_string = _safe_country_from_string
except ImportError:  # pragma: no cover
    scrape_jobs = None

_LAST_STATS: dict[str, Any] = {
    "queries": 0,
    "queries_completed": 0,
    "scrape_errors": 0,
    "empty_queries": 0,
    "raw_rows": 0,
    "source_results": [],
}
ProgressCallback = Callable[[dict[str, Any]], None]


# Countries jobspy's Glassdoor scraper actually has a domain mapping for
# (python-jobspy==1.1.82, jobspy/model.py Country enum). Any query for a country
# outside this set raises "Glassdoor is not available for <COUNTRY>" every time,
# so we drop glassdoor from those queries instead of letting them fail.
_GLASSDOOR_SUPPORTED_COUNTRIES = {
    "argentina", "australia", "austria", "belgium", "brazil", "canada", "france",
    "germany", "hong kong", "india", "ireland", "italy", "luxembourg", "malaysia",
    "malta", "mexico", "netherlands", "new zealand", "singapore", "spain",
    "switzerland", "uk", "united kingdom", "usa", "us", "united states", "vietnam",
}


def _configured_sites(query: dict[str, Any]) -> list[str]:
    configured = query.get("sites")
    if configured:
        sites = [str(x).strip().lower() for x in configured if str(x).strip()]
    else:
        sites = [x.strip().lower() for x in settings.scrape_sites.split(",") if x.strip()] or list(DEFAULT_DISCOVERY_SITES)

    if "glassdoor" in sites:
        country = str(query.get("country") or query.get("location") or "").strip().lower()
        if country not in _GLASSDOOR_SUPPORTED_COUNTRIES:
            sites = [s for s in sites if s != "glassdoor"]

    return sites


def _jobspy_country(query: dict[str, Any], sites: list[str]) -> str | None:
    if not any(site in {"indeed", "glassdoor"} for site in sites):
        return None
    country = str(query.get("country") or "").strip()
    return country or "USA"


def _safe_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
    except TypeError:
        pass
    return value


def _scrape_one_query(query: dict[str, Any]) -> tuple[pd.DataFrame, bool, str, float]:
    if scrape_jobs is None:
        raise RuntimeError("python-jobspy is not installed. Run: pip install -U python-jobspy")

    term = str(query.get("term") or "software engineer")
    location = str(query.get("location") or "")
    is_remote = bool(query.get("is_remote"))
    sites = _configured_sites(query)
    country_indeed = _jobspy_country(query, sites)
    started = time.perf_counter()
    try:
        kwargs: dict[str, Any] = {
            "site_name": sites,
            "search_term": term,
            "location": location,
            "is_remote": is_remote,
            "results_wanted": max(1, settings.results_wanted_per_query),
            "hours_old": max(1, settings.hours_old),
            "description_format": "markdown",
            "verbose": 0,
            "linkedin_fetch_description": bool(settings.linkedin_fetch_description),
        }
        google_term = query.get("google")
        if google_term:
            kwargs["google_search_term"] = str(google_term)
        if country_indeed:
            kwargs["country_indeed"] = country_indeed
        if query.get("distance") is not None:
            kwargs["distance"] = int(query["distance"])
        if query.get("job_type"):
            kwargs["job_type"] = str(query["job_type"])
        if settings.jobspy_proxies:
            proxies_list = [p.strip() for p in settings.jobspy_proxies.split(",") if p.strip()]
            if proxies_list:
                kwargs["proxies"] = proxies_list
        if settings.jobspy_delay_seconds > 0:
            time.sleep(settings.jobspy_delay_seconds)

        df = scrape_jobs(**kwargs)
        duration = round(time.perf_counter() - started, 2)
        return (df if df is not None else pd.DataFrame()), False, "", duration
    except Exception as exc:
        # If google failed (often 429 rate limit when unproxied) and other sites were requested,
        # retry with remaining sites so linkedin / indeed results aren't dropped.
        if "google" in sites and len(sites) > 1:
            fallback_sites = [s for s in sites if s != "google"]
            logger.info("JobSpy retrying id=%s without 'google' due to: %s", query.get("id"), exc)
            try:
                kwargs_retry = dict(kwargs)
                kwargs_retry["site_name"] = fallback_sites
                if "google_search_term" in kwargs_retry:
                    del kwargs_retry["google_search_term"]
                df = scrape_jobs(**kwargs_retry)
                duration = round(time.perf_counter() - started, 2)
                return (df if df is not None else pd.DataFrame()), False, "", duration
            except Exception as retry_exc:
                exc = retry_exc

        duration = round(time.perf_counter() - started, 2)
        logger.warning(
            "JobSpy failed id=%s term=%r location=%r sites=%s: %s",
            query.get("id"), term, location, sites, exc,
        )
        return pd.DataFrame(), True, str(exc)[:1000], duration


def _public_source_names() -> list[str]:
    return [x.strip().lower() for x in settings.public_feed_sources.split(",") if x.strip()]


def run_ingestion(progress_callback: ProgressCallback | None = None, queries: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    global _LAST_STATS

    search_queries = list(queries) if queries is not None else list(SEARCH_MATRIX)
    public_sources = _public_source_names()
    total_tasks = len(search_queries) + len(public_sources)
    stats: dict[str, Any] = {
        "queries": total_tasks,
        "queries_completed": 0,
        "scrape_errors": 0,
        "empty_queries": 0,
        "raw_rows": 0,
        "source_results": [],
    }
    all_rows: list[dict[str, Any]] = []
    workers = max(1, min(int(settings.scrape_workers), total_tasks or 1))

    def emit(event: dict[str, Any]) -> None:
        if progress_callback:
            try:
                progress_callback(event)
            except Exception:
                logger.exception("Scrape progress callback failed")

    emit({
        "stage": "scraping",
        "status": "started",
        "query_completed": 0,
        "query_total": total_tasks,
        "raw_scraped": 0,
        "scrape_errors": 0,
        "progress_percent": 0,
        "message": f"Starting {total_tasks} parallel discovery tasks…",
    })

    tasks = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="discovery") as pool:
        for query in search_queries:
            tasks[pool.submit(_scrape_one_query, query)] = ("jobspy", query)
        for source in public_sources:
            tasks[pool.submit(run_public_source, source, max(1, settings.public_feed_limit))] = ("public", {"id": f"public-{source}", "term": source, "location": "Global/Remote", "sites": [source], "is_remote": True})

        for future in as_completed(tasks):
            task_type, task = tasks[future]
            if task_type == "jobspy":
                query = task
                label = f"{query.get('term', '')} @ {query.get('location') or 'Global remote'}"
                sites = _configured_sites(query)
                try:
                    df, had_error, error, duration = future.result()
                except Exception as exc:
                    df, had_error, error, duration = pd.DataFrame(), True, str(exc)[:1000], 0.0
                row_count = int(len(df.index)) if not df.empty else 0
                if had_error:
                    stats["scrape_errors"] += 1
                if row_count == 0:
                    stats["empty_queries"] += 1
                for _, row in df.iterrows() if not df.empty else []:
                    record = {k: _safe_scalar(v) for k, v in row.to_dict().items()}
                    record["_query_id"] = query.get("id")
                    record["_query_term"] = query.get("term")
                    record["_query_location"] = query.get("location", "")
                    record["_query_country"] = query.get("country")
                    record["_query_sites"] = sites
                    record["_forced_remote_flag"] = bool(query.get("is_remote"))
                    record["_job_url_direct"] = record.get("job_url_direct")
                    record["_application_emails"] = record.get("emails")
                    all_rows.append(record)
            else:
                source = task["term"]
                try:
                    rows, error, duration = future.result()
                except Exception as exc:
                    rows, error, duration = [], str(exc)[:1000], 0.0
                row_count = len(rows)
                had_error = bool(error)
                if had_error:
                    stats["scrape_errors"] += 1
                if row_count == 0:
                    stats["empty_queries"] += 1
                for row in rows:
                    record = dict(row)
                    record["_query_id"] = task["id"]
                    record["_query_term"] = source
                    record["_query_location"] = record.get("location", "Remote")
                    record["_query_country"] = None
                    record["_query_sites"] = [source]
                    record["_forced_remote_flag"] = bool(record.get("is_remote"))
                    record["_job_url_direct"] = record.get("job_url_direct") or record.get("application_url")
                    record["_application_emails"] = record.get("emails")
                    all_rows.append(record)

            stats["raw_rows"] = len(all_rows)
            stats["queries_completed"] += 1
            result = {
                "id": task.get("id"),
                "term": task.get("term"),
                "location": task.get("location", ""),
                "country": task.get("country"),
                "sites": task.get("sites", []),
                "rows": row_count,
                "error": error if had_error else None,
                "duration_seconds": round(duration, 2),
                "status": "error" if had_error else ("empty" if row_count == 0 else "ok"),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            stats["source_results"].append(result)
            progress = int(stats["queries_completed"] * 100 / max(1, total_tasks))
            logger.info(
                "Discovery task complete %d/%d: %s rows=%d duration=%ss status=%s",
                stats["queries_completed"], total_tasks, task.get("id"), row_count, round(duration, 2), result["status"],
            )
            emit({
                "stage": "scraping",
                "status": result["status"],
                "query_completed": stats["queries_completed"],
                "query_total": total_tasks,
                "query_label": task.get("term") or task.get("id"),
                "query_id": task.get("id"),
                "query_sites": result["sites"],
                "query_rows": row_count,
                "query_duration_seconds": round(duration, 2),
                "query_error": error if had_error else None,
                "raw_scraped": len(all_rows),
                "scrape_errors": stats["scrape_errors"],
                "progress_percent": progress,
                "message": f"Finished {task.get('id')}: {row_count} jobs",
                "source_results": list(stats["source_results"]),
            })

    # Expand from public employer/ATS pages discovered in the first wave. This is
    # deliberately bounded and robots-aware; it never bypasses login, CAPTCHA,
    # paywalls, or other access controls.
    if settings.open_web_discovery_enabled and all_rows:
        emit({
            "stage": "web_expansion",
            "status": "started",
            "raw_scraped": len(all_rows),
            "progress_percent": 100,
            "message": "Expanding discovery into public employer career sites and job pages…",
        })
        try:
            web_rows, web_results = expand_from_seed_jobs(
                all_rows,
                max_domains=max(1, settings.open_web_max_domains),
                max_pages_per_domain=max(2, settings.open_web_max_pages_per_domain),
                sitemap_limit=max(1, settings.open_web_sitemap_limit),
                max_total_pages=max(10, settings.open_web_max_total_pages),
                workers=max(1, settings.open_web_workers),
                delay_seconds=max(0.0, settings.open_web_delay_seconds),
                progress_callback=emit,
            )
            for row in web_rows:
                row["_query_id"] = "public-web-expansion"
                row["_query_term"] = "employer career site / public web"
                row["_query_location"] = row.get("location", "Unknown")
                row["_query_country"] = None
                row["_query_sites"] = ["public_web"]
                row["_forced_remote_flag"] = bool(row.get("is_remote"))
                row["_job_url_direct"] = row.get("job_url_direct") or row.get("application_url")
                row["_application_emails"] = row.get("emails")
            all_rows.extend(web_rows)
            stats["web_domains"] = len(web_results)
            stats["web_pages"] = sum(int(x.get("pages", 0) or 0) for x in web_results)
            stats["web_rows"] = len(web_rows)
            stats["raw_rows"] = len(all_rows)
            emit({
                "stage": "web_expansion",
                "status": "finished",
                "web_domains": len(web_results),
                "web_pages": stats["web_pages"],
                "web_rows": len(web_rows),
                "raw_scraped": len(all_rows),
                "scrape_errors": stats["scrape_errors"],
                "progress_percent": 100,
                "message": f"Public-web expansion found {len(web_rows)} additional postings across {len(web_results)} domains.",
            })
        except Exception as exc:
            stats["scrape_errors"] += 1
            logger.exception("Public web expansion failed")
            emit({
                "stage": "web_expansion",
                "status": "error",
                "raw_scraped": len(all_rows),
                "scrape_errors": stats["scrape_errors"],
                "progress_percent": 100,
                "message": f"Public-web expansion failed: {str(exc)[:500]}",
            })

    # Prefer richer source data when the same posting appears more than once.
    _LAST_STATS = stats
    emit({
        "stage": "scraping",
        "status": "finished",
        "query_completed": stats["queries_completed"],
        "query_total": total_tasks,
        "raw_scraped": len(all_rows),
        "scrape_errors": stats["scrape_errors"],
        "progress_percent": 100,
        "message": f"Discovery finished: {len(all_rows)} raw postings collected.",
        "source_results": stats["source_results"],
    })
    return all_rows


def get_last_ingestion_stats() -> dict[str, Any]:
    return dict(_LAST_STATS)
