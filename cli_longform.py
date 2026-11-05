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
    print(f"Total Work Units: {plan.total_units}")
    print(f"  - Completed: {plan.completed_units}")
    print(f"  - Stale:     {plan.stale_units}")
    print(f"  - Pending:   {plan.pending_units}")

    if plan.is_fully_completed:
        print("Status: Project is fully completed and up to date.")
    elif plan.has_work_remaining:
        print(f"Status: Incremental update required ({plan.stale_units} stale, {plan.pending_units} pending)")

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

    from app.services.longform.script_generator import ScriptGenerator
    generator = ScriptGenerator(project_dir=project_dir, provider=args.provider, model=args.model)

    try:
        outline = generator.generate_outline(
            topic=args.topic,
            target_duration_minutes=args.minutes,
        )
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

    from app.services.longform.script_generator import ScriptGenerator
    generator = ScriptGenerator(project_dir=project_dir, provider=args.provider, model=args.model)

    try:
        full_script = generator.write_chapters(
            fail_at_chapter=args.fail_at,
        )
    except Exception as exc:
        print(f"Error writing script chapters: {exc}", file=sys.stderr)
        return 1

    print(f"\nScript Writing Completed Successfully for Project: {args.project_id}")
    print("-" * 65)
    print(f"  Saved Full Script: {full_script}")
    print("  Manifest chapters and groups updated successfully via single pipeline path.")
    print("-" * 65)
    print(f"Run 'python cli_longform.py script quality {args.project_id}' to inspect script quality and facts.\n")
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

    print(f"\nScript Generation Status: {args.project_id}")
    print("=" * 65)
    print(f"  Outline Created:     {'Yes' if status['has_outline'] else 'No'}")
    print(f"  Full Script Created: {'Yes' if status['full_script_exists'] else 'No'}")
    print(f"  LLM Cache Entries:   {status['cache_entries']}")
    print(f"  Chapters:")
    if status["chapters"]:
        for ch in status["chapters"]:
            rev_tag = " (revised for length)" if ch["revised"] else ""
            print(f"    - Ch {ch['chapter_index']}: {ch['title']} [{ch['status']}] - {ch['word_count']}/{ch['target_words']} words{rev_tag}")
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
    p_s_outline.add_argument("--provider", default="fake", help="LLM provider ('fake' or upstream provider)")
    p_s_outline.add_argument("--model", help="Optional model name override")

    # script write
    p_s_write = script_subs.add_parser("write", help="Draft chapters sequentially with context and length control")
    p_s_write.add_argument("project_id", help="Project ID")
    p_s_write.add_argument("--provider", default="fake", help="LLM provider ('fake' or upstream provider)")
    p_s_write.add_argument("--model", help="Optional model name override")
    p_s_write.add_argument("--fail-at", type=int, help="Simulate failure at chapter index for resume testing")

    # script status
    p_s_status = script_subs.add_parser("status", help="Show status of outline, chapters, and word counts")
    p_s_status.add_argument("project_id", help="Project ID")

    # script quality
    p_s_quality = script_subs.add_parser("quality", help="Run script quality and fact-checking inspection")
    p_s_quality.add_argument("project_id", help="Project ID")

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
    elif args.command == "script":
        if args.script_command == "outline":
            return cmd_script_outline(args)
        elif args.script_command == "write":
            return cmd_script_write(args)
        elif args.script_command == "status":
            return cmd_script_status(args)
        elif args.script_command == "quality":
            return cmd_script_quality(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
