from flask import session, render_template, request, redirect, url_for, Blueprint
from functools import wraps

from potato.flask_server import config
from potato.interaction_tracking import get_or_create_behavioral_data
from potato.phase import UserPhase
from potato.item_state_management import get_training_item_state_manager
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
            if not username:
                # User not logged in
                logger.warning(f'User not logged in')
                return "You must provide a username before training.", 401

            username = session['username']
            session_id = session["session_id"]
            user_state = get_user_state_manager().get_user_state(username, session_id)

            current_phase = user_state.get_current_phase()
            if current_phase != required_phase:
                # Optionally flash a message
                logger.debug(f'User {username} (Session ID {session_id}) not in {required_phase} phase, redirecting')
                logger.debug("TODO: Implement redirect to a /home")

            return f(*args, **kwargs)
        return wrapped
    return decorator


training_bp = Blueprint("training", __name__, url_prefix="/training")


# GET: Show the current training example
@training_bp.route("/", methods=["GET"])
@phase_required(UserPhase.TRAINING)
def training_page():
    logger.debug("=== training_page STARTS ===")

    username = session['username']
    session_id = session["session_id"]
    user_state = get_user_state_manager().get_user_state(username, session_id)

    training_state = user_state.get_training_state()

    # Initialize training state if not already done
    if not training_state:
        training_config = config.get('training', {})
        passing_criteria = training_config.get('passing_criteria', {})
        logger.debug(f"passing_criteria: {passing_criteria}")
        allow_retry = training_config.get('allow_retry', False)

        max_mistakes = passing_criteria.get('max_mistakes', -1)
        max_mistakes_per_question = passing_criteria.get('max_mistakes_per_question', -1)
        user_state.init_training_state(max_mistakes, max_mistakes_per_question, allow_retry)
        training_state = user_state.get_training_state()

    # Initialize training instances if not already done
    if not training_state.training_instance_ids:
        tism = get_training_item_state_manager()
        training_instances = tism.get_training_items()
        training_state.set_training_instance_ids([item.get_id() for item in training_instances])

    # Check if user has already failed due to too many mistakes
    if training_state.is_failed() or training_state.should_fail_due_to_mistakes():
        failed_message = "You have exceeded the maximum number of allowed mistakes and cannot continue."
        training_state.set_failed(True, failed_message)
        logger.info(f'User {username} (Session ID {session_id}) - Training failed: total_mistakes {training_state.total_mistakes} exceeded max_mistakes ({training_state.max_mistakes})')

        # Move to DONE phase (kick out)
        user_state.set_current_phase_and_page(UserPhase.DONE, "done")
        return render_template("training_failed.html",
                               message=training_state.failed_message,
                               total_mistakes=training_state.get_total_mistakes(),
                               max_mistakes=training_state.max_mistakes,
                               annotation_task_name=config.get("annotation_task_name", "Annotation Platform"),
                               username=username)

    # Get progress info
    total_questions = len(training_state.get_training_instance_ids())
    current_question_num = training_state.get_current_training_instance_id() + 1
    completed_question_num = training_state.get_current_training_instance_id()

    if training_state.is_passed():
        logger.info(f'User {username} (Session ID {session_id}) has passed training')
        return render_template("training_success.html",
                               current_question=current_question_num,
                               total_questions=total_questions,
                               correct_count=training_state.get_correct_answer_count(),
                               total_mistakes=training_state.get_total_mistakes(),
                               annotation_task_name=config.get("annotation_task_name", "Annotation Platform"),
                               username=username)

    current_instance = training_state.get_current_training_instance()
    if not current_instance:
        logger.error(f'User {username} (Session ID {session_id}): No training instance available')
        return render_template("error.html", message="No training instance available")

    instance_id = current_instance.get_id()
    instance_data = current_instance.get_data()
    instance_text = instance_data.get('displayed_text', current_instance.get_data().get('text', ''))
    instance_paper_title = instance_data.get('paper_title', "???")
    instance_paper_abstract = instance_data.get('paper_abstract', "???")

    cur_phase, cur_page = user_state.get_current_phase_and_page()
    page_fname = get_user_state_manager().get_phase_html_fname(cur_phase, cur_page)

    # Create behavioral data structure if not already available
    get_or_create_behavioral_data(training_state.training_instance_id_to_behavioral_data,
                                  instance_id)

    return render_template(page_fname,
                           instance_text=instance_text,
                           instance_id=instance_id,
                           paper_title=instance_paper_title,
                           paper_abstract=instance_paper_abstract,
                           feedback=training_state.feedback_message,
                           feedback_type=training_state.feedback_type,
                           show_feedback=training_state.show_feedback,
                           allow_retry=training_state.allow_retry,
                           needs_retry=training_state.needs_retry,
                           current_question=current_question_num,
                           completed_question=completed_question_num,
                           total_questions=total_questions,
                           correct_count=training_state.get_correct_answer_count(),
                           mistake_count=training_state.get_total_mistakes(),
                           annotation_task_name=config.get("annotation_task_name", "Annotation Platform"),
                           username=username)


# POST: Submit an answer for the current example
@training_bp.route("/answer", methods=["POST"])
@phase_required(UserPhase.TRAINING)
def submit_answer():
    #logger.debug("=== submit_answer STARTS ===")

    username = session['username']
    session_id = session["session_id"]
    user_state = get_user_state_manager().get_user_state(username, session_id)

    training_state = user_state.get_training_state()

    # Process the annotation
    if request.is_json:
        annotation_data = request.get_json()
    else:
        annotation_data = dict(request.form)

    #logger.debug(f"annotation_data: {annotation_data}")

    current_instance = training_state.get_current_training_instance()
    if not current_instance:
        logger.error(f'User {username} (Session ID {session_id}) - No training instance available')
        return render_template("error.html", message="No training instance available")

    instance_id = current_instance.get_id()

    # Get correct answers for this training instance
    correct_answers = current_instance.get_data().get('correct_answers', None)
    if not correct_answers:
        logger.error(f'User {username} (Session ID {session_id}) - No correct answers found for training instance {instance_id}')
        return render_template("error.html", message="Training data error")

    is_correct = check_training_answer(annotation_data, correct_answers)

    training_state.commit_annotation(instance_id, is_correct)

    training_state.clear_feedback()

    training_config = config.get('training', {})
    passing_criteria = training_config.get('passing_criteria', {})

    if is_correct:
        #logger.info(f'User {username} (Session ID {session_id}) answered training question {instance_id} correctly')
        training_state.needs_retry = False

        # Check if user has passed based on min_correct
        min_correct = passing_criteria.get('min_correct', len(training_state.training_instance_ids))
        if training_state.get_correct_answer_count() >= min_correct:
            # User has passed training
            training_state.set_passed(True)
            logger.info(f'User {username} (Session ID {session_id}) - Training passed: {training_state.get_correct_answer_count()} correct answers')

            user_state = get_user_state_manager().get_user_state(username, session_id)
            get_user_state_manager().save_user_state(user_state)

            return redirect(url_for('training.training_summary'))

        # Move to next training question or complete training
        if training_state.advance_training_instance():
            # More questions available
            training_state.set_feedback(True, "Correct! Moving to next question.", "success")

            user_state = get_user_state_manager().get_user_state(username, session_id)
            get_user_state_manager().save_user_state(user_state)

            return redirect(url_for('training.training_page'))

        else:
            # All questions completed
            require_all = passing_criteria.get('require_all_correct', False)
            total_questions = len(training_state.get_training_instance_ids())

            # Check if user didn't get all correct
            if require_all and training_state.get_correct_answer_count() < total_questions:
                failed_message = "You did not answer all questions correctly and cannot continue."
                training_state.set_failed(True, failed_message)
                logger.info(f'User {username} (Session ID {session_id}) - Training failed: Not all questions were answered correctly')

                # Move to DONE phase (kick out)
                user_state.set_current_phase_and_page(UserPhase.DONE, "done")
                user_state = get_user_state_manager().get_user_state(username, session_id)
                get_user_state_manager().save_user_state(user_state)

                return redirect(url_for('done'))

            else:
                # Training completed successfully
                training_state.set_passed(True)
                logger.info(f'User {username} (Session ID {session_id}) - Training passed: {training_state.get_correct_answer_count()} correct answers')

                user_state = get_user_state_manager().get_user_state(username, session_id)
                get_user_state_manager().save_user_state(user_state)

                return redirect(url_for('training.training_summary'))

    else:
        #logger.info(f'User {username} (Session ID {session_id}) answered training question {instance_id} incorrectly')

        # Check if user should fail due to too many mistakes
        if training_state.should_fail_due_to_mistakes():
            failed_message = "You have exceeded the maximum number of allowed mistakes and cannot continue."
            training_state.set_failed(True, failed_message)
            logger.info(f'User {username} (Session ID {session_id}) - Training failed: exceeded max_mistakes ({training_state.max_mistakes}) on {instance_id}')

            # Move to DONE phase (kick out)
            user_state.set_current_phase_and_page(UserPhase.DONE, "done")
            user_state = get_user_state_manager().get_user_state(username, session_id)
            get_user_state_manager().save_user_state(user_state)

            return redirect(url_for('done'))

        # Check if user should fail due to too many mistakes on this question
        if training_state.should_fail_training_instance_due_to_mistakes(instance_id):
            failed_message = "You have exceeded the maximum number of allowed mistakes per question and cannot continue."
            training_state.set_failed(True, failed_message)
            logger.info(f'User {username} (Session ID {session_id}) - Training failed: exceeded max_mistakes_per_question ({training_state.max_mistakes_per_question}) on {instance_id}')

            # Move to DONE phase (kick out)
            user_state.set_current_phase_and_page(UserPhase.DONE, "done")
            user_state = get_user_state_manager().get_user_state(username, session_id)
            get_user_state_manager().save_user_state(user_state)

            return redirect(url_for('done'))

        # Get explanation for incorrect answer
        explanation = current_instance.get_data().get('explanation', "???")

        # Check if user should be allowed to retry
        if training_state.allow_retry:
            training_state.set_feedback(True, f"Incorrect. {explanation}", "error")
            training_state.needs_retry = True

            user_state = get_user_state_manager().get_user_state(username, session_id)
            get_user_state_manager().save_user_state(user_state)

            return redirect(url_for('training.training_page'))

        #else:
            """# No retry allowed - check failure action
            failure_action = training_config.get('failure_action', 'move_to_done')
            if failure_action == 'move_to_done':
                training_state.set_failed(True)
                logger.info(f'User {username} failed training - no retry allowed')
                user_state.set_current_phase_and_page(UserPhase.DONE, None)
                return render_template("training_failed.html",
                                       message="You answered incorrectly and retries are not allowed.",
                                       explanation=explanation,
                                       annotation_task_name=config.get("annotation_task_name", "Annotation Platform"),
                                       username=username)
            else:
                # Advance to next question even though wrong
                if user_state.advance_training_question():
                    next_instance = user_state.get_current_training_instance()
                    next_instance_text = next_instance.get_data().get('displayed_text', next_instance.get_data().get('text', ''))
                    next_instance_paper_title = next_instance.get_data().get('paper_title', "???")
                    next_instance_paper_abstract = next_instance.get_data().get('paper_abstract', "???")
                    training_state.set_feedback(True, f"Incorrect. {explanation} Moving to next question.", "warning")

                    user_state = get_user_state(username, session_id)
                    get_user_state_manager().save_user_state(user_state)
                    logger.debug(f"User state saved for {username}")

                    cur_phase, cur_page = user_state.current_phase_and_page
                    page_fname = get_user_state_manager().get_phase_html_fname(cur_phase, cur_page)

                    logger.debug("TRAINING render page point #4")
                    return render_template(page_fname,
                                           instance_text=next_instance_text,
                                           instance_id=next_instance.get_id(),
                                           paper_title=next_instance_paper_title,
                                           paper_abstract=next_instance_paper_abstract,
                                           feedback=training_state.feedback_message,
                                           feedback_type=training_state.feedback_type,
                                           show_feedback=training_state.show_feedback,
                                           allow_retry=training_state.allow_retry,
                                           current_question=current_question_num + 1,
                                           total_questions=total_questions,
                                           correct_count=training_state.get_correct_answer_count(),
                                           mistake_count=training_state.get_total_mistakes(),
                                           annotation_task_name=config.get("annotation_task_name", "Annotation Platform"),
                                           username=username)
                else:
                    # No more questions - check if passed
                    min_correct = passing_criteria.get('min_correct', total_questions)
                    if training_state.get_correct_answer_count() >= min_correct:
                        training_state.set_passed(True)
                        usm = get_user_state_manager()
                        usm.advance_phase(username, session_id)
                        return home()
                    else:
                        training_state.set_failed(True)
                        user_state.set_current_phase_and_page(UserPhase.DONE, None)
                        return render_template("training_failed.html",
                                               message="You did not meet the minimum correct answers requirement.",
                                               correct_count=training_state.get_correct_answer_count(),
                                               min_correct=min_correct,
                                               annotation_task_name=config.get("annotation_task_name", "Annotation Platform"),
                                               username=username)"""


# POST: User pressed Continue Button after Training is finished
@training_bp.route("/finish_training", methods=["POST"])
@phase_required(UserPhase.TRAINING)
def finish_training():
    """Called when user clicks 'Continue' after statistics page"""
    #logger.debug("=== finish_training STARTS ===")

    username = session['username']
    session_id = session["session_id"]
    logger.debug(f"User {username} (Session ID {session_id}) - Button finish_training clicked")

    user_state = get_user_state_manager().get_user_state(username, session_id)

    training_state = user_state.get_training_state()

    if training_state.passed:
        usm = get_user_state_manager()
        usm.advance_phase(username, session_id)

        user_state = usm.get_user_state(username, session_id)
        usm.save_user_state(user_state)
        #logger.debug(f"User {username} (Session ID {session_id}) state saved")

        return redirect(url_for("home"))


# GET: Training summary after all examples completed
@training_bp.route("/summary", methods=["GET"])
@phase_required(UserPhase.TRAINING)
def training_summary():
    #logger.debug("=== training_summary STARTS ===")

    username = session['username']
    session_id = session["session_id"]
    #logger.debug(f"User {username} (Session ID {session_id}) pressed finish_training button")

    user_state = get_user_state_manager().get_user_state(username, session_id)

    training_state = user_state.get_training_state()

    total_questions = len(training_state.get_training_instance_ids())
    current_question_num = training_state.get_current_training_instance_id() + 1

    if training_state.passed:
        return render_template("training_success.html",
                               current_question=current_question_num,
                               total_questions=total_questions,
                               correct_count=training_state.get_correct_answer_count(),
                               total_mistakes=training_state.get_total_mistakes(),
                               annotation_task_name=config.get("annotation_task_name", "Annotation Platform"),
                               username=username)
    else:
        return render_template("training_failed.html",
                               message="You have exceeded the maximum number of allowed mistakes and cannot continue.",
                               total_mistakes=training_state.get_total_mistakes(),
                               max_mistakes=training_state.max_mistakes,
                               annotation_task_name=config.get("annotation_task_name", "Annotation Platform"),
                               username=username)


def check_training_answer(user_answer: dict, correct_answers: dict) -> bool:
    """
    Check if the user's answer matches the correct answers.

    Handles different annotation types:
    - Radio/single select: string comparison
    - Multiselect/checkbox: set comparison (order-independent)
    - Likert/number: numeric comparison
    - Text: exact or fuzzy string match

    Args:
        user_answer: Dictionary of user's answers by schema name
        correct_answers: Dictionary of correct answers by schema name

    Returns:
        True if all answers are correct, False otherwise
    """
    for schema_name, correct_value in correct_answers.items():
        if schema_name not in user_answer:
            return False

        user_value = user_answer[schema_name]

        # Handle multiselect/checkbox (list comparison)
        if isinstance(correct_value, list):
            if isinstance(user_value, list):
                if set(user_value) != set(correct_value):
                    return False
            elif isinstance(user_value, str):
                # Single value submitted, check if it's the only correct answer
                if len(correct_value) != 1 or user_value not in correct_value:
                    return False
            else:
                return False
        # Handle numeric values
        elif isinstance(correct_value, (int, float)):
            try:
                if float(user_value) != float(correct_value):
                    return False
            except (ValueError, TypeError):
                return False
        # Handle string comparison (radio, text)
        else:
            if str(user_value).strip().lower() != str(correct_value).strip().lower():
                return False

    return True
