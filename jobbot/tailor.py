"""Module 4: tailor. Pick and rephrase verified bullets for the JD, write cover letter + why-here answer."""
import json

from . import llm
from .config import EFFORT_TAILOR

_BULLET = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_id", "text"],
    "properties": {"source_id": {"type": "string"}, "text": {"type": "string"}},
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "summary", "skills", "experience", "projects", "cover_letter", "why_here"],
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["category", "items"],
                "properties": {"category": {"type": "string"},
                               "items": {"type": "array", "items": {"type": "string"}}},
            },
        },
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["role_id", "bullets", "key_technologies"],
                "properties": {"role_id": {"type": "string"},
                               "bullets": {"type": "array", "items": _BULLET},
                               "key_technologies": {"type": "array", "items": {"type": "string"}}},
            },
        },
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["project_id", "bullets", "key_technologies"],
                "properties": {"project_id": {"type": "string"},
                               "bullets": {"type": "array", "items": _BULLET},
                               "key_technologies": {"type": "array", "items": {"type": "string"}}},
            },
        },
        "cover_letter": {
            "type": "object",
            "additionalProperties": False,
            "required": ["greeting", "paragraphs", "sign_off"],
            "properties": {"greeting": {"type": "string"},
                           "paragraphs": {"type": "array", "items": {"type": "string"}},
                           "sign_off": {"type": "string"}},
        },
        "why_here": {"type": "string"},
    },
}

INSTRUCTIONS = """\
You are the tailoring step of a job-application assistant. Using only the verified profile above, \
write a CV and cover letter for the job in the user message. The user message also contains the \
analysis of that job.

The hard rule is honesty: every claim must trace to the profile. You may rephrase a bullet in the \
JD's wording, reorder it, merge two bullets from the same role, or shorten it, but you may not add \
a tool, technology, number, scope or outcome that the source bullet does not state. Each output \
bullet names the profile bullet it came from in source_id (for a merge, the main one). Skills you \
list must appear in the profile's skills or key_technologies. Skills marked as gaps in the analysis \
(gap_skills) must not appear in experience bullets, project bullets or the summary: the system adds \
them to the Skills section itself after you finish. Skills whose source is "inferred" or "user_confirmed" belong \
in the skills section only: list them where relevant, but don't write them into experience \
bullets, since no bullet records that work. A reviewer will check each bullet against its source and \
revert any that add facts, so a faithful bullet beats an impressive one.

The same rule covers the summary, cover letter and why_here: describe the candidate's work only \
with words the profile supports. Don't relabel it to match the JD (for example, don't call the \
Kaplan LMS "SaaS" or a role "customer-facing" unless the profile says so). And never speak for the \
candidate on things they haven't told us: availability, shift patterns or weekend work, start \
date, salary, relocation, travel, or willingness to accept any condition in the JD. Leave those \
for the candidate to answer.

CV shape (it must fit two A4 pages at 9pt Arial):
- headline: one line in the style "DevOps / Platform Engineer | Linux, CI/CD, Observability", \
using the JD's title wording where it is honest.
- summary: 3-4 sentences adapted from the matching track summary, aimed at this role. Don't name \
the company.
- skills: 6-8 categories ordered by relevance to the JD, most relevant items first, JD terms first. \
Include every profile skill that meets a "covered" or "partial" requirement in the analysis, so an \
ATS finds each JD keyword the candidate actually has.
- experience: every role in the profile, in profile order (most recent first). Unum 3-5 bullets, \
Kaplan Software Engineer I 3-5, Kaplan Associate 3-4; choose the bullets that best match the \
requirements. key_technologies: up to 10, JD-relevant first, taken from that role.
- projects: the 2 most relevant projects, 1-3 bullets each.
- Mark the JD's keywords and the metrics with **double asterisks** so they render bold. Aim for \
2-4 bold spans per bullet, not whole sentences.

Cover letter: 3-4 short paragraphs, under 300 words. Open with why this role and company (only \
facts from the JD), then two concrete matching achievements from the profile, then a brief close. \
Use the hiring manager's name if the JD gives one, else "Dear Hiring Manager,". Plain, warm and \
specific, no clichés like "I am writing to express my interest". sign_off is "Kind regards,\\nShelja Pugaliya".

why_here: a 90-140 word answer to "Why do you want to work here?" for an application form, \
grounded in the JD and the profile.
"""


def tailor(jd_text: str, analysis: dict, cached_context: str) -> dict:
    slim = {k: analysis[k] for k in ("company", "role_title", "track", "requirements",
                                     "ats_keywords", "soft_skills")}
    slim["gap_skills"] = [g["skill"] for g in analysis["gaps"]] + \
                         [g["cv_keyword"] for g in analysis["gaps"] if g.get("cv_keyword")]
    user = (f"<analysis>\n{json.dumps(slim, ensure_ascii=False)}\n</analysis>\n\n"
            f"<job_description>\n{jd_text}\n</job_description>")
    return llm.call_json(cached_context=cached_context, instructions=INSTRUCTIONS, user=user,
                         schema=SCHEMA, effort=EFFORT_TAILOR, label="tailor")
