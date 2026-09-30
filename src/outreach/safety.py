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


def validate_external_email(address: str | None, *, allow_personal_domain: bool = False) -> SafetyResult:
    cleaned = clean_email(address)
    if not cleaned or not CLEAN_EMAIL_RE.match(cleaned):
        return SafetyResult(False, f"Invalid recipient email address syntax: '{address}'", "high")
    local = _local_part(cleaned)
    domain = _domain(cleaned)
    if local in GENERIC_MAILBOXES or local.startswith("no-reply") or local.startswith("noreply"):
        return SafetyResult(False, "Recipient appears to be an automated/no-reply mailbox", "high")
    if not allow_personal_domain and domain in PERSONAL_DOMAINS:
        return SafetyResult(False, "Personal mailbox domain is not eligible for automatic external outreach", "medium")
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

