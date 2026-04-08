"""
Admin Dashboard Module

This module provides comprehensive admin functionality for the annotation platform,
including dashboard data generation, timing analysis, and configuration management.

The admin dashboard offers:
- Real-time overview of annotation progress and statistics
- Detailed annotator performance metrics and timing analysis
- Instance-level annotation tracking and disagreement analysis
- Configuration management and system state monitoring
- Question and annotation scheme analysis
- User progress tracking and completion statistics
- Comprehensive annotation history tracking and suspicious activity detection
- Performance metrics and quality assurance monitoring
- Session tracking and behavioral analysis

Key Components:
- AdminDashboard: Main class for admin functionality
- AnnotatorTimingData: Data class for annotator timing information
- InstanceData: Data class for instance information and statistics
- Dashboard data generation and analysis functions
- Configuration update and management functions
- AnnotationHistoryAnalyzer: Advanced history analysis and suspicious activity detection

The dashboard provides insights into:
- Overall annotation progress and completion rates
- Individual annotator performance and efficiency
- Annotation quality through disagreement analysis
- System configuration and operational status
- Real-time monitoring of active annotation sessions
- Fine-grained annotation timing and behavioral patterns
- Suspicious activity detection and quality assurance
- Session-based performance analysis

Access Control:
- Admin access is controlled via API key authentication
- Debug mode allows admin access without API key
- All admin endpoints require proper authentication
"""

import logging
import datetime
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict, Counter
from dataclasses import dataclass
from dash import html, dash_table

from potato.flask_server import config, logger, get_user_state_manager, get_item_state_manager, UserPhase, test_setup
from potato.quality_control import get_quality_control_manager

#test_setup()

@dataclass
class AnnotatorTimingData:
    """
    Data class for annotator timing information.

    This class encapsulates timing metrics for individual annotators,
    including total annotations, working time, and performance statistics.
    Now enhanced with annotation history tracking and suspicious activity detection.
    """
    user_id: str
    total_annotations: int
    total_seconds: int
    average_seconds_per_annotation: float
    last_activity: Optional[datetime.datetime]
    current_instance_time: Optional[int]
    annotations_per_hour: float
    phase: str
    has_assignments: bool
    remaining_assignments: bool

    # Annotation history metrics
    total_actions: int
    average_action_time_ms: float
    fastest_action_time_ms: int
    slowest_action_time_ms: int
    actions_per_minute: float
    suspicious_score: float
    suspicious_level: str
    fast_actions_count: int
    burst_actions_count: int
    session_start_time: Optional[datetime.datetime]
    current_session_duration_minutes: Optional[float]
    recent_actions_count: int  # Actions in last 5 minutes

    # Training metrics
    training_completed: bool
    training_correct_answers: int
    training_total_attempts: int
    training_pass_rate: float
    training_current_question: int
    training_total_questions: int

@dataclass
class InstanceData:
    """
    Data class for instance information.

    This class encapsulates information about annotation instances,
    including annotation counts, disagreement scores, and annotator lists.
    """
    id: str
    text: str
    displayed_text: str
    annotation_count: int
    completion_percentage: float
    most_frequent_label: Optional[str]
    label_disagreement: float
    annotators: List[str]
    num_ai_instance: int
    average_time_per_annotation: Optional[float]

class AdminDashboard:
    """
    Main class for admin dashboard functionality.

    This class provides comprehensive admin features including dashboard
    data generation, timing analysis, configuration management, and
    system monitoring capabilities.
    """

    def __init__(self):
        """Initialize the admin dashboard."""
        self.logger = logging.getLogger(__name__)

    def get_dashboard_overview(self) -> Dict[str, Any]:
        """
        Get comprehensive dashboard overview data.

        This method generates a complete overview of the annotation system,
        including user statistics, annotation progress, and system configuration.

        Returns:
            Dict containing overview statistics with the following structure:
            - overview: User counts, annotation counts, completion percentages
            - config: System configuration and settings

        Side Effects:
            - Logs errors if data generation fails
        """
        try:
            usm = get_user_state_manager()
            ism = get_item_state_manager()

            # Get all users and their states
            user_session_ids = usm.get_user_session_ids()

            phases_config = config.get("phases", {})
            page_order = ["login"] + phases_config.get("order", []) + ["done"]

            pages2nuser = {page: 0 for page in page_order}

            n_active_users = 0
            n_completed_users = 0
            n_total_annotations = 0

            # Calculate user statistics
            for username, session_ids in user_session_ids.items():
                for session_id in session_ids:
                    user_state = usm.get_user_state(username, session_id)
                    if user_state:
                        phase, page = user_state.get_current_phase_and_page()

                        # count page active user
                        if page in pages2nuser:
                            pages2nuser[page] += 1

                        is_finished = user_state.get_current_phase() == UserPhase.DONE
                        if not is_finished:
                            n_active_users += 1
                        else:
                            n_completed_users += 1

                        n_total_annotations += user_state.get_annotation_count()

            items_with_annotations = 0
            total_assignments = 0

            for item_id, item in ism.item_id_to_item.items():
                annotators = ism.get_annotators_for_item(item_id)
                if annotators:
                    items_with_annotations += 1
                    total_assignments += len(annotators)

            # Calculate completion percentages
            total_items = len(ism.item_id_to_item)
            completion_percentage = (items_with_annotations / total_items * 100) if total_items > 0 else 0

            return {
                "overview": {
                    "total_users": len(user_session_ids),
                    "active_users": n_active_users,
                    "completed_users": n_completed_users,
                    "total_annotations": n_total_annotations,
                    "total_items": total_items,
                    "items_with_annotations": items_with_annotations,
                    "completion_percentage": round(completion_percentage, 1),
                    "total_assignments": total_assignments,
                },
                "config": {
                    "annotation_task_name": config.get("annotation_task_name", "Unknown"),
                    "max_annotations_per_user": config.get("max_annotations_per_user", "Unlimited"),
                    "max_annotations_per_item": config.get("max_annotations_per_item", "Unlimited"),
                    "assignment_strategy": config.get("assignment_strategy", "fixed_order"),
                    "debug_mode": config.get("debug", False)
                }
            }

        except Exception as e:
            self.logger.error(f"Error getting dashboard overview: {e}")
            return {"error": f"Failed to get dashboard overview: {str(e)}"}, 500

    # annotator dashboard
    def get_dash_annotator_overview_data(self):
        annotator_overview_data = []
        try:
            usm = get_user_state_manager()

            # Get all users and their states
            user_session_ids = usm.get_user_session_ids()

            # Calculate user statistics
            for username, session_ids in user_session_ids.items():
                for session_id in session_ids:
                    user_state = usm.get_user_state(username, session_id)
                    if user_state:
                        annotator_data = {
                            "User": username,
                            "Session ID": session_id
                        }

                        # get page start times
                        phases_config = config.get("phases", {})
                        page_order = ["login"] + phases_config.get("order", [])

                        user_page_start_times = {page: format_datetime(st) for k, v in user_state.phase_page_start_times.items() for page, st in v.items()}
                        page2start_times = {page.capitalize(): user_page_start_times.get(page, "") for page in page_order}

                        annotator_data.update(page2start_times)

                        is_finished = user_state.get_current_phase() == UserPhase.DONE
                        num_annotated = user_state.get_annotation_count()

                        training_state = user_state.get_training_state()
                        if training_state:
                            if training_state.is_passed():
                                training_status = "passed"
                            elif training_state.is_failed():
                                training_status = "failed"
                            else:
                                training_status = "active"
                        else:
                            training_status = "not started"

                        last_activity = user_state.last_activity_time
                        if last_activity:
                            last_activity = format_datetime(last_activity)
                        else:
                            last_activity = ""

                        annotator_data.update(
                            {"Training Status": training_status,
                             "# Annotated": num_annotated,
                             "Last Activity": last_activity,
                             "Done": is_finished
                             }
                        )
                        annotator_overview_data.append(annotator_data)

            dash_layout = html.Div([
                dash_table.DataTable(
                    id='annotator_overview-table',
                    data=annotator_overview_data,
                    style_data_conditional=[{
                        'if': {'row_index': 'odd'},
                        'backgroundColor': 'rgb(220, 220, 220)'}],
                    style_header=dict(backgroundColor="#003c78", color="white"),
                    sort_action='native'
                )
            ])

            return dash_layout

        except Exception as e:
            self.logger.error(f"Error getting annotator overview: {e}")
            return {"error": f"Failed to get annotator overview: {str(e)}"}, 500

    # annotation phase dashboards
    def get_dash_annotation_overview_data(self):
        annotation_overview_data = []
        try:
            usm = get_user_state_manager()

            # Get all users and their states
            user_session_ids = usm.get_user_session_ids()

            # Calculate user statistics
            for username, session_ids in user_session_ids.items():
                for session_id in session_ids:
                    user_state = usm.get_user_state(username, session_id)
                    if user_state:
                        annotator_data = {
                            "User": username,
                            "Session ID": session_id
                        }

                        # get annotation start
                        start_time_dt = user_state.phase_page_start_times.get(UserPhase.ANNOTATION, {}).get("annotation")
                        if start_time_dt:
                            start_time = format_datetime(start_time_dt)
                        else:
                            continue # do not include if not already in annotation phase

                        #finished_annotation = any([phase == UserPhase.ANNOTATION for phase, _ in user_state.completed_phase_and_pages])
                        status = "done" if user_state.get_current_phase() == UserPhase.DONE else "active"

                        num_annotated = user_state.get_annotation_count()

                        last_activity_dt = user_state.last_activity_time
                        if last_activity_dt:
                            last_activity = format_datetime(last_activity_dt)
                        else:
                            last_activity = ""

                        if last_activity_dt and start_time_dt:
                            duration_td = last_activity_dt - start_time_dt
                            duration = format_timedelta(duration_td)
                        else:
                            duration = format_timedelta(datetime.timedelta(seconds=0))

                        if num_annotated > 0:
                            avg_duration_per_instance_td = duration_td / num_annotated
                            avg_duration_per_instance = format_timedelta(avg_duration_per_instance_td)
                        else:
                            avg_duration_per_instance = format_timedelta(datetime.timedelta(seconds=0))

                        if num_annotated > 0:
                            bd = user_state.instance_id_to_behavioral_data
                            n_annotation_changes = 0
                            for iid, ibd in bd.items():
                                if len(ibd.annotation_changes) > 0:
                                    n_annotation_changes += (len(ibd.annotation_changes) - 1)

                            avg_annotation_changes = n_annotation_changes / num_annotated
                        else:
                            avg_annotation_changes = 0

                        annotator_data.update(
                            {
                                "Started": start_time,
                                "Status": status,
                                "# Annotated": num_annotated,
                                "Duration": duration,
                                "Last Activity": last_activity,
                                "Avg. Duration per Instance": avg_duration_per_instance,
                                "Avg. Annotation Changes": avg_annotation_changes
                             }
                        )
                        annotation_overview_data.append(annotator_data)

            dash_layout = html.Div([
                dash_table.DataTable(
                    id='annotation_overview-table',
                    data=annotation_overview_data,
                    style_data_conditional=[{
                        'if': {'row_index': 'odd'},
                        'backgroundColor': 'rgb(220, 220, 220)'}],
                    style_header=dict(backgroundColor="#003c78", color="white"),
                    sort_action='native'
                )
            ])

            return dash_layout

        except Exception as e:
            self.logger.error(f"Error getting annotation overview: {e}")
            return {"error": f"Failed to get annotation overview: {str(e)}"}, 500

    def get_dash_annotation_annotator_view_data(self):
        annotation_annotator_view_data = []
        try:
            usm = get_user_state_manager()

            # Get all users and their states
            user_session_ids = usm.get_user_session_ids()

            # Calculate user statistics
            for username, session_ids in user_session_ids.items():
                for session_id in session_ids:
                    user_state = usm.get_user_state(username, session_id)
                    if user_state:
                        for iid, bd in user_state.instance_id_to_behavioral_data.items():
                            if len(bd.annotation_changes) > 0:
                                annotator_instance_data = {
                                    "User": username,
                                    "Session ID": session_id,
                                    "Instance ID": iid,
                                }

                                last_annotation = "not yet annotated"
                                n_annotation_changes = 0
                                annotation_changes = bd.annotation_changes
                                if len(annotation_changes) > 0:
                                    last_annotation = annotation_changes[-1].new_value
                                    n_annotation_changes = len(annotation_changes) - 1

                                duration = datetime.timedelta(seconds=0)
                                loaded_ts = None
                                for interaction in bd.interactions:
                                    if loaded_ts is None and interaction.event_type == "navigation" and interaction.target == "instance_load":
                                        loaded_ts = interaction.client_timestamp

                                    if loaded_ts is not None and interaction.event_type == "navigation" and (interaction.target == "next" or interaction.target == "prev"):
                                        ts = interaction.client_timestamp
                                        stayed_for = ts - loaded_ts
                                        duration += stayed_for
                                        loaded_ts = None

                                duration = format_timedelta(duration)

                                duration_hidden = datetime.timedelta(seconds=0)
                                hidden_ts = None
                                if len(bd.interactions) > 0:
                                    for interaction in bd.interactions:
                                        if hidden_ts is None and interaction.event_type == "page_hidden":
                                            hidden_ts = interaction.client_timestamp
                                        elif hidden_ts is not None and interaction.event_type == "page_visible":
                                            visible_ts = interaction.client_timestamp
                                            duration_hidden += (visible_ts - hidden_ts)
                                            hidden_ts = None
                                        elif hidden_ts is not None and interaction.event_type == "navigation" and interaction.target == "instance_load":
                                            visible_ts = interaction.client_timestamp
                                            duration_hidden += (visible_ts - hidden_ts)
                                            hidden_ts = None

                                duration_hidden = format_timedelta(duration_hidden)

                                annotator_instance_data.update(
                                    {
                                        "Annotation": last_annotation,
                                        "# Annotation Changes": n_annotation_changes,
                                        "Duration": duration,
                                        "Duration Hidden": duration_hidden
                                    }
                                )

                                annotation_annotator_view_data.append(annotator_instance_data)

            dash_layout = html.Div([
                dash_table.DataTable(
                    id='annotation_annotator_view-table',
                    data=annotation_annotator_view_data,
                    columns=[{"name": i, 'id': i} for i in ["User", "Session ID", "Instance ID", "Annotation", "# Annotation Changes", "Duration", "Duration Hidden"]],
                    style_data_conditional=[{
                        'if': {'row_index': 'odd'},
                        'backgroundColor': 'rgb(220, 220, 220)'}],
                    style_header=dict(backgroundColor="#003c78", color="white"),
                    sort_action='native',
                    filter_action='native',
                    filter_options={"placeholder_text": "", "case": "insensitive"},
                )
            ])

            return dash_layout

        except Exception as e:
            self.logger.error(f"Error getting annotation annotator view: {e}")
            return {"error": f"Failed to get annotation annotator view: {str(e)}"}, 500

    # training phase dashboards
    def get_dash_training_overview_data(self):
        training_overview_data = []
        try:
            usm = get_user_state_manager()

            # Get all users and their states
            user_session_ids = usm.get_user_session_ids()

            # Get user data
            for username, session_ids in user_session_ids.items():
                for session_id in session_ids:
                    user_state = usm.get_user_state(username, session_id)
                    if user_state:
                        training_state = user_state.get_training_state()
                        if training_state:
                            annotator_data = {
                                "User": username,
                                "Session ID": session_id
                            }

                            # get training start
                            start_time_dt = user_state.phase_page_start_times.get(UserPhase.TRAINING, {}).get("training")
                            if start_time_dt:
                                start_time = format_datetime(start_time_dt)
                            else:
                                continue

                            if training_state.is_passed():
                                status = "passed"
                            elif training_state.is_failed():
                                status = "failed"
                            else:
                                status = "active"

                            n_correct = training_state.total_correct
                            n_mistakes = training_state.total_mistakes
                            n_attempts = training_state.total_attempts

                            last_activity_dt = training_state.last_activity_time
                            if last_activity_dt:
                                last_activity = format_datetime(last_activity_dt)
                            else:
                                last_activity = ""

                            if last_activity_dt and start_time_dt:
                                duration_td = last_activity_dt - start_time_dt
                                duration = format_timedelta(duration_td)
                            else:
                                duration = format_timedelta(datetime.timedelta(seconds=0))

                            num_annotated = len(training_state.training_instance_id_to_label_to_value)

                            if num_annotated > 0:
                                avg_duration_per_instance_td = duration_td / num_annotated
                                avg_duration_per_instance = format_timedelta(avg_duration_per_instance_td)
                            else:
                                avg_duration_per_instance = format_timedelta(datetime.timedelta(seconds=0))

                            annotator_data.update(
                                {
                                    "Started": start_time,
                                    "Status": status,
                                    "# Correct": n_correct,
                                    "# Mistakes": n_mistakes,
                                    "# Attempts": n_attempts,
                                    "Duration": duration,
                                    "Last Activity": last_activity,
                                    "Avg. Duration per Instance": avg_duration_per_instance,
                                }
                            )
                            training_overview_data.append(annotator_data)

        except Exception as e:
            self.logger.exception(f"Error getting training overview: {e}")
            training_overview_data = [{"Error": e}]

        dash_layout = html.Div([
            dash_table.DataTable(
                id='training_overview-table',
                data=training_overview_data,
                style_data_conditional=[{
                    'if': {'row_index': 'odd'},
                    'backgroundColor': 'rgb(220, 220, 220)'}],
                style_header=dict(backgroundColor="#003c78", color="white"),
                sort_action='native'
            )
        ])

        return dash_layout

    def get_dash_training_annotator_view_data(self):
        training_annotator_view_data = []
        try:
            usm = get_user_state_manager()

            # Get all users and their states
            user_session_ids = usm.get_user_session_ids()

            # Calculate user statistics
            for username, session_ids in user_session_ids.items():
                for session_id in session_ids:
                    user_state = usm.get_user_state(username, session_id)
                    if user_state:
                        training_state = user_state.get_training_state()
                        if training_state:
                            for iid, bd in training_state.training_instance_id_to_behavioral_data.items():
                                annotator_instance_data = {
                                    "User": username,
                                    "Session ID": session_id,
                                    "Instance ID": iid,
                                }

                                last_annotation = "not yet annotated"
                                annotations_changes = bd.annotation_changes
                                if len(annotations_changes) > 0:
                                    last_annotation = annotations_changes[-1].new_value

                                stats = training_state.training_instance_id_stats.get(iid, {})
                                is_correct = stats.get("correct", "?")
                                n_attempts = stats.get("attempts", "?")

                                if bd.session_start and bd.session_end:
                                    duration = format_timedelta(bd.session_end - bd.session_start)
                                else:
                                    duration = format_timedelta(datetime.timedelta(seconds=0))

                                duration_hidden = datetime.timedelta(seconds=0)
                                hidden_ts = None
                                if len(bd.interactions) > 0:
                                    for i in bd.interactions:
                                        if hidden_ts is None and i.event_type == "page_hidden":
                                            hidden_ts = i.client_timestamp
                                        elif hidden_ts is not None and i.event_type == "page_visible":
                                            visible_ts = i.client_timestamp
                                            duration_hidden += (visible_ts - hidden_ts)
                                            hidden_ts = None
                                        elif hidden_ts is not None and i.event_type == "navigation" and i.target == "instance_load":
                                            visible_ts = i.client_timestamp
                                            duration_hidden += (visible_ts - hidden_ts)
                                            hidden_ts = None

                                duration_hidden = format_timedelta(duration_hidden)

                                annotator_instance_data.update(
                                    {
                                        "Annotation": last_annotation,
                                        "Correct": is_correct,
                                        "# Attempts": n_attempts,
                                        "Duration": duration,
                                        "Duration Hidden": duration_hidden
                                    }
                                )

                                training_annotator_view_data.append(annotator_instance_data)

        except Exception as e:
            self.logger.exception(f"Error getting training annotator view: {e}")
            training_annotator_view_data = [{"Error": e}]

        dash_layout = html.Div([
            dash_table.DataTable(
                id='training_annotator_view-table',
                data=training_annotator_view_data,
                columns=[{"name": i, 'id': i} for i in ["User", "Session ID", "Instance ID", "Annotation", "Correct", "# Attempts", "Duration", "Duration Hidden"]],
                style_data_conditional=[{
                    'if': {'row_index': 'odd'},
                    'backgroundColor': 'rgb(220, 220, 220)'}],
                style_header=dict(backgroundColor="#003c78", color="white"),
                sort_action='native',
                filter_action='native',
                filter_options={"placeholder_text": "", "case": "insensitive"},
            )
        ])

        return dash_layout

    def get_dash_annotation_instance_view_data(self):
        return self.get_dash_annotator_overview_data()

    def get_dash_training_instance_view_data(self):
        return self.get_dash_annotator_overview_data()

    def get_annotators_data(self) -> Dict[str, Any]:
        """
        Get detailed annotator data including timing information.

        Returns:
            Dict containing annotator data with timing analysis
        """

        try:
            usm = get_user_state_manager()

            # Get all users and their states
            user_session_ids = usm.get_user_session_ids()
            logger.debug(f"user_session_ids: {user_session_ids}")

            annotators_data = []

            for username, session_ids in user_session_ids.items():
                for session_id in session_ids:
                    #logger.debug(f"username: {username}")
                    #logger.debug(f"session_id: {session_id}")
                    user_state = usm.get_user_state(username, session_id)

                    if user_state:
                        timing_data = self._get_annotator_timing_data(user_state)
                        #logger.debug(f"timing_data: {timing_data}")
                        if timing_data:

                            # Format total working time
                            total_working_time = timing_data.total_seconds
                            hours = total_working_time // 3600
                            minutes = (total_working_time % 3600) // 60
                            total_working_time = f"{hours}h {minutes}m"


                            annotators_data.append({
                                "user_id": timing_data.user_id,
                                "session_id": session_id,
                                "total_annotations": timing_data.total_annotations,
                                "completion_percentage": self._calculate_completion_percentage(user_state),
                                "total_seconds": timing_data.total_seconds,
                                "total_working_time": total_working_time,
                                "average_seconds_per_annotation": timing_data.average_seconds_per_annotation,
                                "annotations_per_hour": f"{timing_data.annotations_per_hour:.2f}",
                                "phase": timing_data.phase,
                                "has_assignments": timing_data.has_assignments,
                                "remaining_assignments": timing_data.remaining_assignments,
                                "last_activity": timing_data.last_activity.isoformat() if timing_data.last_activity else None,
                                "current_instance_time": timing_data.current_instance_time,

                                # NEW: Annotation history metrics
                                "total_actions": timing_data.total_actions,
                                "average_action_time_ms": timing_data.average_action_time_ms,
                                "fastest_action_time_ms": timing_data.fastest_action_time_ms if timing_data.fastest_action_time_ms != float('inf') else None,
                                "slowest_action_time_ms": timing_data.slowest_action_time_ms,
                                "actions_per_minute": timing_data.actions_per_minute,
                                "suspicious_score": timing_data.suspicious_score,
                                "suspicious_level": timing_data.suspicious_level,
                                "fast_actions_count": timing_data.fast_actions_count,
                                "burst_actions_count": timing_data.burst_actions_count,
                                "session_start_time": timing_data.session_start_time.isoformat() if timing_data.session_start_time else None,
                                "current_session_duration_minutes": timing_data.current_session_duration_minutes,
                                "recent_actions_count": timing_data.recent_actions_count,

                                # Training metrics
                                "training_completed": timing_data.training_completed,
                                "training_correct_answers": timing_data.training_correct_answers,
                                "training_total_attempts": timing_data.training_total_attempts,
                                "training_pass_rate": round(timing_data.training_pass_rate, 2),
                                "training_current_question": timing_data.training_current_question,
                                "training_total_questions": timing_data.training_total_questions
                            })

            # Sort by suspicious score (highest first)
            annotators_data.sort(key=lambda x: x["suspicious_score"], reverse=True)

            #logger.debug(f"annotators_data: {annotators_data}")

            return {
                "total_annotators": len(user_session_ids),
                "annotators": annotators_data,
                "summary": {
                    "high_suspicious_count": len([a for a in annotators_data if a["suspicious_level"] in ["High", "Very High"]]),
                    "medium_suspicious_count": len([a for a in annotators_data if a["suspicious_level"] == "Medium"]),
                    "low_suspicious_count": len([a for a in annotators_data if a["suspicious_level"] == "Low"]),
                    "normal_count": len([a for a in annotators_data if a["suspicious_level"] == "Normal"]),
                    "average_suspicious_score": sum(a["suspicious_score"] for a in annotators_data) / len(annotators_data) if annotators_data else 0
                }
            }

        except Exception as e:
            self.logger.error(f"Error getting annotators data: {e}")
            return {"error": f"Failed to get annotators data: {str(e)}"}, 500

    def get_instances_data(self, page: int = 1, page_size: int = 25,
                          sort_by: str = "annotation_count", sort_order: str = "desc",
                          filter_completion: Optional[str] = None) -> Dict[str, Any]:
        """
        Get paginated instances data with sorting and filtering.

        Args:
            page: Page number (1-based)
            page_size: Number of instances per page
            sort_by: Field to sort by (annotation_count, completion_percentage, disagreement, id)
            sort_order: Sort order (asc, desc)
            filter_completion: Filter by completion status (completed, incomplete, all)

        Returns:
            Dict containing paginated instances data
        """
        try:
            ism = get_item_state_manager()
            items = ism.items()

            # Convert items to InstanceData objects
            instances_data = []
            for item in items:
                item_id = item.get_id()
                annotators = ism.get_annotators_for_item(item_id)
                annotation_count = len(annotators) if annotators else 0

                # Calculate completion percentage
                max_annotations = config.get("max_annotations_per_item", -1)
                if max_annotations > 0:
                    completion_percentage = min(100, (annotation_count / max_annotations) * 100)
                else:
                    completion_percentage = 100 if annotation_count > 0 else 0

                # Calculate most frequent label and disagreement
                most_frequent_label, disagreement = self._calculate_label_statistics(item_id)

                # Calculate average time per annotation
                avg_time = self._calculate_average_time_per_annotation(item_id)

                instance_data = InstanceData(
                    id=item_id,
                    text=item.get_text(),
                    displayed_text=item.get_displayed_text(),
                    annotation_count=annotation_count,
                    completion_percentage=completion_percentage,
                    most_frequent_label=most_frequent_label,
                    label_disagreement=disagreement,
                    annotators=list(annotators) if annotators else [],
                    average_time_per_annotation=avg_time,
                    num_ai_instance=0 #self._calculate_total_instance_ai(item_id)
                )
                instances_data.append(instance_data)

            # Apply filters
            if filter_completion == "completed":
                instances_data = [i for i in instances_data if i.completion_percentage >= 100]
            elif filter_completion == "incomplete":
                instances_data = [i for i in instances_data if i.completion_percentage < 100]

            # Apply sorting
            reverse = sort_order.lower() == "desc"
            if sort_by == "annotation_count":
                instances_data.sort(key=lambda x: x.annotation_count, reverse=reverse)
            elif sort_by == "completion_percentage":
                instances_data.sort(key=lambda x: x.completion_percentage, reverse=reverse)
            elif sort_by == "disagreement":
                instances_data.sort(key=lambda x: x.label_disagreement, reverse=reverse)
            elif sort_by == "id":
                instances_data.sort(key=lambda x: x.id, reverse=reverse)
            elif sort_by == "average_time":
                instances_data.sort(key=lambda x: x.average_time_per_annotation or 0, reverse=reverse)

            # Apply pagination
            total_instances = len(instances_data)
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            paginated_instances = instances_data[start_idx:end_idx]

            # Convert to serializable format
            serialized_instances = []
            for instance in paginated_instances:
                serialized_instances.append({
                    "id": instance.id,
                    "text": instance.text[:100] + "..." if len(instance.text) > 100 else instance.text,
                    "displayed_text": instance.displayed_text[:100] + "..." if len(instance.displayed_text) > 100 else instance.displayed_text,
                    "annotation_count": instance.annotation_count,
                    "completion_percentage": round(instance.completion_percentage, 1),
                    "most_frequent_label": instance.most_frequent_label,
                    "label_disagreement": round(instance.label_disagreement, 2),
                    "annotators": instance.annotators,
                    "num_ai_instance": instance.num_ai_instance,
                    "average_time_per_annotation": self._format_seconds(instance.average_time_per_annotation) if instance.average_time_per_annotation else None
                })

            return {
                "instances": serialized_instances,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_instances": total_instances,
                    "total_pages": (total_instances + page_size - 1) // page_size,
                    "has_next": end_idx < total_instances,
                    "has_prev": page > 1
                },
                "summary": {
                    "completed_instances": len([i for i in instances_data if i.completion_percentage >= 100]),
                    "incomplete_instances": len([i for i in instances_data if i.completion_percentage < 100]),
                    "average_annotations_per_instance": round(sum(i.annotation_count for i in instances_data) / len(instances_data), 1) if instances_data else 0,
                    "average_disagreement": round(sum(i.label_disagreement for i in instances_data) / len(instances_data), 2) if instances_data else 0
                }
            }

        except Exception as e:
            self.logger.error(f"Error getting instances data: {e}")
            return {"error": f"Failed to get instances data: {str(e)}"}, 500

    def update_config(self, config_updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update system configuration.

        Args:
            config_updates: Dictionary of configuration updates

        Returns:
            Dict containing update result
        """
        try:
            # Validate and apply updates
            updated_fields = []

            for key, value in config_updates.items():
                if key in ["max_annotations_per_user", "max_annotations_per_item"]:
                    if isinstance(value, int) and value >= -1:
                        config[key] = value
                        updated_fields.append(key)
                    else:
                        return {"error": f"Invalid value for {key}: must be integer >= -1"}, 400

                elif key == "assignment_strategy":
                    valid_strategies = ["random", "fixed_order", "least_annotated", "max_diversity", "active_learning", "llm_confidence"]
                    if value in valid_strategies:
                        config[key] = value
                        updated_fields.append(key)
                    else:
                        return {"error": f"Invalid assignment strategy: {value}"}, 400

            return {
                "status": "success",
                "message": f"Updated configuration fields: {', '.join(updated_fields)}",
                "updated_fields": updated_fields
            }

        except Exception as e:
            self.logger.error(f"Error updating config: {e}")
            return {"error": f"Failed to update config: {str(e)}"}, 500

    def _analyze_annotation_scheme(self, annotation_type: str, scheme: dict,
                                 all_annotations: list, item_annotations: dict) -> dict:
        """
        Analyze annotations based on their type and generate appropriate visualizations.
        """
        if not all_annotations:
            return {"error": "No annotations found"}

        analysis = {
            "type": annotation_type,
            "total_count": len(all_annotations)
        }

        if annotation_type in ["radio", "select"]:
            # Categorical data - show histogram
            label_counts = Counter(all_annotations)
            labels = scheme.get("labels", [])

            analysis.update({
                "visualization_type": "histogram",
                "data": {
                    "labels": labels,
                    "counts": [label_counts.get(label, 0) for label in labels],
                    "percentages": [round(label_counts.get(label, 0) / len(all_annotations) * 100, 1)
                                  for label in labels]
                },
                "most_common": label_counts.most_common(1)[0] if label_counts else None,
                "agreement_score": self._calculate_agreement_score(item_annotations)
            })

        elif annotation_type == "multiselect":
            # Multi-label data - show label frequency and co-occurrence
            label_counts = Counter()
            co_occurrence = defaultdict(int)
            labels = scheme.get("labels", [])

            for annotations in item_annotations.values():
                if isinstance(annotations, list):
                    # Count individual labels
                    for annotation in annotations:
                        if isinstance(annotation, list):
                            for label in annotation:
                                label_counts[label] += 1

                    # Count co-occurrences
                    for i, annotation1 in enumerate(annotations):
                        if isinstance(annotation1, list):
                            for j, annotation2 in enumerate(annotations):
                                if i != j and isinstance(annotation2, list):
                                    for label1 in annotation1:
                                        for label2 in annotation2:
                                            if label1 < label2:
                                                co_occurrence[(label1, label2)] += 1

            analysis.update({
                "visualization_type": "multiselect_analysis",
                "data": {
                    "labels": labels,
                    "counts": [label_counts.get(label, 0) for label in labels],
                    "percentages": [round(label_counts.get(label, 0) / len(item_annotations) * 100, 1)
                                  for label in labels],
                    "co_occurrence": dict(co_occurrence)
                },
                "most_common": label_counts.most_common(3) if label_counts else [],
                "average_labels_per_item": round(sum(len(ann) if isinstance(ann, list) else 1
                                                    for anns in item_annotations.values()
                                                    for ann in anns) / len(all_annotations), 2)
            })

        elif annotation_type in ["likert", "number", "slider"]:
            # Numeric data - show distribution and statistics
            numeric_values = []
            for value in all_annotations:
                try:
                    if isinstance(value, (int, float)):
                        numeric_values.append(float(value))
                    elif isinstance(value, str) and value.replace('.', '').replace('-', '').isdigit():
                        numeric_values.append(float(value))
                except (ValueError, TypeError):
                    continue

            if numeric_values:
                analysis.update({
                    "visualization_type": "distribution",
                    "data": {
                        "values": numeric_values,
                        "bins": self._create_histogram_bins(numeric_values, scheme),
                        "statistics": {
                            "mean": round(sum(numeric_values) / len(numeric_values), 2),
                            "median": round(sorted(numeric_values)[len(numeric_values)//2], 2),
                            "min": min(numeric_values),
                            "max": max(numeric_values),
                            "std": round((sum((x - sum(numeric_values)/len(numeric_values))**2
                                            for x in numeric_values) / len(numeric_values))**0.5, 2)
                        }
                    },
                    "range": scheme.get("min", 0) if "min" in scheme else None,
                    "max": scheme.get("max", 10) if "max" in scheme else None
                })
            else:
                analysis["error"] = "No valid numeric values found"

        elif annotation_type == "text":
            # Text data - show length distribution and common patterns
            text_lengths = []
            word_counts = []
            common_words = Counter()

            for value in all_annotations:
                if isinstance(value, str) and value.strip():
                    text_lengths.append(len(value))
                    words = value.lower().split()
                    word_counts.append(len(words))
                    common_words.update(words)

            if text_lengths:
                analysis.update({
                    "visualization_type": "text_analysis",
                    "data": {
                        "lengths": text_lengths,
                        "word_counts": word_counts,
                        "common_words": common_words.most_common(10),
                        "statistics": {
                            "avg_length": round(sum(text_lengths) / len(text_lengths), 1),
                            "avg_words": round(sum(word_counts) / len(word_counts), 1),
                            "min_length": min(text_lengths),
                            "max_length": max(text_lengths),
                            "empty_responses": len([v for v in all_annotations
                                                  if not isinstance(v, str) or not v.strip()])
                        }
                    }
                })
            else:
                analysis["error"] = "No valid text responses found"

        elif annotation_type == "span":
            # Span data - show coverage and overlap statistics
            span_counts = []
            total_spans = 0

            for annotations in item_annotations.values():
                if isinstance(annotations, list):
                    for annotation in annotations:
                        if isinstance(annotation, list):
                            span_counts.append(len(annotation))
                            total_spans += len(annotation)

            if span_counts:
                analysis.update({
                    "visualization_type": "span_analysis",
                    "data": {
                        "span_counts": span_counts,
                        "total_spans": total_spans,
                        "statistics": {
                            "avg_spans_per_item": round(sum(span_counts) / len(span_counts), 2),
                            "items_with_spans": len([c for c in span_counts if c > 0]),
                            "max_spans": max(span_counts) if span_counts else 0,
                            "min_spans": min(span_counts) if span_counts else 0
                        }
                    }
                })
            else:
                analysis["error"] = "No valid span annotations found"

        else:
            analysis["error"] = f"Unsupported annotation type: {annotation_type}"

        return analysis

    def _calculate_agreement_score(self, item_annotations: dict) -> float:
        """Calculate agreement score for categorical annotations."""
        if not item_annotations:
            return 0.0

        agreement_scores = []
        for annotations in item_annotations.values():
            if len(annotations) > 1:
                # Calculate percentage of most common annotation
                counter = Counter(annotations)
                most_common_count = counter.most_common(1)[0][1]
                agreement_scores.append(most_common_count / len(annotations))

        return round(sum(agreement_scores) / len(agreement_scores) * 100, 1) if agreement_scores else 0.0

    def _create_histogram_bins(self, values: list, scheme: dict) -> dict:
        """Create histogram bins for numeric data."""
        if not values:
            return {"bins": [], "counts": []}

        min_val = scheme.get("min", min(values))
        max_val = scheme.get("max", max(values))

        # Create 10 bins
        bin_size = (max_val - min_val) / 10
        bins = [min_val + i * bin_size for i in range(11)]
        counts = [0] * 10

        for value in values:
            bin_index = min(int((value - min_val) / bin_size), 9)
            counts[bin_index] += 1

        return {
            "bins": [round(b, 2) for b in bins],
            "counts": counts
        }

    def _calculate_label_statistics(self, instance_id: str) -> Tuple[Optional[str], float]:
        """
        Calculate most frequent label and disagreement for an instance.

        Args:
            instance_id: The instance ID to analyze

        Returns:
            Tuple of (most_frequent_label, disagreement_score)
        """
        try:
            usm = get_user_state_manager()

            # Get all users and their states
            user_session_ids = usm.get_user_session_ids()

            all_labels = []

            for username, session_ids in user_session_ids.items():
                for session_id in session_ids:
                    user_state = usm.get_user_state(username, session_id)
                    if user_state:
                        annotations = user_state.get_all_annotations()
                        if instance_id in annotations:
                            instance_annotations = annotations[instance_id]
                            if "labels" in instance_annotations:
                                for label, value in instance_annotations["labels"].items():
                                    if hasattr(label, 'label_name'):
                                        all_labels.append(label.label_name)
                                    else:
                                        all_labels.append(str(value))

                                    logger.debug(f"User ID: {username}, Session ID: {session_id} annotated Instance {instance_id} with {value}")

            if not all_labels:
                return None, 0.0

            # Calculate most frequent label
            label_counts = Counter(all_labels)
            most_frequent_label = label_counts.most_common(1)[0][0]

            # Calculate disagreement (1 - proportion of most frequent label)
            total_annotations = len(all_labels)
            most_frequent_count = label_counts[most_frequent_label]
            disagreement = 1 - (most_frequent_count / total_annotations)

            logger.debug(f"Instance ID: {instance_id}, total_annotations: {total_annotations}, most_frequent_count: {most_frequent_count}, disagreement: {disagreement}")

            return most_frequent_label, disagreement

        except Exception as e:
            self.logger.error(f"Error calculating label statistics for instance {instance_id}: {e}")
            return None, 0.0

    def _calculate_average_time_per_annotation(self, instance_id: str) -> Optional[float]:
        """
        Calculate average time per annotation for an instance.

        Args:
            instance_id: The instance ID to analyze

        Returns:
            Average time in seconds or None if no data
        """
        try:
            usm = get_user_state_manager()

            # Get all users and their states
            user_session_ids = usm.get_user_session_ids()

            total_time = 0
            annotation_count = 0

            for username, session_ids in user_session_ids.items():
                for session_id in session_ids:
                    user_state = usm.get_user_state(username, session_id)
                    if user_state:
                        behavioral_data = user_state.instance_id_to_behavioral_data.get(instance_id, {})
                        if hasattr(behavioral_data, 'total_time_ms'):
                            # BehavioralData object (loaded from JSON)
                            if behavioral_data.total_time_ms:
                                instance_seconds = behavioral_data.total_time_ms / 1000.0
                                total_time += instance_seconds
                                annotation_count += 1

            return total_time / annotation_count if annotation_count > 0 else None

        except Exception as e:
            self.logger.error(f"Error calculating average time for instance {instance_id}: {e}")
            return None

    def _calculate_completion_percentage(self, user_state) -> float:
        """
        Calculate completion percentage for a user.

        Args:
            user_id: The user ID to calculate completion for

        Returns:
            Completion percentage (0-100)
        """
        try:
            if not user_state:
                return 0.0

            user_id = user_state.user_id
            finished_count = user_state.get_annotation_count()
            remaining_count = get_item_state_manager().get_total_assignable_items_for_user(get_user_state_manager().get_all_user_states(user_id))

            # Total = finished + remaining (so counter shows "X / Total" not "X / Remaining")
            total_count = finished_count + remaining_count

            max_assignments = user_state.get_max_assignments()
            total_count = min(total_count, max_assignments)

            completed_assignments = len(user_state.get_all_annotations())

            if total_count == 0:
                return 0.0

            return (completed_assignments / total_count) * 100

        except Exception as e:
            self.logger.error(f"Error calculating completion percentage for user {user_id}: {e}")
            return 0.0

    def _format_seconds(self, seconds: Optional[float]) -> Optional[str]:
        """
        Format seconds into a human-readable string.

        Args:
            seconds: Number of seconds to format

        Returns:
            Formatted time string or None if input is None
        """
        if seconds is None:
            return None

        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            remaining_seconds = int(seconds % 60)
            return f"{minutes}m {remaining_seconds}s"
        else:
            hours = int(seconds // 3600)
            remaining_minutes = int((seconds % 3600) // 60)
            return f"{hours}h {remaining_minutes}m"

    def _interpret_alpha(self, alpha: float) -> str:
        """Human-readable interpretation of Krippendorff's alpha."""
        if alpha >= 0.8:
            return "Good agreement"
        elif alpha >= 0.67:
            return "Tentative agreement"
        elif alpha >= 0.33:
            return "Low agreement"
        else:
            return "Poor agreement"

    def _normalize_annotation_value(self, value: Any) -> Any:
        """Normalize annotation value for comparison."""
        if isinstance(value, list):
            return tuple(sorted(str(v) for v in value))
        elif isinstance(value, bool):
            return str(value).lower()
        return str(value)

    def get_quality_control_data(self) -> Dict[str, Any]:
        """
        Get quality control metrics (attention checks, gold standards, pre-annotation).

        Returns:
            Dict containing quality control metrics
        """
        try:
            qc_manager = get_quality_control_manager()

            if not qc_manager:
                return {
                    "enabled": False,
                    "message": "Quality control not configured"
                }

            metrics = qc_manager.get_quality_metrics()
            return {
                "enabled": True,
                **metrics
            }

        except Exception as e:
            self.logger.error(f"Error getting quality control data: {e}")
            return {"error": f"Failed to get quality control data: {str(e)}"}, 500

    def _analyze_disagreement_patterns(self, adj_mgr) -> List[Dict[str, Any]]:
        """
        Analyze per-schema disagreement patterns across the queue.

        Returns:
            List of dicts sorted by worst agreement first, with schema name
            and average agreement score.
        """
        from collections import defaultdict

        schema_scores = defaultdict(list)

        for item in adj_mgr.queue.values():
            for schema_name, score in item.agreement_scores.items():
                schema_scores[schema_name].append(score)

        patterns = []
        for schema_name, scores in schema_scores.items():
            avg = sum(scores) / len(scores) if scores else 1.0
            patterns.append({
                "schema": schema_name,
                "avg_agreement": round(avg, 3),
                "num_items": len(scores),
            })

        patterns.sort(key=lambda x: x["avg_agreement"])
        return patterns



def format_datetime(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def format_timedelta(td) -> str:
    total_seconds = int(td.total_seconds())
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    if seconds or not parts: parts.append(f"{seconds}s")  # always show something
    return " ".join(parts)

# Global instance
admin_dashboard = AdminDashboard()


