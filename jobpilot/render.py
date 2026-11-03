"""Render a tailored resume / cover letter to DOCX (editable) and PDF (to upload).

Layout matches the resume we built by hand: Calibri-style, one page, ATS-friendly
(real text, no tables or images), projects show their tech stack on the right.
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .tailor import TailoredResume

ACCENT = "1F3864"
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _segments(text: str) -> list[tuple[str, bool]]:
    parts, pos = [], 0
    for m in _BOLD.finditer(text):
        if m.start() > pos:
            parts.append((text[pos:m.start()], False))
        parts.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        parts.append((text[pos:], False))
    return parts


def _sections(tr: TailoredResume) -> list[tuple[str, list[dict]]]:
    """Resume content as a neutral structure both renderers walk."""
    p = tr.profile
    edu = []
    for i, e in enumerate(p["education"]):
        edu.append({"kind": "entry", "title": e["school"], "sub": e["degree"], "right": e["dates"]})
        if i == 0 and tr.show_grad_line:
            g = p.get("graduation", "")
            a = p.get("available_from", g)
            edu.append({"kind": "note", "text": f"Expected graduation: {_month(g)}  |  Available to start full-time: {_month(a)}"})
    projects = []
    for pr in p["projects"]:
        projects.append({"kind": "entry", "title": pr["name"], "right": ", ".join(pr.get("tech", []))})
        projects += [{"kind": "bullet", "text": b["text"]} for b in pr["bullets"]]
    exp = []
    for e in p.get("experience", []):
        exp.append({"kind": "entry", "title": e["title"], "right": e["dates"]})
        exp += [{"kind": "bullet", "text": b} for b in e["bullets"]]
    skills = [{"kind": "skill", "label": k, "text": ", ".join(v)} for k, v in p["skills"].items()]
    research = []
    for r in p.get("research", []):
        research.append({"kind": "entry", "title": r["name"], "right": r.get("dates", "")})
        if r.get("note"):
            research.append({"kind": "note", "text": r["note"]})
        research += [{"kind": "bullet", "text": b["text"]} for b in r["bullets"]]
    out = [("Summary", [{"kind": "para", "text": tr.summary}]), ("Education", edu), ("Projects", projects)]
    if exp:
        out.append(("Experience", exp))
    out.append(("Technical Skills", skills))
    if research:
        out.append(("Research", research))
    return out


def _month(yyyy_mm: str) -> str:
    months = ["January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"]
    try:
        y, m = yyyy_mm.split("-")
        return f"{months[int(m) - 1]} {y}"
    except Exception:
        return yyyy_mm


def _contact(p: dict) -> str:
    bits = [p.get("location"), p.get("phone"), p.get("email")]
    return "  |  ".join(b for b in bits if b)


# ---------------- PDF ----------------

def render_pdf(tr: TailoredResume, path: Path, base_size: float = 10.0) -> int:
    """Write the PDF, shrinking the font slightly if needed to fit one page. Returns page count."""
    size = base_size
    pages = 99
    while size >= 8.8:
        pages = _render_pdf_once(tr, path, size)
        if pages <= 1:
            break
        size -= 0.3
    return pages


def _render_pdf_once(tr: TailoredResume, path: Path, size: float) -> int:
    p = tr.profile
    accent = HexColor("#" + ACCENT)
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=size, leading=size * 1.22)
    small = ParagraphStyle("small", parent=body, fontName="Helvetica-Oblique", fontSize=size - 0.5, textColor=HexColor("#444444"))
    head = ParagraphStyle("head", parent=body, fontName="Helvetica-Bold", fontSize=size + 1, textColor=accent, spaceBefore=6, spaceAfter=1)
    name = ParagraphStyle("name", parent=body, fontName="Helvetica-Bold", fontSize=size + 7, alignment=TA_CENTER, textColor=accent, leading=size + 10)
    center = ParagraphStyle("center", parent=body, alignment=TA_CENTER)
    width = LETTER[0] - 1.0 * inch

    def md(text: str) -> str:
        esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return _BOLD.sub(r"<b>\1</b>", esc)

    story = [Paragraph(p["name"].upper(), name)]
    contact = md(_contact(p))
    if p.get("linkedin"):
        contact += f'  |  <a href="{p["linkedin"]}" color="#{ACCENT}"><u>LinkedIn</u></a>'
    story += [Paragraph(contact, center), Spacer(1, 3)]

    for title, items in _sections(tr):
        story += [Paragraph(title.upper(), head), HRFlowable(width="100%", thickness=0.8, color=accent, spaceAfter=2)]
        bullets: list = []

        def flush():
            if bullets:
                story.append(ListFlowable([ListItem(Paragraph(md(b), body), leftIndent=10) for b in bullets],
                                          bulletType="bullet", start="•", leftIndent=10, bulletFontSize=size))
                bullets.clear()

        for it in items:
            if it["kind"] == "bullet":
                bullets.append(it["text"])
                continue
            flush()
            if it["kind"] == "para":
                story.append(Paragraph(md(it["text"]), body))
            elif it["kind"] == "note":
                story.append(Paragraph(md(it["text"]), small))
            elif it["kind"] == "skill":
                story.append(Paragraph(f"<b>{md(it['label'])}:</b> {md(it['text'])}", body))
            elif it["kind"] == "entry":
                left = f"<b>{md(it['title'])}</b>" + (f' <font color="#444444"><i>| {md(it["sub"])}</i></font>' if it.get("sub") else "")
                right = f"<i>{md(it.get('right', ''))}</i>"
                rw = min(width * 0.42, stringWidth(it.get("right", ""), "Helvetica-Oblique", size) + 8)
                t = Table([[Paragraph(left, body), Paragraph(right, ParagraphStyle("r", parent=body, alignment=2))]],
                          colWidths=[width - rw, rw])
                t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                       ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP")]))
                story.append(t)
        flush()

    pages = {"n": 0}

    def count(canvas, doc):
        pages["n"] = doc.page

    doc = SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                            topMargin=0.38 * inch, bottomMargin=0.36 * inch,
                            title=f"{p['name']} Resume", author=p["name"])
    doc.build(story, onFirstPage=count, onLaterPages=count)
    return pages["n"]


# ---------------- DOCX ----------------

def _bottom_border(paragraph) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in {"w:val": "single", "w:sz": "6", "w:space": "1", "w:color": ACCENT}.items():
        bottom.set(qn(k), v)
    bdr.append(bottom)
    pPr.append(bdr)


def render_docx(tr: TailoredResume, path: Path, size: float = 10.0) -> None:
    p = tr.profile
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(0.5)
    sec.top_margin, sec.bottom_margin = Inches(0.38), Inches(0.36)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(size)
    st.paragraph_format.space_after = Pt(1)
    text_width = Inches(7.5)
    accent = RGBColor.from_string(ACCENT)

    def para(align=None, before=0, after=1):
        par = doc.add_paragraph()
        par.paragraph_format.space_before, par.paragraph_format.space_after = Pt(before), Pt(after)
        if align:
            par.alignment = align
        return par

    def runs(par, text, italic=False, color=None, sz=None):
        for seg, bold in _segments(text):
            r = par.add_run(seg)
            r.bold, r.italic = bold, italic
            if color:
                r.font.color.rgb = color
            if sz:
                r.font.size = Pt(sz)

    h = para(WD_ALIGN_PARAGRAPH.CENTER, after=2)
    r = h.add_run(p["name"].upper())
    r.bold, r.font.size, r.font.color.rgb = True, Pt(size + 7), accent
    runs(para(WD_ALIGN_PARAGRAPH.CENTER, after=4), _contact(p) + (f"  |  {p['linkedin']}" if p.get("linkedin") else ""))

    for title, items in _sections(tr):
        hp = para(before=6, after=2)
        r = hp.add_run(title.upper())
        r.bold, r.font.size, r.font.color.rgb = True, Pt(size + 1), accent
        _bottom_border(hp)
        for it in items:
            if it["kind"] == "para":
                runs(para(), it["text"])
            elif it["kind"] == "note":
                runs(para(), it["text"], italic=True, color=RGBColor(0x44, 0x44, 0x44), sz=size - 0.5)
            elif it["kind"] == "skill":
                sp = para()
                b = sp.add_run(it["label"] + ": ")
                b.bold = True
                runs(sp, it["text"])
            elif it["kind"] == "bullet":
                bp = doc.add_paragraph(style="List Bullet")
                bp.paragraph_format.space_after = Pt(0)
                runs(bp, it["text"])
            elif it["kind"] == "entry":
                ep = para(before=2)
                ep.paragraph_format.tab_stops.add_tab_stop(text_width, WD_TAB_ALIGNMENT.RIGHT)
                t = ep.add_run(it["title"])
                t.bold = True
                if it.get("sub"):
                    runs(ep, " | " + it["sub"], italic=True, color=RGBColor(0x44, 0x44, 0x44))
                runs(ep, "\t" + it.get("right", ""), italic=True)
    doc.save(str(path))


# ---------------- cover letter ----------------

def render_letter_pdf(text: str, profile: dict, path: Path, date_line: str) -> None:
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=10.5, leading=14.5, spaceAfter=9)
    name = ParagraphStyle("name", parent=body, fontName="Helvetica-Bold", fontSize=14, textColor=HexColor("#" + ACCENT), spaceAfter=2)
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
    story = [Paragraph(esc(profile["name"]), name), Paragraph(esc(_contact(profile)), body), Paragraph(date_line, body)]
    story += [Paragraph(esc(par), body) for par in text.split("\n\n") if par.strip()]
    SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                      topMargin=0.8 * inch, bottomMargin=0.8 * inch, title=f"{profile['name']} Cover Letter").build(story)


def render_letter_docx(text: str, profile: dict, path: Path, date_line: str) -> None:
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(11)
    r = doc.add_paragraph().add_run(profile["name"])
    r.bold, r.font.size, r.font.color.rgb = True, Pt(14), RGBColor.from_string(ACCENT)
    doc.add_paragraph(_contact(profile))
    doc.add_paragraph(date_line)
    for par in text.split("\n\n"):
        if par.strip():
            doc.add_paragraph(par.strip())
    doc.save(str(path))
