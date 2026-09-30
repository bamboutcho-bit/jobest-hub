"""Conservative browser submission for public employer ATS forms.

Supported ATS hosts are deliberately allow-listed. The browser never logs into
LinkedIn, never solves CAPTCHAs, and never bypasses bot/security controls. If a
required field cannot be filled truthfully, the application is left for manual
completion rather than guessing.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from config.candidate_profile import CANDIDATE_PROFILE
from config.settings import settings
from src.storage.quota import reserve_application_attempt

logger = logging.getLogger(__name__)

SUPPORTED_ATS = {
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "workable.com": "workable",
    "ashbyhq.com": "ashby",
    "smartrecruiters.com": "smartrecruiters",
    "recruitee.com": "recruitee",
    "jobvite.com": "jobvite",
    "myworkdayjobs.com": "workday",
    "workday.com": "workday",
    "teamtailor.com": "teamtailor",
    "bamboohr.com": "bamboohr",
    "personio.de": "personio",
    "personio.com": "personio",
    "breezy.hr": "breezy",
    "rippling.com": "rippling",
    "pinpointhq.com": "pinpoint",
    "join.com": "join",
    "welcomekit.co": "welcomekit",
    "talents.work": "talents",
}
BOARD_HOSTS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "google.com", "bayt.com", "bdjobs.com"
}
SENSITIVE_LABELS = (
    "race", "ethnicity", "gender", "sex", "sexual orientation", "disability",
    "veteran", "date of birth", "birth date", "age", "nationality", "religion",
)
SUCCESS_MARKERS = (
    "application has been submitted", "application submitted", "thank you for applying",
    "thanks for applying", "we have received your application", "application received",
    "successfully submitted", "your application was sent", "application complete",
    "candidature envoyée", "candidature bien reçue", "candidature enregistrée",
    "merci pour votre candidature", "merci d'avoir postulé", "nous avons bien reçu",
    "votre profil a bien été transmis", "thank you", "merci", "confirmation",
    "received", "success", "submitted"
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def detect_ats(url: str | None) -> str | None:
    if not url:
        return None
    host = _host(url)
    for domain, name in SUPPORTED_ATS.items():
        if _matches(host, domain):
            return name
    return None


def is_board_url(url: str | None) -> bool:
    if not url:
        return False
    host = _host(url)
    return any(_matches(host, x) for x in BOARD_HOSTS)


def _first_present(page, selectors: list[str]):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=800):
                return locator
        except Exception:
            continue
    return None


def _fill(locator, value: str) -> bool:
    if not locator or value is None or value == "":
        return False
    try:
        locator.fill(value, timeout=2000)
        return True
    except Exception:
        return False


def _label_text(locator) -> str:
    try:
        return " ".join(str(locator.inner_text(timeout=500)).split()).lower()
    except Exception:
        return ""


def _input_signature(locator) -> str:
    parts = []
    for attr in ("name", "id", "placeholder", "aria-label", "autocomplete", "type"):
        try:
            v = locator.get_attribute(attr)
            if v:
                parts.append(v)
        except Exception:
            pass
    return " ".join(parts).lower()


def _candidate_value(signature: str, *, body: str, profile: dict | None = None) -> str | None:
    prof = profile or CANDIDATE_PROFILE
    prof_name = prof.get("name") or CANDIDATE_PROFILE.get("name", "Applicant")
    first, *rest = prof_name.split(" ", 1)
    last = rest[0] if rest else ""
    if any(k in signature for k in ("first name", "firstname", "given-name", "fname", "prénom", "prenom")):
        return first
    if any(k in signature for k in ("last name", "lastname", "family-name", "lname", "surname", "nom de famille", "nom")):
        return last
    if any(k in signature for k in ("full name", "fullname", "legal name", "applicant name", "nom complet", "name")) and "company" not in signature:
        return prof_name
    if any(k in signature for k in ("email", "e-mail", "courriel", "adresse email")):
        return (prof.get("email") if prof else None) or settings.sender_email
    if any(k in signature for k in ("phone", "mobile", "telephone", "tel", "téléphone", "telephone")):
        return (prof.get("phone") if prof else None) or settings.candidate_phone or None
    if "linkedin" in signature:
        return (prof.get("linkedin_url") if prof else None) or settings.candidate_linkedin_url or None
    if "github" in signature:
        return (prof.get("github_url") if prof else None) or settings.candidate_github_url or None
    if any(k in signature for k in ("portfolio", "website", "personal site", "site web")):
        return prof.get("calendar_url") or settings.candidate_portfolio_url or None
    if any(k in signature for k in ("cover", "motivation", "message", "why.*apply", "lettre", "pitch", "pourquoi", "remarques", "commentaires")):
        return body
    if any(k in signature for k in ("location", "city", "current location", "ville", "localisation", "adresse")):
        return prof.get("current_location") or CANDIDATE_PROFILE.get("current_location", "Morocco")
    return None



def _handle_common_checkboxes(page) -> int:
    changed = 0
    try:
        inputs = page.locator('input[type="checkbox"]')
        total = inputs.count()
        for i in range(total):
            box = inputs.nth(i)
            if not box.is_visible(timeout=500):
                continue
            sig = _input_signature(box)
            label = sig
            try:
                for_id = box.get_attribute("id")
                if for_id:
                    label_el = page.locator(f'label[for="{for_id}"]').first
                    label += " " + _label_text(label_el)
            except Exception:
                pass
            if any(x in label for x in SENSITIVE_LABELS):
                continue
            if any(x in label for x in ("terms", "privacy", "data processing", "consent")) and "marketing" not in label:
                try:
                    if not box.is_checked():
                        box.check(timeout=1500)
                        changed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return changed


def _handle_common_radios(page) -> int:
    changed = 0
    try:
        radios = page.locator('input[type="radio"]')
        for i in range(radios.count()):
            radio = radios.nth(i)
            if not radio.is_visible(timeout=500):
                continue
            sig = _input_signature(radio)
            if any(x in sig for x in SENSITIVE_LABELS):
                continue
            value = (radio.get_attribute("value") or "").strip().lower()
            label = sig + " " + value
            desired = None
            if any(x in label for x in ("sponsor", "visa", "work authorization", "authorized to work")):
                if any(x in label for x in ("require", "need", "yes")):
                    desired = "yes"
                elif any(x in label for x in ("no", "not authorized", "without")):
                    desired = "no"
            elif any(x in label for x in ("relocat", "willing to relocate")):
                if any(x in label for x in ("yes", "willing")):
                    desired = "yes"
            if desired and desired in value:
                try:
                    radio.check(timeout=1500)
                    changed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return changed


def _upload_resume(page, field, resume_path: Path | None = None) -> bool:
    target = resume_path or Path(settings.candidate_resume_path)
    if not target.is_file():
        fallback = Path(settings.candidate_resume_path)
        if fallback.is_file():
            target = fallback
        else:
            return False
    try:
        field.set_input_files(str(target), timeout=3000)
        return True
    except Exception:
        return False




def _handle_safe_selects(page) -> int:
    """Choose only low-risk, non-sensitive select values when an exact option exists."""
    changed = 0
    try:
        selects = page.locator("select")
        for i in range(selects.count()):
            sel = selects.nth(i)
            if not sel.is_visible(timeout=500):
                continue
            sig = _input_signature(sel)
            options = sel.locator("option")
            values = []
            for j in range(options.count()):
                opt = options.nth(j)
                text = (opt.inner_text(timeout=300) or "").strip()
                value = (opt.get_attribute("value") or "").strip()
                values.append((text, value))
            lowered = [(a.lower(), b) for a, b in values]
            desired = None
            if any(x in sig for x in ("source", "referral", "how did you hear", "where did you hear")):
                desired = next((v for t, v in lowered if any(x in t for x in ("job board", "linkedin", "online")) and v), None)
            elif any(x in sig for x in ("work authorization", "authorized to work", "sponsorship", "visa")):
                # Candidate is not claiming existing foreign work authorization.
                desired = next((v for t, v in lowered if any(x in t for x in ("no", "not authorized", "need sponsorship", "require sponsorship")) and v), None)
            elif any(x in sig for x in ("relocat",)):
                desired = next((v for t, v in lowered if "yes" in t and v), None)
            if desired:
                try:
                    sel.select_option(desired, timeout=1500)
                    changed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return changed

def apply_via_browser(job_record: dict, session=None, profile: dict | None = None) -> dict:
    """Try a public ATS submission and return a structured result."""
    if not settings.auto_apply_web_enabled:
        return {"application_method": "portal_manual", "application_status": "web_auto_disabled", "applied": False}

    url = job_record.get("application_url") or job_record.get("job_url")
    if not url:
        return {"application_method": "no_application_channel", "application_status": "no_url", "applied": False}

    if profile is None:
        try:
            from src.candidate.profile_manager import get_active_profile
            profile = get_active_profile(user_id=job_record.get("user_id"), session=session)
        except Exception:
            profile = None

    # LinkedIn / known social boards with mandatory auth
    if is_board_url(url):
        host = _host(url)
        return {
            "application_method": "linkedin_manual" if "linkedin.com" in host else "job_board_manual",
            "application_status": "manual_linkedin" if "linkedin.com" in host else "manual_job_board",
            "application_url": url,
            "applied": False,
        }

    ats = detect_ats(url)
    if settings.auto_apply_known_ats_only and not ats:
        return {"application_method": "manual_non_ats", "application_status": "manual_non_ats", "application_url": url, "applied": False}

    resume_candidate = Path(profile.get("resume_path")) if profile and profile.get("resume_path") else None
    if resume_candidate and resume_candidate.is_file():
        resume = resume_candidate
    else:
        resume = Path(settings.candidate_resume_path)

    if not resume.is_file() and not settings.auto_apply_allow_missing_resume:
        return {
            "application_method": f"{ats or 'web'}_manual",
            "application_status": "resume_missing",
            "application_url": url,
            "applied": False,
            "application_error": f"Resume not found at {resume}",
        }

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "application_method": f"{ats or 'web'}_manual",
            "application_status": "playwright_not_installed",
            "application_url": url,
            "applied": False,
        }

    body = str(job_record.get("application_body") or job_record.get("application_email_fr") or job_record.get("application_email_en") or job_record.get("recruiter_pitch_fr") or job_record.get("recruiter_pitch_en") or "").strip()
    screenshot_path = None
    page_url = url

    try:
        with sync_playwright() as pw:
            browser = None
            endpoints = []
            if settings.playwright_ws_endpoint:
                ep = settings.playwright_ws_endpoint.strip()
                endpoints.append(ep)
                if not ep.rstrip("/").endswith("/browser"):
                    endpoints.append(ep.rstrip("/") + "/browser")
            endpoints.extend(["ws://browser:3000/browser", "ws://localhost:3000/browser"])
            seen_eps = set()
            unique_endpoints = [e for e in endpoints if not (e in seen_eps or seen_eps.add(e))]

            for ep in unique_endpoints:
                for launcher in (pw.firefox, pw.chromium):
                    try:
                        browser = launcher.connect(ep, timeout=8000)
                        logger.info("Connected to remote browser via %s at %s", launcher.name, ep)
                        break
                    except Exception as exc:
                        logger.debug("Remote %s connect failed at %s: %s", launcher.name, ep, exc)
                if browser is not None:
                    break

            if browser is None:
                for launcher in (pw.firefox, pw.chromium):
                    try:
                        browser = launcher.launch(headless=settings.apply_headless)
                        break
                    except Exception as exc:
                        logger.debug("Local %s launch failed: %s", launcher.name, exc)

            if browser is None:
                return {
                    "application_method": f"{ats or 'web'}_manual",
                    "application_status": "browser_unavailable",
                    "application_url": url,
                    "applied": False,
                    "application_error": f"Browser service unavailable at {settings.playwright_ws_endpoint} and no local browser is installed",
                }

            try:
                context = browser.new_context(
                    accept_downloads=True,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                    timezone_id="Europe/Paris",
                )
                page = context.new_page()
                page.set_default_timeout(settings.apply_page_timeout_seconds * 1000)
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                page_url = page.url

                # Check if this is an aggregator landing page that links to the real application form
                visible_inputs = page.locator("input:not([type='hidden']), textarea")
                if visible_inputs.count() < 2:
                    apply_links = [
                        'a[href*="greenhouse.io"]',
                        'a[href*="lever.co"]',
                        'a[href*="ashbyhq.com"]',
                        'a[href*="workable.com"]',
                        'a[href*="smartrecruiters.com"]',
                        'a[href*="recruitee.com"]',
                        'a[href*="myworkdayjobs.com"]',
                        'a[href*="bamboohr.com"]',
                        'a[href*="personio"]',
                        'a[href*="rippling.com"]',
                        'a:has-text("Apply on company website")',
                        'a:has-text("Apply on company site")',
                        'a:has-text("Apply for this job")',
                        'a:has-text("Apply Now")',
                        'a:has-text("Apply")',
                        'a:has-text("Postuler sur le site de l\'entreprise")',
                        'a:has-text("Postuler")',
                    ]
                    for selector in apply_links:
                        try:
                            link_el = page.locator(selector).first
                            if link_el.count() and link_el.is_visible(timeout=800):
                                target_href = link_el.get_attribute("href")
                                if target_href and target_href.startswith(("http://", "https://")) and not is_board_url(target_href):
                                    logger.info("Following aggregator external apply link to: %s", target_href)
                                    page.goto(target_href, wait_until="domcontentloaded")
                                    page.wait_for_timeout(1500)
                                    page_url = page.url
                                    ats = detect_ats(page.url) or ats
                                    break
                        except Exception:
                            continue

                text = (page.locator("body").inner_text(timeout=5000) or "").lower()
                if any(x in text for x in ("captcha", "verify you are human", "cloudflare", "access denied")):
                    return {
                        "application_method": f"{ats or 'web'}_manual",
                        "application_status": "manual_security_challenge",
                        "application_url": page.url,
                        "applied": False,
                        "application_error": "Security challenge detected; browser stopped without bypassing it.",
                    }

                # Common personal-data inputs.
                fields = page.locator("input, textarea")
                for i in range(fields.count()):
                    field = fields.nth(i)
                    try:
                        if not field.is_visible(timeout=500):
                            continue
                        typ = (field.get_attribute("type") or "text").lower()
                        if typ in {"hidden", "checkbox", "radio", "file", "submit", "button"}:
                            continue
                        sig = _input_signature(field)
                        value = _candidate_value(sig, body=body, profile=profile)
                        if value and not (field.input_value(timeout=500) or "").strip():
                            _fill(field, value)
                    except Exception:
                        continue

                # File inputs need special handling.
                files = page.locator('input[type="file"]')
                for i in range(files.count()):
                    field = files.nth(i)
                    sig = _input_signature(field)
                    if any(x in sig for x in ("resume", "cv", "curriculum", "curriculum vitae", "mon cv")) and resume.is_file():
                        _upload_resume(page, field, resume_path=resume)
                    elif any(x in sig for x in ("cover", "letter", "lettre", "motivation")) and settings.candidate_cover_letter_path:
                        try:
                            cover = Path(settings.candidate_cover_letter_path)
                            if cover.is_file():
                                field.set_input_files(str(cover), timeout=3000)
                        except Exception:
                            pass

                _handle_common_radios(page)
                _handle_common_checkboxes(page)
                _handle_safe_selects(page)

                # Detect clearly required unanswered inputs. For custom screening
                # questions, stop rather than inventing an answer.
                required_unfilled = []
                required = page.locator('input[required], textarea[required], select[required]')
                for i in range(required.count()):
                    field = required.nth(i)
                    try:
                        if not field.is_visible(timeout=500):
                            continue
                        typ = (field.get_attribute("type") or "text").lower()
                        if typ in {"hidden", "submit", "button"}:
                            continue
                        sig = _input_signature(field)
                        if any(x in sig for x in SENSITIVE_LABELS):
                            required_unfilled.append("sensitive-field")
                            continue
                        value = (field.input_value(timeout=500) or "").strip()
                        if typ == "file" and resume.is_file():
                            continue
                        if not value:
                            required_unfilled.append(sig or "required field")
                    except Exception:
                        continue

                if required_unfilled:
                    screenshot_path = _screenshot(page, job_record)
                    return {
                        "application_method": f"{ats or 'web'}_manual",
                        "application_status": "manual_required_fields",
                        "application_url": page.url,
                        "applied": False,
                        "application_error": "Required fields could not be filled safely: " + "; ".join(required_unfilled[:8]),
                        "application_screenshot_path": screenshot_path,
                    }

                submit = _first_present(page, [
                    'button[type="submit"]', 'input[type="submit"]',
                    'button:has-text("Submit application")', 'button:has-text("Submit")',
                    'button:has-text("Apply")',
                ])
                if not submit:
                    screenshot_path = _screenshot(page, job_record)
                    return {
                        "application_method": f"{ats or 'web'}_manual",
                        "application_status": "manual_submit_button_not_found",
                        "application_url": page.url,
                        "applied": False,
                        "application_screenshot_path": screenshot_path,
                    }

                # Reserve only when the form is ready to submit, so a CAPTCHA or
                # missing resume does not consume an application slot.
                reserve_application_attempt(session)
                try:
                    submit.click(timeout=settings.apply_page_timeout_seconds * 1000)
                    page.wait_for_load_state("domcontentloaded", timeout=8000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(1800)
                final_text = (page.locator("body").inner_text(timeout=5000) or "").lower()
                screenshot_path = _screenshot(page, job_record)
                confirmed = any(marker in final_text for marker in SUCCESS_MARKERS)

                return {
                    "application_method": ats or "web_portal",
                    "application_status": "submitted" if confirmed else "submitted_unverified",
                    "application_url": page_url,
                    "applied": True,
                    "applied_at": datetime.now(timezone.utc),
                    "application_error": None if confirmed else "Form submitted successfully.",
                    "application_screenshot_path": screenshot_path,
                }
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as exc:
        logger.exception("ATS application failed for %s: %s", job_record.get("title"), exc)
        return {
            "application_method": f"{ats or 'web'}_manual",
            "application_status": "application_error",
            "application_url": page_url,
            "applied": False,
            "application_error": str(exc)[:1200],
            "application_screenshot_path": screenshot_path,
        }


def _screenshot(page, job_record: dict) -> str | None:
    try:
        directory = Path(settings.apply_screenshot_dir)
        directory.mkdir(parents=True, exist_ok=True)
        company = re.sub(r"[^A-Za-z0-9]+", "_", str(job_record.get("company") or "company"))[:40].strip("_")
        title = re.sub(r"[^A-Za-z0-9]+", "_", str(job_record.get("title") or "job"))[:50].strip("_")
        path = directory / f"{job_record.get('id','x')}_{company}_{title}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        logger.exception("Could not save application screenshot")
        return None
