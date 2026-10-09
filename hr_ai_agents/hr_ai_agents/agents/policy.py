"""Policy Pal: answers only from the active AI Policy Documents, with citations. BM25 over paragraphs."""
import math
import re

import frappe

WORD = re.compile(r"[a-z0-9]+")
STOP = set("the a an of to and or in on for is are be by with as at it this that from your you i we can may must shall will not no if any".split())


def tokens(text):
    return [w for w in WORD.findall((text or "").lower()) if w not in STOP and len(w) > 1]


def extract_text(doc):
    """Fill `content` from the attachment (PDF/DOCX/TXT) when content is empty or the file changed."""
    from hr_ai_agents.hr_ai_agents.agents import ats

    if not doc.attachment:
        return
    content, fname = ats._file_content(doc.attachment)
    if not content:
        return
    if fname.endswith(".pdf"):
        text = ats._pdf_text(content)
    elif fname.endswith(".docx"):
        text = ats._docx_text(content)
    else:
        text = content.decode("utf8", "ignore") if isinstance(content, bytes) else content
    doc.content = (text or "").strip()
    doc.extracted_chars = len(doc.content)


def chunks(text, size=900):
    paras = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text or "") if p.strip()]
    out, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            out.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p).strip()
    if cur:
        out.append(cur)
    # very long single paragraphs
    final = []
    for c in out:
        while len(c) > size * 1.6:
            final.append(c[:size])
            c = c[size:]
        final.append(c)
    return final


def search(query, k=4, category=None):
    flt = {"active": 1}
    if category:
        flt["category"] = category
    docs = frappe.get_all("AI Policy Document", filters=flt, fields=["name", "title", "category", "version", "effective_date", "content"])
    corpus = []
    for d in docs:
        for i, c in enumerate(chunks(d.content)):
            corpus.append({"doc": d, "n": i + 1, "text": c, "toks": tokens(c)})
    q = tokens(query)
    if not corpus or not q:
        return []
    N = len(corpus)
    avg = sum(len(c["toks"]) for c in corpus) / N or 1
    df = {t: sum(1 for c in corpus if t in c["toks"]) for t in set(q)}
    scored = []
    for c in corpus:
        score = 0.0
        L = len(c["toks"]) or 1
        for t in set(q):
            f = c["toks"].count(t)
            if not f:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * (f * 2.2) / (f + 1.2 * (0.25 + 0.75 * L / avg))
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [
        {
            "policy": c["doc"].title, "version": c["doc"].version, "effective": str(c["doc"].effective_date or ""),
            "section": f"part {c['n']}", "text": c["text"], "score": round(s, 2), "source_doc": c["doc"].name,
        }
        for s, c in scored[:k]
    ]
