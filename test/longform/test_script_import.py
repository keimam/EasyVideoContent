"""Tests for script import, abbreviation-safe tokenizer, tag parsing, and duration warnings."""

from app.services.longform.presets import get_preset
from app.services.longform.schema import MotionType, VisualSourceType
from app.services.longform.script_import import (
    estimate_script_duration,
    extract_tags,
    parse_markdown_script,
    split_sentences,
)


def test_sentence_tokenizer_abbreviations_and_decimals():
    """Verify tokenizer does not break on abbreviations, decimals, or ellipsis."""
    text = (
        "Dr. Thomas Harriot met Gov. John F. Kennedy in the U.S. yesterday. "
        "They spent $3.14 on coffee! "
        "Was it worth 99.9% of their budget? "
        "Nobody could tell... But the story continued."
    )
    sentences = split_sentences(text)
    assert len(sentences) == 4
    assert sentences[0] == "Dr. Thomas Harriot met Gov. John F. Kennedy in the U.S. yesterday."
    assert sentences[1] == "They spent $3.14 on coffee!"
    assert sentences[2] == "Was it worth 99.9% of their budget?"
    assert sentences[3] == "Nobody could tell... But the story continued."


def test_tag_extraction_and_cleaning():
    """Verify [visual: ...] and [motion: ...] tags are extracted and cleaned from text."""
    raw = "An ancient castle stood on the hill. [visual: gothic medieval fortress] [motion: pan_left]"
    clean, tags = extract_tags(raw)
    assert clean == "An ancient castle stood on the hill."
    assert tags["visual"] == "gothic medieval fortress"
    assert tags["motion"] == "pan_left"


def test_trailing_tags_after_punctuation_stay_with_sentence():
    """Verify tags positioned after punctuation stay attached to the preceding sentence."""
    raw = (
        "First sentence. [visual: image of ocean] [motion: zoom_in] "
        "Second sentence! [visual: close up face] "
        "Third sentence without tags."
    )
    sentences = split_sentences(raw)
    assert len(sentences) == 3

    clean1, tags1 = extract_tags(sentences[0])
    assert clean1 == "First sentence."
    assert tags1["visual"] == "image of ocean"
    assert tags1["motion"] == "zoom_in"

    clean2, tags2 = extract_tags(sentences[1])
    assert clean2 == "Second sentence!"
    assert tags2["visual"] == "close up face"

    clean3, tags3 = extract_tags(sentences[2])
    assert clean3 == "Third sentence without tags."
    assert len(tags3) == 0


def test_script_import_multi_chapter_structure():
    """Verify full Markdown script import creates Chapter, TtsGroup, and Segment models."""
    preset = get_preset("documentary_en")
    script = """# Title of Documentary

## Chapter 1: The Beginning
First paragraph sentence one. Second sentence of first paragraph. [visual: vintage map]

Second paragraph starts here. It has another sentence.

## Chapter 2: The Climax
Third paragraph in chapter two. [motion: static]
"""
    chapters, title = parse_markdown_script(script, preset)
    assert title == "Title of Documentary"
    assert len(chapters) == 2
    assert chapters[0].chapter_title == "The Beginning"
    assert len(chapters[0].tts_groups) == 2
    assert len(chapters[0].tts_groups[0].segments) == 2

    # Check that prompt was extracted
    seg2 = chapters[0].tts_groups[0].segments[1]
    assert seg2.visual.prompt == "vintage map"

    assert chapters[1].chapter_title == "The Climax"
    assert chapters[1].tts_groups[0].segments[0].visual.motion == MotionType.static


def test_duration_estimation_and_warnings():
    """Verify duration estimator calculates timing and warns when outside 600-1200s (K6)."""
    preset = get_preset("documentary_en")  # 140 WPM

    # 1. Short script (e.g. 14 words = 6 seconds) -> warning
    short_script = "## Ch1\n" + ("word " * 14) + ".\n"
    chapters_short, _ = parse_markdown_script(short_script, preset)
    stats_short = estimate_script_duration(chapters_short, preset.words_per_minute)
    assert stats_short["total_words"] == 14
    assert stats_short["estimated_duration_seconds"] == 6.0
    assert len(stats_short["warnings"]) == 1
    assert "below recommended minimum" in stats_short["warnings"][0]

    # 2. Medium script (e.g. 1820 words = ~13 minutes = 780 seconds) -> no warnings
    medium_script = "## Ch1\n" + " ".join(["word"] * 1820) + ".\n"
    chapters_med, _ = parse_markdown_script(medium_script, preset)
    stats_med = estimate_script_duration(chapters_med, preset.words_per_minute)
    assert stats_med["total_words"] == 1820
    assert 600 <= stats_med["estimated_duration_seconds"] <= 1200
    assert len(stats_med["warnings"]) == 0


def test_clean_chapter_title_prefix_stripping_and_sample_script():
    """Verify clean_chapter_title strips repeated Chapter/Bab prefixes and cleans sample_script.md (E1)."""
    from pathlib import Path
    from app.services.longform.script_import import clean_chapter_title

    # Single prefix
    assert clean_chapter_title("Chapter 1: The Beginning") == "The Beginning"
    assert clean_chapter_title("Bab 2 - Mesin V8") == "Mesin V8"
    assert clean_chapter_title("Ch. 3: Aero Dynamics") == "Aero Dynamics"

    # Repeated prefixes
    assert clean_chapter_title("Chapter 1: Chapter 1: Foundations of the Local Land") == "Foundations of the Local Land"
    assert clean_chapter_title("Bab 1: Chapter 1 - Ch 1: Detroit Steel") == "Detroit Steel"

    # Fallback when only chapter number is provided
    assert clean_chapter_title("Chapter 1", fallback_index=1) == "Chapter 1"
    assert clean_chapter_title("Ch 2", fallback_index=2) == "Chapter 2"

    # Test with actual test/resources/sample_script.md
    sample_path = Path(__file__).resolve().parent.parent / "resources" / "sample_script.md"
    assert sample_path.is_file()
    sample_text = sample_path.read_text(encoding="utf-8")
    preset = get_preset("documentary_en")
    chapters, title = parse_markdown_script(sample_text, preset)
    assert title == "The Local Heritage Journey"
    assert len(chapters) == 1
    # Line 3 of sample_script.md: "## Chapter 1: Foundations of the Local Land" -> stripped to subtitle
    assert chapters[0].chapter_title == "Foundations of the Local Land"

