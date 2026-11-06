"""Sentence-level Segment Alignment Engine for TTS Groups (3.3, R2, R3).

Maps TTS word boundaries/cues to individual sentences (Segments) within a group,
supporting:
- Resilient sequential token matching tolerant of punctuation, numbers, quotes, and case
- Strict zero-gap boundary rules with midpoint pause splitting
- Configurable word match ratio threshold (default 0.85, R2) with fallback reason tracking
- Word-level timing serialization (R3) for 2-line subtitle splitting in Stage 5
- Fallback chain: Word Boundaries -> faster-whisper transcription -> character-count proportional division
- Diagnostic alignment method tracking ("word_cue" | "whisper" | "proportional")
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from app.services.longform.hashing import normalize_text
from app.services.longform.schema import Segment


@dataclass
class WordCue:
    """A timestamped word cue from TTS or ASR."""

    word: str
    start: float
    end: float


@dataclass
class WordTiming:
    """Precise per-word timing within group and chapter (R3)."""

    word: str
    group_start: float
    group_end: float
    chapter_start: Optional[float] = None
    chapter_end: Optional[float] = None
    segment_index: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "word": self.word,
            "group_start": round(self.group_start, 3),
            "group_end": round(self.group_end, 3),
            "chapter_start": round(self.chapter_start, 3) if self.chapter_start is not None else None,
            "chapter_end": round(self.chapter_end, 3) if self.chapter_end is not None else None,
            "segment_index": self.segment_index,
        }


@dataclass
class AlignmentResult:
    """Detailed result of sentence alignment, backwards-compatible with 2-tuple unpacking."""

    boundaries: List[Tuple[float, float]]
    method: str
    match_ratio: float = 1.0
    fallback_reason: Optional[str] = None
    word_timings: List[Dict[str, Any]] = field(default_factory=list)

    def __iter__(self):
        yield self.boundaries
        yield self.method


@dataclass
class GroupAlignmentResult:
    """Detailed result of group segment alignment, backwards-compatible with 2-tuple unpacking."""

    segments: List[Segment]
    method: str
    match_ratio: float = 1.0
    fallback_reason: Optional[str] = None
    word_timings: List[Dict[str, Any]] = field(default_factory=list)

    def __iter__(self):
        yield self.segments
        yield self.method


def normalize_token(token: str) -> str:
    """Normalize a word token by stripping punctuation, quotes, and symbols."""
    if not token:
        return ""
    norm = unicodedata.normalize("NFC", str(token)).lower().strip()
    norm = re.sub(r"^[^\w\d]+|[^\w\d]+$", "", norm)
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
    word_cue_threshold: float = 0.85,
) -> AlignmentResult:
    """Map word cues sequentially to sentence segments using tolerant windowed matching (3.3, R2, R3)."""
    num_sentences = len(sentences)
    if num_sentences == 0:
        return AlignmentResult([], "word_cue", 1.0, None, [])

    sentence_raw_words: List[List[str]] = []
    sentence_tokens_list: List[List[str]] = []
    for s in sentences:
        raw_words = [w for w in re.split(r"\s+", s.strip()) if w]
        sentence_raw_words.append(raw_words)
        clean_words = [normalize_token(w) for w in raw_words if normalize_token(w)]
        sentence_tokens_list.append(clean_words or [normalize_token(s)])

    total_words = sum(len(toks) for toks in sentence_tokens_list)

    if not cues:
        return align_proportionally(
            sentences,
            group_duration,
            match_ratio=0.0,
            fallback_reason="No word cues provided by TTS",
        )

    cue_pointer = 0
    num_cues = len(cues)
    total_matched = 0

    raw_boundaries: List[Tuple[float, float]] = []
    matched_word_records: List[List[Dict[str, Any]]] = []

    for s_idx, tokens in enumerate(sentence_tokens_list):
        if not tokens:
            raw_boundaries.append((0.0, 0.0))
            matched_word_records.append([])
            continue

        start_time: Optional[float] = None
        end_time: Optional[float] = None
        matched_in_sentence = 0
        local_c_idx = cue_pointer
        s_words: List[Dict[str, Any]] = []
        raw_words = sentence_raw_words[s_idx]

        for t_idx, t in enumerate(tokens):
            raw_w = raw_words[t_idx] if t_idx < len(raw_words) else t
            window_end = min(num_cues, local_c_idx + 8)
            found_idx = -1
            for search_idx in range(local_c_idx, window_end):
                if tokens_match(t, cues[search_idx].word):
                    found_idx = search_idx
                    break

            if found_idx != -1:
                matched_in_sentence += 1
                c_start = cues[found_idx].start
                c_end = cues[found_idx].end
                if start_time is None:
                    start_time = c_start
                end_time = c_end
                local_c_idx = found_idx + 1
                s_words.append({
                    "word": raw_w,
                    "group_start": c_start,
                    "group_end": c_end,
                    "chapter_start": None,
                    "chapter_end": None,
                    "segment_index": s_idx + 1,
                })
            else:
                s_words.append({
                    "word": raw_w,
                    "group_start": None,
                    "group_end": None,
                    "chapter_start": None,
                    "chapter_end": None,
                    "segment_index": s_idx + 1,
                })

        total_matched += matched_in_sentence
        cue_pointer = local_c_idx

        if start_time is None or end_time is None:
            if raw_boundaries:
                prev_end = raw_boundaries[-1][1]
                start_time = prev_end
                end_time = prev_end + 0.5
            else:
                start_time = 0.0
                end_time = cues[0].end if cues else 0.5

        raw_boundaries.append((start_time, end_time))
        matched_word_records.append(s_words)

    match_ratio = round(total_matched / float(max(1, total_words)), 3)

    if match_ratio < word_cue_threshold:
        reason = f"Word cue match ratio {match_ratio:.2f} is below threshold {word_cue_threshold:.2f}"
        logger.warning(f"{reason}. Falling back to proportional alignment.")
        return align_proportionally(
            sentences,
            group_duration,
            match_ratio=match_ratio,
            fallback_reason=reason,
        )

    smoothed: List[Tuple[float, float]] = []
    current_start = 0.0

    for i in range(num_sentences):
        _, raw_end = raw_boundaries[i]

        if i == num_sentences - 1:
            end_boundary = group_duration
        else:
            next_raw_start, _ = raw_boundaries[i + 1]
            if next_raw_start >= raw_end:
                midpoint = (raw_end + next_raw_start) / 2.0
                end_boundary = midpoint
            else:
                end_boundary = raw_end

            if end_boundary <= current_start:
                end_boundary = current_start + 0.1

        smoothed.append((round(current_start, 3), round(end_boundary, 3)))
        current_start = end_boundary

    all_word_timings: List[Dict[str, Any]] = []
    for i, s_words in enumerate(matched_word_records):
        seg_start, seg_end = smoothed[i]
        n_words = len(s_words)
        if n_words == 0:
            continue

        for w_idx, w_item in enumerate(s_words):
            if w_item["group_start"] is None:
                step = (seg_end - seg_start) / float(n_words)
                w_start = seg_start + (w_idx * step)
                w_end = w_start + step
            else:
                w_start = max(seg_start, min(seg_end, w_item["group_start"]))
                w_end = max(w_start + 0.05, min(seg_end, w_item["group_end"]))

            all_word_timings.append({
                "word": w_item["word"],
                "group_start": round(w_start, 3),
                "group_end": round(w_end, 3),
                "chapter_start": None,
                "chapter_end": None,
                "segment_index": i + 1,
            })

    return AlignmentResult(
        boundaries=smoothed,
        method="word_cue",
        match_ratio=match_ratio,
        fallback_reason=None,
        word_timings=all_word_timings,
    )


def align_proportionally(
    sentences: List[str],
    group_duration: float,
    match_ratio: float = 0.0,
    fallback_reason: Optional[str] = None,
) -> AlignmentResult:
    """Fallback alignment distributing group duration proportionally by clean character count (3.3, R2, R3)."""
    num_sentences = len(sentences)
    if num_sentences == 0:
        return AlignmentResult([], "proportional", match_ratio, fallback_reason, [])

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

    all_word_timings: List[Dict[str, Any]] = []
    for s_idx, (seg_start, seg_end) in enumerate(boundaries):
        words = [w for w in re.split(r"\s+", sentences[s_idx].strip()) if w]
        if not words:
            continue
        seg_dur = max(0.1, seg_end - seg_start)
        w_dur = seg_dur / float(len(words))
        for w_idx, w in enumerate(words):
            w_start = seg_start + (w_idx * w_dur)
            w_end = w_start + w_dur
            all_word_timings.append({
                "word": w,
                "group_start": round(w_start, 3),
                "group_end": round(w_end, 3),
                "chapter_start": None,
                "chapter_end": None,
                "segment_index": s_idx + 1,
            })

    return AlignmentResult(
        boundaries=boundaries,
        method="proportional",
        match_ratio=match_ratio,
        fallback_reason=fallback_reason or "Proportional character-length division",
        word_timings=all_word_timings,
    )


def align_with_whisper(
    wav_path: str | Path,
    sentences: List[str],
    group_duration: float,
    word_cue_threshold: float = 0.85,
) -> AlignmentResult:
    """Attempt speech-to-text alignment using existing faster-whisper in app.services.subtitle (3.3 Fallback a)."""
    try:
        from app.services import subtitle
        if not subtitle._ensure_model_loaded():
            logger.info("faster-whisper model not available locally; falling back to proportional alignment.")
            return align_proportionally(
                sentences,
                group_duration,
                match_ratio=0.0,
                fallback_reason="faster-whisper model not available locally",
            )

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
            res = align_word_cues_to_segments(
                sentences=sentences,
                cues=cues,
                group_duration=group_duration,
                word_cue_threshold=word_cue_threshold,
            )
            if res.method == "word_cue":
                return AlignmentResult(
                    boundaries=res.boundaries,
                    method="whisper",
                    match_ratio=res.match_ratio,
                    fallback_reason=None,
                    word_timings=res.word_timings,
                )
            return res

    except Exception as exc:
        logger.warning(f"Whisper fallback alignment failed: {exc}. Using proportional fallback.")

    return align_proportionally(
        sentences,
        group_duration,
        match_ratio=0.0,
        fallback_reason="Whisper alignment failed or threw exception",
    )


def align_group_segments(
    segments: List[Segment],
    group_duration: float,
    word_cues: Optional[List[WordCue]] = None,
    wav_path: Optional[str | Path] = None,
    allow_whisper: bool = False,
    word_cue_threshold: float = 0.85,
) -> GroupAlignmentResult:
    """High-level segment alignment coordinator applying fallback chain and boundary invariants (3.3, R2, R3).

    Updates start_offset_seconds, end_offset_seconds, and duration_seconds on segments.
    Returns GroupAlignmentResult (backwards compatible as 2-tuple unpacking (segments, method)).
    """
    if not segments:
        return GroupAlignmentResult([], "word_cue", 1.0, None, [])

    sentences = [s.narration_text for s in segments]
    res: Optional[AlignmentResult] = None

    if word_cues:
        res = align_word_cues_to_segments(
            sentences=sentences,
            cues=word_cues,
            group_duration=group_duration,
            word_cue_threshold=word_cue_threshold,
        )

    if (res is None or res.method == "proportional") and allow_whisper and wav_path and Path(wav_path).is_file():
        res = align_with_whisper(
            wav_path=wav_path,
            sentences=sentences,
            group_duration=group_duration,
            word_cue_threshold=word_cue_threshold,
        )

    if res is None:
        res = align_proportionally(
            sentences=sentences,
            group_duration=group_duration,
            match_ratio=0.0,
            fallback_reason="No cues or Whisper available",
        )

    for seg, (start_sec, end_sec) in zip(segments, res.boundaries):
        seg.start_offset_seconds = round(start_sec, 3)
        seg.end_offset_seconds = round(end_sec, 3)
        seg.duration_seconds = round(end_sec - start_sec, 3)

    return GroupAlignmentResult(
        segments=segments,
        method=res.method,
        match_ratio=res.match_ratio,
        fallback_reason=res.fallback_reason,
        word_timings=res.word_timings,
    )
