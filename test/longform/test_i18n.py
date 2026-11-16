"""Unit tests for Long Video i18n localization system (8A.2 L4)."""

import pytest
from app.services.longform.ui.i18n import (
    ID_TRANSLATIONS,
    EN_TRANSLATIONS,
    get_ui_language,
    t,
)


def test_i18n_key_symmetry():
    """Verify that every single key in ID_TRANSLATIONS exists in EN_TRANSLATIONS and vice versa."""
    id_keys = set(ID_TRANSLATIONS.keys())
    en_keys = set(EN_TRANSLATIONS.keys())

    missing_in_en = id_keys - en_keys
    missing_in_id = en_keys - id_keys

    assert not missing_in_en, f"Keys present in ID but missing in EN: {missing_in_en}"
    assert not missing_in_id, f"Keys present in EN but missing in ID: {missing_in_id}"
    assert len(id_keys) > 100, f"Expected comprehensive dictionary, found {len(id_keys)} keys"


def test_i18n_formatting():
    """Verify that parameter formatting works in both languages."""
    msg_id = t("project_next_step", lang="id", step="Visual")
    assert msg_id == "Berikutnya: Visual"

    msg_en = t("project_next_step", lang="en", step="Visual")
    assert msg_en == "Next: Visual"

    prog_id = t("project_step_progress", lang="id", completed=3)
    assert prog_id == "3/5 langkah"

    prog_en = t("project_step_progress", lang="en", completed=3)
    assert prog_en == "3/5 steps"


def test_i18n_fallback_behavior(monkeypatch):
    """Verify language resolution and fallback: 'zh' or unsupported falls back to 'en'."""
    # Test with monkeypatched session state
    class FakeState(dict):
        pass

    fake_session = FakeState()

    import streamlit as st
    monkeypatch.setattr(st, "session_state", fake_session)

    # 1. Default when session is empty (falls back to config or 'id')
    assert get_ui_language() in {"id", "en"}

    # 2. Indonesian session
    fake_session["ui_language"] = "id"
    assert get_ui_language() == "id"

    fake_session["ui_language"] = "id-ID"
    assert get_ui_language() == "id"

    # 3. English session
    fake_session["ui_language"] = "en"
    assert get_ui_language() == "en"

    fake_session["ui_language"] = "en-US"
    assert get_ui_language() == "en"

    # 4. Chinese session -> Must fallback to 'en' (L4 requirement)
    fake_session["ui_language"] = "zh"
    assert get_ui_language() == "en"

    # 5. Other languages (es, fr, de, etc.) -> Fallback to 'en'
    fake_session["ui_language"] = "de"
    assert get_ui_language() == "en"


def test_brand_names_not_translated():
    """Verify L4 rule: Brand names 'EasyFaceless Studio' and 'MoneyPrinterTurbo' are not translated."""
    for lang in ["id", "en"]:
        about_title = t("about_dialog_title", lang=lang)
        assert "EasyFaceless Studio" in about_title

        built_on = t("nav_about_help", lang=lang)
        assert "MoneyPrinterTurbo" in built_on


def test_i18n_codebase_scanner():
    """Verify V1: Every single t('...') key called anywhere in code exists in both ID and EN dictionaries."""
    import re
    from pathlib import Path

    key_pattern = re.compile(r'\bt\(\s*[\"\']([a-zA-Z0-9_]+)[\"\']')
    repo_root = Path(__file__).resolve().parents[2]

    files = (
        list((repo_root / "app" / "services" / "longform" / "ui").rglob("*.py"))
        + list((repo_root / "app" / "services" / "branding").rglob("*.py"))
        + list((repo_root / "webui" / "pages").rglob("*.py"))
    )

    missing_in_id = set()
    missing_in_en = set()

    for f in files:
        content = f.read_text(encoding="utf-8")
        for m in key_pattern.finditer(content):
            k = m.group(1)
            if k not in ID_TRANSLATIONS:
                missing_in_id.add((k, f.name))
            if k not in EN_TRANSLATIONS:
                missing_in_en.add((k, f.name))

    assert not missing_in_id, f"Found t(...) keys in code missing in ID_TRANSLATIONS: {missing_in_id}"
    assert not missing_in_en, f"Found t(...) keys in code missing in EN_TRANSLATIONS: {missing_in_en}"


def test_no_raw_i18n_keys_rendered_in_apptest(tmp_path, monkeypatch):
    """Verify V1: No raw i18n keys (e.g. gallery_heading, unit_minutes) leak into rendered UI."""
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    from app.services.longform.store import create_project

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    # 1. Check Home page
    repo_root = Path(__file__).resolve().parents[2]
    app_file = str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")

    create_project("test_proj_p1", "Test Project Title", "automotive.muscle_car", projects_root=tmp_path)

    at = AppTest.from_file(app_file)
    at.run(timeout=15)
    assert not at.exception

    all_ui_text = " ".join([m.value for m in at.markdown] + [b.label for b in at.button if b.label] + [t.value for t in at.title])
    known_leaks = ["gallery_heading", "all_chapters_option", "filter_needs_review", "filter_locked", "btn_auto_run_until_done", "unit_minutes"]
    for leak in known_leaks:
        assert leak not in all_ui_text, f"Raw i18n key '{leak}' was rendered on the Home page!"

    # 2. Check Project page
    at_proj = AppTest.from_file(app_file)
    at_proj.query_params["project"] = "test_proj_p1"
    at_proj.run(timeout=15)
    assert not at_proj.exception

    all_proj_text = " ".join([m.value for m in at_proj.markdown] + [b.label for b in at_proj.button if b.label] + [t.value for t in at_proj.title])
    for leak in known_leaks:
        assert leak not in all_proj_text, f"Raw i18n key '{leak}' was rendered on the Project page!"


def test_no_hardcoded_ui_strings_without_t():
    """Verify V7: AST scanner detects any raw Indonesian/English string literals passed to st.* or custom UI without t()."""
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    ui_files = [
        repo_root / "app" / "services" / "longform" / "ui" / "components.py",
        repo_root / "app" / "services" / "longform" / "ui" / "nav.py",
        repo_root / "app" / "services" / "branding" / "sidebar.py",
        repo_root / "webui" / "pages" / "1_🎬_Long_Video.py",
    ]

    st_ui_funcs = {
        "button", "markdown", "caption", "header", "subheader", "title", "write",
        "info", "warning", "error", "success", "expander", "tabs", "text_input",
        "text_area", "selectbox", "slider", "number_input", "checkbox", "radio",
        "pills", "popover", "file_uploader", "download_button", "page_link",
        "spinner", "toast", "metric", "dialog", "progress",
    }
    custom_ui_funcs = {"render_metric_tile", "render_success_banner", "render_stepper_card_html"}
    non_text_whitelist = {
        "---", "✕", "⋯", "▷", "←", "->", "|", "•", "text", "auto", "wide", "large",
        "primary", "secondary", "collapsed", "audio/wav", "N/A", "MP4", "SRT", "FPS",
        "LUFS", "16:9", "9:16", "en-US", "id-ID", "CapCut", "MoneyPrinterTurbo",
        "MoneyPrinterTurbo (MIT)", "EasyFaceless Studio", "MPT_CALIBRATE", "Main.py",
    }

    def is_non_text_literal(s: str) -> bool:
        s2 = s.strip()
        if not s2 or s2 in non_text_whitelist:
            return True
        if s2.startswith((":material/", "http://", "https://", "<div", "<span", "<h1", "<p", "<style", "```")):
            return True
        if all(not c.isalpha() for c in s2):
            return True
        return False

    def check_str_node(node, lineno):
        found = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if not is_non_text_literal(node.value):
                found.append((lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    cleaned = v.value.strip("*#>`-_:.()[]{} \n\t•·›")
                    if len(cleaned) >= 3 and any(c.isalpha() for c in cleaned) and not is_non_text_literal(cleaned):
                        found.append((lineno, repr(v.value)))
                elif isinstance(v, ast.FormattedValue):
                    if isinstance(v.value, ast.Call) and isinstance(v.value.func, ast.Name) and v.value.func.id == "t":
                        continue
                    found.extend(check_str_node(v.value, lineno))
        elif isinstance(node, ast.IfExp):
            found.extend(check_str_node(node.body, lineno))
            found.extend(check_str_node(node.orelse, lineno))
        elif isinstance(node, (ast.List, ast.Tuple)):
            for elt in node.elts:
                found.extend(check_str_node(elt, lineno))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            found.extend(check_str_node(node.left, lineno))
            found.extend(check_str_node(node.right, lineno))
        return found

    violations = []
    for fp in ui_files:
        tree = ast.parse(fp.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fname = ""
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "st":
                    fname = node.func.attr
                elif isinstance(node.func, ast.Name) and node.func.id in custom_ui_funcs:
                    fname = node.func.id
                if fname in st_ui_funcs or fname in custom_ui_funcs:
                    if node.args:
                        is_unsafe = any(
                            kw.arg == "unsafe_allow_html" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                            for kw in node.keywords
                        )
                        if not is_unsafe:
                            for ln, txt in check_str_node(node.args[0], node.lineno):
                                violations.append(f"{fp.name}:{ln}: {txt}")
                    for kw in node.keywords:
                        if kw.arg in {"label", "placeholder", "help", "text", "title"}:
                            for ln, txt in check_str_node(kw.value, node.lineno):
                                violations.append(f"{fp.name}:{ln} ({kw.arg}=): {txt}")

    assert not violations, f"Found hardcoded UI string literals without t():\n" + "\n".join(violations)


def _collect_all_rendered_ui_strings(at) -> list[str]:
    """Collect visible text from all Streamlit AppTest widget containers."""
    texts = []
    for el in at.title:
        texts.append(str(el.value))
    for el in at.header:
        texts.append(str(el.value))
    for el in at.subheader:
        texts.append(str(el.value))
    for el in at.markdown:
        texts.append(str(el.value))
    for el in at.caption:
        texts.append(str(el.value))
    for el in at.info:
        texts.append(str(el.value))
    for el in at.warning:
        texts.append(str(el.value))
    for el in at.error:
        texts.append(str(el.value))
    for el in at.success:
        texts.append(str(el.value))
    for el in at.button:
        if el.label:
            texts.append(str(el.label))
    for el in at.text_input:
        if el.label:
            texts.append(str(el.label))
    for el in at.text_area:
        if el.label:
            texts.append(str(el.label))
    for el in at.selectbox:
        if el.label:
            texts.append(str(el.label))
    for el in at.slider:
        if el.label:
            texts.append(str(el.label))
    for el in at.checkbox:
        if el.label:
            texts.append(str(el.label))
    for el in at.expander:
        if el.label:
            texts.append(str(el.label))
    for el in at.tabs:
        if el.label:
            texts.append(str(el.label))
    return texts


@pytest.mark.parametrize("lang", ["id", "en"])
def test_all_steps_no_mixed_language_apptest(tmp_path, monkeypatch, lang):
    """Verify V7: Rendering Home and all 5 workflow steps in ID and EN produces zero text from the other language."""
    import re
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from app.services.longform.schema import AudioSpec, Chapter, ItemStatus, MotionType, Segment, Shot, TtsGroup
    from app.services.longform.store import create_project, save_manifest

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    repo_root = Path(__file__).resolve().parents[2]
    app_file = str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")

    # Setup realistic project with script, voice, and visual shots (including candidates)
    pid = "v7_i18n_audit_proj"
    manifest, p_dir = create_project(pid, "Audit Bilingual Flow", "automotive.muscle_car", projects_root=tmp_path)
    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter Alpha",
        audio_duration_seconds=8.0,
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="Sample narration line for testing UI translation coverage.",
                audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=8.0),
                segments=[Segment(segment_index=1, narration_text="Sample narration line.")],
            ),
        ],
        shots=[
            Shot(
                shot_id="ch01_shot001",
                chapter_index=1,
                shot_index=1,
                segment_indices=[1],
                visual_prompt="Muscle car on road",
                duration_seconds=4.0,
                target_frames=100,
                motion=MotionType.zoom_in,
                status=ItemStatus.completed,
                candidates=[
                    {"candidate_id": "10001", "provider": "pixabay", "url": "https://example.com/10001.mp4", "relevance_score": 0.9, "duration_seconds": 5.0},
                    {"candidate_id": "10002", "provider": "pixabay", "url": "https://example.com/10002.mp4", "relevance_score": 0.85, "duration_seconds": 6.0},
                ],
            ),
        ],
    )
    manifest.chapters = [ch]
    (p_dir / "assets" / "audio" / "groups").mkdir(parents=True, exist_ok=True)
    (p_dir / "assets" / "audio" / "groups" / "ch1_g1.wav").write_bytes(b"RIFF" + b"\x00" * 100)
    save_manifest(manifest, p_dir)

    # Strictly language-specific phrases that must NOT appear in the opposite language
    id_only_phrases = [
        "naskah", "sintesis audio", "pilihan suara", "filter bahasa suara",
        "pilih karakter suara", "kecepatan bicara", "dengarkan sampel",
        "terapkan suara", "pratinjau bab", "buat video pratinjau",
        "klip siap", "perlu dicek", "terkunci", "sumber terpakai",
        "cari lagi", "unggah", "pakai ini", "kandidat", "bersihkan cache",
        "buat proyek", "mulai video baru", "buka proyek", "kelola niche",
        "video panjang", "video pendek", "pengaturan lanjutan", "kembali ke beranda",
        "cek ulang", "ekspor ke capcut", "ukuran teks subtitle",
    ]
    en_only_phrases = [
        "create project", "open project", "start a new video", "advanced settings",
        "manage niches", "long video", "short video", "all projects",
        "auto-run until done", "start voice synthesis", "voice options, speed",
        "voice language filter", "select voice character", "speech speed",
        "listen to voice sample", "apply voice to project", "chapter 1 preview video",
        "create chapter 1 preview video", "clips ready", "needs review",
        "sources used", "search again", "use this", "candidates",
        "prune old cache", "render final video", "recheck capcut status",
        "export to capcut", "subtitle text size", "next: script", "next: voice",
    ]
    forbidden = id_only_phrases if lang == "en" else en_only_phrases

    # Inspect Home + all 5 steps
    pages_to_check = [
        {"name": "home", "params": {"lang": lang}},
        {"name": "script", "params": {"project": pid, "step": "script", "lang": lang}},
        {"name": "voice", "params": {"project": pid, "step": "voice", "lang": lang}},
        {"name": "visual", "params": {"project": pid, "step": "visual", "lang": lang}},
        {"name": "render", "params": {"project": pid, "step": "render", "lang": lang}},
        {"name": "capcut", "params": {"project": pid, "step": "capcut", "lang": lang}},
    ]

    leaks = []
    for page in pages_to_check:
        at = AppTest.from_file(app_file)
        for pk, pv in page["params"].items():
            at.query_params[pk] = pv
        at.run(timeout=15)
        assert not at.exception, f"Exception in {page['name']} ({lang}): {[e.value for e in at.exception]}"

        raw_texts = _collect_all_rendered_ui_strings(at)
        # Strip HTML tags and check word-boundary/phrase matches (ignoring user-entered project/chapter titles)
        cleaned_texts = [re.sub(r"<[^>]+>", " ", txt).lower() for txt in raw_texts]
        for txt in cleaned_texts:
            for phrase in forbidden:
                # Use regex boundary for short single words to avoid false positives in unrelated strings
                if " " not in phrase:
                    if re.search(rf"\b{re.escape(phrase)}\b", txt):
                        leaks.append(f"[{lang} / {page['name']}] Forbidden term '{phrase}' found in: {txt[:120]}")
                else:
                    if phrase in txt:
                        leaks.append(f"[{lang} / {page['name']}] Forbidden phrase '{phrase}' found in: {txt[:120]}")

    assert not leaks, "Mixed-language strings leaked across UI steps:\n" + "\n".join(leaks)


