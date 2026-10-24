"""Tests for tools/relevance_engine.py — Phase 2 Job Relevance and Verification Engine.

Tests cover:
  1. Separation of relevance_score, eligibility_status, application_status, and verification_confidence.
  2. Classification consistency and shortlist policies (SHORTLIST_READY, VERIFICATION_QUEUE, EXCLUDED).
  3. Positive tests with explicit 2027 graduation eligibility.
  4. Negative tests for unknown batch eligibility on high-scoring roles (NEVER inferred from intern/skills).
  5. Negative tests for expired/closed roles with high skill relevance.
  6. Negative tests for senior management roles returned by intern queries.
  7. Handling conflicting/missing dates and eligibility statements.
  8. Deduplication, URL normalization, state persistence, and rank_state interop.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from tools.relevance_engine import (
    HIGH_MATCH,
    POTENTIAL_MATCH,
    LOW_MATCH,
    NEEDS_VERIFICATION,
    SHORTLIST_READY,
    VERIFICATION_QUEUE,
    EXCLUDED,
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_LOW,
    RelevanceEngine,
    evaluate_job,
    extract_direct_application_url,
    normalize_url,
    are_duplicate_jobs,
    merge_job_records,
    enrich_job_with_linkedin_detail,
    fetch_linkedin_detail,
)

SAMPLE_NAB_SVP = {
    "id": "senior-vice-president-internal-audit-cybersecurity-nab-394821",
    "title": "Senior Vice President - Internal Audit (Cybersecurity)",
    "company": "nab",
    "company_slug": "nab",
    "location": "Gurugram, India",
    "date": "2026-10-16T00:00:00Z",
    "url": "https://nab.wd3.myworkdayjobs.com/nab_careers/job/Gurugram-India/Senior-Vice-President---Internal-Audit-Cybersecurity_24009?utm_source=freehire.me",
    "work_mode": "hybrid",
    "regions": ["apac"],
    "countries": ["in"],
    "skills": ["cybersecurity", "internal-audit", "risk-management", "leadership"],
    "description": "The Senior Vice President - Internal Audit (Cybersecurity) is responsible for leading executive-level audits across enterprise technology and cyber risk. Requirements: 15+ years of relevant experience in cybersecurity leadership, executive stakeholder management, and team oversight.",
}

SAMPLE_TSAARO_HR = {
    "id": "human-resources-intern-tsaaro-l7ndm63a",
    "title": "Human Resources Intern",
    "company": "Tsaaro",
    "company_slug": "tsaaro",
    "location": "Noida, India",
    "date": "2026-10-15T00:00:00Z",
    "url": "https://tsaaro.zohorecruit.in/jobs/Careers/91641000016400093?utm_source=freehire.me",
    "work_mode": "onsite",
    "regions": ["apac"],
    "countries": ["in"],
    "skills": ["recruitment", "talent-acquisition", "hr-operations", "communication"],
    "description": "At Tsaaro, we are hiring a Human Resources Intern to support talent acquisition, screen resumes, schedule candidate interviews, and manage employee onboarding documentation.",
}

SAMPLE_TSAARO_MARKETING = {
    "id": "marketing-intern-to-hire-tsaaro-l7ndm63a",
    "title": "Marketing (Intern to Hire)",
    "company": "Tsaaro",
    "company_slug": "tsaaro",
    "location": "Noida, India",
    "date": "2026-10-12T00:00:00Z",
    "url": "https://tsaaro.zohorecruit.in/jobs/Careers/91641000016400093?utm_source=freehire.me",
    "work_mode": "hybrid",
    "regions": ["apac"],
    "countries": ["in"],
    "skills": ["content-marketing", "social-media", "lead-generation", "canva"],
    "description": "Join Tsaaro as a Marketing Intern to assist in social media campaigns, SEO, lead generation, and brand awareness for our cybersecurity consulting practice.",
}

SAMPLE_DISCOVER_DOLLAR_AI = {
    "id": "ai-engineer-intern-discover-dollar-wfiz7hom",
    "title": "AI Engineer Intern",
    "company": "Discover Dollar",
    "company_slug": "discover-dollar",
    "location": "Bangalore",
    "date": "2026-10-09T13:14:21Z",
    "url": "https://t.me/jobs_and_internships_updates/7436?utm_source=freehire.me",
    "work_mode": "onsite",
    "regions": ["apac"],
    "countries": ["in"],
    "skills": ["python", "ai", "machine-learning", "fastapi", "llms"],
    "description": "Role: AI Engineer Intern\nBatch Eligible: 2027, 2028 & 2029 graduates\nLocation: Bangalore\nStipend: ₹30,000 / month\n\nWe are looking for an AI Engineer Intern skilled in Python, LLMs, AI agents, and building backend APIs with FastAPI.",
}

SAMPLE_CODEZYNG_CYBER = {
    "id": "cyber-security-intern-codezyng-5gcarmh2",
    "title": "Cyber Security Intern",
    "company": "Codezyng",
    "company_slug": "codezyng",
    "location": "Udupi, Karnataka, India",
    "date": "2026-10-14T10:00:00Z",
    "url": "https://codezyng.zohorecruit.in/jobs/Careers/213304000000376098?utm_source=freehire.me",
    "work_mode": "hybrid",
    "regions": ["apac"],
    "countries": ["in"],
    "skills": ["vapt", "network-security", "linux", "burp-suite", "nmap", "python"],
    "description": "Codezyng is seeking a Cyber Security Intern to participate in vulnerability assessments (VAPT), network security scanning with Nmap and Burp Suite, analyzing logs on Linux systems, and developing automation scripts in Python. Ideal for pre-final year or 2027 engineering students.",
}

SAMPLE_NOVARTIS_AI = {
    "id": "intern-ai-data-science-novartis-9921",
    "title": "Intern AI & Data Science",
    "company": "Novartis",
    "company_slug": "novartis",
    "location": "Hyderabad, India",
    "date": "2026-10-15T00:00:00Z",
    "url": "https://novartis.wd3.myworkdayjobs.com/novartis_careers/job/Hyderabad/Intern-AI_102",
    "work_mode": "onsite",
    "skills": ["python", "machine-learning", "deep-learning", "statistics"],
    "description": "Novartis is looking for an Intern in AI & Data Science to build machine learning models, analyze datasets using Python, and collaborate with data scientists on biomedical AI applications. Requirements: Enrolled student in Computer Science or related degree.",
}


class TestRelevanceEnginePhase2(unittest.TestCase):
    """Test suite for Phase 2 Job Relevance and Verification Engine."""

    def test_dimensions_separation_in_record(self):
        """Verify relevance_score, eligibility_status, application_status, and verification_confidence are distinct."""
        evaluated = evaluate_job(SAMPLE_CODEZYNG_CYBER)
        self.assertIn("relevance_score", evaluated)
        self.assertIn("eligibility_status", evaluated)
        self.assertIn("application_status", evaluated)
        self.assertIn("verification_confidence", evaluated)
        self.assertIn("shortlist_tier", evaluated)

        self.assertIsInstance(evaluated["relevance_score"], (int, float))
        self.assertIsInstance(evaluated["eligibility_status"], str)
        self.assertIsInstance(evaluated["application_status"], str)
        self.assertIsInstance(evaluated["verification_confidence"], str)

    def test_highly_relevant_role_with_unknown_batch_eligibility(self):
        """A role with high technical relevance but unknown/unspecified batch eligibility is NEVER HIGH_MATCH.

        It must be routed to NEEDS_VERIFICATION and VERIFICATION_QUEUE.
        """
        evaluated = evaluate_job(SAMPLE_NOVARTIS_AI)
        self.assertGreaterEqual(evaluated["relevance_score"], 70)
        self.assertEqual(evaluated["eligibility_status"], "unspecified")
        # Must not be HIGH_MATCH because 2027 eligibility is unconfirmed
        self.assertNotEqual(evaluated["classification"], HIGH_MATCH)
        self.assertEqual(evaluated["classification"], NEEDS_VERIFICATION)
        self.assertEqual(evaluated["shortlist_tier"], VERIFICATION_QUEUE)
        self.assertIn("Verification queue", evaluated["verification_notes"])

    def test_highly_relevant_role_explicitly_accepting_2027(self):
        """A high technical match with explicit 2027 eligibility qualifies as HIGH_MATCH and SHORTLIST_READY."""
        # Discover Dollar
        eval_dd = evaluate_job(SAMPLE_DISCOVER_DOLLAR_AI)
        self.assertGreaterEqual(eval_dd["relevance_score"], 70)
        self.assertEqual(eval_dd["eligibility_status"], "eligible_2027")
        self.assertEqual(eval_dd["classification"], HIGH_MATCH)
        self.assertEqual(eval_dd["shortlist_tier"], SHORTLIST_READY)

        # Codezyng
        eval_cz = evaluate_job(SAMPLE_CODEZYNG_CYBER)
        self.assertGreaterEqual(eval_cz["relevance_score"], 70)
        self.assertEqual(eval_cz["eligibility_status"], "eligible_2027")
        self.assertEqual(eval_cz["classification"], HIGH_MATCH)
        self.assertEqual(eval_cz["shortlist_tier"], SHORTLIST_READY)

    def test_never_infer_2027_eligibility_from_skills_or_intern(self):
        """Do not infer 2027 batch from high skill matches or the word 'intern' alone."""
        job = {
            "title": "Cyber Security Intern",
            "company": "Global Sec",
            "location": "Bengaluru, India",
            "url": "https://globalsec.com/jobs/1",
            "description": "Looking for a Cyber Security Intern proficient in Python, Linux, Nmap, Burp Suite, and Network Security.",
        }
        evaluated = evaluate_job(job)
        # Even with high relevance score, eligibility must be unknown
        self.assertGreaterEqual(evaluated["relevance_score"], 70)
        self.assertEqual(evaluated["eligibility_status"], "unknown")
        self.assertNotEqual(evaluated["classification"], HIGH_MATCH)
        self.assertEqual(evaluated["shortlist_tier"], VERIFICATION_QUEUE)

    def test_expired_or_closed_role_with_high_relevance_score(self):
        """An expired or closed role with high technical skills must be LOW_MATCH and EXCLUDED, never open."""
        # Role with past deadline
        expired_job = {
            "title": "Cyber Security Intern",
            "company": "SecureNet",
            "location": "Bengaluru, India",
            "url": "https://securenet.com/jobs/1",
            "deadline": "2024-05-01",  # Passed
            "description": "Burp Suite, Nmap, Linux penetration testing for 2027 graduates.",
        }
        evaluated = evaluate_job(expired_job)
        self.assertEqual(evaluated["application_status"], "expired")
        self.assertEqual(evaluated["classification"], LOW_MATCH)
        self.assertEqual(evaluated["shortlist_tier"], EXCLUDED)
        self.assertIn("closed or application deadline has passed", evaluated["verification_notes"])

        # Role with closed text marker
        closed_text_job = {
            "title": "Application Security Intern",
            "company": "AppSec Co",
            "location": "Remote",
            "url": "https://appsec.com/jobs/2",
            "description": "This job has closed and we are no longer accepting applications. VAPT, Python, Linux.",
        }
        eval_closed = evaluate_job(closed_text_job)
        self.assertEqual(eval_closed["application_status"], "expired")
        self.assertEqual(eval_closed["shortlist_tier"], EXCLUDED)

    def test_senior_cybersecurity_role_returned_by_internship_keyword_search(self):
        """Senior roles returned by intern queries must be disqualified as LOW_MATCH and EXCLUDED."""
        evaluated = evaluate_job(SAMPLE_NAB_SVP)
        self.assertEqual(evaluated["classification"], LOW_MATCH)
        self.assertEqual(evaluated["shortlist_tier"], EXCLUDED)
        self.assertLessEqual(evaluated["relevance_score"], 25)
        self.assertEqual(evaluated["eligibility_status"], "ineligible_senior")
        self.assertIn("Senior management", evaluated["verification_notes"])

    def test_unrelated_hr_and_marketing_roles_penalized(self):
        """Unrelated roles (HR, marketing) must be classified as LOW_MATCH and EXCLUDED."""
        eval_hr = evaluate_job(SAMPLE_TSAARO_HR)
        self.assertEqual(eval_hr["classification"], LOW_MATCH)
        self.assertEqual(eval_hr["shortlist_tier"], EXCLUDED)

        eval_mkt = evaluate_job(SAMPLE_TSAARO_MARKETING)
        self.assertEqual(eval_mkt["classification"], LOW_MATCH)
        self.assertEqual(eval_mkt["shortlist_tier"], EXCLUDED)

    def test_conflicting_or_missing_dates_and_eligibility_statements(self):
        """Handles missing dates, malformed deadlines, and conflicting batch statements gracefully."""
        # 1. Missing dates: does not crash
        no_date_job = {
            "title": "Junior Penetration Tester",
            "company": "PenTest Inc",
            "url": "https://pentest.com/job",
            "date": None,
            "deadline": None,
            "description": "Linux and network testing.",
        }
        eval_no_date = evaluate_job(no_date_job)
        self.assertIsNone(eval_no_date["deadline"])
        self.assertEqual(eval_no_date["application_status"], "unknown")

        # 2. Malformed / Non-ISO deadline (e.g. "ASAP")
        asap_job = {
            "title": "SOC Trainee",
            "company": "SOC Lab",
            "url": "https://soclab.com/job",
            "deadline": "ASAP",
            "description": "Log analysis and Splunk monitoring.",
        }
        eval_asap = evaluate_job(asap_job)
        self.assertEqual(eval_asap["deadline"], "ASAP")
        # Unparseable deadline lowers confidence
        self.assertEqual(eval_asap["verification_confidence"], CONFIDENCE_LOW)

        # 3. Conflicting batch statements: "2024 batch only" but also "2027"
        conflict_batch_job = {
            "title": "Security Analyst Intern",
            "company": "Confused Tech",
            "url": "https://confused.com/job",
            "description": "Notice: 2024 batch pass out only. Interns from 2027 may not apply.",
        }
        eval_conflict = evaluate_job(conflict_batch_job)
        self.assertEqual(eval_conflict["eligibility_status"], "conflicting_batch_dates")
        self.assertNotEqual(eval_conflict["classification"], HIGH_MATCH)

    def test_verification_confidence_scoring(self):
        """Verification confidence is evaluated as HIGH, MEDIUM, or LOW based on evidence strength."""
        # HIGH: direct ATS with explicit eligibility statement
        eval_cz = evaluate_job(SAMPLE_CODEZYNG_CYBER)
        self.assertEqual(eval_cz["verification_confidence"], CONFIDENCE_HIGH)

        # LOW: third-party Telegram link
        eval_dd = evaluate_job(SAMPLE_DISCOVER_DOLLAR_AI)
        self.assertEqual(eval_dd["verification_confidence"], CONFIDENCE_LOW)

        # MEDIUM: genuine direct ATS, but eligibility unspecified
        eval_nov = evaluate_job(SAMPLE_NOVARTIS_AI)
        self.assertEqual(eval_nov["verification_confidence"], CONFIDENCE_MEDIUM)

    def test_never_marks_open_merely_from_search_api(self):
        """Never mark a vacancy as open merely because a search API returned it."""
        job = {
            "title": "Cyber Security Trainee",
            "company": "SecureWorks",
            "url": "https://secureworks.com/careers/123",
            "description": "Cyber security trainee role analyzing logs.",
        }
        evaluated = evaluate_job(job)
        self.assertNotEqual(evaluated["application_status"], "open")
        self.assertEqual(evaluated["application_status"], "unknown")

    def test_deduplication(self):
        """Deduplicates records across tracking parameters and secondary keys."""
        jobs = [
            SAMPLE_CODEZYNG_CYBER,
            {**SAMPLE_CODEZYNG_CYBER, "url": SAMPLE_CODEZYNG_CYBER["url"] + "&trackingId=123"},
            SAMPLE_DISCOVER_DOLLAR_AI,
        ]
        engine = RelevanceEngine()
        res = engine.evaluate_all(jobs, deduplicate=True)
        self.assertEqual(res["meta"]["unique_count"], 2)
        self.assertEqual(res["meta"]["duplicate_count"], 1)

    def test_seen_jobs_persistence_and_rank_state_interop(self):
        """Ingests into seen_jobs.json with Phase 2 fields and ensures rank_state candidates only selects active roles."""
        import subprocess
        import sys

        with TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "seen_jobs.json"
            engine = RelevanceEngine(seen_state_file=state_path)
            res = engine.evaluate_all([SAMPLE_NAB_SVP, SAMPLE_CODEZYNG_CYBER, SAMPLE_NOVARTIS_AI])
            added = engine.update_seen_state(res["results"])
            self.assertEqual(added, 3)

            saved_doc = json.loads(state_path.read_text(encoding="utf-8"))
            seen = saved_doc["seen"]

            # NAB SVP must be skipped
            nab_key = [k for k in seen if "nab" in k][0]
            self.assertEqual(seen[nab_key]["status"], "skipped")
            self.assertEqual(seen[nab_key]["shortlist_tier"], EXCLUDED)

            # Codezyng (SHORTLIST_READY) and Novartis (VERIFICATION_QUEUE) must be new
            cz_key = [k for k in seen if "codezyng" in k][0]
            self.assertEqual(seen[cz_key]["status"], "new")
            self.assertEqual(seen[cz_key]["shortlist_tier"], SHORTLIST_READY)

            nov_key = [k for k in seen if "novartis" in k][0]
            self.assertEqual(seen[nov_key]["status"], "new")
            self.assertEqual(seen[nov_key]["shortlist_tier"], VERIFICATION_QUEUE)

            # Test tools/rank_state.py candidates selection
            tool_path = Path(__file__).resolve().parent.parent / "tools" / "rank_state.py"
            proc = subprocess.run(
                [sys.executable, str(tool_path), "candidates", "--state", str(state_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0)
            data = json.loads(proc.stdout)
            # Only Codezyng and Novartis are new; NAB SVP is skipped
            self.assertEqual(data["eligible"], 2)
            companies = {c["company"] for c in data["selected"]}
            self.assertIn("Codezyng", companies)
            self.assertIn("Novartis", companies)
            self.assertNotIn("nab", companies)



class TestPhase3DetailVerificationAndDeduplication(unittest.TestCase):
    """Test suite for Phase 3 LinkedIn detail enrichment, application verification, and deduplication."""

    @patch("tools.relevance_engine.fetch_linkedin_detail")
    def test_linkedin_detail_enrichment_success(self, mock_fetch):
        """Enriches snippet-only LinkedIn result with full detail and upgrades to SHORTLIST_READY."""
        mock_fetch.return_value = {
            "id": "4479651706",
            "title": "Information Security Intern",
            "company": "MediNex Workforce",
            "url": "https://www.linkedin.com/jobs/view/4479651706/",
            "location": "Bengaluru, Karnataka, India",
            "seniority": "Internship",
            "employmentType": "Internship",
            "isActive": True,
            "description": (
                "MediNex Workforce is hiring an Information Security Intern.\n"
                "Batch Eligible: 2027 graduates or 3rd year engineering students.\n"
                "Requirements: Linux administration, Nmap network scanning, Burp Suite VAPT, "
                "and Python scripting for security automation. 6-month stipend provided."
            ),
        }

        raw_snippet = {
            "id": "4479651706",
            "title": "Information Security Intern",
            "company": "MediNex Workforce",
            "url": "https://www.linkedin.com/jobs/view/4479651706/",
            "location": "Bengaluru, Karnataka, India",
            "description": "Information Security Intern card snippet from LinkedIn search.",
            "portal": "linkedin-search",
        }

        enriched = enrich_job_with_linkedin_detail(raw_snippet)

        mock_fetch.assert_called_once_with("4479651706", cli_path=None)
        self.assertGreaterEqual(enriched["relevance_score"], 70)
        self.assertEqual(enriched["eligibility_status"], "eligible_2027")
        self.assertEqual(enriched["classification"], HIGH_MATCH)
        self.assertEqual(enriched["shortlist_tier"], SHORTLIST_READY)
        self.assertEqual(enriched["verification_source"], "linkedin_detail")
        self.assertIn("linux", enriched["matched_skills"])
        self.assertIn("nmap", enriched["matched_skills"])
        self.assertIn("burp_suite", enriched["matched_skills"])
        self.assertIn("python", enriched["matched_skills"])

    @patch("tools.relevance_engine.fetch_linkedin_detail")
    def test_linkedin_detail_enrichment_closed_role(self, mock_fetch):
        """When LinkedIn detail indicates isActive: false, role is marked expired and EXCLUDED."""
        mock_fetch.return_value = {
            "id": "4479651706",
            "title": "Information Security Intern",
            "company": "MediNex",
            "isActive": False,
            "description": "VAPT, Linux, Python penetration testing for 2027 students.",
        }

        raw_snippet = {
            "id": "4479651706",
            "title": "Information Security Intern",
            "company": "MediNex",
            "url": "https://www.linkedin.com/jobs/view/4479651706/",
            "description": "Snippet",
            "portal": "linkedin-search",
        }

        enriched = enrich_job_with_linkedin_detail(raw_snippet)
        self.assertEqual(enriched["application_status"], "expired")
        self.assertEqual(enriched["classification"], LOW_MATCH)
        self.assertEqual(enriched["shortlist_tier"], EXCLUDED)
        self.assertIn("confirmed closed / expired", enriched["verification_notes"])

    @patch("tools.relevance_engine.fetch_linkedin_detail")
    def test_linkedin_detail_enrichment_failure_retained_in_verification_queue(self, mock_fetch):
        """When detail retrieval fails or is unavailable, the result is retained in VERIFICATION_QUEUE."""
        mock_fetch.return_value = None

        raw_snippet = {
            "id": "4479651706",
            "title": "Information Security Intern",
            "company": "MediNex",
            "url": "https://www.linkedin.com/jobs/view/4479651706/",
            "description": "Snippet only",
            "portal": "linkedin-search",
        }

        enriched = enrich_job_with_linkedin_detail(raw_snippet)
        self.assertEqual(enriched["classification"], NEEDS_VERIFICATION)
        self.assertEqual(enriched["shortlist_tier"], VERIFICATION_QUEUE)
        self.assertEqual(enriched["verification_confidence"], CONFIDENCE_LOW)
        self.assertIn("LinkedIn detail retrieval unavailable/failed", enriched["verification_notes"])

    def test_deduplication_normalized_application_url(self):
        """Matches duplicate records by normalized application URL ignoring tracking parameters."""
        job_a = {
            "title": "Security Analyst Intern",
            "employer": "Cyber Corp",
            "application_url": "https://cybercorp.zohorecruit.in/jobs/Careers/12345?utm_source=freehire.me&utm_medium=feed",
            "application_url_type": "direct_employer",
        }
        job_b = {
            "title": "Security Analyst Intern",
            "employer": "Cyber Corp",
            "application_url": "https://cybercorp.zohorecruit.in/jobs/Careers/12345?trackingId=abc987&ref=linkedin",
            "application_url_type": "direct_employer",
        }
        is_dup, reason = are_duplicate_jobs(job_a, job_b)
        self.assertTrue(is_dup)
        self.assertIn("Identical application URL", reason)

    def test_deduplication_company_and_title(self):
        """Matches duplicate records by normalized company and title when application URL is absent."""
        job_a = {
            "title": "Cyber Security Intern",
            "employer": "Codezyng Inc.",
            "source_url": "https://example.com/job1",
        }
        job_b = {
            "title": "Cyber-Security Intern",
            "employer": "Codezyng",
            "source_url": "https://example.com/job2",
        }
        is_dup, reason = are_duplicate_jobs(job_a, job_b)
        self.assertTrue(is_dup)
        self.assertIn("Matching company and title", reason)

    def test_deduplication_distinct_roles_same_company_never_merged(self):
        """Different roles at the same company must NEVER be merged."""
        job_ai = {
            "title": "AI Developer Intern",
            "company": "Codezyng",
            "url": "https://codezyng.zohorecruit.in/jobs/Careers/111",
            "description": "Build LLM agents and FastAPI backend for AI applications.",
        }
        job_sec = {
            "title": "Cyber Security Intern",
            "company": "Codezyng",
            "url": "https://codezyng.zohorecruit.in/jobs/Careers/222",
            "description": "VAPT, Linux, Nmap, Burp Suite network testing.",
        }
        is_dup, reason = are_duplicate_jobs(job_ai, job_sec)
        self.assertFalse(is_dup)
        self.assertIn("Different roles at codezyng", reason)

        engine = RelevanceEngine()
        res = engine.evaluate_all([job_ai, job_sec], deduplicate=True)
        self.assertEqual(res["meta"]["unique_count"], 2)
        self.assertEqual(res["meta"]["duplicate_count"], 0)

    def test_deduplication_same_title_distinct_application_links_never_merged(self):
        """Same title and company with distinct direct ATS requisition links are preserved as distinct opportunities."""
        job_blr = {
            "title": "Security Intern",
            "employer": "Acme Corp",
            "application_url": "https://acme.wd3.myworkdayjobs.com/careers/job/Bengaluru/Security-Intern_R101",
            "application_url_type": "direct_employer",
        }
        job_pune = {
            "title": "Security Intern",
            "employer": "Acme Corp",
            "application_url": "https://acme.wd3.myworkdayjobs.com/careers/job/Pune/Security-Intern_R102",
            "application_url_type": "direct_employer",
        }
        is_dup, reason = are_duplicate_jobs(job_blr, job_pune)
        self.assertFalse(is_dup)
        self.assertIn("Distinct direct application links", reason)

    def test_deduplication_metadata_merging(self):
        """Preserves useful metadata from both records: richer description, ATS link, union of skills, sources."""
        rec_card = {
            "title": "Cyber Security Intern",
            "employer": "Codezyng",
            "description": "Short card description",
            "source_url": "https://freehire.me/job/1",
            "source": "freehire-search",
            "portal": "freehire-search",
            "application_url": "https://t.me/jobs/1",
            "application_url_type": "third_party_aggregator",
            "matched_roles": ["Cybersecurity"],
            "matched_skills": ["python"],
            "eligibility_status": "unknown",
            "verification_confidence": CONFIDENCE_LOW,
            "relevance_score": 65,
            "shortlist_tier": VERIFICATION_QUEUE,
        }
        rec_detail = {
            "title": "Cyber Security Intern",
            "employer": "Codezyng",
            "description": "Codezyng is hiring a Cyber Security Intern for 2027 graduates. Skills: Linux, Nmap, Burp Suite, Python.",
            "source_url": "https://codezyng.zohorecruit.in/jobs/Careers/123",
            "source": "zohorecruit",
            "portal": "zohorecruit",
            "application_url": "https://codezyng.zohorecruit.in/jobs/Careers/123",
            "application_url_type": "direct_employer",
            "matched_roles": ["Cybersecurity"],
            "matched_skills": ["linux", "nmap", "burp_suite", "python"],
            "eligibility_status": "eligible_2027",
            "verification_confidence": CONFIDENCE_HIGH,
            "relevance_score": 85,
            "shortlist_tier": SHORTLIST_READY,
        }

        merged = merge_job_records(rec_card, rec_detail)

        # Longer description preserved
        self.assertEqual(merged["description"], rec_detail["description"])
        # Direct ATS application link preferred over third party
        self.assertEqual(merged["application_url"], rec_detail["application_url"])
        self.assertEqual(merged["application_url_type"], "direct_employer")
        # Union of skills
        self.assertIn("linux", merged["matched_skills"])
        self.assertIn("burp_suite", merged["matched_skills"])
        self.assertIn("python", merged["matched_skills"])
        # All sources tracked
        self.assertIn("freehire-search", merged["sources"])
        self.assertIn("zohorecruit", merged["sources"])
        # Upgraded eligibility and shortlist tier
        self.assertEqual(merged["eligibility_status"], "eligible_2027")
        self.assertEqual(merged["shortlist_tier"], SHORTLIST_READY)
        self.assertEqual(merged["classification"], HIGH_MATCH)

    def test_deduplication_shortlist_no_duplicates(self):
        """Batch evaluation ensures duplicates are discarded and do not appear twice in the shortlist."""
        job1 = SAMPLE_CODEZYNG_CYBER
        job1_dup = {**SAMPLE_CODEZYNG_CYBER, "url": SAMPLE_CODEZYNG_CYBER["url"] + "&trackingid=999"}
        job2 = SAMPLE_DISCOVER_DOLLAR_AI
        job2_dup = {**SAMPLE_DISCOVER_DOLLAR_AI, "url": SAMPLE_DISCOVER_DOLLAR_AI["url"] + "?ref=tg"}
        job3 = SAMPLE_NOVARTIS_AI

        jobs = [job1, job1_dup, job2, job2_dup, job3]
        engine = RelevanceEngine()
        res = engine.evaluate_all(jobs, deduplicate=True)

        self.assertEqual(res["meta"]["total_processed"], 5)
        self.assertEqual(res["meta"]["unique_count"], 3)
        self.assertEqual(res["meta"]["duplicate_count"], 2)

        keys = [j["key"] for j in res["results"]]
        self.assertEqual(len(keys), len(set(keys)), "Duplicate keys found in final shortlist!")

    def test_application_verification_preserves_source_url_and_distinguishes_direct_url(self):
        """Preserves original search URL while distinguishing authentic employer ATS application URL."""
        aggregator_job = {
            "title": "Cloud Security Intern",
            "company": "CloudSec",
            "location": "Bengaluru, India",
            "url": "https://freehire.me/job/cloudsec-intern-1234?utm_source=freehire.me",
            "description": (
                "CloudSec is hiring interns for 2027 batch. "
                "Apply directly on our careers portal: https://cloudsec.greenhouse.io/jobs/778899\n"
                "Skills: Python, Linux, AWS security."
            ),
        }
        evaluated = evaluate_job(aggregator_job)

        # Original search URL preserved
        self.assertEqual(evaluated["source_url"], "https://freehire.me/job/cloudsec-intern-1234?utm_source=freehire.me")
        # Direct application URL distinguished from search URL
        self.assertEqual(evaluated["application_url"], "https://cloudsec.greenhouse.io/jobs/778899")
        self.assertEqual(evaluated["application_url_type"], "extracted_from_description")
        # Never marked open merely because returned by search API
        self.assertEqual(evaluated["application_status"], "unknown")
        # Verification metadata
        self.assertIn("last_verified_at", evaluated)
        self.assertEqual(evaluated["verification_source"], "freehire_api")


class TestRelevanceEngineScoringAuditAndReliability(unittest.TestCase):
    """Reliability and audit regression tests ensuring calibrated, explainable scoring

    and strict separation of technical relevance, eligibility, authenticity, and availability.
    """

    def test_regression_strong_cybersecurity_internship_match(self):
        """A strong cybersecurity internship with core skills and verified 2027 eligibility

        achieves a top-tier relevance score (>= 85) and is SHORTLIST_READY.
        """
        cyber_job = {
            "title": "Cyber Security Analyst Intern",
            "company": "Sentinel Defense",
            "location": "Bengaluru, India",
            "url": "https://sentinel.greenhouse.io/jobs/101",
            "date": "2026-10-20T00:00:00Z",
            "description": (
                "Sentinel Defense is seeking a Cyber Security Analyst Intern to conduct vulnerability "
                "assessments (VAPT), network security scanning with Nmap and Burp Suite on Linux servers, "
                "and security automation scripting in Python. Exclusively open to 2027 batch engineering students. "
                "Stipend: ₹30,000 / month."
            ),
        }
        res = evaluate_job(cyber_job)
        # Technical relevance reflects full alignment
        self.assertGreaterEqual(res["relevance_score"], 85)
        self.assertEqual(res["relevance_breakdown"]["role"], 45)
        self.assertEqual(res["relevance_breakdown"]["seniority"], 15)
        self.assertEqual(res["relevance_breakdown"]["skills"], 25)
        self.assertIn("networking", res["matched_skills"])
        self.assertIn("linux", res["matched_skills"])
        self.assertIn("burp_suite", res["matched_skills"])
        self.assertIn("nmap", res["matched_skills"])
        self.assertIn("python", res["matched_skills"])
        self.assertIn("Cybersecurity Analyst", res["matched_roles"])
        # Eligibility and actionability
        self.assertEqual(res["eligibility_status"], "eligible_2027")
        self.assertEqual(res["classification"], HIGH_MATCH)
        self.assertEqual(res["shortlist_tier"], SHORTLIST_READY)
        self.assertEqual(res["verification_confidence"], CONFIDENCE_HIGH)

    def test_regression_strong_backend_development_internship_match(self):
        """A strong backend development internship with core stack achieves a top score (>= 85)."""
        backend_job = {
            "title": "Backend Developer Intern",
            "company": "CloudScale Systems",
            "location": "Hyderabad, India",
            "url": "https://cloudscale.lever.co/jobs/202",
            "date": "2026-10-22T00:00:00Z",
            "description": (
                "CloudScale Systems is hiring a Backend Developer Intern. You will design scalable microservices "
                "using Python, FastAPI, and PostgreSQL (SQL), containerized with Docker and communicating over REST APIs. "
                "Open to 2027 batch graduates. Stipend: ₹35,000 / month."
            ),
        }
        res = evaluate_job(backend_job)
        self.assertGreaterEqual(res["relevance_score"], 90)
        self.assertEqual(res["relevance_breakdown"]["role"], 45)
        self.assertEqual(res["relevance_breakdown"]["seniority"], 15)
        self.assertEqual(res["relevance_breakdown"]["skills"], 25)
        self.assertEqual(res["relevance_breakdown"]["location"], 5)
        self.assertEqual(res["relevance_breakdown"]["compensation"], 3)
        self.assertEqual(res["relevance_breakdown"]["freshness"], 2)
        # Total equals 95 perfectly (45 + 15 + 25 + 5 + 3 + 2)
        self.assertEqual(res["relevance_score"], 95)
        self.assertEqual(res["eligibility_status"], "eligible_2027")
        self.assertEqual(res["classification"], HIGH_MATCH)
        self.assertEqual(res["shortlist_tier"], SHORTLIST_READY)

    def test_regression_generic_software_internship_with_zero_skills_never_receives_100(self):
        """A generic software internship requiring unrelated technologies (Java, C++) with

        zero candidate skill overlap must NOT receive 100/100 (capped at 75 max).
        """
        generic_unrelated_stack_job = {
            "title": "Software Engineering Intern",
            "company": "Acme Retail Tech",
            "location": "Bengaluru, India",
            "date": "2026-10-22T00:00:00Z",
            "description": (
                "Acme Retail Tech is hiring Software Engineering Interns. Work on our enterprise e-commerce "
                "backend using Java, Spring Boot, React, and C++. Stipend: ₹25,000 / month. 2027 batch eligible."
            ),
        }
        res = evaluate_job(generic_unrelated_stack_job)
        # Zero matching candidate skills
        self.assertEqual(res["matched_skills"], [])
        self.assertEqual(res["relevance_breakdown"]["skills"], 0)
        # Must never receive 100/100 (generic role: 35 + 15 + 5 + 3 + 2 = 60 <= 75)
        self.assertEqual(res["relevance_breakdown"]["role"], 35)
        self.assertLessEqual(res["relevance_score"], 75)
        self.assertNotEqual(res["relevance_score"], 100)

    def test_regression_generic_software_internship_with_partial_skills_is_calibrated(self):
        """A generic software internship with partial overlap (only git and python) receives

        a calibrated proportional score (~70), never an inflated 100.
        """
        partial_job = {
            "title": "Software Engineering Intern",
            "company": "Acme Retail Tech",
            "location": "Bengaluru, India",
            "date": "2026-10-22T00:00:00Z",
            "description": (
                "Software Engineering Intern to write automation scripts in Python and collaborate on Git. "
                "Stipend: ₹25,000 / month. Open to 2027 batch."
            ),
        }
        res = evaluate_job(partial_job)
        self.assertCountEqual(res["matched_skills"], ["python", "git"])
        # 2 skills out of 5 = (2/5)*25 = 10 pts
        self.assertEqual(res["relevance_breakdown"]["skills"], 10)
        self.assertEqual(res["relevance_breakdown"]["role"], 35)
        # Total is calibrated: 35 + 15 + 10 + 5 + 3 + 2 = 70
        self.assertEqual(res["relevance_score"], 70)
        self.assertNotEqual(res["relevance_score"], 100)

    def test_regression_unrelated_sales_or_marketing_role(self):
        """An unrelated sales or marketing vacancy receives a low score (< 40) and is EXCLUDED."""
        unrelated_job = {
            "title": "Digital Marketing & Sales Intern",
            "company": "GrowthBuzz Agency",
            "location": "Mumbai, India",
            "description": (
                "Join us as a Digital Marketing & Sales Intern. Responsibilities: social media marketing, "
                "lead generation, cold outreach, and client demos."
            ),
        }
        res = evaluate_job(unrelated_job)
        self.assertEqual(res["relevance_breakdown"]["role"], 0)
        self.assertLess(res["relevance_score"], 40)
        self.assertEqual(res["classification"], LOW_MATCH)
        self.assertEqual(res["shortlist_tier"], EXCLUDED)

    def test_regression_vacancy_missing_important_fields(self):
        """A vacancy with a missing description cannot verify skills, receives low confidence,

        and is routed to VERIFICATION_QUEUE.
        """
        stub_job = {
            "title": "Software Engineering Intern",
            "company": "Stealth Tech",
            "location": "",
            "description": "",  # Empty description
        }
        res = evaluate_job(stub_job)
        # Skills cannot be verified from empty stub
        self.assertEqual(res["relevance_breakdown"]["skills"], 0)
        self.assertEqual(res["relevance_breakdown"]["location"], 0)
        self.assertLessEqual(res["relevance_score"], 65)
        self.assertEqual(res["verification_confidence"], CONFIDENCE_LOW)
        self.assertEqual(res["classification"], NEEDS_VERIFICATION)
        self.assertEqual(res["shortlist_tier"], VERIFICATION_QUEUE)
        self.assertIn("missing or incomplete", res["verification_notes"])

    def test_regression_explicit_graduation_year_mismatch_preserves_technical_score(self):
        """A strong technical role with graduation mismatch retains its technical relevance score

        while being strictly classified as LOW_MATCH and EXCLUDED (separation of concerns).
        """
        ineligible_batch_job = {
            "title": "Cyber Security Analyst Intern",
            "company": "ShieldCorp",
            "location": "Bengaluru, India",
            "date": "2026-10-20T00:00:00Z",
            "description": (
                "Cyber Security Analyst Intern. Skills: VAPT, Burp Suite, Nmap, Linux, Python. "
                "Strictly restricted to 2024 batch pass-out graduates only. Degree must be completed."
            ),
        }
        res = evaluate_job(ineligible_batch_job)
        # Technical relevance is preserved (not artificially flattened)
        self.assertGreaterEqual(res["relevance_score"], 80)
        self.assertIn("linux", res["matched_skills"])
        self.assertIn("burp_suite", res["matched_skills"])
        self.assertIn("nmap", res["matched_skills"])
        self.assertIn("python", res["matched_skills"])
        self.assertIn("Cybersecurity Analyst", res["matched_roles"])
        # But eligibility and shortlist policy strictly exclude it
        self.assertEqual(res["eligibility_status"], "ineligible_batch")
        self.assertEqual(res["classification"], LOW_MATCH)
        self.assertEqual(res["shortlist_tier"], EXCLUDED)
        self.assertIn("Ineligible graduation batch", res["verification_notes"])

    def test_regression_expired_vacancy_preserves_technical_score(self):
        """An expired vacancy retains its technical relevance score while being excluded

        from active application queues (separation of concerns).
        """
        expired_job = {
            "title": "Backend Developer Intern",
            "company": "DevCloud",
            "location": "Bengaluru, India",
            "deadline": "2024-01-01",  # Past deadline
            "description": (
                "Backend Developer Intern skilled in Python, FastAPI, SQL, Docker, and REST APIs. "
                "For 2027 batch graduates."
            ),
        }
        res = evaluate_job(expired_job)
        # Technical relevance is preserved
        self.assertGreaterEqual(res["relevance_score"], 80)
        # But availability and shortlist policy strictly exclude it
        self.assertEqual(res["application_status"], "expired")
        self.assertEqual(res["classification"], LOW_MATCH)
        self.assertEqual(res["shortlist_tier"], EXCLUDED)
        self.assertIn("closed or application deadline has passed", res["verification_notes"])

    # --- DEFECT 1 REGRESSION TESTS (Verification safeguard) ---

    def test_defect1_target_batch_internship_with_empty_description_routed_to_verification_queue(self):
        """A vacancy with 2027 in the title but an empty description must NOT reach SHORTLIST_READY.

        It preserves its technical relevance score but is routed to VERIFICATION_QUEUE with unconfirmed eligibility.
        """
        job = {
            "title": "Cyber Security Analyst Intern - 2027 Batch",
            "company": "SecureStub Corp",
            "location": "Bengaluru, India",
            "description": "",
        }
        res = evaluate_job(job)
        # Technical relevance score is preserved (45 role + 15 seniority + 5 location = 65)
        self.assertEqual(res["relevance_score"], 65)
        self.assertEqual(res["relevance_breakdown"]["skills"], 0)
        # Must NOT be SHORTLIST_READY or HIGH_MATCH without authentic description evidence
        self.assertNotEqual(res["shortlist_tier"], SHORTLIST_READY)
        self.assertEqual(res["shortlist_tier"], VERIFICATION_QUEUE)
        self.assertEqual(res["classification"], NEEDS_VERIFICATION)
        self.assertEqual(res["eligibility_status"], "unspecified")
        self.assertEqual(res["verification_confidence"], CONFIDENCE_LOW)
        self.assertIn("missing or incomplete", res["verification_notes"])

    def test_defect1_target_batch_internship_with_description_under_50_chars(self):
        """A vacancy with a description shorter than 50 characters cannot verify skills or authenticity

        and must be routed to VERIFICATION_QUEUE rather than SHORTLIST_READY.
        """
        job = {
            "title": "Cyber Security Analyst Intern - 2027 Batch",
            "company": "MicroSnippet Ltd",
            "location": "Bengaluru, India",
            "description": "Apply now for our 2027 batch internship.",  # 40 chars < 50
        }
        res = evaluate_job(job)
        self.assertEqual(res["relevance_score"], 65)
        self.assertNotEqual(res["shortlist_tier"], SHORTLIST_READY)
        self.assertEqual(res["shortlist_tier"], VERIFICATION_QUEUE)
        self.assertEqual(res["classification"], NEEDS_VERIFICATION)
        self.assertEqual(res["eligibility_status"], "unspecified")
        self.assertEqual(res["verification_confidence"], CONFIDENCE_LOW)
        self.assertIn("missing or incomplete", res["verification_notes"])

    def test_defect1_complete_documented_eligible_internship_qualifies_for_shortlist_ready(self):
        """A complete, sufficiently documented, eligible internship qualifies for SHORTLIST_READY."""
        complete_job = {
            "title": "Cyber Security Analyst Intern",
            "company": "Sentinel Defense",
            "location": "Bengaluru, India",
            "url": "https://sentinel.greenhouse.io/jobs/101",
            "date": "2026-10-20T00:00:00Z",
            "description": (
                "Sentinel Defense is seeking a Cyber Security Analyst Intern to conduct vulnerability "
                "assessments (VAPT), network security scanning with Nmap and Burp Suite on Linux servers, "
                "and security automation scripting in Python. Exclusively open to 2027 batch engineering students. "
                "Stipend: ₹30,000 / month."
            ),
        }
        res = evaluate_job(complete_job)
        self.assertGreaterEqual(res["relevance_score"], 65)
        self.assertEqual(res["shortlist_tier"], SHORTLIST_READY)
        self.assertEqual(res["classification"], HIGH_MATCH)
        self.assertEqual(res["eligibility_status"], "eligible_2027")

    def test_defect1_ineligible_batch_with_high_technical_relevance_remains_excluded(self):
        """An ineligible graduation batch vacancy with high technical relevance remains EXCLUDED."""
        ineligible_job = {
            "title": "Cyber Security Analyst Intern",
            "company": "Sentinel Defense",
            "location": "Bengaluru, India",
            "date": "2026-10-20T00:00:00Z",
            "description": (
                "Sentinel Defense seeks a Cyber Security Analyst Intern. Skills: VAPT, Nmap, Burp Suite, "
                "Linux, Python scripting. Strictly for 2024 batch pass-out graduates only. Degree must be completed."
            ),
        }
        res = evaluate_job(ineligible_job)
        # Technical relevance score is preserved independently
        self.assertGreaterEqual(res["relevance_score"], 80)
        self.assertEqual(res["shortlist_tier"], EXCLUDED)
        self.assertEqual(res["classification"], LOW_MATCH)
        self.assertEqual(res["eligibility_status"], "ineligible_batch")

    def test_defect1_expired_vacancy_with_high_technical_relevance_remains_excluded(self):
        """An expired vacancy with high technical relevance remains EXCLUDED."""
        expired_job = {
            "title": "Cyber Security Analyst Intern",
            "company": "Sentinel Defense",
            "location": "Bengaluru, India",
            "deadline": "2024-01-01",
            "description": (
                "Sentinel Defense seeks a Cyber Security Analyst Intern. Skills: VAPT, Nmap, Burp Suite, "
                "Linux, Python scripting. For 2027 batch graduates."
            ),
        }
        res = evaluate_job(expired_job)
        # Technical relevance score is preserved independently
        self.assertGreaterEqual(res["relevance_score"], 80)
        self.assertEqual(res["shortlist_tier"], EXCLUDED)
        self.assertEqual(res["classification"], LOW_MATCH)
        self.assertEqual(res["application_status"], "expired")

    # --- DEFECT 2 REGRESSION TESTS (Generic vs specialized role calibration) ---

    def test_defect2_equivalent_cybersecurity_vs_generic_software_internships(self):
        """Compare otherwise equivalent cybersecurity-specific and generic software-engineering internships.

        The specialized cybersecurity role receives 45 role pts while generic software receives 35 role pts.
        """
        cyber_job = {
            "title": "Cyber Security Analyst Intern",
            "company": "Alpha Corp",
            "location": "Bengaluru, India",
            "date": "2026-10-22T00:00:00Z",
            "description": (
                "Alpha Corp is hiring an intern. Work with Python, Linux, Nmap, Burp Suite, and Docker. "
                "Stipend: ₹30,000 / month. 2027 batch eligible."
            ),
        }
        generic_swe_job = {
            "title": "Software Engineering Intern",
            "company": "Beta Corp",
            "location": "Bengaluru, India",
            "date": "2026-10-22T00:00:00Z",
            "description": (
                "Beta Corp is hiring an intern. Work with Python, Linux, Nmap, Burp Suite, and Docker. "
                "Stipend: ₹30,000 / month. 2027 batch eligible."
            ),
        }
        res_cyber = evaluate_job(cyber_job)
        res_swe = evaluate_job(generic_swe_job)

        # Both match the exact same 5 skills (25 pts), seniority (15 pts), location (5 pts), comp (3 pts), freshness (2 pts)
        self.assertEqual(res_cyber["relevance_breakdown"]["skills"], 25)
        self.assertEqual(res_swe["relevance_breakdown"]["skills"], 25)

        # Specialized vs broad generic role distinction
        self.assertEqual(res_cyber["relevance_breakdown"]["role"], 45)
        self.assertEqual(res_swe["relevance_breakdown"]["role"], 35)

        # Score difference is exactly 10 points
        self.assertEqual(res_cyber["relevance_score"], 95)
        self.assertEqual(res_swe["relevance_score"], 85)
        self.assertEqual(res_cyber["relevance_score"] - res_swe["relevance_score"], 10)

    def test_defect2_candidate_targeting_only_generic_roles_receives_full_role_score(self):
        """A candidate whose profile only specifies generic roles (no specialized domain) receives 45 points."""
        broad_profile = {
            "preferred_roles": ["Software Engineer", "Software Developer"],
            "target_industries": ["Software Development"],
            "skills": ["python", "git"],
            "locations": ["Bengaluru"],
            "experience_levels": ["internship"],
            "education": {"graduation_year": 2027, "require_grad_year_match": True},
        }
        job = {
            "title": "Software Engineering Intern",
            "company": "Tech Corp",
            "location": "Bengaluru",
            "description": "Software Engineering Intern working on Python and Git. 2027 batch.",
        }
        res = evaluate_job(job, profile=broad_profile)
        # Because candidate does not target specialized roles, the generic role in title receives 45 pts
        self.assertEqual(res["relevance_breakdown"]["role"], 45)

    def test_defect2_unrelated_sales_and_marketing_roles(self):
        """Unrelated roles such as Sales and Marketing receive 0 role points and are EXCLUDED."""
        sales_job = {
            "title": "Inside Sales Representative Intern",
            "company": "SalesGrowth",
            "location": "Bengaluru",
            "description": "Cold calling, outbound lead qualification, CRM management.",
        }
        marketing_job = {
            "title": "Digital Marketing & SEO Intern",
            "company": "BrandReach",
            "location": "Bengaluru",
            "description": "Social media content, paid ad campaigns, Google Analytics.",
        }
        res_sales = evaluate_job(sales_job)
        res_mkt = evaluate_job(marketing_job)

        self.assertEqual(res_sales["relevance_breakdown"]["role"], 0)
        self.assertEqual(res_sales["classification"], LOW_MATCH)
        self.assertEqual(res_sales["shortlist_tier"], EXCLUDED)

        self.assertEqual(res_mkt["relevance_breakdown"]["role"], 0)
        self.assertEqual(res_mkt["classification"], LOW_MATCH)
        self.assertEqual(res_mkt["shortlist_tier"], EXCLUDED)


if __name__ == "__main__":
    unittest.main()
