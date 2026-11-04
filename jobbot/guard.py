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


def add_gap_skills(tailored: dict, gaps: list[dict], mode: str) -> list[str]:
    """Put the JD's listable gap skills into the Skills section (user's rule, 2026-11-04).
    Never touches bullets. Returns the keywords added."""
    if mode == "off":
        return []
    cv_text = " ".join(i for g in tailored["skills"] for i in g["items"]).lower()
    todo = []
    for g in gaps:
        kw = (g.get("cv_keyword") or re.sub(r"\s*\(.*?\)", "", g["skill"])).strip()
        if g.get("cv_listable", True) and kw and kw.lower() not in cv_text and kw not in todo:
            todo.append(kw)
    if not todo:
        return []
    if mode == "learning":
        tailored["skills"].append({"category": "Currently Learning", "items": todo})
        return todo
    for kw in todo:
        # best-fitting line = most shared words with the category name and its items,
        # then a topic hint (Kubernetes -> an Infrastructure/Cloud line), then a catch-all line
        kt = _tokens(kw)
        best = max(tailored["skills"], key=lambda grp: len(kt & _tokens(grp["category"] + " " + " ".join(grp["items"]))),
                   default=None)
        if best is None or not (kt & _tokens(best["category"] + " " + " ".join(best["items"]))):
            best = _by_hint(kw, tailored["skills"])
        if best is None:
            best = next((grp for grp in tailored["skills"] if grp["category"] == "Tools & Platforms"), None)
            if best is None:
                best = {"category": "Tools & Platforms", "items": []}
                tailored["skills"].append(best)
        best["items"].append(kw)
    return todo


_HINTS = [  # (pattern on the skill, words to look for in category names, in preference order)
    (r"kubernetes|helm|terraform|argo|eks|gke|openshift|nginx|load bal|serverless|lambda|cloudformation|pulumi",
     ["infrastructure", "cloud", "container", "devops", "platform"]),
    (r"redis|valkey|postgres|mysql|snowflake|dbt|kafka|spark|airflow|bigquery|elasticsearch",
     ["database", "data", "infrastructure"]),
    (r"ssl|tls|firewall|subnet|network|vpn|dns|siem|iam|sso|mfa|zero trust",
     ["network", "security", "infrastructure"]),
    (r"prometheus|opentelemetry|datadog|splunk|new relic|elk|loki|tracing",
     ["observability", "monitoring"]),
    (r"llm|rag|vector|langchain|openai|claude|hugging|pytorch|tensorflow|scikit|ml",
     ["ai", "machine learning", "ml", "data"]),
    (r"graphql|grpc|api|webhook",
     ["api", "integration"]),
    (r"go\b|golang|rust|java\b|kotlin|ruby|scala|c\+\+",
     ["language", "programming"]),
    (r"jest|cypress|playwright|selenium|pytest|junit",
     ["test", "ci/cd", "quality"]),
]


def _by_hint(kw: str, groups: list[dict]):
    for pattern, words in _HINTS:
        if re.search(pattern, kw, re.I):
            for w in words:
                g = next((grp for grp in groups if w in grp["category"].lower()), None)
                if g:
                    return g
    return None


_STOP = {"and", "or", "the", "of", "with", "in", "for", "a", "to", "&"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9+#]+", s.lower()) if t and t not in _STOP}


def _mentions(text: str, term: str) -> bool:
    # match the core of the gap name, e.g. "Terraform (IaC)" -> "terraform"
    core = re.sub(r"\s+", " ", re.sub(r"\(.*?\)", "", term)).strip()
    # "A/B testing" stays whole; "Kubernetes (hands-on) / Helm" -> "Kubernetes" or "Helm"
    parts = [p.strip() for p in core.split(" / ")] if " / " in core else [core]
    for p in parts:
        if not p:
            continue
        # short names like "Go" must match case-sensitively so the verb "go" doesn't count
        flags = re.I if len(p) >= 3 else 0
        if re.search(rf"(?<![\w]){re.escape(p)}(?![\w])", text, flags):
            return True
    return False


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
