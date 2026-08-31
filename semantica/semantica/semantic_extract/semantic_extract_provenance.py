"""
Provenance-enabled wrappers for semantic extraction.

This module provides provenance tracking for all semantic extraction operations:
- Named Entity Recognition (NER)
- Relation Extraction
- Event Detection
- Coreference Resolution
- Triplet Extraction

All classes wrap the original extractors and add optional provenance tracking
without modifying existing functionality.

Usage:
    from semantica.semantic_extract.semantic_extract_provenance import (
        NERExtractorWithProvenance,
        RelationExtractorWithProvenance,
        EventDetectorWithProvenance
    )
    
    # Enable provenance tracking
    ner = NERExtractorWithProvenance(provenance=True)
    entities = ner.extract("Steve Jobs founded Apple.", source="document.pdf")
    
    # Provenance is automatically tracked for each extracted entity
    # Access via: ner._prov_manager.get_lineage(entity_id)

Features:
    - Zero breaking changes - works exactly like original classes
    - Opt-in provenance via provenance=True parameter
    - Tracks: entity text, labels, confidence, source documents
    - Complete lineage tracing
    - Graceful degradation if provenance module unavailable

Author: Semantica Contributors
License: MIT
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid


class ProvenanceMixin:
    """
    Mixin to add provenance tracking to any extractor class.
    
    This mixin provides the common provenance infrastructure that can be
    added to any extraction class without modifying its core functionality.
    """
    
    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **kwargs,
    ):
        """
        Initialize provenance tracking.

        Args:
            provenance: Enable provenance tracking (default: False)
            agent_id: Agent identifier for accountability (issue #825); defaults
                to the wrapping class name
            is_automated: Whether this agent acted without direct human review
            **kwargs: Additional arguments passed to parent class
        """
        self.provenance = provenance
        self._prov_manager = None
        self._agent_id = agent_id or self.__class__.__name__
        self._is_automated = is_automated

        if provenance:
            try:
                from semantica.provenance import ProvenanceManager
                self._prov_manager = ProvenanceManager()
            except ImportError:
                # Graceful degradation if provenance module not available
                self.provenance = False

    def _track_extraction(
        self,
        entity_id: str,
        source: str,
        entity_type: str,
        **metadata
    ) -> None:
        """
        Track extraction with provenance.

        Args:
            entity_id: Unique identifier for extracted entity
            source: Source document or text
            entity_type: Type of entity (e.g., 'named_entity', 'relation')
            **metadata: Additional metadata to track
        """
        if self.provenance and self._prov_manager:
            activity_started_at_time = metadata.pop("activity_started_at_time", None)
            activity_ended_at_time = metadata.pop("activity_ended_at_time", None)
            self._prov_manager.track_entity(
                entity_id=entity_id,
                source=source,
                entity_type=entity_type,
                agent_id=self._agent_id,
                agent_type="software_agent",
                is_automated=self._is_automated,
                activity_started_at_time=activity_started_at_time,
                activity_ended_at_time=activity_ended_at_time,
                metadata=metadata
            )


class NERExtractorWithProvenance(ProvenanceMixin):
    """
    Named Entity Recognition extractor with provenance tracking.
    
    Wraps the original NERExtractor and adds optional provenance tracking
    for all extracted entities.
    
    Example:
        >>> ner = NERExtractorWithProvenance(provenance=True)
        >>> entities = ner.extract("Steve Jobs founded Apple.", source="doc1.pdf")
        >>> # Each entity is tracked with source, confidence, and metadata
    """
    
    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        """
        Initialize NER extractor with optional provenance.

        Args:
            provenance: Enable provenance tracking (default: False)
            **config: Configuration passed to original NERExtractor
        """
        from .ner_extractor import NERExtractor

        ProvenanceMixin.__init__(
            self, provenance=provenance, agent_id=agent_id, is_automated=is_automated
        )
        self._extractor = NERExtractor(**config)
    
    def extract(self, text: str, source: Optional[str] = None, **kwargs):
        """
        Extract named entities with provenance tracking.
        
        Args:
            text: Input text to extract entities from
            source: Source document identifier (for provenance)
            **kwargs: Additional arguments for extraction
            
        Returns:
            List of extracted entities (same as original NERExtractor)
        """
        activity_started_at_time = datetime.utcnow().isoformat()
        entities = self._extractor.extract(text, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance:
            for entity in entities:
                entity_id = getattr(entity, 'id', None)
                if not entity_id:
                    entity_id = f"entity_{uuid.uuid4().hex[:8]}"
                    try:
                        entity.id = entity_id
                    except AttributeError:
                        pass

                self._track_extraction(
                    entity_id=entity_id,
                    source=source or text[:100],
                    entity_type="named_entity",
                    text=entity.text,
                    label=entity.label,
                    confidence=getattr(entity, 'confidence', 1.0),
                    start=entity.start,
                    end=entity.end,
                    activity_started_at_time=activity_started_at_time,
                    activity_ended_at_time=activity_ended_at_time,
                )
        
        return entities
    
    def __getattr__(self, name):
        """Delegate other methods to wrapped extractor."""
        return getattr(self._extractor, name)


class RelationExtractorWithProvenance(ProvenanceMixin):
    """
    Relation extractor with provenance tracking.
    
    Wraps the original RelationExtractor and tracks all extracted relations.
    """
    
    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        """
        Initialize relation extractor with optional provenance.

        Args:
            provenance: Enable provenance tracking (default: False)
            **config: Configuration passed to original RelationExtractor
        """
        from .relation_extractor import RelationExtractor

        ProvenanceMixin.__init__(
            self, provenance=provenance, agent_id=agent_id, is_automated=is_automated
        )
        self._extractor = RelationExtractor(**config)
    
    def extract(self, text: str, source: Optional[str] = None, **kwargs):
        """
        Extract relations with provenance tracking.
        
        Args:
            text: Input text to extract relations from
            source: Source document identifier (for provenance)
            **kwargs: Additional arguments for extraction
            
        Returns:
            List of extracted relations
        """
        activity_started_at_time = datetime.utcnow().isoformat()
        relations = self._extractor.extract(text, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance:
            for relation in relations:
                relation_id = getattr(relation, 'id', None)
                if not relation_id:
                    relation_id = f"rel_{uuid.uuid4().hex[:8]}"
                    try:
                        relation.id = relation_id
                    except AttributeError:
                        pass

                self._track_extraction(
                    entity_id=relation_id,
                    source=source or text[:100],
                    entity_type="relation",
                    subject=relation.subject,
                    predicate=relation.predicate,
                    object=relation.object,
                    confidence=getattr(relation, 'confidence', 1.0),
                    activity_started_at_time=activity_started_at_time,
                    activity_ended_at_time=activity_ended_at_time,
                )
        
        return relations
    
    def __getattr__(self, name):
        """Delegate other methods to wrapped extractor."""
        return getattr(self._extractor, name)


class EventDetectorWithProvenance(ProvenanceMixin):
    """
    Event detector with provenance tracking.
    
    Wraps the original EventDetector and tracks all detected events.
    """
    
    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        """
        Initialize event detector with optional provenance.

        Args:
            provenance: Enable provenance tracking (default: False)
            **config: Configuration passed to original EventDetector
        """
        from .event_detector import EventDetector

        ProvenanceMixin.__init__(
            self, provenance=provenance, agent_id=agent_id, is_automated=is_automated
        )
        self._detector = EventDetector(**config)
    
    def detect(self, text: str, source: Optional[str] = None, **kwargs):
        """
        Detect events with provenance tracking.
        
        Args:
            text: Input text to detect events from
            source: Source document identifier (for provenance)
            **kwargs: Additional arguments for detection
            
        Returns:
            List of detected events
        """
        activity_started_at_time = datetime.utcnow().isoformat()
        events = self._detector.detect(text, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance:
            for event in events:
                event_id = getattr(event, 'id', None)
                if not event_id:
                    event_id = f"event_{uuid.uuid4().hex[:8]}"
                    try:
                        event.id = event_id
                    except AttributeError:
                        pass

                self._track_extraction(
                    entity_id=event_id,
                    source=source or text[:100],
                    entity_type="event",
                    event_type=event.type,
                    trigger=event.trigger,
                    confidence=getattr(event, 'confidence', 1.0),
                    activity_started_at_time=activity_started_at_time,
                    activity_ended_at_time=activity_ended_at_time,
                )
        
        return events
    
    def __getattr__(self, name):
        """Delegate other methods to wrapped detector."""
        return getattr(self._detector, name)


class CoreferenceResolverWithProvenance(ProvenanceMixin):
    """
    Coreference resolver with provenance tracking.
    
    Wraps the original CoreferenceResolver and tracks coreference chains.
    """
    
    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        """
        Initialize coreference resolver with optional provenance.

        Args:
            provenance: Enable provenance tracking (default: False)
            **config: Configuration passed to original CoreferenceResolver
        """
        from .coreference_resolver import CoreferenceResolver

        ProvenanceMixin.__init__(
            self, provenance=provenance, agent_id=agent_id, is_automated=is_automated
        )
        self._resolver = CoreferenceResolver(**config)
    
    def resolve(self, text: str, source: Optional[str] = None, **kwargs):
        """
        Resolve coreferences with provenance tracking.
        
        Args:
            text: Input text to resolve coreferences
            source: Source document identifier (for provenance)
            **kwargs: Additional arguments for resolution
            
        Returns:
            Coreference chains
        """
        activity_started_at_time = datetime.utcnow().isoformat()
        chains = self._resolver.resolve(text, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance:
            for chain in chains:
                chain_id = getattr(chain, 'id', None)
                if not chain_id:
                    chain_id = f"coref_{uuid.uuid4().hex[:8]}"
                    try:
                        chain.id = chain_id
                    except AttributeError:
                        pass

                self._track_extraction(
                    entity_id=chain_id,
                    source=source or text[:100],
                    entity_type="coreference_chain",
                    mentions=len(chain.mentions) if hasattr(chain, 'mentions') else 0,
                    activity_started_at_time=activity_started_at_time,
                    activity_ended_at_time=activity_ended_at_time,
                )
        
        return chains
    
    def __getattr__(self, name):
        """Delegate other methods to wrapped resolver."""
        return getattr(self._resolver, name)


class TripletExtractorWithProvenance(ProvenanceMixin):
    """
    Triplet extractor with provenance tracking.
    
    Wraps the original TripletExtractor and tracks all extracted triplets.
    """
    
    def __init__(
        self,
        provenance: bool = False,
        agent_id: Optional[str] = None,
        is_automated: bool = True,
        **config,
    ):
        """
        Initialize triplet extractor with optional provenance.

        Args:
            provenance: Enable provenance tracking (default: False)
            **config: Configuration passed to original TripletExtractor
        """
        from .triplet_extractor import TripletExtractor

        ProvenanceMixin.__init__(
            self, provenance=provenance, agent_id=agent_id, is_automated=is_automated
        )
        self._extractor = TripletExtractor(**config)
    
    def extract(self, text: str, source: Optional[str] = None, **kwargs):
        """
        Extract triplets with provenance tracking.
        
        Args:
            text: Input text to extract triplets from
            source: Source document identifier (for provenance)
            **kwargs: Additional arguments for extraction
            
        Returns:
            List of extracted triplets
        """
        activity_started_at_time = datetime.utcnow().isoformat()
        triplets = self._extractor.extract(text, **kwargs)
        activity_ended_at_time = datetime.utcnow().isoformat()

        if self.provenance:
            for triplet in triplets:
                triplet_id = getattr(triplet, 'id', None)
                if not triplet_id:
                    triplet_id = f"triplet_{uuid.uuid4().hex[:8]}"
                    try:
                        triplet.id = triplet_id
                    except AttributeError:
                        pass

                self._track_extraction(
                    entity_id=triplet_id,
                    source=source or text[:100],
                    entity_type="triplet",
                    subject=triplet.subject,
                    predicate=triplet.predicate,
                    object=triplet.object,
                    confidence=getattr(triplet, 'confidence', 1.0),
                    activity_started_at_time=activity_started_at_time,
                    activity_ended_at_time=activity_ended_at_time,
                )
        
        return triplets
    
    def __getattr__(self, name):
        """Delegate other methods to wrapped extractor."""
        return getattr(self._extractor, name)


# Convenience exports
__all__ = [
    'NERExtractorWithProvenance',
    'RelationExtractorWithProvenance',
    'EventDetectorWithProvenance',
    'CoreferenceResolverWithProvenance',
    'TripletExtractorWithProvenance',
    'ProvenanceMixin',
]
