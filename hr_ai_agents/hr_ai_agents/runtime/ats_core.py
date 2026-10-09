"""Deterministic ATS maths (no frappe import). The model never produces the score; it only
writes the summary and can propose *semantic* matches, which are accepted only when the quoted
evidence really exists in the CV text."""
import re

DEFAULT_WEIGHTS = {
    "must_have": 40,
    "nice_have": 15,
    "experience": 20,
    "role_fit": 10,
    "education": 5,
    "location": 5,
    "salary": 5,
}
DEFAULT_BANDS = (("Strong", 80), ("Good", 60), ("Partial", 40), ("Weak", 0))
STOP = {"and", "or", "of", "the", "a", "an", "for", "in", "to", "with", "senior", "junior", "lead", "sr", "jr"}
DEGREE_RE = re.compile(
    r"\b(b\.?\s?tech|b\.?\s?e\b|bachelor|b\.?\s?sc|bca|m\.?\s?tech|master|m\.?\s?sc|mca|mba|ph\.?d|diploma|degree)\b",
    re.IGNORECASE,
)


def parse_list(value):
    seen, out = set(), []
    for part in re.split(r"[\n,;|]+", value or ""):
        p = part.strip().strip("-•* ").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def build_synonyms(rows):
    """rows: iterable of (term, aliases_str) -> dict lower-term -> set(lower aliases incl. itself); symmetric."""
    groups = []
    for term, aliases in rows:
        g = {term.strip().lower()} | {a.lower() for a in parse_list(aliases)}
        groups.append(g)
    syn = {}
    for g in groups:
        for t in g:
            syn.setdefault(t, set()).update(g)
    return syn


def _pattern(term):
    return re.compile(r"(?<![\w+#.])" + re.escape(term) + r"(?![\w+#])", re.IGNORECASE)


def find_term(term, text, synonyms=None):
    """Return (matched_via, snippet) or (None, None). Matches the term or any synonym."""
    candidates = {term.lower()} | set((synonyms or {}).get(term.lower(), set()))
    for cand in sorted(candidates, key=len, reverse=True):
        m = _pattern(cand).search(text)
        if m:
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.end())
            end = len(text) if end == -1 else end
            line = text[start:end].strip()
            return cand, (line[:200] + "…") if len(line) > 200 else line
    return None, None


def match_terms(terms, text, synonyms=None):
    matched, missing = [], []
    for t in terms:
        via, snip = find_term(t, text, synonyms)
        if via:
            matched.append({"term": t, "via": via if via != t.lower() else None, "evidence": snip, "type": "keyword"})
        else:
            missing.append(t)
    return matched, missing


def extract_years(text):
    """Largest 'N years ... experience' figure; None if the CV states none."""
    best = None
    for m in re.finditer(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)(?P<tail>[^.\n]{0,40})", text or "", re.IGNORECASE):
        if re.search(r"experience|exp\b|expertise", m.group("tail"), re.IGNORECASE) or best is None:
            val = float(m.group(1))
            if 0 < val <= 45:
                best = val if best is None else max(best, val)
    return best


def title_tokens(title):
    return [t for t in re.findall(r"[A-Za-z][A-Za-z+#.]+", title or "") if t.lower() not in STOP and len(t) > 2]


def role_fit_ratio(titles, text):
    toks = []
    for t in titles:
        toks.extend(title_tokens(t))
    toks = list(dict.fromkeys(x.lower() for x in toks))
    if not toks:
        return None
    hit = sum(1 for t in toks if _pattern(t).search(text))
    return hit / len(toks)


def education_ratio(cert_terms, text, synonyms=None):
    if cert_terms:
        m, _ = match_terms(cert_terms, text, synonyms)
        return len(m) / len(cert_terms)
    return None  # nothing required -> component dropped


def salary_ratio(expected, lower, upper):
    """1.0 inside band, decays to 0 at +30% above the upper bound. None when data missing."""
    if not expected or not upper:
        return None
    if expected <= upper:
        return 1.0
    over = (expected - upper) / upper
    return max(0.0, 1.0 - over / 0.30)


def compute_score(ratios, weights=None):
    """ratios: name -> float 0..1 or None (None = not assessable, weight redistributed)."""
    w = dict(DEFAULT_WEIGHTS)
    w.update({k: v for k, v in (weights or {}).items() if v is not None})
    live = {k: r for k, r in ratios.items() if r is not None and w.get(k, 0) > 0}
    total_w = sum(w[k] for k in live) or 1
    rows, score = [], 0.0
    for k in ratios:
        r = ratios[k]
        if r is None or w.get(k, 0) <= 0:
            rows.append({"name": k, "weight": w.get(k, 0), "ratio": None, "points": None})
            continue
        eff = w[k] / total_w * 100
        pts = r * eff
        score += pts
        rows.append({"name": k, "weight": w[k], "effective_weight": round(eff, 1), "ratio": round(r, 3), "points": round(pts, 1)})
    return int(round(score)), rows


def band_for(score, bands=DEFAULT_BANDS):
    for name, floor in bands:
        if score >= floor:
            return name
    return bands[-1][0]


def norm_ws(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def verify_semantic(requirements_missing, proposals, cv_text):
    """proposals: [{requirement, evidence}] from the model. Accept only if requirement is currently
    missing AND evidence is a verbatim (whitespace/case-insensitive) substring of the CV."""
    hay = norm_ws(cv_text)
    accepted = []
    miss_lower = {m.lower(): m for m in requirements_missing}
    for p in proposals or []:
        req = miss_lower.get(str(p.get("requirement", "")).lower())
        ev = norm_ws(p.get("evidence"))
        if req and len(ev) >= 12 and ev in hay:
            accepted.append({"term": req, "via": "semantic", "evidence": str(p.get("evidence"))[:200], "type": "semantic"})
    return accepted


# ---------------- synonyms that come with the job opening (no manual table upkeep) ----------------
# Built-in groups for common HR / IT / finance terms. They only widen matching; a term still has to
# appear in the CV text (shown as "via ..." in the result), so a loose alias cannot invent evidence.
DEFAULT_SYNONYM_GROUPS = [
    "kubernetes: k8s", "amazon web services: aws", "google cloud platform: gcp, google cloud", "microsoft azure: azure",
    "javascript: js, ecmascript", "typescript: ts", "node.js: nodejs, node js", "react: react.js, reactjs", "angular: angularjs",
    "vue.js: vue, vuejs", "postgresql: postgres, psql", "mysql: maria db, mariadb", "mongodb: mongo", "microsoft sql server: mssql, sql server",
    "structured query language: sql", "continuous integration: ci", "continuous delivery: cd, continuous deployment", "ci/cd: ci cd, cicd",
    "infrastructure as code: iac", "machine learning: ml", "artificial intelligence: ai", "natural language processing: nlp",
    "search engine optimization: seo", "user interface: ui", "user experience: ux", "application programming interface: api, apis, rest api, restful",
    "enterprise resource planning: erp", "customer relationship management: crm", "human resources: hr", "human capital management: hcm",
    "human resource information system: hris, hrms", "applicant tracking system: ats", "talent acquisition: recruitment, recruiting, hiring",
    "learning and development: l&d, l & d, training and development", "compensation and benefits: c&b, compensation & benefits, comp and ben",
    "employee relations: er", "performance management: performance appraisal, performance review", "onboarding: induction",
    "payroll: payroll processing", "key performance indicator: kpi, kpis", "objectives and key results: okr, okrs",
    "microsoft excel: excel, ms excel", "microsoft word: ms word", "microsoft power bi: power bi, powerbi", "business intelligence: bi",
    "extract transform load: etl", "data warehouse: data warehousing, dwh", "project management professional: pmp",
    "certified public accountant: cpa", "chartered accountant: ca", "chartered financial analyst: cfa", "master of business administration: mba",
    "bachelor of technology: b.tech, btech, b tech, bachelor of engineering, b.e, be", "bachelor of science: b.sc, bsc", "master of science: m.sc, msc",
    "master of technology: m.tech, mtech", "bachelor of commerce: b.com, bcom", "master of computer applications: mca",
    "international financial reporting standards: ifrs", "generally accepted accounting principles: gaap", "accounts payable: ap",
    "accounts receivable: ar", "general ledger: gl", "financial planning and analysis: fp&a, fpa", "quality assurance: qa", "quality control: qc",
    "software development life cycle: sdlc", "agile: scrum, kanban", "test driven development: tdd", "object oriented programming: oop, oops",
    "docker: containers, containerization", "terraform: tf", "ansible: ansible playbooks", "jenkins: jenkins pipelines", "git: github, gitlab, bitbucket",
    "linux: unix, ubuntu, red hat, rhel", "sap: sap erp", "sap hana: hana", "sap s/4hana: s4hana, s/4 hana, s4 hana", "sap fiori: fiori", "abap: abap/4",
    "salesforce: sfdc", "oracle: oracle database", "python: python3", "c++: cpp", "c#: csharp, c sharp", ".net: dotnet, dot net, asp.net",
    "java: jdk, j2ee, jee", "spring boot: springboot, spring", "restful services: rest, restful apis", "microservices: microservice, micro services",
]


PLURAL_ENDINGS = ("ments", "tions", "vices", "tems", "ures", "ings", "ules", "ysts", "ners", "kers", "ices")


def _depunct(t):
    return re.sub(r"[\s_./-]+", " ", t.lower()).strip()


def auto_aliases(term):
    """Spelling variants derived from the term itself: punctuation/spacing, '.js' suffix, '(ABBR)' in brackets,
    'A / B' and 'A or B' alternatives, '&' <-> 'and', simple plural/singular."""
    t = (term or "").strip()
    if not t:
        return set()
    out = set()
    low = t.lower()
    m = re.search(r"\(([^)]{1,30})\)", t)
    if m:
        out.add(m.group(1).strip().lower())
        out.add(re.sub(r"\([^)]*\)", "", t).strip().lower())
        out.discard(low)
        return {o for o in out if o and len(o) <= 60}
    for part in re.split(r"\s+/\s+|\s+or\s+", low):
        if part.strip() and part.strip() != low:
            out.add(part.strip())
    if "&" in low:
        out.add(low.replace("&", "and"))
    if " and " in low:
        out.add(low.replace(" and ", " & "))
    if re.search(r"[\s_./-]", low):
        flat = _depunct(low)
        out.update({flat, flat.replace(" ", ""), flat.replace(" ", "-")})
        if low.endswith(".js"):
            out.add(low[:-3].replace(".", "") + "js")
            out.discard("node.j")
    elif low.endswith(PLURAL_ENDINGS):
        out.add(low[:-1])
    out.discard(low)
    return {o for o in out if o and len(o) <= 60}


def parse_synonym_lines(text):
    """'term: alias, alias' per line -> list of (term, aliases_str)."""
    rows = []
    for line in (text or "").splitlines():
        if ":" in line:
            term, aliases = line.split(":", 1)
            if term.strip() and aliases.strip():
                rows.append((term.strip(), aliases.strip()))
    return rows


def synonyms_for(terms, extra_rows=()):
    """Synonym map for matching `terms`: built-ins + settings table / opening-specific rows + variants derived from each term.
    Everything is symmetric (an alias also finds the term)."""
    rows = list(parse_synonym_lines("\n".join(DEFAULT_SYNONYM_GROUPS)))
    rows += [(t, a) for t, a in extra_rows if t and a]
    for t in terms:
        al = auto_aliases(t)
        if al:
            rows.append((t, ", ".join(sorted(al))))
    syn = build_synonyms(rows)
    # keep the map small: only groups that touch one of the terms (the matcher looks terms up by lower-case key)
    keep = {t.lower() for t in terms}
    return {k: v for k, v in syn.items() if k in keep}
