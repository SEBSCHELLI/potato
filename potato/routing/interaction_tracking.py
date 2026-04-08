from flask import session, request, jsonify, Blueprint
from functools import wraps

from potato.phase import UserPhase
from potato.user_state_management import get_user_state_manager, get_training_item_state_manager
from potato.item_state_management import get_item_state_manager, Label
from potato.interaction_tracking import get_or_create_behavioral_data
from potato.quality_control import get_quality_control_manager
from potato.server_utils.date_handler import DateHandler
from potato.logging_config import get_logger

logger = get_logger(__name__)

track_interactions_bp = Blueprint("interaction", __name__)

GRACE_SECONDS = 60

def tracking_allowed(required_phases, grace_seconds):
    """
    Decorator to ensure the current user is in the required phase.
    Redirects to home if not.
    """
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            username = session.get("username")
            session_id = session.get("session_id")

            if not username or not session_id:
                # User not logged in
                logger.warning(f'Session info missing: User {username} (Session ID {session_id})')
                return "You are not logged in", 401

            user_state = get_user_state_manager().get_user_state(username, session_id)
            if not user_state:
                logger.debug(f'User {username} (Session ID {session_id}) does not have user state')
                return "Error: User State not found", 403

            req_allowed = False
            current_phase = user_state.get_current_phase()
            if current_phase in required_phases: # allow tracking if user is in training or annotation phase
                req_allowed = True
            elif current_phase == UserPhase.DONE: # or allow if user has been in annotation phase less than 60 seconds ago (see PHASE_GRACE_SECONDS)
                seconds_since_annotation_finished = (DateHandler.get_timestamp_now() - user_state.phase_page_start_times[UserPhase.DONE]["done"]).total_seconds()
                if seconds_since_annotation_finished <= grace_seconds:
                    req_allowed = True

            if not req_allowed:
                # Optionally flash a message
                logger.debug(f'User {username} (Session ID {session_id} Phase {current_phase}) not in {required_phases} phases, redirecting')
                return "Error: Could not track interactions", 400

            return f(*args, **kwargs)
        return wrapped
    return decorator


@track_interactions_bp.route("/api/track_interactions", methods=["POST"])
@tracking_allowed([UserPhase.TRAINING, UserPhase.ANNOTATION], GRACE_SECONDS)
def track_interactions():
    #logger.debug("=== TRACK INTERACTIONS STARTS ===")

    username = session.get("username")
    session_id = session.get('session_id')
    if not username or not session_id:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json()

    if not data:
        return jsonify({"error": "No data provided"}), 400

    user_state = get_user_state_manager().get_user_state(username, session_id)
    if not user_state:
        return jsonify({"error": "User state not found"}), 404

    instance_id = data.get('instance_id')
    events = data.get('events', [])

    phase = user_state.get_current_phase()
    if phase == UserPhase.DONE: # if user is done recently (less than GRACE_SECONDS ago), still use previous phase (because interactions are sent delayed)
        seconds_since_annotation_finished = (DateHandler.get_timestamp_now() - user_state.phase_page_start_times[UserPhase.DONE]["done"]).total_seconds()
        if seconds_since_annotation_finished <= GRACE_SECONDS:
            prev_phase = user_state.completed_phase_and_pages[-1][0]
            phase = prev_phase

    # Route to appropriate phase handler based on current phase
    if phase == UserPhase.TRAINING:
        training_state = user_state.get_training_state()
        bd = get_or_create_behavioral_data(
            training_state.training_instance_id_to_behavioral_data,
            instance_id
        )
    elif phase == UserPhase.ANNOTATION:
        bd = get_or_create_behavioral_data(
            user_state.instance_id_to_behavioral_data,
            instance_id
        )
    else:
        logger.debug(f"Wrong Phase: {phase}")
        return jsonify({"error": "wrong phase", "events_recorded": 0}), 400

    # Get or create behavioral data for this instance

    # Add events
    for event in events:
        #logger.debug(f"User {username} (Session ID {session_id}) - Tracked event: {event} for instance {instance_id}")

        if event.get("instance_id") == bd.instance_id:
            # Add to behavioral data
            bd.add_interaction(event_type=event.get('event_type', 'unknown'),
                               target=event.get('target', ''),
                               client_timestamp=event.get('client_timestamp'),
                               metadata=event.get('metadata', {}))

        else:
            logger.warning("Could not add Interaction because Instance ID mismatch")

    if event: # only check for latest event
        user_state.check_and_set_last_activity_time(event.get('client_timestamp'))
        if phase == UserPhase.TRAINING:
            training_state = user_state.get_training_state()
            training_state.check_and_set_last_activity_time(event.get('client_timestamp'))

    # Update focus time if provided
    focus_time = data.get('focus_time', {})
    for element, time_ms in focus_time.items():
        if hasattr(bd, 'update_focus_time'):
            bd.update_focus_time(element, time_ms)
        elif hasattr(bd, 'focus_time_by_element'):
            bd.focus_time_by_element[element] = bd.focus_time_by_element.get(element, 0) + time_ms

    # Update scroll depth
    if 'scroll_depth' in data:
        scroll_depth = data['scroll_depth']
        if hasattr(bd, 'update_scroll_depth'):
            bd.update_scroll_depth(scroll_depth)
        elif hasattr(bd, 'scroll_depth_max'):
            bd.scroll_depth_max = max(bd.scroll_depth_max, scroll_depth)

    # Save state
    get_user_state_manager().save_user_state(user_state)
    #logger.debug(f"User {username} (Session ID {session_id}) - State saved")

    #logger.debug("=== TRACK INTERACTIONS ENDS ===")
    return jsonify({"status": "ok", "events_recorded": len(events)}), 200

@track_interactions_bp.route("/api/track_annotation_change", methods=["POST"])
@tracking_allowed([UserPhase.TRAINING, UserPhase.ANNOTATION], GRACE_SECONDS)
def track_annotation_change():
    try:
        #logger.debug("=== TRACK ANNOTATION CHANGE STARTS ===")

        username = session.get("username")
        session_id = session.get('session_id')
        if not username or not session_id:
            logger.error(f"User {session.get('username')} (Session ID {session.get('session_id')}) - Not authenticated")
            return jsonify({"error": "Not authenticated"}), 401

        data = request.get_json()

        if not data:
            logger.error(f"User {username} (Session ID {session_id}) - No data provided")
            return jsonify({"error": "No data provided"}), 400

        user_state = get_user_state_manager().get_user_state(username, session_id)
        if not user_state:
            logger.error(f"User {username} (Session ID {session_id}) - User state not found")
            return jsonify({"error": "User state not found"}), 404

        phase = user_state.get_current_phase()
        if phase == UserPhase.DONE:  # if user is done recently (less than GRACE_SECONDS ago), still use previous phase (because interactions are sent delayed)
            seconds_since_annotation_finished = (DateHandler.get_timestamp_now() - user_state.phase_page_start_times[UserPhase.DONE]["done"]).total_seconds()
            if seconds_since_annotation_finished <= GRACE_SECONDS:
                prev_phase = user_state.completed_phase_and_pages[-1][0]
                phase = prev_phase

        if phase not in [UserPhase.TRAINING, UserPhase.ANNOTATION]:
            logger.error(f"User {username} (Session ID {session_id}) - Wrong Phase: {phase}")
            return jsonify({"status": "error", "message": "Wrong Phase"}), 400

        instance_id = data.get('instance_id')
        schema_name = data.get('schema_name')
        new_value = data.get('new_value')
        client_timestamp = data.get('client_timestamp')
        source = data.get('source', 'user')

        if not instance_id or not schema_name or not new_value:
            logger.error(f"User {username} (Session ID {session_id}) - Missing required fields in {data}")
            return jsonify({"error": "Missing required fields"}), 400

        # Check if provided instance id is the same as backend instance id of current user session
        if phase == UserPhase.TRAINING:
            training_state = user_state.get_training_state()
            current_instance = training_state.get_current_training_instance()
        elif phase == UserPhase.ANNOTATION:
            current_instance = user_state.get_current_instance()

        if not current_instance:
            logger.error(f"User {username} (Session ID {session_id}) - No current instance found")
            return jsonify({"error": "No current instance found"}), 400

        cur_instance_id = current_instance.get_id()

        if cur_instance_id != instance_id:
            logger.error(f"User {username} (Session ID {session_id}) - Could not track annotation change because of wrong instance_id {cur_instance_id} vs. {instance_id}")
            return jsonify({"error": "Current instance id is wrong"}), 400

        # Prepare metadata
        raw_data = request.get_data(cache=True)
        metadata = {
            "request_id": data.get("request_id"),
            "user_agent": request.headers.get("User-Agent"),
            "ip_address": request.remote_addr,
            "content_type": request.content_type,
            "request_size": len(raw_data) if raw_data else 0
        }

        # Prepare annotation_action_type (add annotation vs. update annotation)
        label = Label(schema_name, schema_name)
        old_value = None
        if phase == UserPhase.TRAINING:
            training_state = user_state.get_training_state()
            if instance_id in training_state.training_instance_id_to_label_to_value:
                old_value = training_state.training_instance_id_to_label_to_value[instance_id].get(label)
        elif phase == UserPhase.ANNOTATION:
            if instance_id in user_state.instance_id_to_label_to_value:
                old_value = user_state.instance_id_to_label_to_value[instance_id].get(label)

        annotation_action_type = "add_annotation" if old_value is None else "update_annotation"

        # Add annotation to user state
        if phase == UserPhase.TRAINING:
            training_state = user_state.get_training_state()
            training_state.change_annotation(instance_id, label, new_value)
        elif phase == UserPhase.ANNOTATION:
            user_state.add_annotation(instance_id, label, new_value)

        #logger.debug(f"User {username} (Session ID {session_id}) - Tracked annotation change: {data} for instance {instance_id}")
        logger.info(f"User {username} (Session ID {session_id}) - Added label annotation: {schema_name} = {new_value} for instance {instance_id} (data: {data})")

        # Add annotation change to Behavioral Data
        if phase == UserPhase.TRAINING:
            training_state = user_state.get_training_state()
            bd = get_or_create_behavioral_data(
                training_state.training_instance_id_to_behavioral_data,
                instance_id
            )

        elif phase == UserPhase.ANNOTATION:
            bd = get_or_create_behavioral_data(
                user_state.instance_id_to_behavioral_data,
                instance_id
            )

        bd.add_annotation_change(schema_name=schema_name,
                                 label_name=schema_name,
                                 action=annotation_action_type,
                                 old_value=old_value,
                                 new_value=new_value,
                                 source=source,
                                 client_timestamp=client_timestamp,
                                 metadata=metadata)

        user_state.check_and_set_last_activity_time(client_timestamp)
        if phase == UserPhase.TRAINING:
            training_state = user_state.get_training_state()
            training_state.check_and_set_last_activity_time(client_timestamp)

        # Quality Control
        if phase == UserPhase.ANNOTATION:
            # Quality control validation (attention checks and gold standards)
            qc_manager = get_quality_control_manager()
            #qc_manager = None
            if qc_manager:
                is_attention_check = qc_manager.is_attention_check(instance_id)
                if is_attention_check:
                    annotation = {schema_name: new_value}
                    response_time = bd.total_time_ms / 1000

                    # Check if this is an attention check
                    attention_result = qc_manager.validate_attention_response(
                        username, session_id, instance_id, annotation, response_time
                    )
                    user_state.attention_check_state.add_attention_check_result(attention_result)
                    user_state.attention_check_state.n_items_since_last_check = 0 # reset counter
                else:
                    if annotation_action_type == "add_annotation":
                        user_state.attention_check_state.record_non_attention_check_annotation()

        # Register Annotator
        if phase == UserPhase.TRAINING:
            get_training_item_state_manager().register_annotator(instance_id, username)
        elif phase == UserPhase.ANNOTATION:
            get_item_state_manager().register_annotator(instance_id, username)

        # Save state
        get_user_state_manager().save_user_state(user_state)
        #logger.debug(f"User {username} (Session ID {session_id}) - State saved")

        #logger.debug("=== TRACK ANNOTATION CHANGE ENDS ===")
        return jsonify({"status": "ok"})

    except Exception as e:
        logger.exception(f"User {username} (Session ID {session_id}) - Failed to track annotation change: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500
