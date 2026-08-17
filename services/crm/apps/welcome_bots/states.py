from aiogram.fsm.state import State, StatesGroup


class AddBotState(StatesGroup):
    waiting_for_token = State()


class WelcomeMessageState(StatesGroup):
    waiting_for_message = State()
