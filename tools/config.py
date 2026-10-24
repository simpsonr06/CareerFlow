#!/usr/bin/env python3
"""CareerFlow Configuration and Candidate Profile Management.

Centralizes platform branding, configuration defaults, and candidate profile
persistence for the domain-agnostic job discovery and verification engine.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Centralized product name - easily rebranded from this single constant
PRODUCT_NAME = "CareerFlow"

CONFIG_DIR = REPO_ROOT / "config"
PROFILE_PATH = CONFIG_DIR / "candidate_profile.json"

# Sensible default profile: broad technology and cybersecurity baseline,
# easily customized by any user via the dashboard or CLI.
DEFAULT_PROFILE: dict[str, Any] = {
    "product_name": PRODUCT_NAME,
    "candidate_name": "Default Candidate",
    "headline": "Technology & Security Graduate",
    "preferred_roles": [
        "Cybersecurity Analyst",
        "Security Engineer",
        "Information Security Intern",
        "SOC Analyst",
        "VAPT Analyst",
        "AI Engineer",
        "Backend Developer",
        "Software Engineer",
        "Python Developer",
    ],
    "target_industries": [
        "Cybersecurity",
        "Information Technology",
        "Artificial Intelligence",
        "Software Development",
        "Finance & FinTech",
    ],
    "skills": [
        "python",
        "linux",
        "networking",
        "burp_suite",
        "nmap",
        "fastapi",
        "apis",
        "sql",
        "ai_agents_llms",
        "docker",
        "git",
    ],
    "education": {
        "level": "Bachelor's Degree",
        "degree": "B.Tech / B.S. in Computer Science or related",
        "graduation_year": 2027,
        "require_grad_year_match": True,
    },
    "experience_levels": [
        "internship",
        "entry_level",
        "fresher",
    ],
    "locations": [
        "India",
        "Bangalore",
        "Bengaluru",
        "Noida",
        "Gurugram",
        "Gurgaon",
        "Hyderabad",
        "Pune",
        "Chennai",
        "Mumbai",
        "Delhi",
        "Remote",
    ],
    "work_arrangements": [
        "remote",
        "hybrid",
        "onsite",
    ],
    "employment_types": [
        "internship",
        "full_time",
    ],
    "min_salary": None,
    "scoring_weights": {
        "role": 45,
        "seniority": 25,
        "skills": 20,
        "location": 10,
        "compensation": 5,
        "freshness": 5,
    },
}


def load_profile(path: Path | None = None) -> dict[str, Any]:
    """Load candidate profile from disk or return default."""
    target_path = path or PROFILE_PATH
    if not target_path.is_file():
        return dict(DEFAULT_PROFILE)
    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_PROFILE)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_PROFILE)


def save_profile(profile_data: dict[str, Any], path: Path | None = None) -> None:
    """Save candidate profile to disk atomically."""
    target_path = path or PROFILE_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target_path.parent), prefix=".profile.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(profile_data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, target_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


_ACTIVE_PROFILE: dict[str, Any] | None = None


def get_active_profile() -> dict[str, Any]:
    """Get the currently active in-memory profile, loading from disk if necessary."""
    global _ACTIVE_PROFILE
    if _ACTIVE_PROFILE is None:
        _ACTIVE_PROFILE = load_profile()
    return _ACTIVE_PROFILE


def set_active_profile(profile_data: dict[str, Any], persist: bool = True) -> None:
    """Set the active candidate profile and optionally persist to disk."""
    global _ACTIVE_PROFILE
    _ACTIVE_PROFILE = dict(profile_data)
    if persist:
        save_profile(_ACTIVE_PROFILE)
