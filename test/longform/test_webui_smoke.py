"""Smoke and component tests for Long Video WebUI (Stage 7A.1, C4, C5).

Uses streamlit.testing.v1.AppTest to verify:
- Clean render with empty storage (no projects) (C4.1)
- Clean render with project cards on Beranda (C4.1)
- Project detail page with 5-step stepper and status badges (C4.3)
- Single adaptive primary button labels per status (C4.3)
- API key masking security and safe Windows paths (B6)
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from app.services.longform.schema import ItemStatus, ProjectManifest, Chapter, TtsGroup
from app.services.longform.store import create_project, get_project_dir, save_manifest
from app.services.longform.ui.security import mask_api_key, safe_windows_path


def get_long_video_app_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")


def test_api_key_masking_security():
    """Verify API key masking security."""
    assert mask_api_key(None) == "(belum diatur)"
    assert mask_api_key("") == "(belum diatur)"
    assert mask_api_key("12345") == "****"

    pk_key = "pk_live_123456789abcdef0"
    masked_pk = mask_api_key(pk_key)
    assert masked_pk.startswith("pk_****")
    assert masked_pk.endswith("ef0")
    assert "123456789" not in masked_pk

    sk_key = "sk-proj-9876543210zyxwvu"
    masked_sk = mask_api_key(sk_key)
    assert masked_sk.startswith("sk-****")
    assert masked_sk.endswith("wvu")
    assert "9876543210" not in masked_sk

    aiza_key = "AIzaSyD1234567890abcdef"
    masked_aiza = mask_api_key(aiza_key)
    assert masked_aiza.startswith("AIz****")
    assert masked_aiza.endswith("cdef")
    assert "1234567890" not in masked_aiza


def test_safe_windows_path_handling():
    """Verify safe Windows path normalization with spaces."""
    raw_path = r"C:\Users\IT SUPPORT 2\Desktop\test project"
    safe = safe_windows_path(raw_path)
    assert "IT SUPPORT 2" in safe
    assert isinstance(safe, str)


def test_webui_smoke_empty_storage(tmp_path, monkeypatch):
    """Smoke test: verify Beranda opens cleanly when storage has 0 projects (C4.1, C5)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=10)

    assert not at.exception, f"AppTest threw exceptions: {[e.value for e in at.exception]}"
    info_messages = [inf.value for inf in at.info]
    assert any("Belum ada proyek" in msg for msg in info_messages)
    # Form expander exists
    assert any("Buat Proyek Baru" in str(exp.label) for exp in at.expander)


def test_webui_smoke_with_test_project_cards(tmp_path, monkeypatch):
    """Smoke test: verify Beranda renders project cards with next step and open button (C4.1, C5)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "smoke_card_proj"
    create_project(
        project_id=test_pid,
        title="Smoke Card Muscle Car Legend",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=10)

    assert not at.exception, f"AppTest threw exceptions: {[e.value for e in at.exception]}"
    # Verify project card title and next step rendered in markdown
    all_markdown = " ".join(m.value for m in at.markdown)
    assert "Smoke Card Muscle Car Legend" in all_markdown
    assert "Berikutnya: Naskah" in all_markdown

    # Verify Open button exists
    open_btns = [b for b in at.button if "Buka" in (b.label or "")]
    assert len(open_btns) >= 1


def test_webui_smoke_project_page_stepper_and_adaptive_button(tmp_path, monkeypatch):
    """Smoke test: verify Project Page renders stepper and adaptive primary button (C4.3, C5)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "smoke_step_proj"
    create_project(
        project_id=test_pid,
        title="Stepper Test Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    # Open directly with query parameter ?project=smoke_step_proj
    at = AppTest.from_file(get_long_video_app_file())
    at.query_params["project"] = test_pid
    at.run(timeout=10)

    assert not at.exception, f"AppTest threw exceptions: {[e.value for e in at.exception]}"
    all_titles = " ".join(t.value for t in at.title)
    assert "Stepper Test Project" in all_titles

    # Check back button exists
    back_btns = [b for b in at.button if "Kembali ke Beranda" in (b.label or "")]
    assert len(back_btns) == 1

    # Check stepper buttons exist (5 steps)
    stepper_buttons = [b for b in at.button if any(b.label and step in b.label for step in ["① Naskah", "② Suara", "③ Visual", "④ Render", "⑤ CapCut"])]
    assert len(stepper_buttons) == 5

    # Check single primary action button has adaptive label "Mulai Tulis Naskah"
    action_btns = [b for b in at.button if "Mulai Tulis Naskah" in (b.label or "")]
    assert len(action_btns) == 1


def test_webui_smoke_uji_nokey_project_page():
    """Smoke test: verify real uji_nokey project displays completed stepper and buttons (C4.3, C5)."""
    at = AppTest.from_file(get_long_video_app_file())
    at.query_params["project"] = "uji_nokey"
    at.run(timeout=10)

    assert not at.exception, f"AppTest threw exceptions: {[e.value for e in at.exception]}"
    all_titles = " ".join(t.value for t in at.title)
    assert "Uji Tanpa Key" in all_titles

    # Check stepper displays completed badges
    stepper_buttons = [b for b in at.button if any(b.label and step in b.label for step in ["① Naskah", "② Suara", "③ Visual", "④ Render", "⑤ CapCut"])]
    assert len(stepper_buttons) == 5
    assert all("✅ Selesai" in b.label for b in stepper_buttons)

    # With all steps completed, active step defaults to script, where label is "Selesai ✔ — Lanjut ke Suara"
    completed_nav_btns = [b for b in at.button if "Selesai ✔ — Lanjut ke Suara" in (b.label or "")]
    assert len(completed_nav_btns) == 1


def test_webui_smoke_stopped_state_adaptive_resume_button(tmp_path, monkeypatch):
    """Smoke test: verify that stopped voice step shows 'Lanjutkan Sintesis Audio' button (C1, C4.3, C5)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "stopped_voice_proj"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Stopped Voice Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )
    # Add 2 chapters with TTS groups
    from app.services.longform.schema import AudioSpec
    ch1 = Chapter(chapter_index=1, chapter_title="Ch 1", status=ItemStatus.completed)
    g1 = TtsGroup(group_index=1, paragraph_text="Paragraph 1 text.")
    ch1.tts_groups.append(g1)
    ch2 = Chapter(chapter_index=2, chapter_title="Ch 2", status=ItemStatus.completed)
    g2 = TtsGroup(group_index=1, paragraph_text="Paragraph 2 text.")
    ch2.tts_groups.append(g2)
    manifest.chapters = [ch1, ch2]

    # Fake audio for group 1 only (so 1 of 2 groups is done -> stopped!)
    g1_file = p_dir / "assets" / "audio" / "groups" / "ch1_g1.wav"
    g1_file.parent.mkdir(parents=True, exist_ok=True)
    g1_file.write_bytes(b"RIFF" + b"\x00" * 100)
    g1.audio = AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=3.5)
    save_manifest(manifest, p_dir)

    at = AppTest.from_file(get_long_video_app_file())
    at.query_params["project"] = test_pid
    at.run(timeout=10)

    assert not at.exception, f"AppTest threw exceptions: {[e.value for e in at.exception]}"

    # Script is completed (2 chapters), so active step automatically switches to voice!
    # In voice step, completed 1/2 -> status is "terhenti"!
    resume_btns = [b for b in at.button if "Lanjutkan Sintesis Audio" in (b.label or "")]
    assert len(resume_btns) == 1
    assert "▶️ Lanjutkan Sintesis Audio" in resume_btns[0].label
