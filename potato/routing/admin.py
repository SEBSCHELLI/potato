import os
from flask import session, render_template, request, redirect, url_for, jsonify, Blueprint
from functools import wraps
import dash
from potato.flask_server import config
from potato.admin import admin_dashboard
from potato.logging_config import get_logger

logger = get_logger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def is_admin():
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not session.get("is_admin"):
                logger.warning("Admin Dashboard was tried to get accessed without authentication!")
                return render_template("admin_login.html", title=config.get("annotation_task_name", "Admin Dashboard"))
            return f(*args, **kwargs)
        return wrapped
    return decorator


@admin_bp.route("/login", methods=["GET", "POST"])
def admin_login():
    logger.debug("admin_login starts")
    error = None
    if request.method == "POST":
        submitted_key = request.form.get("api_key")

        # Use constant-time comparison to prevent timing attacks
        import hmac
        if hmac.compare_digest(str(submitted_key or ""), get_admin_api_key()):
            session['is_admin'] = True
            return redirect(url_for('admin.admin_page'))
        else:
            error = "Invalid API Key"

    logger.debug("admin_login get request: render admin_login")

    return render_template("admin_login.html", error=error)


@admin_bp.route("/", methods=["GET"])
@is_admin()
def admin_page():
    """
    Serve the admin dashboard page.

    This route serves the main admin dashboard interface with API key authentication.
    The dashboard provides comprehensive system monitoring and management capabilities.

    Returns:
        flask.Response: Rendered admin dashboard template or login form
    """

    # Check if embedding visualization is available
    # from potato.embedding_visualization import get_embedding_viz_manager
    # viz_manager = get_embedding_viz_manager()
    # embedding_viz_enabled = viz_manager is not None and viz_manager.enabled
    embedding_viz_enabled = False

    # Get basic context for the dashboard
    context = {
        "annotation_task_name": config.get("annotation_task_name", "Annotation Platform"),
        "debug_mode": config.get("debug", False)
    }

    return render_template("admin.html", **context)


@admin_bp.route("/api/overview", methods=["GET"])
@is_admin()
def admin_api_overview():
    """
    Get dashboard overview data.
    Admin-only endpoint requiring API key.

    Returns:
        flask.Response: JSON response with overview statistics
    """
    result = admin_dashboard.get_dashboard_overview()
    if isinstance(result, tuple):
        return jsonify(result[0]), result[1]
    return jsonify(result)


@admin_bp.route("/shutdown", methods=["GET"])
@is_admin()
def shutdown():
    import os, signal, threading
    def stop():
        os.kill(os.getpid(), signal.SIGINT)
    threading.Thread(target=stop).start()
    return jsonify({'status': 'Server shutting down...'})


@admin_bp.route("/delete_user_session", methods=["GET"])
@is_admin()
def delete_user_session():
    """
    Deletes a user session based on query parameters.

    Query Parameters:
        userid (str): The ID of the user.
        sessionid (str): The ID of the session to delete.

    Returns:
        Response: Placeholder for the HTTP response.
    """
    userid = request.args.get("userid")
    sessionid = request.args.get("sessionid")

    if userid and sessionid:


def get_admin_api_key():
    # Check config first
    configured_key = config.get("admin_api_key")
    if configured_key:
        return configured_key

    # Check environment variable
    env_key = os.environ.get("POTATO_ADMIN_API_KEY")
    if env_key:
        return env_key

    # Check if auto-generate key exists
    task_dir = config.get("task_dir", ".")
    if not task_dir:
        task_dir = "."

    key_file_path = os.path.join(task_dir, "admin_api_key.txt")

    if os.path.exists(key_file_path):
        try:
            with open(key_file_path, 'r') as f:
                existing_key = f.read().strip()
                if existing_key:
                    generated_admin_api_key = existing_key
                    logger.info(f"Loaded existing admin API key from {key_file_path}")
                    return generated_admin_api_key
        except Exception as e:
            logger.warning(f"Could not read existing admin API key file: {e}")

    # Auto-generate a key and save it to task directory
    import secrets
    generated_admin_api_key = secrets.token_urlsafe(32)

    # Save to file
    try:
        with open(key_file_path, 'w') as f:
            f.write(_generated_admin_api_key)
        logger.info(f"Generated admin API key and saved to {key_file_path}")
        logger.info(f"Use this key to access the admin dashboard at /admin")
    except Exception as e:
        logger.warning(f"Could not save admin API key to file: {e}")
        logger.info(f"Auto-generated admin API key (not persisted): {_generated_admin_api_key}")

    return generated_admin_api_key


def register_dash_apps(app):

    @app.before_request
    def restrict_dash_to_admins():
        if request.path.startswith("/admin/dash/"):
            if not session.get("is_admin"):
                return jsonify({"error": "unauthorized"}), 401

    # Annotator Overview
    dash_annotator_overview_app = dash.Dash(
        __name__,
        server=app,
        url_base_pathname='/admin/dash/annotator_overview/'
    )

    dash_annotator_overview_app.layout = admin_dashboard.get_dash_annotator_overview_data

    # Annotation Overview
    dash_annotation_overview_app = dash.Dash(
        "annotation_overview",
        server=app,
        url_base_pathname='/admin/dash/annotation_overview/'
    )

    dash_annotation_overview_app.layout = admin_dashboard.get_dash_annotation_overview_data

    # annotation_annotator_view
    dash_annotation_annotator_view_app = dash.Dash(
        "annotation_annotator_view",
        server=app,
        url_base_pathname='/admin/dash/annotation_annotator_view/'
    )

    dash_annotation_annotator_view_app.layout = admin_dashboard.get_dash_annotation_annotator_view_data

    # training_overview
    dash_training_overview_app = dash.Dash(
        "training_overview",
        server=app,
        url_base_pathname='/admin/dash/training_overview/'
    )

    dash_training_overview_app.layout = admin_dashboard.get_dash_training_overview_data

    # training_annotator_view
    dash_training_annotator_view_app = dash.Dash(
        "training_annotator_view",
        server=app,
        url_base_pathname='/admin/dash/training_annotator_view/'
    )

    dash_training_annotator_view_app.layout = admin_dashboard.get_dash_training_annotator_view_data

    # annotation_instance_view
    dash_annotation_instance_view_app = dash.Dash(
        "annotation_instance_view",
        server=app,
        url_base_pathname='/admin/dash/annotation_instance_view/'
    )

    dash_annotation_instance_view_app.layout = admin_dashboard.get_dash_annotation_instance_view_data

    # training_instance_view
    dash_training_instance_view_app = dash.Dash(
        "training_instance_view",
        server=app,
        url_base_pathname='/admin/dash/training_instance_view/'
    )

    dash_training_instance_view_app.layout = admin_dashboard.get_dash_training_instance_view_data

    logger.debug("Dash Apps successfully registered")



