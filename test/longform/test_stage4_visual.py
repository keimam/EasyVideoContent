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
    sample_img = Path("test/resources/sample_image.png")
    if sample_img.is_file():
        fake_asset.write_bytes(sample_img.read_bytes())
        shot.asset_content_hash = compute_file_content_hash(fake_asset)
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


# ---------------------------------------------------------------------------
# Stage 4.2 Tests (S1, S2, S3)
# ---------------------------------------------------------------------------

def test_s1_relevance_scoring_and_terms_filtering():
    """S1: Candidates without required subject terms or containing blocked terms are rejected."""
    from app.services.longform.visual.stock import score_candidate_relevance

    req_terms = ["car", "automobile", "vehicle", "muscle car"]
    blocked_terms = ["accident", "crash", "wreck"]
    query = "classic muscle car rumbling highway"
    visual_kws = ["panning car tracking shot", "engine bay closeup"]

    # 1. Irrelevant candidate: highway only, no car
    valid, score, reason = score_candidate_relevance(
        candidate_tags=["highway", "road", "asphalt", "driving", "sky"],
        search_query=query,
        visual_keywords=visual_kws,
        required_subject_terms=req_terms,
        blocked_terms=blocked_terms,
    )
    assert valid is False
    assert score == 0.0
    assert "lacks required subject terms" in reason

    # 2. Blocked term present
    valid, score, reason = score_candidate_relevance(
        candidate_tags=["car", "muscle car", "highway", "crash"],
        search_query=query,
        visual_keywords=visual_kws,
        required_subject_terms=req_terms,
        blocked_terms=blocked_terms,
    )
    assert valid is False
    assert score == 0.0
    assert "Blocked term" in reason

    # 3. Relevant candidate with high keyword match
    valid, score, reason = score_candidate_relevance(
        candidate_tags=["classic", "muscle car", "car", "highway", "rumbling"],
        search_query=query,
        visual_keywords=visual_kws,
        required_subject_terms=req_terms,
        blocked_terms=blocked_terms,
    )
    assert valid is True
    assert score >= 0.70
    assert reason == "Accepted"

    # 4. Relevant candidate with lower keyword match
    valid2, score2, _ = score_candidate_relevance(
        candidate_tags=["vehicle", "outdoors", "transportation"],
        search_query=query,
        visual_keywords=visual_kws,
        required_subject_terms=req_terms,
        blocked_terms=blocked_terms,
    )
    assert valid2 is True
    assert score2 < score  # Exact match scores higher than generic vehicle


def test_s1_search_candidates_highest_score_chosen(tmp_path: Path):
    """S1: Candidates are ranked and chosen by highest relevance score, not first hit."""
    cand_low = VisualCandidate(
        candidate_id="cand_low",
        provider="fake",
        source_type="ai_image",
        tags=["vehicle", "transportation"],
        width=1920,
        height=1080,
    )
    cand_high = VisualCandidate(
        candidate_id="cand_high",
        provider="fake",
        source_type="ai_image",
        tags=["classic", "muscle car", "highway", "car"],
        width=1920,
        height=1080,
    )
    cand_irrelevant = VisualCandidate(
        candidate_id="cand_irrelevant",
        provider="fake",
        source_type="ai_image",
        tags=["mountains", "clouds", "sky"],
        width=1920,
        height=1080,
    )

    fake_prov = FakeVisualProvider(mock_candidates=[cand_low, cand_high, cand_irrelevant])
    results = fake_prov.search_candidates(
        query="classic muscle car rumbling highway",
        target_duration=4.0,
        limit=3,
        required_subject_terms=["car", "muscle car", "vehicle"],
    )

    # Cand irrelevant must be excluded
    assert all(c.candidate_id != "cand_irrelevant" for c in results)
    # Cand high must be top (rank 0) despite being listed after cand_low in mock list
    assert len(results) >= 2
    assert results[0].candidate_id == "cand_high"
    assert results[0].relevance_score > results[1].relevance_score


def test_s1_all_candidates_rejected_marks_shot_needs_review(tmp_path: Path):
    """S1: When all candidates fail relevance checks, shot is marked needs_review (no silent irrelevant clips)."""
    proj_dir = tmp_path / "s1_review_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=4.0,
        target_frames=100,
        visual_prompt="vintage muscle car racing on asphalt",
        search_query="muscle car race",
    )
    ch = Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot])
    manifest = ProjectManifest(
        project_id="s1_review",
        title="S1 Review Test",
        preset_id="automotive",
        preset_snapshot={
            "required_subject_terms": ["car", "automobile", "muscle car"],
            "blocked_terms": [],
            "visual_keywords": ["panning car tracking shot"],
        },
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    service = VisualService(proj_dir)

    # Use FakeVisualProvider with mock pool containing only irrelevant tags
    irrelevant_pool = [["forest", "trees", "river"], ["sky", "clouds", "sunshine"]]
    fake_prov = FakeVisualProvider(mode="stock_video", mock_tags_pool=irrelevant_pool)

    # Monkeypatch get_provider to return fake_prov
    service.get_provider = lambda p, m: fake_prov  # type: ignore

    manifest = service.fetch_all(manifest, provider_name="fake", allow_fallback=False)
    updated_shot = manifest.chapters[0].shots[0]

    # Verify shot is marked needs_review and NOT completed with an irrelevant asset
    assert updated_shot.needs_review is True
    assert updated_shot.status == ItemStatus.pending
    assert updated_shot.relevance_score == 0.0


def test_s2_source_video_trimming(tmp_path: Path):
    """S2: Source video asset longer than needed duration is trimmed during build to save project storage."""
    from app.services.longform.visual.builder import trim_source_video_asset, _get_video_duration

    # Create a 10-second test video
    src_video = tmp_path / "source_long.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "testsrc=duration=10:size=640x360:rate=25",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(src_video),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    initial_dur = _get_video_duration(src_video)
    assert initial_dur >= 9.9

    # Trim to 4.0 seconds needed duration
    trimmed = trim_source_video_asset(src_video, needed_duration_seconds=4.0)
    assert trimmed is True
    final_dur = _get_video_duration(src_video)
    assert 3.8 <= final_dur <= 4.5


def test_s2_cache_lru_pruning_protects_locked_assets(tmp_path: Path):
    """S2: LRU cache pruning evicts old unlocked files while strictly protecting locked shot assets."""
    import time
    from app.services.longform.visual.cache import prune_cache, get_cache_status

    cache_dir = tmp_path / "test_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    proj_dir = tmp_path / "storage" / "longform_projects" / "test_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    # Create dummy cached files:
    # 1. Locked file: matches locked candidate url
    test_url = "https://example.com/locked_video_clip_123"
    import hashlib
    url_hash = hashlib.sha256(test_url.encode("utf-8")).hexdigest()[:24]
    file_locked = cache_dir / f"cache_{url_hash}.mp4"
    file_locked.write_bytes(b"L" * 1024 * 1024)  # 1 MB
    # Set old access time
    now = time.time()
    os.utime(file_locked, (now - 86400 * 60, now - 86400 * 60))  # 60 days old

    # 2. Old unlocked file (60 days old, 2 MB)
    file_old = cache_dir / "cache_oldunlocked123456789012.mp4"
    file_old.write_bytes(b"O" * (2 * 1024 * 1024))
    os.utime(file_old, (now - 86400 * 60, now - 86400 * 60))

    # 3. Recent unlocked file (1 day old, 2 MB)
    file_recent = cache_dir / "cache_recentunlocked1234567.mp4"
    file_recent.write_bytes(b"R" * (2 * 1024 * 1024))
    os.utime(file_recent, (now - 86400 * 1, now - 86400 * 1))

    # Save manifest with locked shot referencing test_url
    locked_shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        locked=True,
        candidates=[{"url": test_url}],
        asset_path="assets/visual/shots/ch01_shot001_asset.mp4",
    )
    ch = Chapter(chapter_index=1, chapter_title="Ch1", shots=[locked_shot])
    manifest = ProjectManifest(
        project_id="test_proj",
        title="Test",
        preset_id="documentary_en",
        preset_snapshot={},
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    # Prune files older than 30 days, pointing locked references scan to proj_dir.parent
    import app.services.longform.visual.cache as c_mod
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(c_mod, "get_repo_root", lambda: tmp_path)

    res = prune_cache(older_than_days=30, cache_dir=cache_dir)

    # File 2 (old unlocked) must be removed
    assert not file_old.exists()
    # File 1 (locked) must NOT be removed despite being 60 days old!
    assert file_locked.exists()
    # File 3 (recent unlocked) must be preserved
    assert file_recent.exists()
    assert res["files_removed"] == 1


def test_s3_set_voice_cli_accurate_summary(tmp_path: Path):
    """S3: set_voice CLI calculates and reports exact stale vs retained valid counts."""
    from app.services.longform.voice_service import VoiceService
    from app.services.longform.schema import TtsGroup, AudioSpec

    proj_dir = tmp_path / "s3_voice_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    g1 = TtsGroup(
        group_index=1,
        paragraph_text="First group test.",
        audio=AudioSpec(voice_name="en-US-ChristopherNeural", audio_hash="hash1"),
        status=ItemStatus.completed,
    )
    g2 = TtsGroup(
        group_index=2,
        paragraph_text="Second group test.",
        audio=AudioSpec(voice_name="en-US-ChristopherNeural", audio_hash="hash2"),
        status=ItemStatus.completed,
    )
    ch = Chapter(chapter_index=1, chapter_title="Ch1", tts_groups=[g1, g2])
    manifest = ProjectManifest(
        project_id="s3_proj",
        title="S3 Project",
        preset_id="documentary_en",
        preset_snapshot={"default_voice": "en-US-ChristopherNeural", "voice_rate": 1.0},
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    service = VoiceService(proj_dir)
    # Switch to a new voice (which has no matching audio on disk) -> all 2 become stale
    manifest = service.set_voice("en-US-EricNeural", voice_rate=1.0)

    total_groups = sum(len(c.tts_groups) for c in manifest.chapters)
    stale_groups = sum(sum(1 for g in c.tts_groups if g.status == ItemStatus.stale) for c in manifest.chapters)
    valid_groups = total_groups - stale_groups

    assert total_groups == 2
    assert stale_groups == 2
    assert valid_groups == 0



def test_s4_invalid_or_non_decodable_asset_marked_stale(tmp_path: Path):
    """S4: Non-decodable media files and empty-tag assets under required terms are marked stale."""
    from app.services.longform.visual.service import VisualService
    from app.services.longform.visual.base import compute_file_content_hash

    proj_dir = tmp_path / "s4_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = proj_dir / "assets" / "visual" / "shots"
    asset_dir.mkdir(parents=True, exist_ok=True)

    # 1. Corrupt/empty file (0 bytes)
    corrupt_file = asset_dir / "corrupt_asset.mp4"
    corrupt_file.write_bytes(b"not a valid mp4 header or empty")
    h_corrupt = compute_file_content_hash(corrupt_file)

    shot_corrupt = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        asset_path="assets/visual/shots/corrupt_asset.mp4",
        asset_content_hash=h_corrupt,
        actual_source="pixabay",
        is_fake=False,
        tags=["car", "automobile"],
    )

    service = VisualService(proj_dir)
    # Must fail decodability check
    is_valid = service._is_shot_asset_valid_for_provider(
        shot_corrupt,
        target_provider_name="pixabay",
        required_subject_terms=["car", "automobile"],
    )
    assert is_valid is False

    # 2. Shot with missing/empty tags when required_subject_terms are configured
    # Even if valid video, without tags it cannot be verified against required subjects
    sample_clip = Path("test/resources/sample_clip_a.mp4")
    if sample_clip.is_file():
        valid_dest = asset_dir / "valid_clip.mp4"
        valid_dest.write_bytes(sample_clip.read_bytes())
        h_valid = compute_file_content_hash(valid_dest)

        shot_no_tags = Shot(
            shot_id="ch01_shot002",
            chapter_index=1,
            shot_index=2,
            asset_path="assets/visual/shots/valid_clip.mp4",
            asset_content_hash=h_valid,
            actual_source="pixabay",
            is_fake=False,
            tags=[],  # Empty tags!
        )
        is_valid_empty_tags = service._is_shot_asset_valid_for_provider(
            shot_no_tags,
            target_provider_name="pixabay",
            required_subject_terms=["car", "automobile"],
        )
        assert is_valid_empty_tags is False


def test_s4_contact_sheet_missing_or_invalid_asset_red_banner(tmp_path: Path):
    """S4: Contact sheet displays MISSING/INVALID ASSET card with red border when thumbnail fails."""
    from app.services.longform.visual.contact_sheet import _extract_or_load_thumbnail, generate_chapter_contact_sheet

    proj_dir = tmp_path / "s4_contact_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    # Shot with non-existent asset
    shot = Shot(
        shot_id="ch01_shot099",
        chapter_index=1,
        shot_index=1,
        asset_path="assets/visual/shots/missing_file.mp4",
        actual_source="pixabay",
        is_fake=False,
    )

    thumb_img, is_valid = _extract_or_load_thumbnail(shot, proj_dir, thumb_w=400, thumb_h=225)
    assert is_valid is False
    # Check that image was created with red border / warning
    assert thumb_img.size == (400, 225)

    ch = Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot])
    sheet_p = generate_chapter_contact_sheet(ch, proj_dir)
    assert sheet_p.is_file()
    assert sheet_p.stat().st_size > 1000


def test_s5_fixtures_whole_word_matching_and_relevance():
    """S5: Exact user fixtures tested for whole-word boundary matching and relevance scoring."""
    from app.services.longform.visual.stock import (
        score_candidate_relevance,
        term_matches_tags,
        restructure_stock_query,
    )

    req_terms = ["car", "automobile", "vehicle", "muscle car", "hot rod", "race car"]
    blocked = ["lava lamp", "cartoon", "animation", "toy"]

    fixtures = [
        (["lava lamp", "lamp", "light", "wax"], False),
        (["ocean", "sea", "wave"], False),
        (["tiger", "animal"], False),
        (["flower", "nature"], False),
        (["red carpet", "event"], False),
        (["car", "muscle car", "vintage"], True),
        (["automobile", "highway", "road"], True),
    ]

    for tags, should_accept in fixtures:
        is_valid, score, reason = score_candidate_relevance(
            candidate_tags=tags,
            search_query="classic muscle car rumbling highway",
            required_subject_terms=req_terms,
            blocked_terms=blocked,
        )
        assert is_valid == should_accept, f"Fixture failed for tags {tags}: got {is_valid}, reason={reason}"
        if should_accept:
            assert score > 0.0

    # Test whole-word boundary (car does NOT match carpet or cartoon)
    assert term_matches_tags("car", ["muscle car"]) is True
    assert term_matches_tags("car", ["cars"]) is True
    assert term_matches_tags("car", ["red carpet"]) is False
    assert term_matches_tags("car", ["cartoon"]) is False

    # Test query restructuring (2-3 words, subject first, concrete)
    q1 = restructure_stock_query("classic muscle car rumbling highway", req_terms)
    assert q1 == "muscle car highway"

    q2 = restructure_stock_query("dual four barrel engine bay carburetor", req_terms)
    assert q2 == "car engine carburetor"

    q3 = restructure_stock_query("dragstrip burnout tire smoke slow motion", req_terms)
    assert q3 == "car dragstrip burnout"


def test_s6_resolution_aspect_ratio_and_low_res_penalty():
    """S6: Renditions with width < 1920 or extreme ratios get low_res penalty and flag."""
    from app.services.longform.visual.stock import score_candidate_relevance

    req_terms = ["car"]
    tags = ["car", "highway"]

    # High res (1920x1080)
    valid_hr, score_hr, _ = score_candidate_relevance(
        candidate_tags=tags,
        search_query="car highway",
        required_subject_terms=req_terms,
        is_low_res=False,
    )
    # Low res (e.g. 1280x720)
    valid_lr, score_lr, _ = score_candidate_relevance(
        candidate_tags=tags,
        search_query="car highway",
        required_subject_terms=req_terms,
        is_low_res=True,
    )

    assert valid_hr is True
    assert valid_lr is True
    assert score_lr < score_hr  # Low res must have reduced score
    assert round(score_hr - score_lr, 2) == 0.20


def test_s7_quality_checker_flags_excessive_search_keywords(tmp_path: Path):
    """S7: Quality checker marks [search: ...] queries that exceed 4 words."""
    from app.services.longform.quality_checker import ScriptQualityChecker
    from app.services.longform.schema import Segment, VisualSpec

    proj_dir = tmp_path / "s7_quality_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    script_dir = proj_dir / "script"
    script_dir.mkdir(parents=True, exist_ok=True)

    seg_good = Segment(
        segment_index=1,
        narration_text="The 1964 GTO launched an era.",
        visual=VisualSpec(search_keywords="muscle car highway"),
    )
    seg_bad = Segment(
        segment_index=2,
        narration_text="Tire smoke billowed across the strip.",
        visual=VisualSpec(search_keywords="slow motion tire smoke billowing from wide rear radials"),  # 9 words!
    )
    ch = Chapter(chapter_index=1, chapter_title="Genesis", tts_groups=[
        TtsGroup(group_index=1, paragraph_text="Para", segments=[seg_good, seg_bad])
    ])
    manifest = ProjectManifest(
        project_id="s7_proj",
        title="S7 Test",
        preset_id="automotive",
        preset_snapshot={"name": "Automotive"},
        chapters=[ch],
    )

    save_manifest(manifest, proj_dir)
    checker = ScriptQualityChecker(project_dir=proj_dir)
    res = checker.inspect()

    excessive = res.get("excessive_search_queries", [])
    assert len(excessive) == 1
    assert excessive[0]["segment"] == 2
    assert excessive[0]["word_count"] == 9

    # Check that report markdown contains section 8
    report_text = checker.report_file.read_text(encoding="utf-8")
    assert "Stock Search Query Quality" in report_text
    assert "slow motion tire smoke billowing" in report_text



def test_t3_fixture_dubrovnik_ch01_shot002_rejected():
    """T3: Verify candidate tags from ch01_shot002 (Dubrovnik) are rejected (needs_review).

    Because 'cars' is not in the first 5 tags, only 1 subject term exists,
    and heavy negative terms (sea, city, port, bay, ocean, etc.) penalize score.
    """
    from app.services.longform.visual.stock import score_candidate_relevance

    dubrovnik_tags = [
        "dubrovnik", "sunset", "sea", "city", "timelapse", "time lapse", "summer",
        "croatia", "europe", "on the water", "lighting", "artificial light", "port",
        "landscape", "bay", "ocean", "hill", "islands", "by the sea", "quickly",
        "clouds", "colored clouds", "orange clouds", "darkness", "dusk", "fading light",
        "outlook", "boats", "ships", "skyscraper", "house", "cars", "fast movement",
    ]
    req_terms = ["car", "automobile", "vehicle", "muscle car", "hot rod", "race car"]
    neg_terms = [
        "sea", "ocean", "beach", "bay", "port", "boat", "ship", "island", "coast",
        "river", "lake", "waterfall", "flower", "garden", "field", "meadow", "forest",
        "park", "mountain", "landscape", "skyline", "city", "wind turbine", "office", "people"
    ]

    is_valid, score, reason = score_candidate_relevance(
        candidate_tags=dubrovnik_tags,
        search_query="dual four barrel engine bay carburetor",
        required_subject_terms=req_terms,
        negative_terms=neg_terms,
        min_relevance_score=0.35,
    )
    assert is_valid is False
    assert "lacks required subject terms" in reason or score < 0.35


def test_t3_fixture_alps_road_car_passes_with_penalty():
    """T3: 'road, highway, drive, car, alps, aerial, landscape' -> car in first 5 tags, passes with penalty."""
    from app.services.longform.visual.stock import score_candidate_relevance

    tags = ["road", "highway", "drive", "car", "alps", "aerial", "landscape"]
    req_terms = ["car", "automobile", "vehicle", "muscle car", "hot rod", "race car"]
    neg_terms = ["sea", "ocean", "beach", "bay", "port", "boat", "ship", "island", "mountain", "landscape"]

    is_valid, score, reason = score_candidate_relevance(
        candidate_tags=tags,
        search_query="classic muscle car rumbling highway",
        required_subject_terms=req_terms,
        negative_terms=neg_terms,
        min_relevance_score=0.35,
    )
    assert is_valid is True
    assert score >= 0.35
    assert reason == "Accepted"


def test_t3_fixture_high_relevance_muscle_car_passes_high_score():
    """T3: 'car, vehicle, automobile, muscle car' -> passes with high score."""
    from app.services.longform.visual.stock import score_candidate_relevance

    tags = ["car", "vehicle", "automobile", "muscle car"]
    req_terms = ["car", "automobile", "vehicle", "muscle car", "hot rod", "race car"]

    is_valid, score, reason = score_candidate_relevance(
        candidate_tags=tags,
        search_query="classic muscle car rumbling highway",
        required_subject_terms=req_terms,
        min_relevance_score=0.35,
    )
    assert is_valid is True
    assert score >= 0.70
    assert reason == "Accepted"


def test_t3_ambiguous_engine_bay_does_not_credit_bay_when_negative_terms_present():
    """T3: Query 'bay' from 'engine bay' does not credit candidate when negative terms present."""
    from app.services.longform.visual.stock import score_candidate_relevance

    harbor_tags = ["dubrovnik", "port", "bay", "sea", "ocean", "car", "cars"]
    req_terms = ["car", "automobile", "vehicle", "muscle car"]
    neg_terms = ["bay", "port", "sea", "ocean"]

    is_valid, score, reason = score_candidate_relevance(
        candidate_tags=harbor_tags,
        search_query="dual four barrel engine bay carburetor",
        required_subject_terms=req_terms,
        negative_terms=neg_terms,
        min_relevance_score=0.35,
    )
    assert score < 0.35
    assert is_valid is False


def test_t4_visual_replace_preview_grid_and_auto_lock_and_build(tmp_path: Path):
    """T4: visual replace displays candidate grid, selects candidate, auto-locks and auto-rebuilds."""
    from app.services.longform.visual.fake import FakeVisualProvider
    from app.services.longform.visual.service import VisualService
    from PIL import Image

    proj_dir = tmp_path / "t4_replace_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    # Create dummy image asset
    assets_dir = proj_dir / "assets" / "visual" / "shots"
    assets_dir.mkdir(parents=True, exist_ok=True)
    img1 = assets_dir / "ch01_shot001_asset.jpg"
    Image.new("RGB", (1920, 1080), color=(100, 120, 140)).save(img1)

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        start_seconds=0.0,
        end_seconds=4.0,
        duration_seconds=4.0,
        target_frames=100,
        requested_source="fake",
        actual_source="fake",
        search_query="classic muscle car rumbling highway",
        asset_path="assets/visual/shots/ch01_shot001_asset.jpg",
        asset_content_hash=compute_file_content_hash(img1),
        status=ItemStatus.pending,
    )
    ch = Chapter(chapter_index=1, chapter_title="Intro", shots=[shot])
    manifest = ProjectManifest(
        project_id="t4_proj",
        title="T4 Project",
        preset_id="automotive",
        preset_snapshot={"name": "Automotive"},
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    service = VisualService(proj_dir)

    # 1. Preview candidates
    cands, grid_path = service.search_and_preview_candidates(
        manifest=manifest,
        shot_id="ch01_shot001",
        provider_name="fake",
        limit=5,
    )
    assert len(cands) == 5
    assert grid_path.is_file()
    assert len(manifest.chapters[0].shots[0].candidates) == 5

    # 2. Select candidate 2 -> should replace, auto-lock, and auto-build
    updated_shot = service.replace_shot(
        manifest=manifest,
        shot_id="ch01_shot001",
        candidate_index=2,
        rebuild=True,
    )

    assert updated_shot.locked is True
    assert updated_shot.needs_review is False
    assert updated_shot.status == ItemStatus.completed
    assert (proj_dir / updated_shot.rendered_clip).is_file()


def test_t3_locked_shot_never_overwritten_by_new_rules(tmp_path: Path):
    """T3: Locked shot is protected and never marked stale or overwritten by new relevance rules."""
    from app.services.longform.visual.service import VisualService
    proj_dir = tmp_path / "locked_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    assets_dir = proj_dir / "assets" / "visual" / "shots"
    assets_dir.mkdir(parents=True, exist_ok=True)
    asset_file = assets_dir / "ch01_shot001_asset.jpg"
    Image.new("RGB", (1920, 1080), color=(80, 80, 80)).save(asset_file)
    asset_hash = compute_file_content_hash(asset_file)

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        requested_source="pixabay",
        actual_source="pixabay",
        is_fake=False,
        locked=True,  # LOCKED!
        tags=["dubrovnik", "sunset", "sea", "city", "port", "bay", "boats", "cars"],
        relevance_score=0.10,
        asset_path="assets/visual/shots/ch01_shot001_asset.jpg",
        asset_content_hash=asset_hash,
        status=ItemStatus.completed,
    )
    ch = Chapter(chapter_index=1, chapter_title="Intro", shots=[shot])
    manifest = ProjectManifest(
        project_id="locked_test",
        title="Locked Test",
        preset_id="automotive",
        preset_snapshot={
            "required_subject_terms": ["car", "automobile"],
            "negative_terms": ["sea", "port", "bay"],
            "min_relevance_score": 0.35,
        },
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    service = VisualService(proj_dir)
    assert service._is_shot_asset_valid_for_provider(
        shot,
        "pixabay",
        required_subject_terms=["car"],
        negative_terms=["sea"],
        min_relevance_score=0.35,
    ) is True

    # fetch_all must skip locked shot without altering it
    updated_manifest = service.fetch_all(manifest, provider_name="pixabay", allow_fallback=False)
    assert updated_manifest.chapters[0].shots[0].locked is True
    assert updated_manifest.chapters[0].shots[0].asset_content_hash == asset_hash
    assert updated_manifest.chapters[0].shots[0].tags == ["dubrovnik", "sunset", "sea", "city", "port", "bay", "boats", "cars"]

