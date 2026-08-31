"""
Provenance-enabled wrapper for graph storage.

Tracks: nodes added, edges created

Usage:
    from semantica.graph_store.graph_store_provenance import GraphStoreWithProvenance
    
    store = GraphStoreWithProvenance(provenance=True)
    store.add_node(node, source="doc1.pdf")

Author: Semantica Contributors
License: MIT
"""

from typing import Any, Optional
from datetime import datetime
import uuid


class GraphStoreWithProvenance:
    """Graph store with provenance tracking."""

    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        from .graph_store import GraphStore

        self.provenance = provenance
        self._store = GraphStore(**config)
        self._prov_manager = None
        self._agent_id = agent_id or self.__class__.__name__
        self._is_automated = is_automated

        if provenance:
            try:
                from semantica.provenance import ProvenanceManager
                self._prov_manager = ProvenanceManager()
            except ImportError:
                self.provenance = False

    def add_node(self, node: Any, source: str = None, **kwargs):
        """Add node with provenance tracking."""
        activity_started_at_time = datetime.utcnow().isoformat()
        result = self._store.add_node(node, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance and self._prov_manager:
            node_id = getattr(node, 'id', f"node_{uuid.uuid4().hex[:8]}")
            self._prov_manager.track_entity(
                entity_id=node_id,
                source=source or "graph_store",
                entity_type="graph_node",
                agent_id=self._agent_id,
                agent_type="software_agent",
                is_automated=self._is_automated,
                activity_started_at_time=activity_started_at_time,
                activity_ended_at_time=activity_ended_at_time,
                metadata={"properties": getattr(node, 'properties', {})}
            )
        
        return result
    
    def __getattr__(self, name):
        return getattr(self._store, name)


__all__ = ['GraphStoreWithProvenance']
