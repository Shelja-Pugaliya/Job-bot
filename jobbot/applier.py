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
    # Demographic questions are flat ({id, label, type, answer_options}, no "fields"); on the live
    # form they're react-select comboboxes whose element id is the question id.
    demo = form.get("demographic_questions")
    for q in (demo.get("questions") or []) if isinstance(demo, dict) else []:
        if "fields" in q:
            add(q, "demographic")
            continue
        out.append({"name": str(q["id"]), "label": q["label"], "type": q.get("type", "multi_value_single_select"),
                    "required": bool(q.get("required")), "group": "demographic",
                    "options": [o["label"] for o in q.get("answer_options", [])]})
    return out


# --------------------------------------------------------------------------- answering

def pick_option(options: list[str], *wanted: str) -> str | None:
    """Best option label for any of the wanted phrases (exact, then starts-with, then contains).
    Whole words only: "No" must not match "...sponsorship or renewal now"."""
    low = [o.lower() for o in options]
    for w in wanted:
        w = w.lower().strip()
        if not w:  # a blank answer-bank value must not match every option
            continue
        word = re.compile(rf"(?<!\w){re.escape(w)}(?!\w)")
        for test in (lambda o: o == w, lambda o: word.match(o), lambda o: word.search(o)):
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

    if not opts and re.search(r"(which|what) countr(y|ies).*(right|authori[sz]ed|eligible|permi)", label):
        return "Ireland"
    rules = [
        (r"(what|which) type of (visa|work permit|permit)|visa type", p.get("work_status", "").split(" (")[0] or None),
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
        if opts:  # never "College Careers Page / Event" etc.: the bot finds jobs on company boards
            ok = [o for o in opts if not re.search(r"college|universit|campus|event|referr|recruiter", o.lower())]
            return pick_option(ok, "company website", "careers website", "company careers", "careers page",
                               "website", "job board", "other")
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
            answers[f["name"]] = {"value": a, "label": f["label"], "type": f["type"], "source": "rule/saved",
                                  "required": f["required"]}

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
                                      "source": f"claude: {r['reason']}", "required": f["required"]}
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

def _by_id(page, field_id: str):
    # [id="..."] works for ids CSS can't take with "#": numeric ("353") or with brackets ("question_1[]")
    return page.locator(f'[id="{field_id}"]')


def _select_combo(page, field_id: str, value: str) -> bool:
    box = _by_id(page, field_id)
    if not box.count():
        return False
    for attempt in range(2):
        box.first.click()
        if attempt == 0:
            box.first.fill(value)
        else:  # slower retry: some pickers (phone country) only filter on real key presses
            box.first.fill("")
            page.keyboard.type(value, delay=60)
        # exact option text first ("No" must not hit "No options"), then a contains-match
        for opt in (page.get_by_role("option", name=value, exact=True).first,
                    page.locator("[role=option]", has_text=value).first):
            try:
                opt.wait_for(state="visible", timeout=4000)
                opt.click()
                return True
            except Exception:
                continue
        box.first.press("Escape")
    return False


def fill(app_plan: dict, folder: Path, submit: bool = False, headed: bool = False) -> dict:
    """Fill the form. With submit=True also press Submit and confirm. Returns {status, notes, screenshot}."""
    from playwright.sync_api import sync_playwright

    resume = folder / "Shelja_Pugaliya_CV.pdf"
    resume = resume if resume.exists() else None
    cover = folder / "Cover_Letter.pdf"
    notes = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        # Visible window: no fixed viewport, so the page fits the screen and scrolls to the bottom.
        page = browser.new_page(no_viewport=True, locale="en-IE") if headed else \
            browser.new_page(viewport={"width": 1280, "height": 900}, locale="en-IE")
        def _form_ready(timeout_ms: int) -> bool:
            try:
                _by_id(page, "first_name").wait_for(state="visible", timeout=timeout_ms)
                page.wait_for_timeout(1500)  # let the react widgets finish mounting
                return True
            except Exception:
                return False

        # Don't wait for "networkidle": company careers pages with videos never go idle.
        try:
            page.goto(app_plan["url"], wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        if not _form_ready(15000):
            # Some companies (MongoDB, Elastic) redirect the board URL to their own careers site and
            # load the form in an iframe. Greenhouse's embed URL serves the same form directly.
            token, jid = parse_job_id(app_plan["job_id"])
            page.goto(f"https://job-boards.greenhouse.io/embed/job_app?for={token}&token={jid}",
                      wait_until="domcontentloaded", timeout=60000)
            if not _form_ready(30000):
                notes.append(f"no application form found (page ended at {page.url})")

        # Fields on the live form that the API doesn't list (phone country picker)
        if page.locator("#country[role=combobox]").count() and "country" not in app_plan["answers"]:
            if not _select_combo(page, "country", "Ireland"):
                notes.append("could not set Country to Ireland")

        # The Race question only appears after "Are you Hispanic/Latino?" is answered, and the API
        # doesn't always list that one: answer it (No, per the user's EEO answers) and do Race last.
        answers = dict(app_plan["answers"])
        if _by_id(page, "hispanic_ethnicity").count() and "hispanic_ethnicity" not in answers:
            answers = {"hispanic_ethnicity": {"value": "No", "label": "Are you Hispanic/Latino?",
                                              "type": "multi_value_single_select", "source": "rule"}} | answers
        if "race" in answers:
            answers["race"] = answers.pop("race")

        special = {"resume", "cover_letter", "location"}
        for name, a in answers.items():
            v, typ = a["value"], a["type"]
            if name == "race" and _by_id(page, "hispanic_ethnicity").count():
                try:
                    _by_id(page, "race").wait_for(state="visible", timeout=5000)
                except Exception:
                    pass
            # The API lists fields some regional versions of the form don't show (US EEO on an
            # Irish posting). Skip those; a missing field the plan marks required is a real problem.
            if name not in special and not _by_id(page, name).count():
                if a.get("required"):
                    notes.append(f"required field not on the page: {a['label'][:60]}")
                continue
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
                elif _by_id(page, name).first.evaluate("e => e.tagName") == "FIELDSET":
                    # multi-select rendered as checkboxes: tick the one whose label matches
                    fs = _by_id(page, name).first
                    box = fs.get_by_label(v, exact=True)
                    if not box.count():
                        box = fs.get_by_label(v)
                    if box.count():
                        box.first.check()
                    else:
                        notes.append(f"no checkbox '{v}' for {a['label'][:60]}")
                elif typ.startswith("multi_value") or _by_id(page, name).first.get_attribute("role") == "combobox":
                    if not _select_combo(page, name, v):
                        notes.append(f"could not select '{v}' for {a['label'][:60]}")
                    elif typ == "multi_value_multi_select":
                        _by_id(page, name).press("Escape")  # close the menu that stays open
                elif typ == "textarea" or typ == "input_text":
                    _by_id(page, name).fill(v)
                else:
                    notes.append(f"unsupported field type {typ} for {a['label'][:60]}")
            except Exception as e:
                notes.append(f"{a['label'][:60]}: {str(e).splitlines()[0][:120]}")

        # Education block: on the live form but not in the API. Values are the profile's MSc.
        for fid, value in (("school--0", "Maynooth"), ("degree--0", "Master's Degree"),
                           ("discipline--0", "Computer Science")):
            if _by_id(page, fid).count() and not _select_combo(page, fid, value):
                notes.append(f"could not set {fid} to {value}")

        # GDPR consent for the demographic answers we just gave. Required on the live form, absent from
        # the API. The user chose to answer the EEO questions, so consenting to their processing is theirs.
        demo_answered = any(a.get("label") and re.search(r"gender|race|ethnic|veteran|disab|demograph", a["label"], re.I)
                            for a in answers.values())
        for c in page.locator("input[type=checkbox][name^=gdpr_demographic_data_consent_given]").all():
            if demo_answered and not c.is_checked():
                c.check()

        shot = folder / "application_filled.png"
        page.screenshot(path=str(shot), full_page=True)
        status = "filled"

        if submit and not notes:
            page.locator("button[type=submit]").click()
            status, extra = _await_result(page, wait_for_user=headed)
            notes += extra
            try:
                page.screenshot(path=str(folder / "application_result.png"), full_page=True)
            except Exception:
                pass
        elif submit:
            status = "needs_user"
            notes.append("not submitted because some fields could not be filled")
        try:
            browser.close()
        except Exception:
            pass
    return {"status": status, "notes": notes, "screenshot": str(shot)}


CODE_FILE = ROOT / "data" / "verification_code.txt"


def _enter_code(page, code: str) -> None:
    """Type Greenhouse's emailed verification code (one box per character, or a single box) and resubmit."""
    boxes = page.locator("input[id^=security-input], input[maxlength='1']:visible")
    if boxes.count() >= len(code):
        for i, ch in enumerate(code):
            boxes.nth(i).fill(ch)
    else:
        box = page.locator("input[name*=security], input[id*=security], input[aria-label*=ode i]").first
        if box.count():
            box.fill(code)
        else:
            boxes.first.click()
            page.keyboard.type(code, delay=80)
    print("  code entered, submitting…", flush=True)
    page.locator("button[type=submit]").last.click()


def _await_result(page, wait_for_user: bool = False) -> tuple[str, list[str]]:
    deadline, waiting = time.time() + 45, False
    while time.time() < deadline:
        try:
            body = page.inner_text("body").lower()
        except Exception:  # the user closed the window (usually right after submitting by hand)
            return "needs_user", ["browser window was closed before the confirmation page; "
                                  "check your email and confirm with `mark-applied`"]
        # The tab title counts too: some confirmation pages only say it there ("Thank you for applying").
        try:
            title = page.title().lower()
        except Exception:
            title = ""
        if re.search(r"thank(s| you) for (applying|your application)|application (has been |was )?(received|submitted)",
                     title + " " + body):
            return "applied", []
        if re.search(r"verification code|security code|check your email", body):
            if wait_for_user:
                # Wait up to 10 min for the code: typed in the window by the user, or written to
                # data/verification_code.txt (e.g. by Claude from chat), which the bot then types in.
                if not waiting:
                    CODE_FILE.unlink(missing_ok=True)
                    print(f"  Greenhouse emailed you a code. Type it in the window, or put it in {CODE_FILE}",
                          flush=True)
                    waiting, deadline = True, time.time() + 600
                if CODE_FILE.exists():
                    code = re.sub(r"\s", "", CODE_FILE.read_text(encoding="utf8"))
                    CODE_FILE.unlink(missing_ok=True)
                    if code:
                        _enter_code(page, code)
                time.sleep(2)
                continue
            return "needs_user", ["Greenhouse sent a verification code to your email: re-run with "
                                  "`apply \"<job>\" --submit --headed` and type the code in the window"]
        # Not "please select...": that is also ordinary question text on some forms (Stripe).
        m = re.search(r"please accept the terms[^\n]*|this field is required", body)
        if m:
            return "needs_user", [f"form error after submit: {m.group(0)[:100]}"]
        if page.locator("iframe[src*='recaptcha'][src*='bframe']").count() and \
                page.locator("iframe[src*='bframe']").first.is_visible():
            return "needs_user", ["CAPTCHA challenge appeared; finish this one by hand"]
        errs = page.locator("[aria-invalid=true], .error, .field-error, [class*='helper-text--error']").all_inner_texts()
        if errs and any(e.strip() for e in errs):
            return "needs_user", ["form errors: " + "; ".join(e.strip()[:80] for e in errs if e.strip())[:300]]
        time.sleep(1.5)
    return "needs_user", ["no confirmation page after submitting; check by hand"]
