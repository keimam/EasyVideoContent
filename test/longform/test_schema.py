"""Tests for schema models, relative path enforcement (K7), and schema versioning."""

import pytest
from pydantic import ValidationError

from app.services.longform.schema import (
    AudioSpec,
    Chapter,
    EncoderSettings,
    ItemStatus,
    MotionType,
    Preset,
    ProjectManifest,
    Segment,
    TtsGroup,
    VisualSourceType,
    VisualSpec,
)


def test_manifest_roundtrip_serialization():
    """Verify that ProjectManifest cleanly serializes to JSON and deserializes back."""
    manifest = ProjectManifest(
        pipeline_version="1.0.0",
        schema_version="1.0.0",
        project_id="test_roundtrip",
        title="Test Project",
        aspect_ratio="16:9",
        preset_id="documentary_en",
        preset_snapshot={"name": "Test Preset", "language": "en-US"},
        encoder_settings=EncoderSettings(video_encoder="libx264", resolution=(1920, 1080), fps=30.0),
        chapters=[
            Chapter(
                chapter_index=1,
                chapter_title="Chapter 1",
                output_file="assets/chapters/chapter_1.mp4",
                tts_groups=[
                    TtsGroup(
                        group_index=1,
                        paragraph_text="First test paragraph.",
                        audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.mp3"),
                        segments=[
                            Segment(
                                segment_index=1,
                                narration_text="First test paragraph.",
                                visual=VisualSpec(
                                    source_file="assets/visual/raw/img1.png",
                                    rendered_clip="assets/visual/clips/ch1_s1.mp4",
                                ),
                            )
                        ],
                    )
                ],
            )
        ],
        status=ItemStatus.pending,
        final_video_file="output/final_video.mp4",
    )

    serialized = manifest.model_dump_json()
    deserialized = ProjectManifest.model_validate_json(serialized)

    assert deserialized.project_id == manifest.project_id
    assert deserialized.chapters[0].chapter_title == "Chapter 1"
    assert deserialized.chapters[0].tts_groups[0].segments[0].visual.rendered_clip == "assets/visual/clips/ch1_s1.mp4"
    assert deserialized.final_video_file == "output/final_video.mp4"


@pytest.mark.parametrize(
    "abs_path",
    [
        "C:/users/test/video.mp4",
        "C:\\users\\test\\video.mp4",
        "D:/media/clip.mp4",
        "/usr/local/share/clip.mp4",
        "\\network_share\\file.mp4",
    ],
)
def test_manifest_rejects_absolute_paths_in_visual_spec(abs_path):
    """Verify that absolute paths in VisualSpec are rejected by validator (K7)."""
    with pytest.raises(ValidationError) as exc_info:
        VisualSpec(rendered_clip=abs_path)
    assert "must be a relative path to the project directory" in str(exc_info.value)


@pytest.mark.parametrize(
    "abs_path",
    [
        "C:/audio/test.mp3",
        "/var/tmp/audio.mp3",
        "\\share\\audio.mp3",
    ],
)
def test_manifest_rejects_absolute_paths_in_audio_spec(abs_path):
    """Verify that absolute paths in AudioSpec are rejected (K7)."""
    with pytest.raises(ValidationError) as exc_info:
        AudioSpec(file_path=abs_path)
    assert "must be a relative path" in str(exc_info.value)


def test_manifest_rejects_absolute_paths_in_chapter_and_manifest():
    """Verify that Chapter and ProjectManifest reject absolute output paths."""
    with pytest.raises(ValidationError):
        Chapter(chapter_index=1, chapter_title="Ch1", output_file="C:/test/ch1.mp4")

    with pytest.raises(ValidationError):
        ProjectManifest(
            project_id="test",
            title="test",
            preset_id="test",
            preset_snapshot={},
            final_video_file="/root/output.mp4",
        )


def test_manifest_rejects_unsupported_schema_version():
    """Verify that unknown schema versions are rejected with a clear message."""
    with pytest.raises(ValidationError) as exc_info:
        ProjectManifest(
            schema_version="99.0.0",
            project_id="test_ver",
            title="Test",
            preset_id="documentary_en",
            preset_snapshot={},
        )
    assert "Unsupported manifest schema version" in str(exc_info.value)
