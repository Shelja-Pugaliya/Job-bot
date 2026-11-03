"""Module 2: job finder. Poll public ATS job boards, keep Irish/remote-Europe roles that fit the
profile, pre-score them for free with keywords, and store new ones in the tracker DB.

LinkedIn, Indeed and IrishJobs aren't scraped (against their terms, and accounts/IPs get blocked).
For those, paste the job text into a file and use `python -m jobbot run --jd <file>`.
"""
import html
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict

import yaml

from .config import ROOT
from .profile import load_master, verified_view, verified_skill_names
from . import tracker

COMPANIES_PATH = ROOT / "profile" / "companies.yaml"

IRELAND = re.compile(r"\b(ireland|dublin|cork|galway|limerick|waterford|\bIE\b)", re.I)
REMOTE_OK = re.compile(r"remote.*\b(emea|europe|eu|ireland)\b|\b(emea|europe|eu|ireland)\b.*remote", re.I)

TITLE_INCLUDE = re.compile(
    r"devops|\bsre\b|site reliability|reliability|platform|infrastructure|cloud engineer|"
    r"software engineer|software developer|developer|backend|back-end|full[ -]?stack|"
    r"data engineer|data scientist|machine learning|\bml\b|\bai\b engineer|ai engineer|"
    r"salesforce|business analyst|production support|support engineer|release engineer|"
    r"build engineer|automation engineer|observability|integration engineer|solutions engineer",
    re.I)
TITLE_EXCLUDE = re.compile(
    r"\b(staff|principal|director|head of|vp|vice president|manager|lead|intern|internship|"
    r"graduate|new grad|apprentice|architect|distinguished|chief)\b|"
    r"\b(account executive|sales|recruit\w*|marketing|customer success|account manager|pre-sales|presales)\b|"
    r"\b(german|french|spanish|italian|dutch|nordic|swedish|danish|norwegian|finnish|"
    r"portuguese|japanese|polish|korean|mandarin)\b",
    re.I)
SENIOR = re.compile(r"\b(senior|sr\.?)\b", re.I)

TRACK_HINTS = {
    "devops": r"devops|\bsre\b|reliability|platform|infrastructure|cloud|release|build|observability",
    "swe": r"software|developer|backend|back-end|full[ -]?stack|frontend|engineer",
    "data": r"data|machine learning|\bml\b",
    "genai": r"\bai\b|llm|machine learning",
    "salesforce": r"salesforce",
    "ba": r"business analyst",
}


@dataclass
class Job:
    id: str
    source: str
    company: str
    title: str
    location: str
    url: str
    posted: str
    description: str
    prescore: int = 0
    senior: bool = False
    track: str = ""


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "jobbot/0.1 (personal job search)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def _text(raw_html: str) -> str:
    t = html.unescape(raw_html or "")
    t = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "- ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def fetch_greenhouse(token: str) -> list[Job]:
    d = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        company = j.get("company_name") or token
        out.append(Job(f"gh:{token}:{j['id']}", "greenhouse", company, j["title"],
                       j.get("location", {}).get("name", ""), j["absolute_url"],
                       (j.get("first_published") or j.get("updated_at") or "")[:10],
                       _text(j.get("content", ""))))
    return out


def fetch_lever(token: str) -> list[Job]:
    d = _get(f"https://api.lever.co/v0/postings/{token}?mode=json")
    out = []
    for j in d if isinstance(d, list) else []:
        cats = j.get("categories", {})
        locs = [cats.get("location", "")] + cats.get("allLocations", [])
        parts = [j.get("descriptionPlain", "")]
        for lst in j.get("lists", []):
            parts.append(lst.get("text", "") + "\n" + _text(lst.get("content", "")))
        parts.append(j.get("additionalPlain", ""))
        out.append(Job(f"lv:{token}:{j['id']}", "lever", token, j["text"], " / ".join(filter(None, locs)),
                       j["hostedUrl"], "", "\n\n".join(parts)))
    return out


def fetch_ashby(token: str) -> list[Job]:
    d = _get(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    out = []
    for j in d.get("jobs", []):
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", [])]
        if j.get("isRemote"):
            locs.append("Remote")
        out.append(Job(f"ab:{token}:{j['id']}", "ashby", token, j["title"], " / ".join(filter(None, locs)),
                       j.get("jobUrl", ""), (j.get("publishedAt") or "")[:10],
                       j.get("descriptionPlain") or _text(j.get("descriptionHtml", ""))))
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


def _location_ok(loc: str) -> bool:
    return bool(IRELAND.search(loc) or REMOTE_OK.search(loc))


def _skill_patterns(names: list[str]) -> list[re.Pattern]:
    cores = set()
    for n in names:
        for part in re.split(r"[/,()]", n):
            part = part.strip()
            if len(part) >= 2 and part.lower() not in {"and", "basic", "principles"}:
                cores.add(part)
    return [re.compile(rf"(?<![\w]){re.escape(c)}(?![\w])", re.I) for c in sorted(cores)]


def prescore(job: Job, patterns: list[re.Pattern]) -> int:
    """Free keyword score 0-100: how many verified profile skills the JD mentions, plus a title bonus."""
    text = job.title + "\n" + job.description
    hits = sum(1 for p in patterns if p.search(text))
    score = min(80, hits * 4)
    if re.search(TRACK_HINTS["devops"], job.title, re.I):
        score += 20
    elif re.search(TRACK_HINTS["swe"] + "|" + TRACK_HINTS["data"], job.title, re.I):
        score += 10
    if job.senior:
        score -= 10
    return max(0, min(100, score))


def _track(title: str) -> str:
    for t in ("salesforce", "ba", "devops", "genai", "data", "swe"):
        if re.search(TRACK_HINTS[t], title, re.I):
            return t
    return "swe"


def find(verbose: bool = True) -> list[Job]:
    with open(COMPANIES_PATH, encoding="utf8") as f:
        boards = yaml.safe_load(f)["companies"]

    def pull(b):
        try:
            return FETCHERS[b["ats"]](b["token"]), None
        except Exception as e:  # one broken board must not stop the run
            return [], f"{b['ats']}:{b['token']} failed: {e}"

    with ThreadPoolExecutor(12) as ex:
        results = list(ex.map(pull, boards))
    all_jobs = [j for jobs, _ in results for j in jobs]
    errors = [e for _, e in results if e]

    view = verified_view(load_master())
    patterns = _skill_patterns(verified_skill_names(view))

    kept = []
    for j in all_jobs:
        if not _location_ok(j.location):
            continue
        if not TITLE_INCLUDE.search(j.title) or TITLE_EXCLUDE.search(j.title):
            continue
        j.senior = bool(SENIOR.search(j.title))
        j.track = _track(j.title)
        j.prescore = prescore(j, patterns)
        kept.append(j)

    new = tracker.add_found([asdict(j) for j in kept])
    if verbose:
        print(f"Boards: {len(boards)} ({len(errors)} failed) · jobs fetched: {len(all_jobs)} · "
              f"Ireland/remote-EU + relevant title: {len(kept)} · new since last run: {len(new)}")
        for e in errors:
            print("  !", e)
    return [j for j in kept if j.id in new]
