"""
Stats Celery tasks.

send_daily_broadcast_task, send_admin_reminder_task, check_missing_daily_report_task
were removed — they depended on the legacy DailyReport / MissedDay system which
has been replaced by the client-link-based model managed via /stats/clients/.

The only scheduled task that remains from this app is in apps/clients/tasks.py:
  check_worker_inactivity_task — daily 09:00 МСК
"""
