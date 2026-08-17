from aiogram.fsm.state import State, StatesGroup


class SubmitReportState(StatesGroup):
    selecting_template = State()
    waiting_for_report = State()


class WithdrawalState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_wallet = State()


class CryptoAddressState(StatesGroup):
    waiting_for_name = State()
    waiting_for_address = State()


class AdminPenaltyCreateState(StatesGroup):
    waiting_for_username = State()
    waiting_for_amount = State()
    waiting_for_reason = State()
    confirm = State()


class AdminReportReviewState(StatesGroup):
    waiting_for_comment = State()   # used when admin clicks "Reject"


class DisputePenaltyState(StatesGroup):
    waiting_for_comment = State()


class AdminBroadcastControlState(StatesGroup):
    waiting_for_text = State()
    confirm = State()


class AdminWithdrawalState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_wallet = State()


class AdminCryptoAddressState(StatesGroup):
    waiting_for_name = State()
    waiting_for_address = State()
