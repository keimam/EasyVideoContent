"""Tests for V101-A1 items (A1.1 calibration, A1.2 dedup/thumbnails, A1.3 AI keywords/fallback, A1.4 project switching, A1.5 UI/credits)."""

from __future__ import annotations

from app.services.longform.visual.stock import (
    extract_narration_proper_nouns,
    resolve_source_prior_weight,
    score_candidate_relevance,
    score_candidate_relevance_detailed,
)


def test_a1_1_apprentice_rejects_us_navy_ship_and_scores_below_pixabay():
    """A1.1a: Title-based US Navy Seaman Apprentice caption must not beat Pixabay or pass as high score."""
    narration = "His apprentice, Minokichi, was eighteen."
    anchors = ["ferry", "boat", "river", "woman", "white dress", "fog", "water", "dock", "mist", "silhouette"]
    avoids = ["car", "road", "highway", "bridge", "cityscape", "skyscraper", "traffic", "airplane", "airport", "train"]

    navy_tags = [
        "us", "navy", "110329-n-om503-075", "seaman", "apprentice",
        "johnathan", "wilson", "steadies", "boatswain", "mate",
        "amphibious", "dock", "landing", "ship", "ferry",
    ]
    navy_det = score_candidate_relevance_detailed(
        candidate_tags=navy_tags,
        search_query="ferry apprentice",
        anchor_terms=anchors,
        avoid_terms=avoids,
        narration_text=narration,
        source_name="wikimedia",
        recommended_visual_sources={"stock": 0.7, "ai_image": 0.3},
    )
    assert navy_det["score"] < 0.35 or navy_det["is_valid"] is False
    assert "subject" in navy_det["components"]
    assert "query_overlap" in navy_det["components"]
    assert "narration_bonus" in navy_det["components"]
    assert "penalties" in navy_det["components"]

    # Non-military Wikimedia portrait matching only generic word "apprentice" + 1 generic "young" must still stay low
    generic_wiki = score_candidate_relevance_detailed(
        candidate_tags=["portrait", "young", "apprentice", "man", "street", "river"],
        search_query="young apprentice",
        anchor_terms=anchors,
        avoid_terms=avoids,
        narration_text=narration,
        source_name="wikimedia",
    )
    assert generic_wiki["score"] <= 0.35

    pixabay_ferry = score_candidate_relevance_detailed(
        candidate_tags=["ferry", "river", "boat", "mist", "water"],
        search_query="ferry apprentice",
        anchor_terms=anchors,
        avoid_terms=avoids,
        narration_text=narration,
        source_name="pixabay",
        recommended_visual_sources={"stock": 0.7, "ai_image": 0.3},
    )
    assert pixabay_ferry["is_valid"] is True
    assert pixabay_ferry["score"] > generic_wiki["score"]
    assert pixabay_ferry["score"] > navy_det["score"]


def test_a1_1_musashi_province_hokusai_still_allowed_to_win():
    """A1.1a positive check: Wikimedia/Openverse Hokusai print with Musashi Province still scores high."""
    narration = "In a village in Musashi Province, west of what is now Tokyo, two woodcutters walked to the forest each morning and came back each evening."
    anchors = ["ferry", "boat", "river", "woman", "white dress", "fog", "water", "dock", "mist", "silhouette"]

    hokusai_tags = [
        "tsukudajima", "musashi", "province", "fugaku", "fuji", "hokusai",
        "woodblock", "print", "village", "river", "boat", "japan",
    ]
    hokusai_ok, hokusai_score, _ = score_candidate_relevance(
        candidate_tags=hokusai_tags,
        search_query="village musashi province",
        anchor_terms=anchors,
        narration_text=narration,
        source_name="wikimedia",
    )
    generic_pixabay_ok, generic_pixabay_score, _ = score_candidate_relevance(
        candidate_tags=["village", "hills", "mist", "lake", "nature"],
        search_query="village musashi province",
        anchor_terms=anchors,
        narration_text=narration,
        source_name="pixabay",
    )
    assert hokusai_ok is True
    assert generic_pixabay_ok is True
    assert hokusai_score >= 0.72
    assert hokusai_score >= generic_pixabay_score


def test_a1_1_proper_nouns_do_not_inflate_or_penalize_scores():
    """A1.1b: Character proper nouns (Mosaku, Minokichi) must neither raise nor drop scores."""
    detected_m = extract_narration_proper_nouns("Mosaku was an old man.")
    detected_k = extract_narration_proper_nouns("His apprentice, Minokichi, was eighteen.")
    assert "mosaku" in detected_m
    assert "minokichi" in detected_k
    assert "musashi" not in extract_narration_proper_nouns("In a village in Musashi Province.")

    tags_no_name = ["old", "man", "elderly", "woodcutter", "forest", "river"]
    tags_with_name = ["old", "man", "elderly", "woodcutter", "forest", "river", "mosaku", "minokichi"]

    _, sc_query_with_name, _ = score_candidate_relevance(
        candidate_tags=tags_no_name,
        search_query="old man mosaku",
        narration_text="Mosaku was an old man.",
        source_name="pixabay",
    )
    _, sc_query_without_name, _ = score_candidate_relevance(
        candidate_tags=tags_no_name,
        search_query="old man",
        narration_text="An old man.",
        source_name="pixabay",
    )
    assert sc_query_with_name == sc_query_without_name

    _, sc_tag_with_name, _ = score_candidate_relevance(
        candidate_tags=tags_with_name,
        search_query="old man mosaku minokichi",
        narration_text="Mosaku was an old man. His apprentice, Minokichi, was eighteen.",
        source_name="wikimedia",
    )
    _, sc_tag_no_name, _ = score_candidate_relevance(
        candidate_tags=tags_no_name,
        search_query="old man mosaku minokichi",
        narration_text="Mosaku was an old man. His apprentice, Minokichi, was eighteen.",
        source_name="wikimedia",
    )
    assert sc_tag_with_name == sc_tag_no_name


def test_a1_1_recommended_visual_sources_weighting_non_destructive():
    """A1.1c: recommended_visual_sources adjusts source prior without disabling or zeroing any source."""
    rec = {"stock": 0.7, "ai_image": 0.3}
    w_pix = resolve_source_prior_weight("pixabay", rec)
    w_wiki = resolve_source_prior_weight("wikimedia", rec)
    assert w_pix > w_wiki

    tags = ["winter", "forest", "snowstorm", "river", "fog"]
    ok_pix, sc_pix, _ = score_candidate_relevance(
        candidate_tags=tags,
        search_query="winter forest snowstorm",
        narration_text="One winter evening a snowstorm caught them on the far river bank.",
        source_name="pixabay",
        recommended_visual_sources=rec,
    )
    ok_wiki, sc_wiki, _ = score_candidate_relevance(
        candidate_tags=tags,
        search_query="winter forest snowstorm",
        narration_text="One winter evening a snowstorm caught them on the far river bank.",
        source_name="wikimedia",
        recommended_visual_sources=rec,
    )
    assert ok_pix is True and ok_wiki is True
    assert sc_pix > sc_wiki > 0.45


def test_a1_2_dedup_wikimedia_and_cross_provider_canonical():
    """A1.2: Deduplicate identical Commons files across Wikimedia tiers, Openverse, and refresh."""
    from app.services.longform.visual.base import (
        VisualCandidate,
        canonical_candidate_dedup_key,
        deduplicate_candidates,
    )

    c_wiki_thumb = VisualCandidate(
        candidate_id="wikimedia_14751776",
        provider="wikimedia",
        source_type="stock_image",
        url="https://upload.wikimedia.org/wikipedia/commons/thumb/1/12/Village_Bridge.jpg/1920px-Village_Bridge.jpg",
        thumbnail_url="https://upload.wikimedia.org/wikipedia/commons/thumb/1/12/Village_Bridge.jpg/400px-Village_Bridge.jpg",
        source_url="https://commons.wikimedia.org/wiki/File:Village_Bridge.jpg",
    )
    c_wiki_dup_id_diff_size = VisualCandidate(
        candidate_id="wikimedia_99999999",
        provider="wikimedia",
        source_type="stock_image",
        url="https://upload.wikimedia.org/wikipedia/commons/thumb/1/12/Village_Bridge.jpg/800px-Village_Bridge.jpg",
        source_url="https://commons.wikimedia.org/wiki/File:Village_Bridge.jpg",
    )
    c_openverse_same = VisualCandidate(
        candidate_id="openverse_abcd-1234",
        provider="openverse",
        source_type="stock_image",
        url="https://upload.wikimedia.org/wikipedia/commons/1/12/Village_Bridge.jpg",
        thumbnail_url="https://api.openverse.org/v1/images/abcd-1234/thumb/",
        source_url="https://commons.wikimedia.org/wiki/File:Village_Bridge.jpg",
    )
    c_distinct = VisualCandidate(
        candidate_id="pixabay_159022",
        provider="pixabay",
        source_type="stock_video",
        url="https://cdn.pixabay.com/video/2023/04/15/159022_small.mp4",
        thumbnail_url="https://cdn.pixabay.com/video/2023/04/15/159022_small.jpg",
    )

    assert canonical_candidate_dedup_key(c_wiki_thumb) == canonical_candidate_dedup_key(c_wiki_dup_id_diff_size)
    assert canonical_candidate_dedup_key(c_wiki_thumb) == canonical_candidate_dedup_key(c_openverse_same)

    deduped = deduplicate_candidates([c_wiki_thumb, c_wiki_dup_id_diff_size, c_openverse_same, c_distinct])
    assert len(deduped) == 2
    assert [d.candidate_id for d in deduped] == ["wikimedia_14751776", "pixabay_159022"]


def test_a1_2_candidate_thumbnail_fetches_remote_image_without_ffmpeg(tmp_path):
    """A1.2: get_candidate_thumbnail_path uses thumbnail_url HTTP image fetch cleanly."""
    import io
    from unittest.mock import MagicMock, patch
    from PIL import Image
    from app.services.longform.ui.components import get_candidate_thumbnail_path

    buf = io.BytesIO()
    Image.new("RGB", (640, 360), color=(30, 80, 140)).save(buf, format="JPEG")
    fake_jpg = buf.getvalue()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_jpg

    cand_dict = {
        "candidate_id": "wikimedia_123456",
        "provider": "wikimedia",
        "source_type": "stock_image",
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/TestScene.jpg",
        "thumbnail_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/TestScene.jpg/640px-TestScene.jpg",
    }
    with patch("requests.get", return_value=mock_resp) as m_get:
        thumb = get_candidate_thumbnail_path(tmp_path, cand_dict)
        assert thumb is not None and thumb.is_file() and thumb.stat().st_size > 0
        m_get.assert_called_once()
        with Image.open(thumb) as im:
            assert im.size[0] == 320


def test_a1_3_json_recovery_missing_key_retry_and_specific_reasons(tmp_path):
    """A1.3: Recover wrapped/truncated JSON, retry omitted keys, and report explicit reasons."""
    from unittest.mock import patch
    from app.services.longform.schema import Chapter, Segment, Shot, TtsGroup
    from app.services.longform.visual.ai_keywords import (
        _extract_json_any_dict,
        format_keyword_reason,
        generate_chapter_visual_keywords_ai,
    )

    wrapped = '<think>thinking...</think>\n```json\n{"shots": {"ch01_shot001": ["snowstorm forest river"]}}\n```'
    assert _extract_json_any_dict(wrapped) == {"ch01_shot001": ["snowstorm forest river"]}

    truncated = '{"ch01_shot001": ["old wooden bridge", "misty river"], "ch01_shot002": ["incomplete'
    salvaged = _extract_json_any_dict(truncated)
    assert "ch01_shot001" in salvaged

    ch = Chapter(
        chapter_index=1,
        chapter_title="Ch 1",
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="Winter river snowstorm. Wooden ferry boat.",
                segments=[
                    Segment(segment_index=1, narration_text="Winter river snowstorm."),
                    Segment(segment_index=2, narration_text="Wooden ferry boat."),
                ],
            )
        ],
        shots=[
            Shot(shot_id="ch01_shot001", chapter_index=1, shot_index=1, segment_indices=[1]),
            Shot(shot_id="ch01_shot002", chapter_index=1, shot_index=2, segment_indices=[2]),
        ],
    )

    # Simulate partial batch JSON followed by targeted retry recovering shot002
    responses = [
        ('{"ch01_shot001": ["winter river snowstorm dusk", "snowy river bank trees"]}', "openrouter", "openrouter/free"),
        ('{"ch01_shot002": ["wooden ferry boat river", "empty boat dock mist"]}', "openrouter", "openrouter/free"),
    ]
    with (
        patch("app.services.longform.visual.ai_keywords.get_llm_readiness", return_value={"configured": True, "is_invalid": False}),
        patch("app.services.longform.visual.ai_keywords._invoke_translation_llm", side_effect=responses),
    ):
        res = generate_chapter_visual_keywords_ai(ch.shots, ch, project_lang="en-US", cache_dir=tmp_path)
    assert res["llm_calls"] == 2
    assert ch.shots[0].search_query_source == "ai"
    assert ch.shots[1].search_query_source == "ai"
    assert "ferry" in (ch.shots[1].search_query_en or "").lower()

    # Check localized explicit reasons (id & en)
    assert "tidak berformat JSON" in format_keyword_reason("ai_invalid_json", lang="id")
    assert "not valid JSON" in format_keyword_reason("ai_invalid_json", lang="en")
    assert "Kuota" in format_keyword_reason("ai_quota_exceeded", lang="id")
    assert "quota" in format_keyword_reason("ai_quota_exceeded", lang="en").lower()
    assert "respons kosong" in format_keyword_reason("ai_empty_response", lang="id")


def test_a1_3_fallback_extractor_strips_character_proper_nouns_keeps_places():
    """A1.3: Fallback extractor excludes non-visual character proper nouns (Mosaku, Minokichi)."""
    from app.services.longform.visual.query_extractor import extract_short_search_query

    q_mosaku = extract_short_search_query("Mosaku was an old man.", visual_keywords=["forest mist"])
    q_minokichi = extract_short_search_query("His apprentice, Minokichi, was eighteen.", visual_keywords=["young worker", "river"])
    q_musashi = extract_short_search_query("In a village in Musashi Province, two woodcutters walked to the forest.")

    assert "mosaku" not in q_mosaku.lower()
    assert "minokichi" not in q_minokichi.lower()
    assert "eighteen" not in q_minokichi.lower()
    assert "musashi" in q_musashi.lower() and "province" in q_musashi.lower()


def test_a1_4_switch_project_a_to_b_via_back_and_sidebar_reset(tmp_path, monkeypatch):
    """A1.4: Returning from Project A via Back or sidebar Long Video then opening Project B opens B cleanly."""
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from app.services.longform.store import create_project

    proj_root = tmp_path / "projects_a14"
    proj_root.mkdir()
    create_project("proj_alpha", "Project Alpha Title", "umum.en", language="en-US", projects_root=proj_root)
    create_project("proj_beta", "Project Beta Title", "umum.en", language="en-US", projects_root=proj_root)

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)

    app_file = str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")
    at = AppTest.from_file(app_file, default_timeout=20)
    at.run()
    assert not at.exception

    # 1. Open Project A
    at.button(key="btn_open_proj_alpha").click().run()
    assert not at.exception
    assert any("Project Alpha Title" in t.value for t in at.title)
    assert at.query_params.get("project") in ("proj_alpha", ["proj_alpha"])

    # 2. Return via sidebar "Video Panjang" reset button -> back on project home
    at.button(key="efs_btn_lv_home_reset").click().run()
    assert not at.exception
    assert "project" not in at.query_params
    assert any(b.key == "btn_open_proj_beta" for b in at.button)

    # 3. Open Project B -> must open B, NOT A
    at.button(key="btn_open_proj_beta").click().run()
    assert not at.exception
    assert any("Project Beta Title" in t.value for t in at.title)
    assert not any("Project Alpha Title" in t.value for t in at.title)
    assert at.query_params.get("project") in ("proj_beta", ["proj_beta"])

    # 4. Return via "← All projects" button -> open Project A again -> back -> open B
    at.button(key="btn_back_proj_beta").click().run()
    assert not at.exception
    assert "project" not in at.query_params
    at.button(key="btn_open_proj_alpha").click().run()
    assert any("Project Alpha Title" in t.value for t in at.title)
    at.button(key="btn_back_proj_alpha").click().run()
    at.button(key="btn_open_proj_beta").click().run()
    assert any("Project Beta Title" in t.value for t in at.title)


def test_a1_5_mode_captions_priority_vs_mix_best_and_pixabay_credits(tmp_path, monkeypatch):
    """A1.5: Source order caption shown only in Priority; Mix Best shows explanation; credits omit shot_id."""
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from app.services.longform.schema import Chapter, ItemStatus, Segment, Shot, TtsGroup
    from app.services.longform.store import create_project, save_manifest
    from app.services.longform.visual.credits import CreditsManager

    proj_root = tmp_path / "projects_a15"
    proj_root.mkdir()
    manifest, p_dir = create_project("proj_a15", "A15 Mode Test", "umum.id", language="id-ID", projects_root=proj_root)
    manifest.visual_search_mode = "mix_best"
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Bab 1",
            audio_duration_seconds=4.0,
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="River boat at dusk.",
                    segments=[Segment(segment_index=1, narration_text="River boat at dusk.", duration_seconds=4.0)],
                )
            ],
            shots=[
                Shot(
                    shot_id="ch01_shot002",
                    chapter_index=1,
                    shot_index=2,
                    segment_indices=[1],
                    duration_seconds=4.0,
                    target_frames=100,
                    actual_source="pixabay",
                    asset_path="assets/visual/shots/ch01_shot002_asset.mp4",
                    attribution_author="PixabayUser",
                    attribution_license="Pixabay Content License",
                    attribution_title=None,
                    tags=["river", "boat", "dusk"],
                    status=ItemStatus.completed,
                )
            ],
        )
    ]
    save_manifest(manifest, p_dir)

    # Credits markdown must not print shot_id ('ch01_shot002') as asset title
    cm = CreditsManager(p_dir)
    md_path = cm.write_credits_markdown(manifest)
    md_txt = md_path.read_text(encoding="utf-8")
    assert "[pixabay]: ch01_shot002" not in md_txt

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)
    app_file = str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")

    at = AppTest.from_file(app_file, default_timeout=20)
    at.query_params["project"] = "proj_a15"
    at.query_params["step"] = "visual"
    at.run()
    assert not at.exception
    # Mix Best shows concise Mix Best explanation and omits Priority source order caption
    mix_caps = "\n".join(c.value for c in at.caption)
    assert "Mix Terbaik" in mix_caps or "Mix Best" in mix_caps
    assert "Pixabay → Coverr → Wikimedia" not in mix_caps

    # Switching to Priority shows Priority source order caption and hides Mix Best caption
    at.radio(key="rad_vis_mode_proj_a15").set_value("priority").run()
    assert not at.exception
    prio_caps = "\n".join(c.value for c in at.caption)
    assert "Pixabay → Coverr → Wikimedia" in prio_caps
    assert "Mix Terbaik" not in prio_caps and "Mix Best" not in prio_caps





