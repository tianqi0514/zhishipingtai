"""
Provenance-enabled wrapper for normalization.

Usage:
    from semantica.normalize.normalize_provenance import NormalizerWithProvenance
    
    normalizer = NormalizerWithProvenance(provenance=True)
    normalized_data = normalizer.normalize(data)

Author: Semantica Contributors
License: MIT
"""

from typing import Any, Optional
from datetime import datetime
import uuid


class NormalizerWithProvenance:
    """Normalizer with provenance tracking."""

    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        from .normalizer import Normalizer

        self.provenance = provenance
        self._normalizer = Normalizer(**config)
        self._prov_manager = None
        self._agent_id = agent_id or self.__class__.__name__
        self._is_automated = is_automated

        if provenance:
            try:
                from semantica.provenance import ProvenanceManager
                self._prov_manager = ProvenanceManager()
            except ImportError:
                self.provenance = False

    def normalize(self, data: Any, source: str = None, **kwargs):
        """Normalize data with provenance tracking."""
        activity_started_at_time = datetime.utcnow().isoformat()
        result = self._normalizer.normalize(data, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance and self._prov_manager:
            self._prov_manager.track_entity(
                entity_id=f"normalize_{uuid.uuid4().hex[:8]}",
                source=source or "normalization",
                entity_type="normalized_data",
                agent_id=self._agent_id,
                agent_type="software_agent",
                is_automated=self._is_automated,
                activity_started_at_time=activity_started_at_time,
                activity_ended_at_time=activity_ended_at_time,
                metadata={"method": kwargs.get('method', 'default')}
            )
        
        return result
    
    def __getattr__(self, name):
        return getattr(self._normalizer, name)


__all__ = ['NormalizerWithProvenance']
