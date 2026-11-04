"""Module 3: JD analyzer. Extract requirements, map them to the profile, write gap bullets."""
import json

import re

from . import llm
from .config import EFFORT_ANALYZE, RANK_MODEL

TRACKS = ["devops", "sre", "swe", "data", "genai", "salesforce", "ba"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["company", "role_title", "location", "seniority", "work_mode", "salary_text",
                 "track", "requirements", "ats_keywords", "soft_skills", "gaps", "red_flags",
                 "dealbreaker", "dealbreaker_reason", "min_years_required", "implied_skills", "summary"],
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
                "required": ["skill", "cv_keyword", "cv_listable", "importance", "anchor_id", "bullet",
                             "build_plan", "effort"],
                "properties": {
                    "skill": {"type": "string"},
                    "cv_keyword": {"type": "string"},
                    "cv_listable": {"type": "boolean"},
                    "importance": {"type": "string", "enum": ["must", "nice"]},
                    "anchor_id": {"type": "string"},
                    "bullet": {"type": "string"},
                    "build_plan": {"type": "string"},
                    "effort": {"type": "string"},
                },
            },
        },
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "dealbreaker": {"type": "boolean"},
        "dealbreaker_reason": {"type": "string"},
        "implied_skills": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["skill", "implied_by"],
                "properties": {"skill": {"type": "string"}, "implied_by": {"type": "string"}},
            },
        },
        "min_years_required": {"type": "integer",
                               "description": "Minimum years of experience the JD requires; 0 if not stated."},
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
what's missing in note), "gap" when nothing in the profile supports it.
- Skills with source "user_confirmed" are skills the candidate has told us they have. Treat them \
exactly like any other listed skill: a JD requirement they meet is "covered" (evidence_ids may be \
empty), and never put them in gaps. Mark one "partial" only when the JD asks for a depth the \
skill clearly doesn't reach (e.g. "5+ years of X"). The same applies to source "inferred".
- Implied skills: a profile can't list every everyday skill. When the JD asks for something that \
is a standard, unavoidable part of a skill the profile does list, mark it "covered", put the \
profile skill it follows from in note ("implied by AWS"), and add it to implied_skills with the \
JD's wording. Examples: load balancers or ELB from AWS production work; Slack, email and ticket \
handling from production incident support; relational databases from SQL Server or PostgreSQL; \
HTTP status codes and headers from building REST APIs; Git branching from GitFlow. Do not use \
this for a distinct technology, language, product or certification that someone with the listed \
skill might well not know: GraphQL from REST, Kubernetes from Docker, Terraform from Ansible, Go \
from Python, or any vendor platform. Those stay "partial" or "gap" unless the profile names them.
- gaps: one entry per requirement with coverage "gap" or "partial" that the candidate could close \
with a small piece of real work. anchor_id is the profile bullet or project id the new work would \
extend. bullet is the CV line the candidate could honestly use after building it, written in the \
same style as the profile bullets (**bold** keywords). build_plan is the quickest concrete way to \
make that bullet true, and effort is a rough time. cv_keyword is the short name a CV skills \
list would use, in the JD's wording ("Kubernetes", "Helm", "SSL/TLS troubleshooting", "Tines"). \
cv_listable is true for a skill, tool, technology or practice the candidate can learn; false for \
anything that is a checkable credential or fact (certifications, licences, degrees, spoken \
languages, years of experience, clearances) or isn't a skill (shift patterns, travel). \
If an existing gap name in the list below \
matches, reuse its exact skill name. Skip gaps that cannot be closed quickly (for example a degree \
or years of experience) and mention them in red_flags instead.
- ats_keywords: the exact terms an ATS would scan for, in the JD's own wording.
- red_flags: anything that makes this job a poor fit or needs the candidate's attention: required \
years well above the candidate's, citizenship or security clearance, language requirements, salary \
below EUR 55,000, location outside Ireland without remote, or a role that is mainly something the \
candidate hasn't done.
- dealbreaker: true only when the candidate clearly cannot apply or would be screened out \
regardless of the CV: citizenship or security clearance they don't have, a required language \
other than English, a required minimum of more than 5 years' experience (the candidate has 5; \
"5+ years" is fine), a stated salary maximum below EUR 55,000, or a \
location outside Ireland with no remote option. Give the reason in dealbreaker_reason (else ""). \
"No visa sponsorship" is not a dealbreaker: the candidate is authorized to work in Ireland.
- track: which CV variant fits best.
- summary: two sentences on the role and how well the candidate fits.
"""


def analyze(jd_text: str, cached_context: str, existing_gap_names: list[str]) -> dict:
    user = (f"Existing gap names: {json.dumps(existing_gap_names)}\n\n"
            f"<job_description>\n{jd_text}\n</job_description>")
    result = llm.call_json(cached_context=cached_context, instructions=INSTRUCTIONS, user=user,
                           schema=SCHEMA, effort=EFFORT_ANALYZE, label="analyze", model=RANK_MODEL)
    # Ranking score: 70% required skills/qualifications met (must-haves weigh double), 30% JD keywords present.
    return rescore(result, cached_context)


def requirement_score(reqs: list[dict]) -> int:
    """Must-haves weigh 2, nice-to-haves 1; covered=1, partial=0.5, gap=0."""
    weight = {"must": 2, "nice": 1}
    credit = {"covered": 1.0, "partial": 0.5, "gap": 0.0}
    total = sum(weight[r["importance"]] for r in reqs)
    if not total:
        return 0
    got = sum(weight[r["importance"]] * credit[r["coverage"]] for r in reqs)
    return round(100 * got / total)


_KW_STOP = {"and", "or", "the", "of", "with", "in", "for", "a", "an", "to", "based", "tools", "tool",
            "experience", "environments", "environment", "skills", "knowledge"}


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _kw_tokens(s: str) -> list[str]:
    return [_stem(t) for t in re.split(r"[^a-z0-9+#.]+", s.lower().replace("-", " ")) if t and t not in _KW_STOP]


def keyword_coverage(keywords: list[str], profile_text: str) -> tuple[list[str], list[str]]:
    """Which JD keywords appear in the verified profile (roughly what an ATS would see).

    Word-level, ignoring hyphens, plurals and filler words: "infrastructure-as-code" matches
    "Infrastructure as Code". Single-word keywords must match a whole word ("Go" != "good");
    multi-word ones need at least 2/3 of their words present.
    """
    profile_words = set(_kw_tokens(profile_text))
    hits, misses = [], []
    for k in keywords:
        toks = _kw_tokens(re.sub(r"\s*\(.*?\)", "", k))
        if not toks:
            misses.append(k)
            continue
        share = sum(t in profile_words for t in toks) / len(toks)
        (hits if share >= (1.0 if len(toks) == 1 else 0.67) else misses).append(k)
    return hits, misses


def rescore(a: dict, profile_text: str) -> dict:
    """Recompute the deterministic scores on a stored analysis (no API call)."""
    a["requirement_score"] = requirement_score(a["requirements"])
    a["keyword_hits"], a["keyword_misses"] = keyword_coverage(a["ats_keywords"], profile_text)
    kw = len(a["keyword_hits"]) / max(1, len(a["ats_keywords"]))
    a["match_score"] = round(0.7 * a["requirement_score"] + 30 * kw)
    return a
