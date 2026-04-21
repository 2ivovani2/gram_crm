"""
Web stats dashboard for admin use.
URL: /stats/  — protected by Django's staff_member_required.

New model (2026-04-16):
  - DailyReport/MissedDay/RateConfig removed from active use
  - Dashboard shows client/link/assignment-based stats
  - Global flat rates (GlobalRate) editable via /stats/clients/
"""
from __future__ import annotations
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from apps.stats.models import GlobalRate
from apps.users.models import User, UserRole, UserStatus


@method_decorator(staff_member_required, name="dispatch")
class StatsDashboardView(View):

    def get(self, request, *args, **kwargs):
        from apps.clients.models import Client, ClientLink, LinkAssignment, LinkStatus
        from apps.stats.services import MetricsService
        from django.db.models import Sum, Count, Q

        # ── Worker counts ─────────────────────────────────────────────────────
        total_workers = User.objects.filter(role=UserRole.WORKER).count()
        total_curators = User.objects.filter(role=UserRole.CURATOR).count()
        active_workers = User.objects.filter(
            role__in=[UserRole.WORKER, UserRole.CURATOR],
            status=UserStatus.ACTIVE,
        ).count()

        # ── Client / link / assignment counts ────────────────────────────────
        total_clients = Client.objects.count()
        active_links = ClientLink.objects.filter(status=LinkStatus.ACTIVE).count()
        active_assignments = LinkAssignment.objects.filter(is_active=True).count()
        unassigned_active_links = (
            ClientLink.objects
            .filter(status=LinkStatus.ACTIVE)
            .exclude(assignments__is_active=True)
            .count()
        )

        # ── Total applications across all assignments ─────────────────────────
        from apps.users.models import WorkLink
        total_applications = WorkLink.objects.aggregate(
            total=Sum("attracted_count")
        )["total"] or 0

        # ── Global rates ──────────────────────────────────────────────────────
        global_rate = GlobalRate.get()
        total_worker_payout = (Decimal(total_applications) * global_rate.worker_rate).quantize(Decimal("0.01"))
        total_referral_payout = (Decimal(total_applications) * global_rate.referral_rate).quantize(Decimal("0.01"))

        # ── Top workers by attracted_count ────────────────────────────────────
        top_workers = list(
            User.objects
            .filter(role__in=[UserRole.WORKER, UserRole.CURATOR], attracted_count__gt=0)
            .order_by("-attracted_count")[:10]
        )

        # ── Recent active assignments with worker + link data ─────────────────
        recent_assignments = list(
            LinkAssignment.objects
            .filter(is_active=True)
            .select_related("worker", "client_link__client", "work_link")
            .order_by("-assigned_at")[:30]
        )

        # ── Workers with no assignment ────────────────────────────────────────
        idle_workers = list(
            User.objects
            .filter(
                role__in=[UserRole.WORKER, UserRole.CURATOR],
                status=UserStatus.ACTIVE,
                is_activated=True,
            )
            .annotate(active_count=Count(
                "link_assignments",
                filter=Q(link_assignments__is_active=True),
            ))
            .filter(active_count=0)
            .order_by("first_name", "telegram_username")[:20]
        )

        # ── Product metrics ───────────────────────────────────────────────────
        conversion_periods = MetricsService.conversion_by_periods()
        acquisition_weeks = MetricsService.acquisition_metrics(num_weeks=8)
        retention_cohorts = MetricsService.retention_cohorts(num_weeks=8)

        # Max retention columns across cohorts (for table header)
        max_retention_cols = max((len(c["retention"]) for c in retention_cohorts), default=0)

        context = {
            "today": timezone.localdate(),
            "total_workers": total_workers,
            "total_curators": total_curators,
            "active_workers": active_workers,
            "total_clients": total_clients,
            "active_links": active_links,
            "active_assignments": active_assignments,
            "unassigned_active_links": unassigned_active_links,
            "total_applications": total_applications,
            "total_worker_payout": total_worker_payout,
            "total_referral_payout": total_referral_payout,
            "global_rate": global_rate,
            "top_workers": top_workers,
            "recent_assignments": recent_assignments,
            "idle_workers": idle_workers,
            # product metrics
            "conversion_periods": conversion_periods,
            "acquisition_weeks": acquisition_weeks,
            "retention_cohorts": retention_cohorts,
            "max_retention_cols": max_retention_cols,
            "retention_col_range": list(range(max_retention_cols)),
            "stats_msg": request.GET.get("msg", ""),
            "stats_error": request.GET.get("error", ""),
        }
        return render(request, "stats_dashboard.html", context)

    def post(self, request, *args, **kwargs):
        """Handle ad spend form submission."""
        from apps.stats.services import MetricsService
        from django.shortcuts import redirect
        import datetime

        action = request.POST.get("action", "")
        if action == "save_ad_spend":
            week_str = request.POST.get("week_start", "").strip()
            amount_str = request.POST.get("amount", "0").strip()
            notes = request.POST.get("notes", "").strip()
            try:
                week_start = datetime.date.fromisoformat(week_str)
                amount = Decimal(amount_str)
                if amount < 0:
                    raise ValueError("Сумма не может быть отрицательной")
                MetricsService.upsert_ad_spend(week_start, amount, notes)
            except Exception as e:
                return redirect(f"/stats/?error={e}")
            return redirect("/stats/?msg=Расходы+сохранены")

        return redirect("/stats/")


# ── Clients & Links ───────────────────────────────────────────────────────────

@method_decorator(staff_member_required, name="dispatch")
class ClientsView(View):
    """
    List + CRUD for Client and ClientLink + GlobalRate settings.
    Actions (POST):
      create_client     — create new client
      add_link          — add link to client (auto-assigns worker)
      manual_assign     — assign worker to unassigned link
      reassign_worker   — switch worker on an already-assigned link
      update_count      — update attracted_count for an assignment
      update_rates      — save global worker_rate + referral_rate
      delete_client     — delete client and all its links
      delete_link       — delete a specific link
      set_channel       — save Telegram channel_id to client (auto mode setup)
      check_bot         — check bot permissions in client's channel
      toggle_auto       — enable/disable auto mode (requires bot_check_status=ok)
    """

    def _get_workers(self):
        return list(
            User.objects.filter(
                role__in=[UserRole.WORKER, UserRole.CURATOR],
                status=UserStatus.ACTIVE,
                is_activated=True,
                is_blocked_bot=False,
            ).order_by("first_name", "telegram_username")
        )

    def get(self, request):
        from apps.clients.models import Client

        clients = list(
            Client.objects.prefetch_related(
                "links__assignments__work_link",
                "links__assignments__worker",
            ).order_by("-created_at")
        )

        global_rate = GlobalRate.get()

        client_data = []
        for c in clients:
            total_apps = c.total_applications
            client_earned = c.client_earned
            worker_payout = (Decimal(total_apps) * global_rate.worker_rate).quantize(Decimal("0.01"))
            referral_payout = (Decimal(total_apps) * global_rate.referral_rate).quantize(Decimal("0.01"))
            net_profit = (client_earned - worker_payout - referral_payout).quantize(Decimal("0.01"))

            client_data.append({
                "client": c,
                "total_apps": total_apps,
                "client_earned": client_earned,
                "worker_payout": worker_payout,
                "referral_payout": referral_payout,
                "net_profit": net_profit,
                "active_links": list(c.active_links),
                "all_links": list(c.links.all()),
            })

        return render(request, "stats_clients.html", {
            "client_data": client_data,
            "workers": self._get_workers(),
            "global_rate": global_rate,
            "msg": request.GET.get("msg", ""),
            "error": request.GET.get("error", ""),
            "warn": request.GET.get("warn", ""),
            "no_assign_link_id": request.GET.get("no_assign", ""),
            "invite_client_id": request.GET.get("invite_client_id", ""),
        })

    def post(self, request):
        from apps.clients.models import Client, ClientLink
        from django.shortcuts import redirect
        from urllib.parse import urlencode

        action = request.POST.get("action", "")

        # ── Global rates ──────────────────────────────────────────────────────
        if action == "update_rates":
            try:
                worker_rate = Decimal(request.POST.get("worker_rate", "0").strip())
                referral_rate = Decimal(request.POST.get("referral_rate", "0").strip())
                if worker_rate < 0 or referral_rate < 0:
                    raise ValueError("Ставки не могут быть отрицательными")
            except Exception as e:
                return redirect(f"/stats/clients/?error={e}")

            rate = GlobalRate.get()
            rate.worker_rate = worker_rate
            rate.referral_rate = referral_rate
            # updated_by: try to find the User from request.user (staff auth)
            try:
                from apps.users.models import User as BotUser
                bot_user = BotUser.objects.get(username=request.user.username)
                rate.updated_by = bot_user
            except Exception:
                pass
            rate.save()
            return redirect("/stats/clients/?msg=Ставки+обновлены")

        # ── Create client ─────────────────────────────────────────────────────
        if action == "create_client":
            nick = request.POST.get("nick", "").strip()
            rate = request.POST.get("rate", "0").strip()
            notes = request.POST.get("notes", "").strip()
            if not nick:
                return redirect("/stats/clients/?error=Укажите+ник+клиента")
            if Client.objects.filter(nick=nick).exists():
                return redirect(f"/stats/clients/?error=Клиент+{nick}+уже+существует")
            try:
                Client.objects.create(nick=nick, rate=Decimal(rate), notes=notes)
            except Exception as e:
                return redirect(f"/stats/clients/?error={e}")
            return redirect("/stats/clients/?msg=Клиент+создан")

        # ── Add link ──────────────────────────────────────────────────────────
        if action == "add_link":
            from apps.clients.services import AssignmentService, AutoModeService
            from apps.clients.tasks import notify_worker_assigned_sync

            client_id = request.POST.get("client_id", "")
            url = request.POST.get("url", "").strip()
            if not url:
                return redirect("/stats/clients/?error=Укажите+URL")
            try:
                client = Client.objects.get(pk=client_id)
                link = ClientLink.objects.create(client=client, url=url)
            except Client.DoesNotExist:
                return redirect("/stats/clients/?error=Клиент+не+найден")
            except Exception as e:
                return redirect(f"/stats/clients/?error={e}")

            # Auto mode: generate unique invite link for the worker
            invite_url = None
            if client.auto_mode and client.channel_id:
                invite_url = AutoModeService.create_invite_link_sync(
                    client.channel_id, label=f"link_{link.pk}"
                )
                if not invite_url:
                    # Fallback — use manual URL, log warning
                    import logging as _log
                    _log.getLogger(__name__).warning(
                        "add_link: invite link generation failed for client %s, using manual URL", client.pk
                    )

            assignment = AssignmentService.auto_assign(link, invite_url=invite_url)
            if assignment:
                worker_url = assignment.work_link.url if assignment.work_link else link.url
                notify_worker_assigned_sync(assignment.worker.telegram_id, worker_url, client.nick)
                mode_note = " [авто-ссылка]" if invite_url else ""
                msg = f"Ссылка добавлена{mode_note}. Исполнитель: {assignment.worker.display_name}"
                return redirect(f"/stats/clients/?msg={msg}")
            else:
                params = urlencode({"warn": "Исполнитель не найден — назначьте вручную", "no_assign": link.pk})
                return redirect(f"/stats/clients/?{params}")

        # ── Manual assign (for unassigned links) ─────────────────────────────
        if action == "manual_assign":
            from apps.clients.services import AssignmentService, AutoModeService
            from apps.clients.tasks import notify_worker_assigned_sync

            link_id = request.POST.get("link_id", "")
            worker_id = request.POST.get("worker_id", "")
            if not link_id or not worker_id:
                return redirect("/stats/clients/?error=Не+указана+ссылка+или+исполнитель")
            try:
                link = ClientLink.objects.select_related("client").get(pk=link_id)
                worker = User.objects.get(pk=worker_id)
            except (ClientLink.DoesNotExist, User.DoesNotExist):
                return redirect("/stats/clients/?error=Ссылка+или+воркер+не+найдены")

            invite_url = None
            if link.client.auto_mode and link.client.channel_id:
                invite_url = AutoModeService.create_invite_link_sync(
                    link.client.channel_id, label=f"wrkr_{worker.pk}"
                )

            try:
                assignment = AssignmentService.manual_assign(link, worker, invite_url=invite_url)
            except Exception as e:
                return redirect(f"/stats/clients/?error={e}")

            worker_url = assignment.work_link.url if assignment.work_link else link.url
            notify_worker_assigned_sync(worker.telegram_id, worker_url, link.client.nick)
            msg = f"Исполнитель {worker.display_name} назначен на ссылку"
            return redirect(f"/stats/clients/?msg={msg}")

        # ── Reassign worker (for already-assigned links) ──────────────────────
        if action == "reassign_worker":
            from apps.clients.models import LinkAssignment
            from apps.clients.services import AssignmentService, AutoModeService
            from apps.clients.tasks import notify_worker_assigned_sync, notify_worker_unassigned_sync

            link_id = request.POST.get("link_id", "")
            new_worker_id = request.POST.get("new_worker_id", "")
            if not link_id or not new_worker_id:
                return redirect("/stats/clients/?error=Не+указана+ссылка+или+новый+исполнитель")
            try:
                link = ClientLink.objects.select_related("client").get(pk=link_id)
                new_worker = User.objects.get(pk=new_worker_id)
            except (ClientLink.DoesNotExist, User.DoesNotExist):
                return redirect("/stats/clients/?error=Ссылка+или+воркер+не+найдены")

            # Remember old worker before reassigning
            existing = link.assignments.filter(is_active=True).first()
            old_worker = existing.worker if existing else None

            invite_url = None
            if link.client.auto_mode and link.client.channel_id:
                invite_url = AutoModeService.create_invite_link_sync(
                    link.client.channel_id, label=f"wrkr_{new_worker.pk}"
                )

            try:
                assignment = AssignmentService.manual_assign(link, new_worker, invite_url=invite_url)
            except Exception as e:
                return redirect(f"/stats/clients/?error={e}")

            worker_url = assignment.work_link.url if assignment.work_link else link.url
            notify_worker_assigned_sync(new_worker.telegram_id, worker_url, link.client.nick)

            if old_worker and old_worker.pk != new_worker.pk:
                notify_worker_unassigned_sync(old_worker.telegram_id, link.url, link.client.nick)

            msg = f"Исполнитель сменён: {old_worker.display_name if old_worker else '—'} → {new_worker.display_name}"
            return redirect(f"/stats/clients/?msg={msg}")

        # ── Setup auto mode (new UX: parse link → resolve → check → enable) ────
        if action == "setup_auto":
            from apps.clients.services import AutoModeService

            client_id = request.POST.get("client_id", "")
            channel_input = request.POST.get("channel_input", "").strip()
            if not channel_input:
                return redirect("/stats/clients/?error=Введите+ссылку+или+@username+канала")
            try:
                client = Client.objects.get(pk=client_id)
            except Client.DoesNotExist:
                return redirect("/stats/clients/?error=Клиент+не+найден")

            result = AutoModeService.resolve_and_setup(client, channel_input)
            if result.get("invite_link"):
                # Private channel — redirect showing manual Chat ID fallback for this client
                params = urlencode({"invite_client_id": client.pk, "warn": result["detail"]})
                return redirect(f"/stats/clients/?{params}")
            if result["ok"]:
                ch = client.channel_username or ""
                params = urlencode({"msg": f"Авто-режим включён. Канал {ch} подключён."})
                return redirect(f"/stats/clients/?{params}")
            else:
                params = urlencode({"error": result["detail"]})
                return redirect(f"/stats/clients/?{params}")

        # ── Recheck bot permissions + enable if OK ─────────────────────────────
        if action == "recheck_bot":
            from apps.clients.services import AutoModeService

            client_id = request.POST.get("client_id", "")
            try:
                client = Client.objects.get(pk=client_id)
            except Client.DoesNotExist:
                return redirect("/stats/clients/?error=Клиент+не+найден")

            result = AutoModeService.recheck_and_enable(client)
            if result["ok"]:
                ch = client.channel_username or ""
                params = urlencode({"msg": f"Проверка пройдена. Авто-режим включён. Канал: {ch}"})
                return redirect(f"/stats/clients/?{params}")
            else:
                params = urlencode({"error": result["detail"]})
                return redirect(f"/stats/clients/?{params}")

        # ── Disable auto mode ──────────────────────────────────────────────────
        if action == "disable_auto":
            client_id = request.POST.get("client_id", "")
            try:
                client = Client.objects.get(pk=client_id)
            except Client.DoesNotExist:
                return redirect("/stats/clients/?error=Клиент+не+найден")
            client.auto_mode = False
            client.save(update_fields=["auto_mode"])
            return redirect("/stats/clients/?msg=Авто-режим+отключён")

        # ── Reset auto mode (clear all channel settings) ───────────────────────
        if action == "reset_auto":
            from apps.clients.models import BotCheckStatus
            client_id = request.POST.get("client_id", "")
            try:
                client = Client.objects.get(pk=client_id)
            except Client.DoesNotExist:
                return redirect("/stats/clients/?error=Клиент+не+найден")
            client.channel_id = None
            client.channel_username = ""
            client.auto_mode = False
            client.bot_check_status = BotCheckStatus.UNCHECKED
            client.bot_check_detail = ""
            client.bot_check_at = None
            client.save(update_fields=[
                "channel_id", "channel_username", "auto_mode",
                "bot_check_status", "bot_check_detail", "bot_check_at",
            ])
            return redirect("/stats/clients/?msg=Настройки+авто-режима+сброшены")

        # ── Set channel (auto mode setup step 1) ──────────────────────────────
        if action == "set_channel":
            client_id = request.POST.get("client_id", "")
            channel_id_str = request.POST.get("channel_id", "").strip()
            try:
                client = Client.objects.get(pk=client_id)
            except Client.DoesNotExist:
                return redirect("/stats/clients/?error=Клиент+не+найден")

            if not channel_id_str:
                # Clear channel
                client.channel_id = None
                client.channel_username = ""
                client.auto_mode = False
                client.bot_check_status = "unchecked"
                client.bot_check_detail = ""
                client.bot_check_at = None
                client.save(update_fields=[
                    "channel_id", "channel_username", "auto_mode",
                    "bot_check_status", "bot_check_detail", "bot_check_at",
                ])
                return redirect("/stats/clients/?msg=Канал+удалён,+авто-режим+отключён")

            try:
                channel_id = int(channel_id_str)
            except ValueError:
                return redirect("/stats/clients/?error=Chat+ID+должен+быть+числом+(напр.+-1001234567890)")

            client.channel_id = channel_id
            client.bot_check_status = "unchecked"
            client.bot_check_detail = ""
            client.save(update_fields=["channel_id", "bot_check_status", "bot_check_detail"])
            return redirect(f"/stats/clients/?msg=Chat+ID+сохранён.+Теперь+проверьте+права+бота.")

        # ── Check bot permissions ──────────────────────────────────────────────
        if action == "check_bot":
            from apps.clients.services import AutoModeService
            client_id = request.POST.get("client_id", "")
            try:
                client = Client.objects.get(pk=client_id)
            except Client.DoesNotExist:
                return redirect("/stats/clients/?error=Клиент+не+найден")

            result = AutoModeService.check_permissions(client)
            if result["ok"]:
                return redirect(f"/stats/clients/?msg=Права+подтверждены.+Можно+включить+авто-режим.")
            else:
                detail = result.get("detail", "")
                params = urlencode({"error": f"Проверка не пройдена: {detail}"})
                return redirect(f"/stats/clients/?{params}")

        # ── Toggle auto mode ───────────────────────────────────────────────────
        if action == "toggle_auto":
            from apps.clients.models import BotCheckStatus
            client_id = request.POST.get("client_id", "")
            enable = request.POST.get("enable", "0") == "1"
            try:
                client = Client.objects.get(pk=client_id)
            except Client.DoesNotExist:
                return redirect("/stats/clients/?error=Клиент+не+найден")

            if enable and client.bot_check_status != BotCheckStatus.OK:
                return redirect(
                    "/stats/clients/?error=Нельзя+включить+авто-режим+—+сначала+проверьте+права+бота"
                )

            client.auto_mode = enable
            client.save(update_fields=["auto_mode"])
            if enable:
                return redirect("/stats/clients/?msg=Авто-режим+включён.+Новые+назначения+получат+уникальные+ссылки.")
            else:
                return redirect("/stats/clients/?msg=Авто-режим+отключён.")

        # ── Update attracted_count ────────────────────────────────────────────
        if action == "update_count":
            from apps.clients.models import LinkAssignment
            from apps.clients.services import AssignmentService
            from apps.users.services import UserService

            assignment_id = request.POST.get("assignment_id", "")
            count_str = request.POST.get("count", "").strip()
            try:
                count = int(count_str)
                if count < 0:
                    raise ValueError("Значение не может быть отрицательным")
                assignment = (
                    LinkAssignment.objects
                    .select_related("worker", "work_link")
                    .get(pk=assignment_id, is_active=True)
                )
            except LinkAssignment.DoesNotExist:
                return redirect("/stats/clients/?error=Назначение+не+найдено")
            except (ValueError, TypeError) as e:
                return redirect(f"/stats/clients/?error={e}")

            UserService.set_attracted_count(assignment.worker, count)
            AssignmentService.touch_count_updated(assignment)
            return redirect("/stats/clients/?msg=Количество+заявок+обновлено")

        # ── Delete client ─────────────────────────────────────────────────────
        if action == "delete_client":
            client_id = request.POST.get("client_id", "")
            try:
                Client.objects.get(pk=client_id).delete()
            except Client.DoesNotExist:
                pass
            return redirect("/stats/clients/?msg=Клиент+удалён")

        # ── Delete link ───────────────────────────────────────────────────────
        if action == "delete_link":
            link_id = request.POST.get("link_id", "")
            try:
                ClientLink.objects.get(pk=link_id).delete()
            except ClientLink.DoesNotExist:
                pass
            return redirect("/stats/clients/?msg=Ссылка+удалена")

        return redirect("/stats/clients/")
