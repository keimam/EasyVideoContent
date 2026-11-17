"""Script Importer and Markdown Parser for Longform Video Generation.

Converts structured Markdown scripts into Chapters, TtsGroups, and Segments (K3).
Features an abbreviation-safe sentence tokenizer, visual/motion/search/verify tag parsing,
and duration estimation with non-blocking length warnings (K6).
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from app.services.longform.config import load_longform_config
from app.services.longform.hashing import (
    compute_audio_group_hash,
    compute_chapter_hash,
    compute_visual_segment_hash,
    normalize_text,
)
from app.services.longform.schema import (
    AudioSpec,
    Chapter,
    ItemStatus,
    MotionType,
    Preset,
    Segment,
    TtsGroup,
    VisualSourceType,
    VisualSpec,
)

# Known abbreviations that do not terminate a sentence
KNOWN_ABBREVIATIONS = [
    "mr", "mrs", "ms", "dr", "prof", "gen", "col", "lt", "maj", "capt", "sgt",
    "gov", "sen", "rep", "rev", "st", "vs", "etc", "e.g", "i.e", "u.s", "u.k",
    "a.i", "dept", "univ", "no", "vol", "approx", "est", "b.c", "a.d",
    "dll", "dsb", "dst", "bpk", "ibu", "sdr", "sdri",
]

_CHAR_DOT_PLACEHOLDER = chr(0xE000)
_CHAR_ELLIPSIS_PLACEHOLDER = chr(0xE001)

# Tag extraction regexes: e.g. [visual: a dark castle] [motion: zoom_in] [source: stock] [search: keywords] [verify: claim]
TAG_PATTERN = re.compile(
    r"\[(visual|motion|source|file|voice|search|verify)\s*[:=]\s*([^\]]+)\]",
    re.IGNORECASE,
)


def normalize_tag_positions(text: str) -> str:
    """Normalize tags written after sentence-ending punctuation so they stay with their sentence."""
    pattern = r"([.!?]+)(\s*(?:\[(?:visual|motion|source|file|voice|search|verify)[^\]]*\]\s*)+)"

    def _swap(m: re.Match) -> str:
        punct = m.group(1)
        tags = m.group(2).strip()
        return f" {tags}{punct} "

    return re.sub(pattern, _swap, text)


def split_sentences(text: str) -> List[str]:
    """Tokenize a paragraph into sentences without splitting on abbreviations, decimals, or trailing tags."""
    if not text or not text.strip():
        return []

    # First normalize tag positions so tags placed after punctuation stay with the sentence
    t = normalize_tag_positions(text.strip())

    # 1. Protect ellipsis (...)
    t = re.sub(r"\.{3,}", _CHAR_ELLIPSIS_PLACEHOLDER, t)

    # 2. Protect decimals (e.g. 3.14, $1.5M, 1,000.50)
    t = re.sub(r"(\d+)\.(\d+)", rf"\1{_CHAR_DOT_PLACEHOLDER}\2", t)

    # 3. Protect single initials (e.g. John F. Kennedy, J. K. Rowling)
    t = re.sub(r"\b([A-Za-z])\.", rf"\1{_CHAR_DOT_PLACEHOLDER}", t)

    # 4. Protect known multi-letter abbreviations
    for abbr in KNOWN_ABBREVIATIONS:
        pattern = r"\b" + re.escape(abbr) + r"\."
        t = re.sub(
            pattern,
            lambda m: m.group(0)[:-1] + _CHAR_DOT_PLACEHOLDER,
            t,
            flags=re.IGNORECASE,
        )

    # 5. Split on sentence boundaries: punctuation (. ! ?) followed by whitespace or end
    raw_sentences = re.split(r"(?<=[.!?])\s+", t)

    results: List[str] = []
    for s in raw_sentences:
        restored = (
            s.replace(_CHAR_DOT_PLACEHOLDER, ".")
            .replace(_CHAR_ELLIPSIS_PLACEHOLDER, "...")
            .strip()
        )
        if restored:
            results.append(restored)

    return results


def extract_tags(sentence: str) -> Tuple[str, dict[str, str]]:
    """Extract and remove [tag: value] directives from a sentence.

    Returns (clean_narration_text, extracted_tags_dict).
    """
    tags: dict[str, str] = {}

    def _repl(match: re.Match) -> str:
        tag_key = match.group(1).lower().strip()
        tag_val = match.group(2).strip()
        tags[tag_key] = tag_val
        return ""

    clean_text = TAG_PATTERN.sub(_repl, sentence)
    # Collapse leftover duplicate spaces and trim spaces before punctuation
    clean_text = re.sub(r"\s+", " ", clean_text)
    clean_text = re.sub(r"\s+([.!?,;:])", r"\1", clean_text).strip()
    return clean_text, tags


def estimate_script_duration(
    chapters: List[Chapter],
    words_per_minute: int = 140,
) -> dict[str, Any]:
    """Calculate word count, estimated duration, and check duration limits (K6)."""
    config = load_longform_config()
    total_words = 0

    for chapter in chapters:
        for group in chapter.tts_groups:
            for seg in group.segments:
                words = seg.narration_text.split()
                total_words += len(words)

    wpm = max(1, words_per_minute)
    estimated_seconds = (total_words / wpm) * 60.0

    warnings: List[str] = []
    if estimated_seconds < config.min_duration_seconds:
        warnings.append(
            f"Estimated duration ({estimated_seconds:.1f}s / {estimated_seconds/60:.1f} min) "
            f"is below recommended minimum ({config.min_duration_seconds}s / {config.min_duration_seconds/60:.1f} min)."
        )
    elif estimated_seconds > config.max_duration_seconds:
        warnings.append(
            f"Estimated duration ({estimated_seconds:.1f}s / {estimated_seconds/60:.1f} min) "
            f"exceeds target maximum ({config.max_duration_seconds}s / {config.max_duration_seconds/60:.1f} min)."
        )

    return {
        "total_words": total_words,
        "words_per_minute": wpm,
        "estimated_duration_seconds": round(estimated_seconds, 2),
        "min_duration_seconds": config.min_duration_seconds,
        "max_duration_seconds": config.max_duration_seconds,
        "warnings": warnings,
    }


CHAPTER_PREFIX_RE = re.compile(r"^(?:chapter|bab|ch)[.\s]*\d+[\s:.-]*", re.IGNORECASE)

# Matches explicit or missing-space chapter headings at the start of a line or glued before narration (F11):
# Examples: "## Bab 1: Title", "#Bab 1: Title", "Chapter 2: Title", "Bab 3 - Title", "[ch:1] Title"
_CHAPTER_LINE_PREFIX_RE = re.compile(
    r"^(?:"
    r"#{2,6}\s*(?:(?:chapter|bab|ch)\s*\d*\s*[:.\-]?\s*)?"
    r"|#\s*(?:chapter|bab|ch)\b\.?\s*\d*\s*[:.\-]?\s*"
    r"|(?:chapter|bab)\s+\d+\s*[:.\-]+\s*"
    r"|\[\s*ch(?:apter)?\s*[:=]\s*\d+\s*\]\s*"
    r")",
    re.IGNORECASE,
)

# Common title connector words that may appear lowercase inside Title-Case headings (ID & EN)
_TITLE_LOWER_CONNECTORS = {
    "dan", "di", "ke", "dari", "yang", "untuk", "dalam", "pada", "atau", "dengan",
    "tentang", "oleh", "sebuah", "sang", "para", "antara",
    "and", "or", "of", "the", "a", "an", "in", "on", "at", "to", "for", "from",
    "by", "with", "into", "that", "as", "vs",
}


def strip_glued_chapter_heading(text: str) -> Tuple[Optional[str], str]:
    """Separate a leading chapter heading marker/title from body narration text (F11).

    Returns ``(extracted_chapter_title_or_None, remaining_narration_text)``.
    If no heading prefix matches, returns ``(None, text)``.
    """
    raw = (text or "").strip()
    if not raw:
        return None, ""

    m = _CHAPTER_LINE_PREFIX_RE.match(raw)
    if not m:
        return None, raw

    after_prefix = raw[m.end():].strip()
    if not after_prefix:
        return clean_chapter_title(raw[:m.end()]), ""

    # Case 1: Newline separates heading from body
    if "\n" in after_prefix:
        first_line, rest = after_prefix.split("\n", 1)
        return clean_chapter_title(first_line.strip()), rest.strip()

    # Case 2: Single line with explicit punctuation delimiter like ". " / "! " / "? " / " — "
    # Only treat sentence break as end-of-title if short prefix before delimiter
    # Wait: if text is glued WITHOUT punctuation (e.g. "#Bab 1: Panggilan Lapar dan Legenda Kaki Lima Malam selalu..."),
    # find the first capitalized word followed immediately by a lowercase non-connector word!
    words = after_prefix.split()
    split_idx: Optional[int] = None
    for i in range(len(words)):
        raw_cur = words[i]
        w_cur = raw_cur.strip(".,;:!?\"'()")
        m_glued = re.match(r"^([A-Z][a-z]{1,})([A-Z][a-z]{2,}.*)$", raw_cur)
        if m_glued and i >= 1:
            heading_part = " ".join(words[:i] + [m_glued.group(1)]).strip(" :.-")
            body_part = " ".join([m_glued.group(2)] + words[i + 1 :]).strip()
            return (clean_chapter_title(heading_part) if heading_part else None), body_part

        if i >= len(words) - 1:
            continue
        w_nxt = words[i + 1].strip(".,;:!?\"'()")
        if not w_cur or not w_nxt:
            continue
        cur_is_cap = w_cur[0].isupper()
        nxt_is_lower = w_nxt[0].islower()
        nxt_clean_low = w_nxt.lower()
        # A capitalized word starting the spoken sentence has a lowercase non-connector immediately after it
        if cur_is_cap and nxt_is_lower and nxt_clean_low not in _TITLE_LOWER_CONNECTORS:
            # Verify there wasn't ALREADY lowercase non-connector title prose before (only first transition splits!)
            split_idx = i
            break
        # Handle lowercase connector starting sentence (e.g. "di tengah...") after >=2 title words
        if i >= 2 and w_cur[0].islower() and w_cur.lower() not in _TITLE_LOWER_CONNECTORS:
            split_idx = max(0, i - 1)
            break

    if split_idx is not None:
        heading_part = " ".join(words[:split_idx]).strip(" :.-")
        body_part = " ".join(words[split_idx:]).strip()
        return (clean_chapter_title(heading_part) if heading_part else None), body_part

    # Entire string is a standalone heading line (e.g. "Bab 1: The Midnight Queue")
    return clean_chapter_title(after_prefix), ""


def detect_leaked_chapter_headings(manifest: Any) -> List[Dict[str, Any]]:
    """Scan an existing ProjectManifest for chapter headings inside narration/TTS groups (F11)."""
    hits: List[Dict[str, Any]] = []
    for ch in getattr(manifest, "chapters", []) or []:
        for grp in getattr(ch, "tts_groups", []) or []:
            para = (grp.paragraph_text or "").strip()
            h_p, _ = strip_glued_chapter_heading(para)
            if h_p is not None or _CHAPTER_LINE_PREFIX_RE.match(para):
                hits.append({
                    "chapter_index": ch.chapter_index,
                    "group_index": grp.group_index,
                    "sample": para[:90],
                })
                continue
            for seg in getattr(grp, "segments", []) or []:
                stxt = (seg.narration_text or "").strip()
                h_s, _ = strip_glued_chapter_heading(stxt)
                if h_s is not None or _CHAPTER_LINE_PREFIX_RE.match(stxt):
                    hits.append({
                        "chapter_index": ch.chapter_index,
                        "group_index": grp.group_index,
                        "segment_index": seg.segment_index,
                        "sample": stxt[:90],
                    })
                    break
    return hits


def fix_project_headings(project_dir: Any) -> Dict[str, Any]:
    """Remove leaked chapter headings from narration/TTS groups/shots and mark changed audio stale (F11)."""
    from pathlib import Path
    from app.services.longform.store import load_manifest, save_manifest
    from app.services.longform.visual.query_extractor import extract_short_search_query

    p_dir = Path(project_dir).resolve()
    manifest = load_manifest(p_dir)

    fixed_groups = 0
    removed_empty_groups = 0
    fixed_segments = 0
    fixed_shots = 0

    for ch in manifest.chapters:
        new_groups: List[TtsGroup] = []
        removed_seg_indices: set[int] = set()
        recovered_title: Optional[str] = None

        for grp in ch.tts_groups:
            grp_modified = False
            new_segs: List[Segment] = []
            for seg in grp.segments:
                orig_txt = (seg.narration_text or "").strip()
                h_s, rem_s = strip_glued_chapter_heading(orig_txt)
                if h_s is not None or _CHAPTER_LINE_PREFIX_RE.match(orig_txt):
                    grp_modified = True
                    fixed_segments += 1
                    if h_s and not recovered_title:
                        recovered_title = h_s
                    if rem_s:
                        seg.narration_text = rem_s
                        new_segs.append(seg)
                    else:
                        removed_seg_indices.add(seg.segment_index)
                else:
                    new_segs.append(seg)

            # Also check paragraph_text directly in case paragraph was prefixed while segments differed
            if not grp_modified:
                h_p, rem_p = strip_glued_chapter_heading((grp.paragraph_text or "").strip())
                if h_p is not None:
                    grp_modified = True
                    if h_p and not recovered_title:
                        recovered_title = h_p

            if not new_segs:
                removed_empty_groups += 1
                fixed_groups += 1
                continue

            if grp_modified:
                grp.segments = new_segs
                grp.paragraph_text = " ".join(s.narration_text.strip() for s in new_segs if s.narration_text.strip())
                had_audio = bool(grp.audio and grp.audio.file_path)
                if grp.audio:
                    grp.audio.audio_hash = compute_audio_group_hash(
                        paragraph_text=grp.paragraph_text,
                        tts_provider=grp.audio.tts_provider,
                        tts_model=grp.audio.tts_model,
                        voice_name=grp.audio.voice_name,
                        voice_rate=grp.audio.voice_rate,
                        text_norm_version=grp.audio.text_norm_version,
                    )
                if had_audio:
                    grp.status = ItemStatus.stale
                fixed_groups += 1
            new_groups.append(grp)

        for idx, g in enumerate(new_groups, start=1):
            g.group_index = idx
        ch.tts_groups = new_groups

        if recovered_title and (not ch.chapter_title or re.match(r"^chapter\s+\d+$", ch.chapter_title.strip(), re.IGNORECASE)):
            ch.chapter_title = recovered_title

        # Clean up visual shots referencing modified/removed narration segments
        surviving_seg_map = {s.segment_index: s.narration_text for g in ch.tts_groups for s in g.segments}
        new_shots = []
        for sh in ch.shots:
            if sh.segment_indices and all(si in removed_seg_indices for si in sh.segment_indices):
                # Shot only belonged to a standalone removed heading segment -> drop unlocked heading shot cleanly
                if not getattr(sh, "locked", False):
                    fixed_shots += 1
                    continue
            if not getattr(sh, "locked", False) and str(getattr(sh, "search_query_source", "") or "extraction") != "ai":
                # Refresh extracted keyword if it was polluted with heading words
                clean_src_txt = " ".join(surviving_seg_map.get(si, "") for si in (sh.segment_indices or [])).strip()
                if clean_src_txt and sh.search_query:
                    h_q, _ = strip_glued_chapter_heading(f"# {sh.search_query}")
                    if "bab" in sh.search_query.lower() or "chapter" in sh.search_query.lower():
                        sh.search_query = extract_short_search_query(clean_src_txt)
                        fixed_shots += 1
            new_shots.append(sh)
        ch.shots = new_shots

    if fixed_groups > 0 or removed_empty_groups > 0 or fixed_shots > 0:
        save_manifest(manifest, p_dir)

    return {
        "project_id": manifest.project_id,
        "fixed_groups": fixed_groups,
        "removed_empty_groups": removed_empty_groups,
        "fixed_segments": fixed_segments,
        "fixed_shots": fixed_shots,
    }


def clean_chapter_title(title: str, fallback_index: Optional[int] = None) -> str:
    """Strip repeated 'Chapter 1:', 'Bab 2 -', 'Ch 3.' prefixes from a chapter heading (E1).

    Repeatedly applies stripping until no prefix matches.
    If the stripped title is non-empty, returns it.
    If empty (e.g. heading was literally '## Chapter 1' or 'Ch1'), returns original.
    """
    if not title:
        return f"Chapter {fallback_index}" if fallback_index else ""
    cleaned = title.strip()
    while True:
        m = CHAPTER_PREFIX_RE.match(cleaned)
        if not m:
            break
        cleaned = cleaned[m.end():].strip()
    if not cleaned:
        return title.strip()
    return cleaned


def _count_spoken_words(paragraph: str) -> int:
    """Count spoken narration words in a paragraph, excluding bracket tags."""
    spoken = TAG_PATTERN.sub(" ", paragraph or "").strip()
    return len(spoken.split())


def normalize_short_paragraphs(
    paragraphs: List[str],
    min_words: int = 33,
    max_words: int = 120,
    soft_target: int = 75,
) -> List[str]:
    """Merge very short adjacent paragraphs into coherent ~40-120 word narration groups (8B.1 B2).

    Targets ~80 words per merged group before splitting so trailing paragraphs stay within
    40-120 words whenever total words permit. Never alters words, punctuation, or tags.
    """
    non_empty = [p.strip() for p in paragraphs if _count_spoken_words(p) > 0]
    if len(non_empty) <= 1:
        return non_empty

    merged: List[str] = []
    buf_parts: List[str] = []
    buf_words = 0

    for para in non_empty:
        pw = _count_spoken_words(para)
        if not buf_parts:
            buf_parts.append(para)
            buf_words = pw
        elif buf_words < min_words and (buf_words + pw <= max_words or buf_words < 20):
            buf_parts.append(para)
            buf_words += pw
        elif pw < min_words and (buf_words + pw <= soft_target):
            buf_parts.append(para)
            buf_words += pw
        else:
            merged.append(" ".join(buf_parts))
            buf_parts = [para]
            buf_words = pw

    if buf_parts:
        if merged and buf_words < min_words:
            prev_words = _count_spoken_words(merged[-1])
            if prev_words + buf_words <= max_words:
                merged[-1] = f"{merged[-1]} {' '.join(buf_parts)}"
            else:
                merged.append(" ".join(buf_parts))
        else:
            merged.append(" ".join(buf_parts))

    return merged


def parse_markdown_script(
    script_markdown: str,
    preset: Preset,
    normalize_paragraphs: bool = True,
) -> Tuple[List[Chapter], Optional[str]]:
    """Parse Markdown script into structured Chapter, TtsGroup, and Segment models.

    Returns (chapters_list, detected_title_or_none).
    """
    lines = script_markdown.splitlines()

    title: Optional[str] = None
    chapters: List[Chapter] = []

    current_chapter_title: Optional[str] = None
    current_paragraphs: List[str] = []

    def _flush_chapter():
        if not current_chapter_title and not current_paragraphs:
            return

        ch_title = current_chapter_title or f"Chapter {len(chapters) + 1}"
        chapter_index = len(chapters) + 1
        tts_groups: List[TtsGroup] = []

        paragraphs_to_use = (
            normalize_short_paragraphs(current_paragraphs, min_words=33, max_words=120, soft_target=75)
            if normalize_paragraphs
            else list(current_paragraphs)
        )

        global_seg_counter = 1
        for p_idx, para in enumerate(paragraphs_to_use, start=1):
            sentences = split_sentences(para)
            if not sentences:
                continue

            segments: List[Segment] = []
            clean_para_sentences: List[str] = []

            for s in sentences:
                clean_text, tags = extract_tags(s)
                if not clean_text:
                    continue

                clean_para_sentences.append(clean_text)

                # Determine motion
                motion_str = tags.get("motion", "").lower()
                if motion_str in MotionType.__members__:
                    motion = MotionType(motion_str)
                else:
                    # Default Ken Burns alternating
                    motion = (
                        MotionType.zoom_in
                        if (global_seg_counter % 2 != 0)
                        else MotionType.zoom_out
                    )

                # Determine visual source (supports "stock", "local", "ai_image" aliases)
                source_str = tags.get("source", "").lower()
                if source_str in ("stock", "stock_video"):
                    source_type = VisualSourceType.stock_video
                elif source_str in ("local", "local_file"):
                    source_type = VisualSourceType.local
                elif source_str in ("ai_image", "ai"):
                    source_type = VisualSourceType.ai_image
                elif source_str in VisualSourceType.__members__:
                    source_type = VisualSourceType(source_str)
                else:
                    source_type = VisualSourceType.ai_image

                prompt = tags.get("visual", "")
                if not prompt and source_type == VisualSourceType.ai_image:
                    prompt = clean_text

                source_file = tags.get("file", None)
                search_keywords = tags.get("search", None)
                verify_claim = tags.get("verify", None)

                # Estimated rough segment duration (based on words)
                seg_words = len(clean_text.split())
                est_seg_duration = max(2.0, (seg_words / max(1, preset.words_per_minute)) * 60.0)

                visual_hash = compute_visual_segment_hash(
                    source_content_hash="",
                    motion=motion.value,
                    duration=est_seg_duration,
                )

                visual_spec = VisualSpec(
                    type=source_type,
                    prompt=prompt,
                    motion=motion,
                    source_file=source_file,
                    visual_hash=visual_hash,
                    search_keywords=search_keywords,
                )

                seg = Segment(
                    segment_index=global_seg_counter,
                    narration_text=clean_text,
                    duration_seconds=round(est_seg_duration, 2),
                    visual=visual_spec,
                    verify=verify_claim,
                    status=ItemStatus.pending,
                )
                segments.append(seg)
                global_seg_counter += 1

            if not segments:
                continue

            full_para_text = " ".join(clean_para_sentences)
            audio_hash = compute_audio_group_hash(
                paragraph_text=full_para_text,
                tts_provider="edge",
                tts_model="",
                voice_name=preset.default_voice,
                voice_rate=1.0,
                text_norm_version="v1",
            )

            group = TtsGroup(
                group_index=p_idx,
                paragraph_text=full_para_text,
                audio=AudioSpec(
                    tts_provider="edge",
                    voice_name=preset.default_voice,
                    voice_rate=1.0,
                    text_norm_version="v1",
                    audio_hash=audio_hash,
                ),
                segments=segments,
                status=ItemStatus.pending,
            )
            tts_groups.append(group)

        if tts_groups:
            new_chapter = Chapter(
                chapter_index=chapter_index,
                chapter_title=ch_title,
                tts_groups=tts_groups,
                status=ItemStatus.pending,
            )
            chapters.append(new_chapter)

    # State machine over Markdown lines
    raw_para_buffer: List[str] = []

    def _flush_para():
        if raw_para_buffer:
            combined = " ".join(l.strip() for l in raw_para_buffer if l.strip())
            if combined:
                current_paragraphs.append(combined)
            raw_para_buffer.clear()

    for line in lines:
        stripped = line.strip()

        # Blank line = paragraph break
        if not stripped:
            _flush_para()
            continue

        # Level 1 Title (plain "# Title" or leading "#Title" before any chapter, NOT "#Bab 1: ...")
        is_h1_title = (
            stripped.startswith("#")
            and not stripped.startswith("##")
            and not re.match(r"^#\s*(?:bab|chapter|ch)\b", stripped, re.IGNORECASE)
            and (
                stripped.startswith("# ")
                or (not title and not chapters and not current_chapter_title and not current_paragraphs)
            )
        )
        if is_h1_title:
            _flush_para()
            if not title:
                title = stripped[1:].strip()
            continue

        # Chapter Heading detection (including missing-space "#Bab 1:", "Chapter 1:", "Bab 2 -", "[ch:1]")
        h_detected, h_rem_body = strip_glued_chapter_heading(stripped)
        if h_detected is not None or stripped.startswith("##"):
            _flush_para()
            _flush_chapter()
            if h_detected is not None:
                current_chapter_title = clean_chapter_title(h_detected, fallback_index=len(chapters) + 1)
                current_paragraphs = [h_rem_body] if h_rem_body else []
            else:
                raw_title = re.sub(r"^#+\s*", "", stripped).strip()
                current_chapter_title = clean_chapter_title(raw_title, fallback_index=len(chapters) + 1)
                current_paragraphs = []
            continue

        # Ignore comments or metadata banners before the first chapter
        if stripped.startswith("<!--") or (current_chapter_title is None and stripped.startswith(">")):
            continue

        raw_para_buffer.append(stripped)

    # Flush final trailing items
    _flush_para()
    _flush_chapter()

    return chapters, title
