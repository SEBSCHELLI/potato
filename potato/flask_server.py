"""
Flask Server Driver

This module provides the main Flask server implementation for the annotation platform.
Features include:
- User authentication and session management
- Annotation state tracking
- Multi-phase annotation workflow
- Survey flow support
- Data loading and persistence
- AI augmentation support
- Active learning integration
- Admin dashboard functionality

The server handles:
1. Data loading from various file formats (JSON, CSV, TSV, JSONL)
2. User session management and authentication
3. Annotation submission and validation
4. Phase progression and workflow management
5. AI hint generation and integration
6. Active learning model training and instance reordering
7. Admin dashboard data generation
8. Configuration management and validation

Key Components:
- Flask application setup and configuration
- Data loading and preprocessing
- User state initialization
- Annotation scheme processing
- Template rendering and customization
- Session timeout management
- Error handling and logging
"""
from __future__ import annotations
from dataclasses import dataclass

import logging

logging.getLogger("numba").setLevel(logging.WARNING)

import os
import sys
import random
import json
from collections import deque, defaultdict
import yaml
from datetime import timedelta

import pandas as pd

from flask import Flask

# Get current working directory and program directory
cur_working_dir = os.getcwd() #get the current working dir
#cur_working_dir = "/home/schellsn/author2paper_potato_multi_session"
cur_program_dir = os.path.dirname(os.path.abspath(__file__)) #get the current program dir (for the case of pypi, it will be the path where potato is installed)
#cur_program_dir = "/opt/anaconda3/envs/potato_env/lib/python3.10/site-packages/potato"
flask_templates_dir = os.path.join(cur_program_dir,'templates') #get the dir where the flask templates are saved
base_html_dir = os.path.join(cur_program_dir,'base_htmls') #get the dir where the the base_html templates files are saved

#insert the current program dir into sys path
sys.path.insert(0, cur_program_dir)

from potato.item_state_management import get_item_state_manager, init_item_state_manager, get_training_item_state_manager, init_training_item_state_manager
from potato.user_state_management import get_user_state_manager, init_user_state_manager
from potato.authentication import UserAuthenticator
from potato.phase import UserPhase
from potato.quality_control import init_quality_control_manager, get_quality_control_manager

from potato.server_utils.arg_utils import arguments
from potato.server_utils.config_module import init_config, config
from potato.server_utils.cli_utlis import get_project_from_hub, show_project_hub
from potato.server_utils.prolific_apis import ProlificStudy

# Initialize Flask app
app = Flask(__name__)

# Secret key will be set in configure_app() from config

# Use centralized logging configuration
from potato.logging_config import get_logger, setup_logging
logger = get_logger(__name__)

# Set random seed for reproducible behavior
random.seed(0)

# Global variables for file management and user tracking
domain_file_path = ""
file_list = []
file_list_size = 0
default_port = 8000
user_dict = {}

file_to_read_from = ""

# User story position tracking and response queue management
user_story_pos = defaultdict(lambda: 0, dict())
user_response_dicts_queue = defaultdict(deque)

# path to save user information
USER_CONFIG_PATH = "user_config.json"
DEFAULT_LABELS_PER_INSTANCE = 3


def load_instance_data(config: dict):
    """
    Load instance data from the files specified in the config.

    This function reads annotation data from various file formats (JSON, CSV, TSV, JSONL)
    and populates the ItemStateManager with the data. It handles different data structures
    and validates that required fields are present.

    Supports multiple data loading modes:
    1. data_files: List of local file paths (traditional mode)
    2. data_sources: Extended sources including URLs, cloud storage, databases
    3. data_directory: Watch a directory for files (handled separately)

    Args:
        config: Configuration dictionary containing data file paths and item properties

    Side Effects:
        - Populates ItemStateManager with loaded data
        - Validates data structure and required fields
        - Logs loading progress and statistics

    Raises:
        Exception: If file format is unsupported or required fields are missing
    """
    logger.info("=== LOAD ANNOTATION DATA STARTS ===")
    ism = get_item_state_manager()

    # Where to look in the JSON item object for the text to annotate
    text_key = config["item_properties"]["text_key"]
    id_key = config["item_properties"]["id_key"]

    data_files = config.get("data_files", [])
    if not data_files:
        # No data_files, might use data_directory which is handled elsewhere
        logger.debug("No data_files configured, skipping file-based loading")
        return

    logger.info("Loading data from %d files" % (len(data_files)))

    for data_file_entry in data_files:
        # Support both string paths and dict configs
        if isinstance(data_file_entry, dict):
            data_fname = data_file_entry.get("path")
        else:
            data_fname = data_file_entry

        if not data_fname:
            logger.warning(f"Skipping data_files entry with no path: {data_file_entry}")
            continue
        fmt = data_fname.split(".")[-1]
        if fmt not in ["csv", "tsv", "json", "jsonl"]:
            raise Exception("Unsupported input file format %s for %s" % (fmt, data_fname))

        logger.debug("Reading data from " + data_fname)

        if fmt in ["json", "jsonl"]:
            # Handle JSON and JSONL formats
            # Try parsing as a JSON array first, fall back to JSON Lines
            with open(data_fname, "rt") as f:
                raw = f.read()

            items = None
            if fmt == "json":
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        items = parsed
                        logger.debug(f"Parsed {data_fname} as JSON array with {len(items)} items")
                except json.JSONDecodeError:
                    pass  # Fall through to JSON Lines parsing

            if items is None:
                # Parse as JSON Lines (one JSON object per line)
                items = []
                for line_no, line in enumerate(raw.splitlines()):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        raise ValueError(
                            f"Invalid JSON at line {line_no+1} in {data_fname}: {e}"
                        ) from e

            for item_no, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValueError(f"Expected JSON object at item {item_no+1} in {data_fname}, got {type(item).__name__}")

                # Validate that the ID key exists in the item
                if id_key not in item:
                    raise KeyError(f"ID key '{id_key}' not found in item {item_no+1}")

                instance_id = str(item[id_key]) # Ensure ID is string

                # Check for duplicate IDs
                if ism.has_item(instance_id):
                    raise ValueError(f"Duplicate instance ID '{instance_id}' found at item {item_no+1}")

                # Validate text key exists if required
                if text_key not in item:
                    logger.warning(f"Text key '{text_key}' not found in item with ID '{instance_id}'")

                ism.add_item(instance_id, item)

            line_no = len(items)
        else:
            sep = "," if fmt == "csv" else "\t"

            # Validate required columns exist
            df = pd.read_csv(data_fname, sep=sep)
            if id_key not in df.columns:
                raise KeyError(f"ID column '{id_key}' not found in file {data_fname}")
            if text_key not in df.columns:
                logger.warning(f"Text column '{text_key}' not found in file {data_fname}")

            # Convert ID column to string to ensure consistent typing
            df[id_key] = df[id_key].astype(str)

            # Check for duplicate IDs in the dataframe
            if df[id_key].duplicated().any():
                dupes = df[id_key][df[id_key].duplicated()].tolist()
                raise ValueError(f"Duplicate instance IDs found in {data_fname}: {dupes}")

            # Check for duplicate IDs with existing items
            existing_dupes = [id for id in df[id_key] if ism.has_item(id)]
            if existing_dupes:
                raise ValueError(f"Instance IDs in {data_fname} conflict with existing IDs: {existing_dupes}")

            # Load data with proper type conversion
            df = df.astype({id_key: str})
            if text_key in df.columns:
                df = df.astype({text_key: str})

            # Convert to list of dicts for filtering
            items = df.to_dict('records')

            # Add items to state manager
            for item in items:
                instance_id = item[id_key]
                ism.add_item(instance_id, item)

            line_no = len(items)

        # If the admin didn't specify a subset, have the user annotate all instances
        max_annotations_per_user = config.get("max_annotations_per_user", len(ism.get_item_ids()))
        get_user_state_manager().set_max_annotations_per_user(max_annotations_per_user)

        logger.info("Loaded %d instances from %s" % (line_no, data_fname))

    # For each item, render the text to display in the UI ahead of time.
    _render_displayed_text(text_key)

    logger.info("=== LOAD ANNOTATION DATA ENDS ===")


def _render_displayed_text(text_key: str) -> None:
    """
    Render the displayed text for all items.

    This processes the text_key field to generate the displayed_text
    that will be shown in the annotation UI.

    Args:
        text_key: The key in item data containing the text to display
    """
    for item in get_item_state_manager().get_items():
        item_data = item.get_data()

        # Validate text key exists before rendering
        if text_key in item_data:
            item_data["displayed_text"] = get_displayed_text(item_data[text_key])
        else:
            item_data["displayed_text"] = ""
            logger.warning(f"No text found for item {item.get_id()}, using empty string")


def load_user_data(config: dict):
    logger.info("=== LOAD USER DATA STARTS ===")
    user_data_dir = config['output_annotation_dir']
    usm = get_user_state_manager()

    # Check if the output directory exists
    if not os.path.exists(user_data_dir):
        os.makedirs(user_data_dir)
        logger.info("Created output directory: %s" % user_data_dir)
        return

    # For each user's directory, load in their state
    user_dirs = [d for d in os.listdir(user_data_dir) if os.path.isdir(os.path.join(user_data_dir, d))]
    user_dirs = [d for d in user_dirs if d != "REMOVED"]

    logger.info(f"Load user data for the following users: {user_dirs}")
    for user_dir in user_dirs:
        for fn in os.listdir(f"{user_data_dir}{user_dir}"):
            session_id = fn.split("_")[0]
            try:
                usm.load_user_state(user_dir, session_id)
            except ValueError as e:
                # Skip directories that don't have valid user state files
                logger.warning("Skipping invalid user directory %s: %s" % (user_dir, str(e)))
                continue

    # Rebuild instance_annotators from loaded user state so that
    # adjudication build_queue() (and other code that relies on
    # ism.instance_annotators) works with pre-loaded annotation data.
    ism = get_item_state_manager()
    logger.info(f"Number of items {len(ism.remaining_item_ids)}")
    #logger.info(f"item_annotation_counts[item_id]: {ism.item_annotation_counts['165']}")
    for user_id, session_ids in usm.get_user_session_ids().items():
        for session_id in session_ids:
            user_state = usm.get_user_state(user_id, session_id)
            if user_state:
                for instance_id in user_state.instance_id_to_label_to_value:
                    if instance_id in ism.item_id_to_item:
                        ism.register_annotator(instance_id, user_id)
                        #if str(instance_id) == "165":
                        #    logger.info(f"165: {user_id}")
                        #    logger.info(f"item_annotation_counts[item_id]: {ism.item_annotation_counts['165']}")

    #logger.info(f"item_annotation_counts[item_id]: {ism.item_annotation_counts['165']}")
    logger.info("Loaded user data for %d users" % len(usm.get_user_ids()))
    logger.info(f"Number of items remaining {len(ism.remaining_item_ids)}")

    tism = get_training_item_state_manager()
    for user_id, session_ids in usm.get_user_session_ids().items():
        for session_id in session_ids:
            user_state = usm.get_user_state(user_id, session_id)
            if user_state:
                training_state = user_state.get_training_state()
                if training_state:
                    for instance_id in training_state.training_instance_id_to_label_to_value:
                        if instance_id in tism.training_item_id_to_training_item:
                            tism.register_annotator(instance_id, user_id)

    logger.info("Loaded user data for %d users" % len(usm.get_user_ids()))

    logger.info("=== LOAD USER DATA ENDS ===")


def load_training_data(config: dict) -> None:
    """
    Load training data from the training data file specified in the config.

    This function loads training instances with correct answers and explanations
    for the training phase. It validates the training data format and stores
    the training instances for use during the training phase.

    Args:
        config: Configuration dictionary containing training settings

    Side Effects:
        - Stores training instances in global training data storage
        - Validates training data format and consistency
        - Logs loading progress and statistics

    Raises:
        Exception: If training data file is not found or invalid
    """
    logger.info("=== LOAD TRAINING DATA STARTS ===")

    if 'training' not in config or not config['training'].get('enabled', False):
        logger.debug("Training not enabled, skipping training data loading")
        return

    training_config = config['training']
    data_file = training_config.get('data_file')

    if not data_file:
        logger.warning("Training enabled but no data_file specified")
        return

    # Resolve the training data file path
    try:
        training_data_path = get_abs_or_rel_path(data_file, config)
    except FileNotFoundError:
        logger.error(f"Training data file not found: {data_file}")
        raise Exception(f"Training data file not found: {data_file}")

    logger.info(f"Loading training data from {training_data_path}")

    try:
        with open(training_data_path, 'r', encoding='utf-8') as f:
            training_data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Invalid training data file format: {e}")
        raise Exception(f"Invalid training data file format: {e}")

    init_training_item_state_manager(config)
    tism = get_training_item_state_manager()

    if not isinstance(training_data, dict):
        raise Exception("Training data must be a JSON object")

    if 'training_instances' not in training_data:
        raise Exception("Training data must contain 'training_instances' field")

    training_instances = training_data['training_instances']
    if not isinstance(training_instances, list):
        raise Exception("training_instances must be a list")

    if not training_instances:
        raise Exception("training_instances cannot be empty")

    # Validate training data against annotation schemes
    annotation_schemes = training_config.get('annotation_schemes', config.get('annotation_schemes', []))

    # Handle both string references and full scheme dictionaries
    scheme_names = set()
    for scheme in annotation_schemes:
        if isinstance(scheme, str):
            # String reference to existing scheme
            scheme_names.add(scheme)
        elif isinstance(scheme, dict) and 'name' in scheme:
            # Full scheme dictionary
            scheme_names.add(scheme['name'])
        else:
            logger.warning(f"Invalid annotation scheme format: {scheme}")

    for instance in training_instances:
        # Validate required fields
        if 'id' not in instance or 'text' not in instance or 'correct_answers' not in instance:
            raise Exception(f"Training instance missing required fields: {instance}")

        # Validate correct_answers correspond to annotation schemes
        for scheme_name in instance['correct_answers'].keys():
            if scheme_name not in scheme_names:
                logger.warning(f"Training instance {instance['id']} contains unknown scheme: {scheme_name}")

        # Create Item object for training instance
        item_data = {
            'id': instance['id'],
            'text': instance['text'],
            'paper_title': instance['paper_title'],
            'paper_abstract': instance['paper_abstract'],
            'correct_answers': instance['correct_answers'],
            'explanation': instance.get('explanation', ''),
            'displayed_text': get_displayed_text(instance['text'])
        }

        tism.add_training_item(instance['id'], item_data)

    logger.info(f"Loaded {len(tism.get_training_item_ids())} training instances")
    #logger.debug(f"Training instances: {[i for i in tism.get_training_item_ids()]}")
    logger.info("=== LOAD TRAINING DATA ENDS ===")


def init_prolific_study(config: dict) -> None:
    """
    Initialize the Prolific study instance from config.

    This function reads the Prolific configuration and initializes the
    ProlificStudy API wrapper for tracking participants and managing
    study status.

    Args:
        config: The application configuration dictionary

    Side Effects:
        - Sets global PROLIFIC_STUDY_INSTANCE
        - May start workload checker thread
    """
    logger.info("=== INIT PROLIFIC STUDY STARTS ===")

    prolific_config = config.get('prolific', {})
    if not prolific_config:
        logger.error("No Prolific configuration found")
        return

    # Check for config file path
    config_file_path = prolific_config.get('config_file_path')
    if config_file_path:
        # Load Prolific config from file
        import yaml
        prolific_config_path = get_abs_or_rel_path(config_file_path, config)
        if os.path.exists(prolific_config_path):
            with open(prolific_config_path, 'r') as f:
                prolific_settings = yaml.safe_load(f)
                logger.info(f"Loaded Prolific config from {prolific_config_path}")
        else:
            logger.warning(f"Prolific config file not found: {prolific_config_path}")
            return
    else:
        # Use inline config
        prolific_settings = prolific_config

    # Validate required fields
    token = prolific_settings.get('token')
    study_id = prolific_settings.get('study_id')

    if not token or not study_id:
        logger.warning("Prolific config missing 'token' or 'study_id'")
        return

    # Get optional settings
    max_concurrent_sessions = prolific_settings.get('max_concurrent_sessions', 30)
    workload_checker_period = prolific_settings.get('workload_checker_period', 60)

    # Get saving directory for submission data
    saving_dir = config.get('output_annotation_dir', 'annotation_output')

    try:
        prolific_study_instance = ProlificStudy(
            token=token,
            study_id=study_id,
            saving_dir=saving_dir,
            max_concurrent_sessions=max_concurrent_sessions,
            workload_checker_period=workload_checker_period
        )

        logger.info(f"Initialized Prolific study: {study_id}")

        keys = ['id', 'name', 'internal_name', 'reward', 'average_reward_per_hour', 'external_study_url', 'status', 'total_available_places', 'places_taken']
        for k in keys:
            logger.info(f"prolific_study_instance[{k}]: {prolific_study_instance.study_info.get(k, 'UNK')}")
        
        app.prolific_study_instance = prolific_study_instance
        logger.info(f"app.prolific_study_instance Study info: {app.prolific_study_instance.get_basic_study_info()}")

    except Exception as e:
        logger.error(f"Failed to initialize Prolific study: {e}")
        app.prolific_study_instance = None

    logger.info("=== INIT PROLIFIC STUDY ENDS ===")


def load_all_data(config: dict):
    '''Loads instance and annotation data from the files specified in the config.'''
    load_phase_data(config)
    load_instance_data(config)
    load_training_data(config)
    load_user_data(config)


def load_phase_data(config: dict) -> None:
    # Lazy import - only when this function is called
    logger.info(f"=== LOAD PHASE DATA STARTS ===")

    from potato.server_utils.front_end import generate_html_from_schematic, generate_training_html, generate_static_html

    if "phases" not in config or not config["phases"]:
        return

    #logger.debug(f"config: {config}")

    phases = config["phases"]

    # Handle both list and dictionary formats for phases
    if isinstance(phases, list):
        # If phases is a list, use the order as defined in the list
        phase_order = [phase["name"] for phase in phases]
        # Convert list to dict for easier access
        phases_dict = {phase["name"]: phase for phase in phases}
    else:
        # Original dictionary format
        if "order" in phases:
            phase_order = phases["order"]
        else:
            phase_order = [k for k in phases.keys() if k != "order"]
        phases_dict = phases

    #logger.debug(f"[PHASE LOAD] phases: {phases}")
    #logger.debug(f"[PHASE LOAD] phase_order: {phase_order}")

    #logger.debug(f"phases_dict: {phases_dict}")

    #logger.debug("Loading %d phases in order: %s" % (len(phase_order), phase_order))

    for phase_name in phase_order:
        try:
            #logger.debug(f"PHASE: {phase_name}")
            phase = phases_dict[phase_name]

            # Handle new format with annotation_schemes directly in phase
            if "annotation_schemes" in phase:
                phase_labeling_schemes = phase["annotation_schemes"]
                # Determine phase type by checking all annotation schemes
                if phase_labeling_schemes:
                    display_only_count = sum(
                        1 for s in phase_labeling_schemes
                        if s.get("annotation_type") == "pure_display"
                    )
                    interactive_count = len(phase_labeling_schemes) - display_only_count

                    if display_only_count > 0 and interactive_count > 0:
                        logger.warning(
                            f"Phase '{phase_name}' has mixed scheme types: "
                            f"{display_only_count} display-only and {interactive_count} interactive. "
                            f"Treating as ANNOTATION phase."
                        )
                        phase_type = UserPhase.ANNOTATION
                    elif display_only_count == len(phase_labeling_schemes):
                        phase_type = UserPhase.INSTRUCTIONS
                    else:
                        phase_type = UserPhase.ANNOTATION
                else:
                    phase_type = UserPhase.ANNOTATION
            else:
                # Legacy format with file and type
                if not "type" in phase or not phase['type']:
                    logger.error(f"Phase {phase_name} does not have a type")
                    raise Exception("Phase %s does not have a type" % phase_name)

                phase_type = UserPhase.fromstr(phase['type'])

                # Training and annotation phases can work without a file
                # They use the main annotation schemes from the config
                if phase_type in [UserPhase.TRAINING, UserPhase.ANNOTATION]:
                    if "file" not in phase or not phase['file']:
                        # Use the main annotation schemes for training/annotation
                        phase_labeling_schemes = config.get('annotation_schemes', [])
                        #logger.debug(f"Phase {phase_name} using main annotation schemes: {phase_labeling_schemes}")
                    else:
                        # Use the file if specified
                        phase_scheme_fname = get_abs_or_rel_path(phase['file'], config)
                        #logger.debug(f"Resolved phase file for {phase_name}: {phase_scheme_fname}")
                        phase_labeling_schemes = get_phase_annotation_schemes(phase_scheme_fname)
                elif phase_type == UserPhase.INSTRUCTIONS:
                    phase_html_fname = phase['file']
                else:
                    # Other phases (prestudy, poststudy, etc.)
                    # Support instrument/instruments keys for standard survey instruments
                    phase_labeling_schemes = []

                    # Handle single instrument reference
                    if "instrument" in phase:
                        from potato.survey_instruments import get_instrument_questions
                        inst_id = phase["instrument"]
                        logger.debug(f"Phase {phase_name} loading instrument: {inst_id}")
                        phase_labeling_schemes = get_instrument_questions(inst_id)

                    # Handle multiple instruments
                    elif "instruments" in phase:
                        from potato.survey_instruments import get_instrument_questions
                        for inst_id in phase["instruments"]:
                            logger.debug(f"Phase {phase_name} loading instrument: {inst_id}")
                            phase_labeling_schemes.extend(get_instrument_questions(inst_id))

                    # Handle file reference (can be combined with instrument)
                    if "file" in phase and phase['file']:
                        phase_scheme_fname = get_abs_or_rel_path(phase['file'], config)
                        logger.debug(f"Resolved phase file for {phase_name}: {phase_scheme_fname}")
                        file_schemes = get_phase_annotation_schemes(phase_scheme_fname)
                        if phase_labeling_schemes:
                            # Append file schemes after instrument schemes
                            phase_labeling_schemes.extend(file_schemes)
                        else:
                            phase_labeling_schemes = file_schemes

                    # Require at least one source of questions
                    if not phase_labeling_schemes:
                        logger.error(f"Phase {phase_name} requires 'instrument', 'instruments', or 'file'")
                        raise Exception(
                            f"Phase {phase_name} requires 'instrument', 'instruments', or 'file' "
                            "to specify its annotation schemes"
                        )

            # Use the default templates unless specified in the phase config
            # Note: Template paths are now hardcoded in front_end.py
            # Only handle custom task_layout if specified
            task_layout_file = None
            if 'task_layout' in phase:
                #logger.debug(f"task_layout in phase: {phase['task_layout']}")
                task_layout_file = phase['task_layout']

            try:
                phase_type = UserPhase.fromstr(phase['type'])
                if phase_type == UserPhase.TRAINING:
                    phase_html_fname = generate_training_html(phase_name, config, task_layout_file)
                elif phase_type == UserPhase.INSTRUCTIONS:
                    phase_html_fname = generate_static_html(phase_html_fname, phase_name, config)
                else:
                    phase_html_fname = generate_html_from_schematic(
                                                    phase_labeling_schemes,
                                                    False, False,
                                                    phase_name, config,
                                                    task_layout_file)

                #logger.debug(f"phase_html_fname: {phase_html_fname}")
            except KeyError as e:
                logger.error(f"Error generating HTML for phase {phase_name}: {e}")
                raise Exception("Error generating HTML for phase %s: %s" \
                                % (phase_name, str(e)))

            # Register the HTML so it's easy to find later
            user_state_manager = get_user_state_manager()
            user_state_manager.add_phase(phase_type, phase_name, phase_html_fname)
            #logger.debug(f"Registered phase {phase_name} as {phase_type} with HTML {phase_html_fname}")

        except Exception as e:
            logger.error(f"Failed to load phase '{phase_name}': {e}")
            logger.error(e, exc_info=True)
            continue

    user_state_manager = get_user_state_manager()
    logger.info(f"[PHASE LOAD] phase_type_to_name_to_page: {user_state_manager.phase_type_to_name_to_page}")
    logger.info(f"=== LOAD PHASE DATA ENDS ===")


def get_phase_annotation_schemes(filename: str) -> list[dict]:
    '''Returns the annotation schemes for a phase from a file.'''

    schemes = []
    if not os.path.exists(filename):
        raise Exception("Phase labeling schemes file %s does not exist" % filename)

    if filename.endswith(".json"):
        with open(filename, "rt") as f:
            schemes = json.load(f)
        # Allow users to have specified a single scheme in the JSON file
        if type(schemes) != list:
            schemes = [schemes]
    elif filename.endswith(".jsonl"):
        with open(filename, 'rt') as f:
            for line_no, line in enumerate(f):
                line = line.strip()
                if not line:  # Skip empty lines
                    continue
                try:
                    schemes.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"Invalid JSON at line {line_no+1} in {filename}: {e}"
                    ) from e
    elif filename.endswith(".yaml") or filename.endswith(".yml"):
        with open(filename, 'rt') as f:
            schemes = yaml.safe_load(f)
    else:
        raise Exception("Unknown file format for phase labeling schemes file %s" % filename)
    return schemes


def get_abs_or_rel_path(fname: str, config: dict) -> str:
    """
    Returns the path to the fname if it exists as specified, or if not, attempts to find
    the file in the relative paths from the config file.
    """
    import os
    #logger = globals().get('logger', None)
    #if logger:
    #    logger.debug(f"get_abs_or_rel_path: input fname={fname}")
    if os.path.exists(fname):
        #if logger:
        #    logger.debug(f"get_abs_or_rel_path: found file at {fname}")
        return fname

    # See if we can find the file in the same directory as the config file
    dname = os.path.dirname(config["__config_file__"]) if "__config_file__" in config else os.getcwd()
    rel_path = os.path.join(dname, fname)
    #if logger:
    #    logger.debug(f"get_abs_or_rel_path: trying {rel_path}")
    if os.path.exists(rel_path):
        #if logger:
        #    logger.debug(f"get_abs_or_rel_path: found file at {rel_path}")
        return rel_path

    # See if we can locate the file in the current working directory
    cwd = os.getcwd()
    rel_path = os.path.join(cwd, fname)
    if logger:
        logger.debug(f"get_abs_or_rel_path: trying {rel_path}")
    if os.path.exists(rel_path):
        if logger:
            logger.debug(f"get_abs_or_rel_path: found file at {rel_path}")
        return rel_path

    # See if we can figure it out from the real path directory
    real_path = os.path.abspath(dname)
    dir_path = os.path.dirname(real_path)
    fname2 = os.path.join(dir_path, fname)
    if logger:
        logger.debug(f"get_abs_or_rel_path: trying {fname2}")
    if not os.path.exists(fname2):
        if logger:
            logger.error(f"File not found: {fname2}")
        raise FileNotFoundError("File not found: %s" % fname2)
    return fname2


def get_displayed_text(text):
    """Render the text to display to the user in the annotation interface.

    Handles both string and list inputs. When text is a list (for dialogue
    or pairwise comparisons), it formats the list items according to list_as_text config.

    Supported prefix types:
    - alphabet: A. B. C. prefixes
    - number: 1. 2. 3. prefixes
    - bullet: • prefixes
    - none: No prefix (use for dialogue with speaker names in text)

    Additional options:
    - horizontal: Display items side-by-side (for pairwise comparison)
    - alternating_shading: Shade every other turn (for dialogue readability)
    """
    import re

    # Handle list inputs (for dialogue or pairwise comparisons with list_as_text config)
    if isinstance(text, list):
        list_config = config.get("list_as_text", {})
        prefix_type = list_config.get("text_list_prefix_type", "alphabet")
        horizontal = list_config.get("horizontal", False)
        alternating_shading = list_config.get("alternating_shading", False)

        formatted_items = []
        for i, item in enumerate(text):
            # Generate prefix based on type
            if prefix_type == "alphabet":
                prefix = f"<b>{chr(ord('A') + i)}.</b> "
            elif prefix_type == "number":
                prefix = f"<b>{i + 1}.</b> "
            elif prefix_type == "bullet":
                prefix = "<b>•</b> "
            elif prefix_type == "none":
                prefix = ""
            else:
                # Default to alphabet for unknown types
                prefix = f"<b>{chr(ord('A') + i)}.</b> "

            # Recursively process each item
            processed_item = get_displayed_text(item) if isinstance(item, str) else str(item)

            # Apply alternating shading for dialogue readability
            if alternating_shading:
                shade_class = "dialogue-turn-even" if i % 2 == 0 else "dialogue-turn-odd"

                # Try to extract speaker name (text before first colon)
                speaker_match = re.match(r'^([^:]+):\s*(.*)$', processed_item, re.DOTALL)
                if speaker_match:
                    speaker_name = speaker_match.group(1).strip()
                    speaker_text = speaker_match.group(2).strip()
                    # Generate a consistent color index based on speaker name
                    speaker_hash = sum(ord(c) for c in speaker_name) % 6
                    # Use span with display:block style (spans are in sanitizer allowlist)
                    formatted_items.append(
                        f'<span class="dialogue-turn {shade_class}" style="display:block;">'
                        f'<b class="dialogue-speaker speaker-color-{speaker_hash}">{speaker_name}:</b> '
                        f'{prefix}{speaker_text}</span>'
                    )
                else:
                    # No speaker detected, use simple format
                    formatted_items.append(
                        f'<span class="dialogue-turn {shade_class}" style="display:block;">{prefix}{processed_item}</span>'
                    )
            else:
                formatted_items.append(f"{prefix}{processed_item}")

        # Join based on layout type
        if horizontal:
            # Horizontal layout for pairwise comparison
            cell_width = 100 // len(formatted_items) if formatted_items else 100
            cells = [
                f'<span class="pairwise-cell" style="width:{cell_width}%;display:inline-block;vertical-align:top;padding:10px;box-sizing:border-box;">{item}</span>'
                for item in formatted_items
            ]
            text = '<span class="pairwise-container" style="display:flex;gap:20px;">' + ''.join(cells) + '</span>'
        elif alternating_shading:
            # Already wrapped in divs, join without extra breaks
            text = ''.join(formatted_items)
        else:
            # Vertical layout with double line breaks
            text = "<br/><br/>".join(formatted_items)
        return text

    # Normalize text for consistent positioning (matches client-side normalization)
    # Remove non-printable characters and normalize whitespace
    text = re.sub(r'[^\x20-\x7E\n]', '', text)
    text = re.sub(r'[ \t]+', ' ', text)  # Normalize horizontal whitespace only
    text = text.strip()

    if config.get("highlight_linebreaks", False):
        text = text.replace("\n", "<br/>")

    return text


# Configure the Flask application
def configure_app(flask_app):
    """
    Configure the Flask application instance

    Args:
        flask_app: The Flask application instance

    Returns:
        The configured Flask application instance
    """
    global app
    app = flask_app

    # Set application configuration
    # Use a random secret key if sessions shouldn't persist, otherwise use the configured one
    if config.get("persist_sessions", False):
        secret_key = config.get("secret_key") or os.environ.get("POTATO_SECRET_KEY")
        if not secret_key:
            raise ValueError(
                "persist_sessions is enabled but no secret_key is configured. "
                "Set 'secret_key' in your config file or POTATO_SECRET_KEY environment variable."
            )
        app.secret_key = secret_key
    else:
        # Generate a random secret key to ensure sessions don't persist between restarts
        import secrets
        app.secret_key = secrets.token_hex(32)

    app.permanent_session_lifetime = timedelta(days=config.get("session_lifetime_days", 2))

    # Configure routes from the routes module
    from potato.routes import configure_routes
    configure_routes(app, config)

    # Configure admin dash app routes
    from potato.routing.admin import register_dash_apps
    register_dash_apps(app)

    return app


# Function to create and initialize the Flask application
def create_app():
    """
    Create and configure the Flask application

    Returns:
        The configured Flask application instance
    """
    global app

    # Initialize the app with explicit static folder configuration
    static_folder = os.path.join(cur_program_dir, 'static')
    app = Flask(__name__, static_folder=static_folder)

    # Configure Jinja2 to look in both main templates and generated templates directories
    real_templates_dir = os.path.join(cur_program_dir, 'templates')
    generated_templates_dir = os.path.join(real_templates_dir, 'generated')

    # Ensure the generated directory exists
    if not os.path.exists(generated_templates_dir):
        os.makedirs(generated_templates_dir, exist_ok=True)

    # Add the generated directory to the template search path
    from jinja2 import ChoiceLoader, FileSystemLoader
    app.jinja_loader = ChoiceLoader([
        FileSystemLoader(real_templates_dir),
        FileSystemLoader(generated_templates_dir)
    ])

    # Register HTML sanitization filters for XSS protection
    from potato.server_utils.html_sanitizer import register_jinja_filters
    register_jinja_filters(app)

    # Configure the app
    configure_app(app)

    # Add context processor for debug settings and common config values
    @app.context_processor
    def inject_template_context():
        """Inject debug settings and common config values into all templates."""
        from potato.logging_config import is_ui_debug_enabled, is_server_debug_enabled
        return {
            'ui_debug': is_ui_debug_enabled(),
            'server_debug': is_server_debug_enabled(),
            'debug_mode': config.get('debug', False),
            'debug_phase': config.get('debug_phase'),
            # Add common config values needed by templates
            'annotation_task_name': config.get('annotation_task_name', 'Annotation Task'),
        }

    return app


def test_setup():
    from argparse import Namespace
    args = Namespace(mode='start', config_file='/home/schellsn/author2paper_potato_multi_session/config.yaml', port=8946, verbose=False, debug=True, debug_log=None, debug_phase=None, very_verbose=False, customjs=False,
              customjs_hostname=None, require_password=None, persist_sessions=False, ssl_cert=None, ssl_key=None, to_v2=False, output_file=None, in_place=False, dry_run=False,
              quiet=False, show_path=False, show_similarity=False)

    init_config(args)

    # Set up centralized logging with appropriate verbosity
    setup_logging(
        verbose=config.get("verbose", False),
        debug=config.get("debug", False) or config.get("very_verbose", False),
        debug_log=config.get("debug_log"),
        log_dir=config.get("output_annotation_dir"),
    )

    # --- Add support for random seed ---
    # Admins can set 'random_seed' in config YAML to control assignment randomness (default 1234)
    if "random_seed" not in config:
        config["random_seed"] = 1234
    logger.info(f"Assignment random seed set to: {config['random_seed']}")
    # -----------------------------------

    # Initialize authenticator
    UserAuthenticator.init_from_config(config)

    init_user_state_manager(config)
    init_item_state_manager(config)

    load_all_data(config)


def run_server(args):
    """
    Run the Flask server with the given arguments.
    """
    # Initialize configuration
    init_config(args)

    # Set up centralized logging with appropriate verbosity
    setup_logging(
        verbose=config.get("verbose", False),
        debug=config.get("debug", False) or config.get("very_verbose", False) or True,
        debug_log=config.get("debug_log"),
        log_dir=config.get("output_annotation_dir"),
    )

    logger.debug(f"os.getcwd(): {os.getcwd()}")
    logger.debug(f"cur_working_dir: {cur_working_dir}")
    logger.debug(f"cur_program_dir: {cur_program_dir}")
    logger.debug(f"flask_templates_dir: {flask_templates_dir}")
    logger.debug(f"base_html_dir: {base_html_dir}")

    logger.debug(f"args: {args}")


    # Apply command line flags that override config settings
    if args.require_password is not None:
        # Command line flag takes precedence over config file
        config["require_password"] = args.require_password
        logger.debug(f"Password requirement set from command line: {args.require_password}")

    # Handle require_no_password (inverse of require_password) for backwards compatibility
    # This is commonly used in Prolific/MTurk configs
    if config.get("require_no_password", False):
        config["require_password"] = False
        logger.debug("Password requirement disabled via require_no_password config")

    # For URL-direct login, automatically disable password requirement
    login_config = config.get('login', {})
    if login_config.get('type') in ['url_direct', 'prolific']:
        config["require_password"] = False
        logger.debug(f"Password requirement disabled for {login_config.get('type')} login type")

    # Override port from command line if specified
    if args.port is not None:
        config["port"] = args.port
        logger.debug(f"Port set from command line: {args.port}")

    # Apply persist_sessions flag from command line
    config["persist_sessions"] = args.persist_sessions
    logger.debug(f"Session persistence set from command line: {args.persist_sessions}")

    # --- Add support for random seed ---
    # Admins can set 'random_seed' in config YAML to control assignment randomness (default 1234)
    if "random_seed" not in config:
        config["random_seed"] = 1234
    logger.debug(f"Assignment random seed set to: {config['random_seed']}")
    # -----------------------------------

    # Log debug phase setting if specified
    if config.get("debug_phase"):
        logger.info(f"Debug phase set to: {config['debug_phase']}")

    # Ensure that the task directory exists
    task_dir = config["task_dir"]
    if not os.path.exists(task_dir):
        os.makedirs(task_dir)

    # Ensure that the output annotation directory exists
    output_annotation_dir = config["output_annotation_dir"]
    if not os.path.exists(output_annotation_dir):
        os.makedirs(output_annotation_dir)

    # Initialize authenticator
    UserAuthenticator.init_from_config(config)

    init_user_state_manager(config)
    init_item_state_manager(config)

    load_all_data(config)

    # Initialize quality control manager if any QC features are enabled
    qc_enabled = (
        config.get('attention_checks', {}).get('enabled', False) or
        config.get('gold_standards', {}).get('enabled', False) or
        config.get('pre_annotation', {}).get('enabled', False)
    )
    if qc_enabled:
        task_dir = config.get('task_dir', os.path.dirname(config.get('config_file', '')))
        logger.info("=== INIT QUALITY CONTROL MANAGER STARTS ===")
        init_quality_control_manager(config, task_dir)
        logger.info("=== INIT QUALITY CONTROL MANAGER ENDS ===")

    # Log password requirement status
    logger.info(f"Password authentication required: {config.get('require_password', True)}")

    # Create and configure the Flask app
    app = create_app()

    init_prolific_study(config)

    logger.info(f"Open Routes: ")
    for rule in app.url_map.iter_rules():
        logger.info(f"{rule} -> endpoint: {rule.endpoint}, methods: {rule.methods}")

    # Run the Flask app
    host = config.get("host", "0.0.0.0")
    port = config.get("port", 8000)
    app.run(host=host, port=port, debug=config.get("debug", False), use_reloader=False)


# Define the main entry point for the Flask server
def main():
    """
    Main entry point for the Flask server

    This function initializes the application, loads data, and runs the server.
    """
    # Parse command line arguments
    args = arguments()

    if args.mode == 'start':
        logger.info("Starting server mode")
        run_server(args)
    elif args.mode == 'get':
        logger.info("Starting project retrieval")
        get_project_from_hub(args.config_file)
    elif args.mode == 'list':
        logger.info("Listing available projects")
        show_project_hub(args.config_file)
    elif args.mode == 'migrate':
        logger.info("Starting config migration")
        from potato.migrate_cli import main as migrate_main
        # Pass arguments to migrate CLI
        migrate_args = [args.config_file]
        if args.to_v2:
            migrate_args.append("--to-v2")
        if args.output_file:
            migrate_args.extend(["--output", args.output_file])
        if args.in_place:
            migrate_args.append("--in-place")
        if args.dry_run:
            migrate_args.append("--dry-run")
        if args.quiet:
            migrate_args.append("--quiet")
        sys.exit(migrate_main(migrate_args))

    logger.debug("Annotation platform shutdown complete")


# Main entry point
if __name__ == "__main__":
    main()
