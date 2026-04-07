from aiogram.fsm.state import State, StatesGroup


class InviteKeyInputState(StatesGroup):
    """Worker: entering invite key for account activation."""
    waiting_for_key = State()


class AdminInviteCreateState(StatesGroup):
    """Admin/Curator: multi-step invite key creation."""
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


class AdminSetPersonalRateState(StatesGroup):
    """Admin: setting personal_rate for a specific worker."""
    waiting_for_rate = State()


class AdminSetReferralRatePerUserState(StatesGroup):
    """Admin: setting referral_rate for a specific worker."""
    waiting_for_rate = State()


class WorkerWithdrawalState(StatesGroup):
    """Worker/Curator: withdrawal request flow."""
    choosing_method = State()
    entering_details = State()


class AdminDailyReportState(StatesGroup):
    """Admin: daily client data entry form (4-step FSM)."""
    waiting_for_link = State()
    waiting_for_client_nick = State()
    waiting_for_client_rate = State()
    waiting_for_total_applications = State()
    confirm = State()


class AdminSetRateConfigState(StatesGroup):
    """Admin: update RateConfig worker_share and referral_share."""
    waiting_for_worker_share = State()
    waiting_for_referral_share = State()
