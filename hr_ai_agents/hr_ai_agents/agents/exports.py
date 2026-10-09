"""File exports (Word / Excel) built by ERPNext, not by the model.

The model only chooses WHAT to export (a source and a few options). Every figure comes from a
database query that runs with the requesting user's own permissions, and the file is written here
with plain code, so no tokens are spent on formatting and the model never sees the rows.

A document is a small neutral structure:

    {"title": str, "subtitle": str, "notes": [str],
     "sections": [{"heading": str, "paragraphs": [str], "columns": [str], "rows": [[...]]}]}

`build_docx` and `build_xlsx` turn it into bytes; `save_file` stores it as a private File that only
the requesting user (and System Managers) can open.
"""
import datetime
import re
import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, today

MAX_ROWS = 5000
MAX_SECTIONS = 40
MAX_COLS = 30
MAX_CELL = 2000
FORMATS = {"word": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), "excel": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
FILE_PREFIX = "AI-Export-"

# ------------------------------------------------------------------ cell helpers
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _txt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1e15 else str(v)
    if isinstance(v, (datetime.datetime, datetime.date)):
        return str(v)[:19] if isinstance(v, datetime.datetime) else str(v)
    s = _CTRL.sub("", str(v))
    s = re.sub(r"<[^>]+>", " ", s) if "<" in s and ">" in s else s
    return re.sub(r"[ \t]+", " ", s).strip()[:MAX_CELL]


def _clean_str(v):
    v = _CTRL.sub("", v)
    if "<" in v and ">" in v:
        v = re.sub(r"<[^>]+>", " ", v)
        v = re.sub(r"\s+", " ", v).strip()
    return v[:MAX_CELL]


def clean_doc(doc):
    """Validate and normalise a document structure (also used for model-supplied content)."""
    out = {"title": _txt(doc.get("title")) or "Report", "subtitle": _txt(doc.get("subtitle")), "notes": [_txt(n) for n in (doc.get("notes") or []) if _txt(n)], "sections": []}
    for s in (doc.get("sections") or [])[:MAX_SECTIONS]:
        cols = [_txt(c) or f"Column {i + 1}" for i, c in enumerate((s.get("columns") or [])[:MAX_COLS])]
        rows = []
        for r in (s.get("rows") or [])[:MAX_ROWS]:
            r = list(r) if isinstance(r, (list, tuple)) else [r]
            r = (r + [""] * len(cols))[: len(cols)] if cols else []
            rows.append(r)
        out["sections"].append({"heading": _txt(s.get("heading")), "paragraphs": [_txt(p) for p in (s.get("paragraphs") or []) if _txt(p)], "columns": cols, "rows": rows})
    return out


# ------------------------------------------------------------------ DOCX (minimal, valid WordprocessingML)
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_NUMERIC = re.compile(r"^-?[\d,]+(\.\d+)?%?$")


def _run(text, bold=False, size=None, color=None, italic=False):
    props = ""
    if bold:
        props += "<w:b/>"
    if italic:
        props += "<w:i/>"
    if color:
        props += f'<w:color w:val="{color}"/>'
    if size:
        props += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    parts = escape(text).split("\n")
    body = '<w:br/>'.join(f'<w:t xml:space="preserve">{p}</w:t>' for p in parts)
    return f"<w:r>{('<w:rPr>' + props + '</w:rPr>') if props else ''}{body}</w:r>"


def _para(text, style=None, bold=False, size=None, color=None, italic=False, align=None, after=None):
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if after is not None:
        ppr += f'<w:spacing w:after="{after}"/>'
    if align:
        ppr += f'<w:jc w:val="{align}"/>'
    return f"<w:p>{('<w:pPr>' + ppr + '</w:pPr>') if ppr else ''}{_run(text, bold, size, color, italic)}</w:p>"


def _table(cols, rows, total_width):
    n = max(len(cols), 1)
    widths = _col_widths(cols, rows, total_width)
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    border = "".join(f'<w:{e} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>' for e in ("top", "left", "bottom", "right", "insideH", "insideV"))
    x = [f'<w:tbl><w:tblPr><w:tblW w:w="{total_width}" w:type="dxa"/><w:tblBorders>{border}</w:tblBorders><w:tblLayout w:type="fixed"/>'
         '<w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>'
         f"<w:tblGrid>{grid}</w:tblGrid>"]
    x.append('<w:tr><w:trPr><w:tblHeader/></w:trPr>' + "".join(
        f'<w:tc><w:tcPr><w:tcW w:w="{widths[i]}" w:type="dxa"/><w:shd w:val="clear" w:color="auto" w:fill="1F3864"/></w:tcPr>{_para(c, bold=True, size=18, color="FFFFFF", after=0)}</w:tc>'
        for i, c in enumerate(cols)) + "</w:tr>")
    for ri, r in enumerate(rows):
        fill = "F2F2F2" if ri % 2 else "FFFFFF"
        cells = []
        for i in range(n):
            v = _txt(r[i]) if i < len(r) else ""
            cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{widths[i]}" w:type="dxa"/><w:shd w:val="clear" w:color="auto" w:fill="{fill}"/></w:tcPr>{_para(v, size=18, after=0, align="right" if _NUMERIC.match(v) else None)}</w:tc>')
        x.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>" + "".join(cells) + "</w:tr>")
    x.append("</w:tbl>")
    return "".join(x)


def _col_widths(cols, rows, total):
    n = max(len(cols), 1)
    weights = []
    for i in range(n):
        longest = max([len(_txt(cols[i]))] + [len(_txt(r[i])) for r in rows[:200] if i < len(r)])
        weights.append(min(max(longest, 10), 40))
    s = sum(weights)
    w = [max(int(total * x / s), 700) for x in weights]
    w[-1] += total - sum(w)
    return w


def build_docx(doc, who=None):
    doc = clean_doc(doc)
    landscape = any(len(s["columns"]) > 5 for s in doc["sections"])
    pw, ph = (15840, 12240) if landscape else (12240, 15840)  # US Letter dimensions are swapped for landscape
    margin = 1080
    total = pw - 2 * margin
    body = [_para(doc["title"], style="Title")]
    meta = " | ".join(x for x in [doc["subtitle"], f"Generated {now_datetime().strftime('%d %b %Y %H:%M')}", f"for {who}" if who else ""] if x)
    body.append(_para(meta, size=18, color="595959", after=200))
    for s in doc["sections"]:
        if s["heading"]:
            body.append(_para(s["heading"], style="Heading1"))
        for p in s["paragraphs"]:
            body.append(_para(p, after=120))
        if s["columns"]:
            body.append(_table(s["columns"], s["rows"], total))
            body.append(_para("", after=120))
            if not s["rows"]:
                body.append(_para("No records.", italic=True, color="595959"))
    for n in doc["notes"]:
        body.append(_para(n, size=16, color="7F7F7F", italic=True, after=60))
    orient = ' w:orient="landscape"' if landscape else ""
    sect = (f'<w:sectPr><w:footerReference w:type="default" r:id="rId3"/><w:pgSz w:w="{pw}" w:h="{ph}"{orient}/>'
            f'<w:pgMar w:top="{margin}" w:right="{margin}" w:bottom="{margin}" w:left="{margin}" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_W} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f"<w:body>{''.join(body)}{sect}</w:body></w:document>")
    # Word expects portrait width/height first then orient=landscape with the long side as width
    footer = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {_W}><w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
              + _run("Prepared by an AI agent from system data. Check before acting.  Page ", size=16, color="7F7F7F")
              + '<w:r><w:rPr><w:sz w:val="16"/><w:color w:val="7F7F7F"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
              '<w:r><w:rPr><w:sz w:val="16"/><w:color w:val="7F7F7F"/></w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
              '<w:r><w:rPr><w:sz w:val="16"/><w:color w:val="7F7F7F"/></w:rPr><w:fldChar w:fldCharType="separate"/></w:r>'
              '<w:r><w:rPr><w:sz w:val="16"/><w:color w:val="7F7F7F"/></w:rPr><w:t>1</w:t></w:r>'
              '<w:r><w:rPr><w:sz w:val="16"/><w:color w:val="7F7F7F"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {_W}>'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri" w:eastAsia="Calibri"/><w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault>'
              '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:b/><w:color w:val="1F3864"/><w:sz w:val="44"/><w:szCs w:val="44"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="280" w:after="100"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="1F3864"/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr></w:style>'
              "</w:styles>")
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/></Relationships>')
    stamp = now_datetime().strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(doc["title"])}</dc:title><dc:creator>HR AI Agents</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created></cp:coreProperties>')
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/footer1.xml", footer)
        z.writestr("docProps/core.xml", core)
    return buf.getvalue()


# ------------------------------------------------------------------ XLSX (openpyxl, Frappe's own cell cleaning)
def build_xlsx(doc, who=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from frappe.utils.csvutils import FORMULA_TRIGGER_CHARS

    doc = clean_doc(doc)
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    head = Font(name="Calibri", bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F3864")
    ws.append([doc["title"]])
    ws["A1"].font = Font(name="Calibri", bold=True, size=16, color="1F3864")
    ws.append([" | ".join(x for x in [doc["subtitle"], f"Generated {now_datetime().strftime('%d %b %Y %H:%M')}", f"for {who}" if who else ""] if x)])
    ws.append([])
    used = {"Summary"}
    index = []
    for i, s in enumerate(doc["sections"], 1):
        base = re.sub(r"[\[\]\*\?/\\:]", " ", s["heading"] or f"Table {i}")[:28].strip() or f"Table {i}"
        name, k = base, 2
        while name in used:
            name = f"{base[:25]} {k}"
            k += 1
        used.add(name)
        if s["paragraphs"]:
            ws.append([s["heading"] or name])
            ws.cell(ws.max_row, 1).font = Font(name="Calibri", bold=True, color="1F3864")
            for p in s["paragraphs"]:
                ws.append([p])
            ws.append([])
        if s["columns"]:
            sh = wb.create_sheet(name)
            sh.append(s["columns"])
            for c in sh[1]:
                c.font, c.fill, c.alignment = head, fill, Alignment(wrap_text=True, vertical="center")
            text_cells = []
            for ri, r in enumerate(s["rows"], start=2):
                row = []
                for ci, v in enumerate(r, start=1):
                    if isinstance(v, str):
                        v = _clean_str(v)
                        if v.startswith(FORMULA_TRIGGER_CHARS):
                            text_cells.append((ri, ci))  # kept as literal text below, never a formula
                    elif v is not None and not isinstance(v, (int, float, bool, datetime.date, datetime.datetime)):
                        v = _clean_str(_txt(v))
                    row.append(v)
                sh.append(row)
            for ri, ci in text_cells:
                sh.cell(ri, ci).data_type = "s"
            sh.freeze_panes = "A2"
            if s["rows"]:
                sh.auto_filter.ref = f"A1:{get_column_letter(len(s['columns']))}{len(s['rows']) + 1}"
            for ci, c in enumerate(s["columns"], 1):
                longest = max([len(c)] + [len(_txt(r[ci - 1])) for r in s["rows"][:300] if ci - 1 < len(r)])
                sh.column_dimensions[get_column_letter(ci)].width = min(max(longest + 2, 10), 60)
            index.append(f"{name}: {len(s['rows'])} row(s)")
    if index:
        ws.append(["Sheets in this workbook"])
        ws.cell(ws.max_row, 1).font = Font(name="Calibri", bold=True, color="1F3864")
        for x in index:
            ws.append([x])
    for n in doc["notes"]:
        ws.append([n])
        ws.cell(ws.max_row, 1).font = Font(name="Calibri", italic=True, color="7F7F7F", size=9)
    ws.column_dimensions["A"].width = 110
    for row in ws.iter_rows(min_row=4):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ storing
def safe_name(title):
    base = re.sub(r"[^A-Za-z0-9 _-]+", "", title or "Report").strip().replace(" ", "-")[:60] or "Report"
    return f"{FILE_PREFIX}{base}-{now_datetime().strftime('%Y%m%d-%H%M%S')}"


def save_file(doc, fmt, attached_to=None, who=None):
    """Build the file and store it as a private File. Returns {file, file_name, url, format, size}."""
    fmt = (fmt or "").lower()
    if fmt not in FORMATS:
        frappe.throw(_("Format must be 'word' or 'excel'."))
    ext = FORMATS[fmt][0]
    content = build_docx(doc, who) if fmt == "word" else build_xlsx(doc, who)
    d = {"doctype": "File", "file_name": f"{safe_name(doc.get('title'))}.{ext}", "content": content, "is_private": 1}
    if attached_to:
        d.update({"attached_to_doctype": attached_to[0], "attached_to_name": attached_to[1]})
    f = frappe.get_doc(d)
    f.flags.ignore_permissions = True
    f.insert()
    return {"file": f.name, "file_name": f.file_name, "url": f.file_url, "format": fmt, "size": len(content)}


# ------------------------------------------------------------------ data sources (all permission-checked)
def _fmt_status(d):
    return ", ".join(f"{k}: {v}" for k, v in (d or {}).items()) or "-"


def sections_from_snapshot(snap):
    """Turn an Atlas data snapshot (plans, requisitions, org figures, attention items) into tables."""
    secs = []
    org = snap.get("org") or {}
    summary = [f"Report date {snap.get('report_date')}. Scope: {snap.get('scope')}."]
    u = org.get("agent_usage_today") or {}
    if org:
        summary.append(f"SLA alerts raised today: {org.get('sla_new_today', 0)}. AI usage today: {u.get('runs', 0)} run(s), {u.get('tokens', 0)} token(s).")
    secs.append({"heading": "Summary", "paragraphs": summary})
    att = snap.get("attention") or []
    secs.append({"heading": "Needs attention", "columns": ["Severity", "Item"], "rows": [[a["severity"], a["text"]] for a in att] or []} if att else {"heading": "Needs attention", "paragraphs": ["Nothing needs attention."]})
    plans = snap.get("plans") or []
    if plans:
        secs.append({"heading": "Workforce plans", "columns": ["Plan", "State", "Department", "Currency", "Approved HC", "Committed HC", "Consumed HC", "Remaining HC", "Approved budget", "Committed budget", "Consumed budget", "Remaining budget", "Applicants"],
                     "rows": [[p.get("name"), p.get("workflow_state"), p.get("department"), p.get("currency"), p.get("approved_headcount"), p.get("committed_headcount"), p.get("consumed_headcount"), p.get("remaining_headcount"),
                               flt(p.get("approved_budget")), flt(p.get("committed_budget")), flt(p.get("consumed_budget")), flt(p.get("remaining_budget")), _fmt_status(p.get("applicants_by_status"))] for p in plans]})
    reqs = [(p.get("name"), r) for p in plans for r in p.get("requisitions", [])] + [(None, r) for r in (snap.get("requisitions") or [])] if (plans or snap.get("requisitions")) else []
    if reqs:
        secs.append({"heading": "Requisitions", "columns": ["Plan", "Requisition", "Title", "State", "Positions", "Est. cost USD", "Openings", "Vendor SLA breached", "Applicants", "ATS average", "Days since change"],
                     "rows": [[pl, r.get("name"), r.get("custom_requisition_title"), r.get("workflow_state"), r.get("positions"), r.get("estimated_cost_usd"), len(r.get("openings") or []), r.get("vendor_sla_breached"),
                               _fmt_status(r.get("applicants_by_status")), (r.get("ats") or {}).get("average"), r.get("days_since_last_change")] for pl, r in reqs]})
    pend = org.get("pending_by_state") or {}
    if pend:
        secs.append({"heading": "Pending items by workflow state", "columns": ["Document type", "State", "Count"], "rows": [[dt, st, n] for dt, v in pend.items() for st, n in v.items()]})
    return secs


def source_status(run, subject_doctype=None, subject_name=None):
    from hr_ai_agents.hr_ai_agents.agents import atlas

    snap = atlas.collect_snapshot(run, subject_doctype, subject_name)
    t = "HR Hiring Progress Report" + (f": {subject_name}" if subject_name else "")
    return {"title": t, "subtitle": snap["report_date"], "sections": sections_from_snapshot(snap), "notes": ["Figures are database queries run for the requesting user at the time of export."]}


def source_openings(run):
    run.require("Job Opening", "read")
    m = frappe.get_meta("Job Opening")
    want = ["name", "job_title", "status", "job_requisition", "department", "designation", "vacancies", "custom_vendor", "custom_vendor_ageing_days", "custom_vendor_sla_days", "custom_sla_breached"]
    fields = [f for f in want if f == "name" or m.has_field(f)]
    rows = _list(run, "Job Opening", fields, {}, "modified desc")
    return {"title": "Job Openings", "subtitle": today(), "sections": [{"heading": "Job openings", "columns": [m.get_label(f) if f != "name" else "Opening" for f in fields], "rows": [[r.get(f) for f in fields] for r in rows]}]}


def source_sla(run):
    rows = frappe.get_all("SLA Alert Log", filters={"status": ("!=", "Dismissed")}, fields=["name", "rule", "reference_doctype", "reference_name", "level", "status", "elapsed_days", "target_days"], order_by="creation desc", limit=MAX_ROWS)
    cols = ["Alert", "Rule", "Document type", "Document", "Level", "Status", "Elapsed days", "Target days"]
    return {"title": "SLA alerts", "subtitle": today(), "sections": [{"heading": "Open SLA alerts", "columns": cols, "rows": [[r.name, r.rule, r.reference_doctype, r.reference_name, r.level, r.status, r.elapsed_days, r.target_days] for r in rows]}]}


def _list(run, doctype, fields, filters, order):
    kw = {"filters": filters, "fields": fields, "order_by": order}
    if run.trigger == "User":
        return frappe.get_list(doctype, limit_page_length=MAX_ROWS, **kw)
    return frappe.get_all(doctype, limit=MAX_ROWS, **kw)


def source_records(run, doctype, fields=None, filters=None, group_by=None, months=None):
    """Generic record list for the analytics doctypes. Personal identifiers and protected attributes are never exported;
    pay / cost fields need the HR Manager role. Honours the user's own read permissions."""
    from hr_ai_agents.hr_ai_agents.agents import tools_r2 as t2
    from frappe.utils import add_months

    if doctype not in t2.analytics_doctypes():
        raise frappe.PermissionError(f"Exports are not enabled for {doctype}. A manager can add it in AI Agent Settings (Analytics document types).")
    run.require(doctype, "read")
    meta = frappe.get_meta(doctype)
    is_mgr = "HR Manager" in frappe.get_roles(run.user)
    flt_ = {"docstatus": ("<", 2)} if meta.is_submittable or True else {}
    for k, v in (filters or {}).items():
        if k in t2.BLOCKED_GROUP or not (meta.has_field(k) or k in ("name", "creation", "modified")):
            raise frappe.ValidationError(f"Cannot filter by '{k}'.")
        flt_[k] = ("in", v) if isinstance(v, list) else v
    if months:
        flt_["creation"] = (">=", add_months(today(), -cint(months)))
    skipped = []
    if not fields:
        fields = [f for f in ("name", "workflow_state", "status", "department", "designation", "company", "posting_date", "creation") if f == "name" or f == "creation" or meta.has_field(f)]
    sel = []
    for f in fields[:MAX_COLS]:
        df = meta.get_field(f)
        if f in ("name", "creation", "modified"):
            sel.append(f)
        elif not df or f in t2.BLOCKED_GROUP or df.fieldtype in ("Table", "Table MultiSelect", "Attach", "Attach Image", "Password", "Text Editor", "Long Text", "HTML", "Code", "Signature"):
            skipped.append(f)
        elif t2.SALARY_PAT.search(f) and not is_mgr:
            skipped.append(f)
        else:
            sel.append(f)
    if not sel:
        raise frappe.ValidationError("None of the requested fields can be exported. Use category, date, status or numeric fields.")
    secs = []
    if group_by:
        if group_by in t2.BLOCKED_GROUP or not meta.has_field(group_by):
            raise frappe.ValidationError(f"Cannot group by '{group_by}'.")
        counts = {}
        for r in _list(run, doctype, [group_by, "name"], flt_, "modified desc"):
            counts[str(r.get(group_by) or "Not set")] = counts.get(str(r.get(group_by) or "Not set"), 0) + 1
        shown = {k: n for k, n in counts.items() if n >= t2.K_MIN}
        secs.append({"heading": f"{doctype} by {meta.get_label(group_by)}", "columns": [meta.get_label(group_by), "Count"], "rows": sorted(([k, n] for k, n in shown.items()), key=lambda x: -x[1]),
                     "paragraphs": ([f"{len(counts) - len(shown)} group(s) with fewer than {t2.K_MIN} records are hidden to protect privacy."] if len(counts) > len(shown) else [])})
    rows = _list(run, doctype, sel, flt_, "modified desc")
    labels = [meta.get_label(f) if meta.has_field(f) else f.replace("_", " ").title() for f in sel]
    if not group_by:
        secs.append({"heading": doctype, "columns": labels, "rows": [[r.get(f) for f in sel] for r in rows]})
    notes = [f"Fields left out for privacy or permission reasons: {', '.join(skipped)}."] if skipped else []
    for r in rows[:500]:
        run.touch(doctype, r.get("name"), "exported")
    return {"title": f"{doctype} list" if not group_by else f"{doctype} by {meta.get_label(group_by)}", "subtitle": f"{len(rows)} record(s)", "sections": secs, "notes": notes}


def source_custom(sections, title=None, subtitle=None):
    """Content written by the model in the chat (narrative and tables). Cleaned and size-limited here."""
    return {"title": title or "Report", "subtitle": subtitle or "", "sections": sections or [], "notes": ["Written by an AI agent from the conversation; check figures against the system before relying on them."]}


SOURCES = ["status_overview", "plan_status", "requisition_status", "job_openings", "sla_alerts", "records", "custom"]
