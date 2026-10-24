#!/usr/bin/env python3
"""CareerFlow — Domain-Agnostic Job Relevance and Verification Engine.

Evaluates, scores, classifies, deduplicates, and verifies job postings collected
from FreeHire, LinkedIn, and other job-search portals against a configurable candidate profile.

Supports all professional career domains (technology, finance, marketing, design, sales,
HR, operations, consulting, healthcare, education, engineering, etc.).

Key Capabilities:
  1. Profile-Driven Relevance Scoring:
     - Matches job title, description, skills, experience, and location against the active candidate profile.
     - Does not globally penalize non-technical domains; alignment is evaluated strictly against user preferences.
     - Prevents generic "intern" or "analyst" keywords from artificially shortlisting unrelated roles.
  2. Independent Eligibility & Verification:
     - Strict separation of relevance_score (0-100), eligibility_status, application_status, and verification_confidence.
     - Configurable graduation eligibility evaluation (e.g. 2027, 2026, or general).
     - Safeguards: unconfirmed batch remains unknown; vacancies never marked open without direct evidence.
  3. LinkedIn Detail Enrichment:
     - Retrieves full JobDetail via the supported LinkedIn CLI interface when available.
     - Feeds enriched description, seniority, and isActive flags into relevance engine.
  4. Advanced Deduplication & Metadata Merging:
     - Primary deduplication by normalized application URL.
     - Secondary match by normalized company and title.
     - Merges metadata: keeps richer descriptions, direct ATS links, union of skills/roles, tracks sources.
     - Prevents merging distinct roles at the same company or distinct requisition URLs.

Usage:
  # Evaluate a JSON list of jobs against default or active profile:
  python tools/relevance_engine.py evaluate --input search_results.json --format table

  # Evaluate with a custom candidate profile:
  python tools/relevance_engine.py evaluate --input search_results.json --profile config/finance_profile.json

  # Ingest into seen_jobs.json:
  python tools/relevance_engine.py ingest --input search_results.json --state job_scraper/seen_jobs.json
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Import canonical key generator and configuration
try:
    from tools.config import DEFAULT_PROFILE, PRODUCT_NAME, get_active_profile, load_profile
    from tools.job_key import make_key, slugify
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.config import DEFAULT_PROFILE, PRODUCT_NAME, get_active_profile, load_profile
    from tools.job_key import make_key, slugify

# Classification categories
HIGH_MATCH = "HIGH_MATCH"
POTENTIAL_MATCH = "POTENTIAL_MATCH"
LOW_MATCH = "LOW_MATCH"
NEEDS_VERIFICATION = "NEEDS_VERIFICATION"

# Shortlist Policy Tiers
SHORTLIST_READY = "SHORTLIST_READY"
VERIFICATION_QUEUE = "VERIFICATION_QUEUE"
EXCLUDED = "EXCLUDED"

# Verification Confidence Levels
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"

# Known ATS / direct employer hiring domains
KNOWN_ATS_DOMAINS = {
    "myworkdayjobs.com",
    "zohorecruit.in",
    "zohorecruit.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "taleo.net",
    "bamboohr.com",
    "icims.com",
    "workable.com",
    "jobvite.com",
    "rippling-ats.com",
}

# Known third-party aggregator / channel domains
THIRD_PARTY_DOMAINS = {
    "t.me",
    "telegram.me",
    "whatjobs.com",
    "freehire.me",
    "naukri.com",
    "foundit.in",
    "indeed.com",
    "glassdoor.com",
}

# URL query parameters used for tracking
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "source",
    "fbclid",
    "gclid",
    "trk",
    "trkinfo",
    "trackingid",
    "position",
    "pagenum",
}

LINKEDIN_CLI_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / ".agents"
    / "skills"
    / "linkedin-search"
    / "cli"
    / "src"
    / "cli.ts"
)

# Standard role domain synonym clusters for rich matching across careers
ROLE_DOMAIN_SYNONYMS = {
    "cybersecurity": [
        r"\bcyber\s*security\b", r"\binformation\s*security\b", r"\binfosec\b",
        r"\bsoc\b", r"\bvapt\b", r"\bpenetration\s*test(er|ing)?\b",
        r"\bapplication\s*security\b", r"\bappsec\b", r"\bcloud\s*security\b",
        r"\bnetwork\s*security\b", r"\bincident\s*response\b", r"\bmalware\b",
        r"\bthreat\s*intel(ligence)?\b", r"\bvulnerability\b", r"\bethical\s*hack(er|ing)?\b",
    ],
    "ai_engineering": [
        r"\bai\s*engineer\b", r"\bai\s*developer\b", r"\bai\s*agent\b",
        r"\bllm\b", r"\bmachine\s*learning\b", r"\bdeep\s*learning\b",
        r"\bnlp\b", r"\bcomputer\s*vision\b", r"\bgenai\b", r"\bgenerative\s*ai\b",
        r"\bdata\s*science\b", r"\bai\b",
    ],
    "software_engineering": [
        r"\bbackend\b", r"\bback-end\b", r"\bsoftware\s*engineer(ing)?\b",
        r"\bsoftware\s*developer(s)?\b", r"\bsoftware\s*development\b",
        r"\bapi\s*developer(s)?\b", r"\bpython\s*developer(s)?\b",
        r"\bfull\s*stack\b", r"\bfrontend\b", r"\bweb\s*developer(s)?\b",
    ],
    "finance": [
        r"\bfinance\b", r"\bfinancial\s*analyst\b", r"\binvestment\s*banking\b",
        r"\bvaluation\b", r"\baccounting\b", r"\baccountant\b",
        r"\bequity\s*research\b", r"\bcorporate\s*finance\b", r"\bportfolio\s*management\b",
    ],
    "marketing": [
        r"\bmarketing\b", r"\bdigital\s*marketing\b", r"\bcontent\s*marketing\b",
        r"\bsocial\s*media\b", r"\bseo\b", r"\bgrowth\s*marketing\b",
        r"\bbrand\s*management\b", r"\bcopywriter\b",
    ],
    "human_resources": [
        r"\bhuman\s*resources\b", r"\bhr\b", r"\btalent\s*acquisition\b",
        r"\brecruiter\b", r"\brecruiting\b", r"\bpeople\s*operations\b",
        r"\bpeople\s*partner\b",
    ],
    "sales": [
        r"\bsales\b", r"\bbusiness\s*development\b", r"\bbde\b",
        r"\baccount\s*executive\b", r"\bsdr\b", r"\bbdr\b",
        r"\bclient\s*relationship\b",
    ],
    "design": [
        r"\bproduct\s*designer\b", r"\bui[/-]?ux\b", r"\bux\s*designer\b",
        r"\bui\s*designer\b", r"\bgraphic\s*designer\b", r"\bvisual\s*designer\b",
        r"\binteraction\s*design\b", r"\bfigma\b",
    ],
    "healthcare": [
        r"\bhealthcare\b", r"\bclinical\s*research\b", r"\bnursing\b",
        r"\bhealth\s*informatics\b", r"\bbiomedical\b", r"\bmedical\s*affairs\b",
        r"\bpharmaceutical\b",
    ],
    "consulting": [
        r"\bconsultant\b", r"\bconsulting\b", r"\bmanagement\s*consulting\b",
        r"\bstrategy\b", r"\badvisory\b",
    ],
    "operations": [
        r"\boperations\b", r"\bsupply\s*chain\b", r"\blogistics\b",
        r"\bprocurement\b", r"\bops\s*analyst\b",
    ],
    "data_science": [
        r"\bdata\s*scientist\b", r"\bdata\s*analyst\b", r"\bdata\s*analytics\b",
        r"\bdata\s*engineer\b", r"\bstatistics\b", r"\bbi\s*analyst\b",
    ],
}

# Seniority level patterns
SENIORITY_INTERN_KEYWORDS = [
    r"\bintern(ship)?\b",
    r"\btrainee\b",
    r"\bapprentice\b",
    r"\bgraduate\s*engineer\s*trainee\b",
    r"\bgraduate\s*trainee\b",
    r"\bfresher\b",
    r"\bentry\s*level\b",
    r"\bjunior\b",
    r"\bassociate\b",
    r"\bstudent\b",
    r"\bcampus\b",
]

DISQUALIFYING_SENIORITY_KEYWORDS = [
    r"\bsenior\s*vice\s*president\b",
    r"\bvice\s*president\b",
    r"\bsvp\b",
    r"\bvp\b",
    r"\bdirector\b",
    r"\bhead\s*of\b",
    r"\bprincipal\s*(engineer|architect|consultant|analyst)?\b",
    r"\bchief\b",
    r"\bciso\b",
    r"\bcto\b",
    r"\bcfo\b",
    r"\bcoo\b",
    r"\bsenior\s*manager\b",
    r"\bgeneral\s*manager\b",
    r"\bpractice\s*lead\b",
    r"\barchitect\b",
]

# Baseline skill patterns with regex boundaries
SKILL_PATTERNS = {
    "python": r"\bpython\b",
    "linux": r"\blinux\b|\bunix\b|\bubuntu\b|\bkali\b",
    "networking": r"\bnetworking\b|\btcp[/-]?ip\b|\bdns\b|\bvlan\b|\brouting\b|\bosi\b|\bwireshark\b|\bnetwork\s*security\b",
    "burp_suite": r"\bburp(\s*suite)?\b",
    "nmap": r"\bnmap\b",
    "fastapi": r"\bfastapi\b",
    "apis": r"\brest(ful)?\s*api(s)?\b|\bapi(s)?\b|\bendpoints?\b",
    "sql": r"\bsql\b|\bpostgresql\b|\bmysql\b|\bdatabase\b",
    "ai_agents_llms": r"\bai\s*agents?\b|\bllm(s)?\b|\blangchain\b|\bllamaindex\b|\brag\b|\bprompt\s*engineering\b|\bmachine\s*learning\b|\bdeep\s*learning\b|\bgenai\b|\bgenerative\s*ai\b",
    "docker": r"\bdocker\b|\bcontainer(s|ization)?\b",
    "git": r"\bgit\b|\bgithub\b",
    "excel": r"\bexcel\b|\badvanced\s*excel\b|\bspreadsheets?\b",
    "financial_modeling": r"\bfinancial\s*model(l?ing)?\b|\bdcf\b|\bvaluation\b",
    "seo": r"\bseo\b|\bsearch\s*engine\s*optimization\b",
    "figma": r"\bfigma\b|\bwirefram(ing|es)?\b|\bprototyp(ing|es)?\b",
    "clinical_trials": r"\bclinical\s*trials?\b|\bgcp\b|\bich[- ]gcp\b",
    "vapt": r"\bvapt\b|\bvulnerability\s*assessment\b|\bpenetration\s*test(ing|er)?\b",
    "recruiting": r"\brecruit(ing|ment|er|ers)?\b|\bscreen(ing)?\s*(applicants?|resumes?|candidates?)\b|\binterview(s|ing)?\b|\btalent\s*acquisition\b",
    "talent_sourcing": r"\btalent\s*sourc(ing|e)\b|\bsourc(ing)?\s*candidates\b|\bcandidate\s*pipeline(s)?\b",
    "onboarding": r"\bonboard(ing)?\b|\borientation\b",
}

# Generic / umbrella role terms that should not be scored as specialized target roles
# when the candidate's profile specifies distinct domain specializations
GENERIC_BROAD_ROLES = {
    "software engineer",
    "software engineering",
    "software developer",
    "software development",
    "engineer",
    "developer",
    "analyst",
    "intern",
    "trainee",
    "associate",
    "specialist",
    "consultant",
}



def normalize_url(url: str) -> str:
    """Normalize a URL by dropping tracking parameters and fragments."""
    if not url or not isinstance(url, str):
        return ""
    try:
        parsed = urlparse(url.strip())
        if not parsed.scheme or not parsed.netloc:
            return url.strip()
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        filtered_query = [
            (k, v) for k, v in parse_qsl(parsed.query)
            if k.lower() not in TRACKING_PARAMS
        ]
        new_query = urlencode(filtered_query)
        path = parsed.path.rstrip("/")
        normalized = urlunparse((parsed.scheme.lower(), netloc, path, "", new_query, ""))
        return normalized
    except Exception:
        return url.strip()


def parse_iso_date(value: str) -> date | None:
    """Defensive date parser: handles YYYY-MM-DD or ISO timestamp prefixes safely."""
    if not value or not isinstance(value, str):
        return None
    val = value.strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", val)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    return None


def extract_direct_application_url(description: str, original_url: str) -> tuple[str | None, str]:
    """Distinguish authentic employer application URLs from third-party aggregators."""
    orig_norm = normalize_url(original_url)
    orig_parsed = urlparse(orig_norm)
    orig_domain = orig_parsed.netloc.lower()

    is_third_party = any(t in orig_domain for t in THIRD_PARTY_DOMAINS)
    is_direct_ats = any(a in orig_domain for a in KNOWN_ATS_DOMAINS)

    if is_direct_ats:
        return orig_norm, "direct_ats"

    if description:
        found_urls = re.findall(r"https?://[^\s<>\"'()]+", description)
        for u in found_urls:
            u_norm = normalize_url(u)
            u_parsed = urlparse(u_norm)
            u_domain = u_parsed.netloc.lower()
            if any(a in u_domain for a in KNOWN_ATS_DOMAINS) or (
                "careers" in u_norm.lower() and not any(t in u_domain for t in THIRD_PARTY_DOMAINS)
            ):
                return u_norm, "extracted_from_description"

    if is_third_party:
        return None, "third_party_aggregator"

    return orig_norm, "direct_employer"


def fetch_linkedin_detail(job_id_or_url: str, cli_path: Path | None = None) -> dict | None:
    """Fetch full job detail from LinkedIn using the official linkedin-search CLI."""
    script = cli_path or LINKEDIN_CLI_SCRIPT
    if not script.is_file():
        return None

    m = re.search(r"(\d{6,})", str(job_id_or_url))
    if not m:
        return None
    job_id = m.group(1)

    try:
        proc = subprocess.run(
            ["bun", "run", str(script), "detail", job_id, "--format", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=25,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        if isinstance(data, dict) and "title" in data:
            return data
        return None
    except Exception:
        return None


def enrich_job_with_linkedin_detail(
    job: dict,
    cli_path: Path | None = None,
    profile: dict | None = None,
) -> dict:
    """Enrich a LinkedIn search result with full job detail and re-evaluate against profile."""
    job_id = str(job.get("id") or job.get("url") or job.get("source_url") or "")
    detail = fetch_linkedin_detail(job_id, cli_path=cli_path)
    if not detail:
        res = evaluate_job(job, profile=profile)
        res["classification"] = NEEDS_VERIFICATION
        res["shortlist_tier"] = VERIFICATION_QUEUE
        res["verification_confidence"] = CONFIDENCE_LOW
        res["verification_notes"] = f"{res.get('verification_notes', '')}; LinkedIn detail retrieval unavailable/failed; retained in verification queue."
        return res

    enriched_raw = dict(job)
    if detail.get("description"):
        enriched_raw["description"] = detail["description"]
    if detail.get("seniority"):
        enriched_raw["seniority"] = detail["seniority"]
    if detail.get("employmentType"):
        enriched_raw["employment_type"] = detail["employmentType"]
    if "isActive" in detail:
        enriched_raw["isActive"] = detail["isActive"]
    if detail.get("location"):
        enriched_raw["location"] = detail["location"]
    if detail.get("title") and not enriched_raw.get("title"):
        enriched_raw["title"] = detail["title"]
    if detail.get("company") and not enriched_raw.get("company"):
        enriched_raw["company"] = detail["company"]

    enriched = evaluate_job(enriched_raw, profile=profile)
    enriched["verification_source"] = "linkedin_detail"
    if detail.get("isActive") is False:
        enriched["application_status"] = "expired"
        enriched["classification"] = LOW_MATCH
        enriched["shortlist_tier"] = EXCLUDED
        enriched["verification_notes"] = "Excluded: Posting confirmed closed / expired by LinkedIn detail."
    return enriched


def evaluate_graduation_eligibility(
    text: str,
    target_year: int | None = 2027,
    require_match: bool = True,
) -> tuple[str, str, bool]:
    """Check graduation year eligibility dynamically based on target graduation year.

    Returns:
      (eligibility_status, note, has_conflicts)
    """
    if not require_match or target_year is None:
        return "eligible_general", "Eligibility not restricted by graduation year", False

    text_lower = text.lower()
    target_str = str(target_year)

    has_target = False
    if re.search(rf"\b{target_str}\b", text_lower):
        has_target = True

    # Pre-final year / Penultimate year / 3rd year students (applicable when target year is near graduation cycle)
    current_cycle = 2026
    if target_year in (current_cycle, current_cycle + 1, current_cycle + 2):
        if re.search(r"\b(pre[- ]final\s*year|penultimate\s*year|3rd\s*year|third\s*year)\b", text_lower):
            has_target = True

    # Check for exclusionary previous batches (prior to target_year)
    prev_batches = re.findall(rf"\b(202[0-{max(0, (target_year % 10) - 1)}])\s*(batch|pass\s*out|graduates?)\b", text_lower)

    # Conflicting statements
    if has_target and prev_batches:
        if re.search(rf"\b202\d\s*(to|-)\s*{target_str}\b", text_lower):
            return f"eligible_{target_year}", f"Eligible across multi-year graduation window including {target_year}", False
        return "conflicting_batch_dates", f"Conflicting batch statements (mentions {target_year} and {', '.join(b[0] for b in prev_batches)})", True

    if has_target:
        return f"eligible_{target_year}", f"Explicitly accepts {target_year} graduates / pre-final year students", False

    if prev_batches and not has_target:
        return "ineligible_batch", f"Restricted to earlier batches ({', '.join(b[0] for b in prev_batches)})", False

    # Explicit requirement for degree already completed
    if re.search(r"\bmust\s*have\s*completed\s*(degree|b\.?tech|graduation)\b", text_lower):
        return "ineligible_degree_completed", "Requires degree already completed (graduated students only)", False

    # Generic student / internship without batch specification
    if re.search(r"\b(currently\s*pursuing|enrolled\s*(in|student)|(college|university|enrolled|current)\s*student|b\.?tech\s*student)\b", text_lower):
        return "unspecified", "Requires enrolled student status, but exact graduation batch is unspecified", False

    return "unknown", "Graduation year eligibility not stated in posting", False


def compute_verification_confidence(
    description: str,
    source_url: str,
    app_url: str | None,
    url_type: str,
    eligibility_status: str,
    has_conflicts: bool,
) -> str:
    """Evaluate the strength of available evidence."""
    if url_type == "third_party_aggregator" or len(description) < 150 or has_conflicts:
        return CONFIDENCE_LOW
    if url_type in ("direct_ats", "extracted_from_description") and (
        eligibility_status.startswith("eligible_")
        or eligibility_status in ("ineligible_batch", "ineligible_senior", "ineligible_experienced")
    ):
        return CONFIDENCE_HIGH
    return CONFIDENCE_MEDIUM


def match_role_and_industry(
    title: str,
    description: str,
    preferred_roles: list[str],
    target_industries: list[str],
) -> tuple[int, list[str]]:
    """Score role alignment and extract matched labels based on candidate preferences.

    Returns:
      (score, matched_labels)
      - Specialized preferred role in title: 45 pts
      - Broad/generic preferred role in title: 35 pts (when candidate targets specialized roles)
      - Industry matched in title: 25 pts
      - Preferred role or industry in body: 20 pts
      - Conflicting title domain or no match: 0 pts
    """
    full_text = f"{title}\n{description}"
    title_lower = title.lower()
    full_lower = full_text.lower()

    matched_labels = []
    title_role_matched = False
    title_industry_matched = False
    body_matched = False
    is_specialized_title_match = False

    all_targets = preferred_roles + target_industries
    if not all_targets:
        return 20, ["General Opportunity"]

    # Detect domains the title belongs to
    title_domains = set()
    for domain, syns in ROLE_DOMAIN_SYNONYMS.items():
        if any(re.search(s, title_lower) for s in syns):
            title_domains.add(domain)

    # Detect domains candidate is targeting
    candidate_domains = set()
    for target in all_targets:
        target_norm = target.strip().lower()
        domain_key = re.sub(r"[^\w]", "_", target_norm).strip("_")
        for k in ROLE_DOMAIN_SYNONYMS:
            if k in domain_key or any(word in target_norm for word in k.split("_")):
                candidate_domains.add(k)

    # If title belongs explicitly to a known domain the candidate is not targeting,
    # passing mentions of candidate's industry in description do not qualify as a match
    is_conflicting_title_domain = bool(title_domains and not (title_domains & candidate_domains))
    if is_conflicting_title_domain:
        return 0, []

    # Check if candidate specified specialized roles in addition to generic broad roles
    has_specialized_roles = any(
        r.strip().lower() not in GENERIC_BROAD_ROLES
        for r in preferred_roles
    )

    def build_patterns(target_str: str) -> list[str]:
        target_norm = target_str.strip().lower()
        if not target_norm:
            return []
        patterns = []
        domain_key = re.sub(r"[^\w]", "_", target_norm).strip("_")
        for k, syns in ROLE_DOMAIN_SYNONYMS.items():
            if k in domain_key or any(word in target_norm for word in k.split("_")):
                patterns.extend(syns)

        core_role = re.sub(
            r"\s*(specialist|specialists|coordinator|coordinators|generalist|generalists|associate|associates|lead|leads|expert|experts|consultant|consultants|officer|officers|executive|executives)\s*$",
            "",
            target_norm,
        ).strip()

        def word_variant(w: str) -> str:
            if w.endswith("er") and len(w) > 4:
                return re.escape(w[:-2]) + r"(?:er|ers|ering|ing|ed|ment)?"
            elif w.endswith("or") and len(w) > 4:
                return re.escape(w[:-2]) + r"(?:or|ors|ing)?"
            elif w.endswith("ing") and len(w) > 5:
                return re.escape(w[:-3]) + r"(?:ing|er|ers)?"
            return re.escape(w) + r"s?"

        for phrase in (target_norm, core_role):
            if not phrase:
                continue
            words = [word_variant(w) for w in re.split(r"[^\w]+", phrase) if len(w) >= 2]
            if words:
                patterns.append(r"\b" + r"[\s-]*".join(words) + r"\b")
        return patterns

    # 1. Evaluate preferred roles
    for role in preferred_roles:
        r_patterns = build_patterns(role)
        if not r_patterns:
            continue
        t_match = any(re.search(p, title_lower) for p in r_patterns)
        b_match = any(re.search(p, full_lower) for p in r_patterns)
        if t_match:
            title_role_matched = True
            is_generic = role.strip().lower() in GENERIC_BROAD_ROLES
            if not is_generic:
                is_specialized_title_match = True
            if role not in matched_labels:
                matched_labels.append(role)
        elif b_match:
            body_matched = True
            if role not in matched_labels:
                matched_labels.append(role)

    # 2. Evaluate target industries
    for ind in target_industries:
        i_patterns = build_patterns(ind)
        if not i_patterns:
            continue
        t_match = any(re.search(p, title_lower) for p in i_patterns)
        b_match = any(re.search(p, full_lower) for p in i_patterns)
        if t_match:
            title_industry_matched = True
            if ind not in matched_labels:
                matched_labels.append(ind)
        elif b_match:
            body_matched = True
            if ind not in matched_labels:
                matched_labels.append(ind)

    if is_specialized_title_match:
        return 45, matched_labels
    elif title_role_matched:
        return (35 if has_specialized_roles else 45), matched_labels
    elif title_industry_matched:
        return 25, matched_labels
    elif body_matched:
        return 20, matched_labels

    return 0, []


def match_skills(
    full_text: str,
    skills_list: list[str],
    job_skills: list[str] | None = None,
) -> tuple[int, list[str]]:
    """Match candidate skills dynamically against posting text and structured tags.

    Max score: 25 pts.
    """
    if not skills_list:
        return 15, []

    # If description is missing/empty and no structured skills, skills cannot be confirmed
    if len(full_text.strip()) < 50 and not job_skills:
        return 0, []

    search_corpus = full_text
    if job_skills and isinstance(job_skills, list):
        search_corpus += " " + " ".join(str(s) for s in job_skills)

    matched = []
    for skill in skills_list:
        clean = skill.strip().lower()
        if not clean:
            continue
        if clean in SKILL_PATTERNS:
            pattern = SKILL_PATTERNS[clean]
        else:
            words = [re.escape(w) for w in re.split(r"[\s_-]+|[^\w]+", clean) if w]
            if not words:
                continue
            pattern = r"\b" + r"[\s_-]*".join(words) + r"\b"

        if re.search(pattern, search_corpus, re.IGNORECASE):
            matched.append(clean)

    target_count = max(1, min(5, len(skills_list)))
    score = min(25, round((len(matched) / target_count) * 25))
    return score, matched


def evaluate_job(job: dict, profile: dict | None = None) -> dict:
    """Evaluate, score, classify, and verify a single job record against candidate profile."""
    active_profile = profile or get_active_profile()

    title = str(job.get("title") or "").strip()
    employer = str(job.get("company") or job.get("company_slug") or job.get("employer") or "").strip()
    source_url = str(job.get("url") or job.get("source_url") or "").strip()
    location = str(job.get("location") or "").strip()
    work_mode = job.get("work_mode")
    listing_date = job.get("date") or job.get("posted_date") or job.get("posted_at")
    raw_deadline = job.get("deadline")
    description = str(job.get("description") or "").strip()
    portal = str(
        job.get("portal")
        or (
            "freehire-search"
            if "public_slug" in job or ("skills" in job and isinstance(job.get("skills"), list) and "company_slug" in job)
            else "linkedin-search"
        )
    )
    source = str(job.get("source") or portal or "cli")

    full_text = f"{title}\n{employer}\n{location}\n{description}"
    title_lower = title.lower()

    # 1. Experience Level & Seniority Disqualification Checks
    hard_penalties = []
    is_disqualified = False
    exp_levels = active_profile.get("experience_levels", ["internship", "entry_level"])
    wants_intern_or_entry = any(l in exp_levels for l in ("internship", "entry_level", "fresher"))

    # If candidate seeks internship/entry roles, executive/senior management is disqualified
    if wants_intern_or_entry:
        for pattern in DISQUALIFYING_SENIORITY_KEYWORDS:
            if re.search(pattern, title_lower):
                is_disqualified = True
                hard_penalties.append(f"Senior management / executive title matched '{pattern}'")
                break

        # Check substantial experience requirement (> 4 years)
        exp_match = re.search(r"\b([4-9]|[1-9]\d)\s*\+?\s*(years?|yrs?)\b(?:\s*of\s*experience)?", title_lower)
        if not exp_match and description:
            exp_match = re.search(
                r"\b([5-9]|[1-9]\d)\s*\+?\s*(years?|yrs?)\b(?:\s*of\s*experience)?",
                description[:600].lower(),
            )
        if exp_match:
            is_disqualified = True
            hard_penalties.append(f"Requires substantial experience ({exp_match.group(0)})")

    # 2. Positive Scoring Dimensions (Pure Alignment with Candidate Profile)
    relevance_breakdown = {
        "role": 0,
        "seniority": 0,
        "skills": 0,
        "location": 0,
        "compensation": 0,
        "freshness": 0,
    }

    # Role & Industry Match (up to 45 pts)
    preferred_roles = active_profile.get("preferred_roles", [])
    target_industries = active_profile.get("target_industries", [])
    role_score, matched_roles = match_role_and_industry(
        title=title,
        description=description,
        preferred_roles=preferred_roles,
        target_industries=target_industries,
    )
    relevance_breakdown["role"] = role_score

    # Seniority Match (up to 15 pts)
    title_is_intern = any(re.search(p, title_lower) for p in SENIORITY_INTERN_KEYWORDS)
    is_intern = any(re.search(p, full_text, re.IGNORECASE) for p in SENIORITY_INTERN_KEYWORDS)

    if wants_intern_or_entry:
        if is_disqualified:
            relevance_breakdown["seniority"] = 0
        elif title_is_intern:
            relevance_breakdown["seniority"] = 15
        elif is_intern:
            relevance_breakdown["seniority"] = 10
    else:
        # Non-intern preference (mid/senior/any)
        relevance_breakdown["seniority"] = 15

    # Skills Match (up to 25 pts)
    profile_skills = active_profile.get("skills", [])
    job_skills = job.get("skills") if isinstance(job.get("skills"), list) else None
    skills_score, matched_skills = match_skills(full_text, profile_skills, job_skills=job_skills)
    relevance_breakdown["skills"] = skills_score

    # Location & Work Mode Match (up to 5 pts)
    raw_locations = active_profile.get("locations")
    pref_locations = [loc.strip().lower() for loc in raw_locations if loc.strip()] if raw_locations is not None else []
    loc_lower = location.lower()
    work_mode_str = str(work_mode or "").lower()

    if not pref_locations:
        # Candidate is location-agnostic; award full location score
        relevance_breakdown["location"] = 5
    elif work_mode_str == "remote" or "remote" in full_text.lower():
        relevance_breakdown["location"] = 5
    elif any(ploc in loc_lower for ploc in pref_locations):
        relevance_breakdown["location"] = 5
    elif any(ploc in full_text.lower() for ploc in pref_locations):
        relevance_breakdown["location"] = 3

    # Compensation bonus (up to 3 pts)
    if re.search(r"\b(stipend|salary|inr|₹|\$|eur|£|per\s*month|lpa|compensation)\b", full_text, re.IGNORECASE):
        relevance_breakdown["compensation"] = 3

    # Freshness (up to 2 pts)
    if listing_date and isinstance(listing_date, str):
        parsed_post = parse_iso_date(listing_date)
        if parsed_post:
            days_old = (datetime.now(timezone.utc).date() - parsed_post).days
            if 0 <= days_old <= 14:
                relevance_breakdown["freshness"] = 2

    raw_score = sum(relevance_breakdown.values())

    # 3. Graduation Eligibility Check
    edu_conf = active_profile.get("education") or {}
    target_grad_year = edu_conf.get("graduation_year")
    require_grad_match = edu_conf.get("require_grad_year_match", False if target_grad_year is None else True)
    elig_status, elig_note, has_batch_conflicts = evaluate_graduation_eligibility(
        full_text,
        target_year=target_grad_year,
        require_match=require_grad_match,
    )

    # 4. Application Status & Dates
    app_url, url_type = extract_direct_application_url(description, source_url)

    parsed_deadline = parse_iso_date(raw_deadline) if raw_deadline else None
    deadline_has_conflict = False
    is_past_deadline = False
    if raw_deadline and not parsed_deadline:
        deadline_has_conflict = True
    elif parsed_deadline:
        if parsed_deadline < datetime.now(timezone.utc).date():
            is_past_deadline = True

    has_closed_text = bool(re.search(
        r"\b(no longer accepting applications|job (has )?closed|applications (are )?closed)\b",
        full_text,
        re.IGNORECASE,
    ))

    # Never mark open merely because a search API returned it
    if job.get("status") == "expired" or job.get("isActive") is False or is_past_deadline or has_closed_text:
        app_status = "expired"
    elif url_type == "third_party_aggregator":
        app_status = "third_party_only"
    elif job.get("application_status") == "open":
        app_status = "open"
    else:
        app_status = "unknown"

    # 5. Verification Confidence
    confidence = compute_verification_confidence(
        description=description,
        source_url=source_url,
        app_url=app_url,
        url_type=url_type,
        eligibility_status=elig_status,
        has_conflicts=has_batch_conflicts or deadline_has_conflict,
    )

    # 6. Classification & Shortlist Policy
    is_eligible = elig_status.startswith("eligible_") or elig_status == "eligible_general"
    seniority_satisfied = (title_is_intern or is_intern) if wants_intern_or_entry else True

    if is_disqualified:
        score = max(0, min(25, raw_score - 70))
        classification = LOW_MATCH
        shortlist_tier = EXCLUDED
        verification_notes = f"Excluded: {'; '.join(hard_penalties)}"
        if "Senior management" in verification_notes:
            elig_status = "ineligible_senior"
        elif "experience" in verification_notes:
            elig_status = "ineligible_experienced"
        else:
            elig_status = "unrelated_role"

    elif app_status == "expired":
        score = min(100, raw_score)
        classification = LOW_MATCH
        shortlist_tier = EXCLUDED
        verification_notes = "Excluded: Posting is confirmed closed or application deadline has passed."

    elif elig_status in ("ineligible_batch", "ineligible_degree_completed"):
        score = min(100, raw_score)
        classification = LOW_MATCH
        shortlist_tier = EXCLUDED
        verification_notes = f"Excluded: Ineligible graduation batch ({elig_note})."

    elif raw_score < 45:
        score = min(100, raw_score)
        classification = LOW_MATCH
        shortlist_tier = EXCLUDED
        verification_notes = f"Low match: Weak alignment with configured candidate profile (score: {score})."

    elif len(description) < 50:
        score = min(100, raw_score)
        classification = NEEDS_VERIFICATION
        shortlist_tier = VERIFICATION_QUEUE
        verification_notes = "Verification queue: Description is missing or incomplete; authentic employer posting details required before shortlisting."
        if require_grad_match and elig_status.startswith("eligible_"):
            elig_status = "unspecified"

    elif raw_score >= 65 and seniority_satisfied and is_eligible:
        score = min(100, raw_score)
        classification = HIGH_MATCH
        shortlist_tier = SHORTLIST_READY
        matched_str = ', '.join(matched_roles[:3]) if matched_roles else "target domain"
        verification_notes = f"Shortlist ready: Strong alignment with profile ({matched_str}), seniority satisfied, and verified eligibility."

    elif raw_score >= 60 and elig_status in ("unknown", "unspecified", "conflicting_batch_dates"):
        score = min(100, raw_score)
        classification = NEEDS_VERIFICATION
        shortlist_tier = VERIFICATION_QUEUE
        verification_notes = f"Verification queue: High technical suitability (score: {score}), but {elig_note.lower()}. Verify batch eligibility before applying."

    elif len(description) < 150 or url_type == "third_party_aggregator":
        score = min(100, raw_score)
        classification = NEEDS_VERIFICATION
        shortlist_tier = VERIFICATION_QUEUE
        verification_notes = "Verification queue: Incomplete snippet or third-party aggregator source; inspect authentic employer posting."

    else:
        score = min(100, raw_score)
        classification = POTENTIAL_MATCH
        shortlist_tier = SHORTLIST_READY if is_eligible else VERIFICATION_QUEUE
        verification_notes = f"Potential match: Relevant domain skills ({', '.join(matched_skills[:4])}), {elig_note.lower()}."

    verif_source = (
        "linkedin_detail"
        if job.get("verification_source") == "linkedin_detail"
        else (
            "freehire_api"
            if "public_slug" in job or "freehire" in source_url
            else ("linkedin_guest" if "linkedin" in source_url or portal == "linkedin-search" else "unknown")
        )
    )

    record = {
        "title": title,
        "employer": employer,
        "company": employer,
        "source": source,
        "portal": portal,
        "listing_date": listing_date,
        "posted_date": listing_date,
        "deadline": raw_deadline,
        "location": location,
        "work_mode": work_mode,
        "description": description,
        "source_url": source_url,
        "url": source_url,
        "application_url": app_url,
        "application_url_type": url_type,
        "relevance_score": score,
        "relevance_breakdown": relevance_breakdown,
        "classification": classification,
        "shortlist_tier": shortlist_tier,
        "matched_roles": matched_roles,
        "matched_skills": matched_skills,
        "eligibility_status": elig_status,
        "application_status": app_status,
        "verification_confidence": confidence,
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
        "verification_source": verif_source,
        "verification_notes": verification_notes,
    }

    record["key"] = make_key(employer, title, app_url or source_url)
    return record


def normalize_company_name(name: str) -> str:
    """Normalize company name by stripping common legal suffixes and punctuation."""
    if not name or not isinstance(name, str):
        return ""
    slug = slugify(name)
    for suffix in ("-inc", "-incorporated", "-corp", "-corporation", "-llc", "-ltd", "-limited", "-pvt-ltd", "-pvt"):
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
            break
    return slug.strip("-")


def are_duplicate_jobs(job_a: dict, job_b: dict) -> tuple[bool, str]:
    """Determine whether two jobs represent duplicate listings."""
    # 1. Primary match: Normalized application URL
    app_a = normalize_url(job_a.get("application_url") or "")
    app_b = normalize_url(job_b.get("application_url") or "")
    if app_a and app_b and app_a == app_b:
        return True, f"Identical application URL: {app_a}"

    src_a = normalize_url(job_a.get("source_url") or job_a.get("url") or "")
    src_b = normalize_url(job_b.get("source_url") or job_b.get("url") or "")
    type_a = job_a.get("application_url_type")
    type_b = job_b.get("application_url_type")

    if src_a and src_b and src_a == src_b:
        return True, f"Identical source URL: {src_a}"

    # 2. Secondary match: Company and Title normalization
    comp_a = normalize_company_name(job_a.get("employer") or job_a.get("company") or "")
    comp_b = normalize_company_name(job_b.get("employer") or job_b.get("company") or "")
    title_a = slugify(job_a.get("title") or "")
    title_b = slugify(job_b.get("title") or "")

    if comp_a and comp_b and comp_a == comp_b:
        # Different roles at the same company must NOT be merged
        if title_a != title_b:
            return False, f"Different roles at {comp_a} ({title_a} vs {title_b})"

        # If titles match, check if they have conflicting distinct direct application URLs
        if (
            app_a
            and app_b
            and app_a != app_b
            and type_a != "third_party_aggregator"
            and type_b != "third_party_aggregator"
        ):
            return False, f"Distinct direct application links for {comp_a}_{title_a} ({app_a} vs {app_b})"

        return True, f"Matching company and title ({comp_a}_{title_a})"

    return False, "Distinct company"


def merge_job_records(primary: dict, secondary: dict) -> dict:
    """Preserve useful metadata from both records without data loss."""
    merged = dict(primary)

    # 1. Preserve longer description
    desc_p = str(primary.get("description") or "")
    desc_s = str(secondary.get("description") or "")
    if len(desc_s) > len(desc_p):
        merged["description"] = desc_s

    # 2. Prefer authentic direct employer application link over third party
    type_p = primary.get("application_url_type")
    type_s = secondary.get("application_url_type")
    url_p = primary.get("application_url")
    url_s = secondary.get("application_url")
    if (not url_p or type_p == "third_party_aggregator") and (url_s and type_s != "third_party_aggregator"):
        merged["application_url"] = url_s
        merged["application_url_type"] = type_s

    # 3. Union of matched roles and skills
    roles = list(dict.fromkeys(primary.get("matched_roles", []) + secondary.get("matched_roles", [])))
    skills = list(dict.fromkeys(primary.get("matched_skills", []) + secondary.get("matched_skills", [])))
    merged["matched_roles"] = roles
    merged["matched_skills"] = skills

    # 4. Track all provenance sources
    sources = list(primary.get("sources") or [])
    for src in [primary.get("source"), primary.get("portal"), secondary.get("source"), secondary.get("portal")]:
        if src and src not in sources:
            sources.append(src)
    merged["sources"] = sources

    # 5. Eligibility: If secondary has confirmed eligibility and primary was unconfirmed, upgrade
    if primary.get("eligibility_status") in ("unknown", "unspecified") and str(secondary.get("eligibility_status", "")).startswith("eligible_"):
        merged["eligibility_status"] = secondary["eligibility_status"]
        merged["verification_notes"] = f"{primary.get('verification_notes', '')}; enriched eligibility from secondary source"

    # 6. Verification confidence: take the stronger confidence level
    conf_order = {CONFIDENCE_HIGH: 0, CONFIDENCE_MEDIUM: 1, CONFIDENCE_LOW: 2}
    conf_p = primary.get("verification_confidence", CONFIDENCE_LOW)
    conf_s = secondary.get("verification_confidence", CONFIDENCE_LOW)
    if conf_order.get(conf_s, 2) < conf_order.get(conf_p, 2):
        merged["verification_confidence"] = conf_s

    # 7. Relevance score: take the higher score
    if secondary.get("relevance_score", 0) > primary.get("relevance_score", 0):
        merged["relevance_score"] = secondary["relevance_score"]

    # 8. Re-evaluate shortlist tier and classification
    if merged.get("shortlist_tier") != EXCLUDED:
        if merged.get("relevance_score", 0) >= 70 and str(merged.get("eligibility_status", "")).startswith("eligible_"):
            merged["classification"] = HIGH_MATCH
            merged["shortlist_tier"] = SHORTLIST_READY
        elif merged.get("relevance_score", 0) >= 60 and merged.get("eligibility_status") in ("unknown", "unspecified"):
            merged["classification"] = NEEDS_VERIFICATION
            merged["shortlist_tier"] = VERIFICATION_QUEUE

    return merged


class RelevanceEngine:
    """Batch processor and deduplicator for job relevance and verification."""

    def __init__(
        self,
        seen_state_file: str | Path | None = None,
        linkedin_cli_path: Path | None = None,
        profile: dict | None = None,
    ):
        self.seen_state_file = Path(seen_state_file) if seen_state_file else None
        self.linkedin_cli_path = linkedin_cli_path
        self.profile = profile or get_active_profile()
        self.seen_data = {}
        if self.seen_state_file and self.seen_state_file.is_file():
            self._load_seen_state()

    def _load_seen_state(self):
        try:
            content = json.loads(self.seen_state_file.read_text(encoding="utf-8"))
            self.seen_data = content.get("seen", content) if isinstance(content, dict) else {}
        except Exception:
            self.seen_data = {}

    def deduplicate(self, jobs: list[dict]) -> tuple[list[dict], list[dict]]:
        """Deduplicate records by normalized application URL and secondary company/title matching."""
        unique: list[dict] = []
        duplicates: list[dict] = []

        for job in jobs:
            is_dup = False
            dup_reason = ""
            matched_idx = -1

            # Check against currently accepted unique jobs
            for idx, existing in enumerate(unique):
                dup_found, reason = are_duplicate_jobs(existing, job)
                if dup_found:
                    is_dup = True
                    dup_reason = reason
                    matched_idx = idx
                    break

            if is_dup and matched_idx >= 0:
                unique[matched_idx] = merge_job_records(unique[matched_idx], job)
                dup_record = dict(job)
                dup_record["duplicate_reason"] = dup_reason
                duplicates.append(dup_record)
                continue

            # Check against historical seen state if present
            if self.seen_data:
                for key, state_entry in self.seen_data.items():
                    dup_found, reason = are_duplicate_jobs(state_entry, job)
                    if dup_found:
                        is_dup = True
                        dup_reason = f"Historical seen state match: {reason}"
                        break

            if is_dup:
                dup_record = dict(job)
                dup_record["duplicate_reason"] = dup_reason
                duplicates.append(dup_record)
            else:
                unique.append(job)

        return unique, duplicates

    def evaluate_all(
        self,
        raw_jobs: list[dict],
        deduplicate: bool = True,
        enrich_linkedin: bool = False,
        profile: dict | None = None,
    ) -> dict:
        """Evaluate a collection of raw jobs, score, classify, enrich, and deduplicate."""
        active_prof = profile or self.profile or get_active_profile()
        evaluated = []
        for j in raw_jobs:
            portal_name = str(j.get("portal") or "")
            src_url = str(j.get("url") or j.get("source_url") or "")
            desc_len = len(str(j.get("description") or "").strip())

            if enrich_linkedin and (portal_name == "linkedin-search" or "linkedin.com" in src_url) and desc_len < 150:
                evaluated_job = enrich_job_with_linkedin_detail(j, cli_path=self.linkedin_cli_path, profile=active_prof)
            else:
                evaluated_job = evaluate_job(j, profile=active_prof)

            evaluated.append(evaluated_job)

        if deduplicate:
            unique, duplicates = self.deduplicate(evaluated)
        else:
            unique, duplicates = evaluated, []

        tier_order = {SHORTLIST_READY: 0, VERIFICATION_QUEUE: 1, EXCLUDED: 2}
        unique.sort(key=lambda x: (tier_order.get(x["shortlist_tier"], 9), -x["relevance_score"]))

        counts = {
            HIGH_MATCH: sum(1 for j in unique if j["classification"] == HIGH_MATCH),
            POTENTIAL_MATCH: sum(1 for j in unique if j["classification"] == POTENTIAL_MATCH),
            LOW_MATCH: sum(1 for j in unique if j["classification"] == LOW_MATCH),
            NEEDS_VERIFICATION: sum(1 for j in unique if j["classification"] == NEEDS_VERIFICATION),
        }
        tier_counts = {
            SHORTLIST_READY: sum(1 for j in unique if j["shortlist_tier"] == SHORTLIST_READY),
            VERIFICATION_QUEUE: sum(1 for j in unique if j["shortlist_tier"] == VERIFICATION_QUEUE),
            EXCLUDED: sum(1 for j in unique if j["shortlist_tier"] == EXCLUDED),
        }

        return {
            "meta": {
                "total_processed": len(raw_jobs),
                "unique_count": len(unique),
                "duplicate_count": len(duplicates),
                "counts_by_classification": counts,
                "counts_by_shortlist_tier": tier_counts,
            },
            "results": unique,
            "duplicates": duplicates,
        }

    def update_seen_state(self, jobs: list[dict]) -> int:
        """Persist evaluated jobs additively into seen_jobs.json."""
        if not self.seen_state_file:
            raise ValueError("seen_state_file must be specified to persist state.")

        doc = {"seen": self.seen_data}
        added_count = 0

        for j in jobs:
            key = j["key"]
            self.seen_data[key] = {
                "title": j["title"],
                "company": j["employer"],
                "url": j["source_url"],
                "first_seen": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "posted_date": j.get("posted_date"),
                "deadline": j.get("deadline"),
                "fit": "high" if j["classification"] == HIGH_MATCH else (
                    "medium" if j["classification"] in (POTENTIAL_MATCH, NEEDS_VERIFICATION) else "low"
                ),
                "status": "skipped" if j["shortlist_tier"] == EXCLUDED else "new",
                "portal": j["portal"],
                "source": j["source"],
                "sources": j.get("sources", [j["source"]]),
                "application_url": j.get("application_url"),
                "relevance_score": j["relevance_score"],
                "classification": j["classification"],
                "shortlist_tier": j["shortlist_tier"],
                "eligibility_status": j["eligibility_status"],
                "application_status": j["application_status"],
                "verification_confidence": j["verification_confidence"],
                "last_verified_at": j["last_verified_at"],
                "verification_source": j["verification_source"],
                "verification_notes": j["verification_notes"],
                "matched_roles": j.get("matched_roles", []),
                "matched_skills": j.get("matched_skills", []),
                "description": j.get("description", ""),
                "location": j.get("location", ""),
                "work_mode": j.get("work_mode", ""),
            }
            added_count += 1

        self.seen_state_file.parent.mkdir(parents=True, exist_ok=True)
        if self.seen_state_file.is_file():
            try:
                shutil.copy2(self.seen_state_file, self.seen_state_file.with_suffix(".json.bak"))
            except Exception:
                pass

        fd, tmp = tempfile.mkstemp(dir=str(self.seen_state_file.parent), prefix=".seen_jobs.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.seen_state_file)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return added_count


def render_table(results: list[dict]) -> str:
    """Render a clean summary table of evaluated jobs."""
    if not results:
        return "No results."
    headers = ["SCORE", "CLASSIFICATION", "SHORTLIST_TIER", "TITLE", "COMPANY", "ELIGIBILITY", "APP_STATUS", "CONF"]
    widths = [5, 18, 18, 30, 18, 16, 12, 6]
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep_line = "-" * len(header_line)
    rows = []
    for r in results:
        cols = [
            str(r.get("relevance_score", 0)).rjust(widths[0]),
            str(r.get("classification", "")).ljust(widths[1])[:widths[1]],
            str(r.get("shortlist_tier", "")).ljust(widths[2])[:widths[2]],
            str(r.get("title", "")).ljust(widths[3])[:widths[3]],
            str(r.get("employer", "")).ljust(widths[4])[:widths[4]],
            str(r.get("eligibility_status", "")).ljust(widths[5])[:widths[5]],
            str(r.get("application_status", "")).ljust(widths[6])[:widths[6]],
            str(r.get("verification_confidence", "")).ljust(widths[7])[:widths[7]],
        ]
        rows.append("  ".join(cols))
    return "\n".join([header_line, sep_line] + rows)


def main():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=f"{PRODUCT_NAME} — Domain-Agnostic Job Relevance & Verification Engine")
    subparsers = parser.add_subparsers(dest="command")

    eval_p = subparsers.add_parser("evaluate", help="Score and classify jobs from JSON or stdin")
    eval_p.add_argument("--input", "-i", default="-", help="Input JSON file with jobs array, or '-' for stdin")
    eval_p.add_argument("--output", "-o", help="Output JSON file path")
    eval_p.add_argument("--format", "-f", choices=["json", "table"], default="json", help="Output format (json|table)")
    eval_p.add_argument("--profile", "-p", help="Path to custom candidate profile JSON")
    eval_p.add_argument("--enrich-linkedin", action="store_true", help="Fetch LinkedIn details for snippet-only listings")
    eval_p.add_argument("--no-dedup", action="store_true", help="Disable deduplication")

    ingest_p = subparsers.add_parser("ingest", help="Score jobs and persist into seen_jobs.json")
    ingest_p.add_argument("--input", "-i", default="-", help="Input JSON file or '-' for stdin")
    ingest_p.add_argument("--state", "-s", default="job_scraper/seen_jobs.json", help="Path to seen_jobs.json")
    ingest_p.add_argument("--profile", "-p", help="Path to custom candidate profile JSON")
    ingest_p.add_argument("--enrich-linkedin", action="store_true", help="Fetch LinkedIn details before ingesting")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.input == "-" or not args.input:
        raw_text = sys.stdin.read()
    else:
        input_path = Path(args.input)
        if not input_path.is_file():
            sys.exit(f"Input file not found: {input_path}")
        raw_text = input_path.read_text(encoding="utf-8")

    try:
        raw_data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        sys.exit(f"Invalid JSON input: {exc}")

    jobs = raw_data.get("results", raw_data) if isinstance(raw_data, dict) else raw_data
    if not isinstance(jobs, list):
        sys.exit("Input JSON must contain an array of jobs or an object with a 'results' array.")

    custom_profile = load_profile(Path(args.profile)) if getattr(args, "profile", None) else None
    engine = RelevanceEngine(seen_state_file=getattr(args, "state", None), profile=custom_profile)

    if args.command == "evaluate":
        res = engine.evaluate_all(
            jobs,
            deduplicate=not args.no_dedup,
            enrich_linkedin=args.enrich_linkedin,
            profile=custom_profile,
        )
        if getattr(args, "format", "json") == "table":
            print(render_table(res["results"]))
            print(f"\nTotal: {res['meta']['total_processed']} | Unique: {res['meta']['unique_count']} | Discarded Duplicates: {res['meta']['duplicate_count']}")
            print(f"Counts: {res['meta']['counts_by_classification']}")
            print(f"Shortlist Tiers: {res['meta']['counts_by_shortlist_tier']}")
        else:
            out_json = json.dumps(res, indent=2, ensure_ascii=False)
            if args.output:
                Path(args.output).write_text(out_json + "\n", encoding="utf-8")
                print(f"Wrote evaluated jobs to {args.output}")
            else:
                print(out_json)

    elif args.command == "ingest":
        res = engine.evaluate_all(
            jobs,
            deduplicate=True,
            enrich_linkedin=args.enrich_linkedin,
            profile=custom_profile,
        )
        added = engine.update_seen_state(res["results"])
        print(f"Successfully ingested and verified {added} unique jobs into {args.state}")


if __name__ == "__main__":
    main()
