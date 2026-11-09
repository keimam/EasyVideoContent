"""Subtitle generation, chunking, and ASS/SRT formatting (Stage 5.2).

Handles sentence chunking (max 2 lines, max chars per line, 0.8-6.0s duration,
punctuation breaks, orphan word prevention), font preflight in resource/fonts,
and ASS rendering with exact PlayRes matching project resolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds into SubRip timestamp: HH:MM:SS,mmm."""
    sec = max(0.0, float(seconds))
    total_ms = int(round(sec * 1000.0))
    hours = total_ms // (3600 * 1000)
    remainder = total_ms % (3600 * 1000)
    minutes = remainder // (60 * 1000)
    remainder = remainder % (60 * 1000)
    secs = remainder // 1000
    millis = remainder % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_ass_timestamp(seconds: float) -> str:
    """Format seconds into ASS timestamp: H:MM:SS.cc."""
    sec = max(0.0, float(seconds))
    total_cs = int(round(sec * 100.0))
    hours = total_cs // (3600 * 100)
    remainder = total_cs % (3600 * 100)
    minutes = remainder // (60 * 100)
    remainder = remainder % (60 * 100)
    secs = remainder // 100
    centis = remainder % 100
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def normalize_ass_color(val: Any, default: str = "&H00FFFFFF") -> str:
    """Normalize color strings to ASS &HAABBGGRR format."""
    if not val or not isinstance(val, str):
        return default
    s = val.strip()
    if s.startswith("&H") or s.startswith("&h"):
        return s
    if s.startswith("#"):
        hex_val = s.lstrip("#")
        if len(hex_val) == 6:
            r = hex_val[0:2]
            g = hex_val[2:4]
            b = hex_val[4:6]
            return f"&H00{b}{g}{r}".upper()
        if len(hex_val) == 8:
            a = hex_val[0:2]
            r = hex_val[2:4]
            g = hex_val[4:6]
            b = hex_val[6:8]
            return f"&H{a}{b}{g}{r}".upper()
    return default


def wrap_chunk_lines(words: List[str], max_chars_per_line: int = 42) -> List[str]:
    """Wrap words into at most 2 lines, preventing single hanging orphan words on line 2 (5.2)."""
    if not words:
        return []
    total_text = " ".join(words)
    if len(total_text) <= max_chars_per_line or len(words) <= 1:
        return [total_text]

    # If 2 or more words, find best split point
    best_split = None
    min_len_diff = float("inf")

    for i in range(1, len(words)):
        line1 = " ".join(words[:i])
        line2 = " ".join(words[i:])

        # Constraint 1: both lines <= max_chars_per_line
        if len(line1) <= max_chars_per_line and len(line2) <= max_chars_per_line:
            # Rule: no single word hanging on line 2 (if words[i:] has only 1 word, penalize unless unavoidable)
            is_orphan = len(words[i:]) == 1 and len(words[:i]) > 1
            penalty = 1000 if is_orphan else 0

            # Balance lengths
            diff = abs(len(line1) - len(line2)) + penalty
            if diff < min_len_diff:
                min_len_diff = diff
                best_split = (line1, line2)

    if best_split:
        return [best_split[0], best_split[1]]

    # Fallback if text exceeds 2 lines of max_chars_per_line: greedy break
    l1_words = []
    l2_words = []
    cur_len = 0
    for w in words:
        if cur_len + len(w) + (1 if l1_words else 0) <= max_chars_per_line and not l2_words:
            l1_words.append(w)
            cur_len += len(w) + (1 if len(l1_words) > 1 else 0)
        else:
            l2_words.append(w)

    # Rebalance if line 2 is an orphan word and line 1 has >= 2 words
    if len(l2_words) == 1 and len(l1_words) > 1:
        l2_words.insert(0, l1_words.pop())

    return [" ".join(l1_words), " ".join(l2_words)]


@dataclass
class SubtitleChunk:
    """Represents a coherent subtitle unit of 0.8-6.0 seconds displayed on screen."""
    start_sec: float
    end_sec: float
    lines: List[str]
    words: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)

    @property
    def plain_text(self) -> str:
        return " ".join(self.lines)

    @property
    def ass_text(self) -> str:
        return "\\N".join(self.lines)

    def karaoke_text(self) -> str:
        """Render karaoke \\k tags for word highlighting."""
        parts = []
        for w in self.words:
            w_text = w.get("word", "")
            w_start = w.get("start", self.start_sec)
            w_end = w.get("end", self.end_sec)
            dur_cs = max(1, int(round((w_end - w_start) * 100.0)))
            parts.append(f"{{\\k{dur_cs}}}{w_text}")
        return " ".join(parts)


class SubtitleChunker:
    """Pecah timing kata menjadi potongan subtitle 0.8-6.0 detik (5.2)."""

    def __init__(
        self,
        max_chars_per_line: int = 42,
        min_duration: float = 0.8,
        max_duration: float = 6.0,
    ):
        self.max_chars_per_line = max_chars_per_line
        self.min_duration = min_duration
        self.max_duration = max_duration

    def chunk_words(self, words: List[Dict[str, Any]]) -> List[SubtitleChunk]:
        """Convert a list of word dictionaries into timed subtitle chunks."""
        if not words:
            return []

        chunks: List[SubtitleChunk] = []
        current_chunk_words: List[Dict[str, Any]] = []

        punctuation_pattern = re.compile(r"[,;:\.!\?\—\-]$")

        for idx, w in enumerate(words):
            current_chunk_words.append(w)
            c_start = current_chunk_words[0]["start"]
            c_end = current_chunk_words[-1]["end"]
            dur = c_end - c_start
            w_text = w.get("word", "")
            has_punct = bool(punctuation_pattern.search(w_text))

            total_chars = sum(len(x.get("word", "")) for x in current_chunk_words) + len(current_chunk_words) - 1
            max_chunk_chars = self.max_chars_per_line * 2

            # Decide whether to close current chunk
            is_last = (idx == len(words) - 1)
            time_to_close = False

            if dur >= self.min_duration:
                if has_punct and dur <= self.max_duration:
                    time_to_close = True
                elif dur >= (self.max_duration - 1.0):
                    time_to_close = True
                elif total_chars >= (max_chunk_chars - 10):
                    time_to_close = True

            if dur >= self.max_duration or total_chars >= max_chunk_chars:
                time_to_close = True

            if (time_to_close or is_last) and current_chunk_words:
                chunk_obj = self._build_chunk(current_chunk_words)
                chunks.append(chunk_obj)
                current_chunk_words = []

        return chunks

    def _build_chunk(self, chunk_words: List[Dict[str, Any]]) -> SubtitleChunk:
        raw_words = [w.get("word", "") for w in chunk_words]
        lines = wrap_chunk_lines(raw_words, self.max_chars_per_line)
        start_sec = chunk_words[0]["start"]
        end_sec = max(chunk_words[-1]["end"], start_sec + 0.1)
        return SubtitleChunk(
            start_sec=start_sec,
            end_sec=end_sec,
            lines=lines,
            words=chunk_words,
        )


def check_font_availability(font_name: str, fonts_dir: Path) -> Tuple[bool, Optional[Path]]:
    """Check if font exists in fonts_dir and return file path if found (5.2)."""
    if not fonts_dir.is_dir():
        return False, None

    # Direct match or with extensions
    candidates = [
        font_name,
        f"{font_name}.ttf",
        f"{font_name}.otf",
        f"{font_name}.ttc",
    ]
    for c in candidates:
        target = fonts_dir / c
        if target.is_file():
            return True, target

    # Case-insensitive scan
    font_lower = font_name.lower().replace(" ", "")
    for f in fonts_dir.iterdir():
        if f.is_file():
            stem_lower = f.stem.lower().replace(" ", "")
            name_lower = f.name.lower().replace(" ", "")
            if font_lower in stem_lower or font_lower in name_lower:
                return True, f

    return False, None


def generate_ass_header(
    subtitle_style: Dict[str, Any],
    play_res_x: int = 1920,
    play_res_y: int = 1080,
    fonts_dir: Optional[Path] = None,
) -> str:
    """Generate ASS script info and style definition with strict PlayRes matching (5.2)."""
    font_name = subtitle_style.get("font_name", "BeVietnamPro-Bold")
    if fonts_dir:
        found, font_path = check_font_availability(font_name, fonts_dir)
        if not found:
            logger.warning(
                f"Font '{font_name}' not found in '{fonts_dir}'. "
                f"Subtitles will fall back to system font."
            )
            print(f"WARNING: Font '{font_name}' not found in '{fonts_dir}'. Subtitles will use font fallback.")

    font_size = int(subtitle_style.get("font_size", 64))
    primary = normalize_ass_color(subtitle_style.get("primary_color"), "&H00FFFFFF")
    secondary = normalize_ass_color(subtitle_style.get("secondary_color"), "&H000000FF")
    outline = normalize_ass_color(subtitle_style.get("outline_color"), "&H00000000")
    back = normalize_ass_color(subtitle_style.get("back_color"), "&H80000000")
    bold = 1 if subtitle_style.get("bold", True) else 0
    italic = 1 if subtitle_style.get("italic", False) else 0
    border_style = int(subtitle_style.get("border_style", 1))
    outline_w = float(subtitle_style.get("outline_width", 3.5))
    shadow_d = float(subtitle_style.get("shadow_depth", subtitle_style.get("shadow", 1.0)))
    alignment = int(subtitle_style.get("alignment", 2))
    margin_v = int(subtitle_style.get("margin_v", 80))

    header = f"""[Script Info]
Title: Longform Video Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {play_res_x}
PlayResY: {play_res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary},{secondary},{outline},{back},{bold},{italic},0,0,100,100,0,0,{border_style},{outline_w},{shadow_d},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header


def extract_chapter_words(chapter: Any, project_dir: Path) -> List[Dict[str, Any]]:
    """Extract chronological word timings with absolute chapter timestamps (5.2)."""
    all_words: List[Dict[str, Any]] = []

    for group in getattr(chapter, "tts_groups", []):
        word_file_rel = getattr(group.audio, "word_timings_file", None)
        group_words = []
        if word_file_rel:
            full_word_path = project_dir / word_file_rel
            if full_word_path.is_file():
                try:
                    data = json.loads(full_word_path.read_text(encoding="utf-8"))
                    raw_w = data.get("words", [])
                    for w in raw_w:
                        group_words.append({
                            "word": w.get("word", ""),
                            "start": float(w.get("chapter_start", w.get("start", 0.0))),
                            "end": float(w.get("chapter_end", w.get("end", 0.0))),
                        })
                except Exception as exc:
                    logger.warning(f"Could not read word timing file {full_word_path}: {exc}")

        # Fallback if no word timings file: interpolate from segments
        if not group_words:
            for seg in getattr(group, "segments", []):
                seg_start = getattr(seg, "chapter_start_seconds", None)
                seg_end = getattr(seg, "chapter_end_seconds", None)
                text = getattr(seg, "narration_text", "")
                if seg_start is not None and seg_end is not None and text:
                    seg_words = text.split()
                    if seg_words:
                        dur = max(0.1, seg_end - seg_start)
                        w_dur = dur / len(seg_words)
                        for i, sw in enumerate(seg_words):
                            group_words.append({
                                "word": sw,
                                "start": seg_start + i * w_dur,
                                "end": seg_start + (i + 1) * w_dur,
                            })

        all_words.extend(group_words)

    # Sort strictly by start time
    all_words.sort(key=lambda x: x["start"])
    return all_words


def generate_chapter_ass_and_srt(
    chapter: Any,
    project_dir: Path,
    preset_snapshot: Dict[str, Any],
    encoder_settings: Any,
    output_ass_path: Path,
    output_srt_path: Optional[Path] = None,
) -> Tuple[Optional[Path], Optional[Path]]:
    """Generate ASS and SRT subtitle files for a specific chapter (5.2)."""
    subtitle_mode = preset_snapshot.get("subtitle_mode", "sentence_chunks").lower()
    if subtitle_mode == "off":
        return None, None

    subtitle_style = preset_snapshot.get("subtitle_style", {})
    max_c = preset_snapshot.get("max_chars_per_line", 42)
    min_d = preset_snapshot.get("min_chunk_duration", 0.8)
    max_d = preset_snapshot.get("max_chunk_duration", 6.0)

    res_w = 1920
    res_h = 1080
    if encoder_settings and getattr(encoder_settings, "resolution", None):
        res_w, res_h = encoder_settings.resolution

    fonts_dir = project_dir / "resource" / "fonts"
    if not fonts_dir.is_dir():
        # Check workspace root
        fonts_dir = Path("resource/fonts")

    chunker = SubtitleChunker(max_chars_per_line=max_c, min_duration=min_d, max_duration=max_d)
    words = extract_chapter_words(chapter, project_dir)
    chunks = chunker.chunk_words(words)

    # 1. Build ASS
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    header = generate_ass_header(
        subtitle_style=subtitle_style,
        play_res_x=res_w,
        play_res_y=res_h,
        fonts_dir=fonts_dir,
    )

    events: List[str] = []
    for c in chunks:
        start_ts = format_ass_timestamp(c.start_sec)
        end_ts = format_ass_timestamp(c.end_sec)
        if subtitle_mode == "word_highlight":
            text = c.karaoke_text()
        else:
            text = c.ass_text
        events.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text}")

    full_ass = header + "\n".join(events) + "\n"
    output_ass_path.write_text(full_ass, encoding="utf-8")

    # 2. Build SRT if requested
    if output_srt_path:
        output_srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_lines: List[str] = []
        for i, c in enumerate(chunks, 1):
            s_ts = format_srt_timestamp(c.start_sec)
            e_ts = format_srt_timestamp(c.end_sec)
            txt = "\n".join(c.lines)
            srt_lines.append(f"{i}\n{s_ts} --> {e_ts}\n{txt}\n")
        output_srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

    return output_ass_path, output_srt_path


def generate_full_timeline_subtitles(
    manifest: Any,
    project_dir: Path,
) -> Tuple[Path, Path]:
    """Generate export/subtitles/full_timeline.srt and .ass across all chapters (5.2)."""
    sub_dir = project_dir / "export" / "subtitles"
    sub_dir.mkdir(parents=True, exist_ok=True)
    out_srt = sub_dir / "full_timeline.srt"
    out_ass = sub_dir / "full_timeline.ass"

    preset_snap = manifest.preset_snapshot or {}
    subtitle_style = preset_snap.get("subtitle_style", {})
    max_c = preset_snap.get("max_chars_per_line", 42)
    min_d = preset_snap.get("min_chunk_duration", 0.8)
    max_d = preset_snap.get("max_chunk_duration", 6.0)

    res_w = 1920
    res_h = 1080
    if manifest.encoder_settings and getattr(manifest.encoder_settings, "resolution", None):
        res_w, res_h = manifest.encoder_settings.resolution

    fonts_dir = project_dir / "resource" / "fonts"
    if not fonts_dir.is_dir():
        fonts_dir = Path("resource/fonts")

    chunker = SubtitleChunker(max_chars_per_line=max_c, min_duration=min_d, max_duration=max_d)

    all_timeline_chunks: List[SubtitleChunk] = []
    current_timeline_offset = 0.0

    for ch in manifest.chapters:
        ch_words = extract_chapter_words(ch, project_dir)
        # Shift words by current timeline offset
        shifted_words = []
        for w in ch_words:
            shifted_words.append({
                "word": w["word"],
                "start": w["start"] + current_timeline_offset,
                "end": w["end"] + current_timeline_offset,
            })
        ch_chunks = chunker.chunk_words(shifted_words)
        all_timeline_chunks.extend(ch_chunks)

        # Advance timeline by chapter audio duration
        ch_dur = ch.audio_duration_seconds or 0.0
        current_timeline_offset += ch_dur

    # Write SRT
    srt_parts = []
    for idx, c in enumerate(all_timeline_chunks, 1):
        s_ts = format_srt_timestamp(c.start_sec)
        e_ts = format_srt_timestamp(c.end_sec)
        txt = "\n".join(c.lines)
        srt_parts.append(f"{idx}\n{s_ts} --> {e_ts}\n{txt}\n")
    out_srt.write_text("\n".join(srt_parts), encoding="utf-8")

    # Write ASS
    header = generate_ass_header(
        subtitle_style=subtitle_style,
        play_res_x=res_w,
        play_res_y=res_h,
        fonts_dir=fonts_dir,
    )
    ass_events = []
    for c in all_timeline_chunks:
        s_ts = format_ass_timestamp(c.start_sec)
        e_ts = format_ass_timestamp(c.end_sec)
        ass_events.append(f"Dialogue: 0,{s_ts},{e_ts},Default,,0,0,0,,{c.ass_text}")

    full_ass = header + "\n".join(ass_events) + "\n"
    out_ass.write_text(full_ass, encoding="utf-8")

    logger.info(f"Generated full timeline subtitles: {out_srt} and {out_ass}")
    return out_srt, out_ass


def generate_subtitle_preview_frames(
    project_dir: Path,
    manifest: Any,
    chapter_index: int,
    timestamps_seconds: List[float],
    font_size_override: Optional[int] = None,
    margin_v_override: Optional[int] = None,
    output_dir: Optional[Path] = None,
) -> List[Path]:
    """Generate burned subtitle PNG preview frames at given timestamps without full chapter re-render (R1)."""
    from app.services.longform.render.chapter_render import escape_ffmpeg_filter_path
    import subprocess

    p_dir = Path(project_dir)
    ch = next((c for c in manifest.chapters if c.chapter_index == chapter_index), None)
    if not ch:
        raise ValueError(f"Chapter {chapter_index} not found in project manifest")

    if not ch.shots:
        raise ValueError(f"Chapter {chapter_index} has no shots built")

    preview_dir = output_dir or (p_dir / "export" / "preview" / "subtitles")
    preview_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare custom or preset subtitle style
    preset_snap = dict(manifest.preset_snapshot or {})
    sub_style = dict(preset_snap.get("subtitle_style", {}))
    if font_size_override is not None:
        sub_style["font_size"] = int(font_size_override)
    if margin_v_override is not None:
        sub_style["margin_v"] = int(margin_v_override)

    preset_snap["subtitle_style"] = sub_style

    # 2. Generate temporary ASS file for this chapter
    temp_ass = preview_dir / f"temp_preview_ch{chapter_index:02d}.ass"
    generate_chapter_ass_and_srt(
        chapter=ch,
        project_dir=p_dir,
        preset_snapshot=preset_snap,
        encoder_settings=manifest.encoder_settings,
        output_ass_path=temp_ass,
    )

    # 3. Create temporary concat demuxer list for chapter shot clips
    temp_concat = preview_dir / f"temp_concat_ch{chapter_index:02d}.txt"
    concat_lines = []
    for shot in ch.shots:
        clip_rel = shot.rendered_clip or f"assets/visual/shots/{shot.shot_id}.mp4"
        clip_p = (p_dir / clip_rel).resolve()
        if not clip_p.is_file():
            raise FileNotFoundError(f"Shot clip not found: {clip_p}")
        safe_p = str(clip_p).replace("\\", "/")
        concat_lines.append(f"file '{safe_p}'")
    temp_concat.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    fonts_dir = p_dir / "resource" / "fonts"
    if not fonts_dir.is_dir():
        fonts_dir = Path("resource/fonts").resolve()

    esc_ass = escape_ffmpeg_filter_path(temp_ass)
    esc_fonts = escape_ffmpeg_filter_path(fonts_dir)

    results: List[Path] = []
    try:
        for t in timestamps_seconds:
            out_png = preview_dir / f"sub_preview_ch{chapter_index:02d}_at_{int(t):03d}s.png"
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(temp_concat.resolve()),
                "-ss", str(t),
                "-vf", f"ass=filename='{esc_ass}':fontsdir='{esc_fonts}'",
                "-vframes", "1",
                str(out_png),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                logger.error(f"Failed to generate subtitle preview at {t}s: {res.stderr}")
            elif out_png.is_file():
                results.append(out_png)
    finally:
        if temp_concat.is_file():
            temp_concat.unlink(missing_ok=True)
        if temp_ass.is_file():
            temp_ass.unlink(missing_ok=True)

    return results
