#!/usr/bin/env python3
"""Phase 5.1 Validation Suite — Profile Isolation, Multi-Domain Matching, and Data Integrity.

Comprehensive tests verifying:
  1. Seven domain profiles (Finance, Design, Marketing, Healthcare, HR, Software Engineering, Cybersecurity)
     across 4 evaluation scenarios each:
       - Relevant vacancy
       - Unrelated vacancy
       - Seniority mismatch (executive/VP for entry/intern profile)
       - Eligibility unknown (requires batch verification)
  2. Source job data immutability during scoring.
  3. Preservation of verification audit logs and application tracker rows across profile updates.
  4. Profile persistence robustness:
       - Invalid JSON / corrupted file fallback
       - Empty preferences handling (zero roles, zero skills, empty locations)
       - Missing optional fields
       - Atomic persistence round-trip
"""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.config import DEFAULT_PROFILE, load_profile, save_profile
from tools.relevance_engine import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    EXCLUDED,
    HIGH_MATCH,
    LOW_MATCH,
    NEEDS_VERIFICATION,
    POTENTIAL_MATCH,
    SHORTLIST_READY,
    VERIFICATION_QUEUE,
    RelevanceEngine,
    evaluate_job,
)


class TestMultiDomainMatchingMatrix(unittest.TestCase):
    """Test 7 domains across relevant, unrelated, seniority-mismatched, and eligibility-unknown scenarios."""

    # 1. FINANCE DOMAIN
    def test_finance_profile_scenarios(self):
        fin_profile = {
            **DEFAULT_PROFILE,
            "preferred_roles": ["Financial Analyst", "Investment Analyst", "Equity Research Associate"],
            "target_industries": ["Finance & FinTech", "Banking"],
            "skills": ["financial_modeling", "excel", "valuation", "accounting", "python"],
            "experience_levels": ["entry_level", "internship"],
            "education": {"graduation_year": 2027, "require_grad_year_match": True},
        }

        # A. Relevant with verified batch
        relevant_job = {
            "title": "Financial Analyst Intern - 2027 Batch",
            "company": "Apex Capital",
            "description": "Financial modeling, DCF valuation, and equity research for 2027 graduates. High proficiency in Excel required.",
        }
        res_rel = evaluate_job(relevant_job, profile=fin_profile)
        self.assertGreaterEqual(res_rel["relevance_score"], 70)
        self.assertEqual(res_rel["shortlist_tier"], SHORTLIST_READY)
        self.assertIn("Financial Analyst", res_rel["matched_roles"])

        # B. Unrelated
        unrelated_job = {
            "title": "Creative Art Director",
            "company": "DesignHub Agency",
            "description": "Visual branding, illustration, and print media design campaigns.",
        }
        res_unrel = evaluate_job(unrelated_job, profile=fin_profile)
        self.assertLess(res_unrel["relevance_score"], 45)
        self.assertEqual(res_unrel["shortlist_tier"], EXCLUDED)

        # C. Seniority mismatch (VP / Executive)
        senior_job = {
            "title": "Vice President - Corporate Treasury & Finance",
            "company": "Global Bank Corp",
            "description": "15+ years of capital markets and treasury leadership.",
        }
        res_sen = evaluate_job(senior_job, profile=fin_profile)
        self.assertEqual(res_sen["shortlist_tier"], EXCLUDED)
        self.assertEqual(res_sen["eligibility_status"], "ineligible_senior")

        # D. Eligibility unknown (high technical fit, but batch unmentioned)
        unknown_elig_job = {
            "title": "Junior Financial Analyst",
            "company": "Morgan Analytics",
            "description": "Entry-level opportunity for financial modeling, balance sheets, and equity research. Open to all students.",
        }
        res_unk = evaluate_job(unknown_elig_job, profile=fin_profile)
        self.assertGreaterEqual(res_unk["relevance_score"], 60)
        self.assertEqual(res_unk["eligibility_status"], "unknown")
        self.assertEqual(res_unk["classification"], NEEDS_VERIFICATION)
        self.assertEqual(res_unk["shortlist_tier"], VERIFICATION_QUEUE)

    # 2. DESIGN DOMAIN
    def test_design_profile_scenarios(self):
        design_profile = {
            **DEFAULT_PROFILE,
            "preferred_roles": ["UI/UX Designer", "Product Designer"],
            "target_industries": ["Design", "Technology"],
            "skills": ["figma", "wireframing", "user_research", "prototyping"],
            "experience_levels": ["entry_level", "internship"],
            "education": {"graduation_year": 2027, "require_grad_year_match": True},
        }

        # A. Relevant with verified batch
        relevant_job = {
            "title": "Associate UI/UX Designer - 2027 Batch",
            "company": "CraftPixel Studios",
            "description": "Looking for an associate product designer proficient in Figma, user research, wireframing, and interactive prototyping. 2027 graduates eligible.",
        }
        res_rel = evaluate_job(relevant_job, profile=design_profile)
        self.assertGreaterEqual(res_rel["relevance_score"], 70)
        self.assertEqual(res_rel["shortlist_tier"], SHORTLIST_READY)

        # B. Unrelated
        unrelated_job = {
            "title": "SOC Analyst",
            "company": "CyberSec Systems",
            "description": "Splunk alert triage, log inspection, and intrusion prevention.",
        }
        res_unrel = evaluate_job(unrelated_job, profile=design_profile)
        self.assertLess(res_unrel["relevance_score"], 45)
        self.assertEqual(res_unrel["shortlist_tier"], EXCLUDED)

        # C. Seniority mismatch
        senior_job = {
            "title": "VP of Design",
            "company": "MegaTech Apps",
            "description": "Head global user experience and design strategy for 200+ designers.",
        }
        res_sen = evaluate_job(senior_job, profile=design_profile)
        self.assertEqual(res_sen["shortlist_tier"], EXCLUDED)

        # D. Eligibility unknown
        unknown_elig_job = {
            "title": "Product Design Intern",
            "company": "Startup Labs",
            "description": "Hands-on UI/UX prototyping and Figma component library building for web apps. Flexible university internship.",
        }
        res_unk = evaluate_job(unknown_elig_job, profile=design_profile)
        self.assertGreaterEqual(res_unk["relevance_score"], 60)
        self.assertEqual(res_unk["eligibility_status"], "unknown")
        self.assertEqual(res_unk["shortlist_tier"], VERIFICATION_QUEUE)

    # 3. MARKETING DOMAIN
    def test_marketing_profile_scenarios(self):
        mkt_profile = {
            **DEFAULT_PROFILE,
            "preferred_roles": ["Growth Marketer", "Digital Marketing Specialist", "SEO Analyst"],
            "target_industries": ["Marketing & Advertising", "E-commerce"],
            "skills": ["seo", "content_strategy", "google_analytics", "social_media"],
            "experience_levels": ["entry_level", "internship"],
            "education": {"graduation_year": 2027, "require_grad_year_match": True},
        }

        # A. Relevant
        relevant_job = {
            "title": "Digital Marketing Intern - 2027 Batch",
            "company": "BrandBoost Media",
            "description": "Work with our digital marketing and growth team on SEO optimization, Google Analytics tracking, and campaign strategy. Open to 2027 batch.",
        }
        res_rel = evaluate_job(relevant_job, profile=mkt_profile)
        self.assertGreaterEqual(res_rel["relevance_score"], 70)
        self.assertEqual(res_rel["shortlist_tier"], SHORTLIST_READY)

        # B. Unrelated
        unrelated_job = {
            "title": "Hardware Embedded Firmware Engineer",
            "company": "CircuitCore Labs",
            "description": "C/C++ microcontrollers, RTOS, and PCB schematic design.",
        }
        res_unrel = evaluate_job(unrelated_job, profile=mkt_profile)
        self.assertLess(res_unrel["relevance_score"], 45)
        self.assertEqual(res_unrel["shortlist_tier"], EXCLUDED)

        # C. Seniority mismatch
        senior_job = {
            "title": "Chief Marketing Officer (CMO)",
            "company": "Retail Empire",
            "description": "Lead nationwide brand expansion and multi-million dollar ad spend.",
        }
        res_sen = evaluate_job(senior_job, profile=mkt_profile)
        self.assertEqual(res_sen["shortlist_tier"], EXCLUDED)

        # D. Eligibility unknown
        unknown_elig_job = {
            "title": "Growth Marketing Intern",
            "company": "ScaleUp Apps",
            "description": "Support user acquisition campaigns and SEO analytics. Inquire with university credentials.",
        }
        res_unk = evaluate_job(unknown_elig_job, profile=mkt_profile)
        self.assertGreaterEqual(res_unk["relevance_score"], 60)
        self.assertEqual(res_unk["eligibility_status"], "unknown")
        self.assertEqual(res_unk["shortlist_tier"], VERIFICATION_QUEUE)

    # 4. HEALTHCARE DOMAIN
    def test_healthcare_profile_scenarios(self):
        health_profile = {
            **DEFAULT_PROFILE,
            "preferred_roles": ["Clinical Research Coordinator", "Healthcare Data Analyst"],
            "target_industries": ["Healthcare & Life Sciences", "Biotechnology"],
            "skills": ["clinical_trials", "gcp", "data_management", "epidemiology"],
            "experience_levels": ["entry_level", "internship"],
            "education": {"graduation_year": 2027, "require_grad_year_match": True},
        }

        # A. Relevant
        relevant_job = {
            "title": "Clinical Research Coordinator Trainee (2027 Batch)",
            "company": "BioPharma Health",
            "description": "Support clinical trials protocol execution, GCP compliance, and patient data management. Exclusively 2027 graduates.",
        }
        res_rel = evaluate_job(relevant_job, profile=health_profile)
        self.assertGreaterEqual(res_rel["relevance_score"], 65)
        self.assertEqual(res_rel["shortlist_tier"], SHORTLIST_READY)

        # B. Unrelated
        unrelated_job = {
            "title": "Cloud DevOps Engineer",
            "company": "InfraNet Global",
            "description": "Kubernetes cluster administration, Terraform, and AWS CI/CD pipelines.",
        }
        res_unrel = evaluate_job(unrelated_job, profile=health_profile)
        self.assertLess(res_unrel["relevance_score"], 45)
        self.assertEqual(res_unrel["shortlist_tier"], EXCLUDED)

        # C. Seniority mismatch
        senior_job = {
            "title": "Director of Clinical Operations",
            "company": "MedLife Research",
            "description": "Executive clinical operations management with 12+ years experience.",
        }
        res_sen = evaluate_job(senior_job, profile=health_profile)
        self.assertEqual(res_sen["shortlist_tier"], EXCLUDED)

        # D. Eligibility unknown
        unknown_elig_job = {
            "title": "Healthcare Data Trainee",
            "company": "Hospital Analytics",
            "description": "Conduct epidemiological patient data management and assist clinical trials reporting.",
        }
        res_unk = evaluate_job(unknown_elig_job, profile=health_profile)
        self.assertGreaterEqual(res_unk["relevance_score"], 60)
        self.assertEqual(res_unk["eligibility_status"], "unknown")
        self.assertEqual(res_unk["shortlist_tier"], VERIFICATION_QUEUE)

    # 5. HUMAN RESOURCES (HR) DOMAIN
    def test_hr_profile_scenarios(self):
        hr_profile = {
            **DEFAULT_PROFILE,
            "preferred_roles": ["Talent Acquisition Specialist", "HR Generalist", "Recruiter"],
            "target_industries": ["Human Resources", "Consulting"],
            "skills": ["recruiting", "talent_sourcing", "onboarding", "hris"],
            "experience_levels": ["entry_level", "internship"],
            "education": {"graduation_year": 2027, "require_grad_year_match": True},
        }

        # A. Relevant
        relevant_job = {
            "title": "Talent Acquisition Intern - 2027 Batch",
            "company": "PeopleFirst Consulting",
            "description": "Screen applicants, manage candidate pipelines, and coordinate interviews. Tailored for 2027 graduating students.",
        }
        res_rel = evaluate_job(relevant_job, profile=hr_profile)
        self.assertGreaterEqual(res_rel["relevance_score"], 65)
        self.assertEqual(res_rel["shortlist_tier"], SHORTLIST_READY)

        # B. Unrelated
        unrelated_job = {
            "title": "Quantitative Hedge Fund Trader",
            "company": "AlphaCapital Quant",
            "description": "High-frequency algorithmic execution and statistical arbitrage.",
        }
        res_unrel = evaluate_job(unrelated_job, profile=hr_profile)
        self.assertLess(res_unrel["relevance_score"], 45)
        self.assertEqual(res_unrel["shortlist_tier"], EXCLUDED)

        # C. Seniority mismatch
        senior_job = {
            "title": "Vice President - People Operations & Human Resources",
            "company": "Global Enterprise",
            "description": "Chief talent strategist overseeing HR for 10,000 employees.",
        }
        res_sen = evaluate_job(senior_job, profile=hr_profile)
        self.assertEqual(res_sen["shortlist_tier"], EXCLUDED)

        # D. Eligibility unknown
        unknown_elig_job = {
            "title": "Recruitment & HR Intern",
            "company": "Staffing Pro",
            "description": "Assist talent acquisition specialists with resume screening and candidate communications.",
        }
        res_unk = evaluate_job(unknown_elig_job, profile=hr_profile)
        self.assertGreaterEqual(res_unk["relevance_score"], 60)
        self.assertEqual(res_unk["eligibility_status"], "unknown")
        self.assertEqual(res_unk["shortlist_tier"], VERIFICATION_QUEUE)

    # 6. SOFTWARE ENGINEERING DOMAIN
    def test_software_engineering_profile_scenarios(self):
        swe_profile = {
            **DEFAULT_PROFILE,
            "preferred_roles": ["Full Stack Developer", "Backend Engineer", "Software Engineer"],
            "target_industries": ["Software Development", "Information Technology"],
            "skills": ["python", "fastapi", "react", "postgresql", "docker"],
            "experience_levels": ["entry_level", "internship"],
            "education": {"graduation_year": 2027, "require_grad_year_match": True},
        }

        # A. Relevant
        relevant_job = {
            "title": "Software Engineering Intern - 2027 Batch",
            "company": "CloudTech Labs",
            "description": "Build high-throughput REST APIs using Python and FastAPI with PostgreSQL. Exclusively for 2027 batch.",
        }
        res_rel = evaluate_job(relevant_job, profile=swe_profile)
        # Broad generic role receives 35 role pts + 15 seniority + 15 skills = 65 pts
        self.assertGreaterEqual(res_rel["relevance_score"], 65)
        self.assertEqual(res_rel["shortlist_tier"], SHORTLIST_READY)

        # Specialized backend role achieves 45 role pts + 15 seniority + 15 skills = 75 pts
        specialized_swe_job = {
            "title": "Backend Engineering Intern - 2027 Batch",
            "company": "CloudTech Labs",
            "description": "Build high-throughput REST APIs using Python and FastAPI with PostgreSQL. Exclusively for 2027 batch.",
        }
        res_spec = evaluate_job(specialized_swe_job, profile=swe_profile)
        self.assertGreaterEqual(res_spec["relevance_score"], 75)
        self.assertEqual(res_spec["shortlist_tier"], SHORTLIST_READY)

        # B. Unrelated
        unrelated_job = {
            "title": "Commercial Real Estate Broker",
            "company": "Prime Realty Group",
            "description": "Leasing industrial warehouse space and tenant negotiations.",
        }
        res_unrel = evaluate_job(unrelated_job, profile=swe_profile)
        self.assertLess(res_unrel["relevance_score"], 45)
        self.assertEqual(res_unrel["shortlist_tier"], EXCLUDED)

        # C. Seniority mismatch
        senior_job = {
            "title": "Chief Technology Officer (CTO)",
            "company": "Unicorn Fintech",
            "description": "Lead engineering architecture and direct VP of Engineering.",
        }
        res_sen = evaluate_job(senior_job, profile=swe_profile)
        self.assertEqual(res_sen["shortlist_tier"], EXCLUDED)

        # D. Eligibility unknown
        unknown_elig_job = {
            "title": "Backend Engineering Intern",
            "company": "CloudScale Systems",
            "description": "Build microservices in Python and Docker. Great learning opportunity for college students.",
        }
        res_unk = evaluate_job(unknown_elig_job, profile=swe_profile)
        self.assertGreaterEqual(res_unk["relevance_score"], 65)
        self.assertEqual(res_unk["eligibility_status"], "unknown")
        self.assertEqual(res_unk["shortlist_tier"], VERIFICATION_QUEUE)

    # 7. CYBERSECURITY DOMAIN
    def test_cybersecurity_profile_scenarios(self):
        cyber_profile = {
            **DEFAULT_PROFILE,
            "preferred_roles": ["Cybersecurity Analyst", "Security Engineer", "SOC Analyst"],
            "target_industries": ["Cybersecurity", "Information Technology"],
            "skills": ["python", "burp_suite", "nmap", "linux", "networking"],
            "experience_levels": ["entry_level", "internship"],
            "education": {"graduation_year": 2027, "require_grad_year_match": True},
        }

        # A. Relevant
        relevant_job = {
            "title": "Cyber Security Intern (2027 Batch)",
            "company": "SecureNet Guard",
            "description": "Conduct VAPT security audits using Burp Suite and Nmap on Linux environments. Pre-final year / 2027 batch.",
        }
        res_rel = evaluate_job(relevant_job, profile=cyber_profile)
        self.assertGreaterEqual(res_rel["relevance_score"], 75)
        self.assertEqual(res_rel["shortlist_tier"], SHORTLIST_READY)

        # B. Unrelated
        unrelated_job = {
            "title": "Brand Partnership Manager",
            "company": "Influencer Hub",
            "description": "Sponsor outreach and social media influencer management.",
        }
        res_unrel = evaluate_job(unrelated_job, profile=cyber_profile)
        self.assertLess(res_unrel["relevance_score"], 45)
        self.assertEqual(res_unrel["shortlist_tier"], EXCLUDED)

        # C. Seniority mismatch
        senior_job = {
            "title": "Chief Information Security Officer (CISO)",
            "company": "Bank of Metropolis",
            "description": "Global enterprise cybersecurity governance and board oversight.",
        }
        res_sen = evaluate_job(senior_job, profile=cyber_profile)
        self.assertEqual(res_sen["shortlist_tier"], EXCLUDED)

        # D. Eligibility unknown
        unknown_elig_job = {
            "title": "SOC Analyst Intern",
            "company": "ThreatWatch Operations",
            "description": "Monitor SIEM alerts and perform triage with Python scripts on Linux.",
        }
        res_unk = evaluate_job(unknown_elig_job, profile=cyber_profile)
        self.assertGreaterEqual(res_unk["relevance_score"], 65)
        self.assertEqual(res_unk["eligibility_status"], "unknown")
        self.assertEqual(res_unk["shortlist_tier"], VERIFICATION_QUEUE)


class TestSourceJobDataImmutability(unittest.TestCase):
    """Verify evaluating jobs never mutates the original source job dictionary."""

    def test_original_job_dict_remains_unmodified(self):
        source_job = {
            "title": "Product Designer Intern",
            "company": "Studio Innovate",
            "location": "Bangalore",
            "description": "Figma prototyping and user research.",
            "url": "https://studio.com/jobs/design-1",
        }
        job_copy = copy.deepcopy(source_job)

        profile_a = {**DEFAULT_PROFILE, "preferred_roles": ["Product Designer"]}
        profile_b = {**DEFAULT_PROFILE, "preferred_roles": ["Financial Analyst"]}

        res_a = evaluate_job(source_job, profile=profile_a)
        self.assertEqual(source_job, job_copy, "evaluate_job must not mutate input job dictionary")

        res_b = evaluate_job(source_job, profile=profile_b)
        self.assertEqual(source_job, job_copy, "evaluate_job must not mutate input job dictionary across evaluations")

        # Returned objects reflect different evaluations
        self.assertNotEqual(res_a["relevance_score"], res_b["relevance_score"])


class TestProfileChangesPreserveAuditAndTracker(unittest.TestCase):
    """Verify changing candidate profile does not modify verification logs or application tracker CSV."""

    def test_profile_update_preserves_audit_and_tracker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            seen_file = tmp_path / "seen_jobs.json"
            tracker_file = tmp_path / "job_search_tracker.csv"
            profile_file = tmp_path / "candidate_profile.json"

            # Create existing verified job with audit record
            initial_seen = {
                "seen": {
                    "apex_financial-analyst": {
                        "title": "Financial Analyst",
                        "company": "Apex Capital",
                        "eligibility_status": "eligible_2027",
                        "application_status": "open",
                        "manual_verification": {
                            "verified_at": "2026-10-18T10:00:00Z",
                            "evidence": "Verified official career portal link",
                            "notes": "Confirmed by team",
                        },
                    }
                }
            }
            seen_file.write_text(json.dumps(initial_seen), encoding="utf-8")

            # Create existing application tracker row
            tracker_rows = [
                {
                    "date": "2026-10-15",
                    "company": "Apex Capital",
                    "sector": "Finance",
                    "role": "Financial Analyst",
                    "role_type": "internship",
                    "channel": "direct",
                    "status": "applied",
                    "contact_person": "Jane Doe",
                    "fit_rating": "Strong",
                    "notes": "First round submitted",
                    "cv_file": "",
                    "cover_letter_file": "",
                    "source": "cli",
                    "deadline": "2026-11-01",
                }
            ]
            fieldnames = list(tracker_rows[0].keys())
            with tracker_file.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(tracker_rows)

            # Now update profile to a completely different domain (e.g. Design)
            design_profile = {
                **DEFAULT_PROFILE,
                "candidate_name": "Jordan Lee",
                "preferred_roles": ["UI/UX Designer"],
                "target_industries": ["Design"],
            }
            save_profile(design_profile, path=profile_file)

            # Verify seen_jobs.json audit trail remains strictly identical
            reloaded_seen = json.loads(seen_file.read_text(encoding="utf-8"))
            self.assertEqual(
                reloaded_seen["seen"]["apex_financial-analyst"]["manual_verification"]["evidence"],
                "Verified official career portal link",
                "Manual verification evidence must not be overwritten by profile updates",
            )

            # Verify tracker CSV remains intact
            with tracker_file.open(encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["company"], "Apex Capital")
            self.assertEqual(rows[0]["status"], "applied")


class TestProfilePersistenceEdgeCases(unittest.TestCase):
    """Verify profile persistence handles invalid inputs, empty lists, and missing fields."""

    def test_corrupted_json_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "candidate_profile.json"
            bad_file.write_text("NOT_VALID_JSON{::: broken", encoding="utf-8")
            loaded = load_profile(path=bad_file)
            self.assertEqual(loaded["product_name"], "CareerFlow")
            self.assertIn("preferred_roles", loaded)

    def test_empty_preferences_evaluate_safely(self):
        empty_profile = {
            "product_name": "CareerFlow",
            "candidate_name": "",
            "headline": "",
            "preferred_roles": [],
            "target_industries": [],
            "skills": [],
            "education": None,
            "experience_levels": [],
            "locations": [],
            "work_arrangements": [],
            "employment_types": [],
        }
        test_job = {
            "title": "Software Engineer",
            "company": "Tech Corp",
            "location": "Remote",
            "description": "General software engineering vacancy.",
        }
        # Must evaluate without KeyError or unhandled exception
        res = evaluate_job(test_job, profile=empty_profile)
        self.assertIn("relevance_score", res)
        self.assertIn("shortlist_tier", res)
        self.assertEqual(res["eligibility_status"], "eligible_general")

    def test_missing_optional_fields_load_gracefully(self):
        sparse_profile = {
            "preferred_roles": ["Data Analyst"],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sparse.json"
            save_profile(sparse_profile, path=path)
            loaded = load_profile(path=path)
            # Merges with DEFAULT_PROFILE defaults
            self.assertEqual(loaded["preferred_roles"], ["Data Analyst"])
            self.assertIn("scoring_weights", loaded)
            self.assertEqual(loaded["product_name"], "CareerFlow")


if __name__ == "__main__":
    unittest.main()
