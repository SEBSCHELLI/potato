"""
Quality Control Module

This module provides comprehensive quality control features for annotation projects:
- Attention Checks: Inject known-answer items to verify annotator engagement
- Gold Standards: Compare annotations against expert-labeled items for accuracy tracking
- Pre-annotation Support: Pre-fill forms with model predictions

The module integrates with ItemStateManager for item injection and UserStateManager
for tracking results.
"""

import json
import logging
import random
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from pathlib import Path
from collections import defaultdict
from potato.server_utils.date_handler import DateHandler

logger = logging.getLogger(__name__)

# Singleton instance
_QUALITY_CONTROL_MANAGER = None
_QUALITY_CONTROL_LOCK = threading.Lock()


@dataclass
class AttentionCheckResult:
    """Result of an attention check evaluation."""
    item_id: str
    user_id: str
    session_id: str
    passed: bool
    expected: Dict[str, Any]
    actual: Dict[str, Any]
    timestamp: Optional[datetime] = None
    response_time_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data['timestamp'] = DateHandler.datetime_to_str(self.timestamp)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AttentionCheckState':
        """Reconstruct from serialized dictionary."""
        _data = data.copy()
        _data['timestamp'] = DateHandler.str_to_datetime(_data.get('timestamp'))
        return cls(**_data)


@dataclass
class QualityControlConfig:
    """Configuration for quality control features."""
    # Attention checks config
    attention_checks_enabled: bool = False
    attention_items_file: Optional[str] = None
    attention_frequency: Optional[int] = None  # Insert one every N items
    attention_probability: Optional[float] = None  # OR probability per item
    attention_min_response_time: float = 0.0  # Minimum seconds (flag fast responses)
    attention_warn_threshold: int = 1000
    attention_warn_message: str = "Please read items carefully before answering."
    attention_block_threshold: int = 1000
    attention_block_message: str = "You have been blocked due to too many incorrect attention check responses."


class QualityControlManager:
    """
    Manages quality control features including attention checks, gold standards,
    and pre-annotation support.
    """

    def __init__(self, config: Dict[str, Any], base_dir: str):
        """
        Initialize the quality control manager.

        Args:
            config: The full application configuration
            base_dir: Base directory for resolving file paths
        """
        self.config = config
        self.base_dir = base_dir
        self.logger = logging.getLogger(__name__)
        self._lock = threading.RLock()

        # Parse configuration
        self.qc_config = self._parse_config(config)

        # Attention check data
        self.attention_items: List[Dict] = []
        self.attention_expected: Dict[str, Dict[str, Any]] = {}  # item_id -> expected_answer

        self.user_session_to_attention_results: Dict[str, Dict[str, List[AttentionCheckResult]]] = defaultdict(lambda: defaultdict(list))  # user_id -> session_id -> results

        # Load data files if configured
        self._load_attention_checks()
        self._load_attention_check_data()

    def _parse_config(self, config: Dict[str, Any]) -> QualityControlConfig:
        """Parse quality control configuration from the main config."""
        qc = QualityControlConfig()

        # Parse attention checks config
        attn_config = config.get('attention_checks', {})
        if attn_config.get('enabled', False):
            qc.attention_checks_enabled = True
            qc.attention_items_file = attn_config.get('items_file')
            qc.attention_frequency = attn_config.get('frequency')
            qc.attention_probability = attn_config.get('probability')
            qc.attention_min_response_time = attn_config.get('min_response_time', 0.0)

            failure_handling = attn_config.get('failure_handling', {})
            qc.attention_warn_threshold = failure_handling.get('warn_threshold', 2)
            qc.attention_warn_message = failure_handling.get('warn_message', qc.attention_warn_message)
            qc.attention_block_threshold = failure_handling.get('block_threshold', 5)
            qc.attention_block_message = failure_handling.get('block_message', qc.attention_block_message)

        return qc

    def _load_attention_checks(self) -> None:
        """Load attention check items from file."""
        if not self.qc_config.attention_checks_enabled:
            return

        if not self.qc_config.attention_items_file:
            self.logger.warning("Attention checks enabled but no items_file specified")
            return

        file_path = Path(self.base_dir) / self.qc_config.attention_items_file
        if not file_path.exists():
            self.logger.warning(f"Attention checks file not found: {file_path}")
            return

        try:
            with open(file_path, 'r') as f:
                items = json.load(f)

            if not isinstance(items, list):
                self.logger.error("Attention checks file must contain a JSON array")
                return

            from potato.flask_server import get_item_state_manager
            ism = get_item_state_manager()

            for item in items:
                if 'id' not in item or 'expected_answer' not in item:
                    self.logger.warning(f"Attention check item missing required fields: {item}")
                    continue

                self.attention_items.append(item)
                self.attention_expected[item['id']] = item['expected_answer']

                # Add items to Item State Manager
                # Validate that the ID key exists in the item
                instance_id = str(item['id'])  # Ensure ID is string

                ism.add_gold_item(instance_id, item)

            self.logger.info(f"Loaded {len(self.attention_items)} attention check items")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse attention checks file: {e}")
        except Exception as e:
            self.logger.error(f"Failed to load attention checks: {e}")

    def _load_attention_check_data(self) -> None:
        from potato.flask_server import get_user_state_manager
        usm = get_user_state_manager()

        # Get all users and their states
        user_session_ids = usm.get_user_session_ids()

        for username, session_ids in user_session_ids.items():
            for session_id in session_ids:
                user_state = usm.get_user_state(username, session_id)
                if user_state:
                    ac_state = user_state.attention_check_state
                    if ac_state:
                        self.user_session_to_attention_results[username][session_id] = list(ac_state.attention_instance_id_to_attention_check_result.values())
                        logger.debug(f"Loaded Attention Check results for User {username} (Session ID {session_id})")

    # =========================================================================
    # Attention Check Methods
    # =========================================================================

    def is_attention_check(self, item_id: str) -> bool:
        """Check if an item is an attention check."""
        return item_id in self.attention_expected

    def should_inject_attention_check(self, user_state) -> bool:
        """
        Determine if an attention check should be injected for this user.

        Args:
            user_state: The user_state

        Returns:
            True if an attention check should be injected
        """
        if not self.qc_config.attention_checks_enabled or not self.attention_items:
            return False

        with self._lock:
            # Frequency-based injection
            if self.qc_config.attention_frequency:
                items_since = user_state.attention_check_state.n_items_since_last_check
                return items_since >= self.qc_config.attention_frequency

            # Probability-based injection
            if self.qc_config.attention_probability:
                return random.random() < self.qc_config.attention_probability

        return False

    def get_attention_check_item(self, user_id: str) -> Optional[Dict]:
        """
        Get a random attention check item for a user.
        """
        if not self.attention_items:
            return None

        with self._lock:
            # Get items this user hasn't seen yet

            # seen_in_session_ids = {r.item_id for r in self.user_session_to_attention_results[user_id][session_id]}
            all_seen_ids = {r.item_id for session_results in self.user_session_to_attention_results[user_id].values() for r in session_results}

            available = [item for item in self.attention_items if item['id'] not in all_seen_ids]

            if not available:
                # Recycle items if all have been seen
                available = self.attention_items

            selected = random.choice(available)

            return selected

    def validate_attention_response(
            self,
            user_id: str,
            session_id: str,
            item_id: str,
            response: Dict[str, Any],
            response_time_seconds: Optional[float] = None,
            timestamp = None
    ) -> Optional[Dict[str, Any]]:
        """
        Validate a response to an attention check.

        Args:
            user_id: The user ID
            session_id: The session ID
            item_id: The attention check item ID
            response: The user's response (schema_name -> value)
            response_time_seconds: Time taken to respond

        Returns:
            Dict with validation result if this is an attention check, None otherwise.
            Result includes: passed, warning (optional), blocked (optional), message (optional)
        """

        if item_id not in self.attention_expected:
            return None

        expected = self.attention_expected[item_id]
        passed = self._compare_responses(expected, response)

        # Check for suspiciously fast response
        if (response_time_seconds is not None and
                self.qc_config.attention_min_response_time > 0 and
                response_time_seconds < self.qc_config.attention_min_response_time):
            self.logger.warning(f"User {user_id} responded to attention check {item_id} in {response_time_seconds:.1f}s (min: {self.qc_config.attention_min_response_time}s)")
            # Still record the result but log the fast response

        # Record result
        result = AttentionCheckResult(
            item_id=item_id,
            session_id=session_id,
            user_id=user_id,
            passed=passed,
            expected=expected,
            actual=response,
            response_time_seconds=response_time_seconds,
            timestamp=DateHandler.timestamp_to_datetime(timestamp)
        )

        with self._lock:
            self.user_session_to_attention_results[user_id][session_id].append(result)


        # Check thresholds
        """if failures >= self.qc_config.attention_block_threshold:
            response_data["blocked"] = True
            response_data["message"] = self.qc_config.attention_block_message
            self.logger.warning(f"User {user_id} blocked after {failures} attention check failures")
        elif failures >= self.qc_config.attention_warn_threshold:
            response_data["warning"] = True
            response_data["message"] = self.qc_config.attention_warn_message
            self.logger.info(f"User {user_id} warned after {failures} attention check failures")"""

        return result

    def get_attention_check_stats_for_user_session(self, user_id: str, session_id: str) -> Dict[str, Any]:
        """Get attention check statistics for a user."""
        with self._lock:
            results = self.user_session_to_attention_results[user_id][session_id]
            passed = len([r for r in results if r.passed])
            failed = len([r for r in results if not r.passed])
            total = passed + failed

            return {
                "total": total,
                "passed": passed,
                "failed": failed,
                "pass_rate": passed / total if total > 0 else 0.0
            }

    def get_attention_check_stats_for_user(self, user_id: str) -> Dict[str, Any]:
        """Get attention check statistics for a user."""
        with self._lock:
            results = [r for session_results in self.user_session_to_attention_results[user_id].values() for r in session_results]
            passed = len([r for r in results if r.passed])
            failed = len([r for r in results if not r.passed])
            total = passed + failed

            return {
                "total": total,
                "passed": passed,
                "failed": failed,
                "pass_rate": passed / total if total > 0 else 0.0
            }

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _compare_responses(self, expected: Dict[str, Any], actual: Dict[str, Any]) -> bool:
        """
        Compare expected and actual responses.

        Handles various response formats:
        - Simple key-value pairs
        - Lists (for multiselect)
        - Nested structures

        Args:
            expected: The expected response
            actual: The actual response

        Returns:
            True if responses match
        """
        for key, expected_value in expected.items():
            # Handle both "schema_name" and "schema_name:label_name" formats
            actual_value = None

            # Direct match
            if key in actual:
                actual_value = actual[key]
            else:
                # Check for prefixed keys (schema_name:label_name format)
                for actual_key, val in actual.items():
                    if actual_key.startswith(key + ":") or actual_key == key:
                        actual_value = val
                        break

            if actual_value is None:
                return False

            # Compare values
            if isinstance(expected_value, list):
                if not isinstance(actual_value, list):
                    actual_value = [actual_value]
                if set(expected_value) != set(actual_value):
                    return False
            elif isinstance(expected_value, dict):
                if not isinstance(actual_value, dict):
                    return False
                if not self._compare_responses(expected_value, actual_value):
                    return False
            else:
                # Simple value comparison
                if str(expected_value).lower() != str(actual_value).lower():
                    return False

        return True

    def get_quality_metrics(self) -> Dict[str, Any]:
        """Get comprehensive quality control metrics for admin dashboard."""
        with self._lock:
            # Attention check metrics
            attention_metrics = {
                "enabled": self.qc_config.attention_checks_enabled,
                "total_items": len(self.attention_items),
                "total_checks": sum(len(r) for r in self.attention_results.values()),
                "total_passed": sum(
                    len([x for x in r if x.passed])
                    for r in self.attention_results.values()
                ),
                "total_failed": sum(
                    len([x for x in r if not x.passed])
                    for r in self.attention_results.values()
                ),
                "by_user": {}
            }

            for user_id, results in self.attention_results.items():
                passed = len([r for r in results if r.passed])
                failed = len([r for r in results if not r.passed])
                attention_metrics["by_user"][user_id] = {
                    "passed": passed,
                    "failed": failed,
                    "pass_rate": passed / (passed + failed) if (passed + failed) > 0 else 0
                }

            return {
                "attention_checks": attention_metrics
            }


def init_quality_control_manager(config: Dict[str, Any], base_dir: str) -> QualityControlManager:
    """Initialize the singleton QualityControlManager."""
    global _QUALITY_CONTROL_MANAGER

    with _QUALITY_CONTROL_LOCK:
        if _QUALITY_CONTROL_MANAGER is None:
            _QUALITY_CONTROL_MANAGER = QualityControlManager(config, base_dir)

    return _QUALITY_CONTROL_MANAGER


def get_quality_control_manager() -> Optional[QualityControlManager]:
    """Get the singleton QualityControlManager instance."""
    return _QUALITY_CONTROL_MANAGER


def clear_quality_control_manager():
    """Clear the singleton (for testing)."""
    global _QUALITY_CONTROL_MANAGER
    with _QUALITY_CONTROL_LOCK:
        _QUALITY_CONTROL_MANAGER = None
