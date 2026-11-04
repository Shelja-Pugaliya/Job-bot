"""Module 5: applier for Greenhouse job boards.

1. plan  - fetch the form's questions from the public Greenhouse API and decide every answer:
           fixed rules (contact, sponsorship, EEO...) -> saved answers (profile/answers.yaml) ->
           Claude for the rest. Anything only the user can answer is marked needs_user, never guessed.
2. fill  - open the real form in Chromium, fill it, upload the CV + cover letter, screenshot it.
3. submit (only when asked) - click Submit and confirm the "thank you" page. CAPTCHA challenges and
           email verification codes are never bypassed: the job is handed to the user.
"""
import json
import re
import time
import urllib.request
from pathlib import Path

from ruamel.yaml import YAML

from . import llm
from .config import ROOT, MODEL

ANSWERS_PATH = ROOT / "profile" / "answers.yaml"
_yaml = YAML()
_yaml.width = 4096


# --------------------------------------------------------------------------- saved answers

def _norm_q(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", label.lower())).strip()


def load_answers() -> dict:
    if not ANSWERS_PATH.exists():
        return {}
    with open(ANSWERS_PATH, encoding="utf8") as f:
        return dict((_yaml.load(f) or {}).get("answers") or {})


def save_answer(label: str, answer: str) -> None:
    with open(ANSWERS_PATH, encoding="utf8") as f:
        doc = _yaml.load(f) or {}
    if not doc.get("answers"):
        doc["answers"] = {}
    doc["answers"][_norm_q(label)] = answer
    with open(ANSWERS_PATH, "w", encoding="utf8") as f:
        _yaml.dump(doc, f)


# --------------------------------------------------------------------------- form schema

def parse_job_id(job_id: str) -> tuple[str, str]:
    """'gh:tines:6220290004' -> ('tines', '6220290004')"""
    _, token, jid = job_id.split(":", 2)
    return token, jid


def fetch_form(token: str, jid: str) -> dict:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}?questions=true"
    req = urllib.request.Request(url, headers={"User-Agent": "jobbot/0.1 (personal job search)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def form_fields(form: dict) -> list[dict]:
    """Flatten the form into fields: {name, label, type, required, options, group}."""
    out = []

    def add(q, group):
        for f in q["fields"]:
            if f["type"] == "input_hidden":
                continue
            out.append({"name": f["name"], "label": q["label"], "type": f["type"],
                        "required": bool(q.get("required")), "group": group,
                        "options": [v["label"] for v in f.get("values", [])]})

    for q in form.get("questions", []):
        add(q, "question")
    for q in form.get("location_questions", []):
        add(q, "location")
    for block in form.get("compliance", []) or []:
        for q in block.get("questions", []):
            add(q, "eeo")
    for q in form.get("demographic_questions", {}).get("questions", []) if isinstance(
            form.get("demographic_questions"), dict) else []:
        add(q, "demographic")
    return out


# --------------------------------------------------------------------------- answering

def pick_option(options: list[str], *wanted: str) -> str | None:
    """Best option label for any of the wanted phrases (exact, then starts-with, then contains)."""
    low = [o.lower() for o in options]
    for w in wanted:
        w = w.lower()
        for test in (lambda o: o == w, lambda o: o.startswith(w), lambda o: w in o):
            for o, ol in zip(options, low):
                if test(ol):
                    return o
    return None


def _decline(options):
    return pick_option(options, "i don't wish to answer", "decline", "prefer not", "i do not wish")


def rule_answer(f: dict, p: dict, ab: dict, company: str) -> str | None:
    """Fixed answers for common fields. None = not handled here."""
    name, label, opts = f["name"], f["label"].lower(), f["options"]
    first, last = p["name"].split(" ", 1)
    direct = {
        "first_name": first, "last_name": last, "email": p["email"], "phone": p["phone"],
        "location": "Dublin, Ireland", "country": "Ireland",
        "resume": "__RESUME__", "cover_letter": "__COVER__",
    }
    if name in direct:
        return direct[name]
    if name in ("resume_text", "cover_letter_text"):
        return "__SKIP__"  # we upload files instead
    eeo = ab.get("eeo", {})
    if f["group"] in ("eeo", "demographic") or name in ("gender", "race", "hispanic_ethnicity", "veteran_status",
                                                        "disability_status"):
        if "gender" in name or "gender" in label:
            return pick_option(opts, eeo.get("gender", ""), "female", "woman") or _decline(opts)
        if "hispanic" in name or "hispanic" in label or "latino" in label:
            return pick_option(opts, "no") or _decline(opts)
        if "race" in name or "race" in label or "ethnic" in label:
            return pick_option(opts, "asian") or _decline(opts)
        if "veteran" in name or "veteran" in label:
            return pick_option(opts, "i am not a protected veteran", "not a protected veteran", "no") or _decline(opts)
        if "disab" in name or "disab" in label:
            return pick_option(opts, "no, i do not have a disability", "no") or _decline(opts)
        if "religio" in label:
            return pick_option(opts, eeo.get("religion", ""), "hindu") or _decline(opts)
        return _decline(opts)  # sexual orientation, pronouns etc.: user gave no answer -> decline

    rules = [
        (r"sponsor", "No" if ab.get("requires_sponsorship") == "No" else None),
        (r"(authori[sz]ed|eligible|legally|right) to work|work permit|permission to work", "Yes"),
        (r"(based|located|living|reside|live) in (the )?(republic of )?ireland|ireland based", "Yes"),
        (r"relocat", "Yes" if ab.get("willing_to_relocate") == "Yes" else None),
        (r"notice period|earliest start|when can you start|available to start|start date", ab.get("notice_period")),
        (r"(expected|desired|target) (salary|compensation|pay)|salary expectation|compensation expectation",
         ab.get("expected_salary")),
        (r"current (salary|compensation|base)", ab.get("current_salary")),
        (r"linkedin", p["linkedin"]),
        (r"github", p["github"]),
        (r"portfolio|personal (web)?site|^website", p["portfolio"]),
        (r"(keep|retain|hold).*(details|data|information|cv|resume).*(file|future)|future (job )?opportunit|talent (pool|community)",
         "Yes"),
        (r"previously (worked|been employed)|former employee|worked (at|for) .* before", "No"),
        (r"total years of (professional )?experience|how many years of experience do you have", "5"),
        (r"privacy (policy|notice)|consent to (the )?process|i (agree|acknowledge|confirm)|data protection", "Yes"),
    ]
    for pattern, ans in rules:
        if re.search(pattern, label) and ans:
            if f["type"].startswith("multi_value"):
                return pick_option(opts, ans, "yes" if ans == "Yes" else ans, "i agree", "i acknowledge")
            return ans
    if re.search(r"how did you (hear|find|learn)|where did you (hear|find|see)|source", label):
        if opts:
            return pick_option(opts, "company website", "careers page", "website", "job board", "other")
        return "Company careers page"
    return None


LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answers"],
    "properties": {"answers": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "answer", "needs_user", "reason"],
        "properties": {"name": {"type": "string"}, "answer": {"type": "string"},
                       "needs_user": {"type": "boolean"}, "reason": {"type": "string"}}}}},
}

LLM_INSTRUCTIONS = """\
You are filling a job application form for the candidate whose verified profile is above. The \
user message has the job, the candidate's answer bank, their "why this company" answer, and the \
form questions still unanswered. Return one entry per question name.

- For a select question, answer must be exactly one of its options, character for character.
- Answer only from the profile, answer bank and job description. If the honest answer needs a \
fact we don't have, set needs_user true and leave answer "". Always set needs_user for: work \
schedules, shifts, hours, travel or on-call willingness; background checks, criminal record, \
non-compete or legal declarations; referrals or people they know at the company; pronouns; \
salary questions with a currency or format the answer bank doesn't cover; and any yes/no or \
years question about experience with a skill that is not in the profile's skills (gaps are \
things the candidate is still building, so the candidate must answer those personally).
- Years of experience with a profile skill: use the skill's level (strong 4, working 3, basic 1), \
never more than 5.
- Free-text questions (why this role, tell us about a project): 60-150 words, first person, plain \
and specific, using only profile facts. Reuse the why-here answer when it fits.
- reason: a few words on where the answer came from, or why the user must answer.
"""


def plan(job_row, folder: Path, ctx: str, master: dict) -> dict:
    """Decide every answer for a Greenhouse job. Returns the plan (also saved to folder)."""
    token, jid = parse_job_id(job_row["id"])
    form = fetch_form(token, jid)
    fields = form_fields(form)
    p, ab = master["personal"], master["answer_bank"]
    saved = load_answers()
    answers, pending, todo = {}, [], []
    for f in fields:
        a = rule_answer(f, p, ab, job_row["company"])
        if a is None and _norm_q(f["label"]) in saved:
            a = str(saved[_norm_q(f["label"])])
        if a is None:
            todo.append(f)
        elif a != "__SKIP__":
            answers[f["name"]] = {"value": a, "label": f["label"], "type": f["type"], "source": "rule/saved"}

    if todo:
        why = (folder / "why_here.txt").read_text(encoding="utf8") if (folder / "why_here.txt").exists() else ""
        user = json.dumps({
            "job": {"company": job_row["company"], "title": job_row["title"],
                    "description": job_row["description"][:6000]},
            "answer_bank": ab, "why_here": why,
            "questions": [{"name": f["name"], "label": f["label"], "type": f["type"],
                           "required": f["required"], "options": f["options"]} for f in todo],
        }, ensure_ascii=False)
        res = llm.call_json(cached_context=ctx, instructions=LLM_INSTRUCTIONS, user=user, schema=LLM_SCHEMA,
                            effort="low", label="apply-answers", model=MODEL, max_tokens=16000)
        by_name = {r["name"]: r for r in res["answers"]}
        for f in todo:
            r = by_name.get(f["name"])
            ok = r and not r["needs_user"] and r["answer"] and (not f["options"] or r["answer"] in f["options"])
            if ok:
                answers[f["name"]] = {"value": r["answer"], "label": f["label"], "type": f["type"],
                                      "source": f"claude: {r['reason']}"}
            elif f["required"]:
                pending.append({"name": f["name"], "label": f["label"], "options": f["options"],
                                "reason": r["reason"] if r else "not answered"})

    for f in fields:  # required fields with no answer at all
        if f["required"] and f["name"] not in answers and f["name"] not in [x["name"] for x in pending] \
                and f["name"] not in ("resume_text", "cover_letter_text"):
            pending.append({"name": f["name"], "label": f["label"], "options": f["options"], "reason": "no answer"})

    result = {"job_id": job_row["id"], "url": f"https://job-boards.greenhouse.io/{token}/jobs/{jid}",
              "answers": answers, "pending": pending}
    (folder / "application_plan.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf8")
    return result


# --------------------------------------------------------------------------- browser

def _select_combo(page, field_id: str, value: str) -> bool:
    box = page.locator(f"#{field_id}")
    box.click()
    box.fill(value)
    opt = page.locator("[role=option]", has_text=value).first
    try:
        opt.wait_for(timeout=5000)
        opt.click()
        return True
    except Exception:
        box.press("Escape")
        return False


def fill(app_plan: dict, folder: Path, submit: bool = False, headed: bool = False) -> dict:
    """Fill the form. With submit=True also press Submit and confirm. Returns {status, notes, screenshot}."""
    from playwright.sync_api import sync_playwright

    resume = next(folder.glob("Shelja_Pugaliya_CV_*.pdf"), None)
    cover = folder / "Cover_Letter.pdf"
    notes = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="en-IE")
        page.goto(app_plan["url"], wait_until="networkidle", timeout=60000)

        # Fields on the live form that the API doesn't list (phone country picker)
        if page.locator("#country[role=combobox]").count() and "country" not in app_plan["answers"]:
            if not _select_combo(page, "country", "Ireland"):
                notes.append("could not set Country to Ireland")

        for name, a in app_plan["answers"].items():
            v, typ = a["value"], a["type"]
            try:
                if v == "__RESUME__":
                    page.set_input_files("#resume", str(resume))
                elif v == "__COVER__":
                    if cover.exists() and page.locator("#cover_letter").count():
                        page.set_input_files("#cover_letter", str(cover))
                elif name == "phone":
                    page.fill("#phone", v.replace(" ", ""))
                elif name == "location":
                    loc = page.locator("#candidate-location")
                    if loc.count():
                        loc.click()
                        loc.fill("Dublin")
                        opt = page.locator("[role=option]", has_text="Dublin").first
                        opt.wait_for(timeout=8000)
                        opt.click()
                elif typ.startswith("multi_value") or page.locator(f"#{name}[role=combobox]").count():
                    if not _select_combo(page, name, v):
                        notes.append(f"could not select '{v}' for {a['label'][:60]}")
                elif typ == "textarea" or typ == "input_text":
                    page.fill(f"#{name}", v)
                else:
                    notes.append(f"unsupported field type {typ} for {a['label'][:60]}")
            except Exception as e:
                notes.append(f"{a['label'][:60]}: {str(e).splitlines()[0][:120]}")

        shot = folder / "application_filled.png"
        page.screenshot(path=str(shot), full_page=True)
        status = "filled"

        if submit and not notes:
            page.locator("button[type=submit]").click()
            status, extra = _await_result(page)
            notes += extra
            page.screenshot(path=str(folder / "application_result.png"), full_page=True)
        elif submit:
            status = "needs_user"
            notes.append("not submitted because some fields could not be filled")
        browser.close()
    return {"status": status, "notes": notes, "screenshot": str(shot)}


def _await_result(page) -> tuple[str, list[str]]:
    deadline = time.time() + 45
    while time.time() < deadline:
        body = page.inner_text("body").lower()
        if re.search(r"thank you for (applying|your application)|application (has been )?(received|submitted)", body):
            return "applied", []
        if re.search(r"verification code|security code|check your email", body):
            return "needs_user", ["Greenhouse sent a verification code to your email; finish this one by hand"]
        if page.locator("iframe[src*='recaptcha'][src*='bframe']").count() and \
                page.locator("iframe[src*='bframe']").first.is_visible():
            return "needs_user", ["CAPTCHA challenge appeared; finish this one by hand"]
        errs = page.locator("[aria-invalid=true], .error, .field-error").all_inner_texts()
        if errs and any(e.strip() for e in errs):
            return "needs_user", ["form errors: " + "; ".join(e.strip()[:80] for e in errs if e.strip())[:300]]
        time.sleep(1.5)
    return "needs_user", ["no confirmation page after submitting; check by hand"]
