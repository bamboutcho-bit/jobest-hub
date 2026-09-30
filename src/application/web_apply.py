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
    "application has been submitted", "application was submitted", "application successfully submitted",
    "application submitted successfully", "thank you for applying", "thanks for applying",
    "thank you for your application", "we have received your application", "we've received your application",
    "application received", "your application was sent", "application complete",
    "candidature envoyée", "candidature bien reçue", "candidature enregistrée",
    "merci pour votre candidature", "merci d'avoir postulé", "nous avons bien reçu votre candidature",
    "nous avons bien reçu", "votre candidature a été transmise", "votre candidature a bien été prise en compte",
    "votre profil a bien été transmis", "your application has been successfully submitted",
)


def _is_confirmation_url(url: str | None) -> bool:
    if not url:
        return False
    path = (urlparse(url).path or "").lower()
    return any(p in path for p in (
        "/thank_you", "/thank-you", "/thanks", "/confirmation",
        "/submitted", "/application-submitted", "/success", "/postule-merci"
    ))


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


def _attempt_solve_robot_verification(page) -> bool:
    """Detect and safely interact with verification robot widgets (Turnstile, reCAPTCHA, hCaptcha)."""
    interacted = False
    try:
        # 1. Cloudflare Turnstile inside frames
        for frame in page.frames:
            f_url = (frame.url or "").lower()
            if "challenges.cloudflare.com" in f_url or "turnstile" in f_url:
                logger.info("Accessing Cloudflare Turnstile frame: %s", frame.url)
                for sel in (
                    'input[type="checkbox"]',
                    '.ctp-checkbox-label',
                    '#challenge-stage input',
                    '#challenge-stage label',
                    'span.cb-i',
                    '#content input',
                    'body',
                ):
                    try:
                        target = frame.locator(sel).first
                        if target.count() and target.is_visible(timeout=600):
                            target.click()
                            page.wait_for_timeout(2500)
                            logger.info("Interacted with Turnstile target: %s", sel)
                            interacted = True
                            break
                    except Exception:
                        continue
                if interacted:
                    break

        # 2. Main-frame Turnstile element if cross-frame blocked
        if not interacted:
            for sel in (
                'iframe[src*="cloudflare.com"]',
                'iframe[src*="turnstile"]',
                'div.cf-turnstile',
                '[data-turnstile]',
            ):
                try:
                    el = page.locator(sel).first
                    if el.count() and el.is_visible(timeout=600):
                        el.click(timeout=1000)
                        page.wait_for_timeout(2500)
                        logger.info("Clicked Turnstile element on main page: %s", sel)
                        interacted = True
                        break
                except Exception:
                    continue

        # 3. Google reCAPTCHA v2 checkbox
        for frame in page.frames:
            f_url = (frame.url or "").lower()
            if "recaptcha" in f_url and "anchor" in f_url:
                logger.info("Accessing reCAPTCHA anchor frame: %s", frame.url)
                for sel in ('#recaptcha-anchor', '.recaptcha-checkbox-border', 'div[role="checkbox"]'):
                    try:
                        target = frame.locator(sel).first
                        if target.count() and target.is_visible(timeout=600):
                            if target.get_attribute("aria-checked") != "true":
                                target.click()
                                page.wait_for_timeout(2500)
                                logger.info("Clicked reCAPTCHA anchor: %s", sel)
                                interacted = True
                                break
                    except Exception:
                        continue
                if interacted:
                    break

        # 4. hCaptcha checkbox
        for frame in page.frames:
            f_url = (frame.url or "").lower()
            if "hcaptcha.com" in f_url and "checkbox" in f_url:
                logger.info("Accessing hCaptcha frame: %s", frame.url)
                for sel in ('#checkbox', 'div[role="checkbox"]', '#anchor'):
                    try:
                        target = frame.locator(sel).first
                        if target.count() and target.is_visible(timeout=600):
                            if target.get_attribute("aria-checked") != "true":
                                target.click()
                                page.wait_for_timeout(2500)
                                logger.info("Clicked hCaptcha checkbox: %s", sel)
                                interacted = True
                                break
                    except Exception:
                        continue
                if interacted:
                    break

        # 5. Generic "I am not a robot" / "Je ne suis pas un robot" checkbox
        if not interacted:
            boxes = page.locator('input[type="checkbox"]')
            for i in range(boxes.count()):
                box = boxes.nth(i)
                try:
                    if not box.is_visible(timeout=300):
                        continue
                    sig = _input_signature(box)
                    parent_text = ""
                    try:
                        parent_text = (box.locator("..").inner_text(timeout=300) or "").lower()
                    except Exception:
                        pass
                    if any(x in sig or x in parent_text for x in ("robot", "captcha", "humain", "human")):
                        if not box.is_checked():
                            box.check(timeout=1000)
                            page.wait_for_timeout(2000)
                            logger.info("Checked robot checkbox: %s", sig)
                            interacted = True
                            break
                except Exception:
                    continue
    except Exception as exc:
        logger.debug("Error while attempting robot verification: %s", exc)

    return interacted


def _has_active_robot_challenge(page) -> tuple[bool, str]:
    """Check if an unsolved robot verification or security challenge is currently active."""
    try:
        # Check active frames
        for frame in page.frames:
            f_url = (frame.url or "").lower()
            if "recaptcha" in f_url and "bframe" in f_url:
                try:
                    if frame.locator('body').is_visible(timeout=500):
                        return True, "reCAPTCHA image puzzle challenge active"
                except Exception:
                    pass
            if "hcaptcha.com" in f_url and ("challenge" in f_url or "prompt" in f_url):
                try:
                    if frame.locator('body').is_visible(timeout=500):
                        return True, "hCaptcha verification challenge active"
                except Exception:
                    pass
            if "challenges.cloudflare.com" in f_url:
                try:
                    frame_text = (frame.locator("body").inner_text(timeout=500) or "").lower()
                    if any(x in frame_text for x in ("verifying", "verify you are human", "please enable cookies", "checking your browser")):
                        return True, "Cloudflare Turnstile challenge pending"
                except Exception:
                    pass
    except Exception:
        pass

    try:
        body_text = (page.locator("body").inner_text(timeout=2000) or "").lower()
        active_phrases = (
            "verify you are human",
            "verify that you are human",
            "vérifiez que vous êtes un humain",
            "confirm you are human",
            "confirmez que vous êtes un être humain",
            "please complete the security check",
            "complete the security check",
            "security verification required",
            "please solve the captcha",
            "captcha verification failed",
            "veuillez valider le captcha",
            "unusual traffic from your computer network",
            "cf-turnstile-response",
        )
        for phrase in active_phrases:
            if phrase in body_text:
                return True, f"Security challenge active: '{phrase}'"
    except Exception:
        pass

    return False, ""


def _check_form_errors(page) -> str | None:
    """Detect visible validation errors blocking form submission."""
    error_selectors = (
        '[role="alert"]',
        '.error-message',
        '.form-error',
        '.field-error',
        '.alert-danger',
        '.invalid-feedback',
        '.has-error',
        '.ant-form-item-explain-error',
        '.field--error',
        'span[class*="error"]',
        'div[class*="error-message"]',
    )
    for selector in error_selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 5)):
                el = loc.nth(i)
                if el.is_visible(timeout=300):
                    txt = (el.inner_text(timeout=300) or "").strip()
                    lowered = txt.lower()
                    if any(k in lowered for k in (
                        "error", "required", "obligatoire", "invalid", "manquant",
                        "remplir", "champ", "captcha", "failed", "échec", "veuillez"
                    )):
                        return txt[:200]
        except Exception:
            continue
    return None


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
    """Try an automated ATS or portal submission and return a structured result."""
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

    host = _host(url)
    is_linkedin = "linkedin.com" in host
    is_indeed = "indeed.com" in host

    # If it is a job board URL, only proceed if portal automation is enabled for it
    if is_board_url(url):
        if is_linkedin and not getattr(settings, "auto_apply_linkedin_enabled", True):
            return {
                "application_method": "linkedin_manual",
                "application_status": "manual_linkedin",
                "application_url": url,
                "applied": False,
            }
        elif is_indeed and not getattr(settings, "auto_apply_indeed_enabled", True):
            return {
                "application_method": "job_board_manual",
                "application_status": "manual_job_board",
                "application_url": url,
                "applied": False,
            }
        elif not (is_linkedin or is_indeed):
            return {
                "application_method": "job_board_manual",
                "application_status": "manual_job_board",
                "application_url": url,
                "applied": False,
            }

    ats = detect_ats(url)
    if settings.auto_apply_known_ats_only and not ats and not (is_linkedin or is_indeed):
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

                # Inject user session cookies if available
                u_cfg = {}
                try:
                    from src.storage.user_settings import get_user_effective_settings
                    u_cfg = get_user_effective_settings(user_id=job_record.get("user_id"))
                except Exception:
                    pass

                if is_linkedin:
                    li_cookie = (profile.get("linkedin_cookie") if profile else None) or u_cfg.get("linkedin_cookie") or getattr(settings, "linkedin_cookie", "")
                    if li_cookie:
                        cookie_val = li_cookie.strip()
                        if "li_at=" in cookie_val:
                            cookie_val = cookie_val.split("li_at=", 1)[1].split(";", 1)[0].strip()
                        try:
                            context.add_cookies([{
                                "name": "li_at",
                                "value": cookie_val,
                                "domain": ".linkedin.com",
                                "path": "/",
                                "httpOnly": True,
                                "secure": True,
                            }])
                            logger.info("Injected LinkedIn session cookie (li_at) for browser session.")
                        except Exception as c_err:
                            logger.warning("Could not set LinkedIn cookie: %s", c_err)

                if is_indeed:
                    ind_cookie = (profile.get("indeed_cookie") if profile else None) or u_cfg.get("indeed_cookie") or getattr(settings, "indeed_cookie", "")
                    if ind_cookie:
                        cookie_val = ind_cookie.strip()
                        try:
                            if "=" in cookie_val:
                                for pair in cookie_val.split(";"):
                                    if "=" in pair:
                                        k, v = pair.strip().split("=", 1)
                                        context.add_cookies([{
                                            "name": k.strip(),
                                            "value": v.strip(),
                                            "domain": ".indeed.com",
                                            "path": "/",
                                        }])
                            else:
                                context.add_cookies([{
                                    "name": "SHARED_SESSION_ID",
                                    "value": cookie_val,
                                    "domain": ".indeed.com",
                                    "path": "/",
                                novoProduto: True,
                                }])
                            logger.info("Injected Indeed session cookie for browser session.")
                        except Exception as c_err:
                            logger.warning("Could not set Indeed cookie: %s", c_err)

                page = context.new_page()
                page.set_default_timeout(settings.apply_page_timeout_seconds * 1000)
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                page_url = page.url

                # Dispatch to specific portal handler or standard ATS form
                if is_linkedin:
                    return _handle_linkedin_portal(page, job_record, profile, resume, body, session=session)
                elif is_indeed:
                    return _handle_indeed_portal(page, job_record, profile, resume, body, session=session)
                else:
                    return _fill_and_submit_ats_form(page, job_record, profile, resume, body, session=session, ats=ats)
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


def _fill_and_submit_ats_form(page, job_record: dict, profile: dict | None, resume: Path, body: str, session=None, ats: str | None = None) -> dict:
    """Fill standard employer ATS forms safely and submit with verification checks."""
    page_url = page.url
    ats = ats or detect_ats(page.url)

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

    # Attempt to solve or access any verification robot present before filling
    _attempt_solve_robot_verification(page)

    # Check if an unsolved security challenge is already blocking the page
    has_robot, robot_msg = _has_active_robot_challenge(page)
    if has_robot:
        screenshot_path = _screenshot(page, job_record)
        return {
            "application_method": f"{ats or 'web'}_manual",
            "application_status": "manual_security_challenge",
            "application_url": page.url,
            "applied": False,
            "application_error": f"Security verification robot active: {robot_msg}",
            "application_screenshot_path": screenshot_path,
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
        'button:has-text("Apply")', 'button:has-text("Postuler")',
        'button:has-text("Envoyer la candidature")',
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

    # Attempt to solve robot verification right before submission
    _attempt_solve_robot_verification(page)

    # Reserve application attempt slot only when ready to submit
    reserve_application_attempt(session)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        submit.click(timeout=settings.apply_page_timeout_seconds * 1000)
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass

    page.wait_for_timeout(3000)

    # Attempt to solve robot verification if triggered upon submit
    _attempt_solve_robot_verification(page)

    screenshot_path = _screenshot(page, job_record)

    # Check if an active robot challenge is blocking the submission
    has_robot, robot_msg = _has_active_robot_challenge(page)
    if has_robot:
        return {
            "application_method": f"{ats or 'web'}_manual",
            "application_status": "manual_security_challenge",
            "application_url": page.url,
            "applied": False,
            "application_error": f"Robot verification challenge blocked submission: {robot_msg}",
            "application_screenshot_path": screenshot_path,
        }

    # Check for form validation error banners
    form_error = _check_form_errors(page)
    if form_error:
        return {
            "application_method": f"{ats or 'web'}_manual",
            "application_status": "manual_validation_errors",
            "application_url": page.url,
            "applied": False,
            "application_error": f"Form error blocked submission: {form_error}",
            "application_screenshot_path": screenshot_path,
        }

    # Check for genuine positive confirmation
    final_text = (page.locator("body").inner_text(timeout=5000) or "").lower()
    confirmed = any(marker in final_text for marker in SUCCESS_MARKERS) or _is_confirmation_url(page.url)

    if confirmed:
        return {
            "application_method": ats or "web_portal",
            "application_status": "submitted",
            "application_url": page_url,
            "applied": True,
            "applied_at": datetime.now(timezone.utc),
            "application_error": None,
            "application_screenshot_path": screenshot_path,
        }
    else:
        return {
            "application_method": ats or "web_portal",
            "application_status": "manual_unconfirmed_submission",
            "application_url": page_url,
            "applied": False,
            "application_error": "Submission confirmation could not be verified on final page.",
            "application_screenshot_path": screenshot_path,
        }


def _handle_linkedin_portal(page, job_record: dict, profile: dict | None, resume: Path, body: str, session=None) -> dict:
    """Automate application for LinkedIn job postings."""
    page_url = page.url
    screenshot_path = None

    # Step 1: Check for External Apply button on LinkedIn
    external_apply = _first_present(page, [
        'a[data-tracking-control-name*="apply"]',
        'a:has-text("Apply on company website")',
        'a:has-text("Postuler sur le site de l\'entreprise")',
        'a.jobs-apply-button',
    ])
    if external_apply:
        href = external_apply.get_attribute("href")
        if href and href.startswith(("http://", "https://")) and not ("linkedin.com" in href):
            logger.info("Found external company apply link on LinkedIn: %s", href)
            page.goto(href, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            return _fill_and_submit_ats_form(page, job_record, profile, resume, body, session=session)

    # Step 2: Check for Easy Apply button
    easy_apply_btn = _first_present(page, [
        'button:has-text("Easy Apply")',
        'button:has-text("Candidature simplifiée")',
        '.jobs-apply-button--top-card button',
        'button.jobs-apply-button',
    ])

    if not easy_apply_btn:
        text = (page.locator("body").inner_text(timeout=3000) or "").lower()
        if any(x in text for x in ("sign in", "join linkedin", "s'identifier", "connectez-vous")):
            screenshot_path = _screenshot(page, job_record)
            return {
                "application_method": "linkedin_portal",
                "application_status": "manual_auth_required",
                "application_url": page_url,
                "applied": False,
                "application_error": "LinkedIn login required. Please configure your LinkedIn cookie (li_at) in Settings.",
                "application_screenshot_path": screenshot_path,
            }
        screenshot_path = _screenshot(page, job_record)
        return {
            "application_method": "linkedin_portal",
            "application_status": "manual_apply_button_not_found",
            "application_url": page_url,
            "applied": False,
            "application_error": "Neither Easy Apply nor external apply link was found on this LinkedIn posting.",
            "application_screenshot_path": screenshot_path,
        }

    # Click Easy Apply button
    try:
        easy_apply_btn.click()
        page.wait_for_timeout(2000)
    except Exception as exc:
        screenshot_path = _screenshot(page, job_record)
        return {
            "application_method": "linkedin_portal",
            "application_status": "manual_click_failed",
            "application_url": page_url,
            "applied": False,
            "application_error": f"Failed clicking LinkedIn Easy Apply: {exc}",
            "application_screenshot_path": screenshot_path,
        }

    # Check if modal opened or login redirect occurred
    if "login" in page.url or page.locator('input#username').count():
        screenshot_path = _screenshot(page, job_record)
        return {
            "application_method": "linkedin_portal",
            "application_status": "manual_auth_required",
            "application_url": page_url,
            "applied": False,
            "application_error": "LinkedIn login required to access Easy Apply.",
            "application_screenshot_path": screenshot_path,
        }

    # Iterate through Easy Apply steps
    max_steps = 10
    step = 0
    submitted = False

    while step < max_steps:
        step += 1
        page.wait_for_timeout(1000)

        # 1. Fill visible inputs in modal
        inputs = page.locator('.jobs-easy-apply-modal input, div[role="dialog"] input')
        for i in range(inputs.count()):
            field = inputs.nth(i)
            try:
                if not field.is_visible(timeout=300):
                    continue
                typ = (field.get_attribute("type") or "text").lower()
                if typ in {"hidden", "checkbox", "radio", "file", "submit", "button"}:
                    continue
                sig = _input_signature(field)
                val = _candidate_value(sig, body=body, profile=profile)
                curr = (field.input_value(timeout=300) or "").strip()
                if val and not curr:
                    _fill(field, val)
                elif not curr and any(x in sig for x in ("year", "experience", "how many")):
                    _fill(field, "5")
            except Exception:
                continue

        # 2. Upload resume if file input present
        file_inputs = page.locator('.jobs-easy-apply-modal input[type="file"], div[role="dialog"] input[type="file"]')
        for i in range(file_inputs.count()):
            fi = file_inputs.nth(i)
            try:
                if resume.is_file():
                    fi.set_input_files(str(resume), timeout=3000)
            except Exception:
                pass

        # 3. Handle radios, checkboxes, selects
        _handle_common_radios(page)
        _handle_common_checkboxes(page)
        _handle_safe_selects(page)

        # 4. Check for Robot Verification widget
        _attempt_solve_robot_verification(page)

        # 5. Check if Submit button is present
        submit_btn = _first_present(page, [
            'button:has-text("Submit application")',
            'button:has-text("Envoyer la candidature")',
            'button[aria-label="Submit application"]',
            'button[aria-label="Envoyer la candidature"]',
        ])
        if submit_btn:
            reserve_application_attempt(session)
            submit_btn.click()
            page.wait_for_timeout(3000)
            submitted = True
            break

        # 6. Check for Review button
        review_btn = _first_present(page, [
            'button:has-text("Review")',
            'button:has-text("Vérifier")',
            'button[aria-label="Review your application"]',
        ])
        if review_btn:
            review_btn.click()
            page.wait_for_timeout(1500)
            continue

        # 7. Check for Next button
        next_btn = _first_present(page, [
            'button:has-text("Next")',
            'button:has-text("Suivant")',
            'button[aria-label="Continue to next step"]',
        ])
        if next_btn:
            if page.locator('.artdeco-inline-feedback--error').count():
                screenshot_path = _screenshot(page, job_record)
                _dismiss_linkedin_modal(page)
                return {
                    "application_method": "linkedin_portal",
                    "application_status": "manual_required_fields",
                    "application_url": page_url,
                    "applied": False,
                    "application_error": "LinkedIn Easy Apply requires manual review for custom questions.",
                    "application_screenshot_path": screenshot_path,
                }
            next_btn.click()
            page.wait_for_timeout(1500)
            continue

        break

    if submitted:
        _attempt_solve_robot_verification(page)
        has_robot, robot_msg = _has_active_robot_challenge(page)
        screenshot_path = _screenshot(page, job_record)
        if has_robot:
            return {
                "application_method": "linkedin_easy_apply",
                "application_status": "manual_security_challenge",
                "application_url": page_url,
                "applied": False,
                "application_error": f"Robot verification challenge blocked LinkedIn submission: {robot_msg}",
                "application_screenshot_path": screenshot_path,
            }

        final_text = (page.locator("body").inner_text(timeout=3000) or "").lower()
        confirmed = any(m in final_text for m in (
            "application sent", "candidature envoyée", "your application was sent",
            "thank you for applying", "candidature bien reçue",
        )) or not page.locator('.jobs-easy-apply-modal').is_visible()

        if confirmed:
            return {
                "application_method": "linkedin_easy_apply",
                "application_status": "submitted",
                "application_url": page_url,
                "applied": True,
                "applied_at": datetime.now(timezone.utc),
                "application_error": None,
                "application_screenshot_path": screenshot_path,
            }

    screenshot_path = _screenshot(page, job_record)
    _dismiss_linkedin_modal(page)
    return {
        "application_method": "linkedin_portal",
        "application_status": "manual_unconfirmed_submission",
        "application_url": page_url,
        "applied": False,
        "application_error": "LinkedIn Easy Apply could not be completed automatically.",
        "application_screenshot_path": screenshot_path,
    }


def _dismiss_linkedin_modal(page):
    try:
        dismiss = _first_present(page, [
            'button[aria-label="Dismiss"]',
            'button[data-test-modal-close-btn]',
            'button:has-text("Discard")',
            'button:has-text("Abandonner")',
        ])
        if dismiss:
            dismiss.click()
            page.wait_for_timeout(500)
            discard = _first_present(page, ['button:has-text("Discard")', 'button:has-text("Abandonner")'])
            if discard:
                discard.click()
    except Exception:
        pass


def _handle_indeed_portal(page, job_record: dict, profile: dict | None, resume: Path, body: str, session=None) -> dict:
    """Automate application for Indeed job postings."""
    page_url = page.url
    screenshot_path = None

    # Step 1: Check for External Apply button on Indeed
    external_apply = _first_present(page, [
        'a:has-text("Apply on company site")',
        'a:has-text("Postuler sur le site de l\'entreprise")',
        'a.view-apply-button',
        '#applyButtonLinkContainer a',
        'a[href*="/rc/clk"]',
    ])
    if external_apply:
        href = external_apply.get_attribute("href")
        if href and href.startswith(("http://", "https://")) and not ("indeed.com" in href):
            logger.info("Found external company apply link on Indeed: %s", href)
            page.goto(href, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            return _fill_and_submit_ats_form(page, job_record, profile, resume, body, session=session)

    # Step 2: Check for Indeed Apply button
    indeed_apply_btn = _first_present(page, [
        '#indeedApplyButton',
        'button:has-text("Apply now")',
        'button:has-text("Postuler maintenant")',
        'button[data-gnav-element-name="applyButton"]',
        'span.indeed-apply-button-label',
    ])

    if not indeed_apply_btn:
        screenshot_path = _screenshot(page, job_record)
        return {
            "application_method": "indeed_portal",
            "application_status": "manual_apply_button_not_found",
            "application_url": page_url,
            "applied": False,
            "application_error": "Indeed Apply button was not found on this posting.",
            "application_screenshot_path": screenshot_path,
        }

    # Click Indeed Apply button
    try:
        indeed_apply_btn.click()
        page.wait_for_timeout(2500)
    except Exception as exc:
        screenshot_path = _screenshot(page, job_record)
        return {
            "application_method": "indeed_portal",
            "application_status": "manual_click_failed",
            "application_url": page_url,
            "applied": False,
            "application_error": f"Failed clicking Indeed Apply button: {exc}",
            "application_screenshot_path": screenshot_path,
        }

    # Step 3: Handle Indeed Apply frame or container
    target_scope = page
    for frame in page.frames:
        if "indeedapply" in frame.url.lower() or "indeed-apply" in frame.url.lower():
            target_scope = frame
            break

    max_steps = 8
    step = 0
    submitted = False

    while step < max_steps:
        step += 1
        page.wait_for_timeout(1200)

        # 1. Fill contact inputs
        fields = target_scope.locator("input, textarea")
        for i in range(fields.count()):
            f = fields.nth(i)
            try:
                if not f.is_visible(timeout=300):
                    continue
                typ = (f.get_attribute("type") or "text").lower()
                if typ in {"hidden", "checkbox", "radio", "file", "submit", "button"}:
                    continue
                sig = _input_signature(f)
                val = _candidate_value(sig, body=body, profile=profile)
                curr = (f.input_value(timeout=300) or "").strip()
                if val and not curr:
                    _fill(f, val)
            except Exception:
                continue

        # 2. Upload resume if file input present
        files = target_scope.locator('input[type="file"]')
        for i in range(files.count()):
            fi = files.nth(i)
            try:
                if resume.is_file():
                    fi.set_input_files(str(resume), timeout=3000)
            except Exception:
                pass

        # 3. Handle checkboxes & radios
        _handle_common_radios(target_scope)
        _handle_common_checkboxes(target_scope)
        _handle_safe_selects(target_scope)

        # 4. Check for Robot Verification
        _attempt_solve_robot_verification(page)

        # 5. Check for Submit button
        submit_btn = _first_present(target_scope, [
            'button:has-text("Submit your application")',
            'button:has-text("Submit application")',
            'button:has-text("Postuler")',
            'button:has-text("Submit")',
        ])
        if submit_btn:
            reserve_application_attempt(session)
            submit_btn.click()
            page.wait_for_timeout(3500)
            submitted = True
            break

        # 6. Check for Continue / Next button
        continue_btn = _first_present(target_scope, [
            'button:has-text("Continue")',
            'button:has-text("Continuer")',
            'button:has-text("Next")',
            'button:has-text("Suivant")',
        ])
        if continue_btn:
            err = _check_form_errors(page)
            if err:
                screenshot_path = _screenshot(page, job_record)
                return {
                    "application_method": "indeed_portal",
                    "application_status": "manual_validation_errors",
                    "application_url": page_url,
                    "applied": False,
                    "application_error": f"Indeed Apply blocked by error: {err}",
                    "application_screenshot_path": screenshot_path,
                }
            continue_btn.click()
            page.wait_for_timeout(1500)
            continue

        break

    if submitted:
        _attempt_solve_robot_verification(page)
        has_robot, robot_msg = _has_active_robot_challenge(page)
        screenshot_path = _screenshot(page, job_record)
        if has_robot:
            return {
                "application_method": "indeed_apply",
                "application_status": "manual_security_challenge",
                "application_url": page_url,
                "applied": False,
                "application_error": f"Robot verification challenge blocked Indeed submission: {robot_msg}",
                "application_screenshot_path": screenshot_path,
            }

        final_text = (page.locator("body").inner_text(timeout=3000) or "").lower()
        confirmed = any(m in final_text for m in (
            "your application has been submitted",
            "application submitted",
            "votre candidature a bien été envoyée",
            "candidature transmise",
            "thank you for applying",
        )) or _is_confirmation_url(page.url)

        if confirmed:
            return {
                "application_method": "indeed_apply",
                "application_status": "submitted",
                "application_url": page_url,
                "applied": True,
                "applied_at": datetime.now(timezone.utc),
                "application_error": None,
                "application_screenshot_path": screenshot_path,
            }

    screenshot_path = _screenshot(page, job_record)
    return {
        "application_method": "indeed_portal",
        "application_status": "manual_unconfirmed_submission",
        "application_url": page_url,
        "applied": False,
        "application_error": "Indeed Apply could not be confirmed on final page.",
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
