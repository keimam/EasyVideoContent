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

