import json
from pathlib import Path
import pytest

from app.services.longform.alignment import WordCue, align_word_cues_to_segments
from app.services.longform.planner import compute_plan
from app.services.longform.schema import (
    Chapter,
    ItemStatus,
    ProjectManifest,
    Segment,
    TtsGroup,
    VisualSpec,
)
from app.services.longform.store import load_manifest, save_manifest
from app.services.longform.voice_service import VoiceService


def create_sample_project(project_dir: Path) -> ProjectManifest:
    manifest = ProjectManifest(
        project_id="sample_fixes_proj",
        title="Sample Fixes Project",
        preset_id="automotive.muscle_car",
        preset_snapshot={
            "id": "automotive.muscle_car",
            "name": "Classic Muscle Cars",
            "default_voice": "en-US-ChristopherNeural-Male",
            "tts_provider": "fake",
            "words_per_minute": 150,
            "pause_between_groups_ms": 300,
            "word_cue_threshold": 0.85,
        },
        chapters=[
            Chapter(
                chapter_index=1,
                chapter_title="Chapter One",
                tts_groups=[
                    TtsGroup(
                        group_index=1,
                        paragraph_text="The first sentence of chapter one. The second sentence has more detail.",
                        segments=[
                            Segment(
                                segment_index=1,
                                narration_text="The first sentence of chapter one.",
                                visual=VisualSpec(prompt="Historic photo of Detroit"),
                            ),
                            Segment(
                                segment_index=2,
                                narration_text="The second sentence has more detail.",
                                visual=VisualSpec(prompt="Engine blueprint"),
                            ),
                        ],
                    ),
                    TtsGroup(
                        group_index=2,
                        paragraph_text="Third sentence in group two. Fourth sentence concludes the thought.",
                        segments=[
                            Segment(
                                segment_index=3,
                                narration_text="Third sentence in group two.",
                                visual=VisualSpec(prompt="Drag strip race"),
                            ),
                            Segment(
                                segment_index=4,
                                narration_text="Fourth sentence concludes the thought.",
                                visual=VisualSpec(prompt="Checkered flag"),
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
    save_manifest(manifest, project_dir)
    return manifest


def test_r1_audio_preview_mp4_in_path_with_spaces(tmp_path: Path):
    """R1: Test generating chapter_01_preview.mp4 in a directory with spaces."""
    spaced_dir = tmp_path / "path with spaces" / "project folder"
    spaced_dir.mkdir(parents=True, exist_ok=True)

    create_sample_project(spaced_dir)
    service = VoiceService(project_dir=spaced_dir)
    service.synthesize_all(provider="fake")

    wav_path, srt_path, mp4_path = service.generate_preview(chapter_index=1)

    assert wav_path.is_file(), f"Preview WAV missing: {wav_path}"
    assert srt_path.is_file(), f"Preview SRT missing: {srt_path}"
    assert mp4_path.is_file(), f"Preview MP4 missing: {mp4_path}"
    assert mp4_path.stat().st_size > 1000, "Preview MP4 is unexpectedly small"
    assert mp4_path.name == "chapter_01_preview.mp4"

    # Verify ASS subtitle was generated and has PlayRes 1280x720
    ass_path = spaced_dir / "export" / "preview" / "chapter_01_preview.ass"
    assert ass_path.is_file()
    ass_text = ass_path.read_text(encoding="utf-8")
    assert "PlayResX: 1280" in ass_text
    assert "PlayResY: 720" in ass_text


def test_r2_word_match_ratio_and_fallback(tmp_path: Path):
    """R2: Test word_cue threshold 0.85 and 0.60 match ratio triggering fallback."""
    sentences = [
        "First sentence with some words.",
        "Second sentence is completely unmatched with cues.",
    ]
    # Sentence 1 has 5 words, Sentence 2 has 6 words. Total 11 words.
    # We supply cues matching 4 words (~36% match ratio, well below 85%)
    cues = [
        WordCue(word="first", start=0.1, end=0.4),
        WordCue(word="sentence", start=0.4, end=0.9),
        WordCue(word="with", start=0.9, end=1.2),
        WordCue(word="words", start=1.2, end=1.6),
    ]

    res = align_word_cues_to_segments(
        sentences=sentences,
        cues=cues,
        group_duration=5.0,
        word_cue_threshold=0.85,
    )

    assert res.method == "proportional"
    assert res.match_ratio < 0.85
    assert res.fallback_reason is not None
    assert "below threshold" in res.fallback_reason
    assert len(res.word_timings) > 0


def test_r3_word_level_timing_sidecar(tmp_path: Path):
    """R3: Test storing per-word timings sidecar JSON with relative and absolute offsets."""
    create_sample_project(tmp_path)
    service = VoiceService(project_dir=tmp_path)
    service.synthesize_all(provider="fake")

    sidecar_path = tmp_path / "assets" / "audio" / "words" / "ch1_g1_words.json"
    assert sidecar_path.is_file(), f"Word timing sidecar not found: {sidecar_path}"

    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert data["chapter_index"] == 1
    assert data["group_index"] == 1
    assert len(data["words"]) > 0

    first_word = data["words"][0]
    assert "word" in first_word
    assert "group_start" in first_word
    assert "group_end" in first_word
    assert "chapter_start" in first_word
    assert "chapter_end" in first_word
    assert first_word["chapter_start"] is not None
    assert first_word["chapter_end"] is not None
    assert first_word["chapter_start"] >= first_word["group_start"]


def test_r4_set_voice_and_sentence_edit_staleness(tmp_path: Path):
    """R4: Test set-voice invalidates all audio, and editing 1 sentence invalidates only that group."""
    create_sample_project(tmp_path)
    service = VoiceService(project_dir=tmp_path)
    service.synthesize_all(provider="fake")

    # 1. Edit 1 sentence in group 2
    m = load_manifest(tmp_path)
    m.chapters[0].tts_groups[1].segments[0].narration_text = "Modified third sentence in group two."
    save_manifest(m, tmp_path)

    plan_after_edit = compute_plan(m, tmp_path)
    g1_unit = [u for u in plan_after_edit.units if u.unit_id == "ch1_g1_audio"][0]
    g2_unit = [u for u in plan_after_edit.units if u.unit_id == "ch1_g2_audio"][0]

    # Group 1 remains completed!
    assert g1_unit.status == ItemStatus.completed
    # Group 2 is stale!
    assert g2_unit.status == ItemStatus.stale

    # 2. Test set-voice invalidates all audio groups
    service.set_voice("en-US-GuyNeural", voice_rate=1.1)
    m_voice = load_manifest(tmp_path)
    plan_after_voice = compute_plan(m_voice, tmp_path)

    audio_units = [u for u in plan_after_voice.units if u.unit_type == "tts_group_audio"]
    assert len(audio_units) == 2
    assert all(u.status == ItemStatus.stale for u in audio_units), "All audio units must be stale after set-voice"
