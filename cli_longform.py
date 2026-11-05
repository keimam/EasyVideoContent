#!/usr/bin/env python
"""CLI for Longform Video Generation (Stage 1 & Addendum 1.5).

A standalone CLI providing multi-tier preset management (B1), project scaffolding,
planning, resume validation, script updates (A4), and simulated pipeline execution.
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
    """Create a new longform project from a script file with optional focus (B3)."""
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
    duration_stats = estimate_script_duration(manifest.chapters, preset.words_per_minute)

    print(f"\nProject created successfully!")
    print(f"  - ID:                 {manifest.project_id}")
    print(f"  - Title:              {manifest.title}")
    print(f"  - Directory:          {project_dir}")
    print(f"  - Preset:             {manifest.preset_id} ({preset.name})")
    if manifest.preset_inheritance_chain:
        print(f"  - Preset Chain:       {' -> '.join(manifest.preset_inheritance_chain)}")
    if manifest.sub_niche_focus:
        print(f"  - Sub-Niche Focus:    \"{manifest.sub_niche_focus}\"")
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
    print()
    return 0


def cmd_project_show(args: argparse.Namespace) -> int:
    """Display information about an existing project."""
    project_dir = get_project_dir(args.project_id)
    try:
        manifest = load_manifest(project_dir)
    except Exception as exc:
        print(f"Error loading project: {exc}", file=sys.stderr)
        return 1

    wpm = manifest.preset_snapshot.get("words_per_minute", 140)
    duration_stats = estimate_script_duration(manifest.chapters, wpm)

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
        groups_cnt = len(ch.tts_groups)
        segs_cnt = sum(len(g.segments) for g in ch.tts_groups)
        ch_status = ch.status.value
        print(f"    - Ch {ch.chapter_index}: {ch.chapter_title} [{ch_status}] ({groups_cnt} groups, {segs_cnt} segs)")
    print("=" * 65 + "\n")
    return 0


def cmd_project_plan(args: argparse.Namespace) -> int:
    """Compute and display execution plan for a project."""
    project_dir = get_project_dir(args.project_id)
    try:
        manifest = load_manifest(project_dir)
    except Exception as exc:
        print(f"Error loading project: {exc}", file=sys.stderr)
        return 1

    plan = compute_plan(manifest, project_dir)

    print(f"\n{plan.summary()}\n")
    print("-" * 90)
    print(f"{'Unit ID':<22} | {'Type':<16} | {'Status':<10} | {'Reason'}")
    print("-" * 90)
    for u in plan.all_units:
        print(f"{u.unit_id:<22} | {u.unit_type:<16} | {u.status.value:<10} | {u.reason}")
    print("-" * 90 + "\n")
    return 0


def cmd_project_update_script(args: argparse.Namespace) -> int:
    """Re-import an edited script into existing project with diff summary (A4)."""
    project_dir = get_project_dir(args.project_id)
    script_path = Path(args.script)
    if not script_path.is_file():
        print(f"Error: Script file not found: {script_path}", file=sys.stderr)
        return 1

    try:
        with open(script_path, "r", encoding="utf-8-sig") as fp:
            script_text = fp.read()
    except Exception as exc:
        print(f"Error reading script: {exc}", file=sys.stderr)
        return 1

    try:
        stats = update_project_script(project_dir, script_text)
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
    """Refresh project preset snapshot from latest disk definitions (B2)."""
    project_dir = get_project_dir(args.project_id)
    try:
        manifest, details = refresh_project_preset(project_dir)
    except Exception as exc:
        print(f"Error refreshing preset: {exc}", file=sys.stderr)
        return 1

    print(f"\nPreset Refreshed for Project: {args.project_id}")
    print("-" * 60)
    print(f"  Preset ID:       {details['preset_id']}")
    print(f"  New Chain:       {' -> '.join(details['new_chain'])}")
    print(f"  Snapshot Change: {'YES (Updated)' if details['snapshot_updated'] else 'NO (Identical)'}")
    print("-" * 60)
    print(f"Run 'python cli_longform.py project plan {args.project_id}' to inspect affected units.\n")
    return 0


def cmd_project_dev_simulate(args: argparse.Namespace) -> int:
    """Simulate pipeline execution with interruption handling (A3)."""
    project_dir = get_project_dir(args.project_id)
    try:
        manifest = load_manifest(project_dir)
    except Exception as exc:
        print(f"Error loading project: {exc}", file=sys.stderr)
        return 1

    delay = max(0.0, args.delay)
    fail_at = args.fail_at

    print("\n" + "=" * 70)
    print(" [DEV SIMULATION MODE] No real TTS, AI image, or FFmpeg calls are made.")
    print(" Creating dummy assets to verify atomic state transitions and resume logic.")
    print("=" * 70)

    try:
        with ProjectLock(project_dir):
            plan = compute_plan(manifest, project_dir)
            active_units = [u for u in plan.all_units if u.status != ItemStatus.completed]

            if not active_units:
                print("\n[SIMULATION] All units are already completed. Nothing to do.\n")
                return 0

            print(f"\n[SIMULATION] Found {len(active_units)} units needing processing.")

            for unit in active_units:
                print(f"[SIMULATION] Processing work unit: {unit.unit_id} ({unit.unit_type})...")

                if fail_at and unit.unit_id == fail_at:
                    print(f"\n[SIMULATION ERROR] Simulated failure triggered at unit '{fail_at}'!")
                    print("[SIMULATION] Aborting execution as requested to test resume capability.\n")
                    return 1

                if delay > 0:
                    time.sleep(delay)

                # Process unit: write dummy asset to temp file first, then atomic rename (A3)
                target_path = project_dir / unit.target_file
                target_path.parent.mkdir(parents=True, exist_ok=True)
                temp_asset_path = target_path.parent / f"{target_path.name}.tmp.{uuid.uuid4().hex}"

                try:
                    with open(temp_asset_path, "wb") as fp:
                        fp.write(b"SIMULATED_ASSET_CONTENT\n")
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(temp_asset_path, target_path)
                except Exception as exc:
                    if temp_asset_path.exists():
                        temp_asset_path.unlink(missing_ok=True)
                    raise exc

                # Update in-memory manifest data structures
                if unit.unit_type == "tts_group_audio":
                    for ch in manifest.chapters:
                        if ch.chapter_index == unit.chapter_index:
                            for g in ch.tts_groups:
                                if g.group_index == unit.group_index:
                                    g.audio.file_path = unit.target_file
                                    g.audio.audio_hash = unit.expected_hash
                                    g.status = ItemStatus.completed
                elif unit.unit_type == "visual_segment":
                    for ch in manifest.chapters:
                        if ch.chapter_index == unit.chapter_index:
                            for g in ch.tts_groups:
                                for s in g.segments:
                                    if s.segment_index == unit.segment_index:
                                        s.visual.rendered_clip = unit.target_file
                                        s.visual.visual_hash = unit.expected_hash
                                        s.status = ItemStatus.completed
                elif unit.unit_type == "chapter_render":
                    for ch in manifest.chapters:
                        if ch.chapter_index == unit.chapter_index:
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

                # Save updated manifest atomically after each step
                save_manifest(manifest, project_dir)
                print(f"[SIMULATION] Completed and saved state for: {unit.unit_id}")

            # Verify final state
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

    # presets
    p_presets = subparsers.add_parser("presets", help="Manage and validate presets")
    preset_subs = p_presets.add_subparsers(dest="preset_command", required=True)
    p_p_list = preset_subs.add_parser("list", help="List available presets in hierarchy tree")
    p_p_validate = preset_subs.add_parser("validate", help="Validate a preset JSON file")
    p_p_validate.add_argument("file", help="Path to preset JSON file")

    # project
    p_proj = subparsers.add_parser("project", help="Manage longform projects")
    proj_subs = p_proj.add_subparsers(dest="project_command", required=True)

    # project create
    p_create = proj_subs.add_parser("create", help="Create a new longform project")
    p_create.add_argument("--title", required=True, help="Title of the project")
    p_create.add_argument("--preset", required=True, help="Preset ID to use")
    p_create.add_argument("--script", required=True, help="Path to Markdown script file")
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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
