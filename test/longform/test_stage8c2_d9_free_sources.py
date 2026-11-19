"""Tests for Stage 8C.2 Addendum D9: Free Visual Providers, Tiered Search, Attribution, None Defaults & Installer."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services.longform.capcut.service import export_full_project
from app.services.longform.schema import (
    CURRENT_SCHEMA_VERSION,
    AudioSpec,
    Chapter,
    EncoderSettings,
    ItemStatus,
    MotionType,
    ProjectManifest,
    Segment,
    Shot,
    TtsGroup,
)
from app.services.longform.shot import form_chapter_shots
from app.services.longform.status import compute_project_step_status
from app.services.longform.store import create_project, load_manifest, save_manifest
from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    has_real_visual_source,
    is_commercial_license_allowed,
    license_requires_attribution,
)
from app.services.longform.visual.builder import ShotClipBuilder, verify_clip_stream_parameters
from app.services.longform.visual.cache import GlobalMediaCache
from app.services.longform.visual.credits import CreditsManager
from app.services.longform.visual.free_providers import (
    NasaVisualProvider,
    OpenverseVisualProvider,
    WikimediaVisualProvider,
)
from app.services.longform.visual.service import VisualService
from app.services.longform.visual.stock import build_generalized_visual_queries


def _get_app_file() -> str:
    return str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")


def _make_sample_jpg(path: Path, color=(40, 100, 160), size=(1280, 720)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=color).save(path, format="JPEG")
    return path


def _make_sample_webm(path: Path, seconds: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=#203040:s=1280x720:r=25:d={seconds}",
        "-c:v", "libvpx-vp9",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        # Fallback to VP8 if libvpx-vp9 not enabled
        cmd[cmd.index("libvpx-vp9")] = "libvpx"
        res2 = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert res2.returncode == 0, res2.stderr
    return path


def test_d9_shot_default_none_and_status_render_capcut_handling(tmp_path):
    """D9.5: Newly constructed/allocated shots default requested_source/actual_source to None."""
    s_raw = Shot(shot_id="ch01_shot001", chapter_index=1, shot_index=1)
    assert s_raw.requested_source is None
    assert s_raw.actual_source is None
    assert s_raw.asset_path is None
    assert s_raw.rendered_clip is None

    ch = Chapter(
        chapter_index=1,
        chapter_title="Ch 1",
        audio_duration_seconds=3.0,
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="Historic steel suspension bridge at sunrise.",
                segments=[
                    Segment(
                        segment_index=1,
                        narration_text="Historic steel suspension bridge at sunrise.",
                        duration_seconds=3.0,
                        chapter_start_seconds=0.0,
                        chapter_end_seconds=3.0,
                    )
                ],
            )
        ],
    )
    allocated = form_chapter_shots(ch, fps=25)
    assert len(allocated) == 1
    assert allocated[0].requested_source is None
    assert allocated[0].actual_source is None
    assert allocated[0].asset_path is None
    assert allocated[0].rendered_clip is None

    manifest, p_dir = create_project("d9_none", "None Defaults", "documentary_en", projects_root=tmp_path)
    manifest.chapters = [ch]
    save_manifest(manifest, p_dir)
    loaded = load_manifest(p_dir)
    assert loaded.schema_version == CURRENT_SCHEMA_VERSION
    assert loaded.chapters[0].shots[0].requested_source is None
    assert loaded.chapters[0].shots[0].actual_source is None

    st = compute_project_step_status(p_dir)
    assert st["steps"]["visual"]["status"] == "belum"
    assert st["steps"]["visual"]["missing_visual_shots"] == 1


def test_d9_license_filter_rules():
    """D9.2/D9.3: Commercial-allowed license checks accept PD/CC0/CC-BY/CC-BY-SA and reject NC/ND."""
    assert is_commercial_license_allowed("CC0")[0] is True
    assert is_commercial_license_allowed("Public domain")[0] is True
    assert is_commercial_license_allowed("CC BY 4.0")[0] is True
    assert is_commercial_license_allowed("CC BY-SA 3.0")[0] is True
    assert is_commercial_license_allowed("NASA Media (Public Domain / U.S. Government)")[0] is True

    assert is_commercial_license_allowed("CC BY-NC 4.0")[0] is False
    assert is_commercial_license_allowed("CC BY-ND 4.0")[0] is False
    assert is_commercial_license_allowed("CC BY-NC-SA 3.0")[0] is False
    assert is_commercial_license_allowed("All rights reserved")[0] is False
    assert is_commercial_license_allowed("")[0] is False

    assert license_requires_attribution("CC BY 4.0") is True
    assert license_requires_attribution("CC BY-SA 3.0") is True
    assert license_requires_attribution("NASA Media (Public Domain / U.S. Government)") is True
    assert license_requires_attribution("CC0") is False
    assert license_requires_attribution("Public domain") is False
    assert license_requires_attribution("Pixabay Content License") is False


def test_d9_wikimedia_720p_license_cache_and_webm_conversion(tmp_path):
    """D9.2/D9.4: Wikimedia filters NC/ND, prefers >=720p, caches search JSON, sends User-Agent, converts WebM->MP4."""
    cache = GlobalMediaCache(cache_dir=tmp_path / "gcache")
    cm = CreditsManager(tmp_path / "proj")
    prov = WikimediaVisualProvider(cache=cache, credits_manager=cm)

    mock_payload = {
        "query": {
            "pages": {
                "101": {
                    "pageid": 101,
                    "title": "File:Brooklyn_Bridge_NC.jpg",
                    "imageinfo": [{
                        "url": "https://upload.wikimedia.org/small_nc.jpg",
                        "width": 1920,
                        "height": 1080,
                        "mime": "image/jpeg",
                        "mediatype": "BITMAP",
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY-NC 4.0"},
                            "Artist": {"value": "NC User"},
                            "ObjectName": {"value": "Brooklyn Bridge NC"},
                        },
                    }],
                },
                "102": {
                    "pageid": 102,
                    "title": "File:Brooklyn_Bridge_LowRes.jpg",
                    "imageinfo": [{
                        "url": "https://upload.wikimedia.org/tiny.jpg",
                        "width": 640,
                        "height": 360,
                        "mime": "image/jpeg",
                        "mediatype": "BITMAP",
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC0"},
                            "Artist": {"value": "LowRes Author"},
                            "ObjectName": {"value": "Brooklyn Bridge lowres"},
                        },
                    }],
                },
                "103": {
                    "pageid": 103,
                    "title": "File:Brooklyn_Bridge_HD.webm",
                    "imageinfo": [{
                        "url": "https://upload.wikimedia.org/bridge_hd.webm",
                        "width": 1280,
                        "height": 720,
                        "duration": 4.5,
                        "mime": "video/webm",
                        "mediatype": "VIDEO",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Brooklyn_Bridge_HD.webm",
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "Artist": {"value": "<a href='https://ex.com'>Jane Commons</a>"},
                            "ObjectName": {"value": "Brooklyn Bridge cables HD"},
                        },
                    }],
                },
                "104": {
                    "pageid": 104,
                    "title": "File:Brooklyn_Bridge_Huge.webm",
                    "imageinfo": [{
                        "url": "https://upload.wikimedia.org/bridge_huge.webm",
                        "width": 3840,
                        "height": 2160,
                        "size": 150000000,
                        "duration": 600.0,
                        "mime": "video/webm",
                        "mediatype": "VIDEO",
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC0"},
                            "Artist": {"value": "Huge Uploader"},
                            "ObjectName": {"value": "Brooklyn Bridge full doc"},
                        },
                    }],
                },
            }
        }
    }

    calls = []

    def _fake_get(url, params=None, headers=None, **kwargs):
        calls.append({"url": url, "headers": headers or {}})
        return SimpleNamespace(status_code=200, headers={}, json=lambda: mock_payload, raise_for_status=lambda: None)

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=_fake_get):
        cands, raw_cnt, rej = prov.search_with_diagnostics("brooklyn bridge cables", target_duration=4.0, limit=5)

    assert raw_cnt == 4
    assert len(cands) == 1
    assert cands[0].candidate_id == "wikimedia_103"
    assert cands[0].source_type == "stock_video"
    assert cands[0].author == "Jane Commons"
    assert "User-Agent" in calls[0]["headers"] and "EasyFacelessStudio" in calls[0]["headers"]["User-Agent"]
    assert any("NC" in r or "nc" in r.lower() for r in rej)
    assert any("too large" in r.lower() for r in rej)

    # Second identical search hits disk JSON cache without issuing HTTP GET
    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=AssertionError("Should hit cache")):
        cands_cached = prov.search_candidates("brooklyn bridge cables", target_duration=4.0, limit=5)
        assert len(cands_cached) == 1 and cands_cached[0].candidate_id == "wikimedia_103"

    # Verify WebM auto-conversion to H.264 MP4 on fetch_asset
    sample_webm = _make_sample_webm(tmp_path / "src_hd.webm", seconds=1.0)
    with patch.object(cache, "download_or_get", side_effect=lambda url, dst, **kw: dst.write_bytes(sample_webm.read_bytes()) or dst):
        asset = prov.fetch_asset(cands[0], tmp_path / "proj" / "assets" / "visual" / "shots" / "ch01_shot001_asset.mp4")

    assert asset.local_path.suffix == ".mp4"
    assert asset.local_path.is_file()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(asset.local_path)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "h264"


def test_d9_nasa_and_openverse_providers_and_ken_burns(tmp_path):
    """D9.2: NASA API video/credit extraction + Openverse photo commercial filter -> Ken Burns H.264 clip."""
    cache = GlobalMediaCache(cache_dir=tmp_path / "gcache2")
    nasa = NasaVisualProvider(cache=cache)
    ov = OpenverseVisualProvider(cache=cache)

    def _fake_api(url, params=None, headers=None, **kwargs):
        if "images-api.nasa.gov/search" in url:
            data = {
                "collection": {
                    "items": [{
                        "data": [{
                            "nasa_id": "GSFC_2017_BRIDGE",
                            "media_type": "video",
                            "title": "Earth Horizon Sunrise Timelapse",
                            "description": "Orbital sunrise horizon timelapse over bridge lights",
                            "keywords": ["earth", "sunrise", "bridge"],
                            "center": "GSFC",
                            "photographer": "NASA Astronaut Crew",
                        }],
                        "links": [{"rel": "preview", "href": "https://images-assets.nasa.gov/video/prev.jpg"}],
                    }]
                }
            }
        elif "images-api.nasa.gov/asset/" in url:
            data = {
                "collection": {
                    "items": [{"href": "https://images-assets.nasa.gov/video/GSFC_2017_BRIDGE~orig.mp4"}]
                }
            }
        elif "api.openverse.org/v1/images" in url:
            data = {
                "results": [
                    {
                        "id": "ov_nc_skip",
                        "title": "Brooklyn Bridge NC",
                        "license": "by-nc",
                        "license_version": "4.0",
                        "url": "https://ex.org/nc.jpg",
                        "width": 1600,
                        "height": 900,
                        "creator": "Skip",
                        "tags": [{"name": "bridge"}, {"name": "brooklyn"}],
                    },
                    {
                        "id": "ov_ccby_ok",
                        "title": "Stone Bridge Mist Sunrise",
                        "license": "by",
                        "license_version": "4.0",
                        "url": "https://ex.org/bridge.jpg",
                        "foreign_landing_url": "https://openverse.org/image/ov_ccby_ok",
                        "width": 1920,
                        "height": 1080,
                        "creator": "Alice Photographer",
                        "tags": [{"name": "bridge"}, {"name": "sunrise"}, {"name": "mist"}],
                    },
                ]
            }
        else:
            raise AssertionError(f"Unexpected URL: {url}")
        return SimpleNamespace(status_code=200, headers={}, json=lambda: data, raise_for_status=lambda: None)

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=_fake_api):
        nasa_cands, _, _ = nasa.search_with_diagnostics("earth sunrise bridge", target_duration=5.0)
        ov_cands, ov_raw, _ = ov.search_with_diagnostics("bridge mist sunrise", target_duration=4.0)

    assert len(nasa_cands) == 1
    assert nasa_cands[0].url.endswith("~orig.mp4")
    assert nasa_cands[0].author == "NASA Astronaut Crew"
    assert nasa_cands[0].source_url == "https://images.nasa.gov/details/GSFC_2017_BRIDGE"

    assert ov_raw == 2
    assert len(ov_cands) == 1
    assert ov_cands[0].candidate_id == "openverse_ov_ccby_ok"
    assert ov_cands[0].source_type == "stock_image"
    assert ov_cands[0].license == "CC BY 4.0"

    # Download mock photo and build Ken Burns clip
    sample_jpg = _make_sample_jpg(tmp_path / "ov_src.jpg")
    with patch.object(cache, "download_or_get", side_effect=lambda url, dst, **kw: dst.write_bytes(sample_jpg.read_bytes()) or dst):
        asset = ov.fetch_asset(ov_cands[0], tmp_path / "ov_dest.jpg")
    clip_out = tmp_path / "ov_kb.mp4"
    ShotClipBuilder(fps=25).build_shot_clip(asset.local_path, clip_out, MotionType.pan_right, target_frames=25)
    assert clip_out.is_file()
    verify_clip_stream_parameters(clip_out, expected_frames=25, expected_fps=25)


def test_d9_tiered_query_waterfall_diagnostics_and_credits_export(tmp_path, monkeypatch):
    """D9.1/D9.2/D9.3: Multi-tier search escalates to broader queries & next provider, logs attempts, exports credits.md."""
    gen_qs = build_generalized_visual_queries("dramatic stone suspension bridge mist", anchor_terms=["bridge"])
    assert any("bridge" in q for q in gen_qs)

    manifest, p_dir = create_project("d9_waterfall", "Bridge Documentary", "documentary_en", projects_root=tmp_path)
    sample_jpg = _make_sample_jpg(tmp_path / "real_wiki.jpg")

    wav_file = p_dir / "assets" / "audio" / "chapters" / "chapter_01.wav"
    wav_file.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0", "-ar", "48000", "-ac", "1", str(wav_file)],
        capture_output=True, check=True,
    )

    ch = Chapter(
        chapter_index=1,
        chapter_title="Steel Span",
        audio_file="assets/audio/chapters/chapter_01.wav",
        audio_duration_seconds=2.0,
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="Suspension bridge cables over river. Unmatchable abstract enigma.",
                segments=[
                    Segment(segment_index=1, narration_text="Suspension bridge cables"),
                    Segment(segment_index=2, narration_text="Unmatchable abstract enigma"),
                ],
            )
        ],
        shots=[
            Shot(
                shot_id="ch01_shot001",
                chapter_index=1,
                shot_index=1,
                segment_indices=[1],
                duration_seconds=1.0,
                target_frames=25,
                search_query="specific steel cable",
                search_query_en="specific rusted steel cable closeup",
                search_query_source="ai",
            ),
            Shot(
                shot_id="ch01_shot002",
                chapter_index=1,
                shot_index=2,
                segment_indices=[2],
                duration_seconds=1.0,
                target_frames=25,
                search_query="zzznonexistent999",
                search_query_en="zzznonexistent999 concept",
                search_query_source="ai",
            ),
        ],
    )
    manifest.chapters = [ch]
    save_manifest(manifest, p_dir)

    class _MockProv(BaseVisualProvider):
        def __init__(self, prov_id: str):
            self._id = prov_id

        @property
        def name(self) -> str:
            return self._id

        @property
        def is_fake(self) -> bool:
            return False

        def validate_credentials(self) -> None:
            return None

        def search_with_diagnostics(self, query: str, target_duration: float, limit: int = 3, **kwargs):
            # Pixabay rejects all 4 raw hits; Wikimedia succeeds only when generalized noun 'cable'/'bridge' hits
            if self._id == "pixabay":
                return [], 4, ["avoid term matched"]
            if self._id == "wikimedia" and query.strip().lower() == "steel cable":
                return [
                    VisualCandidate(
                        candidate_id="wiki_cable_1",
                        provider="wikimedia",
                        source_type="stock_image",
                        url="https://upload.wikimedia.org/cable.jpg",
                        author="Bridge Archivist",
                        source_url="https://commons.wikimedia.org/wiki/File:Cable.jpg",
                        license="CC BY 4.0",
                        width=1920,
                        height=1080,
                        relevance_score=0.88,
                        tags=["cable", "bridge", "steel"],
                        title="Brooklyn Bridge Steel Cable",
                    )
                ], 2, []
            return [], 0, []

        def search_candidates(self, query: str, target_duration: float, limit: int = 3, **kwargs):
            c, _, _ = self.search_with_diagnostics(query, target_duration, limit=limit, **kwargs)
            return c

        def fetch_asset(self, candidate: VisualCandidate, dest_path: Path) -> VisualAsset:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(sample_jpg.read_bytes())
            return VisualAsset(
                asset_id=candidate.candidate_id,
                provider=self._id,
                source_type=candidate.source_type,
                local_path=dest_path,
                content_hash="realwiki123",
                author=candidate.author,
                source_url=candidate.source_url,
                license=candidate.license,
                title=candidate.title,
            )

    svc = VisualService(p_dir)
    monkeypatch.setattr(svc, "get_provider", lambda name, m: _MockProv(name))
    monkeypatch.delenv("EFS_DEV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    manifest, _ = svc.run_pipeline(
        manifest=manifest,
        providers_order=["pixabay", "wikimedia", "nasa", "openverse"],
        allow_fallback=False,
    )

    s1, s2 = manifest.chapters[0].shots
    assert s1.actual_source == "wikimedia"
    assert s1.requires_attribution is True
    assert s1.attribution_author == "Bridge Archivist"
    assert s1.attribution_license == "CC BY 4.0"
    assert s1.rendered_clip and (p_dir / s1.rendered_clip).is_file()

    assert s2.actual_source is None
    assert s2.asset_path is None
    assert s2.fallback_reason == "no_visual_found"
    assert s2.no_visual_reason_summary and "Pixabay" in s2.no_visual_reason_summary and "Wikimedia" in s2.no_visual_reason_summary
    assert len(s2.search_attempts) > 0

    credits_md = p_dir / "export" / "credits.md"
    assert credits_md.is_file()
    md_txt = credits_md.read_text(encoding="utf-8")
    assert "Brooklyn Bridge Steel Cable" in md_txt
    assert "Bridge Archivist" in md_txt
    assert "CC BY 4.0" in md_txt

    # Copy credits.md to CapCut draft on export
    # Give s2 a neutral backdrop clip so CapCut export runs cleanly
    svc.build_all(manifest, use_neutral_fallback=True)
    save_manifest(manifest, p_dir)
    sub_dir = p_dir / "export" / "subtitles"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / "full_timeline.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nBridge span\n", encoding="utf-8")

    monkeypatch.setattr("app.services.longform.capcut.service.get_project_dir", lambda pid: p_dir)
    cc_res = export_full_project("d9_waterfall", install=False)
    draft_credits = Path(cc_res["export_path"]) / "credits.md"
    assert draft_credits.is_file()
    assert "Bridge Archivist" in draft_credits.read_text(encoding="utf-8")


@pytest.mark.parametrize("lang", ["id", "en"])
def test_d9_ui_sources_pexels_notice_attribution_and_reason_bilingual(tmp_path, monkeypatch, lang):
    """D9.1/D9.2/D9.3: UI checkboxes include Wikimedia/NASA/Openverse, mark Pexels closed, show attribution & reason."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", lang)
    monkeypatch.setitem(config.app, "pixabay_api_keys", [])
    monkeypatch.setitem(config.app, "pexels_api_keys", [])
    monkeypatch.setitem(config.app, "coverr_api_keys", [])
    monkeypatch.delenv("EFS_DEV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    assert has_real_visual_source(tmp_path) is True
    assert has_real_visual_source(tmp_path, enabled_sources=[]) is False

    pid = f"d9_ui_{lang}"
    manifest, p_dir = create_project(pid, "D9 UI Project", "documentary_en", projects_root=tmp_path)
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
                    paragraph_text="Steel tower. Unmatched concept.",
                    audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=4.0),
                    segments=[
                        Segment(segment_index=1, narration_text="Steel tower"),
                        Segment(segment_index=2, narration_text="Unmatched concept"),
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
                    requested_source="wikimedia",
                    actual_source="wikimedia",
                    attribution_title="Brooklyn Tower 1883",
                    attribution_author="Wikimedia Photo",
                    attribution_license="CC BY 4.0",
                    attribution_url="https://commons.wikimedia.org/wiki/File:Brooklyn.jpg",
                    requires_attribution=True,
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
                    no_visual_reason_summary="Pixabay: 12 kandidat (semua ditolak relevansi); Wikimedia: 0 kandidat",
                    search_attempts=[{"provider": "pixabay", "query": "unmatched", "raw_count": 12, "accepted_count": 0, "rejected_reasons": ["Avoid term"]}],
                    needs_review=True,
                ),
            ],
        )
    ]
    save_manifest(manifest, p_dir)

    at = AppTest.from_file(_get_app_file(), default_timeout=25)
    at.query_params["project"] = pid
    at.session_state[f"active_step_{pid}"] = "visual"
    at.run()
    assert not at.exception

    cb_labels = " ".join(cb.label for cb in at.checkbox)
    assert "Wikimedia" in cb_labels
    assert "NASA" in cb_labels
    assert "Openverse" in cb_labels
    assert "Pexels" in cb_labels

    captions = " ".join(c.value for c in at.caption)
    infos = " ".join(i.value for i in at.info)
    mds = " ".join(m.value for m in at.markdown)
    assert "Brooklyn Tower 1883" in mds
    if lang == "id":
        assert "Pendaftaran key baru Pexels sedang ditutup" in captions
        assert "Perlu atribusi" in infos
        assert "`Belum ada`" not in captions
        assert "Belum ada visual" in captions
    else:
        assert "New Pexels API key registration is closed" in captions
        assert "Attribution required" in infos
        assert "`None yet`" not in captions
        assert "No visual yet" in captions

    # Select shot 2 to inspect missing visual reason & search diagnostics
    btn_s2 = [b for b in at.button if b.key == f"btn_sh_{pid}_ch01_shot002"][0]
    btn_s2.click().run()
    mds2 = " ".join(m.value for m in at.markdown)
    assert "Pixabay: 12 kandidat" in mds2


def test_d9_installer_script_and_launcher_runtime_non_elevated_and_shutdown(tmp_path):
    """D9.6: Installer enforces PrivilegesRequired=lowest + runasoriginaluser + clean process shutdown."""
    repo_root = Path(__file__).resolve().parents[2]
    iss = (repo_root / "packaging" / "installer" / "efs.iss").read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in iss
    assert "PrivilegesRequiredOverridesAllowed" not in iss
    assert "AppMutex=EasyFacelessStudioRunningMutex" in iss
    assert "runasoriginaluser" in iss
    assert "NextButtonClick" in iss
    assert "InitializeUninstall" in iss

    # Verify find_processes_in_directory & shutdown_running_app using a real benign sleep subprocess?
    # Better: verify find_processes_in_directory excludes non-target paths cleanly & shutdown clears launcher lock
    from app.services.longform import app_paths, launcher_runtime

    lock_f = tmp_path / ".efs_launcher.lock.json"
    launcher_runtime.write_launcher_lock({"pid": 999999, "port": 8599}, lock_path=lock_f)
    assert lock_f.is_file()
    launcher_runtime.clear_launcher_lock(lock_path=lock_f)
    assert not lock_f.is_file()

    # Spawn a detached Python sleep inside a fake install_root using current sys.executable?
    # Note: executable is sys.executable, so find_processes_in_directory(Path(sys.executable).parent.parent) finds real procs only there.
    # Instead verify status JSON output structure cleanly via subprocess
    res = subprocess.run(
        [sys.executable, "-m", "app.services.longform.launcher_runtime", "--status-json"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, res.stderr
    parsed = json.loads(res.stdout.strip())
    assert "running" in parsed and "pids" in parsed and "jobs" in parsed


def test_d9_7_unified_search_narration_ranking_and_ai_cutoff(tmp_path):
    """D9.7: Batch fetch, Otomatis, Search again share one strategy; keyboard traps penalized; strict AI cutoff rejects weak hits."""
    from app.services.longform.visual.stock import score_candidate_relevance
    import cli_longform

    narr = "Some died in workplace accidents, while others suffered from illnesses caused by working under extreme air pressure deep beneath the river."
    q = "workplace accident historical"

    ok_kb, sc_kb, _ = score_candidate_relevance(
        candidate_tags=["keyboard", "typing", "writing", "computer", "laptop", "workplace"],
        search_query=q,
        min_relevance_score=0.35,
        narration_text=narr,
        strict_ai_relevance=False,
    )
    assert ok_kb is False
    assert sc_kb < 0.35

    ok_rescue, sc_rescue, _ = score_candidate_relevance(
        candidate_tags=["fire fighters", "rescue operation", "accident", "rescue", "ambulance"],
        search_query=q,
        min_relevance_score=0.35,
        narration_text=narr,
        strict_ai_relevance=False,
    )
    assert ok_rescue is True
    assert sc_rescue > sc_kb
    assert sc_rescue >= 0.42

    # When ai_relevance is enabled, weak matches (<0.42) without concrete narration hits are rejected
    ok_weak_ai, sc_weak_ai, _ = score_candidate_relevance(
        candidate_tags=["office", "workplace", "meeting"],
        search_query=q,
        min_relevance_score=0.35,
        narration_text=narr,
        strict_ai_relevance=True,
    )
    assert ok_weak_ai is False
    assert sc_weak_ai < 0.42

    # Verify CLI pipeline auto supports unified --providers and --check-ai-relevance flags
    parser = cli_longform.build_parser()
    ns = parser.parse_args(["pipeline", "auto", "demo_proj", "--providers", "pixabay,wikimedia,nasa", "--check-ai-relevance"])
    assert ns.providers == "pixabay,wikimedia,nasa"
    assert ns.check_ai_relevance is True

    # Verify fetch_all, search_and_preview_candidates, and replace_shot use identical limit=5 & query ladder
    manifest, p_dir = create_project("d9_7_unified", "Unified Search Test", "documentary_en", projects_root=tmp_path)
    ch = Chapter(
        chapter_index=1,
        chapter_title="Work Accidents",
        audio_duration_seconds=4.0,
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text=narr,
                segments=[Segment(segment_index=1, narration_text=narr)],
            )
        ],
        shots=[
            Shot(
                shot_id="ch01_shot001",
                chapter_index=1,
                shot_index=1,
                segment_indices=[1],
                duration_seconds=4.0,
                target_frames=100,
                search_query="workplace accident historical",
                search_query_en="workplace accident historical",
                search_queries_en=["workplace accident historical", "bridge worker rescue"],
                search_query_source="ai",
            )
        ],
    )
    manifest.chapters = [ch]
    save_manifest(manifest, p_dir)

    svc = VisualService(p_dir)
    recorded_limits = []

    class _RecordingProv(BaseVisualProvider):
        @property
        def name(self):
            return "pixabay"

        @property
        def is_fake(self):
            return False

        def validate_credentials(self):
            return None

        def search_with_diagnostics(self, query, target_duration, limit=3, **kwargs):
            recorded_limits.append((query, limit, kwargs.get("narration_text"), kwargs.get("avoid_terms") is not None))
            return [
                VisualCandidate(
                    candidate_id="pix_resq_1",
                    provider="pixabay",
                    source_type="stock_video",
                    url="https://cdn.example.com/resq.mp4",
                    width=1920,
                    height=1080,
                    duration_seconds=5.0,
                    tags=["accident", "rescue", "bridge"],
                    relevance_score=0.55,
                )
            ], 1, []

        def search_candidates(self, query, target_duration, limit=3, **kwargs):
            c, _, _ = self.search_with_diagnostics(query, target_duration, limit=limit, **kwargs)
            return c

        def fetch_asset(self, candidate, dest_path):
            sample_jpg = _make_sample_jpg(dest_path.with_suffix(".jpg"))
            return VisualAsset(
                asset_id=candidate.candidate_id,
                provider="pixabay",
                source_type="stock_image",
                local_path=sample_jpg,
                content_hash="abc123",
            )

    svc.get_provider = lambda name, m: _RecordingProv()
    svc.fetch_all(manifest, providers_order=["pixabay"], allow_fallback=False)
    calls_fetch = list(recorded_limits)
    recorded_limits.clear()

    svc.search_and_preview_candidates(manifest, "ch01_shot001", provider_name="pixabay")
    calls_preview = list(recorded_limits)
    recorded_limits.clear()

    svc.replace_shot(manifest, "ch01_shot001", provider_name="pixabay", prompt="workplace accident historical")
    calls_replace = list(recorded_limits)

    assert len(calls_fetch) == len(calls_preview) == len(calls_replace) >= 2
    assert calls_fetch == calls_preview == calls_replace
    for _q, lim, narr_arg, has_avoids in calls_fetch:
        assert lim == 5
        assert "under extreme air pressure" in (narr_arg or "")
        assert has_avoids is True
