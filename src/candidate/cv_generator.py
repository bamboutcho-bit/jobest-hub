"""Pure Python ATS-friendly PDF resume generator for dynamic user profiles.

Generates standard, beautifully-formatted PDF documents with zero external C/pip dependencies.
Works across all operating systems and environments.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _escape_pdf_text(text: str) -> str:
    """Escape parentheses and backslashes for PDF string literals."""
    if not text:
        return ""
    # Remove characters outside latin1 range or replace with ASCII equivalents
    replacements = {
        "’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-",
        "é": "e", "è": "e", "ê": "e", "ë": "e", "à": "a", "â": "a",
        "î": "i", "ï": "i", "ô": "o", "ù": "u", "û": "u", "ç": "c",
        "É": "E", "È": "E", "Ê": "E", "À": "A", "Ç": "C", "•": "*",
        "€": "EUR", "£": "GBP", "…": "...", "✓": "[v]", "·": "-",
    }
    for orig, rep in replacements.items():
        text = text.replace(orig, rep)
    
    # Filter to printable ASCII / latin-1
    cleaned = ""
    for ch in text:
        code = ord(ch)
        if 32 <= code <= 126 or code in (9, 10, 13):
            cleaned += ch
        else:
            cleaned += " "
            
    return cleaned.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_text(text: str, max_chars: int = 85) -> list[str]:
    """Wrap text to avoid overflowing the printable width."""
    lines: list[str] = []
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split()
        current_line: list[str] = []
        current_len = 0
        for w in words:
            if current_len + len(w) + 1 > max_chars:
                lines.append(" ".join(current_line))
                current_line = [w]
                current_len = len(w)
            else:
                current_line.append(w)
                current_len += len(w) + 1
        if current_line:
            lines.append(" ".join(current_line))
    return lines


def build_pdf_document(pages_content: list[list[tuple[str, int, float, float, str]]]) -> bytes:
    """Build a complete, compliant PDF 1.4 binary document from page commands.
    
    pages_content: list of pages. Each page is a list of commands:
      (font_alias, font_size, x, y, text)
    """
    total_pages = len(pages_content)
    if total_pages == 0:
        total_pages = 1
        pages_content = [[("/F1", 11, 50, 750, "Resume")]]

    page_obj_ids = [3 + i for i in range(total_pages)]
    content_obj_ids = [3 + total_pages + i for i in range(total_pages)]
    font1_id = 3 + 2 * total_pages
    font2_id = font1_id + 1
    total_objects = font2_id

    objs: list[bytes] = []

    # Object 1: Catalog
    objs.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # Object 2: Pages container
    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objs.append(f"2 0 obj\n<< /Type /Pages /Kids [{kids_str}] /Count {total_pages} >>\nendobj\n".encode("latin1"))

    # Page objects
    for i, pid in enumerate(page_obj_ids):
        cid = content_obj_ids[i]
        objs.append(
            f"{pid} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font1_id} 0 R /F2 {font2_id} 0 R >> >> "
            f"/Contents {cid} 0 R >>\nendobj\n".encode("latin1")
        )

    # Content streams for each page
    for i, cid in enumerate(content_obj_ids):
        cmds = pages_content[i]
        stream_parts = ["BT"]
        for font_alias, size, x, y, text in cmds:
            esc = _escape_pdf_text(text)
            stream_parts.append(f"{font_alias} {size} Tf {x:.1f} {y:.1f} Td ({esc}) Tj ET BT")
        stream_parts.append("ET")
        stream_bytes = "\n".join(stream_parts).encode("latin1")
        objs.append(
            f"{cid} 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin1")
            + stream_bytes +
            b"\nendstream\nendobj\n"
        )

    # Font 1: Helvetica (Regular)
    objs.append(f"{font1_id} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n".encode("latin1"))

    # Font 2: Helvetica-Bold
    objs.append(f"{font2_id} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n".encode("latin1"))

    # Assemble document with cross-reference table
    output = b"%PDF-1.4\n"
    offsets = [0]
    for obj in objs:
        offsets.append(len(output))
        output += obj

    xref_pos = len(output)
    output += f"xref\n0 {len(offsets)}\n".encode("latin1")
    output += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        output += f"{off:010d} 00000 n \n".encode("latin1")

    output += f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin1")
    return output


def generate_resume_pdf(profile_data: dict[str, Any], user_email: str | None = None) -> bytes:
    """Generate a clean, structured, ATS-compliant PDF resume from profile data."""
    name = (profile_data.get("name") or "Candidate Name").strip()
    headline = (profile_data.get("headline") or "Software Engineer").strip()
    location = (profile_data.get("current_location") or "Remote / Morocco").strip()
    email = (profile_data.get("email") or user_email or "contact@autohunt.io").strip()
    calendar = (profile_data.get("calendar_url") or "").strip()
    degree = (profile_data.get("degree_level") or "Bachelor / Licence").replace("_", " ").title()
    experience_years = profile_data.get("experience_years", 0)
    seniority = (profile_data.get("target_experience_level") or "entry").replace("_", " ").title()
    visa_notes = (profile_data.get("visa_requirement") or "").strip()

    target_titles = profile_data.get("target_titles") or []
    if isinstance(target_titles, str):
        target_titles = [t.strip() for t in target_titles.split(",") if t.strip()]

    core_stack = profile_data.get("core_stack") or []
    if isinstance(core_stack, str):
        core_stack = [s.strip() for s in core_stack.split(",") if s.strip()]

    keywords = profile_data.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    languages = profile_data.get("languages") or {}
    resume_text = (profile_data.get("resume_text") or "").strip()

    # Layout coordinates: Page is 595 x 842 pt
    # Margins: Left=50, Right=545 (width=495)
    pages: list[list[tuple[str, int, float, float, str]]] = []
    current_page: list[tuple[str, int, float, float, str]] = []
    y = 795.0

    def check_page_break(needed_space: float = 30.0):
        nonlocal y, current_page
        if y - needed_space < 55.0:
            pages.append(current_page)
            current_page = []
            y = 795.0

    # Header: Name (Bold 18pt)
    current_page.append(("/F2", 18, 50, y, name))
    y -= 20

    # Headline (11pt Regular)
    current_page.append(("/F1", 11, 50, y, headline))
    y -= 16

    # Contact line (9pt Regular)
    contact_parts = [f"Location: {location}", f"Email: {email}"]
    if calendar:
        contact_parts.append(f"Calendar: {calendar}")
    contact_line = "  |  ".join(contact_parts)
    current_page.append(("/F1", 9, 50, y, contact_line))
    y -= 14

    # Divider line
    current_page.append(("/F1", 9, 50, y, "__________________________________________________________________________________"))
    y -= 22

    # Section 1: Professional Summary / Profile Overview
    check_page_break(50)
    current_page.append(("/F2", 12, 50, y, "PROFESSIONAL PROFILE"))
    y -= 16

    summary_bullets = [
        f"Seniority & Experience: {seniority} level ({experience_years}+ years relevant experience)",
        f"Education / Degree: {degree} Degree in Engineering / Computer Science",
    ]
    if visa_notes:
        summary_bullets.append(f"Work Authorization & Mobility: {visa_notes}")

    for bullet in summary_bullets:
        check_page_break(14)
        current_page.append(("/F1", 9.5, 55, y, f"* {bullet}"))
        y -= 14

    y -= 8

    # Section 2: Core Technical Stack & Competencies
    if core_stack or keywords:
        check_page_break(60)
        current_page.append(("/F2", 12, 50, y, "TECHNICAL COMPETENCIES & STACK"))
        y -= 16

        if core_stack:
            check_page_break(24)
            current_page.append(("/F2", 9.5, 55, y, "Primary Technologies:"))
            y -= 13
            stack_str = ", ".join(core_stack)
            for line in _wrap_text(stack_str, 80):
                check_page_break(13)
                current_page.append(("/F1", 9.5, 65, y, line))
                y -= 13
            y -= 4

        if keywords:
            check_page_break(24)
            current_page.append(("/F2", 9.5, 55, y, "Domains & Tools:"))
            y -= 13
            kw_str = ", ".join(keywords)
            for line in _wrap_text(kw_str, 80):
                check_page_break(13)
                current_page.append(("/F1", 9.5, 65, y, line))
                y -= 13
            y -= 4

        if target_titles:
            check_page_break(24)
            current_page.append(("/F2", 9.5, 55, y, "Target Roles:"))
            y -= 13
            titles_str = " | ".join(target_titles)
            for line in _wrap_text(titles_str, 80):
                check_page_break(13)
                current_page.append(("/F1", 9.5, 65, y, line))
                y -= 13
            y -= 4

        y -= 6

    # Section 3: Professional Experience & Accomplishments (from resume_text)
    check_page_break(60)
    current_page.append(("/F2", 12, 50, y, "EXPERIENCE, PROJECTS & ACCOMPLISHMENTS"))
    y -= 16

    if resume_text:
        wrapped_lines = _wrap_text(resume_text, max_chars=82)
        for line in wrapped_lines:
            check_page_break(13)
            if not line:
                y -= 6
                continue
            is_header = line.isupper() or line.endswith(":") or (line.startswith("#") and len(line) < 50)
            if is_header:
                clean_header = line.lstrip("#").strip()
                check_page_break(20)
                current_page.append(("/F2", 10, 55, y, clean_header))
                y -= 14
            elif line.startswith("-") or line.startswith("*"):
                current_page.append(("/F1", 9, 60, y, line))
                y -= 13
            else:
                current_page.append(("/F1", 9, 55, y, line))
                y -= 13
    else:
        # Default placeholder accomplishments if text not filled yet
        default_bullets = [
            f"Designed, developed, and deployed modern solutions leveraging {', '.join(core_stack[:4]) if core_stack else 'modern software stack'}.",
            "Built clean RESTful APIs, distributed architectures, and maintainable services with automated tests.",
            "Collaborated within cross-functional engineering teams following Agile methodology and CI/CD pipelines.",
        ]
        for b in default_bullets:
            check_page_break(14)
            current_page.append(("/F1", 9, 55, y, f"* {b}"))
            y -= 14

    y -= 8

    # Section 4: Languages & International Communication
    if languages:
        check_page_break(40)
        current_page.append(("/F2", 12, 50, y, "LANGUAGES"))
        y -= 16
        if isinstance(languages, dict):
            lang_str = "   |   ".join(f"{k}: {v}" for k, v in languages.items())
        else:
            lang_str = str(languages)
        current_page.append(("/F1", 9.5, 55, y, lang_str))
        y -= 18

    # Footer note on last page
    check_page_break(20)
    current_page.append(("/F1", 8, 50, 40, f"Generated directly via AutoHunt Platform for {name} ({email})"))

    pages.append(current_page)
    return build_pdf_document(pages)


def save_user_resume_pdf(user_id: int, profile_data: dict[str, Any], user_email: str | None = None) -> str:
    """Generate and save resume PDF to user's dedicated upload directory. Return relative/absolute path."""
    upload_dir = Path("uploads") / "resumes" / f"user_{user_id}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    name_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', (profile_data.get("name") or f"user_{user_id}")).strip("_")
    filename = f"{name_slug}_CV.pdf"
    file_path = upload_dir / filename

    pdf_bytes = generate_resume_pdf(profile_data, user_email=user_email)
    file_path.write_bytes(pdf_bytes)
    logger.info("Generated user resume PDF at: %s (%d bytes)", file_path, len(pdf_bytes))
    return str(file_path.resolve())
