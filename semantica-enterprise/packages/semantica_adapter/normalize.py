from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NormalizedChunk:
    chunk_id: str
    ordinal: int
    text: str
    content_hash: str
    start_index: int
    end_index: int
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_and_split(
    text: str,
    *,
    version_id: str,
    element_key: str,
    method: str = "recursive",
    chunk_size: int = 800,
    chunk_overlap: int = 120,
    config: dict[str, Any] | None = None,
) -> list[NormalizedChunk]:
    """Normalize and split one parsed element with Semantica and stable identities."""
    from semantica.normalize import TextNormalizer
    from semantica.split import TextSplitter

    options = dict(config or {})
    normalized = TextNormalizer(config=options.get("normalization", {})).normalize(
        text,
        unicode_form=str(options.get("unicode_form", "NFKC")),
        case="preserve",
    )
    if not normalized or not str(normalized).strip():
        return []
    splitter = TextSplitter(
        method=method,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        **options.get("split", {}),
    )
    result: list[NormalizedChunk] = []
    for ordinal, item in enumerate(splitter.split(str(normalized))):
        chunk_text = item.text.strip()
        if not chunk_text:
            continue
        content_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        stable_key = f"{version_id}:{element_key}:{ordinal}:{content_hash}"
        result.append(
            NormalizedChunk(
                chunk_id=hashlib.sha256(stable_key.encode("utf-8")).hexdigest(),
                ordinal=ordinal,
                text=chunk_text,
                content_hash=content_hash,
                start_index=int(item.start_index),
                end_index=int(item.end_index),
                metadata=dict(item.metadata or {}),
            )
        )
    return result
