import pytest
from pathlib import Path

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
from app.services.longform.voice_service import VoiceService, list_available_voices


def create_dummy_manifest(project_dir: Path) -> ProjectManifest:
    manifest = ProjectManifest(
        project_id="test_voice_proj",
        title="1964 Pontiac GTO Test",
        preset_id="automotive.muscle_car",
        preset_snapshot={
            "id": "automotive.muscle_car",
            "name": "Classic Muscle Cars",
            "default_voice": "en-US-ChristopherNeural-Male",
            "tts_provider": "fake",
            "words_per_minute": 150,
            "pause_between_groups_ms": 300,
        },
        chapters=[
            Chapter(
                chapter_index=1,
                chapter_title="Birth of the Legend",
                tts_groups=[
                    TtsGroup(
                        group_index=1,
                        paragraph_text="The 1964 GTO was Pontiac's boldest bet. It transformed Detroit forever.",
                        segments=[
                            Segment(
                                segment_index=1,
                                narration_text="The 1964 GTO was Pontiac's boldest bet.",
                                visual=VisualSpec(prompt="1964 GTO profile"),
                            ),
                            Segment(
                                segment_index=2,
                                narration_text="It transformed Detroit forever.",
                                visual=VisualSpec(prompt="Detroit skyline 1960s"),
                            ),
                        ],
                    ),
                    TtsGroup(
                        group_index=2,
                        paragraph_text="Under the hood lay pure American horsepower.",
                        segments=[
                            Segment(
                                segment_index=1,
                                narration_text="Under the hood lay pure American horsepower.",
                                visual=VisualSpec(prompt="V8 engine bay"),
                            )
                        ],
                    ),
                ],
            )
        ],
    )
    save_manifest(manifest, project_dir)
    return manifest


def test_voice_synth_and_status(tmp_path: Path):
    create_dummy_manifest(tmp_path)
    service = VoiceService(project_dir=tmp_path)

    # Initial status
    status_before = service.get_audio_status()
    assert status_before["total_groups"] == 2
    assert status_before["completed_groups"] == 0
    assert not status_before["is_fully_synthesized"]

    # Synthesize with fake provider
    manifest = service.synthesize_all(provider="fake")
    assert manifest.chapters[0].tts_groups[0].status == ItemStatus.completed
    assert manifest.chapters[0].tts_groups[1].status == ItemStatus.completed

    # Status after synthesis
    status_after = service.get_audio_status()
    assert status_after["total_groups"] == 2
    assert status_after["completed_groups"] == 2
    assert status_after["is_fully_synthesized"]
    assert status_after["is_fake"] is True
    assert status_after["total_duration_seconds"] > 0


def test_resume_after_failure(tmp_path: Path):
    create_dummy_manifest(tmp_path)
    service = VoiceService(project_dir=tmp_path)

    # Inject failure at group 2
    with pytest.raises(RuntimeError, match="Simulated failure at audio unit ch1_g2_audio"):
        service.synthesize_all(provider="fake", fail_at_group="ch1_g2_audio")

    # Verify group 1 completed and saved, group 2 remains pending
    m1 = load_manifest(tmp_path)
    assert m1.chapters[0].tts_groups[0].status == ItemStatus.completed
    assert m1.chapters[0].tts_groups[1].status == ItemStatus.pending

    g1_wav = tmp_path / m1.chapters[0].tts_groups[0].audio.file_path
    assert g1_wav.is_file()
    mtime_g1 = g1_wav.stat().st_mtime_ns

    # Re-run synthesis without failure
    m2 = service.synthesize_all(provider="fake")
    assert m2.chapters[0].tts_groups[0].status == ItemStatus.completed
    assert m2.chapters[0].tts_groups[1].status == ItemStatus.completed

    # Verify group 1 was NOT re-synthesized (file mtime unchanged)
    assert g1_wav.stat().st_mtime_ns == mtime_g1


def test_set_voice_marks_audio_stale(tmp_path: Path):
    create_dummy_manifest(tmp_path)
    service = VoiceService(project_dir=tmp_path)
    service.synthesize_all(provider="fake")

    # Set new voice
    service.set_voice(voice_name="en-US-GuyNeural", voice_rate=1.1)

    m = load_manifest(tmp_path)
    assert m.preset_snapshot["default_voice"] == "en-US-GuyNeural"
    assert m.status == ItemStatus.stale
    assert m.chapters[0].tts_groups[0].status == ItemStatus.stale
    assert m.chapters[0].tts_groups[1].status == ItemStatus.stale


def test_preview_generation_and_srt(tmp_path: Path):
    create_dummy_manifest(tmp_path)
    service = VoiceService(project_dir=tmp_path)
    service.synthesize_all(provider="fake")

    wav_preview, srt_preview, mp4_preview = service.generate_preview(chapter_index=1)
    assert wav_preview.is_file()
    assert srt_preview.is_file()
    assert mp4_preview.is_file()

    srt_content = srt_preview.read_text(encoding="utf-8")
    assert "-->" in srt_content
    assert "The 1964 GTO was Pontiac's boldest bet." in srt_content
    assert "Under the hood lay pure American horsepower." in srt_content


def test_script_edit_one_sentence_isolation(tmp_path: Path):
    create_dummy_manifest(tmp_path)
    service = VoiceService(project_dir=tmp_path)
    service.synthesize_all(provider="fake")

    m = load_manifest(tmp_path)
    # Edit sentence in group 2
    m.chapters[0].tts_groups[1].segments[0].narration_text = "Under the hood lay 389 cubic inches of roaring muscle."
    save_manifest(m, tmp_path)

    plan = compute_plan(m, tmp_path)
    g1_unit = [u for u in plan.units if u.unit_id == "ch1_g1_audio"][0]
    g2_unit = [u for u in plan.units if u.unit_id == "ch1_g2_audio"][0]

    # Group 1 remains completed! Group 2 is stale!
    assert g1_unit.status == ItemStatus.completed
    assert g2_unit.status == ItemStatus.stale


def test_list_available_voices():
    voices = list_available_voices(language_prefix="en-US")
    assert len(voices) > 0
    assert any("ChristopherNeural" in v for v in voices)
