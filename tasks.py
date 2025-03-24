import os
import logging
from datetime import datetime, timedelta
import requests
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.query import Query
from celery_app import celery_app

# setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s]  %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

# configuration
APPWRITE_ENDPOINT = os.getenv("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID", "")
APPWRITE_API_KEY = os.getenv("APPWRITE_API_KEY", "")
APPWRITE_DATABASE_ID = os.getenv("APPWRITE_DATABASE_ID", "")
APPWRITE_TASKS_COLLECTION = os.getenv("APPWRITE_TASKS_COLLECTION", "tasks")
MESSAGING_QUEUE_URL = os.getenv("MESSAGING_QUEUE_URL", "http://localhost:5001/queue")
MESSAGING_QUEUE_HEALTH_URL = os.getenv("MESSAGING_QUEUE_HEALTH_URL", "http://localhost:5001/health")

# initialize appwrite client
def get_appwrite_client():
    client = Client()
    client.set_endpoint(APPWRITE_ENDPOINT)
    client.set_project(APPWRITE_PROJECT_ID)
    client.set_key(APPWRITE_API_KEY)
    return client

@celery_app.task(name="tasks.process_recurring_tasks")
def process_recurring_tasks():
    """
    Find the recurring tasks that need new instances created
    """
    logger.info("Processing recurring tasks")

    # get current time (note the parentheses to actually call datetime.now)
    now = datetime.now()
    now_str = now.isoformat()

    try:
        client = get_appwrite_client()
        databases = Databases(client)

        # find recurring tasks where the next recurrence is due
        recurring_tasks = databases.list_documents(
            database_id=APPWRITE_DATABASE_ID,
            collection_id=APPWRITE_TASKS_COLLECTION,
            queries=[
                Query.equal("isRecurring", True),
                Query.less_than("nextRecurrence", now_str),
                Query.not_equal("archived", True)
            ]
        )
        for task in recurring_tasks["documents"]:
            # queue the task for creating a new instance
            requests.post(
                MESSAGING_QUEUE_URL,
                json={
                    "type": "create_recurring_instance",
                    "userId": task["userId"],
                    "taskId": task["$id"],
                    "taskName": task["name"],
                    "frequency": task.get("frequency", "daily"),
                    "currentDate": task["nextRecurrence"],
                }
            )

            logger.info(f"Queued recurring task '{task['name']}' for new instance creation")

    except Exception as e:
        logger.error(f"Error processing recurring tasks: {str(e)}")


@celery_app.task(name="tasks.check_upcoming_deadlines")
def check_upcoming_deadlines():
    """
    Check for tasks that are due within the next 24 hours and notify once.
    """

    logger.info("Checking deadlines within 24 hours")
    now = datetime.now()
    now_str = now.isoformat()

    # Calculate 24 hours from now
    deadline_threshold = now + timedelta(hours=24)
    deadline_threshold_str = deadline_threshold.isoformat()

    try:
        client = get_appwrite_client()
        databases = Databases(client)

        # Get tasks that are due between now and the next 24 hours
        upcoming_tasks = databases.list_documents(
            database_id=APPWRITE_DATABASE_ID,
            collection_id=APPWRITE_TASKS_COLLECTION,
            queries=[
                Query.not_equal("completed", True),
                Query.not_equal("archived", True),
                Query.greater_than("dueDate", now_str),
                Query.less_than("dueDate", deadline_threshold_str)
            ]
        )

        for task in upcoming_tasks["documents"]:
            # Check if we've already sent a 24-hour notification
            if not task.get("notif_24hr_deadline", False):
                task_due_date = datetime.fromisoformat(task["dueDate"].replace('Z', '+00:00'))
                hours_until_due = (task_due_date - now).total_seconds() / 3600

                # Queue a 24-hour notification
                requests.post(
                    MESSAGING_QUEUE_URL,
                    json={
                        "type": "task_deadline_24hr_reminder",
                        "userId": task["userId"],
                        "taskId": task["$id"],
                        "taskName": task["name"],
                        "dueDate": task["dueDate"],
                        "hoursRemaining": round(hours_until_due, 1),
                    }
                )

                # Mark that we've notified for this threshold
                databases.update_document(
                    database_id=APPWRITE_DATABASE_ID,
                    collection_id=APPWRITE_TASKS_COLLECTION,
                    document_id=task["$id"],
                    data={"notif_24hr_deadline": True}
                )

                logger.info(f"Queued 24-hour deadline reminder for task '{task['name']}'")

    except Exception as e:
        logger.error(f"Error checking upcoming deadlines: {str(e)}")


@celery_app.task(name='tasks.check_upcoming_meetings_planning')
def check_upcoming_meetings_planning():
    """
    Check for upcoming meetings in the next 12 hours and notify once.
    """
    logger.info("Checking meetings within 12 hours")

    # Get current time
    now = datetime.utcnow()
    now_str = now.isoformat()

    # Calculate 12 hours from now
    meeting_threshold = now + timedelta(hours=12)
    meeting_threshold_str = meeting_threshold.isoformat()

    try:
        client = get_appwrite_client()
        databases = Databases(client)

        # Get meetings in the next 12 hours
        upcoming_meetings = databases.list_documents(
            database_id=APPWRITE_DATABASE_ID,
            collection_id=APPWRITE_TASKS_COLLECTION,
            queries=[
                Query.equal("type", "meeting"),
                Query.not_equal("completed", True),
                Query.not_equal("archived", True),
                Query.greater_than("meetingDateTime", now_str),
                Query.less_than("meetingDateTime", meeting_threshold_str)
            ]
        )

        for meeting in upcoming_meetings["documents"]:
            # Check if we've already sent a 12-hour notification
            if not meeting.get("notif_12hr_meeting", False):
                meeting_time = datetime.fromisoformat(meeting["meetingDateTime"].replace('Z', '+00:00'))
                hours_until_meeting = (meeting_time - now).total_seconds() / 3600

                # Queue a meeting reminder
                requests.post(
                    MESSAGING_QUEUE_URL,
                    json={
                        "type": "meeting_12hr_reminder",
                        "userId": meeting["userId"],
                        "taskId": meeting["$id"],
                        "meetingName": meeting["name"],
                        "meetingDateTime": meeting["meetingDateTime"],
                        "location": meeting.get("location", "No location specified"),
                        "hoursRemaining": round(hours_until_meeting, 1),
                    }
                )

                # Mark that we've notified for this 12-hour threshold
                databases.update_document(
                    database_id=APPWRITE_DATABASE_ID,
                    collection_id=APPWRITE_TASKS_COLLECTION,
                    document_id=meeting["$id"],
                    data={"notif_12hr_meeting": True}
                )

                logger.info(f"Queued 12-hour meeting reminder for meeting '{meeting['name']}'")

    except Exception as e:
        logger.error(f"Error checking upcoming meetings: {str(e)}")


@celery_app.task(name='tasks.check_system_health')
def check_system_health():
    """
    Monitor system health metrics
    """
    logger.info("Checking system health")

    # Check Appwrite connection
    try:
        client = get_appwrite_client()
        databases = Databases(client)

        # Just checking if we can connect by fetching one document
        result = databases.list_documents(
            database_id=APPWRITE_DATABASE_ID,
            collection_id=APPWRITE_TASKS_COLLECTION,
            queries=[Query.limit(1)]
        )

        document_count = len(result["documents"])
        logger.info(f"Appwrite connection: OK (found {document_count} documents)")

    except Exception as e:
        logger.error(f"Appwrite connection error: {str(e)}")

    # Check messaging queue health
    try:
        response = requests.get(MESSAGING_QUEUE_HEALTH_URL, timeout=5)
        logger.info(f"Messaging queue health status: {response.status_code}")
    except Exception as e:
        logger.error(f"Error checking messaging queue health: {str(e)}")
