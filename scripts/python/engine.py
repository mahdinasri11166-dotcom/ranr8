# -*- coding: utf-8 -*-
"""Headless model engine — load/unload/chat with performance defaults."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from model_meta import read_gguf_metadata, estimate_ram_gb
from app_logging import get_logger

log = get_logger("engine")


def default_threads() -> int:
    n = os.cpu_count() or 4
    # leave 1–2 cores free for UI
    return max(2, min(16, n - 1 if n > 2 else n))


def default_batch(n_ctx: int) -> int:
    # Larger batch = faster prompt ingest; cap by context
    if n_ctx <= 2048:
        return 256
    if n_ctx <= 4096:
        return 512
    return min(1024, max(256, n_ctx // 8))


class LocalEngine:
    def __init__(self):
        self.llm = None
        self.model_path: Path | None = None
        self.n_gpu_layers = 0
        self.n_ctx = 4096
        self.n_threads = default_threads()
        self.n_batch = 512
        self.meta: dict[str, Any] = {}
        self.load_opts: dict[str, Any] = {}

    def inspect(self, path: Path | str) -> dict[str, Any]:
        meta = read_gguf_metadata(path)
        est = estimate_ram_gb(meta.get("size_bytes") or 0, self.n_ctx, self.n_gpu_layers)
        meta["estimate"] = est
        return meta

    def load(
        self,
        path: Path,
        n_ctx: int = 4096,
        n_gpu_layers: int = 0,
        n_threads: int | None = None,
        n_batch: int | None = None,
        use_mmap: bool = True,
        use_mlock: bool = False,
        flash_attn: bool | None = None,
    ) -> str:
        try:
            from llama_cpp import Llama
        except ImportError:
            return "llama-cpp-python نصب نیست"

        path = Path(path)
        try:
            self.unload()
            self.meta = read_gguf_metadata(path)
            self.n_ctx = int(n_ctx)
            self.n_gpu_layers = int(n_gpu_layers)
            self.n_threads = max(1, int(n_threads if n_threads is not None else default_threads()))
            self.n_batch = int(n_batch if n_batch is not None else default_batch(self.n_ctx))

            kwargs: dict[str, Any] = dict(
                model_path=str(path),
                n_ctx=self.n_ctx,
                n_gpu_layers=self.n_gpu_layers,
                n_threads=self.n_threads,
                n_batch=self.n_batch,
                verbose=False,
                use_mmap=bool(use_mmap),
                use_mlock=bool(use_mlock),
                logits_all=False,
                embedding=False,
            )
            # optional speed flags (version-dependent)
            if self.n_gpu_layers != 0:
                kwargs["offload_kqv"] = True
            if flash_attn is True:
                kwargs["flash_attn"] = True

            # Try progressive kwargs strip for older llama-cpp-python
            attempt = dict(kwargs)
            last_err = None
            for drop in (
                [],
                ["flash_attn"],
                ["flash_attn", "offload_kqv"],
                ["flash_attn", "offload_kqv", "logits_all", "embedding"],
                ["flash_attn", "offload_kqv", "logits_all", "embedding", "use_mlock"],
                ["flash_attn", "offload_kqv", "logits_all", "embedding", "use_mlock", "n_batch"],
            ):
                for k in drop:
                    attempt.pop(k, None)
                try:
                    self.llm = Llama(**attempt)
                    self.load_opts = dict(attempt)
                    last_err = None
                    break
                except TypeError as e:
                    last_err = e
                    attempt = dict(kwargs)
                    for k in drop:
                        attempt.pop(k, None)
                    continue
                except Exception as e:
                    last_err = e
                    break

            if self.llm is None:
                raise last_err or RuntimeError("load failed")

            self.model_path = path
            log.info(
                "loaded %s ngl=%s ctx=%s threads=%s batch=%s",
                path.name,
                self.n_gpu_layers,
                self.n_ctx,
                self.n_threads,
                self.n_batch,
            )
            return ""
        except Exception as e:
            self.llm = None
            self.model_path = None
            msg = str(e)
            low = msg.lower()
            log.error("load failed: %s", msg)
            if "out of memory" in low or "oom" in low:
                return "حافظه کافی نیست. ctx یا GPU layers را کم کنید.\n" + msg
            if "cuda" in low:
                return "مشکل GPU/CUDA — n_gpu_layers=0 یا wheel مناسب GPU.\n" + msg
            return msg

    def unload(self) -> None:
        if self.llm is not None:
            log.info("unload %s", self.model_path)
        self.llm = None
        self.model_path = None
        self.meta = {}
        self.load_opts = {}

    def chat(
        self,
        messages,
        temperature=0.7,
        max_tokens=1024,
        top_p=0.9,
        top_k: int = 40,
        repeat_penalty: float = 1.08,
        stream=True,
    ):
        if not self.llm:
            raise RuntimeError("مدلی لود نشده")
        kwargs = dict(
            messages=messages,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            top_p=float(top_p),
            stream=stream,
        )
        # optional sampling knobs
        for key, val in (("top_k", int(top_k)), ("repeat_penalty", float(repeat_penalty))):
            kwargs[key] = val
        try:
            return self.llm.create_chat_completion(**kwargs)
        except TypeError:
            kwargs.pop("top_k", None)
            try:
                return self.llm.create_chat_completion(**kwargs)
            except TypeError:
                kwargs.pop("repeat_penalty", None)
                return self.llm.create_chat_completion(**kwargs)

    def performance_summary(self) -> str:
        if not self.model_path:
            return "مدلی لود نشده"
        return (
            f"{self.model_path.name} | ctx={self.n_ctx} | "
            f"threads={self.n_threads} | batch={self.n_batch} | "
            f"ngl={self.n_gpu_layers}"
        )
