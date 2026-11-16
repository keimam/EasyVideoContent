"""Language resolution, prompt directives, and output validation for Longform scripts (8B.1 B2).

Ensures script generation uses strictly the project's narration language (never UI language),
provides unambiguous prompt instructions, and validates generated outlines/chapters with
lightweight stopword heuristics.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

_TAG_STRIP_RE = re.compile(
    r"\[(visual|motion|source|file|voice|search|verify)\s*[:=]\s*[^\]]*\]",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ']+")

_EN_STOPWORDS = frozenset({
    "the", "and", "of", "to", "in", "is", "that", "for", "it", "with",
    "was", "as", "on", "by", "from", "are", "this", "which", "an", "were",
    "had", "but", "not", "their", "his", "her", "they", "when", "what",
    "how", "more", "than", "into", "about", "would", "could", "been",
    "has", "have", "did", "will", "its", "at", "or", "all", "one", "only",
    "chapter", "behind", "why", "who",
})

_ID_STOPWORDS = frozenset({
    "yang", "dan", "di", "ke", "dari", "untuk", "dengan", "pada", "ini", "itu",
    "adalah", "menjadi", "dalam", "oleh", "tidak", "atau", "sebagai", "karena",
    "telah", "akan", "dapat", "mereka", "kita", "tersebut", "juga", "lebih",
    "sangat", "bukan", "hanya", "saat", "antara", "namun", "tetapi", "ketika",
    "sudah", "belum", "mengapa", "bagaimana", "tentang", "melalui", "hingga",
    "sampai", "kisah", "sejarah", "latar", "belakang", "kesimpulan", "bab",
    "sang", "mengubah", "segalanya", "senilai", "lahirnya", "fakta", "menarik",
})


def normalize_language_code(raw: Optional[str]) -> str:
    """Map language identifier (e.g. 'en-US', 'English', 'id-ID') to 'en' or 'id'."""
    s = (raw or "").strip().lower()
    if s.startswith("en") or "english" in s or "inggris" in s:
        return "en"
    if s.startswith("id") or "indonesian" in s or "indonesia" in s:
        return "id"
    return s or "en"


def canonical_language_tag(raw: Optional[str]) -> str:
    """Return canonical tag 'en-US' or 'id-ID' (or normalized input for other locales)."""
    norm = normalize_language_code(raw)
    if norm == "en":
        return "en-US"
    if norm == "id":
        return "id-ID"
    return (raw or "en-US").strip()


def format_language_directive(raw: Optional[str]) -> Tuple[str, str]:
    """Return ``(human_language_name, canonical_locale_tag)`` for LLM prompts."""
    norm = normalize_language_code(raw)
    if norm == "en":
        return ("English (en-US)", "en-US")
    if norm == "id":
        return ("Indonesian / Bahasa Indonesia (id-ID)", "id-ID")
    clean = (raw or "en-US").strip()
    return (clean, clean)


def resolve_project_language(manifest: Any, preset: Any = None) -> str:
    """Resolve the authoritative project narration language (never UI language)."""
    m_lang = getattr(manifest, "language", None)
    if m_lang and str(m_lang).strip():
        return canonical_language_tag(str(m_lang))
    snap = getattr(manifest, "preset_snapshot", None)
    if isinstance(snap, dict) and snap.get("language"):
        return canonical_language_tag(str(snap["language"]))
    p_lang = getattr(preset, "language", None) if preset is not None else None
    if p_lang and str(p_lang).strip():
        return canonical_language_tag(str(p_lang))
    return "en-US"


def clean_spoken_text_for_lang(text: str) -> str:
    """Strip bracket directives and markdown symbols before counting words or detecting language."""
    no_tags = _TAG_STRIP_RE.sub(" ", text or "")
    lines = []
    for ln in no_tags.splitlines():
        s = ln.strip()
        if s.startswith("<!--"):
            continue
        if s.startswith("#"):
            s = s.lstrip("#").strip()
        if s:
            lines.append(s)
    return " ".join(lines)


def detect_text_language(text: str, *, include_tags: bool = False) -> Dict[str, Any]:
    """Detect whether text is primarily English ('en'), Indonesian ('id'), or 'unknown'."""
    cleaned = (text or "") if include_tags else clean_spoken_text_for_lang(text)
    tokens = [t.lower().strip("'") for t in _WORD_RE.findall(cleaned)]
    en_hits = sum(1 for tok in tokens if tok in _EN_STOPWORDS)
    id_hits = sum(1 for tok in tokens if tok in _ID_STOPWORDS)
    total_hits = en_hits + id_hits

    if total_hits == 0:
        return {"detected": "unknown", "en_hits": 0, "id_hits": 0, "tokens": len(tokens)}
    if en_hits > id_hits and (en_hits >= 2 or id_hits == 0):
        return {"detected": "en", "en_hits": en_hits, "id_hits": id_hits, "tokens": len(tokens)}
    if id_hits > en_hits and (id_hits >= 2 or en_hits == 0):
        return {"detected": "id", "en_hits": en_hits, "id_hits": id_hits, "tokens": len(tokens)}
    return {"detected": "unknown", "en_hits": en_hits, "id_hits": id_hits, "tokens": len(tokens)}


def check_language_compliance(
    body_text: str,
    target_lang: str,
    *,
    title_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Check whether text (and optional heading title) strictly matches ``target_lang``."""
    target_norm = normalize_language_code(target_lang)
    body_info = detect_text_language(body_text)
    body_det = body_info["detected"]

    title_det = "unknown"
    if title_text and title_text.strip():
        title_det = detect_text_language(title_text, include_tags=True)["detected"]

    if target_norm not in ("en", "id"):
        return {
            "ok": True,
            "detected": body_det,
            "title_detected": title_det,
            "target": target_norm,
            "reason": "",
        }

    if body_det != "unknown" and body_det != target_norm:
        return {
            "ok": False,
            "detected": body_det,
            "title_detected": title_det,
            "target": target_norm,
            "reason": f"Body language detected as '{body_det}' instead of '{target_norm}' (en={body_info['en_hits']}, id={body_info['id_hits']}).",
        }

    if title_det != "unknown" and title_det != target_norm:
        return {
            "ok": False,
            "detected": title_det,
            "title_detected": title_det,
            "target": target_norm,
            "reason": f"Title language detected as '{title_det}' instead of '{target_norm}'.",
        }

    return {
        "ok": True,
        "detected": body_det,
        "title_detected": title_det,
        "target": target_norm,
        "reason": "",
    }
