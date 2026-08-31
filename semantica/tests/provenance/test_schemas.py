"""
Test W3C PROV-O Compliant Schemas

Tests for provenance schemas including ProvenanceEntry, SourceReference,
and PropertySource dataclasses.
"""

import pytest
from datetime import datetime
from semantica.provenance.schemas import (
    ProvenanceEntry,
    SourceReference,
    PropertySource,
    AgentRecord,
    ActivityRecord,
    Invalidation,
)


class TestProvenanceEntry:
    """Test ProvenanceEntry dataclass."""
    
    def test_create_basic_entry(self):
        """Test creating a basic provenance entry."""
        entry = ProvenanceEntry(
            entity_id="entity_1",
            entity_type="entity",
            activity_id="extraction"
        )
        
        assert entry.entity_id == "entity_1"
        assert entry.entity_type == "entity"
        assert entry.activity_id == "extraction"
        assert entry.agent_id == "semantica"
        assert entry.confidence == 1.0
    
    def test_entry_with_source_tracking(self):
        """Test entry with audit-grade source tracking."""
        entry = ProvenanceEntry(
            entity_id="entity_1",
            entity_type="entity",
            activity_id="extraction",
            source_document="DOI:10.1371/journal.pone.0023601",
            source_location="Figure 2",
            source_quote="Total fish biomass increased by 463%",
            confidence=0.92
        )
        
        assert entry.source_document == "DOI:10.1371/journal.pone.0023601"
        assert entry.source_location == "Figure 2"
        assert entry.source_quote == "Total fish biomass increased by 463%"
        assert entry.confidence == 0.92
    
    def test_entry_with_lineage(self):
        """Test entry with parent-child relationships."""
        entry = ProvenanceEntry(
            entity_id="entity_2",
            entity_type="entity",
            activity_id="transformation",
            parent_entity_id="entity_1",
            used_entities=["entity_1", "axiom_1"]
        )
        
        assert entry.parent_entity_id == "entity_1"
        assert len(entry.used_entities) == 2
        assert "entity_1" in entry.used_entities
    
    def test_entry_to_dict(self):
        """Test converting entry to dictionary."""
        entry = ProvenanceEntry(
            entity_id="entity_1",
            entity_type="entity",
            activity_id="extraction"
        )
        
        data = entry.to_dict()
        
        assert isinstance(data, dict)
        assert data["entity_id"] == "entity_1"
        assert data["entity_type"] == "entity"
        assert "timestamp" in data
    
    def test_entry_from_dict(self):
        """Test creating entry from dictionary."""
        data = {
            "entity_id": "entity_1",
            "entity_type": "entity",
            "activity_id": "extraction",
            "agent_id": "semantica",
            "source_document": "doc_1",
            "confidence": 0.9
        }
        
        entry = ProvenanceEntry.from_dict(data)
        
        assert entry.entity_id == "entity_1"
        assert entry.confidence == 0.9


class TestSourceReference:
    """Test SourceReference dataclass."""
    
    def test_create_basic_source(self):
        """Test creating a basic source reference."""
        source = SourceReference(
            document="DOI:10.1038/s41586-021-03371-z"
        )
        
        assert source.document == "DOI:10.1038/s41586-021-03371-z"
        assert source.confidence == 1.0
    
    def test_source_with_location(self):
        """Test source with page and section."""
        source = SourceReference(
            document="DOI:10.1038/s41586-021-03371-z",
            page=4,
            section="Table S4",
            confidence=0.92
        )
        
        assert source.page == 4
        assert source.section == "Table S4"
        assert source.confidence == 0.92
    
    def test_source_to_dict(self):
        """Test converting source to dictionary."""
        source = SourceReference(
            document="doc_1",
            page=1
        )
        
        data = source.to_dict()
        
        assert isinstance(data, dict)
        assert data["document"] == "doc_1"
        assert data["page"] == 1


class TestPropertySource:
    """Test PropertySource dataclass."""
    
    def test_create_property_source(self):
        """Test creating a property source."""
        source_ref = SourceReference(document="doc_1")
        
        prop_source = PropertySource(
            property_name="biomass_increase",
            value="463%",
            sources=[source_ref],
            entity_id="cabo_pulmo_mpa"
        )
        
        assert prop_source.property_name == "biomass_increase"
        assert prop_source.value == "463%"
        assert len(prop_source.sources) == 1
        assert prop_source.entity_id == "cabo_pulmo_mpa"
    
    def test_property_source_to_dict(self):
        """Test converting property source to dictionary."""
        source_ref = SourceReference(document="doc_1")
        prop_source = PropertySource(
            property_name="name",
            value="test",
            sources=[source_ref]
        )
        
        data = prop_source.to_dict()

        assert isinstance(data, dict)
        assert data["property_name"] == "name"
        assert len(data["sources"]) == 1


class TestProvenanceEntryPart825Fields:
    """Issue #825, Part A — new additive fields on ProvenanceEntry."""

    def test_defaults(self):
        entry = ProvenanceEntry(entity_id="e1", entity_type="entity", activity_id="act")
        assert entry.agent_type == "software_agent"
        assert entry.is_automated is True
        assert entry.role is None
        assert entry.previous_version_id is None
        assert entry.derived_from_id is None
        assert entry.sequence_id is None
        assert entry.previous_checksum is None
        assert entry.invalidated is False
        assert entry.invalidated_at_time is None
        assert entry.invalidated_by is None
        assert entry.invalidation_reason is None
        # Part B additive fields
        assert entry.activity_started_at_time is None
        assert entry.activity_ended_at_time is None
        assert entry.acted_on_behalf_of is None
        assert entry.informed_by_activities == []
        assert entry.valid_from is None
        assert entry.valid_until is None
        assert entry.revision_type is None
        assert entry.supersedes is None
        assert entry.bundle_id is None

    def test_round_trip_to_dict_from_dict(self):
        entry = ProvenanceEntry(
            entity_id="e1",
            entity_type="entity",
            activity_id="act",
            agent_id="alice",
            agent_type="person",
            is_automated=False,
            role="approver",
            previous_version_id="e1:v:1",
            derived_from_id="source_entity",
            sequence_id=5,
            previous_checksum="abc123",
            invalidated=True,
            invalidated_at_time="2026-01-01T00:00:00",
            invalidated_by="reviewer_jane",
            invalidation_reason="retracted",
            activity_started_at_time="2026-01-01T00:00:00",
            activity_ended_at_time="2026-01-01T00:00:05",
            acted_on_behalf_of="org1",
            informed_by_activities=["ingest_activity"],
            valid_from="2026-01-01",
            valid_until="2026-06-01",
            revision_type="correction",
            supersedes="old_claim",
            bundle_id="ingestion_run_1",
        )

        data = entry.to_dict()
        restored = ProvenanceEntry.from_dict(data)

        assert restored == entry


class TestActivityRecord:
    """Issue #825, Part B Tier 1 — minimum-viable typed activity."""

    def test_defaults(self):
        activity = ActivityRecord(id="act1")
        assert activity.activity_type == "process"
        assert activity.started_at_time is None
        assert activity.ended_at_time is None

    def test_round_trip(self):
        activity = ActivityRecord(
            id="bureau_parsing_run_42",
            activity_type="extraction",
            started_at_time="2026-01-01T00:00:00",
            ended_at_time="2026-01-01T00:00:03",
        )
        data = activity.to_dict()
        restored = ActivityRecord.from_dict(data)
        assert restored == activity


class TestAgentRecord:
    """Issue #825, Part A item 3 — minimum-viable typed agent."""

    def test_defaults(self):
        agent = AgentRecord(id="bot1")
        assert agent.agent_type == "software_agent"
        assert agent.is_automated is True
        assert agent.name is None

    def test_round_trip(self):
        agent = AgentRecord(id="reviewer_jane", agent_type="person", is_automated=False, name="Jane")
        data = agent.to_dict()
        restored = AgentRecord.from_dict(data)
        assert restored == agent


class TestInvalidation:
    """Issue #825, Part A item 1 — tombstone record."""

    def test_round_trip(self):
        inv = Invalidation(
            entity_id="e1",
            invalidated_at_time="2026-01-01T00:00:00",
            invalidated_by="reviewer_jane",
            reason="retracted",
        )
        data = inv.to_dict()
        restored = Invalidation.from_dict(data)
        assert restored == inv
