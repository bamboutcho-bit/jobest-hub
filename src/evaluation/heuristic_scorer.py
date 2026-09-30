"""Smart offline heuristic scorer with precision requirement & continent parsing.

Evaluates job postings directly against candidate profile:
- Accurate Job Title matching
- Experience level extraction (Entry/Junior 0-2y, Mid 3-5y, Senior 5y+, Lead 8y+)
- Degree requirement parsing (Bachelor's, Master's, PhD, None)
- Visa sponsorship & relocation assistance classification
- Continent & geographic region classification (Europe, North America, MENA, Asia-Pacific, Global Remote)
"""
from __future__ import annotations

import re
from typing import Any

try:
    from config.settings import settings
except ImportError:
    class _FallbackSettings:
        min_match_score: int = 70
        min_visa_confidence: bool = False
    settings = _FallbackSettings()


EUROPE_COUNTRIES = {
    "switzerland", "suisse", "schweiz", "svizzera", "zurich", "geneva", "genève", "lausanne", "basel", "bern",
    "germany", "deutschland", "berlin", "munich", "münchen", "frankfurt", "hamburg", "cologne", "köln",
    "france", "paris", "lyon", "toulouse", "bordeaux", "nantes", "lille", "marseille",
    "united kingdom", "uk", "london", "manchester", "birmingham", "edinburgh", "bristol",
    "netherlands", "amsterdam", "rotterdam", "utrecht", "the hague", "eindhoven",
    "belgium", "belgique", "brussels", "bruxelles", "antwerp", "ghent",
    "spain", "españa", "madrid", "barcelona", "valencia", "seville",
    "portugal", "lisbon", "lisboa", "porto",
    "ireland", "dublin", "cork",
    "italy", "italia", "milan", "milano", "rome", "roma",
    "sweden", "stockholm", "gothenburg",
    "norway", "oslo", "denmark", "copenhagen", "finland", "helsinki",
    "poland", "warsaw", "krakow", "austria", "vienna", "wien",
    "czech", "czechia", "prague", "romania", "bucharest", "hungary", "budapest",
    "estonia", "tallinn", "latvia", "lithuania", "luxembourg", "europe", "eu"
}

NORTH_AMERICA_PLACES = {
    "united states", "usa", "us", "u.s.", "u.s.a.", "new york", "san francisco", "california",
    "seattle", "austin", "texas", "boston", "chicago", "los angeles", "denver",
    "canada", "toronto", "vancouver", "montreal", "ottawa", "quebec"
}

MENA_PLACES = {
    "morocco", "maroc", "rabat", "casablanca", "tanger", "marrakech", "fes", "salé",
    "united arab emirates", "uae", "dubai", "abu dhabi",
    "saudi arabia", "riyadh", "jeddah",
    "qatar", "doha", "egypt", "cairo", "alexandria",
    "kuwait", "bahrain", "oman", "jordan", "amman", "tunisia", "tunis", "mena"
}

ASIA_PACIFIC_PLACES = {
    "singapore", "japan", "tokyo", "australia", "sydney", "melbourne", "brisbane",
    "new zealand", "auckland", "india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai",
    "south korea", "seoul", "malaysia", "kuala lumpur", "indonesia", "jakarta", "asia", "apac"
}


def _visa_keywords() -> list[str]:
    return [x.strip().lower() for x in settings.visa_signal_keywords.split(",") if x.strip()]


class ExperienceResult(dict):
    def __iter__(self):
        return iter((self["experience_level"], self["experience_years_required"]))


class DegreeResult(str):
    def __getitem__(self, item):
        if item == "degree_required":
            return str(self)
        return super().__getitem__(item)

    def get(self, item, default=None):
        if item == "degree_required":
            return str(self)
        return default


class VisaRelocationResult(dict):
    def __iter__(self):
        return iter((self["visa_category"], self["relocation_detected"]))


def extract_continent(location: str, is_remote: bool = False, full_text: str = "") -> str:
    """Classify location into continents: Europe, North America, MENA, Asia-Pacific, Global Remote, Other."""
    loc_low = (location or "").lower()
    text_low = (full_text or "").lower()
    combined = f"{loc_low} {text_low}"

    # Check remote worldwide signals
    if is_remote or "remote" in loc_low or "remote" in text_low or "worldwide" in loc_low:
        if any(w in combined for w in ["worldwide", "anywhere", "global", "all countries", "work from anywhere"]):
            return "Global Remote"
        if any(c in loc_low for c in EUROPE_COUNTRIES):
            return "Europe"
        if any(c in loc_low for c in NORTH_AMERICA_PLACES):
            return "North America"
        if any(c in loc_low for c in MENA_PLACES):
            return "MENA"
        if any(c in loc_low for c in ASIA_PACIFIC_PLACES):
            return "Asia-Pacific"
        if is_remote or "remote" in loc_low:
            return "Global Remote"

    # Specific country/city mapping from location
    for token in EUROPE_COUNTRIES:
        pattern = r"\b" + re.escape(token) + r"\b"
        if re.search(pattern, loc_low) or (len(token) > 4 and token in loc_low):
            return "Europe"

    for token in NORTH_AMERICA_PLACES:
        pattern = r"\b" + re.escape(token) + r"\b"
        if re.search(pattern, loc_low):
            return "North America"

    for token in MENA_PLACES:
        pattern = r"\b" + re.escape(token) + r"\b"
        if re.search(pattern, loc_low):
            return "MENA"

    for token in ASIA_PACIFIC_PLACES:
        pattern = r"\b" + re.escape(token) + r"\b"
        if re.search(pattern, loc_low):
            return "Asia-Pacific"

    # Fallback checking full_text if location didn't match
    for token in EUROPE_COUNTRIES:
        pattern = r"\b" + re.escape(token) + r"\b"
        if re.search(pattern, text_low) or (len(token) > 4 and token in text_low):
            return "Europe"

    for token in NORTH_AMERICA_PLACES:
        pattern = r"\b" + re.escape(token) + r"\b"
        if re.search(pattern, text_low):
            return "North America"

    for token in MENA_PLACES:
        pattern = r"\b" + re.escape(token) + r"\b"
        if re.search(pattern, text_low):
            return "MENA"

    for token in ASIA_PACIFIC_PLACES:
        pattern = r"\b" + re.escape(token) + r"\b"
        if re.search(pattern, text_low):
            return "Asia-Pacific"

    return "Other"


def extract_experience_requirement(text: str, title: str) -> ExperienceResult:
    """Extract required years of experience and seniority level with high precision."""
    t_low = (title or "").lower()
    full_low = (text or "").lower()

    # 1. Seniority from title first
    seniority = None
    if any(re.search(r"\b" + re.escape(kw) + r"\b", t_low) for kw in ["lead", "principal", "staff", "architect", "director", "head of", "vp"]):
        seniority = "lead"
    elif any(re.search(r"\b" + re.escape(kw) + r"\b", t_low) for kw in ["senior", "sr", "sr."]):
        seniority = "senior"
    elif any(re.search(r"\b" + re.escape(kw) + r"\b", t_low) for kw in ["junior", "jr", "jr.", "entry level", "entry-level", "graduate", "trainee", "intern", "stage", "alternance", "débutant"]):
        seniority = "entry"
    elif any(re.search(r"\b" + re.escape(kw) + r"\b", t_low) for kw in ["mid", "intermediate", "confirmé"]):
        seniority = "mid"

    # 2. Extract numeric years of experience from text
    years = None
    patterns = [
        r"(?:minimum|at least|min\.?|au moins)\s*(?:of\s*)?(\d+)\+?\s*(?:years?|yrs?|ans?|années?)",
        r"(\d+)\+?\s*(?:to|-)\s*(\d+)?\s*(?:years?|yrs?|ans?|années?)\s*(?:of)?\s*(?:relevant|proven|commercial|professional|hands-on)?\s*(?:experience|exp)",
        r"(\d+)\+\s*(?:years?|yrs?|ans?|années?)\s*(?:of)?\s*(?:relevant|proven|commercial|professional|hands-on)?\s*(?:experience|exp)",
        r"(\d+)\s*(?:years?|yrs?|ans?|années?)\s*(?:of\s+)(?:relevant|proven|commercial|professional|hands-on)?\s*(?:experience|exp)",
    ]
    for pat in patterns:
        m = re.search(pat, full_low)
        if m:
            val = int(m.group(1))
            if 0 <= val <= 25:
                years = val
                break

    if years is None:
        m2 = re.search(r"\b(\d+)\+?\s*(?:years?|yrs?|ans)\b", full_low)
        if m2:
            val2 = int(m2.group(1))
            if 1 <= val2 <= 20:
                years = val2

    if seniority is None:
        if years is not None:
            if years <= 2:
                seniority = "entry"
            elif years <= 5:
                seniority = "mid"
            elif years <= 7:
                seniority = "senior"
            else:
                seniority = "lead"
        else:
            if any(k in full_low for k in ["junior", "entry-level", "entry level", "new grad", "graduate developer", "no experience required"]):
                seniority = "entry"
                years = 0
            elif any(k in full_low for k in ["senior engineer", "senior developer", "5+ years", "significant experience"]):
                seniority = "senior"
            else:
                seniority = "not_specified"

    return ExperienceResult({
        "experience_level": seniority or "not_specified",
        "experience_years_required": years,
    })


def extract_degree_requirement(text: str) -> DegreeResult:
    """Extract required education / degree level from job text."""
    low = (text or "").lower()

    if re.search(r"\b(phd|doctorate|doctoral|thèse|docteur)\b", low):
        return DegreeResult("phd")

    if re.search(r"\b(master'?s?|m\.?sc|m\.?s\.|bac\s*\+\s*5|diplôme d'ingénieur|ingénieur d'état|postgraduate)\b", low):
        return DegreeResult("master")

    if re.search(r"\b(bachelor'?s?|b\.?sc|b\.?s\.|bac\s*\+\s*3|licence|undergraduate|university degree|degree in computer science|bs in cs)\b", low):
        return DegreeResult("bachelor")

    if any(k in low for k in ["degree not required", "no degree required", "or equivalent practical experience", "or equivalent experience", "self-taught"]):
        return DegreeResult("none")

    return DegreeResult("not_specified")


def extract_visa_relocation(text: str, is_remote: bool = False) -> VisaRelocationResult:
    """Accurately classify visa sponsorship and relocation support."""
    low = (text or "").lower()

    negative_patterns = [
        "no visa", "not sponsor", "cannot sponsor", "no sponsorship", "unable to sponsor",
        "without sponsorship", "not provide sponsorship", "does not offer sponsorship",
        "no sponsorship available", "must be authorized to work", "valid work authorization required",
        "must be legally authorized", "us citizenship required", "swiss or eu", "eu citizenship required",
        "titulaire d'un permis", "carte de séjour exigée", "only eu/efta", "swiss citizens only"
    ]
    has_restricted = any(nv in low for nv in negative_patterns)

    relocation_patterns = [
        "relocation package", "relocation support", "relocation assistance", "relocation bonus",
        "relocation stipend", "help with relocation", "aide au déménagement", "prime d'installation",
        "visa and relocation", "relocation to"
    ]
    has_relocation = any(rp in low for rp in relocation_patterns)

    visa_patterns = [
        "visa sponsorship", "visa sponsor", "sponsor visa", "visa support",
        "willing to sponsor", "sponsor employment visa", "eu blue card", "skilled worker visa",
        "sponsorship available", "work permit sponsorship", "sponsor your visa", "visa assistance"
    ]
    # Do not detect visa if text explicitly states negative/restricted sponsorship
    has_visa = False if has_restricted else any(vp in low for vp in visa_patterns)

    if has_restricted and not has_relocation:
        return VisaRelocationResult({
            "visa_category": "restricted",
            "visa_detected": False,
            "relocation_detected": False,
            "visa_notes": "Explicit restriction: Local work authorization required; no visa sponsorship offered."
        })

    if has_visa:
        notes = "Explicit visa sponsorship detected"
        if has_relocation:
            notes += " with relocation support package"
        return VisaRelocationResult({
            "visa_category": "sponsored",
            "visa_detected": True,
            "relocation_detected": has_relocation,
            "visa_notes": notes + "."
        })

    if has_relocation:
        return VisaRelocationResult({
            "visa_category": "relocation",
            "visa_detected": True,
            "relocation_detected": True,
            "visa_notes": "Relocation package / moving support offered by employer."
        })

    if is_remote or "remote" in low:
        if any(w in low for w in ["worldwide", "anywhere", "global", "morocco", "all countries"]):
            return VisaRelocationResult({
                "visa_category": "remote_global",
                "visa_detected": True,
                "relocation_detected": False,
                "visa_notes": "Global remote position — legally accessible from Morocco without visa."
            })
        return VisaRelocationResult({
            "visa_category": "remote_global",
            "visa_detected": False,
            "relocation_detected": False,
            "visa_notes": "Remote position — work authorization requirements depend on employer entity."
        })

    return VisaRelocationResult({
        "visa_category": "unspecified",
        "visa_detected": False,
        "relocation_detected": False,
        "visa_notes": "No explicit visa sponsorship mentioned; standard work authorization verification applies."
    })


def evaluate_heuristic(job: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate job match with precision role, experience, degree, visa, and continent scoring."""
    if profile is None:
        from src.candidate.profile_manager import get_active_profile
        profile = get_active_profile()

    if not isinstance(job, dict):
        desc_val = getattr(job, "raw_description", None) or getattr(job, "description", "")
        job = {
            "id": getattr(job, "id", None),
            "title": getattr(job, "title", ""),
            "company": getattr(job, "company", ""),
            "location": getattr(job, "location", ""),
            "is_remote": getattr(job, "is_remote", False),
            "raw_description": desc_val,
            "description": desc_val,
        }

    title = str(job.get("title") or "").strip()
    company = str(job.get("company") or "Hiring Team").strip()
    location = str(job.get("location") or "").strip()
    is_remote = bool(job.get("is_remote", False))
    desc = str(job.get("raw_description") or job.get("description") or "").lower()
    full_text = f"{title.lower()} {company.lower()} {location.lower()} {desc}"

    # 1. Target Title Matching
    title_lower = title.lower()
    target_titles = profile.get("target_titles") or []
    title_score = 0

    for target in target_titles:
        t_low = target.lower()
        if t_low in title_lower:
            title_score = 35
            break
        words = [w for w in t_low.split() if len(w) > 3]
        matches = sum(1 for w in words if w in title_lower)
        if words and (matches / len(words)) >= 0.5:
            title_score = max(title_score, 25)

    # 2. Core Stack & Keywords Matching
    core_stack = profile.get("core_stack") or []
    custom_keywords = profile.get("keywords") or []
    all_candidate_skills = list(dict.fromkeys(core_stack + custom_keywords))

    matched_skills: list[str] = []
    for skill in all_candidate_skills:
        pattern = r"\b" + re.escape(skill.lower()) + r"\b"
        if re.search(pattern, full_text):
            matched_skills.append(skill)

    skill_score = min(40, len(matched_skills) * 8)

    # 3. Continent Classification
    continent = extract_continent(location, is_remote=is_remote, full_text=full_text)

    # 4. Visa & Relocation Classification
    visa_res = extract_visa_relocation(full_text, is_remote=is_remote)
    visa_detected = visa_res["visa_detected"]
    relocation_detected = visa_res["relocation_detected"]
    visa_category = visa_res["visa_category"]
    visa_notes = visa_res["visa_notes"]

    # 5. Experience Requirement & Candidate Matching
    exp_res = extract_experience_requirement(full_text, title)
    experience_level = exp_res["experience_level"]
    experience_years_required = exp_res["experience_years_required"]

    exp_specified = ("experience_years" in profile and profile.get("experience_years") is not None) or bool(profile.get("target_experience_level"))
    candidate_exp_years = int(profile.get("experience_years") or 0)
    candidate_is_entry = (candidate_exp_years <= 2 or profile.get("target_experience_level") in ("entry", "junior")) if exp_specified else False

    exp_score_mod = 0
    if candidate_is_entry:
        if experience_level in ("senior", "lead") or (experience_years_required and experience_years_required >= 5):
            # Heavy penalty for senior roles when candidate is junior
            exp_score_mod = -40
        elif experience_level == "entry" or (experience_years_required is not None and experience_years_required <= 2):
            # Priority bonus for true entry level / junior roles
            exp_score_mod = +20
        elif experience_level == "mid" or (experience_years_required and experience_years_required in (3, 4)):
            exp_score_mod = -10
    elif exp_specified:
        if experience_years_required and abs(candidate_exp_years - experience_years_required) <= 2:
            exp_score_mod = +15

    # 6. Degree Requirement & Candidate Matching
    deg_res = extract_degree_requirement(full_text)
    degree_required = str(deg_res["degree_required"])

    candidate_degree = (profile.get("degree_level") or "bachelor").lower()
    DEGREE_RANKS = {"none": 0, "not_specified": 0, "bachelor": 1, "master": 2, "phd": 3}
    cand_rank = DEGREE_RANKS.get(candidate_degree, 1)
    req_rank = DEGREE_RANKS.get(degree_required, 0)

    degree_matched = True
    deg_score_mod = 0
    if req_rank > 0:
        if cand_rank >= req_rank:
            degree_matched = True
            deg_score_mod = +10
        else:
            degree_matched = False
            deg_score_mod = -20

    # 7. Negative / Exclusion Keywords Penalty
    neg_keywords = profile.get("negative_keywords") or []
    neg_penalty = 0
    for neg in neg_keywords:
        if neg.lower() in full_text:
            neg_penalty += 25

    total_score = max(0, min(100, title_score + skill_score + exp_score_mod + deg_score_mod - neg_penalty))

    reasons: list[str] = []
    if candidate_is_entry and (experience_level == "entry" or (experience_years_required is not None and experience_years_required <= 2)):
        reasons.append("🟢 Entry-Level Match")
    elif candidate_is_entry and (experience_level in ("senior", "lead") or (experience_years_required and experience_years_required >= 5)):
        reasons.append("🔴 Senior / Overqualified Penalty (-40)")
    
    if visa_category == "restricted":
        reasons.append("⚠️ Restricted Work Authorization (No Visa)")
    elif visa_category == "sponsored":
        reasons.append("✈️ Visa Sponsorship Detected")
    elif visa_category == "remote_global":
        reasons.append("🌐 Global Remote Legally Accessible")

    if degree_matched and degree_required not in ("none", "not_specified"):
        reasons.append(f"🎓 Degree Aligned ({degree_required})")
    elif not degree_matched:
        reasons.append(f"🎓 Degree Gap ({degree_required})")

    reason_str = " · ".join(reasons) if reasons else f"Heuristic match score: {total_score}% based on stack and target title alignment."

    candidate_name = profile.get("name") or "Candidate"
    skills_summary = ", ".join(matched_skills[:4]) if matched_skills else ", ".join(core_stack[:3])

    pitch_en = (
        f"Hi {company} Team,\n\n"
        f"I saw your opening for {title} and wanted to reach out directly. "
        f"With a strong background in {skills_summary}, I have built scalable services and delivered robust solutions. "
        f"I would welcome the opportunity to discuss how my technical experience aligns with your roadmap at {company}.\n\n"
        f"Best regards,\n{candidate_name}"
    )

    pitch_fr = (
        f"Bonjour l'équipe {company},\n\n"
        f"J'ai pris connaissance de votre offre pour le poste de {title}. "
        f"Disposant d'une solide expertise technique sur {skills_summary}, j'ai conçu et déployé des applications performantes. "
        f"Je serais ravi d'échanger avec vous sur la manière dont mes compétences peuvent contribuer aux projets de {company}.\n\n"
        f"Bien cordialement,\n{candidate_name}"
    )

    app_subject = f"Application — {title} — {candidate_name}"
    app_subject_fr = f"Candidature — {title} — {candidate_name}"
    app_email_en = (
        f"Dear {company} Hiring Team,\n\n"
        f"I am writing to submit my application for the {title} position at {company}.\n\n"
        f"With hands-on expertise across {skills_summary}, I design and maintain reliable systems, clean APIs, and responsive applications. "
        f"Having reviewed the responsibilities for this role, I am confident that my technical foundation and problem-solving approach align closely with your team's goals.\n\n"
        f"My complete CV is attached with details on past projects and accomplishments. I am open to discussing how I can add value from day one.\n\n"
        f"Thank you for your time and consideration.\n\n"
        f"Sincerely,\n{candidate_name}\n{profile.get('current_location', '')}"
    )

    app_email_fr = (
        f"Bonjour l'équipe recrutement de {company},\n\n"
        f"Je me permets de vous soumettre ma candidature pour le poste de {title} au sein de {company}.\n\n"
        f"Fort d'une solide expertise technique ({skills_summary}), j'ai développé et maintenu des architectures fiables, des APIs performantes et des solutions logicielles adaptées aux enjeux métiers. "
        f"La lecture de votre offre confirme que mon profil technique et ma rigueur méthodologique correspondent aux besoins de votre équipe.\n\n"
        f"Vous trouverez ci-joint mon CV détaillé retraçant mon parcours et mes réalisations. Je reste à votre entière disposition pour tout échange approfondi.\n\n"
        f"En vous remerciant pour l'attention portée à ma candidature.\n\n"
        f"Bien cordialement,\n{candidate_name}\n{profile.get('current_location', '')}"
    )

    return {
        "score": total_score,
        "match_score": total_score,
        "reason": reason_str,
        "continent": continent,
        "experience_level": experience_level,
        "experience_years_required": experience_years_required,
        "degree_required": degree_required,
        "degree_matched": degree_matched,
        "visa_category": visa_category,
        "relocation_detected": relocation_detected,
        "visa_sponsorship_detected": visa_detected,
        "visa_status_notes": visa_notes,
        "key_skills_matched": matched_skills if matched_skills else core_stack[:3],
        "recruiter_pitch_fr": pitch_fr,
        "recruiter_pitch_en": pitch_en,
        "application_subject": app_subject,
        "application_subject_fr": app_subject_fr,
        "application_email_en": app_email_en,
        "application_email_fr": app_email_fr,
    }


evaluate_job_heuristic = evaluate_heuristic
