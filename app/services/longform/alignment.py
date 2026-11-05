"""Sentence-level Segment Alignment Engine for TTS Groups (3.3).

Maps TTS word boundaries/cues to individual sentences (Segments) within a group,
supporting:
- Resilient sequential token matching tolerant of punctuation, numbers, quotes, and case
- Strict zero-gap boundary rules with midpoint pause splitting
- Fallback chain: Word Boundaries -> faster-whisper transcription -> character-count proportional division
- Diagnostic alignment method tracking ("word_cue" | "whisper" | "proportional")
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from loguru import logger

from app.services.longform.hashing import normalize_text
from app.services.longform.schema import Segment


@dataclass
class WordCue:
    """A timestamped word cue from TTS or ASR."""

    word: str
    start: float
    end: float


def normalize_token(token: str) -> str:
    """Normalize a word token by stripping punctuation, quotes, and symbols."""
    if not token:
        return ""
    norm = unicodedata.normalize("NFC", str(token)).lower().strip()
    # Strip leading and trailing punctuation/quotes
    norm = re.sub(r"^[^\w\d]+|[^\w\d]+$", "", norm)
    # Remove internal apostrophes and commas (e.g. 1,000 -> 1000, don't -> dont)
    norm = norm.replace("'", "").replace(",", "")
    return norm


def tokens_match(t1: str, t2: str) -> bool:
    """Check if two tokens match, with tolerance for contractions and minor phonetic differences."""
    n1 = normalize_token(t1)
    n2 = normalize_token(t2)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    # Substring / stem matching and fuzzy comparison only for words >= 4 chars
    if min(len(n1), len(n2)) >= 4:
        if n1 in n2 or n2 in n1:
            return True
        ratio = difflib.SequenceMatcher(None, n1, n2).ratio()
        if ratio >= 0.75:
            return True
    return False


def align_word_cues_to_segments(
    sentences: List[str],
    cues: List[WordCue],
    group_duration: float,
) -> Tuple[List[Tuple[float, float]], str]:
    """Map word cues sequentially to sentence segments using tolerant windowed matching (3.3).

    Returns: (list of (start_offset, end_offset) per sentence, alignment_method).
    """
    num_sentences = len(sentences)
    if num_sentences == 0:
        return [], "word_cue"

    if num_sentences == 1:
        # Single sentence occupies entire group duration
        return [(0.0, round(group_duration, 3))], "word_cue"

    if not cues:
        return align_proportionally(sentences, group_duration)

    # Tokenize each sentence
    sentence_tokens_list: List[List[str]] = []
    for s in sentences:
        words = [w for w in re.split(r"\s+", s.strip()) if w]
        clean_words = [normalize_token(w) for w in words if normalize_token(w)]
        sentence_tokens_list.append(clean_words or [normalize_token(s)])

    total_words = sum(len(toks) for toks in sentence_tokens_list)
    cue_pointer = 0
    num_cues = len(cues)
    total_matched = 0

    raw_boundaries: List[Tuple[float, float]] = []

    for s_idx, tokens in enumerate(sentence_tokens_list):
        if not tokens:
            raw_boundaries.append((0.0, 0.0))
            continue

        start_time: Optional[float] = None
        end_time: Optional[float] = None

        # Look for the words of this sentence starting at cue_pointer
        matched_in_sentence = 0
        local_c_idx = cue_pointer

        for t in tokens:
            # Lookahead up to 8 cues
            window_end = min(num_cues, local_c_idx + 8)
            found_idx = -1
            for search_idx in range(local_c_idx, window_end):
                if tokens_match(t, cues[search_idx].word):
                    found_idx = search_idx
                    break

            if found_idx != -1:
                matched_in_sentence += 1
                if start_time is None:
                    start_time = cues[found_idx].start
                end_time = cues[found_idx].end
                local_c_idx = found_idx + 1

        total_matched += matched_in_sentence
        cue_pointer = local_c_idx

        # If sentence was completely missed by window, estimate from neighbor
        if start_time is None or end_time is None:
            if raw_boundaries:
                prev_end = raw_boundaries[-1][1]
                start_time = prev_end
                end_time = prev_end + 0.5
            else:
                start_time = 0.0
                end_time = cues[0].end if cues else 0.5

        raw_boundaries.append((start_time, end_time))

    # Quality check: if matched < 30% of total words, alignment is untrustworthy
    match_ratio = total_matched / float(max(1, total_words))
    if match_ratio < 0.30:
        logger.warning(
            f"Word cue alignment match ratio too low ({match_ratio:.1%}). Falling back to proportional."
        )
        return align_proportionally(sentences, group_duration)

    # Apply strict zero-gap boundary rules (3.3):
    # - Sentence 1 starts at 0.0
    # - Sentence N ends at group_duration
    # - Pause between sentences divided at midpoint
    smoothed: List[Tuple[float, float]] = []
    current_start = 0.0

    for i in range(num_sentences):
        _, raw_end = raw_boundaries[i]

        if i == num_sentences - 1:
            # Last sentence extends to the end of the group audio
            end_boundary = group_duration
        else:
            next_raw_start, _ = raw_boundaries[i + 1]
            if next_raw_start >= raw_end:
                # Divide the silent pause between sentences at midpoint
                midpoint = (raw_end + next_raw_start) / 2.0
                end_boundary = midpoint
            else:
                # Slight overlap: boundary is raw_end
                end_boundary = raw_end

            # Ensure minimum positive duration
            if end_boundary <= current_start:
                end_boundary = current_start + 0.1

        smoothed.append((round(current_start, 3), round(end_boundary, 3)))
        current_start = end_boundary

    return smoothed, "word_cue"


def align_proportionally(
    sentences: List[str],
    group_duration: float,
) -> Tuple[List[Tuple[float, float]], str]:
    """Fallback alignment distributing group duration proportionally by clean character count (3.3)."""
    num_sentences = len(sentences)
    if num_sentences == 0:
        return [], "proportional"
    if num_sentences == 1:
        return [(0.0, round(group_duration, 3))], "proportional"

    # Clean characters count (excluding excessive whitespace)
    lengths = [max(1, len(re.sub(r"\s+", "", s))) for s in sentences]
    total_len = sum(lengths)

    boundaries: List[Tuple[float, float]] = []
    current_time = 0.0

    for i, length in enumerate(lengths):
        if i == num_sentences - 1:
            end_time = group_duration
        else:
            fraction = length / float(total_len)
            dur = group_duration * fraction
            end_time = current_time + dur

        boundaries.append((round(current_time, 3), round(end_time, 3)))
        current_time = end_time

    return boundaries, "proportional"


def align_with_whisper(
    wav_path: str | Path,
    sentences: List[str],
    group_duration: float,
) -> Tuple[List[Tuple[float, float]], str]:
    """Attempt speech-to-text alignment using existing faster-whisper in app.services.subtitle (3.3 Fallback a)."""
    try:
        from app.services import subtitle
        if not subtitle._ensure_model_loaded():
            logger.info("faster-whisper model not available locally; falling back to proportional alignment.")
            return align_proportionally(sentences, group_duration)

        # Transcribe audio file to retrieve segment timestamps
        segments, _ = subtitle.model.transcribe(
            str(wav_path),
            word_timestamps=True,
            vad_filter=True,
        )

        cues: List[WordCue] = []
        for segment in segments:
            if hasattr(segment, "words") and segment.words:
                for w in segment.words:
                    cues.append(WordCue(word=w.word, start=w.start, end=w.end))
            else:
                cues.append(WordCue(word=segment.text, start=segment.start, end=segment.end))

        if cues:
            boundaries, _ = align_word_cues_to_segments(sentences, cues, group_duration)
            return boundaries, "whisper"

    except Exception as exc:
        logger.warning(f"Whisper fallback alignment failed: {exc}. Using proportional fallback.")

    return align_proportionally(sentences, group_duration)


def align_group_segments(
    segments: List[Segment],
    group_duration: float,
    word_cues: Optional[List[WordCue]] = None,
    wav_path: Optional[str | Path] = None,
    allow_whisper: bool = False,
) -> Tuple[List[Segment], str]:
    """High-level segment alignment coordinator applying fallback chain and boundary invariants (3.3).

    Updates start_offset_seconds, end_offset_seconds, and duration_seconds on segments.
    Returns (updated_segments, alignment_method).
    """
    if not segments:
        return [], "word_cue"

    sentences = [s.narration_text for s in segments]
    method = "word_cue"
    boundaries: List[Tuple[float, float]] = []

    # 1. Primary: Word boundary cues from Edge TTS SubMaker
    if word_cues:
        boundaries, method = align_word_cues_to_segments(sentences, word_cues, group_duration)

    # 2. Fallback a: Whisper if word cues missing or failed
    if (not boundaries or method == "proportional") and allow_whisper and wav_path and Path(wav_path).is_file():
        boundaries, method = align_with_whisper(wav_path, sentences, group_duration)

    # 3. Fallback b: Proportional division if still unaligned
    if not boundaries:
        boundaries, method = align_proportionally(sentences, group_duration)

    # Apply computed offsets to segments
    for seg, (start_sec, end_sec) in zip(segments, boundaries):
        seg.start_offset_seconds = round(start_sec, 3)
        seg.end_offset_seconds = round(end_sec, 3)
        seg.duration_seconds = round(end_sec - start_sec, 3)

    return segments, method
