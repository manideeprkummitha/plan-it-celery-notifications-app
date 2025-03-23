import os
import ssl
from celery import Celery
from celery.schedules import crontab

# celery configuration
CELERY_BROKER_URL=os.getenv("CELERY_BROKER_URL", 'redis://localhost:6379/0')

# Create Celery application
celery_app = Celery('planit_cron_jobs')
celery_app.conf.broker_url = CELERY_BROKER_URL

# Configure ssl for redis is using ssl
if CELERY_BROKER_URL and CELERY_BROKER_URL.startswith('rediss://'):
    celery_app.conf.broker_transport_options = {'ssl_cert_reqs': ssl.CERT_NONE}

#  configure periodic tasks
celery_app.conf.beat_schedule = {
    'process-recurring-tasks-hourly':{
        'task':'tasks.process_recurring_tasks',
        'schedule':crontab(minute=0), # runs at the top of every hour
    },
    'check-upcoming-deadlines-daily':{
        'task':'tasks.check_upcoming_deadlines',
        'schedule':crontab(hour=9, minute=0), # runs at 9:00AM every day
    },
    'check-upcoming-meetings-daily':{
        'task':'tasks.check_upcoming_meetings_planning',
        'schedule':crontab(hour=9, minute=30),
    },
    'check-system-health':{
        'task':'tasks.check_system_health',
        'schedule':crontab(minute=0)
    },
}

# set timezone
celery_app.conf.timezone = 'UTC'
