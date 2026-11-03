"""Load the master profile and expose only what the LLM is allowed to use."""
import yaml

from .config import PROFILE_PATH, GAPS_PATH


def load_master() -> dict:
    with open(PROFILE_PATH, encoding="utf8") as f:
        return yaml.safe_load(f)


def load_gaps() -> list[dict]:
    with open(GAPS_PATH, encoding="utf8") as f:
        return (yaml.safe_load(f) or {}).get("gaps") or []


def _verified(items):
    return [i for i in items if i.get("status", "verified") == "verified"]


def verified_view(m: dict) -> dict:
    """The master profile with every `confirm` item removed. This is all the LLM ever sees."""
    roles = [
        {
            "id": e["id"], "company": e["company"], "location": e["location"], "title": e["title"],
            "start": e["start"], "end": e["end"], "key_technologies": e.get("key_technologies", []),
            "bullets": [{"id": b["id"], "text": b["text"], "tracks": b.get("tracks", [])}
                        for b in _verified(e["bullets"])],
        }
        for e in m["experience"]
    ]
    projects = [
        {
            "id": p["id"], "name": p["name"], "one_liner": p.get("one_liner", ""),
            "key_technologies": p.get("key_technologies", []),
            "bullets": [{"id": b["id"], "text": b["text"], "tracks": b.get("tracks", [])}
                        for b in _verified(p["bullets"])],
            "stories": p.get("stories", []),
        }
        for p in m["projects"]
    ]
    skills = {cat: [{"name": s["name"], "level": s.get("level", "")} for s in _verified(items)]
              for cat, items in m["skills"].items()}
    education = [
        {k: v for k, v in e.items() if k != "confirm"}
        | {"bullets": [b["text"] if isinstance(b, dict) else b for b in e.get("bullets", [])]}
        for e in m["education"]
    ]
    return {
        "personal": {k: m["personal"][k] for k in ("name", "city", "work_status", "total_experience_years")},
        "headlines": m["headlines"],
        "summaries": m["summaries"],
        "skills": skills,
        "experience": roles,
        "projects": projects,
        "education": education,
        "certifications": m.get("certifications", []),
        "awards": m.get("awards", []),
    }


def bullet_index(view: dict) -> dict[str, tuple[str, str]]:
    """bullet id -> (owner role/project id, source text)"""
    idx = {}
    for owner in view["experience"] + view["projects"]:
        for b in owner["bullets"]:
            idx[b["id"]] = (owner["id"], b["text"])
    return idx


def verified_skill_names(view: dict) -> list[str]:
    names = [s["name"] for items in view["skills"].values() for s in items]
    for owner in view["experience"] + view["projects"]:
        names += owner.get("key_technologies", [])
    return names
