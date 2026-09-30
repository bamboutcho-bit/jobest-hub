"""Candidate profile and keyword package."""
from src.candidate.profile_manager import (
    get_active_profile,
    list_profiles,
    get_profile_by_id,
    create_profile,
    update_profile,
    set_active_profile,
    delete_profile,
    generate_search_matrix,
    generate_freelance_queries,
)

__all__ = [
    "get_active_profile",
    "list_profiles",
    "get_profile_by_id",
    "create_profile",
    "update_profile",
    "set_active_profile",
    "delete_profile",
    "generate_search_matrix",
    "generate_freelance_queries",
]
