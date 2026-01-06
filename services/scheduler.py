# -*- coding: utf-8 -*-
"""
Scheduler Service Module
Manage scheduled tasks using APScheduler
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from datetime import datetime, timedelta
import atexit

from config import (
    LOCAL_TZ,
    SCHEDULER_TIMEZONE,
    get_logger
)
from services.reminder import (
    send_reminder,
    check_and_alert_no_response
)
from database.reminders import get_scheduled_reminders

logger = get_logger(__name__)

# Initialize scheduler
jobstores = {
    'default': MemoryJobStore()
}

scheduler = BackgroundScheduler(
    jobstores=jobstores,
    timezone=SCHEDULER_TIMEZONE
)


def init_scheduler():
    """
    Initialize and start the scheduler
    """
    try:
        if not scheduler.running:
            scheduler.start()
            logger.info("✅ Scheduler started successfully")
            
            # Schedule recurring job to check no-response reminders
            scheduler.add_job(
                func=check_and_alert_no_response,
                trigger=CronTrigger(hour=10, minute=0, timezone=LOCAL_TZ),  # Daily at 10 AM
                id='check_no_response',
                name='Check for no-response reminders',
                replace_existing=True
            )
            logger.info("✅ Scheduled daily no-response check at 10:00")
            
            # Load and schedule pending reminders from database
            load_pending_reminders()
            
            # Register shutdown handler
            atexit.register(shutdown_scheduler)
            
        else:
            logger.warning("Scheduler already running")
            
    except Exception as e:
        logger.exception(f"Error initializing scheduler: {e}")


def shutdown_scheduler():
    """
    Gracefully shutdown the scheduler
    """
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("Scheduler shutdown successfully")
    except Exception as e:
        logger.exception(f"Error shutting down scheduler: {e}")


def load_pending_reminders():
    """
    Load pending reminders from database and schedule them
    """
    try:
        logger.info("Loading pending reminders from database")
        
        scheduled_reminders = get_scheduled_reminders()
        
        if not scheduled_reminders:
            logger.info("No pending reminders to schedule")
            return
        
        loaded_count = 0
        skipped_count = 0
        
        now = datetime.now(tz=LOCAL_TZ)
        
        for reminder in scheduled_reminders:
            user_id = reminder.get('User_ID')
            reminder_type = reminder.get('Reminder_Type')
            scheduled_date_str = reminder.get('Scheduled_Date')
            
            if not all([user_id, reminder_type, scheduled_date_str]):
                logger.warning(f"Incomplete reminder data: {reminder}")
                skipped_count += 1
                continue
            
            try:
                # Parse scheduled date
                scheduled_date = datetime.strptime(scheduled_date_str, "%Y-%m-%d %H:%M:%S")
                scheduled_date = scheduled_date.replace(tzinfo=LOCAL_TZ)
                
                # Skip if in the past
                if scheduled_date < now:
                    logger.info(f"Skipping past reminder: {reminder_type} for {user_id} scheduled at {scheduled_date}")
                    skipped_count += 1
                    continue
                
                # Schedule the job
                job_id = f"{user_id}_{reminder_type}_{scheduled_date.strftime('%Y%m%d%H%M')}"
                
                scheduler.add_job(
                    func=send_reminder,
                    trigger=DateTrigger(run_date=scheduled_date, timezone=LOCAL_TZ),
                    args=[user_id, reminder_type],
                    id=job_id,
                    name=f"Reminder {reminder_type} for {user_id}",
                    replace_existing=True
                )
                
                loaded_count += 1
                logger.info(f"Scheduled {reminder_type} for {user_id} at {scheduled_date}")
                
            except Exception as e:
                logger.exception(f"Error scheduling reminder {reminder}: {e}")
                skipped_count += 1
        
        logger.info(f"Loaded {loaded_count} reminders, skipped {skipped_count}")
        
    except Exception as e:
        logger.exception(f"Error loading pending reminders: {e}")


def schedule_reminder_job(user_id, reminder_type, scheduled_date):
    """
    Schedule a single reminder job
    
    Args:
        user_id: User ID
        reminder_type: Type of reminder
        scheduled_date: When to send (datetime)
        
    Returns:
        bool: True if scheduled successfully
    """
    try:
        # Ensure timezone aware
        if scheduled_date.tzinfo is None:
            scheduled_date = scheduled_date.replace(tzinfo=LOCAL_TZ)
        
        # Check if not in the past
        now = datetime.now(tz=LOCAL_TZ)
        if scheduled_date < now:
            logger.warning(f"Cannot schedule reminder in the past: {scheduled_date}")
            return False
        
        # Create unique job ID
        job_id = f"{user_id}_{reminder_type}_{scheduled_date.strftime('%Y%m%d%H%M')}"
        
        # Add job to scheduler
        scheduler.add_job(
            func=send_reminder,
            trigger=DateTrigger(run_date=scheduled_date, timezone=LOCAL_TZ),
            args=[user_id, reminder_type],
            id=job_id,
            name=f"Reminder {reminder_type} for {user_id}",
            replace_existing=True
        )
        
        logger.info(f"Scheduled reminder job: {job_id} at {scheduled_date}")
        return True
        
    except Exception as e:
        logger.exception(f"Error scheduling reminder job: {e}")
        return False


def cancel_reminder_job(user_id, reminder_type):
    """
    Cancel a scheduled reminder job
    
    Args:
        user_id: User ID
        reminder_type: Type of reminder
        
    Returns:
        bool: True if cancelled successfully
    """
    try:
        # Find jobs matching this user and reminder type
        jobs = scheduler.get_jobs()
        cancelled_count = 0
        
        for job in jobs:
            if (user_id in job.id and reminder_type in job.id):
                scheduler.remove_job(job.id)
                cancelled_count += 1
                logger.info(f"Cancelled job: {job.id}")
        
        if cancelled_count > 0:
            logger.info(f"Cancelled {cancelled_count} reminder jobs for {user_id}/{reminder_type}")
            return True
        else:
            logger.warning(f"No jobs found to cancel for {user_id}/{reminder_type}")
            return False
            
    except Exception as e:
        logger.exception(f"Error cancelling reminder job: {e}")
        return False


def get_scheduled_jobs():
    """
    Get all currently scheduled jobs
    
    Returns:
        list: List of job information
    """
    try:
        jobs = scheduler.get_jobs()
        
        job_list = []
        for job in jobs:
            job_info = {
                'id': job.id,
                'name': job.name,
                'next_run_time': job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else None,
                'func': job.func.__name__
            }
            job_list.append(job_info)
        
        return job_list
        
    except Exception as e:
        logger.exception(f"Error getting scheduled jobs: {e}")
        return []


def reschedule_all_reminders():
    """
    Reload and reschedule all reminders from database
    Useful after app restart
    
    Returns:
        int: Number of reminders rescheduled
    """
    try:
        logger.info("Rescheduling all reminders")
        
        # Clear existing reminder jobs (keep system jobs like check_no_response)
        jobs = scheduler.get_jobs()
        for job in jobs:
            if job.id != 'check_no_response':
                scheduler.remove_job(job.id)
        
        # Reload from database
        load_pending_reminders()
        
        # Count current jobs
        jobs_after = scheduler.get_jobs()
        reminder_jobs = [j for j in jobs_after if j.id != 'check_no_response']
        
        logger.info(f"Rescheduled {len(reminder_jobs)} reminders")
        return len(reminder_jobs)
        
    except Exception as e:
        logger.exception(f"Error rescheduling all reminders: {e}")
        return 0


def get_scheduler_status():
    """
    Get scheduler status and statistics
    
    Returns:
        dict: Scheduler status information
    """
    try:
        jobs = scheduler.get_jobs()
        
        status = {
            'running': scheduler.running,
            'total_jobs': len(jobs),
            'reminder_jobs': len([j for j in jobs if j.id != 'check_no_response']),
            'system_jobs': len([j for j in jobs if j.id == 'check_no_response']),
            'timezone': str(LOCAL_TZ),
            'current_time': datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        }
        
        return status
        
    except Exception as e:
        logger.exception(f"Error getting scheduler status: {e}")
        return {'error': str(e)}


# Debug function
def print_scheduled_jobs():
    """
    Print all scheduled jobs (for debugging)
    """
    try:
        jobs = scheduler.get_jobs()
        
        if not jobs:
            logger.info("No scheduled jobs")
            return
        
        logger.info(f"=== Scheduled Jobs ({len(jobs)}) ===")
        for job in jobs:
            logger.info(f"  ID: {job.id}")
            logger.info(f"  Name: {job.name}")
            logger.info(f"  Next run: {job.next_run_time}")
            logger.info(f"  Function: {job.func.__name__}")
            logger.info("  ---")
            
    except Exception as e:
        logger.exception(f"Error printing jobs: {e}")
