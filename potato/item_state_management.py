"""
Item State Management Module

This module provides the core data structures and management logic for annotation items
in the Potato platform. It handles item storage, assignment strategies, and tracking
of annotation progress across users.

The module includes:
- Item class: Represents individual annotation items with metadata
- Label class: Represents annotation labels with schema information
- SpanAnnotation class: Represents text span annotations with position data
- AssignmentStrategy enum: Defines different strategies for assigning items to users
- ItemStateManager: Main class for managing item state and assignments

The system supports multiple assignment strategies including random, fixed order,
active learning, and diversity-based assignment to optimize annotation efficiency.
"""

from __future__ import annotations

# Need to import UserState as a type hint for the ItemStateManager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from potato.user_state_management import UserState

from enum import Enum
from collections import OrderedDict, deque, defaultdict
import random
import logging
import threading

# Singleton item of the ItemStateManager with thread-safe lock
ITEM_STATE_MANAGER = None
_ITEM_STATE_MANAGER_LOCK = threading.Lock()


def init_item_state_manager(config: dict) -> ItemStateManager:
    """
    Initialize the singleton ItemStateManager item.

    This function creates the global ItemStateManager that will be shared
    across all users. It's designed to be called once during application startup.
    Thread-safe initialization using double-checked locking pattern.

    Args:
        config: Configuration dictionary containing item management settings

    Returns:
        ItemStateManager: The initialized singleton item

    Note:
        TODO: make the manager type configurable between in-memory and DB-backed.
        The DB back-end is for when we have a ton of data and don't want it sitting in
        memory all the time (or where some external process is going to be adding new items)
    """
    global ITEM_STATE_MANAGER

    # Double-checked locking for thread safety
    if ITEM_STATE_MANAGER is None:
        with _ITEM_STATE_MANAGER_LOCK:
            # Check again inside the lock
            if ITEM_STATE_MANAGER is None:
                ITEM_STATE_MANAGER = ItemStateManager(config)

    return ITEM_STATE_MANAGER


def clear_item_state_manager():
    """
    Clear the singleton item state manager item (for testing).

    This function is primarily used for testing purposes to reset the
    global state between test runs. Thread-safe.
    """
    global ITEM_STATE_MANAGER
    with _ITEM_STATE_MANAGER_LOCK:
        ITEM_STATE_MANAGER = None


def get_item_state_manager() -> ItemStateManager:
    """
    Get the singleton ItemStateManager item.

    Returns:
        ItemStateManager: The singleton item

    Raises:
        ValueError: If the manager has not been initialized

    Note:
        TODO: make the manager type configurable between in-memory and DB-backed.
        The DB back-end is for when we have a ton of data and don't want it sitting in
        memory all the time (or where some external process is going to be adding new items)
    """
    global ITEM_STATE_MANAGER

    if ITEM_STATE_MANAGER is None:
        raise ValueError("Item State Manager has not been initialized yet!")

    return ITEM_STATE_MANAGER


class Item:
    """
    A class for maintaining state on items that are being annotated.

    The state of the annotations themselves are stored in the UserState class.
    The item itself is largely immutable but can be updated with metadata.
    """

    def __init__(self, item_id, item_data):
        """
        Initialize an annotation item.

        Args:
            item_id: Unique identifier for this item
            item_data: Dictionary containing the item's data (text, context, etc.)
        """
        self.item_id = item_id
        self.item_data = item_data
        self.metadata = {}

        # This data structure keeps the label-based annotations the user has
        # completed so far
        self.labels = {}

        # This data structure keeps the span-based annotations the user has
        # completed so far
        self.span_annotations = {}

    def add_metadata(self, metadata_name: str, metadata_value: str):
        """Add metadata to this item"""
        self.metadata[metadata_name] = metadata_value

    def get_id(self):
        """Get the item's unique identifier"""
        return self.item_id

    def get_data(self):
        """Get the item's raw data dictionary"""
        return self.item_data

    def get_text(self):
        """
        Get the text content from the item data.

        This method intelligently extracts text from various data structures,
        trying common keys first, then falling back to string conversion.

        Returns:
            str: The text content for annotation
        """
        if isinstance(self.item_data, dict):
            # Try to get text from common keys
            for key in ['text', 'content', 'message', 'title']:
                if key in self.item_data:
                    return self.item_data[key]
            # If no text key found, return the first string value
            for value in self.item_data.values():
                if isinstance(value, str):
                    return value
        elif isinstance(self.item_data, str):
            return self.item_data
        return str(self.item_data)

    def get_displayed_text(self):
        """Get the displayed text (same as get_text for now)"""
        return self.get_text()

    def get_metadata(self, metadata_name: str):
        """Get metadata value by name"""
        return self.metadata.get(metadata_name, None)

    def __str__(self):
        return f"Item(id:{self.item_id}, data:{self.item_data}, metadata:{self.metadata})"


class Label:
    """
    A utility class for representing a single label in any annotation scheme.

    Labels may have a integer value (likert), a string value (text), or a boolean value (binary).
    Span annotations are represented with a different class.
    """

    def __init__(self, schema: str, name: str):
        """
        Initialize a label.

        Args:
            schema: The annotation scheme this label belongs to
            name: The label name/value
        """
        self.schema = schema
        self.name = name

    def get_schema(self):
        """Get the schema this label belongs to"""
        return self.schema

    def get_name(self):
        """Get the label name/value"""
        return self.name

    def __str__(self):
        return f"Label(schema:{self.schema}, name:{self.name})"

    def __eq__(self, other):
        """Check if two labels are equal"""
        return self.schema == other.schema and self.name == other.name

    def __hash__(self):
        """Generate hash for label (enables use in sets/dicts)"""
        return hash((self.schema, self.name))


class AssignmentStrategy(Enum):
    """
    Enumeration of strategies for assigning items to users.

    Different strategies optimize for different goals:
    - RANDOM: Maximizes diversity and reduces bias
    - FIXED_ORDER: Ensures consistent ordering across users
    - ACTIVE_LEARNING: Prioritizes items with high uncertainty
    - LLM_CONFIDENCE: Uses AI model confidence for prioritization
    - MAX_DIVERSITY: Prioritizes items with high disagreement
    - LEAST_ANNOTATED: Prioritizes items with fewest annotations
    - CATEGORY_BASED: Assigns items matching user's qualified categories
    - DIVERSITY_CLUSTERING: Samples items round-robin from embedding clusters
    """
    RANDOM = 'random'
    FIXED_ORDER = 'fixed_order'
    ACTIVE_LEARNING = 'active_learning'
    LLM_CONFIDENCE = 'llm_confidence'
    MAX_DIVERSITY = 'max_diversity'
    LEAST_ANNOTATED = 'least_annotated'
    CATEGORY_BASED = 'category_based'
    DIVERSITY_CLUSTERING = 'diversity_clustering'

    def fromstr(phase: str) -> AssignmentStrategy:
        """
        Convert a string representation to an AssignmentStrategy enum value.

        Args:
            phase: String representation of the strategy (case-insensitive)

        Returns:
            AssignmentStrategy: The corresponding enum value

        Raises:
            ValueError: If the string doesn't match any known strategy
        """
        phase = phase.lower()
        if phase == "random":
            return AssignmentStrategy.RANDOM
        elif phase == "fixed_order":
            return AssignmentStrategy.FIXED_ORDER
        elif phase == "active_learning":
            return AssignmentStrategy.ACTIVE_LEARNING
        elif phase == "llm_confidence":
            return AssignmentStrategy.LLM_CONFIDENCE
        elif phase == "max_diversity":
            return AssignmentStrategy.MAX_DIVERSITY
        elif phase == "least_annotated":
            return AssignmentStrategy.LEAST_ANNOTATED
        elif phase == "category_based":
            return AssignmentStrategy.CATEGORY_BASED
        elif phase == "diversity_clustering":
            return AssignmentStrategy.DIVERSITY_CLUSTERING
        else:
            raise ValueError(f"Unknown phase: {phase}")


class ItemStateManager:
    """
    A class for maintaining state on the ordering and metadata of items that are being annotated.

    This class aims to be a singleton that is shared across all users and provides the functionality
    of determining which item is next to be annotated.
    The state of the annotations themselves are stored in the UserState class.
    """

    def __init__(self, config: dict):
        """
        Initialize the item state manager.

        Args:
            config: Configuration dictionary containing item management settings
        """
        # Cache the config for later
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Thread-safe lock for concurrent access to item data
        self._lock = threading.RLock()

        # This data structure keeps the ordering of the items that are being annotated
        # and a mapping from item ID to the Item object
        self.item_id_to_item = OrderedDict()

        # Load max annotations per item from config
        self.max_annotations_per_item = config.get('max_annotations_per_item', -1)

        # Track which annotators have worked on each item
        self.item_annotators = defaultdict(set)

        # Queue of remaining items to be assigned
        self.remaining_item_ids = deque()

        # NOTE: We use an extra set to keep track of completed items to allow for
        # O(1) tests of whether an item needs to be removed from the remaining list
        self.completed_item_ids = set()

        # Initialize item annotation counts for tracking
        self.item_annotation_counts = defaultdict(int)

        # Load how we want to assign items to users
        if 'assignment_strategy' in config:
            strat = config['assignment_strategy']
            if isinstance(strat, str):
                self.assignment_strategy = AssignmentStrategy.fromstr(strat)
            elif isinstance(strat, dict):
                self.assignment_strategy = AssignmentStrategy.fromstr(strat['name'])
            else:
                raise ValueError("Invalid assignment_strategy in config")
        else:
            self.assignment_strategy = AssignmentStrategy.FIXED_ORDER

        # Set up random seed for assignment strategies
        self.random_seed = config.get('random_seed', 1234)
        self.random = random.Random(self.random_seed)
        self.logger.info(f"ItemStateManager initialized with random_seed={self.random_seed}")

    def has_item(self, item_id: str) -> bool:
        """Returns True if the item is in the state manager"""
        return item_id in self.item_id_to_item

    def add_item(self, item_id: str, item_data: dict):
        """
        Adds a new item to be annotated to the state manager (thread-safe).

        Args:
            item_id: Unique identifier for the item
            item_data: Dictionary containing the item's data

        Raises:
            ValueError: If an item with the same ID already exists
        """
        with self._lock:
            item = Item(item_id, item_data)
            if item_id in self.item_id_to_item:
                raise ValueError(f"Duplicate Item ID! Item with ID {item_id} already exists in the state manager")

            self.item_id_to_item[item_id] = item
            self.remaining_item_ids.append(item_id)

    def add_gold_item(self, gold_item_id: str, gold_item_data: dict):
        """
        Adds a new gold_item to be annotated to the state manager (thread-safe).

        Args:
            gold_item_id: Unique identifier for the gold_item
            gold_item_data: Dictionary containing the gold_item's data

        Raises:
            ValueError: If an gold_item with the same ID already exists
        """
        with self._lock:
            gold_item = Item(gold_item_id, gold_item_data)
            if gold_item_id in self.item_id_to_item:
                raise ValueError(f"Duplicate Gold Item ID! Gold Item with ID {gold_item_id} already exists in the state manager")

            self.item_id_to_item[gold_item_id] = gold_item
            # do not add to remaining_item_ids

    def add_items(self, items: dict[str, dict]):
        """
        Given a dictionary of item IDs to item data, add them to the state manager.

        Args:
            items: Dictionary mapping item IDs to item data dictionaries
        """
        for iid, item_data in items.items():
            self.add_item(iid, item_data)

    # =========================================================================
    # Assignment Methods
    # =========================================================================

    def assign_items_to_user(self, user_state: UserState, user_states: list(UserState)) -> int:
        """
        Assigns a set of items to a user based on the current state of the system
        and returns the number of items assigned.

        This method implements various assignment strategies to optimize annotation
        efficiency and quality. The strategy used depends on the configuration.

        If ICL verification is enabled with mix_with_regular_assignments, this method
        may include verification tasks from the ICL labeler's queue. These appear as
        regular annotation tasks (blind labeling) so users don't know they're verifying
        LLM predictions.

        Args:
            user_state: The user state object to assign items to

        Returns:
            int: Number of items assigned to the user

        Side Effects:
            - Updates user_state with new item assignments
            - Updates internal tracking of item assignments
            - May modify remaining_item_ids queue
        """
        #self.logger.debug("=== START item ASSIGNMENT ===")
        #self.logger.debug(f"Assigning items to user {user_state.user_id} (Session ID {user_state.session_id}) with strategy {self.assignment_strategy} and random_seed={self.random_seed}")

        # Decline to assign new items to users that have completed the maximum
        if not user_state.is_allowed_remaining_assignments():
            self.logger.debug(f"User {user_state.user_id} (Session ID {user_state.get_session_id()}) does not have remaining assignments.")
            return 0

        # Determine how many items to assign
        num_current_assignments = user_state.get_assigned_instances_count()
        max_assignments = user_state.get_max_assignments()

        if max_assignments > 0:
            remaining_capacity = max_assignments - num_current_assignments

            if remaining_capacity <= 0:
                return 0

            # For fixed_order strategy, assign all remaining capacity at once
            # For other strategies, use the original incremental logic
            if self.assignment_strategy == AssignmentStrategy.FIXED_ORDER:
                num_items_to_assign = remaining_capacity
            #elif self.assignment_strategy == AssignmentStrategy.RANDOM:
            #    num_items_to_assign = remaining_capacity
            else:
                # If user has less than 3 assignments, assign up to 3 more (or remaining capacity)
                if num_current_assignments < 3:
                    num_items_to_assign = min(3, remaining_capacity)
                else:
                    # Otherwise, assign one at a time
                    num_items_to_assign = 1
        else:
            # No maximum, assign one at a time
            num_items_to_assign = 1

        if self.assignment_strategy == AssignmentStrategy.RANDOM or self.assignment_strategy == AssignmentStrategy.FIXED_ORDER:
            # Random assignment strategy
            unlabeled_items = []
            for iid in self.remaining_item_ids:
                annotation_count = len(self.item_annotators[iid])
                # self.logger.debug(f"[ASSIGNMENT] Considering {iid}: annotation_count={annotation_count}, cap={self.max_annotations_per_item}")
                # Always skip items that have reached max annotations, but do not remove here
                if self.max_annotations_per_item >= 0 and annotation_count >= self.max_annotations_per_item:
                    if iid in self.remaining_item_ids:
                        self.remaining_item_ids.remove(iid)
                        self.logger.debug(f"[ASSIGNMENT] Skipping {iid}: reached annotation cap. Remove from remaining_item_ids")
                    continue

                already_labeled = False

                for iter_session_id, iter_user_state in user_states.items():
                    if iter_user_state.has_annotated(iid):
                        already_labeled = True
                        self.logger.debug(f"User {user_state.user_id} (Session ID {iter_session_id}) - Instance {iid} already annotated, skipping.")
                        break

                if not already_labeled:
                    unlabeled_items.append(iid)

            self.logger.debug(f"User {user_state.user_id} (Session ID {user_state.session_id}) - Number unlabeled items: {len(unlabeled_items)}")
            if not unlabeled_items:
                self.logger.info(f"User {user_state.user_id} (Session ID {user_state.session_id}) - No unlabeled items available")
                return 0

            if self.assignment_strategy == AssignmentStrategy.RANDOM:
                to_assign = self.random.sample(unlabeled_items, min(num_items_to_assign, len(unlabeled_items)))
            else:
                to_assign = unlabeled_items[:min(num_items_to_assign, len(unlabeled_items))]

            for item_id in to_assign:
                user_state.assign_instance(self.item_id_to_item[item_id])
            return len(to_assign)
        else:
            # Default fallback to fixed order
            self.logger.warning(f"Unknown assignment strategy: {self.assignment_strategy}, falling back to fixed order")
            self.assignment_strategy = AssignmentStrategy.FIXED_ORDER
            return self.assign_items_to_user(user_state, user_states)

    def get_item_ids(self) -> list[str]:
        """Get all item IDs in the manager"""
        return list(self.item_id_to_item.keys())

    def get_items(self) -> list[Item]:
        """Get all items in the manager"""
        return list(self.item_id_to_item.values())

    def get_item(self, item_id: str) -> Item:
        """Get an item by its ID"""
        return self.item_id_to_item[item_id]

    def get_annotators_for_item(self, item_id: str) -> set[str]:
        """Get the set of annotators who have worked on this item"""
        return self.item_annotators[item_id]

    def get_total_assignable_items_for_user(self, user_states: list(UserState)) -> int:
        """
        Get the total number of items that can be assigned to a user.

        This takes into account:
        - Items the user hasn't already annotated
        - Items that haven't reached their annotation limit
        - Items that are still available for assignment

        Args:
            user_state: The user state to check assignments for

        Returns:
            int: Number of items that can be assigned
        """
        #self.logger.debug("=== GET TOTAL ASSIGNABLE ITEMS FOR USER ===")

        if len(user_states) == 0:
            return 0

        count = 0
        for iid in self.remaining_item_ids:
            # self.logger.debug(f"count: {count}")

            # Check if item has reached annotation limit
            annotation_count = len(self.item_annotators[iid])
            if 0 <= self.max_annotations_per_item <= annotation_count:
                self.remaining_item_ids.remove(iid)
                #self.logger.debug(f"Item {iid} reached annotation limit")
                continue

            # Check if user has already annotated this item
            already_labeled = False

            for iter_session_id, iter_user_state in user_states.items():
                if iter_user_state.has_annotated(iid):
                    already_labeled = True
                    #self.logger.debug(f"User {iter_user_state.user_id} (Session ID {iter_session_id}) already annotated {iid}, skipping.")
                    break

            if not already_labeled:
                count += 1

        #self.logger.debug(f"Number of items that could still be assigned to User {iter_user_state.user_id}: {count}")
        return count

    def register_annotator(self, item_id: str, user_id: str):
        """
        Register that a user has annotated an item.

        This method updates the tracking of which users have worked on which
        items, and may trigger cleanup of completed items.

        Args:
            item_id: The ID of the item that was annotated
            user_id: The ID of the user who did the annotation

        Side Effects:
            - Updates item_annotators tracking
            - May remove items from remaining_item_ids if they reach limits
            - Updates item_annotation_counts
        """
        # Add user to the set of annotators for this item
        self.item_annotators[item_id].add(user_id)

        # Update annotation count
        self.item_annotation_counts[item_id] += 1

        # Check if this item has reached its annotation limit
        if self.max_annotations_per_item >= 0 and len(self.item_annotators[item_id]) >= self.max_annotations_per_item:
            # Remove from remaining items if it's there
            if item_id in self.remaining_item_ids:
                self.remaining_item_ids.remove(item_id)
            # Mark as completed
            self.completed_item_ids.add(item_id)

    def clear(self):
        """Clear all data from the manager (for testing)"""
        self.item_id_to_item.clear()
        self.remaining_item_ids.clear()
        self.completed_item_ids.clear()
        self.item_annotators.clear()
        self.item_annotation_counts.clear()


# Singleton item of the ItemStateManager with thread-safe lock
TRAINING_ITEM_STATE_MANAGER = None
_TRAINING_ITEM_STATE_MANAGER_LOCK = threading.Lock()


def init_training_item_state_manager(config: dict) -> TRAINING_ITEM_STATE_MANAGER:
    global TRAINING_ITEM_STATE_MANAGER

    # Double-checked locking for thread safety
    if TRAINING_ITEM_STATE_MANAGER is None:
        with _TRAINING_ITEM_STATE_MANAGER_LOCK:
            # Check again inside the lock
            if TRAINING_ITEM_STATE_MANAGER is None:
                TRAINING_ITEM_STATE_MANAGER = TrainingItemStateManager(config)

    return TRAINING_ITEM_STATE_MANAGER


def clear_training_item_state_manager():
    global TRAINING_ITEM_STATE_MANAGER
    with _TRAINING_ITEM_STATE_MANAGER_LOCK:
        TRAINING_ITEM_STATE_MANAGER = None


def get_training_item_state_manager() -> TRAINING_ITEM_STATE_MANAGER:
    global TRAINING_ITEM_STATE_MANAGER

    if TRAINING_ITEM_STATE_MANAGER is None:
        raise ValueError("Training Item State Manager has not been initialized yet!")

    return TRAINING_ITEM_STATE_MANAGER


class TrainingItemStateManager:
    """
    A class for maintaining state on the metadata of training training_items that are being annotated.

    This class aims to be a singleton that is shared across all users and provides the functionality
    of determining which training training_item is next to be annotated.
    The state of the annotations themselves are stored in the UserState class.
    """

    def __init__(self, config: dict):
        """
        Initialize the training training_item state manager.

        Args:
            config: Configuration dictionary containing training_item management settings
        """
        # Cache the config for later
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Thread-safe lock for concurrent access to training training_item data
        self._lock = threading.RLock()

        # This data structure keeps the ordering of the training_items that are being annotated
        # and a mapping from training_item ID to the Item object
        self.training_item_id_to_training_item = OrderedDict()

        # Track which annotators have worked on each training_item
        self.training_item_annotators = defaultdict(set)

        self.logger.info(f"TrainingItemStateManager initialized")

    def has_training_item(self, training_item_id: str) -> bool:
        """Returns True if the training_item is in the state manager"""
        return training_item_id in self.training_item_id_to_training_item

    def add_training_item(self, training_item_id: str, training_item_data: dict):
        """
        Adds a new training_item to be annotated to the state manager (thread-safe).

        Args:
            training_item_id: Unique identifier for the training_item
            training_item_data: Dictionary containing the training_item's data

        Raises:
            ValueError: If an training_item with the same ID already exists
        """
        with self._lock:
            training_item = Item(training_item_id, training_item_data)
            if training_item_id in self.training_item_id_to_training_item:
                raise ValueError(f"Duplicate Item ID! Item with ID {training_item_id} already exists in the state manager")

            self.training_item_id_to_training_item[training_item_id] = training_item

    def add_training_items(self, training_items: dict[str, dict]):
        """
        Given a dictionary of training_item IDs to training_item data, add them to the state manager.

        Args:
            training_items: Dictionary mapping training_item IDs to training_item data dictionaries
        """
        for iid, training_item_data in training_items.training_items():
            self.add_training_item(iid, training_item_data)

    # =========================================================================
    # Assignment Methods
    # =========================================================================

    def assign_training_items_to_user(self, user_state: UserState) -> int:
        """
        Assigns a set of training_items to a user based on the current state of the system
        and returns the number of training_items assigned.

        This method implements various assignment strategies to optimize annotation
        efficiency and quality. The strategy used depends on the configuration.

        If ICL verification is enabled with mix_with_regular_assignments, this method
        may include verification tasks from the ICL labeler's queue. These appear as
        regular annotation tasks (blind labeling) so users don't know they're verifying
        LLM predictions.

        Args:
            user_state: The user state object to assign training_items to

        Returns:
            int: Number of training_items assigned to the user

        Side Effects:
            - Updates user_state with new training_item assignments
            - Updates internal tracking of training_item assignments
            - May modify remaining_training_item_ids queue
        """
        self.logger.debug("=== START training_item ASSIGNMENT ===")
        training_state = user_state.get_training_state()
        training_state.set_training_instances(list(self.training_item_id_to_training_item.keys()))
        self.logger.debug("=== FINISH training_item ASSIGNMENT ===")

    def get_training_item_ids(self) -> list[str]:
        """Get all training_item IDs in the manager"""
        return list(self.training_item_id_to_training_item.keys())

    def get_training_items(self) -> list[Item]:
        """Get all training_items in the manager"""
        return list(self.training_item_id_to_training_item.values())

    def get_training_item(self, training_item_id: str) -> Item:
        """Get an training_item by its ID"""
        return self.training_item_id_to_training_item[training_item_id]

    def get_annotators_for_training_item(self, training_item_id: str) -> set[str]:
        """Get the set of annotators who have worked on this training_item"""
        return self.training_item_annotators[training_item_id]

    def register_annotator(self, training_item_id: str, user_id: str):
        # Add user to the set of annotators for this training_item
        self.training_item_annotators[training_item_id].add(user_id)

    def clear(self):
        """Clear all data from the manager (for testing)"""
        self.training_item_id_to_training_item.clear()
        self.remaining_training_item_ids.clear()
        self.completed_training_item_ids.clear()
        self.training_item_annotators.clear()
        self.training_item_annotation_counts.clear()
