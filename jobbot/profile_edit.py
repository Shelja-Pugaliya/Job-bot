"""Add skills to master_profile.yaml (comments preserved) and retire matching gap entries.

Two kinds of additions:
  user_confirmed : skills the user says they have (`python -m jobbot know GraphQL MCP`)
  inferred       : skills a JD needed that are standard parts of a verified skill
                   (e.g. load balancers, implied by AWS). Added automatically, listed in the
                   gap report so the user can delete any that are wrong.
Both are usable in the Skills section of a CV, never as new facts inside experience bullets.
"""
import re
from datetime import date

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .config import PROFILE_PATH, GAPS_PATH

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096
_yaml.indent(mapping=2, sequence=4, offset=2)  # match the hand-written files' layout


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", s.lower())


def _load(path):
    with open(path, encoding="utf8") as f:
        return _yaml.load(f)


def _save(path, doc):
    with open(path, "w", encoding="utf8") as f:
        _yaml.dump(doc, f)


def existing_skill_keys(doc) -> set[str]:
    return {_key(s["name"]) for items in doc["skills"].values() for s in items}


def add_skills(entries: list[dict], category: str) -> list[str]:
    """entries: [{"name": ..., "note": ...}]. Returns names actually added (skips duplicates)."""
    doc = _load(PROFILE_PATH)
    have = existing_skill_keys(doc)
    bucket = doc["skills"].setdefault(category, [])
    added = []
    for e in entries:
        if not e["name"].strip() or _key(e["name"]) in have:
            continue
        item = CommentedMap(name=e["name"].strip(), level="working", evidence=[], status="verified",
                            source=category, added=str(date.today()))
        if e.get("note"):
            item["note"] = e["note"]
        item.fa.set_flow_style()
        bucket.append(item)
        have.add(_key(e["name"]))
        added.append(e["name"].strip())
    if added:
        _save(PROFILE_PATH, doc)
        retire_gaps(added)
    return added


def retire_gaps(names: list[str]) -> list[str]:
    """Mark gap entries the user now has as status: known, so they stop being suggested."""
    doc = _load(GAPS_PATH)
    # Search terms per name: the name without brackets, plus each bracketed alias.
    # "Model Context Protocol (MCP)" -> "Model Context Protocol", "MCP"
    terms = set()
    for n in names:
        terms.add(re.sub(r"\s*\(.*?\)", "", n).strip())
        for inner in re.findall(r"\((.*?)\)", n):
            terms.update(t.strip() for t in inner.split("/"))
    terms = {t for t in terms if len(t) >= 3 or t.isupper()}
    retired = []
    for g in doc["gaps"]:
        if g.get("status") == "known":
            continue
        if any(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", g["skill"], re.I) for t in terms):
            g["status"] = "known"
            retired.append(g["skill"])
    if retired:
        _save(GAPS_PATH, doc)
    return retired
