"""
Assembles all routers into the root Dispatcher.

Router priority: admin handlers are registered first so admin users
don't accidentally fall through to worker handlers on /start.
"""
from aiogram import Dispatcher


def setup_routers(dp: Dispatcher) -> None:
    from apps.telegram_bot.handlers.admin.menu import router as admin_menu_router
    from apps.telegram_bot.handlers.admin.users import router as admin_users_router
    from apps.telegram_bot.handlers.admin.broadcasts import router as admin_broadcasts_router
    from apps.telegram_bot.handlers.admin.stats import router as admin_stats_router
    from apps.telegram_bot.handlers.admin.settings import router as admin_settings_router
    from apps.telegram_bot.handlers.admin.withdrawals import router as admin_withdrawals_router
    from apps.telegram_bot.handlers.admin.daily import router as admin_daily_router
    from apps.telegram_bot.handlers.admin.applications import router as admin_applications_router
    from apps.telegram_bot.handlers.admin.clients import router as admin_clients_router

    from apps.telegram_bot.handlers.curator.menu import router as curator_menu_router
    from apps.telegram_bot.handlers.curator.referrals import router as curator_referrals_router

    from apps.telegram_bot.handlers.join_request import router as join_request_router
    from apps.telegram_bot.subscription import router as subscription_router

    from apps.telegram_bot.handlers.worker.start import router as worker_start_router
    from apps.telegram_bot.handlers.worker.profile import router as worker_profile_router
    from apps.telegram_bot.handlers.worker.invite import router as worker_invite_router
    from apps.telegram_bot.handlers.worker.withdrawal import router as worker_withdrawal_router

    # Join request handler — no filters needed, fires for all ChatJoinRequest updates
    dp.include_router(join_request_router)

    # Subscription check handler — must be first so it's never blocked by itself
    dp.include_router(subscription_router)

    # Admin routers first — they have IsAdmin() filter so no conflict
    dp.include_router(admin_menu_router)
    dp.include_router(admin_users_router)
    dp.include_router(admin_broadcasts_router)
    dp.include_router(admin_stats_router)
    dp.include_router(admin_settings_router)
    dp.include_router(admin_withdrawals_router)
    dp.include_router(admin_daily_router)
    dp.include_router(admin_applications_router)
    dp.include_router(admin_clients_router)

    # Curator routers — IsCurator() filter
    dp.include_router(curator_menu_router)
    dp.include_router(curator_referrals_router)

    # Worker routers
    dp.include_router(worker_start_router)
    dp.include_router(worker_profile_router)
    dp.include_router(worker_invite_router)  # handles referrals view (not invites)
    dp.include_router(worker_withdrawal_router)
