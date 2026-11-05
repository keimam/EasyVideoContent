"""Unified LLM Adapter for Longform Video Generation.

Routes all LLM requests through a single adapter (2.1), supporting:
- Upstream MPT LLM providers configured in config.toml (calling upstream_llm._generate_response)
- Deterministic 'fake' provider for zero-cost offline development and automated unit testing
- File-based atomic caching per prompt in the project directory
- Timeout and bounded exponential retry with sanitized error messages (no leaked API keys)
- Call duration and token tracking (when provided by upstream)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from app.config import config
from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID, get_llm_provider

# Regex to sanitize sensitive keys from error messages
_SENSITIVE_KEY_RE = re.compile(
    r"((?:api[_-]?key|access[_-]?token|secret|password|bearer\s+)[=:\s]+)([a-zA-Z0-9_\-\.]{8,})",
    re.IGNORECASE,
)


def sanitize_error(msg: str) -> str:
    """Mask potential API keys or secrets in error messages."""
    return _SENSITIVE_KEY_RE.sub(r"\1***REDACTED***", str(msg))


@dataclass
class LLMResponse:
    """Structured response from LLMAdapter."""

    content: str
    provider: str
    model: str
    duration_seconds: float
    tokens: Optional[Dict[str, int]] = None
    cache_hit: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LLMAdapterError(Exception):
    """Raised when an LLM call fails after retries."""
    pass


class LLMConfigurationError(LLMAdapterError):
    """Raised when an LLM provider lacks required configuration (e.g. missing API key)."""
    pass


class LLMAdapter:
    """Adapter mediating LLM calls with caching, retries, and fake provider support."""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        cache_dir: Optional[str | Path] = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        fake_delay: float = 0.0,
    ) -> None:
        self.provider = (provider or config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID).lower().strip()
        self.fake_delay = float(fake_delay)
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else None
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.retry_delay = max(0.1, retry_delay)

        # Resolve model name
        if model:
            self.model = model.strip()
        elif self.provider == "fake":
            self.model = "deterministic-fake-v1"
        else:
            prov_def = get_llm_provider(self.provider)
            if prov_def:
                raw_model = config.app.get(prov_def.config_key("model_name"), "")
                self.model = prov_def.resolve_model_name(raw_model)
            else:
                self.model = ""

        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_fake(self) -> bool:
        """Return True if this adapter is running against the local fake test mock."""
        return self.provider == "fake"

    def validate_configuration(self) -> None:
        """Verify that provider has required credentials before execution."""
        if self.is_fake:
            return

        prov_def = get_llm_provider(self.provider)
        if prov_def:
            if prov_def.requires_api_key:
                key_name = prov_def.config_key("api_key")
                key_val = (config.app.get(key_name) or "").strip()
                if not key_val:
                    url_hint = f" ({prov_def.api_key_url})" if prov_def.api_key_url else ""
                    raise LLMConfigurationError(
                        f"LLM Provider '{self.provider}' is selected, but '{key_name}' is not configured or empty.\n"
                        f"To configure this provider:\n"
                        f"  1. Open WebUI -> 'Basic Settings' -> 'LLM Settings' (or edit config.toml)\n"
                        f"  2. Select Provider '{prov_def.default_label}' and enter your API Key{url_hint}\n"
                        f"  3. Save settings, OR pass '--provider fake' if you wish to run in offline testing/simulation mode."
                    )
        else:
            key_name = f"{self.provider}_api_key"
            key_val = (config.app.get(key_name) or "").strip()
            if not key_val:
                raise LLMConfigurationError(
                    f"LLM Provider '{self.provider}' is selected, but '{key_name}' is not configured or empty.\n"
                    f"Please configure '{key_name}' in config.toml or pass '--provider fake' for offline testing."
                )

    def _compute_cache_key(self, prompt: str) -> str:
        """Compute sha256 hash representing prompt and call configuration."""
        normalized_prompt = prompt.strip()
        data = f"{self.provider}:{self.model}:{normalized_prompt}".encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def _read_cache(self, cache_key: str) -> Optional[LLMResponse]:
        """Read cached response from disk if present."""
        if not self.cache_dir:
            return None
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.is_file():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                return LLMResponse(
                    content=data["content"],
                    provider=data.get("provider", self.provider),
                    model=data.get("model", self.model),
                    duration_seconds=0.0,
                    tokens=data.get("tokens"),
                    cache_hit=True,
                )
            except Exception as exc:
                logger.warning(f"Failed to read LLM cache '{cache_file}': {exc}")
        return None

    def _write_cache(self, cache_key: str, response: LLMResponse) -> None:
        """Atomically write response to cache."""
        if not self.cache_dir:
            return
        cache_file = self.cache_dir / f"{cache_key}.json"
        tmp_file = self.cache_dir / f"{cache_key}.json.tmp.{uuid.uuid4().hex}"
        try:
            payload = json.dumps(
                {
                    "content": response.content,
                    "provider": response.provider,
                    "model": response.model,
                    "tokens": response.tokens,
                    "created_at": time.time(),
                },
                indent=2,
            ).encode("utf-8")
            with open(tmp_file, "wb") as fp:
                fp.write(payload)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp_file, cache_file)
        except Exception as exc:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            logger.warning(f"Failed to write LLM cache '{cache_file}': {exc}")

    def generate(
        self,
        prompt: str,
        fake_kind: Optional[str] = None,  # "outline" | "chapter" | "revise_length" | "summary"
        fake_kwargs: Optional[Dict[str, Any]] = None,
        force_refresh: bool = False,
    ) -> LLMResponse:
        """Execute prompt against configured provider or fake mock, checking cache first."""
        # Fail fast if real provider credentials are missing
        self.validate_configuration()

        cache_key = self._compute_cache_key(prompt)

        # 1. Check cache
        if not force_refresh:
            cached = self._read_cache(cache_key)
            if cached is not None:
                return cached

        # 2. Fake provider execution
        if self.provider == "fake":
            start_time = time.perf_counter()
            content = self._generate_fake_response(prompt, fake_kind, fake_kwargs)
            duration = time.perf_counter() - start_time
            resp = LLMResponse(
                content=content,
                provider="fake",
                model=self.model or "deterministic-fake-v1",
                duration_seconds=round(duration, 4),
                tokens=None,
                cache_hit=False,
            )
            self._write_cache(cache_key, resp)
            return resp

        # 3. Real upstream execution with bounded retries and backoff
        # Calls upstream: app.services.llm._generate_response
        # Note on upstream interface: calls app/services/llm.py:_generate_response (line 315).
        from app.services import llm as upstream_llm

        last_error = ""
        resolved_model = self.model
        if not resolved_model:
            prov_def = get_llm_provider(self.provider)
            if prov_def:
                raw_model = config.app.get(prov_def.config_key("model_name"), "")
                resolved_model = prov_def.resolve_model_name(raw_model)

        for attempt in range(1, self.max_retries + 1):
            start_time = time.perf_counter()
            try:
                raw_text = upstream_llm._generate_response(
                    prompt=prompt,
                    request_timeout=self.timeout,
                    max_retries=0,  # Let adapter manage bounded backoff
                )
                duration = time.perf_counter() - start_time
                resp = LLMResponse(
                    content=raw_text.strip(),
                    provider=self.provider,
                    model=resolved_model,
                    duration_seconds=round(duration, 3),
                    tokens=None,  # upstream _generate_response extracts pure text
                    cache_hit=False,
                )
                self._write_cache(cache_key, resp)
                return resp
            except Exception as exc:
                duration = time.perf_counter() - start_time
                last_error = sanitize_error(str(exc))
                logger.warning(
                    f"LLM call attempt {attempt}/{self.max_retries} failed ({duration:.2f}s): {last_error}"
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * (2 ** (attempt - 1)))

        raise LLMAdapterError(
            f"LLM request to provider '{self.provider}' failed after {self.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    def _generate_fake_response(
        self,
        prompt: str,
        fake_kind: Optional[str] = None,
        fake_kwargs: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate high-quality deterministic fake responses for offline testing."""
        kwargs = fake_kwargs or {}

        # Allow tests to inject errors or invalid JSON directly
        if kwargs.get("fake_error"):
            raise ValueError(kwargs["fake_error"])
        if kwargs.get("fake_invalid_json"):
            return "INVALID JSON {{{ missing closing bracket"
        if kwargs.get("fake_raw_content"):
            return kwargs["fake_raw_content"]

        if self.fake_delay > 0:
            time.sleep(self.fake_delay)

        kind = fake_kind
        if not kind:
            # Infer from prompt
            if "narrative_summary" in prompt or "editorial assistant for documentary video productions" in prompt:
                kind = "summary"
            elif "Required JSON Output Structure:" in prompt or "candidate_titles" in prompt:
                kind = "outline"
            elif "Required Action:" in prompt or "Current Chapter Status:" in prompt:
                kind = "revise_length"
            else:
                kind = "chapter"

        if kind == "summary":
            ch_title = kwargs.get("chapter_title", "Chapter Title")
            ch_index = kwargs.get("chapter_index", 1)
            topic = kwargs.get("topic", "the subject")
            summary_dict = {
                "narrative_summary": (
                    f"Chapter {ch_index} ({ch_title}) established the core narrative developments and historical context. "
                    f"Key mechanical milestones were reached under competitive conditions in the evolution of {topic}. "
                    "The momentum set the stage for subsequent technical and competitive challenges."
                ),
                "key_facts": [
                    f"Milestone engineering benchmark established in Chapter {ch_index}",
                    f"Primary competition dynamics and records set for {topic} in Chapter {ch_index}",
                ],
            }
            return json.dumps(summary_dict, indent=2)

        elif kind == "outline":
            topic = kwargs.get("topic", "The 1969 Dodge Charger Legend")
            target_min = float(kwargs.get("target_duration_minutes", 12.0))
            wpm = int(kwargs.get("words_per_minute", 140))
            total_words = int(target_min * wpm)

            # Generate 4 chapters
            words_per_ch = total_words // 4
            chapters_data = [
                {
                    "chapter_index": 1,
                    "title": f"The Genesis of {topic}",
                    "purpose": "Introduce the cultural moment and engineering rivalry that birthed the machine.",
                    "key_points": [
                        "Detroit streets in the late 1960s",
                        "The raw competition between the Big Three",
                    ],
                    "target_words": words_per_ch,
                },
                {
                    "chapter_index": 2,
                    "title": "Engineering the Beast: Iron and Aerodynamics",
                    "purpose": "Examine the technical development, powerplants, and racing homologation.",
                    "key_points": [
                        "NASCAR aero development in wind tunnels",
                        "The legendary 426 Hemi engine development",
                    ],
                    "target_words": words_per_ch,
                },
                {
                    "chapter_index": 3,
                    "title": "On the Track and Dragstrip Dominance",
                    "purpose": "Document track triumphs, quarter-mile dominance, and legendary driver rivalries.",
                    "key_points": [
                        "Quarter-mile acceleration and tire smoke",
                        "Breaking speed records at Talladega",
                    ],
                    "target_words": words_per_ch,
                },
                {
                    "chapter_index": 4,
                    "title": "The Enduring Icon: Hollywood and Collector Lore",
                    "purpose": "Analyze how Hollywood film appearances and auctions cemented legendary status.",
                    "key_points": [
                        "Dukes of Hazzard and Bullitt screen presence",
                        "Modern six-figure auction values",
                    ],
                    "target_words": total_words - (words_per_ch * 3),
                },
            ]

            outline_dict = {
                "topic": topic,
                "target_duration_minutes": target_min,
                "words_per_minute": wpm,
                "total_target_words": total_words,
                "candidate_titles": [
                    f"The Untold Story of {topic}",
                    f"How the {topic} Conquered the Asphalt",
                    f"The Muscle Car King: {topic} Explained",
                ],
                "opening_hook": (
                    f"In 1969, an unholy thunder cracked across the backstreets of Detroit as {topic} "
                    "unleashed four hundred and twenty-five horses of pure, unadulterated iron."
                ),
                "chapters": chapters_data,
            }
            return json.dumps(outline_dict, indent=2)

        elif kind == "revise_length":
            # Length revision fake generator
            target_words = int(kwargs.get("target_words", 400))
            ch_title = kwargs.get("chapter_title", "Revised Chapter")
            return self._build_fake_chapter_markdown(
                title=ch_title,
                target_words=target_words,
                is_revision=True,
            )

        else:
            # Chapter generator
            ch_title = kwargs.get("chapter_title", "Chapter Title")
            ch_index = kwargs.get("chapter_index", 1)
            target_words = int(kwargs.get("target_words", 420))
            return self._build_fake_chapter_markdown(
                title=ch_title,
                target_words=target_words,
                chapter_index=ch_index,
            )

    def _build_fake_chapter_markdown(
        self,
        title: str,
        target_words: int,
        chapter_index: int = 1,
        is_revision: bool = False,
    ) -> str:
        """Synthesize realistic markdown chapter text with exact tag patterns matching target words."""
        # Clean title of any markdown markers
        clean_title = title.replace("#", "").strip()

        base_sentences = [
            (
                f"The asphalt trembled under the immense vibration of Detroit steel as the engine roared to life.",
                "black classic muscle car rumbling on a rural highway at sunset",
                "zoom_in",
                "stock",
                "classic muscle car rumbling highway",
                None,
            ),
            (
                f"Under the sculpted hood lay a dual-carbureted monster producing four hundred and twenty-five gross horsepower.",
                "closeup shot of massive chrome air cleaner and dual four-barrel carburetors",
                "pan_left",
                "local",
                "dual four barrel engine bay carburetor",
                "426 Hemi factory rated at 425 gross horsepower at 5000 RPM",
            ),
            (
                f"Engineers worked through grueling night shifts in clandestine Michigan workshops to beat their crosstown rivals.",
                "vintage black and white photo of automotive draftsmen over drafting tables",
                "zoom_out",
                "stock",
                "1960s automotive drafting room engineers",
                None,
            ),
            (
                f"When the green flag dropped, the rear tires broke traction in a violent cloud of burning white smoke.",
                "slow motion tire smoke billowing from wide rear drag radials at dragstrip",
                "zoom_in",
                "stock",
                "dragstrip burnout tire smoke slow motion",
                "Quarter mile completed in under 13.8 seconds on bias-ply tires",
            ),
            (
                f"Every curve of the sheet metal was refined to achieve maximum high-speed aerodynamic stability.",
                "wind tunnel smoke stream gliding smoothly over vintage fastback roofline",
                "pan_right",
                "ai_image",
                "wind tunnel aerodynamic smoke stream car",
                None,
            ),
            (
                f"By the end of the racing season, the machine had claimed a permanent place in automotive history.",
                "victory lane celebration with vintage trophy and cheering racing pit crew",
                "static",
                "stock",
                "1960s victory lane trophy celebration racing",
                "Produced in limited quantities of under 500 homologation units",
            ),
        ]

        # Assemble paragraphs
        # Target roughly ~18-22 words per sentence
        total_generated_words = 0
        paragraphs = []
        current_para = []

        # We repeat or trim sentences to get close to target_words
        cycle_idx = 0
        while total_generated_words < target_words:
            sent_tpl = base_sentences[cycle_idx % len(base_sentences)]
            cycle_idx += 1

            text, visual, motion, source, search, verify = sent_tpl
            tag_str = f"[visual: {visual}] [motion: {motion}] [source: {source}] [search: {search}]"
            if verify:
                tag_str += f" [verify: {verify}]"

            sentence_line = f"{text} {tag_str}"
            current_para.append(sentence_line)
            total_generated_words += len(text.split())

            # Group every 3 sentences into a paragraph
            if len(current_para) >= 3:
                paragraphs.append(" ".join(current_para))
                current_para = []

        if current_para:
            paragraphs.append(" ".join(current_para))

        joined_paragraphs = "\n\n".join(paragraphs)
        return f"## {clean_title}\n\n{joined_paragraphs}\n"
