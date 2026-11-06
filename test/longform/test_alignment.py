import pytest
from app.services.longform.alignment import (
    WordCue,
    align_group_segments,
    align_proportionally,
    align_word_cues_to_segments,
    normalize_token,
    tokens_match,
)
from app.services.longform.schema import Segment, VisualSpec


def test_normalize_token():
    assert normalize_token("Hello,") == "hello"
    assert normalize_token('"Quote"') == "quote"
    assert normalize_token("1964.") == "1964"
    assert normalize_token("1,000") == "1000"
    assert normalize_token("don't") == "dont"
    assert normalize_token("") == ""


def test_tokens_match():
    assert tokens_match("pontiac", "Pontiac")
    assert tokens_match("1964", "1964")
    assert tokens_match("muscle-car", "muscle")
    assert tokens_match("introduction", "introduct")
    assert not tokens_match("apple", "banana")


def test_align_single_sentence():
    sentences = ["The 1964 Pontiac GTO created the American muscle car era."]
    cues = [
        WordCue(word="the", start=0.1, end=0.3),
        WordCue(word="1964", start=0.35, end=0.8),
        WordCue(word="gto", start=0.85, end=1.5),
    ]
    boundaries, method = align_word_cues_to_segments(sentences, cues, group_duration=3.5, word_cue_threshold=0.25)
    assert method == "word_cue"
    assert len(boundaries) == 1
    assert boundaries[0] == (0.0, 3.5)


def test_align_multi_sentence_zero_gap_midpoint():
    sentences = [
        "In 1964, Pontiac shocked Detroit with the GTO.",
        "It combined a lightweight tempest body with a massive 389 Tri-Power V8 engine.",
        "Sales exploded beyond all expectations.",
    ]
    cues = [
        # Sentence 1
        WordCue(word="in", start=0.2, end=0.4),
        WordCue(word="1964", start=0.4, end=0.8),
        WordCue(word="pontiac", start=0.8, end=1.2),
        WordCue(word="shocked", start=1.2, end=1.6),
        WordCue(word="detroit", start=1.6, end=2.0),
        WordCue(word="with", start=2.0, end=2.2),
        WordCue(word="the", start=2.2, end=2.4),
        WordCue(word="gto", start=2.4, end=2.8),
        # Pause: 2.8 to 3.4 (midpoint = 3.1)
        # Sentence 2
        WordCue(word="it", start=3.4, end=3.6),
        WordCue(word="combined", start=3.6, end=4.0),
        WordCue(word="a", start=4.0, end=4.1),
        WordCue(word="lightweight", start=4.1, end=4.6),
        WordCue(word="engine", start=4.6, end=5.2),
        # Pause: 5.2 to 5.6 (midpoint = 5.4)
        # Sentence 3
        WordCue(word="sales", start=5.6, end=6.0),
        WordCue(word="exploded", start=6.0, end=6.5),
        WordCue(word="beyond", start=6.5, end=7.0),
        WordCue(word="expectations", start=7.0, end=7.6),
    ]

    boundaries, method = align_word_cues_to_segments(sentences, cues, group_duration=8.0, word_cue_threshold=0.50)
    assert method == "word_cue"
    assert len(boundaries) == 3

    # Segment 1 must start at 0.000
    assert boundaries[0][0] == 0.0
    # Boundary between 1 and 2 split at midpoint of (2.8, 3.4) -> 3.1
    assert boundaries[0][1] == 3.1
    assert boundaries[1][0] == 3.1
    # Boundary between 2 and 3 split at midpoint of (5.2, 5.6) -> 5.4
    assert boundaries[1][1] == 5.4
    assert boundaries[2][0] == 5.4
    # Segment 3 must end exactly at group_duration
    assert boundaries[2][1] == 8.0

    # Ensure zero gaps and zero overlaps
    for i in range(len(boundaries) - 1):
        assert boundaries[i][1] == boundaries[i + 1][0]


def test_align_fallback_proportional_low_match():
    sentences = [
        "First sentence here.",
        "Second sentence is completely mismatched with cues.",
    ]
    # Cues that do not match the text at all
    cues = [
        WordCue(word="astronomy", start=0.5, end=1.0),
        WordCue(word="galaxy", start=1.2, end=2.0),
    ]

    boundaries, method = align_word_cues_to_segments(sentences, cues, group_duration=6.0)
    assert method == "proportional"
    assert len(boundaries) == 2
    assert boundaries[0][0] == 0.0
    assert boundaries[0][1] == boundaries[1][0]
    assert boundaries[1][1] == 6.0


def test_align_group_segments_helper():
    segments = [
        Segment(
            segment_index=1,
            narration_text="The concept was revolutionary.",
            visual=VisualSpec(prompt="Car on open road"),
        ),
        Segment(
            segment_index=2,
            narration_text="Drivers loved the raw power.",
            visual=VisualSpec(prompt="Engine close-up"),
        ),
    ]

    updated, method = align_group_segments(
        segments=segments,
        group_duration=5.0,
        word_cues=None,  # triggers proportional
    )

    assert method == "proportional"
    assert len(updated) == 2
    assert updated[0].start_offset_seconds == 0.0
    assert updated[0].end_offset_seconds == updated[1].start_offset_seconds
    assert updated[1].end_offset_seconds == 5.0
    assert round(updated[0].duration_seconds + updated[1].duration_seconds, 2) == 5.0
