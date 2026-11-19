"""Tests for Stage 8C.2 Addendum D8: Eliminate 'fake' placeholders in installed/normal mode."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services.longform.schema import (
    CURRENT_SCHEMA_VERSION,
    AudioSpec,
    Chapter,
    ItemStatus,
    Segment,
    Shot,
    TtsGroup,
)
from app.services.longform.status import compute_project_step_status
from app.services.longform.store import ProjectStoreError, create_project, load_manifest, save_manifest
from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualConfigurationError,
    inspect_project_missing_visuals,
    is_dev_or_test_mode,
)
from app.services.longform.visual.builder import ShotClipBuilder, verify_clip_stream_parameters
from app.services.longform.visual.service import VisualService
from scripts.build.verify_package import check_default_configs_no_fake, verify_staging_dir


def _get_long_video_app_file() -> str:
    return str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")


def _make_sample_jpg(path: Path, color=(35, 90, 140)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (640, 360), color=color)
    img.save(path, format="JPEG")
    return path


def test_d8_fake_provider_gated_strictly_to_dev_or_test_mode(tmp_path, monkeypatch):
    """Fake provider must raise in normal/installed mode even if allow_fallback=True."""
    manifest, p_dir = create_project("d8_gate", "Gate Test", "automotive.muscle_car", projects_root=tmp_path)
    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1",
        audio_duration_seconds=4.0,
        shots=[
            Shot(
                shot_id="ch01_shot001",
                chapter_index=1,
                shot_index=1,
                duration_seconds=4.0,
                target_frames=100,
                visual_prompt="Classic muscle car cruising down the highway.",
                search_query="muscle car highway",
                search_query_en="classic muscle car highway",
                search_query_source="ai",
            )
        ],
    )
    manifest.chapters = [ch]
    manifest.preset_snapshot = dict(manifest.preset_snapshot or {}, preferred_visual_source="fake")
    save_manifest(manifest, p_dir)

    monkeypatch.delenv("EFS_DEV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setitem(config.app, "pixabay_api_keys", [])
    monkeypatch.setitem(config.app, "pexels_api_keys", [])
    monkeypatch.setitem(config.app, "coverr_api_keys", [])

    assert is_dev_or_test_mode() is False
    svc = VisualService(p_dir)
    assert svc._resolve_default_provider(manifest) != "fake"

    with pytest.raises(VisualConfigurationError):
        svc.get_provider("fake", manifest)

    with pytest.raises(VisualConfigurationError):
        svc.fetch_all(manifest, provider_name="fake", allow_fallback=True, translate_keywords=False)

    with pytest.raises(VisualConfigurationError):
        svc.fetch_all(manifest, provider_name="pexels", allow_fallback=True, translate_keywords=False)

    # Explicit dev mode re-enables fake provider cleanly
    monkeypatch.setenv("EFS_DEV", "1")
    assert is_dev_or_test_mode() is True
    assert svc.get_provider("fake", manifest).is_fake is True


def test_d8_single_shot_search_failure_marks_missing_visual_not_fake(tmp_path, monkeypatch):
    """If 1 shot returns 0 candidates in normal mode, it stays 'Belum ada visual' while valid shots still build."""
    manifest, p_dir = create_project("d8_partial", "Partial Visual", "automotive.muscle_car", projects_root=tmp_path)
    sample_img = _make_sample_jpg(tmp_path / "sample_real.jpg")

    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1",
        audio_duration_seconds=4.0,
        shots=[
            Shot(
                shot_id="ch01_shot001",
                chapter_index=1,
                shot_index=1,
                duration_seconds=2.0,
                target_frames=50,
                visual_prompt="Unique_shot1 roaring V8 engine under neon lights.",
                search_query="unique_shot1 neon",
                search_query_en="unique_shot1 car neon",
                search_query_source="ai",
            ),
            Shot(
                shot_id="ch01_shot002",
                chapter_index=1,
                shot_index=2,
                duration_seconds=2.0,
                target_frames=50,
                visual_prompt="Empty obscure conceptual feeling with no footage.",
                search_query="obscure empty shot",
                search_query_en="obscure conceptual empty scene",
                search_query_source="ai",
            ),
        ],
    )
    manifest.chapters = [ch]
    save_manifest(manifest, p_dir)

    class _PartialRealProvider(BaseVisualProvider):
        @property
        def name(self) -> str:
            return "pixabay"

        @property
        def is_fake(self) -> bool:
            return False

        def validate_credentials(self) -> None:
            return None

        def search_candidates(self, query: str, target_duration: float, limit: int = 3, **kwargs):
            excluded = kwargs.get("excluded_ids") or set()
            if "unique_shot1" in query.lower() and "pix_real_1" not in excluded:
                return [
                    VisualCandidate(
                        candidate_id="pix_real_1",
                        provider="pixabay",
                        source_type="stock_image",
                        local_path=str(sample_img),
                        width=640,
                        height=360,
                        relevance_score=0.92,
                        tags=["car", "neon"],
                    )
                ]
            return []

        def fetch_asset(self, candidate: VisualCandidate, dest_path: Path) -> VisualAsset:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(sample_img.read_bytes())
            return VisualAsset(
                asset_id=candidate.candidate_id,
                provider="pixabay",
                source_type="stock_image",
                local_path=dest_path,
                content_hash="realhash123",
            )

    monkeypatch.delenv("EFS_DEV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    svc = VisualService(p_dir)
    monkeypatch.setattr(svc, "get_provider", lambda name, m: _PartialRealProvider())

    manifest, stats = svc.run_pipeline(
        manifest=manifest,
        provider_name="pixabay",
        allow_fallback=True,  # must still NOT invoke FakeVisualProvider in normal mode!
    )

    s1, s2 = manifest.chapters[0].shots
    assert s1.is_fake is False
    assert s1.rendered_clip and (p_dir / s1.rendered_clip).is_file()

    assert s2.is_fake is False
    assert s2.asset_path is None
    assert s2.rendered_clip is None
    assert s2.actual_source is None
    assert s2.fallback_reason == "no_visual_found"
    assert s2.needs_review is True
    assert stats.get("missing_asset_count", 0) == 1

    diag = inspect_project_missing_visuals(p_dir, manifest)
    assert diag["missing_count"] == 1
    assert diag["legacy_fake_count"] == 0
    assert diag["unready_count"] == 1


def test_d8_neutral_backdrop_clip_builder_and_legacy_fake_replacement(tmp_path):
    """Neutral backdrop clip builds clean H.264 stream with no debug text and repairs legacy fake shots."""
    out_clip = tmp_path / "neutral.mp4"
    _, elapsed = ShotClipBuilder(fps=25).build_neutral_backdrop_clip(out_clip, target_frames=25)
    assert elapsed >= 0.0
    assert out_clip.is_file()
    verify_clip_stream_parameters(out_clip, expected_frames=25, expected_fps=25)

    manifest, p_dir = create_project("d8_neutral", "Neutral Test", "automotive.muscle_car", projects_root=tmp_path)
    fake_clip = p_dir / "assets" / "visual" / "shots" / "ch01_shot002.mp4"
    fake_clip.parent.mkdir(parents=True, exist_ok=True)
    fake_clip.write_bytes(b"fake_bytes")
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Ch 1",
            audio_duration_seconds=2.0,
            shots=[
                Shot(
                    shot_id="ch01_shot001",
                    chapter_index=1,
                    shot_index=1,
                    duration_seconds=1.0,
                    target_frames=25,
                    visual_prompt="Shot missing visual",
                    fallback_reason="no_visual_found",
                ),
                Shot(
                    shot_id="ch01_shot002",
                    chapter_index=1,
                    shot_index=2,
                    duration_seconds=1.0,
                    target_frames=25,
                    visual_prompt="Legacy fake shot",
                    rendered_clip="assets/visual/shots/ch01_shot002.mp4",
                    is_fake=True,
                    actual_source="fake",
                ),
            ],
        )
    ]
    save_manifest(manifest, p_dir)

    diag_before = inspect_project_missing_visuals(p_dir, manifest)
    assert diag_before["missing_count"] == 1
    assert diag_before["legacy_fake_count"] == 1
    assert diag_before["unready_count"] == 2

    svc = VisualService(p_dir)
    manifest, build_stats = svc.build_all(manifest, use_neutral_fallback=True)
    assert build_stats["neutral_built_count"] == 2

    for s in manifest.chapters[0].shots:
        assert s.is_fake is False
        assert s.actual_source == "neutral_backdrop"
        assert s.rendered_clip and (p_dir / s.rendered_clip).is_file()

    diag_after = inspect_project_missing_visuals(p_dir, manifest)
    assert diag_after["unready_count"] == 0


@pytest.mark.parametrize("lang", ["id", "en"])
def test_d8_no_source_warning_and_block_in_visual_and_auto(tmp_path, monkeypatch, lang):
    """When no Pixabay/Pexels/local/AI source is configured in normal mode, Visual & Otomatis warn and disable start."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", lang)
    monkeypatch.setitem(config.app, "pixabay_api_keys", [])
    monkeypatch.setitem(config.app, "pexels_api_keys", [])
    monkeypatch.setitem(config.app, "coverr_api_keys", [])
    monkeypatch.setitem(config.app, "openai_image_api_key", "")
    monkeypatch.setitem(config.app, "codex_image_enabled", False)
    monkeypatch.delenv("EFS_DEV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    pid = f"d8_nosrc_{lang}"
    manifest, p_dir = create_project(pid, "No Visual Key", "automotive.muscle_car", projects_root=tmp_path)
    wav_file = p_dir / "assets" / "audio" / "groups" / "ch1_g1.wav"
    wav_file.parent.mkdir(parents=True, exist_ok=True)
    wav_file.write_bytes(b"RIFF" + b"\x00" * 100)
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Ch 1",
            audio_duration_seconds=3.0,
            tts_groups=[TtsGroup(group_index=1, paragraph_text="Narration.", audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=3.0))],
            shots=[],
        )
    ]
    save_manifest(manifest, p_dir)

    at = AppTest.from_file(_get_long_video_app_file(), default_timeout=25)
    at.query_params["project"] = pid
    at.session_state[f"active_step_{pid}"] = "visual"
    # D9.2: explicitly uncheck all free keyless providers so ZERO visual sources are enabled
    at.session_state[f"cb_prov_wikimedia_{pid}"] = False
    at.session_state[f"cb_prov_nasa_{pid}"] = False
    at.session_state[f"cb_prov_openverse_{pid}"] = False
    at.session_state[f"cb_prov_local_{pid}"] = False
    at.run()
    assert not at.exception

    warns = " ".join(w.value for w in at.warning)
    if lang == "id":
        assert "Isi API key Pexels/Pixabay di API & Penyedia AI" in warns
        assert any("Buka Pengaturan Penyedia" in (b.label or "") for b in at.button)
    else:
        assert "Add a Pexels/Pixabay API key" in warns
        assert any("Open Provider Settings" in (b.label or "") for b in at.button)

    vis_btn = [b for b in at.button if b.key == f"btn_start_vis_{pid}"][0]
    assert vis_btn.disabled is True
    auto_btn = [b for b in at.button if b.key == f"btn_start_auto_{pid}"][0]
    assert auto_btn.disabled is True


@pytest.mark.parametrize("lang", ["id", "en"])
def test_d8_render_and_capcut_confirmation_prompt_bilingual(tmp_path, monkeypatch, lang):
    """Render & CapCut confirm when any shot lacks visual: Search again vs Continue (--use-neutral-fallback)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", lang)

    pid = f"d8_confirm_{lang}"
    manifest, p_dir = create_project(pid, "Confirm Visual", "automotive.muscle_car", projects_root=tmp_path)
    wav_file = p_dir / "assets" / "audio" / "groups" / "ch1_g1.wav"
    wav_file.parent.mkdir(parents=True, exist_ok=True)
    wav_file.write_bytes(b"RIFF" + b"\x00" * 100)
    clip1 = p_dir / "assets" / "visual" / "shots" / "ch01_shot001.mp4"
    clip1.parent.mkdir(parents=True, exist_ok=True)
    clip1.write_bytes(b"ftypisom" + b"\x00" * 150)

    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Ch 1",
            audio_duration_seconds=4.0,
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Shot one ready. Warung tenda malam tanpa footage.",
                    audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=4.0),
                    segments=[
                        Segment(segment_index=1, narration_text="Shot one ready"),
                        Segment(segment_index=2, narration_text="Warung tenda malam tanpa footage"),
                    ],
                )
            ],
            shots=[
                Shot(
                    shot_id="ch01_shot001",
                    chapter_index=1,
                    shot_index=1,
                    segment_indices=[1],
                    duration_seconds=2.0,
                    target_frames=50,
                    rendered_clip="assets/visual/shots/ch01_shot001.mp4",
                    actual_source="pixabay",
                    status=ItemStatus.completed,
                ),
                Shot(
                    shot_id="ch01_shot002",
                    chapter_index=1,
                    shot_index=2,
                    segment_indices=[2],
                    duration_seconds=2.0,
                    target_frames=50,
                    fallback_reason="no_visual_found",
                    needs_review=True,
                ),
            ],
        )
    ]
    save_manifest(manifest, p_dir)

    # 1. Visual gallery displays missing badge and filter
    at_v = AppTest.from_file(_get_long_video_app_file(), default_timeout=25)
    at_v.query_params["project"] = pid
    at_v.session_state[f"active_step_{pid}"] = "visual"
    at_v.run()
    assert not at_v.exception
    cap_txt = " ".join(c.value for c in at_v.caption)
    if lang == "id":
        assert "Belum ada visual" in cap_txt
    else:
        assert "No visual yet" in cap_txt

    # 2. Render step confirmation -> Search again first jumps to visual
    at_r = AppTest.from_file(_get_long_video_app_file(), default_timeout=25)
    at_r.query_params["project"] = pid
    at_r.session_state[f"active_step_{pid}"] = "render"
    at_r.run()
    assert not at_r.exception

    rnd_btn = [b for b in at_r.button if b.key == f"btn_render_main_{pid}"][0]
    assert rnd_btn.disabled is False
    rnd_btn.click().run()
    assert not at_r.exception

    search_btn = [b for b in at_r.button if b.key == f"btn_render_search_first_{pid}"][0]
    cont_btn = [b for b in at_r.button if b.key == f"btn_render_continue_neutral_{pid}"][0]
    assert search_btn.proto.type == "primary"
    assert cont_btn.proto.type == "secondary"
    md_text = " ".join(m.value for m in at_r.markdown)
    assert "ch01_shot002" in md_text
    assert "Warung tenda malam tanpa footage" in md_text

    search_btn.click().run()
    assert at_r.session_state[f"active_step_{pid}"] == "visual"

    # 3. Continue launches --use-neutral-fallback
    at_r.session_state[f"active_step_{pid}"] = "render"
    at_r.session_state[f"render_missing_confirm_{pid}"] = True
    at_r.run()
    launched = []
    with patch("app.services.longform.ui.components._start_job_safe", side_effect=lambda *a, **kw: launched.append(a[2])):
        cont_btn2 = [b for b in at_r.button if b.key == f"btn_render_continue_neutral_{pid}"][0]
        cont_btn2.click().run()
    assert launched and "--use-neutral-fallback" in launched[0]

    # 4. CapCut step also triggers missing visual confirmation & passes --use-neutral-fallback
    at_c = AppTest.from_file(_get_long_video_app_file(), default_timeout=25)
    at_c.query_params["project"] = pid
    at_c.session_state[f"active_step_{pid}"] = "capcut"
    with patch(
        "app.services.longform.capcut.detector.get_capcut_doctor_report",
        return_value={"installed": True, "installed_version": "8.9.0", "template_version": "8.9.0", "is_running": False, "draft_folder": str(tmp_path)},
    ):
        at_c.run()
        cc_btn = [b for b in at_c.button if b.key == f"btn_capcut_main_{pid}"][0]
        assert cc_btn.disabled is False
        cc_btn.click().run()
        assert [b for b in at_c.button if b.key == f"btn_capcut_search_first_{pid}"][0].proto.type == "primary"
        launched_cc = []
        with patch("app.services.longform.ui.components._start_job_safe", side_effect=lambda *a, **kw: launched_cc.append(a[2])):
            [b for b in at_c.button if b.key == f"btn_capcut_continue_neutral_{pid}"][0].click().run()
        assert launched_cc and "--use-neutral-fallback" in launched_cc[0]


def test_d8_verify_package_rejects_fake_in_default_configs(tmp_path):
    """verify_package must fail if packaged defaults enable 'fake' or allow_fallback=true."""
    repo_root = Path(__file__).resolve().parents[2]
    assert check_default_configs_no_fake(repo_root) == []

    # Clean temp staging defaults pass
    (tmp_path / "config").mkdir()
    (tmp_path / "resource" / "presets").mkdir(parents=True)
    (tmp_path / "config.example.toml").write_text('[app]\npixabay_api_keys = []\n', encoding="utf-8")
    (tmp_path / "config" / "longform.toml").write_text('[visual]\npreferred_visual_source = "auto"\n', encoding="utf-8")
    (tmp_path / "resource" / "presets" / "sample.json").write_text(json.dumps({"id": "sample", "preferred_visual_source": "auto"}), encoding="utf-8")
    assert verify_staging_dir(tmp_path, check_required=False) == []

    # Mutate each to activate 'fake' or allow_fallback=true -> must fail
    (tmp_path / "config.example.toml").write_text('[app]\npreferred_visual_source = "fake"\n', encoding="utf-8")
    assert any("config.example.toml" in e for e in verify_staging_dir(tmp_path, check_required=False))
    (tmp_path / "config.example.toml").write_text('[app]\npixabay_api_keys = []\n', encoding="utf-8")

    (tmp_path / "config" / "longform.toml").write_text('[visual]\nallow_fallback = true\n', encoding="utf-8")
    assert any("config/longform.toml" in e for e in verify_staging_dir(tmp_path, check_required=False))
    (tmp_path / "config" / "longform.toml").write_text('[visual]\npreferred_visual_source = "auto"\n', encoding="utf-8")

    (tmp_path / "resource" / "presets" / "sample.json").write_text(json.dumps({"id": "sample", "preferred_visual_source": "fake"}), encoding="utf-8")
    assert any("sample.json" in e for e in verify_staging_dir(tmp_path, check_required=False))


def test_d8_1_roundtrip_missing_visual_neutral_and_legacy_fake_shots(tmp_path):
    """D8.1: Save -> load round-trip preserves missing visual, neutral_backdrop, legacy fake, and status counts."""
    manifest, p_dir = create_project("d81_roundtrip", "Roundtrip Test", "automotive.muscle_car", projects_root=tmp_path)
    shots_dir = p_dir / "assets" / "visual" / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    (shots_dir / "ch01_shot002.mp4").write_bytes(b"ftypisom" + b"\x00" * 120)
    (shots_dir / "ch01_shot003.mp4").write_bytes(b"ftypisom" + b"\x00" * 120)

    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Ch 1",
            shots=[
                Shot(
                    shot_id="ch01_shot001",
                    chapter_index=1,
                    shot_index=1,
                    duration_seconds=3.0,
                    target_frames=75,
                    requested_source="pixabay",
                    actual_source=None,
                    asset_path=None,
                    rendered_clip=None,
                    fallback_reason="no_visual_found",
                    is_fake=False,
                    needs_review=True,
                ),
                Shot(
                    shot_id="ch01_shot002",
                    chapter_index=1,
                    shot_index=2,
                    duration_seconds=3.0,
                    target_frames=75,
                    requested_source="pixabay",
                    actual_source="neutral_backdrop",
                    asset_path=None,
                    rendered_clip="assets/visual/shots/ch01_shot002.mp4",
                    fallback_reason="neutral_backdrop_confirmed",
                    status=ItemStatus.completed,
                ),
                Shot(
                    shot_id="ch01_shot003",
                    chapter_index=1,
                    shot_index=3,
                    duration_seconds=3.0,
                    target_frames=75,
                    requested_source="pixabay",
                    actual_source="fake",
                    is_fake=True,
                    rendered_clip="assets/visual/shots/ch01_shot003.mp4",
                    status=ItemStatus.completed,
                ),
            ],
        )
    ]
    save_manifest(manifest, p_dir)
    loaded = load_manifest(p_dir)
    assert loaded.schema_version == CURRENT_SCHEMA_VERSION
    s1, s2, s3 = loaded.chapters[0].shots
    assert s1.actual_source is None and s1.asset_path is None and s1.rendered_clip is None
    assert s2.actual_source == "neutral_backdrop" and s2.is_fake is False
    assert s3.actual_source == "fake" and s3.is_fake is True

    st = compute_project_step_status(p_dir)
    vis = st["steps"]["visual"]
    assert vis["total"] == 3
    assert vis["built_shots"] == 2
    assert vis["missing_visual_shots"] == 1
    assert vis["legacy_fake_shots"] == 1
    assert vis["unready_visual_shots"] == 2
    assert vis["status"] == "terhenti"


def test_d8_1_load_legacy_1_0_0_manifest_with_null_actual_source_pattern(tmp_path):
    """D8.1: Exact bug pattern (schema 1.0.0 with actual_source=None on shots 7..23) loads without manual fixes."""
    manifest, p_dir = create_project("d81_bugfix", "New York Bridge", "documentary_en", projects_root=tmp_path)
    shots_dir = p_dir / "assets" / "visual" / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    raw = manifest.model_dump(mode="json")
    raw["schema_version"] = "1.0.0"

    raw_shots = []
    for i in range(24):
        sid = f"ch01_shot{i + 1:03d}"
        if i < 7:
            (shots_dir / f"{sid}_asset.mp4").write_bytes(b"asset" * 30)
            (shots_dir / f"{sid}.mp4").write_bytes(b"ftypisom" + b"\x00" * 100)
            raw_shots.append({
                "shot_id": sid,
                "chapter_index": 1,
                "shot_index": i + 1,
                "duration_seconds": 3.0,
                "target_frames": 75,
                "requested_source": "pixabay",
                "actual_source": "pixabay",
                "asset_path": f"assets/visual/shots/{sid}_asset.mp4",
                "rendered_clip": f"assets/visual/shots/{sid}.mp4",
                "status": "completed",
            })
        else:
            raw_shots.append({
                "shot_id": sid,
                "chapter_index": 1,
                "shot_index": i + 1,
                "duration_seconds": 3.0,
                "target_frames": 75,
                "requested_source": "pixabay",
                "actual_source": None,
                "asset_path": None,
                "rendered_clip": None,
                "fallback_reason": "no_visual_found",
                "visual_prompt": None,
                "tags": None,
                "is_fake": False,
                "needs_review": True,
                "status": "pending",
            })
    raw["chapters"] = [{"chapter_index": 1, "chapter_title": "Chapter 1", "shots": raw_shots}]
    (p_dir / "manifest.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")

    loaded = load_manifest(p_dir)
    assert loaded.schema_version == CURRENT_SCHEMA_VERSION
    assert len(loaded.chapters[0].shots) == 24
    assert loaded.chapters[0].shots[7].actual_source is None
    assert loaded.chapters[0].shots[23].fallback_reason == "no_visual_found"

    st = compute_project_step_status(p_dir)
    assert st["steps"]["visual"]["built_shots"] == 7
    assert st["steps"]["visual"]["missing_visual_shots"] == 17
    assert st["steps"]["visual"]["status"] == "terhenti"


def test_d8_1_save_manifest_atomic_validation_preserves_existing_file(tmp_path):
    """D8.1: Failed model validation during save_manifest must abort before os.replace and preserve old manifest."""
    manifest, p_dir = create_project("d81_atomic", "Atomic Valid Save", "automotive.muscle_car", projects_root=tmp_path)
    original_bytes = (p_dir / "manifest.json").read_bytes()

    # Corrupt model via direct assignment on non-validated attribute or construct bad object
    object.__setattr__(manifest, "schema_version", "99.0.0")
    with pytest.raises(ProjectStoreError) as exc_info:
        save_manifest(manifest, p_dir)
    assert "Manifest validation failed before saving" in str(exc_info.value)

    # No orphaned temp files and existing manifest.json untouched
    assert list(p_dir.glob("manifest.json.tmp.*")) == []
    assert (p_dir / "manifest.json").read_bytes() == original_bytes


@pytest.mark.parametrize("lang", ["id", "en"])
def test_d8_1_ui_friendly_load_error_corrupt_manifest_bilingual(tmp_path, monkeypatch, lang):
    """D8.1: Corrupt/incompatible manifest shows friendly message, suggestion, Copy button, and expander traceback."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", lang)

    pid = f"d81_broken_{lang}"
    manifest, p_dir = create_project(pid, "Broken Project", "automotive.muscle_car", projects_root=tmp_path)
    data = manifest.model_dump(mode="json")
    data["schema_version"] = "99.0.0"
    (p_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    # Home page surfaces broken project warning without crashing
    at_home = AppTest.from_file(_get_long_video_app_file(), default_timeout=25)
    at_home.run()
    assert not at_home.exception
    home_warns = " ".join(w.value for w in at_home.warning)
    if lang == "id":
        assert "tidak bisa dibuka" in home_warns
    else:
        assert "could not be opened" in home_warns

    # Direct project page renders friendly error + Copy details + technical expander
    at_proj = AppTest.from_file(_get_long_video_app_file(), default_timeout=25)
    at_proj.query_params["project"] = pid
    at_proj.run()
    assert not at_proj.exception

    errs = " ".join(e.value for e in at_proj.error)
    infos = " ".join(i.value for i in at_proj.info)
    code_blocks = " ".join(c.value for c in at_proj.code)
    assert "Unsupported manifest schema version" in code_blocks
    if lang == "id":
        assert "data rusak atau versi skema lama" in errs
        assert "Saran:" in infos
        copy_btn = [b for b in at_proj.button if b.key == f"btn_copy_load_err_{pid}"][0]
        assert "Salin detail" in copy_btn.label
    else:
        assert "data is corrupted or uses an incompatible legacy schema" in errs
        assert "Suggestion:" in infos
        copy_btn = [b for b in at_proj.button if b.key == f"btn_copy_load_err_{pid}"][0]
        assert "Copy details" in copy_btn.label

    with patch("app.services.longform.ui.components._copy_text_to_clipboard_best_effort", return_value=True):
        copy_btn.click().run()
        assert at_proj.session_state[f"copy_load_err_{pid}"] is True

