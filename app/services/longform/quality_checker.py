"""Script Quality and Fact-Checking Inspector (2.4).

Performs non-blocking static analysis on longform scripts:
- Chapter word count vs target and total estimated duration
- Detection of AI slop and cliché phrases from configurable lists
- Cross-chapter repetition (repeated n-grams and sentences)
- Hook rules compliance (flagging conversational greetings, channel intros)
- Coverage of visual directives ([visual: ...])
- Collection of [verify: ...] claims into a structured fact-checking checklist
- Sensitive claims detection (flagging specs, prices, and numbers lacking [verify] tags)
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.longform.schema import Chapter, ProjectManifest, Segment
from app.services.longform.script_import import estimate_script_duration, extract_tags, split_sentences
from app.services.longform.store import load_manifest

DEFAULT_CLICHE_PHRASES = [
    "delve",
    "delving",
    "tapestry",
    "in today's world",
    "buckle up",
    "let's dive in",
    "dive in",
    "dive into",
    "game-changer",
    "game changer",
    "it's important to note",
    "testament to",
    "beacon of",
    "unbeknownst to",
    "seamlessly",
    "rich history",
    "fast-forward to",
    "at the end of the day",
]

HOOK_VIOLATION_PATTERNS = [
    r"\bwelcome\s+back\b",
    r"\bwelcome\s+to\b",
    r"\bhello\s+everyone\b",
    r"\bin\s+this\s+video\b",
    r"\btoday\s+we\s+will\b",
    r"\bdon't\s+forget\s+to\s+subscribe\b",
    r"\bhit\s+the\s+bell\b",
    r"\bleave\s+a\s+comment\s+below\b",
]

# Regex detecting technical specs, numbers, prices, or performance ratings
SPEC_NUMBERS_RE = re.compile(
    r"(\b\d+(?:[\.,]\d+)?\s*(?:hp|horsepower|mph|rpm|cubic\s*inches|cu\s*in|cid|seconds?|sec|liters?|ci)\b|"
    r"\$\s*\d+(?:[\.,]\d+)?(?:\s*(?:million|thousand|k|m))?|"
    r"\b\d{3,}\b)",
    re.IGNORECASE,
)


def _get_ngrams(tokens: List[str], n: int = 5) -> List[str]:
    """Extract consecutive word n-grams."""
    if len(tokens) < n:
        return []
    return [" ".join(tokens[i : i + n]).lower() for i in range(len(tokens) - n + 1)]


class ScriptQualityChecker:
    """Analyzes full script and outputs a comprehensive quality report."""

    def __init__(
        self,
        project_dir: str | Path,
        custom_cliches: Optional[List[str]] = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.script_dir = self.project_dir / "script"
        self.script_dir.mkdir(parents=True, exist_ok=True)
        self.report_file = self.script_dir / "quality_report.md"
        self.cliche_list = custom_cliches or DEFAULT_CLICHE_PHRASES

    def inspect(self) -> Dict[str, Any]:
        """Perform comprehensive quality analysis on project manifest and chapter scripts."""
        manifest = load_manifest(self.project_dir)
        preset_snap = manifest.preset_snapshot

        # 1. Word counts & duration
        duration_info = estimate_script_duration(
            manifest.chapters,
            words_per_minute=preset_snap.get("words_per_minute", 140),
        )

        # 2. Clichés and slop detection
        cliche_matches: List[Dict[str, Any]] = []
        # 3. Hook violations
        hook_violations: List[str] = []
        # 4. Cross-chapter repetition
        chapter_ngrams: Dict[int, Set[str]] = {}
        all_ngrams: List[str] = []
        # 5. Missing visual directives
        missing_visuals: List[Dict[str, Any]] = []
        # 6. Verification checklist
        verify_claims: List[Dict[str, Any]] = []
        # 7. Unverified technical specifications
        unverified_specs: List[Dict[str, Any]] = []

        is_first_chapter = True

        # Check if project was generated with fake provider
        is_fake = False
        state_file = self.script_dir / "state.json"
        if state_file.is_file():
            try:
                import json
                s_data = json.loads(state_file.read_text(encoding="utf-8"))
                if s_data.get("outline_provider") == "fake":
                    is_fake = True
                for ch_info in s_data.get("chapters", {}).values():
                    if ch_info.get("provider") == "fake":
                        is_fake = True
            except Exception:
                pass
        outline_file = self.script_dir / "outline.json"
        if not is_fake and outline_file.is_file():
            try:
                import json
                o_data = json.loads(outline_file.read_text(encoding="utf-8"))
                if o_data.get("provider") == "fake":
                    is_fake = True
            except Exception:
                pass
        if not is_fake and "[FAKE]" in manifest.title:
            is_fake = True

        for ch in manifest.chapters:
            ch_tokens: List[str] = []

            for group in ch.tts_groups:
                for seg in group.segments:
                    text = seg.narration_text.strip()
                    words = text.split()
                    ch_tokens.extend(words)

                    # Check clichés
                    lower_text = text.lower()
                    for cliche in self.cliche_list:
                        if cliche in lower_text:
                            cliche_matches.append(
                                {
                                    "chapter": ch.chapter_index,
                                    "cliche": cliche,
                                    "sentence": text,
                                }
                            )

                    # Check hook violations in opening chapter
                    if is_first_chapter and seg.segment_index <= 3:
                        for pattern in HOOK_VIOLATION_PATTERNS:
                            if re.search(pattern, lower_text):
                                hook_violations.append(
                                    f"Chapter 1 Segment {seg.segment_index}: '{text}' violates hook rules."
                                )

                    # Check missing visuals
                    if not seg.visual or not seg.visual.prompt:
                        missing_visuals.append(
                            {
                                "chapter": ch.chapter_index,
                                "segment": seg.segment_index,
                                "text": text,
                            }
                        )

                    # Collect [verify: ...] tags
                    if seg.verify:
                        verify_claims.append(
                            {
                                "chapter": ch.chapter_index,
                                "segment": seg.segment_index,
                                "claim": seg.verify,
                                "sentence": text,
                            }
                        )
                    else:
                        # Check for unverified sensitive numbers/specs
                        matches = SPEC_NUMBERS_RE.findall(text)
                        if matches:
                            unverified_specs.append(
                                {
                                    "chapter": ch.chapter_index,
                                    "segment": seg.segment_index,
                                    "specs": list(set(matches)),
                                    "sentence": text,
                                }
                            )

            # Store n-grams
            ch_ng = set(_get_ngrams(ch_tokens, n=6))
            chapter_ngrams[ch.chapter_index] = ch_ng
            all_ngrams.extend(_get_ngrams(ch_tokens, n=6))
            is_first_chapter = False

        # Find n-grams repeated across multiple chapters
        repeated_ngrams = []
        ch_indices = list(chapter_ngrams.keys())
        for i in range(len(ch_indices)):
            for j in range(i + 1, len(ch_indices)):
                ch1 = ch_indices[i]
                ch2 = ch_indices[j]
                overlap = chapter_ngrams[ch1] & chapter_ngrams[ch2]
                for phrase in overlap:
                    repeated_ngrams.append(
                        {
                            "chapters": f"Ch {ch1} and Ch {ch2}",
                            "phrase": phrase,
                        }
                    )

        report_data = {
            "title": manifest.title,
            "project_id": manifest.project_id,
            "preset_id": manifest.preset_id,
            "is_fake": is_fake,
            "duration_info": duration_info,
            "cliche_matches": cliche_matches,
            "hook_violations": hook_violations,
            "repeated_ngrams": repeated_ngrams[:10],  # Top 10
            "missing_visuals": missing_visuals,
            "verify_claims": verify_claims,
            "unverified_specs": unverified_specs,
        }

        # Render markdown report
        md_report = self._render_markdown_report(report_data)
        self.report_file.write_text(md_report, encoding="utf-8")

        return report_data

    def _render_markdown_report(self, data: Dict[str, Any]) -> str:
        d = data["duration_info"]
        total_w = d["total_words"]
        dur_s = d["estimated_duration_seconds"]
        dur_m = dur_s / 60.0

        lines = [
            f"# Script Quality & Fact-Checking Report: {data['title']}",
            f"- **Project ID**: `{data['project_id']}`",
            f"- **Preset**: `{data['preset_id']}`",
            f"- **Total Word Count**: **{total_w} words**",
            f"- **Estimated Video Duration**: **{dur_m:.1f} minutes** ({dur_s:.1f} seconds)",
        ]

        if data.get("is_fake"):
            lines.extend([
                "",
                "> [!WARNING]",
                "> **LLM PROVIDER: FAKE (TESTING MOCK ONLY)**",
                "> This script was generated using the deterministic offline test provider ('fake').",
                "> It is intended exclusively for workflow simulation and automated unit testing, not for production release.",
            ])

        lines.extend([
            "",
            "---",
            "",
            "## 1. Duration & Length Summary",
        ])

        if d["warnings"]:
            for w in d["warnings"]:
                lines.append(f"> [!WARNING] {w}")
        else:
            lines.append("✓ Script duration is within the recommended 10 to 20 minute target range.")

        lines.extend([
            "",
            "---",
            "",
            "## 2. Hook Rules Compliance",
        ])

        if data["hook_violations"]:
            lines.append("> [!CAUTION] Hook violations detected in opening segments:")
            for v in data["hook_violations"]:
                lines.append(f"- ⚠️ {v}")
        else:
            lines.append("✓ Opening hook complies with preset rules (no pleasantries or channel intros detected).")

        lines.extend([
            "",
            "---",
            "",
            "## 3. Cliché & AI Slop Detection",
        ])

        if data["cliche_matches"]:
            lines.append(f"Found {len(data['cliche_matches'])} potential cliché/slop phrase occurrences:")
            for m in data["cliche_matches"][:15]:
                lines.append(f"- **Ch {m['chapter']}** [`{m['cliche']}`]: *\"{m['sentence']}\"*")
        else:
            lines.append("✓ No overused AI slop or cliché phrases detected.")

        lines.extend([
            "",
            "---",
            "",
            "## 4. Cross-Chapter Narrative Repetition",
        ])

        if data["repeated_ngrams"]:
            lines.append("Repeated phrasing found across distinct chapters:")
            for r in data["repeated_ngrams"]:
                lines.append(f"- {r['chapters']}: \"{r['phrase']}\"")
        else:
            lines.append("✓ No significant phrase or sentence repetition detected across chapters.")

        lines.extend([
            "",
            "---",
            "",
            "## 5. Visual Coverage",
        ])

        if data["missing_visuals"]:
            lines.append(f"> [!WARNING] {len(data['missing_visuals'])} segments lack visual directives:")
            for mv in data["missing_visuals"][:10]:
                lines.append(f"- Ch {mv['chapter']} Seg {mv['segment']}: \"{mv['text']}\"")
        else:
            lines.append("✓ 100% of segments have valid visual descriptions and motion tags.")

        lines.extend([
            "",
            "---",
            "",
            f"## 6. Fact-Checking Checklist ({len(data['verify_claims'])} Claims)",
            "",
            "The following claims were flagged with `[verify: ...]` for human fact-checking:",
            "",
            "| Chapter | Segment | Claim to Verify | Context Sentence |",
            "|:---:|:---:|:---|:---|",
        ])

        if data["verify_claims"]:
            for c in data["verify_claims"]:
                lines.append(
                    f"| Ch {c['chapter']} | Seg {c['segment']} | **{c['claim']}** | *{c['sentence']}* |"
                )
        else:
            lines.append("| - | - | *No claims explicitly marked with [verify]* | - |")

        lines.extend([
            "",
            "---",
            "",
            f"## 7. Unverified Technical Figures ({len(data['unverified_specs'])} Occurrences)",
            "",
            "Sentences containing technical numbers/ratings without an attached `[verify]` tag:",
            "",
        ])

        if data["unverified_specs"]:
            for u in data["unverified_specs"][:15]:
                specs_str = ", ".join(f"`{s}`" for s in u["specs"])
                lines.append(
                    f"- **Ch {u['chapter']} Seg {u['segment']}** (Specs: {specs_str}): *\"{u['sentence']}\"*"
                )
        else:
            lines.append("✓ All technical specifications and numbers carry appropriate verification tags.")

        lines.append("")
        return "\n".join(lines)
