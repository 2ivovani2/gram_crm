"""Django project package.

Importing the Celery application here makes it the current/default app inside
web and management-command processes. Without this, ``shared_task.delay()``
silently falls back to AMQP on localhost even though workers use Redis.
"""
from .celery import app as celery_app

__all__ = ("celery_app",)
