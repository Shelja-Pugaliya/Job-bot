"""Render the tailored CV and cover letter to PDF (Chromium print) and DOCX (python-docx).

Layout follows the user's template CVs/SheljaPugaliya_CV Devops main.pdf: centred name, headline and
contact lines, a rule, summary, blue section headings, company + italic title, grey dates, bullets.
"""
import copy
import html
import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Mm
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from .config import FONT, BODY_PT, NAME_PT, HEADING_PT, HEADING_COLOR, MAX_PAGES

_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _month(ym: str) -> str:
    y, m = str(ym).split("-")
    return date(int(y), int(m), 1).strftime("%B %Y")


def _html_inline(text: str) -> str:
    return _BOLD.sub(r"<b>\1</b>", html.escape(text))


def _plain(text: str) -> str:
    return _BOLD.sub(r"\1", text)


# --------------------------------------------------------------------------- CV model

def build_cv(tailored: dict, master: dict) -> dict:
    """Merge LLM output with fixed facts (contact, dates, education) into one renderable model."""
    p = master["personal"]
    roles = {e["id"]: e for e in master["experience"]}
    projects = {pr["id"]: pr for pr in master["projects"]}
    order = [e["id"] for e in master["experience"]]
    exp = sorted(tailored["experience"], key=lambda r: order.index(r["role_id"]) if r["role_id"] in order else 99)
    return {
        "name": p["name"],
        "headline": tailored["headline"],
        "contact": [p["work_status"].split(" (")[0], p["email"], p["phone"],
                    ("linkedin.com/in/sheljapugaliya", p["linkedin"])],
        "contact2": [("Portfolio: shelportfolio.vercel.app", p["portfolio"]),
                     ("GitHub: github.com/Shelja-Pugaliya", p["github"])],
        "summary": tailored["summary"],
        "skills": tailored["skills"],
        "experience": [
            {"company": roles[r["role_id"]]["company"], "location": roles[r["role_id"]]["location"],
             "title": roles[r["role_id"]]["title"],
             "dates": f"{_month(roles[r['role_id']]['start'])} - {_month(roles[r['role_id']]['end'])}",
             "bullets": [b["text"] for b in r["bullets"]], "tech": r["key_technologies"]}
            for r in exp if r["role_id"] in roles and r["bullets"]
        ],
        "projects": [
            {"name": projects[pr["project_id"]]["name"], "bullets": [b["text"] for b in pr["bullets"]],
             "tech": pr["key_technologies"]}
            for pr in tailored["projects"] if pr["project_id"] in projects and pr["bullets"]
        ],
        "education": [
            {"school": e["school"], "location": e["location"], "degree": e["degree"],
             "dates": f"{_month(e['start'])} - {_month(e['end'])}",
             "bullets": [b["text"] if isinstance(b, dict) else b for b in e.get("bullets", [])]}
            for e in master["education"]
        ],
        "certifications": master.get("certifications", []),
        "awards": master.get("awards", []),
    }


# --------------------------------------------------------------------------- HTML / PDF

CSS = f"""
@page {{ size: A4; margin: 11mm 14mm 11mm 14mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: {FONT}, Helvetica, sans-serif; font-size: {BODY_PT}pt; line-height: 1.28; color: #000; margin: 0; }}
a {{ color: #1155CC; text-decoration: underline; }}
.name {{ text-align: center; font-size: {NAME_PT}pt; font-weight: bold; margin: 0; }}
.headline, .contact {{ text-align: center; font-weight: bold; margin: 1pt 0 0 0; }}
.contact {{ margin-top: 5pt; }}
hr {{ border: 0; border-top: 0.6pt solid #999; margin: 5pt 0 6pt 0; }}
.summary {{ text-align: justify; margin: 0; }}
h2 {{ color: {HEADING_COLOR}; font-size: {HEADING_PT}pt; margin: 9pt 0 3pt 0; text-transform: uppercase; break-after: avoid-page; }}
h2 + div, h2 + .job, h2 + .proj {{ break-before: avoid-page; }}
.skills p {{ margin: 0 0 1pt 0; }}
.job {{ margin-top: 6pt; break-inside: avoid-page; }}
.jobhead {{ font-size: {BODY_PT + 1.5}pt; margin: 0; }}
.dates {{ color: #666; font-size: {BODY_PT - 0.5}pt; margin: 1pt 0 1pt 0; }}
ul {{ margin: 1pt 0 1pt 0; padding-left: 14pt; }}
li {{ margin: 0 0 1pt 0; text-align: justify; }}
.tech {{ margin: 1pt 0 0 0; }}
.proj {{ margin-top: 5pt; break-inside: avoid-page; }}
.letter p {{ margin: 0 0 8pt 0; text-align: justify; }}
"""


def cv_html(cv: dict) -> str:
    def contact_line(items):
        parts = []
        for it in items:
            parts.append(f'<a href="{html.escape(it[1])}">{html.escape(it[0])}</a>' if isinstance(it, tuple)
                         else html.escape(it))
        return " | ".join(parts)

    h = [f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>",
         f"<p class='name'>{html.escape(cv['name'])}</p>",
         f"<p class='headline'>{html.escape(cv['headline'])}</p>",
         f"<p class='contact'>{contact_line(cv['contact'])} |<br>{contact_line(cv['contact2'])}</p>",
         "<hr>", f"<p class='summary'>{_html_inline(cv['summary'])}</p>",
         "<h2>Skills</h2><div class='skills'>"]
    for g in cv["skills"]:
        h.append(f"<p><b>{html.escape(g['category'])}:</b> {html.escape(', '.join(g['items']))}</p>")
    h.append("</div><h2>Experience</h2>")
    for j in cv["experience"]:
        h.append(f"<div class='job'><p class='jobhead'><b>{html.escape(j['company'])}</b>, "
                 f"{html.escape(j['location'])} — <i>{html.escape(j['title'])}</i></p>"
                 f"<p class='dates'>{j['dates']}</p><ul>")
        h += [f"<li>{_html_inline(b)}</li>" for b in j["bullets"]]
        h.append(f"</ul><p class='tech'><b>Key Technologies:</b> {html.escape(', '.join(j['tech']))}</p></div>")
    if cv["projects"]:
        h.append("<h2>Projects</h2>")
        for pr in cv["projects"]:
            h.append(f"<div class='proj'><p class='jobhead' style='font-size:{BODY_PT}pt'>"
                     f"<b>{html.escape(pr['name'])}</b></p><ul>")
            h += [f"<li>{_html_inline(b)}</li>" for b in pr["bullets"]]
            h.append(f"</ul><p class='tech'><b>Key Technologies:</b> {html.escape(', '.join(pr['tech']))}</p></div>")
    h.append("<h2>Education</h2>")
    for e in cv["education"]:
        h.append(f"<div class='job'><p class='jobhead'>{html.escape(e['school'])}, {html.escape(e['location'])}"
                 f" — <i>{html.escape(e['degree'])}</i></p><p class='dates'>{e['dates']}</p><ul>")
        h += [f"<li>{_html_inline(b)}</li>" for b in e["bullets"]]
        h.append("</ul></div>")
    if cv["certifications"]:
        h.append("<h2>Certifications</h2><ul>" + "".join(f"<li>{html.escape(c)}</li>" for c in cv["certifications"]) + "</ul>")
    if cv["awards"]:
        h.append("<h2>Awards</h2><ul>" + "".join(f"<li><b>{html.escape(a)}</b></li>" for a in cv["awards"]) + "</ul>")
    h.append("</body></html>")
    return "".join(h)


def letter_html(cv: dict, letter: dict) -> str:
    paras = "".join(f"<p>{_html_inline(p)}</p>" for p in letter["paragraphs"])
    sign = "<br>".join(html.escape(x) for x in letter["sign_off"].split("\n"))
    return (f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body class='letter'>"
            f"<p class='name'>{html.escape(cv['name'])}</p>"
            f"<p class='contact'>{' | '.join(html.escape(c if isinstance(c, str) else c[0]) for c in cv['contact'])}</p><hr>"
            f"<div class='letter' style='font-size:{BODY_PT + 1}pt'><p>{date.today():%d %B %Y}</p>"
            f"<p>{html.escape(letter['greeting'])}</p>{paras}<p>{sign}</p></div></body></html>")


def _print_pdf(page, markup: str, out: Path) -> int:
    page.set_content(markup, wait_until="load")
    page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True)
    return len(PdfReader(str(out)).pages)


def _trim_once(cv: dict) -> bool:
    """Drop the least important bullet: last project bullet first, then the longest role's last bullet."""
    for pr in reversed(cv["projects"]):
        if len(pr["bullets"]) > 1:
            pr["bullets"].pop()
            return True
    if len(cv["projects"]) > 1:
        cv["projects"].pop()
        return True
    roles = [j for j in cv["experience"] if len(j["bullets"]) > 2]
    if roles:
        max(roles, key=lambda j: len(j["bullets"]))["bullets"].pop()
        return True
    return False


def render_all(cv: dict, letter: dict, out_dir: Path, stem: str) -> tuple[dict, int, list[str]]:
    """Writes CV + letter as PDF and DOCX. Returns (final cv model, pages, trimmed notes)."""
    cv = copy.deepcopy(cv)
    notes = []
    pdf_path = out_dir / f"{stem}.pdf"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        pages = _print_pdf(page, cv_html(cv), pdf_path)
        while pages > MAX_PAGES and _trim_once(cv):
            notes.append("trimmed one bullet to fit 2 pages")
            pages = _print_pdf(page, cv_html(cv), pdf_path)
        _print_pdf(page, letter_html(cv, letter), out_dir / "Cover_Letter.pdf")
        browser.close()
    cv_docx(cv, out_dir / f"{stem}.docx")
    letter_docx(cv, letter, out_dir / "Cover_Letter.docx")
    return cv, pages, notes


# --------------------------------------------------------------------------- DOCX

def _runs(par, text: str, size=BODY_PT, bold_all=False, italic=False, color=None):
    pos = 0
    for m in _BOLD.finditer(text):
        if m.start() > pos:
            _run(par, text[pos:m.start()], size, bold_all, italic, color)
        _run(par, m.group(1), size, True, italic, color)
        pos = m.end()
    if pos < len(text):
        _run(par, text[pos:], size, bold_all, italic, color)


def _run(par, text, size, bold, italic, color):
    r = par.add_run(text)
    r.font.name = FONT
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    r.font.size = Pt(size)
    r.bold, r.italic = bold, italic
    if color:
        r.font.color.rgb = RGBColor.from_string(color.lstrip("#"))
    return r


def _para(doc, align=None, space_after=1, style=None):
    p = doc.add_paragraph(style=style)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(0)
    if align:
        p.alignment = align
    return p


def _rule(doc):
    p = _para(doc, space_after=4)
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "4"), ("w:space", "1"), ("w:color", "999999")):
        bottom.set(qn(k), v)
    bdr.append(bottom)
    pPr.append(bdr)


def _doc():
    doc = Document()
    for s in doc.sections:
        s.page_height, s.page_width = Mm(297), Mm(210)
        s.top_margin = s.bottom_margin = Mm(11)
        s.left_margin = s.right_margin = Mm(14)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = FONT, Pt(BODY_PT)
    return doc


def cv_docx(cv: dict, path: Path):
    doc = _doc()
    C = WD_ALIGN_PARAGRAPH.CENTER
    _runs(_para(doc, C), cv["name"], NAME_PT, True)
    _runs(_para(doc, C), cv["headline"], BODY_PT, True)
    contact = [c if isinstance(c, str) else c[0] for c in cv["contact"] + cv["contact2"]]
    _runs(_para(doc, C), " | ".join(contact), BODY_PT, True)
    _rule(doc)
    _runs(_para(doc, WD_ALIGN_PARAGRAPH.JUSTIFY), cv["summary"])

    def heading(t):
        p = _para(doc, space_after=2)
        p.paragraph_format.space_before = Pt(8)
        _runs(p, t.upper(), HEADING_PT, True, color=HEADING_COLOR)

    def bullets(items, bold_all=False):
        for b in items:
            _runs(_para(doc, style="List Bullet"), b, BODY_PT, bold_all)

    heading("Skills")
    for g in cv["skills"]:
        p = _para(doc)
        _run(p, g["category"] + ": ", BODY_PT, True, False, None)
        _run(p, ", ".join(g["items"]), BODY_PT, False, False, None)
    heading("Experience")
    for j in cv["experience"]:
        p = _para(doc)
        p.paragraph_format.space_before = Pt(5)
        _run(p, j["company"], BODY_PT + 1.5, True, False, None)
        _run(p, f", {j['location']} — ", BODY_PT + 1.5, False, False, None)
        _run(p, j["title"], BODY_PT + 1.5, False, True, None)
        _run(_para(doc), j["dates"], BODY_PT - 0.5, False, False, "#666666")
        bullets(j["bullets"])
        p = _para(doc)
        _run(p, "Key Technologies: ", BODY_PT, True, False, None)
        _run(p, ", ".join(j["tech"]), BODY_PT, False, False, None)
    if cv["projects"]:
        heading("Projects")
        for pr in cv["projects"]:
            p = _para(doc)
            p.paragraph_format.space_before = Pt(4)
            _run(p, pr["name"], BODY_PT, True, False, None)
            bullets(pr["bullets"])
            p = _para(doc)
            _run(p, "Key Technologies: ", BODY_PT, True, False, None)
            _run(p, ", ".join(pr["tech"]), BODY_PT, False, False, None)
    heading("Education")
    for e in cv["education"]:
        p = _para(doc)
        p.paragraph_format.space_before = Pt(4)
        _run(p, f"{e['school']}, {e['location']} — ", BODY_PT + 1.5, False, False, None)
        _run(p, e["degree"], BODY_PT + 1.5, False, True, None)
        _run(_para(doc), e["dates"], BODY_PT - 0.5, False, False, "#666666")
        bullets(e["bullets"])
    if cv["certifications"]:
        heading("Certifications")
        bullets(cv["certifications"])
    if cv["awards"]:
        heading("Awards")
        bullets(cv["awards"], bold_all=True)
    doc.save(path)


def letter_docx(cv: dict, letter: dict, path: Path):
    doc = _doc()
    _runs(_para(doc, WD_ALIGN_PARAGRAPH.CENTER), cv["name"], NAME_PT, True)
    _runs(_para(doc, WD_ALIGN_PARAGRAPH.CENTER),
          " | ".join(c if isinstance(c, str) else c[0] for c in cv["contact"]), BODY_PT, True)
    _rule(doc)
    for t in [f"{date.today():%d %B %Y}", letter["greeting"], *letter["paragraphs"], letter["sign_off"]]:
        _runs(_para(doc, WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=8), t, BODY_PT + 1)
    doc.save(path)


def plain(text: str) -> str:
    return _plain(text)
