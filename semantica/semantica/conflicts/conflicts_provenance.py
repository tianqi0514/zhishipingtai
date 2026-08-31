"""
Provenance-enabled wrapper for conflict detection with unified backend.

This module provides unified provenance backend integration for SourceTracker
while maintaining 100% backward compatibility.

Usage:
    from semantica.conflicts.conflicts_provenance import SourceTrackerWithUnifiedBackend
    
    tracker = SourceTrackerWithUnifiedBackend()
    tracker.track_property_source(entity_id, property_name, value, source)

Author: Semantica Contributors
License: MIT
"""

from typing import Optional, Dict, Any, List
from datetime import datetime


class SourceTrackerWithUnifiedBackend:
    """SourceTracker using unified provenance backend."""
    
    def __init__(
        self,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        """Initialize with unified backend or fallback to legacy."""
        from .source_tracker import SourceTracker

        self._agent_id = agent_id or self.__class__.__name__
        self._is_automated = is_automated

        try:
            from semantica.provenance import ProvenanceManager
            self._unified_manager = ProvenanceManager()
            self._use_unified = True
        except ImportError:
            self._use_unified = False

        self._original_tracker = SourceTracker(**config)

    def track_property_source(self, entity_id: str, property_name: str, value: Any, source: Any, **metadata):
        """Track property source with unified backend."""
        activity_started_at_time = datetime.utcnow().isoformat()
        if self._use_unified:
            from semantica.provenance import SourceReference

            source_ref = SourceReference(
                document=source.document if hasattr(source, 'document') else str(source),
                page=getattr(source, 'page', None),
                section=getattr(source, 'section', None),
                confidence=getattr(source, 'confidence', 1.0)
            )

            metadata.setdefault("agent_id", self._agent_id)
            metadata.setdefault("agent_type", "software_agent")
            metadata.setdefault("is_automated", self._is_automated)
            metadata.setdefault("activity_started_at_time", activity_started_at_time)
            metadata.setdefault("activity_ended_at_time", datetime.utcnow().isoformat())

            self._unified_manager.track_property_source(
                entity_id=entity_id,
                property_name=property_name,
                value=value,
                source=source_ref,
                **metadata
            )
        else:
            self._original_tracker.track_property_source(
                entity_id, property_name, value, source, **metadata
            )
    
    def __getattr__(self, name):
        return getattr(self._original_tracker, name)


__all__ = ['SourceTrackerWithUnifiedBackend']
