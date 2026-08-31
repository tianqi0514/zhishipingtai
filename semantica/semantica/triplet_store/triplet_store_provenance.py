"""
Provenance-enabled wrapper for triplet storage.

Tracks: triplets stored

Usage:
    from semantica.triplet_store.triplet_store_provenance import TripletStoreWithProvenance
    
    store = TripletStoreWithProvenance(provenance=True)
    store.add_triplet(subject, predicate, object, source="kg.json")

Author: Semantica Contributors
License: MIT
"""

from typing import Any, Optional
from datetime import datetime
import uuid


class TripletStoreWithProvenance:
    """Triplet store with provenance tracking."""

    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        from .triplet_store import TripletStore

        self.provenance = provenance
        self._store = TripletStore(**config)
        self._prov_manager = None
        self._agent_id = agent_id or self.__class__.__name__
        self._is_automated = is_automated

        if provenance:
            try:
                from semantica.provenance import ProvenanceManager
                self._prov_manager = ProvenanceManager()
            except ImportError:
                self.provenance = False

    def add_triplet(self, subject: Any, predicate: Any, obj: Any, source: str = None, **kwargs):
        """Add triplet with provenance tracking."""
        activity_started_at_time = datetime.utcnow().isoformat()
        result = self._store.add_triplet(subject, predicate, obj, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance and self._prov_manager:
            self._prov_manager.track_entity(
                entity_id=f"triplet_{uuid.uuid4().hex[:8]}",
                source=source or "triplet_store",
                entity_type="triplet",
                agent_id=self._agent_id,
                agent_type="software_agent",
                is_automated=self._is_automated,
                activity_started_at_time=activity_started_at_time,
                activity_ended_at_time=activity_ended_at_time,
                metadata={
                    "subject": str(subject),
                    "predicate": str(predicate),
                    "object": str(obj)
                }
            )
        
        return result
    
    def __getattr__(self, name):
        return getattr(self._store, name)


__all__ = ['TripletStoreWithProvenance']
