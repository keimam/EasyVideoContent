"""Comprehensive unit and integration tests for Stage 4: Visual Engine (4.1 - 4.6).

Covers:
- Visual source selection & fail-fast without API keys
- Fallback recording in manifest when --allow-fallback is active
- Candidate deduplication across project
- Global media cache hits across calls
- Shot formation: merge <2s, split >8s, carry-over frame precision ±1
- Ken Burns and stock video clip generation with uniform stream parameters (ffprobe)
- Visual shot replace, lock, and unlock
- Audio duration change invalidates shot hash (stale in planner)
- Local media library file copy (Rule K7 compliance, relative paths only)
- Automatic deduplicated attribution tracking in export/credits.txt
- Path-with-spaces compatibility for preview video concat
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.config import config
from app.services.longform.planner import compute_plan
from app.services.longform.schema import (
    AudioSpec,
    Chapter,
    EncoderSettings,
    ItemStatus,
    MotionType,
    ProjectManifest,
    Segment,
    Shot,
    TtsGroup,
    VisualSpec,
)
from app.services.longform.store import load_manifest, save_manifest
from app.services.longform.shot import (
    compute_shot_hash,
    form_chapter_shots,
)
from app.services.longform.visual.ai_image import AIImageProvider
from app.services.longform.visual.base import (
    VisualCandidate,
    VisualConfigurationError,
    compute_file_content_hash,
)
from app.services.longform.visual.builder import (
    ShotClipBuilder,
    verify_clip_stream_parameters,
)
from app.services.longform.visual.cache import GlobalMediaCache
from app.services.longform.visual.contact_sheet import generate_chapter_contact_sheet
from app.services.longform.visual.credits import CreditsManager
from app.services.longform.visual.fake import FakeVisualProvider
from app.services.longform.visual.local import LocalLibraryManager, LocalVisualProvider
from app.services.longform.visual.service import VisualService
from app.services.longform.visual.stock import StockVisualProvider


def test_visual_shot_formation_merge_split_and_frame_precision():
    """Verify shot formation merges <2s, splits >8s, and produces exact frame carry-over."""
    # Group with 1 short (1.2s), 1 normal (3.5s), 1 very long (12.0s)
    seg1 = Segment(
        segment_index=1,
        narration_text="Short sentence.",
        duration_seconds=1.2,
        chapter_start_seconds=0.0,
        chapter_end_seconds=1.2,
    )
    seg2 = Segment(
        segment_index=2,
        narration_text="Medium sentence.",
        duration_seconds=3.5,
        chapter_start_seconds=1.2,
        chapter_end_seconds=4.7,
    )
    seg3 = Segment(
        segment_index=3,
        narration_text="Very long descriptive storytelling sentence.",
        duration_seconds=12.0,
        chapter_start_seconds=4.7,
        chapter_end_seconds=16.7,
    )

    group = TtsGroup(group_index=1, paragraph_text="All sentences.", segments=[seg1, seg2, seg3])
    chapter = Chapter(
        chapter_index=1,
        chapter_title="Test Chapter",
        audio_duration_seconds=16.7,
        tts_groups=[group],
    )

    fps = 25
    shots = form_chapter_shots(chapter, min_shot_seconds=2.0, max_shot_seconds=8.0, fps=fps)

    # seg1 (1.2s) should be merged with seg2 (3.5s) -> 4.7s
    # seg3 (12.0s) should be split into 2 sub-shots (6.0s each)
    # Total shots expected = 1 (merged) + 2 (split) = 3 shots
    assert len(shots) == 3
    assert shots[0].segment_indices == [1, 2]
    assert pytest.approx(shots[0].duration_seconds, 0.05) == 4.7
    assert shots[1].segment_indices == [3]
    assert shots[2].segment_indices == [3]

    # Verify frame carry over: sum of shot target_frames == round(total_audio * fps)
    expected_total_frames = int(round(16.7 * fps))  # 418 frames
    total_shot_frames = sum(s.target_frames for s in shots)
    assert abs(total_shot_frames - expected_total_frames) <= 1
    assert chapter.visual_frames == total_shot_frames


def test_visual_provider_fail_fast_without_keys(monkeypatch):
    """Verify stock and AI providers fail fast when required API keys are missing."""
    monkeypatch.setitem(config.app, "pexels_api_keys", "")
    monkeypatch.setitem(config.app, "pixabay_api_keys", "")
    monkeypatch.setitem(config.app, "openai_image_base_url", "")
    monkeypatch.setitem(config.app, "openai_image_model", "")

    # Pexels provider
    stock_prov = StockVisualProvider(provider_name="pexels")
    with pytest.raises(VisualConfigurationError) as exc_pexels:
        stock_prov.validate_credentials()
    assert "Pexels API key is not configured" in str(exc_pexels.value)

    # Pixabay provider
    pix_prov = StockVisualProvider(provider_name="pixabay")
    with pytest.raises(VisualConfigurationError) as exc_pix:
        pix_prov.validate_credentials()
    assert "Pixabay API key is not configured" in str(exc_pix.value)

    # AI image provider
    ai_prov = AIImageProvider()
    with pytest.raises(VisualConfigurationError) as exc_ai:
        ai_prov.validate_credentials()
    assert "AI image provider credentials missing" in str(exc_ai.value)


def test_visual_fallback_recorded_in_manifest(tmp_path: Path, monkeypatch):
    """Verify --allow-fallback records fallback reason and falls back to fake provider."""
    monkeypatch.setitem(config.app, "pexels_api_keys", "")

    project_dir = tmp_path / "fallback_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    seg = Segment(segment_index=1, narration_text="Test scene.", duration_seconds=4.0)
    group = TtsGroup(group_index=1, paragraph_text="Test scene.", segments=[seg])
    ch = Chapter(chapter_index=1, chapter_title="Ch 1", audio_duration_seconds=4.0, tts_groups=[group])
    manifest = ProjectManifest(
        project_id="fallback_proj",
        title="Fallback Test",
        preset_id="documentary_en",
        preset_snapshot={},
        chapters=[ch],
    )
    save_manifest(manifest, project_dir)

    service = VisualService(project_dir)
    # Fetch with pexels but with allow_fallback=True
    service.fetch_all(manifest, provider_name="pexels", allow_fallback=True)

    shot = manifest.chapters[0].shots[0]
    assert shot.is_fake is True
    assert shot.actual_source == "fake"
    assert shot.fallback_reason is not None
    assert "credentials missing" in shot.fallback_reason
    assert (project_dir / shot.asset_path).is_file()


def test_visual_candidate_deduplication():
    """Verify candidate search deduplicates IDs already in excluded_ids set."""
    fake_prov = FakeVisualProvider(mode="stock_video")
    excluded = {"fake_stock_video_123_1"}

    candidates = fake_prov.search_candidates(
        query="muscle car",
        target_duration=5.0,
        limit=3,
        excluded_ids=excluded,
    )
    cand_ids = [c.candidate_id for c in candidates]
    assert "fake_stock_video_123_1" not in cand_ids


def test_global_media_cache(tmp_path: Path):
    """Verify GlobalMediaCache saves files and provides cache hits for identical URLs."""
    cache_dir = tmp_path / "global_cache"
    cache = GlobalMediaCache(cache_dir=cache_dir)

    # Create dummy local file to serve as "remote"
    source_file = tmp_path / "source.mp4"
    source_file.write_bytes(b"dummy video content 12345")

    # Mock download_or_get using local file copy
    test_url = "https://example.com/videos/test_clip_101.mp4"
    dest1 = tmp_path / "proj1" / "clip.mp4"
    dest2 = tmp_path / "proj2" / "clip.mp4"

    # Pre-populate cache directly
    cache_path = cache.get_cache_path_for_url(test_url)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_file, cache_path)
    assert cache.is_cached(test_url)

    # First fetch from cache
    cache.download_or_get(test_url, dest1)
    assert dest1.is_file()
    assert dest1.read_bytes() == b"dummy video content 12345"

    # Second fetch from cache for different project
    cache.download_or_get(test_url, dest2)
    assert dest2.is_file()
    assert dest2.read_bytes() == b"dummy video content 12345"


def test_shot_clip_builder_stream_uniformity(tmp_path: Path):
    """Verify Ken Burns animation and stock video trimming produce identical uniform streams."""
    builder = ShotClipBuilder(fps=25, width=1920, height=1080)

    # 1. Image -> Ken Burns clip
    img_path = tmp_path / "test_image.jpg"
    img = Image.new("RGB", (1920, 1080), color=(80, 120, 180))
    img.save(img_path)

    out_clip_img = tmp_path / "clip_kb.mp4"
    _, elapsed_kb = builder.build_image_clip(
        image_path=img_path,
        output_path=out_clip_img,
        motion=MotionType.zoom_in,
        target_frames=50,
    )
    assert out_clip_img.is_file()
    assert elapsed_kb > 0

    params_kb = verify_clip_stream_parameters(out_clip_img, expected_frames=50, expected_fps=25)
    assert params_kb["codec"] == "h264"
    assert params_kb["profile"] == "High"
    assert params_kb["pix_fmt"] in ("yuv420p", "yuvj420p")
    assert params_kb["resolution"] == "1920x1080"
    assert params_kb["read_frames"] == 50

    # 2. Synthetic video -> build_video_clip (with trim and slowdown check)
    out_clip_vid = tmp_path / "clip_vid.mp4"
    _, elapsed_vid = builder.build_video_clip(
        video_path=out_clip_img,  # use out_clip_img as input video
        output_path=out_clip_vid,
        target_frames=35,
    )
    assert out_clip_vid.is_file()
    params_vid = verify_clip_stream_parameters(out_clip_vid, expected_frames=35, expected_fps=25)
    assert params_vid["codec"] == params_kb["codec"]
    assert params_vid["profile"] == params_kb["profile"]
    assert params_vid["resolution"] == params_kb["resolution"]
    assert params_vid["read_frames"] == 35


def test_shot_replace_and_locking(tmp_path: Path):
    """Verify locking protects shot from re-fetch, and replacing invalidates rendered clip."""
    project_dir = tmp_path / "replace_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    seg = Segment(segment_index=1, narration_text="Test scene.", duration_seconds=3.0)
    group = TtsGroup(group_index=1, paragraph_text="Test scene.", segments=[seg])
    ch = Chapter(chapter_index=1, chapter_title="Ch 1", audio_duration_seconds=3.0, tts_groups=[group])
    manifest = ProjectManifest(
        project_id="replace_proj",
        title="Replace Test",
        preset_id="documentary_en",
        preset_snapshot={},
        chapters=[ch],
    )
    save_manifest(manifest, project_dir)

    service = VisualService(project_dir)
    service.fetch_all(manifest, provider_name="fake")
    shot = manifest.chapters[0].shots[0]
    initial_hash = shot.asset_content_hash

    # Lock shot
    service.set_lock(manifest, shot.shot_id, locked=True)
    assert shot.locked is True

    # Re-fetch should skip locked shot
    service.fetch_all(manifest, provider_name="fake")
    assert shot.asset_content_hash == initial_hash

    # Replace shot with a custom image file
    rep_img = tmp_path / "replacement.jpg"
    Image.new("RGB", (1920, 1080), color=(200, 50, 50)).save(rep_img)

    service.replace_shot(manifest, shot.shot_id, file_path=rep_img)
    assert shot.actual_source == "local"
    assert shot.asset_content_hash != initial_hash
    assert shot.shot_hash is None  # invalidated
    assert shot.status == ItemStatus.pending


def test_audio_duration_change_invalidates_shot_in_planner(tmp_path: Path):
    """Verify changing audio duration alters shot hash and marks visual_shot stale in planner."""
    project_dir = tmp_path / "stale_shot_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    builder = ShotClipBuilder(fps=25)
    img_path = tmp_path / "img.jpg"
    Image.new("RGB", (1920, 1080), color=(50, 50, 50)).save(img_path)

    clip_file = project_dir / "assets" / "visual" / "shots" / "ch01_shot001.mp4"
    clip_file.parent.mkdir(parents=True, exist_ok=True)
    builder.build_image_clip(img_path, clip_file, MotionType.zoom_in, target_frames=75)

    asset_hash = compute_file_content_hash(img_path)
    initial_shot_hash = compute_shot_hash(
        shot_id="ch01_shot001",
        duration_seconds=3.0,
        target_frames=75,
        motion=MotionType.zoom_in.value,
        asset_content_hash=asset_hash,
        fps=25,
        requested_provider="ai_image",
        actual_provider="ai_image",
    )

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        motion=MotionType.zoom_in,
        asset_path="assets/visual/shots/ch01_shot001_asset.jpg",
        asset_content_hash=asset_hash,
        rendered_clip="assets/visual/shots/ch01_shot001.mp4",
        shot_hash=initial_shot_hash,
        status=ItemStatus.completed,
    )
    ch = Chapter(chapter_index=1, chapter_title="Ch 1", shots=[shot])
    manifest = ProjectManifest(
        project_id="stale_shot_proj",
        title="Stale Shot Test",
        preset_id="documentary_en",
        preset_snapshot={},
        encoder_settings=EncoderSettings(fps=25.0),
        chapters=[ch],
    )

    # Initially completed
    plan = compute_plan(manifest, project_dir)
    shot_unit = [u for u in plan.all_units if u.unit_id == "ch01_shot001"][0]
    assert shot_unit.status == ItemStatus.completed

    # Change duration (e.g. audio was re-synthesized to 4.2s / 105 frames)
    shot.duration_seconds = 4.2
    shot.target_frames = 105

    plan_stale = compute_plan(manifest, project_dir)
    stale_unit = [u for u in plan_stale.all_units if u.unit_id == "ch01_shot001"][0]
    assert stale_unit.status == ItemStatus.stale
    assert "Shot hash mismatch" in stale_unit.reason


def test_local_media_library_copies_files(tmp_path: Path):
    """Verify local library copies external media into project root (Rule K7 compliance)."""
    project_dir = tmp_path / "lib_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    ext_folder = tmp_path / "external_source_media"
    ext_folder.mkdir(parents=True, exist_ok=True)
    img1 = ext_folder / "car1.jpg"
    img2 = ext_folder / "track.png"
    Image.new("RGB", (640, 480), color=(10, 20, 30)).save(img1)
    Image.new("RGB", (640, 480), color=(40, 50, 60)).save(img2)

    mgr = LocalLibraryManager(project_dir)
    added = mgr.add_from_folder(ext_folder)
    assert len(added) == 2

    # Verify files reside inside project
    lib_dir = project_dir / "assets" / "visual" / "library"
    assert (lib_dir / "car1.jpg").is_file()
    assert (lib_dir / "track.png").is_file()

    # Search with LocalVisualProvider
    prov = LocalVisualProvider(project_dir)
    cands = prov.search_candidates("car racing", target_duration=4.0)
    assert len(cands) >= 1
    # Check that candidate path is relative to project
    assert not os.path.isabs(cands[0].local_path)
    assert cands[0].local_path.startswith("assets/visual/library/")


def test_credits_manager_records_and_deduplicates(tmp_path: Path):
    """Verify CreditsManager formats citations and avoids duplicates in export/credits.txt."""
    mgr = CreditsManager(tmp_path)
    cand1 = VisualCandidate(
        candidate_id="pexels_12345",
        provider="pexels",
        source_type="stock_video",
        author="John Doe Filmmaker",
        author_url="https://pexels.com/@johndoe",
        source_url="https://pexels.com/video/12345",
        license="Pexels License",
    )

    mgr.record_asset_credit(cand1, shot_id="ch01_shot001")
    # Record again
    mgr.record_asset_credit(cand1, shot_id="ch01_shot002")

    credits_content = mgr.credits_file.read_text(encoding="utf-8")
    assert credits_content.count("[PEXELS] pexels_12345") == 1
    assert "John Doe Filmmaker" in credits_content
    assert "https://pexels.com/video/12345" in credits_content


def test_visual_contact_sheet_and_preview_in_spaced_path(tmp_path: Path):
    """Verify contact sheet and preview MP4 work properly in a directory with spaces."""
    spaced_project_dir = tmp_path / "My Longform Video 2026 Project"
    spaced_project_dir.mkdir(parents=True, exist_ok=True)

    builder = ShotClipBuilder(fps=25)
    img_path = spaced_project_dir / "temp_img.jpg"
    Image.new("RGB", (1920, 1080), color=(70, 90, 110)).save(img_path)

    # 1. Build clip 1
    clip1 = spaced_project_dir / "assets" / "visual" / "shots" / "ch01_shot001.mp4"
    clip1.parent.mkdir(parents=True, exist_ok=True)
    builder.build_image_clip(img_path, clip1, MotionType.zoom_in, target_frames=50)

    # 2. Build clip 2
    clip2 = spaced_project_dir / "assets" / "visual" / "shots" / "ch01_shot002.mp4"
    builder.build_image_clip(img_path, clip2, MotionType.pan_right, target_frames=50)

    # 3. Create dummy 48kHz audio file via ffmpeg
    audio_file = spaced_project_dir / "assets" / "audio" / "chapters" / "chapter_01.wav"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "sine=frequency=440:duration=4.0",
        "-ar", "48000",
        "-ac", "1",
        str(audio_file),
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    shot1 = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=2.0,
        target_frames=50,
        motion=MotionType.zoom_in,
        rendered_clip="assets/visual/shots/ch01_shot001.mp4",
        status=ItemStatus.completed,
    )
    shot2 = Shot(
        shot_id="ch01_shot002",
        chapter_index=1,
        shot_index=2,
        duration_seconds=2.0,
        target_frames=50,
        motion=MotionType.pan_right,
        rendered_clip="assets/visual/shots/ch01_shot002.mp4",
        status=ItemStatus.completed,
    )

    ch = Chapter(
        chapter_index=1,
        chapter_title="Spaced Path Chapter",
        audio_file="assets/audio/chapters/chapter_01.wav",
        audio_duration_seconds=4.0,
        shots=[shot1, shot2],
        visual_duration_seconds=4.0,
        visual_frames=100,
    )
    manifest = ProjectManifest(
        project_id="spaced_proj",
        title="Spaced Path Test",
        preset_id="documentary_en",
        preset_snapshot={},
        chapters=[ch],
    )
    save_manifest(manifest, spaced_project_dir)

    # Generate contact sheet
    cs_img = generate_chapter_contact_sheet(ch, spaced_project_dir)
    assert cs_img.is_file()
    assert cs_img.stat().st_size > 0

    # Generate visual preview MP4
    service = VisualService(spaced_project_dir)
    preview_mp4 = service.generate_preview(manifest, chapter_index=1)
    assert preview_mp4.is_file()
    assert preview_mp4.stat().st_size > 0


def test_v1_fail_fast_without_keys_and_assets_preserved(tmp_path: Path, monkeypatch):
    """V1: Without API keys, fetch fails fast with clear error and preserves existing assets."""
    monkeypatch.setitem(config.app, "pixabay_api_keys", "")
    monkeypatch.setitem(config.app, "pexels_api_keys", "")
    monkeypatch.setitem(config.app, "coverr_api_keys", "")
    monkeypatch.setitem(config.app, "openai_image_base_url", "")

    proj_dir = tmp_path / "v1_failfast_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    # Place an existing asset file
    asset_dir = proj_dir / "assets" / "visual" / "shots"
    asset_dir.mkdir(parents=True, exist_ok=True)
    existing_file = asset_dir / "ch01_shot001_asset.jpg"
    existing_file.write_bytes(b"EXISTING_ASSET_BYTES_12345")
    existing_hash = compute_file_content_hash(existing_file)

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        requested_source="pixabay",
        actual_source="fake",
        is_fake=True,
        asset_path="assets/visual/shots/ch01_shot001_asset.jpg",
        asset_content_hash=existing_hash,
        status=ItemStatus.completed,
    )
    ch = Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot])
    manifest = ProjectManifest(project_id="v1_test", title="V1 Test", preset_id="documentary_en", preset_snapshot={}, chapters=[ch])
    save_manifest(manifest, proj_dir)

    service = VisualService(proj_dir)

    # Calling fetch without provider (resolves to pixabay/pexels) must raise VisualConfigurationError
    with pytest.raises(VisualConfigurationError) as exc_info:
        service.fetch_all(manifest, provider_name=None, allow_fallback=False)

    err_msg = str(exc_info.value)
    assert "not properly configured" in err_msg or "API key is not configured" in err_msg
    # Ensure error guidance mentions alternatives
    assert "Pixabay" in err_msg or "Pexels" in err_msg or "Coverr" in err_msg

    # Verify existing asset was not modified or deleted
    assert existing_file.is_file()
    assert existing_file.read_bytes() == b"EXISTING_ASSET_BYTES_12345"


def test_v1_fake_assets_stale_when_real_provider_requested(tmp_path: Path):
    """V1: Fake assets are considered stale when a real provider is requested."""
    proj_dir = tmp_path / "v1_stale_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    asset_dir = proj_dir / "assets" / "visual" / "shots"
    asset_dir.mkdir(parents=True, exist_ok=True)
    fake_asset = asset_dir / "ch01_shot001_asset.jpg"
    fake_asset.write_bytes(b"FAKE_IMAGE_DATA")
    fake_hash = compute_file_content_hash(fake_asset)

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        requested_source="fake",
        actual_source="fake",
        is_fake=True,
        asset_path="assets/visual/shots/ch01_shot001_asset.jpg",
        asset_content_hash=fake_hash,
        status=ItemStatus.completed,
    )

    service = VisualService(proj_dir)

    # Real provider requested -> fake asset is NOT valid (stale)
    assert not service._is_shot_asset_valid_for_provider(shot, "pixabay")
    assert not service._is_shot_asset_valid_for_provider(shot, "pexels")
    assert not service._is_shot_asset_valid_for_provider(shot, "local")

    # Fake provider requested -> fake asset IS valid
    assert service._is_shot_asset_valid_for_provider(shot, "fake")

    # Switching real providers: asset fetched from pexels is stale if pixabay is requested
    shot.is_fake = False
    shot.actual_source = "pexels"
    assert not service._is_shot_asset_valid_for_provider(shot, "pixabay")
    assert service._is_shot_asset_valid_for_provider(shot, "pexels")


def test_v2_actual_frame_count_probed_from_clips(tmp_path: Path):
    """V2: Chapter actual frames are probed from real clip files using ffprobe."""
    proj_dir = tmp_path / "v2_frame_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    builder = ShotClipBuilder(fps=25)
    img_path = proj_dir / "base.jpg"
    Image.new("RGB", (1920, 1080), color=(50, 60, 70)).save(img_path)

    clip1 = proj_dir / "assets" / "visual" / "shots" / "ch01_shot001.mp4"
    clip2 = proj_dir / "assets" / "visual" / "shots" / "ch01_shot002.mp4"
    clip1.parent.mkdir(parents=True, exist_ok=True)

    # Build clip 1 with 25 frames, clip 2 with 50 frames
    builder.build_image_clip(img_path, clip1, MotionType.static, target_frames=25)
    builder.build_image_clip(img_path, clip2, MotionType.static, target_frames=50)

    shot1 = Shot(
        shot_id="ch01_shot001", chapter_index=1, shot_index=1,
        duration_seconds=1.0, target_frames=25,
        rendered_clip="assets/visual/shots/ch01_shot001.mp4",
        status=ItemStatus.completed,
    )
    shot2 = Shot(
        shot_id="ch01_shot002", chapter_index=1, shot_index=2,
        duration_seconds=2.0, target_frames=50,
        rendered_clip="assets/visual/shots/ch01_shot002.mp4",
        status=ItemStatus.completed,
    )
    ch = Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot1, shot2])

    service = VisualService(proj_dir)
    total_probed = service.get_chapter_actual_frames(ch)
    assert total_probed == 75


def test_v3_set_voice_hash_based_invalidation_roundtrip(tmp_path: Path):
    """V3: Changing voice A -> B -> A restores completed status without re-synthesis."""
    from app.services.longform.voice_service import VoiceService

    proj_dir = tmp_path / "v3_voice_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    group_audio_dir = proj_dir / "assets" / "audio" / "groups"
    group_audio_dir.mkdir(parents=True, exist_ok=True)
    wav_file = group_audio_dir / "ch1_g1.wav"
    wav_file.write_bytes(b"RIFF" + b"\x00" * 100)  # dummy wav > 44 bytes

    voice_a = "en-US-ChristopherNeural"
    voice_b = "en-US-GuyNeural"

    # Compute expected hash for voice A
    from app.services.longform.hashing import compute_audio_group_hash
    hash_a = compute_audio_group_hash(
        paragraph_text="This is a test paragraph.",
        tts_provider="edge",
        voice_name=voice_a,
        voice_rate=1.0,
    )

    group = TtsGroup(
        group_index=1,
        paragraph_text="This is a test paragraph.",
        audio=AudioSpec(
            file_path="assets/audio/groups/ch1_g1.wav",
            audio_hash=hash_a,
            voice_name=voice_a,
            voice_rate=1.0,
            duration_seconds=2.5,
        ),
        status=ItemStatus.completed,
    )
    ch = Chapter(chapter_index=1, chapter_title="Ch1", tts_groups=[group], status=ItemStatus.completed)
    manifest = ProjectManifest(
        project_id="v3_test",
        title="V3 Test",
        preset_id="documentary_en",
        preset_snapshot={"default_voice": voice_a, "voice_rate": 1.0},
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    v_service = VoiceService(proj_dir)

    # 1. Switch voice A -> B: group should become stale
    v_service.set_voice(voice_b, voice_rate=1.0)
    m_after_b = load_manifest(proj_dir)
    assert m_after_b.chapters[0].tts_groups[0].status == ItemStatus.stale
    assert m_after_b.chapters[0].tts_groups[0].audio.voice_name == voice_b

    # 2. Switch voice B -> A: group hash matches again, should become completed without synth!
    v_service.set_voice(voice_a, voice_rate=1.0)
    m_after_a = load_manifest(proj_dir)
    assert m_after_a.chapters[0].tts_groups[0].status == ItemStatus.completed
    assert m_after_a.chapters[0].tts_groups[0].audio.voice_name == voice_a
    assert m_after_a.chapters[0].tts_groups[0].audio.audio_hash == hash_a


def test_v6_local_provider_real_source_and_credits(tmp_path: Path):
    """V6: Local files added to library work as real non-fake source with credits recorded."""
    proj_dir = tmp_path / "v6_local_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    # Create local source media folder
    media_folder = tmp_path / "user_footage"
    media_folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1920, 1080), color=(100, 150, 200)).save(media_folder / "nature_scene.jpg")

    # Add to library
    mgr = LocalLibraryManager(proj_dir)
    mgr.add_from_folder(media_folder)

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        visual_prompt="nature scene outdoors",
        search_query="nature scene",
    )
    ch = Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot])
    manifest = ProjectManifest(project_id="v6_test", title="V6 Test", preset_id="documentary_en", preset_snapshot={}, chapters=[ch])
    save_manifest(manifest, proj_dir)

    service = VisualService(proj_dir)
    manifest = service.fetch_all(manifest, provider_name="local")

    updated_shot = manifest.chapters[0].shots[0]
    # Must NOT be fake
    assert updated_shot.is_fake is False
    assert updated_shot.actual_source == "local"
    assert updated_shot.asset_path is not None
    assert (proj_dir / updated_shot.asset_path).is_file()

    # Verify credits recorded for local asset
    credits_file = proj_dir / "export" / "credits.txt"
    assert credits_file.is_file()
    credits_text = credits_file.read_text(encoding="utf-8")
    assert "[LOCAL]" in credits_text
    assert "Local User Library" in credits_text
