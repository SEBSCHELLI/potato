from flask import session, render_template, redirect, url_for, Blueprint
from functools import wraps

from potato.flask_server import config
from potato.phase import UserPhase
from potato.user_state_management import get_user_state_manager
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

instructions_bp = Blueprint("instructions", __name__)

# GET: Show the instructions page for the current phase
@instructions_bp.route("/instructions", methods=["GET"])
@phase_required(UserPhase.INSTRUCTIONS)
def instructions_page():
    username = session['username']
    session_id = session["session_id"]
    user_state = get_user_state_manager().get_user_state(username, session_id)

    # Get the page the user is currently on
    cur_phase, cur_page = user_state.current_phase_and_page
    page_fname = get_user_state_manager().get_phase_html_fname(cur_phase, cur_page)

    # Render the instructions with necessary context variables
    return render_template(page_fname,
                           annotation_task_name=config.get("annotation_task_name"),
                           title=config.get("annotation_task_name"),
                           username=session.get('username'),
                           debug_mode=config.get("debug", False),
                           ui_debug=config.get("ui_debug", False),
                           server_debug=config.get("server_debug", False),
                           debug_phase=config.get("debug_phase"),
                           ui_config=config.get("ui_config", {}),
                           min_correct="?????",
                           n_train_items="?????",
                           n_items_to_annotate="????",
                           coin_to_money="???")

# POST: User clicks on continue button
@instructions_bp.route("/continue_instructions", methods=["POST"])
@phase_required(UserPhase.INSTRUCTIONS)
def continue_instructions():
    username = session['username']
    session_id = session["session_id"]
    user_state = get_user_state_manager().get_user_state(username, session_id)

    prev_phase, prev_page = user_state.current_phase_and_page

    usm = get_user_state_manager()
    usm.advance_phase(username, session_id)

    cur_phase, cur_page = user_state.current_phase_and_page

    logger.debug(f"User {username} (Session ID: {session_id}) - Advancing from (Phase: {prev_phase} Page: {prev_page}) to (Phase: {cur_phase} Page: {cur_page})")

    return redirect(url_for("home"))