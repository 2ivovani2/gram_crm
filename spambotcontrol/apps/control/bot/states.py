from aiogram.fsm.state import State, StatesGroup


class SubmitReportState(StatesGroup):
    waiting_for_report = State()


class WithdrawalState(StatesGroup):
    waiting_for_wallet = State()


class AdminPenaltyCreateState(StatesGroup):
    waiting_for_username = State()
    waiting_for_amount = State()
    waiting_for_reason = State()
    confirm = State()


class AdminReportReviewState(StatesGroup):
    waiting_for_comment = State()


class DisputePenaltyState(StatesGroup):
    waiting_for_comment = State()


class AdminBroadcastControlState(StatesGroup):
    waiting_for_text = State()
    confirm = State()
