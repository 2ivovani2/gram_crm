"""
Callback data classes using aiogram's CallbackData factory.

Convention:
  - Short prefixes to stay within Telegram's 64-byte callback_data limit
  - action field is always present and drives the handler logic
  - IDs default to 0 (no entity selected / list view)
  - page defaults to 1

Prefix map:
  w       → worker flows
  cur     → curator flows
  adm     → admin main menu
  adm_u   → admin users
  adm_b   → admin broadcasts
  adm_s   → admin stats
  adm_cfg → admin settings
  adm_w   → admin withdrawals
  adm_d   → admin daily report
  adm_a   → admin applications (join requests)
  adm_cl  → admin clients & links
  ww      → worker withdrawal
"""
from aiogram.filters.callback_data import CallbackData


class WorkerCallback(CallbackData, prefix="w"):
    action: str


class CuratorCallback(CallbackData, prefix="cur"):
    action: str


class AdminMenuCallback(CallbackData, prefix="adm"):
    section: str


class AdminUserCallback(CallbackData, prefix="adm_u"):
    action: str
    user_id: int = 0
    page: int = 1


class AdminApplicationCallback(CallbackData, prefix="adm_a"):
    action: str          # list | view | approve | reject | noop
    request_id: int = 0
    page: int = 1


class AdminClientCallback(CallbackData, prefix="adm_cl"):
    action: str          # list | view_client | view_link | deactivate | assign | noop
    client_id: int = 0
    link_id: int = 0
    worker_id: int = 0
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
    period: str = "week"  # today | week | last_week | month


class AdminSettingsCallback(CallbackData, prefix="adm_cfg"):
    action: str
    user_id: int = 0


class AdminWithdrawalCallback(CallbackData, prefix="adm_w"):
    action: str
    withdrawal_id: int = 0
    page: int = 1


class AdminDailyCallback(CallbackData, prefix="adm_d"):
    action: str
    report_id: int = 0
    date_str: str = ""  # ISO date string (YYYY-MM-DD) for backdated entry


class WorkerWithdrawalCallback(CallbackData, prefix="ww"):
    action: str
    withdrawal_id: int = 0


class SubscriptionCallback(CallbackData, prefix="sub"):
    action: str  # "check"
