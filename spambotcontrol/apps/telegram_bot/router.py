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

    # Control bot — Gramly Control HR system
    from apps.control.bot.admin_handlers import router as control_admin_router
    from apps.control.bot.worker_handlers import router as control_worker_router
    from apps.control.bot.accountant_handlers import router as control_accountant_router

    # Admin routers first — IsAdmin() filter prevents conflicts
    dp.include_router(admin_menu_router)
    dp.include_router(admin_users_router)

    # Control bot routers
    from apps.control.bot.invite_handlers import router as control_invite_router
    dp.include_router(control_invite_router)
    dp.include_router(control_admin_router)
    dp.include_router(control_accountant_router)
    dp.include_router(control_worker_router)

    # General /start for all users (last — catches everything else)
    dp.include_router(start_router)
