"""
Flask Routes Module

"""
from __future__ import annotations

import os
import json
import logging
import datetime
from datetime import timedelta
from flask import Flask, session, render_template, request, redirect, url_for, jsonify, make_response
import time
import uuid

# Import from the main flask_server.py module
from potato.flask_server import (
    app, config, logger,
    get_user_state_manager, get_item_state_manager, get_training_item_state_manager,
    UserPhase, Label
)

# Import annotation history
from potato.annotation_history import AnnotationHistoryManager
from potato.logging_config import is_ui_debug_enabled, get_debug_log_settings

# Import quality control
from potato.quality_control import get_quality_control_manager

from potato.routing.training import training_bp
from potato.routing.interaction_tracking import track_interactions_bp
from potato.routing.annotation import annotation_bp
from potato.routing.instructions import instructions_bp
from potato.routing.login_logout import loginout_bp
from potato.routing.admin import admin_bp

@app.route("/", methods=["GET", "POST"])
def home():
    """
    Handle requests to the home page.

    This route serves as the main entry point for the annotation platform.
    It handles session management, user authentication, and phase routing
    based on the user's current state in the annotation workflow.

    Features:
    - Session validation and timeout management
    - User authentication and state initialization
    - Phase-based routing to appropriate pages
    - Survey flow management
    - Progress tracking and validation
    - URL-direct login for crowdsourcing platforms (Prolific, MTurk, etc.)

    Returns:
        flask.Response: Rendered template or redirect based on user state

    Side Effects:
        - May initialize new user state
        - May advance user phases
        - May clear invalid sessions
    """

    # Check if user has an active session
    username = session.get("username")
    session_id = session.get("session_id")

    if not username or not session_id:
        # User not logged in
        logger.warning(f'Session info missing: User {username} (Session ID {session_id})')
        return render_template("error.html", error_message="Not logged in.")

    usm = get_user_state_manager()
    user_state = usm.get_user_state(username, session_id)

    # Get the current phase of the user and route accordingly
    phase = user_state.get_current_phase()
    #logger.debug(f"User phase: {phase}")

    # Route to appropriate phase handler based on current phase
    if phase == UserPhase.LOGIN:
        render_template("error.html", message="Invalid application state")
    elif phase == UserPhase.CONSENT:
        render_template("error.html", message="Invalid application state")
    elif phase == UserPhase.PRESTUDY:
        render_template("error.html", message="Invalid application state")
    elif phase == UserPhase.INSTRUCTIONS:
        return redirect(url_for('instructions.instructions_page'))
    elif phase == UserPhase.TRAINING:
        return redirect(url_for('training.training_page'))
    elif phase == UserPhase.ANNOTATION:
        return redirect(url_for('annotation.annotation_page'))
    elif phase == UserPhase.POSTSTUDY:
        render_template("error.html", message="Invalid application state")
    elif phase == UserPhase.DONE:
        return done()

    logger.error(f"Invalid phase for user {user_id}: {phase}")
    return render_template("error.html", message="Invalid application state")



@app.route("/api/current_instance", methods=["GET"])
def get_current_instance():
    """Get the current instance information for the current user."""
    logger.debug(f"=== GET_CURRENT_INSTANCE START ===")

    if 'username' not in session:
        logger.warning("Get current instance without active session")
        return jsonify({"error": "No active session"}), 401

    username = session['username']
    session_id = session['session_id']
    logger.debug(f"Username: {username}")

    try:
        usm = get_user_state_manager()
        user_state = usm.get_user_state(username, session_id)
        if not user_state:
            logger.error(f"User state not found for user: {username}")
            return jsonify({"error": "User state not found"}), 404

        current_instance = user_state.get_current_instance()
        if not current_instance:
            logger.error(f"No current instance for user: {username}")
            return jsonify({"error": "No current instance"}), 404

        instance_id = current_instance.get_id()
        logger.debug(f"Current instance ID: {instance_id}")

        return jsonify({
            "instance_id": instance_id,
            "current_index": user_state.get_current_instance_index(),
            "total_instances": len(user_state.instance_id_ordering)
        })

    except Exception as e:
        logger.error(f"Error getting current instance: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/done", methods=["GET", "POST"])
def done():
    """
    Handle the done phase of the annotation process.

    This route displays the completion page with:
    - A thank you message
    - The completion code (if configured)
    - A redirect link to Prolific (if configured)

    Returns:
        flask.Response: Rendered template or redirect
    """
    if 'username' not in session:
        return home()

    username = session['username']
    session_id = session["session_id"]
    usm = get_user_state_manager()
    user_state = usm.get_user_state(username, session_id)

    # Check that the user is in the done phase
    if user_state.get_current_phase() != UserPhase.DONE:
        # If not in the done phase, redirect
        return home()

    # Get completion code from config
    completion_code = config.get("completion_code", "")

    # Build Prolific redirect URL if completion code is set
    prolific_redirect_url = None
    login_config = config.get('login', {})
    login_type = login_config.get('type', 'standard')

    if completion_code and login_type in ['url_direct', 'prolific']:
        # Build the Prolific completion URL (only if using Prolific-style URL argument)
        url_argument = login_config.get('url_argument', 'PROLIFIC_PID')
        if url_argument in ['PROLIFIC_PID', 'prolific_pid']:
            # Format: https://app.prolific.co/submissions/complete?cc=YOUR_CODE
            prolific_redirect_url = f"https://app.prolific.co/submissions/complete?cc={completion_code}"

    # Get MTurk submission parameters from session
    mturk_submit_url = session.get('mturk_submit_to')
    mturk_assignment_id = session.get('mturk_assignment_id')

    # Check for auto-redirect setting
    auto_redirect = config.get('auto_redirect_on_completion', False)
    auto_redirect_delay = config.get('auto_redirect_delay', 5000)  # milliseconds

    # Show the completion page
    return render_template("done.html",
                           title=config.get("annotation_task_name", "Annotation Platform"),
                           completion_code=completion_code,
                           prolific_redirect_url=prolific_redirect_url,
                           mturk_submit_url=mturk_submit_url,
                           mturk_assignment_id=mturk_assignment_id,
                           auto_redirect=auto_redirect,
                           auto_redirect_delay=auto_redirect_delay)


#@app.route('/shutdown', methods=['POST'])
def shutdown():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        return jsonify({'error': 'Not running with the Werkzeug Server'}), 500
    logger.info('Shutting down server via /shutdown')
    func()
    return jsonify({'status': 'Server shutting down...'})


def configure_routes(flask_app, app_config):
    """
    Initialize the Flask routes with the given Flask app instance
    and configuration.

    This function is called by flask_server.py when initializing the application.

    Args:
        flask_app: The Flask application instance
        app_config: The application configuration
    """
    global app, config
    app = flask_app
    config = app_config

    # Set up session configuration
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

    app.permanent_session_lifetime = timedelta(days=config.get("session_lifetime_days", 7))

    # Register all routes with the flask app instance
    app.add_url_rule("/", "home", home, methods=["GET", "POST"])
    app.add_url_rule("/done", "done", done, methods=["GET", "POST"])
    #app.add_url_rule("/shutdown", "shutdown", shutdown, methods=["POST"])

    app.register_blueprint(loginout_bp)
    app.register_blueprint(instructions_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(annotation_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(track_interactions_bp)
