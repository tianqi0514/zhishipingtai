"""
Provenance-enabled wrapper for context management.

Usage:
    from semantica.context.context_provenance import ContextManagerWithProvenance
    
    ctx = ContextManagerWithProvenance(provenance=True)
    ctx.add_context("context data", source="doc1.pdf")

Author: Semantica Contributors
License: MIT
"""

from typing import Optional, Any
from datetime import datetime
import uuid


class ContextManagerWithProvenance:
    """Context manager with provenance tracking."""
    
    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        """Initialize context manager with optional provenance."""
        from .context_manager import ContextManager

        self.provenance = provenance
        self._context_manager = ContextManager(**config)
        self._prov_manager = None
        self._agent_id = agent_id or self.__class__.__name__
        self._is_automated = is_automated

        if provenance:
            try:
                from semantica.provenance import ProvenanceManager
                self._prov_manager = ProvenanceManager()
            except ImportError:
                self.provenance = False

    def add_context(self, context: Any, source: Optional[str] = None, **kwargs):
        """Add context with provenance tracking."""
        activity_started_at_time = datetime.utcnow().isoformat()
        result = self._context_manager.add_context(context, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance and self._prov_manager:
            self._prov_manager.track_entity(
                entity_id=f"context_{uuid.uuid4().hex[:8]}",
                source=source or "context_manager",
                entity_type="context",
                agent_id=self._agent_id,
                agent_type="software_agent",
                is_automated=self._is_automated,
                activity_started_at_time=activity_started_at_time,
                activity_ended_at_time=activity_ended_at_time,
                metadata={"context_preview": str(context)[:100]}
            )
        
        return result
    
    def __getattr__(self, name):
        return getattr(self._context_manager, name)


__all__ = ['ContextManagerWithProvenance']
