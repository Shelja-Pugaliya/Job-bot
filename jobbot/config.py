"""Settings. Everything tunable lives here or in .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# The SDK reads ANTHROPIC_API_KEY; the user's .env uses CLAUDE_API_KEY.
API_KEY = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")

MODEL = os.getenv("JOBBOT_MODEL", "claude-opus-5-5")            # tailoring: CV, cover letter
RANK_MODEL = os.getenv("JOBBOT_RANK_MODEL", "claude-haiku-5-5")  # JD analysis used to rank jobs

# Daily funnel: pre-score -> analyze RANK_POOL with RANK_MODEL -> tailor the best DAILY_APPLY
RANK_POOL = int(os.getenv("JOBBOT_RANK_POOL", "25"))
DAILY_APPLY = int(os.getenv("JOBBOT_DAILY_APPLY", "10"))

# Candidate's years of experience: jobs requiring more than this are skipped.
MAX_YEARS = int(os.getenv("JOBBOT_MAX_YEARS", "5"))

# Models that accept the server-side refusal fallback (Haiku 5.5 doesn't)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
EFFORT_ANALYZE = os.getenv("JOBBOT_EFFORT_ANALYZE", "medium")
EFFORT_TAILOR = os.getenv("JOBBOT_EFFORT_TAILOR", "medium")

# USD per million tokens: (input, output, cache_read, cache_write_5m). Used only for the cost log.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

PROFILE_PATH = ROOT / "profile" / "master_profile.yaml"
GAPS_PATH = ROOT / "profile" / "gap_bullets.yaml"
OUTPUT_DIR = ROOT / "output"

# CV format (from the user's template: CVs/SheljaPugaliya_CV Devops main.pdf)
FONT = "Arial"
BODY_PT = 9
NAME_PT = 18
HEADING_PT = 11          # section headings; user asked for 9pt body, template headings are larger
HEADING_COLOR = "#2E75B6"
MAX_PAGES = 2

# Gap skills (JD skills you're building) on the CV:
#   listed   - added to the best-fitting Skills line like any other skill (user's choice, 2026-11-04)
#   learning - put on a separate "Currently Learning" line
#   off      - not on the CV, report only
# Never listed in any mode: certifications, degrees, languages, years (cv_listable = false).
GAP_SKILLS_IN_CV = os.getenv("JOBBOT_GAP_SKILLS_IN_CV", "listed")

# Real submission switch. Until this is "true", apply-queue and daily only fill forms + screenshot.
AUTO_SUBMIT = os.getenv("JOBBOT_AUTO_SUBMIT", "false").lower() == "true"

# Review vs auto-submit: each day's top N tailored jobs (by match score) go to the user for review,
# the rest go to the auto-submit queue (user's choice, 2026-11-04).
REVIEW_TOP = int(os.getenv("JOBBOT_REVIEW_TOP", "3"))
