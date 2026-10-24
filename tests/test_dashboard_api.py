"""Tests for dashboard/server.py — Phase 4 Job Search Dashboard API & Quality Safeguards.

Tests cover:
  1. Overview metrics, role breakdowns, and pipeline stats.
  2. Job Explorer filtering (text query, tier, classification, score, sorting).
  3. Job detail retrieval and 404 handling.
  4. Explicit evidence safeguards: setting eligible_2027 rejects empty/vague evidence.
  5. Application status safeguards: marking open rejects empty/unverified evidence.
  6. Audit trail persistence for manual verification changes.
  7. Application tracker validation against canonical status vocabulary.
  8. Tracker CSV delimiter sanitization (commas, quotes, newlines safe).
  9. Search input validation, rate limit caps (max 5), and safe execution.
"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from dashboard.server import app, CANONICAL_TRACKER_STATUSES, sanitize_csv_field
import dashboard.server as server_module

SAMPLE_SEEN_DATA = {
    "seen": {
        "tsaaro_cybersecurity-intern": {
            "title": "Cybersecurity Intern",
            "company": "Tsaaro",
            "url": "https://tsaaro.com/jobs/1",
            "first_seen": "2026-10-15",
            "relevance_score": 85,
            "classification": "NEEDS_VERIFICATION",
            "shortlist_tier": "VERIFICATION_QUEUE",
            "eligibility_status": "unknown",
            "application_status": "unknown",
            "verification_confidence": "MEDIUM",
            "matched_roles": ["Cybersecurity"],
            "matched_skills": ["vapt", "linux", "python", "burp_suite"],
            "description": "Tsaaro is hiring a Cyber Security Intern for security assessments and VAPT.",
            "location": "Noida, India",
            "verification_notes": "Verification queue: High technical suitability, batch unconfirmed.",
        },
        "discover-dollar_ai-engineer-intern": {
            "title": "AI Engineer Intern",
            "company": "Discover Dollar",
            "url": "https://discoverdollar.com/jobs/2",
            "first_seen": "2026-10-16",
            "relevance_score": 90,
            "classification": "HIGH_MATCH",
            "shortlist_tier": "SHORTLIST_READY",
            "eligibility_status": "eligible_2027",
            "application_status": "unknown",
            "verification_confidence": "HIGH",
            "matched_roles": ["AI Engineering"],
            "matched_skills": ["python", "fastapi", "llms"],
            "description": "AI Engineer Intern for 2027 batch graduates building LLM agents.",
            "location": "Bengaluru, India",
            "verification_notes": "Shortlist ready: Verified 2027 eligibility.",
        },
        "nab_svp-cybersecurity": {
            "title": "Senior Vice President - Internal Audit (Cybersecurity)",
            "company": "nab",
            "url": "https://nab.com/jobs/3",
            "first_seen": "2026-10-14",
            "relevance_score": 15,
            "classification": "LOW_MATCH",
            "shortlist_tier": "EXCLUDED",
            "eligibility_status": "ineligible_senior",
            "application_status": "unknown",
            "verification_confidence": "HIGH",
            "matched_roles": ["Cybersecurity"],
            "matched_skills": [],
            "description": "Executive leadership 15+ years experience required.",
            "location": "Gurugram, India",
            "verification_notes": "Excluded: Senior management.",
        },
    }
}


class TestDashboardAPI(unittest.TestCase):
    """Test suite for Phase 4 Dashboard Server API and Quality Safeguards."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self.seen_file = self.tmp_path / "seen_jobs.json"
        self.tracker_file = self.tmp_path / "job_search_tracker.csv"
        self.profile_file = self.tmp_path / "candidate_profile.json"

        # Initialize mock seen_jobs.json
        self.seen_file.write_text(json.dumps(SAMPLE_SEEN_DATA, indent=2), encoding="utf-8")

        # Patch paths in dashboard.server and tools.config
        import tools.config as config_module
        self.patcher1 = patch.object(server_module, "SEEN_JOBS_FILE", self.seen_file)
        self.patcher2 = patch.object(server_module, "TRACKER_CSV_FILE", self.tracker_file)
        self.patcher3 = patch.object(config_module, "PROFILE_PATH", self.profile_file)
        self.patcher1.start()
        self.patcher2.start()
        self.patcher3.start()

        config_module.set_active_profile(config_module.DEFAULT_PROFILE, persist=False)
        self.client = TestClient(app)

    def tearDown(self):
        import tools.config as config_module
        config_module.set_active_profile(config_module.DEFAULT_PROFILE, persist=False)
        self.patcher1.stop()
        self.patcher2.stop()
        self.patcher3.stop()
        self.tmpdir.cleanup()

    def test_overview_metrics_and_distributions(self):
        """GET /api/overview returns aggregated statistics and breakdowns."""
        res = self.client.get("/api/overview")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        m = data["metrics"]
        self.assertEqual(m["total_discovered"], 3)
        self.assertEqual(m["shortlist_ready"], 1)
        self.assertEqual(m["verification_queue"], 1)
        self.assertEqual(m["excluded"], 1)

        # Classifications
        c = data["classifications"]
        self.assertEqual(c["HIGH_MATCH"], 1)
        self.assertEqual(c["NEEDS_VERIFICATION"], 1)
        self.assertEqual(c["LOW_MATCH"], 1)

        # Role breakdown
        roles = data["roles_breakdown"]
        self.assertEqual(roles.get("Cybersecurity"), 2)
        self.assertEqual(roles.get("AI Engineering"), 1)

    def test_jobs_filter_by_query_tier_and_score(self):
        """GET /api/jobs filters correctly on query string, tier, and min_score."""
        # Query filter: "vapt"
        res_q = self.client.get("/api/jobs?q=vapt")
        self.assertEqual(res_q.status_code, 200)
        data_q = res_q.json()
        self.assertEqual(data_q["count"], 1)
        self.assertEqual(data_q["results"][0]["company"], "Tsaaro")

        # Tier filter: "SHORTLIST_READY"
        res_tier = self.client.get("/api/jobs?tier=SHORTLIST_READY")
        self.assertEqual(res_tier.status_code, 200)
        data_tier = res_tier.json()
        self.assertEqual(data_tier["count"], 1)
        self.assertEqual(data_tier["results"][0]["company"], "Discover Dollar")

        # Score filter: min_score=80
        res_score = self.client.get("/api/jobs?min_score=80")
        self.assertEqual(res_score.status_code, 200)
        self.assertEqual(res_score.json()["count"], 2)

    def test_get_job_detail_found_and_not_found(self):
        """GET /api/jobs/{key} returns job detail for valid keys, 404 for unknown keys."""
        res_ok = self.client.get("/api/jobs/tsaaro_cybersecurity-intern")
        self.assertEqual(res_ok.status_code, 200)
        self.assertEqual(res_ok.json()["title"], "Cybersecurity Intern")

        res_404 = self.client.get("/api/jobs/nonexistent_job_key")
        self.assertEqual(res_404.status_code, 404)

    def test_verification_safeguard_requires_explicit_evidence_for_2027(self):
        """POST /api/verify rejects setting eligible_2027 without explicit supporting evidence."""
        # Attempt with missing evidence -> 422 (Pydantic min_length validation) or 400
        res_empty = self.client.post("/api/verify", json={
            "key": "tsaaro_cybersecurity-intern",
            "eligibility_status": "eligible_2027",
            "evidence": "",
        })
        self.assertIn(res_empty.status_code, (400, 422))

        # Attempt with too short / trivial evidence ("ok") -> 400
        res_short = self.client.post("/api/verify", json={
            "key": "tsaaro_cybersecurity-intern",
            "eligibility_status": "eligible_2027",
            "evidence": "no",
        })
        self.assertIn(res_short.status_code, (400, 422))

        # Valid explicit evidence -> Success, upgrades to SHORTLIST_READY & HIGH_MATCH
        res_valid = self.client.post("/api/verify", json={
            "key": "tsaaro_cybersecurity-intern",
            "eligibility_status": "eligible_2027",
            "evidence": "Verified on official Zoho careers posting: explicitly states 2027 batch B.Tech eligible.",
            "notes": "Spoke to recruiter on LinkedIn.",
        })
        self.assertEqual(res_valid.status_code, 200)
        data = res_valid.json()
        job = data["job"]
        self.assertEqual(job["eligibility_status"], "eligible_2027")
        self.assertEqual(job["classification"], "HIGH_MATCH")
        self.assertEqual(job["shortlist_tier"], "SHORTLIST_READY")
        self.assertEqual(job["verification_source"], "manual_verification")

        # Verify audit log was recorded
        self.assertIn("manual_verification_log", job)
        self.assertEqual(len(job["manual_verification_log"]), 1)
        log_entry = job["manual_verification_log"][0]
        self.assertEqual(log_entry["previous_eligibility"], "unknown")
        self.assertEqual(log_entry["new_eligibility"], "eligible_2027")
        self.assertIn("Zoho careers posting", log_entry["evidence"])

    def test_verification_safeguard_requires_evidence_for_open_status(self):
        """POST /api/verify rejects marking a vacancy open without direct verifiable evidence."""
        res_invalid = self.client.post("/api/verify", json={
            "key": "tsaaro_cybersecurity-intern",
            "application_status": "open",
            "evidence": "hi",
        })
        self.assertIn(res_invalid.status_code, (400, 422))

        res_valid = self.client.post("/api/verify", json={
            "key": "tsaaro_cybersecurity-intern",
            "application_status": "open",
            "evidence": "Confirmed live application portal accepts submissions at https://tsaaro.zohorecruit.in/jobs/123",
        })
        self.assertEqual(res_valid.status_code, 200)
        self.assertEqual(res_valid.json()["job"]["application_status"], "open")

    def test_tracker_crud_validates_canonical_status(self):
        """POST /api/tracker enforces canonical status vocabulary and appends/updates CSV."""
        # Invalid status rejected
        res_invalid = self.client.post("/api/tracker", json={
            "company": "SecureNet",
            "role": "SOC Analyst",
            "status": "pending_review",  # Invalid!
        })
        self.assertEqual(res_invalid.status_code, 400)
        self.assertIn("Invalid status", res_invalid.json()["detail"])

        # Valid status creates entry
        res_create = self.client.post("/api/tracker", json={
            "company": "SecureNet",
            "role": "SOC Analyst",
            "status": "applied",
            "date": "2026-10-18",
            "deadline": "2026-11-01",
            "sector": "Cybersecurity",
            "notes": "Direct application via portal",
        })
        self.assertEqual(res_create.status_code, 200)
        self.assertEqual(res_create.json()["action"], "created")

        # GET /api/tracker returns the created entry
        res_get = self.client.get("/api/tracker")
        self.assertEqual(res_get.status_code, 200)
        apps = res_get.json()["applications"]
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["company"], "SecureNet")
        self.assertEqual(apps[0]["status"], "applied")

        # Updating the same company and role updates in place
        res_update = self.client.post("/api/tracker", json={
            "company": "SecureNet",
            "role": "SOC Analyst",
            "status": "interview",
            "notes": "Technical round scheduled",
        })
        self.assertEqual(res_update.status_code, 200)
        self.assertEqual(res_update.json()["action"], "updated")

        apps_updated = self.client.get("/api/tracker").json()["applications"]
        self.assertEqual(len(apps_updated), 1)
        self.assertEqual(apps_updated[0]["status"], "interview")

    def test_tracker_csv_delimiter_safety(self):
        """Sanitizes notes containing commas, quotes, and newlines to preserve CSV integrity."""
        unsafe_note = 'Round 1 passed, technical interview on Monday.\n"Great questions asked", referral from John.'
        sanitized = sanitize_csv_field(unsafe_note)
        self.assertNotIn(",", sanitized, "Commas must be replaced to prevent CSV column shifts")
        self.assertNotIn("\n", sanitized, "Newlines must be replaced to prevent broken rows")
        self.assertNotIn('"', sanitized, "Quotes must be replaced")

    def test_search_endpoint_limits_and_sanitizes_input(self):
        """POST /api/search caps limit at 5 and rejects empty keywords."""
        res_empty = self.client.post("/api/search", json={
            "keywords": "",
            "location": "India",
        })
        self.assertEqual(res_empty.status_code, 400)

        # Limit > 5 is capped to 5 via Pydantic validator
        res_overlimit = self.client.post("/api/search", json={
            "keywords": "cybersecurity",
            "limit": 10,
        })
        self.assertEqual(res_overlimit.status_code, 422)

    def test_audit_log_endpoint(self):
        """GET /api/audit-log retrieves historical audit entries across jobs."""
        # Record a verification first
        self.client.post("/api/verify", json={
            "key": "tsaaro_cybersecurity-intern",
            "eligibility_status": "eligible_2027",
            "evidence": "Direct email confirmation from HR team for 2027 batch.",
        })

        res = self.client.get("/api/audit-log")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertGreaterEqual(data["count"], 1)
        self.assertEqual(data["entries"][0]["job_company"], "Tsaaro")
        self.assertIn("HR team", data["entries"][0]["evidence"])

    def test_profile_get_and_update_endpoints(self):
        """GET /api/profile and POST /api/profile retrieve and update candidate profile."""
        res_get = self.client.get("/api/profile")
        self.assertEqual(res_get.status_code, 200)
        profile_data = res_get.json()
        self.assertEqual(profile_data.get("product_name"), "CareerFlow")

        # Update candidate profile
        update_payload = {
            "candidate_name": "Jordan Lee",
            "headline": "FinTech & Quantitative Analyst",
            "preferred_roles": ["Quantitative Analyst", "Financial Engineer"],
            "target_industries": ["Finance & FinTech", "Hedge Funds"],
            "skills": ["python", "financial_modeling", "statistics", "sql"],
            "locations": ["Mumbai", "Remote"],
            "experience_levels": ["entry_level", "internship"],
            "education": {"graduation_year": 2026, "require_grad_year_match": True},
        }
        res_post = self.client.post("/api/profile", json=update_payload)
        self.assertEqual(res_post.status_code, 200)
        self.assertEqual(res_post.json()["status"], "success")

        # Verify active profile updated
        res_updated = self.client.get("/api/profile")
        self.assertEqual(res_updated.json()["candidate_name"], "Jordan Lee")
        self.assertEqual(res_updated.json()["preferred_roles"], ["Quantitative Analyst", "Financial Engineer"])

    def test_search_presets_endpoint(self):
        """GET /api/profile/search-presets returns dynamic queries derived from candidate profile."""
        res = self.client.get("/api/profile/search-presets")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("presets", data)
        self.assertGreaterEqual(len(data["presets"]), 1)
        self.assertIn("query", data["presets"][0])
        self.assertIn("location", data["presets"][0])

    def test_verification_safeguards_whitespace_only_evidence_rejected(self):
        """POST /api/verify rejects whitespace-only evidence even if length passes field check."""
        res = self.client.post("/api/verify", json={
            "key": "tsaaro_cybersecurity-intern",
            "eligibility_status": "eligible_2027",
            "evidence": "     ",
        })
        self.assertIn(res.status_code, (400, 422))

    def test_verification_updates_ineligible_and_expired(self):
        """POST /api/verify reclassifies jobs as EXCLUDED when marked ineligible or expired."""
        # Mark as ineligible_batch
        res1 = self.client.post("/api/verify", json={
            "key": "discover-dollar_ai-engineer-intern",
            "eligibility_status": "ineligible_batch",
            "evidence": "Careers portal states only 2025 and 2026 graduates may apply.",
        })
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.json()["job"]["shortlist_tier"], "EXCLUDED")
        self.assertEqual(res1.json()["job"]["classification"], "LOW_MATCH")

        # Mark as expired
        res2 = self.client.post("/api/verify", json={
            "key": "tsaaro_cybersecurity-intern",
            "application_status": "expired",
            "evidence": "Application window closed on October 15th as noted on posting.",
        })
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["job"]["shortlist_tier"], "EXCLUDED")

    def test_verify_nonexistent_job_returns_404(self):
        """POST /api/verify for unknown job key returns 404."""
        res = self.client.post("/api/verify", json={
            "key": "completely_unknown_key",
            "eligibility_status": "eligible_2027",
            "evidence": "Valid evidence text for unknown job",
        })
        self.assertEqual(res.status_code, 404)

    def test_tracker_empty_company_or_role_rejected(self):
        """POST /api/tracker rejects blank company or role."""
        res_empty_comp = self.client.post("/api/tracker", json={
            "company": "   ",
            "role": "Security Analyst",
            "status": "applied",
        })
        self.assertIn(res_empty_comp.status_code, (400, 422))

        res_empty_role = self.client.post("/api/tracker", json={
            "company": "SecureCorp",
            "role": "   ",
            "status": "applied",
        })
        self.assertIn(res_empty_role.status_code, (400, 422))

    def test_tracker_duplicate_upsert_idempotent(self):
        """Submitting duplicate company/role updates in place rather than creating duplicate rows."""
        payload1 = {
            "company": "NovaSec",
            "role": "Security Engineer",
            "status": "applied",
            "notes": "Application sent",
        }
        res1 = self.client.post("/api/tracker", json=payload1)
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.json()["action"], "created")

        # Same company with different casing / whitespace
        payload2 = {
            "company": "  novasec  ",
            "role": "security engineer",
            "status": "interview",
            "notes": "Screening call scheduled",
        }
        res2 = self.client.post("/api/tracker", json=payload2)
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["action"], "updated")

        tracker_data = self.client.get("/api/tracker").json()
        self.assertEqual(tracker_data["count"], 1)
        self.assertEqual(tracker_data["applications"][0]["status"], "interview")
        self.assertEqual(tracker_data["applications"][0]["notes"], "Screening call scheduled")

    def test_tracker_backup_resilience_and_recovery(self):
        """load_tracker_rows recovers from .bak file if primary CSV is corrupted."""
        self.client.post("/api/tracker", json={
            "company": "BackupTest Co",
            "role": "DevOps Intern",
            "status": "applied",
        })
        # Save again so backup exists
        self.client.post("/api/tracker", json={
            "company": "BackupTest Co",
            "role": "DevOps Intern",
            "status": "interview",
        })
        bak_file = self.tracker_file.with_suffix(".csv.bak")
        self.assertTrue(bak_file.is_file(), "Backup file should be created on save")

        # Corrupt the primary tracker CSV
        self.tracker_file.write_text("CORRUPTED_NON_CSV_HEADER_DATA_GARBAGE\n\x00\xff", encoding="latin-1")
        # Ensure load_tracker_rows recovers without raising
        rows = server_module.load_tracker_rows()
        self.assertIsInstance(rows, list)

    def test_seen_jobs_backup_resilience_and_recovery(self):
        """load_seen_jobs recovers from .bak file if primary seen_jobs.json is corrupted."""
        self.client.post("/api/verify", json={
            "key": "tsaaro_cybersecurity-intern",
            "eligibility_status": "eligible_2027",
            "evidence": "Verified batch 2027 in official careers notice.",
        })
        bak_file = self.seen_file.with_suffix(".json.bak")
        self.assertTrue(bak_file.is_file(), "seen_jobs backup should be created on save")

        # Corrupt primary seen_jobs.json
        self.seen_file.write_text("{this is not valid json!}", encoding="utf-8")
        seen = server_module.load_seen_jobs()
        self.assertIn("tsaaro_cybersecurity-intern", seen, "Should fallback to .bak on corrupted primary")

    def test_search_rate_limiting(self):
        """POST /api/search enforces rate limit between successive invocations."""
        with patch.object(server_module, "SEARCH_RATE_LIMIT_INTERVAL", 1.0):
            # First search
            res1 = self.client.post("/api/search", json={"keywords": "software engineer", "limit": 1})
            # Immediate second search within rate limit interval
            res2 = self.client.post("/api/search", json={"keywords": "software engineer", "limit": 1})
            self.assertEqual(res2.status_code, 429)
            self.assertIn("Rate limit", res2.json()["detail"])

    def test_jobs_filter_sorting_with_none_relevance_score(self):
        """GET /api/jobs handles jobs with missing or None relevance_score without crashing."""
        seen = server_module.load_seen_jobs()
        seen["null_score_job"] = {
            "title": "Unscored Job",
            "company": "NullCorp",
            "relevance_score": None,
            "shortlist_tier": "VERIFICATION_QUEUE",
        }
        server_module.save_seen_jobs(seen)

        res = self.client.get("/api/jobs?sort_by=relevance_score&order=desc")
        self.assertEqual(res.status_code, 200)
        results = res.json()["results"]
        # Null scored job should sort to the end
        self.assertEqual(results[-1]["company"], "NullCorp")

    def test_concurrent_writes_protection(self):
        """Multiple threads modifying tracker rows concurrently do not crash or corrupt state."""
        import concurrent.futures

        def add_entry(i):
            return self.client.post("/api/tracker", json={
                "company": f"ConcurrentCo_{i}",
                "role": f"Engineer_{i}",
                "status": "applied",
            })

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(add_entry, i) for i in range(10)]
            results = [f.result() for f in futures]

        for r in results:
            self.assertEqual(r.status_code, 200)

        rows = self.client.get("/api/tracker").json()["applications"]
        self.assertEqual(len(rows), 10)

    def test_interrupted_write_preserves_original_file(self):
        """Simulated exception during write preserves the original file intact."""
        original_content = self.tracker_file.read_text(encoding="utf-8") if self.tracker_file.is_file() else ""
        rows = server_module.load_tracker_rows()

        with patch("os.replace", side_effect=OSError("Disk full simulation")):
            with self.assertRaises(OSError):
                server_module.save_tracker_rows(rows + [{"company": "FailCo", "role": "Dev", "status": "applied"}])

        # Original file must remain unchanged
        after_content = self.tracker_file.read_text(encoding="utf-8") if self.tracker_file.is_file() else ""
        self.assertEqual(original_content, after_content)


if __name__ == "__main__":
    unittest.main()
