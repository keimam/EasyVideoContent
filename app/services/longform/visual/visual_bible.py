"""Project Visual Bible builder for consistent external image generation (V102 F1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from app.services.longform.hashing import hash_string, normalize_text
from app.services.longform.llm_adapter import (
    LLMAdapter,
    LLMCreditError,
    LLMDailyQuotaError,
    LLMModelNotFoundError,
    get_llm_readiness,
)
from app.services.longform.schema import (
    ProjectManifest,
    VisualBible,
    VisualBibleChapterOverride,
    VisualBibleCharacter,
    VisualBibleLocation,
)
from app.services.longform.visual.cache import get_global_cache_dir
from app.services.longform.visual.keyword_translator import (
    _invoke_translation_llm,
    call_translation_llm_json,
    get_last_translation_llm_meta,
)

STYLE_MODE_OPTIONS = ("realistic", "illustration", "painting", "custom")
STYLE_MODE_BASE_TEXTS: Dict[str, str] = {
    "realistic": "photorealistic cinematic film still, 35mm, natural light, realistic skin and fabric textures, period-accurate costumes and architecture",
    "illustration": "detailed storybook illustration",
    "painting": "oil painting, visible brushwork",
    "custom": "",
}
REALISTIC_AVOID_TERMS = (
    "illustration",
    "painting",
    "drawing",
    "cartoon",
    "anime",
    "CGI",
    "3D render",
)
ARTISTIC_CONFLICT_AVOID_TOKENS = (
    "illustration",
    "drawing",
    "painting",
    "digital painting",
    "watercolor",
    "sketch",
)

FORBIDDEN_CHARACTER_ALIASES = frozenset({
    "you", "your", "yours", "yourself",
    "he", "him", "his", "himself",
    "she", "her", "hers", "herself",
    "they", "them", "their", "theirs", "themselves",
    "i", "me", "my", "mine", "myself",
    "we", "us", "our", "ours",
    "it", "its", "one", "someone", "anyone", "nobody",
    "protagonist", "antagonist", "hero", "heroine", "character",
    "man", "woman", "child", "children", "kid", "girl", "boy",
    "old man", "old woman", "young man", "young woman", "elder", "person", "people",
    "sister", "brother", "mother", "father", "mom", "dad", "parent",
    "grandmother", "grandfather", "witch",
})

PALETTE_TIME_LIGHT_WORDS = frozenset({
    "dawn", "sunrise", "morning", "noon", "midday", "afternoon",
    "sunset", "dusk", "twilight", "evening", "night", "midnight",
    "sun", "sunlight", "sunbeam", "daylight", "moon", "moonlight",
    "starlight", "candlelit", "candlelight", "overhead",
})


def _normalize_alias_token(alias: str) -> str:
    txt = re.sub(r"\([^)]*\)", "", str(alias or "")).strip().lower()
    txt = re.sub(r"^(?:the|a|an)\s+", "", txt).strip()
    return txt


def is_forbidden_character_alias(alias: str) -> bool:
    norm = _normalize_alias_token(alias)
    if not norm:
        return True
    return norm in FORBIDDEN_CHARACTER_ALIASES


def filter_valid_character_aliases(aliases: Any, *, existing_global: Optional[set[str]] = None) -> List[str]:
    """Keep only concrete non-pronoun/non-generic aliases and prevent cross-character duplicates (FA1.3)."""
    if isinstance(aliases, str):
        raw_list = [x.strip() for x in re.split(r"[,;\n]+", aliases) if x.strip()]
    elif isinstance(aliases, (list, tuple)):
        raw_list = [str(x).strip() for x in aliases if str(x or "").strip()]
    else:
        return []
    out: List[str] = []
    seen_local: set[str] = set()
    for a in raw_list:
        if is_forbidden_character_alias(a):
            continue
        low = a.lower().strip()
        if low in seen_local:
            continue
        if existing_global is not None and low in existing_global:
            continue
        seen_local.add(low)
        if existing_global is not None:
            existing_global.add(low)
        out.append(a)
    return out


def clean_world_slug_prefix(world_text: str, *, project_id: Optional[str] = None) -> str:
    """Strip snake_case project slug prefix (e.g. 'proj_slug — ...') from world string (FA1.3)."""
    txt = str(world_text or "").strip()
    if not txt:
        return ""
    if project_id:
        pid_esc = re.escape(str(project_id).strip())
        if pid_esc:
            txt = re.sub(rf"^{pid_esc}\s*(?:—|--|-|:)\s*", "", txt, flags=re.IGNORECASE).strip()
    txt = re.sub(r"^[a-z0-9]+(?:_[a-z0-9]+)+\s*(?:—|--|-|:)\s*", "", txt).strip()
    return txt


def sanitize_palette_lighting(raw_palette: str) -> str:
    """Remove time-of-day and specific light-source tokens from palette_lighting (FA1.3)."""
    txt = str(raw_palette or "").strip()
    if not txt:
        return "cohesive atmospheric color tones and moody emotional tone"
    out = txt
    # Remove phrases like "with moonlight and snowstorm lighting" or "with soft dawn light"
    time_alt = "|".join(sorted(PALETTE_TIME_LIGHT_WORDS, key=len, reverse=True))
    out = re.sub(
        rf"\b(?:soft|warm|cold|bright|dim|pale|harsh|golden|dramatic)?\s*(?:{time_alt})\s*(?:light|lighting|glow|rays)?\b",
        " ",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(rf"\b(?:{time_alt})\b", " ", out, flags=re.IGNORECASE)
    out = re.sub(r"\bwith\s+and\b|\bwith\s*(?:,|$)", " ", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r"\s*,\s*,+", ", ", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;-")
    if len(out) < 8 or not re.search(r"[a-z]{3,}", out, re.I):
        return "cohesive muted color palette and tense atmospheric mood"
    return out


def sanitize_visual_description_time_words(raw_desc: str) -> str:
    """Strip time-of-day/lighting phrases (e.g. 'riding at dawn', 'at twilight') from character/location descriptions (FA2.5)."""
    txt = str(raw_desc or "").strip()
    if not txt:
        return ""
    time_alt = "|".join(sorted(PALETTE_TIME_LIGHT_WORDS, key=len, reverse=True))
    out = re.sub(
        rf"\b(?:at|in|during|by|under|before|after)?\s*(?:the\s+)?(?:soft|warm|cold|bright|dim|pale|harsh|golden|dramatic)?\s*(?:{time_alt})\s*(?:light|lighting|glow|rays|sky|hours?)?\b",
        " ",
        txt,
        flags=re.IGNORECASE,
    )
    out = re.sub(rf"(?<![a-z0-9])(?:{time_alt})(?![a-z0-9])", " ", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r",\s*,+", ", ", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;-")
    return out


_CARRIED_PROP_NOUNS = (
    r"(?:skull\s+lantern|glowing\s+lantern|oil\s+lamp|brass\s+lamp|lantern|"
    r"wooden\s+doll|porcelain\s+doll|rag\s+doll|doll|"
    r"iron\s+key|rusty\s+key|golden\s+key|key|"
    r"wicker\s+basket|heavy\s+basket|basket|"
    r"candle|taper|torch|dagger|knife|sword|satchel|pouch|scroll|letter|map|goblet|chalice|bucket|pail)"
)
_LOCATION_HELD_PROP_RE = re.compile(
    rf",?\s*\b(?:while\s+)?(?:holds?|holding|carries|carrying|clutching|clutches|grasping|with)\s+"
    rf"(?:a|an|the|her|his|their)?\s*(?:[a-z]+\s+){{0,2}}{_CARRIED_PROP_NOUNS}\b[^,.;]*",
    re.IGNORECASE,
)
_LOCATION_LOOSE_PROP_RE = re.compile(
    rf",?\s*\b(?:a|an|the|small|tiny|glowing|carved|wooden|iron|old|brass)?\s*{_CARRIED_PROP_NOUNS}\s+"
    rf"(?:lies|lying|resting|rests|placed|sitting|sits|left|dropped|hanging|hangs|gleaming)\s+"
    rf"(?:on|upon|by|near|beside|inside|at)\s+[^,.;]*",
    re.IGNORECASE,
)
_LOCATION_SIMPLE_PROP_PHRASE_RE = re.compile(
    rf",?\s*(?:with\s+)?(?:a|an|the)?\s*(?:small\s+|glowing\s+|carved\s+)?(?:skull\s+lantern|wooden\s+doll|iron\s+key)\b[^,.;]*",
    re.IGNORECASE,
)


def strip_carried_objects_from_location_desc(raw_desc: str) -> str:
    """Remove transient handheld/carried props from static location descriptions so shot continuity owns props (G5)."""
    txt = str(raw_desc or "").strip()
    if not txt:
        return ""
    out = _LOCATION_HELD_PROP_RE.sub("", txt)
    out = _LOCATION_LOOSE_PROP_RE.sub("", out)
    out = _LOCATION_SIMPLE_PROP_PHRASE_RE.sub("", out)
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r",\s*,+", ", ", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;-.")
    if len(out) < 6 or not re.search(r"[a-z]{3,}", out, re.I):
        return txt
    return out


_AGE_WORDS_MAP: Dict[str, int] = {
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "empat": 4, "lima": 5, "enam": 6, "tujuh": 7, "delapan": 8, "sembilan": 9, "sepuluh": 10,
}
_AGE_TOKEN_PATTERN = "|".join(sorted(_AGE_WORDS_MAP.keys(), key=len, reverse=True) + [r"\d{1,2}"])
_FLASHBACK_CUE_PATTERNS = (
    re.compile(
        rf"\bwhen\s+(?P<subj>you|she|he|they|[A-Z][a-z]+)\s+(?:was|were)\s+(?P<age>{_AGE_TOKEN_PATTERN})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bat\s+(?:the\s+)?age\s+(?:of\s+)?(?P<age>{_AGE_TOKEN_PATTERN})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?P<age>{_AGE_TOKEN_PATTERN})[\-\s]+years?[\-\s]+old\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:saat|ketika)\s+(?:(?P<subj>kamu|ia|dia|[A-Z][a-z]+)\s+)?(?:masih\s+)?(?:kecil|anak-anak|berusia\s+(?P<age>{_AGE_TOKEN_PATTERN}))\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bberusia\s+(?P<age>{_AGE_TOKEN_PATTERN})\s+tahun\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:as\s+a\s+(?:young\s+)?(?:child|little\s+girl|little\s+boy)|in\s+(?:her|his|your)\s+(?:childhood|youth)|years?\s+earlier)\b",
        re.IGNORECASE,
    ),
)


def _parse_age_token(raw_tok: Optional[str]) -> Optional[int]:
    if not raw_tok:
        return None
    low = str(raw_tok).strip().lower()
    if low.isdigit():
        val = int(low)
        return val if 2 <= val <= 16 else None
    return _AGE_WORDS_MAP.get(low)


def detect_script_flashback_cues(script_text: str) -> List[Dict[str, Any]]:
    """Detect childhood/younger-age flashback cues across the narration script (G5)."""
    text = str(script_text or "").strip()
    if not text:
        return []
    clauses = [c.strip() for c in re.split(r"(?<=[.!?\n])\s+", text) if c.strip()]
    cues: List[Dict[str, Any]] = []
    for clause in clauses:
        for pat in _FLASHBACK_CUE_PATTERNS:
            m = pat.search(clause)
            if not m:
                continue
            gd = m.groupdict()
            age_num = _parse_age_token(gd.get("age"))
            # Ignore adult ages >= 17 even if matched by "X years old"
            if gd.get("age") is not None and age_num is None:
                continue
            cues.append({
                "age": age_num,
                "subject": str(gd.get("subj") or "").strip(),
                "snippet": clause[:220],
            })
            break
    return cues


def _derive_flashback_description(base_desc: str, age: Optional[int]) -> str:
    clean = sanitize_visual_description_time_words(base_desc) or str(base_desc or "").strip()
    low = clean.lower()
    is_male = bool(re.search(r"\b(?:man|boy|male|gentleman|husband|father|brother)\b", low)) and not bool(
        re.search(r"\b(?:woman|girl|female|daughter|sister|mother)\b", low)
    )
    noun = "boy" if is_male else "girl"
    age_phrase = f"{age}-year-old {noun}" if age else f"young {noun}"
    if not clean:
        return f"{age_phrase} in simple traditional clothing"
    out = re.sub(
        r"\b(?:young\s+)?(?:woman|man|adult|maiden|youth|girl|boy)\b",
        age_phrase,
        clean,
        count=1,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r",?\s*\b(?:late|early|mid)?[\s\-]*(?:\d{2}s|\d{2}[\-\s]+years?[\-\s]+old|teens?|twenties|thirties|forties|fifties)\b",
        "",
        out,
        flags=re.IGNORECASE,
    )
    if str(age or "") not in out and age_phrase not in out.lower():
        out = f"{age_phrase}, {out}"
    return re.sub(r"\s{2,}", " ", re.sub(r",\s*,+", ", ", out)).strip(" ,")


def ensure_flashback_character_variants(
    bible: VisualBible,
    script_text: str,
) -> Tuple[VisualBible, bool]:
    """Ensure every younger-age flashback cue in `script_text` has a matching `variant_of` entry (G5)."""
    cues = detect_script_flashback_cues(script_text)
    if not cues or not (bible.characters or []):
        return bible, False

    base_chars = [c for c in bible.characters if not getattr(c, "variant_of", None)]
    if not base_chars:
        return bible, False

    existing_ids = {c.id for c in bible.characters}
    added_variants: Dict[str, List[VisualBibleCharacter]] = {}
    changed = False

    for cue in cues:
        age = cue.get("age")
        subj = str(cue.get("subject") or "").strip().lower()
        snip_low = str(cue.get("snippet") or "").lower()

        target_base: Optional[VisualBibleCharacter] = None
        # 1. Explicit proper name match in snippet/subject
        for bc in base_chars:
            names = [bc.name] + list(bc.aliases or [])
            if any(re.search(rf"(?<![a-z0-9]){re.escape(nm.lower())}(?![a-z0-9])", snip_low) for nm in names if nm):
                target_base = bc
                break
        # 2. Second-person POV fallback
        if target_base is None and (subj in ("you", "kamu") or re.search(r"\b(?:you|your|kamu)\b", snip_low)):
            pov_id = str(getattr(bible, "pov_character_id", None) or "").strip()
            if pov_id:
                target_base = next((bc for bc in base_chars if bc.id == pov_id), None)
        # 3. Protagonist fallback
        if target_base is None:
            target_base = next(
                (bc for bc in base_chars if "protagonist" in str(bc.role or "").lower()),
                base_chars[0],
            )

        existing_variants = [
            c for c in list(bible.characters) + added_variants.get(target_base.id, [])
            if getattr(c, "variant_of", None) == target_base.id
        ]
        if age is not None:
            already = any(
                re.search(rf"(?<![a-z0-9])(?:{age}|child{age})(?![a-z0-9])", f"{v.id} {v.name} {v.description}".lower())
                for v in existing_variants
            )
        else:
            already = bool(existing_variants)
        if already:
            continue

        suffix = f"_child{age}" if age is not None else "_child"
        vid = f"{target_base.id}{suffix}"
        n = 2
        while vid in existing_ids:
            vid = f"{target_base.id}{suffix}_{n}"
            n += 1
        existing_ids.add(vid)

        variant = VisualBibleCharacter(
            id=vid,
            name=f"Young {target_base.name}",
            aliases=[],
            role="flashback_younger_self",
            variant_of=target_base.id,
            description=_derive_flashback_description(target_base.description, age),
        )
        added_variants.setdefault(target_base.id, []).append(variant)
        changed = True

    if not changed:
        return bible, False

    new_chars: List[VisualBibleCharacter] = []
    for ch in bible.characters:
        new_chars.append(ch)
        for v in added_variants.get(ch.id, []):
            new_chars.append(v)

    updated = VisualBible(
        world=bible.world,
        style_mode=bible.style_mode,
        style=bible.style,
        palette_lighting=bible.palette_lighting,
        pov_character_id=bible.pov_character_id,
        characters=new_chars,
        locations=list(bible.locations or []),
        chapter_overrides=list(bible.chapter_overrides or []),
        avoid=bible.avoid,
        source=bible.source,
    )
    return updated, True


_ERA_REGEX = re.compile(
    r"(?:"
    r"\b\d{1,2}(?:st|nd|rd|th)\s+century\b"
    r"|\babad\s+(?:ke-?)?\d+"
    r"|\b(?:1[0-9]{3}|20[0-2][0-9]|9\d{2}|8\d{2})s?\b"
    r"|\b(?:era|period|zaman|masa|ancient|medieval|victorian|edwardian|georgian|renaissance|baroque|"
    r"colonial|imperial|feudal|prehistoric|industrial revolution|cold war|world war|modern\s+day|"
    r"contemporary|late\s+\d{2}th|early\s+\d{2}th|mid-?\d{4}s?|old\s+russia|tsarist|soviet)\b"
    r")",
    re.IGNORECASE,
)


def world_mentions_era(world_text: str, *, chapter_overrides: Optional[Sequence[Any]] = None) -> bool:
    """Return True when world (or any chapter override era) explicitly indicates a time period/era (FA2.5)."""
    w = str(world_text or "").strip()
    if w and _ERA_REGEX.search(w):
        return True
    for ov in chapter_overrides or []:
        ov_era = str(getattr(ov, "era", "") or "").strip()
        ov_world = str(getattr(ov, "world", "") or "").strip()
        if (ov_era and _ERA_REGEX.search(ov_era)) or (ov_world and _ERA_REGEX.search(ov_world)):
            return True
    return False


def auto_clean_visual_bible(
    bible: VisualBible,
    manifest: Optional[ProjectManifest] = None,
) -> Tuple[VisualBible, List[str]]:
    """Automatically sanitize legacy Visual Bible issues (forbidden/dup aliases, time/light words, slug, POV) without blocking save (FA2.5)."""
    notes: List[str] = []
    project_id = getattr(manifest, "project_id", None) if manifest is not None else None

    eff_mode = normalize_style_mode(getattr(bible, "style_mode", "realistic"))
    orig_world = str(bible.world or "").strip()
    clean_world = clean_world_slug_prefix(orig_world, project_id=project_id)
    if clean_world != orig_world:
        notes.append("prefix slug pada Setting dihapus")

    orig_pal = str(bible.palette_lighting or "").strip()
    clean_pal = sanitize_palette_lighting(orig_pal) if orig_pal else orig_pal
    if orig_pal and clean_pal != orig_pal:
        notes.append("kata waktu/cahaya pada Palette dihapus")

    orig_avoid = str(bible.avoid or "").strip()
    clean_avoid = reconcile_avoid_with_style(orig_avoid, eff_mode)
    if clean_avoid != orig_avoid:
        notes.append("istilah Avoid diselaraskan dengan mode gaya")

    cleaned_chars: List[VisualBibleCharacter] = []
    used_aliases_global: set[str] = set()
    removed_aliases_note: List[str] = []
    cleaned_desc_count = 0

    for c in bible.characters or []:
        kept_aliases: List[str] = []
        for raw_a in c.aliases or []:
            tok = str(raw_a or "").strip()
            if not tok:
                continue
            low = tok.lower()
            if is_forbidden_character_alias(tok):
                removed_aliases_note.append(f"{c.id}:{tok}")
                continue
            if low in used_aliases_global:
                removed_aliases_note.append(f"{c.id}:{tok} (ganda)")
                continue
            used_aliases_global.add(low)
            kept_aliases.append(tok)

        orig_cd = str(c.description or "").strip()
        clean_cd = sanitize_visual_description_time_words(orig_cd) or orig_cd
        if clean_cd != orig_cd:
            cleaned_desc_count += 1

        cleaned_chars.append(
            VisualBibleCharacter(
                id=c.id,
                name=c.name,
                aliases=kept_aliases,
                role=c.role,
                variant_of=getattr(c, "variant_of", None),
                description=clean_cd,
            )
        )

    if removed_aliases_note:
        notes.append(f"alias terlarang/ganda dihapus ({', '.join(removed_aliases_note[:5])})")

    cleaned_locs: List[VisualBibleLocation] = []
    stripped_prop_count = 0
    for loc in bible.locations or []:
        orig_ld = str(loc.description or "").strip()
        clean_ld = sanitize_visual_description_time_words(orig_ld) or orig_ld
        no_prop_ld = strip_carried_objects_from_location_desc(clean_ld)
        if no_prop_ld != clean_ld:
            stripped_prop_count += 1
            clean_ld = no_prop_ld
        if clean_ld != orig_ld:
            cleaned_desc_count += 1
        cleaned_locs.append(
            VisualBibleLocation(id=loc.id, name=loc.name, description=clean_ld)
        )

    if cleaned_desc_count > 0:
        notes.append(f"kata waktu/cahaya dihapus dari {cleaned_desc_count} deskripsi tokoh/lokasi")
    if stripped_prop_count > 0:
        notes.append(f"benda bawaan dihapus dari {stripped_prop_count} deskripsi lokasi")

    pov_id = str(getattr(bible, "pov_character_id", None) or "").strip() or None
    if not pov_id and cleaned_chars and manifest is not None:
        script_sample = collect_project_script_text(manifest, max_chars=8000)
        if re.search(r"(?<![a-z0-9])(?:you|your|yourself)(?![a-z0-9])", script_sample, re.IGNORECASE):
            prot = next(
                (
                    c for c in cleaned_chars
                    if not getattr(c, "variant_of", None)
                    and "protagonist" in str(c.role or "").lower()
                ),
                None,
            )
            if prot is not None:
                pov_id = prot.id
                notes.append(f"pov_character_id diisi otomatis ke '{pov_id}'")

    cleaned_vb = VisualBible(
        world=clean_world,
        style_mode=eff_mode,
        style=bible.style,
        palette_lighting=clean_pal,
        pov_character_id=pov_id,
        characters=cleaned_chars,
        locations=cleaned_locs,
        chapter_overrides=list(bible.chapter_overrides or []),
        avoid=clean_avoid,
        source=bible.source,
    )
    if manifest is not None and cleaned_vb.characters:
        fb_script = collect_project_script_text(manifest, max_chars=24000)
        cleaned_vb, added_fb = ensure_flashback_character_variants(cleaned_vb, fb_script)
        if added_fb:
            notes.append("varian tokoh kilas balik (variant_of) dilengkapi otomatis")
    return cleaned_vb, notes


def validate_visual_bible(bible: VisualBible) -> List[str]:
    """Validate VisualBible quality rules (FA1.3): no pronoun/generic/duplicate aliases, no time words in palette, clean world."""
    errors: List[str] = []
    if re.match(r"^[a-z0-9]+(?:_[a-z0-9]+)+\s*(?:—|--|-|:)", str(bible.world or "").strip()):
        errors.append("world masih diawali slug proyek")

    pal_low = str(bible.palette_lighting or "").lower()
    bad_time = [w for w in sorted(PALETTE_TIME_LIGHT_WORDS) if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", pal_low)]
    if bad_time:
        errors.append(f"palette_lighting memuat kata waktu/cahaya ({', '.join(bad_time[:3])})")

    alias_owners: Dict[str, List[str]] = {}
    for ch in bible.characters or []:
        bad_aliases = [a for a in (ch.aliases or []) if is_forbidden_character_alias(a)]
        if bad_aliases:
            errors.append(f"tokoh '{ch.id}' memakai alias kata ganti/umum ({', '.join(bad_aliases[:3])})")
        for a in ch.aliases or []:
            low = str(a).strip().lower()
            if not low:
                continue
            alias_owners.setdefault(low, [])
            if ch.id not in alias_owners[low]:
                alias_owners[low].append(ch.id)

    dups = {a: owners for a, owners in alias_owners.items() if len(owners) > 1}
    if dups:
        dup_desc = ", ".join(f"'{a}' ({'/'.join(owners)})" for a, owners in list(dups.items())[:3])
        errors.append(f"alias ganda dipakai lebih dari satu tokoh: {dup_desc}")

    return errors


def normalize_style_mode(value: Any) -> str:
    low = str(value or "realistic").strip().lower()
    mapping = {
        "realistic": "realistic",
        "realistis": "realistic",
        "realistis sinematik": "realistic",
        "cinematic realistic": "realistic",
        "illustration": "illustration",
        "ilustrasi": "illustration",
        "painting": "painting",
        "lukisan": "painting",
        "custom": "custom",
        "kustom": "custom",
    }
    return mapping.get(low, "custom" if low else "realistic")


def reconcile_avoid_with_style(avoid_text: str, style_mode: str) -> str:
    """Keep Avoid terms strictly consistent with chosen style_mode (FA1.2)."""
    mode = normalize_style_mode(style_mode)
    raw_items = [x.strip() for x in re.split(r"[,;\n]+", str(avoid_text or "")) if x.strip()]

    if mode in ("illustration", "painting"):
        filtered: List[str] = []
        for item in raw_items:
            low = item.lower()
            if any(tok in low for tok in ARTISTIC_CONFLICT_AVOID_TOKENS):
                continue
            filtered.append(item)
        raw_items = filtered
    elif mode == "realistic":
        existing_low = {x.lower() for x in raw_items}
        for req_term in REALISTIC_AVOID_TERMS:
            if req_term.lower() not in existing_low:
                raw_items.append(req_term)
                existing_low.add(req_term.lower())

    dedup: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        low = item.lower()
        if low not in seen:
            seen.add(low)
            dedup.append(item)
    return ", ".join(dedup)


def _visual_bible_cache_file(cache_dir: Optional[Path] = None) -> Path:
    root = Path(cache_dir) if cache_dir else get_global_cache_dir()
    path = root / "visual_bible_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_cache(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to persist visual bible cache: {exc}")


def collect_project_script_text(manifest: ProjectManifest, *, max_chars: int = 24000) -> str:
    """Collect full narration script per chapter cleanly from manifest."""
    sections: List[str] = []
    for ch in manifest.chapters or []:
        lines: List[str] = []
        for grp in ch.tts_groups or []:
            segs = grp.segments or []
            if segs:
                seg_text = " ".join(s.narration_text.strip() for s in segs if s.narration_text and s.narration_text.strip())
                if seg_text:
                    lines.append(seg_text)
            elif grp.paragraph_text and grp.paragraph_text.strip():
                lines.append(grp.paragraph_text.strip())
        if not lines:
            for sh in ch.shots or []:
                vp = str(sh.visual_prompt or sh.search_query or "").strip()
                if vp:
                    lines.append(vp)
        if lines:
            header = f"[Chapter {ch.chapter_index}: {ch.chapter_title}]"
            sections.append(header + "\n" + "\n".join(lines))
    joined = "\n\n".join(sections).strip()
    if len(joined) <= max_chars:
        return joined
    head_len = int(max_chars * 0.7)
    tail_len = max_chars - head_len - 40
    return joined[:head_len] + "\n...[middle excerpt trimmed]...\n" + joined[-tail_len:]


def _preset_style_from_manifest(manifest: ProjectManifest) -> str:
    snap = manifest.preset_snapshot or {}
    if isinstance(snap, dict):
        vis = snap.get("visual")
        if isinstance(vis, dict) and str(vis.get("style") or "").strip():
            return str(vis.get("style")).strip()
        if str(snap.get("style") or "").strip():
            return str(snap.get("style")).strip()
    return ""


def build_template_visual_bible(
    manifest: ProjectManifest,
    *,
    style_mode: Optional[str] = None,
) -> VisualBible:
    """Build an editable template VisualBible when LLM is unavailable (F1, FA1.2)."""
    topic = str(manifest.topic or manifest.title or "").strip()
    anchors = [str(a).strip() for a in (manifest.visual_anchor_terms or []) if str(a).strip()]
    avoids = [str(a).strip() for a in (manifest.visual_avoid_terms or []) if str(a).strip()]

    world_parts: List[str] = []
    clean_topic = clean_world_slug_prefix(topic, project_id=manifest.project_id)
    if clean_topic and clean_topic.lower() != str(manifest.project_id or "").lower():
        world_parts.append(clean_topic)
    if manifest.visual_summary:
        world_parts.append(str(manifest.visual_summary).strip())
    elif anchors:
        world_parts.append(", ".join(anchors[:6]))
    world = clean_world_slug_prefix(" — ".join(world_parts), project_id=manifest.project_id) or "Historical or documentary setting consistent with project narration"

    preset_style = _preset_style_from_manifest(manifest)
    if style_mode is not None:
        eff_mode = normalize_style_mode(style_mode)
    elif preset_style:
        low_ps = preset_style.lower()
        if "paint" in low_ps:
            eff_mode = "painting"
        elif "illustrat" in low_ps:
            eff_mode = "illustration"
        elif "photo" in low_ps or "realistic" in low_ps or "35mm" in low_ps:
            eff_mode = "realistic"
        else:
            eff_mode = "custom"
    else:
        eff_mode = "realistic"

    if style_mode is not None:
        style = STYLE_MODE_BASE_TEXTS.get(eff_mode) or preset_style or STYLE_MODE_BASE_TEXTS["realistic"]
    else:
        style = preset_style or STYLE_MODE_BASE_TEXTS.get(eff_mode) or STYLE_MODE_BASE_TEXTS["realistic"]

    palette_lighting = "cohesive atmospheric color tones and emotional mood"

    avoid_pool: List[str] = list(avoids)
    for item in ("text", "watermark", "logo", "UI overlay", "collage"):
        if item.lower() not in {x.lower() for x in avoid_pool}:
            avoid_pool.append(item)
    avoid_str = reconcile_avoid_with_style(", ".join(avoid_pool), eff_mode)

    return VisualBible(
        world=world,
        style_mode=eff_mode,
        style=style,
        palette_lighting=palette_lighting,
        characters=[],
        locations=[],
        chapter_overrides=[],
        avoid=avoid_str,
        source="template",
    )


def _slugify_id(raw: str, prefix: str = "item", index: int = 1) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")
    return cleaned or f"{prefix}_{index}"


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    """Extract and recover a JSON dict from LLM output (F1 recovery)."""
    text = str(raw_text or "").strip()
    if not text:
        return {}
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()

    candidates: List[str] = [text]
    m_brace = re.search(r"\{[\s\S]*\}", text)
    if m_brace:
        candidates.append(m_brace.group(0))

    for cand in candidates:
        # Try direct load and trailing-comma cleanup
        for attempt_txt in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):
            try:
                parsed = json.loads(attempt_txt)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue

    # Partial salvage when output was cut off mid-object
    if "{" in text:
        tail = text[text.find("{"):]
        for closing in ("}", "]}", '"}', '"]}'):
            try:
                fixed = re.sub(r",\s*$", "", tail.rstrip()) + closing
                fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
                parsed = json.loads(fixed)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    return {}


def _coerce_style_preserving_mode(raw_style: str, style_mode: str, fallback_style: str) -> str:
    """Keep AI style elaboration compatible with chosen style_mode without medium drift (FA1.2)."""
    mode = normalize_style_mode(style_mode)
    base = STYLE_MODE_BASE_TEXTS.get(mode, "")
    candidate = str(raw_style or "").strip()
    if mode == "custom":
        return candidate or fallback_style
    if not candidate:
        return base or fallback_style
    low = candidate.lower()
    if mode == "realistic":
        if any(tok in low for tok in ("illustration", "painting", "watercolor", "cartoon", "anime", "drawing")):
            return base
        if "photorealistic" not in low and "35mm" not in low and "film still" not in low:
            return f"{base}, {candidate}"
        return candidate
    if mode == "illustration":
        if any(tok in low for tok in ("photorealistic", "film still", "35mm")):
            return base
        if "illustrat" not in low:
            return f"{base}, {candidate}"
        return candidate
    if mode == "painting":
        if any(tok in low for tok in ("photorealistic", "film still", "35mm")):
            return base
        if "paint" not in low:
            return f"{base}, {candidate}"
        return candidate
    return candidate


def _coerce_visual_bible(
    raw: Dict[str, Any],
    *,
    fallback: VisualBible,
    source: str = "ai",
    style_mode: Optional[str] = None,
    project_id: Optional[str] = None,
    script_text: str = "",
) -> VisualBible:
    eff_mode = normalize_style_mode(style_mode or fallback.style_mode)
    world = clean_world_slug_prefix(str(raw.get("world") or ""), project_id=project_id) or fallback.world
    style = _coerce_style_preserving_mode(str(raw.get("style") or ""), eff_mode, fallback.style)
    raw_pal = str(raw.get("palette_lighting") or "").strip()
    palette_lighting = sanitize_palette_lighting(raw_pal) if raw_pal else fallback.palette_lighting

    avoid_val = raw.get("avoid")
    if isinstance(avoid_val, list):
        avoid_raw = ", ".join(str(x).strip() for x in avoid_val if str(x or "").strip())
    else:
        avoid_raw = str(avoid_val or "").strip()
    if not avoid_raw:
        avoid_raw = fallback.avoid
    avoid = reconcile_avoid_with_style(avoid_raw, eff_mode)

    chars: List[VisualBibleCharacter] = []
    seen_cids: set[str] = set()
    used_aliases_global: set[str] = set()
    raw_chars = raw.get("characters")
    if isinstance(raw_chars, list):
        for idx, item in enumerate(raw_chars, start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            desc = str(item.get("description") or "").strip()
            if not name and not desc:
                continue
            cid = _slugify_id(str(item.get("id") or name or f"char_{idx}"), prefix="char", index=idx)
            while cid in seen_cids:
                cid = f"{cid}_{idx}"
            seen_cids.add(cid)
            aliases = filter_valid_character_aliases(item.get("aliases"), existing_global=used_aliases_global)
            variant_of = str(item.get("variant_of") or "").strip() or None
            chars.append(
                VisualBibleCharacter(
                    id=cid,
                    name=name or cid,
                    aliases=aliases,
                    role=str(item.get("role") or "").strip(),
                    variant_of=variant_of,
                    description=sanitize_visual_description_time_words(desc) or desc,
                )
            )

    pov_char_id = str(raw.get("pov_character_id") or "").strip() or None
    if pov_char_id and pov_char_id not in seen_cids:
        matched_pov = next((c.id for c in chars if c.name.lower() == pov_char_id.lower()), None)
        pov_char_id = matched_pov

    locs: List[VisualBibleLocation] = []
    seen_lids: set[str] = set()
    raw_locs = raw.get("locations")
    if isinstance(raw_locs, list):
        for idx, item in enumerate(raw_locs, start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            desc = str(item.get("description") or "").strip()
            if not name and not desc:
                continue
            lid = _slugify_id(str(item.get("id") or name or f"loc_{idx}"), prefix="loc", index=idx)
            while lid in seen_lids:
                lid = f"{lid}_{idx}"
            seen_lids.add(lid)
            clean_ld = sanitize_visual_description_time_words(desc) or desc
            clean_ld = strip_carried_objects_from_location_desc(clean_ld) or clean_ld
            locs.append(
                VisualBibleLocation(
                    id=lid,
                    name=name or lid,
                    description=clean_ld,
                )
            )

    overrides: List[VisualBibleChapterOverride] = []
    raw_ov = raw.get("chapter_overrides")
    if isinstance(raw_ov, list):
        for item in raw_ov:
            if not isinstance(item, dict):
                continue
            try:
                c_idx = int(item.get("chapter_index"))
            except (TypeError, ValueError):
                continue
            w = clean_world_slug_prefix(str(item.get("world") or ""), project_id=project_id)
            e = str(item.get("era") or "").strip()
            if w or e:
                overrides.append(VisualBibleChapterOverride(chapter_index=c_idx, world=w, era=e))

    vb = VisualBible(
        world=world,
        style_mode=eff_mode,
        style=style,
        palette_lighting=palette_lighting,
        pov_character_id=pov_char_id,
        characters=chars,
        locations=locs,
        chapter_overrides=overrides,
        avoid=avoid,
        source=source,
    )
    if script_text:
        vb, _ = ensure_flashback_character_variants(vb, script_text)
    return vb


def _build_visual_bible_llm_prompt(
    manifest: ProjectManifest,
    script_text: str,
    *,
    style_mode: str = "realistic",
) -> str:
    snap = manifest.preset_snapshot or {}
    preset_name = str(snap.get("name") or manifest.preset_id or "").strip() if isinstance(snap, dict) else ""
    eff_mode = normalize_style_mode(style_mode)
    base_style = STYLE_MODE_BASE_TEXTS.get(eff_mode) or _preset_style_from_manifest(manifest)
    anchors = ", ".join(str(x) for x in (manifest.visual_anchor_terms or []))
    avoids = ", ".join(str(x) for x in (manifest.visual_avoid_terms or []))
    ch_titles = "; ".join(f"Ch{ch.chapter_index}: {ch.chapter_title}" for ch in (manifest.chapters or []) if ch.chapter_title)

    return (
        "You are a visual continuity director preparing a reusable English Visual Bible JSON for an image generator.\n"
        "The output MUST work accurately for ANY genre (folklore, history, modern drama, documentary without recurring characters, science, engineering).\n\n"
        f"Project title: {manifest.title}\n"
        f"Topic: {manifest.topic or manifest.title}\n"
        f"Preset/Niche: {preset_name} ({manifest.preset_id})\n"
        f"Chosen style_mode: {eff_mode}\n"
        + (f"Base style text (preserve this medium, only enrich compatible details): {base_style}\n" if base_style else "")
        + (f"Visual summary: {manifest.visual_summary}\n" if manifest.visual_summary else "")
        + (f"Visual anchors: {anchors}\n" if anchors else "")
        + (f"Existing avoid terms: {avoids}\n" if avoids else "")
        + (f"Chapters: {ch_titles}\n" if ch_titles else "")
        + "\nFull narration script:\n"
        + (script_text or "(no narration provided)")
        + "\n\nRules (strict):\n"
        "1. NEVER invent characters that do not appear in the script.\n"
        "2. Derive each character's gender, age, hair, outfit (with specific colors), and one distinguishing detail from the script or well-known source tale (do NOT include time-of-day/lighting words like 'dawn', 'twilight', or 'candlelit' inside character or location descriptions).\n"
        "   - Primary entry age MUST reflect the current main timeline, NOT a flashback.\n"
        "   - FLASHBACK & AGE SHIFTS (MANDATORY): Scan the entire script for any younger/older age or flashback cue involving a recurring character (e.g. `when you were eight`, `at age 8`, `as a child`, `years earlier`, `in her youth`, `eight years old`). Whenever found, you MUST create a separate flashback entry with `\"variant_of\": \"<base_character_id>\"` (e.g. `id: \"<base_id>_child8\"`, `name: \"Young <Name>\"`, `role: \"flashback_younger_self\"`, `description: \"8-year-old girl/boy, ...\"` preserving consistent hair/eye color and recurring physical markers from the adult entry).\n"
        "   - `aliases` MUST contain ONLY proper names, nicknames, or specific titles. NEVER put pronouns (`you`, `your`, `he`, `him`, `his`, `she`, `her`, `they`, `them`, `i`, `me`) or generic words (`sister`, `brother`, `man`, `woman`, `child`, `girl`, `boy`, `old woman`, `witch`, `protagonist`) into `aliases`, and NEVER assign the same alias to more than one character.\n"
        "   - If narration uses second-person POV (`you`, `your`) for an identifiable main character, set top-level `\"pov_character_id\"` to that character's `id` (otherwise `\"\"`).\n"
        "3. If the script is a documentary/explainer without recurring people (e.g. machines, vehicles, science, geopolitics), set `\"characters\": []` and maintain visual consistency through `world`, `style`, `palette_lighting`, `locations`, and `chapter_overrides` (`chapter_index`, `world`, `era`) when chapters move across eras or regions.\n"
        f"4. STRICTLY respect the user's chosen `style_mode` ({eff_mode}). Do NOT switch medium (e.g. do NOT change photorealistic film still into illustration/painting, and do NOT change illustration/painting into photography). Only refine compatible visual details.\n"
        "5. `world` MUST state a concrete geographic/cultural place AND historical era/time period, NEVER a raw project ID/slug.\n"
        "6. `palette_lighting` MUST describe ONLY colors and emotional mood (e.g. `muted earth tones and cold slate greys, tense somber atmosphere`). NEVER include time-of-day or directional light words (`dawn`, `noon`, `night`, `dusk`, `sun`, `sunlight`, `moonlight`, `candlelit`) — lighting & time come strictly from Continuity.\n"
        "7. `locations`: whenever a setting has both inside and outside scenes, split it into TWO separate entries (`<place>_interior` and `<place>_exterior`) with concrete architectural details.\n"
        "8. `avoid` MUST be consistent with `style` (never put artistic terms inside `avoid` that contradict `style`). Include anachronisms/distractors like `modern buildings, cars, electric lights, plastic, modern clothing, text, watermark` when historically appropriate.\n"
        "\nReturn ONLY valid JSON matching this schema:\n"
        "{\n"
        '  "world": "place and historical era/setting in English (never a project slug)",\n'
        '  "style": "cohesive visual art/camera style for this project",\n'
        '  "palette_lighting": "color tones and emotional atmosphere ONLY (no dawn/noon/night/sun/moonlight)",\n'
        '  "pov_character_id": "character id addressed as second-person you, or empty string",\n'
        '  "characters": [\n'
        '    {"id": "snake_case_id", "name": "Name", "aliases": ["Proper Name", "Nickname"], "role": "protagonist", "variant_of": "", "description": "gender, age, hair, colored outfit, one distinguishing trait"}\n'
        "  ],\n"
        '  "locations": [\n'
        '    {"id": "place_exterior", "name": "Location Exterior", "description": "fixed outdoor architectural/environmental details"},\n'
        '    {"id": "place_interior", "name": "Location Interior", "description": "fixed indoor architectural/furniture details"}\n'
        "  ],\n"
        '  "chapter_overrides": [\n'
        '    {"chapter_index": 1, "world": "optional replacement chapter setting", "era": "optional era"}\n'
        "  ],\n"
        '  "avoid": "comma-separated items to exclude"\n'
        "}\n"
    )


def ensure_project_visual_bible(
    manifest: ProjectManifest,
    *,
    cache_dir: Optional[Path] = None,
    llm_adapter: Optional[LLMAdapter] = None,
    force: bool = False,
    allow_llm: bool = True,
    raise_on_error: bool = False,
    style_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Load, generate (with active LLM + JSON recovery), or template-build `manifest.visual_bible` (F1, FA1.2)."""
    if not force and manifest.visual_bible is not None:
        cur_vb = manifest.visual_bible
        fb_txt = collect_project_script_text(manifest)
        cur_vb, fb_changed = ensure_flashback_character_variants(cur_vb, fb_txt)
        if fb_changed:
            manifest.visual_bible = cur_vb
        return {
            "visual_bible": cur_vb,
            "source": cur_vb.source or "manifest",
            "cached": False,
        }

    eff_mode = normalize_style_mode(
        style_mode if style_mode is not None else (manifest.visual_bible.style_mode if manifest.visual_bible else "realistic")
    )
    fallback_bible = build_template_visual_bible(
        manifest,
        style_mode=(style_mode if style_mode is not None else None),
    )
    if style_mode is None and manifest.visual_bible is None:
        eff_mode = fallback_bible.style_mode

    script_text = collect_project_script_text(manifest)
    sig_raw = "|".join(
        [
            str(manifest.title or "").strip().lower(),
            str(manifest.topic or "").strip().lower(),
            str(manifest.preset_id or "").strip().lower(),
            eff_mode,
            str(manifest.visual_summary or "").strip().lower(),
            ",".join(str(x).lower() for x in (manifest.visual_anchor_terms or [])),
            hash_string(normalize_text(script_text)),
        ]
    )
    c_key = hash_string(f"v102_fa1_vb|{sig_raw}")
    cache_path = _visual_bible_cache_file(cache_dir)
    cache = _load_cache(cache_path)

    if not force and isinstance(cache.get(c_key), dict) and cache[c_key].get("bible"):
        try:
            loaded_vb = VisualBible.model_validate(cache[c_key]["bible"])
            manifest.visual_bible = loaded_vb
            return {
                "visual_bible": loaded_vb,
                "source": str(cache[c_key].get("source") or "cache"),
                "cached": True,
            }
        except Exception:
            pass

    from app.services.longform.llm_adapter import is_primary_or_failover_ready

    readiness = get_llm_readiness()
    llm_ready = (llm_adapter is not None) or is_primary_or_failover_ready(
        readiness, get_llm_readiness
    )

    if not allow_llm or not llm_ready:
        manifest.visual_bible = fallback_bible
        return {
            "visual_bible": fallback_bible,
            "source": "template",
            "cached": False,
        }

    prompt = _build_visual_bible_llm_prompt(manifest, script_text, style_mode=eff_mode)
    last_err: Optional[Exception] = None
    try:
        raw_content, prov, model_name = call_translation_llm_json(
            _invoke_translation_llm,
            prompt,
            llm_adapter=llm_adapter,
            max_tokens=1500,
            json_validator=lambda txt: bool(_extract_json_object(txt).get("world") or _extract_json_object(txt).get("style")),
        )
        call_meta = get_last_translation_llm_meta()
        parsed = _extract_json_object(raw_content)
        if not parsed or (not parsed.get("world") and not parsed.get("style")):
            raise ValueError("LLM VisualBible JSON missing required world/style fields")
        vb = _coerce_visual_bible(
            parsed,
            fallback=fallback_bible,
            source="ai",
            style_mode=eff_mode,
            project_id=manifest.project_id,
            script_text=script_text,
        )
        manifest.visual_bible = vb
        cache[c_key] = {
            "bible": vb.model_dump(mode="json"),
            "source": "ai",
            "provider": prov,
            "model": model_name,
        }
        _save_cache(cache_path, cache)
        return {
            "visual_bible": vb,
            "source": "ai",
            "cached": False,
            "provider": prov,
            "model": model_name,
            "failover_used": bool(call_meta.get("failover_used", False)),
            "providers_tried": list(call_meta.get("providers_tried") or ([prov] if prov else [])),
        }
    except (LLMCreditError, LLMDailyQuotaError, LLMModelNotFoundError):
        raise
    except Exception as exc:
        last_err = exc
        logger.warning(f"VisualBible LLM failed: {exc}")

    if raise_on_error and last_err is not None:
        raise last_err
    logger.warning(f"VisualBible falling back to template after LLM failure: {last_err}")
    manifest.visual_bible = fallback_bible
    return {
        "visual_bible": fallback_bible,
        "source": "template_fallback",
        "cached": False,
    }
