"""Deterministic honesty checks on the tailored CV. Runs after the LLM, before rendering.

A bullet is reverted to its source text when it:
  - cites a source_id that doesn't exist, isn't verified, or belongs to another role/project
  - contains a number that its source bullet doesn't contain
  - mentions a gap skill that its source bullet doesn't mention
Skills-section items that aren't in the verified profile are dropped.
"""
import re

_NUM = re.compile(r"\d[\d,.]*")


def _nums(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text)}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", s.lower())


_STOP = {"and", "or", "the", "of", "with", "in", "for", "a", "to", "&"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9+#]+", s.lower()) if t and t not in _STOP}


def _mentions(text: str, term: str) -> bool:
    # match the core of the gap name, e.g. "Terraform (IaC)" -> "terraform"
    core = re.split(r"[(/]", term)[0].strip()
    return bool(core) and re.search(rf"(?<![\w]){re.escape(core)}(?![\w])", text, re.I) is not None


def check(tailored: dict, bullet_idx: dict, skill_names: list[str], gap_skills: list[str]) -> list[str]:
    warnings = []
    known = {_norm(n) for n in skill_names}

    def fix_bullets(owner_id: str, bullets: list[dict]) -> list[dict]:
        out = []
        seen = set()
        for b in bullets:
            src = bullet_idx.get(b["source_id"])
            if not src or src[0] != owner_id:
                warnings.append(f"dropped bullet with unknown source '{b['source_id']}' in {owner_id}: {b['text'][:80]}")
                continue
            if b["source_id"] in seen:
                warnings.append(f"dropped duplicate of {b['source_id']}")
                continue
            seen.add(b["source_id"])
            src_text = src[1]
            new_nums = _nums(b["text"]) - _nums(src_text)
            new_gaps = [g for g in gap_skills if _mentions(b["text"], g) and not _mentions(src_text, g)]
            if new_nums or new_gaps:
                why = (f"new numbers {sorted(new_nums)}" if new_nums else "") + \
                      (f" gap skills {new_gaps}" if new_gaps else "")
                warnings.append(f"reverted {b['source_id']} to source text ({why.strip()})")
                b = {"source_id": b["source_id"], "text": src_text}
            out.append(b)
        return out

    for role in tailored["experience"]:
        role["bullets"] = fix_bullets(role["role_id"], role["bullets"])
    for proj in tailored["projects"]:
        proj["bullets"] = fix_bullets(proj["project_id"], proj["bullets"])

    # A skill item passes if it is exactly a verified name, or every word in it is a word that
    # appears in some verified name ("AWS (EC2, S3)" passes; "Java" does not match "JavaScript").
    known_tokens = {t for n in skill_names for t in _tokens(n)}
    for group in tailored["skills"]:
        kept = []
        for item in group["items"]:
            exact = _norm(item) in known
            if exact or (_tokens(item) and _tokens(item) <= known_tokens):
                if not exact and any(_mentions(item, g) for g in gap_skills):
                    warnings.append(f"dropped skill '{item}' (gap skill)")
                    continue
                kept.append(item)
            else:
                warnings.append(f"dropped skill '{item}' (not in verified profile)")
        group["items"] = kept
    tailored["skills"] = [g for g in tailored["skills"] if g["items"]]
    return warnings
