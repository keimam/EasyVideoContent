"""Tests for V102-F-A2 Addendum (FA2.8 a-i)."""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from loguru import logger
from PIL import Image
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services.longform.logging_guard import dedupe_terminal_stream_handlers
from app.services.longform.schema import (
    Chapter,
    ItemStatus,
    ProjectManifest,
    SceneContinuity,
    Segment,
    Shot,
    TtsGroup,
    VisualBible,
    VisualBibleCharacter,
    VisualBibleLocation,
)
from app.services.longform.store import create_project, save_manifest
from app.services.longform.visual.external_bridge import (
    apply_bulk_external_import,
    compose_shot_external_prompt,
    detect_missing_browser_base_file,
    export_external_image_prompts,
    plan_bulk_external_import,
)
from app.services.longform.visual.scene_continuity import assign_scenes_and_prompts_for_shots
from app.services.longform.visual.visual_bible import (
    STYLE_MODE_BASE_TEXTS,
    _build_visual_bible_llm_prompt,
    auto_clean_visual_bible,
    ensure_project_visual_bible,
    world_mentions_era,
)


def _make_manifest(
    proj_dir: Path,
    narrations: list[str],
    *,
    project_id: str | None = None,
    visual_bible: VisualBible | None = None,
    prefill_fallback_scenes: bool = False,
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
            scene_id="ch01_scene01" if prefill_fallback_scenes else None,
            scene_en=txt if prefill_fallback_scenes else None,
            continuity=SceneContinuity() if prefill_fallback_scenes else None,
            status=ItemStatus.pending,
        )
        for i, txt in enumerate(narrations, start=1)
    ]
    ch = Chapter(
        chapter_index=1,
        chapter_title="Sent for Fire",
        tts_groups=[TtsGroup(group_index=1, paragraph_text=" ".join(narrations), segments=segs, status=ItemStatus.completed)],
        shots=shots,
        status=ItemStatus.completed,
    )
    manifest = ProjectManifest(
        project_id=project_id or proj_dir.name,
        title="Baba Yaga",
        topic="Slavic folktale",
        preset_id="umum.en",
        preset_snapshot={"name": "Storytelling", "min_relevance_score": 0.35},
        visual_bible=visual_bible,
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)
    return manifest


def _png_bytes(color: tuple[int, int, int] = (40, 120, 180)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


def test_fa2_8a_prepare_with_use_ai_calls_llm_per_batch_even_on_legacy_fallback_and_returns_ai_used(tmp_path: Path):
    """FA2.8(a): Prepare with Use AI calls LLM per batch (even when legacy fallback scene_en/scene_id existed) and returns ai_used=True."""
    proj_dir = tmp_path / "fa2_8a"
    vb = VisualBible(
        world="19th-century Russian forest clearing",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="muted slate tones",
        pov_character_id="vasilisa",
        characters=[
            VisualBibleCharacter(id="vasilisa", name="Vasilisa", role="protagonist", description="young woman, dark hair braid, faded blue dress"),
        ],
        locations=[
            VisualBibleLocation(id="cottage_exterior", name="Cottage Exterior", description="weathered timber cottage beside silver birches"),
        ],
        avoid="illustration, cartoon, text",
    )
    narrs = [f"You walk along step {i} beside the timber cottage." for i in range(1, 18)]
    manifest = _make_manifest(proj_dir, narrs, visual_bible=vb, prefill_fallback_scenes=True)

    calls: list[str] = []

    class _BatchLLM:
        def generate(self, prompt: str, max_tokens: int = 2048):
            calls.append(prompt)
            if '"ch01_shot001"' in prompt:
                items = [
                    {
                        "shot_id": f"ch01_shot{i:03d}",
                        "scene_id": "ch01_scene01",
                        "location_id": "cottage_exterior",
                        "time_of_day": "dusk",
                        "characters_in_shot": ["vasilisa"],
                        "scene_en": f"Vasilisa walks carefully past birch trees step {i}",
                    }
                    for i in range(1, 16)
                ]
            else:
                items = [
                    {
                        "shot_id": f"ch01_shot{i:03d}",
                        "scene_id": "ch01_scene01",
                        "location_id": "cottage_exterior",
                        "time_of_day": "dusk",
                        "characters_in_shot": ["vasilisa"],
                        "scene_en": f"Vasilisa nears the edge of the trees step {i}",
                    }
                    for i in range(16, 18)
                ]
            return SimpleNamespace(content=json.dumps({"shots": items}), provider="mock", model="m")

    exp = export_external_image_prompts(
        manifest,
        proj_dir,
        auto_prepare_llm=True,
        llm_adapter=_BatchLLM(),
        cache_dir=tmp_path / "cache",
    )
    assert len(calls) == 2
    assert exp["ai_used"] is True
    assert exp["batches_ok"] == 2
    assert exp["batches_failed"] == 0
    assert exp["fallback_shots"] == 0
    assert manifest.chapters[0].shots[0].scene_source == "ai"


def test_fa2_8b_llm_fails_batch2_gives_fallback_warning_and_resume_retries_only_failed_batch(tmp_path: Path):
    """FA2.8(b): Batch 2 LLM failure reports fallback_shots accurately and retry_failed_only only calls LLM for failed Batch 2."""
    from app.services.longform.llm_adapter import LLMDailyQuotaError

    proj_dir = tmp_path / "fa2_8b"
    vb = VisualBible(
        world="19th-century Russian forest edge",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="muted slate blue",
        pov_character_id="vasilisa",
        characters=[VisualBibleCharacter(id="vasilisa", name="Vasilisa", role="protagonist", description="young woman in blue dress")],
        avoid="illustration, text",
    )
    narrs = [f"Narration line {i}." for i in range(1, 21)]
    manifest = _make_manifest(proj_dir, narrs, visual_bible=vb)
    cache_dir = tmp_path / "cache"

    calls_run1: list[str] = []

    class _FailBatch2LLM:
        def generate(self, prompt: str, max_tokens: int = 2048):
            calls_run1.append(prompt)
            if '"ch01_shot016"' in prompt:
                raise LLMDailyQuotaError("Error code: 429 free-models-per-day sk-secret1234567890")
            items = [
                {
                    "shot_id": f"ch01_shot{i:03d}",
                    "scene_id": "ch01_scene01",
                    "time_of_day": "dusk",
                    "characters_in_shot": ["vasilisa"],
                    "scene_en": f"Vasilisa walks through snowy mist line {i}",
                }
                for i in range(1, 16)
            ]
            return SimpleNamespace(content=json.dumps({"shots": items}), provider="openrouter", model="m")

    exp1 = export_external_image_prompts(
        manifest,
        proj_dir,
        auto_prepare_llm=True,
        llm_adapter=_FailBatch2LLM(),
        cache_dir=cache_dir,
        raise_on_llm_error=False,
    )
    assert exp1["batches_ok"] == 1
    assert exp1["batches_failed"] == 1
    assert exp1["fallback_shots"] == 5
    assert exp1["failure_reason"] == "quota"
    assert "sk-secret1234567890" not in exp1["failure_detail"]

    calls_run2: list[str] = []

    class _ResumeBatch2OnlyLLM:
        def generate(self, prompt: str, max_tokens: int = 2048):
            calls_run2.append(prompt)
            items = [
                {
                    "shot_id": f"ch01_shot{i:03d}",
                    "scene_id": "ch01_scene02",
                    "time_of_day": "night",
                    "characters_in_shot": ["vasilisa"],
                    "scene_en": f"Vasilisa steps toward the dark cabin line {i}",
                }
                for i in range(16, 21)
            ]
            return SimpleNamespace(content=json.dumps({"shots": items}), provider="openrouter", model="m")

    exp2 = export_external_image_prompts(
        manifest,
        proj_dir,
        auto_prepare_llm=True,
        llm_adapter=_ResumeBatch2OnlyLLM(),
        cache_dir=cache_dir,
        raise_on_llm_error=False,
        retry_failed_only=True,
    )
    assert len(calls_run2) == 1
    assert '"ch01_shot016"' in calls_run2[0]
    assert exp2["ai_used"] is True
    assert exp2["batches_failed"] == 0
    assert exp2["fallback_shots"] == 0


def test_fa2_8c_non_ai_fallback_grammar_for_second_person_pov(tmp_path: Path):
    """FA2.8(c): Non-AI fallback replaces 'you live'/'you were'/'your' into grammatical 3rd-person singular without 'Vasilisa live/were'."""
    proj_dir = tmp_path / "fa2_8c"
    vb = VisualBible(
        world="19th-century Russian forest edge",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="earthy natural tones",
        pov_character_id="vasilisa",
        characters=[
            VisualBibleCharacter(id="vasilisa", name="Vasilisa", role="protagonist", description="young woman, dark hair braid, blue dress"),
            VisualBibleCharacter(id="mother", name="Mother", role="supporting", description="older woman in linen shawl"),
        ],
        avoid="illustration, text",
    )
    manifest = _make_manifest(
        proj_dir,
        [
            "It is autumn in old Russia, and you live in a house at the edge of a great forest.",
            "Your mother died when you were eight.",
            "She told you to keep it hidden until you carry your lantern and you spin.",
        ],
        visual_bible=vb,
    )
    exp = export_external_image_prompts(manifest, proj_dir, auto_prepare_llm=False, cache_dir=tmp_path / "cache")
    p1 = exp["rows"][0]["prompt_en"]
    p2 = exp["rows"][1]["prompt_en"]
    p3 = exp["rows"][2]["prompt_en"]

    assert "Vasilisa lives" in p1
    assert "Vasilisa live " not in p1
    assert "Vasilisa's mother died when Vasilisa was eight." in p2
    assert "Vasilisa were" not in p2
    assert "told Vasilisa to keep it hidden" in p3
    assert "Vasilisa carries Vasilisa's lantern and Vasilisa spins." in p3


def test_fa2_8d_missing_browser_first_file_warning_detected_and_shown(tmp_path: Path, monkeypatch):
    """FA2.8(d): Uploading/reading browser-numbered files 'proyek (1).webp, proyek (2).webp' without 'proyek.webp' shows warning."""
    raw = _png_bytes()
    assert detect_missing_browser_base_file(["proyek (1).webp", "proyek (2).webp"]) == "proyek"
    assert detect_missing_browser_base_file(["proyek.webp", "proyek (1).webp"]) is None

    proj_root = tmp_path / "projects_d"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)

    manifest, p_dir = create_project("proj_d", "Browser Warn", "umum.id", language="id-ID", projects_root=proj_root)
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Bab 1",
            tts_groups=[TtsGroup(group_index=1, paragraph_text="Shot 1. Shot 2.", segments=[Segment(segment_index=1, narration_text="Shot 1."), Segment(segment_index=2, narration_text="Shot 2.")], status=ItemStatus.completed)],
            shots=[
                Shot(shot_id="ch01_shot001", chapter_index=1, shot_index=1, segment_indices=[1], duration_seconds=3.0, target_frames=75),
                Shot(shot_id="ch01_shot002", chapter_index=1, shot_index=2, segment_indices=[2], duration_seconds=3.0, target_frames=75),
            ],
            status=ItemStatus.completed,
        )
    ]
    manifest.last_prompt_export_shot_ids = ["ch01_shot001", "ch01_shot002"]
    save_manifest(manifest, p_dir)

    dl_dir = tmp_path / "dl"
    dl_dir.mkdir()
    (dl_dir / "baba_yaga (1).png").write_bytes(raw)
    (dl_dir / "baba_yaga (2).png").write_bytes(raw)

    app_file = str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")
    monkeypatch.setitem(config.ui, "language", "id")
    at = AppTest.from_file(app_file, default_timeout=25)
    at.query_params["project"] = "proj_d"
    at.query_params["step"] = "visual"
    at.query_params["lang"] = "id"
    at.run()
    at.text_input(key="in_ext_folder_proj_d").set_value(str(dl_dir)).run()
    at.button(key="btn_ext_read_folder_proj_d").click().run()
    assert not at.exception
    warn_texts = [w.value for w in at.warning]
    assert any("File pertama (tanpa nomor" in w for w in warn_texts)


def test_fa2_8e_single_apply_writes_once_and_single_stream_log_handler(tmp_path: Path, monkeypatch):
    """FA2.8(e): Duplicate StreamSink handlers collapse to 1, and one Apply click writes & invokes apply_bulk_external_import strictly once."""
    # Verify dedupe_terminal_stream_handlers removes extra StreamSink while preserving callable sinks
    extra_id = logger.add(sys.stdout, level="INFO")
    try:
        removed = dedupe_terminal_stream_handlers()
        assert removed >= 1
        stream_sinks = [h for h in logger._core.handlers.values() if type(getattr(h, "_sink", None)).__name__ == "StreamSink"]
        assert len(stream_sinks) == 1
    finally:
        try:
            logger.remove(extra_id)
        except ValueError:
            pass

    proj_root = tmp_path / "projects_e"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)

    manifest, p_dir = create_project("proj_e", "Single Apply", "umum.id", language="id-ID", projects_root=proj_root)
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Bab 1",
            tts_groups=[TtsGroup(group_index=1, paragraph_text="Shot 1.", segments=[Segment(segment_index=1, narration_text="Shot 1.")], status=ItemStatus.completed)],
            shots=[Shot(shot_id="ch01_shot001", chapter_index=1, shot_index=1, segment_indices=[1], duration_seconds=3.0, target_frames=75)],
            status=ItemStatus.completed,
        )
    ]
    manifest.last_prompt_export_shot_ids = ["ch01_shot001"]
    save_manifest(manifest, p_dir)

    dl_dir = tmp_path / "dl_single"
    dl_dir.mkdir()
    (dl_dir / "ch01_shot001.png").write_bytes(_png_bytes())

    apply_calls: list[list[str]] = []
    real_apply = apply_bulk_external_import

    def _spy_apply(project_dir, man, mappings, *, rebuild=True):
        apply_calls.append([m["matched_shot_id"] for m in mappings])
        return real_apply(project_dir, man, mappings, rebuild=False)

    monkeypatch.setattr("app.services.longform.visual.external_bridge.apply_bulk_external_import", _spy_apply)

    app_file = str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")
    monkeypatch.setitem(config.ui, "language", "id")
    at = AppTest.from_file(app_file, default_timeout=25)
    at.query_params["project"] = "proj_e"
    at.query_params["step"] = "visual"
    at.query_params["lang"] = "id"
    at.run()
    at.text_input(key="in_ext_folder_proj_e").set_value(str(dl_dir)).run()
    at.button(key="btn_ext_read_folder_proj_e").click().run()
    at.button(key="btn_ext_apply_proj_e").click().run(timeout=20)
    assert not at.exception
    assert apply_calls == [["ch01_shot001"]]
    assert (p_dir / "assets" / "visual" / "shots" / "ch01_shot001_asset.png").is_file()


def test_fa2_8f_home_with_duplicate_project_id_folders_does_not_crash(tmp_path: Path, monkeypatch):
    """FA2.8(f): Home screen with two project folders sharing identical project_id renders both cards cleanly and shows duplicate warning."""
    proj_root = tmp_path / "projects_dup"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)

    _m1, d1 = create_project("folder_alpha", "Baba Yaga Original", "umum.en", language="en-US", projects_root=proj_root)
    m2, d2 = create_project("folder_beta", "Baba Yaga Salinan", "umum.en", language="en-US", projects_root=proj_root)
    m2.project_id = "folder_alpha"  # Intentionally duplicate project_id across distinct folders
    save_manifest(m2, d2)
    assert (d1 / "manifest.json").is_file()

    app_file = str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")
    monkeypatch.setitem(config.ui, "language", "id")
    at = AppTest.from_file(app_file, default_timeout=25)
    at.run()
    assert not at.exception
    btn_keys = [b.key for b in at.button]
    assert "btn_open_folder_alpha" in btn_keys
    assert "btn_open_folder_beta" in btn_keys
    assert any("folder_alpha" in w.value and "folder_beta" in w.value for w in at.warning)


def test_fa2_8g_legacy_bible_auto_cleaned_and_saved_with_missing_era_warning(tmp_path: Path, monkeypatch):
    """FA2.8(g): Legacy VisualBible (pronoun/dup aliases, candlelit/twilight time words, missing era) auto-cleans and saves without blocking."""
    proj_root = tmp_path / "projects_g"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)

    legacy_vb = VisualBible(
        world="baba_yaga — Dark birch forest clearing",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="earthy natural tones, warm candlelit interior vs cool twilight exterior contrast",
        pov_character_id=None,
        characters=[
            VisualBibleCharacter(id="vasilisa", name="Vasilisa", aliases=["you", "your", "protagonist"], role="protagonist", description="girl riding at dawn"),
            VisualBibleCharacter(id="s1", name="Sister 1", aliases=["witch"], role="antagonist", description="teen girl"),
            VisualBibleCharacter(id="s2", name="Sister 2", aliases=["witch", "her"], role="antagonist", description="teen girl"),
        ],
        locations=[
            VisualBibleLocation(id="hut", name="Hut", description="wooden hut in the clearing at twilight"),
        ],
        avoid="modern cars, text",
    )
    manifest, p_dir = create_project("proj_g", "Legacy Clean", "umum.en", language="en-US", projects_root=proj_root)
    manifest.visual_bible = legacy_vb
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Ch 1",
            tts_groups=[TtsGroup(group_index=1, paragraph_text="You walk alone at night.", segments=[Segment(segment_index=1, narration_text="You walk alone at night.")], status=ItemStatus.completed)],
            shots=[Shot(shot_id="ch01_shot001", chapter_index=1, shot_index=1, segment_indices=[1], duration_seconds=3.0, target_frames=75)],
            status=ItemStatus.completed,
        )
    ]
    save_manifest(manifest, p_dir)

    cleaned, notes = auto_clean_visual_bible(legacy_vb, manifest)
    assert cleaned.world == "Dark birch forest clearing"
    assert "candlelit" not in cleaned.palette_lighting.lower()
    assert "twilight" not in cleaned.palette_lighting.lower()
    assert "cool exterior contrast" in cleaned.palette_lighting.lower()
    assert cleaned.pov_character_id == "vasilisa"
    assert cleaned.characters[0].aliases == []
    assert "at dawn" not in cleaned.characters[0].description.lower()
    assert "at twilight" not in cleaned.locations[0].description.lower()
    assert not world_mentions_era(cleaned.world)
    assert notes

    app_file = str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")
    monkeypatch.setitem(config.ui, "language", "id")
    at = AppTest.from_file(app_file, default_timeout=25)
    at.query_params["project"] = "proj_g"
    at.query_params["step"] = "visual"
    at.query_params["lang"] = "id"
    at.run()
    assert not at.exception
    assert any("belum menyebut zaman" in w.value.lower() for w in at.warning)
    at.button(key="btn_ext_vb_save_proj_g").click().run()
    assert not at.exception
    assert any("Konteks Visual disimpan" in s.value for s in at.success)


def test_fa2_8h_flashback_variant_created_and_assigned_per_shot(tmp_path: Path):
    """FA2.8(h): Visual Bible prompt requires flashback age variant and per-shot assignment picks variant_of for flashback shots."""
    proj_dir = tmp_path / "fa2_8h"
    manifest = _make_manifest(
        proj_dir,
        [
            "Your mother died when you were eight, pressing a tiny wooden doll into your hands.",
            "Years later, you walk bravely into the misty birch woods.",
        ],
    )
    prompt_sent = _build_visual_bible_llm_prompt(manifest, "Your mother died when you were eight. Years later, you walk into the woods.")
    assert "FLASHBACK & AGE SHIFTS" in prompt_sent
    assert "variant_of" in prompt_sent
    assert "when you were eight" in prompt_sent

    class _FlashbackBibleLLM:
        def generate(self, prompt: str, max_tokens: int = 1500):
            return SimpleNamespace(
                content=json.dumps({
                    "world": "19th-century Russian forest edge and peasant village",
                    "style": STYLE_MODE_BASE_TEXTS["realistic"],
                    "palette_lighting": "earthy muted blues and forest greens",
                    "pov_character_id": "vasilisa",
                    "characters": [
                        {
                            "id": "vasilisa",
                            "name": "Vasilisa",
                            "aliases": ["Vasilisa"],
                            "role": "protagonist",
                            "variant_of": "",
                            "description": "18-year-old Slavic woman, long dark hair in single thick braid, faded blue homespun sarafan, carries tiny carved wooden doll",
                        },
                        {
                            "id": "vasilisa_child8",
                            "name": "Young Vasilisa",
                            "aliases": ["Little Vasilisa"],
                            "role": "flashback_younger_self",
                            "variant_of": "vasilisa",
                            "description": "8-year-old Slavic girl, same dark hair in twin braids, faded blue homespun dress, holds wooden doll",
                        },
                    ],
                    "locations": [],
                    "avoid": "modern buildings, text",
                }),
                provider="mock",
                model="m",
            )

    res_vb = ensure_project_visual_bible(manifest, llm_adapter=_FlashbackBibleLLM(), cache_dir=tmp_path / "cache")
    vb = res_vb["visual_bible"]
    assert any(c.id == "vasilisa_child8" and c.variant_of == "vasilisa" for c in vb.characters)

    # Even if scene assignment LLM forgets and passes adult "vasilisa" on flashback shot 1, variant resolver promotes vasilisa_child8
    class _ShotAssignLLM:
        def generate(self, prompt: str, max_tokens: int = 2048):
            return SimpleNamespace(
                content=json.dumps({
                    "shots": [
                        {"shot_id": "ch01_shot001", "scene_id": "ch01_scene01", "time_of_day": "night", "characters_in_shot": ["vasilisa"], "scene_en": "Young Vasilisa kneels weeping beside her mother's bed"},
                        {"shot_id": "ch01_shot002", "scene_id": "ch01_scene02", "time_of_day": "day", "characters_in_shot": ["vasilisa"], "scene_en": "Vasilisa walks bravely along the birch path"},
                    ]
                }),
                provider="mock",
                model="m",
            )

    assign_scenes_and_prompts_for_shots(manifest, llm_adapter=_ShotAssignLLM(), cache_dir=tmp_path / "cache2")
    assert manifest.chapters[0].shots[0].characters_in_shot == ["vasilisa_child8"]
    assert manifest.chapters[0].shots[1].characters_in_shot == ["vasilisa"]

    p1 = compose_shot_external_prompt(manifest, manifest.chapters[0], manifest.chapters[0].shots[0])["prompt_en"]
    p2 = compose_shot_external_prompt(manifest, manifest.chapters[0], manifest.chapters[0].shots[1])["prompt_en"]
    assert "8-year-old Slavic girl" in p1 and "18-year-old Slavic woman" not in p1
    assert "18-year-old Slavic woman" in p2


def test_fa2_8i_three_tabs_and_import_apply_button_above_mapping_list_apptest(tmp_path: Path, monkeypatch):
    """FA2.8(i): External bridge renders 3 tabs and Tab Import puts Apply button above mapping list rows."""
    proj_root = tmp_path / "projects_i"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)

    manifest, p_dir = create_project("proj_i", "Tabs Layout", "umum.id", language="id-ID", projects_root=proj_root)
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Bab 1",
            tts_groups=[TtsGroup(group_index=1, paragraph_text="Shot 1. Shot 2.", segments=[Segment(segment_index=1, narration_text="Shot 1."), Segment(segment_index=2, narration_text="Shot 2.")], status=ItemStatus.completed)],
            shots=[
                Shot(shot_id="ch01_shot001", chapter_index=1, shot_index=1, segment_indices=[1], duration_seconds=3.0, target_frames=75, scene_en="Scene 1"),
                Shot(shot_id="ch01_shot002", chapter_index=1, shot_index=2, segment_indices=[2], duration_seconds=3.0, target_frames=75, scene_en="Scene 2"),
            ],
            status=ItemStatus.completed,
        )
    ]
    manifest.last_prompt_export_shot_ids = ["ch01_shot001", "ch01_shot002"]
    save_manifest(manifest, p_dir)

    dl_dir = tmp_path / "dl_i"
    dl_dir.mkdir()
    (dl_dir / "ch01_shot001.png").write_bytes(_png_bytes())
    (dl_dir / "ch01_shot002.png").write_bytes(_png_bytes())

    app_file = str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")
    monkeypatch.setitem(config.ui, "language", "id")
    at = AppTest.from_file(app_file, default_timeout=25)
    at.query_params["project"] = "proj_i"
    at.query_params["step"] = "visual"
    at.query_params["lang"] = "id"
    at.run()
    assert not at.exception

    tab_labels = [tb.label for tb in at.tabs]
    for expected_lbl in ("1. Konteks Visual", "2. Ekspor Prompt", "3. Impor Gambar"):
        assert expected_lbl in tab_labels

    # Load mapping rows in Tab Impor and verify Apply button precedes mapping selectboxes in Tab 3 subtree
    at.text_input(key="in_ext_folder_proj_i").set_value(str(dl_dir)).run()
    at.button(key="btn_ext_read_folder_proj_i").click().run()
    assert not at.exception

    import_tab = [tb for tb in at.tabs if tb.label == "3. Impor Gambar"][0]
    pre_order: list[str] = []

    def _walk(node):
        key = getattr(node, "key", None)
        if key:
            pre_order.append(str(key))
        for child in getattr(node, "children", {}).values() if isinstance(getattr(node, "children", None), dict) else []:
            _walk(child)

    _walk(import_tab)
    assert "btn_ext_apply_proj_i" in pre_order
    map_indices = [i for i, k in enumerate(pre_order) if k.startswith("sel_ext_map_proj_i_")]
    assert map_indices
    assert pre_order.index("btn_ext_apply_proj_i") < map_indices[0]
