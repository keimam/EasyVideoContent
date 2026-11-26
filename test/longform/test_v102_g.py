"""Stage V102-G tests: AI failover chain, JSON/reasoning retry, 15->5 scene split, UI/i18n, flashback age & prop-free locations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from app.config import config
from app.services.longform.config import (
    get_llm_failover_chain,
    normalize_llm_failover_chain,
    save_llm_failover_chain,
)
from app.services.longform.llm_adapter import (
    LLMAdapter,
    LLMAuthError,
    LLMCreditError,
    LLMDailyQuotaError,
    LLMModelNotFoundError,
    LLMRateLimitError,
    LLMResponse,
    call_llm_with_failover,
    clear_provider_pause,
    get_provider_chain_status,
    mark_provider_paused,
    sanitize_error,
    test_all_llm_providers as run_test_all_providers,
)
from app.services.longform.schema import (
    Chapter,
    ItemStatus,
    ProjectManifest,
    Shot,
    VisualBible,
    VisualBibleCharacter,
    VisualBibleLocation,
)
from app.services.longform.ui.i18n import EN_TRANSLATIONS, ID_TRANSLATIONS
from app.services.longform.visual.external_bridge import export_external_image_prompts
from app.services.longform.visual.scene_continuity import (
    SCENE_RETRY_SUB_BATCH_SIZE,
    assign_scenes_and_prompts_for_shots,
)
from app.services.longform.visual.visual_bible import (
    STYLE_MODE_BASE_TEXTS,
    _coerce_visual_bible,
    auto_clean_visual_bible,
    detect_script_flashback_cues,
    ensure_flashback_character_variants,
    ensure_project_visual_bible,
    strip_carried_objects_from_location_desc,
)


@pytest.fixture(autouse=True)
def _isolate_pause_and_config(monkeypatch, tmp_path: Path):
    """Isolate config.app mutations, EFS_DATA_DIR, and persistent pause file per test."""
    from app.services.longform import config as lf_config

    pause_path = tmp_path / "pauses.json"
    data_dir = tmp_path / "efs_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EFS_LLM_PAUSE_STATE_PATH", str(pause_path))
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    lf_config._CONFIG_CACHE = None
    clear_provider_pause()

    snap = dict(config.app)
    monkeypatch.setattr(config, "save_config", lambda: None)
    yield
    config.app.clear()
    config.app.update(snap)
    lf_config._CONFIG_CACHE = None
    clear_provider_pause()


def _make_manifest(
    project_dir: Path,
    narrations: List[str],
    *,
    visual_bible: VisualBible | None = None,
) -> ProjectManifest:
    project_dir.mkdir(parents=True, exist_ok=True)
    shots = [
        Shot(
            shot_id=f"ch01_shot{idx:03d}",
            chapter_index=1,
            shot_index=idx,
            duration_seconds=4.0,
            visual_prompt=narr,
            search_query="village woman forest",
            status=ItemStatus.pending,
        )
        for idx, narr in enumerate(narrations, start=1)
    ]
    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1",
        shots=shots,
        tts_groups=[],
        status=ItemStatus.completed,
    )
    return ProjectManifest(
        project_id=project_dir.name or "g_test",
        title="Folklore Test",
        topic="Russian Folklore",
        preset_id="mystery_folklore",
        preset_snapshot={"preset_id": "mystery_folklore", "name": "Mystery & Folklore"},
        chapters=[ch],
        visual_bible=visual_bible,
    )


def test_g7a_failover_on_daily_quota_marks_pause_until_utc_midnight():
    """(a) Primary OpenRouter daily quota pauses OpenRouter until 00:00 UTC and succeeds via Gemini."""
    config.app["llm_provider"] = "openrouter"
    config.app["openrouter_api_key"] = "sk-or-v1-dummytestkey0000111122223333"
    config.app["gemini_api_key"] = "AIzaSyDummyGeminiTestKey1234567890"
    config.app["gemini_model_name"] = ""

    calls: List[str] = []

    def fake_upstream(prompt: str, app_config: Dict[str, Any], **_kw) -> str:
        prov = str(app_config.get("llm_provider") or "")
        calls.append(prov)
        if prov == "openrouter":
            raise LLMDailyQuotaError("Error 429: free-models-per-day limit reached")
        return '{"ok": true, "world": "Old village"}'

    with patch("app.services.llm._generate_response", side_effect=fake_upstream):
        resp = call_llm_with_failover(
            "Return JSON",
            failover_chain=["gemini", "groq"],
            force_refresh=True,
            expect_json=True,
        )

    assert resp.provider == "gemini"
    assert resp.failover_used is True
    assert "openrouter" in (resp.providers_tried or [])
    assert "gemini" in (resp.providers_tried or [])
    assert calls[0] == "openrouter" and "gemini" in calls

    statuses = {s["provider"]: s for s in get_provider_chain_status(["openrouter", "gemini"])}
    assert statuses["openrouter"]["status"] == "paused_quota"
    assert statuses["openrouter"]["paused_until"] is not None
    assert statuses["openrouter"]["paused_until"] > time.time()
    assert statuses["gemini"]["status"] == "ready"


def test_g7b_skips_providers_without_key_or_model_cleanly():
    """(b) Providers missing API keys or required models are skipped without calling SDK."""
    config.app["llm_provider"] = "groq"
    config.app["groq_api_key"] = ""
    config.app["oneapi_api_key"] = "sk-oneapi-dummy1234567890"
    config.app["oneapi_model_name"] = ""
    config.app["gemini_api_key"] = "AIzaSyDummyGeminiSkipTest123456789"
    config.app["gemini_model_name"] = ""

    called: List[str] = []

    def fake_upstream(prompt: str, app_config: Dict[str, Any], **_kw) -> str:
        prov = str(app_config.get("llm_provider") or "")
        called.append(prov)
        return "OK"

    with patch("app.services.llm._generate_response", side_effect=fake_upstream):
        resp = call_llm_with_failover(
            "Reply OK",
            failover_chain=["oneapi", "gemini"],
            force_refresh=True,
        )

    assert resp.provider == "gemini"
    assert called == ["gemini"]
    assert resp.provider_errors["groq"] == "no_key"
    assert resp.provider_errors["oneapi"] == "needs_model"


def test_g7c_pause_on_auth_credit_404_and_auto_clears_on_key_change():
    """(c) Auth/402/404 pauses provider on subsequent calls until its key changes."""
    config.app["llm_provider"] = "openrouter"
    config.app["openrouter_api_key"] = "sk-or-v1-badkey1111222233334444"
    config.app["gemini_api_key"] = "AIzaSyDummyGeminiBackupKey987654321"
    config.app["gemini_model_name"] = ""

    calls_first: List[str] = []
    calls_second: List[str] = []

    def fake_auth_fail(prompt: str, app_config: Dict[str, Any], **_kw) -> str:
        prov = str(app_config.get("llm_provider") or "")
        calls_first.append(prov)
        if prov == "openrouter":
            raise LLMAuthError("401 Unauthorized: Invalid API key sk-or-v1-badkey1111222233334444")
        return "OK"

    with patch("app.services.llm._generate_response", side_effect=fake_auth_fail):
        r1 = call_llm_with_failover("Hi", failover_chain=["gemini"], force_refresh=True)
        assert r1.provider == "gemini"
        assert calls_first == ["openrouter", "gemini"]

    # Subsequent call skips paused openrouter immediately
    with patch("app.services.llm._generate_response", side_effect=lambda prompt, app_config, **kw: (calls_second.append(app_config["llm_provider"]) or "OK")):
        r2 = call_llm_with_failover("Hi2", failover_chain=["gemini"], force_refresh=True)
        assert r2.provider == "gemini"
        assert calls_second == ["gemini"]

    # Changing API key automatically clears auth pause
    config.app["openrouter_api_key"] = "sk-or-v1-newfreshkey9999888877776666"
    st = {s["provider"]: s for s in get_provider_chain_status(["openrouter", "gemini"])}
    assert st["openrouter"]["status"] == "ready"


def test_g7d_empty_or_non_json_retries_once_on_same_provider_with_min_reasoning():
    """(d) Empty or non-JSON text retries once on the SAME provider with 'Return JSON only...' & minimize_reasoning=True."""
    config.app["llm_provider"] = "openrouter"
    config.app["openrouter_api_key"] = "sk-or-v1-validkey0000111122223333"
    config.app["gemini_api_key"] = "AIzaSyDummyGeminiKeyShouldNotRun1234"
    config.app["gemini_model_name"] = ""

    recorded: List[Dict[str, Any]] = []

    def fake_generate(self, prompt: str, **kwargs) -> LLMResponse:
        recorded.append({
            "provider": self.provider,
            "prompt": prompt,
            "minimize_reasoning": kwargs.get("minimize_reasoning"),
            "max_tokens": kwargs.get("max_tokens"),
        })
        if self.provider == "openrouter" and len(recorded) == 1:
            return LLMResponse(content="Let me think about this carefully...", provider="openrouter", model="openrouter/free", duration_seconds=0.1)
        return LLMResponse(content='{"result": "ok"}', provider=self.provider, model="openrouter/free", duration_seconds=0.1)

    with patch.object(LLMAdapter, "generate", fake_generate):
        resp = call_llm_with_failover(
            "Build visual bible JSON",
            failover_chain=["gemini"],
            expect_json=True,
            max_tokens=400,
        )

    assert resp.provider == "openrouter"
    assert resp.failover_used is False
    assert len(recorded) == 2
    assert all(r["provider"] == "openrouter" for r in recorded)
    assert "Return JSON only" in recorded[1]["prompt"]
    assert recorded[1]["minimize_reasoning"] is True


def test_g7e_scene_batch_15_splits_to_5_on_same_provider_before_failover(tmp_path: Path):
    """(e) 15-shot batch failing JSON splits into 5-shot sub-batches on SAME provider before failing over."""
    config.app["llm_provider"] = "openrouter"
    config.app["openrouter_api_key"] = "sk-or-v1-dummytestkey1234567890abcd"
    config.app["gemini_api_key"] = "AIzaSyDummyGeminiKey9988776655443322"
    config.app["gemini_model_name"] = ""
    save_llm_failover_chain(["gemini"])

    vb = VisualBible(
        world="19th-century Russian village",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="muted winter tones",
        characters=[VisualBibleCharacter(id="vasilisa", name="Vasilisa", role="protagonist", description="young woman in blue wool dress")],
        locations=[VisualBibleLocation(id="cottage_interior", name="Cottage Interior", description="log walls and hearth")],
    )
    narrs = [f"Shot {i}: Vasilisa walks across the wooden floor holding a basket." for i in range(1, 16)]
    manifest = _make_manifest(tmp_path / "split_proj", narrs, visual_bible=vb)

    calls: List[Dict[str, Any]] = []

    def fake_upstream_split(prompt: str, app_config: Dict[str, Any], **_kw) -> str:
        prov = str(app_config.get("llm_provider") or "")
        shot_ids_in_prompt = [f"ch01_shot{i:03d}" for i in range(1, 16) if f"ch01_shot{i:03d}" in prompt]
        calls.append({"provider": prov, "count": len(shot_ids_in_prompt), "sids": shot_ids_in_prompt})
        if prov == "openrouter" and len(shot_ids_in_prompt) > SCENE_RETRY_SUB_BATCH_SIZE:
            return "invalid non-json thinking monologue"
        items = [
            {
                "shot_id": sid,
                "scene_id": "ch01_scene01",
                "location_id": "cottage_interior",
                "time_of_day": "day",
                "weather": "clear",
                "lighting": "soft window light",
                "props": ["basket"],
                "character_state": "walking",
                "characters_in_shot": ["vasilisa"],
                "scene_en": f"Vasilisa walks carefully in shot {sid}.",
            }
            for sid in shot_ids_in_prompt
        ]
        return json.dumps({"shots": items})

    with patch("app.services.llm._generate_response", side_effect=fake_upstream_split):
        res = assign_scenes_and_prompts_for_shots(
            manifest,
            cache_dir=tmp_path / "cache1",
            force=True,
            allow_llm=True,
        )

    assert res["ai_used"] is True
    assert res["batches_ok"] == 1
    # 15-shot batch fails initially + 1 minimal-reasoning retry on openrouter, then splits into 3x 5-shot sub-batches all on openrouter
    assert calls[0]["provider"] == "openrouter" and calls[0]["count"] == 15
    sub_calls = [c for c in calls if c["count"] == 5]
    assert len(sub_calls) == 3
    assert all(c["provider"] == "openrouter" for c in calls)
    assert all(sh.scene_source == "ai" for sh in manifest.chapters[0].shots)

    # Now test when 5-shot sub-batches ALSO fail on openrouter -> fails over to gemini
    calls2: List[str] = []

    def fake_upstream_fail_sub(prompt: str, app_config: Dict[str, Any], **_kw) -> str:
        prov = str(app_config.get("llm_provider") or "")
        calls2.append(prov)
        shot_ids_in_prompt = [f"ch01_shot{i:03d}" for i in range(1, 16) if f"ch01_shot{i:03d}" in prompt]
        if prov == "openrouter":
            return "broken json output"
        items = [
            {
                "shot_id": sid,
                "scene_id": "ch01_scene01",
                "location_id": "cottage_interior",
                "time_of_day": "day",
                "weather": "clear",
                "lighting": "soft light",
                "props": [],
                "character_state": "standing",
                "characters_in_shot": ["vasilisa"],
                "scene_en": f"Vasilisa stands in shot {sid}.",
            }
            for sid in shot_ids_in_prompt
        ]
        return json.dumps({"shots": items})

    manifest2 = _make_manifest(tmp_path / "split_proj2", narrs, visual_bible=vb)
    with patch("app.services.llm._generate_response", side_effect=fake_upstream_fail_sub):
        res2 = assign_scenes_and_prompts_for_shots(
            manifest2,
            cache_dir=tmp_path / "cache2",
            force=True,
            allow_llm=True,
        )

    assert res2["ai_used"] is True
    assert "openrouter" in calls2 and "gemini" in calls2
    assert res2["batch_providers"][0]["provider"] == "gemini"
    assert res2["batch_providers"][0]["failover"] is True


def test_g7f_all_providers_fail_redacts_all_secrets_everywhere():
    """(f) When every provider fails, error summary and logs never expose sk-, AIza, or gsk_ keys."""
    s_or = "sk-or-v1-topsecretopenrouter123456"
    s_gm = "AIzaSyTopSecretGeminiKey99887766"
    s_gq = "gsk_TopSecretGroqKey112233445566"
    config.app["llm_provider"] = "openrouter"
    config.app["openrouter_api_key"] = s_or
    config.app["gemini_api_key"] = s_gm
    config.app["gemini_model_name"] = ""
    config.app["groq_api_key"] = s_gq
    config.app["groq_model_name"] = ""

    def fake_all_fail(prompt: str, app_config: Dict[str, Any], **_kw) -> str:
        prov = str(app_config.get("llm_provider") or "")
        if prov == "openrouter":
            raise LLMDailyQuotaError(f"429 free-models-per-day for key {s_or}")
        if prov == "gemini":
            raise LLMAuthError(f"401 bad key {s_gm}")
        raise LLMCreditError(f"402 exhausted {s_gq}")

    with patch("app.services.llm._generate_response", side_effect=fake_all_fail):
        with pytest.raises(Exception) as exc_info:
            call_llm_with_failover(
                "Generate scene",
                failover_chain=["gemini", "groq"],
                force_refresh=True,
            )

    msg = str(exc_info.value)
    for secret in (s_or, s_gm, s_gq):
        assert secret not in msg
        assert secret not in sanitize_error(msg)
    assert "openrouter" in msg and "gemini" in msg and "groq" in msg


def test_g7g_backup_ai_config_persistence_and_i18n_symmetry(monkeypatch, tmp_path: Path):
    """(g) Ordered failover chain persists atomically and UI i18n keys match symmetrically in id & en."""
    from app.services.longform import app_paths

    expected_toml = app_paths.get_data_dir() / "config" / "longform.toml"
    assert normalize_llm_failover_chain(["Gemini", "groq", "gemini", "  ", "deepseek"]) == ("gemini", "groq", "deepseek")
    saved = save_llm_failover_chain(["groq", "gemini", "deepseek"])
    assert saved == ["groq", "gemini", "deepseek"]
    assert get_llm_failover_chain(reload=True) == ["groq", "gemini", "deepseek"]
    assert 'llm_failover_chain = [ "groq", "gemini", "deepseek",]' in expected_toml.read_text(encoding="utf-8") or 'groq' in expected_toml.read_text(encoding="utf-8")

    # Also check UI test_all_llm_providers safely redacts output
    config.app["llm_provider"] = "openrouter"
    config.app["openrouter_api_key"] = "sk-or-v1-testallsecret99887766"
    config.app["gemini_api_key"] = ""
    with patch("app.services.longform.llm_adapter.test_llm_provider", return_value=(False, "Denied sk-or-v1-testallsecret99887766", 0.2)):
        res = run_test_all_providers(["openrouter", "gemini"])
    assert len(res) == 2
    assert "sk-or-v1-testallsecret99887766" not in json.dumps(res)
    assert res[1]["status"] == "no_key"

    # Required i18n keys must match between ID & EN
    required_keys = [
        "settings_failover_heading",
        "settings_failover_help",
        "settings_failover_order_label",
        "settings_failover_test_all_btn",
        "ext_batch_progress_label_provider",
        "ext_batch_provider_backup_tag",
        "ext_scene_ai_status_providers",
        "ext_scene_ai_tried_providers_note",
    ]
    for k in required_keys:
        assert k in ID_TRANSLATIONS and k in EN_TRANSLATIONS


def test_g7h_flashback_age_creates_child_variant_and_tags_flashback_shots(tmp_path: Path):
    """(h) Narration cue like 'When you were eight...' creates child variant_of & selects child for flashback shots."""
    script = (
        "Vasilisa lived quietly in the village with her stepfamily. "
        "When you were eight years old, your mother gave you a small blessing in the dark cabin. "
        "Now eighteen, Vasilisa walked into the pine forest."
    )
    cues = detect_script_flashback_cues(script)
    assert any(c.get("age") == 8 for c in cues)

    adult_vb = VisualBible(
        world="19th-century rural Russia",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="muted slate blue and warm amber tones",
        pov_character_id="vasilisa",
        characters=[
            VisualBibleCharacter(
                id="vasilisa",
                name="Vasilisa",
                aliases=["Vasilisa"],
                role="protagonist",
                description="young woman, 18 years old, long dark chestnut braid, embroidered linen apron over navy skirt",
            )
        ],
    )
    enriched_vb, changed = ensure_flashback_character_variants(adult_vb, script)
    assert changed is True
    child_entries = [c for c in enriched_vb.characters if c.variant_of == "vasilisa"]
    assert len(child_entries) == 1
    child = child_entries[0]
    assert child.id == "vasilisa_child8"
    assert "8-year-old" in child.description.lower()
    assert "chestnut braid" in child.description.lower()

    # Check scene continuity assigns child variant on flashback shot & adult on present shot
    manifest = _make_manifest(
        tmp_path / "fb_proj",
        [
            "When you were eight years old, your dying mother pressed her hand to your cheek.",
            "Now Vasilisa stepped along the snowy path between tall pines.",
        ],
        visual_bible=enriched_vb,
    )
    exp = export_external_image_prompts(manifest, tmp_path / "fb_proj", auto_prepare_llm=False, cache_dir=tmp_path / "cache")
    assert exp["count"] == 2
    assert "vasilisa_child8" in exp["rows"][0]["characters_in_shot"]
    assert "vasilisa_child8" not in exp["rows"][1]["characters_in_shot"]
    assert "vasilisa" in exp["rows"][1]["characters_in_shot"]


def test_g7i_location_description_strips_carried_props_in_coerce_and_autoclean():
    """(i) Carried objects like 'holding a glowing skull lantern' or 'wooden doll' are stripped from location descriptions."""
    raw_loc = "Small dark timber hut interior with stone hearth, holding a glowing skull lantern near the table"
    cleaned = strip_carried_objects_from_location_desc(raw_loc)
    assert "lantern" not in cleaned.lower()
    assert "timber hut interior" in cleaned.lower()
    assert "stone hearth" in cleaned.lower()

    fallback = VisualBible(world="19th-century village", style=STYLE_MODE_BASE_TEXTS["realistic"], palette_lighting="muted tones")
    coerced = _coerce_visual_bible(
        {
            "world": "19th-century Russian forest",
            "style": STYLE_MODE_BASE_TEXTS["realistic"],
            "palette_lighting": "muted grey and amber tones",
            "locations": [
                {
                    "id": "forest_clearing_exterior",
                    "name": "Forest Clearing Exterior",
                    "description": "Snowy birch clearing around a wooden fence, carrying a wooden doll on the floor by the gate",
                }
            ],
        },
        fallback=fallback,
    )
    loc_desc = coerced.locations[0].description.lower()
    assert "wooden doll" not in loc_desc
    assert "snowy birch clearing" in loc_desc

    cleaned_vb, notes = auto_clean_visual_bible(
        VisualBible(
            world="19th-century Russian forest",
            style_mode="realistic",
            style=STYLE_MODE_BASE_TEXTS["realistic"],
            palette_lighting="muted slate tones",
            locations=[
                VisualBibleLocation(
                    id="hut_interior",
                    name="Hut Interior",
                    description="Rustic log cabin interior with glowing stove, holds an iron key near the bench",
                )
            ],
        )
    )
    assert "iron key" not in cleaned_vb.locations[0].description.lower()
    assert "log cabin interior" in cleaned_vb.locations[0].description.lower()
    assert any("benda bawaan" in n for n in notes)


def test_g7j_export_external_prompts_reports_provider_and_failover_in_progress(tmp_path: Path):
    """(j) End-to-end export_external_image_prompts propagates batch provider_used & failover status via progress & summary."""
    config.app["llm_provider"] = "openrouter"
    config.app["openrouter_api_key"] = "sk-or-v1-testprojkey000011112222"
    config.app["gemini_api_key"] = "AIzaSyTestProjGemini00001111222233"
    config.app["gemini_model_name"] = ""
    save_llm_failover_chain(["gemini"])

    vb = VisualBible(
        world="19th-century Russian forest",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="muted winter tones",
        characters=[VisualBibleCharacter(id="vasilisa", name="Vasilisa", role="protagonist", description="young woman in blue cloak")],
        locations=[VisualBibleLocation(id="forest_exterior", name="Forest Exterior", description="snowy pine forest path")],
    )
    manifest = _make_manifest(
        tmp_path / "ui_bridge_proj",
        ["Vasilisa walked through the snow carrying a lantern."],
        visual_bible=vb,
    )

    progress_events: List[Dict[str, Any]] = []

    def fake_upstream(prompt: str, app_config: Dict[str, Any], **_kw) -> str:
        prov = str(app_config.get("llm_provider") or "")
        if prov == "openrouter":
            raise LLMDailyQuotaError("429 free-models-per-day limit")
        return json.dumps({
            "shots": [
                {
                    "shot_id": "ch01_shot001",
                    "scene_id": "ch01_scene01",
                    "location_id": "forest_exterior",
                    "time_of_day": "night",
                    "weather": "snow",
                    "lighting": "lantern glow",
                    "props": ["lantern"],
                    "character_state": "walking in cloak",
                    "characters_in_shot": ["vasilisa"],
                    "scene_en": "Vasilisa walks down the snowy pine path holding a lantern.",
                }
            ]
        })

    with patch("app.services.llm._generate_response", side_effect=fake_upstream):
        exp = export_external_image_prompts(
            manifest,
            tmp_path / "ui_bridge_proj",
            auto_prepare_llm=True,
            cache_dir=tmp_path / "ext_cache",
            progress_callback=progress_events.append,
        )

    assert exp["ai_used"] is True
    assert exp["batches_ok"] == 1
    assert exp["batch_providers"][0]["provider"] == "gemini"
    assert exp["batch_providers"][0]["failover"] is True
    done_ev = [e for e in progress_events if e.get("stage") == "done"]
    assert done_ev and done_ev[-1]["provider_used"] == "gemini"
    assert done_ev[-1]["failover_used"] is True
