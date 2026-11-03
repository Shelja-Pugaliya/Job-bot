"""Module 3: JD analyzer. Extract requirements, map them to the profile, write gap bullets."""
import json

from . import llm
from .config import EFFORT_ANALYZE

TRACKS = ["devops", "sre", "swe", "data", "genai", "salesforce", "ba"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["company", "role_title", "location", "seniority", "work_mode", "salary_text",
                 "track", "requirements", "ats_keywords", "soft_skills", "gaps", "red_flags", "summary"],
    "properties": {
        "company": {"type": "string"},
        "role_title": {"type": "string"},
        "location": {"type": "string"},
        "seniority": {"type": "string", "enum": ["junior", "mid", "senior", "lead", "unclear"]},
        "work_mode": {"type": "string", "enum": ["remote", "hybrid", "onsite", "unclear"]},
        "salary_text": {"type": "string", "description": "Salary as stated in the JD, or empty string."},
        "track": {"type": "string", "enum": TRACKS},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["skill", "importance", "coverage", "evidence_ids", "note"],
                "properties": {
                    "skill": {"type": "string"},
                    "importance": {"type": "string", "enum": ["must", "nice"]},
                    "coverage": {"type": "string", "enum": ["covered", "partial", "gap"]},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
            },
        },
        "ats_keywords": {"type": "array", "items": {"type": "string"}},
        "soft_skills": {"type": "array", "items": {"type": "string"}},
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["skill", "importance", "anchor_id", "bullet", "build_plan", "effort"],
                "properties": {
                    "skill": {"type": "string"},
                    "importance": {"type": "string", "enum": ["must", "nice"]},
                    "anchor_id": {"type": "string"},
                    "bullet": {"type": "string"},
                    "build_plan": {"type": "string"},
                    "effort": {"type": "string"},
                },
            },
        },
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
}

INSTRUCTIONS = """\
You are the JD-analysis step of a job-application assistant for the candidate whose verified \
profile is above. Read the job description in the user message and return the JSON object the \
schema describes.

- requirements: every distinct skill, tool, qualification or responsibility the JD asks for. Mark \
importance "must" for required/essential items and "nice" for preferred/bonus items. For coverage, \
judge against the profile only: "covered" when a profile bullet or skill clearly demonstrates it \
(list those bullet ids in evidence_ids), "partial" when there is related but weaker evidence (say \
what's missing in note), "gap" when nothing in the profile supports it. Do not count a skill as \
covered because it seems plausible for someone with this background; the profile is the only evidence.
- gaps: one entry per requirement with coverage "gap" or "partial" that the candidate could close \
with a small piece of real work. anchor_id is the profile bullet or project id the new work would \
extend. bullet is the CV line the candidate could honestly use after building it, written in the \
same style as the profile bullets (**bold** keywords). build_plan is the quickest concrete way to \
make that bullet true, and effort is a rough time. If an existing gap name in the list below \
matches, reuse its exact skill name. Skip gaps that cannot be closed quickly (for example a degree \
or years of experience) and mention them in red_flags instead.
- ats_keywords: the exact terms an ATS would scan for, in the JD's own wording.
- red_flags: anything that makes this job a poor fit or needs the candidate's attention: required \
years well above the candidate's, citizenship or security clearance, language requirements, salary \
below EUR 55,000, location outside Ireland without remote, or a role that is mainly something the \
candidate hasn't done.
- track: which CV variant fits best.
- summary: two sentences on the role and how well the candidate fits.
"""


def analyze(jd_text: str, cached_context: str, existing_gap_names: list[str]) -> dict:
    user = (f"Existing gap names: {json.dumps(existing_gap_names)}\n\n"
            f"<job_description>\n{jd_text}\n</job_description>")
    result = llm.call_json(cached_context=cached_context, instructions=INSTRUCTIONS, user=user,
                           schema=SCHEMA, effort=EFFORT_ANALYZE, label="analyze")
    result["match_score"] = match_score(result["requirements"])
    return result


def match_score(reqs: list[dict]) -> int:
    """Deterministic score: must-haves weigh 2, nice-to-haves 1; covered=1, partial=0.5, gap=0."""
    weight = {"must": 2, "nice": 1}
    credit = {"covered": 1.0, "partial": 0.5, "gap": 0.0}
    total = sum(weight[r["importance"]] for r in reqs)
    if not total:
        return 0
    got = sum(weight[r["importance"]] * credit[r["coverage"]] for r in reqs)
    return round(100 * got / total)
