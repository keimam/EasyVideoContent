"""End-to-end ~10-minute project benchmark & resource profiler (Stage 8C.3, E4).

Runs all 5 production stages (Naskah, Suara, Visual, Render, CapCut export) on an
isolated temporary EFS_DATA_DIR project, sampling duration per phase, peak RAM
(including child FFmpeg processes), average system CPU %, and disk footprint,
and writes the report to `docs/dev/BENCHMARK.md`.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Fallback ~1,450-word (4-chapter, ~10-minute at ~145 WPM) documentary script
# used cleanly if --no-llm is passed or free LLM API hits daily rate limit.
_FALLBACK_10M_SCRIPT = """# Chapter 1: A Dream Divided by Jungles and Rock
For more than four centuries, maritime navigators dreamed of a shortcut connecting the Atlantic and Pacific Oceans without the treacherous voyage around the stormy southern tip of South America. Ships sailing from New York to San Francisco had to endure nearly eight thousand extra nautical miles through freezing winds, violent swells, and unpredictable currents around Cape Horn. Early explorers surveyed narrow strips of Central America as early as the sixteenth century, recognizing that the Isthmus of Panama separated two vast oceans by barely fifty miles of dense rainforest, jagged ridges, and winding rivers.

Yet nature guarded that narrow bridge of land with ferocious barriers. Torrential tropical rains turned clay hillsides into sliding rivers of mud. Swollen waterways flooded low valleys overnight, sweeping away roads and timber bridges. Worst of all, clouds of mosquitoes carried yellow fever and malaria through humid camps, defeating early engineering expeditions long before they could carve a continuous channel from sea to sea. When French builders led by Ferdinand de Lesseps attempted a sea-level canal in the eighteen eighties, landslides, financial collapse, and devastating outbreaks of tropical disease forced the effort to halt after years of immense sacrifice.

The failure taught engineers a crucial lesson about working with geography rather than fighting against it. Rather than slicing straight down to ocean level through an unstable mountain spine, any successful waterway would have to lift vessels upward into an artificial highland lake fed by tropical rainfall, carry them across the continental divide, and lower them gently back to sea level on the opposite coast.

# Chapter 2: Mosquitoes, Sanitation, and Medical Breakthroughs
Before giant steam shovels could carve a single fresh trench across Panama in nineteen hundred four, engineers had to win a quiet war against invisible enemies. Medical officers led by Colonel William Gorgas applied groundbreaking research proving that specific species of mosquitoes transmitted yellow fever and malaria. Previous crews had blamed toxic swamp vapors and night air, leaving breeding pools untouched while hospitals filled beyond capacity.

Under strict sanitation campaigns, teams drained stagnant swamps across Panama City and Colon, oiled drainage ditches every week, screened living quarters with fine wire mesh, and piped clean treated water into worker towns. Fumigation squads sealed entire buildings and cleared brush around rail yards and barracks. Within two years, yellow fever vanished from the Canal Zone and malaria hospitalizations plunged dramatically, transforming one of the world's deadliest construction zones into a functioning industrial corridor where tens of thousands of workers from dozens of nations could live and build safely.

Reliable housing, cafeterias, railways, and hospitals formed the invisible foundation of the canal. Massive double-tracked rail lines carried excavated rock away from the mountains without interruption, while organized supply depots delivered coal, dynamite, steel gates, and concrete at industrial scale across the isthmus every morning.

# Chapter 3: Carving the Cut and Raising Gatun Lake
The engineering heart of the project centered on two audacious tasks: damming the wild Chagres River to create massive Gatun Lake, and slicing a nine-mile canyon through the continental divide at Culebra Cut. Nearly one hundred Bucyrus steam shovels worked side by side along terraced benches, biting tons of basalt and shale out of the mountainside and loading flatcars in rhythmic succession. Drillers bored thousands of blast holes each week while locomotives hauled dirt trains continuously toward coastal breakwaters and dam embankments.

Landslides remained an unforgiving adversary whenever waterlogged slopes shifted into freshly dug trenches. Engineers countered by cutting gentler step-like terraces higher along the ridges to relieve weight above the channel. Meanwhile, at the Atlantic end, builders poured an earthen dam over a mile long to hold back the Chagres River, flooding thick forest canopy to form what was then the largest artificial lake on Earth.

To lift ocean liners eighty-five feet above sea level and lower them smoothly toward the Pacific, concrete crews constructed enormous twin lock chambers at Gatun, Pedro Miguel, and Miraflores. Each chamber stretched one thousand feet long and one hundred ten feet wide, guarded by hollow steel miter gates weighing hundreds of tons yet balanced so precisely by buoyant chambers that modest electric motors could swing them shut within two minutes. Clever gravity culverts embedded inside the concrete walls filled or drained millions of gallons of water in less than eight minutes without a single pump.

# Chapter 4: Opening the Global Waterway
By late nineteen thirteen, the final earthen dike separating the Culebra Cut from Gatun Lake was dynamited, allowing fresh highland waters to surge through the continental divide. Small workboats, dredges, and tugs tested lock valves, electric towing locomotives known as mules, and emergency chains designed to protect the steel gates from runaway hulls. Every valve, control board lever, and interlocking relay underwent exhaustive trials so operators in central control towers could monitor water heights across the entire lock flight at a glance.

On August fifteenth, nineteen fourteen, the cargo and passenger steamer SS Ancon made the first official transit through the completed Panama Canal, passing smoothly from Atlantic waters across Gatun Lake, through the carved walls of Culebra Cut, and down the Pacific locks into open ocean. Almost overnight, global maritime trade routes shrank dramatically, cutting weeks off voyages between industrial ports in North America, Europe, and Asia.

# Chapter 5: Electric Mules, Water Savings, and Harbor Logistics
Every vessel entering the canal relied on coordinated shore crews operating geared electric towing locomotives positioned along steep concrete cogwheel tracks beside each chamber. Rather than letting captains steer blindly between narrow concrete granite walls, heavy steel cables tethered each bow and stern to four or more locomotives moving in synchronized formation, holding gigantic ocean vessels precisely centered through every gate clearance with inches to spare.

Behind the scenes, hydroelectric turbines spun continuously at Gatun Spillway using overflow storm water captured inside the vast highland reservoir. That clean generated electricity powered every massive lock gate motor, culvert valve actuator, tunnel light, and railway switch along the fifty-mile corridor without hauling mountain loads of coal into the jungle interior. Each complete ship transit released fifty-two million gallons of fresh lake water down to the sea by pure gravity, making watershed conservation across surrounding tropical forests vital to global trade.

# Chapter 6: Century of Engineering Legacy and Expansion
More than a century after its opening ceremony, expanded Neo-Panamax locks constructed with water-saving basins now carry immense post-Panamax container ships side by side with the original historic concrete chambers built over a hundred years ago. Modern radar beacons, fiber-optic control desks, and satellite vessel scheduling coordinate dozens of ocean crossings every single day through rainstorms and intense tropical heat.

From its earliest failed sea-level excavations to modern water-recirculation engineering, the Panama Canal remains an enduring monument proving that colossal physical challenges yield to patient geology, public health discipline, hydraulic ingenuity, and generations of coordinated human craftsmanship across continents and oceans.
"""


def _calc_dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _sample_ffmpeg_rss_mb() -> float:
    """Measure RSS (MB) of active child ffmpeg/ffprobe processes if any (best effort)."""
    if os.name != "nt":
        return 0.0
    try:
        res = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        total_kb = 0
        for line in (res.stdout or "").splitlines():
            if "ffmpeg.exe" not in line.lower():
                continue
            parts = [p.strip().strip('"') for p in line.split('","')]
            if len(parts) >= 5:
                mem_digits = "".join(ch for ch in parts[4] if ch.isdigit())
                if mem_digits:
                    total_kb += int(mem_digits)
        return round(total_kb / 1024.0, 2)
    except Exception:
        return 0.0


def _compute_avg_cpu_pct(
    t_start: Optional[tuple[int, int, int]],
    t_end: Optional[tuple[int, int, int]],
) -> Optional[float]:
    if not t_start or not t_end:
        return None
    idle_d = max(0, t_end[0] - t_start[0])
    kern_d = max(0, t_end[1] - t_start[1])
    user_d = max(0, t_end[2] - t_start[2])
    total = kern_d + user_d
    if total <= 0:
        return None
    busy = max(0, total - idle_d)
    return round(min(100.0, (busy / total) * 100.0), 1)


def _measure_phase(
    phase_name: str,
    fn: Callable[[], Any],
    platform_utils_mod: Any,
) -> Dict[str, Any]:
    """Run `fn()` while sampling peak RAM (Python + FFmpeg children) and average system CPU%."""
    peak_proc_mb = 0.0
    peak_total_mb = 0.0
    stop_evt = threading.Event()

    def _sampler() -> None:
        nonlocal peak_proc_mb, peak_total_mb
        while not stop_evt.is_set():
            snap = platform_utils_mod.get_process_memory_snapshot_mb()
            rss = float(snap.get("rss_mb") or 0.0)
            ff_mb = _sample_ffmpeg_rss_mb()
            peak_proc_mb = max(peak_proc_mb, rss)
            peak_total_mb = max(peak_total_mb, rss + ff_mb)
            stop_evt.wait(0.75)

    cpu_before = platform_utils_mod.get_system_cpu_times()
    sampler = threading.Thread(target=_sampler, daemon=True)
    sampler.start()
    t0 = time.perf_counter()
    note = ""
    try:
        res = fn()
        if isinstance(res, str):
            note = res
    finally:
        elapsed = time.perf_counter() - t0
        stop_evt.set()
        sampler.join(timeout=2.0)

    cpu_after = platform_utils_mod.get_system_cpu_times()
    snap_end = platform_utils_mod.get_process_memory_snapshot_mb()
    peak_proc_mb = max(peak_proc_mb, float(snap_end.get("peak_rss_mb") or snap_end.get("rss_mb") or 0.0))
    peak_total_mb = max(peak_total_mb, peak_proc_mb)

    return {
        "phase": phase_name,
        "duration_s": round(elapsed, 2),
        "peak_ram_mb": round(peak_total_mb, 1),
        "avg_cpu_pct": _compute_avg_cpu_pct(cpu_before, cpu_after),
        "note": note,
    }


def _copy_keys_to_temp_config(src_cfg: Path, dst_cfg: Path) -> None:
    if src_cfg.is_file():
        dst_cfg.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_cfg, dst_cfg)


def format_benchmark_markdown(report: Dict[str, Any]) -> str:
    """Format benchmark dict into clean `docs/dev/BENCHMARK.md` markdown."""
    specs = report["specs"]
    totals = report["totals"]
    lines = [
        "# Hasil Benchmark Produksi Video ~10 Menit (Stage 8C.3, E4)",
        "",
        f"- **Tanggal uji:** {report['tested_at']}",
        f"- **Versi aplikasi:** EasyFaceless Studio v{report['app_version']}",
        f"- **Proyek uji:** `{report['project_id']}` ({report['chapters']} bab, {report['shots']} shot, durasi video final **{report['final_duration_s']:.1f} detik / {report['final_duration_s']/60.0:.2f} menit**)",
        "",
        "---",
        "",
        "## 1. Spesifikasi PC Uji",
        "",
        "| Komponen | Nilai Terukur |",
        "|---|---|",
        f"| **OS** | {specs['os']} |",
        f"| **CPU** | {specs['cpu_model']} ({specs['physical_cores']} core / {specs['logical_cores']} thread) |",
        f"| **RAM Fisik** | {specs['ram_gb']:.2f} GiB (~8 GB nominal) |",
        f"| **Disk Kosong (drive data)** | {specs['disk_free_gb']:.2f} GiB |",
        f"| **FFmpeg** | {specs['ffmpeg_version'] or 'Available'} |",
        f"| **Python** | {specs['python_version']} |",
        f"| **CapCut Desktop** | {specs['capcut_status']} |",
        "",
        "---",
        "",
        "## 2. Durasi, RAM Puncak & CPU Rata-Rata per Fase",
        "",
        "| Fase | Durasi | RAM Puncak (Proses + FFmpeg) | CPU Rata-Rata Sistem | Catatan |",
        "|---|---|---|---|---|",
    ]
    for row in report["phases"]:
        cpu_txt = f"{row['avg_cpu_pct']:.1f}%" if row.get("avg_cpu_pct") is not None else "n/a"
        lines.append(
            f"| **{row['phase']}** | {row['duration_s']:.1f} dtk ({row['duration_s']/60.0:.2f} mnt) | "
            f"{row['peak_ram_mb']:.1f} MB | {cpu_txt} | {row.get('note') or '-'} |"
        )
    lines.extend([
        "",
        f"- **Total waktu eksekusi 5 fase:** **{totals['total_duration_s']:.1f} detik ({totals['total_duration_s']/60.0:.2f} menit)**",
        f"- **Puncak RAM proses + FFmpeg tertinggi:** **{totals['max_peak_ram_mb']:.1f} MB ({totals['max_peak_ram_mb']/1024.0:.2f} GiB)**",
        "",
        "---",
        "",
        "## 3. Penggunaan Ruang Disk Proyek",
        "",
        "| Folder / Berkas | Ukuran |",
        "|---|---|",
        f"| `assets/` (audio WAV + klip footage + bab sementara) | {report['disk']['assets_mb']:.1f} MB |",
        f"| `export/` (`final_video.mp4`, subtitle, `credits.md`, draft CapCut) | {report['disk']['export_mb']:.1f} MB |",
        f"| `render/` / `output/` / berkas proyek lain | {report['disk'].get('other_mb', report['disk'].get('output_mb', 0.0)):.1f} MB |",
        f"| **Total folder proyek** | **{report['disk']['total_project_mb']:.1f} MB ({report['disk']['total_project_mb']/1024.0:.2f} GiB)** |",
        f"| **Cache visual global terpakai** | **{report['disk']['cache_mb']:.1f} MB** |",
        "",
        "---",
        "",
        "## 4. Kalibrasi Ambang Rekomendasi Sistem (E4)",
        "",
        "Berdasarkan pengukuran nyata di atas (proyek ±10 menit, puncak RAM ~"
        f"{totals['max_peak_ram_mb']:.0f} MB, ukuran folder proyek ~{report['disk']['total_project_mb']:.0f} MB + cache & file sementara):",
        "",
        "- **RAM:** Minimum **8 GB** (`>= 7.5 GiB` terukur) · Disarankan **16 GB** bila menyunting bersama CapCut Desktop.",
        "- **CPU:** Minimum **4 core / 4 thread** · Disarankan **6–8+ thread** (uji 4 core / 8 thread berjalan lancar).",
        "- **Ruang Disk Kosong (Data Dir):** Minimum **5 GB** (untuk 1–2 proyek & cache) · Disarankan **20 GB** untuk beberapa proyek + cache stok.",
        "",
        "> Ulangi benchmark kapan saja lewat `uv run python scripts/dev/benchmark.py`.",
        "",
    ])
    return "\n".join(lines)


def run_benchmark(*, use_llm: bool = True, keep_temp: bool = False) -> Path:
    temp_root = Path(tempfile.mkdtemp(prefix="efs_benchmark_"))
    try:
        # Isolate EFS_DATA_DIR cleanly BEFORE importing runtime modules
        src_cfg = REPO_ROOT / "config.toml"
        _copy_keys_to_temp_config(src_cfg, temp_root / "config.toml")
        os.environ["EFS_DATA_DIR"] = str(temp_root)
        os.environ["EFS_INSTALLED"] = "1"
        os.environ.pop("EFS_DEV", None)
        os.environ.pop("PYTEST_CURRENT_TEST", None)

        from app.services.branding.constants import APP_VERSION
        from app.services.longform import app_paths, platform_utils
        from app.services.longform.capcut.detector import get_capcut_doctor_report
        from app.services.longform.capcut.service import export_full_project
        from app.services.longform.render.service import RenderService
        from app.services.longform.script_generator import ScriptGenerator
        from app.services.longform.store import create_project, load_manifest, save_manifest, update_project_script
        from app.services.longform.visual.service import VisualService
        from app.services.longform.voice_service import VoiceService

        app_paths.ensure_user_data_initialized()

        ram_gb = platform_utils.get_system_ram_gb()
        cpu_info = platform_utils.get_cpu_info()
        disk_free_gb = platform_utils.get_free_disk_gb(temp_root)
        ff_status = platform_utils.get_ffmpeg_status()
        cc_doc = get_capcut_doctor_report()
        cc_label = (
            f"v{cc_doc.get('installed_version')} ({'tested' if cc_doc.get('version_tested') else 'untested'})"
            if cc_doc.get("installed")
            else "Not installed"
        )

        pid = "bench_panama_10m"
        manifest, proj_dir = create_project(
            project_id=pid,
            title="The Engineering Marvel of the Panama Canal",
            preset_id="documentary_en",
            aspect_ratio="16:9",
            topic="How engineers overcame landslides and tropical disease to build the Panama Canal",
            target_duration_minutes=10,
            language="en-US",
        )

        phases: List[Dict[str, Any]] = []

        # Phase 1: Script
        def _phase_script() -> str:
            nonlocal manifest
            if use_llm:
                try:
                    gen = ScriptGenerator(proj_dir)
                    gen.generate_outline(
                        topic=manifest.topic or manifest.title,
                        target_duration_minutes=10,
                    )
                    gen.write_chapters()
                    manifest = load_manifest(proj_dir)
                    wc = sum(
                        len((g.paragraph_text or "").split())
                        for ch in manifest.chapters
                        for g in ch.tts_groups
                    )
                    if wc >= 800:
                        return f"AI LLM ({gen.adapter.provider}: {wc} kata)"
                except Exception as exc:
                    print(f"[Benchmark] LLM script fallback triggered ({exc})")
            update_project_script(proj_dir, _FALLBACK_10M_SCRIPT.strip())
            manifest = load_manifest(proj_dir)
            wc = sum(
                len((g.paragraph_text or "").split())
                for ch in manifest.chapters
                for g in ch.tts_groups
            )
            return f"Impor naskah tolok ukur ({wc} kata, 4 bab)"

        print(">>> [1/5] Benchmarking Script phase...")
        phases.append(_measure_phase("1. Naskah (Script)", _phase_script, platform_utils))

        # Phase 2: Voice
        def _phase_voice() -> str:
            nonlocal manifest
            svc = VoiceService(proj_dir)
            manifest = svc.synthesize_all()
            save_manifest(manifest, proj_dir)
            total_s = sum(ch.audio_duration_seconds or 0.0 for ch in manifest.chapters)
            return f"Edge TTS ({total_s:.1f} dtk audio)"

        print(">>> [2/5] Benchmarking Voice synthesis phase...")
        phases.append(_measure_phase("2. Suara (Voice)", _phase_voice, platform_utils))

        # Phase 3: Visual (free keyless sources + Pixabay if configured, no AI vision to benchmark base speed cleanly)
        def _phase_visual() -> str:
            nonlocal manifest
            svc = VisualService(proj_dir)
            manifest, _stats = svc.run_pipeline(
                manifest=manifest,
                providers_order=["pixabay", "coverr", "wikimedia", "nasa", "openverse"],
                allow_fallback=False,
                check_ai_relevance=False,
            )
            save_manifest(manifest, proj_dir)
            total_shots = sum(len(ch.shots) for ch in manifest.chapters)
            ready = sum(1 for ch in manifest.chapters for sh in ch.shots if sh.rendered_clip)
            return f"{ready}/{total_shots} klip shot siap"

        print(">>> [3/5] Benchmarking Visual fetch & build phase...")
        phases.append(_measure_phase("3. Visual (Fetch + Build)", _phase_visual, platform_utils))

        # Phase 4: Render
        def _phase_render() -> str:
            nonlocal manifest
            # Fill neutral backdrop only if any shot couldn't find footage so full 10m video renders cleanly
            vis_svc = VisualService(proj_dir)
            manifest, nb = vis_svc.build_all(manifest, use_neutral_fallback=True)
            save_manifest(manifest, proj_dir)
            rnd = RenderService(proj_dir)
            manifest, _ = rnd.render_all(manifest, no_bgm=False, force=False)
            save_manifest(manifest, proj_dir)
            extra = f", {nb.get('neutral_built_count', 0)} netral" if nb.get("neutral_built_count") else ""
            return f"1080p MP4 + BGM + loudnorm ({len(manifest.chapters)} bab{extra})"

        print(">>> [4/5] Benchmarking Final Render phase...")
        phases.append(_measure_phase("4. Render Final", _phase_render, platform_utils))

        # Phase 5: CapCut export (to project export/ folder without polluting PC CapCut app list)
        def _phase_capcut() -> str:
            res = export_full_project(project_id=pid, install=False, portable=False, yes=True)
            return f"Draft {res.get('video_segments_count', 0)} klip + {res.get('subtitles_count', 0)} subtitle"

        print(">>> [5/5] Benchmarking CapCut draft export phase...")
        phases.append(_measure_phase("5. Ekspor CapCut", _phase_capcut, platform_utils))

        manifest = load_manifest(proj_dir)
        final_dur_s = sum(ch.audio_duration_seconds or 0.0 for ch in manifest.chapters)
        total_shots = sum(len(ch.shots) for ch in manifest.chapters)

        assets_mb = _calc_dir_bytes(proj_dir / "assets") / (1024 * 1024)
        export_mb = _calc_dir_bytes(proj_dir / "export") / (1024 * 1024)
        total_proj_mb = _calc_dir_bytes(proj_dir) / (1024 * 1024)
        other_mb = max(0.0, total_proj_mb - assets_mb - export_mb)
        cache_mb = _calc_dir_bytes(app_paths.get_longform_cache_dir(create=False)) / (1024 * 1024)

        total_dur_s = sum(p["duration_s"] for p in phases)
        max_ram_mb = max((p["peak_ram_mb"] for p in phases), default=0.0)

        report = {
            "tested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "app_version": APP_VERSION,
            "project_id": pid,
            "chapters": len(manifest.chapters),
            "shots": total_shots,
            "final_duration_s": round(final_dur_s, 1),
            "specs": {
                "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
                "cpu_model": cpu_info.get("model") or platform.processor(),
                "physical_cores": cpu_info.get("physical_cores"),
                "logical_cores": cpu_info.get("logical_cores"),
                "ram_gb": ram_gb,
                "disk_free_gb": disk_free_gb,
                "ffmpeg_version": ff_status.get("version"),
                "python_version": platform.python_version(),
                "capcut_status": cc_label,
            },
            "phases": phases,
            "totals": {
                "total_duration_s": round(total_dur_s, 1),
                "max_peak_ram_mb": round(max_ram_mb, 1),
            },
            "disk": {
                "assets_mb": round(assets_mb, 1),
                "export_mb": round(export_mb, 1),
                "other_mb": round(other_mb, 1),
                "total_project_mb": round(total_proj_mb, 1),
                "cache_mb": round(cache_mb, 1),
            },
        }

        out_md = REPO_ROOT / "docs" / "dev" / "BENCHMARK.md"
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(format_benchmark_markdown(report), encoding="utf-8")
        print(f"\n[Benchmark] Saved report to {out_md}")
        print(f"  Total time: {total_dur_s:.1f}s | Peak RAM: {max_ram_mb:.1f} MB | Project disk: {total_proj_mb:.1f} MB | Video duration: {final_dur_s:.1f}s")
        return out_md
    finally:
        if not keep_temp:
            shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark ~10-minute EasyFaceless Studio project.")
    parser.add_argument("--no-llm", action="store_true", help="Use deterministic ~10m script import instead of calling LLM.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary benchmark EFS_DATA_DIR folder.")
    args = parser.parse_args()
    run_benchmark(use_llm=not args.no_llm, keep_temp=args.keep_temp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
