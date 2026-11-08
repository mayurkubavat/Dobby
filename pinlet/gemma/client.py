"""Minimal, dependency-free client for local Gemma models served by Ollama.

Every call goes to ``/api/chat`` with ``format`` set to a JSON schema, so the
reply is constrained to that schema and always parses. Model availability is
checked by exact name and cached for a short TTL. Each call is timed and
counted so callers can report how often Gemma ran, how long it took and how
often it failed.

The module only uses the standard library (plus Pillow for image encoding)
so it can be lifted into a shared package later without changes.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

# (method, url, json_body_or_None, timeout_seconds) -> parsed JSON response
Transport = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]

_LEGACY_SUFFIXES = ("/api/generate", "/api/chat", "/api/tags", "/api")


class GemmaError(RuntimeError):
    """Raised when a Gemma call fails, times out, or returns unusable output."""


def normalize_base_url(endpoint: str) -> str:
    """Accepts a bare host URL or a legacy ``.../api/generate`` endpoint."""
    url = (endpoint or "http://127.0.0.1:11434").strip().rstrip("/")
    for suffix in _LEGACY_SUFFIXES:
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url.rstrip("/")


def encode_image(image: Image.Image, quality: int = 90) -> str:
    """JPEG-encodes a PIL image and returns base64 text for the Ollama API."""
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _urllib_transport(
    method: str, url: str, body: dict[str, Any] | None, timeout: float
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": "pinlet"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _model_matches(wanted: str, installed: Iterable[str]) -> bool:
    """Exact match, treating an untagged name as ``:latest``."""
    candidates = {wanted}
    if ":" not in wanted:
        candidates.add(f"{wanted}:latest")
    return any(name in candidates for name in installed)


def first_token_logprobs(raw: dict[str, Any]) -> list[tuple] | None:
    """The first generated token's alternatives as ``[(token, logprob), ...]``.

    Reads Ollama's ``logprobs`` list (``[{token, logprob, top_logprobs: [...]}, ...]``)
    and, for OpenAI-compatible servers, ``choices[0].logprobs.content``. Returns
    None when the reply carries no logprobs at all.
    """
    entries = raw.get("logprobs")
    if entries is None:
        try:
            entries = raw["choices"][0]["logprobs"]["content"]
        except (KeyError, IndexError, TypeError):
            entries = None
    if isinstance(entries, dict):  # some servers wrap the list
        entries = entries.get("content")
    if not entries or not isinstance(entries, list):
        return None
    first = entries[0] if isinstance(entries[0], dict) else {}
    alts = first.get("top_logprobs") or []
    out: list[tuple] = []
    for alt in alts:
        if isinstance(alt, dict) and "token" in alt and "logprob" in alt:
            try:
                out.append((str(alt["token"]), float(alt["logprob"])))
            except (TypeError, ValueError):
                continue
    if not out and "token" in first and "logprob" in first:
        try:
            out.append((str(first["token"]), float(first["logprob"])))
        except (TypeError, ValueError):
            pass
    return out or None


@dataclass
class _ModelStats:
    calls: int = 0
    errors: int = 0
    total_ms: float = 0.0


@dataclass
class GemmaStats:
    """Call counts and timings for a Gemma client, overall and per model."""

    calls: int = 0
    errors: int = 0
    total_ms: float = 0.0
    last_error: str | None = None
    per_model: dict[str, _ModelStats] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """The stats as a JSON-ready dict, with average latency and error rate."""
        ok = self.calls - self.errors
        return {
            "calls": self.calls,
            "errors": self.errors,
            "mean_ms": round(self.total_ms / self.calls, 1) if self.calls else None,
            "success_rate": round(ok / self.calls, 3) if self.calls else None,
            "last_error": self.last_error,
            "per_model": {
                m: {
                    "calls": s.calls,
                    "errors": s.errors,
                    "mean_ms": round(s.total_ms / s.calls, 1) if s.calls else None,
                }
                for m, s in self.per_model.items()
            },
        }


class GemmaClient:
    """Schema-constrained JSON calls to local Gemma models through Ollama."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 25.0,
        keep_alive: str | None = "10m",
        availability_ttl: float = 30.0,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.availability_ttl = availability_ttl
        self._transport: Transport = transport or _urllib_transport
        self._models: set[str] | None = None
        self._models_at: float = 0.0
        self._lock = threading.Lock()
        self._stats = GemmaStats()

    # ------------------------------------------------------------------ models

    def installed_models(self, refresh: bool = False) -> set[str]:
        """Names reported by ``/api/tags``; empty when Ollama is unreachable."""
        now = time.monotonic()
        if (
            not refresh
            and self._models is not None
            and now - self._models_at < self.availability_ttl
        ):
            return self._models
        try:
            data = self._transport("GET", f"{self.base_url}/api/tags", None, 2.0)
            models = {m.get("name", "") for m in data.get("models", []) if m.get("name")}
        except Exception as exc:  # unreachable, refused, bad JSON
            logger.debug("Ollama /api/tags failed: %s", exc)
            models = set()
        self._models, self._models_at = models, now
        return models

    def has_model(self, model: str) -> bool:
        """True when ``model`` is installed on the Ollama server."""
        return _model_matches(model, self.installed_models())

    def has_models(self, models: Iterable[str]) -> bool:
        """True when every model in ``models`` is installed on the Ollama server."""
        installed = self.installed_models()
        return all(_model_matches(m, installed) for m in models)

    # ------------------------------------------------------------------- calls

    def chat_json(
        self,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        images: list[str] | None = None,
        timeout: float | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Sends one chat turn and returns the reply parsed as a JSON object.

        ``images`` are base64 strings (see :func:`encode_image`). Raises
        :class:`GemmaError` on transport errors, a missing model, or a reply
        that is not a JSON object.
        """
        user_msg: dict[str, Any] = {"role": "user", "content": user}
        if images:
            user_msg["images"] = list(images)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, user_msg],
            "format": schema,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens:
            payload["options"]["num_predict"] = int(max_tokens)
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive

        start = time.perf_counter()
        error: str | None = None
        try:
            if not self.has_model(model):
                raise GemmaError(f"model '{model}' is not installed in Ollama at {self.base_url}")
            raw = self._transport(
                "POST", f"{self.base_url}/api/chat", payload, timeout or self.timeout
            )
            content = (raw.get("message") or {}).get("content", "")
            try:
                parsed = json.loads(content)
            except (TypeError, ValueError) as exc:
                raise GemmaError(f"reply is not valid JSON: {content[:200]!r}") from exc
            if not isinstance(parsed, dict):
                raise GemmaError(f"reply is not a JSON object: {content[:200]!r}")
            return parsed
        except GemmaError as exc:
            error = str(exc)
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise GemmaError(error) from exc
        finally:
            self._record(model, (time.perf_counter() - start) * 1000.0, error)

    def chat_logprobs(
        self,
        model: str,
        system: str,
        user: str,
        images: list[str] | None = None,
        top_logprobs: int = 20,
        timeout: float | None = None,
        max_tokens: int = 1,
    ) -> dict[str, Any]:
        """Sends one chat turn asking for the first token's log-probabilities.

        Returns ``{"content": str, "top": [(token, logprob), ...] or None}``.
        ``top`` is None when this Ollama does not return logprobs (older than
        the release that added them), so callers can fall back. Raises
        :class:`GemmaError` on transport errors or a missing model.
        """
        user_msg: dict[str, Any] = {"role": "user", "content": user}
        if images:
            user_msg["images"] = list(images)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, user_msg],
            "stream": False,
            "logprobs": True,
            "top_logprobs": int(max(1, min(20, top_logprobs))),
            "options": {"temperature": 0.0, "num_predict": int(max_tokens)},
        }
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive

        start = time.perf_counter()
        error: str | None = None
        try:
            if not self.has_model(model):
                raise GemmaError(f"model '{model}' is not installed in Ollama at {self.base_url}")
            raw = self._transport(
                "POST", f"{self.base_url}/api/chat", payload, timeout or self.timeout
            )
            content = str((raw.get("message") or {}).get("content", ""))
            return {"content": content, "top": first_token_logprobs(raw)}
        except GemmaError as exc:
            error = str(exc)
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise GemmaError(error) from exc
        finally:
            self._record(model, (time.perf_counter() - start) * 1000.0, error)

    def warmup(self, model: str, timeout: float = 120.0) -> bool:
        """Loads ``model`` into memory (empty prompt) so the first real call is not a cold start."""
        if not self.has_model(model):
            return False
        try:
            body: dict[str, Any] = {"model": model, "prompt": "", "stream": False}
            if self.keep_alive is not None:
                body["keep_alive"] = self.keep_alive
            self._transport("POST", f"{self.base_url}/api/generate", body, timeout)
            return True
        except Exception as exc:
            logger.debug("Gemma warmup failed: %s", exc)
            return False

    # ------------------------------------------------------------------- stats

    def _record(self, model: str, ms: float, error: str | None) -> None:
        with self._lock:
            s = self._stats
            s.calls += 1
            s.total_ms += ms
            m = s.per_model.setdefault(model, _ModelStats())
            m.calls += 1
            m.total_ms += ms
            if error:
                s.errors += 1
                m.errors += 1
                s.last_error = error

    def stats(self) -> dict[str, Any]:
        """A snapshot of this client's call stats and server URL."""
        with self._lock:
            out = self._stats.as_dict()
        out["base_url"] = self.base_url
        return out
