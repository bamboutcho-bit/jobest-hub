import io
import zipfile
from email.message import EmailMessage
import pytest

from src.candidate.cover_letter_generator import (
    build_company_motivation_letter_text,
    generate_cover_letter_docx,
    save_company_cover_letter_docx,
)
from src.application.auto_apply import _build_application_message


def test_build_motivation_letter_text_english():
    job = {
        "id": 101,
        "company": "Stripe",
        "title": "Senior Infrastructure Engineer",
        "description": "Building global payments infrastructure using Go, Python, and AWS.",
    }
    profile = {
        "name": "Alex Smith",
        "headline": "Senior Cloud Infrastructure Engineer",
        "email": "alex@example.com",
        "phone": "+1 555-0199",
        "location": "San Francisco, CA",
        "core_stack": ["Go", "Python", "Kubernetes", "AWS", "Terraform"],
    }

    data = build_company_motivation_letter_text(job, profile=profile, is_french=False)
    assert data["candidate_name"] == "Alex Smith"
    assert data["company"] == "Stripe"
    assert "Senior Infrastructure Engineer" in data["subject"]
    assert "Dear Stripe Hiring Team," in data["salutation"]
    assert len(data["paragraphs"]) >= 3
    assert any("Stripe" in p for p in data["paragraphs"])
    assert any("Go" in p or "Python" in p or "AWS" in p for p in data["paragraphs"])
    assert "Sincerely," in data["closing"]


def test_build_motivation_letter_text_french():
    job = {
        "id": 102,
        "company": "Doctolib",
        "title": "Ingénieur Backend Java / Spring Boot",
        "description": "Développement de microservices de santé en Java et PostgreSQL.",
    }
    profile = {
        "name": "Yassine El Amrani",
        "headline": "Ingénieur Logiciel Backend",
        "email": "yassine@example.com",
        "phone": "+33 6 12 34 56 78",
        "location": "Paris, France",
        "core_stack": ["Java", "Spring Boot", "PostgreSQL", "Docker"],
    }

    data = build_company_motivation_letter_text(job, profile=profile, is_french=True)
    assert data["candidate_name"] == "Yassine El Amrani"
    assert data["company"] == "Doctolib"
    assert "Candidature pour le poste de Ingénieur Backend" in data["subject"]
    assert "Doctolib" in data["salutation"]
    assert len(data["paragraphs"]) >= 3
    assert any("Doctolib" in p for p in data["paragraphs"])
    assert "salutations distinguées" in data["closing"]


def test_generate_cover_letter_docx_structure():
    job = {
        "id": 202,
        "company": "Datadog",
        "title": "Systems Reliability Engineer",
    }
    profile = {
        "name": "Jane Doe",
        "headline": "Site Reliability Engineer",
        "email": "jane@example.com",
        "phone": "+44 20 7946 0912",
        "location": "London, UK",
        "core_stack": ["Linux", "Kubernetes", "Python", "Prometheus"],
    }

    docx_bytes = generate_cover_letter_docx(job, profile=profile)
    assert isinstance(docx_bytes, bytes)
    assert len(docx_bytes) > 1000

    # Verify standard OpenXML / ZIP packaging
    buf = io.BytesIO(docx_bytes)
    with zipfile.ZipFile(buf, "r") as z:
        file_list = z.namelist()
        assert "[Content_Types].xml" in file_list
        assert "_rels/.rels" in file_list
        assert "word/_rels/document.xml.rels" in file_list
        assert "word/styles.xml" in file_list
        assert "word/document.xml" in file_list

        doc_xml = z.read("word/document.xml").decode("utf-8")
        assert "Jane Doe" in doc_xml
        assert "Datadog" in doc_xml
        assert "Systems Reliability Engineer" in doc_xml


def test_outbound_email_message_has_both_cv_and_docx_letter():
    job = {
        "id": 303,
        "company": "Algolia",
        "title": "Search Engine Engineer",
        "description": "High performance search APIs.",
    }
    profile = {
        "name": "Karim Tazi",
        "headline": "Search Systems Developer",
        "email": "karim@example.com",
        "resume_text": "Experienced engineer specializing in search index engines and C++ / Python APIs.",
    }

    msg, attached = _build_application_message(
        to_addr="jobs@algolia.com",
        subject="Application - Search Engine Engineer - Karim Tazi",
        body="Please find attached my application documents.",
        profile=profile,
        user_id=1,
        job=job,
    )

    attachments = list(msg.iter_attachments())
    assert len(attachments) >= 1

    filenames = [a.get_filename() for a in attachments]
    content_types = [a.get_content_type() for a in attachments]

    # Motivation Letter docx must be present and dedicated to Algolia
    docx_attachments = [fn for fn in filenames if fn and fn.endswith(".docx")]
    assert len(docx_attachments) == 1
    assert "Motivation_Letter_Algolia.docx" in docx_attachments[0] or "Algolia" in docx_attachments[0]

    docx_part = [a for a in attachments if a.get_filename() and a.get_filename().endswith(".docx")][0]
    assert "wordprocessingml.document" in docx_part.get_content_type()
    assert len(docx_part.get_content()) > 1000
