"""
Provenance-enabled wrapper for visualization.

Usage:
    from semantica.visualization.visualization_provenance import VisualizerWithProvenance
    
    viz = VisualizerWithProvenance(provenance=True)
    viz.visualize(data, output="graph.png")

Author: Semantica Contributors
License: MIT
"""

from typing import Any, Optional
from datetime import datetime
import uuid


class VisualizerWithProvenance:
    """Visualizer with provenance tracking."""

    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        from .visualizer import Visualizer

        self.provenance = provenance
        self._visualizer = Visualizer(**config)
        self._prov_manager = None
        self._agent_id = agent_id or self.__class__.__name__
        self._is_automated = is_automated

        if provenance:
            try:
                from semantica.provenance import ProvenanceManager
                self._prov_manager = ProvenanceManager()
            except ImportError:
                self.provenance = False

    def visualize(self, data: Any, output: str = None, **kwargs):
        """Visualize data with provenance tracking."""
        activity_started_at_time = datetime.utcnow().isoformat()
        result = self._visualizer.visualize(data, output=output, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance and self._prov_manager:
            self._prov_manager.track_entity(
                entity_id=f"viz_{uuid.uuid4().hex[:8]}",
                source="visualization",
                entity_type="visualization",
                agent_id=self._agent_id,
                agent_type="software_agent",
                is_automated=self._is_automated,
                activity_started_at_time=activity_started_at_time,
                activity_ended_at_time=activity_ended_at_time,
                metadata={"output": output, "type": kwargs.get('type', 'unknown')}
            )
        
        return result
    
    def __getattr__(self, name):
        return getattr(self._visualizer, name)


__all__ = ['VisualizerWithProvenance']
