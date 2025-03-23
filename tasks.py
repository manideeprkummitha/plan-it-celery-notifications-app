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

    # get current time
    now = datetime.now
    now_str = now.isoformat()

    try:
        client = get_appwrite_client()
        databases = Databases(client)

        # find recurring tasks where the next recurrence is due
        recurring_tasks = databases.list_documents(
            database_id=APPWRITE_DATABASE_ID,
            collection_id=APPWRITE_TASKS_COLLECTION,
            queries=[
                Query.equal("isRecurring",True),
                Query.less_than("nextRecurrence", now_str),
                Query.not_equal("archived", True)
            ]
        )
        for tasks in recurring_tasks["documents"]:
            # queue the task for creating a new instance
            requests.post(
                MESSAGING_QUEUE_URL,
                json={
                    "type":"create_recurring_instance",
                    "userId":task["userId"],
                    "taskId":task["$id"],
                    "taskName":task["name"],
                    "frequency":task.get("frequency","daily"),
                    "currentDate":task["nextRecurrence"],
                 }
            )

            logger.info(f"Queued recurring task {task['name']} for new instance creation")

    except Exception as e:
        logger.error(f"Error processing recurring tasks: {str(e)}")

@celery_app.task(name="tasks.check_upcoming_deadlines")
def check_upcoming_deadlines():
    """
    check for tasks with deadlines approaching in the longer term og (1-2 weeks)
    complements the scheduler service which handles more immediate notifications
    """

    # get current time
    now = datetime.now()
    now_str = now.isoformat()

    # calculate time 2 weeks from now
    two_weeks_later = now + timedelta(days=14)
    two_weeks_later_str = two_weeks_later.isoformat()

    # define planning thresholds
    planning_thresholds = [14,7] # 2 weeks and 1 week ahead

    try:
        client = get_appwrite_client()
        databases = Databases(client)

        #  get active tasks due in the next 2 weeks
        upcoming_tasks = databases.list_documents(
            database_id=APPWRITE_DATABASE_ID,
            collection_id=APPWRITE_TASKS_COLLECTION,
            queries=[
                    Query.not_equal("completed", True),
                    Query.not_equal("archived", True),
                    Query.greater_than("dueDate", now_str),
                    Query.less_than("dueDate", two_weeks_later_str)
            ]
        )

        for task in upcoming_tasks["documents"]:
            # Parse due date
            task_due_date = datetime.fromisoformat(task["dueDate"].replace('Z', '+00:00'))

            # calculate days until due
            days_until_due = (task_due_date - now).total_seconds()/(24*3600)

            for days in planning_thresholds:
                # define threshold window
                lower_bound = days - 0.5 #half day margin
                upper_bound = days + 0.5

                threshold_key = f"planning_notified_{days}d"

                if lower_bound <= days_until_due <= upper_bound and not task.get(threshold_key, False):
                    # queue planning notification
                    requests.post(
                         MESSAGING_QUEUE_URL,
                            json={
                                "type": "task_planning_reminder",
                                "userId": task["userId"],
                                "taskId": task["$id"],
                                "taskName": task["name"],
                                "dueDate": task["dueDate"],
                                "daysRemaining": round(days_until_due),
                                "threshold": days
                            }
                    )

                    #  Mark this planning threshold as notified
                    databases.update_document(
                        database_id=APPWRITE_DATABASE_ID,
                        collection_id=APPWRITE_TASKS_COLLECTION,
                        document_id=task["$id"],
                        data={threshold_key: True}
                    )

                    logger.info(f"Queued {days}-day planning reminder for task '{task['name']}'")

    except Exception as e:
        logger.error(f"Error checking upcoming deadlines: {str(e)}")


@celery_app.task(name='tasks.check_upcoming_meetings_planning')
def check_upcoming_meetings_planning():
    """
    Check for upcoming meetings for long-term planning (1-2 weeks ahead)
    """
    logger.info("Checking for upcoming meetings (long-term planning)")
    
    # Get current time
    now = datetime.utcnow()
    now_str = now.isoformat()
    
    # Calculate time 2 weeks from now
    two_weeks_later = now + timedelta(days=14)
    two_weeks_later_str = two_weeks_later.isoformat()
    
    # Define meeting planning thresholds (in days)
    meeting_planning_thresholds = [14, 7, 3]  # 2 weeks, 1 week, and 3 days ahead
    
    try:
        client = get_appwrite_client()
        databases = Databases(client)
        
        # Get meetings in the next 2 weeks
        upcoming_meetings = databases.list_documents(
            database_id=APPWRITE_DATABASE_ID,
            collection_id=APPWRITE_TASKS_COLLECTION,
            queries=[
                Query.equal("type", "meeting"),
                Query.not_equal("completed", True),
                Query.not_equal("archived", True),
                Query.greater_than("meetingDateTime", now_str),
                Query.less_than("meetingDateTime", two_weeks_later_str)
            ]
        )
        
        for meeting in upcoming_meetings["documents"]:
            # Parse meeting time
            meeting_time = datetime.fromisoformat(meeting["meetingDateTime"].replace('Z', '+00:00'))
            
            # Calculate days until meeting
            days_until_meeting = (meeting_time - now).total_seconds() / (24 * 3600)
            
            # Check against planning thresholds
            for days in meeting_planning_thresholds:
                # Define threshold window
                lower_bound = days - 0.5
                upper_bound = days + 0.5
                
                threshold_key = f"planning_notified_{days}d"
                
                if lower_bound <= days_until_meeting <= upper_bound and not meeting.get(threshold_key, False):
                    # Queue meeting planning notification
                    requests.post(
                        MESSAGING_QUEUE_URL,
                        json={
                            "type": "meeting_planning_reminder",
                            "userId": meeting["userId"],
                            "taskId": meeting["$id"],
                            "meetingName": meeting["name"],
                            "meetingDateTime": meeting["meetingDateTime"],
                            "location": meeting.get("location", "No location specified"),
                            "daysRemaining": round(days_until_meeting),
                            "threshold": days
                        }
                    )
                    
                    # Mark this planning threshold as notified
                    databases.update_document(
                        database_id=APPWRITE_DATABASE_ID,
                        collection_id=APPWRITE_TASKS_COLLECTION,
                        document_id=meeting["$id"],
                        data={threshold_key: True}
                    )
                    
                    logger.info(f"Queued {days}-day planning reminder for meeting '{meeting['name']}'")
                    
    except Exception as e:
        logger.error(f"Error checking upcoming meetings: {str(e)}")

@celery_app.task(name='tasks.check_system_health')
def check_system_health():
    """Monitor system health metrics"""
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

