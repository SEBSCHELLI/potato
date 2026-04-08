import logging
from datetime import timezone, timedelta, datetime
logger = logging.getLogger(__name__)

utc_plus_2 = timezone(timedelta(hours=2))

class DateHandler:
    @staticmethod
    def get_timestamp_now() -> datetime :
        return datetime.now(tz=timezone(timedelta(hours=2)))

    @staticmethod
    def timestamp_to_datetime(timestamp: float) -> datetime | None:
        """
        Convert a Unix timestamp (seconds) to a UTC datetime object.
        Returns None if conversion fails.
        """
        try:
            if timestamp:
                return datetime.fromtimestamp(timestamp, tz=utc_plus_2)
            else:
                return None
        except (TypeError, ValueError, OSError) as e:
            logger.warning("Failed to convert timestamp %s to datetime: %s", timestamp, e)
            return None

    @staticmethod
    def datetime_to_str(dt: datetime) -> str | None:
        """
        Convert a datetime object to a string with seconds precision.
        Returns '?' if conversion fails.
        """
        try:
            if dt:
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                return None
        except Exception as e:
            logger.warning("Failed to convert datetime %s to string: %s", dt, e)
            return None

    @staticmethod
    def str_to_datetime(dtstr: str) -> datetime | None:
        """
        Convert a string to a datetime object (assumes UTC, format '%Y-%m-%d %H:%M:%S').
        Returns None if parsing fails.
        """
        try:
            if dtstr:
                return datetime.strptime(dtstr, "%Y-%m-%d %H:%M:%S").replace(tzinfo=utc_plus_2)
            else:
                return None
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse datetime string '%s': %s", dtstr, e)
            return None