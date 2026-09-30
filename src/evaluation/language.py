import re
import unicodedata
from typing import Any


def _fold_accents(s: str) -> str:
    """Normalize text to lowercase ASCII without diacritics."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(s or "").lower())
        if not unicodedata.combining(c)
    )


FRENCH_LOCATIONS = {
    "france", "paris", "lyon", "marseille", "toulouse", "bordeaux", "nantes",
    "lille", "strasbourg", "rennes", "nice", "montpellier", "grenoble",
    "sophia antipolis", "rouen", "toulon", "angers", "dijon", "brest", "le mans",
    "aix-en-provence", "clermont-ferrand", "tours", "amiens", "limoges", "metz",
    "besancon", "perpignan", "orleans", "caen", "mulhouse", "boulogne-billancourt",
    "nancy", "saint-etienne", "ile-de-france", "idf", "fr", "fra"
}

FRENCH_KEYWORDS = [
    "developpeur", "developpeuse", "ingenieur", "ingenieure", "candidature",
    "nous recherchons", "recherche un", "recherche une", "competences requises",
    "profil recherche", "vos missions", "votre mission", "au sein de",
    "rejoignez-nous", "rejoignez notre equipe", "teletravail", "salaire",
    "bac+", "bac +", "cdi", "cdd", "alternance", "stage", "maitrise de",
    "bonne connaissance", "connaissance approfondie", "experience souhaitee",
    "contexte du poste", "responsabilites", "remuneration", "titulaire d'un",
    "autonomie", "esprit d'equipe", "lettre de motivation", "postuler",
    "poste a pourvoir"
]


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def is_french_job(job: Any) -> bool:
    """Determine whether a job posting is dedicated to France / the French market or requires French.
    
    Supports both dict payloads and SQLAlchemy JobPosting ORM models.
    Checks location, title, country, source board, and description keywords with accent normalization.
    """
    if not job:
        return False

    # 1. Location text check
    location_norm = _fold_accents(_get_field(job, "location"))
    if location_norm:
        loc_tokens = re.split(r"[,/|\-\s\(\)]+", location_norm)
        if any(tok in FRENCH_LOCATIONS for tok in loc_tokens if len(tok) >= 2):
            return True
        if any(loc in location_norm for loc in ("france", "paris", "lyon", "bordeaux", "nantes", "toulouse", "lille", "strasbourg", "rennes", "ile-de-france", "marseille", "nice")):
            return True

    # 2. Country code check
    country_norm = _fold_accents(_get_field(job, "country")).strip()
    if country_norm in ("france", "fr", "fra"):
        return True

    # 3. Title check for French role designations
    title_norm = _fold_accents(_get_field(job, "title"))
    if any(kw in title_norm for kw in ("developpeur", "developpeuse", "ingenieur", "ingenieure", "alternance", "cdd", "cdi", "chef de projet", "lead dev fr", "stage ")):
        return True

    # 4. Description content check for French language cues
    desc_norm = _fold_accents(_get_field(job, "raw_description") or _get_field(job, "description"))
    if desc_norm:
        # Direct French requirement cues
        french_req_phrases = (
            "francais obligatoire", "maitrise du francais", "niveau de francais",
            "french required", "fluent in french", "french is required",
            "langue francaise", "bilingue francais", "environnement francophone",
            "langue de travail : francais", "langue de travail: francais",
            "francais courant", "francais b2", "francais c1", "francais c2",
            "lettre de motivation en francais", "cv en francais"
        )
        if any(phrase in desc_norm for phrase in french_req_phrases):
            return True

        match_count = sum(1 for kw in FRENCH_KEYWORDS if kw in desc_norm)
        if match_count >= 2:
            return True

    # 5. Source site / platform check
    source_norm = _fold_accents(_get_field(job, "source_site")).strip()
    if any(s in source_norm for s in ("welcometothejungle", "frenchtech", "hellowork", "apec", "lesjeudis")):
        return True

    return False
