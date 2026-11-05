"""Tests for Script Quality Inspector, Cliché Detection, Fact-Checking, and New Tags (2.3, 2.4, 2.7)."""

from pathlib import Path
import pytest

from app.services.longform.presets import get_preset
from app.services.longform.quality_checker import ScriptQualityChecker
from app.services.longform.schema import VisualSourceType
from app.services.longform.script_generator import ScriptGenerator
from app.services.longform.script_import import extract_tags, parse_markdown_script
from app.services.longform.store import create_project, update_project_script


def test_new_tags_parsed_by_script_import():
    """Verify script_import parses [source: ...], [search: ...], and [verify: ...] tags (2.3)."""
    preset = get_preset("automotive.muscle_car")
    script_text = """# Muscle Car Legend

## Chapter 1: The Detroit Beast
In 1969, the street racing scene exploded in Detroit. [visual: black classic muscle car on highway] [motion: zoom_in] [source: stock] [search: 1969 muscle car highway cruising]

Under the sculpted hood lay a dual-quad iron block. [visual: engine bay closeup] [motion: pan_left] [source: local] [search: v8 engine bay carburetor] [verify: 426 Hemi factory rated at 425 horsepower]
"""
    chapters, title = parse_markdown_script(script_text, preset)
    assert len(chapters) == 1
    assert title == "Muscle Car Legend"

    ch1 = chapters[0]
    assert len(ch1.tts_groups) == 2

    # Segment 1
    seg1 = ch1.tts_groups[0].segments[0]
    assert "In 1969, the street racing scene exploded in Detroit." == seg1.narration_text
    assert seg1.visual.type == VisualSourceType.stock_video
    assert seg1.visual.search_keywords == "1969 muscle car highway cruising"
    assert seg1.verify is None

    # Segment 2
    seg2 = ch1.tts_groups[1].segments[0]
    assert "Under the sculpted hood lay a dual-quad iron block." == seg2.narration_text
    assert seg2.visual.type == VisualSourceType.local
    assert seg2.visual.search_keywords == "v8 engine bay carburetor"
    assert seg2.verify == "426 Hemi factory rated at 425 horsepower"


def test_quality_checker_detects_cliches_hook_repetition_and_verify(tmp_path: Path):
    """Verify ScriptQualityChecker identifies clichés, hook violations, repetition, and fact checks (2.4, 2.7)."""
    manifest, proj_dir = create_project(
        project_id="quality_test",
        title="Quality Test Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    # Script containing intentional flaws:
    # - Hook violation in opening sentence: "Welcome back to our channel..."
    # - Clichés: "buckle up", "tapestry", "game-changer"
    # - Repeated phrase between Chapter 1 and Chapter 2: "the raw mechanical torque shook the chassis violently"
    # - Unverified specification: "producing 500 horsepower at 6500 rpm" (no verify tag)
    # - Verified claim: with [verify: ...]
    test_script = """# Flawed Script

## Chapter 1: Bad Opening
Welcome back to our channel! In today's video, buckle up as we delve into the rich tapestry of horsepower. [visual: car] [motion: zoom_in]

The raw mechanical torque shook the chassis violently during acceleration. [visual: track] [motion: pan_right] [verify: Chassis flexed under 500 lb-ft of torque]

## Chapter 2: Repetition & Specs
The raw mechanical torque shook the chassis violently during acceleration. [visual: track repeat] [motion: zoom_out]

It was a true game-changer producing 500 horsepower at 6500 rpm without doubt. [visual: engine] [motion: static]
"""
    update_project_script(proj_dir, test_script)

    checker = ScriptQualityChecker(project_dir=proj_dir)
    report = checker.inspect()

    # 1. Hook violation
    assert len(report["hook_violations"]) >= 1
    assert "violates hook rules" in report["hook_violations"][0]

    # 2. Clichés found
    cliche_names = [c["cliche"] for c in report["cliche_matches"]]
    assert "buckle up" in cliche_names
    assert "delve" in cliche_names
    assert "tapestry" in cliche_names
    assert "game-changer" in cliche_names

    # 3. Repeated phrase between Chapter 1 and Chapter 2
    assert len(report["repeated_ngrams"]) >= 1
    repeated_phrases = [r["phrase"] for r in report["repeated_ngrams"]]
    assert any("raw mechanical torque shook the chassis" in p for p in repeated_phrases)

    # 4. Verified claim collected
    assert len(report["verify_claims"]) >= 1
    assert report["verify_claims"][0]["claim"] == "Chassis flexed under 500 lb-ft of torque"

    # 5. Unverified specification flagged
    assert len(report["unverified_specs"]) >= 1
    spec_sentences = [u["sentence"] for u in report["unverified_specs"]]
    assert any("500 horsepower" in s for s in spec_sentences)

    # 6. Report file generated on disk
    assert checker.report_file.is_file()
    report_content = checker.report_file.read_text(encoding="utf-8")
    assert "Fact-Checking Checklist" in report_content
    assert "Cliché & AI Slop Detection" in report_content
