"""
Assembles routers into the root Dispatcher.

Minimal bot for Gramly new architecture:
  - Admin: user management panel
  - All users: /start → CRM link + docs link

Removed: broadcasts, stats, clients, applications, withdrawals,
         curator flows, worker management flows, join_request, subscription.
"""
from aiogram import Dispatcher


def setup_routers(dp: Dispatcher) -> None:
    from apps.telegram_bot.handlers.admin.menu import router as admin_menu_router
    from apps.telegram_bot.handlers.admin.users import router as admin_users_router
    from apps.telegram_bot.handlers.worker.start import router as start_router

    # Admin routers first — IsAdmin() filter prevents conflicts
    dp.include_router(admin_menu_router)
    dp.include_router(admin_users_router)

    # General /start for all users
    dp.include_router(start_router)
