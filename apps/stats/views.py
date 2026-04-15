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
        }
        return render(request, "stats_dashboard.html", context)


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
            from apps.clients.services import AssignmentService
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

            assignment = AssignmentService.auto_assign(link)
            if assignment:
                notify_worker_assigned_sync(
                    assignment.worker.telegram_id, link.url, client.nick,
                )
                msg = f"Ссылка добавлена. Исполнитель назначен: {assignment.worker.display_name}"
                return redirect(f"/stats/clients/?msg={msg}")
            else:
                params = urlencode({"warn": "Исполнитель не найден — назначьте вручную", "no_assign": link.pk})
                return redirect(f"/stats/clients/?{params}")

        # ── Manual assign (for unassigned links) ─────────────────────────────
        if action == "manual_assign":
            from apps.clients.services import AssignmentService
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

            try:
                assignment = AssignmentService.manual_assign(link, worker)
            except Exception as e:
                return redirect(f"/stats/clients/?error={e}")

            notify_worker_assigned_sync(worker.telegram_id, link.url, link.client.nick)
            msg = f"Исполнитель {worker.display_name} назначен на ссылку"
            return redirect(f"/stats/clients/?msg={msg}")

        # ── Reassign worker (for already-assigned links) ──────────────────────
        if action == "reassign_worker":
            from apps.clients.models import LinkAssignment
            from apps.clients.services import AssignmentService
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

            try:
                assignment = AssignmentService.manual_assign(link, new_worker)
            except Exception as e:
                return redirect(f"/stats/clients/?error={e}")

            # Notify new worker
            notify_worker_assigned_sync(new_worker.telegram_id, link.url, link.client.nick)

            # Notify old worker (if different from new)
            if old_worker and old_worker.pk != new_worker.pk:
                notify_worker_unassigned_sync(old_worker.telegram_id, link.url, link.client.nick)

            msg = f"Исполнитель сменён: {old_worker.display_name if old_worker else '—'} → {new_worker.display_name}"
            return redirect(f"/stats/clients/?msg={msg}")

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
