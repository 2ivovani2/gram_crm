"""Administrative helpers for CRM identity lifecycle."""

from __future__ import annotations

import logging

from django.contrib.sessions.models import Session
from django.utils import timezone

logger = logging.getLogger(__name__)


def terminate_user_sessions(user_id: int) -> int:
    """Delete every unexpired Django session authenticated as ``user_id``."""

    session_keys: list[str] = []
    for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        try:
            data = session.get_decoded()
        except Exception:  # A corrupt/old session must not block revocation.
            continue
        if str(data.get("_auth_user_id", "")) == str(user_id):
            session_keys.append(session.session_key)

    if not session_keys:
        return 0
    deleted, _ = Session.objects.filter(session_key__in=session_keys).delete()
    logger.info(
        "crm_sessions_revoked crm_user_id=%s session_count=%s",
        user_id,
        deleted,
    )
    return deleted
