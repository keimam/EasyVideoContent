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
from app.services.longform.llm_adapter import LLMAdapter, LLMAdapterError
from app.services.longform.presets import get_preset
from app.services.longform.prompts import render_prompt
from app.services.longform.schema import (
    OutlineChapter,
    Preset,
    ProjectManifest,
    ScriptOutline,
)
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
        provider: str = "fake",
        model: Optional[str] = None,
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

    def generate_outline(
        self,
        topic: str,
        target_duration_minutes: float = 12.0,
        sub_niche_focus: Optional[str] = None,
        force_refresh: bool = False,
    ) -> ScriptOutline:
        """Generate outline (JSON) and save to script/outline.json (2.3 Step 1)."""
        manifest = load_manifest(self.project_dir)
        preset = Preset.model_validate(manifest.preset_snapshot)

        focus = sub_niche_focus or manifest.sub_niche_focus or ""
        wpm = preset.words_per_minute
        total_target_words = int(target_duration_minutes * wpm)

        prompt_context = {
            "topic": topic,
            "sub_niche_focus": focus or "General authoritative exploration",
            "target_duration_minutes": target_duration_minutes,
            "words_per_minute": wpm,
            "total_target_words": total_target_words,
            "language": preset.language,
            "audience": preset.audience,
            "narration_style": preset.narration_style,
            "hook_rules": preset.hook_rules,
            "chapter_structure": json.dumps(preset.chapter_structure),
            "topic_keywords": ", ".join(preset.topic_keywords),
            "visual_keywords": ", ".join(preset.visual_keywords),
            "sensitivity_notes": preset.sensitivity_notes or "Verify all facts.",
            "recommended_visual_sources": json.dumps(preset.recommended_visual_sources),
        }

        prompt = render_prompt("outline", prompt_context)
        resp = self.adapter.generate(
            prompt=prompt,
            fake_kind="outline",
            fake_kwargs={
                "topic": topic,
                "target_duration_minutes": target_duration_minutes,
                "words_per_minute": wpm,
            },
            force_refresh=force_refresh,
        )

        json_str = extract_json_payload(resp.content)
        try:
            outline_dict = json.loads(json_str)
        except Exception as exc:
            raise LLMAdapterError(
                f"Failed to parse outline JSON returned by provider '{resp.provider}': {exc}\nRaw text:\n{resp.content}"
            ) from exc

        # Validate with Pydantic
        outline = ScriptOutline.model_validate(outline_dict)

        # Atomically save outline.json
        tmp_outline = self.script_dir / f"outline.json.tmp.{uuid.uuid4().hex}"
        with open(tmp_outline, "w", encoding="utf-8") as fp:
            fp.write(outline.model_dump_json(indent=2))
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_outline, self.outline_file)

        # Update state outline hash
        outline_hash = hashlib.sha256(self.outline_file.read_bytes()).hexdigest()
        state = self._read_state()
        state["outline_hash"] = outline_hash
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
        if not self.outline_file.is_file():
            raise FileNotFoundError(
                f"Outline file not found at '{self.outline_file}'. Run 'script outline' first."
            )

        manifest = load_manifest(self.project_dir)
        preset = Preset.model_validate(manifest.preset_snapshot)
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

            # Compute input hash for this chapter
            ch_canonical = ch.model_dump_json()
            input_hash_material = (
                f"{ch_canonical}|{preset_hash}|{manifest.sub_niche_focus or ''}|"
                f"{';'.join(previous_summaries_list)}"
            )
            expected_input_hash = hashlib.sha256(input_hash_material.encode("utf-8")).hexdigest()

            ch_state = chapters_state.get(idx_str, {})
            already_done = (
                not force_refresh
                and ch_file.is_file()
                and ch_state.get("status") == "completed"
                and ch_state.get("input_hash") == expected_input_hash
            )

            if already_done:
                logger.info(f"Chapter {ch.chapter_index} up-to-date. Reusing cached output.")
                ch_content = ch_file.read_text(encoding="utf-8")
                summary, facts = extract_chapter_summary_and_facts(ch_content)
                previous_summaries_list.append(f"Chapter {ch.chapter_index} ({ch.title}): {summary}")
                cumulative_facts_list.extend(facts)
                continue

            # Check simulated failure injection for resume testing
            if fail_at_chapter is not None and ch.chapter_index == fail_at_chapter:
                raise RuntimeError(f"Simulated failure at chapter {ch.chapter_index}")

            logger.info(f"Writing Chapter {ch.chapter_index}: {ch.title} (target: ~{ch.target_words} words)...")

            # Prepare chapter prompt context
            context = {
                "topic": outline.topic,
                "sub_niche_focus": manifest.sub_niche_focus or "",
                "language": preset.language,
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

            prompt = render_prompt("chapter", context)
            resp = self.adapter.generate(
                prompt=prompt,
                fake_kind="chapter",
                fake_kwargs={
                    "chapter_title": ch.title,
                    "chapter_index": ch.chapter_index,
                    "target_words": ch.target_words,
                },
                force_refresh=force_refresh,
            )

            chapter_content = resp.content.strip()

            # Word count check and length revision (2.3 Step 3)
            words = count_narration_words(chapter_content)
            target = max(1, ch.target_words)
            deviation = abs(words - target) / target
            revised = False

            if deviation > 0.15:
                logger.info(
                    f"Chapter {ch.chapter_index} word count ({words}) deviates >15% from target ({target}). "
                    "Running automatic length revision..."
                )
                action = (
                    "Expand narrative depth, technical details, and descriptions"
                    if words < target
                    else "Condense phrasing, eliminate passive voice, and tighten narrative"
                )
                rev_context = {
                    "chapter_title": ch.title,
                    "target_words": ch.target_words,
                    "current_words": words,
                    "action": action,
                    "sensitivity_notes": preset.sensitivity_notes or "",
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
                    force_refresh=True,  # Fresh revision
                )
                chapter_content = rev_resp.content.strip()
                words = count_narration_words(chapter_content)
                revised = True

            # Save chapter file atomically
            tmp_ch = self.script_dir / f"chapter_{ch.chapter_index:02d}.md.tmp.{uuid.uuid4().hex}"
            with open(tmp_ch, "w", encoding="utf-8") as fp:
                fp.write(chapter_content)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp_ch, ch_file)

            # Extract summary and facts for next chapters
            summary, facts = extract_chapter_summary_and_facts(chapter_content)
            previous_summaries_list.append(f"Chapter {ch.chapter_index} ({ch.title}): {summary}")
            cumulative_facts_list.extend(facts)

            # Record in state
            chapters_state[idx_str] = {
                "status": "completed",
                "input_hash": expected_input_hash,
                "word_count": words,
                "target_words": ch.target_words,
                "revised": revised,
            }
            self._write_state(state)

        # Step 4: Combine all chapters into script/full_script.md
        combined_lines = [f"# {manifest.title}\n"]
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

        # Import into manifest via update_project_script (single source of truth)
        update_project_script(self.project_dir, full_content)

        return self.full_script_file

    def get_status(self) -> Dict[str, Any]:
        """Return status of outline and each chapter (2.6)."""
        state = self._read_state()
        has_outline = self.outline_file.is_file()
        chapters_info = []

        if has_outline:
            try:
                outline_dict = json.loads(self.outline_file.read_text(encoding="utf-8"))
                for ch in outline_dict.get("chapters", []):
                    idx = ch["chapter_index"]
                    idx_str = str(idx)
                    ch_file = self.script_dir / f"chapter_{idx:02d}.md"
                    c_state = state.get("chapters", {}).get(idx_str, {})
                    chapters_info.append(
                        {
                            "chapter_index": idx,
                            "title": ch.get("title", f"Chapter {idx}"),
                            "target_words": ch.get("target_words", 0),
                            "status": c_state.get("status", "pending" if not ch_file.is_file() else "completed"),
                            "word_count": c_state.get("word_count", count_narration_words(ch_file.read_text(encoding="utf-8")) if ch_file.is_file() else 0),
                            "revised": c_state.get("revised", False),
                        }
                    )
            except Exception:
                pass

        cache_files = list(self.cache_dir.glob("*.json")) if self.cache_dir.is_dir() else []

        return {
            "has_outline": has_outline,
            "chapters": chapters_info,
            "cache_entries": len(cache_files),
            "full_script_exists": self.full_script_file.is_file(),
        }
