"""Job-bot CLI.

  python -m jobbot run --jd path/to/jd.txt      analyze + tailor + render one job
  python -m jobbot render output/<job folder>    re-render after hand-editing tailored.json
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from . import analyze as analyze_mod, gaps, guard, render, tailor as tailor_mod
from .config import OUTPUT_DIR, HIGH_MATCH
from .profile import load_master, load_gaps, verified_view, bullet_index, verified_skill_names

sys.stdout.reconfigure(encoding="utf-8")


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:40] or "job"


def _context(view: dict) -> str:
    return ("<candidate_profile>\nThis is the candidate's verified profile. It is the only source of "
            "facts about the candidate.\n" + json.dumps(view, ensure_ascii=False, indent=1)
            + "\n</candidate_profile>")


def run(jd_path: Path) -> Path:
    return run_text(jd_path.read_text(encoding="utf8"))


def run_text(jd: str) -> tuple[Path, dict]:
    master = load_master()
    view = verified_view(master)
    ctx = _context(view)
    gap_names = [g["skill"] for g in load_gaps()]

    print("Analyzing JD…")
    a = analyze_mod.analyze(jd, ctx, gap_names)
    out = OUTPUT_DIR / f"{date.today():%Y-%m-%d}_{_slug(a['company'])}_{_slug(a['role_title'])}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "jd.txt").write_text(jd, encoding="utf8")
    (out / "analysis.json").write_text(json.dumps(a, indent=2, ensure_ascii=False), encoding="utf8")
    print(f"  {a['role_title']} @ {a['company']} | track={a['track']} | match={a['match_score']}")

    added, bumped = gaps.merge(a)

    print("Tailoring CV + cover letter…")
    t = tailor_mod.tailor(jd, a, ctx)
    gap_skills = [g["skill"] for g in a["gaps"]] + gap_names
    warnings = guard.check(t, bullet_index(view), verified_skill_names(view), gap_skills)
    (out / "tailored.json").write_text(json.dumps(t, indent=2, ensure_ascii=False), encoding="utf8")

    _render(out, t, master, a, warnings, added, bumped)
    return out, a


def cmd_find(top: int) -> None:
    from . import finder
    new = finder.find()
    new.sort(key=lambda j: -j.prescore)
    if not new:
        print("No new jobs since last run.")
    for j in new[:top]:
        flag = " (senior)" if j.senior else ""
        print(f"  {j.prescore:3}  {j.company[:18]:18} {j.title[:60]}{flag}\n       {j.location[:70]} · {j.url}")
    if len(new) > top:
        print(f"  … and {len(new) - top} more (stored in data/jobs.db)")


def cmd_next(n: int, min_prescore: int) -> None:
    from . import tracker
    rows = tracker.queue(n, min_prescore)
    if not rows:
        print("Queue is empty. Run `python -m jobbot find` first.")
    for r in rows:
        print(f"\n=== {r['company']} · {r['title']} (prescore {r['prescore']})")
        jd = f"{r['title']}\n{r['company']} · {r['location']}\n{r['url']}\n\n{r['description']}"
        try:
            out, a = run_text(jd)
        except Exception as e:
            print(f"  failed: {e}")
            tracker.update(r["id"], notes=f"tailor failed: {e}")
            raise
        status = "review" if a["match_score"] >= HIGH_MATCH else "tailored"
        tracker.update(r["id"], status=status, match_score=a["match_score"], folder=str(out))
    print(f"\nTracker: {tracker.counts()}")


def _render(out: Path, t: dict, master: dict, a: dict, warnings: list[str],
            added=(), bumped=()) -> None:
    print("Rendering…")
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
    decision = "REVIEW (high match)" if a["match_score"] >= HIGH_MATCH else "auto-submit queue"
    L = [f"# {a['role_title']} — {a['company']}", "",
         f"**Match score: {a['match_score']}/100** → {decision}  ",
         f"Track: {a['track']} · Seniority: {a['seniority']} · {a['work_mode']} · {a['location']}"
         + (f" · Salary: {a['salary_text']}" if a["salary_text"] else ""), "",
         a["summary"], ""]
    if a["red_flags"]:
        L += ["## ⚠ Red flags", *[f"- {r}" for r in a["red_flags"]], ""]
    L += [f"## ATS keywords in CV: {len(kw_hit)}/{len(a['ats_keywords'])}",
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


def rerender(folder: Path) -> None:
    master = load_master()
    a = json.loads((folder / "analysis.json").read_text(encoding="utf8"))
    t = json.loads((folder / "tailored.json").read_text(encoding="utf8"))
    _render(folder, t, master, a, [])


def main():
    ap = argparse.ArgumentParser(prog="jobbot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="analyze + tailor + render one JD")
    r.add_argument("--jd", required=True, type=Path)
    rr = sub.add_parser("render", help="re-render a job folder after editing tailored.json")
    rr.add_argument("folder", type=Path)
    f = sub.add_parser("find", help="poll job boards, store new matching jobs (free, no API calls)")
    f.add_argument("--top", type=int, default=25)
    nx = sub.add_parser("next", help="analyze + tailor the top N queued jobs (uses API credits)")
    nx.add_argument("--n", type=int, default=10)
    nx.add_argument("--min-prescore", type=int, default=30)
    args = ap.parse_args()
    if args.cmd == "run":
        run(args.jd)
    elif args.cmd == "find":
        cmd_find(args.top)
    elif args.cmd == "next":
        cmd_next(args.n, args.min_prescore)
    else:
        rerender(args.folder)


if __name__ == "__main__":
    main()
