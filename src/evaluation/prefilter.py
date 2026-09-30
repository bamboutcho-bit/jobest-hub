"""Cheap deterministic relevance gate; sponsorship is never a hard discovery gate."""
import re
import requests
import numpy as np

from config.candidate_profile import CANDIDATE_PROFILE
from config.settings import settings


def _keywords() -> list[str]:
    return [x.strip().lower() for x in settings.visa_signal_keywords.split(",") if x.strip()]


_embedding_available: bool | None = None
_profile_embedding = None
_profile_embedding_attempted: bool = False


def _get_embedding(text: str, model: str = "nomic-embed-text") -> np.ndarray | None:
    global _embedding_available
    if _embedding_available is False:
        return None

    base_url = (settings.ollama_base_url or "http://ollama:11434").rstrip("/")
    url = f"{base_url}/api/embeddings"
    payload = {"model": model, "prompt": text}
    try:
        response = requests.post(url, json=payload, timeout=2)
        if response.status_code == 200:
            _embedding_available = True
            return np.array(response.json()["embedding"])
        _embedding_available = False
    except Exception:
        _embedding_available = False
    return None


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(np.dot(a, b) / norm)


def _get_profile_embedding() -> np.ndarray | None:
    global _profile_embedding, _profile_embedding_attempted
    if not _profile_embedding_attempted:
        _profile_embedding_attempted = True
        text = " ".join(CANDIDATE_PROFILE.get("target_titles", [])) + " " + \
               " ".join(CANDIDATE_PROFILE.get("core_stack", [])) + " " + \
               " ".join(CANDIDATE_PROFILE.get("keywords", []))
        _profile_embedding = _get_embedding(text)
    return _profile_embedding


def relevance_score(job: dict) -> int:
    text = " ".join(str(job.get(k) or "") for k in ("title", "company", "location", "raw_description")).lower()
    title = str(job.get("title") or "").lower()
    score = 0
    title_terms = {word.lower() for t in CANDIDATE_PROFILE.get("target_titles", []) for word in t.split() if len(word) > 2}
    score += min(40, sum(1 for word in title_terms if word in title) * 5)
    stack_terms = [str(x).lower().split()[0] for x in CANDIDATE_PROFILE.get("core_stack", [])]
    stack_terms += [str(x).lower() for x in CANDIDATE_PROFILE.get("keywords", [])]
    score += min(45, sum(1 for term in set(stack_terms) if term in text) * 4)
    if job.get("is_remote") or any(x in text for x in ("remote", "work from anywhere", "distributed team")):
        score += 8
    hard_penalties = [str(x).lower() for x in CANDIDATE_PROFILE.get("negative_keywords", [])]
    if any(term in title or term in text[:2000] for term in hard_penalties if term):
        score -= 35
    
    final_score = score
    
    prof_emb = _get_profile_embedding()
    if prof_emb is not None:
        # Embed the job text (first 3000 chars to avoid context limits)
        job_emb = _get_embedding(text[:3000])
        if job_emb is not None:
            sim = _cosine_similarity(prof_emb, job_emb)
            # Scale similarity: map 0.55-0.80 range to 0-100 points
            semantic_score = int(max(0, min(100, (sim - 0.55) * 400)))
            
            # Use semantic score to boost the deterministic score
            final_score = max(score, semantic_score)

    return max(0, min(100, final_score))


def visa_relocation_prefilter(job: dict) -> tuple[bool, list[str]]:
    """Return cheap signals, but never use sponsorship language as a hard AI gate.

    Discovery is intentionally broad: a legitimate local, junior, graduate, internship,
    pre-hire or remote job may contain no visa/relocation wording at all. Ollama gets the
    final fit decision after deterministic ranking.
    """
    score = relevance_score(job)
    text = " ".join(str(job.get(k) or "") for k in ("title", "company", "location", "raw_description")).lower()
    matches = [kw for kw in _keywords() if kw in text]
    if job.get("is_remote") or any(token in text for token in ("remote", "anywhere", "work from anywhere")):
        matches.append("remote_signal")
    if any(token in text for token in ("intern", "internship", "stage", "stagiaire", "graduate", "apprentice", "alternance", "pre-hire", "pre hire")):
        matches.append("early_career_signal")
    if score >= 60:
        matches.append(f"relevance_{score}")
    # Preserve the legacy optional hard prefilter only when explicitly enabled.
    # Normal operation keeps this disabled so sponsorship wording can never block
    # otherwise relevant local/remote/early-career jobs.
    if settings.claude_prefilter_enabled:
        return (score >= 50 or bool(matches)), matches
    return True, matches
