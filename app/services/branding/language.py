"""Single source of truth for UI language normalization, mapping, and widget sync (Stage 8C.3, E9.1)."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

from loguru import logger
import streamlit as st

CANONICAL_LANGUAGES = ("id", "en")

# Explicit E9.1 code mapping:
#   id <-> id-ID / "Bahasa Indonesia"
#   en <-> en / "English"
LANGUAGE_MAPPINGS: Dict[str, Dict[str, str]] = {
    "id": {
        "canonical": "id",
        "mpt_code": "id",
        "narration_code": "id-ID",
        "display_name": "Bahasa Indonesia",
        "wizard_label": "Bahasa Indonesia (id)",
    },
    "en": {
        "canonical": "en",
        "mpt_code": "en",
        "narration_code": "en",
        "display_name": "English",
        "wizard_label": "English (en)",
    },
}

_LANGUAGE_ALIASES: Dict[str, str] = {
    "id": "id",
    "id-id": "id",
    "id_id": "id",
    "indonesia": "id",
    "indonesian": "id",
    "bahasa indonesia": "id",
    "en": "en",
    "en-us": "en",
    "en_us": "en",
    "en-gb": "en",
    "en_gb": "en",
    "english": "en",
}

LANGUAGE_WIDGET_KEYS = (
    "wiz_lang_radio_main",
    "wiz_lang_radio_home",
    "wiz_lang_radio_wizard",
    "settings_ui_language_select",
    "top_language_code_selector",
)


def normalize_ui_language(value: Any, *, default: str = "id") -> str:
    """Map any UI/narration/display language representation to canonical 'id' or 'en'."""
    raw = str(value or "").strip()
    if not raw:
        return "id" if default == "id" else "en"
    lowered = raw.lower()
    if lowered in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[lowered]
    normalized = lowered.replace("_", "-")
    if normalized in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[normalized]
    base = normalized.split("-", 1)[0]
    if base == "id":
        return "id"
    # Unsupported non-empty languages (e.g. 'zh', 'de') fall back to 'en' per L4/i18n spec.
    return "en"


def resolve_mpt_locale_code(
    value: Any,
    *,
    supported_codes: Optional[Iterable[str]] = None,
    default: str = "id",
) -> str:
    """Resolve `value` to an upstream MPT locale key ('id', 'en', or another supported locale)."""
    supported = [str(c).strip() for c in (supported_codes or ())]
    raw = str(value or "").strip()
    if not raw:
        return default
    lowered = raw.lower()
    if lowered in _LANGUAGE_ALIASES:
        mapped = _LANGUAGE_ALIASES[lowered]
        if not supported or mapped in supported:
            return mapped
    normalized = lowered.replace("_", "-")
    if supported:
        by_lower = {code.lower(): code for code in supported if code}
        if normalized in by_lower:
            return by_lower[normalized]
        base = normalized.split("-", 1)[0]
        if base in by_lower:
            return by_lower[base]
        return default if default in supported else supported[0]
    if normalized.startswith("id"):
        return "id"
    if normalized.startswith("en"):
        return "en"
    return raw


def to_narration_code(value: Any) -> str:
    """Map canonical/arbitrary language to explicit narration code ('id-ID' or 'en')."""
    return LANGUAGE_MAPPINGS[normalize_ui_language(value)]["narration_code"]


def to_display_name(value: Any) -> str:
    """Map canonical/arbitrary language to explicit display name ('Bahasa Indonesia' or 'English')."""
    return LANGUAGE_MAPPINGS[normalize_ui_language(value)]["display_name"]


def to_wizard_label(value: Any) -> str:
    """Return consistent radio/select label for EasyFaceless language selectors."""
    return LANGUAGE_MAPPINGS[normalize_ui_language(value)]["wizard_label"]


def _read_config_ui_language() -> str:
    try:
        from app.config import config

        return str(config.ui.get("language", "") or "").strip()
    except Exception:
        return ""


def _write_config_ui_language(mpt_code: str, *, persist: bool = True) -> None:
    try:
        from app.config import config

        if not config.update_config_nonblocking(config.ui, "language", mpt_code):
            logger.debug(f"Deferred config.ui.language='{mpt_code}' until active task completes")
        if persist:
            if hasattr(config, "try_save_config"):
                config.try_save_config()
            else:
                config.save_config()
    except Exception as exc:
        logger.debug(f"Could not persist config.ui.language='{mpt_code}': {exc}")


def _sync_widget_keys(mpt_code: str, canonical: str, *, exclude_key: Optional[str] = None) -> None:
    """Keep all registered EasyFaceless & MPT language widget keys aligned without script-body reruns."""
    try:
        ss = st.session_state
    except Exception:
        return
    target_by_key = {
        "wiz_lang_radio_main": canonical,
        "wiz_lang_radio_home": canonical,
        "wiz_lang_radio_wizard": canonical,
        "settings_ui_language_select": canonical,
        "top_language_code_selector": mpt_code,
    }
    for key, target in target_by_key.items():
        if key == exclude_key:
            continue
        if key in ss and ss.get(key) != target:
            ss[key] = target


def sync_query_param_language_once() -> None:
    """Apply `?lang=...` query param only when it changes externally (never overriding user clicks)."""
    try:
        qp_lang = str(st.query_params.get("lang") or "").strip()
    except Exception:
        return
    if not qp_lang:
        return
    try:
        applied_qp = st.session_state.get("_efs_applied_qp_lang")
    except Exception:
        applied_qp = None
    if applied_qp != qp_lang:
        canonical = normalize_ui_language(qp_lang)
        try:
            st.session_state["ui_language"] = canonical
            st.session_state["_efs_applied_qp_lang"] = qp_lang
            _sync_widget_keys(canonical, canonical)
        except Exception:
            pass


def ensure_ui_language_initialized(supported_codes: Optional[Iterable[str]] = None) -> str:
    """Initialize `st.session_state['ui_language']` from single canonical source before widgets render."""
    sync_query_param_language_once()
    try:
        ss = st.session_state
        raw = ss.get("ui_language")
    except Exception:
        ss = None
        raw = None

    if not raw:
        raw = _read_config_ui_language() or "id"

    mpt_code = resolve_mpt_locale_code(raw, supported_codes=supported_codes, default="id")
    canonical = normalize_ui_language(mpt_code)

    if ss is not None:
        if ss.get("ui_language") != mpt_code:
            ss["ui_language"] = mpt_code
        for wk in ("wiz_lang_radio_main", "wiz_lang_radio_home", "wiz_lang_radio_wizard", "settings_ui_language_select"):
            ss.setdefault(wk, canonical)
        ss.setdefault("top_language_code_selector", mpt_code)

    return canonical


def get_canonical_language() -> str:
    """Return active canonical UI language ('id' or 'en') for EasyFaceless UI & t()."""
    sync_query_param_language_once()
    try:
        raw = st.session_state.get("ui_language")
    except Exception:
        raw = None

    if not raw:
        raw = _read_config_ui_language() or "id"

    return normalize_ui_language(raw)


def set_ui_language(
    raw_value: Any,
    *,
    persist: bool = True,
    source_widget_key: Optional[str] = None,
    supported_codes: Optional[Iterable[str]] = None,
    set_runtime_cfg_fn: Optional[Callable[[str, str, Any], Any]] = None,
    save_runtime_cfg_fn: Optional[Callable[[], Any]] = None,
) -> str:
    """Set canonical language across session_state, config.ui.language, query_params, and sibling widgets."""
    mpt_code = resolve_mpt_locale_code(raw_value, supported_codes=supported_codes, default="id")
    canonical = normalize_ui_language(mpt_code)

    try:
        ss = st.session_state
        previous = str(ss.get("ui_language") or "")
        ss["ui_language"] = mpt_code
        _sync_widget_keys(mpt_code, canonical, exclude_key=source_widget_key)
        for content_key in ("video_subject", "video_script", "video_terms"):
            if content_key in ss:
                ss[content_key] = ss[content_key]
        if previous != mpt_code:
            logger.info(f"UI language set via '{source_widget_key or 'api'}': {previous or '<empty>'} -> {mpt_code}")
    except Exception:
        pass

    try:
        if "lang" in st.query_params:
            st.query_params["lang"] = canonical
            st.session_state["_efs_applied_qp_lang"] = canonical
    except Exception:
        pass

    if set_runtime_cfg_fn is not None:
        set_runtime_cfg_fn("ui", "language", mpt_code)
        if persist and save_runtime_cfg_fn is not None:
            save_runtime_cfg_fn()
    else:
        _write_config_ui_language(mpt_code, persist=persist)

    return canonical


def on_language_widget_change(
    widget_key: str,
    supported_codes: Optional[Iterable[str]] = None,
) -> None:
    """Streamlit widget `on_change` callback shared by Wizard, Settings, and MPT dropdown (E9.1)."""
    try:
        chosen = st.session_state.get(widget_key)
    except Exception:
        return
    if not chosen:
        return
    set_ui_language(
        chosen,
        persist=True,
        source_widget_key=widget_key,
        supported_codes=supported_codes,
    )
