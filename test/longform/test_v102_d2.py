"""Offline tests (a)-(k) for V102-D2 Internet Archive & Smithsonian Open Access providers.

All HTTP and FFmpeg operations are strictly mocked using empirical JSON shapes from
docs/dev/ARCHIVE_SOURCES.md (D1) — zero real HTTP or LLM calls occur.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.longform.schema import Chapter, ProjectManifest, Shot
from app.services.longform.visual.archive_providers import (
    IA_IMAGE_COLLECTION_ALLOWLIST,
    IA_VIDEO_COLLECTION_ALLOWLIST,
    InternetArchiveVisualProvider,
    SmithsonianAuthBlockedError,
    SmithsonianVisualProvider,
    get_smithsonian_auth_status,
    reset_smithsonian_auth_block,
)
from app.services.longform.visual.base import canonical_candidate_dedup_key
from app.services.longform.visual.cache import GlobalMediaCache
from app.services.longform.visual.credits import CreditsManager
from app.services.longform.visual.service import VisualService


def _fake_http_response(payload: dict, status_code: int = 200):
    raw_txt = json.dumps(payload)
    return SimpleNamespace(
        status_code=status_code,
        headers={},
        text=raw_txt,
        json=lambda: payload,
        raise_for_status=lambda: None,
    )


def _d1_ia_prelinger_search_doc(identifier: str = "SpeechSt1949", title: str = "Speech on Stage Harbor 1949") -> dict:
    return {
        "identifier": identifier,
        "title": title,
        "description": "Coronet Instructional Films harbor ship archival reel",
        "subject": ["harbor", "ship", "history"],
        "creator": "Coronet Instructional Films",
        "collection": ["prelinger"],
        "mediatype": "movies",
        "licenseurl": "http://creativecommons.org/licenses/publicdomain/",
    }


def _d1_ia_prelinger_meta(identifier: str = "SpeechSt1949", duration: str = "642.0") -> dict:
    return {
        "metadata": {
            "identifier": identifier,
            "title": "Speech on Stage Harbor 1949",
            "collection": ["prelinger"],
            "mediatype": "movies",
            "licenseurl": "http://creativecommons.org/licenses/publicdomain/",
            "creator": "Coronet Instructional Films",
        },
        "files": [
            {"name": f"{identifier}_master.mov", "format": "Apple ProRes", "size": "4800000000", "width": "1920", "height": "1080", "length": duration},
            {"name": f"{identifier}.mpg", "format": "MPEG2", "size": "1900000000", "width": "720", "height": "480", "length": duration},
            {"name": f"{identifier}.mp4", "format": "MPEG4", "size": "45000000", "width": "640", "height": "480", "length": duration},
            {"name": f"{identifier}_512kb.mp4", "format": "512Kb MPEG4", "size": "18000000", "width": "640", "height": "480", "length": duration},
        ],
    }


def _d1_smithsonian_chsdm_row(row_id: str = "ld1-1643399887910-0") -> dict:
    return {
        "id": row_id,
        "title": "Illustration for Hamlet: Ghost Harbor Palace",
        "content": {
            "descriptiveNonRepeating": {
                "unitCode": "CHNDM",
                "data_source": "Cooper Hewitt, Smithsonian Design Museum",
                "record_link": "https://collections.si.edu/search/detail/edanmdm:ld1-1643399887910-0",
                "metadata_usage": {"access": "CC0"},
                "online_media": {
                    "media": [
                        {
                            "type": "Images",
                            "usage": {"access": "CC0"},
                            "width": 2597,
                            "height": 1820,
                            "content": "https://ids.si.edu/ids/deliveryService?id=CHSDM-BDD4E32E62C72-000001",
                            "resources": [
                                {"label": "High-resolution TIFF", "url": "https://ids.si.edu/1.tif", "width": 2810, "height": 1960},
                                {"label": "High-resolution JPEG", "url": "https://ids.si.edu/ids/download?id=CHSDM-BDD4E32E62C72-000001.jpg", "width": 2810, "height": 1960},
                            ],
                        }
                    ]
                },
            },
            "indexedStructured": {
                "online_media_rights": ["CC0"],
                "topic": ["ghost", "harbor", "palace"],
            },
        },
    }


def test_a_ia_allowlist_applied_in_query_and_blocks_opensource(tmp_path: Path):
    """(a) Curated collection allowlist is enforced both in advancedsearch q= and client check."""
    assert IA_VIDEO_COLLECTION_ALLOWLIST == ("prelinger",)
    assert IA_IMAGE_COLLECTION_ALLOWLIST == ("clevelandart",)

    cache = GlobalMediaCache(tmp_path / "cache")
    prov = InternetArchiveVisualProvider(cache=cache)
    captured_params = []

    def fake_get(url, params=None, **kw):
        if "advancedsearch.php" in url:
            captured_params.append(dict(params or {}))
            return _fake_http_response({
                "response": {
                    "docs": [
                        _d1_ia_prelinger_search_doc("SpeechSt1949", "Harbor Vessel Reel"),
                        {
                            "identifier": "random_opensource_vid",
                            "title": "Harbor Vessel Open",
                            "collection": ["opensource_movies"],
                            "mediatype": "movies",
                            "licenseurl": "http://creativecommons.org/licenses/by/4.0/",
                        },
                    ]
                }
            })
        if "SpeechSt1949" in url:
            return _fake_http_response(_d1_ia_prelinger_meta("SpeechSt1949"))
        raise AssertionError(f"Unexpected metadata fetch for disallowed collection: {url}")

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=fake_get):
        cands, _, rejected = prov.search_with_diagnostics("harbor", 5.0, limit=5)

    assert len(captured_params) == 1
    q_sent = captured_params[0]["q"]
    assert "collection:prelinger" in q_sent
    assert "collection:clevelandart" in q_sent
    assert "collection:opensource" not in q_sent
    assert "*" not in q_sent  # avoid archive.org wildcard BACKEND_ERROR
    assert [c.candidate_id for c in cands] == ["ia_SpeechSt1949_seg0"]
    assert any("allowlist" in r.lower() for r in rejected)


def test_b_ia_rejects_fair_use_rights_even_when_pdm(tmp_path: Path):
    """(b) Items like D1's 'western-tradition' with PDM licenseurl + Fair Use rights are rejected."""
    cache = GlobalMediaCache(tmp_path / "cache")
    prov = InternetArchiveVisualProvider(cache=cache)

    def fake_get(url, params=None, **kw):
        if "advancedsearch.php" in url:
            return _fake_http_response({
                "response": {
                    "docs": [
                        {
                            "identifier": "western-tradition",
                            "title": "Harbor Tradition",
                            "collection": ["prelinger"],
                            "mediatype": "movies",
                            "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
                            "rights": "Fair Use Notice: Copyrighted excerpts for educational broadcast.",
                        }
                    ]
                }
            })
        raise AssertionError(f"Fair Use doc should be rejected before metadata call: {url}")

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=fake_get):
        cands, _, rejected = prov.search_with_diagnostics("harbor", 5.0, limit=5)

    assert cands == []
    assert any("western-tradition" in r and "rights restricted" in r.lower() for r in rejected)


def test_c_ia_rejects_nc_nd_licenseurl(tmp_path: Path):
    """(c) NC, ND, and missing licenseurl items are strictly rejected."""
    cache = GlobalMediaCache(tmp_path / "cache")
    prov = InternetArchiveVisualProvider(cache=cache)

    docs = [
        {
            "identifier": "ia_nc_item",
            "title": "Harbor NC",
            "collection": ["prelinger"],
            "mediatype": "movies",
            "licenseurl": "https://creativecommons.org/licenses/by-nc/4.0/",
        },
        {
            "identifier": "ia_nd_item",
            "title": "Harbor ND",
            "collection": ["prelinger"],
            "mediatype": "movies",
            "licenseurl": "https://creativecommons.org/licenses/by-nd/4.0/",
        },
        {
            "identifier": "ia_empty_lic",
            "title": "Harbor Empty",
            "collection": ["prelinger"],
            "mediatype": "movies",
            "licenseurl": "",
        },
    ]

    def _meta_for_doc(doc):
        return {
            "metadata": doc,
            "files": [
                {"name": f"{doc['identifier']}.mp4", "format": "MPEG4", "size": "45000000", "width": "640", "height": "480", "length": "300"}
            ],
        }

    def fake_cached(pkey, url, params=None, **kw):
        if "advancedsearch.php" in url:
            return {"response": {"docs": docs}}
        for d in docs:
            if d["identifier"] in url:
                return _meta_for_doc(d)
        raise AssertionError(url)

    with patch("app.services.longform.visual.archive_providers.cached_json_get", side_effect=fake_cached):
        cands, _, rejected = prov.search_with_diagnostics("harbor", 5.0, limit=5)

    assert cands == []
    assert len(rejected) == 3
    assert any("ia_nc_item" in r for r in rejected)
    assert any("ia_nd_item" in r for r in rejected)
    assert any("ia_empty_lic" in r for r in rejected)


def test_d_ia_selects_mp4_and_ffmpeg_http_range_safe_offset(tmp_path: Path):
    """(d) Picks reasonable MP4 (not 4.8GB ProRes/MPEG2) and trims remote URL with safe offset."""
    cache = GlobalMediaCache(tmp_path / "cache")
    prov = InternetArchiveVisualProvider(cache=cache)

    def fake_get(url, params=None, **kw):
        if "advancedsearch.php" in url:
            return _fake_http_response({"response": {"docs": [_d1_ia_prelinger_search_doc("SpeechSt1949")]}})
        return _fake_http_response(_d1_ia_prelinger_meta("SpeechSt1949", duration="600.0"))

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=fake_get):
        cands, _, _ = prov.search_with_diagnostics("harbor", 5.0, limit=3)

    assert len(cands) == 1
    cand = cands[0]
    assert cand.url.endswith("/SpeechSt1949/SpeechSt1949.mp4")
    assert cand.source_info["file_name"] == "SpeechSt1949.mp4"
    assert cand.rendition_name == "internet_archive (640x480)"
    offset = cand.source_info["segment_offset"]
    seg_dur = cand.source_info["segment_duration"]
    assert 60.0 <= offset <= (600.0 - 60.0 - seg_dur)
    assert seg_dur == pytest.approx(6.5, abs=0.01)

    ffmpeg_cmds = []

    def fake_ffmpeg(cmd, **kw):
        ffmpeg_cmds.append(list(cmd))
        out_p = Path(cmd[-1])
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_bytes(b"trimmed-mp4-bytes")
        return SimpleNamespace(returncode=0, stderr="")

    dest = tmp_path / "shot1.mp4"
    with patch("app.services.longform.visual.archive_providers.subprocess.run", side_effect=fake_ffmpeg):
        with patch.object(cache, "download_or_get") as full_dl_spy:
            asset = prov.fetch_asset(cand, dest)

    assert full_dl_spy.call_count == 0
    assert asset.local_path.is_file()
    assert len(ffmpeg_cmds) == 1
    cmd = ffmpeg_cmds[0]
    ss_idx = cmd.index("-ss")
    i_idx = cmd.index("-i")
    t_idx = cmd.index("-t")
    assert ss_idx < i_idx
    assert cmd[i_idx + 1] == cand.url
    assert float(cmd[ss_idx + 1]) == pytest.approx(offset, abs=0.01)
    assert float(cmd[t_idx + 1]) == pytest.approx(seg_dur, abs=0.01)


def test_e_ia_two_shots_same_film_get_distinct_segments(tmp_path: Path):
    """(e) Two shots reusing the same Prelinger film receive distinct offsets and dedup keys."""
    cache = GlobalMediaCache(tmp_path / "cache")
    prov = InternetArchiveVisualProvider(cache=cache)

    def fake_cached(pkey, url, params=None, **kw):
        if "advancedsearch.php" in url:
            return {"response": {"docs": [_d1_ia_prelinger_search_doc("SpeechSt1949")]}}
        return _d1_ia_prelinger_meta("SpeechSt1949", duration="600.0")

    with patch("app.services.longform.visual.archive_providers.cached_json_get", side_effect=fake_cached):
        c1, _, _ = prov.search_with_diagnostics("harbor", 5.0, limit=3, excluded_ids=set())
        c2, _, _ = prov.search_with_diagnostics("harbor", 5.0, limit=3, excluded_ids={c1[0].candidate_id})

    assert c1[0].candidate_id == "ia_SpeechSt1949_seg0"
    assert c2[0].candidate_id == "ia_SpeechSt1949_seg1"
    off1 = c1[0].source_info["segment_offset"]
    off2 = c2[0].source_info["segment_offset"]
    assert off1 != off2
    assert 60.0 <= off1 <= 533.5
    assert 60.0 <= off2 <= 533.5
    assert canonical_candidate_dedup_key(c1[0]) != canonical_candidate_dedup_key(c2[0])


def test_f_archive_short_query_tier_runs_only_when_full_zero(tmp_path: Path):
    """(f) Short 1-2 word core English query tier executes for archive sources only after full query yields 0 hits."""
    svc = VisualService(tmp_path)
    prov = InternetArchiveVisualProvider(cache=GlobalMediaCache(tmp_path / "cache"))
    shot = Shot(
        shot_id="s1",
        chapter_index=1,
        shot_index=1,
        duration_seconds=5.0,
        search_query="cinematic illustration 4k ancient harbor lantern storm",
        search_query_en="cinematic illustration 4k ancient harbor lantern storm",
        search_queries_en=["cinematic illustration 4k ancient harbor lantern storm"],
    )
    tiers = svc._provider_query_tiers("internet_archive", [(shot.search_query_en, None)])
    other_tiers = svc._provider_query_tiers("wikimedia", [(shot.search_query_en, None)])
    assert any(cat == "archive_short" and "illustration" not in q and "4k" not in q for q, cat in tiers)
    assert all(cat != "archive_short" for _, cat in other_tiers)

    queries_tried = []

    def fake_search(query, target_duration, **kw):
        queries_tried.append(query)
        if len(queries_tried) == 1:
            return [], 0, []
        return [
            prov._score_and_filter_items(
                [
                    {
                        "candidate_id": "ia_SpeechSt1949_seg0",
                        "source_type": "stock_video",
                        "url": "https://archive.org/download/SpeechSt1949/SpeechSt1949.mp4",
                        "width": 640,
                        "height": 480,
                        "duration_seconds": 6.5,
                        "author": "Coronet",
                        "author_url": "https://archive.org/details/SpeechSt1949",
                        "source_url": "https://archive.org/details/SpeechSt1949",
                        "license": "Public Domain",
                        "title": "Ancient Harbor Lantern Film",
                        "tags": ["ancient", "harbor", "lantern", "storm"],
                    }
                ],
                query=query,
                limit=3,
                excluded_ids=None,
                required_subject_terms=None,
                blocked_terms=None,
                visual_keywords=None,
                negative_terms=None,
                anchor_terms=None,
                avoid_terms=None,
                min_relevance_score=0.2,
            )[0][0]
        ], 1, []

    with patch.object(prov, "search_with_diagnostics", side_effect=fake_search):
        cands, _, attempts = svc._search_single_provider_tiers(
            prov,
            "internet_archive",
            shot,
            tiers,
            limit=3,
            excluded_ids=None,
            req_terms=[],
            block_terms=[],
            neg_terms=[],
            proj_anchors=None,
            proj_avoids=[],
            min_rel_score=0.2,
            visual_kws=[],
            narration_txt="",
            strict_ai_relevance=False,
        )

    assert len(cands) == 1
    assert len(queries_tried) == 2
    assert queries_tried[1] == "ancient harbor"
    assert len(attempts) == 2


def test_g_smithsonian_zero_requests_without_key_and_auth_blocked_on_403(tmp_path: Path):
    """(g) Smithsonian makes 0 requests without BYOK key; 403 activates process-level auth_blocked until key changes."""
    reset_smithsonian_auth_block()
    cache = GlobalMediaCache(tmp_path / "cache")

    with patch("app.services.longform.config.get_smithsonian_api_key", return_value=""):
        prov_no_key = SmithsonianVisualProvider(cache=cache)
        with patch("app.services.longform.visual.free_providers.requests.get") as req_spy:
            cands, total, rej = prov_no_key.search_with_diagnostics("harbor", 5.0)
            assert cands == [] and total == 0
            assert req_spy.call_count == 0
            assert "inactive" in rej[0].lower()

    prov_bad = SmithsonianVisualProvider(cache=cache, api_key="SI_TEST_SECRET_BAD1")
    http_calls = {"count": 0}

    def fake_403(*a, **kw):
        http_calls["count"] += 1
        return SimpleNamespace(
            status_code=403,
            headers={},
            text='{"error":{"code":"API_KEY_INVALID","message":"SI_TEST_SECRET_BAD1 rejected"}}',
            json=lambda: {},
        )

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=fake_403):
        with pytest.raises(SmithsonianAuthBlockedError) as exc_info:
            prov_bad.search_with_diagnostics("harbor", 5.0)
        assert "SI_TEST_SECRET_BAD1" not in str(exc_info.value)
        assert get_smithsonian_auth_status("SI_TEST_SECRET_BAD1")["auth_blocked"] is True
        # Subsequent call with same key must short-circuit with 0 HTTP requests
        with pytest.raises(SmithsonianAuthBlockedError):
            prov_bad.search_with_diagnostics("palace", 5.0)
        assert http_calls["count"] == 1

    # Different key resets block status cleanly
    assert get_smithsonian_auth_status("SI_TEST_NEW_KEY_2")["auth_blocked"] is False


def test_h_smithsonian_rejects_limited_use_accepts_only_cc0(tmp_path: Path):
    """(h) Limited Use / Permission required trap is rejected even when metadata_usage=CC0."""
    reset_smithsonian_auth_block()
    cache = GlobalMediaCache(tmp_path / "cache")
    prov = SmithsonianVisualProvider(cache=cache, api_key="SI_VALID_KEY_TEST")

    limited_use_trap = {
        "id": "nmaahc_2016.31.3.22.1abc",
        "title": "Ghost Dance Performance",
        "content": {
            "descriptiveNonRepeating": {
                "data_source": "National Museum of African American History and Culture",
                "metadata_usage": {"access": "CC0"},
                "online_media": {
                    "media": [
                        {
                            "type": "Images",
                            "usage": {"access": "Not determined"},
                            "width": 2000,
                            "height": 1200,
                            "resources": [
                                {"label": "High-resolution JPEG", "url": "https://ids.si.edu/trap.jpg", "width": 2000, "height": 1200}
                            ],
                        }
                    ]
                },
            },
            "indexedStructured": {"online_media_rights": ["Limited Use"]},
            "freetext": {"objectRights": [{"content": "© Studio Holder. Permission required for use."}]},
        },
    }

    non_cc0_silent_row = {
        "id": "si_non_cc0_silent",
        "title": "Ghost Harbor Painting Non-CC0",
        "content": {
            "descriptiveNonRepeating": {
                "data_source": "Smithsonian American Art Museum",
                "metadata_usage": {"access": "CC0"},
                "online_media": {
                    "media": [
                        {
                            "type": "Images",
                            "usage": {"access": "Not determined"},
                            "width": 2000,
                            "height": 1200,
                            "resources": [
                                {"label": "High-resolution JPEG", "url": "https://ids.si.edu/noncc0.jpg", "width": 2000, "height": 1200}
                            ],
                        }
                    ]
                },
            },
        },
    }

    with patch(
        "app.services.longform.visual.archive_providers.cached_json_get",
        return_value={"response": {"rows": [limited_use_trap, non_cc0_silent_row, _d1_smithsonian_chsdm_row()]}},
    ):
        cands, total, rejected = prov.search_with_diagnostics("ghost harbor", 5.0, limit=5)

    assert total == 3
    assert len(cands) == 1
    assert cands[0].candidate_id.startswith("si_ld1-")
    assert cands[0].url.endswith("CHSDM-BDD4E32E62C72-000001.jpg")
    assert cands[0].license == "CC0 1.0"
    assert any("nmaahc_2016.31.3.22.1abc" in r for r in rejected)
    assert any("si_non_cc0_silent" in r for r in rejected)


def test_i_credits_md_formats_internet_archive_and_smithsonian(tmp_path: Path):
    """(i) credits.md writes complete attribution for IA (Prelinger CC BY + PD) and Smithsonian CC0."""
    manifest = ProjectManifest(
        project_id="folklore_test",
        title="Legenda Pelabuhan",
        preset_id="mystery_folklore",
        preset_snapshot={},
        chapters=[
            Chapter(
                chapter_index=1,
                chapter_title="Opening",
                shots=[
                    Shot(
                        shot_id="ch01_s01",
                        chapter_index=1,
                        shot_index=1,
                        actual_source="internet_archive",
                        source_candidate_id="ia_SpeechSt1949_seg0",
                        asset_path="assets/visual/ch01_s01.mp4",
                        rendered_clip="clips/ch01_s01.mp4",
                        attribution_title="Speech on Stage Harbor 1949",
                        attribution_author="Coronet Instructional Films",
                        attribution_license="CC BY 4.0",
                        attribution_url="https://archive.org/details/SpeechSt1949",
                        requires_attribution=True,
                    ),
                    Shot(
                        shot_id="ch01_s02",
                        chapter_index=1,
                        shot_index=2,
                        actual_source="smithsonian",
                        source_candidate_id="si_ld1-chsdm",
                        asset_path="assets/visual/ch01_s02.jpg",
                        rendered_clip="clips/ch01_s02.mp4",
                        attribution_title="Illustration for Hamlet Ghost",
                        attribution_author="Cooper Hewitt, Smithsonian Design Museum",
                        attribution_license="CC0 1.0",
                        attribution_url="https://collections.si.edu/search/detail/edanmdm:ld1-chsdm",
                        requires_attribution=False,
                    ),
                ],
            )
        ],
    )
    cm = CreditsManager(tmp_path)
    md_path = cm.write_credits_markdown(manifest)
    content = md_path.read_text(encoding="utf-8")
    assert "Prelinger Archives / Internet Archive" in content
    assert "Coronet Instructional Films" in content
    assert "https://archive.org/details/SpeechSt1949" in content
    assert "Smithsonian Open Access" in content
    assert "Cooper Hewitt, Smithsonian Design Museum" in content
    assert "CC BY 4.0" in content
    assert "CC0 1.0" in content


def test_j_prefetch_preserves_request_count_under_cache_and_lambat_profile(tmp_path: Path, monkeypatch):
    """(j) CA1 search prefetch window (lambat=1 vs normal=3) adds 0 extra HTTP requests per source."""
    def _build_manifest() -> ProjectManifest:
        shots = [
            Shot(
                shot_id=f"ch01_s{i:02d}",
                chapter_index=1,
                shot_index=i,
                duration_seconds=5.0,
                search_query="harbor",
                search_query_en="harbor",
                search_queries_en=["harbor"],
            )
            for i in range(1, 4)
        ]
        m = ProjectManifest(
            project_id="prefetch_test",
            title="Harbor Archive",
            preset_id="documentary_en",
            preset_snapshot={},
            visual_anchor_terms=["harbor"],
            chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=shots)],
        )
        return m

    counts_by_profile = {}
    for prof in ("lambat", "normal"):
        proj_dir = tmp_path / prof
        proj_dir.mkdir(parents=True, exist_ok=True)
        cache = GlobalMediaCache(proj_dir / "cache")
        svc = VisualService(proj_dir)
        svc.cache = cache
        manifest = _build_manifest()

        http_calls = {"search": 0, "meta": 0}

        def fake_get(url, params=None, **kw):
            if "advancedsearch.php" in url:
                http_calls["search"] += 1
                return _fake_http_response({"response": {"docs": [_d1_ia_prelinger_search_doc("SpeechSt1949")]}})
            http_calls["meta"] += 1
            return _fake_http_response(_d1_ia_prelinger_meta("SpeechSt1949", duration="600.0"))

        monkeypatch.setenv("EFS_NETWORK_PROFILE", prof)
        with patch("app.services.longform.config.get_network_profile_settings", return_value={"profile": prof}):
            with patch("app.services.longform.visual.free_providers.requests.get", side_effect=fake_get):
                with patch("app.services.longform.visual.archive_providers.subprocess.run") as ff_mock:
                    def _mk_vid(cmd, **kw):
                        p = Path(cmd[-1])
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_bytes(b"mp4")
                        return SimpleNamespace(returncode=0, stderr="")
                    ff_mock.side_effect = _mk_vid
                    with patch("app.services.longform.visual.service.ShotClipBuilder") as bld_cls:
                        bld_cls.return_value.build_shot_clip.side_effect = lambda asset_path, out_path, **kw: (
                            Path(out_path).parent.mkdir(parents=True, exist_ok=True) or Path(out_path).write_bytes(b"clip") or Path(out_path)
                        )
                        svc.fetch_all(manifest, provider_name=None, providers_order=["internet_archive"])
        counts_by_profile[prof] = (http_calls["search"], http_calls["meta"])

    # Prefetch=3 (normal) emits zero extra HTTP requests vs sequential prefetch=1 (lambat)
    assert counts_by_profile["lambat"] == (1, 1)
    assert counts_by_profile["normal"] == counts_by_profile["lambat"]


def test_k_no_demo_key_or_embedded_dev_key_in_production_code():
    """(k) Zero DEMO_KEY or hardcoded Smithsonian developer key exists in production code."""
    api_data_gov_token_re = re.compile(r"DEMO_KEY|X-Api-Key\s*:\s*['\"]?[A-Za-z0-9]{16,}")
    for py_file in Path("app").rglob("*.py"):
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        assert "DEMO_KEY" not in content, f"Forbidden DEMO_KEY literal in {py_file}"
        assert not api_data_gov_token_re.search(content), f"Suspicious hardcoded key in {py_file}"
