"""HR & Talent Acquisition Email Scraping and Discovery Engine.

Discovers direct recruiter, talent acquisition, and hiring emails for companies
using zero-cost multi-method scraping and optional API enrichment:
1. Company Website & Career Page Crawler (/careers, /jobs, /contact, /about)
2. Search Engine Dorking (DuckDuckGo HTML queries for talent/recruiter contacts)
3. Domain Mailbox Pattern Generation + Pure-Python DNS MX Verification
4. Optional API Hooks (Hunter.io, Apollo) if configured
5. Email Scorer & Priority Ranker
"""
from __future__ import annotations

import html
import logging
import re
import socket
import struct
import urllib.parse
from dataclasses import dataclass
from typing import Any, List, Optional

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
TIMEOUT = 6

# Known job board/aggregator domains to ignore when extracting company domains
JOB_BOARD_DOMAINS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "arbeitnow.com", "jobicy.com", "remoteok.com", "weworkremotely.com",
    "himalayas.app", "wellfound.com", "monster.com", "simplyhired.com",
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "smartrecruiters.com", "bamboohr.com", "recruitee.com", "jobvite.com",
}

# Domains of email services/analytics/trackers to ignore
JUNK_EMAIL_DOMAINS = {
    "example.com", "domain.com", "sentry.io", "wixpress.com", "cloudflare.com",
    "google.com", "github.com", "facebook.com", "twitter.com", "schema.org",
    "w3.org", "sentry-cdn.com", "gravatar.com", "wordpress.org", "medium.com",
}

# Generic unmonitored, compliance, or non-hiring email prefixes to reject
EXCLUDED_EMAIL_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "notifications",
    "abuse", "postmaster", "mailer-daemon", "privacy", "data.privacy", "dataprivacy",
    "dpo", "gdpr", "legal", "security", "support", "help", "sales", "billing", "invoice",
    "press", "media", "pr", "marketing", "investors", "dev", "admin", "hostmaster",
    "accommodation", "_accommodation", "accommodations", "accessibility", "compliance",
    "copyright", "dmca", "webmaster", "security-alert", "bounce", "bounces", "daemon",
}


PUNCTUATION_STRIP = " \t\r\n-_.:;,()[]{}'\"/\\<>~*!?•–—"
CLEAN_EMAIL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def clean_email_address(raw_email: str | None) -> str:
    """Clean and strip any leading/trailing symbols, bullets, or punctuation from email.
    
    Guarantees the email starts and ends with a valid alphanumeric character.
    E.g. '-kathrin.buchloh@materna.group' -> 'kathrin.buchloh@materna.group'
         '-macandidature.cer.fct@intradef.gouv.fr' -> 'macandidature.cer.fct@intradef.gouv.fr'
    """
    if not raw_email or "@" not in str(raw_email):
        return ""
    raw = str(raw_email).strip().lower()
    local, _, domain = raw.partition("@")
    clean_local = local.strip(PUNCTUATION_STRIP)
    clean_domain = domain.strip(PUNCTUATION_STRIP)

    # Strip any remaining leading/trailing non-alphanumeric chars
    clean_local = re.sub(r"^[^a-zA-Z0-9]+", "", clean_local)
    clean_local = re.sub(r"[^a-zA-Z0-9]+$", "", clean_local)
    clean_domain = re.sub(r"^[^a-zA-Z0-9]+", "", clean_domain)
    clean_domain = re.sub(r"[^a-zA-Z0-9]+$", "", clean_domain)

    if not clean_local or not clean_domain or "." not in clean_domain:
        return ""

    candidate = f"{clean_local}@{clean_domain}"
    if not CLEAN_EMAIL_RE.match(candidate):
        return ""
    return candidate


def is_excluded_email(email_addr: str) -> bool:
    """Check if an email address is generic, compliance-related, or non-hiring."""
    cleaned = clean_email_address(email_addr)
    if not cleaned:
        return True
    local, _, domain = cleaned.lower().partition("@")
    clean_local = local.strip("._-")
    if not clean_local:
        return True
    if clean_local in EXCLUDED_EMAIL_PREFIXES:
        return True
    if any(clean_local.startswith(p) for p in ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon")):
        return True
    if any(k in clean_local for k in ("privacy", "accommodation", "accessibility", "gdpr", "compliance", "copyright", "dmca")):
        return True
    if domain in JUNK_EMAIL_DOMAINS or "duckduckgo" in domain:
        return True
    return False

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


@dataclass
class DiscoveredContact:
    email: str
    name: str = ""
    title: str = ""
    source: str = ""  # "website_crawler", "search_dork", "role_pattern", "hunter_api", "apollo_api"
    confidence: int = 50
    domain: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "name": self.name,
            "title": self.title,
            "source": self.source,
            "confidence": self.confidence,
            "domain": self.domain,
        }


def check_domain_has_mx(domain: str, timeout: float = 3.0) -> bool:
    """Validate that a domain has active MX records using DNS over UDP (pure standard library)."""
    if not domain or "." not in domain:
        return False
    domain = domain.strip().lower()
    
    # 1. Direct DNS Query over UDP port 53 to public DNS (8.8.8.8)
    try:
        header = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
        qname = b"".join(bytes([len(part)]) + part.encode("latin1") for part in domain.split(".")) + b"\x00"
        qtype_qclass = struct.pack(">HH", 15, 1)  # 15 = MX, 1 = IN
        packet = header + qname + qtype_qclass

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(packet, ("8.8.8.8", 53))
        resp, _ = sock.recvfrom(1024)
        sock.close()
        ancount = struct.unpack(">H", resp[6:8])[0]
        if ancount > 0:
            return True
    except Exception:
        pass

    # 2. Fallback to socket getaddrinfo for general domain existence
    try:
        socket.getaddrinfo(domain, 25, socket.AF_INET, socket.SOCK_STREAM)
        return True
    except Exception:
        try:
            socket.gethostbyname(domain)
            return True
        except Exception:
            return False


def extract_clean_domain(url_or_company: str) -> Optional[str]:
    """Extract an apex or clean company domain from a URL or company name."""
    if not url_or_company:
        return None
    val = str(url_or_company).strip()

    if "://" in val or val.startswith("www."):
        parsed = urllib.parse.urlparse(val if "://" in val else f"https://{val}")
        netloc = parsed.netloc.lower().split(":")[0]
        if netloc.startswith("www."):
            netloc = netloc[4:]
        # If it's a known job board/ATS, don't treat it as company domain
        for jb in JOB_BOARD_DOMAINS:
            if netloc == jb or netloc.endswith("." + jb):
                return None
        return netloc if "." in netloc else None

    # If it's a domain-like string (e.g. 'axon.com')
    if "." in val and not " " in val:
        d = val.lower().strip()
        if d.startswith("www."):
            d = d[4:]
        for jb in JOB_BOARD_DOMAINS:
            if d == jb or d.endswith("." + jb):
                return None
        return d

    return None


def resolve_company_domain(company_name: str) -> Optional[str]:
    """Resolve the official company domain via Clearbit autocomplete, search engines, and MX verification."""
    if not company_name:
        return None
    cleaned_name = re.sub(r"(?i)\b(inc|corp|corporation|llc|ltd|gmbh|sa|sarl|co|company)\b|[,.]", "", company_name).strip()
    if not cleaned_name:
        return None

    # Tier 1: Free Clearbit Company Autocomplete API
    try:
        url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={urllib.parse.quote(cleaned_name)}"
        res = requests.get(url, headers={"User-Agent": UA}, timeout=4)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                dom = data[0].get("domain")
                if dom and "." in dom:
                    clean = extract_clean_domain(dom)
                    if clean and check_domain_has_mx(clean):
                        return clean
    except Exception as e:
        logger.debug("Clearbit domain resolution skipped for %s: %s", company_name, e)

    # Tier 2: Slug-based Domain Generation with DNS MX Verification
    slug = re.sub(r"[^a-zA-Z0-9]", "", cleaned_name).lower()
    if slug and len(slug) >= 3:
        for tld in (".com", ".io", ".co", ".ai", ".tech", ".fr", ".de", ".co.uk"):
            candidate = f"{slug}{tld}"
            if check_domain_has_mx(candidate):
                return candidate

    # Tier 3: Search Engine Dorking fallback
    query = f"{cleaned_name} official website"
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    try:
        res = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if res.status_code == 200:
            matches = re.findall(r'uddg=([^&"\']+)', res.text)
            for m in matches:
                unquoted = urllib.parse.unquote(m)
                d = extract_clean_domain(unquoted)
                if d and d not in JOB_BOARD_DOMAINS and "." in d and check_domain_has_mx(d):
                    return d
    except Exception as e:
        logger.debug("Search domain resolution failed for %s: %s", company_name, e)

    return None


def scrape_website_for_hr_emails(domain: str) -> list[DiscoveredContact]:
    """Crawl company homepage and contact/careers subpages to extract candidate HR emails."""
    contacts: list[DiscoveredContact] = []
    seen_emails: set[str] = set()

    paths_to_check = [
        f"https://{domain}/careers",
        f"https://{domain}/jobs",
        f"https://{domain}/contact",
        f"https://{domain}/contact-us",
        f"https://{domain}/about",
        f"https://{domain}/team",
        f"https://careers.{domain}",
        f"https://jobs.{domain}",
        f"https://{domain}/",
    ]

    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    discovered_links = set()

    for p in paths_to_check:
        try:
            r = session.get(p, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code != 200:
                continue

            content = r.text
            # Extract mailto links
            mailtos = re.findall(r'mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})', content, re.I)
            # Extract regex emails
            body_emails = EMAIL_REGEX.findall(content)

            all_raw = set(mailtos + body_emails)
            for raw in all_raw:
                em = clean_email_address(raw)
                if not em or em in seen_emails or is_excluded_email(em):
                    continue

                prefix, _, email_dom = em.partition("@")

                # Filter out image extensions erroneously matched
                if em.endswith((".png", ".jpg", ".svg", ".gif", ".webp")):
                    continue

                # Verify email domain has active mail server (pure DNS MX check)
                if not check_domain_has_mx(email_dom):
                    continue

                # Rank confidence
                confidence = score_email_relevance(prefix, email_dom, domain)
                if confidence > 30:
                    seen_emails.add(em)
                    contacts.append(DiscoveredContact(
                        email=em,
                        source="website_crawler",
                        confidence=confidence,
                        domain=domain,
                    ))

            # On homepage, look for career/contact links to crawl if not yet visited
            if p == f"https://{domain}/":
                hrefs = re.findall(r'href=["\']([^"\']+)["\']', content, re.I)
                for h in hrefs:
                    h_lower = h.lower()
                    if any(k in h_lower for k in ("career", "job", "contact", "join", "team", "talent", "recruiting")):
                        full_url = urllib.parse.urljoin(p, h)
                        parsed = urllib.parse.urlparse(full_url)
                        if domain in parsed.netloc and full_url not in paths_to_check and full_url not in discovered_links:
                            discovered_links.add(full_url)

            # If we found dedicated talent/recruiting emails, no need to crawl all remaining subpaths
            if any(c.confidence >= 85 for c in contacts):
                break

        except Exception as e:
            logger.debug("Failed crawling %s: %s", p, e)

    # Crawl up to 3 dynamically discovered career/contact links from homepage
    for extra_url in list(discovered_links)[:3]:
        try:
            r = session.get(extra_url, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code != 200:
                continue
            content = r.text
            found = set(re.findall(r'mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})', content, re.I) + EMAIL_REGEX.findall(content))
            for raw in found:
                em = raw.lower().strip()
                if em in seen_emails or is_excluded_email(em):
                    continue
                prefix, _, email_dom = em.partition("@")
                if not check_domain_has_mx(email_dom):
                    continue
                conf = score_email_relevance(prefix, email_dom, domain)
                if conf > 30:
                    seen_emails.add(em)
                    contacts.append(DiscoveredContact(
                        email=em,
                        source="website_crawler",
                        confidence=conf,
                        domain=domain,
                    ))
        except Exception as e:
            logger.debug("Failed crawling discovered link %s: %s", extra_url, e)

    return contacts


def search_dork_hr_contacts(company_name: str, domain: Optional[str] = None) -> list[DiscoveredContact]:
    """Query search engines for public recruiter listings, LinkedIn profiles, and talent acquisition emails."""
    contacts: list[DiscoveredContact] = []
    seen_emails: set[str] = set()

    # Dork queries targeting LinkedIn recruiter profiles, posts, and company careers
    queries: list[tuple[str, str]] = [
        # (query, source_label)
        (f'site:linkedin.com/in/ "{company_name}" ("recruiter" OR "talent acquisition" OR "technical recruiter" OR "talent partner" OR "hiring manager") ("email" OR "contact" OR "@")', "linkedin_dork"),
        (f'site:linkedin.com/posts/ "{company_name}" ("hiring" OR "recruiting") ("@" OR "email")', "linkedin_dork"),
        (f'"{company_name}" ("talent acquisition" OR "recruiting" OR "recruiter" OR "careers") email', "search_dork"),
    ]
    if domain:
        queries.append((f'site:{domain} ("recruiting" OR "talent" OR "careers" OR "contact" OR "jobs") email', "search_dork"))
        queries.append((f'"{company_name}" "@{domain}" ("recruiter" OR "talent" OR "careers" OR "jobs")', "search_dork"))

    headers = {"User-Agent": UA}

    for q, source_label in queries:
        try:
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(q)}"
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            if r.status_code != 200:
                continue

            clean_text = html.unescape(r.text)
            found_emails = EMAIL_REGEX.findall(clean_text)

            for em_raw in found_emails:
                em = clean_email_address(em_raw)
                if not em or em in seen_emails or is_excluded_email(em):
                    continue

                prefix, _, email_dom = em.partition("@")
                if em.endswith((".png", ".jpg", ".svg", ".gif", ".webp")):
                    continue

                # Ensure domain has active MX servers
                if not check_domain_has_mx(email_dom):
                    continue

                conf = score_email_relevance(prefix, email_dom, domain or "")
                if conf >= 35:
                    seen_emails.add(em)
                    contacts.append(DiscoveredContact(
                        email=em,
                        source=source_label,
                        confidence=conf,
                        domain=domain or email_dom,
                    ))

            # Also inspect unquoted target URLs returned by DuckDuckGo
            result_urls = re.findall(r'uddg=([^&"\']+)', r.text)
            for u in result_urls[:3]:
                unquoted = urllib.parse.unquote(u)
                for mailto in re.findall(r'mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})', unquoted, re.I):
                    m_em = mailto.lower().strip()
                    if m_em not in seen_emails and not is_excluded_email(m_em):
                        m_dom = m_em.partition("@")[2]
                        if check_domain_has_mx(m_dom):
                            seen_emails.add(m_em)
                            contacts.append(DiscoveredContact(
                                email=m_em,
                                source=source_label,
                                confidence=score_email_relevance(m_em.partition("@")[0], m_dom, domain or ""),
                                domain=domain or m_dom,
                            ))

        except Exception as e:
            logger.debug("Search dorking failed for %s: %s", q, e)

    return contacts


def generate_verified_role_patterns(domain: str) -> list[DiscoveredContact]:
    """DEPRECATED: We do NOT generate speculative or fake role mailboxes.
    
    Returns an empty list to guarantee only real, scraped emails are ever used.
    """
    return []


def query_hunter_api(domain: str, api_key: str) -> list[DiscoveredContact]:
    """Query Hunter.io API for verified HR & Talent Acquisition emails (if API key configured)."""
    if not domain or not api_key:
        return []

    contacts: list[DiscoveredContact] = []
    try:
        url = "https://api.hunter.io/v2/domain-search"
        params = {
            "domain": domain,
            "department": "hr",
            "api_key": api_key,
            "limit": 10,
        }
        res = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if res.status_code == 200:
            data = res.json().get("data", {})
            emails_data = data.get("emails", [])
            for item in emails_data:
                em = item.get("value")
                if not em:
                    continue
                first = item.get("first_name") or ""
                last = item.get("last_name") or ""
                pos = item.get("position") or "HR / Recruiter"
                score = item.get("confidence") or 85

                contacts.append(DiscoveredContact(
                    email=em.lower().strip(),
                    name=f"{first} {last}".strip(),
                    title=pos,
                    source="hunter_api",
                    confidence=min(100, max(50, score)),
                    domain=domain,
                ))
    except Exception as e:
        logger.warning("Hunter API query failed for %s: %s", domain, e)

    return contacts


def score_email_relevance(prefix: str, email_dom: str, company_dom: str) -> int:
    """Score the relevance of an email for job application and recruiter outreach."""
    prefix = prefix.lower()
    score = 40

    if company_dom and (email_dom == company_dom or email_dom.endswith("." + company_dom)):
        score += 20

    if any(k in prefix for k in ["talent", "recruiting", "recruiter", "talentacquisition"]):
        score += 35
    elif any(k in prefix for k in ["careers", "career", "jobs", "job", "hiring"]):
        score += 25
    elif any(k in prefix for k in ["people", "hr", "humanresources"]):
        score += 20
    elif any(k in prefix for k in ["contact", "info", "hello", "team"]):
        score += 5

    return min(100, score)


def extract_emails_from_text(text: str) -> list[str]:
    """Quick helper to extract raw valid emails from any job description or string."""
    if not text:
        return []
    matches = EMAIL_REGEX.findall(text)
    results: list[str] = []
    for m in matches:
        em = clean_email_address(m)
        if not em or is_excluded_email(em):
            continue
        if em.endswith((".png", ".jpg", ".svg", ".gif", ".webp")):
            continue
        _, _, dom = em.partition("@")
        if not check_domain_has_mx(dom):
            continue
        results.append(em)
    return list(dict.fromkeys(results))


def discover_hr_contacts_for_company(company_name: str, company_url: Optional[str] = None, job_url: Optional[str] = None) -> list[DiscoveredContact]:
    """Full multi-engine discovery: crawls website, searches LinkedIn/web dorks, and queries APIs.
    
    Guaranteed ZERO speculative or generated emails. Only real scraped or API contacts are returned.
    """
    if not (company_name or "").strip() and not (company_url or "").strip():
        return []

    all_contacts: list[DiscoveredContact] = []
    seen: set[str] = set()

    # 1. Resolve Company Domain
    domain = extract_clean_domain(company_url)
    if not domain and job_url:
        domain = extract_clean_domain(job_url)
    if not domain and company_name:
        domain = resolve_company_domain(company_name)

    logger.info("HR Discovery for '%s': resolved domain '%s'", company_name, domain or "None")

    # 2. Optional Hunter.io API if API key provided
    if domain and getattr(settings, "hunter_api_key", None):
        hunter_contacts = query_hunter_api(domain, settings.hunter_api_key)
        for c in hunter_contacts:
            if c.email not in seen and not is_excluded_email(c.email):
                seen.add(c.email)
                all_contacts.append(c)

    # 3. Scrape Company Website & Career Pages (real text & mailto)
    if domain:
        web_contacts = scrape_website_for_hr_emails(domain)
        for c in web_contacts:
            if c.email not in seen and not is_excluded_email(c.email):
                seen.add(c.email)
                all_contacts.append(c)

    # 4. Search Engine & LinkedIn Dorking (real public recruiter profiles and snippets)
    dork_contacts = search_dork_hr_contacts(company_name, domain)
    for c in dork_contacts:
        if c.email not in seen and not is_excluded_email(c.email):
            seen.add(c.email)
            all_contacts.append(c)

    # Sort contacts by confidence descending
    all_contacts.sort(key=lambda x: x.confidence, reverse=True)
    return all_contacts


def enrich_job_hr_contacts(job: Any) -> Optional[str]:
    """Enrich a JobPosting object with discovered real HR & Talent emails.
    
    Returns the top discovered real email, and updates job.application_emails.
    Never fabricates emails. If no real email is found, returns None.
    """
    if getattr(job, "application_emails", None):
        # Already has an email — clean leading/trailing punctuation and verify
        raw_list = [clean_email_address(e) for e in str(job.application_emails).split(",") if e.strip()]
        existing_emails = [e for e in raw_list if e and not is_excluded_email(e)]
        if existing_emails:
            job.application_emails = ", ".join(existing_emails)
            return existing_emails[0]

    # Check raw description first
    if getattr(job, "raw_description", None):
        desc_emails = extract_emails_from_text(job.raw_description)
        if desc_emails:
            best = desc_emails[0]
            job.application_emails = best
            logger.info("Extracted email %s directly from job description for %s", best, job.title)
            return best

    # Run multi-tier discovery (real website and LinkedIn dork scraping only)
    contacts = discover_hr_contacts_for_company(
        company_name=getattr(job, "company", ""),
        company_url=getattr(job, "company_url", None),
        job_url=getattr(job, "job_url_direct", None) or getattr(job, "job_url", None),
    )

    if contacts:
        emails_list = [c.email for c in contacts if c.confidence >= 35 and not is_excluded_email(c.email)]
        if emails_list:
            top_email = emails_list[0]
            job.application_emails = top_email
            logger.info("Enriched job %s (%s) with real HR email: %s (source: %s, conf: %s%%)",
                        getattr(job, "id", "?"), getattr(job, "company", "?"), top_email, contacts[0].source, contacts[0].confidence)
            return top_email

    return None
