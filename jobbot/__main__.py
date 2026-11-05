"""Job-bot CLI.

  python -m jobbot daily                         find -> rank -> tailor the best 10, write a daily report
  python -m jobbot find                          poll job boards, store new matching jobs (free)
  python -m jobbot rank --pool 25                analyze the best pre-scored jobs (cheap model)
  python -m jobbot next --n 10                   tailor the best-ranked jobs (main model)
  python -m jobbot list                          show saved jobs, export output/jobs.csv
  python -m jobbot run --jd path/to/jd.txt       analyze + tailor one pasted JD (LinkedIn etc.)
  python -m jobbot render output/<job folder>    re-render after hand-editing tailored.json
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from . import analyze as analyze_mod, gaps, guard, render, tailor as tailor_mod, tracker
from .config import (OUTPUT_DIR, REVIEW_TOP, RANK_POOL, DAILY_APPLY, MAX_YEARS, GAP_SKILLS_IN_CV,
                     AUTO_SUBMIT)
from .profile import load_master, load_gaps, verified_view, bullet_index, verified_skill_names

sys.stdout.reconfigure(encoding="utf-8")


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:40] or "job"


def _context(view: dict) -> str:
    return ("<candidate_profile>\nThis is the candidate's verified profile. It is the only source of "
            "facts about the candidate.\n" + json.dumps(view, ensure_ascii=False, indent=1)
            + "\n</candidate_profile>")


class Session:
    """Profile loaded once per command, shared by every job in the batch."""

    def __init__(self):
        self.refresh()

    def refresh(self):
        """Reload after the profile changes (e.g. inferred skills were added)."""
        self.master = load_master()
        self.view = verified_view(self.master)
        self.ctx = _context(self.view)


def _jd_from_row(r) -> str:
    return f"{r['title']}\n{r['company']} · {r['location']}\n{r['url']}\n\n{r['description']}"


# --------------------------------------------------------------------------- steps

def analyze_job(s: Session, jd: str, count_gaps: bool = True) -> tuple[dict, list[str], list[str]]:
    """Step 2: analysis with the rank model. Also records this job's gaps in gap_bullets.yaml
    (count_gaps=False when re-analyzing a job already counted)."""
    s.refresh()
    a = analyze_mod.analyze(jd, s.ctx, [g["skill"] for g in load_gaps() if g.get("status") != "known"])
    added, bumped = gaps.merge(a) if count_gaps else ([], [])
    record_implied(a)
    return a, added, bumped


def record_implied(a: dict) -> None:
    """Write skills the analysis treated as implied into the profile's skills list."""
    from . import profile_edit
    entries = [{"name": i["skill"], "note": f"implied by {i['implied_by']} (first needed by {a['company']})"}
               for i in a.get("implied_skills", [])]
    a["skills_added_to_profile"] = profile_edit.add_skills(entries, "inferred")


def tailor_job(s: Session, jd: str, a: dict, added=(), bumped=()) -> Path:
    """Step 3: tailored CV, cover letter, why-here answer and gap report with the main model."""
    out = OUTPUT_DIR / f"{date.today():%Y-%m-%d}_{_slug(a['company'])}_{_slug(a['role_title'])}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "jd.txt").write_text(jd, encoding="utf8")
    (out / "analysis.json").write_text(json.dumps(a, indent=2, ensure_ascii=False), encoding="utf8")
    s.refresh()  # pick up skills added since the session started (inferred or `know`)
    t = tailor_mod.tailor(jd, a, s.ctx)
    gap_skills = ([g["skill"] for g in a["gaps"]] + [g["cv_keyword"] for g in a["gaps"] if g.get("cv_keyword")]
                  + [g["skill"] for g in load_gaps() if g.get("status") != "known"])
    warnings = guard.check(t, bullet_index(s.view), verified_skill_names(s.view), gap_skills)
    a["gap_skills_in_cv"] = guard.add_gap_skills(t, a["gaps"], GAP_SKILLS_IN_CV)
    (out / "analysis.json").write_text(json.dumps(a, indent=2, ensure_ascii=False), encoding="utf8")
    (out / "tailored.json").write_text(json.dumps(t, indent=2, ensure_ascii=False), encoding="utf8")
    _render(out, t, s.master, a, warnings, added, bumped)
    return out


def run(jd_path: Path) -> None:
    s = Session()
    jd = jd_path.read_text(encoding="utf8")
    print("Analyzing JD…")
    a, added, bumped = analyze_job(s, jd)
    print(f"  {a['role_title']} @ {a['company']} | match={a['match_score']}"
          + (f" | DEALBREAKER: {a['dealbreaker_reason']}" if a["dealbreaker"] else ""))
    print("Tailoring CV + cover letter…")
    tailor_job(s, jd, a, added, bumped)


def cmd_find(top: int) -> None:
    from . import finder
    new = finder.find()
    new.sort(key=lambda j: -j.prescore)
    if not new:
        print("No new jobs since last run. Use `python -m jobbot list` to see saved jobs.")
    for j in new[:top]:
        flag = " (senior)" if j.senior else ""
        print(f"  {j.prescore:3}  {j.company[:18]:18} {j.title[:60]}{flag}\n       {j.location[:70]} · {j.url}")
    if len(new) > top:
        print(f"  … and {len(new) - top} more (`python -m jobbot list` to see all)")


def cmd_rank(pool: int, s: Session | None = None) -> list[dict]:
    """Analyze up to `pool` unanalyzed jobs. Returns a summary row per job analyzed."""
    s = s or Session()
    rows = tracker.to_rank(pool)
    print(f"Ranking {len(rows)} job(s)…")
    done = []
    for r in rows:
        try:
            a, _, _ = analyze_job(s, _jd_from_row(r))
        except Exception as e:
            print(f"  ! {r['company']} · {r['title']}: {e}")
            tracker.update(r["id"], notes=f"analyze failed: {e}")
            if "credit balance" in str(e):
                raise SystemExit("Stopping: API credit balance is too low.")
            continue
        if a.get("min_years_required", 0) > MAX_YEARS and not a["dealbreaker"]:
            a["dealbreaker"] = True
            a["dealbreaker_reason"] = f"requires {a['min_years_required']}+ years (you have {MAX_YEARS})"
        status = "skipped" if a["dealbreaker"] else "ranked"
        tracker.update(r["id"], status=status, match_score=a["match_score"],
                       analysis=json.dumps(a, ensure_ascii=False),
                       dealbreaker=a["dealbreaker_reason"] if a["dealbreaker"] else None)
        tag = f"  ✗ {a['dealbreaker_reason']}" if a["dealbreaker"] else ""
        print(f"  {a['match_score']:3}  {r['company'][:18]:18} {r['title'][:55]}{tag}")
        done.append({"row": r, "a": a})
    return done


def cmd_next(n: int, s: Session | None = None) -> list[dict]:
    """Tailor the n best-ranked jobs. The top REVIEW_TOP (highest match) go to the user for review,
    the rest to the auto-submit queue."""
    s = s or Session()
    rows = tracker.best_ranked(n)  # already sorted by match score, best first
    if not rows:
        print("Nothing ranked yet. Run `python -m jobbot rank` (or `daily`).")
    picked = []
    for i, r in enumerate(rows):
        a = json.loads(r["analysis"])
        status = "review" if i < REVIEW_TOP else "tailored"
        a["next_step"] = status
        print(f"\n=== {a['match_score']}  {r['company']} · {r['title']}  → "
              f"{'REVIEW' if status == 'review' else 'auto-submit queue'}")
        try:
            out = tailor_job(s, _jd_from_row(r), a)
        except Exception as e:
            print(f"  ! tailoring failed: {e}")
            tracker.update(r["id"], notes=f"tailor failed: {e}")
            if "credit balance" in str(e):
                raise SystemExit("Stopping: API credit balance is too low.")
            continue
        tracker.update(r["id"], status=status, folder=str(out))
        picked.append({"row": r, "a": a, "folder": out, "status": status})
    return picked


def cmd_tailor(query: str, reanalyze: bool = False) -> None:
    """Tailor one saved job, picked by id or by a piece of its title/company."""
    with tracker.db() as con:
        rows = con.execute("SELECT * FROM jobs WHERE id=? OR (company || ' ' || title) LIKE ?",
                           (query, f"%{query}%")).fetchall()
    if len(rows) != 1:
        print(f"{len(rows)} jobs match '{query}'. Be more specific:" if rows else f"No job matches '{query}'.")
        for r in rows[:15]:
            print(f"  {r['id']}  {r['company']} · {r['title']}")
        return
    r = rows[0]
    s = Session()
    jd = _jd_from_row(r)
    if r["analysis"] and not reanalyze:
        a = json.loads(r["analysis"])
    else:
        print("Analyzing…")
        a, _, _ = analyze_job(s, jd, count_gaps=not r["analysis"])
        tracker.update(r["id"], analysis=json.dumps(a, ensure_ascii=False), match_score=a["match_score"])
    print(f"=== {a['match_score']}  {r['company']} · {r['title']}")
    a["next_step"] = "review"  # a job you picked by hand always comes to you
    out = tailor_job(s, jd, a)
    tracker.update(r["id"], status="review", folder=str(out))


def _find_one(query: str):
    with tracker.db() as con:
        rows = con.execute("SELECT * FROM jobs WHERE id=? OR (company || ' ' || title) LIKE ?",
                           (query, f"%{query}%")).fetchall()
    if len(rows) != 1:
        print(f"{len(rows)} jobs match '{query}'. Be more specific:" if rows else f"No job matches '{query}'.")
        for r in rows[:15]:
            print(f"  {r['id']}  {r['company']} · {r['title']}")
        return None
    return rows[0]


def cmd_apply(query: str, submit: bool, headed: bool) -> None:
    """Plan + fill one tailored Greenhouse job. Submits only with --submit."""
    from . import applier
    r = _find_one(query)
    if r is None:
        return
    if r["source"] != "greenhouse":
        print(f"Auto-apply supports Greenhouse only so far; this job is on {r['source']}. Apply at {r['url']}")
        return
    if not r["folder"]:
        print("Tailor it first: python -m jobbot tailor \"" + query + "\"")
        return
    folder = Path(r["folder"])
    s = Session()
    print(f"Planning answers for {r['company']} · {r['title']}…")
    app = applier.plan(r, folder, s.ctx, s.master)
    for name, a in app["answers"].items():
        v = a["value"] if not a["value"].startswith("__") else a["value"].strip("_").lower() + " file"
        print(f"  ✓ {a['label'][:55]:55} → {v[:60]}  ({a['source'][:40]})")
    if app["pending"]:
        print("\nNeeds your answer (run `python -m jobbot answer`):")
        for q in app["pending"]:
            print(f"  ? {q['label'][:90]}  [{q['reason']}]" + (f"  options: {q['options']}" if q["options"] else ""))
        tracker.update(r["id"], status="needs_input", notes="answers needed: " +
                       "; ".join(q["label"][:60] for q in app["pending"]))
        if submit:
            print("Not submitting until those are answered.")
            return
        print("(Dry run continues with those left blank.)")
    mode = "Filling and SUBMITTING" if submit else "Filling (dry run, nothing is submitted)"
    print(f"\n{mode}…")
    res = applier.fill(app, folder, submit=submit, headed=headed)
    for n in res["notes"]:
        print("  !", n)
    print(f"  status: {res['status']} · screenshot: {res['screenshot']}")
    if submit:
        tracker.update(r["id"], status=res["status"] if res["status"] == "applied" else "needs_input",
                       notes="; ".join(res["notes"]) or "submitted")


def cmd_apply_queue(limit: int = DAILY_APPLY) -> None:
    """Apply to the auto-submit queue (status 'tailored'). Submits only if JOBBOT_AUTO_SUBMIT=true.
    Review jobs (your top 3) are never auto-submitted: use `apply "<job>" --submit` after reviewing."""
    with tracker.db() as con:
        rows = con.execute("SELECT id, source, company, title, url FROM jobs WHERE status='tailored' "
                           "ORDER BY match_score DESC LIMIT ?", (limit,)).fetchall()
    print(f"Auto-submit queue: {len(rows)} job(s) · real submission is {'ON' if AUTO_SUBMIT else 'OFF (dry run)'}")
    for r in rows:
        print(f"\n--- {r['company']} · {r['title']}")
        if r["source"] != "greenhouse":
            print(f"  skipped: {r['source']} isn't supported yet. Apply by hand at {r['url']}")
            continue
        try:
            cmd_apply(r["id"], submit=AUTO_SUBMIT, headed=False)
        except Exception as e:
            print(f"  ! failed: {e}")
            tracker.update(r["id"], notes=f"apply failed: {e}")


def cmd_answer() -> None:
    """Ask the user every pending application question once; answers are saved for reuse."""
    from . import applier
    with tracker.db() as con:
        rows = con.execute("SELECT * FROM jobs WHERE status='needs_input' AND folder IS NOT NULL").fetchall()
    seen, saved = set(), applier.load_answers()
    asked = 0
    for r in rows:
        plan_path = Path(r["folder"]) / "application_plan.json"
        if not plan_path.exists():
            continue
        for q in json.loads(plan_path.read_text(encoding="utf8"))["pending"]:
            key = applier._norm_q(q["label"])
            if key in seen or key in saved:
                continue
            seen.add(key)
            print(f"\n[{r['company']} · {r['title']}]\n{q['label']}")
            if q["options"]:
                for i, o in enumerate(q["options"], 1):
                    print(f"  {i}. {o}")
                raw = input("Choose a number (Enter to skip): ").strip()
                ans = q["options"][int(raw) - 1] if raw.isdigit() and 0 < int(raw) <= len(q["options"]) else ""
            else:
                ans = input("Your answer (Enter to skip): ").strip()
            if ans:
                applier.save_answer(q["label"], ans)
                asked += 1
    print(f"\nSaved {asked} answer(s) to profile/answers.yaml. Re-run `apply` for those jobs."
          if asked else "\nNo new questions waiting.")


def cmd_daily(pool: int, n: int) -> None:
    s = Session()
    print("1/4 Finding jobs…")
    cmd_find(top=0)
    print("\n2/4 Ranking…")
    ranked = cmd_rank(pool, s)
    print("\n3/4 Tailoring the best…")
    picked = cmd_next(n, s)
    print("\n4/4 Applying to the auto-submit queue…")
    cmd_apply_queue(n)
    path = daily_report(ranked, picked)
    print(f"\nDaily report: {path}\nTracker: {tracker.counts()}")


# --------------------------------------------------------------------------- reports

def daily_report(ranked: list[dict], picked: list[dict]) -> Path:
    picked_ids = {p["row"]["id"] for p in picked}
    L = [f"# Daily job run — {date.today():%d %B %Y}", "",
         f"Analyzed {len(ranked)} new job(s), tailored {len(picked)}.", ""]

    def must_gaps(a):
        return [r["skill"] for r in a["requirements"] if r["importance"] == "must" and r["coverage"] == "gap"]

    near = [x for x in ranked if not x["a"]["dealbreaker"] and 1 <= len(must_gaps(x["a"])) <= 2]
    if near:
        L += ["## 🎯 Close matches: 1–2 must-have skills away", "",
              "These skills are already listed on the CVs sent for these jobs, so build them before "
              "any interview. Once built: `python -m jobbot know <skill>`. For an experience bullet, "
              "set `status: built` in `profile/gap_bullets.yaml`.", ""]
        for x in sorted(near, key=lambda x: -x["a"]["match_score"]):
            a, r = x["a"], x["row"]
            L.append(f"### {a['match_score']} · {r['company']} — [{r['title']}]({r['url']})")
            missing = must_gaps(a)
            by_skill = {g["skill"].lower(): g for g in a["gaps"]}
            for skill in missing:
                g = by_skill.get(skill.lower()) or next(
                    (g for g in a["gaps"] if g["importance"] == "must"
                     and (skill.lower() in g["skill"].lower() or g["skill"].lower() in skill.lower())), None)
                L.append(f"- **Missing: {skill}**")
                if g:
                    L += [f"  - Bullet: {g['bullet']}", f"  - Builds on: `{g['anchor_id']}`",
                          f"  - Plan: {g['build_plan']} ({g['effort']})"]
                else:
                    L.append("  - No quick build. It needs real experience (see that job's gap report).")
            L.append("")

    if picked:
        L += ["## Tailored today", "",
              "| Score | Company | Role | Keywords | Must-have gaps | Next step | Folder |",
              "|---|---|---|---|---|---|---|"]
        for p in picked:
            a, r = p["a"], p["row"]
            kw = f"{len(a['keyword_hits'])}/{len(a['ats_keywords'])}"
            nxt = "**review**" if p["status"] == "review" else "auto-submit queue"
            L.append(f"| {a['match_score']} | {r['company']} | [{r['title']}]({r['url']}) | {kw} | "
                     f"{', '.join(must_gaps(a)) or '—'} | {nxt} | `{p['folder'].name}` |")
        L.append("")

    rest = sorted([x for x in ranked if x["row"]["id"] not in picked_ids], key=lambda x: -x["a"]["match_score"])
    if rest:
        L += ["## Analyzed, not tailored today", "",
              "Ranked lower than today's picks, or blocked by a dealbreaker. Ranked jobs stay in the queue.", "",
              "| Score | Company | Role | Must-have gaps | Why not today |", "|---|---|---|---|---|"]
        for x in rest:
            a, r = x["a"], x["row"]
            why = f"✗ {a['dealbreaker_reason']}" if a["dealbreaker"] else "lower score"
            L.append(f"| {a['match_score']} | {r['company']} | [{r['title']}]({r['url']}) | "
                     f"{', '.join(must_gaps(a)) or '—'} | {why} |")
        L.append("")

    counter = Counter(g["skill"] for x in ranked for g in x["a"]["gaps"])
    if counter:
        L += ["## Skills to build (asked for by today's jobs)", "",
              "Each has a ready bullet and build plan in `profile/gap_bullets.yaml`.", "",
              "| Skill | Jobs asking today | Seen in total |", "|---|---|---|"]
        totals = {g["skill"]: g.get("seen_in", 0) for g in load_gaps()}
        for skill, c in counter.most_common(15):
            L.append(f"| {skill} | {c} | {totals.get(skill, c)} |")
        L.append("")

    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"daily_{date.today():%Y-%m-%d}.md"
    path.write_text("\n".join(L) + "\n", encoding="utf8")
    return path


def _render(out: Path, t: dict, master: dict, a: dict, warnings: list[str],
            added=(), bumped=()) -> None:
    print("  rendering…")
    stem = f"Shelja_Pugaliya_CV_{_slug(a['company'])}"
    cv, pages, notes = render.render_all(render.build_cv(t, master), t["cover_letter"], out, stem)
    (out / "why_here.txt").write_text(render.plain(t["why_here"]), encoding="utf8")
    report = gap_report(a, cv, warnings + notes, pages, added, bumped)
    (out / "gap_report.md").write_text(report, encoding="utf8")
    print(f"  {pages} page(s). Guard notes: {len(warnings)}. Output: {out}")


def gap_report(a: dict, cv: dict, notes: list[str], pages: int, added, bumped) -> str:
    cv_text = json.dumps(cv, ensure_ascii=False).lower()
    kw_hit = [k for k in a["ats_keywords"] if k.lower() in cv_text]
    kw_miss = [k for k in a["ats_keywords"] if k.lower() not in cv_text]
    decision = ("REVIEW (one of today's top picks)" if a.get("next_step", "review") == "review"
                else "auto-submit queue")
    L = [f"# {a['role_title']} — {a['company']}", "",
         f"**Match score: {a['match_score']}/100** → {decision}  ",
         f"Required skills & qualifications met: {a.get('requirement_score', '?')}/100 · "
         f"JD keywords in your profile: {len(a.get('keyword_hits', []))}/{len(a['ats_keywords'])}  ",
         f"Track: {a['track']} · Seniority: {a['seniority']} · {a['work_mode']} · {a['location']}"
         + (f" · Salary: {a['salary_text']}" if a["salary_text"] else ""), "",
         a["summary"], ""]
    if a.get("dealbreaker"):
        L += [f"## ✗ Dealbreaker", a["dealbreaker_reason"], ""]
    on_cv = a.get("gap_skills_in_cv", [])
    if on_cv:
        L += ["## 🔨 On your CV: build these before an interview", "",
              "Added to your Skills section because the JD asks for them. Plans are below; "
              "once built, `python -m jobbot know <skill>`.", ""]
        for kw in on_cv:
            g = next((g for g in a["gaps"] if (g.get("cv_keyword") or "") == kw or g["skill"].startswith(kw)), None)
            L.append(f"- **{kw}**" + (f" [{g['importance']}] — {g['build_plan']} ({g['effort']})" if g else ""))
        L.append("")
    not_listed = [g for g in a["gaps"] if g.get("cv_listable") is False]
    if not_listed:
        L += ["## Not on your CV (credentials/facts you can't list until you have them)", ""]
        L += [f"- {g['skill']} [{g['importance']}] — {g['build_plan']} ({g['effort']})" for g in not_listed]
        L.append("")
    implied = a.get("implied_skills", [])
    if implied:
        new = set(a.get("skills_added_to_profile", []))
        L += ["## Implied skills (counted as covered)", "",
              "Standard parts of skills you have. New ones were added to the `inferred` list in "
              "profile/master_profile.yaml. Delete any that aren't right.", ""]
        L += [f"- {i['skill']} ← {i['implied_by']}{' · **added to profile**' if i['skill'] in new else ''}"
              for i in implied]
        L.append("")
    if a["red_flags"]:
        L += ["## ⚠ Red flags", *[f"- {r}" for r in a["red_flags"]], ""]
    L += [f"## ATS keywords in tailored CV: {len(kw_hit)}/{len(a['ats_keywords'])}",
          f"Missing: {', '.join(kw_miss) or 'none'}", ""]
    for label, cov in (("Covered", "covered"), ("Partial", "partial"), ("Gaps", "gap")):
        rows = [r for r in a["requirements"] if r["coverage"] == cov]
        if rows:
            L.append(f"## {label} ({len(rows)})")
            for r in rows:
                ev = f" — {', '.join(r['evidence_ids'])}" if r["evidence_ids"] else ""
                note = f" ({r['note']})" if r["note"] else ""
                L.append(f"- [{r['importance']}] {r['skill']}{ev}{note}")
            L.append("")
    if a["gaps"]:
        L += ["## Gap bullets you could build", "",
              "Not used in the CV. Build it, then set `status: built` in profile/gap_bullets.yaml.", ""]
        for g in a["gaps"]:
            tag = "new" if g["skill"] in added else ("seen before" if g["skill"] in bumped else "")
            L += [f"### {g['skill']} [{g['importance']}]{' · ' + tag if tag else ''}",
                  f"- **Bullet:** {g['bullet']}",
                  f"- **Builds on:** {g['anchor_id']}",
                  f"- **Plan:** {g['build_plan']}",
                  f"- **Effort:** {g['effort']}", ""]
    L += [f"## Checks", f"- CV pages: {pages}"]
    L += [f"- {n}" for n in notes] or ["- honesty guard: no changes needed"]
    return "\n".join(L) + "\n"


def cmd_list(status: str | None, top: int) -> None:
    """Print saved jobs and export all of them to output/jobs.csv (opens in Excel)."""
    import csv
    rows = tracker.all_jobs(status)
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / "jobs.csv"
    cols = ["match_score", "prescore", "status", "company", "title", "location", "senior", "track",
            "dealbreaker", "posted", "url", "folder"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel shows accents
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([("yes" if r[c] else "") if c == "senior" else r[c] for c in cols])
    for r in rows[:top]:
        score = f"{r['match_score']:3}" if r["match_score"] is not None else f"~{r['prescore']:2}"
        flag = " (senior)" if r["senior"] else ""
        print(f"  {score}  [{r['status']}] {r['company'][:18]:18} {r['title'][:60]}{flag}\n"
              f"       {r['location'][:70]} · {r['url']}")
    if len(rows) > top:
        print(f"  … {len(rows) - top} more")
    print(f"\n{len(rows)} job(s). Scores with ~ are keyword pre-scores (not analyzed yet).\n"
          f"Full list exported to {path}")


def cmd_know(skills: list[str]) -> None:
    """Add skills the user has to the profile; matching gap entries become status: known."""
    from . import profile_edit
    added = profile_edit.add_skills([{"name": s} for s in skills], "user_confirmed")
    retired = profile_edit.retire_gaps(skills)
    skipped = [s for s in skills if s not in added]
    if added:
        print("Added to profile (skills.user_confirmed): " + ", ".join(added))
    if skipped:
        print("Already in profile: " + ", ".join(skipped))
    if retired:
        print("No longer suggested as gaps: " + ", ".join(retired))


def cmd_rescore() -> None:
    """Recompute match scores for analyzed jobs after a scoring change (free)."""
    s = Session()
    with tracker.db() as con:
        rows = con.execute("SELECT id, analysis FROM jobs WHERE analysis IS NOT NULL").fetchall()
    for r in rows:
        a = analyze_mod.rescore(json.loads(r["analysis"]), s.ctx)
        tracker.update(r["id"], match_score=a["match_score"], analysis=json.dumps(a, ensure_ascii=False))
    print(f"Rescored {len(rows)} job(s).")


def rerender(folder: Path) -> None:
    master = load_master()
    a = json.loads((folder / "analysis.json").read_text(encoding="utf8"))
    t = json.loads((folder / "tailored.json").read_text(encoding="utf8"))
    _render(folder, t, master, a, [])


def main():
    ap = argparse.ArgumentParser(prog="jobbot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daily", help="find -> rank -> tailor the best jobs, write a daily report")
    d.add_argument("--pool", type=int, default=RANK_POOL)
    d.add_argument("--n", type=int, default=DAILY_APPLY)
    f = sub.add_parser("find", help="poll job boards, store new matching jobs (free, no API calls)")
    f.add_argument("--top", type=int, default=25)
    rk = sub.add_parser("rank", help="analyze the best pre-scored jobs with the rank model")
    rk.add_argument("--pool", type=int, default=RANK_POOL)
    nx = sub.add_parser("next", help="tailor the best-ranked jobs with the main model")
    nx.add_argument("--n", type=int, default=DAILY_APPLY)
    ls = sub.add_parser("list", help="show saved jobs and export them to output/jobs.csv")
    ls.add_argument("--status", help="only this status, e.g. found, ranked, review, applied")
    ls.add_argument("--top", type=int, default=30)
    r = sub.add_parser("run", help="analyze + tailor one pasted JD")
    r.add_argument("--jd", required=True, type=Path)
    rr = sub.add_parser("render", help="re-render a job folder after editing tailored.json")
    rr.add_argument("folder", type=Path)
    sub.add_parser("rescore", help="recompute match scores from stored analyses (free)")
    tl = sub.add_parser("tailor", help="tailor one saved job by id or part of its company/title")
    tl.add_argument("query")
    tl.add_argument("--reanalyze", action="store_true", help="redo the analysis (e.g. after adding skills)")
    ap_ = sub.add_parser("apply", help="fill a tailored job's application form (dry run unless --submit)")
    ap_.add_argument("query")
    ap_.add_argument("--submit", action="store_true", help="actually submit the application")
    ap_.add_argument("--headed", action="store_true", help="show the browser window")
    sub.add_parser("answer", help="answer application questions the bot couldn't")
    sub.add_parser("apply-queue", help="apply to the auto-submit queue (dry run unless JOBBOT_AUTO_SUBMIT=true)")
    ma = sub.add_parser("mark-applied", help="record a job you finished applying to by hand")
    ma.add_argument("query")
    kn = sub.add_parser("know", help='add skills you have, e.g. know GraphQL "Model Context Protocol (MCP)"')
    kn.add_argument("skills", nargs="+")
    args = ap.parse_args()
    if args.cmd == "know":
        return cmd_know(args.skills)
    if args.cmd == "apply":
        return cmd_apply(args.query, args.submit, args.headed)
    if args.cmd == "answer":
        return cmd_answer()
    if args.cmd == "apply-queue":
        return cmd_apply_queue()
    if args.cmd == "mark-applied":
        r = _find_one(args.query)
        if r is not None:
            tracker.update(r["id"], status="applied", notes="applied (confirmed by user)")
            print(f"Marked applied: {r['company']} · {r['title']}")
        return
    if args.cmd == "rescore":
        return cmd_rescore()
    if args.cmd == "tailor":
        return cmd_tailor(args.query, args.reanalyze)
    if args.cmd == "daily":
        cmd_daily(args.pool, args.n)
    elif args.cmd == "find":
        cmd_find(args.top)
    elif args.cmd == "rank":
        cmd_rank(args.pool)
    elif args.cmd == "next":
        cmd_next(args.n)
    elif args.cmd == "list":
        cmd_list(args.status, args.top)
    elif args.cmd == "run":
        run(args.jd)
    else:
        rerender(args.folder)


if __name__ == "__main__":
    main()
