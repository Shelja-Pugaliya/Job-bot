"""Merge gap bullets from a JD analysis into profile/gap_bullets.yaml (comments preserved)."""
from ruamel.yaml import YAML

from .config import GAPS_PATH

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096


def _key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def merge(analysis: dict) -> tuple[list[str], list[str]]:
    """Returns (new gap names, existing gap names whose seen_in went up)."""
    with open(GAPS_PATH, encoding="utf8") as f:
        doc = _yaml.load(f)
    gaps = doc["gaps"]
    by_key = {_key(g["skill"]): g for g in gaps}
    added, bumped = [], []
    job = f"{analysis['company']} - {analysis['role_title']}"
    for g in analysis["gaps"]:
        existing = by_key.get(_key(g["skill"]))
        if existing is not None:
            existing["seen_in"] = int(existing.get("seen_in", 0)) + 1
            bumped.append(existing["skill"])
            continue
        entry = {
            "skill": g["skill"],
            "tracks": [analysis["track"]],
            "anchor": g["anchor_id"] or None,
            "bullet": g["bullet"],
            "build_plan": g["build_plan"],
            "effort": g["effort"],
            "status": "to_build",
            "seen_in": 1,
            "first_seen": job,
        }
        gaps.append(entry)
        by_key[_key(g["skill"])] = entry
        added.append(g["skill"])
    with open(GAPS_PATH, "w", encoding="utf8") as f:
        _yaml.dump(doc, f)
    return added, bumped
