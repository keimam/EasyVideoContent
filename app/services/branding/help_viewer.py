"""In-app Help document viewer for EasyFaceless Studio (C8.1)."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple

import streamlit as st

from app.services.branding.constants import PROJECT_ROOT
from app.services.longform.ui.i18n import get_ui_language, t


# Canonical bilingual document mapping (relative paths inside PROJECT_ROOT)
_DOC_CATALOG: Dict[str, List[Tuple[str, str, str]]] = {
    "id": [
        ("docs/user/id/README.md", "Indeks Panduan (Bahasa Indonesia)", "docs/user/en/README.md"),
        ("docs/user/id/mulai-cepat.md", "Mulai Cepat (Proyek → CapCut)", "docs/user/en/quick-start.md"),
        ("docs/user/id/pengaturan-ai.md", "Pengaturan AI & Penyedia API", "docs/user/en/ai-settings.md"),
        ("docs/user/id/niche-preset.md", "Niche & Preset Kustom", "docs/user/en/niches-presets.md"),
        ("docs/user/id/galeri-visual.md", "Galeri Visual & Kata Kunci AI", "docs/user/en/visual-gallery.md"),
        ("docs/user/id/capcut-ekspor.md", "Ekspor CapCut Desktop", "docs/user/en/capcut-export.md"),
        ("docs/user/id/tempat-sampah.md", "Tempat Sampah & Penghapusan", "docs/user/en/trash-management.md"),
        ("docs/user/id/faq-troubleshooting.md", "FAQ & Pemecahan Masalah", "docs/user/en/faq-troubleshooting.md"),
        ("README.md", "README — Ringkasan Aplikasi", "README.md"),
        ("THIRD_PARTY_NOTICES.md", "Pemberitahuan Pihak Ketiga", "THIRD_PARTY_NOTICES.md"),
        ("LICENSE", "Lisensi Aplikasi (MIT)", "LICENSE"),
    ],
    "en": [
        ("docs/user/en/README.md", "User Guide Index (English)", "docs/user/id/README.md"),
        ("docs/user/en/quick-start.md", "Quick Start (Project → CapCut)", "docs/user/id/mulai-cepat.md"),
        ("docs/user/en/ai-settings.md", "AI & API Provider Settings", "docs/user/id/pengaturan-ai.md"),
        ("docs/user/en/niches-presets.md", "Niches & Custom Presets", "docs/user/id/niche-preset.md"),
        ("docs/user/en/visual-gallery.md", "Visual Gallery & AI Keywords", "docs/user/id/galeri-visual.md"),
        ("docs/user/en/capcut-export.md", "CapCut Desktop Export", "docs/user/id/capcut-ekspor.md"),
        ("docs/user/en/trash-management.md", "Trash & Safe Deletion", "docs/user/id/tempat-sampah.md"),
        ("docs/user/en/faq-troubleshooting.md", "FAQ & Troubleshooting", "docs/user/id/faq-troubleshooting.md"),
        ("README.md", "README — Project Overview", "README.md"),
        ("THIRD_PARTY_NOTICES.md", "Third-Party Notices", "THIRD_PARTY_NOTICES.md"),
        ("LICENSE", "Application License (MIT)", "LICENSE"),
    ],
}

_IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK_MD_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")


def get_help_documents(doc_lang: str) -> List[Tuple[str, str, str]]:
    """Return list of `(rel_path, label, counterpart_rel_path)` for `doc_lang` ('id' or 'en')."""
    norm = "id" if str(doc_lang or "").lower().startswith("id") else "en"
    return list(_DOC_CATALOG[norm])


def _resolve_repo_relative(base_rel_path: str, target: str) -> Optional[str]:
    """Resolve a markdown link/image path relative to `base_rel_path` safely within `PROJECT_ROOT`."""
    raw = str(target or "").strip().split("#", 1)[0].split("?", 1)[0]
    if not raw or raw.startswith(("http://", "https://", "mailto:", "data:")):
        return None
    root = PROJECT_ROOT.resolve()
    base_dir = (root / base_rel_path).resolve().parent
    try:
        resolved = (base_dir / raw).resolve()
        resolved.relative_to(root)
    except Exception:
        # Also try resolving directly from repo root
        try:
            resolved = (root / raw).resolve()
            resolved.relative_to(root)
        except Exception:
            return None
    if resolved.exists():
        return resolved.relative_to(root).as_posix()
    return None


def prepare_help_markdown(
    doc_rel_path: str,
    raw_text: str,
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str]]]:
    """Transform local markdown for clean in-app Streamlit rendering.

    Returns:
        - `blocks`: ordered list of `("markdown", text, "")` or `("image", abs_path, alt)`
        - `related_docs`: deduplicated list of `(rel_path, label)` for valid repo doc links
    """
    all_valid_docs = {
        rel: lbl
        for lang_list in _DOC_CATALOG.values()
        for rel, lbl, _ in lang_list
    }
    related_docs: List[Tuple[str, str]] = []
    seen_related: set[str] = set()
    lines_out: List[str] = []
    pending_images: List[Tuple[str, str]] = []

    for line in (raw_text or "").splitlines():
        stripped = line.strip()

        # Skip pure markdown table separator lines only if the whole table is image-only
        # Replace relative markdown images cleanly: keep alt text inline & queue valid image files
        def _img_sub(match: re.Match[str]) -> str:
            alt = (match.group(1) or "").strip()
            target = (match.group(2) or "").strip()
            if target.startswith(("http://", "https://")):
                return match.group(0)
            rel_img = _resolve_repo_relative(doc_rel_path, target)
            if rel_img and (PROJECT_ROOT / rel_img).is_file():
                pending_images.append((str((PROJECT_ROOT / rel_img).resolve()), alt))
                return f"🖼️ *{alt}*" if alt else ""
            # Omit missing image URL without breaking markdown rendering
            return f"*{alt}*" if alt else ""

        line_no_img = _IMG_MD_RE.sub(_img_sub, line)

        # Replace relative links (`mulai-cepat.md`, `../../../README.md`, etc.) with bold text
        # and collect valid document targets so the viewer can offer quick jump buttons.
        def _link_sub(match: re.Match[str]) -> str:
            label = (match.group(1) or "").strip()
            target = (match.group(2) or "").strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                return match.group(0)
            rel_doc = _resolve_repo_relative(doc_rel_path, target)
            if rel_doc and rel_doc in all_valid_docs and rel_doc != doc_rel_path:
                if rel_doc not in seen_related:
                    seen_related.add(rel_doc)
                    related_docs.append((rel_doc, label or all_valid_docs[rel_doc]))
                return f"**`{label}`**"
            return f"**{label}**"

        clean_line = _LINK_MD_RE.sub(_link_sub, line_no_img)
        lines_out.append(clean_line)

        # Flush standalone images right after non-table lines, or after tables finish
        if pending_images and not stripped.startswith("|"):
            pass

    # Partition into markdown + image blocks (flush images cleanly without breaking tables)
    blocks: List[Tuple[str, str, str]] = [("markdown", "\n".join(lines_out), "")]
    for img_path, alt in pending_images:
        blocks.append(("image", img_path, alt))
    return blocks, related_docs


def _find_counterpart_doc(current_rel: str, target_lang: str) -> str:
    """Map a doc path to its counterpart in `target_lang`, defaulting to that language's README."""
    norm = "id" if str(target_lang or "").lower().startswith("id") else "en"
    target_docs = _DOC_CATALOG[norm]
    target_paths = {rel for rel, _, _ in target_docs}
    if current_rel in target_paths:
        return current_rel
    other_lang = "en" if norm == "id" else "id"
    for rel, _, counterpart in _DOC_CATALOG[other_lang]:
        if rel == current_rel and counterpart in target_paths:
            return counterpart
    return target_docs[0][0]


def _on_help_lang_change() -> None:
    """Update selected help doc language and map current doc to counterpart."""
    new_lang = st.session_state.get("efs_help_lang_radio", "id")
    st.session_state["efs_help_doc_lang"] = new_lang
    new_doc = _find_counterpart_doc(st.session_state.get("efs_help_doc_path", ""), new_lang)
    st.session_state["efs_help_doc_path"] = new_doc
    st.session_state["efs_help_doc_select"] = new_doc


def _on_help_doc_select() -> None:
    """Persist dropdown document choice."""
    st.session_state["efs_help_doc_path"] = st.session_state.get("efs_help_doc_select", "")


def _on_help_jump(rel_target: str, current_doc_lang: str) -> None:
    """Jump to a related document from inside the Help viewer."""
    target_lang = (
        "en"
        if rel_target.startswith("docs/user/en/")
        else ("id" if rel_target.startswith("docs/user/id/") else current_doc_lang)
    )
    st.session_state["efs_help_doc_lang"] = target_lang
    st.session_state["efs_help_lang_radio"] = target_lang
    st.session_state["efs_help_doc_path"] = rel_target
    st.session_state["efs_help_doc_select"] = rel_target


def render_help_dialog_body() -> None:
    """Render shared update banner (E11.3) + in-app Help document viewer inside `st.dialog` (C8.1)."""
    try:
        from app.services.branding.lifecycle_ui import render_update_banner
        render_update_banner(key_prefix="help_dlg")
    except Exception:
        pass
    ui_lang = get_ui_language()
    last_ui_lang = st.session_state.get("efs_help_last_ui_lang")
    if last_ui_lang != ui_lang:
        st.session_state["efs_help_doc_lang"] = ui_lang
        st.session_state["efs_help_lang_radio"] = ui_lang
        st.session_state["efs_help_last_ui_lang"] = ui_lang
        cur_doc = st.session_state.get("efs_help_doc_path", "")
        mapped = _find_counterpart_doc(cur_doc, ui_lang) if cur_doc else get_help_documents(ui_lang)[0][0]
        st.session_state["efs_help_doc_path"] = mapped
        st.session_state["efs_help_doc_select"] = mapped

    doc_lang = st.session_state.get("efs_help_doc_lang", ui_lang)
    if doc_lang not in ("id", "en"):
        doc_lang = ui_lang

    docs = get_help_documents(doc_lang)
    doc_paths = [rel for rel, _, _ in docs]
    doc_labels = {rel: lbl for rel, lbl, _ in docs}
    selected_path = st.session_state.get("efs_help_doc_path", doc_paths[0])
    if selected_path not in doc_labels:
        selected_path = doc_paths[0]
        st.session_state["efs_help_doc_path"] = selected_path
    st.session_state["efs_help_doc_select"] = selected_path
    st.session_state["efs_help_lang_radio"] = doc_lang

    ctrl_col1, ctrl_col2 = st.columns([2.2, 1.0])
    with ctrl_col1:
        st.selectbox(
            t("help_doc_select_label"),
            options=doc_paths,
            format_func=lambda r: doc_labels.get(r, r),
            key="efs_help_doc_select",
            on_change=_on_help_doc_select,
        )
    with ctrl_col2:
        st.radio(
            t("help_doc_lang_label"),
            options=["id", "en"],
            format_func=lambda x: "🇮🇩 Bahasa Indonesia" if x == "id" else "🇬🇧 English",
            horizontal=True,
            key="efs_help_lang_radio",
            on_change=_on_help_lang_change,
        )

    selected_path = st.session_state.get("efs_help_doc_path", doc_paths[0])
    file_path = (PROJECT_ROOT / selected_path).resolve()
    st.caption(f"{t('help_doc_source_label')} `{selected_path}`")
    st.divider()

    if not file_path.is_file():
        st.warning(t("help_doc_missing", path=selected_path))
    else:
        raw_text = file_path.read_text(encoding="utf-8", errors="replace")
        if file_path.suffix.lower() not in (".md", ".markdown"):
            st.code(raw_text, language="text")
        else:
            blocks, related_docs = prepare_help_markdown(selected_path, raw_text)
            for kind, val, alt in blocks:
                if kind == "markdown" and val.strip():
                    st.markdown(val)
                elif kind == "image":
                    try:
                        st.image(val, caption=alt or None, use_container_width=True)
                    except Exception:
                        if alt:
                            st.caption(alt)

            if related_docs:
                st.divider()
                st.caption(t("help_doc_related_label"))
                btn_cols = st.columns(min(3, len(related_docs)))
                for idx, (rel_target, rel_lbl) in enumerate(related_docs[:6]):
                    with btn_cols[idx % len(btn_cols)]:
                        st.button(
                            f"📄 {rel_lbl}",
                            key=f"efs_help_jump_{idx}_{rel_target}",
                            use_container_width=True,
                            on_click=_on_help_jump,
                            args=(rel_target, doc_lang),
                        )

    st.divider()
    if st.button(t("dialog_close"), key="btn_close_help_dialog", use_container_width=True):
        st.rerun()
