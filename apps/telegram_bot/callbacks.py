"""
Callback data classes using aiogram's CallbackData factory.

Convention:
  - Short prefixes to stay within Telegram's 64-byte callback_data limit
  - action field is always present and drives the handler logic
  - IDs default to 0 (no entity selected / list view)
  - page defaults to 1

Prefix map:
  w       → worker flows
  adm     → admin main menu
  adm_u   → admin users
  adm_i   → admin invites
  adm_b   → admin broadcasts
  adm_s   → admin stats
"""
from aiogram.filters.callback_data import CallbackData


class WorkerCallback(CallbackData, prefix="w"):
    action: str


class AdminMenuCallback(CallbackData, prefix="adm"):
    section: str


class AdminUserCallback(CallbackData, prefix="adm_u"):
    action: str
    user_id: int = 0
    page: int = 1


class AdminInviteCallback(CallbackData, prefix="adm_i"):
    action: str
    key_id: int = 0
    page: int = 1


class AdminBroadcastCallback(CallbackData, prefix="adm_b"):
    action: str
    broadcast_id: int = 0
    page: int = 1


class AdminStatsCallback(CallbackData, prefix="adm_s"):
    action: str


class AdminSettingsCallback(CallbackData, prefix="adm_cfg"):
    action: str
    user_id: int = 0
