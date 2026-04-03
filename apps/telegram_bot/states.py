from aiogram.fsm.state import State, StatesGroup


class InviteKeyInputState(StatesGroup):
    """Worker: entering invite key for account activation."""
    waiting_for_key = State()


class AdminInviteCreateState(StatesGroup):
    """Admin: multi-step invite key creation."""
    waiting_for_label = State()
    waiting_for_max_uses = State()
    waiting_for_expiry = State()
    confirm = State()


class AdminBroadcastCreateState(StatesGroup):
    """Admin: multi-step broadcast creation."""
    waiting_for_title = State()
    waiting_for_text = State()
    selecting_audience = State()
    confirm = State()


class AdminUserSearchState(StatesGroup):
    """Admin: free-text user search."""
    waiting_for_query = State()


class AdminSetWorkUrlState(StatesGroup):
    """Admin: setting work URL for a specific worker."""
    waiting_for_url = State()


class AdminSetAttractedCountState(StatesGroup):
    """Admin: manually setting attracted_count for a worker."""
    waiting_for_count = State()


class AdminSetReferralRateState(StatesGroup):
    """Admin: changing global referral rate."""
    waiting_for_rate = State()
