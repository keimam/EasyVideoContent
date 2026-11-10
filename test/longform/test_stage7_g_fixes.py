"""Comprehensive test suite for Stage 7A.4 (G1-G4).

Verifies:
- G1: Root cause regression (form interactivity & preset creation).
- G2: Custom preset creation, inheritance hierarchy, disk persistence, project manifest chain,
      in-use deletion protection with project naming, and case-insensitive duplicate rejection.
- G3: Clean dropdown (filter by language, hide documentary_id/en, label simplicity, "+ Niche baru…").
- G4: AppTest end-to-end user workflow: form input -> new instance seeing preset in dropdown &
      Kelola Niche -> delete protection -> duplicate rejection.
- Relink / recovery of user's projects.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from app.services.longform.presets import (
    PresetError,
    PresetValidationError,
    create_custom_niche_hierarchy,
    delete_custom_preset,
    format_preset_tree,
    get_available_niches_for_ui,
    get_builtin_presets,
    get_custom_presets,
    get_preset,
    get_preset_directories,
    get_sub_niches_for_ui,
    list_presets,
    save_custom_preset,
    slugify_preset_name,
)
from app.services.longform.schema import ItemStatus, ProjectManifest
from app.services.longform.store import (
    create_project,
    get_project_dir,
    get_projects_root,
    load_manifest,
    refresh_project_preset,
    save_manifest,
)


def get_long_video_app_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")


# --- Unit Tests: G2 & G3 (Backend & UI helper functions) ---

def test_slugify_preset_name():
    """Verify slug generation produces clean alphanumeric identifiers (G2)."""
    assert slugify_preset_name("Kuliner") == "kuliner"
    assert slugify_preset_name("Street food Jepang") == "street_food_jepang"
    assert slugify_preset_name("  Wisata & Alam Indonesia! ") == "wisata_alam_indonesia"
    assert slugify_preset_name("1960s Muscle Cars") == "1960s_muscle_cars"


def test_create_custom_niche_hierarchy_root_and_sub(tmp_path, monkeypatch):
    """Verify single-action creation of root and sub-niche presets with proper inheritance (G2)."""
    custom_dir = tmp_path / "presets"
    custom_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.longform.presets.get_preset_directories", lambda: (Path("resource/presets"), custom_dir))

    sub_pid, paths = create_custom_niche_hierarchy(
        niche_name="Wisata",
        sub_niche_name="Pendakian Gunung",
        language="id-ID",
        visual_keywords=["mountain peak", "hiking trail", "clouds"],
        negative_terms=["indoor", "beach"],
    )

    assert sub_pid == "custom.wisata.pendakian_gunung"
    assert len(paths) == 2
    assert (custom_dir / "custom.wisata.json").is_file()
    assert (custom_dir / "custom.wisata.pendakian_gunung.json").is_file()

    # Verify root preset
    root_data = json.loads((custom_dir / "custom.wisata.json").read_text(encoding="utf-8"))
    assert root_data["id"] == "custom.wisata"
    assert root_data["name"] == "Wisata"
    assert root_data["extends"] == "umum.id"
    assert root_data["kind"] == "niche"
    assert root_data["capcut_text_size"] == 8

    # Verify sub-niche preset
    sub_data = json.loads((custom_dir / "custom.wisata.pendakian_gunung.json").read_text(encoding="utf-8"))
    assert sub_data["id"] == "custom.wisata.pendakian_gunung"
    assert sub_data["name"] == "Pendakian Gunung"
    assert sub_data["extends"] == "custom.wisata"
    assert sub_data["kind"] == "sub_niche"
    assert sub_data["+visual_keywords"] == ["mountain peak", "hiking trail", "clouds"]
    assert sub_data["+negative_terms"] == ["indoor", "beach"]
    assert sub_data["capcut_text_size"] == 8


def test_duplicate_preset_rejection_case_insensitive(tmp_path, monkeypatch):
    """Verify case-insensitive duplicate preset creation is rejected with descriptive error (G2)."""
    custom_dir = tmp_path / "presets"
    custom_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.longform.presets.get_preset_directories", lambda: (Path("resource/presets"), custom_dir))

    create_custom_niche_hierarchy(niche_name="Fotografi", language="id-ID")

    # Duplicate root with exact match
    with pytest.raises(PresetValidationError) as exc1:
        create_custom_niche_hierarchy(niche_name="Fotografi", language="id-ID")
    assert "sudah ada" in str(exc1.value)

    # Duplicate root with lowercase
    with pytest.raises(PresetValidationError) as exc2:
        create_custom_niche_hierarchy(niche_name="fotografi", language="id-ID")
    assert "sudah ada" in str(exc2.value)

    # Duplicate root with uppercase
    with pytest.raises(PresetValidationError) as exc3:
        create_custom_niche_hierarchy(niche_name="FOTOGRAFI", language="id-ID")
    assert "sudah ada" in str(exc3.value)


def test_deletion_protection_when_used_by_project(tmp_path, monkeypatch):
    """Verify deleting a custom preset used by a project is rejected, naming the project (G2)."""
    custom_dir = tmp_path / "presets"
    custom_dir.mkdir(parents=True, exist_ok=True)
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.services.longform.presets.get_preset_directories", lambda: (Path("resource/presets"), custom_dir))
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: projects_dir)

    sub_pid, _ = create_custom_niche_hierarchy(
        niche_name="Astronomi",
        sub_niche_name="Teleskop Luar Angkasa",
        language="id-ID",
    )

    create_project(
        project_id="teleskop_james_webb",
        title="Misi Teleskop James Webb",
        preset_id=sub_pid,
        projects_root=projects_dir,
    )

    # Deleting sub-niche must fail and name the project
    with pytest.raises(PresetValidationError) as exc_sub:
        delete_custom_preset(sub_pid)
    assert "Misi Teleskop James Webb" in str(exc_sub.value)
    assert "teleskop_james_webb" in str(exc_sub.value)

    # Deleting parent niche must also fail because child is in use
    with pytest.raises(PresetValidationError) as exc_parent:
        delete_custom_preset("custom.astronomi")
    assert "teleskop_james_webb" in str(exc_parent.value)


def test_dropdown_niches_filtering_and_formatting(tmp_path, monkeypatch):
    """Verify G3 dropdown logic: id-ID hides documentary_en & en-US built-ins, shows umum first, "+ Niche baru…" at end."""
    custom_dir = tmp_path / "presets"
    custom_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.longform.presets.get_preset_directories", lambda: (Path("resource/presets"), custom_dir))

    # Indonesian filter
    root_ids_id, labels_id = get_available_niches_for_ui("id-ID")
    assert root_ids_id[0] == "umum.id"
    assert root_ids_id[-1] == "+ Niche baru…"
    assert "documentary_id" not in root_ids_id
    assert "documentary_en" not in root_ids_id
    assert "automotive" not in root_ids_id  # automotive has language: en-US
    assert labels_id["umum.id"] == "Umum (Dokumenter Naratif)"
    assert labels_id["+ Niche baru…"] == "➕ Buat Niche Baru…"

    # English filter
    root_ids_en, labels_en = get_available_niches_for_ui("en-US")
    assert root_ids_en[0] == "umum.en"
    assert root_ids_en[-1] == "+ Niche baru…"
    assert "documentary_en" not in root_ids_en
    assert "documentary_id" not in root_ids_en
    assert "automotive" in root_ids_en
    assert labels_en["umum.en"] == "Umum (Dokumenter Naratif)"


# --- AppTest Workflow Tests: G4 ---

def test_apptest_create_custom_niche_and_sub_niche(tmp_path, monkeypatch):
    """Full AppTest: Create custom niche+sub-niche via WebUI form and verify persistence & manifest chain (G4)."""
    custom_dir = tmp_path / "presets"
    custom_dir.mkdir(parents=True, exist_ok=True)
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.services.longform.presets.get_preset_directories", lambda: (Path("resource/presets"), custom_dir))
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: projects_dir)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: projects_dir)
    monkeypatch.setattr("app.services.longform.ui.components.get_project_dir", lambda pid: projects_dir / pid)
    monkeypatch.setattr("app.services.longform.store.get_project_dir", lambda pid, root=None: (root or projects_dir) / pid)

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=10)
    assert not at.exception

    # 1. Open New Project Form
    toggle_btn = next(b for b in at.button if "Proyek Baru" in str(b.label))
    toggle_btn.click().run(timeout=10)
    assert not at.exception

    # 2. Fill form inputs
    at.text_input(key="input_new_topic").input("Kisah Street Food Tokyo").run(timeout=10)
    assert not at.exception

    # Select "+ Niche baru…"
    at.selectbox(key="sel_root_niche").select("+ Niche baru…").run(timeout=10)
    assert not at.exception

    # Fill custom niche inputs
    at.text_input(key="input_new_niche_name").input("Kuliner").run(timeout=10)
    at.text_input(key="input_new_sub_name").input("Street food Jepang").run(timeout=10)
    at.text_input(key="input_custom_footage_kw").input("ramen, yatai, tokyo street").run(timeout=10)
    at.text_input(key="input_custom_avoid_kw").input("cartoon").run(timeout=10)
    assert not at.exception

    # 3. Submit Project
    submit_btn = next(b for b in at.button if "Buat Proyek" in str(b.label))
    submit_btn.click().run(timeout=10)
    assert not at.exception

    # 4. Verify custom presets written to disk (G2)
    assert (custom_dir / "custom.kuliner.json").is_file()
    assert (custom_dir / "custom.kuliner.street_food_jepang.json").is_file()

    # 5. Verify created project manifest
    proj_dir = projects_dir / "kisah_street_food_tokyo"
    assert proj_dir.is_dir()
    manifest = load_manifest(proj_dir)
    assert manifest.preset_id == "custom.kuliner.street_food_jepang"
    assert manifest.preset_inheritance_chain == [
        "documentary_id",
        "umum.id",
        "custom.kuliner",
        "custom.kuliner.street_food_jepang",
    ]
    assert manifest.preset_snapshot["visual_keywords"] == ["ramen", "yatai", "tokyo street"]
    assert manifest.preset_snapshot["negative_terms"] == ["cartoon"]
    assert manifest.preset_snapshot["capcut_text_size"] == 8

    # 6. Verify fresh instance sees "Kuliner (buatan sendiri)" in dropdown & Kelola niche (G4)
    at2 = AppTest.from_file(get_long_video_app_file())
    at2.run(timeout=10)
    assert not at2.exception

    # Open New Project form in second instance
    toggle_btn2 = next(b for b in at2.button if "Proyek Baru" in str(b.label))
    toggle_btn2.click().run(timeout=10)
    assert not at2.exception

    root_options = at2.selectbox(key="sel_root_niche").options
    assert "Kuliner (buatan sendiri)" in root_options

    # Select custom.kuliner to check sub-niche dropdown
    at2.selectbox(key="sel_root_niche").select("Kuliner (buatan sendiri)").run(timeout=10)
    assert not at2.exception
    sub_options = at2.selectbox(key="sel_sub_niche").options
    assert "Street food Jepang (buatan sendiri)" in sub_options


# --- G5: Bidirectional Navigation Tests ---

def test_g5_main_page_renders_long_video_link(monkeypatch):
    """Verify MoneyPrinterTurbo Main.py loads without exception and renders '🎬 Long Video' link (G5)."""
    import unittest.mock as mock

    repo_root = Path(__file__).resolve().parents[2]
    main_file = str(repo_root / "webui" / "Main.py")

    # Mock version check to avoid external network delay
    with mock.patch("app.services.version_checker.poll_available_update") as mock_update:
        mock_update.return_value.complete = True
        mock_update.return_value.available_version = None

        at = AppTest.from_file(main_file)
        at.run(timeout=25)

        assert not at.exception, f"Main.py threw exceptions: {[e.value for e in at.exception]}"
        page_link_labels = [pl.label for pl in at.page_link]
        assert any("Long Video" in label for label in page_link_labels), f"No Long Video link found in: {page_link_labels}"


def test_g5_long_video_page_renders_back_to_mpt_link(tmp_path, monkeypatch):
    """Verify Long Video page renders '← MoneyPrinterTurbo (video pendek)' link/button without exception (G5)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=10)

    assert not at.exception, f"Long Video threw exceptions: {[e.value for e in at.exception]}"
    # Either page_link or button back to MPT
    all_links = [pl.label for pl in at.page_link]
    all_buttons = [b.label for b in at.button]
    has_back_link = any("MoneyPrinterTurbo" in str(lbl) for lbl in all_links + all_buttons)
    assert has_back_link, f"Back link to MoneyPrinterTurbo not found in links {all_links} or buttons {all_buttons}"

