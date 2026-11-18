"""Script Generator Workflow for Longform Video Generation (2.3).

Orchestrates multi-stage narrative generation:
1. Outline generation (candidate titles, opening hook, chapters with target words)
2. Chapter-by-chapter sequential writing with previous chapter summaries and used facts tracking
3. Word count control with single-pass length revision (revise_length.md)
4. Assembly into script/full_script.md and manifest update via update_project_script
5. Persistent state tracking in script/state.json supporting incremental resume
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.longform.hashing import compute_preset_snapshot_hash, normalize_text
from app.services.longform.language_guard import (
    check_language_compliance,
    format_language_directive,
    resolve_project_language,
)
from app.services.longform.llm_adapter import (
    LLMAdapter,
    LLMAdapterError,
    compute_script_max_tokens,
)
from app.services.longform.presets import get_preset
from app.services.longform.prompts import render_prompt
from app.services.longform.schema import (
    OutlineChapter,
    Preset,
    ProjectManifest,
    ScriptOutline,
)
from app.services.longform.script_import import strip_glued_chapter_heading
from app.services.longform.store import (
    ProjectLock,
    ProjectStoreError,
    load_manifest,
    save_manifest,
    update_project_script,
)


def extract_json_payload(raw_text: str) -> str:
    """Extract embedded JSON string from LLM output, stripping code fences if present."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    # Search for first '{' and last '}'
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1]
    return text


def _parse_json_lenient(json_str: str) -> Any:
    """Parse JSON strictly first, repairing minor LLM quote/stray-line typos cleanly."""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # Remove stray bare identifier lines sometimes emitted by small free models (e.g., `_LARGE_`)
        cleaned = re.sub(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*\n(?=\s*[\]}])", "", json_str, flags=re.MULTILINE)
        # Escape interior unescaped ASCII quotes inside string lines (`"text "quoted" end"`)
        repaired_lines = []
        for ln in cleaned.splitlines():
            m = re.match(r'^(\s*(?:"[^"]+"\s*:\s*)?")(.*)("(?:\s*,?\s*))$', ln)
            if m and '"' in m.group(2):
                inner = m.group(2).replace('\\"', '"').replace('"', '\\"')
                repaired_lines.append(f"{m.group(1)}{inner}{m.group(3)}")
            else:
                repaired_lines.append(ln)
        return json.loads("\n".join(repaired_lines))


def count_narration_words(text: str) -> int:
    """Count narration words in a markdown chapter, ignoring headers and bracket tags."""
    # Strip markdown headers
    clean = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE)
    # Strip bracket directives: [visual: ...], [verify: ...], etc.
    clean = re.sub(r"\[[^\]]+\]", "", clean)
    words = clean.split()
    return len(words)


def extract_chapter_summary_and_facts(chapter_md: str) -> Tuple[str, List[str]]:
    """Extract first 2 sentences as summary and all [verify: ...] tags as facts."""
    from app.services.longform.script_import import extract_tags, split_sentences

    clean_sentences = []
    facts = []

    # Strip chapter header
    lines = [l for l in chapter_md.splitlines() if not l.strip().startswith("#")]
    body = "\n".join(lines)

    for para in body.split("\n\n"):
        for sent in split_sentences(para):
            text, tags = extract_tags(sent)
            if text:
                clean_sentences.append(text)
            if "verify" in tags:
                facts.append(tags["verify"].strip())

    summary = " ".join(clean_sentences[:2]) if clean_sentences else "No narrative summary."
    return summary, facts


class ScriptGenerator:
    """Manages the lifecycle of LLM script generation, revision, and assembly."""

    def __init__(
        self,
        project_dir: str | Path,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        fake_delay: float = 0.0,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.script_dir = self.project_dir / "script"
        self.cache_dir = self.project_dir / "cache" / "llm"
        self.state_file = self.script_dir / "state.json"
        self.outline_file = self.script_dir / "outline.json"
        self.full_script_file = self.script_dir / "full_script.md"

        self.script_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.adapter = LLMAdapter(
            provider=provider,
            model=model,
            cache_dir=self.cache_dir,
            fake_delay=fake_delay,
        )

    def _read_state(self) -> Dict[str, Any]:
        if self.state_file.is_file():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"outline_hash": "", "chapters": {}}

    def _write_state(self, state: Dict[str, Any]) -> None:
        tmp_file = self.script_dir / f"state.json.tmp.{uuid.uuid4().hex}"
        payload = json.dumps(state, indent=2).encode("utf-8")
        with open(tmp_file, "wb") as fp:
            fp.write(payload)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_file, self.state_file)

    def _generate_chapter_summary(
        self,
        chapter_index: int,
        chapter_title: str,
        chapter_text: str,
        topic: str,
        language: str = "en-US",
        force_refresh: bool = False,
    ) -> Tuple[str, List[str]]:
        """Generate narrative summary and key facts using summary.md template."""
        lang_name, lang_tag = format_language_directive(language)
        context = {
            "topic": topic,
            "language": lang_tag,
            "language_name": lang_name,
            "chapter_index": chapter_index,
            "chapter_title": chapter_title,
            "chapter_text": chapter_text,
        }
        prompt = render_prompt("summary", context)
        resp = self.adapter.generate(
            prompt=prompt,
            fake_kind="summary",
            fake_kwargs={
                "topic": topic,
                "chapter_index": chapter_index,
                "chapter_title": chapter_title,
            },
            force_refresh=force_refresh,
            max_tokens=compute_script_max_tokens("summary"),
        )
        json_str = extract_json_payload(resp.content)
        try:
            data = json.loads(json_str)
            summary = data.get("narrative_summary", "").strip()
            facts = data.get("key_facts", [])
            if not summary:
                summary = f"Chapter {chapter_index} narrative developments."
            return summary, [str(f).strip() for f in facts if f]
        except Exception as exc:
            logger.warning(f"Failed to parse chapter summary JSON: {exc}. Falling back to default.")
            return f"Chapter {chapter_index} ({chapter_title}) established core story points.", []

    @staticmethod
    def _outline_combined_text(outline: ScriptOutline) -> str:
        """Flatten candidate titles, hook, chapter titles, and purposes for language check."""
        parts = list(outline.candidate_titles or [])
        if outline.opening_hook:
            parts.append(outline.opening_hook)
        for ch in outline.chapters:
            parts.append(ch.title)
            if ch.purpose:
                parts.append(ch.purpose)
            parts.extend(ch.key_points or [])
        return "\n".join(parts)

    @staticmethod
    def _extract_heading_and_body(chapter_md: str) -> Tuple[Optional[str], str]:
        """Split optional leading chapter heading (`## Title`, `#Bab N:`, `Chapter N:`, etc.) from spoken narration body."""
        lines = (chapter_md or "").splitlines()
        heading: Optional[str] = None
        body_lines: List[str] = []
        for idx, ln in enumerate(lines):
            stripped = ln.strip()
            if idx == 0 and stripped:
                if stripped.startswith("## "):
                    heading = stripped[3:].strip()
                    continue
                if stripped.startswith("# ") and not re.match(r"^#\s*(?:bab|chapter|ch)\b", stripped, re.IGNORECASE):
                    heading = stripped[2:].strip()
                    continue
                detected_heading, rem_body = strip_glued_chapter_heading(stripped)
                if detected_heading is not None:
                    heading = detected_heading
                    if rem_body:
                        body_lines.append(rem_body)
                    continue
            # Also strip leaked leading heading on subsequent lines just in case
            if not body_lines and stripped:
                detected_heading, rem_body = strip_glued_chapter_heading(stripped)
                if detected_heading is not None:
                    heading = heading or detected_heading
                    if rem_body:
                        body_lines.append(rem_body)
                    continue
            body_lines.append(ln)
        return heading, "\n".join(body_lines).strip()

    def _validate_outline_language(self, outline: ScriptOutline, target_lang: str) -> Dict[str, Any]:
        """Verify outline candidate titles, hook, and chapter titles match project language."""
        combined = self._outline_combined_text(outline)
        titles_text = "\n".join(
            [*(outline.candidate_titles or []), *(c.title for c in outline.chapters)]
        )
        return check_language_compliance(combined, target_lang, title_text=titles_text)

    def generate_outline(
        self,
        topic: str,
        target_duration_minutes: float = 12.0,
        sub_niche_focus: Optional[str] = None,
        force_refresh: bool = False,
    ) -> ScriptOutline:
        """Generate outline (JSON) and save to script/outline.json (2.3 Step 1, 8B.1 B2)."""
        # Fail fast if provider is not configured
        self.adapter.validate_configuration()

        manifest = load_manifest(self.project_dir)
        preset = Preset.model_validate(manifest.preset_snapshot)
        target_lang = resolve_project_language(manifest, preset)
        lang_name, lang_tag = format_language_directive(target_lang)

        focus = sub_niche_focus or manifest.sub_niche_focus or ""
        wpm = preset.words_per_minute
        total_target_words = int(target_duration_minutes * wpm)

        prompt_context = {
            "topic": topic,
            "sub_niche_focus": focus or "General authoritative exploration",
            "target_duration_minutes": target_duration_minutes,
            "words_per_minute": wpm,
            "total_target_words": total_target_words,
            "language": lang_tag,
            "language_name": lang_name,
            "audience": preset.audience,
            "narration_style": preset.narration_style,
            "hook_rules": preset.hook_rules,
            "chapter_structure": json.dumps(preset.chapter_structure),
            "topic_keywords": ", ".join(preset.topic_keywords),
            "visual_keywords": ", ".join(preset.visual_keywords),
            "sensitivity_notes": preset.sensitivity_notes or "Verify all facts.",
            "recommended_visual_sources": json.dumps(preset.recommended_visual_sources),
        }

        base_prompt = render_prompt("outline", prompt_context)
        max_lang_retries = 2
        outline: Optional[ScriptOutline] = None
        lang_warning: Optional[str] = None

        for lang_attempt in range(max_lang_retries + 1):
            prompt = base_prompt
            eff_refresh = force_refresh or (lang_attempt > 0)
            if lang_attempt > 0:
                prompt = (
                    f"LANGUAGE CORRECTION (ATTEMPT {lang_attempt}/{max_lang_retries}): "
                    f"Your previous JSON outline was written in the WRONG language! "
                    f"You MUST write EVERY candidate_title, opening_hook, chapters[*].title, purpose, and key_points "
                    f"100% in {lang_name} ({lang_tag}). Zero words in any other language.\n\n"
                    + base_prompt
                )

            resp = self.adapter.generate(
                prompt=prompt,
                fake_kind="outline",
                fake_kwargs={
                    "topic": topic,
                    "target_duration_minutes": target_duration_minutes,
                    "words_per_minute": wpm,
                    "target_words": total_target_words,
                },
                force_refresh=eff_refresh,
                max_tokens=compute_script_max_tokens("outline", total_target_words),
            )

            json_str = extract_json_payload(resp.content)
            try:
                outline_dict = _parse_json_lenient(json_str)
            except Exception as exc:
                raise LLMAdapterError(
                    f"Failed to parse outline JSON returned by provider '{resp.provider}': {exc}\nRaw text:\n{resp.content}"
                ) from exc

            outline = ScriptOutline.model_validate(outline_dict)
            lang_check = self._validate_outline_language(outline, lang_tag)
            if lang_check["ok"]:
                lang_warning = None
                break
            lang_warning = lang_check["reason"]
            logger.warning(
                f"Outline language mismatch (attempt {lang_attempt + 1}/{max_lang_retries + 1}): {lang_warning}"
            )

        assert outline is not None
        outline.provider = self.adapter.provider
        outline.model = self.adapter.model

        # Atomically save outline.json
        tmp_outline = self.script_dir / f"outline.json.tmp.{uuid.uuid4().hex}"
        with open(tmp_outline, "w", encoding="utf-8") as fp:
            fp.write(outline.model_dump_json(indent=2))
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_outline, self.outline_file)

        # Update state outline hash, language, and provider metadata
        outline_hash = hashlib.sha256(self.outline_file.read_bytes()).hexdigest()
        state = self._read_state()
        state["outline_hash"] = outline_hash
        state["outline_provider"] = self.adapter.provider
        state["outline_model"] = self.adapter.model
        state["project_language"] = lang_tag
        if lang_warning:
            state["outline_language_warning"] = lang_warning
        else:
            state.pop("outline_language_warning", None)
        self._write_state(state)

        # Update project manifest title if not already set specifically
        if outline.candidate_titles and manifest.title in ("Untitled Longform Video", topic):
            manifest.title = outline.candidate_titles[0]
            save_manifest(manifest, self.project_dir)

        return outline

    def write_chapters(
        self,
        force_refresh: bool = False,
        fail_at_chapter: Optional[int] = None,
    ) -> Path:
        """Write all chapters sequentially with previous context, resume, and length control (2.3 Steps 2-4)."""
        # Fail fast if provider is not configured
        self.adapter.validate_configuration()

        if not self.outline_file.is_file():
            raise FileNotFoundError(
                f"Outline file not found at '{self.outline_file}'. Run 'script outline' first."
            )

        manifest = load_manifest(self.project_dir)
        preset = Preset.model_validate(manifest.preset_snapshot)
        target_lang = resolve_project_language(manifest, preset)
        lang_name, lang_tag = format_language_directive(target_lang)
        preset_hash = compute_preset_snapshot_hash(manifest.preset_snapshot)

        outline_data = json.loads(self.outline_file.read_text(encoding="utf-8"))
        outline = ScriptOutline.model_validate(outline_data)

        state = self._read_state()
        current_outline_hash = hashlib.sha256(self.outline_file.read_bytes()).hexdigest()

        # If outline was edited, invalidate chapters whose outline definition changed
        outline_changed = (state.get("outline_hash") != current_outline_hash)
        if outline_changed:
            state["outline_hash"] = current_outline_hash

        chapters_state = state.setdefault("chapters", {})

        outline_summary = "\n".join(
            f"- Chapter {c.chapter_index}: {c.title} (~{c.target_words} words) - {c.purpose}"
            for c in outline.chapters
        )

        previous_summaries_list: List[str] = []
        cumulative_facts_list: List[str] = []

        for ch in outline.chapters:
            idx_str = str(ch.chapter_index)
            ch_file = self.script_dir / f"chapter_{ch.chapter_index:02d}.md"

            # Compute input hash for this chapter (preserve finished real chapters if user switches model/provider on resume)
            ch_canonical = ch.model_dump_json()
            content_hash_material = (
                f"{ch_canonical}|{lang_tag}|{preset_hash}|{manifest.sub_niche_focus or ''}|"
                f"{';'.join(previous_summaries_list)}"
            )
            expected_content_hash = hashlib.sha256(content_hash_material.encode("utf-8")).hexdigest()
            input_hash_material = f"{self.adapter.provider}:{self.adapter.model or ''}|{content_hash_material}"
            expected_input_hash = hashlib.sha256(input_hash_material.encode("utf-8")).hexdigest()

            ch_state = chapters_state.get(idx_str, {})
            prev_is_fake = (ch_state.get("provider") == "fake")
            already_done = (
                not force_refresh
                and ch_file.is_file()
                and ch_state.get("status") == "completed"
                and (
                    ch_state.get("input_hash") == expected_input_hash
                    or (not prev_is_fake and not self.adapter.is_fake and ch_state.get("content_hash") == expected_content_hash)
                )
            )

            if already_done:
                logger.info(f"Chapter {ch.chapter_index} up-to-date. Reusing cached output.")
                summary = ch_state.get("summary")
                facts = ch_state.get("key_facts", [])
                if not summary:
                    # Generate summary if missing from legacy state
                    ch_content = ch_file.read_text(encoding="utf-8")
                    summary, facts = self._generate_chapter_summary(
                        chapter_index=ch.chapter_index,
                        chapter_title=ch.title,
                        chapter_text=ch_content,
                        topic=outline.topic,
                    )
                    ch_state["summary"] = summary
                    ch_state["key_facts"] = facts
                    self._write_state(state)
                previous_summaries_list.append(f"Chapter {ch.chapter_index} ({ch.title}): {summary}")
                cumulative_facts_list.extend(facts)
                continue

            # Check simulated failure injection for resume testing
            if fail_at_chapter is not None and ch.chapter_index == fail_at_chapter:
                raise RuntimeError(f"Simulated failure at chapter {ch.chapter_index}")

            logger.info(f"Writing Chapter {ch.chapter_index}: {ch.title} (target: ~{ch.target_words} words)...")

            tmp_ch = self.script_dir / f"chapter_{ch.chapter_index:02d}.md.tmp.{uuid.uuid4().hex}"
            try:
                # Prepare chapter prompt context with explicit start & end narration language
                context = {
                    "topic": outline.topic,
                    "sub_niche_focus": manifest.sub_niche_focus or "",
                    "language": lang_tag,
                    "language_name": lang_name,
                    "audience": preset.audience,
                    "narration_style": preset.narration_style,
                    "hook_rules": preset.hook_rules,
                    "topic_keywords": ", ".join(preset.topic_keywords),
                    "visual_keywords": ", ".join(preset.visual_keywords),
                    "sensitivity_notes": preset.sensitivity_notes or "",
                    "recommended_visual_sources": json.dumps(preset.recommended_visual_sources),
                    "outline_summary": outline_summary,
                    "previous_summaries": (
                        "\n".join(f"- {s}" for s in previous_summaries_list)
                        if previous_summaries_list
                        else "None (This is Chapter 1, the opening chapter)."
                    ),
                    "used_facts": (
                        "\n".join(f"- {f}" for f in cumulative_facts_list)
                        if cumulative_facts_list
                        else "None yet."
                    ),
                    "chapter_index": ch.chapter_index,
                    "chapter_title": ch.title,
                    "chapter_purpose": ch.purpose,
                    "chapter_key_points": ", ".join(ch.key_points),
                    "target_words": ch.target_words,
                }

                chapter_content, words, revised, needs_lang_review, lang_warning = self._draft_and_validate_chapter(
                    ch=ch,
                    context=context,
                    lang_tag=lang_tag,
                    lang_name=lang_name,
                    sensitivity_notes=preset.sensitivity_notes or "",
                    force_refresh=force_refresh,
                )

                # Generate chapter summary via LLM summary prompt (F4)
                summary, facts = self._generate_chapter_summary(
                    chapter_index=ch.chapter_index,
                    chapter_title=ch.title,
                    chapter_text=chapter_content,
                    topic=outline.topic,
                    language=lang_tag,
                    force_refresh=force_refresh,
                )

                # Save chapter file atomically
                with open(tmp_ch, "w", encoding="utf-8") as fp:
                    fp.write(chapter_content)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp_ch, ch_file)

                # Record in state
                chapters_state[idx_str] = {
                    "status": "completed",
                    "provider": self.adapter.provider,
                    "model": self.adapter.model,
                    "input_hash": expected_input_hash,
                    "content_hash": expected_content_hash,
                    "word_count": words,
                    "target_words": ch.target_words,
                    "revised": revised,
                    "needs_language_review": needs_lang_review,
                    "language_warning": lang_warning,
                    "summary": summary,
                    "key_facts": facts,
                }
                self._write_state(state)

                previous_summaries_list.append(f"Chapter {ch.chapter_index} ({ch.title}): {summary}")
                cumulative_facts_list.extend(facts)

            except (KeyboardInterrupt, Exception):
                if tmp_ch.exists():
                    tmp_ch.unlink(missing_ok=True)
                raise

        return self._assemble_and_import_full_script(manifest, outline, state)

    def _draft_and_validate_chapter(
        self,
        *,
        ch: OutlineChapter,
        context: Dict[str, Any],
        lang_tag: str,
        lang_name: str,
        sensitivity_notes: str,
        force_refresh: bool = False,
        extra_instruction: str = "",
    ) -> Tuple[str, int, bool, bool, Optional[str]]:
        """Draft a chapter, run length revision if needed, and retry up to 2x on language mismatch."""
        base_prompt = render_prompt("chapter", context)
        if extra_instruction:
            base_prompt = f"{extra_instruction.strip()}\n\n{base_prompt}"

        max_lang_retries = 2
        chapter_content = ""
        words = 0
        revised = False
        needs_lang_review = False
        lang_warning: Optional[str] = None

        for lang_attempt in range(max_lang_retries + 1):
            prompt = base_prompt
            eff_refresh = force_refresh or (lang_attempt > 0)
            if lang_attempt > 0:
                prompt = (
                    f"LANGUAGE CORRECTION (ATTEMPT {lang_attempt}/{max_lang_retries}): "
                    f"Your previous draft used the WRONG language! Write BOTH the `## ...` chapter heading "
                    f"AND every spoken sentence strictly in {lang_name} ({lang_tag}).\n\n"
                    + base_prompt
                )

            resp = self.adapter.generate(
                prompt=prompt,
                fake_kind="chapter",
                fake_kwargs={
                    "chapter_title": ch.title,
                    "chapter_index": ch.chapter_index,
                    "target_words": ch.target_words,
                },
                force_refresh=eff_refresh,
                max_tokens=compute_script_max_tokens("chapter", ch.target_words),
            )

            chapter_content = resp.content.strip()
            words = count_narration_words(chapter_content)
            target = max(1, ch.target_words)
            deviation = abs(words - target) / target

            if not revised and deviation > 0.15:
                logger.info(
                    f"Chapter {ch.chapter_index} word count ({words}) deviates >15% from target ({target}). "
                    "Running automatic length revision (max 1 per chapter)..."
                )
                action = (
                    "Expand narrative depth, technical details, and descriptions"
                    if words < target
                    else "Condense phrasing, eliminate passive voice, and tighten narrative"
                )
                rev_context = {
                    "chapter_title": ch.title,
                    "language": lang_tag,
                    "language_name": lang_name,
                    "target_words": ch.target_words,
                    "current_words": words,
                    "action": action,
                    "sensitivity_notes": sensitivity_notes,
                    "chapter_text": chapter_content,
                }
                rev_prompt = render_prompt("revise_length", rev_context)
                rev_resp = self.adapter.generate(
                    prompt=rev_prompt,
                    fake_kind="revise_length",
                    fake_kwargs={
                        "chapter_title": ch.title,
                        "target_words": ch.target_words,
                    },
                    force_refresh=True,
                    max_tokens=compute_script_max_tokens("revise_length", ch.target_words),
                )
                chapter_content = rev_resp.content.strip()
                words = count_narration_words(chapter_content)
                revised = True
                new_dev = abs(words - target) / target
                if new_dev > 0.15:
                    logger.info(
                        f"Chapter {ch.chapter_index} remains {words}/{target} words ({new_dev:.0%} diff) "
                        "after 1 length revision; accepting draft without further revision."
                    )

            heading, body_text = self._extract_heading_and_body(chapter_content)
            if words < 8:
                needs_lang_review = False
                lang_warning = None
                break

            comp = check_language_compliance(body_text, lang_tag, title_text=heading)
            if comp["ok"]:
                needs_lang_review = False
                lang_warning = None
                break

            needs_lang_review = True
            lang_warning = f"Perlu dicek: bahasa ({comp['reason']})"
            logger.warning(
                f"Chapter {ch.chapter_index} language mismatch (attempt {lang_attempt + 1}/{max_lang_retries + 1}): {lang_warning}"
            )

        return chapter_content, words, revised, needs_lang_review, lang_warning

    def _assemble_and_import_full_script(
        self,
        manifest: ProjectManifest,
        outline: ScriptOutline,
        state: Dict[str, Any],
    ) -> Path:
        """Assemble full_script.md, sync manifest & language flags, and inspect quality."""
        chapters_state = state.get("chapters", {})
        is_any_fake = self.adapter.is_fake or any(
            c.get("provider") == "fake" for c in chapters_state.values()
        )
        if state.get("outline_provider") == "fake":
            is_any_fake = True

        combined_lines = []
        if is_any_fake:
            combined_lines.append("<!-- GENERATED BY FAKE PROVIDER (TESTING ONLY) -->")
            combined_lines.append(f"# [FAKE] {manifest.title}\n")
        else:
            combined_lines.append(f"# {manifest.title}\n")

        for ch in outline.chapters:
            ch_path = self.script_dir / f"chapter_{ch.chapter_index:02d}.md"
            if ch_path.is_file():
                combined_lines.append(ch_path.read_text(encoding="utf-8").strip())
                combined_lines.append("")

        full_content = "\n\n".join(combined_lines).strip() + "\n"

        tmp_full = self.script_dir / f"full_script.md.tmp.{uuid.uuid4().hex}"
        with open(tmp_full, "w", encoding="utf-8") as fp:
            fp.write(full_content)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_full, self.full_script_file)

        # Import into manifest via update_project_script (single source of truth + normalized groups)
        update_project_script(self.project_dir, full_content)

        # Sync language review flags onto manifest chapters
        refreshed = load_manifest(self.project_dir)
        dirty = False
        for m_ch in refreshed.chapters:
            c_st = chapters_state.get(str(m_ch.chapter_index), {})
            flag = bool(c_st.get("needs_language_review", False))
            warn = c_st.get("language_warning")
            if m_ch.needs_language_review != flag or m_ch.language_warning != warn:
                m_ch.needs_language_review = flag
                m_ch.language_warning = warn
                dirty = True
        if dirty:
            save_manifest(refreshed, self.project_dir)

        from app.services.longform.quality_checker import ScriptQualityChecker
        checker = ScriptQualityChecker(self.project_dir)
        checker.inspect()

        return self.full_script_file

    def rewrite_chapter(self, chapter_index: int, force_refresh: bool = True) -> Path:
        """Rewrite a single chapter in the project narration language without touching others (8B.1 B2)."""
        self.adapter.validate_configuration()

        manifest = load_manifest(self.project_dir)
        preset = Preset.model_validate(manifest.preset_snapshot)
        target_lang = resolve_project_language(manifest, preset)
        lang_name, lang_tag = format_language_directive(target_lang)
        preset_hash = compute_preset_snapshot_hash(manifest.preset_snapshot)

        if self.outline_file.is_file():
            outline = ScriptOutline.model_validate(
                json.loads(self.outline_file.read_text(encoding="utf-8"))
            )
        else:
            # Synthesize outline view from existing manifest chapters (e.g. imported scripts)
            synth_chapters = [
                OutlineChapter(
                    chapter_index=c.chapter_index,
                    title=c.chapter_title,
                    purpose=c.chapter_title,
                    key_points=[],
                    target_words=max(
                        150,
                        sum(len(g.paragraph_text.split()) for g in c.tts_groups if g.paragraph_text),
                    ),
                )
                for c in manifest.chapters
            ]
            outline = ScriptOutline(
                topic=manifest.topic or manifest.title,
                target_duration_minutes=float(manifest.target_duration_minutes or 12.0),
                words_per_minute=preset.words_per_minute,
                total_target_words=sum(c.target_words for c in synth_chapters),
                candidate_titles=[manifest.title, f"{manifest.title} Part 1", f"{manifest.title} Full Story"],
                opening_hook=manifest.title,
                chapters=synth_chapters,
            )

        target_ch = next((c for c in outline.chapters if c.chapter_index == chapter_index), None)
        if target_ch is None:
            raise ValueError(f"Chapter {chapter_index} not found in project '{manifest.project_id}'.")

        state = self._read_state()
        chapters_state = state.setdefault("chapters", {})
        prev_summaries: List[str] = []
        used_facts: List[str] = []
        for prev_ch in outline.chapters:
            if prev_ch.chapter_index >= chapter_index:
                break
            c_st = chapters_state.get(str(prev_ch.chapter_index), {})
            if c_st.get("summary"):
                prev_summaries.append(f"Chapter {prev_ch.chapter_index} ({prev_ch.title}): {c_st['summary']}")
            used_facts.extend(c_st.get("key_facts", []))

        outline_summary = "\n".join(
            f"- Chapter {c.chapter_index}: {c.title} (~{c.target_words} words) - {c.purpose}"
            for c in outline.chapters
        )

        # Ensure heading gets translated if the outline title itself was in a mismatched language
        title_check = check_language_compliance(target_ch.title, lang_tag, title_text=target_ch.title)
        extra_instr = ""
        if not title_check["ok"]:
            extra_instr = (
                f"IMPORTANT: The chapter title `{target_ch.title}` is in the wrong language. "
                f"Translate/rewrite the `## ...` chapter heading strictly into {lang_name} ({lang_tag}) too!"
            )

        ch_canonical = target_ch.model_dump_json()
        content_hash_material = (
            f"{ch_canonical}|{lang_tag}|{preset_hash}|{manifest.sub_niche_focus or ''}|"
            f"{';'.join(prev_summaries)}"
        )
        expected_content_hash = hashlib.sha256(content_hash_material.encode("utf-8")).hexdigest()
        expected_input_hash = hashlib.sha256(
            f"{self.adapter.provider}:{self.adapter.model or ''}|{content_hash_material}".encode("utf-8")
        ).hexdigest()

        context = {
            "topic": outline.topic,
            "sub_niche_focus": manifest.sub_niche_focus or "",
            "language": lang_tag,
            "language_name": lang_name,
            "audience": preset.audience,
            "narration_style": preset.narration_style,
            "hook_rules": preset.hook_rules,
            "topic_keywords": ", ".join(preset.topic_keywords),
            "visual_keywords": ", ".join(preset.visual_keywords),
            "sensitivity_notes": preset.sensitivity_notes or "",
            "recommended_visual_sources": json.dumps(preset.recommended_visual_sources),
            "outline_summary": outline_summary,
            "previous_summaries": "\n".join(f"- {s}" for s in prev_summaries) if prev_summaries else "None.",
            "used_facts": "\n".join(f"- {f}" for f in used_facts) if used_facts else "None yet.",
            "chapter_index": target_ch.chapter_index,
            "chapter_title": target_ch.title,
            "chapter_purpose": target_ch.purpose,
            "chapter_key_points": ", ".join(target_ch.key_points),
            "target_words": target_ch.target_words,
        }

        chapter_content, words, revised, needs_lang_review, lang_warning = self._draft_and_validate_chapter(
            ch=target_ch,
            context=context,
            lang_tag=lang_tag,
            lang_name=lang_name,
            sensitivity_notes=preset.sensitivity_notes or "",
            force_refresh=force_refresh,
            extra_instruction=extra_instr,
        )

        # If chapter heading was rewritten cleanly into target language, persist to outline.json if present
        new_heading, _ = self._extract_heading_and_body(chapter_content)
        if new_heading and self.outline_file.is_file():
            target_ch.title = new_heading
            tmp_out = self.script_dir / f"outline.json.tmp.{uuid.uuid4().hex}"
            with open(tmp_out, "w", encoding="utf-8") as fp:
                fp.write(outline.model_dump_json(indent=2))
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp_out, self.outline_file)

        summary, facts = self._generate_chapter_summary(
            chapter_index=target_ch.chapter_index,
            chapter_title=target_ch.title,
            chapter_text=chapter_content,
            topic=outline.topic,
            language=lang_tag,
            force_refresh=True,
        )

        ch_file = self.script_dir / f"chapter_{chapter_index:02d}.md"
        tmp_ch = self.script_dir / f"chapter_{chapter_index:02d}.md.tmp.{uuid.uuid4().hex}"
        with open(tmp_ch, "w", encoding="utf-8") as fp:
            fp.write(chapter_content)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_ch, ch_file)

        chapters_state[str(chapter_index)] = {
            "status": "completed",
            "provider": self.adapter.provider,
            "model": self.adapter.model,
            "input_hash": expected_input_hash,
            "content_hash": expected_content_hash,
            "word_count": words,
            "target_words": target_ch.target_words,
            "revised": revised,
            "needs_language_review": needs_lang_review,
            "language_warning": lang_warning,
            "summary": summary,
            "key_facts": facts,
        }
        self._write_state(state)

        return self._assemble_and_import_full_script(manifest, outline, state)

    def get_status(self) -> Dict[str, Any]:
        """Return status of outline and each chapter (2.6)."""
        state = self._read_state()
        has_outline = self.outline_file.is_file()
        cache_files = list(self.cache_dir.glob("*.json")) if self.cache_dir.is_dir() else []
        outline_provider = state.get("outline_provider", "")
        outline_model = state.get("outline_model", "")
        chapters_info = []

        if has_outline and not outline_provider and cache_files:
            for cf in cache_files:
                try:
                    cdata = json.loads(cf.read_text(encoding="utf-8"))
                    if cdata.get("provider"):
                        outline_provider = cdata["provider"]
                        outline_model = cdata.get("model", "")
                        break
                except Exception:
                    pass

        is_fake_project = (outline_provider == "fake") or self.adapter.is_fake

        if has_outline:
            try:
                outline_dict = json.loads(self.outline_file.read_text(encoding="utf-8"))
                if not outline_provider:
                    outline_provider = outline_dict.get("provider", "")
                    outline_model = outline_dict.get("model", "")
                if outline_provider == "fake":
                    is_fake_project = True

                for ch in outline_dict.get("chapters", []):
                    idx = ch["chapter_index"]
                    idx_str = str(idx)
                    ch_file = self.script_dir / f"chapter_{idx:02d}.md"
                    c_state = state.get("chapters", {}).get(idx_str, {})
                    ch_prov = c_state.get("provider", outline_provider or self.adapter.provider)
                    ch_mod = c_state.get("model", outline_model or self.adapter.model)
                    if ch_prov == "fake":
                        is_fake_project = True

                    chapters_info.append(
                        {
                            "chapter_index": idx,
                            "title": ch.get("title", f"Chapter {idx}"),
                            "target_words": ch.get("target_words", 0),
                            "status": c_state.get("status", "pending" if not ch_file.is_file() else "completed"),
                            "provider": ch_prov,
                            "model": ch_mod,
                            "is_fake": (ch_prov == "fake"),
                            "word_count": c_state.get("word_count", count_narration_words(ch_file.read_text(encoding="utf-8")) if ch_file.is_file() else 0),
                            "revised": c_state.get("revised", False),
                            "has_summary": bool(c_state.get("summary")),
                        }
                    )
            except Exception:
                pass

        cache_files = list(self.cache_dir.glob("*.json")) if self.cache_dir.is_dir() else []

        return {
            "has_outline": has_outline,
            "outline_provider": outline_provider or self.adapter.provider,
            "outline_model": outline_model or self.adapter.model,
            "is_fake": is_fake_project,
            "chapters": chapters_info,
            "cache_entries": len(cache_files),
            "full_script_exists": self.full_script_file.is_file(),
            "quality_report_exists": (self.script_dir / "quality_report.md").is_file(),
        }
