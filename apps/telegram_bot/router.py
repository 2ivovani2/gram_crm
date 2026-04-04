"""
Assembles all routers into the root Dispatcher.

Router priority: admin handlers are registered first so admin users
don't accidentally fall through to worker handlers on /start.
"""
from aiogram import Dispatcher


def setup_routers(dp: Dispatcher) -> None:
    from apps.telegram_bot.handlers.admin.menu import router as admin_menu_router
    from apps.telegram_bot.handlers.admin.users import router as admin_users_router
    from apps.telegram_bot.handlers.admin.invites import router as admin_invites_router
    from apps.telegram_bot.handlers.admin.broadcasts import router as admin_broadcasts_router
    from apps.telegram_bot.handlers.admin.stats import router as admin_stats_router
    from apps.telegram_bot.handlers.admin.settings import router as admin_settings_router

    from apps.telegram_bot.handlers.admin.withdrawals import router as admin_withdrawals_router

    from apps.telegram_bot.handlers.worker.start import router as worker_start_router
    from apps.telegram_bot.handlers.worker.profile import router as worker_profile_router
    from apps.telegram_bot.handlers.worker.invite import router as worker_invite_router
    from apps.telegram_bot.handlers.worker.withdrawal import router as worker_withdrawal_router

    # Admin routers first — they have IsAdmin() filter so no conflict
    dp.include_router(admin_menu_router)
    dp.include_router(admin_users_router)
    dp.include_router(admin_invites_router)
    dp.include_router(admin_broadcasts_router)
    dp.include_router(admin_stats_router)
    dp.include_router(admin_settings_router)
    dp.include_router(admin_withdrawals_router)

    # Worker routers
    dp.include_router(worker_start_router)
    dp.include_router(worker_profile_router)
    dp.include_router(worker_invite_router)
    dp.include_router(worker_withdrawal_router)
