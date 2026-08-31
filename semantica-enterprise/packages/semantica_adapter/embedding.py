from __future__ import annotations

from typing import Any

import numpy as np


class SemanticEmbedder:
    """Production guard around Semantica TextEmbedder (hash fallback is forbidden)."""

    def __init__(self, model_name: str, config: dict[str, Any] | None = None):
        from semantica.embeddings.text_embedder import TextEmbedder

        options = dict(config or {})
        method = str(options.pop("method", "fastembed"))
        self._embedder = TextEmbedder(
            model_name=model_name,
            method=method,
            device=str(options.pop("device", "cpu")),
            normalize=bool(options.pop("normalize", True)),
            **options,
        )
        if self._embedder.get_method() == "fallback":
            raise RuntimeError(f"向量模型 {model_name} 加载失败，禁止使用非语义哈希降级")

    @property
    def dimension(self) -> int:
        return int(self._embedder.get_embedding_dimension())

    @property
    def method(self) -> str:
        return self._embedder.get_method()

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        vectors = self._embedder.embed_batch(texts)
        if len(vectors) != len(texts):
            raise RuntimeError("向量模型返回数量与输入不一致")
        return vectors

    def embed_query(self, text: str) -> np.ndarray:
        return self._embedder.embed_text(text)
