#!/usr/bin/env python
"""CLI for Longform Video Generation (Stage 1 & Stage 2).

A standalone CLI providing:
- Multi-tier preset management and hierarchy visualization (B1)
- Project scaffolding, planning, resume validation, and script updates (A4)
- Automated AI script generation: outline, sequential chapter drafting, length revision (2.3, 2.6)
- Script quality, cliché, and fact-checking inspection (2.4, 2.6)
- Simulated pipeline execution (dev-simulate)

Zero modifications to upstream files.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

# Reconfigure stdout/stderr for UTF-8 on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.longform.config import load_longform_config
from app.services.longform.hashing import (
    compute_audio_group_hash,
    compute_chapter_hash,
    compute_final_concat_hash,
    compute_visual_segment_hash,
)
from app.services.longform.planner import compute_plan
from app.services.longform.presets import (
    format_preset_tree,
    get_preset,
    get_preset_directories,
    get_preset_inheritance_chain,
    list_presets,
    load_preset_file,
    validate_preset_file,
)
from app.services.longform.schema import ItemStatus, ProjectManifest
from app.services.longform.script_import import estimate_script_duration, parse_markdown_script
from app.services.longform.store import (
    ProjectLock,
    ProjectLockedError,
    create_project,
    get_project_dir,
    load_manifest,
    refresh_project_preset,
    save_manifest,
    update_project_script,
)


def cmd_presets_list(args: argparse.Namespace) -> int:
    """List all available presets in a visual hierarchy (B1)."""
    presets = list_presets()
    print("\n" + format_preset_tree(presets) + "\n")
    return 0


def cmd_presets_validate(args: argparse.Namespace) -> int:
    """Validate a preset JSON file and resolve its inheritance."""
    path = Path(args.file)
    if not path.is_file():
        print(f"Error: File not found: {path}", file=sys.stderr)
        return 1

    is_valid, err = validate_preset_file(path)
    if is_valid:
        preset = load_preset_file(path)
        chain = get_preset_inheritance_chain(preset.id)
        print(f"SUCCESS: Preset '{path.name}' is valid.")
        print(f"  - ID:          {preset.id}")
        print(f"  - Name:        {preset.name}")
        print(f"  - Kind:        {preset.kind}")
        print(f"  - Extends:     {preset.extends or 'None (Root Niche)'}")
        print(f"  - Chain:       {' -> '.join(chain)}")
        print(f"  - Language:    {preset.language}")
        print(f"  - WPM:         {preset.words_per_minute}")
        if preset.topic_keywords:
            print(f"  - Keywords:    {len(preset.topic_keywords)} topic, {len(preset.visual_keywords)} visual")
        return 0
    else:
        print(f"VALIDATION ERROR in '{path}':\n{err}", file=sys.stderr)
        return 1


def cmd_project_create(args: argparse.Namespace) -> int:
    """Create a new longform project from a script file or for LLM outline generation (2.6)."""
    if not args.script and not args.topic:
        print("Error: Either --script <file.md> or --topic <string> is required.", file=sys.stderr)
        return 1

    script_markdown = None
    if args.script:
        script_path = Path(args.script)
        if not script_path.is_file():
            print(f"Error: Script file not found: {script_path}", file=sys.stderr)
            return 1
        try:
            with open(script_path, "r", encoding="utf-8-sig") as fp:
                script_markdown = fp.read()
        except Exception as exc:
            print(f"Error reading script: {exc}", file=sys.stderr)
            return 1

    project_id = args.id or re_slugify(args.title)

    try:
        manifest, project_dir = create_project(
            project_id=project_id,
            title=args.title,
            preset_id=args.preset,
            script_markdown=script_markdown,
            aspect_ratio=args.aspect_ratio or "16:9",
            sub_niche_focus=args.focus,
        )
    except Exception as exc:
        print(f"Error creating project: {exc}", file=sys.stderr)
        return 1

    preset = get_preset(args.preset)

    print(f"\nProject created successfully!")
    print(f"  - ID:                 {manifest.project_id}")
    print(f"  - Title:              {manifest.title}")
    print(f"  - Directory:          {project_dir}")
    print(f"  - Preset:             {manifest.preset_id} ({preset.name})")
    if manifest.preset_inheritance_chain:
        print(f"  - Preset Chain:       {' -> '.join(manifest.preset_inheritance_chain)}")
    if manifest.sub_niche_focus:
        print(f"  - Sub-Niche Focus:    \"{manifest.sub_niche_focus}\"")

    if manifest.chapters:
        duration_stats = estimate_script_duration(manifest.chapters, preset.words_per_minute)
        print(f"  - Chapters:           {len(manifest.chapters)}")
        total_groups = sum(len(c.tts_groups) for c in manifest.chapters)
        total_segments = sum(len(g.segments) for c in manifest.chapters for g in c.tts_groups)
        print(f"  - TTS Groups:         {total_groups}")
        print(f"  - Visual Segments:    {total_segments}")
        print(f"  - Total Words:        {duration_stats['total_words']}")
        print(f"  - Estimated Duration: {duration_stats['estimated_duration_seconds']:.1f}s ({duration_stats['estimated_duration_seconds']/60:.1f} min)")

        if duration_stats["warnings"]:
            print("\nWarnings:")
            for w in duration_stats["warnings"]:
                print(f"  [WARNING] {w}")
    else:
        print(f"  - Chapters:           0 (Ready for AI script generation)")
        if args.topic:
            print(f"  - Topic:              \"{args.topic}\"")
            print(f"\nNext Step: Run 'cli_longform.py script outline {project_id} --topic \"{args.topic}\"' to generate outline.")

    print()
    return 0


def cmd_project_show(args: argparse.Namespace) -> int:
    """Display information about an existing project."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project directory not found at {project_dir}", file=sys.stderr)
        return 1

    try:
        manifest = load_manifest(project_dir)
    except Exception as exc:
        print(f"Error loading project: {exc}", file=sys.stderr)
        return 1

    preset = get_preset(manifest.preset_id)
    duration_stats = estimate_script_duration(manifest.chapters, preset.words_per_minute)

    print(f"\nProject Overview: {manifest.project_id}")
    print("=" * 65)
    print(f"  Title:          {manifest.title}")
    print(f"  Status:         {manifest.status.value}")
    print(f"  Preset:         {manifest.preset_id}")
    if manifest.preset_inheritance_chain:
        print(f"  Preset Chain:   {' -> '.join(manifest.preset_inheritance_chain)}")
    if manifest.sub_niche_focus:
        print(f"  Sub-Niche Focus:\"{manifest.sub_niche_focus}\"")
    print(f"  Aspect Ratio:   {manifest.aspect_ratio}")
    print(f"  Pipeline Ver:   {manifest.pipeline_version}")
    print(f"  Schema Ver:     {manifest.schema_version}")
    print(f"  Est. Duration:  {duration_stats['estimated_duration_seconds']:.1f}s ({duration_stats['estimated_duration_seconds']/60:.1f} min)")
    print(f"  Chapters ({len(manifest.chapters)}):")
    for ch in manifest.chapters:
        num_g = len(ch.tts_groups)
        num_s = sum(len(g.segments) for g in ch.tts_groups)
        print(f"    - Ch {ch.chapter_index}: {ch.chapter_title} [{ch.status.value}] ({num_g} groups, {num_s} segs)")
    print("=" * 65 + "\n")
    return 0


def cmd_project_plan(args: argparse.Namespace) -> int:
    """Compute and display execution plan for a project."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    try:
        manifest = load_manifest(project_dir)
    except Exception as exc:
        print(f"Error loading manifest: {exc}", file=sys.stderr)
        return 1

    plan = compute_plan(manifest, project_dir)

    print(f"\nExecution Plan for Project: {manifest.project_id}")
    print(f"Total Work Units: {plan.total_count}")
    print(f"  - Completed: {plan.completed_count}")
    print(f"  - Stale:     {plan.stale_count}")
    print(f"  - Pending:   {plan.pending_count}")

    if plan.is_fully_completed:
        print("Status: Project is fully completed and up to date.")
    elif plan.has_work_remaining:
        print(f"Status: Incremental update required ({plan.stale_count} stale, {plan.pending_count} pending)")

    print("\n" + "-" * 90)
    print(f"{'Unit ID':<22} | {'Type':<16} | {'Status':<10} | {'Reason'}")
    print("-" * 90)
    for u in plan.units:
        print(f"{u.unit_id:<22} | {u.unit_type:<16} | {u.status.value:<10} | {u.reason}")
    print("-" * 90 + "\n")
    return 0


def cmd_project_update_script(args: argparse.Namespace) -> int:
    """Re-import script into project and output summary diff (A4)."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    script_path = Path(args.script)
    if not script_path.is_file():
        print(f"Error: Script file not found at '{script_path}'", file=sys.stderr)
        return 1

    try:
        with open(script_path, "r", encoding="utf-8-sig") as fp:
            new_script = fp.read()
    except Exception as exc:
        print(f"Error reading script: {exc}", file=sys.stderr)
        return 1

    try:
        stats = update_project_script(project_dir, new_script)
    except Exception as exc:
        print(f"Error updating script: {exc}", file=sys.stderr)
        return 1

    print(f"\nScript Update Summary for Project: {args.project_id}")
    print("-" * 60)
    print(f"  Chapters:    {stats['chapters_unchanged']} unchanged, {stats['chapters_modified']} modified, {stats['chapters_added']} added, {stats['chapters_removed']} removed")
    print(f"  TTS Groups:  {stats['groups_unchanged']} unchanged, {stats['groups_modified']} modified, {stats['groups_added']} added, {stats['groups_removed']} removed")
    print(f"  Segments:    {stats['segments_unchanged']} unchanged, {stats['segments_modified']} modified, {stats['segments_added']} added, {stats['segments_removed']} removed")
    print("-" * 60)
    print("Status: Manifest updated successfully.")
    print(f"Run 'python cli_longform.py project plan {args.project_id}' to inspect invalidated units.\n")
    return 0


def cmd_project_refresh_preset(args: argparse.Namespace) -> int:
    """Refresh project preset snapshot from disk and mark dependencies stale (B2)."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    try:
        manifest, details = refresh_project_preset(project_dir)
    except Exception as exc:
        print(f"Error refreshing preset: {exc}", file=sys.stderr)
        return 1

    print(f"\nPreset Snapshot Refreshed for Project: {args.project_id}")
    print("-" * 60)
    print(f"  Preset ID:         {details['preset_id']}")
    print(f"  Chain:             {' -> '.join(details['preset_inheritance_chain'])}")
    print(f"  Old Snapshot Hash: {details['old_snapshot_hash'][:16]}...")
    print(f"  New Snapshot Hash: {details['new_snapshot_hash'][:16]}...")
    print("-" * 60)
    print("Status: Preset snapshot updated. Associated chapter hashes have been refreshed.\n")
    return 0


def cmd_project_dev_simulate(args: argparse.Namespace) -> int:
    """Simulate pipeline execution for testing resume and error handling (A3)."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    print(f"\n[SIMULATION START] Beginning pipeline simulation for project: {args.project_id}")
    if args.fail_at:
        print(f"[SIMULATION] Planned injected failure at unit: {args.fail_at}")

    try:
        with ProjectLock(project_dir):
            manifest = load_manifest(project_dir)
            plan = compute_plan(manifest, project_dir)

            if plan.is_fully_completed:
                print("[SIMULATION] All units already completed. Nothing to simulate.\n")
                return 0

            units_to_run = [u for u in plan.units if u.status != ItemStatus.completed]
            print(f"[SIMULATION] Total units to process: {len(units_to_run)}")

            for idx, unit in enumerate(units_to_run, start=1):
                if args.fail_at and unit.unit_id == args.fail_at:
                    print(f"\n[SIMULATION INJECTED FAILURE] Triggering deliberate failure at unit: {unit.unit_id}")
                    print(f"[SIMULATION] Pipeline terminated prematurely. Assets before this remain valid.\n")
                    return 1

                if args.delay > 0:
                    time.sleep(args.delay)

                # Simulate creation of dummy asset atomically via temp file
                if unit.target_file:
                    target = project_dir / unit.target_file
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp_asset = target.parent / f"{target.name}.tmp.{uuid.uuid4().hex}"
                    with open(tmp_asset, "w", encoding="utf-8") as fp:
                        fp.write(f"SIMULATED_ASSET:{unit.unit_id}:{unit.expected_hash}\n")
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(tmp_asset, target)

                # Update state in manifest
                if unit.unit_type == "tts_group_audio":
                    parts = unit.unit_id.split("_")
                    ch_idx = int(parts[0].replace("ch", ""))
                    g_idx = int(parts[1].replace("g", ""))
                    for ch in manifest.chapters:
                        if ch.chapter_index == ch_idx:
                            for g in ch.tts_groups:
                                if g.group_index == g_idx:
                                    g.audio.file_path = unit.target_file
                                    g.audio.audio_hash = unit.expected_hash
                                    g.status = ItemStatus.completed

                elif unit.unit_type == "visual_segment":
                    parts = unit.unit_id.split("_")
                    ch_idx = int(parts[0].replace("ch", ""))
                    s_idx = int(parts[1].replace("s", ""))
                    for ch in manifest.chapters:
                        if ch.chapter_index == ch_idx:
                            for g in ch.tts_groups:
                                for s in g.segments:
                                    if s.segment_index == s_idx:
                                        s.visual.rendered_clip = unit.target_file
                                        s.visual.visual_hash = unit.expected_hash
                                        s.status = ItemStatus.completed

                elif unit.unit_type == "chapter_render":
                    ch_idx = int(unit.unit_id.replace("ch", "").replace("_render", ""))
                    for ch in manifest.chapters:
                        if ch.chapter_index == ch_idx:
                            latest_hash = compute_chapter_hash(
                                chapter=ch,
                                encoder_settings=manifest.encoder_settings,
                                subtitle_style=manifest.preset_snapshot.get("subtitle_style", {}),
                                preset_snapshot=manifest.preset_snapshot,
                                pipeline_version=manifest.pipeline_version,
                                sub_niche_focus=manifest.sub_niche_focus or "",
                            )
                            ch.output_file = unit.target_file
                            ch.chapter_hash = latest_hash
                            ch.status = ItemStatus.completed

                elif unit.unit_type == "final_concat":
                    latest_final_hash = compute_final_concat_hash(
                        manifest.chapters,
                        bgm_hash="",
                        pipeline_version=manifest.pipeline_version,
                    )
                    manifest.final_video_file = unit.target_file
                    manifest.final_video_hash = latest_final_hash
                    manifest.status = ItemStatus.completed

                save_manifest(manifest, project_dir)
                print(f"[SIMULATION] Completed and saved state for: {unit.unit_id}")

            final_plan = compute_plan(manifest, project_dir)
            if final_plan.is_fully_completed:
                manifest.status = ItemStatus.completed
                save_manifest(manifest, project_dir)
                print("\n[SIMULATION SUCCESS] Pipeline run simulated successfully!")
                print("All chapters, audio groups, visual segments, and final video are up to date.\n")
            else:
                print("\n[SIMULATION FINISHED] Run ended with partial completion.\n")

    except KeyboardInterrupt:
        print("\n[SIMULATION INTERRUPTED] KeyboardInterrupt received. Safely exiting and releasing project lock.\n", file=sys.stderr)
        return 130
    except ProjectLockedError as exc:
        print(f"\n[ERROR] Project lock conflict: {exc}\n", file=sys.stderr)
        return 2

    return 0


# =========================================================================
# STAGE 2 CLI COMMANDS: SCRIPT GENERATION & QUALITY
# =========================================================================

def cmd_script_outline(args: argparse.Namespace) -> int:
    """Generate structured outline with candidate titles, hook, and chapters (2.3, 2.6)."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    from app.services.longform.llm_adapter import LLMConfigurationError
    from app.services.longform.script_generator import ScriptGenerator
    generator = ScriptGenerator(project_dir=project_dir, provider=args.provider, model=args.model)

    prov_str = f"{generator.adapter.provider} [FAKE TEST MOCK]" if generator.adapter.is_fake else generator.adapter.provider
    print(f"\n[LLM Configuration] Provider: {prov_str} | Model: {generator.adapter.model or '(default)'}")
    sys.stdout.flush()

    try:
        outline = generator.generate_outline(
            topic=args.topic,
            target_duration_minutes=args.minutes,
        )
    except LLMConfigurationError as exc:
        print(f"\n[CONFIGURATION ERROR] {exc}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[SCRIPT OUTLINE INTERRUPTED] KeyboardInterrupt received. Safely exiting.\n", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error generating outline: {exc}", file=sys.stderr)
        return 1

    print(f"\nScript Outline Generated Successfully for Project: {args.project_id}")
    print("=" * 65)
    print(f"  Topic:           {outline.topic}")
    print(f"  Target Duration: {outline.target_duration_minutes} minutes ({outline.total_target_words} words)")
    print(f"  Candidate Titles:")
    for i, t in enumerate(outline.candidate_titles, 1):
        print(f"    {i}. {t}")
    print(f"  Opening Hook:    \"{outline.opening_hook}\"")
    print(f"  Chapters ({len(outline.chapters)}):")
    for ch in outline.chapters:
        print(f"    - Ch {ch.chapter_index}: {ch.title} (~{ch.target_words} words)")
        print(f"      Purpose: {ch.purpose}")
    print("=" * 65)
    print(f"Saved to: {project_dir / 'script' / 'outline.json'}")
    print(f"Run 'python cli_longform.py script write {args.project_id}' to generate chapter narration.\n")
    return 0


def cmd_script_write(args: argparse.Namespace) -> int:
    """Write chapters sequentially with context, length revision, and resume (2.3, 2.6)."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    from app.services.longform.llm_adapter import LLMConfigurationError
    from app.services.longform.script_generator import ScriptGenerator
    generator = ScriptGenerator(
        project_dir=project_dir,
        provider=args.provider,
        model=args.model,
        fake_delay=getattr(args, "fake_delay", 0.0),
    )

    prov_str = f"{generator.adapter.provider} [FAKE TEST MOCK]" if generator.adapter.is_fake else generator.adapter.provider
    print(f"\n[LLM Configuration] Provider: {prov_str} | Model: {generator.adapter.model or '(default)'}")
    sys.stdout.flush()

    try:
        full_script = generator.write_chapters(
            fail_at_chapter=args.fail_at,
        )
    except LLMConfigurationError as exc:
        print(f"\n[CONFIGURATION ERROR] {exc}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[SCRIPT WRITE INTERRUPTED] KeyboardInterrupt received. Safely exiting without saving incomplete chapter.\n", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error writing script chapters: {exc}", file=sys.stderr)
        return 1

    print(f"\nScript Writing Completed Successfully for Project: {args.project_id}")
    print("-" * 65)
    print(f"  Saved Full Script: {full_script}")
    print(f"  Quality Report:    {project_dir / 'script' / 'quality_report.md'}")
    print("  Manifest chapters and groups updated successfully via single pipeline path.")
    print("-" * 65)
    print(f"Run 'python cli_longform.py script quality {args.project_id}' to re-inspect quality anytime.\n")
    return 0


def cmd_script_status(args: argparse.Namespace) -> int:
    """Show status of outline, chapters, cache entries, and word counts (2.6)."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    from app.services.longform.script_generator import ScriptGenerator
    generator = ScriptGenerator(project_dir=project_dir)
    status = generator.get_status()

    fake_badge = " [FAKE MOCK CONTENT]" if status.get("is_fake") else ""
    print(f"\nScript Generation Status: {args.project_id}{fake_badge}")
    print("=" * 65)
    print(f"  Outline Created:     {'Yes' if status['has_outline'] else 'No'}")
    if status['has_outline']:
        print(f"  Outline Provider:    {status.get('outline_provider')} (Model: {status.get('outline_model')})")
    print(f"  Full Script Created: {'Yes' if status['full_script_exists'] else 'No'}")
    print(f"  Quality Report:      {'Yes' if status.get('quality_report_exists') else 'No'}")
    print(f"  LLM Cache Entries:   {status['cache_entries']}")
    print(f"  Chapters:")
    if status["chapters"]:
        for ch in status["chapters"]:
            rev_tag = " (revised for length)" if ch["revised"] else ""
            fake_tag = " [FAKE]" if ch.get("is_fake") else ""
            print(f"    - Ch {ch['chapter_index']}: {ch['title']} [{ch['status']}{fake_tag}] - {ch['word_count']}/{ch['target_words']} words{rev_tag}")
            print(f"      (Provider: {ch.get('provider')} | Model: {ch.get('model')})")
    else:
        print(f"    No chapters generated yet.")
    print("=" * 65 + "\n")
    return 0


def cmd_script_quality(args: argparse.Namespace) -> int:
    """Inspect script for clichés, hook compliance, repetition, and fact-checking (2.4, 2.6)."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    from app.services.longform.quality_checker import ScriptQualityChecker
    checker = ScriptQualityChecker(project_dir=project_dir)
    try:
        report_data = checker.inspect()
    except Exception as exc:
        print(f"Error inspecting script quality: {exc}", file=sys.stderr)
        return 1

    d = report_data["duration_info"]
    print(f"\nQuality Inspection Summary: {args.project_id}")
    print("-" * 65)
    print(f"  Title:              {report_data['title']}")
    print(f"  Total Words:        {d['total_words']} words")
    print(f"  Estimated Duration: {d['estimated_duration_seconds']:.1f}s ({d['estimated_duration_seconds']/60:.1f} min)")
    print(f"  Hook Violations:    {len(report_data['hook_violations'])}")
    print(f"  Cliché / AI Slop:   {len(report_data['cliche_matches'])} occurrences")
    print(f"  Repeated N-grams:   {len(report_data['repeated_ngrams'])}")
    print(f"  Missing Visuals:    {len(report_data['missing_visuals'])}")
    print(f"  Fact-Check Claims:  {len(report_data['verify_claims'])} items ([verify])")
    print(f"  Unverified Specs:   {len(report_data['unverified_specs'])} items")
    print("-" * 65)
    print(f"Detailed Markdown Report saved to: {checker.report_file}\n")
    return 0



# =========================================================================
# STAGE 3 CLI COMMANDS: AUDIO SYNTHESIS, TIMELINE, PREVIEW, & VOICE
# =========================================================================

def cmd_audio_synth(args: argparse.Namespace) -> int:
    """Synthesize audio groups into 48kHz PCM WAVs and align sentence segments (3.1-3.3, 3.7)."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    from app.services.longform.voice_service import VoiceService
    from app.services.longform.tts_adapter import TTSConfigurationError, TTSAdapterError
    service = VoiceService(project_dir=project_dir)

    print(f"Starting audio synthesis for project: {args.project_id}")
    sys.stdout.flush()

    try:
        manifest = service.synthesize_all(
            provider=args.provider,
            concurrency=args.concurrency,
            fake_delay=args.fake_delay,
            fail_at_group=getattr(args, "fail_at", None),
        )
    except TTSConfigurationError as exc:
        print(f"\n[CONFIGURATION ERROR] {exc}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[AUDIO SYNTH INTERRUPTED] KeyboardInterrupt received. Safely halting. Incomplete groups remain pending for next resume.\n", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error synthesizing audio: {exc}", file=sys.stderr)
        return 1

    print(f"\nAudio Synthesis Completed Successfully for Project: {args.project_id}")
    print("-" * 65)
    status = service.get_audio_status()
    fake_note = " [FAKE MOCK AUDIO]" if status.get("is_fake") else ""
    print(f"  Status:                  {status['completed_groups']}/{status['total_groups']} groups completed{fake_note}")
    print(f"  Total Audio Duration:    {status['total_duration_seconds']:.1f}s ({status['total_duration_seconds']/60:.1f} min)")
    print(f"  Alignment Breakdown:     {status['alignment_method_counts']}")
    print("-" * 65)
    print(f"Run 'python cli_longform.py audio preview {args.project_id} --chapter 1' to verify synchronization in VLC.\n")
    return 0


def cmd_audio_status(args: argparse.Namespace) -> int:
    """Show detailed audio group completion and alignment method diagnostics (3.7, R2)."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    from app.services.longform.voice_service import VoiceService
    service = VoiceService(project_dir=project_dir)
    status = service.get_audio_status()

    fake_badge = " [FAKE MOCK AUDIO]" if status.get("is_fake") else ""
    print(f"\nAudio Synthesis Status: {args.project_id}{fake_badge}")
    print("=" * 65)
    print(f"  Total Groups:       {status['total_groups']}")
    print(f"  Completed Groups:   {status['completed_groups']}/{status['total_groups']}")
    print(f"  Total Duration:     {status['total_duration_seconds']:.1f} seconds ({status['total_duration_seconds']/60:.1f} minutes)")
    print(f"  Word Match Ratio:   min={status.get('word_match_min', 0.0):.1%}, avg={status.get('word_match_avg', 0.0):.1%}")
    m_counts = status['alignment_method_counts']
    print(f"  Alignment Methods:  word_cue={m_counts.get('word_cue', 0)}, whisper={m_counts.get('whisper', 0)}, proportional={m_counts.get('proportional', 0)}")
    print("  Chapters:")
    if status["chapters"]:
        for ch in status["chapters"]:
            ch_methods = ", ".join(f"{k}:{v}" for k, v in ch["alignment_methods"].items() if v > 0) or "none"
            print(f"    - Ch {ch['chapter_index']}: {ch['chapter_title']}")
            print(f"      Groups: {ch['completed_groups']}/{ch['total_groups']} | Duration: {ch['duration_seconds']:.1f}s | Match: min={ch.get('word_match_min', 0.0):.1%}, avg={ch.get('word_match_avg', 0.0):.1%} | Alignments: [{ch_methods}]")
    else:
        print("    No chapters found.")
    print("=" * 65 + "\n")
    return 0


def cmd_audio_preview(args: argparse.Namespace) -> int:
    """Generate chapter_NN_preview.wav, .srt, and .mp4 with burned subtitles in export/preview/ (3.6, R1)."""
    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    from app.services.longform.voice_service import VoiceService
    service = VoiceService(project_dir=project_dir)
    try:
        preview_res = service.generate_preview(chapter_index=args.chapter)
        if isinstance(preview_res, (list, tuple)) and len(preview_res) >= 3:
            wav_path, srt_path, mp4_path = preview_res[0], preview_res[1], preview_res[2]
        else:
            wav_path, srt_path = preview_res[0], preview_res[1]
            mp4_path = None
    except Exception as exc:
        print(f"Error generating preview: {exc}", file=sys.stderr)
        return 1

    print(f"\nAudio Preview Generated Successfully for Chapter {args.chapter}:")
    print("-" * 65)
    print(f"  Preview WAV:  {wav_path}")
    print(f"  Preview SRT:  {srt_path}")
    if mp4_path:
        print(f"  Preview MP4:  {mp4_path}")
    print("-" * 65)
    print("Open the MP4 or WAV along with the SRT subtitle file to verify audio and subtitle sync.\n")
    return 0


def cmd_project_set_voice(args: argparse.Namespace) -> int:
    """Change project narrator voice, invalidating existing audio groups (3.5, 3.7)."""
    voice_name = args.voice_name or getattr(args, "voice_flag", None)
    if not voice_name:
        print("Error: Voice name must be specified (e.g. 'python cli_longform.py project set-voice my_proj en-US-ChristopherNeural-Male')", file=sys.stderr)
        return 1

    project_dir = get_project_dir(args.project_id)
    if not project_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {project_dir}", file=sys.stderr)
        return 1

    from app.services.longform.voice_service import VoiceService
    service = VoiceService(project_dir=project_dir)
    try:
        manifest = service.set_voice(voice_name=voice_name, voice_rate=args.rate)
    except Exception as exc:
        print(f"Error setting voice: {exc}", file=sys.stderr)
        return 1

    print(f"\nNarrator Voice Updated for Project: {args.project_id}")
    print("-" * 65)
    print(f"  New Voice:   {voice_name}")
    print(f"  Speech Rate: {args.rate}")
    print("  Note: All existing audio groups and chapter renders have been marked STALE.")
    print("-" * 65)
    print(f"Run 'python cli_longform.py audio synth {args.project_id}' to synthesize audio with the new voice.\n")
    return 0


def cmd_voice_list(args: argparse.Namespace) -> int:
    """List available Edge TTS voices from the local catalog (3.6)."""
    from app.services.longform.voice_service import list_available_voices
    voices = list_available_voices(language_prefix=args.language)
    lang_hint = f" matching '{args.language}'" if args.language else ""
    print(f"\nAvailable Edge TTS Voices{lang_hint} ({len(voices)} voices):")
    print("-" * 65)
    for v in voices:
        print(f"  - {v}")
    print("-" * 65 + "\n")
    return 0


def cmd_voice_test(args: argparse.Namespace) -> int:
    """Synthesize a short sample phrase to test a voice (3.6)."""
    import tempfile
    from app.services import voice as upstream_voice
    from app.services.longform.audio_util import convert_to_pcm_wav

    clean_text = args.text.strip()
    if not clean_text:
        print("Error: --text cannot be empty", file=sys.stderr)
        return 1

    tmp_mp3 = Path(tempfile.mktemp(suffix=".mp3"))
    tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
    print(f"Testing voice '{args.voice}' with text: \"{clean_text}\"...")
    try:
        sub = upstream_voice.tts(
            text=clean_text,
            voice_name=args.voice,
            voice_rate=1.0,
            voice_file=str(tmp_mp3),
        )
        if not tmp_mp3.is_file() or tmp_mp3.stat().st_size == 0:
            print("Error: Upstream TTS produced no audio.", file=sys.stderr)
            return 1

        convert_to_pcm_wav(tmp_mp3, tmp_wav)
        print(f"SUCCESS: Voice sample synthesized to: {tmp_wav}")
    except Exception as exc:
        print(f"Error testing voice: {exc}", file=sys.stderr)
        return 1
    finally:
        if tmp_mp3.exists():
            tmp_mp3.unlink(missing_ok=True)
    return 0


# ---------------------------------------------------------------------------
# Visual Commands (Stage 4)
# ---------------------------------------------------------------------------

def cmd_visual_fetch(args: argparse.Namespace) -> int:
    """Acquire visual assets for project shots (4.1, 4.5)."""
    from app.services.longform.visual.base import VisualConfigurationError, VisualError
    from app.services.longform.visual.service import VisualService

    proj_dir = get_project_dir(args.project_id)
    if not proj_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {proj_dir}", file=sys.stderr)
        return 1

    try:
        with ProjectLock(proj_dir):
            manifest = load_manifest(proj_dir)
            service = VisualService(proj_dir)

            prov_display = args.provider or "preset/default"
            print(f"\n[Visual Fetch] Project: {args.project_id} | Provider: {prov_display} | Fallback Allowed: {args.allow_fallback}")
            print("-" * 70)

            manifest = service.fetch_all(
                manifest=manifest,
                provider_name=args.provider,
                allow_fallback=args.allow_fallback,
            )
            save_manifest(manifest, proj_dir)

            # Summarize fetched assets
            total_shots = sum(len(ch.shots) for ch in manifest.chapters)
            fake_count = sum(1 for ch in manifest.chapters for s in ch.shots if s.is_fake)
            print("-" * 70)
            print(f"SUCCESS: Visual assets acquired for {total_shots} shots (Fake: {fake_count})")
            if fake_count > 0:
                print("Notice: [FAKE] visual assets were generated for offline development/testing.")
            return 0

    except VisualConfigurationError as conf_err:
        print(f"\nCONFIGURATION ERROR: {conf_err}", file=sys.stderr)
        print("Tip: pass '--provider fake' or '--allow-fallback' for keyless testing.", file=sys.stderr)
        return 1
    except ProjectLockedError:
        print(f"Error: Project '{args.project_id}' is locked by another process.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error during visual fetch: {exc}", file=sys.stderr)
        return 1


def cmd_visual_build(args: argparse.Namespace) -> int:
    """Render shot clips with uniform encoding parameters (4.3, 4.5)."""
    from app.services.longform.visual.service import VisualService

    proj_dir = get_project_dir(args.project_id)
    if not proj_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {proj_dir}", file=sys.stderr)
        return 1

    try:
        with ProjectLock(proj_dir):
            manifest = load_manifest(proj_dir)
            service = VisualService(proj_dir)

            print(f"\n[Visual Build] Rendering shot clips for project '{args.project_id}'...")
            print("-" * 70)
            t0 = time.perf_counter()
            manifest, stats = service.build_all(manifest)
            save_manifest(manifest, proj_dir)
            elapsed = time.perf_counter() - t0

            print("-" * 70)
            print(f"SUCCESS: Shot clips built: {stats['built_count']} | Skipped: {stats['skipped_count']}")
            print(f"Total Time: {elapsed:.2f}s | Average Speed: {stats['average_seconds_per_clip']:.2f}s/clip\n")
            return 0

    except ProjectLockedError:
        print(f"Error: Project '{args.project_id}' is locked by another process.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error during visual build: {exc}", file=sys.stderr)
        return 1


def cmd_visual_status(args: argparse.Namespace) -> int:
    """Show detailed visual status and frame drift analysis (4.5)."""
    proj_dir = get_project_dir(args.project_id)
    if not proj_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {proj_dir}", file=sys.stderr)
        return 1

    manifest = load_manifest(proj_dir)
    fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25

    print(f"\nVisual Status for Project: {manifest.project_id} ({manifest.title})")
    print("=" * 80)
    print(f"{'Ch':<4} {'Shots':<7} {'Completed':<10} {'Pending':<8} {'Stale':<7} {'Visual Dur':<12} {'Audio Dur':<12} {'Frames':<8} {'Drift'}")
    print("-" * 80)

    total_shots = 0
    total_completed = 0
    total_pending = 0
    total_stale = 0
    total_frames = 0
    total_fake = 0

    for ch in manifest.chapters:
        num_shots = len(ch.shots)
        c_done = sum(1 for s in ch.shots if s.status == ItemStatus.completed)
        c_pend = sum(1 for s in ch.shots if s.status == ItemStatus.pending)
        c_stale = sum(1 for s in ch.shots if s.status == ItemStatus.stale)
        c_fake = sum(1 for s in ch.shots if s.is_fake)
        ch_frames = sum(s.target_frames for s in ch.shots)
        v_dur = ch.visual_duration_seconds or 0.0
        a_dur = ch.audio_duration_seconds or 0.0

        expected_frames = int(round(a_dur * fps))
        drift_frames = ch_frames - expected_frames if a_dur > 0 else 0
        drift_str = f"{drift_frames:+d}f" if a_dur > 0 else "N/A"

        total_shots += num_shots
        total_completed += c_done
        total_pending += c_pend
        total_stale += c_stale
        total_frames += ch_frames
        total_fake += c_fake

        print(
            f"{ch.chapter_index:<4} {num_shots:<7} {c_done:<10} {c_pend:<8} {c_stale:<7} "
            f"{v_dur:>8.2f}s    {a_dur:>8.2f}s    {ch_frames:<8} {drift_str}"
        )

    print("-" * 80)
    print(
        f"TOTAL: {total_shots} shots ({total_completed} done, {total_pending} pending, {total_stale} stale) "
        f"| Frames: {total_frames} | Synthetic Fake: {total_fake}"
    )
    if total_fake > 0:
        print("Note: [FAKE] watermark badges are embedded in synthetic test clips.")
    print("=" * 80 + "\n")
    return 0


def cmd_visual_library_add(args: argparse.Namespace) -> int:
    """Add external folder assets into local media library (4.1, 4.5)."""
    from app.services.longform.visual.local import LocalLibraryManager

    proj_dir = get_project_dir(args.project_id)
    if not proj_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {proj_dir}", file=sys.stderr)
        return 1

    try:
        with ProjectLock(proj_dir):
            mgr = LocalLibraryManager(proj_dir)
            print(f"\nAdding media from '{args.folder}' into project library...")
            added = mgr.add_from_folder(args.folder)
            print(f"SUCCESS: Copied {len(added)} files into assets/visual/library/ (Rule K7 compliant):")
            for item in added:
                print(f"  - {item['filename']} [{item['asset_type']}] ({item['size_bytes']} bytes, sha256: {item['content_hash'][:8]}...)")
            print()
            return 0
    except Exception as exc:
        print(f"Error adding to visual library: {exc}", file=sys.stderr)
        return 1


def cmd_visual_contact_sheet(args: argparse.Namespace) -> int:
    """Generate visual contact sheet image (4.4, 4.5)."""
    from app.services.longform.visual.contact_sheet import generate_chapter_contact_sheet

    proj_dir = get_project_dir(args.project_id)
    if not proj_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {proj_dir}", file=sys.stderr)
        return 1

    manifest = load_manifest(proj_dir)
    ch = next((c for c in manifest.chapters if c.chapter_index == args.chapter), None)
    if not ch:
        print(f"Error: Chapter {args.chapter} not found in project.", file=sys.stderr)
        return 1

    try:
        print(f"\nGenerating contact sheet for Chapter {args.chapter}...")
        out_img = generate_chapter_contact_sheet(
            chapter=ch,
            project_dir=proj_dir,
            columns=args.columns,
        )
        print(f"SUCCESS: Contact sheet saved to: {out_img}\n")
        return 0
    except Exception as exc:
        print(f"Error generating contact sheet: {exc}", file=sys.stderr)
        return 1


def cmd_visual_preview(args: argparse.Namespace) -> int:
    """Concatenate chapter shot clips with chapter audio into preview MP4 (4.4, 4.5)."""
    from app.services.longform.visual.service import VisualService

    proj_dir = get_project_dir(args.project_id)
    if not proj_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {proj_dir}", file=sys.stderr)
        return 1

    manifest = load_manifest(proj_dir)
    service = VisualService(proj_dir)

    try:
        print(f"\nGenerating visual preview for Chapter {args.chapter}...")
        out_mp4 = service.generate_preview(manifest, args.chapter)
        print(f"SUCCESS: Chapter visual preview created at: {out_mp4}\n")
        return 0
    except Exception as exc:
        print(f"Error generating visual preview: {exc}", file=sys.stderr)
        return 1


def cmd_visual_replace(args: argparse.Namespace) -> int:
    """Replace asset for a specific shot (4.4, 4.5)."""
    from app.services.longform.visual.service import VisualService

    proj_dir = get_project_dir(args.project_id)
    if not proj_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {proj_dir}", file=sys.stderr)
        return 1

    try:
        with ProjectLock(proj_dir):
            manifest = load_manifest(proj_dir)
            service = VisualService(proj_dir)

            shot = service.replace_shot(
                manifest=manifest,
                shot_id=args.shot,
                file_path=args.file,
                candidate_index=args.candidate,
                provider_name=args.provider,
                prompt=args.prompt,
            )
            save_manifest(manifest, proj_dir)
            print(f"\nSUCCESS: Shot '{args.shot}' asset replaced.")
            print(f"New asset path: {shot.asset_path}")
            print(f"Source: {shot.actual_source} | Status: {shot.status.value}")
            print("Note: Run 'visual build' to re-render the clip.\n")
            return 0
    except Exception as exc:
        print(f"Error replacing shot: {exc}", file=sys.stderr)
        return 1


def cmd_visual_lock(args: argparse.Namespace) -> int:
    """Lock shot against automated re-fetch (4.4, 4.5)."""
    from app.services.longform.visual.service import VisualService

    proj_dir = get_project_dir(args.project_id)
    if not proj_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {proj_dir}", file=sys.stderr)
        return 1

    try:
        with ProjectLock(proj_dir):
            manifest = load_manifest(proj_dir)
            service = VisualService(proj_dir)
            shot = service.set_lock(manifest, args.shot, locked=True)
            save_manifest(manifest, proj_dir)
            print(f"\nSUCCESS: Shot '{shot.shot_id}' is now LOCKED [protected from re-fetch].\n")
            return 0
    except Exception as exc:
        print(f"Error locking shot: {exc}", file=sys.stderr)
        return 1


def cmd_visual_unlock(args: argparse.Namespace) -> int:
    """Unlock shot to allow automated re-fetch (4.4, 4.5)."""
    from app.services.longform.visual.service import VisualService

    proj_dir = get_project_dir(args.project_id)
    if not proj_dir.is_dir():
        print(f"Error: Project '{args.project_id}' not found at {proj_dir}", file=sys.stderr)
        return 1

    try:
        with ProjectLock(proj_dir):
            manifest = load_manifest(proj_dir)
            service = VisualService(proj_dir)
            shot = service.set_lock(manifest, args.shot, locked=False)
            save_manifest(manifest, proj_dir)
            print(f"\nSUCCESS: Shot '{shot.shot_id}' is now UNLOCKED.\n")
            return 0
    except Exception as exc:
        print(f"Error unlocking shot: {exc}", file=sys.stderr)
        return 1


def re_slugify(text: str) -> str:
    """Create a URL/folder-safe slug from title."""
    import re
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "_", s)
    return s[:32] or "project_1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli_longform.py",
        description="EasyVideoContent Longform Video Generation Tool",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. presets
    p_presets = subparsers.add_parser("presets", help="Manage and validate presets")
    preset_subs = p_presets.add_subparsers(dest="preset_command", required=True)
    p_p_list = preset_subs.add_parser("list", help="List available presets in hierarchy tree")
    p_p_validate = preset_subs.add_parser("validate", help="Validate a preset JSON file")
    p_p_validate.add_argument("file", help="Path to preset JSON file")

    # 2. project
    p_proj = subparsers.add_parser("project", help="Manage longform projects")
    proj_subs = p_proj.add_subparsers(dest="project_command", required=True)

    # project create
    p_create = proj_subs.add_parser("create", help="Create a new longform project")
    p_create.add_argument("--title", required=True, help="Title of the project")
    p_create.add_argument("--preset", required=True, help="Preset ID to use")
    p_create.add_argument("--script", help="Path to Markdown script file (optional if --topic is provided)")
    p_create.add_argument("--topic", help="Video topic for automated AI script generation")
    p_create.add_argument("--id", help="Optional custom project ID")
    p_create.add_argument("--aspect-ratio", default="16:9", help="Aspect ratio (default 16:9)")
    p_create.add_argument("--focus", help="Sub-niche specific focus text (e.g. '1960s American drag racing')")

    # project show
    p_show = proj_subs.add_parser("show", help="Show project summary")
    p_show.add_argument("project_id", help="Project ID")

    # project plan
    p_plan = proj_subs.add_parser("plan", help="Compute execution plan")
    p_plan.add_argument("project_id", help="Project ID")

    # project update-script
    p_upd = proj_subs.add_parser("update-script", help="Re-import script into project and calculate diff")
    p_upd.add_argument("project_id", help="Project ID")
    p_upd.add_argument("--script", required=True, help="Path to new Markdown script file")

    # project refresh-preset
    p_ref = proj_subs.add_parser("refresh-preset", help="Refresh preset snapshot from disk")
    p_ref.add_argument("project_id", help="Project ID")

    # project set-voice (3.5, 3.7)
    p_set_voice = proj_subs.add_parser("set-voice", help="Change project default voice (marks audio stale)")
    p_set_voice.add_argument("project_id", help="Project ID")
    p_set_voice.add_argument("voice_name", nargs="?", default=None, help="New voice name (e.g. 'en-US-ChristopherNeural-Male')")
    p_set_voice.add_argument("--voice", dest="voice_flag", default=None, help="New voice name (flag form)")
    p_set_voice.add_argument("--rate", type=float, default=1.0, help="Speech rate multiplier (default 1.0)")

    # project dev-simulate
    p_sim = proj_subs.add_parser("dev-simulate", help="Simulate pipeline execution for testing")
    p_sim.add_argument("project_id", help="Project ID")
    p_sim.add_argument("--delay", type=float, default=0.0, help="Artificial delay per unit (seconds)")
    p_sim.add_argument("--fail-at", help="Simulate failure at specific unit_id")

    # 3. script (Stage 2)
    p_script = subparsers.add_parser("script", help="Manage AI longform script generation")
    script_subs = p_script.add_subparsers(dest="script_command", required=True)

    # script outline
    p_s_outline = script_subs.add_parser("outline", help="Generate structured outline with candidate titles")
    p_s_outline.add_argument("project_id", help="Project ID")
    p_s_outline.add_argument("--topic", required=True, help="Video topic")
    p_s_outline.add_argument("--minutes", type=float, default=12.0, help="Target duration in minutes (default: 12)")
    p_s_outline.add_argument("--provider", default=None, help="LLM provider (defaults to upstream config.toml; pass 'fake' for offline test mock)")
    p_s_outline.add_argument("--model", help="Optional model name override")

    # script write
    p_s_write = script_subs.add_parser("write", help="Draft chapters sequentially with context and length control")
    p_s_write.add_argument("project_id", help="Project ID")
    p_s_write.add_argument("--provider", default=None, help="LLM provider (defaults to upstream config.toml; pass 'fake' for offline test mock)")
    p_s_write.add_argument("--model", help="Optional model name override")
    p_s_write.add_argument("--fake-delay", type=float, default=0.0, help="Artificial delay per chapter generation step in seconds (useful for testing Ctrl+C)")
    p_s_write.add_argument("--fail-at", type=int, help="Simulate failure at chapter index for resume testing")

    # script status
    p_s_status = script_subs.add_parser("status", help="Show status of outline, chapters, and word counts")
    p_s_status.add_argument("project_id", help="Project ID")

    # script quality
    p_s_quality = script_subs.add_parser("quality", help="Run script quality and fact-checking inspection")
    p_s_quality.add_argument("project_id", help="Project ID")

    # 4. audio (Stage 3)
    p_audio = subparsers.add_parser("audio", help="Manage TTS voice synthesis, alignment, and chapter audio")
    audio_subs = p_audio.add_subparsers(dest="audio_command", required=True)

    # audio synth
    p_a_synth = audio_subs.add_parser("synth", help="Synthesize paragraph audio into 48kHz PCM WAVs with alignment")
    p_a_synth.add_argument("project_id", help="Project ID")
    p_a_synth.add_argument("--provider", default=None, help="TTS provider ('edge' or 'fake')")
    p_a_synth.add_argument("--concurrency", type=int, default=2, help="Parallel worker threads (default: 2)")
    p_a_synth.add_argument("--fake-delay", type=float, default=0.0, help="Simulate artificial delay per group in seconds (for Ctrl+C test)")
    p_a_synth.add_argument("--fail-at", help="Simulate failure at unit ID (e.g. 'ch1_g2_audio')")

    # audio status
    p_a_status = audio_subs.add_parser("status", help="Show audio synthesis completion and alignment diagnostics")
    p_a_status.add_argument("project_id", help="Project ID")

    # audio preview
    p_a_preview = audio_subs.add_parser("preview", help="Generate preview WAV and SRT subtitle for VLC verification")
    p_a_preview.add_argument("project_id", help="Project ID")
    p_a_preview.add_argument("--chapter", type=int, required=True, help="Chapter index to preview")

    # 5. voice (Stage 3)
    p_voice = subparsers.add_parser("voice", help="Query available voices and test samples")
    voice_subs = p_voice.add_subparsers(dest="voice_command", required=True)

    # voice list
    p_v_list = voice_subs.add_parser("list", help="List available Edge TTS voices from catalog")
    p_v_list.add_argument("--language", help="Optional language code filter (e.g. 'en-US', 'id-ID')")

    # voice test
    p_v_test = voice_subs.add_parser("test", help="Test a voice with a sample text phrase")
    p_v_test.add_argument("--voice", required=True, help="Voice name to test")
    p_v_test.add_argument("--text", default="Hello! This is a test of Edge TTS voice synthesis for long video production.", help="Sample phrase to speak")

    # 6. visual (Stage 4)
    p_visual = subparsers.add_parser("visual", help="Manage visual assets, stock footage, AI images, Ken Burns clips, and inspection")
    visual_subs = p_visual.add_subparsers(dest="visual_command", required=True)

    # visual fetch
    p_v_fetch = visual_subs.add_parser("fetch", help="Search and acquire visual assets (stock, AI, local, fake)")
    p_v_fetch.add_argument("project_id", help="Project ID")
    p_v_fetch.add_argument("--provider", default=None, help="Visual provider ('fake', 'pexels', 'pixabay', 'ai_image', 'local')")
    p_v_fetch.add_argument("--allow-fallback", action="store_true", help="Allow fallback to fake provider if credentials missing")

    # visual build
    p_v_build = visual_subs.add_parser("build", help="Render shot clips (Ken Burns or trimmed stock video) with uniform parameters")
    p_v_build.add_argument("project_id", help="Project ID")

    # visual status
    p_v_status = visual_subs.add_parser("status", help="Show visual asset acquisition and render status across all chapters")
    p_v_status.add_argument("project_id", help="Project ID")

    # visual library
    p_v_lib = visual_subs.add_parser("library", help="Local media library operations")
    lib_subs = p_v_lib.add_subparsers(dest="library_command", required=True)
    p_lib_add = lib_subs.add_parser("add", help="Add external media folder to project local library (Rule K7)")
    p_lib_add.add_argument("project_id", help="Project ID")
    p_lib_add.add_argument("--folder", required=True, help="Folder containing images or videos to copy into library")

    # visual contact-sheet
    p_v_cs = visual_subs.add_parser("contact-sheet", help="Generate grid image contact sheet for chapter visual review")
    p_v_cs.add_argument("project_id", help="Project ID")
    p_v_cs.add_argument("--chapter", type=int, required=True, help="Chapter index")
    p_v_cs.add_argument("--columns", type=int, default=4, help="Number of thumbnail columns (default: 4)")

    # visual preview
    p_v_prev = visual_subs.add_parser("preview", help="Render chapter visual preview video combining shots with audio")
    p_v_prev.add_argument("project_id", help="Project ID")
    p_v_prev.add_argument("--chapter", type=int, required=True, help="Chapter index")

    # visual replace
    p_v_replace = visual_subs.add_parser("replace", help="Replace visual asset for a specific shot")
    p_v_replace.add_argument("project_id", help="Project ID")
    p_v_replace.add_argument("--shot", required=True, help="Shot ID to replace (e.g. 'ch01_shot002')")
    p_v_replace.add_argument("--file", help="Path to local replacement image or video file")
    p_v_replace.add_argument("--candidate", type=int, help="Candidate index (0, 1, or 2) to activate")
    p_v_replace.add_argument("--provider", help="Provider to re-fetch with")
    p_v_replace.add_argument("--prompt", help="Custom prompt or search query")

    # visual lock
    p_v_lock = visual_subs.add_parser("lock", help="Lock a shot to prevent automated re-fetch or replacement")
    p_v_lock.add_argument("project_id", help="Project ID")
    p_v_lock.add_argument("--shot", required=True, help="Shot ID to lock")

    # visual unlock
    p_v_unlock = visual_subs.add_parser("unlock", help="Unlock a shot to allow re-fetch and updates")
    p_v_unlock.add_argument("project_id", help="Project ID")
    p_v_unlock.add_argument("--shot", required=True, help="Shot ID to unlock")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "presets":
        if args.preset_command == "list":
            return cmd_presets_list(args)
        elif args.preset_command == "validate":
            return cmd_presets_validate(args)
    elif args.command == "project":
        if args.project_command == "create":
            return cmd_project_create(args)
        elif args.project_command == "show":
            return cmd_project_show(args)
        elif args.project_command == "plan":
            return cmd_project_plan(args)
        elif args.project_command == "update-script":
            return cmd_project_update_script(args)
        elif args.project_command == "refresh-preset":
            return cmd_project_refresh_preset(args)
        elif args.project_command == "dev-simulate":
            return cmd_project_dev_simulate(args)
        elif args.project_command == "set-voice":
            return cmd_project_set_voice(args)
    elif args.command == "script":
        if args.script_command == "outline":
            return cmd_script_outline(args)
        elif args.script_command == "write":
            return cmd_script_write(args)
        elif args.script_command == "status":
            return cmd_script_status(args)
        elif args.script_command == "quality":
            return cmd_script_quality(args)
    elif args.command == "audio":
        if args.audio_command == "synth":
            return cmd_audio_synth(args)
        elif args.audio_command == "status":
            return cmd_audio_status(args)
        elif args.audio_command == "preview":
            return cmd_audio_preview(args)
    elif args.command == "voice":
        if args.voice_command == "list":
            return cmd_voice_list(args)
        elif args.voice_command == "test":
            return cmd_voice_test(args)
    elif args.command == "visual":
        if args.visual_command == "fetch":
            return cmd_visual_fetch(args)
        elif args.visual_command == "build":
            return cmd_visual_build(args)
        elif args.visual_command == "status":
            return cmd_visual_status(args)
        elif args.visual_command == "library":
            if args.library_command == "add":
                return cmd_visual_library_add(args)
        elif args.visual_command == "contact-sheet":
            return cmd_visual_contact_sheet(args)
        elif args.visual_command == "preview":
            return cmd_visual_preview(args)
        elif args.visual_command == "replace":
            return cmd_visual_replace(args)
        elif args.visual_command == "lock":
            return cmd_visual_lock(args)
        elif args.visual_command == "unlock":
            return cmd_visual_unlock(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
