import datetime
from flask import session, render_template, request, redirect, url_for, jsonify, Blueprint

from potato.flask_server import config
from potato.authentication import UserAuthenticator
from potato.phase import UserPhase
from potato.user_state_management import get_user_state_manager
from potato.logging_config import get_logger

logger = get_logger(__name__)

loginout_bp = Blueprint("loginout", __name__)

@loginout_bp.route("/prolific_login", methods=["GET"])
def prolific_login():
    """
    Register a new user and initialize their user state.

    Args:
        username: The username to initialize state for
    """
    #logger.debug("=== PROLIFIC LOGIN START ===")
    #logger.debug(f"Session before login: {dict(session)}")

    # Check if prolific login is active in config
    login_config = config.get('login', {})
    login_type = login_config.get('type', 'standard')
    if login_type != "prolific":
        logger.warning(f"Prolific login not active.")
        return render_template("error.html", error_message="Prolific login is not active.")

    # Check if user already has active session
    username = session.get("username")
    session_id = session.get("session_id")
    if username and session_id:
        logger.warning(f"User {username} (Session ID {session_id}) - Prolific User already logged in, redirecting to home")
        return redirect(url_for("home"))

    # Check if all Prolific URL parameters are present
    if not all(k in request.args for k in ("PROLIFIC_PID", "STUDY_ID", "SESSION_ID")):
        logger.warning(f"Prolific parameters missing: {str(request.args)}")
        return render_template("error.html", error_message="Prolific parameters missing.")

    # Get Prolific URL parameters
    username = request.args.get("PROLIFIC_PID")
    study_id = request.args.get("STUDY_ID")
    session_id = request.args.get("SESSION_ID")

    logger.warning(f"TODO: Prolific Verification.")
    logger.info(f"Prolific login with user={username}, session_id={session_id}, study_id={study_id}")

    # Set session parameters
    session['username'] = str(username)
    session['prolific_session_id'] = str(session_id)
    session["session_id"] = str(session_id)
    session['prolific_study_id'] = str(study_id)
    session.permanent = True

    # Check if this is a new session or if user continues a previous session
    if get_user_state_manager().has_user_sessionid(username, session_id):
        logger.warning(f"User {username} (Session ID {session_id}) - Prolific user already registered with this session ID")
        #logger.debug("=== PROLIFIC LOGIN END - Redirecting to home ===")
        return redirect(url_for("home"))

    # Register and login the new user + session
    user_authenticator = UserAuthenticator.get_instance()
    result = user_authenticator.add_user(username, None,
                                         prolific_session_id=session_id,
                                         prolific_study_id=study_id)

    logger.debug(f"User {username} (Session ID {session_id}) - Registered Prolific user: {result}")

    # Initialize user state
    logger.debug(f"User {username} (Session ID {session_id}) - Initializing user state")
    usm = get_user_state_manager()
    usm.add_user(username, session_id)

    # Store the session creation time
    session['created_at'] = datetime.datetime.now()

    # Advance user to the first phase if they're in LOGIN
    user_state = usm.get_user_state(username, session_id)
    if user_state:
        if user_state.get_current_phase() == UserPhase.LOGIN:
            logger.debug(f"User {username} (Session ID {session_id}) - Advancing to first phase")
            usm.advance_phase(username, session_id)

    # Redirect to home which will route to the appropriate phase
    #logger.debug("=== PROLIFIC LOGIN END - Redirecting to home ===")
    return redirect(url_for("home"))

@loginout_bp.route("/logout", methods=["POST"])
def logout():
    """
    Handle user logout requests.

    Features:
    - Session cleanup
    - State persistence
    - Progress saving

    Returns:
        flask.Response: Redirect to login page
    """

    username = session.get("username")
    session_id = session.get('session_id')
    if not username or not session_id:
        return jsonify({"error": "Not authenticated"}), 401

    user_state = get_user_state_manager().get_user_state(username, session_id)
    if not user_state:
        return jsonify({"error": "User state not found"}), 404

    # Save state
    get_user_state_manager().save_user_state(user_state)
    # logger.debug(f"User {username} (Session ID {session_id}) - State saved")

    session.clear()

    logger.debug(f"User {username} (Session ID {session_id}) - Logged out")

    return render_template("logged_out.html", message="Please use the study link in Prolific to log in again.")