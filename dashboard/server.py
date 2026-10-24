#!/usr/bin/env python3
"""CareerFlow — Domain-Agnostic Job Search Dashboard FastAPI Server.

Local-first backend server for configurable career discovery and application tracking.
Manages discovered opportunities in seen_jobs.json, application tracking
in job_search_tracker.csv, candidate profile preferences in config/candidate_profile.json,
manual verification with audit logs, and on-demand search.

Security & Safeguards:
  - Binds strictly to 127.0.0.1 (local loopback only).
  - Explicit supporting evidence required before confirming eligibility or marking open.
  - Tracker status validated against canonical vocabulary.
  - CSV notes sanitized against delimiter injection (no unquoted commas/newlines).
  - Search command arguments strictly validated; no shell=True.
"""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure repo root is in python path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.config import (
    DEFAULT_PROFILE,
    PRODUCT_NAME,
    get_active_profile,
    load_profile,
    save_profile,
    set_active_profile,
)
from tools.relevance_engine import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    EXCLUDED,
    HIGH_MATCH,
    LOW_MATCH,
    NEEDS_VERIFICATION,
    POTENTIAL_MATCH,
    SHORTLIST_READY,
    VERIFICATION_QUEUE,
    RelevanceEngine,
)

# Paths
SEEN_JOBS_FILE = REPO_ROOT / "job_scraper" / "seen_jobs.json"
TRACKER_CSV_FILE = REPO_ROOT / "job_search_tracker.csv"
STATIC_DIR = Path(__file__).resolve().parent / "static"
LINKEDIN_CLI = REPO_ROOT / ".agents" / "skills" / "linkedin-search" / "cli" / "src" / "cli.ts"
FREEHIRE_CLI = REPO_ROOT / ".agents" / "skills" / "freehire-search" / "cli" / "src" / "cli.ts"

# Canonical Tracker Status Vocabulary
CANONICAL_TRACKER_STATUSES = {
    "drafted",
    "applied",
    "interview",
    "offer",
    "hired",
    "rejected",
    "no_response",
    "offer_declined",
    "withdrawn",
}

TRACKER_HEADER = [
    "date",
    "company",
    "sector",
    "role",
    "role_type",
    "channel",
    "status",
    "contact_person",
    "fit_rating",
    "notes",
    "cv_file",
    "cover_letter_file",
    "source",
    "deadline",
]

app = FastAPI(
    title=f"{PRODUCT_NAME} Job Discovery Platform",
    description=f"Local-first career discovery and application management platform.",
    version="2.0.0",
)

# Enable CORS for localhost access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Synchronization and rate-limiting guards
_seen_jobs_lock = threading.RLock()
_tracker_lock = threading.RLock()
_search_rate_lock = threading.Lock()
_last_search_timestamp: float = 0.0
SEARCH_RATE_LIMIT_INTERVAL = 1.0  # Minimum seconds between on-demand searches


# Helper functions
def load_seen_jobs() -> dict[str, dict]:
    """Load seen jobs from seen_jobs.json safely with backup fallback."""
    with _seen_jobs_lock:
        target = SEEN_JOBS_FILE
        if not target.is_file():
            bak = target.with_suffix(".json.bak")
            if bak.is_file():
                try:
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    return data.get("seen", data) if isinstance(data, dict) else {}
                except Exception:
                    return {}
            return {}
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return data.get("seen", data) if isinstance(data, dict) else {}
        except Exception:
            bak = target.with_suffix(".json.bak")
            if bak.is_file():
                try:
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    return data.get("seen", data) if isinstance(data, dict) else {}
                except Exception:
                    pass
            return {}


def save_seen_jobs(seen_data: dict[str, dict]) -> None:
    """Save seen jobs to seen_jobs.json atomically with fsync and backup."""
    with _seen_jobs_lock:
        SEEN_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if SEEN_JOBS_FILE.is_file():
            try:
                shutil.copy2(SEEN_JOBS_FILE, SEEN_JOBS_FILE.with_suffix(".json.bak"))
            except Exception:
                pass

        doc = {"seen": seen_data}
        fd, tmp = tempfile.mkstemp(dir=str(SEEN_JOBS_FILE.parent), prefix=".seen_jobs.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, SEEN_JOBS_FILE)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def sanitize_csv_field(val: Any) -> str:
    """Ensure no commas, newlines, or quotes break the tracker CSV format."""
    if val is None:
        return ""
    text = str(val).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    text = text.replace(",", ";").replace('"', "'")
    return text.strip()


def load_tracker_rows() -> list[dict]:
    """Read tracker CSV rows as dictionaries with backup fallback."""
    with _tracker_lock:
        target = TRACKER_CSV_FILE
        if not target.is_file():
            bak = target.with_suffix(".csv.bak")
            if bak.is_file():
                target = bak
            else:
                return []
        try:
            rows = []
            with target.open(encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                for r in reader:
                    rows.append(dict(r))
            return rows
        except Exception:
            bak = TRACKER_CSV_FILE.with_suffix(".csv.bak")
            if bak.is_file() and target != bak:
                try:
                    rows = []
                    with bak.open(encoding="utf-8-sig", newline="") as fh:
                        reader = csv.DictReader(fh)
                        for r in reader:
                            rows.append(dict(r))
                    return rows
                except Exception:
                    pass
            return []


def save_tracker_rows(rows: list[dict]) -> None:
    """Write tracker rows back to job_search_tracker.csv preserving header with atomic replace and backup."""
    with _tracker_lock:
        TRACKER_CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
        if TRACKER_CSV_FILE.is_file():
            try:
                shutil.copy2(TRACKER_CSV_FILE, TRACKER_CSV_FILE.with_suffix(".csv.bak"))
            except Exception:
                pass

        fd, tmp = tempfile.mkstemp(dir=str(TRACKER_CSV_FILE.parent), prefix=".tracker.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=TRACKER_HEADER)
                writer.writeheader()
                for r in rows:
                    clean_row = {k: sanitize_csv_field(r.get(k, "")) for k in TRACKER_HEADER}
                    writer.writerow(clean_row)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, TRACKER_CSV_FILE)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


# Request / Response Schemas
class CandidateProfileRequest(BaseModel):
    product_name: Optional[str] = PRODUCT_NAME
    candidate_name: Optional[str] = "Candidate"
    headline: Optional[str] = ""
    preferred_roles: list[str] = Field(default_factory=list)
    target_industries: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    education: Optional[dict[str, Any]] = Field(default_factory=dict)
    experience_levels: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    work_arrangements: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    min_salary: Optional[float] = None
    scoring_weights: Optional[dict[str, int]] = None


class VerifyRequest(BaseModel):
    key: str
    eligibility_status: Optional[str] = None
    application_status: Optional[str] = None
    evidence: str = Field(..., min_length=3, description="Explicit supporting evidence required")
    notes: Optional[str] = ""


class TrackerEntryRequest(BaseModel):
    company: str
    role: str
    status: str
    date: Optional[str] = None
    deadline: Optional[str] = None
    channel: Optional[str] = "direct"
    sector: Optional[str] = "Technology"
    role_type: Optional[str] = "full_time"
    contact_person: Optional[str] = ""
    fit_rating: Optional[str] = "Strong Fit"
    notes: Optional[str] = ""
    source: Optional[str] = ""


class SearchRequest(BaseModel):
    keywords: str = Field(default="software engineer", max_length=100)
    location: str = Field(default="Remote", max_length=100)
    portal: str = Field(default="all", pattern="^(all|linkedin|freehire)$")
    limit: int = Field(default=3, ge=1, le=5)
    enrich_linkedin: bool = True


# Profile Endpoints
@app.get("/api/profile")
def get_profile():
    """Retrieve the active candidate profile configuration."""
    profile = get_active_profile()
    return profile


@app.post("/api/profile")
def update_profile(req: CandidateProfileRequest):
    """Update and persist candidate preferences."""
    profile_dict = req.model_dump()
    profile_dict["product_name"] = PRODUCT_NAME
    set_active_profile(profile_dict, persist=True)
    return {
        "status": "success",
        "message": "Candidate profile updated successfully.",
        "profile": profile_dict,
    }


@app.get("/api/profile/search-presets")
def get_search_presets():
    """Generate dynamic search suggestions based on active candidate profile."""
    profile = get_active_profile()
    roles = profile.get("preferred_roles", [])
    locations = profile.get("locations", ["Remote"])
    primary_loc = locations[0] if locations else "Remote"

    presets = []
    wants_intern = "internship" in profile.get("experience_levels", [])

    for r in roles[:6]:
        term = f"{r} intern" if wants_intern and "intern" not in r.lower() else r
        presets.append({
            "label": term,
            "query": term,
            "location": primary_loc,
        })

    if not presets:
        presets = [
            {"label": "Software Engineer", "query": "software engineer", "location": primary_loc},
            {"label": "Product Manager", "query": "product manager", "location": primary_loc},
            {"label": "Data Analyst", "query": "data analyst", "location": primary_loc},
        ]

    return {
        "presets": presets,
        "primary_location": primary_loc,
    }


# Overview Endpoints
@app.get("/api/overview")
def get_overview():
    """Return high-level summary metrics across discovered jobs and applications."""
    seen = load_seen_jobs()
    tracker_rows = load_tracker_rows()
    profile = get_active_profile()

    total_discovered = len(seen)
    shortlist_ready = sum(1 for j in seen.values() if j.get("shortlist_tier") == SHORTLIST_READY)
    verification_queue = sum(1 for j in seen.values() if j.get("shortlist_tier") == VERIFICATION_QUEUE)
    excluded = sum(1 for j in seen.values() if j.get("shortlist_tier") == EXCLUDED)

    classifications = {
        HIGH_MATCH: sum(1 for j in seen.values() if j.get("classification") == HIGH_MATCH),
        POTENTIAL_MATCH: sum(1 for j in seen.values() if j.get("classification") == POTENTIAL_MATCH),
        NEEDS_VERIFICATION: sum(1 for j in seen.values() if j.get("classification") == NEEDS_VERIFICATION),
        LOW_MATCH: sum(1 for j in seen.values() if j.get("classification") == LOW_MATCH),
    }

    # Role breakdown
    roles_count: dict[str, int] = {}
    for j in seen.values():
        for r in j.get("matched_roles", []):
            roles_count[r] = roles_count.get(r, 0) + 1

    # Skills frequency
    skills_count: dict[str, int] = {}
    for j in seen.values():
        for s in j.get("matched_skills", []):
            skills_count[s] = skills_count.get(s, 0) + 1

    top_skills = sorted(skills_count.items(), key=lambda x: x[1], reverse=True)[:10]

    # Tracker status summary
    tracker_status_counts: dict[str, int] = {}
    for r in tracker_rows:
        st = r.get("status", "unknown").lower().replace(" ", "_")
        tracker_status_counts[st] = tracker_status_counts.get(st, 0) + 1

    edu = profile.get("education", {})
    target_grad_year = edu.get("graduation_year") if edu.get("require_grad_year_match") else None

    return {
        "product_name": PRODUCT_NAME,
        "metrics": {
            "total_discovered": total_discovered,
            "shortlist_ready": shortlist_ready,
            "verification_queue": verification_queue,
            "excluded": excluded,
            "total_tracked_applications": len(tracker_rows),
        },
        "profile_summary": {
            "headline": profile.get("headline", ""),
            "target_roles_count": len(profile.get("preferred_roles", [])),
            "target_industries_count": len(profile.get("target_industries", [])),
            "target_graduation_year": target_grad_year,
            "experience_levels": profile.get("experience_levels", []),
            "locations": profile.get("locations", []),
        },
        "classifications": classifications,
        "roles_breakdown": roles_count,
        "top_skills": top_skills,
        "tracker_status_counts": tracker_status_counts,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/jobs")
def get_jobs(
    q: Optional[str] = None,
    tier: Optional[str] = None,
    classification: Optional[str] = None,
    eligibility: Optional[str] = None,
    min_score: Optional[int] = None,
    max_score: Optional[int] = None,
    location: Optional[str] = None,
    portal: Optional[str] = None,
    sort_by: str = Query(default="relevance_score", pattern="^(relevance_score|first_seen|title|company)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    """Filter, search, and sort discovered job postings."""
    seen = load_seen_jobs()
    results = []

    q_lower = q.strip().lower() if q else None
    loc_lower = location.strip().lower() if location else None

    for key, job in seen.items():
        job_copy = dict(job)
        job_copy["key"] = key

        # Text query search
        if q_lower:
            text = f"{job.get('title', '')} {job.get('company', '')} {job.get('description', '')} {' '.join(job.get('matched_skills', []))}".lower()
            if q_lower not in text:
                continue

        # Shortlist tier filter
        if tier and tier.upper() != "ALL" and job.get("shortlist_tier") != tier.upper():
            continue

        # Classification filter
        if classification and classification.upper() != "ALL" and job.get("classification") != classification.upper():
            continue

        # Eligibility filter
        if eligibility and eligibility != "ALL":
            if eligibility == "eligible" and not str(job.get("eligibility_status", "")).startswith("eligible"):
                continue
            elif eligibility != "eligible" and job.get("eligibility_status") != eligibility:
                continue

        # Score filter
        score = job.get("relevance_score", 0)
        if min_score is not None and score < min_score:
            continue
        if max_score is not None and score > max_score:
            continue

        # Location filter
        if loc_lower and loc_lower not in str(job.get("location", "")).lower():
            continue

        # Portal filter
        if portal and portal.lower() != "all" and portal.lower() not in str(job.get("portal", "")).lower() and portal.lower() not in str(job.get("source", "")).lower():
            continue

        results.append(job_copy)

    # Sort
    reverse = (order == "desc")
    if sort_by == "relevance_score":
        results.sort(key=lambda x: (x.get("relevance_score") or 0), reverse=reverse)
    elif sort_by == "first_seen":
        results.sort(key=lambda x: str(x.get("first_seen") or ""), reverse=reverse)
    elif sort_by == "title":
        results.sort(key=lambda x: str(x.get("title") or "").lower(), reverse=reverse)
    elif sort_by == "company":
        results.sort(key=lambda x: str(x.get("company") or "").lower(), reverse=reverse)

    return {
        "count": len(results),
        "results": results,
    }


@app.get("/api/jobs/{key}")
def get_job_detail(key: str):
    """Retrieve full details of a specific job by key."""
    seen = load_seen_jobs()
    if key not in seen:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job with key '{key}' not found.")
    job = dict(seen[key])
    job["key"] = key
    return job


@app.post("/api/verify")
def verify_job(req: VerifyRequest):
    """Record manual verification with explicit evidence safeguards and audit trail."""
    with _seen_jobs_lock:
        seen = load_seen_jobs()
        if req.key not in seen:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job with key '{req.key}' not found.")

        job = seen[req.key]
        evidence_clean = req.evidence.strip()
        notes_clean = (req.notes or "").strip()

        if len(evidence_clean) < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Explicit supporting evidence is required and cannot be empty or whitespace-only.",
            )

        # Safeguard 1: Explicit evidence requirement when confirming eligibility
        if req.eligibility_status and req.eligibility_status.startswith("eligible_"):
            if len(evidence_clean) < 5:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Explicit supporting evidence is required to confirm candidate eligibility. Vague claims or generic titles alone are not sufficient.",
                )

        # Safeguard 2: Direct evidence requirement for open application status
        if req.application_status == "open":
            if len(evidence_clean) < 5:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Direct verifiable evidence (e.g. active ATS portal link verified) is required to mark a vacancy as open.",
                )

        now_iso = datetime.now(timezone.utc).isoformat()
        old_elig = job.get("eligibility_status", "unknown")
        old_app = job.get("application_status", "unknown")
        old_tier = job.get("shortlist_tier", VERIFICATION_QUEUE)

        # Update fields
        if req.eligibility_status:
            job["eligibility_status"] = req.eligibility_status
        if req.application_status:
            job["application_status"] = req.application_status

        job["verification_source"] = "manual_verification"
        job["last_verified_at"] = now_iso
        job["verification_confidence"] = CONFIDENCE_HIGH

        audit_entry = {
            "timestamp": now_iso,
            "action": "manual_verification",
            "previous_eligibility": old_elig,
            "new_eligibility": job["eligibility_status"],
            "previous_application_status": old_app,
            "new_application_status": job["application_status"],
            "previous_tier": old_tier,
            "evidence": evidence_clean,
            "notes": notes_clean,
        }

        # Re-evaluate shortlist tier based on updated eligibility and status
        score = job.get("relevance_score", 0)
        if job["application_status"] == "expired":
            job["classification"] = LOW_MATCH
            job["shortlist_tier"] = EXCLUDED
            job["verification_notes"] = f"Manually verified expired/closed: {evidence_clean}"
        elif str(job.get("eligibility_status", "")).startswith("eligible_") and score >= 70:
            job["classification"] = HIGH_MATCH
            job["shortlist_tier"] = SHORTLIST_READY
            job["verification_notes"] = f"Manually verified eligible: {evidence_clean}"
        elif job["eligibility_status"] in ("ineligible_batch", "ineligible_senior", "ineligible_experienced"):
            job["classification"] = LOW_MATCH
            job["shortlist_tier"] = EXCLUDED
            job["verification_notes"] = f"Manually verified ineligible: {evidence_clean}"
        else:
            job["verification_notes"] = f"Manual verification updated: {evidence_clean}"

        audit_entry["new_tier"] = job["shortlist_tier"]

        # Append to audit trail log
        log = job.get("manual_verification_log", [])
        log.append(audit_entry)
        job["manual_verification_log"] = log

        save_seen_jobs(seen)

        return {
            "status": "success",
            "key": req.key,
            "job": job,
            "audit_entry": audit_entry,
        }


@app.get("/api/tracker")
def get_tracker_applications():
    """Retrieve all tracked job applications from job_search_tracker.csv."""
    rows = load_tracker_rows()
    return {
        "count": len(rows),
        "applications": rows,
    }


@app.post("/api/tracker")
def create_or_update_tracker_entry(req: TrackerEntryRequest):
    """Add or update an application row in job_search_tracker.csv with canonical status validation."""
    if not req.company.strip() or not req.role.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company and role are required and cannot be empty or whitespace-only.",
        )

    clean_status = req.status.strip().lower().replace(" ", "_")
    if clean_status not in CANONICAL_TRACKER_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status '{req.status}'. Must be one of: {sorted(CANONICAL_TRACKER_STATUSES)}",
        )

    with _tracker_lock:
        rows = load_tracker_rows()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_val = req.date.strip() if req.date else today_str

        # Normalize company and role for upsert matching
        comp_norm = unicodedata.normalize("NFC", req.company.strip().casefold())
        role_norm = unicodedata.normalize("NFC", req.role.strip().casefold())

        match_idx = -1
        for idx, r in enumerate(rows):
            r_comp = unicodedata.normalize("NFC", str(r.get("company", "")).casefold())
            r_role = unicodedata.normalize("NFC", str(r.get("role", "")).casefold())
            if r_comp and r_comp == comp_norm and r_role and r_role == role_norm:
                match_idx = idx
                break

        row_data = {
            "date": date_val,
            "company": sanitize_csv_field(req.company),
            "sector": sanitize_csv_field(req.sector or "Technology"),
            "role": sanitize_csv_field(req.role),
            "role_type": sanitize_csv_field(req.role_type or "full_time"),
            "channel": sanitize_csv_field(req.channel or "direct"),
            "status": clean_status,
            "contact_person": sanitize_csv_field(req.contact_person or ""),
            "fit_rating": sanitize_csv_field(req.fit_rating or "Strong Fit"),
            "notes": sanitize_csv_field(req.notes or ""),
            "cv_file": "",
            "cover_letter_file": "",
            "source": sanitize_csv_field(req.source or ""),
            "deadline": sanitize_csv_field(req.deadline or ""),
        }

        if match_idx >= 0:
            row_data["cv_file"] = rows[match_idx].get("cv_file", "")
            row_data["cover_letter_file"] = rows[match_idx].get("cover_letter_file", "")
            rows[match_idx] = row_data
            action = "updated"
        else:
            rows.append(row_data)
            action = "created"

        save_tracker_rows(rows)

        return {
            "status": "success",
            "action": action,
            "entry": row_data,
        }


@app.post("/api/search")
def trigger_search(req: SearchRequest):
    """Run a small, rate-limit conscious search via portal CLIs and ingest into seen_jobs.json."""
    global _last_search_timestamp
    now = time.time()
    with _search_rate_lock:
        if now - _last_search_timestamp < SEARCH_RATE_LIMIT_INTERVAL:
            retry_after = max(0.1, round(SEARCH_RATE_LIMIT_INTERVAL - (now - _last_search_timestamp), 1))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: please wait {retry_after}s before triggering another live search.",
            )
        _last_search_timestamp = now

    query = re.sub(r"[^a-zA-Z0-9\s\-_]", "", req.keywords).strip()
    loc = re.sub(r"[^a-zA-Z0-9\s\-_,]", "", req.location).strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search keywords cannot be empty.")

    limit = min(5, max(1, req.limit))
    raw_results = []
    errors = []

    # LinkedIn Search
    if req.portal in ("all", "linkedin") and LINKEDIN_CLI.is_file():
        cmd = ["bun", "run", str(LINKEDIN_CLI), "search", "--location", loc or "Remote", "--query", query, "--limit", str(limit), "--format", "json"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=30)
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                items = data.get("results", data) if isinstance(data, dict) else data
                if isinstance(items, list):
                    for item in items:
                        item["portal"] = "linkedin-search"
                    raw_results.extend(items)
            else:
                errors.append(f"LinkedIn CLI returned non-zero ({proc.returncode}): {proc.stderr[:150]}")
        except Exception as exc:
            errors.append(f"LinkedIn search error: {str(exc)[:150]}")

    # FreeHire Search
    if req.portal in ("all", "freehire") and FREEHIRE_CLI.is_file():
        cmd = ["bun", "run", str(FREEHIRE_CLI), "search", "--query", query, "--limit", str(limit), "--format", "json"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=25)
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                items = data.get("results", data) if isinstance(data, dict) else data
                if isinstance(items, list):
                    for item in items:
                        item["portal"] = "freehire-search"
                    raw_results.extend(items)
            else:
                errors.append(f"FreeHire CLI returned non-zero ({proc.returncode}): {proc.stderr[:150]}")
        except Exception as exc:
            errors.append(f"FreeHire search error: {str(exc)[:150]}")

    if not raw_results:
        msg = f"Search portal error(s): {'; '.join(errors)}" if errors else "No results returned by search portals."
        return {
            "status": "warning" if errors else "no_results",
            "message": msg,
            "errors": errors,
            "added_count": 0,
            "results": [],
        }

    # Evaluate results using RelevanceEngine against active profile
    engine = RelevanceEngine(seen_state_file=SEEN_JOBS_FILE, profile=get_active_profile())
    eval_res = engine.evaluate_all(
        raw_results,
        deduplicate=True,
        enrich_linkedin=req.enrich_linkedin,
    )

    added_count = engine.update_seen_state(eval_res["results"])

    return {
        "status": "success",
        "total_fetched": len(raw_results),
        "unique_evaluated": eval_res["meta"]["unique_count"],
        "added_to_seen": added_count,
        "results": eval_res["results"],
        "errors": errors,
    }


@app.get("/api/audit-log")
def get_audit_log():
    """Retrieve audit trail of all manual verification actions."""
    seen = load_seen_jobs()
    entries = []
    for key, job in seen.items():
        logs = job.get("manual_verification_log", [])
        for entry in logs:
            e = dict(entry)
            e["job_key"] = key
            e["job_title"] = job.get("title", "")
            e["job_company"] = job.get("company", "")
            entries.append(e)

    entries.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {
        "count": len(entries),
        "entries": entries,
    }


# Mount Static Assets & Frontend
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(str(STATIC_DIR / "index.html"))


def run():
    """Entry point to run the server on localhost only."""
    import uvicorn

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")

    port = 8000
    host = "127.0.0.1"  # Local-only loopback; never 0.0.0.0
    print(f"Starting {PRODUCT_NAME} on http://{host}:{port}")
    uvicorn.run("dashboard.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
