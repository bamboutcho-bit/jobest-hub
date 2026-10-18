"""Machine Learning CV Analysis, Feature Extraction, and Candidate-Job Vector Matcher.

Extracts structured candidate data from PDF resumes using multi-filter stream decoders,
builds TF-IDF term-weight representations, and calculates Cosine Similarity between
the candidate's competencies and job requirements.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import zlib
from collections import Counter
from pathlib import Path
from typing import Any, Optional

try:
    import numpy as np
except ImportError:
    np = None

logger = logging.getLogger(__name__)

# Standard technical competencies taxonomy for classification and vector space modeling
TECH_TAXONOMY: dict[str, list[str]] = {
    "backend": [
        "java", "spring boot", "spring mvc", "spring data", "spring security", "hibernate", "jpa",
        "python", "django", "fastapi", "flask", "node.js", "express", "nest.js", "golang", "c#",
        ".net", "php", "laravel", "ruby", "rails", "rust", "scala", "c++", "c", "rest api", "restful apis",
        "graphql", "microservices", "grpc", "soap", "jwt", "oauth2", "openapi", "swagger"
    ],
    "frontend": [
        "javascript", "typescript", "react", "reactjs", "next.js", "angular", "vue", "vuejs",
        "nuxt", "html", "html5", "css", "css3", "sass", "tailwind", "tailwind css", "bootstrap",
        "redux", "axios", "webpack", "vite", "ui/ux", "responsive design"
    ],
    "databases": [
        "sql", "postgresql", "postgres", "mysql", "mariadb", "oracle", "sql server", "sqlite",
        "mongodb", "redis", "elasticsearch", "cassandra", "dynamodb", "neo4j", "database design"
    ],
    "cloud_devops": [
        "docker", "kubernetes", "k8s", "azure", "aws", "gcp", "google cloud", "ci/cd", "git",
        "github", "gitlab", "linux", "bash", "terraform", "ansible", "jenkins", "github actions",
        "nginx", "apache", "prometheus", "grafana", "devops", "cloud computing"
    ],
    "ai_ml": [
        "machine learning", "artificial intelligence", "deep learning", "nlp", "llm", "tensorflow",
        "pytorch", "scikit-learn", "pandas", "numpy", "computer vision", "generative ai", "langchain",
        "prompt engineering", "data science", "data analysis"
    ],
    "architecture_tools": [
        "microservices", "event-driven", "kafka", "rabbitmq", "activemq", "unit testing", "junit",
        "mockito", "test-driven development", "tdd", "agile", "scrum", "jira", "solid principles",
        "design patterns", "clean code", "system architecture"
    ]
}

FLATTENED_SKILLS = [skill for sublist in TECH_TAXONOMY.values() for skill in sublist]


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """Extract plain text from a PDF file using standard library stream decoders and ToUnicode CMaps.
    
    Handles:
    - High-fidelity ToUnicode CMap font mapping for TrueType / Type0 / CID fonts
    - Standard Page /Contents streams and arrays
    - ReportLab / Adobe ASCII85 + FlateDecode streams
    - Pure FlateDecode zlib streams
    - String literals (text) and Hex encoded strings <hex>
    - Excludes font binary tables (/FontFile2) and raster images to prevent binary noise
    """
    path = Path(pdf_path)
    if not path.is_file():
        logger.warning("PDF file not found: %s", pdf_path)
        return ""

    # 1. Try pypdf / pypdf2 if installed
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        extracted = []
        for p in reader.pages:
            pt = p.extract_text()
            if pt:
                extracted.append(pt)
        full = "\n".join(extracted).strip()
        if len(full) > 50:
            return full
    except Exception:
        pass

    try:
        content = path.read_bytes()
    except Exception as e:
        logger.error("Failed to read PDF file %s: %s", pdf_path, e)
        return ""

    # 2. Parse PDF Objects
    obj_pattern = re.compile(rb'(\d+)\s+(\d+)\s+obj\s*(.*?)endobj', re.DOTALL)
    objects: dict[int, bytes] = {}
    for m in obj_pattern.finditer(content):
        objects[int(m.group(1))] = m.group(3)

    def _get_decompressed_stream(raw_obj: bytes) -> bytes:
        if not raw_obj:
            return b""
        sm = re.search(rb'stream\r?\n(.*?)\r?\nendstream', raw_obj, re.DOTALL)
        if not sm:
            return b""
        data = sm.group(1)
        if b"/FlateDecode" in raw_obj:
            try:
                data = zlib.decompress(data)
            except Exception:
                try:
                    data = zlib.decompress(data, -15)
                except Exception:
                    pass
        elif b"/ASCII85Decode" in raw_obj:
            try:
                a85 = data.strip()
                if not a85.endswith(b"~>"):
                    a85 += b"~>"
                data = base64.a85decode(a85, adobe=True)
                if b"/FlateDecode" in raw_obj:
                    data = zlib.decompress(data)
            except Exception:
                pass
        return data

    def _parse_cmap(cmap_data: bytes) -> dict[int, str]:
        mapping: dict[int, str] = {}
        if not cmap_data:
            return mapping
        # 1. Parse beginbfchar blocks only
        for block in re.finditer(rb'beginbfchar\s*(.*?)\s*endbfchar', cmap_data, re.DOTALL):
            for bfc in re.finditer(rb'<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>', block.group(1)):
                try:
                    src_code = int(bfc.group(1), 16)
                    dst_hex = bfc.group(2).decode("ascii")
                    mapping[src_code] = bytes.fromhex(dst_hex).decode("utf-16-be", errors="ignore")
                except Exception:
                    pass
        # 2. Parse beginbfrange blocks only
        for block in re.finditer(rb'beginbfrange\s*(.*?)\s*endbfrange', cmap_data, re.DOTALL):
            for bfr in re.finditer(rb'<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>', block.group(1)):
                try:
                    s_code = int(bfr.group(1), 16)
                    e_code = int(bfr.group(2), 16)
                    dst_start = int(bfr.group(3), 16)
                    for c in range(s_code, e_code + 1):
                        target = dst_start + (c - s_code)
                        h = f"{target:04x}" if target <= 0xffff else f"{target:08x}"
                        mapping[c] = bytes.fromhex(h).decode("utf-16-be", errors="ignore")
                except Exception:
                    pass
        return mapping

    # Parse all ToUnicode CMaps for font objects
    font_cmaps: dict[int, dict[int, str]] = {}
    for num, obj_raw in objects.items():
        if b"/ToUnicode" in obj_raw:
            m_tu = re.search(rb'/ToUnicode\s+(\d+)\s+\d+\s+R', obj_raw)
            if m_tu:
                tu_id = int(m_tu.group(1))
                tu_stream = _get_decompressed_stream(objects.get(tu_id, b""))
                if tu_stream:
                    font_cmaps[num] = _parse_cmap(tu_stream)

    # 3. Process Page objects and their content streams
    page_texts: list[str] = []

    for num, obj_raw in objects.items():
        if re.search(rb'/Type\s*/Page(?!\w)', obj_raw):
            content_ids: list[int] = []
            m_c = re.search(rb'/Contents\s+(\d+)\s+\d+\s+R', obj_raw)
            if m_c:
                content_ids.append(int(m_c.group(1)))
            else:
                m_c_arr = re.search(rb'/Contents\s*\[(.*?)\]', obj_raw)
                if m_c_arr:
                    content_ids.extend([int(x) for x in re.findall(rb'(\d+)\s+\d+\s+R', m_c_arr.group(1))])

            res_raw = obj_raw
            m_res = re.search(rb'/Resources\s+(\d+)\s+\d+\s+R', obj_raw)
            if m_res:
                res_raw = objects.get(int(m_res.group(1)), b"")

            font_name_map: dict[str, dict[int, str]] = {}
            m_font_dict = re.search(rb'/Font\s*<<\s*(.*?)\s*>>', res_raw, re.DOTALL)
            if not m_font_dict:
                m_font_ref = re.search(rb'/Font\s+(\d+)\s+\d+\s+R', res_raw)
                if m_font_ref:
                    font_dict_obj = objects.get(int(m_font_ref.group(1)), b"")
                    m_font_dict = re.search(rb'<<\s*(.*?)\s*>>', font_dict_obj, re.DOTALL)

            if m_font_dict:
                for f_match in re.finditer(rb'/(F\w+)\s+(\d+)\s+\d+\s+R', m_font_dict.group(1)):
                    fn = "/" + f_match.group(1).decode("ascii")
                    f_id = int(f_match.group(2))
                    if f_id in font_cmaps:
                        font_name_map[fn] = font_cmaps[f_id]

            # Decode content stream(s) for this page
            page_lines: list[str] = []
            for cid in content_ids:
                stream_bytes = _get_decompressed_stream(objects.get(cid, b""))
                if not stream_bytes:
                    continue
                content_str = stream_bytes.decode("latin1", errors="ignore")
                current_font = ""

                for bt in re.finditer(r'BT(.*?)ET', content_str, re.DOTALL):
                    block = bt.group(1)
                    for op in re.finditer(r'(/F\w+)\s+[\d\.]+\s+Tf|(\[[^\]]*\])\s*TJ|(<[0-9a-fA-F]+>)\s*Tj|(\([^)]*\))\s*Tj', block):
                        if op.group(1):
                            current_font = op.group(1)
                        elif op.group(2):
                            cmap = font_name_map.get(current_font, {})
                            chars: list[str] = []
                            for item in re.finditer(r'<([0-9a-fA-F]+)>|\(([^)]*)\)', op.group(2)):
                                if item.group(1):
                                    hex_str = item.group(1)
                                    for i in range(0, len(hex_str), 2):
                                        bval = int(hex_str[i:i+2], 16)
                                        chars.append(cmap.get(bval, chr(bval) if 32 <= bval < 127 else " "))
                                elif item.group(2):
                                    chars.append(_clean_pdf_string(item.group(2)))
                            line_text = "".join(chars).strip()
                            if line_text:
                                page_lines.append(line_text)
                        elif op.group(3):
                            hex_str = op.group(3)[1:-1]
                            cmap = font_name_map.get(current_font, {})
                            chars = []
                            for i in range(0, len(hex_str), 2):
                                bval = int(hex_str[i:i+2], 16)
                                chars.append(cmap.get(bval, chr(bval) if 32 <= bval < 127 else " "))
                            line_text = "".join(chars).strip()
                            if line_text:
                                page_lines.append(line_text)
                        elif op.group(4):
                            line_text = _clean_pdf_string(op.group(4)[1:-1])
                            if line_text:
                                page_lines.append(line_text)

            if page_lines:
                page_texts.append("\n".join(page_lines))

    if page_texts:
        full_result = "\n".join(page_texts).strip()
        if len(full_result) > 40:
            return full_result

    # 4. Fallback for flat unformatted or ReportLab streams (excluding font/image binaries)
    fallback_parts: list[str] = []
    for num, obj_raw in objects.items():
        if any(bad in obj_raw for bad in (b"/FontFile", b"/FontFile2", b"/FontFile3", b"/Length1", b"/Length2", b"/Subtype/Image", b"/Metadata")):
            continue
        stream_bytes = _get_decompressed_stream(obj_raw)
        if not stream_bytes or len(stream_bytes) < 10:
            continue
        for s in re.findall(rb'\((.*?)\)', stream_bytes):
            t = _clean_pdf_string(s.decode("latin1", errors="ignore"))
            if len(t) >= 2:
                fallback_parts.append(t)

    fallback_text = " ".join(fallback_parts)
    fallback_text = re.sub(r'\s+', ' ', fallback_text).strip()
    return fallback_text


def _clean_pdf_string(raw: str) -> str:
    """Clean PDF string escapes and non-printable characters."""
    t = raw.replace(r'\(', '(').replace(r'\)', ')').replace(r'\\', '\\')
    # Filter control characters
    return "".join(c if (c.isalnum() or c in " .,;:/?!@#$%^&*()-_+='\"<>[]{}|~`\n") else " " for c in t).strip()


def analyze_cv_content(text: str) -> dict[str, Any]:
    """Perform NLP & ML feature extraction on raw CV text.
    
    Extracts:
    - Contact Information (Name, Email, Phone, Location, Profiles)
    - Professional Experience & Estimated Years
    - Education Degrees & Institutions
    - Categorized Technical Competencies
    - TF-IDF Vocabulary Weights
    - Target Roles Recommendations
    """
    if not text:
        return {
            "name": "", "email": "", "phone": "", "location": "",
            "linkedin": "", "github": "", "education": [], "experience": [],
            "experience_years": 0, "degree_level": "none", "categorized_skills": {},
            "top_skills": [], "target_roles": [], "summary": ""
        }

    # 1. Contact Information Extraction
    email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)
    email = email_match.group(0) if email_match else ""

    phone_match = re.search(r'(\+?\d{1,4}[-.\s]??(?:\(?\d{1,4}\)?[-.\s]??)?\d{1,4}[-.\s]??\d{1,4}[-.\s]??\d{1,9})', text)
    phone = phone_match.group(0).strip() if phone_match else ""

    linkedin_match = re.search(r'(linkedin\.com/in/[A-Za-z0-9_-]+)', text, re.I)
    linkedin = f"https://{linkedin_match.group(0)}" if linkedin_match else ""

    github_match = re.search(r'(github\.com/[A-Za-z0-9_-]+)', text, re.I)
    github = f"https://{github_match.group(0)}" if github_match else ""

    # Candidate Name (Header identification: clean first 2-3 words, stopping before title)
    name = ""
    header_chunk = text[:150]
    for stopper in ("ENGINEER", "DEVELOPER", "INGÉNIEUR", "INGENIEUR", "PROFILE", "CURRICULUM"):
        if stopper in header_chunk.upper():
            header_chunk = re.split(stopper, header_chunk, flags=re.I)[0]
    name_tokens = [w for w in re.findall(r'[A-Za-z]+', header_chunk) if len(w) > 1][:3]
    if len(name_tokens) >= 2:
        name = " ".join(name_tokens).title()

    # Location Extraction
    location = ""
    if "morocco" in text.lower() or "maroc" in text.lower():
        if "salé" in text.lower() or "sale" in text.lower() or "rabat" in text.lower():
            location = "Salé / Rabat, Morocco"
        elif "casablanca" in text.lower():
            location = "Casablanca, Morocco"
        else:
            location = "Morocco"
    elif "france" in text.lower() or "paris" in text.lower():
        location = "Paris, France"

    # 2. Education & Degree Extraction
    education_entries = []
    degree_level = "bachelor"  # default
    if re.search(r'\b(phd|doctorate|doctorat)\b', text, re.I):
        degree_level = "phd"
        education_entries.append("PhD / Doctorate")
    elif re.search(r'\b(engineer|ingénieur|master|miage|bac\+5|msc|magister)\b', text, re.I):
        degree_level = "master"
        if "miage" in text.lower():
            education_entries.append("Engineer in Computer Methods Applied to Business Management (MIAGE) — EMSI")
        else:
            education_entries.append("Master's Degree / State Engineer (Bac+5)")
    elif re.search(r'\b(bachelor|licence|bac\+3|bsc)\b', text, re.I):
        degree_level = "bachelor"
        education_entries.append("Bachelor's Degree / Licence (Bac+3)")

    if "emsi" in text.lower() or "école marocaine des sciences de l'ingénieur" in text.lower():
        education_entries.append("École Marocaine des Sciences de l'Ingénieur (EMSI)")

    # 3. Experience & Years Extraction
    years_found = [int(y) for y in re.findall(r'\b(20[12]\d)\b', text)]
    if years_found:
        min_year = min(years_found)
        max_year = max(years_found)
        # Career span
        exp_years = max(1, min(15, max_year - min_year + 1))
    else:
        exp_years = 4

    exp_roles: list[dict[str, str]] = []
    role_patterns = [
        (r'Freelance Fullstack Developer[^\d]+(\d{4}|\w+\s+\d{4})[^\w]+(Present|\d{4})', "Freelance Fullstack Developer (HR CRM / Spring Boot & React)"),
        (r'IT Instructor[^\d]+(\d{4}|\w+\s+\d{4})', "IT Instructor (Computer Science, AI, Cloud & Web Dev)"),
        (r'Support Engineer[^\d]+(\d{2}/\d{2}/\d{4}|\d{4})', "Support Engineer (Microsoft Intune & Azure AD)"),
        (r'Junior Fullstack Engineer[^\d]+(\d{4})', "Junior Fullstack Engineer (Spring Boot / ReactJS)"),
        (r'Internship[^\d]+(\d{4})', "Software Engineering Intern (DXC Technologies)"),
    ]
    for pat, label in role_patterns:
        if re.search(pat, text, re.I):
            exp_roles.append({"role": label})

    # 4. Categorized Technical Skills Identification
    text_lower = text.lower()
    categorized_skills: dict[str, list[str]] = {}
    matched_skills_set: set[str] = set()

    for category, skills in TECH_TAXONOMY.items():
        found = []
        for s in skills:
            # Word boundary matching
            pattern = r'\b' + re.escape(s) + r'\b'
            if re.search(pattern, text_lower):
                found.append(s.title() if len(s) > 3 else s.upper())
                matched_skills_set.add(s)
        if found:
            categorized_skills[category] = found

    # 5. TF-IDF Term Weight Modeling
    words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9+#.-]{1,20}\b', text_lower)
    word_counts = Counter(words)
    total_words = len(words) or 1

    # Skill TF weights
    skill_tf: dict[str, float] = {}
    for s in matched_skills_set:
        count = word_counts.get(s, 0)
        # Term Frequency with sublinear scaling
        tf = 1.0 + math.log(count) if count > 0 else 0.5
        skill_tf[s] = round(tf, 3)

    # Top skills sorted by TF weight
    sorted_skills = sorted(skill_tf.items(), key=lambda x: x[1], reverse=True)
    top_skills = [k.title() if len(k) > 3 else k.upper() for k, _ in sorted_skills[:20]]

    # 6. Target Roles Recommendation
    target_roles = [
        "Backend Engineer",
        "Java Developer",
        "Java Backend Engineer",
        "Spring Boot Developer",
        "Fullstack Developer",
        "Software Engineer",
        "DevOps Engineer",
        "Machine Learning Engineer"
    ]

    return {
        "name": name,
        "email": email,
        "phone": phone,
        "location": location,
        "linkedin": linkedin,
        "github": github,
        "education": education_entries,
        "degree_level": degree_level,
        "experience": exp_roles,
        "experience_years": exp_years,
        "categorized_skills": categorized_skills,
        "top_skills": top_skills,
        "skill_weights": skill_tf,
        "target_roles": target_roles,
        "summary": f"Engineer in Computer Methods Applied to Business Management (MIAGE) with hands-on expertise in {', '.join(top_skills[:6])}. Specializes in scalable backend architectures and modern full-stack engineering."
    }


def compute_job_cv_match(job_data: dict[str, Any], candidate_features: dict[str, Any]) -> dict[str, Any]:
    """Compute vector space Cosine Similarity and multi-dimensional fit between a job and candidate CV.
    
    Returns:
    - match_score: int (0 to 100)
    - cosine_similarity: float (0.0 to 1.0)
    - matching_skills: list[str]
    - missing_skills: list[str]
    - seniority_fit: dict (candidate vs job requirement)
    - degree_fit: dict
    - recommendations: list[str]
    """
    job_text = " ".join([
        str(job_data.get("title") or ""),
        str(job_data.get("description") or ""),
        str(job_data.get("raw_description") or ""),
        " ".join(job_data.get("skills") or []),
    ]).lower()

    cand_skills = candidate_features.get("skill_weights", {})
    if not cand_skills and candidate_features.get("top_skills"):
        cand_skills = {s.lower(): 1.0 for s in candidate_features["top_skills"]}

    # 1. Vector Space Model across taxonomy vocabulary
    vocab = list(TECH_TAXONOMY.keys()) + FLATTENED_SKILLS
    if np is not None:
        cand_vector = np.zeros(len(vocab))
        job_vector = np.zeros(len(vocab))
    else:
        cand_vector = [0.0] * len(vocab)
        job_vector = [0.0] * len(vocab)

    matching_skills: list[str] = []
    missing_skills: list[str] = []

    for idx, term in enumerate(vocab):
        # Candidate weight
        if term in cand_skills:
            cand_vector[idx] = float(cand_skills[term])
        
        # Job weight (TF in job description)
        pattern = r'\b' + re.escape(term) + r'\b'
        matches = len(re.findall(pattern, job_text))
        if matches > 0:
            job_vector[idx] = 1.0 + math.log(matches)
            if term in cand_skills:
                matching_skills.append(term.title() if len(term) > 3 else term.upper())
            else:
                # Highlight prominent missing skills
                if term in ("kafka", "redis", "aws", "gcp", "graphql", "kubernetes", "typescript", "golang"):
                    missing_skills.append(term.title() if len(term) > 3 else term.upper())

    # Cosine Similarity Calculation: dot(u, v) / (norm(u) * norm(v))
    if np is not None:
        norm_c = float(np.linalg.norm(cand_vector))
        norm_j = float(np.linalg.norm(job_vector))
        dot_product = float(np.dot(cand_vector, job_vector))
    else:
        norm_c = math.sqrt(sum(x * x for x in cand_vector))
        norm_j = math.sqrt(sum(x * x for x in job_vector))
        dot_product = sum(x * y for x, y in zip(cand_vector, job_vector))

    if norm_c > 0 and norm_j > 0:
        cosine_sim = float(dot_product / (norm_c * norm_j))
    else:
        cosine_sim = 0.0

    # 2. Seniority & Experience Level Fit
    cand_years = candidate_features.get("experience_years", 4)
    req_years = job_data.get("experience_years_required")
    job_exp_level = (job_data.get("experience_level") or "entry").lower()

    seniority_fit = "optimal"
    seniority_note = "Experience aligns with role scope."
    if req_years is not None:
        if req_years <= cand_years:
            seniority_fit = "qualified"
            seniority_note = f"Candidate has {cand_years}y vs {req_years}y required ✓"
        elif req_years > cand_years + 2:
            seniority_fit = "gap"
            seniority_note = f"Role requires {req_years}y (candidate has {cand_years}y)"
    elif job_exp_level in ("senior", "lead"):
        seniority_fit = "moderate"
        seniority_note = "Senior/Lead role — emphasize leadership and production achievements."

    # 3. Degree Level Fit
    cand_degree = candidate_features.get("degree_level", "master")
    req_degree = (job_data.get("degree_required") or "none").lower()
    degree_fit = "qualified"
    if req_degree == "phd" and cand_degree != "phd":
        degree_fit = "gap"
    else:
        degree_fit = "qualified"

    # 4. Composite Fit Score (0-100)
    # Cosine similarity accounts for 65%, seniority 20%, degree 15%
    raw_score = (cosine_sim * 65)
    if seniority_fit in ("optimal", "qualified"):
        raw_score += 20
    elif seniority_fit == "moderate":
        raw_score += 12
    else:
        raw_score += 5

    if degree_fit == "qualified":
        raw_score += 15
    else:
        raw_score += 5

    # Bonus if core stack matches
    if any(k in [s.lower() for s in matching_skills] for k in ("spring boot", "java", "react", "microservices")):
        raw_score += 10

    final_score = int(min(100, max(15, round(raw_score))))

    # Deduplicate and sort skills
    matching_skills = list(dict.fromkeys(matching_skills))
    missing_skills = list(dict.fromkeys(missing_skills))[:8]

    return {
        "match_score": final_score,
        "cosine_similarity": round(cosine_sim, 3),
        "similarity_percent": int(round(cosine_sim * 100)),
        "matching_skills": matching_skills,
        "missing_skills": missing_skills,
        "matching_skills_count": len(matching_skills),
        "seniority_fit": {
            "status": seniority_fit,
            "note": seniority_note,
            "candidate_years": cand_years,
            "required_years": req_years,
        },
        "degree_fit": {
            "status": degree_fit,
            "candidate_degree": cand_degree,
            "required_degree": req_degree,
        },
        "summary": f"ML Vector match of {final_score}% based on {len(matching_skills)} overlapping technical skills."
    }
