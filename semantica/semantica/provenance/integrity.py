"""
Integrity Verification Utilities

This module provides utilities for data integrity verification using
SHA-256 checksums, ensuring provenance data has not been tampered with.

Features:
    - SHA-256 checksum computation
    - Checksum verification
    - Data integrity validation
    - Tamper detection

Compliance:
    - FDA 21 CFR Part 11 (electronic records)
    - SOX (Sarbanes-Oxley)
    - HIPAA (healthcare data integrity)

Author: Semantica Contributors
License: MIT
"""

import hashlib
from typing import Any, Dict, Optional
from .schemas import ProvenanceEntry


def compute_checksum(entry: Any) -> str:
    """
    Compute SHA-256 checksum for a provenance entry.
    
    Creates a deterministic checksum based on critical provenance fields
    to detect any tampering or corruption of provenance data.

    Includes `previous_checksum` (issue #825, Part A item 2), which chains
    each entry to the prior entry in insertion order (see
    ProvenanceStorage.get_chain_head / ProvenanceManager.verify_chain) —
    wholesale deletion of a row breaks the chain for the entry that used to
    follow it, making the deletion detectable even though per-row checksums
    only prove a surviving row wasn't edited in place. Also includes
    agent_id/agent_type and the lineage-link fields (parent_entity_id,
    previous_version_id, derived_from_id, used_entities) so tampering with
    attribution or lineage is detected, not just tampering with the six
    original fields.

    Deliberately excludes `entity_id` itself: entity_id is the storage
    primary key, and ProvenanceManager.track_entity()'s versioning archives
    a prior value by copying it to a new entity_id (e.g. "X" -> "X:v:...").
    If entity_id were hashed, that relabeling would change the archived
    copy's checksum, permanently orphaning any later entry whose
    previous_checksum had already chained from the pre-relabel value —
    a false-positive "broken chain" for a legitimate rename, not tampering.
    Tampering that swaps a row's entity_id while keeping its content is a
    narrower threat than content tampering, already partially caught by
    verify_chain() (it requires a delete+insert, which breaks the chain for
    whatever the deleted row's successor was).

    Args:
        entry: ProvenanceEntry or dict to compute checksum for

    Returns:
        SHA-256 checksum as hexadecimal string

    Example:
        >>> entry = ProvenanceEntry(
        ...     entity_id="entity_123",
        ...     entity_type="entity",
        ...     activity_id="extraction",
        ...     source_document="DOI:10.1371/..."
        ... )
        >>> checksum = compute_checksum(entry)
        >>> print(checksum)
        'a3b2c1d4e5f6...'
    """
    # Concatenate critical fields for checksum (entity_id intentionally excluded, see docstring)
    if isinstance(entry, dict):
        used_entities = entry.get("used_entities") or []
        data = (
            f"{entry.get('entity_type') or ''}"
            f"{entry.get('activity_id') or ''}"
            f"{entry.get('agent_id') or ''}"
            f"{entry.get('agent_type') or ''}"
            f"{entry.get('source_document') or ''}"
            f"{entry.get('timestamp') or ''}"
            f"{entry.get('confidence') if entry.get('confidence') is not None else 1.0}"
            f"{entry.get('parent_entity_id') or ''}"
            f"{entry.get('previous_version_id') or ''}"
            f"{entry.get('derived_from_id') or ''}"
            f"{','.join(used_entities)}"
            f"{entry.get('previous_checksum') or ''}"
            f"{bool(entry.get('invalidated'))}"
            f"{entry.get('invalidated_at_time') or ''}"
            f"{entry.get('invalidated_by') or ''}"
            f"{entry.get('invalidation_reason') or ''}"
        )
    else:
        used_entities = getattr(entry, "used_entities", None) or []
        data = (
            f"{entry.entity_type}"
            f"{entry.activity_id}"
            f"{getattr(entry, 'agent_id', '') or ''}"
            f"{getattr(entry, 'agent_type', '') or ''}"
            f"{entry.source_document}"
            f"{entry.timestamp}"
            f"{entry.confidence}"
            f"{getattr(entry, 'parent_entity_id', '') or ''}"
            f"{getattr(entry, 'previous_version_id', '') or ''}"
            f"{getattr(entry, 'derived_from_id', '') or ''}"
            f"{','.join(used_entities)}"
            f"{getattr(entry, 'previous_checksum', '') or ''}"
            f"{bool(getattr(entry, 'invalidated', False))}"
            f"{getattr(entry, 'invalidated_at_time', '') or ''}"
            f"{getattr(entry, 'invalidated_by', '') or ''}"
            f"{getattr(entry, 'invalidation_reason', '') or ''}"
        )

    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def verify_checksum(entry: Any, expected_checksum: Optional[str] = None) -> bool:
    """
    Verify checksum for a provenance entry.
    
    Computes the current checksum and compares it with the expected checksum
    to detect tampering or corruption.
    
    Args:
        entry: ProvenanceEntry or dict to verify
        expected_checksum: Expected checksum (uses entry.checksum if None)
        
    Returns:
        True if checksum matches, False otherwise
        
    Example:
        >>> entry = ProvenanceEntry(...)
        >>> entry.checksum = compute_checksum(entry)
        >>> is_valid = verify_checksum(entry)
        >>> print(is_valid)
        True
    """
    if expected_checksum is None:
        if isinstance(entry, dict):
            expected_checksum = entry.get("checksum")
        else:
            expected_checksum = getattr(entry, "checksum", None)
    
    if expected_checksum is None:
        return False
    
    current_checksum = compute_checksum(entry)
    return current_checksum == expected_checksum


def compute_data_checksum(data: str) -> str:
    """
    Compute SHA-256 checksum for arbitrary data.
    
    Args:
        data: String data to compute checksum for
        
    Returns:
        SHA-256 checksum as hexadecimal string
        
    Example:
        >>> checksum = compute_data_checksum("some data")
        >>> print(checksum)
        'a3b2c1d4e5f6...'
    """
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def verify_data_checksum(data: str, expected_checksum: str) -> bool:
    """
    Verify checksum for arbitrary data.
    
    Args:
        data: String data to verify
        expected_checksum: Expected checksum
        
    Returns:
        True if checksum matches, False otherwise
        
    Example:
        >>> data = "some data"
        >>> checksum = compute_data_checksum(data)
        >>> is_valid = verify_data_checksum(data, checksum)
        >>> print(is_valid)
        True
    """
    current_checksum = compute_data_checksum(data)
    return current_checksum == expected_checksum


def compute_dict_checksum(data: Dict[str, Any]) -> str:
    """
    Compute SHA-256 checksum for dictionary data.
    
    Sorts keys to ensure deterministic checksum computation.
    
    Args:
        data: Dictionary data to compute checksum for
        
    Returns:
        SHA-256 checksum as hexadecimal string
        
    Example:
        >>> data = {"key1": "value1", "key2": "value2"}
        >>> checksum = compute_dict_checksum(data)
        >>> print(checksum)
        'a3b2c1d4e5f6...'
    """
    # Sort keys for deterministic checksum
    sorted_items = sorted(data.items())
    data_str = str(sorted_items)
    return hashlib.sha256(data_str.encode('utf-8')).hexdigest()


def verify_dict_checksum(data: Dict[str, Any], expected_checksum: str) -> bool:
    """
    Verify checksum for dictionary data.
    
    Args:
        data: Dictionary data to verify
        expected_checksum: Expected checksum
        
    Returns:
        True if checksum matches, False otherwise
        
    Example:
        >>> data = {"key1": "value1", "key2": "value2"}
        >>> checksum = compute_dict_checksum(data)
        >>> is_valid = verify_dict_checksum(data, checksum)
        >>> print(is_valid)
        True
    """
    current_checksum = compute_dict_checksum(data)
    return current_checksum == expected_checksum
