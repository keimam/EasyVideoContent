"""Tests for V102-F-A1 Addendum (FA1.1-FA1.7)."""

from __future__ import annotations

import json
import os
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from app.services.longform.schema import (
    Chapter,
    ItemStatus,
    ProjectManifest,
    Segment,
    Shot,
    TtsGroup,
    VisualBible,
    VisualBibleChapterOverride,
    VisualBibleCharacter,
)
from app.services.longform.store import save_manifest
from app.services.longform.visual.external_bridge import (
    export_external_image_prompts,
    get_bulk_import_count_mismatch,
    plan_bulk_external_import,
    plan_bulk_external_import_from_folder,
)
from app.services.longform.visual.scene_continuity import (
    ExternalPromptGenerationError,
    assign_scenes_and_prompts_for_shots,
)
from app.services.longform.visual.visual_bible import (
    STYLE_MODE_BASE_TEXTS,
    build_template_visual_bible,
    ensure_project_visual_bible,
    reconcile_avoid_with_style,
    validate_visual_bible,
)


def _make_manifest(
    proj_dir: Path,
    narrations: list[str],
    *,
    project_id: str | None = None,
    title: str = "Historical Journey",
    topic: str = "19th century mountain village",
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
            search_query="generic query",
            status=ItemStatus.pending,
        )
        for i, txt in enumerate(narrations, start=1)
    ]
    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1",
        tts_groups=[TtsGroup(group_index=1, paragraph_text=" ".join(narrations), segments=segs, status=ItemStatus.completed)],
        shots=shots,
        status=ItemStatus.completed,
    )
    m = ProjectManifest(
        project_id=project_id or proj_dir.name,
        title=title,
        topic=topic,
        preset_id="umum.en",
        preset_snapshot={"name": "General Documentary", "min_relevance_score": 0.35},
        visual_bible=visual_bible,
        chapters=[ch],
    )
    save_manifest(m, proj_dir)
    return m


def test_fa1_7a_batch_progress_and_cache_resume_after_batch2_failure(tmp_path: Path):
    """FA1.7(a): Batch 1 stays cached after Batch 2 fails; retrying skips Batch 1 LLM call and emits progress updates."""
    proj_dir = tmp_path / "fa1_7a"
    cache_dir = tmp_path / "cache"
    vb = VisualBible(
        world="19th-century alpine borderland",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="muted slate greys and deep pine tones",
        characters=[VisualBibleCharacter(id="lead", name="Klara", description="young woman, brown braid, wool shawl")],
        avoid="illustration, cartoon, text",
    )
    narrs = [f"Klara walks step {i} along the frosty ridge." for i in range(1, 21)]  # 2 batches: 1..15, 16..20
    manifest = _make_manifest(proj_dir, narrs, visual_bible=vb)

    batch1_json = json.dumps({
        "shots": [
            {
                "shot_id": f"ch01_shot{i:03d}",
                "scene_id": "ch01_scene01",
                "time_of_day": "night",
                "characters_in_shot": ["lead"],
                "scene_en": f"Klara walks along the frosty ridge part {i}",
            }
            for i in range(1, 16)
        ]
    })
    batch2_json = json.dumps({
        "shots": [
            {
                "shot_id": f"ch01_shot{i:03d}",
                "scene_id": "ch01_scene01",
                "time_of_day": "night",
                "characters_in_shot": ["lead"],
                "scene_en": f"Klara continues along the ridge part {i}",
            }
            for i in range(16, 21)
        ]
    })

    calls_run1: list[str] = []

    class _FailOnBatch2LLM:
        def generate(self, prompt: str, max_tokens: int = 1024):
            calls_run1.append(prompt)
            if len(calls_run1) == 1:
                return SimpleNamespace(content=batch1_json, provider="mock", model="m")
            raise RuntimeError("Read timed out waiting for OpenRouter")

    prog_events: list[dict] = []
    try:
        assign_scenes_and_prompts_for_shots(
            manifest,
            llm_adapter=_FailOnBatch2LLM(),
            cache_dir=cache_dir,
            progress_callback=prog_events.append,
            raise_on_error=True,
        )
        raise AssertionError("Expected ExternalPromptGenerationError on Batch 2 timeout")
    except ExternalPromptGenerationError as exc:
        assert exc.reason == "timeout"
        assert exc.completed_batches == 1
        assert exc.total_batches == 2

    # Progress emitted batch 1..2 with shot ranges and ETA
    assert any(ev["batch_index"] == 1 and ev["shot_start"] == 1 and ev["shot_end"] == 15 for ev in prog_events)
    assert any(ev["batch_index"] == 2 and ev["shot_start"] == 16 and ev["shot_end"] == 20 for ev in prog_events)

    # Clear manifest scene fields to simulate fresh rerun after partial failure, keeping cache_dir
    for sh in manifest.chapters[0].shots:
        sh.scene_id = None
        sh.scene_en = None

    calls_run2: list[str] = []

    class _ResumeBatch2OnlyLLM:
        def generate(self, prompt: str, max_tokens: int = 1024):
            calls_run2.append(prompt)
            return SimpleNamespace(content=batch2_json, provider="mock", model="m")

    res2 = assign_scenes_and_prompts_for_shots(
        manifest,
        llm_adapter=_ResumeBatch2OnlyLLM(),
        cache_dir=cache_dir,
        raise_on_error=True,
    )
    # Batch 1 was served from cache -> only Batch 2 (1 call) invoked!
    assert len(calls_run2) == 1
    assert res2["cache_hits"] == 1
    assert res2["llm_calls"] == 1
    assert '"ch01_shot016"' in calls_run2[0]
    assert manifest.chapters[0].shots[15].continuity.time_of_day == "night"


def test_fa1_7b_style_mode_realistic_adds_illustration_to_avoid_and_illustration_strips_it(tmp_path: Path):
    """FA1.7(b): Realistic mode puts illustration in Avoid (not Style); Illustration mode strips illustration from Avoid and keeps mode even on AI drift."""
    proj_dir = tmp_path / "fa1_7b"
    manifest = _make_manifest(
        proj_dir,
        ["Old wooden cabin in misty pine forest."],
        topic="Traditional Slavic forest folklore",
    )
    manifest.visual_avoid_terms = ["cartoon", "illustration", "drawing", "painting", "digital painting"]

    # Realistic template default
    vb_real = build_template_visual_bible(manifest, style_mode="realistic")
    assert vb_real.style_mode == "realistic"
    assert "photorealistic cinematic film still" in vb_real.style
    assert "illustration" not in vb_real.style.lower()
    assert "illustration" in vb_real.avoid.lower()
    assert "3d render" in vb_real.avoid.lower()

    # Illustration mode strips conflicting artistic terms from Avoid, even when AI echoes them or tries to switch medium
    class _DriftingLLM:
        def generate(self, prompt: str, max_tokens: int = 1024):
            return SimpleNamespace(
                content=json.dumps({
                    "world": "Slavic forest edge, 19th century",
                    "style": "photorealistic documentary photograph",
                    "palette_lighting": "muted earth tones",
                    "characters": [],
                    "locations": [],
                    "avoid": "illustration, drawing, digital painting, modern cars, text",
                }),
                provider="mock",
                model="m",
            )

    res_ill = ensure_project_visual_bible(
        manifest,
        cache_dir=tmp_path / "cache",
        llm_adapter=_DriftingLLM(),
        force=True,
        style_mode="illustration",
    )
    vb_ill = res_ill["visual_bible"]
    assert vb_ill.style_mode == "illustration"
    assert "illustration" in vb_ill.style.lower()
    assert "photorealistic" not in vb_ill.style.lower()
    assert "illustration" not in vb_ill.avoid.lower()
    assert "drawing" not in vb_ill.avoid.lower()
    assert "painting" not in vb_ill.avoid.lower()
    assert "modern cars" in vb_ill.avoid.lower()

    # Reconcile helper sanity check
    assert "illustration" in reconcile_avoid_with_style("text", "realistic").lower()


def test_fa1_7c_pronoun_and_duplicate_aliases_rejected_and_pov_character_used_for_you(tmp_path: Path):
    """FA1.7(c): Pronoun/generic aliases & cross-character duplicate aliases fail validation; pov_character_id resolves 'you'."""
    bad_pronoun_vb = VisualBible(
        world="19th-century village",
        style="photorealistic film still",
        palette_lighting="muted tones",
        characters=[
            VisualBibleCharacter(id="hero", name="Elena", aliases=["you", "her"], description="young woman, dark braid"),
        ],
    )
    errs_pron = validate_visual_bible(bad_pronoun_vb)
    assert any("kata ganti" in e for e in errs_pron)

    bad_dup_vb = VisualBible(
        world="19th-century village",
        style="photorealistic film still",
        palette_lighting="muted tones",
        characters=[
            VisualBibleCharacter(id="s1", name="Marta", aliases=["Red Ribbon"], description="teenage girl in green dress"),
            VisualBibleCharacter(id="s2", name="Nora", aliases=["Red Ribbon"], description="teenage girl in blue dress"),
        ],
    )
    errs_dup = validate_visual_bible(bad_dup_vb)
    assert any("alias ganda" in e for e in errs_dup)

    # Clean bible with pov_character_id (NO pronoun alias!) resolves "you" -> Elena cleanly
    proj_dir = tmp_path / "fa1_7c"
    clean_vb = VisualBible(
        world="19th-century village",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="muted slate blue tones",
        pov_character_id="elena",
        characters=[
            VisualBibleCharacter(id="elena", name="Elena", aliases=["Leni"], role="protagonist", description="young woman, early 20s, dark braid, blue wool dress"),
        ],
        avoid="illustration, cartoon, text",
    )
    assert validate_visual_bible(clean_vb) == []
    manifest = _make_manifest(proj_dir, ["You walk steadily into the mist carrying a wooden lantern."], visual_bible=clean_vb)
    exp = export_external_image_prompts(manifest, proj_dir, auto_prepare_llm=False, cache_dir=tmp_path / "cache")
    assert exp["rows"][0]["characters_in_shot"] == "elena"
    assert "Characters: Elena (young woman, early 20s, dark braid, blue wool dress)" in exp["rows"][0]["prompt_en"]


def test_fa1_7d_palette_no_time_words_and_chapter_override_replaces_world(tmp_path: Path):
    """FA1.7(d): Palette excludes dawn/noon/night/sun/moonlight; chapter_overrides.world replaces base world without merging."""
    proj_dir = tmp_path / "fa1_7d"
    manifest = _make_manifest(
        proj_dir,
        ["Settlers arrive at the coast."],
        project_id="roanoke_story",
        topic="roanoke_story",
        visual_bible=None,
    )

    class _LeakyPaletteLLM:
        def generate(self, prompt: str, max_tokens: int = 1024):
            return SimpleNamespace(
                content=json.dumps({
                    "world": "roanoke_story — Roanoke Island, North Carolina, 1587",
                    "style": "photorealistic cinematic film still",
                    "palette_lighting": "muted earth tones with soft dawn light and moonlight glow",
                    "characters": [],
                    "locations": [],
                    "chapter_overrides": [
                        {"chapter_index": 2, "world": "London Harbor, England", "era": "1587"},
                    ],
                    "avoid": "modern buildings, text",
                }),
                provider="mock",
                model="m",
            )

    res_vb = ensure_project_visual_bible(manifest, cache_dir=tmp_path / "cache", llm_adapter=_LeakyPaletteLLM(), force=True)
    vb = res_vb["visual_bible"]
    # Slug prefix stripped cleanly from world
    assert not vb.world.startswith("roanoke_story")
    assert "Roanoke Island, North Carolina, 1587" in vb.world
    # Time/light tokens removed from palette_lighting
    for forbidden in ("dawn", "noon", "night", "sun", "moonlight"):
        assert forbidden not in vb.palette_lighting.lower()
    assert validate_visual_bible(vb) == []

    # Add Chapter 2 with London override -> MUST replace Roanoke world, NEVER concatenate "Roanoke..., London"
    ch2 = Chapter(
        chapter_index=2,
        chapter_title="Chapter 2",
        tts_groups=[TtsGroup(group_index=1, paragraph_text="Ships wait in London dock.", segments=[Segment(segment_index=1, narration_text="Ships wait in London dock.")], status=ItemStatus.completed)],
        shots=[Shot(shot_id="ch02_shot001", chapter_index=2, shot_index=1, segment_indices=[1], duration_seconds=4.0, target_frames=100, scene_en="Timber docks stretch beside moored galleons")],
        status=ItemStatus.completed,
    )
    manifest.chapters.append(ch2)
    save_manifest(manifest, proj_dir)

    exp = export_external_image_prompts(manifest, proj_dir, auto_prepare_llm=False, cache_dir=tmp_path / "cache")
    p_ch2 = exp["rows"][1]["prompt_en"]
    assert "Setting: London Harbor, England, 1587." in p_ch2
    assert "Roanoke Island" not in p_ch2


def test_fa1_7e_prompt_order_starts_with_style_then_setting_before_scene(tmp_path: Path):
    """FA1.7(e): Final prompt strictly orders Style -> Setting -> scene -> Characters -> Location -> Continuity -> Palette -> aspect -> Avoid."""
    proj_dir = tmp_path / "fa1_7e"
    vb = VisualBible(
        world="19th-century mountain pass",
        style_mode="realistic",
        style="photorealistic cinematic film still, 35mm",
        palette_lighting="cold slate greys and muted browns",
        characters=[VisualBibleCharacter(id="guide", name="Tomas", description="older mountaineer in wool coat")],
        avoid="illustration, cartoon, text",
    )
    manifest = _make_manifest(proj_dir, ["Tomas checks the frozen pass."], visual_bible=vb)
    sh = manifest.chapters[0].shots[0]
    sh.characters_in_shot = ["guide"]
    sh.scene_en = "Tomas peers across the frozen pass"

    exp = export_external_image_prompts(manifest, proj_dir, auto_prepare_llm=False, cache_dir=tmp_path / "cache")
    prompt = exp["rows"][0]["prompt_en"]
    assert prompt.startswith("Style: ")
    idx_style = prompt.index("Style: ")
    idx_setting = prompt.index("Setting: ")
    idx_scene = prompt.index("Tomas peers across the frozen pass.")
    idx_chars = prompt.index("Characters: ")
    idx_pal = prompt.index("Palette: ")
    idx_avoid = prompt.index("Avoid: ")
    assert idx_style < idx_setting < idx_scene < idx_chars < idx_pal < idx_avoid


def test_fa1_7f_bulk_export_file_has_exact_n_lines_no_headers_no_bom_no_blanks(tmp_path: Path):
    """FA1.7(f): <project>_prompts_bulk.txt contains N lines = N prompts, no [ch prefix, no BOM, no empty lines."""
    proj_dir = tmp_path / "fa1_7f"
    manifest = _make_manifest(proj_dir, ["First scene text.", "Second scene text.", "Third scene text."])
    exp = export_external_image_prompts(manifest, proj_dir, auto_prepare_llm=False, cache_dir=tmp_path / "cache")

    bulk_str = exp["bulk_txt"]
    bulk_bytes = bulk_str.encode("utf-8")
    assert not bulk_bytes.startswith(b"\xef\xbb\xbf")  # no UTF-8 BOM
    assert "[ch" not in bulk_str
    assert "=== KONTEKS VISUAL ===" not in bulk_str

    lines = bulk_str.split("\n")
    assert len(lines) == 3
    assert all(line.strip() for line in lines)
    assert lines[0] == exp["rows"][0]["prompt_en"]
    assert lines[1] == exp["rows"][1]["prompt_en"]
    assert lines[2] == exp["rows"][2]["prompt_en"]


def test_fa1_7g_browser_duplicate_pattern_and_count_mismatch_warning(tmp_path: Path):
    """FA1.7(g): baba_yaga.webp, baba_yaga (1).webp, baba_yaga (2).webp map to shots 1, 2, 3 and unequal count returns mismatch warning."""
    proj_dir = tmp_path / "fa1_7g"
    manifest = _make_manifest(proj_dir, ["Shot 1.", "Shot 2.", "Shot 3.", "Shot 4."])
    manifest.last_prompt_export_shot_ids = ["ch01_shot001", "ch01_shot002", "ch01_shot003", "ch01_shot004"]

    png_b = BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(png_b, format="PNG")
    raw = png_b.getvalue()

    plan = plan_bulk_external_import(
        manifest,
        [
            ("baba_yaga.webp", raw),
            ("baba_yaga (1).webp", raw),
            ("baba_yaga (2).webp", raw),
        ],
    )
    assert [r["matched_shot_id"] for r in plan] == ["ch01_shot001", "ch01_shot002", "ch01_shot003"]
    assert all(r["match_reason"] == "browser_duplicate_sequence" for r in plan)

    mismatch = get_bulk_import_count_mismatch(manifest, plan)
    assert mismatch == (3, 4)


def test_fa1_7h_folder_import_sorts_by_created_time_and_preserves_source_files(tmp_path: Path):
    """FA1.7(h): Reading local folder sorts files by creation/mtime order cleanly and never modifies source files."""
    proj_dir = tmp_path / "fa1_7h"
    manifest = _make_manifest(proj_dir, ["S1.", "S2.", "S3."])
    manifest.last_prompt_export_shot_ids = ["ch01_shot001", "ch01_shot002", "ch01_shot003"]

    dl_dir = tmp_path / "zapi_downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    # Create non-alphabetical filenames whose timestamps define exact chronological order: z -> a -> m
    f_first = dl_dir / "zebra.png"
    f_second = dl_dir / "alpha.webp"
    f_third = dl_dir / "middle.jpg"
    f_ignored = dl_dir / "notes.txt"
    (dl_dir / "subdir").mkdir()
    Image.new("RGB", (64, 64), (1, 1, 1)).save(dl_dir / "subdir" / "nested.png", format="PNG")  # non-recursive: ignored!

    for p, fmt in ((f_first, "PNG"), (f_second, "WEBP"), (f_third, "JPEG")):
        Image.new("RGB", (64, 64), (50, 80, 120)).save(p, format=fmt)
    f_ignored.write_text("ignore", encoding="utf-8")

    base_ts = time.time() - 1000
    os.utime(f_first, (base_ts + 10, base_ts + 10))
    os.utime(f_second, (base_ts + 20, base_ts + 20))
    os.utime(f_third, (base_ts + 30, base_ts + 30))

    plan = plan_bulk_external_import_from_folder(manifest, dl_dir, sort_by="created")
    assert [r["filename"] for r in plan] == ["zebra.png", "alpha.webp", "middle.jpg"]
    assert [r["matched_shot_id"] for r in plan] == ["ch01_shot001", "ch01_shot002", "ch01_shot003"]
    assert f_first.is_file() and f_second.is_file() and f_third.is_file()
