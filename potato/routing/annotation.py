from flask import session, render_template, request, redirect, url_for, jsonify, Blueprint
from functools import wraps
from bs4 import BeautifulSoup

from potato.flask_server import config
from potato.phase import UserPhase
from potato.item_state_management import get_item_state_manager
from potato.user_state_management import get_user_state_manager
from potato.quality_control import get_quality_control_manager
from potato.logging_config import get_logger

logger = get_logger(__name__)

def phase_required(required_phase):
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

            current_phase = user_state.get_current_phase()
            if current_phase != required_phase:
                # Optionally flash a message
                logger.debug(f'User {username} (Session ID {session_id} Phase {current_phase}) not in {required_phase} phase, redirecting')
                return redirect(url_for("home"))

            return f(*args, **kwargs)
        return wrapped
    return decorator

annotation_bp = Blueprint("annotation", __name__, url_prefix="/annotation")

annotation_html_file = get_user_state_manager().get_phase_html_fname(UserPhase.ANNOTATION, "annotation")

# GET: Show the annotation page (without annotation info)
@annotation_bp.route("/", methods=["GET"])
@phase_required(UserPhase.ANNOTATION)
def annotation_page():
    #logger.debug(f"All users in state manager: {get_user_state_manager().get_user_session_ids()}")

    username = session['username']
    session_id = session["session_id"]
    user_state = get_user_state_manager().get_user_state(username, session_id)

    logger.debug(f"User {username} (Session ID {session_id}) - Render annotation page")

    # Get all existing user states for current user
    all_user_states_for_cur_user = get_user_state_manager().get_all_user_states(username)
    #logger.debug(f"User {username} (Session ID {session_id}) - All sessions: {all_user_states_for_cur_user.keys()}")

    # See if this user does not have assignments yet
    if not user_state.has_assignments():
        logger.debug(f"User {username} (Session ID {session_id}) - No assignments, assigning instances")
        get_item_state_manager().assign_items_to_user(user_state, all_user_states_for_cur_user)

    # User does not have open assignments
    user_is_finished = False
    if not user_state.has_open_assignments():
        if user_state.is_allowed_remaining_assignments(): # User is allowed more assignments
            # Try assigning new instances
            all_user_states_for_cur_user = get_user_state_manager().get_all_user_states(username)
            n_assigned = get_item_state_manager().assign_items_to_user(user_state, all_user_states_for_cur_user)
            if n_assigned == 0:
                user_is_finished = True
                logger.debug(f"User {username} (Session ID {session_id}) - User annotated all remaining instances")
        else:
            user_is_finished = True
            logger.debug(f"User {username} (Session ID {session_id}) - User reached maximum number of annotations")

    if user_is_finished:
        # If the user is done annotating, advance to the next phase
        get_user_state_manager().advance_phase(username, session_id)

        # Save state
        user_state = get_user_state_manager().get_user_state(username, session_id)
        get_user_state_manager().save_user_state(user_state)
        logger.debug(f"User {username} (Session ID {session_id}) - State saved")

        return redirect(url_for("home"))

    _inject_quality_control_item_if_needed(username, session_id, user_state)

    # See if this user has finished annotating
    total_num_items_assignable_to_user = get_item_state_manager().get_total_assignable_items_for_user(all_user_states_for_cur_user)
    logger.debug(f"User {username} (Session ID {session_id}) - Number of items that can still be annotated: {total_num_items_assignable_to_user}")

    # Get current annotation instance
    current_instance = user_state.get_current_instance()
    if not current_instance:
        logger.error(f'User {username} (Session ID {session_id}) - No annotation instance available')
        return render_template("error.html", message="No annotation instance available")

    instance_id = current_instance.get_id()
    instance_data = current_instance.get_data()
    instance_text = instance_data.get('displayed_text', instance_data.get('text', '???'))
    instance_paper_title = instance_data.get('paper_title', "???")
    instance_paper_abstract = instance_data.get('paper_abstract', "???")

    # Calculate progress counter values
    # Get the number of completed annotations and remaining assignable items
    finished_count = user_state.get_annotation_count()

    # Total = finished + remaining (so counter shows "X / Total" not "X / Remaining")
    total_count = finished_count + total_num_items_assignable_to_user

    max_assignments = user_state.get_max_assignments()
    total_count = min(total_count, max_assignments)

    # Check if the current instance has been annotated (for status indicator)
    instance_has_annotations = user_state.has_annotated(instance_id)

    totals_acs_passed = user_state.attention_check_state.passed_checks

    rendered_html = render_template(
        annotation_html_file,
        # Pass current instance info
        instance_text=instance_text,
        instance_id=instance_id,
        paper_title=instance_paper_title,
        paper_abstract=instance_paper_abstract,
        # Pass annotation schemes to the template
        annotation_schemes=config["annotation_schemes"],
        # Information for Page Header Bar
        annotation_task_name=config["annotation_task_name"],
        instance_has_annotations=instance_has_annotations,
        totals_acs_passed=totals_acs_passed,
        finished_count=finished_count,
        total_count=total_count,
        username=username,
        # Pass debug info
        debug=config.get("debug", False),
    )

    # Parse the page so we can programmatically reset the annotation state to what it was before
    soup = BeautifulSoup(rendered_html, "html.parser")

    # If the user has annotated this before, walk the DOM and fill out what they did
    annotations = get_annotations_for_user_on(username, instance_id)
    if annotations is not None:
        # Reset the annotation state
        for schema_name, label_dict in annotations.items():
            for label_name, value in label_dict.items():

                input_fields = soup.find_all(["input"], {"schema": schema_name, "value": value, "type": "radio"})

                for input_field in input_fields:
                    if input_field:
                        if input_field.get('value') == value:
                            input_field['checked'] = True
                            logger.error(f'User {username} (Session ID {session_id}) - Reset radio buttons to existing annotation: {value} for instance {instance_id}')

    rendered_html = str(soup)

    return rendered_html

@annotation_bp.route("/old_navigate_to_next", methods=["POST"])
@phase_required(UserPhase.ANNOTATION)
def old_navigate_to_next():
    #logger.debug("=== navigate_to_next STARTS ===")
    username = session.get("username")
    session_id = session.get('session_id')
    if not username or not session_id:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json()

    if not data:
        return jsonify({"error": "No data provided"}), 400

    logger.debug(f"data: {data}")

    action = data.get("action")
    instance_id = data.get('instance_id')
    if action != "next_instance" or not instance_id:
        return jsonify({"error": "Need to send action='next_instance' and instance_id"}), 404

    user_state = get_user_state_manager().get_user_state(username, session_id)
    if not user_state:
        return jsonify({"error": "User state not found"}), 404

    current_instance = user_state.get_current_instance()
    if not current_instance:
        logger.error(f'User {username} (Session ID {session_id}) - No annotation instance available')
        return render_template("error.html", message="No annotation instance available")

    current_instance_id = current_instance.get_id()
    if instance_id != current_instance_id:
        logger.error(f'User {username} (Session ID {session_id}) - Instance ID provided by browser and ID in user state are not equal.')
        return render_template("error.html", message="Instance ID provided by browser and ID in user state are not equal.")

    instance_has_annotations = user_state.has_annotated(instance_id)
    if not instance_has_annotations:
        logger.error(f'User {username} (Session ID {session_id}) - No annotation, cannot navigate to next instance')
        return jsonify({"status": "error", "message": "Could not navigate to next. Annotate first"}), 400

    if str(instance_id).startswith("GOLD"):
        logger.debug(f"User {username} (Session ID {session_id}) - user_state.instance_id_ordering {user_state.instance_id_ordering}")
        user_state.instance_id_ordering = [iid for iid in user_state.instance_id_ordering if not str(iid).startswith("GOLD")]
        logger.debug(f"User {username} (Session ID {session_id}) - user_state.instance_id_ordering {user_state.instance_id_ordering}")
        user_state.current_instance_index = user_state.current_instance_index - 1
        logger.debug(f"User {username} (Session ID {session_id}) - user_state.current_instance_index {user_state.current_instance_index}")

    success = move_to_next_instance(username, session_id)

    if success == "finished":
        return jsonify({"status": "finished"}), 200
    elif success:
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error", "message": "Could not navigate to next"}), 400

@annotation_bp.route("/navigate_to_next", methods=["POST"])
@phase_required(UserPhase.ANNOTATION)
def navigate_to_next():
    #logger.debug("=== navigate_to_next STARTS ===")
    username = session.get("username")
    session_id = session.get('session_id')
    if not username or not session_id:
        return jsonify({"error": "Not authenticated"}), 401

    # Process the annotation
    if request.is_json:
        annotation_data = request.get_json()
    else:
        annotation_data = dict(request.form)

    action = annotation_data.get("btn")
    instance_id = annotation_data.get('instance_id')

    if action != "next-btn" or not instance_id:
        return jsonify({"error": "Need to send btn='next-btn' and instance_id"}), 404

    user_state = get_user_state_manager().get_user_state(username, session_id)
    if not user_state:
        return jsonify({"error": "User state not found"}), 404

    current_instance = user_state.get_current_instance()
    if not current_instance:
        logger.error(f'User {username} (Session ID {session_id}) - No annotation instance available')
        return render_template("error.html", message="No annotation instance available")

    current_instance_id = current_instance.get_id()
    if instance_id != current_instance_id:
        logger.error(f'User {username} (Session ID {session_id}) - Instance ID provided by browser and ID in user state are not equal.')
        return render_template("error.html", message="Instance ID provided by browser and ID in user state are not equal.")

    instance_has_annotations = user_state.has_annotated(instance_id)
    if not instance_has_annotations:
        logger.error(f'User {username} (Session ID {session_id}) - No annotation, cannot navigate to next instance')
        return jsonify({"status": "error", "message": "Could not navigate to next. Annotate first"}), 400

    if str(instance_id).startswith("GOLD"):
        logger.debug(f"User {username} (Session ID {session_id}) - user_state.instance_id_ordering {user_state.instance_id_ordering}")
        user_state.instance_id_ordering = [iid for iid in user_state.instance_id_ordering if not str(iid).startswith("GOLD")]
        logger.debug(f"User {username} (Session ID {session_id}) - user_state.instance_id_ordering {user_state.instance_id_ordering}")
        user_state.current_instance_index = user_state.current_instance_index - 1
        logger.debug(f"User {username} (Session ID {session_id}) - user_state.current_instance_index {user_state.current_instance_index}")

    success = move_to_next_instance(username, session_id)

    if success == "finished":
        return redirect(url_for('annotation.annotation_page'))
    elif success:
        return redirect(url_for('annotation.annotation_page'))
    else:
        return render_template("error.html", message="Could not navigate to next item")

"""@annotation_bp.route("/navigate_to_prev", methods=["POST"])
@phase_required(UserPhase.ANNOTATION)
def navigate_to_prev():
    logger.debug("=== navigate_to_prev STARTS ===")
    username = session.get("username")
    session_id = session.get('session_id')
    if not username or not session_id:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json()

    if not data:
        return jsonify({"error": "No data provided"}), 400

    logger.debug(f"data: {data}")

    action = data.get("action")
    instance_id = data.get('instance_id')
    if action != "prev_instance" or not instance_id:
        return jsonify({"error": "Need to send action='prev_instance' and instance_id"}), 404

    user_state = get_user_state_manager().get_user_state(username, session_id)
    if not user_state:
        return jsonify({"error": "User state not found"}), 404

    current_instance = user_state.get_current_instance()
    if not current_instance:
        logger.error(f'User {username} (Session ID {session_id}) - No annotation instance available')
        return render_template("error.html", message="No annotation instance available")

    #current_instance_id = current_instance.get_id()
    #if instance_id != current_instance_id:
    #    logger.error(f'User {username} (Session ID {session_id}) - Instance ID provided by browser and ID in user state are not equal.')
    #    return render_template("error.html", message="Instance ID provided by browser and ID in user state are not equal.")

    #instance_has_annotations = user_state.has_annotated(instance_id)
    #if not instance_has_annotations:
    #    logger.error(f'User {username} (Session ID {session_id}) - No annotation, cannot navigate to next instance')
    #    return jsonify({"success": False})

    success = move_to_prev_instance(username, session_id)
    logger.error(f'User {username} (Session ID {session_id}) - Could navigate to prev: {success}')

    if success:
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error", "message": "Could not navigate to prev"}), 400

"""


def get_annotations_for_user_on(username, instance_id):
    """
    Returns the label-based annotations made by this user on the instance.

    Handles two data formats:
    1. Label objects as keys: {Label("schema", "label"): value}
       - Created by add_label_annotation() via /updateinstance endpoint
    2. Nested string dicts: {"schema": {"label": value}}
       - Created by set_annotation() via /annotate navigation
    """

    # Normalize instance_id to string for consistent key lookup
    #logger.debug("=== get_annotations_for_user_on STARTS ===")
    instance_id = str(instance_id)

    user_states = get_user_state_manager().get_all_user_states(username)
    for _, user_state in user_states.items():
        raw_annotations = user_state.get_annotation(instance_id)
        #logger.debug(f"raw_annotations: {raw_annotations}")

        if len(raw_annotations) > 0:
            break

    # Process the raw annotations into the expected format
    processed_annotations = {}
    for label, value in raw_annotations.items():
        # Check for Label object - the Label class uses 'schema' and 'name' attributes
        # with get_schema() and get_name() getter methods

        schema_name = label.get_schema()
        label_name = label.get_name()
        if schema_name not in processed_annotations:
            processed_annotations[schema_name] = {}

        #processed_annotations[schema_name][label_name] = value
        processed_annotations[schema_name][schema_name] = value

    return processed_annotations


def move_to_next_instance(username, session_id) -> bool:
    '''Moves the user forward to the next instance and returns True if successful'''
    #logger.debug(f"=== MOVE_TO_NEXT_INSTANCE START ===")
    #logger.debug(f"User {username}, Session ID: {session_id}")

    user_state = get_user_state_manager().get_user_state(username, session_id)
    #logger.debug(f"User {username} (Session ID {session_id}) - Instance Index before navigation: {user_state.get_current_instance_index()}")
    #logger.debug(f"User {username} (Session ID {session_id}) - Instance Indices before navigation: {user_state.instance_id_ordering}")

    # User does not have open assignments
    user_is_finished = False
    if not user_state.has_open_assignments():
        if user_state.is_allowed_remaining_assignments():  # User is allowed more assignments
            # Try assigning new instances
            all_user_states_for_cur_user = get_user_state_manager().get_all_user_states(username)
            n_assigned = get_item_state_manager().assign_items_to_user(user_state, all_user_states_for_cur_user)
            if n_assigned == 0:
                user_is_finished = True
                logger.debug(f"User {username} (Session ID {session_id}) - User annotated all remaining instances")
        else:
            user_is_finished = True
            logger.debug(f"User {username} (Session ID {session_id}) - User reached maximum number of annotations")

    if user_is_finished:
        # If the user is done annotating, advance to the next phase
        get_user_state_manager().advance_phase(username, session_id)

        # Save state
        user_state = get_user_state_manager().get_user_state(username, session_id)
        get_user_state_manager().save_user_state(user_state)
        logger.debug(f"User {username} (Session ID {session_id}) - State saved")

        #return redirect(url_for("home"))
        return "finished"

    result = user_state.go_forward()
    #logger.debug(f"User {username} (Session ID {session_id}) - Instance Index after navigation: {user_state.get_current_instance_index()}")
    #logger.debug(f"User {username} (Session ID {session_id}) - Instance Indices after navigation: {user_state.instance_id_ordering}")
    #logger.debug(f"User {username} (Session ID {session_id}) - Navigation result: {result}")

    #logger.debug(f"=== MOVE_TO_NEXT_INSTANCE END ===")
    return result


def move_to_prev_instance(username, session_id) -> bool:
    '''Moves the user forward to the next instance and returns True if successful'''
    #logger.debug(f"=== MOVE_TO_PREV_INSTANCE START ===")
    #logger.debug(f"User {username}, Session ID: {session_id}")

    user_state = get_user_state_manager().get_user_state(username, session_id)
    logger.debug(f"User {username} (Session ID {session_id}) - Instance Index before navigation: {user_state.get_current_instance_index()}")
    logger.debug(f"User {username} (Session ID {session_id}) - Instance Indices before navigation: {user_state.instance_id_ordering}")

    result = user_state.go_back()
    logger.debug(f"User {username} (Session ID {session_id}) - Instance Index after navigation: {user_state.get_current_instance_index()}")
    logger.debug(f"User {username} (Session ID {session_id}) - Instance Indices after navigation: {user_state.instance_id_ordering}")
    logger.debug(f"User {username} (Session ID {session_id}) - Navigation result: {result}")

    #logger.debug(f"=== MOVE_TO_PREV_INSTANCE END ===")
    return result


def _inject_quality_control_item_if_needed(username, session_id, user_state):
    #logger.debug(f"User {username} (Session ID {session_id}) - _inject_quality_control_item_if_needed starts")
    qc_manager = get_quality_control_manager()
    if not qc_manager:
        return

    current_instance = user_state.get_current_instance()
    #logger.debug(f"User {username} (Session ID {session_id}) - current_instance {current_instance}")
    current_instance_id = current_instance.get_id() if current_instance else None
    #logger.debug(f"User {username} (Session ID {session_id}) - current_instance_id {current_instance_id}")

    if current_instance_id and qc_manager.is_attention_check(current_instance_id):
        return

    assigned_ids = set(getattr(user_state, "assigned_instance_ids", set()) or set())
    annotated_ids = set(user_state.get_annotated_instance_ids()) if hasattr(user_state, "get_annotated_instance_ids") else set()
    seen_qc_ids = assigned_ids | annotated_ids

    #logger.debug(f"User {username} (Session ID {session_id}) - seen_qc_ids {seen_qc_ids}")

    insert_index = user_state.current_instance_index if user_state.current_instance_index >= 0 else 0

    def inject_item(item_data):
        item_id = item_data.get("id")
        if not item_id or item_id in user_state.instance_id_ordering or item_id in seen_qc_ids:
            return False

        if item_id in user_state.assigned_instance_ids:
            return False

        user_state.instance_id_ordering.insert(insert_index, item_id)
        user_state.assigned_instance_ids.add(item_id)
        #user_state.instance_id_to_order = user_state.generate_id_order_mapping(user_state.instance_id_ordering)
        return True

    if qc_manager.should_inject_attention_check(user_state):
        #logger.debug(f"User {username} (Session ID {session_id}) - should_inject_attention_check = True")
        attention_item = qc_manager.get_attention_check_item(username)
        #logger.debug(f"User {username} (Session ID {session_id}) - attention_item {attention_item}")
        logger.debug(f"User {username} (Session ID {session_id}) - user_state.instance_id_ordering {user_state.instance_id_ordering}")
        logger.debug(f"User {username} (Session ID {session_id}) - user_state.current_instance_index {user_state.current_instance_index}")
        logger.debug(f"User {username} (Session ID {session_id}) - insert_index {insert_index}")

        if attention_item and inject_item(attention_item):
            logger.debug(f"User {username} (Session ID {session_id}) - user_state.instance_id_ordering {user_state.instance_id_ordering}")
            logger.info(f"User {username} (Session ID {session_id}) - Injected attention check {attention_item.get('id')}")
            user_state.attention_check_state.n_items_since_last_check = 0  # reset counter
            return


