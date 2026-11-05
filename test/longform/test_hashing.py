"""Tests for canonical hashing, invalidation propagation, and normalization (K1, K2, K3)."""

from app.services.longform.hashing import (
    canonical_json,
    compute_audio_group_hash,
    compute_chapter_hash,
    compute_final_concat_hash,
    compute_visual_segment_hash,
    normalize_text,
)
from app.services.longform.schema import (
    AudioSpec,
    Chapter,
    EncoderSettings,
    Segment,
    TtsGroup,
    VisualSpec,
)


def test_text_normalization():
    """Verify Unicode NFC normalization and whitespace collapse."""
    # Composed vs decomposed é
    decomposed = "e\u0301cole"
    composed = "\u00e9cole"
    assert normalize_text(decomposed) == normalize_text(composed)

    # Whitespace normalization
    raw = "  Hello   world!  \n\n This is \t a test.   "
    assert normalize_text(raw) == "Hello world! This is a test."


def test_tts_group_hashing_isolation():
    """Verify editing group 1 changes only group 1 hash, leaving group 2 unaffected (K3)."""
    g1_hash_initial = compute_audio_group_hash("This is paragraph one.")
    g2_hash_initial = compute_audio_group_hash("This is paragraph two.")

    # Modify only paragraph 1
    g1_hash_modified = compute_audio_group_hash("This is paragraph one, with an edit.")

    assert g1_hash_modified != g1_hash_initial
    # Group 2 remains identical
    assert compute_audio_group_hash("This is paragraph two.") == g2_hash_initial


def test_chapter_hash_sensitive_to_segment_order():
    """Verify sequential composition (K1): reversing segments changes chapter hash."""
    encoder = EncoderSettings()
    subtitle = {"font_size": 18}
    preset_snap = {"name": "Test"}

    seg1 = Segment(
        segment_index=1,
        narration_text="Sentence 1",
        visual=VisualSpec(visual_hash="hash_v1"),
    )
    seg2 = Segment(
        segment_index=2,
        narration_text="Sentence 2",
        visual=VisualSpec(visual_hash="hash_v2"),
    )

    group1 = TtsGroup(
        group_index=1,
        paragraph_text="Sentence 1 Sentence 2",
        audio=AudioSpec(audio_hash="hash_a1"),
        segments=[seg1, seg2],
    )
    ch_order1 = Chapter(chapter_index=1, chapter_title="Ch1", tts_groups=[group1])
    hash1 = compute_chapter_hash(ch_order1, encoder, subtitle, preset_snap)

    # Reverse segments in group
    group2 = TtsGroup(
        group_index=1,
        paragraph_text="Sentence 2 Sentence 1",
        audio=AudioSpec(audio_hash="hash_a1"),
        segments=[seg2, seg1],
    )
    ch_order2 = Chapter(chapter_index=1, chapter_title="Ch1", tts_groups=[group2])
    hash2 = compute_chapter_hash(ch_order2, encoder, subtitle, preset_snap)

    assert hash1 != hash2


def test_chapter_hash_sensitive_to_preset_snapshot_change():
    """Verify snapshot hash (K2): changing preset snapshot changes chapter hash."""
    encoder = EncoderSettings()
    subtitle = {"font_size": 18}
    ch = Chapter(chapter_index=1, chapter_title="Ch1", tts_groups=[])

    hash_snap_a = compute_chapter_hash(ch, encoder, subtitle, {"words_per_minute": 140})
    hash_snap_b = compute_chapter_hash(ch, encoder, subtitle, {"words_per_minute": 160})

    assert hash_snap_a != hash_snap_b


def test_visual_hash_sensitive_to_image_source_content():
    """Verify segment visual hash changes when underlying image content changes."""
    hash_img1 = compute_visual_segment_hash(source_content_hash="aaaa1111", motion="zoom_in", duration=5.0)
    hash_img2 = compute_visual_segment_hash(source_content_hash="bbbb2222", motion="zoom_in", duration=5.0)

    assert hash_img1 != hash_img2


def test_final_concat_hash_sensitive_to_chapter_order():
    """Verify final concat hash changes if chapters are reordered."""
    ch1 = Chapter(chapter_index=1, chapter_title="Ch1", chapter_hash="ch1_hash")
    ch2 = Chapter(chapter_index=2, chapter_title="Ch2", chapter_hash="ch2_hash")

    concat1 = compute_final_concat_hash([ch1, ch2])
    concat2 = compute_final_concat_hash([ch2, ch1])

    assert concat1 != concat2
