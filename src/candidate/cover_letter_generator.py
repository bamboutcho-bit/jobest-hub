"""Dedicated Company Motivation Letter (Cover Letter) Generator for OpenXML (.docx).

Generates tailored, professional, company-specific motivation letters as Microsoft Word (.docx) files
with zero external pip or C dependencies (using Python's standard zipfile and XML).
Attached automatically alongside the candidate's CV when applying to companies.
"""
from __future__ import annotations

import io
import re
import xml.sax.saxutils as saxutils
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from src.evaluation.language import is_french_job


def _xml_escape(text: str) -> str:
    """Escape text for XML safely."""
    return saxutils.escape(text or "", entities={'"': "&quot;", "'": "&apos;"})


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def build_company_motivation_letter_text(
    job: Any,
    profile: dict | None = None,
    is_french: bool | None = None,
) -> dict[str, Any]:
    """Generate structured, company-dedicated motivation letter content tailored to the job and profile."""
    if is_french is None:
        is_french = is_french_job(job)

    # Candidate details
    p = profile or {}
    candidate_name = _clean_str(p.get("name") or "Candidate")
    if " - " in candidate_name:
        candidate_name = candidate_name.split(" - ")[0].strip()
    headline = _clean_str(p.get("headline") or "Software Engineer")
    email = _clean_str(p.get("email") or "")
    phone = _clean_str(p.get("phone") or "")
    location = _clean_str(p.get("location") or p.get("current_location") or "Remote")
    core_stack = p.get("core_stack") or ["Python", "FastAPI", "PostgreSQL", "Docker"]
    if isinstance(core_stack, str):
        try:
            import json
            core_stack = json.loads(core_stack)
        except Exception:
            core_stack = [s.strip() for s in core_stack.split(",") if s.strip()]

    stack_str = ", ".join(core_stack[:5]) if core_stack else "modern software engineering architectures"

    # Job / Company details
    if isinstance(job, dict):
        company = _clean_str(job.get("company") or "the hiring team")
        title = _clean_str(job.get("title") or "Software Engineer")
        custom_fr = _clean_str(job.get("application_email_fr") or job.get("recruiter_pitch_fr"))
        custom_en = _clean_str(job.get("application_email_en") or job.get("recruiter_pitch_en"))
    else:
        company = _clean_str(getattr(job, "company", "the hiring team"))
        title = _clean_str(getattr(job, "title", "Software Engineer"))
        custom_fr = _clean_str(getattr(job, "application_email_fr", "") or getattr(job, "recruiter_pitch_fr", ""))
        custom_en = _clean_str(getattr(job, "application_email_en", "") or getattr(job, "recruiter_pitch_en", ""))

    now = datetime.now()
    if is_french:
        date_str = now.strftime("%d/%m/%Y")
        subject = f"Candidature pour le poste de {title} — {candidate_name}"
        salutation = f"Madame, Monsieur, Équipe Recrutement de {company},"

        if custom_fr and len(custom_fr) > 80:
            # Clean AI-generated message into paragraphs
            paragraphs = [p.strip() for p in custom_fr.split("\n\n") if p.strip()]
            # Filter greeting and signoff if already present
            paragraphs = [
                p for p in paragraphs 
                if not any(p.lower().startswith(x) for x in ("bonjour", "madame", "cher", "chère", "cordialement", "bien cordialement", "salutations"))
            ]
        else:
            paragraphs = [
                f"Je me permets de vous adresser ma candidature pour le poste de {title} au sein de {company}. Passionné par les défis d'ingénierie et l'impact des technologies modernes, j'ai suivi avec beaucoup d'intérêt le développement de votre organisation et la pertinence de vos réalisations.",
                f"Fort de mon parcours en tant que {headline} et d'une maîtrise approfondie de technologies clés telles que {stack_str}, j'ai conçu et mis en production des architectures performantes, maintenables et sécurisées. Mon approche repose sur une forte rigueur méthodologique, l'optimisation continue des performances et une collaboration étroite avec les équipes produit et métier.",
                f"Rejoindre {company} représente pour moi l'opportunité de mettre mon énergie, mes compétences techniques et mon sens du résultat au service de vos objectifs stratégiques. Je suis convaincu que mon autonomie et ma capacité d'adaptation me permettront d'être rapidement opérationnel et force de proposition au sein de votre équipe.",
                f"Vous trouverez ci-joint mon Curriculum Vitae détaillant mes expériences professionnelles et réalisations techniques. Je serais honoré de vous rencontrer lors d'un entretien afin d'approfondir l'adéquation de mon profil avec les enjeux de {company}."
            ]

        closing = "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées."
    else:
        date_str = now.strftime("%B %d, %Y")
        subject = f"Application for {title} — {candidate_name}"
        salutation = f"Dear {company} Hiring Team,"

        if custom_en and len(custom_en) > 80:
            paragraphs = [p.strip() for p in custom_en.split("\n\n") if p.strip()]
            paragraphs = [
                p for p in paragraphs 
                if not any(p.lower().startswith(x) for x in ("dear", "hello", "hi", "best regards", "sincerely", "regards"))
            ]
        else:
            paragraphs = [
                f"I am writing to express my strong enthusiasm and formally submit my application for the {title} position at {company}. Having followed your company's milestones and industry impact, I am genuinely excited by the prospect of contributing to your team's ongoing initiatives.",
                f"As a {headline} with hands-on expertise across {stack_str}, I specialize in designing scalable systems, building resilient microservices, and delivering clean, maintainable code. In my previous work, I have consistently focused on engineering excellence, high system availability, and rapid delivery of user-centric features.",
                f"What specifically appeals to me about {company} is your commitment to technical innovation and quality. I thrive in collaborative environments where ownership, problem-solving, and continuous learning are valued, and I am confident that my technical background and problem-solving mindset align directly with your current goals.",
                f"Please find attached my resume providing full details on my technical projects and professional achievements. I would welcome the opportunity to discuss how my skill set and experience can support {company}'s roadmap during an interview. Thank you for your time and consideration."
            ]

        closing = "Sincerely,"

    return {
        "candidate_name": candidate_name,
        "headline": headline,
        "email": email,
        "phone": phone,
        "location": location,
        "date_str": date_str,
        "company": company,
        "title": title,
        "subject": subject,
        "salutation": salutation,
        "paragraphs": paragraphs,
        "closing": closing,
        "is_french": is_french,
    }


def generate_cover_letter_docx(
    job: Any,
    profile: dict | None = None,
    is_french: bool | None = None,
    custom_body_text: str | None = None,
) -> bytes:
    """Generate a clean, professional ATS-standard Microsoft Word (.docx) document for the motivation letter."""
    data = build_company_motivation_letter_text(job, profile, is_french)

    if custom_body_text and custom_body_text.strip():
        raw_paras = [p.strip() for p in custom_body_text.strip().split("\n\n") if p.strip()]
        if raw_paras:
            data["paragraphs"] = raw_paras

    # Contact line formatting
    contact_parts = []
    if data["email"]:
        contact_parts.append(data["email"])
    if data["phone"]:
        contact_parts.append(data["phone"])
    if data["location"]:
        contact_parts.append(data["location"])
    contact_line = "  •  ".join(contact_parts) if contact_parts else "Candidate Profile"

    # Build XML document paragraphs
    body_xml_parts = []

    # 1. Candidate Header / Letterhead
    body_xml_parts.append(f"""
    <w:p>
      <w:pPr>
        <w:spacing w:after="40" w:line="240" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
          <w:b/>
          <w:sz w:val="36"/>
          <w:szCs w:val="36"/>
          <w:color w:val="0F172A"/>
        </w:rPr>
        <w:t>{_xml_escape(data['candidate_name'])}</w:t>
      </w:r>
    </w:p>
    """)

    body_xml_parts.append(f"""
    <w:p>
      <w:pPr>
        <w:spacing w:after="80" w:line="240" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
          <w:sz w:val="22"/>
          <w:szCs w:val="22"/>
          <w:color w:val="0284C7"/>
        </w:rPr>
        <w:t>{_xml_escape(data['headline'])}</w:t>
      </w:r>
    </w:p>
    """)

    body_xml_parts.append(f"""
    <w:p>
      <w:pPr>
        <w:pBdr>
          <w:bottom w:val="single" w:sz="12" w:space="8" w:color="0284C7"/>
        </w:pBdr>
        <w:spacing w:after="300" w:line="240" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
          <w:sz w:val="18"/>
          <w:szCs w:val="18"/>
          <w:color w:val="64748B"/>
        </w:rPr>
        <w:t>{_xml_escape(contact_line)}</w:t>
      </w:r>
    </w:p>
    """)

    # 2. Date & Recipient Details
    body_xml_parts.append(f"""
    <w:p>
      <w:pPr>
        <w:jc w:val="right"/>
        <w:spacing w:after="180" w:line="240" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
          <w:sz w:val="20"/>
          <w:color w:val="64748B"/>
        </w:rPr>
        <w:t>{_xml_escape(data['date_str'])}</w:t>
      </w:r>
    </w:p>
    """)

    body_xml_parts.append(f"""
    <w:p>
      <w:pPr>
        <w:spacing w:after="40" w:line="240" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
          <w:b/>
          <w:sz w:val="22"/>
          <w:color w:val="1E293B"/>
        </w:rPr>
        <w:t>{_xml_escape(data['company'])}</w:t>
      </w:r>
    </w:p>
    """)

    body_xml_parts.append(f"""
    <w:p>
      <w:pPr>
        <w:spacing w:after="240" w:line="240" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
          <w:sz w:val="20"/>
          <w:color w:val="475569"/>
        </w:rPr>
        <w:t>{'Équipe Recrutement &amp; Ressources Humaines' if data['is_french'] else 'Talent Acquisition &amp; Hiring Team'}</w:t>
      </w:r>
    </w:p>
    """)

    # 3. Subject Line (Bold, Boxed Accent)
    body_xml_parts.append(f"""
    <w:p>
      <w:pPr>
        <w:spacing w:before="120" w:after="240" w:line="260" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
          <w:b/>
          <w:sz w:val="22"/>
          <w:color w:val="0F172A"/>
        </w:rPr>
        <w:t>{_xml_escape(('Objet : ' if data['is_french'] else 'Subject: ') + data['subject'])}</w:t>
      </w:r>
    </w:p>
    """)

    # 4. Salutation
    body_xml_parts.append(f"""
    <w:p>
      <w:pPr>
        <w:spacing w:after="160" w:line="260" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
          <w:sz w:val="22"/>
          <w:color w:val="1E293B"/>
        </w:rPr>
        <w:t>{_xml_escape(data['salutation'])}</w:t>
      </w:r>
    </w:p>
    """)

    # 5. Motivation Paragraphs
    for p_text in data["paragraphs"]:
        body_xml_parts.append(f"""
        <w:p>
          <w:pPr>
            <w:spacing w:after="180" w:line="276" w:lineRule="auto"/>
          </w:pPr>
          <w:r>
            <w:rPr>
              <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
              <w:sz w:val="21"/>
              <w:color w:val="334155"/>
            </w:rPr>
            <w:t xml:space="preserve">{_xml_escape(p_text)}</w:t>
          </w:r>
        </w:p>
        """)

    # 6. Closing Formula & Signature
    body_xml_parts.append(f"""
    <w:p>
      <w:pPr>
        <w:spacing w:before="160" w:after="240" w:line="260" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
          <w:sz w:val="21"/>
          <w:color w:val="1E293B"/>
        </w:rPr>
        <w:t>{_xml_escape(data['closing'])}</w:t>
      </w:r>
    </w:p>
    """)

    body_xml_parts.append(f"""
    <w:p>
      <w:pPr>
        <w:spacing w:after="40" w:line="240" w:lineRule="auto"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
          <w:b/>
          <w:sz w:val="24"/>
          <w:color w:val="0F172A"/>
        </w:rPr>
        <w:t>{_xml_escape(data['candidate_name'])}</w:t>
      </w:r>
    </w:p>
    """)

    body_xml = "".join(body_xml_parts)

    # Standard OpenXML Files
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        '  <Default Extension="xml" ContentType="application/xml"/>\n'
        '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
        '  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>\n'
        '</Types>'
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
        '</Relationships>'
    )

    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>\n'
        '</Relationships>'
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
        '  <w:docDefaults>\n'
        '    <w:rPrDefault>\n'
        '      <w:rPr>\n'
        '        <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>\n'
        '        <w:sz w:val="22"/>\n'
        '        <w:szCs w:val="22"/>\n'
        '        <w:color w:val="334155"/>\n'
        '      </w:rPr>\n'
        '    </w:rPrDefault>\n'
        '    <w:pPrDefault>\n'
        '      <w:pPr>\n'
        '        <w:spacing w:after="160" w:line="276" w:lineRule="auto"/>\n'
        '      </w:pPr>\n'
        '    </w:pPrDefault>\n'
        '  </w:docDefaults>\n'
        '</w:styles>'
    )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
        '  <w:body>\n'
        f'{body_xml}\n'
        '    <w:sectPr>\n'
        '      <w:pgSz w:w="12240" w:h="15840"/>\n'
        '      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>\n'
        '    </w:sectPr>\n'
        '  </w:body>\n'
        '</w:document>'
    )

    # Package into ZIP archive (.docx)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types.encode("utf-8"))
        z.writestr("_rels/.rels", root_rels.encode("utf-8"))
        z.writestr("word/_rels/document.xml.rels", doc_rels.encode("utf-8"))
        z.writestr("word/styles.xml", styles_xml.encode("utf-8"))
        z.writestr("word/document.xml", document_xml.encode("utf-8"))

    return buf.getvalue()


def save_company_cover_letter_docx(
    job: Any,
    profile: dict | None = None,
    user_id: int | None = None,
    custom_body_text: str | None = None,
) -> Path:
    """Generate and save the company-dedicated motivation letter docx file into the user's uploads folder."""
    docx_bytes = generate_cover_letter_docx(job, profile=profile, custom_body_text=custom_body_text)

    uid = user_id or (profile.get("user_id") if profile else 1) or 1
    company = _clean_str(job.get("company") if isinstance(job, dict) else getattr(job, "company", "Company"))
    safe_company = re.sub(r"[^A-Za-z0-9_-]", "_", company)[:30].strip("_") or "Company"
    job_id = (job.get("id") if isinstance(job, dict) else getattr(job, "id", None)) or "new"

    out_dir = Path("uploads") / "cover_letters" / f"user_{uid}"
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"Motivation_Letter_{safe_company}_{job_id}.docx"
    target_path = out_dir / filename
    target_path.write_bytes(docx_bytes)
    return target_path
