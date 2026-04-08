"""
Interaction tracking data structures and utilities for behavioral analysis.

This module provides dataclasses for tracking user interactions during annotation,
including clicks, focus changes, navigation, AI assistance usage, and annotation changes.
All data is designed to be serializable for persistence and later analysis.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional
import time
import datetime

from potato.server_utils.date_handler import DateHandler
import logging

logger = logging.getLogger(__name__)

@dataclass
class InteractionEvent:
    """
    A single user interaction with the annotation interface.

    Attributes:
        event_type: Type of interaction ("click", "focus_in", "focus_out",
                    "navigation", "save", "scroll", "keypress", etc.)
        timestamp: Server-side Unix timestamp when event was recorded
        client_timestamp: Client-side timestamp in milliseconds (for latency analysis)
        target: Element identifier (e.g., "label:positive", "nav:next", "schema:sentiment")
        instance_id: The annotation instance this event occurred on
        metadata: Additional context (position, value changes, duration, etc.)
    """
    event_type: str
    timestamp: datetime.datetime
    client_timestamp: datetime.datetime
    target: str
    instance_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'event_type': self.event_type,
            'timestamp': DateHandler.datetime_to_str(self.timestamp),
            'client_timestamp': DateHandler.datetime_to_str(self.client_timestamp),
            'target': self.target,
            'instance_id': self.instance_id,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InteractionEvent':
        """Reconstruct from serialized dictionary."""
        return cls(
            event_type=data.get('event_type', ''),
            timestamp=DateHandler.str_to_datetime(data.get('timestamp')),
            client_timestamp=DateHandler.str_to_datetime(data.get('client_timestamp')),
            target=data.get('target', ''),
            instance_id=data.get('instance_id', ''),
            metadata=data.get('metadata', {})
        )


@dataclass
class AnnotationChange:
    """
    Records a single change to an annotation.

    Attributes:
        timestamp: When the change occurred
        client_timestamp: Client-side timestamp in milliseconds (for latency analysis)
        schema_name: Which schema was modified
        label_name: Which label was affected (if applicable)
        action: Type of change ("select", "deselect", "update", "clear")
        old_value: Previous value (if any)
        new_value: New value after the change
        source: What triggered the change ("user", "ai_accept", "prefill", "keyboard")
    """
    timestamp: datetime.datetime
    client_timestamp: datetime.datetime
    schema_name: str
    action: str
    label_name: Optional[str] = None
    old_value: Any = None
    new_value: Any = None
    source: str = "user"
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data['timestamp'] = DateHandler.datetime_to_str(self.timestamp)
        data['client_timestamp'] = DateHandler.datetime_to_str(self.client_timestamp)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AnnotationChange':
        """Reconstruct from serialized dictionary."""
        data['timestamp'] = DateHandler.str_to_datetime(data.get('timestamp'))
        data['client_timestamp'] = DateHandler.str_to_datetime(data.get('client_timestamp'))
        return cls(**data)

@dataclass
class BehavioralData:
    """
    Complete behavioral data for an annotation instance session.

    Aggregates all tracking data for a single instance annotation session,
    including timing, interactions, AI usage, and annotation changes.

    Attributes:
        instance_id: The annotation instance ID
        session_start: Unix timestamp when user first loaded this instance
        session_end: Unix timestamp when user navigated away or saved
        total_time_ms: Total milliseconds spent on this instance
        interactions: List of all interaction events
        annotation_changes: List of annotation modifications
        focus_time_by_element: Milliseconds spent focused on each element
        scroll_depth_max: Maximum scroll percentage reached (0-100)
    """
    instance_id: str
    session_start: Optional[float] = None
    session_end: Optional[float] = None
    total_time_ms: int = 0
    interactions: List[InteractionEvent] = field(default_factory=list)
    annotation_changes: List[AnnotationChange] = field(default_factory=list)
    focus_time_by_element: Dict[str, int] = field(default_factory=dict)
    scroll_depth_max: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""

        return {
            'instance_id': self.instance_id,
            'session_start': DateHandler.datetime_to_str(self.session_start),
            'session_end': DateHandler.datetime_to_str(self.session_end),
            'total_time_ms': self.total_time_ms,
            'interactions': [e.to_dict() if hasattr(e, 'to_dict') else e for e in self.interactions],
            'annotation_changes': [e.to_dict() if hasattr(e, 'to_dict') else e for e in self.annotation_changes],
            'focus_time_by_element': self.focus_time_by_element,
            'scroll_depth_max': self.scroll_depth_max,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BehavioralData':
        """
        Reconstruct from serialized dictionary.

        Handles both raw dictionaries and properly typed objects.
        """
        bd = cls(instance_id=data.get('instance_id', ''))

        bd.session_start = DateHandler.str_to_datetime(data.get('session_start'))
        bd.session_end = DateHandler.str_to_datetime(data.get('session_end'))
        bd.total_time_ms = data.get('total_time_ms', 0)

        # Reconstruct interactions
        interactions = data.get('interactions', [])
        bd.interactions = [InteractionEvent.from_dict(e) if isinstance(e, dict) else e for e in interactions]

        # Reconstruct annotation changes
        changes = data.get('annotation_changes', [])
        bd.annotation_changes = [AnnotationChange.from_dict(e) if isinstance(e, dict) else e for e in changes]

        bd.focus_time_by_element = data.get('focus_time_by_element', {})
        bd.scroll_depth_max = data.get('scroll_depth_max', 0.0)

        return bd

    def add_interaction(self, event_type: str, target: str,
                       client_timestamp: Optional[float] = None,
                       metadata: Optional[Dict[str, Any]] = None) -> None:

        client_timestamp = DateHandler.timestamp_to_datetime(client_timestamp)

        self.update_session_times(client_timestamp)

        """Add an interaction event with current timestamp."""
        self.interactions.append(InteractionEvent(
            event_type=event_type,
            timestamp=datetime.datetime.now(),
            target=target,
            instance_id=self.instance_id,
            client_timestamp=client_timestamp,
            metadata=metadata or {},
        ))

    def add_annotation_change(self, schema_name: str, label_name: str,
                              action: str, old_value: Any, new_value: Any,
                              source: str = "user", client_timestamp: Optional[float] = None,
                              metadata: dict = None) -> None:

        # Transform client timestamp (milliseconds) to datetime
        client_timestamp = DateHandler.timestamp_to_datetime(client_timestamp)

        self.update_session_times(client_timestamp)

        self.annotation_changes.append(AnnotationChange(
            timestamp=datetime.datetime.now(),
            client_timestamp=client_timestamp,
            schema_name=schema_name,
            label_name=label_name,
            action=action,
            old_value=old_value,
            new_value=new_value,
            source=source,
            metadata=metadata
        ))

    def update_focus_time(self, element: str, duration_ms: int) -> None:
        """Add time spent focused on an element."""
        current = self.focus_time_by_element.get(element, 0)
        self.focus_time_by_element[element] = current + duration_ms

    def update_scroll_depth(self, depth: float) -> None:
        """Update maximum scroll depth if new depth is greater."""
        if depth > self.scroll_depth_max:
            self.scroll_depth_max = depth

    def update_session_times(self, client_timestamp) -> None:
        if client_timestamp:
            if self.session_start is None or client_timestamp < self.session_start:
                self.session_start = client_timestamp

            if self.session_end is None or client_timestamp > self.session_end:
                self.session_end = client_timestamp

            if self.session_start and self.session_end:
                self.total_time_ms = int((self.session_end - self.session_start).total_seconds() * 1000)

    def finalize_session(self) -> None:
        """Mark session as ended and calculate total time."""
        #self.session_end = datetime.datetime.now()
        #self.total_time_ms = int((self.session_end - self.session_start).total_seconds() * 1000)
        a = 1

def create_behavioral_data(instance_id: str) -> BehavioralData:
    """Factory function to create new behavioral data for an instance."""
    return BehavioralData(instance_id=instance_id)


def get_or_create_behavioral_data(
    behavioral_data_dict: Dict[str, Any],
    instance_id: str
) -> BehavioralData:
    """
    Get existing behavioral data or create new one.

    Args:
        behavioral_data_dict: Dictionary mapping instance_id to BehavioralData
        instance_id: The instance to get/create data for

    Returns:
        BehavioralData object for the instance
    """
    if instance_id not in behavioral_data_dict:
        behavioral_data_dict[instance_id] = create_behavioral_data(instance_id)

    bd = behavioral_data_dict[instance_id]

    # Handle case where dict contains raw dict instead of BehavioralData
    if isinstance(bd, dict):
        bd = BehavioralData.from_dict(bd)
        behavioral_data_dict[instance_id] = bd

    return bd
