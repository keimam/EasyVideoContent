"""Tests for V102-F v4 (Visual Bible, Continuous Scenes, Prompt Composer/Validator, UI & Exports)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services.longform.schema import (
    Chapter,
    ItemStatus,
    ProjectManifest,
    SceneContinuity,
    Segment,
    Shot,
    TtsGroup,
    VisualBible,
    VisualBibleChapterOverride,
    VisualBibleCharacter,
    VisualBibleLocation,
)
from app.services.longform.store import create_project, load_manifest, save_manifest
from app.services.longform.visual.external_bridge import (
    apply_bulk_external_import,
    compose_shot_external_prompt,
    export_external_image_prompts,
    plan_bulk_external_import,
    validate_external_prompt,
)
from app.services.longform.visual.scene_continuity import assign_scenes_and_prompts_for_shots
from app.services.longform.visual.visual_bible import ensure_project_visual_bible


class _FakeLLM:
    """Mock LLMAdapter recording all prompts and returning queued JSON responses."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def generate(self, prompt: str, max_tokens: int = 1024):
        self.calls.append(prompt)
        content = self._responses.pop(0) if self._responses else "{}"
        return SimpleNamespace(content=content, provider="mock", model="mock-llm")


def _make_manifest_with_narration(
    proj_dir: Path,
    narrations: list[str],
    *,
    title: str = "Folklore Story",
    topic: str = "19th century Slavic folktale",
    preset_id: str = "umum.en",
    preset_snapshot: dict | None = None,
    visual_bible: VisualBible | None = None,
) -> ProjectManifest:
    proj_dir.mkdir(parents=True, exist_ok=True)
    segs = [Segment(segment_index=i, narration_text=txt, duration_seconds=4.0) for i, txt in enumerate(narrations, start=1)]
    shots = [
        Shot(
            shot_id=f"ch01_shot{i:03d}",
            chapter_index=1,
            shot_index=i,
            segment_indices=[i],
            duration_seconds=4.0,
            target_frames=100,
            visual_prompt=txt,
            search_query="generic search phrase",
            search_query_en="generic search phrase",
            status=ItemStatus.pending,
        )
        for i, txt in enumerate(narrations, start=1)
    ]
    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1",
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text=" ".join(narrations),
                segments=segs,
                status=ItemStatus.completed,
            )
        ],
        shots=shots,
        status=ItemStatus.completed,
    )
    manifest = ProjectManifest(
        project_id=proj_dir.name,
        title=title,
        topic=topic,
        preset_id=preset_id,
        preset_snapshot=preset_snapshot or {"name": "Storytelling", "min_relevance_score": 0.35},
        visual_bible=visual_bible,
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)
    return manifest


def test_f6_a_flashback_uses_young_character_and_no_photographic_still(tmp_path: Path):
    """F6(a): Flashback narration uses younger flashback character entry and omits hardcoded photographic documentary still."""
    proj_dir = tmp_path / "f6_a"
    vb = VisualBible(
        world="19th-century rural winter village",
        style="painterly historical storybook illustration",
        palette_lighting="muted winter greys and warm candlelit amber",
        characters=[
            VisualBibleCharacter(
                id="vasilisa",
                name="Vasilisa",
                aliases=["you", "your", "she"],
                role="protagonist",
                description="a 14-year-old Russian peasant girl, long fair braid, red sarafan over linen shirt, embroidered headscarf",
            ),
            VisualBibleCharacter(
                id="vasilisa_child8",
                name="Young Vasilisa",
                aliases=["you (eight years old flashback)", "eight", "mother died when you were eight"],
                role="flashback_younger_self",
                description="an 8-year-old Russian peasant girl, short fair braids, faded wool dress, tearful eyes",
            ),
        ],
        locations=[
            VisualBibleLocation(id="peasant_cottage_bedside", name="Peasant Cottage Bedside", description="dim timber cottage bedroom with woven blanket and flickering tallow candle"),
        ],
        avoid="modern buildings, cars, electric lights, plastic, modern clothing, text, watermark",
        source="ai",
    )
    manifest = _make_manifest_with_narration(
        proj_dir,
        ["Your mother died when you were eight, whispering farewell by candlelight."],
        visual_bible=vb,
    )
    llm = _FakeLLM([
        json.dumps({
            "shots": [
                {
                    "shot_id": "ch01_shot001",
                    "scene_id": "ch01_scene01",
                    "location_id": "peasant_cottage_bedside",
                    "time_of_day": "night",
                    "weather": "calm winter night",
                    "lighting": "warm candlelit bedside",
                    "props": ["carved wooden doll"],
                    "character_state": "grieving in faded dress",
                    "characters_in_shot": ["vasilisa_child8"],
                    "scene_en": "Young Vasilisa kneels weeping beside her dying mother's wooden bed by flickering candlelight",
                }
            ]
        })
    ])

    exp = export_external_image_prompts(
        manifest,
        proj_dir,
        llm_adapter=llm,
        cache_dir=tmp_path / "cache",
    )
    prompt = exp["rows"][0]["prompt_en"]
    assert "an 8-year-old Russian peasant girl" in prompt
    assert "a 14-year-old Russian peasant girl" not in prompt
    assert "photographic documentary still" not in prompt.lower()

    # Non-LLM alias matching also picks the flashback entry on "when you were eight"
    sh = manifest.chapters[0].shots[0]
    sh.scene_id = None
    sh.scene_en = None
    sh.characters_in_shot = []
    exp_fb = export_external_image_prompts(manifest, proj_dir, auto_prepare_llm=False, cache_dir=tmp_path / "cache2")
    assert exp_fb["rows"][0]["characters_in_shot"] == "vasilisa_child8"
    assert "an 8-year-old Russian peasant girl" in exp_fb["rows"][0]["prompt_en"]
    assert "photographic documentary still" not in exp_fb["rows"][0]["prompt_en"].lower()


def test_f6_b_sequential_shots_identical_character_description_and_no_standalone_boy_child(tmp_path: Path):
    """F6(b): Sequential 'you carry' & 'you leave' shots embed word-for-word identical female character description without standalone child/boy."""
    proj_dir = tmp_path / "f6_b"
    locked_desc = "a 14-year-old village girl, long braided chestnut hair, crimson patterned apron over homespun tunic, silver clasp"
    vb = VisualBible(
        world="19th-century forested borderland village",
        style="textured oil-painted folktale illustration",
        palette_lighting="twilight cobalt blues and hearth amber",
        characters=[
            VisualBibleCharacter(
                id="protagonist",
                name="Elena",
                aliases=["you", "your", "she"],
                role="protagonist",
                description=locked_desc,
            )
        ],
        locations=[
            VisualBibleLocation(id="timber_hut_door", name="Timber Hut Doorway", description="weathered timber hut porch bordered by tall pines and snowdrifts"),
        ],
        avoid="modern clothing, cars, text, watermark",
        source="ai",
    )
    manifest = _make_manifest_with_narration(
        proj_dir,
        [
            "You carry the tiny wooden doll safely tucked against your ribs.",
            "You leave the wooden hut quietly before dawn breaks.",
        ],
        visual_bible=vb,
    )
    llm = _FakeLLM([
        json.dumps({
            "shots": [
                {
                    "shot_id": "ch01_shot001",
                    "scene_id": "ch01_scene01",
                    "location_id": "timber_hut_door",
                    "time_of_day": "dawn",
                    "weather": "crisp cold mist",
                    "lighting": "pale pre-dawn glow",
                    "props": ["small carved wooden doll in apron pocket"],
                    "character_state": "dressed warmly in wool cloak",
                    "characters_in_shot": ["protagonist"],
                    "scene_en": "Elena carries the tiny wooden doll tucked carefully in her apron pocket inside the doorway",
                },
                {
                    "shot_id": "ch01_shot002",
                    "scene_id": "ch01_scene01",
                    "location_id": "timber_hut_door",
                    "time_of_day": "dawn",
                    "weather": "crisp cold mist",
                    "lighting": "pale pre-dawn glow",
                    "props": ["small carved wooden doll in apron pocket"],
                    "character_state": "dressed warmly in wool cloak",
                    "characters_in_shot": ["protagonist"],
                    "scene_en": "Elena steps over the timber threshold into the misty yard outside the hut",
                },
            ]
        })
    ])

    exp = export_external_image_prompts(manifest, proj_dir, llm_adapter=llm, cache_dir=tmp_path / "cache")
    p1 = exp["rows"][0]["prompt_en"]
    p2 = exp["rows"][1]["prompt_en"]

    expected_clause = f"Characters: Elena ({locked_desc})"
    assert expected_clause in p1
    assert expected_clause in p2
    assert p1.count(locked_desc) == 1 and p2.count(locked_desc) == 1
    assert exp["unlocked_char_count"] == 0
    for txt in (p1, p2):
        # Verify standalone boy/child never slipped into either prompt
        lowered = txt.lower()
        assert " boy " not in f" {lowered} "
        assert " child " not in f" {lowered} "


def test_f6_c_validator_flags_generic_subject_without_locked_character():
    """F6(c): Lock validator flags prompts with generic subject nouns when no locked character description exists."""
    vb = VisualBible(world="historic village", style="oil painting", characters=[])
    warn = validate_external_prompt(
        "A young child carrying a lantern walks down a muddy trail. Setting: historic village. Style: oil painting.",
        scene_en="A young child carrying a lantern walks down a muddy trail",
        locked_character_ids=[],
        bible=vb,
    )
    assert warn.startswith("⚠")
    assert "child" in warn

    warn_boy = validate_external_prompt("The boy stares at the doorway", bible=vb)
    assert warn_boy.startswith("⚠")
    assert "boy" in warn_boy


def test_f6_d_sequential_shots_same_location_id_get_identical_location_description(tmp_path: Path):
    """F6(d): Sequential shots sharing location_id receive word-for-word identical Location descriptions."""
    proj_dir = tmp_path / "f6_d"
    loc_desc = "cramped timber kitchen with a brick hearth oven, copper kettle, and wooden bench"
    vb = VisualBible(
        world="19th-century northern countryside",
        style="storybook oil painting",
        palette_lighting="warm hearth firelight",
        characters=[
            VisualBibleCharacter(id="hero", name="Anya", aliases=["she"], description="a 14-year-old girl, braided hair, wool dress"),
        ],
        locations=[
            VisualBibleLocation(id="cottage_kitchen", name="Cottage Kitchen", description=loc_desc),
        ],
        avoid="modern appliances, text",
    )
    manifest = _make_manifest_with_narration(proj_dir, ["Anya sweeps ash.", "Anya lights the hearth."], visual_bible=vb)
    for sh in manifest.chapters[0].shots:
        sh.scene_id = "ch01_scene01"
        sh.location_id = "cottage_kitchen"
        sh.characters_in_shot = ["hero"]
        sh.continuity = SceneContinuity(location_id="cottage_kitchen", time_of_day="dusk", lighting="amber firelight")
        sh.scene_en = "Anya tends the hearth inside the cottage"

    exp = export_external_image_prompts(manifest, proj_dir, cache_dir=tmp_path / "cache")
    loc_clause = f"Location: {loc_desc}."
    assert loc_clause in exp["rows"][0]["prompt_en"]
    assert loc_clause in exp["rows"][1]["prompt_en"]


def test_f6_e_legacy_manifest_without_visual_bible_loads_and_exports_via_template(tmp_path: Path):
    """F6(e): Legacy manifest lacking visual_bible loads cleanly and exports prompts via deterministic template fallback."""
    proj_dir = tmp_path / "f6_e"
    proj_dir.mkdir(parents=True, exist_ok=True)
    legacy_payload = {
        "pipeline_version": "1.0.0",
        "schema_version": "1.0.0",
        "project_id": "legacy_proj",
        "title": "Ancient Maritime Trade",
        "topic": "Ancient wooden sailing vessels",
        "aspect_ratio": "16:9",
        "preset_id": "umum.en",
        "preset_snapshot": {"visual": {"style": "classical maritime oil painting"}},
        "chapters": [
            {
                "chapter_index": 1,
                "chapter_title": "Ch1",
                "tts_groups": [
                    {
                        "group_index": 1,
                        "paragraph_text": "Merchant galleys crossed the Mediterranean harbor at sunrise.",
                        "segments": [{"segment_index": 1, "narration_text": "Merchant galleys crossed the Mediterranean harbor at sunrise."}],
                    }
                ],
                "shots": [
                    {
                        "shot_id": "ch01_shot001",
                        "chapter_index": 1,
                        "shot_index": 1,
                        "segment_indices": [1],
                        "duration_seconds": 3.5,
                        "target_frames": 88,
                        "visual_prompt": "harbor ship",
                        "search_query": "harbor ship",
                    }
                ],
            }
        ],
    }
    (proj_dir / "manifest.json").write_text(json.dumps(legacy_payload), encoding="utf-8")

    manifest = load_manifest(proj_dir)
    assert manifest.visual_bible is None

    exp = export_external_image_prompts(manifest, proj_dir, auto_prepare_llm=False, cache_dir=tmp_path / "cache")
    assert exp["count"] == 1
    assert "=== KONTEKS VISUAL ===" in exp["txt"]
    assert "classical maritime oil painting" in exp["rows"][0]["prompt_en"]
    assert "photographic documentary still" not in exp["rows"][0]["prompt_en"].lower()


def test_f6_f_bulk_import_still_matches_new_f5_exports_and_applies(tmp_path: Path):
    """F6(f): F5 .txt/.csv exports keep chXX_shotYYY & last_prompt_export_shot_ids compatibility with V3 bulk import."""
    proj_dir = tmp_path / "f6_f"
    manifest = _make_manifest_with_narration(proj_dir, ["Shot one scene.", "Shot two scene.", "Shot three scene."])
    exp = export_external_image_prompts(
        manifest,
        proj_dir,
        scope="selected",
        selected_shot_ids=["ch01_shot002", "ch01_shot003"],
        auto_prepare_llm=False,
        cache_dir=tmp_path / "cache",
    )
    assert "=== KONTEKS VISUAL ===" in exp["txt"]
    assert "== Scene ch01_scene01 ==" in exp["txt"]
    assert "[ch01_shot002]" in exp["txt"] and "[ch01_shot003]" in exp["txt"]

    csv_rows = list(csv.DictReader(io.StringIO(exp["csv"])))
    for col in ("shot_id", "scene_id", "scene_en", "characters_in_shot", "location_id", "continuity", "lock_warning", "prompt_en"):
        assert col in csv_rows[0]

    png_b = io.BytesIO()
    Image.new("RGB", (640, 360), color=(40, 120, 180)).save(png_b, format="PNG")

    plan = plan_bulk_external_import(
        manifest,
        [
            ("ch01_shot002.png", png_b.getvalue()),  # exact shot_id match
            ("flow_02.png", png_b.getvalue()),       # 2nd in export order -> ch01_shot003
        ],
    )
    assert plan[0]["matched_shot_id"] == "ch01_shot002"
    assert plan[1]["matched_shot_id"] == "ch01_shot003"

    res = apply_bulk_external_import(proj_dir, manifest, plan, rebuild=False)
    assert res["applied_shots"] == ["ch01_shot002", "ch01_shot003"]


def test_f6_g_i18n_keys_symmetric_id_and_en():
    """F6(g): All F4 external bridge i18n keys (and entire dictionary) have matching ID & EN keys and format placeholders."""
    import re
    from app.services.longform.ui.i18n import EN_TRANSLATIONS, ID_TRANSLATIONS

    assert set(ID_TRANSLATIONS.keys()) == set(EN_TRANSLATIONS.keys())
    assert ID_TRANSLATIONS["expander_external_bridge"] == "🖼️ Gambar Eksternal (Flow, dll.) — Ekspor Prompt & Impor Massal"
    assert EN_TRANSLATIONS["expander_external_bridge"] == "🖼️ External Images (Flow, etc.) — Export Prompts & Bulk Import"

    f4_keys = [
        "expander_external_bridge",
        "ext_vb_heading",
        "ext_vb_generate_ai_btn",
        "ext_vb_world_label",
        "ext_vb_style_label",
        "ext_vb_palette_label",
        "ext_vb_chars_label",
        "ext_vb_locs_label",
        "ext_vb_avoid_label",
        "ext_vb_save_btn",
        "ext_preview_heading",
        "ext_validator_summary_warn",
        "btn_ext_apply_preview",
    ]
    for k in f4_keys:
        assert k in ID_TRANSLATIONS and ID_TRANSLATIONS[k].strip()
        assert k in EN_TRANSLATIONS and EN_TRANSLATIONS[k].strip()
        ph_id = set(re.findall(r"\{([a-z0-9_]+)\}", ID_TRANSLATIONS[k]))
        ph_en = set(re.findall(r"\{([a-z0-9_]+)\}", EN_TRANSLATIONS[k]))
        assert ph_id == ph_en, f"Placeholder mismatch for key {k}: {ph_id} vs {ph_en}"


def test_f6_h_prop_continuity_carries_across_shots_until_buried(tmp_path: Path):
    """F6(h): Prop hidden in shot 1 persists in shots 2-4 Continuity even without mention, then clears after shot 5 buries it."""
    proj_dir = tmp_path / "f6_h"
    vb = VisualBible(
        world="19th-century forest homestead",
        style="storybook oil illustration",
        palette_lighting="deep pine green and dusk amber",
        characters=[
            VisualBibleCharacter(id="lead", name="Mira", aliases=["she"], description="a 14-year-old girl, braided hair, green apron"),
        ],
        locations=[],
        avoid="modern items, text",
    )
    narrations = [
        "She hides the carved doll deep in her apron pocket.",
        "Mira walks along the quiet birch trail.",
        "Wind rustles through the dark canopy overhead.",
        "Mira climbs the mossy steps toward the gate.",
        "At last she buries the carved doll beneath the oak roots.",
        "Mira washes her empty hands in the morning brook.",
    ]
    manifest = _make_manifest_with_narration(proj_dir, narrations, visual_bible=vb)

    # Mock LLM returns prop only on shot 1 and props=[] after shot 5 burial; omits props on 2-4 to verify code propagation too
    llm = _FakeLLM([
        json.dumps({
            "shots": [
                {"shot_id": "ch01_shot001", "scene_id": "ch01_scene01", "time_of_day": "dusk", "props": ["carved doll hidden in apron pocket"], "characters_in_shot": ["lead"], "scene_en": "Mira tucks the carved doll inside her green apron pocket"},
                {"shot_id": "ch01_shot002", "scene_id": "ch01_scene01", "characters_in_shot": ["lead"], "scene_en": "Mira walks steadily down the birch trail"},
                {"shot_id": "ch01_shot003", "scene_id": "ch01_scene01", "characters_in_shot": [], "scene_en": "Tall dark pines sway gently above the misty trail"},
                {"shot_id": "ch01_shot004", "scene_id": "ch01_scene01", "characters_in_shot": ["lead"], "scene_en": "Mira ascends weathered mossy stone steps beside the gate"},
                {"shot_id": "ch01_shot005", "scene_id": "ch01_scene02", "time_of_day": "dawn", "props_removed": ["carved doll"], "characters_in_shot": ["lead"], "scene_en": "Mira kneels beside oak roots covering the carved doll with soil"},
                {"shot_id": "ch01_shot006", "scene_id": "ch01_scene02", "time_of_day": "dawn", "characters_in_shot": ["lead"], "scene_en": "Mira rinses dirt from her empty hands in a cold clear brook"},
            ]
        })
    ])

    exp = export_external_image_prompts(manifest, proj_dir, llm_adapter=llm, cache_dir=tmp_path / "cache")
    rows = exp["rows"]
    for idx in (0, 1, 2, 3):  # shots 1..4 MUST carry the doll in Continuity!
        assert "carved doll" in rows[idx]["continuity"]
        assert "carved doll" in rows[idx]["prompt_en"]
    # Shots 2..4 share identical Continuity string
    assert rows[1]["continuity"] == rows[2]["continuity"] == rows[3]["continuity"]
    # Shot 6 after burial MUST NOT mention the buried doll in Continuity!
    assert "carved doll" not in rows[5]["continuity"]


def test_f6_i_time_continuity_night_survives_llm_batch_boundary_15_to_16_until_dawn(tmp_path: Path):
    """F6(i): Night scene continuity carries seamlessly across 15->16 batch boundary until narration announces dawn."""
    proj_dir = tmp_path / "f6_i"
    vb = VisualBible(
        world="medieval alpine pass",
        style="dark romantic oil painting",
        palette_lighting="moonlit indigo and distant lantern gold",
        characters=[VisualBibleCharacter(id="traveler", name="Stefan", aliases=["he"], description="a bearded courier, dark wool cloak, leather satchel")],
        locations=[VisualBibleLocation(id="ridge_path", name="Snowy Ridge Path", description="narrow mountain ridge trail lined with frost-covered pines")],
        avoid="flashlights, modern gear, text",
    )
    narrations = [f"Stefan treks through midnight darkness step {i}." for i in range(1, 18)] + [
        "Golden dawn finally crests over the snowy eastern ridge."
    ]
    manifest = _make_manifest_with_narration(proj_dir, narrations, visual_bible=vb)

    batch1 = {
        "shots": [
            {
                "shot_id": f"ch01_shot{i:03d}",
                "scene_id": "ch01_scene01",
                "location_id": "ridge_path",
                "time_of_day": "night",
                "weather": "light snowfall",
                "lighting": "pale moonlight",
                "characters_in_shot": ["traveler"],
                "scene_en": f"Stefan trudges along the snow-covered alpine trail under moonlight part {i}",
            }
            for i in range(1, 16)
        ]
    }
    # Batch 2 intentionally leaves time_of_day/location_id blank on shots 16-17 while staying in ch01_scene01 -> MUST inherit from Batch 1!
    batch2 = {
        "shots": [
            {"shot_id": "ch01_shot016", "scene_id": "ch01_scene01", "characters_in_shot": ["traveler"], "scene_en": "Stefan shields his eyes against falling snow on the dark path"},
            {"shot_id": "ch01_shot017", "scene_id": "ch01_scene01", "characters_in_shot": ["traveler"], "scene_en": "Stefan pauses beneath a frost-laden pine tree"},
            {"shot_id": "ch01_shot018", "scene_id": "ch01_scene02", "location_id": "ridge_path", "time_of_day": "dawn", "weather": "clear crisp morning", "lighting": "golden sunrise rays", "characters_in_shot": ["traveler"], "scene_en": "Stefan gazes east as warm sunrise breaks over the alpine crest"},
        ]
    }
    llm = _FakeLLM([json.dumps(batch1), json.dumps(batch2)])

    exp = export_external_image_prompts(manifest, proj_dir, llm_adapter=llm, cache_dir=tmp_path / "cache")
    assert len(llm.calls) == 2
    # Verify Batch 2 prompt explicitly received Batch 1's final state ('night', 'ridge_path')
    assert '"time_of_day": "night"' in llm.calls[1]
    assert '"scene_id": "ch01_scene01"' in llm.calls[1]

    rows = exp["rows"]
    # Shots 1..17 all inherit exact 'time: night' across the 15->16 boundary
    for idx in range(17):
        assert "time: night" in rows[idx]["continuity"]
        assert rows[idx]["continuity"] == rows[0]["continuity"]
    # Shot 18 transitions cleanly to dawn without carrying night
    assert "time: dawn" in rows[17]["continuity"]
    assert "time: night" not in rows[17]["continuity"]


def test_f6_j_non_character_documentary_has_empty_characters_chapter_era_and_zero_false_warnings(tmp_path: Path):
    """F6(j): Character-less documentary (e.g. 20th-century war machines) keeps characters=[], injects chapter world/era, and triggers 0 false character warnings."""
    proj_dir = tmp_path / "f6_j"
    proj_dir.mkdir(parents=True, exist_ok=True)
    vb_llm = _FakeLLM([
        json.dumps({
            "world": "20th-century industrial military proving grounds and battlefronts",
            "style": "authentic archival 35mm documentary photography",
            "palette_lighting": "gritty steel grey, olive drab, and overcast daylight",
            "characters": [],
            "locations": [
                {"id": "proving_ground", "name": "Muddy Proving Ground", "description": "rain-soaked testing track churned by steel treads"},
            ],
            "chapter_overrides": [
                {"chapter_index": 1, "world": "Western Front trench line", "era": "1917 World War I"},
                {"chapter_index": 2, "world": "Eastern European steppe", "era": "1943 World War II"},
            ],
            "avoid": "modern CGI, neon glow, fantasy armor, text, watermark",
        })
    ])

    ch1 = Chapter(
        chapter_index=1,
        chapter_title="Early Riveted Giants",
        tts_groups=[TtsGroup(group_index=1, paragraph_text="In 1917 riveted iron tanks rolled over muddy craters.", segments=[Segment(segment_index=1, narration_text="In 1917 riveted iron tanks rolled over muddy craters.")], status=ItemStatus.completed)],
        shots=[Shot(shot_id="ch01_shot001", chapter_index=1, shot_index=1, segment_indices=[1], duration_seconds=4.0, target_frames=100, visual_prompt="riveted tank")],
        status=ItemStatus.completed,
    )
    ch2 = Chapter(
        chapter_index=2,
        chapter_title="Sloped Armor Era",
        tts_groups=[TtsGroup(group_index=1, paragraph_text="By 1943 sloped steel hulls deflected heavy cannon shells.", segments=[Segment(segment_index=1, narration_text="By 1943 sloped steel hulls deflected heavy cannon shells.")], status=ItemStatus.completed)],
        shots=[Shot(shot_id="ch02_shot001", chapter_index=2, shot_index=1, segment_indices=[1], duration_seconds=4.0, target_frames=100, visual_prompt="sloped tank")],
        status=ItemStatus.completed,
    )
    manifest = ProjectManifest(
        project_id="f6_j",
        title="Evolution of 20th-Century Armored Machines",
        topic="History of 20th-century tanks and armored engineering",
        preset_id="history.military",
        preset_snapshot={"name": "Military Engineering History", "min_relevance_score": 0.35},
        chapters=[ch1, ch2],
    )
    save_manifest(manifest, proj_dir)

    res_vb = ensure_project_visual_bible(manifest, llm_adapter=vb_llm, cache_dir=tmp_path / "cache")
    assert res_vb["visual_bible"].characters == []

    # Export deterministically using the generated visual_bible (with no characters!)
    manifest.chapters[0].shots[0].scene_en = "A riveted rhomboid steel tank climbs across waterlogged trench mud"
    manifest.chapters[1].shots[0].scene_en = "A sloped-armor medium tank speeds across open steppe dust"
    exp = export_external_image_prompts(manifest, proj_dir, auto_prepare_llm=False, cache_dir=tmp_path / "cache")

    assert exp["warning_count"] == 0
    assert exp["unlocked_char_count"] == 0
    assert "1917 World War I" in exp["rows"][0]["prompt_en"]
    assert "1943 World War II" in exp["rows"][1]["prompt_en"]
    assert "Characters:" not in exp["rows"][0]["prompt_en"]
    assert "Characters:" not in exp["rows"][1]["prompt_en"]


def test_f6_k_no_hardcoded_story_or_character_names_in_production_modules():
    """F6(k): Production Visual Bible / continuity / external bridge / UI modules contain zero test-story hardcodes."""
    repo_root = Path(__file__).resolve().parents[2]
    prod_files = [
        repo_root / "app" / "services" / "longform" / "visual" / "visual_bible.py",
        repo_root / "app" / "services" / "longform" / "visual" / "scene_continuity.py",
        repo_root / "app" / "services" / "longform" / "visual" / "external_bridge.py",
        repo_root / "app" / "services" / "longform" / "schema.py",
        repo_root / "app" / "services" / "longform" / "ui" / "components.py",
        repo_root / "app" / "services" / "longform" / "ui" / "i18n.py",
    ]
    forbidden_names = (
        "vasilisa",
        "baba yaga",
        "baba_yaga",
        "yuki-onna",
        "yuki_onna",
        "minokichi",
    )
    for fpath in prod_files:
        txt = fpath.read_text(encoding="utf-8").lower()
        for bad in forbidden_names:
            assert bad not in txt, f"Story-specific hardcode {bad!r} found in {fpath}"


def test_f4_ui_visual_context_and_scene_preview_edit_propagates_to_scene_shots(tmp_path: Path, monkeypatch):
    """F4 UI: Visual expander renders updated ID/EN label, Konteks Visual controls, and scene continuity edit updates all shots in that scene."""
    proj_root = tmp_path / "projects_f4"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)
    monkeypatch.setattr(
        "app.services.longform.visual.external_bridge.ensure_project_visual_bible",
        lambda *a, **kw: None,
    )

    manifest, p_dir = create_project("proj_f4", "F4 UI Bridge", "umum.id", language="id-ID", projects_root=proj_root)
    vb = VisualBible(
        world="19th-century mountain village",
        style="storybook watercolor painting",
        palette_lighting="warm lantern light",
        characters=[VisualBibleCharacter(id="hero", name="Sari", aliases=["dia"], description="young villager in woven indigo coat")],
        locations=[VisualBibleLocation(id="bridge", name="Timber Bridge", description="old cedar footbridge over icy river")],
        avoid="modern cars, text",
        source="manual",
    )
    manifest.visual_bible = vb
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Bab 1",
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Sari menyeberangi jembatan. Angin berhembus kencang.",
                    segments=[
                        Segment(segment_index=1, narration_text="Sari menyeberangi jembatan.", duration_seconds=3.0),
                        Segment(segment_index=2, narration_text="Angin berhembus kencang.", duration_seconds=3.0),
                    ],
                    status=ItemStatus.completed,
                )
            ],
            shots=[
                Shot(shot_id="ch01_shot001", chapter_index=1, shot_index=1, segment_indices=[1], duration_seconds=3.0, target_frames=75, scene_id="sc01", scene_en="Sari steps onto the cedar footbridge", characters_in_shot=["hero"], location_id="bridge", continuity=SceneContinuity(location_id="bridge", time_of_day="dusk", lighting="warm lantern")),
                Shot(shot_id="ch01_shot002", chapter_index=1, shot_index=2, segment_indices=[2], duration_seconds=3.0, target_frames=75, scene_id="sc01", scene_en="Gusting wind shakes lanterns over the footbridge", characters_in_shot=["hero"], location_id="bridge", continuity=SceneContinuity(location_id="bridge", time_of_day="dusk", lighting="warm lantern")),
            ],
            status=ItemStatus.completed,
        )
    ]
    save_manifest(manifest, p_dir)

    app_file = str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")
    monkeypatch.setitem(config.ui, "language", "id")
    at = AppTest.from_file(app_file, default_timeout=25)
    at.query_params["project"] = "proj_f4"
    at.query_params["step"] = "visual"
    at.query_params["lang"] = "id"
    at.run()
    assert not at.exception

    exp_labels = [e.label for e in at.expander]
    assert "🖼️ Gambar Eksternal (Flow, dll.) — Ekspor Prompt & Impor Massal" in exp_labels
    assert any(b.key == "btn_ext_vb_ai_proj_f4" for b in at.button)
    assert any(b.key == "btn_ext_vb_save_proj_f4" for b in at.button)

    # Prepare export -> opens scene-grouped preview -> edit scene time_of_day to 'night' -> propagates to BOTH sc01 shots!
    at.button(key="btn_ext_prepare_proj_f4").click().run(timeout=20)
    assert not at.exception
    assert any(sb.key == "sel_sc_tod_proj_f4_sc01" for sb in at.selectbox)

    [sb for sb in at.selectbox if sb.key == "sel_sc_tod_proj_f4_sc01"][0].set_value("night").run(timeout=20)
    at.button(key="btn_ext_apply_preview_proj_f4").click().run(timeout=20)
    assert not at.exception

    reloaded = load_manifest(p_dir)
    assert reloaded.chapters[0].shots[0].continuity.time_of_day == "night"
    assert reloaded.chapters[0].shots[1].continuity.time_of_day == "night"
    assert "time: night" in str(at.session_state["ext_export_payload_proj_f4"]["rows"][0]["prompt_en"])
    assert "time: night" in str(at.session_state["ext_export_payload_proj_f4"]["rows"][1]["prompt_en"])

    # English UI expander label check
    monkeypatch.setitem(config.ui, "language", "en")
    at_en = AppTest.from_file(app_file, default_timeout=25)
    at_en.query_params["project"] = "proj_f4"
    at_en.query_params["step"] = "visual"
    at_en.query_params["lang"] = "en"
    at_en.run()
    assert not at_en.exception
    assert "🖼️ External Images (Flow, etc.) — Export Prompts & Bulk Import" in [e.label for e in at_en.expander]
