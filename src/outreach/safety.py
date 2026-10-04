"""Outbound email safety policy: recipient validation, anti-spam checks, and suppression."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from config.settings import settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
SHORTENER_DOMAINS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "cutt.ly", "shorturl.at"
}
GENERIC_MAILBOXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "notifications", "mailer-daemon"
}
PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "yahoo.fr", "icloud.com", "me.com", "proton.me", "protonmail.com", "gmx.com"
}


INVERTED_PHRASES = [
    "ravis de recevoir votre candidature",
    "ravi de recevoir votre candidature",
    "bien recu votre candidature",
    "bien recue votre candidature",
    "merci pour votre candidature",
    "merci d'avoir postule",
    "merci d'avoir postulee",
    "nous recrutons",
    "nous recherchons un developpeur pour rejoindre notre equipe",
    "nous recherchons un developpeur",
    "nous recherchons un profil",
    "nous recherchons activement",
    "nous avons bien recu",
    "suite a votre candidature",
    "retenir votre candidature",
    "profil a retenu notre attention",
    "nous vous proposons un entretien",
    "notre equipe souhaite vous rencontrer",
    "nous serions ravis de vous compter",
    "pleased to receive your application",
    "excited to receive your application",
    "thank you for your application",
    "thank you for applying",
    "we have received your application",
    "we are actively recruiting",
    "we are looking for a developer to join our team",
    "we are looking for someone to join",
    "we are reviewing your application",
    "we would like to invite you",
]

PLACEHOLDER_PATTERN = re.compile(r"\[[A-Za-z0-9\s_/\-–—]+\]")


def is_inverted_perspective(text: str | None) -> bool:
    """Return True if text appears written from employer/recruiter to candidate instead of candidate to recruiter."""
    if not text:
        return False
    try:
        from src.evaluation.language import _fold_accents
        folded = _fold_accents(text)
    except Exception:
        folded = (text or "").lower()
    return any(phrase in folded for phrase in INVERTED_PHRASES)


def has_unresolved_placeholders(text: str | None) -> bool:
    """Return True if text contains brackets like [Nom] or literal 'Candidate Name'."""
    if not text:
        return False
    low = text.lower()
    if "candidate name" in low or "insert company" in low:
        return True
    return bool(PLACEHOLDER_PATTERN.search(text))


import socket
import struct
import time

_MX_CACHE: dict[str, tuple[bool, float]] = {}
_MX_CACHE_TTL = 3600.0  # 1 hour cache

DUMMY_LOCAL_EXACT = {
    "name", "yourname", "your-name", "your_name", "your", "youremail", "your-email", "your_email",
    "email", "e-mail", "myemail", "mail", "mailaddress", "mailbox", "test", "testing",
    "example", "sample", "user", "username", "candidate", "applicant", "candidat",
    "placeholder", "info-job", "fake", "dummy", "admin", "null", "none", "void",
    "firstname.lastname", "fname.lname", "fname_lname",
    "max.mustermann", "erika.mustermann", "maxmustermann", "erikamustermann", "max_mustermann",
    "etunimi.sukunimi", "etunimisukunimi", "etunimi_sukunimi",
    "prenom.nom", "nom.prenom", "prenom_nom", "nom_prenom",
    "fulano.detal", "fulanodetal", "fulano_detal",
}

DUMMY_DOMAINS = {
    "email.com", "example.com", "example.org", "example.net", "domain.com", "test.com",
    "sample.com", "invalid", "localhost", "wixpress.com", "cloudflare.com", "github.com",
    "facebookmail.com", "schema.org", "w3.org", "gravatar.com", "wordpress.org",
    "duckduckgo.com", "googlemail.invalid",
}

TELEMETRY_DOMAIN_KEYWORDS = {
    "sentry", "ingest", "bugsnag", "rollbar", "datadoghq", "logrocket",
    "telemetry", "analytics", "crashlytics", "track", "pixel",
}

COMPLIANCE_AND_NON_HIRING_PREFIXES = {
    # Compliance & Legal
    "dpo", "gdpr", "privacy", "data.privacy", "dataprivacy", "dataprotection",
    "legal", "compliance", "copyright", "dmca", "abuse", "security", "security-alert",
    "infosec", "fraud",
    # Non-hiring operational
    "press", "media", "pr", "ir", "investors", "investor-relations",
    "billing", "invoice", "invoices", "accounting", "roadtax",
    "customer-service", "accessibility", "accommodation", "accommodations",
    # Automated / System
    "noreply", "no-reply", "donotreply", "do-not-reply", "notifications", "notification",
    "mailer-daemon", "daemon", "postmaster", "hostmaster", "webmaster", "bounce", "bounces",
    "auto-confirm", "alerts", "alert", "system",
}

HEX_HASH_RE = re.compile(r"^[0-9a-f]{16,}$", re.I)


def check_domain_has_mx(domain: str, timeout: float = 2.5) -> bool:
    """Validate that a domain has active MX records using DNS over UDP (pure standard library).
    
    Checks 8.8.8.8 and 1.1.1.1 with in-memory caching.
    """
    if not domain or "." not in domain:
        return False
    clean_dom = domain.strip().lower().rstrip(".")
    if clean_dom in DUMMY_DOMAINS or any(clean_dom.endswith("." + d) for d in DUMMY_DOMAINS):
        return False
    if any(t in clean_dom for t in TELEMETRY_DOMAIN_KEYWORDS):
        return False
    if clean_dom.endswith((".invalid", ".test", ".example", ".local", ".localhost")):
        return False

    now = time.time()
    if clean_dom in _MX_CACHE:
        val, ts = _MX_CACHE[clean_dom]
        if now - ts < _MX_CACHE_TTL:
            return val

    # Fast-path for major known corporate/public email systems
    major_providers = {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
        "yahoo.com", "yahoo.fr", "icloud.com", "proton.me", "protonmail.com"
    }
    if clean_dom in major_providers:
        _MX_CACHE[clean_dom] = (True, now)
        return True

    # 1. Query UDP port 53 to public DNS (8.8.8.8, fallback 1.1.1.1)
    for dns_ip in ("8.8.8.8", "1.1.1.1"):
        try:
            header = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
            qname = b"".join(bytes([len(p)]) + p.encode("latin1") for p in clean_dom.split(".")) + b"\x00"
            packet = header + qname + struct.pack(">HH", 15, 1)  # 15 = MX, 1 = IN

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.sendto(packet, (dns_ip, 53))
            resp, _ = sock.recvfrom(1024)
            sock.close()
            ancount = struct.unpack(">H", resp[6:8])[0]
            if ancount > 0:
                _MX_CACHE[clean_dom] = (True, now)
                return True
            else:
                # DNS server explicitly answered that 0 MX records exist
                _MX_CACHE[clean_dom] = (False, now)
                return False
        except Exception:
            continue

    # 2. Fallback only if UDP DNS was blocked by network firewall
    try:
        socket.getaddrinfo(clean_dom, 25, socket.AF_INET, socket.SOCK_STREAM)
        _MX_CACHE[clean_dom] = (True, now)
        return True
    except Exception:
        try:
            socket.gethostbyname(clean_dom)
            _MX_CACHE[clean_dom] = (True, now)
            return True
        except Exception:
            _MX_CACHE[clean_dom] = (False, now)
            return False


@dataclass(frozen=True)
class SafetyResult:
    allowed: bool
    reason: str = ""
    risk: str = "low"


PUNCTUATION_STRIP = " \t\r\n-_.:;,()[]{}'\"/\\<>~*!?•–—"
CLEAN_EMAIL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def clean_email(address: str | None) -> str:
    """Clean and strip any leading/trailing symbols, bullets, or punctuation from email.
    
    Guarantees the email starts and ends with a valid alphanumeric character.
    E.g. '-kathrin.buchloh@materna.group' -> 'kathrin.buchloh@materna.group'
         '-macandidature.cer.fct@intradef.gouv.fr' -> 'macandidature.cer.fct@intradef.gouv.fr'
    """
    if not address:
        return ""
    raw = str(address).strip().lower()
    if "@" not in raw:
        return ""
    local, _, domain = raw.partition("@")
    clean_local = local.strip(PUNCTUATION_STRIP)
    clean_domain = domain.strip(PUNCTUATION_STRIP)

    # Strip HTML escapes / corrupted prefixes
    clean_local = re.sub(r"^(?:u003e|&gt;|&lt;|3e)+", "", clean_local)

    # Strip any remaining leading/trailing non-alphanumeric chars (e.g. bullets, dashes, dots)
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


def normalize_email(address: str | None) -> str:
    cleaned = clean_email(address)
    return cleaned if cleaned else (address or "").strip().lower()


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[1].lower()


def _local_part(address: str) -> str:
    return address.split("@", 1)[0].lower()


def _urls(body: str) -> list[str]:
    return URL_RE.findall(body or "")


def validate_external_email(address: str | None, *, allow_personal_domain: bool = False, check_mx: bool = True) -> SafetyResult:
    """Validate that an email address is real, active, hiring-related, and not a dummy or tracker."""
    cleaned = clean_email(address)
    if not cleaned or not CLEAN_EMAIL_RE.match(cleaned):
        return SafetyResult(False, f"Invalid recipient email address syntax: '{address}'", "high")
    
    local = _local_part(cleaned)
    domain = _domain(cleaned)

    # 1. Corrupted / HTML prefix check
    if local.startswith(("u003e", "&gt;", "3e")):
        return SafetyResult(False, f"Corrupted email prefix: '{local}'", "high")

    # 2. Dummy / Placeholder local names
    if local in DUMMY_LOCAL_EXACT or local.strip("._-") in DUMMY_LOCAL_EXACT:
        return SafetyResult(False, f"Placeholder/dummy recipient mailbox: '{local}'", "high")
    
    # 3. Dummy / Placeholder domains
    if domain in DUMMY_DOMAINS or any(domain.endswith("." + d) for d in DUMMY_DOMAINS):
        return SafetyResult(False, f"Placeholder/sample email domain: '{domain}'", "high")

    # 4. Sentry / Telemetry / Crash reporting / DSN tokens
    if HEX_HASH_RE.match(local):
        return SafetyResult(False, f"Telemetry/Sentry DSN key detected instead of human email: '{local}'", "high")
    if any(t in domain for t in TELEMETRY_DOMAIN_KEYWORDS):
        return SafetyResult(False, f"Telemetry/error tracking domain detected: '{domain}'", "high")

    # 5. Non-hiring / Legal / Compliance / Generic mailboxes
    if local in COMPLIANCE_AND_NON_HIRING_PREFIXES or any(local.startswith(p) for p in ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon")):
        return SafetyResult(False, f"Non-hiring/compliance or automated mailbox: '{local}'", "high")
    if any(k in local for k in ("privacy", "dataprotection", "accommodation", "accommodations")):
        return SafetyResult(False, f"Legal/compliance mailbox: '{local}'", "high")

    # 6. Personal mailbox domains
    if not allow_personal_domain and domain in PERSONAL_DOMAINS:
        return SafetyResult(False, f"Personal mailbox domain is not eligible for automatic external outreach: '{domain}'", "medium")

    # 7. Active DNS MX verification
    if check_mx and not check_domain_has_mx(domain):
        return SafetyResult(False, f"Domain '{domain}' has no active mail exchange (MX) servers.", "high")

    return SafetyResult(True)


def validate_message_content(subject: str, body: str) -> SafetyResult:
    subject = subject or ""
    body = body or ""
    if len(body.strip()) < max(40, settings.outbound_min_body_chars):
        return SafetyResult(False, "Email body is too short for safe automated outreach", "medium")
    if is_inverted_perspective(body) or is_inverted_perspective(subject):
        return SafetyResult(False, "Email contains inverted employer perspective (written as recruiter instead of candidate)", "high")
    if has_unresolved_placeholders(body) or has_unresolved_placeholders(subject):
        return SafetyResult(False, "Email contains unresolved bracketed or candidate placeholders", "high")
    urls = _urls(body)
    if len(urls) > settings.outbound_max_urls:
        return SafetyResult(False, "Email contains too many URLs", "high")
    for raw in urls:
        host = (urlparse(raw).hostname or "").lower()
        if host in SHORTENER_DOMAINS:
            return SafetyResult(False, f"URL shortener is not allowed: {host}", "high")
    exclamations = body.count("!") + subject.count("!")
    if exclamations > settings.outbound_max_exclamation_marks:
        return SafetyResult(False, "Email uses excessive exclamation marks", "medium")
    if sum(1 for c in body if c.isupper()) > max(20, len(body) // 3):
        return SafetyResult(False, "Email contains excessive uppercase text", "medium")
    return SafetyResult(True)


def validate_message(*, recipient: str, subject: str, body: str) -> SafetyResult:
    recipient_result = validate_external_email(recipient)
    if not recipient_result.allowed:
        return recipient_result
    content_result = validate_message_content(subject, body)
    if not content_result.allowed:
        return content_result
    return SafetyResult(True)

