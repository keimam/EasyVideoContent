"""Tests for Script Generator Workflow, Resume, and Outline Editing (2.3, 2.7)."""

import json
from pathlib import Path
import pytest

from app.services.longform.llm_adapter import LLMAdapterError
from app.services.longform.presets import get_preset
from app.services.longform.script_generator import (
    ScriptGenerator,
    count_narration_words,
    extract_chapter_summary_and_facts,
)
from app.services.longform.store import create_project, load_manifest


def test_outline_generation_valid_and_total_words(tmp_path: Path):
    """Verify outline generation produces valid Pydantic model with words matching formula (2.3 Step 1)."""
    manifest, proj_dir = create_project(
        project_id="outline_test",
        title="Outline Test Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    gen = ScriptGenerator(project_dir=proj_dir, provider="fake")
    outline = gen.generate_outline(
        topic="1969 Dodge Charger",
        target_duration_minutes=10.0,
    )

    assert len(outline.candidate_titles) >= 3
    assert len(outline.opening_hook) > 10
    assert len(outline.chapters) >= 3

    # Total words formula: 10 min * 145 wpm = 1450 words
    preset = get_preset("automotive.muscle_car")
    expected_words = int(10.0 * preset.words_per_minute)
    assert outline.total_target_words == expected_words

    # Sum of chapter words should match total_target_words
    ch_sum = sum(c.target_words for c in outline.chapters)
    assert abs(ch_sum - expected_words) <= 5

    # File outline.json exists
    assert (proj_dir / "script" / "outline.json").is_file()


def test_outline_generation_invalid_json_clear_error(tmp_path: Path):
    """Verify invalid JSON from LLM raises clear LLMAdapterError without crashing (2.7)."""
    manifest, proj_dir = create_project(
        project_id="broken_outline",
        title="Broken Outline",
        preset_id="automotive",
        projects_root=tmp_path,
    )

    gen = ScriptGenerator(project_dir=proj_dir, provider="fake")
    # Force fake provider to return invalid JSON
    gen.adapter.generate = lambda *args, **kwargs: type("Resp", (), {
        "content": "NOT VALID JSON { missing closing",
        "provider": "fake",
        "model": "test",
        "duration_seconds": 0.1,
        "tokens": None,
        "cache_hit": False,
    })()

    with pytest.raises(LLMAdapterError) as exc_info:
        gen.generate_outline(topic="Broken Test")

    assert "Failed to parse outline JSON" in str(exc_info.value)


def test_chapter_writing_context_contains_summary_not_full_text(tmp_path: Path, monkeypatch):
    """Verify that chapter generation context receives concise summary, NOT full chapter text (2.3 Step 2)."""
    manifest, proj_dir = create_project(
        project_id="context_test",
        title="Context Test",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    gen = ScriptGenerator(project_dir=proj_dir, provider="fake")
    gen.generate_outline(topic="1969 Dodge Charger", target_duration_minutes=10.0)

    prompts_captured = []
    original_generate = gen.adapter.generate

    def spy_generate(prompt, **kwargs):
        prompts_captured.append(prompt)
        return original_generate(prompt, **kwargs)

    monkeypatch.setattr(gen.adapter, "generate", spy_generate)

    gen.write_chapters()

    # We generated 4 chapters, prompt for Chapter 2 should be captured
    # Check that Chapter 2 prompt contains summary and used facts
    ch2_prompts = [p for p in prompts_captured if "Chapter Index: 2" in p]
    assert len(ch2_prompts) >= 1
    ch2_prompt = ch2_prompts[0]

    # Verify context structure:
    assert "Previous Story Context (DO NOT REPEAT THESE FACTS OR TOPICS):" in ch2_prompt
    assert "Key Claims & Facts Already Established:" in ch2_prompt

    # Full text of chapter 1 has tags like [visual: ...] which should NOT be in previous_summaries
    assert "[visual:" not in ch2_prompt.split("Previous Story Context")[1].split("Assignment for This Chapter")[0]


def test_script_resume_after_failure_at_chapter_n(tmp_path: Path):
    """Verify script drafting can be interrupted at chapter N and resumed without redoing earlier chapters (2.3, 2.7)."""
    manifest, proj_dir = create_project(
        project_id="resume_test",
        title="Resume Test",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    gen = ScriptGenerator(project_dir=proj_dir, provider="fake")
    gen.generate_outline(topic="1969 Dodge Charger", target_duration_minutes=10.0)

    # 1. First run: deliberate failure at Chapter 3
    with pytest.raises(RuntimeError) as exc_info:
        gen.write_chapters(fail_at_chapter=3)
    assert "Simulated failure at chapter 3" in str(exc_info.value)

    # Chapters 1 and 2 should exist on disk, 3 and 4 should not
    assert (proj_dir / "script" / "chapter_01.md").is_file()
    assert (proj_dir / "script" / "chapter_02.md").is_file()
    assert not (proj_dir / "script" / "chapter_03.md").is_file()

    status1 = gen.get_status()
    assert status1["chapters"][0]["status"] == "completed"
    assert status1["chapters"][1]["status"] == "completed"
    assert status1["chapters"][2]["status"] == "pending"

    # 2. Second run: resume to completion without fail_at
    full_script_path = gen.write_chapters()
    assert full_script_path.is_file()

    status2 = gen.get_status()
    assert all(c["status"] == "completed" for c in status2["chapters"])
    assert status2["full_script_exists"] is True

    # Manifest should also be updated with all chapters
    updated_manifest = load_manifest(proj_dir)
    assert len(updated_manifest.chapters) == len(status2["chapters"])


def test_length_revision_max_once(tmp_path: Path, monkeypatch):
    """Verify chapter word count deviation > 15% triggers revise_length exactly once (2.3 Step 3)."""
    manifest, proj_dir = create_project(
        project_id="revision_test",
        title="Revision Test",
        preset_id="automotive",
        projects_root=tmp_path,
    )

    gen = ScriptGenerator(project_dir=proj_dir, provider="fake")
    gen.generate_outline(topic="Automotive History", target_duration_minutes=8.0)

    # Mock adapter to return a very short chapter on first draft (e.g. 50 words instead of ~280)
    original_generate = gen.adapter.generate
    revision_calls = []

    def mock_deviant_generate(prompt, fake_kind=None, **kwargs):
        if fake_kind == "chapter":
            # Very short text that deviates > 15%
            return type("Resp", (), {
                "content": "## Chapter Title\n\nShort single paragraph. [visual: car] [motion: zoom_in]",
                "provider": "fake",
                "model": "test",
                "duration_seconds": 0.05,
                "tokens": None,
                "cache_hit": False,
            })()
        elif fake_kind == "revise_length":
            revision_calls.append(1)
            return original_generate(prompt, fake_kind=fake_kind, **kwargs)
        return original_generate(prompt, fake_kind=fake_kind, **kwargs)

    monkeypatch.setattr(gen.adapter, "generate", mock_deviant_generate)

    gen.write_chapters()

    # Revisions should have run for each chapter, but strictly bounded to 1 per chapter
    assert len(revision_calls) == 4
    status = gen.get_status()
    assert all(c["revised"] is True for c in status["chapters"])


def test_editing_outline_makes_affected_chapters_stale(tmp_path: Path):
    """Verify editing a chapter in outline.json invalidates its hash on next run (2.3, 2.7)."""
    manifest, proj_dir = create_project(
        project_id="outline_edit_test",
        title="Outline Edit Test",
        preset_id="automotive",
        projects_root=tmp_path,
    )

    gen = ScriptGenerator(project_dir=proj_dir, provider="fake")
    gen.generate_outline(topic="Automotive History", target_duration_minutes=8.0)
    gen.write_chapters()

    # Modify Chapter 2 in outline.json
    outline_path = proj_dir / "script" / "outline.json"
    outline_data = json.loads(outline_path.read_text(encoding="utf-8"))
    outline_data["chapters"][1]["title"] = "RADICALLY REVISED CHAPTER TWO TITLE"
    outline_data["chapters"][1]["purpose"] = "Completely different narrative trajectory"
    outline_path.write_text(json.dumps(outline_data, indent=2), encoding="utf-8")

    # Run write_chapters again
    gen.write_chapters()

    # Verify Chapter 2 content now has the new title
    ch2_text = (proj_dir / "script" / "chapter_02.md").read_text(encoding="utf-8")
    assert "RADICALLY REVISED CHAPTER TWO TITLE" in ch2_text


def test_chapter_3_context_receives_summarizer_outputs(tmp_path: Path, monkeypatch):
    """Verify chapter 3 receives narrative summaries from summary.md and not full chapter text (F4)."""
    manifest, proj_dir = create_project(
        project_id="ch3_summary_test",
        title="Chapter 3 Summary Test",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    gen = ScriptGenerator(project_dir=proj_dir, provider="fake")
    gen.generate_outline(topic="1969 Dodge Charger", target_duration_minutes=10.0)

    chapter_prompts = []
    original_generate = gen.adapter.generate

    def spy_generate(prompt, **kwargs):
        chapter_prompts.append(prompt)
        return original_generate(prompt, **kwargs)

    monkeypatch.setattr(gen.adapter, "generate", spy_generate)
    gen.write_chapters()

    # Find the prompt for Chapter 3
    ch3_prompts = [p for p in chapter_prompts if "Chapter Index: 3" in p]
    assert len(ch3_prompts) >= 1
    ch3_prompt = ch3_prompts[0]

    # Verify that summaries for Chapters 1 and 2 are present
    assert "Chapter 1 (" in ch3_prompt
    assert "Chapter 2 (" in ch3_prompt
    assert "narrative developments" in ch3_prompt or "established" in ch3_prompt

    # Verify that raw narration tags from earlier chapters are NOT in the prompt context
    # (Full chapter text has tags like [visual: black classic muscle car])
    context_section = ch3_prompt.split("Previous Story Context")[1].split("Assignment for This Chapter")[0]
    assert "[visual:" not in context_section
    assert "[motion:" not in context_section
    assert "[source:" not in context_section


def test_script_write_keyboard_interrupt_leaves_active_chapter_uncompleted(tmp_path: Path, monkeypatch):
    """Verify KeyboardInterrupt cleans up partial files and allows clean resume (F5)."""
    manifest, proj_dir = create_project(
        project_id="interrupt_test",
        title="Interrupt Test",
        preset_id="automotive",
        projects_root=tmp_path,
    )

    gen = ScriptGenerator(project_dir=proj_dir, provider="fake")
    gen.generate_outline(topic="Interruption Handling", target_duration_minutes=8.0)

    call_count = {"chapter": 0}
    original_generate = gen.adapter.generate

    def interrupt_at_chapter_2(prompt, fake_kind=None, **kwargs):
        if fake_kind == "chapter":
            call_count["chapter"] += 1
            if call_count["chapter"] == 2:
                # Trigger KeyboardInterrupt during Chapter 2 generation
                raise KeyboardInterrupt("Simulated user Ctrl+C")
        return original_generate(prompt, fake_kind=fake_kind, **kwargs)

    monkeypatch.setattr(gen.adapter, "generate", interrupt_at_chapter_2)

    # 1. Run interrupted execution
    with pytest.raises(KeyboardInterrupt):
        gen.write_chapters()

    # Chapter 1 must be completed, Chapter 2 must NOT be marked completed
    status1 = gen.get_status()
    assert status1["chapters"][0]["status"] == "completed"
    assert status1["chapters"][1]["status"] == "pending"

    # Temporary files should be cleaned up
    tmp_files = list((proj_dir / "script").glob("*.tmp.*"))
    assert len(tmp_files) == 0

    # 2. Resume execution with normal adapter
    monkeypatch.setattr(gen.adapter, "generate", original_generate)
    full_script = gen.write_chapters()
    assert full_script.is_file()

    status2 = gen.get_status()
    assert all(c["status"] == "completed" for c in status2["chapters"])
    assert status2["full_script_exists"] is True


def test_fake_labels_and_automatic_quality_inspection(tmp_path: Path):
    """Verify FAKE labels in full_script, quality_report, status, and automatic quality inspection (F1, F3)."""
    manifest, proj_dir = create_project(
        project_id="fake_label_test",
        title="Fake Label Test",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    gen = ScriptGenerator(project_dir=proj_dir, provider="fake")
    gen.generate_outline(topic="Test FAKE Labels", target_duration_minutes=8.0)
    full_script_path = gen.write_chapters()

    # 1. full_script.md must have FAKE comment banner and [FAKE] title
    full_text = full_script_path.read_text(encoding="utf-8")
    assert "<!-- GENERATED BY FAKE PROVIDER (TESTING ONLY) -->" in full_text
    assert "# [FAKE]" in full_text

    # 2. Quality report must exist automatically (F3) and contain FAKE warning (F1)
    quality_report_path = proj_dir / "script" / "quality_report.md"
    assert quality_report_path.is_file()
    report_text = quality_report_path.read_text(encoding="utf-8")
    assert "LLM PROVIDER: FAKE (TESTING MOCK ONLY)" in report_text

    # 3. Script status must report is_fake = True
    status = gen.get_status()
    assert status["is_fake"] is True
    assert status["quality_report_exists"] is True
    assert all(c["is_fake"] is True for c in status["chapters"])
