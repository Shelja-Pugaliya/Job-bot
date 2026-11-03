"""Settings. Everything tunable lives here or in .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# The SDK reads ANTHROPIC_API_KEY; the user's .env uses CLAUDE_API_KEY.
API_KEY = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")

MODEL = os.getenv("JOBBOT_MODEL", "claude-opus-5-5")
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

# Match-score thresholds (module 6 uses these for review vs auto-submit)
HIGH_MATCH = 75
