"""
User State Management Module

This module provides comprehensive user state tracking and management for the Potato
annotation platform. It handles user progress through annotation phases, instance
assignments, annotation storage, and state persistence.

Key Components:
- UserStateManager: Singleton manager for all user states
- UserState: Abstract interface for user state implementations
- InMemoryUserState: In-memory implementation of user state
- MysqlUserState: Database-backed implementation (placeholder)

The system supports:
- Multi-phase annotation workflows (consent, instructions, training, annotation, post-study)
- Instance assignment and navigation
- Annotation storage (labels and spans)
- Progress tracking and statistics
- State persistence to disk
- Active learning integration
- Behavioral data collection

User states track:
- Current phase and page in the annotation workflow
- Assigned instances and current position
- Completed annotations (labels and spans)
- Timing information and statistics
- Pre-study and consent status
- Assignment limits and progress
"""

from __future__ import annotations

import json
import datetime
from collections import defaultdict, OrderedDict
import logging
import os
import threading
from typing import Optional, Dict, Any, List

from potato.phase import UserPhase
from potato.item_state_management import get_item_state_manager, Item, Label, get_training_item_state_manager
from potato.interaction_tracking import BehavioralData
from potato.quality_control import AttentionCheckResult
from potato.server_utils.date_handler import DateHandler

from dataclasses import dataclass, asdict


@dataclass
class AttentionCheckState:
    total_checks: int
    passed_checks: int
    failed_checks: int

    n_items_since_last_check: int
    attention_instance_ids = List[str]
    attention_instance_id_to_attention_check_result: Dict[str, AttentionCheckResult]

    def __init__(self, total_checks=0, passed_checks=0, failed_checks=0, n_items_since_last_check=0, attention_instance_ids=[], attention_instance_id_to_attention_check_result={}):
        self.total_checks = total_checks
        self.passed_checks = passed_checks
        self.failed_checks = failed_checks

        self.n_items_since_last_check = n_items_since_last_check
        self.attention_instance_ids = attention_instance_ids
        self.attention_instance_id_to_attention_check_result = attention_instance_id_to_attention_check_result

    def add_attention_check_result(self, attention_check_result):
        instance_id = attention_check_result.item_id

        # this should not be the case!!!
        if instance_id in self.attention_instance_id_to_attention_check_result: # not the first time this attention_check was annotated by this user in this session
            prev_result = self.attention_instance_id_to_attention_check_result[instance_id]
            if prev_result.passed:
                self.passed_checks -= 1
            else:
                self.failed_checks -= 1

            self.total_checks -= 1
            logger.warning("Attention Check was seen more than once!!!")
        else:
            self.attention_instance_ids.append(instance_id)

        self.attention_instance_id_to_attention_check_result[instance_id] = attention_check_result
        self.total_checks += 1
        if attention_check_result.passed:
            self.passed_checks += 1
        else:
            self.failed_checks += 1

    def record_non_attention_check_annotation(self):
        self.n_items_since_last_check += 1

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data['attention_instance_id_to_attention_check_result'] = {iid: ar.to_dict() for iid, ar in self.attention_instance_id_to_attention_check_result.items()}
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AttentionCheckState':
        """Reconstruct from serialized dictionary."""
        _data = data.copy()

        _data['attention_instance_id_to_attention_check_result'] = {iid: AttentionCheckResult.from_dict(ar) for iid, ar in _data['attention_instance_id_to_attention_check_result'].items()}
        return cls(**_data)


@dataclass
class TrainingState:
    max_mistakes: int  # Maximum mistakes allowed before failure (-1 = unlimited)
    max_mistakes_per_question: int  # Maximum mistakes per question before failure (-1 = unlimited)
    allow_retry: bool  # Whether to allow retry for the current question

    total_correct: int
    total_attempts: int
    total_mistakes: int

    passed: bool
    failed: bool

    show_feedback: bool  # Whether to show feedback on the current question
    feedback_message: str  # The feedback message to display
    feedback_type: str
    needs_retry: bool

    last_activity_time: Optional[datetime.datetime]

    current_training_instance_id: int

    training_instance_ids: List[str]  # List of training instance IDs
    training_instance_id_to_label_to_value: Dict[str, Dict[str, Any]] # instance_id -> label
    training_instance_id_stats: Dict[str, Dict[str, Any]] # instance_id -> {correct: bool, attempts: int}
    training_instance_id_to_behavioral_data: Dict[str, BehavioralData] # instance_id -> BehavioralData

    def __init__(self, max_mistakes: int = -1, max_mistakes_per_question: int = -1, allow_retry: bool = False):
        self.max_mistakes = max_mistakes
        self.max_mistakes_per_question = max_mistakes_per_question
        self.allow_retry = allow_retry

        self.total_correct = 0
        self.total_attempts = 0
        self.total_mistakes = 0

        self.passed = False
        self.failed = False

        self.show_feedback = False
        self.feedback_message = ""
        self.feedback_type = ""
        self.needs_retry = False

        self.last_activity_time: Optional[datetime.datetime] = None

        self.current_training_instance_id = 0

        self.training_instance_ids = [] # List of training instance IDs
        self.training_instance_id_to_label_to_value: Dict[str, Dict[str, Any]] = defaultdict(dict)  # instance_id -> label -> value
        self.training_instance_id_stats = {}  # instance_id -> {correct: bool, attempts: int}
        self.training_instance_id_to_behavioral_data = {}  # instance_id -> BehavioralData

    def change_annotation(self, instance_id: str, label: Label, value: any):
        # Change answer when frontend is changed
        # logger.debug("change_annotation")
        self.training_instance_id_to_label_to_value[instance_id][label] = value

    def commit_annotation(self, instance_id: str, is_correct: bool):
        # Commit answer when button is clicked
        # logger.debug("commit_annotation")

        if instance_id in self.training_instance_id_stats:
            prev_attempts = self.training_instance_id_stats[instance_id]["attempts"]
            prev_correct = self.training_instance_id_stats[instance_id]["correct"]
        else:
            prev_attempts = 0
            prev_correct = False

        self.training_instance_id_stats[instance_id] = {
            'correct': is_correct,
            'attempts': prev_attempts+1,
        }

        if is_correct and not prev_correct:
            self.total_correct += 1

        self.total_attempts += 1

        # Update total mistakes
        if not is_correct:
            self.total_mistakes += 1

    def advance_training_instance(self) -> bool:
        # logger.debug("advance_training_instance")
        current_index = self.get_current_training_instance_id()
        if current_index < len(self.training_instance_ids) - 1:
            self.set_current_training_instance_id(current_index + 1)
            return True
        return False

    def get_mistakes_for_training_instance(self, instance_id: str) -> int:
        # logger.debug("get_mistakes_for_training_instance")
        """Get the number of mistakes (incorrect attempts) for a specific question."""

        if instance_id not in self.training_instance_id_stats:
            return 0

        stats_data = self.training_instance_id_stats[instance_id]
        # If correct, mistakes = attempts - 1; if not correct, mistakes = attempts
        if stats_data.get('correct', False):
            return stats_data.get('attempts', 1) - 1
        else:
            return stats_data.get('attempts', 0)

    def get_total_mistakes(self) -> int:
        # logger.debug("get_total_mistakes")
        """Get the total number of mistakes across all questions."""
        return self.total_mistakes

    def should_fail_due_to_mistakes(self) -> bool:
        # logger.debug("should_fail_due_to_mistakes")
        """Check if the user should fail due to too many mistakes."""
        if 0 < self.max_mistakes <= self.total_mistakes:
            return True

        return False

    def should_fail_training_instance_due_to_mistakes(self, instance_id: str) -> bool:
        # logger.debug("should_fail_training_instance_due_to_mistakes")
        """Check if the user should fail due to too many mistakes on a single question."""
        if self.max_mistakes_per_question > 0:
            n_mistakes = self.get_mistakes_for_training_instance(instance_id)
            if n_mistakes >= self.max_mistakes_per_question:
                return True
        return False

    def get_training_instance_stats(self, instance_id: str) -> Optional[Dict[str, Any]]:
        # logger.debug("get_training_instance_stats")
        """Get statistics for a specific training instance."""
        return self.training_instance_id_stats.get(instance_id)

    def get_correct_answer_count(self) -> int:
        # logger.debug("get_correct_answer_count")
        """Get the total number of correct answers."""
        return self.total_correct

    def get_total_attempts(self) -> int:
        # logger.debug("get_total_attempts")
        """Get the total number of attempts across all questions."""
        return self.total_attempts

    def is_passed(self) -> bool:
        # logger.debug("is_passed")
        """Check if the user has passed training."""
        return self.passed

    def is_failed(self) -> bool:
        # logger.debug("is_failed")
        """Check if the user has failed training."""
        return self.failed

    def set_passed(self, passed: bool) -> None:
        # logger.debug("set_passed")
        """Set the passed status."""
        self.passed = passed

    def set_failed(self, failed: bool) -> None:
        # logger.debug("set_failed")
        """Set the failed status."""
        self.failed = failed

    def get_current_training_instance_id(self) -> int:
        # logger.debug("get_current_training_instance_id")
        """Get the current question index."""
        return self.current_training_instance_id

    def set_current_training_instance_id(self, index: int) -> None:
        # logger.debug("set_current_training_instance_id")
        """Set the current question index."""
        self.current_training_instance_id = index

    def get_training_instance_ids(self) -> List[str]:
        # logger.debug("get_training_instance_ids")
        """Get the list of training instance IDs."""
        return self.training_instance_ids

    def set_training_instance_ids(self, instance_ids: List[str]) -> None:
        # logger.debug("set_training_instance_ids")
        """Set the list of training instance IDs."""
        self.training_instance_ids = instance_ids

    def get_current_training_instance(self) -> Optional[Item]:
        # logger.debug("get_current_training_instance")
        """Get the current training instance."""
        if not self.training_instance_ids:
            return None

        current_index = self.get_current_training_instance_id()
        if current_index >= len(self.training_instance_ids):
            return None

        instance_id = self.training_instance_ids[current_index]

        # Import here to avoid circular imports
        tism = get_training_item_state_manager()
        training_items = tism.get_training_items()

        for item in training_items:
            if item.get_id() == instance_id:
                return item
        return None

    def set_feedback(self, show_feedback: bool, message: str, feedback_type: str) -> None:
        # logger.debug("set_feedback")
        """Set feedback state for the current question."""
        self.show_feedback = show_feedback
        self.feedback_message = message
        self.feedback_type = feedback_type

    def clear_feedback(self) -> None:
        # logger.debug("clear_feedback")
        """Clear feedback state."""
        self.show_feedback = False
        self.feedback_message = ""
        self.feedback_type = "info"

    def check_and_set_last_activity_time(self, activity_time : float = None):
        if activity_time:
            activity_time = DateHandler.timestamp_to_datetime(activity_time)
            if not self.last_activity_time:
                self.last_activity_time = activity_time
            elif self.last_activity_time < activity_time:
                self.last_activity_time = activity_time

    def to_dict(self) -> Dict[str, Any]:
        """Convert training state to dictionary for serialization."""
        def label_to_dict(l: Label) -> dict[str, any]:
            return {"schema": l.get_schema(), "name": l.get_name()}

        def convert_label_dict(d: dict[Label, any]) -> list[tuple[dict[str], str]]:
            return [(label_to_dict(k), v) for k, v in d.items()]

        d = {
            'total_correct': self.total_correct,
            'total_attempts': self.total_attempts,
            'total_mistakes': self.total_mistakes,
            'passed': self.passed,
            'failed': self.failed,
            'current_training_instance_id': self.current_training_instance_id,
            'show_feedback': self.show_feedback,
            'feedback_message': self.feedback_message,
            'allow_retry': self.allow_retry,
            'feedback_type': self.feedback_type,
            'max_mistakes': self.max_mistakes,
            'max_mistakes_per_question': self.max_mistakes_per_question,
            'last_activity_time': DateHandler.datetime_to_str(self.last_activity_time),
            "training_instance_ids": list(self.training_instance_ids),
            "training_instance_id_to_label_to_value": {iid: convert_label_dict(label_to_value) for iid, label_to_value in self.training_instance_id_to_label_to_value.items()},
            "training_instance_id_stats": {iid: stats.copy() for iid, stats in self.training_instance_id_stats.items()},
            "training_instance_id_to_behavioral_data": {iid: bd.to_dict() if hasattr(bd, "to_dict") else bd for iid, bd in self.training_instance_id_to_behavioral_data.items()},
        }

        #logger.debug(f"training_state to_dict(): {d}")

        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TrainingState':
        """Create training state from dictionary."""
        training_state = cls()
        training_state.total_correct = data['total_correct']
        training_state.total_attempts = data['total_attempts']
        training_state.total_mistakes = data['total_mistakes']
        training_state.passed = data['passed']
        training_state.failed = data['failed']
        training_state.current_training_instance_id = data['current_training_instance_id']
        training_state.show_feedback = data['show_feedback']
        training_state.feedback_message = data['feedback_message']
        training_state.allow_retry = data['allow_retry']
        training_state.feedback_type = data['feedback_type']
        training_state.max_mistakes = data['max_mistakes']
        training_state.max_mistakes_per_question = data['max_mistakes_per_question']

        training_state.last_activity_time = DateHandler.str_to_datetime(data.get('last_activity_time'))

        training_state.training_instance_ids = data['training_instance_ids']

        # restore defaultdict(list) structure for labels
        def to_label(d: dict[str, str]) -> Label:
            return Label(d['schema'], d['name'])

        training_state.training_instance_id_to_label_to_value = defaultdict(
            dict,
            {
                iid: {to_label(k): v for k, v in l2v}
                for iid, l2v in data['training_instance_id_to_label_to_value'].items()
            }
        )

        # shallow copy is fine
        training_state.training_instance_id_stats = {iid: stats.copy() for iid, stats in data['training_instance_id_stats'].items()}

        from potato.interaction_tracking import BehavioralData
        training_state.training_instance_id_to_behavioral_data = {iid: BehavioralData.from_dict(bd) for iid, bd in data['training_instance_id_to_behavioral_data'].items()}

        return training_state


# Database imports
try:
    from potato.database import DatabaseManager, MysqlUserState

    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.basicConfig()

# Singleton instance of the user state manager with thread-safe lock
USER_STATE_MANAGER = None
_USER_STATE_MANAGER_LOCK = threading.Lock()


def init_user_state_manager(config: dict) -> UserStateManager:
    """
    Initialize the singleton UserStateManager instance.

    This function creates the global UserStateManager that will be shared
    across all users. It's designed to be called once during application startup.
    Thread-safe initialization using double-checked locking pattern.

    Args:
        config: Configuration dictionary containing user management settings

    Returns:
        UserStateManager: The initialized singleton instance
    """
    global USER_STATE_MANAGER

    # Double-checked locking for thread safety
    if USER_STATE_MANAGER is None:
        with _USER_STATE_MANAGER_LOCK:
            # Check again inside the lock
            if USER_STATE_MANAGER is None:
                USER_STATE_MANAGER = UserStateManager(config)
    return USER_STATE_MANAGER


def clear_user_state_manager():
    """
    Clear the singleton user state manager instance (for testing).

    This function is primarily used for testing purposes to reset the
    global state between test runs. Thread-safe.
    """
    global USER_STATE_MANAGER
    with _USER_STATE_MANAGER_LOCK:
        USER_STATE_MANAGER = None


def get_user_state_manager() -> UserStateManager:
    """
    Get the singleton UserStateManager instance.

    Returns:
        UserStateManager: The singleton instance

    Raises:
        ValueError: If the manager has not been initialized
    """
    global USER_STATE_MANAGER
    if USER_STATE_MANAGER is None:
        raise ValueError('User state manager has not been initialized')
    return USER_STATE_MANAGER


class UserStateManager:
    """
    Manages all user states in the annotation system.

    This singleton class provides centralized management of all user states,
    including user creation, state tracking, phase management, and persistence.
    It coordinates with the ItemStateManager for instance assignments and
    supports various annotation workflows.
    """

    def __init__(self, config: dict):
        """
        Initialize the user state manager.

        Args:
            config: Configuration dictionary containing user management settings
        """
        self.config = config
        self.user_to_annotation_state = {}
        self.task_assignment = {}
        self.prolific_study = None
        self.phase_type_to_name_to_page = defaultdict(OrderedDict)

        # Thread-safe lock for shared state access
        self._state_lock = threading.RLock()

        # TODO: load this from the config
        self.max_annotations_per_user = -1

        # Database support
        self.db_manager = None
        self.use_database = False

        # Initialize database if configured
        if DATABASE_AVAILABLE and 'database' in config:
            db_config = config['database']
            if db_config.get('type') == 'mysql':
                try:
                    self.db_manager = DatabaseManager(config)
                    self.use_database = True
                    self.db_manager.create_tables()
                    logger.info("Initialized MySQL database backend")
                except Exception as e:
                    logger.error(f"Failed to initialize database: {e}")
                    self.use_database = False

        self.logger = logging.getLogger(__name__)
        # setting to debug
        self.logger.setLevel(logging.DEBUG)
        logging.basicConfig()

    def add_phase(self, phase_type: UserPhase, phase_name: str, page_fname: str):
        logger.debug(f"Add Phase phase_type: {phase_type}, phase_name: {phase_name}, page_fname: {page_fname}")
        """
        Add a phase page to the phase mapping.

        Args:
            phase_type: The type of phase (e.g., CONSENT, INSTRUCTIONS)
            phase_name: The name of the page within the phase
            page_fname: The filename of the HTML page
        """
        self.phase_type_to_name_to_page[phase_type][phase_name] = page_fname

    def add_user(self, user_id: str, session_id: str) -> UserState:
        """
        Add a new user to the user state manager (thread-safe).

        Args:
            user_id: Unique identifier for the new user

        Returns:
            UserState: The created user state object

        Raises:
            ValueError: If a user with the same ID already exists
        """
        with self._state_lock:
            logger.debug(f"=== ADD USER START ===")
            logger.debug(f"Adding user {user_id} (Session ID: {session_id})")
            logger.debug(f"Current users and sessions: {[f'{u} ({sid})' for u, s in self.user_to_annotation_state.items() for sid, _ in s.items()]}")
            # logger.debug(f"User already exists: {user_id in self.user_to_annotation_state}")

            if user_id in self.user_to_annotation_state and session_id in self.user_to_annotation_state.get(user_id, {}):
                logger.warning(f'User "{user_id}" already exists in the user state manager with Session ID {session_id}')
                raise ValueError(f'User "{user_id}" already exists in the user state manager with Session ID {session_id}')

            self.set_or_create_user_state_for_session(user_id, session_id)
            logger.debug(f"Current users and sessions: {[f'{u} ({sid})' for u, s in self.user_to_annotation_state.items() for sid, _ in s.items()]}")
            logger.debug(f"=== ADD USER END ===")

    def set_or_create_user_state_for_session(self, user_id: str, session_id: str, user_state=None):
        logger.debug(f"=== ADD USER STATE FOR SESSION START ===")
        logger.debug(f"Adding user state for user {user_id} (Session ID: {session_id})")

        # Create appropriate user state based on configuration
        if not user_state:
            if self.use_database and self.db_manager:
                # logger.debug(f"Creating MysqlUserState for user {user_id} (Session ID: {session_id})")
                user_state = MysqlUserState(user_id, self.db_manager, self.max_annotations_per_user)
            else:
                # logger.debug(f"Creating InMemoryUserState for user {user_id} (Session ID: {session_id})")
                user_state = InMemoryUserState(user_id, session_id, self.max_annotations_per_user)

            logger.debug(f"User State created: {user_state.to_json()}")
        else:
            logger.debug(f"User State provided: {user_state.to_json()}")

        if user_id in self.user_to_annotation_state:
            current_annotation_state = self.user_to_annotation_state[user_id]

            if type(current_annotation_state) != dict:
                raise ValueError(f'Type of current_annotation_state is not a dict. Instead: {type(current_annotation_state)}.')

            current_annotation_state[session_id] = user_state
            self.user_to_annotation_state[user_id] = current_annotation_state

        else:
            self.user_to_annotation_state[user_id] = {session_id: user_state}

    def remove_get_or_create_user(self, user_id: str) -> UserState:
        """
        Get a user from the user state manager, creating a new user if they don't exist.

        Args:
            user_id: Unique identifier for the user

        Returns:
            UserState: The user state object (existing or newly created)
        """
        if user_id not in self.user_to_annotation_state:
            self.logger.debug('Previously unknown user "%s"; creating new annotation state' % (user_id))
            user_state = self.add_user(user_id)
        else:
            user_state = self.user_to_annotation_state[user_id]
        return user_state

    def get_max_annotations_per_user(self) -> int:
        """
        Get the maximum number of items that each annotator should annotate.

        Returns:
            int: Maximum annotations per user (-1 for unlimited)
        """
        return self.max_annotations_per_user

    def set_max_annotations_per_user(self, max_annotations_per_user: int) -> None:
        """
        Set the maximum number of items that each annotator should annotate.

        Args:
            max_annotations_per_user: Maximum annotations per user (-1 for unlimited)
        """
        self.max_annotations_per_user = max_annotations_per_user

    def get_user_state(self, user_id: str, session_id: str) -> UserState:
        '''
        Gets a user from the user state manager or None if the user does not exist (thread-safe).'''
        # logger.debug("=== GET USER STATE ===")
        # logger.debug(f"user_id: {user_id}, session_id: {session_id}")

        with self._state_lock:
            if user_id in self.user_to_annotation_state:
                if session_id in self.user_to_annotation_state[user_id]:
                    # logger.debug(f"Return existing user state from user_to_annotation_state: user_id= {self.user_to_annotation_state[user_id][session_id].user_id}, session_id= {self.user_to_annotation_state[user_id][session_id].session_id}")
                    return self.user_to_annotation_state[user_id][session_id]

            if self.use_database and self.db_manager:
                # Try to load from database
                try:
                    user_state = MysqlUserState(user_id, self.db_manager, self.max_annotations_per_user)
                    self.user_to_annotation_state[user_id] = user_state
                    return user_state
                except Exception as e:
                    logger.warning(f"Failed to load user state from database for {user_id}: {e}")
            else:
                # Try to load the user state from disk if it exists
                try:
                    output_annotation_dir = self.config["output_annotation_dir"]
                    user_dir = os.path.join(output_annotation_dir, user_id)
                    if os.path.exists(user_dir):
                        user_state = InMemoryUserState.load(user_dir, session_id)
                        self.set_or_create_user_state_for_session(user_id, session_id, user_state)
                        return user_state
                except Exception as e:
                    logger.warning(f"Failed to load user state for {user_id} and session id {session_id}: {e}")

    def get_all_user_states(self, user_id: str) -> list[UserState]:
        '''
        Gets a user from the user state manager or None if the user does not exist (thread-safe).'''

        # logger.debug("=== GET ALL USER STATES ===")
        # logger.debug(f"user_id: {user_id}")

        with self._state_lock:
            return self.user_to_annotation_state[user_id]

    def get_all_users(self) -> list[UserState]:
        '''Gets all users from the user state manager (thread-safe).'''
        with self._state_lock:
            return list(self.user_to_annotation_state.values())

    def get_phase_html_fname(self, phase: UserPhase, page: str) -> str:
        '''Returns the filename of the page for the given phase and page name'''
        return self.phase_type_to_name_to_page[phase][page]

    def has_user(self, user_id: str) -> bool:
        '''Checks if a user exists in the user state manager'''
        return user_id in self.user_to_annotation_state

    def has_user_sessionid(self, user_id: str, session_id: str) -> bool:
        '''Checks if a user exists in the user state manager'''
        return self.user_to_annotation_state.get(user_id, {}).get(session_id, None) is not None

    def advance_phase(self, user_id: str, session_id: str) -> None:
        '''Moves the user to the next page in the current phase or the next phase'''
        #logger.debug("ADVANCE PHASE")
        phase, page = self.get_next_user_phase_page(user_id, session_id)
        #logger.debug(f"next phase: {phase}, next page: {page}")

        # Get the current user's state
        user_state = self.get_user_state(user_id, session_id)

        user_state.completed_phase_and_pages.append(user_state.get_current_phase_and_page())

        user_state.set_current_phase_and_page(phase, page)

    def get_next_user_phase_page(self, user_id: str, session_id: str) -> tuple[UserPhase, str]:
        '''Returns the name and filename of next the page for the user, either
           in the current phase or next phase. This method handles the
           case of where there are multiple pages within the same phase type'''

        #logger.debug(f"[get_next_user_phase_page] phase_type_to_name_to_page: {self.phase_type_to_name_to_page}")

        # Get the current user's state
        user_state = self.get_user_state(user_id, session_id)

        # Get the current of their phase
        cur_phase, cur_page = user_state.get_current_phase_and_page()
        if cur_phase == UserPhase.DONE:
            return UserPhase.DONE, "done"

        #logger.debug(f"cur_phase: {cur_phase}, cur_page: {cur_page}")

        page2file_for_cur_phase = self.phase_type_to_name_to_page[cur_phase]
        # logger.debug(f"page2file_for_cur_phase: {page2file_for_cur_phase}")

        if len(page2file_for_cur_phase) > 1 and cur_page is not None:
            pages_for_cur_phase = list(page2file_for_cur_phase.keys())
            # Handle case where cur_page is not in the list
            if cur_page in pages_for_cur_phase:
                cur_page_index = pages_for_cur_phase.index(cur_page)
                # If there are more pages in this phase, return the next one
                if cur_page_index < len(pages_for_cur_phase) - 1:
                    next_page = pages_for_cur_phase[cur_page_index + 1]
                    return cur_phase, next_page

        # If there are no more pages in this phase, return the next phase.
        # Use the config's phase order instead of the enum order
        if "phases" in self.config and "order" in self.config["phases"]:
            # Use config phase order
            config_phase_order = self.config["phases"]["order"]
            # Convert config phase names to UserPhase enums
            config_phases = []
            for phase_name in config_phase_order:
                if phase_name in self.config["phases"]:
                    phase_type_str = self.config["phases"][phase_name]["type"]
                    phase_type = UserPhase.fromstr(phase_type_str)
                    if phase_type in config_phases:
                        continue  # skip if phasetype already tracked
                    if phase_type in self.phase_type_to_name_to_page:
                        config_phases.append(phase_type)
                    else:
                        pass  # Phase not found in phase_type_to_name_to_page
                else:
                    pass  # Phase not found in config phases

            # Add ANNOTATION phase if it's not in config but exists in phase_type_to_name_to_page
            if UserPhase.ANNOTATION in self.phase_type_to_name_to_page and UserPhase.ANNOTATION not in config_phases:
                config_phases.append(UserPhase.ANNOTATION)

            # Find current phase in config order
            #logger.debug(f"config_phases: {config_phases}")
            if cur_phase == UserPhase.LOGIN:
                next_phase = config_phases[0]
                #logger.debug(f"next_phase: {next_phase}")
                next_page = list(self.phase_type_to_name_to_page[next_phase].keys())[0]
                #logger.debug(f"next_page: {next_page}")
                return next_phase, next_page

            elif cur_phase in config_phases:
                cur_phase_index = config_phases.index(cur_phase)
                #logger.debug(f"cur_phase_index: {cur_phase_index}")
                if cur_phase_index < len(config_phases) - 1:
                    next_phase = config_phases[cur_phase_index + 1]
                    #logger.debug(f"next_phase: {next_phase}")
                    # Use the first page in the next phase
                    next_page = list(self.phase_type_to_name_to_page[next_phase].keys())[0]
                    #logger.debug(f"next_page: {next_page}")

                    return next_phase, next_page
                else:
                    pass  # Current phase is last in config order
            else:
                pass  # Current phase not found in config_phases
        else:
            # Fallback to enum order if no config order is specified
            all_phases = [p for p in list(UserPhase) if p in self.phase_type_to_name_to_page]
            cur_phase_index = all_phases.index(cur_phase)
            if cur_phase_index < len(all_phases) - 1:
                next_phase = all_phases[cur_phase_index + 1]
                # Use the first page in the next phase
                next_page = list(self.phase_type_to_name_to_page[next_phase].keys())[0]
                return next_phase, next_page

        return UserPhase.DONE, "done"

    def get_user_session_ids(self):
        user_session_ids = {}

        with self._state_lock:
            for username, session2state in self.user_to_annotation_state.items():
                sessions = list(session2state.keys())
                for session_id_key in sessions:
                    if self.user_to_annotation_state[username][session_id_key].session_id != session_id_key:
                        logger.warning(
                            f"User State Session ID Key is not the same as stored in User State (Key: {session_id_key}, User State: {self.user_to_annotation_state[username][session_id_key].session_id})")

                user_session_ids[username] = sessions

        return user_session_ids

    def get_user_ids(self) -> list[str]:
        '''Gets all user IDs from the user state manager'''
        with self._state_lock:
            return list(self.user_to_annotation_state.keys())

    def get_user_count(self) -> int:
        '''Get the number of users in the user state manager'''
        return len(self.user_to_annotation_state)

    def get_total_annotations(self) -> int:
        """
        Returns the total number of unique annotations done across all users.
        """
        total = 0
        user_session_ids = self.get_user_session_ids()
        for username, session_ids in user_session_ids.items():
            for session_id in session_ids:
                user_state = self.get_user_state(username, session_id)
                total += user_state.get_annotation_count()

        return total

    def is_consent_required(self) -> bool:
        return UserPhase.CONSENT in self.phase_type_to_name_to_page

    def is_instructions_required(self) -> bool:
        return UserPhase.INSTRUCTIONS in self.phase_type_to_name_to_page

    def is_prestudy_required(self) -> bool:
        return UserPhase.PRESTUDY in self.phase_type_to_name_to_page

    def is_training_required(self) -> bool:
        return UserPhase.TRAINING in self.phase_type_to_name_to_page

    def is_poststudy_required(self) -> bool:
        return UserPhase.POSTSTUDY in self.phase_type_to_name_to_page

    def save_user_state(self, user_state: UserState) -> None:
        '''Saves the user state for the given user ID'''
        # Figure out where this user's data would be stored on disk
        output_annotation_dir = self.config["output_annotation_dir"]
        username = user_state.get_user_id()

        # Save the user state
        user_state.save()

    def load_user_state(self, user_id: str, session_id: str) -> UserState:
        '''Loads the user state for the given user ID and session ID'''

        # TODO: make the user state type configurable between in-memory and DB-backed.
        #logger.debug("=== LOAD USER STATE STARTS===")
        #logger.debug(f"user: {user_id} (Session ID: {session_id})")

        output_annotation_dir = self.config["output_annotation_dir"]
        user_dir = os.path.join(output_annotation_dir, user_id)
        state_fp = f"{user_dir}/{session_id}_user_state.json"

        user_state = InMemoryUserState.load(state_file=state_fp)

        if user_state.session_id != session_id:
            logger.warning(f'user_state.session_id "{user_state.session_id}" != provided session_id {session_id}')
            return None

        user_id = user_state.user_id

        if user_id in self.user_to_annotation_state:
            if session_id in self.user_to_annotation_state.get(user_id, {}):
                logger.warning(f'User "{user_id}" (Session ID: {session_id}) already exists in the user state manager, but is being overwritten by load_state()')
            else:
                self.user_to_annotation_state[user_id][session_id] = user_state
                logger.debug(f'User "{user_id}" (Session ID: {session_id}) loaded')
        else:
            self.user_to_annotation_state[user_id] = {session_id: user_state}
            logger.debug(f'User "{user_id}" (Session ID: {session_id}) loaded')

        #logger.debug("=== END LOAD USER STATE ===")

        return user_state

    def clear(self):
        """Clear all user state (for testing/debugging)."""
        self.user_to_annotation_state.clear()
        self.task_assignment.clear()
        self.prolific_study = None
        self.phase_type_to_name_to_page.clear()
        self.max_annotations_per_user = -1

        # Clear database if using it
        if self.use_database and self.db_manager:
            try:
                self.db_manager.drop_tables()
                self.db_manager.create_tables()
                logger.info("Cleared database tables")
            except Exception as e:
                logger.error(f"Failed to clear database: {e}")

        # Reload phases after clearing to ensure phase_type_to_name_to_page is repopulated
        from potato.flask_server import load_phase_data
        load_phase_data(self.config)


class UserState:
    """
    An interface class for maintaining state on which annotations users have completed.
    """

    def __init__(self, user_id: str, session_id: str, max_assignments: int = -1):
        self.user_id = user_id
        self.session_id = session_id
        self.max_assignments = max_assignments

    def get_user_id(self) -> str:
        return self.user_id

    def get_session_id(self) -> str:
        return self.session_id

    def get_max_assignments(self) -> int:
        return self.max_assignments

    # Methods for phase and page handling
    def get_current_phase(self) -> UserPhase:
        return NotImplementedError

    def get_current_phase_and_page(self) -> tuple[UserPhase, str]:
        raise NotImplementedError()

    def set_current_phase_and_page(self, phase: UserPhase, page: str) -> None:
        raise NotImplementedError()

    # Methods to handle instances
    def get_current_instance(self) -> Item:
        raise NotImplementedError()

    def get_current_instance_id(self) -> str:
        raise NotImplementedError()

    def get_current_instance_index(self) -> int:
        raise NotImplementedError()

    def assign_instance(self, item: Item) -> bool:
        raise NotImplementedError()

    def get_assigned_instances_count(self) -> int:
        raise NotImplementedError()

    def get_assigned_instance_ids(self) -> set[str]:
        return NotImplementedError()

    def has_remaining_assignments(self) -> bool:
        return self.get_assigned_instances_count() > self.get_annotation_count()
        return NotImplementedError()

    # Methods to navigate instances
    def go_back(self) -> bool:
        raise NotImplementedError()

    def go_forward(self) -> bool:
        raise NotImplementedError()

    def is_at_end_index(self) -> bool:
        raise NotImplementedError()

    # Methods to handle annotations
    def get_all_annotations(self):
        raise NotImplementedError()

    def get_annotation_count(self) -> int:
        raise NotImplementedError()

    def get_annotated_instance_ids(self) -> set[str]:
        raise NotImplementedError()

    def set_all_annotations(self):
        raise NotImplementedError()

    def add_annotation(self, instance_id, annotation):
        raise NotImplementedError()

    def has_annotated(self, instance_id: str) -> bool:
        return NotImplementedError()

    # Methods to save and load
    def save(self) -> None:
        raise NotImplementedError()

    def load(self) -> UserState:
        raise NotImplementedError()

    # Methods for session timing
    def start_session_timer(self) -> None:
        raise NotImplementedError()

    def end_session_timer(self) -> None:
        raise NotImplementedError()

    # Methods for training handling
    def get_training_state(self) -> TrainingState:
        return NotImplementedError()

    def init_training_state(self, max_mistakes: int = -1, max_mistakes_per_question: int = -1, allow_retry: bool = False) -> None:
        return NotImplementedError()

    # Methods for admin
    def get_recent_actions(self, minutes: int = 5):
        raise NotImplementedError()

    def get_total_working_time(self):
        raise NotImplementedError()

    def get_user_statistics(self):
        raise NotImplementedError()

    def get_suspicious_activity(self):
        raise NotImplementedError()

    def clear_all_annotations(self):
        raise NotImplementedError()

    def reset_training_state(self):
        return NotImplementedError()

    # Static methods
    @staticmethod
    def parse_time_string(time_string):
        """
        Parse the time string generated by front end,
        e.g., 'time_string': 'Time spent: 0d 0h 0m 5s '
        """
        time_dict = {}
        items = time_string.strip().split(" ")
        if len(items) != 6:
            return None
        time_dict["day"] = int(items[2][:-1])
        time_dict["hour"] = int(items[3][:-1])
        time_dict["minute"] = int(items[4][:-1])
        time_dict["second"] = int(items[5][:-1])
        time_dict["total_seconds"] = (
                time_dict["second"] + 60 * time_dict["minute"] + 3600 * time_dict["hour"]
        )

        return time_dict


class InMemoryUserState(UserState):

    def __init__(self, user_id: str, session_id: str, max_assignments: int = -1, output_annotation_dir: str = "output/"):
        super().__init__(user_id, session_id, max_assignments)

        self.output_annotation_dir = output_annotation_dir

        # How many items a user can be assigned
        self.max_assignments = max_assignments

        # This keeps track of which page the user is on in the annotation process.
        # All users start at the LOGIN page.
        self.current_phase_and_page = (UserPhase.LOGIN, "login")

        # This data structure keeps track of which phases and pages the user has completed
        # and shouldn't include the current phase (yet)
        self.completed_phase_and_pages = list()

        self.phase_page_start_times = {UserPhase.LOGIN: {"login": DateHandler.get_timestamp_now()}}

        # This data struction records the specific ordering for which instances have been
        # labeled so that, should orderings differ between users, we can still determine
        # the previous and next instances if a user navigates back and forth.
        self.instance_id_ordering = []

        # Utilit data structure for O(1) look up of whether some ID is already in our ordering
        self.assigned_instance_ids = set()

        # This is the index in instance_id_ordering that the user is currently being shown.
        self.current_instance_index = -1

        # TODO: Put behavioral information of each instance with the labels
        # together however, that requires too many changes of the data structure
        # therefore, we contruct a separate dictionary to save all the
        # behavioral information (e.g. time, click, ..)
        self.instance_id_to_behavioral_data = defaultdict(dict)

        # The data structure to save the labels (e.g. multiselect, radio, text) that
        # a user labels for each instance.
        self.instance_id_to_label_to_value = defaultdict(dict)

        # New: Session tracking
        self.session_start_time: Optional[datetime.datetime] = None
        self.last_activity_time: Optional[datetime.datetime] = None

        # New: Training state tracking
        self.training_state = None # use init_training_state()

        self.attention_check_state = AttentionCheckState()

    # Methods for phase and page handling
    def get_current_phase(self) -> UserPhase:
        return self.current_phase_and_page[0]

    def get_current_phase_and_page(self) -> tuple[UserPhase, str]:
        return self.current_phase_and_page

    def set_current_phase_and_page(self, phase: UserPhase, page: str) -> None:
        self.current_phase_and_page = (phase, page)

        page_times = self.phase_page_start_times.get(phase, {})
        page_times[page] = DateHandler.get_timestamp_now()
        self.phase_page_start_times[phase] = page_times

    # Methods to handle instances
    def get_current_instance_index(self) -> int:
        '''Returns the index of the item the user is annotating within the list of items that the user has currently been assigned to annotate'''
        return self.current_instance_index

    def is_at_last_assigned_instance(self) -> bool:
        return self.current_instance_index == len(self.assigned_instance_ids) - 1

    def get_current_instance_id(self) -> str:
        '''Returns the ID of the instance that the user is currently annotating'''
        if self.current_instance_index == -1:
            return None

        if self.current_instance_index >= len(self.instance_id_ordering):
            return None

        inst_id = self.instance_id_ordering[self.current_instance_index]
        return inst_id

    def get_current_instance(self) -> Item:
        '''Returns the instance that the user is currently annotating'''
        current_instance_id = self.get_current_instance_id()

        if current_instance_id:
            return get_item_state_manager().get_item(current_instance_id)
        else:
            return None

    def assign_instance(self, item: Item) -> bool:
        ''' Assigns an instance to the user for annotation'''

        # check that the item has not already been assigned to the user
        item_id = item.get_id()
        if item_id in self.assigned_instance_ids:
            return False

        self.instance_id_ordering.append(item_id)
        self.assigned_instance_ids.add(item_id)

        # If this is the first assigned instance, set the current instance to be the first one
        if self.current_instance_index == -1:
            self.current_instance_index = 0

        logger.debug(f"User {self.user_id} (Session ID {self.session_id}) - Assigned item {item_id}")
        return True

    def get_assigned_instances_count(self) -> int:
        """Returns the number of currently assigned instances"""
        return len(self.assigned_instance_ids)

    def get_assigned_instance_ids(self) -> set[str]:
        """Returns the set of assigned instance IDs"""
        return self.assigned_instance_ids.copy()

    def has_assignments(self) -> bool:
        """Returns whether this user has currently assigned instances"""
        return self.get_assigned_instances_count() > 0

    def has_open_assignments(self) -> bool:
        """Returns whether this user has open assignments that still need annotation"""
        return self.get_annotation_count() < self.get_assigned_instances_count()

    def is_allowed_remaining_assignments(self) -> bool:
        """Returns whether this is user is still allowed to be assigned instances before reaching max_assignments"""
        logger.debug(f"self.get_assigned_instances_count(): {self.get_assigned_instances_count()}")
        logger.debug(f"self.max_assignments: {self.max_assignments}")

        return self.max_assignments < 0 or self.get_assigned_instances_count() < self.max_assignments

    # Methods to navigate instances
    def go_back(self) -> bool:
        '''Moves the user back to the previous instance and returns True if successful'''
        if self.current_instance_index > 0:
            self.current_instance_index -= 1
            return True
        else:
            return False

    def go_forward(self) -> bool:
        '''Moves the user forward to the next instance and returns True if successful'''
        if self.current_instance_index < len(self.instance_id_ordering) - 1:
            self.current_instance_index += 1
            return True
        else:
            return False

    # Methods to handle annotations
    def get_annotation(self, instance_id: int):
        return self.instance_id_to_label_to_value.get(instance_id, {})

    def get_all_annotations(self):
        """Returns all annotations for all annotated instances"""
        labeled = set(self.instance_id_to_label_to_value.keys())

        anns = {}
        for iid in labeled:
            labels = {}
            if iid in self.instance_id_to_label_to_value:
                labels = self.instance_id_to_label_to_value[iid]

            anns[iid] = {"labels": labels}

        return anns

    def get_annotation_count(self) -> int:
        '''Returns the total number of instances annotated by this user.'''
        return len(self.get_annotated_instance_ids())

    def get_annotated_instance_ids(self) -> set[str]:
        return set(self.instance_id_to_label_to_value.keys())

    def set_all_annotations(self):
        raise NotImplementedError()

    def add_annotation(self, instance_id: str, label: Label, value: any) -> None:
        #logger.debug("add_annotation")
        self.instance_id_to_label_to_value[instance_id][label] = value

    def has_annotated(self, instance_id: str) -> bool:
        '''Returns True if the user has annotated the instance with the given ID'''
        return instance_id in self.instance_id_to_label_to_value

    # Methods to save and load
    def save(self) -> None:
        '''Saves the user's state to disk using atomic write (temp file + rename).'''
        #logger.debug("=== SAVE ===")
        #logger.debug(f"Session ID: {self.session_id}")
        import tempfile

        user_dir = os.path.join(self.output_annotation_dir, self.user_id)

        # Convert the state to something JSON serializable
        user_state = self.to_json()

        #logger.debug(f"user_state: {user_state}")

        # Ensure directory exists (use exist_ok to avoid race conditions)
        os.makedirs(user_dir, exist_ok=True)

        # Write atomically: write to temp file, then rename
        session_id = self.session_id
        fn = f"{session_id}_user_state.json"
        state_file = os.path.join(user_dir, fn)

        # Create temp file in same directory to ensure atomic rename works
        fd, temp_path = tempfile.mkstemp(dir=user_dir, suffix='.tmp')
        try:
            with os.fdopen(fd, 'wt') as outf:
                json.dump(user_state, outf, indent=2)
                outf.flush()
                os.fsync(outf.fileno())  # Ensure data is written to disk
            # Atomic rename (works on POSIX, best-effort on Windows)
            os.replace(temp_path, state_file)
            #logger.debug(f"saved user_state: {user_state}")

        except Exception:
            # Clean up temp file if something went wrong
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    @staticmethod
    def load(state_file) -> UserState:
        '''Loads the user's state from disk'''
        #state_file = "/home/schellsn/author2paper_potato_multi_session/output/1233/18743864515_user_state.json"
        #user_dir = os.path.join(self.output_annotation_dir, self.user_id)

        #fn = f"{self.session_id}_user_state.json"

        #state_file = os.path.join(user_dir, fn)
        if not os.path.exists(state_file):
            raise ValueError(f'User state file {state_file} not found"')

        with open(state_file, 'rt') as f:
            j = json.load(f)

        def to_label(d: dict[str, str]) -> Label:
            return Label(d['schema'], d['name'])

        def to_phase_and_page(t: tuple[str, str]) -> tuple[UserPhase, str]:
            return (UserPhase.fromstr(t[0]), t[1])

        user_state = InMemoryUserState(j['user_id'], j['session_id'], j['max_assignments'])

        user_state.instance_id_ordering = j['instance_id_ordering']
        user_state.assigned_instance_ids = set(j['instance_id_ordering'])
        user_state.current_instance_index = j['current_instance_index']

        # Restore behavioral data (used for interaction tracking)
        from potato.interaction_tracking import BehavioralData
        behavioral_data = j.get('instance_id_to_behavioral_data', {})
        for instance_id, bd_dict in behavioral_data.items():
            if isinstance(bd_dict, dict):
                user_state.instance_id_to_behavioral_data[instance_id] = BehavioralData.from_dict(bd_dict)
            else:
                user_state.instance_id_to_behavioral_data[instance_id] = bd_dict

        for iid, l2v in j['instance_id_to_label_to_value'].items():
            user_state.instance_id_to_label_to_value[iid] = {to_label(k): v for k, v in l2v}

        # These require converting the dictionaries back to the original types
        user_state.current_phase_and_page = to_phase_and_page(j['current_phase_and_page'])
        user_state.completed_phase_and_pages = [
            to_phase_and_page(pp) for pp in j['completed_phase_and_pages']
        ]

        user_state.phase_page_start_times = {}
        for pp, start_time in j["phase_page_start_times"].items():
            phase_str, page = json.loads(pp)
            phase = UserPhase.fromstr(phase_str)

            if phase not in user_state.phase_page_start_times:
                user_state.phase_page_start_times[phase] = {}

            start_time = DateHandler.str_to_datetime(start_time)

            user_state.phase_page_start_times[phase][page] = start_time

        #logger.debug(f"j.get('last_activity_time'): {j.get('last_activity_time')}")
        user_state.last_activity_time = DateHandler.str_to_datetime(j.get('last_activity_time'))

        # Restore training state if present
        if 'training_state' in j:
            user_state.training_state = TrainingState.from_dict(j['training_state'])

        # Restore attention_check_state if present
        if 'attention_check_state' in j:
            user_state.attention_check_state = AttentionCheckState.from_dict(j['attention_check_state'])

        #logger.debug(f"loaded user_state: {user_state.to_json()}")

        return user_state

    def to_json(self):

        def pp_to_tuple(pp: tuple[UserPhase, str]) -> tuple[str, str]:
            return (str(pp[0]), pp[1])

        def label_to_dict(l: Label) -> dict[str, any]:
            return {
                "schema": l.get_schema(),
                "name": l.get_name()
            }

        def convert_label_dict(d: dict[Label, any]) -> list[tuple[dict[str], str]]:
            return [(label_to_dict(k), v) for k, v in d.items()]

        # Do the easy cases first
        d = {'user_id': self.user_id,
             'session_id': self.session_id,
             'instance_id_ordering': self.instance_id_ordering,
             'current_instance_index': self.current_instance_index,
             'current_phase_and_page': pp_to_tuple(self.current_phase_and_page),
             'completed_phase_and_pages': [pp_to_tuple(pp) for pp in self.completed_phase_and_pages],
             'max_assignments': self.max_assignments,
             'instance_id_to_behavioral_data': {},
             "phase_page_start_times": {},
             "last_activity_time": DateHandler.datetime_to_str(self.last_activity_time)
        }

        for phase, page_times in self.phase_page_start_times.items():
            for page, time in page_times.items():
                #pp = pp_to_tuple((phase, page)) tuple as key not possible
                pp = json.dumps([str(phase), page])
                d["phase_page_start_times"][pp] = DateHandler.datetime_to_str(time)

        # Serialize behavioral data (used for interaction tracking)
        for instance_id, bd in self.instance_id_to_behavioral_data.items():
            if hasattr(bd, 'to_dict'):
                d['instance_id_to_behavioral_data'][instance_id] = bd.to_dict()
            elif isinstance(bd, dict):
                d['instance_id_to_behavioral_data'][instance_id] = bd
            else:
                d['instance_id_to_behavioral_data'][instance_id] = {}

        d['instance_id_to_label_to_value'] = {k: convert_label_dict(v) for k, v in self.instance_id_to_label_to_value.items()}

        if self.training_state:
            d['training_state'] = self.training_state.to_dict()

        if self.attention_check_state:
            d['attention_check_state'] = self.attention_check_state.to_dict()

        #logger.debug(f"user_state to_json(): {d}")
        return d

    # Methods for session timing
    def old_start_session_timer(self) -> None:
        self.session_start_time = DateHandler.get_timestamp_now()
        self.last_activity_time = self.session_start_time

    def old_end_session_timer(self) -> None:
        self.session_end_time = DateHandler.get_timestamp_now()
        self.last_activity_time = self.session_end_time

    def check_and_set_last_activity_time(self, activity_time : float = None):
        if activity_time:
            activity_time = DateHandler.timestamp_to_datetime(activity_time)
            if not self.last_activity_time:
                self.last_activity_time = activity_time
            elif self.last_activity_time < activity_time:
                self.last_activity_time = activity_time

    # Methods for training handling
    def get_training_state(self) -> TrainingState:
        return self.training_state

    def init_training_state(self, max_mistakes: int = -1, max_mistakes_per_question: int = -1, allow_retry: bool = False) -> None:
        logger.debug(f"init_training_state max_mistakes:{max_mistakes} max_mistakes_per_question:{max_mistakes_per_question} allow_retry:{allow_retry}")
        self.training_state = TrainingState(max_mistakes=max_mistakes, max_mistakes_per_question=max_mistakes_per_question, allow_retry=allow_retry)

    # Methods for admin
    def get_total_working_time(self):
        raise NotImplementedError()

    def get_user_statistics(self):
        raise NotImplementedError()

    def get_suspicious_activity(self):
        raise NotImplementedError()

    def clear_all_annotations(self):
        '''Clears all annotations for this user'''
        self.instance_id_to_label_to_value.clear()
        self.instance_id_to_behavioral_data.clear()

    def reset_training_state(self):
        return NotImplementedError()