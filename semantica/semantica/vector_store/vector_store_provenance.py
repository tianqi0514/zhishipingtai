"""
Provenance-enabled wrapper for vector storage.

Tracks: vectors stored, dimensions

Usage:
    from semantica.vector_store.vector_store_provenance import VectorStoreWithProvenance
    
    store = VectorStoreWithProvenance(provenance=True)
    store.add_vectors(vectors, source="embeddings.npy")

Author: Semantica Contributors
License: MIT
"""

from typing import List, Any, Optional
from datetime import datetime
import uuid


class VectorStoreWithProvenance:
    """Vector store with provenance tracking."""

    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        from .vector_store import VectorStore

        self.provenance = provenance
        self._store = VectorStore(**config)
        self._prov_manager = None
        self._agent_id = agent_id or self.__class__.__name__
        self._is_automated = is_automated

        if provenance:
            try:
                from semantica.provenance import ProvenanceManager
                self._prov_manager = ProvenanceManager()
            except ImportError:
                self.provenance = False

    def add_vectors(self, vectors: List[Any], source: str = None, **kwargs):
        """Add vectors with provenance tracking."""
        activity_started_at_time = datetime.utcnow().isoformat()
        result = self._store.add_vectors(vectors, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance and self._prov_manager:
            self._prov_manager.track_entity(
                entity_id=f"vectors_{uuid.uuid4().hex[:8]}",
                source=source or "vector_store",
                entity_type="vector_collection",
                agent_id=self._agent_id,
                agent_type="software_agent",
                is_automated=self._is_automated,
                activity_started_at_time=activity_started_at_time,
                activity_ended_at_time=activity_ended_at_time,
                metadata={
                    "count": len(vectors),
                    "dimensions": len(vectors[0]) if vectors else 0
                }
            )
        
        return result
    
    def __getattr__(self, name):
        return getattr(self._store, name)


__all__ = ['VectorStoreWithProvenance']
