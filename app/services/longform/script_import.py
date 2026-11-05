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


def parse_markdown_script(
    script_markdown: str,
    preset: Preset,
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

        global_seg_counter = 1
        for p_idx, para in enumerate(current_paragraphs, start=1):
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

        # Level 1 Title
        if stripped.startswith("# ") and not stripped.startswith("## "):
            _flush_para()
            if not title:
                title = stripped[2:].strip()
            continue

        # Level 2 Chapter
        if stripped.startswith("## "):
            _flush_para()
            _flush_chapter()
            current_chapter_title = stripped[3:].strip()
            current_paragraphs = []
            continue

        # Blank line = paragraph break
        if not stripped:
            _flush_para()
            continue

        raw_para_buffer.append(stripped)

    # Flush final trailing items
    _flush_para()
    _flush_chapter()

    return chapters, title
