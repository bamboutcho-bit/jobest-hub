"""Candidate profile and dynamic discovery matrix.

Backed by the database-driven CandidateProfile engine in src/candidate/profile_manager.py.
Changes made in the dashboard or DB immediately reflect here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_DISCOVERY_SITES = [
    "linkedin", "indeed", "glassdoor", "google", "zip_recruiter", "bayt"
]

CANDIDATE_PROFILE_FALLBACK = {
    "name": "",
    "current_location": "",
    "target_locations": [],
    "target_titles": [],
    "core_stack": [],
    "current_roles": [],
    "languages": {},
    "visa_requirement": "",
    "resume_text": "",
    "resume_path": "",
}

SEARCH_MATRIX_FALLBACK: list[dict[str, Any]] = []


class _DynamicProfileDict(dict):
    """Dynamic dict proxy reflecting the active CandidateProfile from DB."""

    def _current(self) -> dict[str, Any]:
        try:
            from src.candidate.profile_manager import get_active_profile
            cur = get_active_profile()
            if cur and cur.get("name"):
                merged = dict(CANDIDATE_PROFILE_FALLBACK)
                merged.update(cur)
                return merged
        except Exception:
            pass
        return CANDIDATE_PROFILE_FALLBACK

    def __getitem__(self, key):
        return self._current().get(key)

    def get(self, key, default=None):
        return self._current().get(key, default)

    def __contains__(self, key):
        return key in self._current()

    def keys(self):
        return self._current().keys()

    def values(self):
        return self._current().values()

    def items(self):
        return self._current().items()

    def __iter__(self):
        return iter(self._current())

    def __len__(self):
        return len(self._current())

    def copy(self):
        return dict(self._current())

    def __repr__(self):
        return repr(self._current())


class _DynamicSearchMatrixList(list):
    """Dynamic list proxy reflecting the generated search matrix for the active profile."""

    def _current(self) -> list[dict[str, Any]]:
        try:
            from src.candidate.profile_manager import generate_search_matrix
            mat = generate_search_matrix()
            if mat:
                return mat
        except Exception:
            pass
        return SEARCH_MATRIX_FALLBACK

    def __getitem__(self, index):
        return self._current()[index]

    def __len__(self):
        return len(self._current())

    def __iter__(self):
        return iter(self._current())

    def copy(self):
        return list(self._current())

    def __repr__(self):
        return repr(self._current())


class _DynamicFreelanceDict(dict):
    """Dynamic dict proxy reflecting freelance settings from active profile."""

    def _current(self) -> dict[str, Any]:
        try:
            from src.candidate.profile_manager import get_active_profile
            p = get_active_profile()
            if not p:
                p = {}
            return {
                "name": p.get("name", ""),
                "tagline": p.get("headline", ""),
                "services": p.get("freelance_services") or [],
                "core_stack": p.get("core_stack", []),
                "rates": {
                    "hourly_usd": p.get("freelance_hourly_usd", 0),
                    "daily_eur": p.get("freelance_daily_eur", 0),
                    "preferred_currency": p.get("freelance_currency", "EUR"),
                    "negotiable": True,
                    "note": "",
                },
                "portfolio_links": {
                    "github": "", "linkedin": "", "portfolio": "",
                },
                "typical_delivery": "",
                "availability": "",
                "languages": p.get("languages", {}),
                "location": p.get("current_location", ""),
            }
        except Exception:
            return {
                "name": "",
                "tagline": "",
                "services": [],
                "core_stack": [],
                "rates": {"hourly_usd": 0, "daily_eur": 0, "preferred_currency": "EUR"},
                "languages": {},
                "location": "",
            }

    def __getitem__(self, key):
        return self._current().get(key)

    def get(self, key, default=None):
        return self._current().get(key, default)

    def __contains__(self, key):
        return key in self._current()

    def keys(self):
        return self._current().keys()

    def values(self):
        return self._current().values()

    def items(self):
        return self._current().items()

    def __iter__(self):
        return iter(self._current())

    def __len__(self):
        return len(self._current())

    def copy(self):
        return dict(self._current())

    def __repr__(self):
        return repr(self._current())


class _DynamicFreelanceQueriesList(list):
    """Dynamic list proxy reflecting freelance search queries for the active profile."""

    def _current(self) -> list[dict[str, Any]]:
        try:
            from src.candidate.profile_manager import generate_freelance_queries
            return generate_freelance_queries()
        except Exception:
            return []

    def __getitem__(self, index):
        return self._current()[index]

    def __len__(self):
        return len(self._current())

    def __iter__(self):
        return iter(self._current())

    def copy(self):
        return list(self._current())

    def __repr__(self):
        return repr(self._current())


CANDIDATE_PROFILE = _DynamicProfileDict(CANDIDATE_PROFILE_FALLBACK)
SEARCH_MATRIX = _DynamicSearchMatrixList(SEARCH_MATRIX_FALLBACK)
FREELANCE_PROFILE = _DynamicFreelanceDict()
FREELANCE_SEARCH_QUERIES = _DynamicFreelanceQueriesList()
SUPPORTED_SITES = DEFAULT_DISCOVERY_SITES

