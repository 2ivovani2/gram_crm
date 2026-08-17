from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from urllib.parse import urlencode

from apps.crm.views import CRMOwnerMixin

from .forms import ChannelForm, OwnerForm, StatusForm
from .models import (
    OwnerChannel, OwnerChannelHistory, OwnerStatus, SavedOwnerFilter,
    TechnicalState, TelegramOwner,
)
from .services import create_owner, log_event, recalculate, update_owner


def _owner_queryset(request):
    return TelegramOwner.objects.filter(workspace=request.crm_workspace, deleted_at__isnull=True).select_related(
        "status", "responsible", "notes_updated_by", "last_login_by"
    ).annotate(active_channel_count=Count("channels", filter=Q(channels__is_archived=False), distinct=True))


class OwnersDashboardView(CRMOwnerMixin, View):
    template_name = "owners/dashboard.html"

    def get(self, request):
        queryset = _owner_queryset(request)
        show_archive = request.GET.get("archive") == "1"
        queryset = queryset.filter(archived_at__isnull=not show_archive)
        query = request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(phone__icontains=query)
                | Q(telegram_username__icontains=query.lstrip("@"))
                | Q(display_name__icontains=query)
                | Q(notes__icontains=query)
                | Q(status__name__icontains=query)
                | Q(responsible__telegram_username__icontains=query.lstrip("@"))
                | Q(channels__title__icontains=query)
                | Q(channels__username__icontains=query.lstrip("@"))
            ).distinct()

        status_id = request.GET.get("status")
        if status_id and status_id.isdecimal():
            queryset = queryset.filter(status_id=status_id)
        rank = request.GET.get("rank")
        if rank in {"S", "A", "B", "C", "D"}:
            queryset = queryset.filter(rank=rank)
        health = request.GET.get("health")
        if health == "critical":
            queryset = queryset.filter(health__lt=60)
        elif health == "warning":
            queryset = queryset.filter(health__gte=60, health__lt=80)
        elif health == "healthy":
            queryset = queryset.filter(health__gte=80)
        state = request.GET.get("state")
        if state == "attention":
            queryset = queryset.filter(
                Q(health__lt=60) | Q(is_scam=True) | Q(is_blocked=True)
                | ~Q(session_state=TechnicalState.READY)
                | ~Q(sim_state=TechnicalState.READY)
                | ~Q(twofa_state=TechnicalState.READY)
                | ~Q(proxy_state=TechnicalState.READY)
            ).distinct()
        elif state == "premium":
            queryset = queryset.filter(has_premium=True)
        elif state == "no_session":
            queryset = queryset.exclude(session_state=TechnicalState.READY)

        ordering = request.GET.get("sort", "attention")
        orderings = {
            "name": ("display_name", "id"),
            "health": ("-health", "display_name"),
            "health_asc": ("health", "display_name"),
            "rank": ("rank", "-health"),
            "channels": ("-active_channel_count", "display_name"),
            "updated": ("-updated_at",),
            "attention": ("health", "-is_blocked", "-is_scam", "display_name"),
        }
        queryset = queryset.order_by(*orderings.get(ordering, orderings["attention"]))
        page = Paginator(queryset, 30).get_page(request.GET.get("page"))
        saved_filters = list(SavedOwnerFilter.objects.filter(
            workspace=request.crm_workspace, user=request.crm_user
        ))
        for saved in saved_filters:
            saved.query_string = urlencode(saved.query)

        all_active = _owner_queryset(request).filter(archived_at__isnull=True)
        aggregate = all_active.aggregate(total=Count("id", distinct=True), avg_health=Avg("health"), channels=Count("channels", distinct=True))
        attention = all_active.filter(
            Q(health__lt=60) | Q(is_scam=True) | Q(is_blocked=True)
            | ~Q(session_state=TechnicalState.READY)
            | ~Q(sim_state=TechnicalState.READY)
            | ~Q(twofa_state=TechnicalState.READY)
            | ~Q(proxy_state=TechnicalState.READY)
        ).distinct().count()
        ctx = self.get_crm_context(request)
        ctx.update({
            "page": page,
            "owners": page.object_list,
            "statuses": OwnerStatus.objects.filter(workspace=request.crm_workspace),
            "stats": {
                **aggregate,
                "avg_health": round(aggregate["avg_health"] or 0),
                "attention": attention,
                "archived": TelegramOwner.objects.filter(
                    workspace=request.crm_workspace, archived_at__isnull=False, deleted_at__isnull=True
                ).count(),
            },
            "filters": request.GET,
            "show_archive": show_archive,
            "saved_filters": saved_filters,
        })
        return render(request, self.template_name, ctx)


class OwnerDetailView(CRMOwnerMixin, View):
    template_name = "owners/detail.html"

    def get(self, request, pk):
        owner = get_object_or_404(_owner_queryset(request), pk=pk)
        ctx = self.get_crm_context(request)
        ctx.update({
            "owner": owner,
            "channels": owner.channels.order_by("is_archived", "title"),
            "activity": owner.activity.select_related("actor")[:30],
            "channel_form": ChannelForm(workspace=request.crm_workspace),
            "transfer_targets": TelegramOwner.objects.filter(
                workspace=request.crm_workspace, archived_at__isnull=True, deleted_at__isnull=True
            ).exclude(pk=owner.pk).order_by("display_name", "telegram_username"),
        })
        return render(request, self.template_name, ctx)


class OwnerCreateView(CRMOwnerMixin, View):
    template_name = "owners/form.html"

    def get(self, request):
        form = OwnerForm(workspace=request.crm_workspace)
        return self._render(request, form)

    def post(self, request):
        form = OwnerForm(request.POST, workspace=request.crm_workspace)
        if form.is_valid():
            data = {name: form.cleaned_data[name] for name in form.Meta.fields}
            owner = create_owner(
                workspace=request.crm_workspace, actor=request.crm_user,
                cleaned_data=data, secrets=form.secret_values,
            )
            messages.success(request, "Владелец создан, ранг и индекс здоровья рассчитаны.")
            return redirect("owners:detail", pk=owner.pk)
        return self._render(request, form, status=400)

    def _render(self, request, form, status=200):
        ctx = self.get_crm_context(request)
        ctx.update({"form": form, "owner": None})
        return render(request, self.template_name, ctx, status=status)


class OwnerEditView(CRMOwnerMixin, View):
    template_name = "owners/form.html"

    def get_owner(self, request, pk):
        return get_object_or_404(
            TelegramOwner, pk=pk, workspace=request.crm_workspace, deleted_at__isnull=True
        )

    def get(self, request, pk):
        return self._render(request, self.get_owner(request, pk))

    def post(self, request, pk):
        owner = self.get_owner(request, pk)
        form = OwnerForm(request.POST, instance=owner, workspace=request.crm_workspace)
        if form.is_valid():
            data = {name: form.cleaned_data[name] for name in form.Meta.fields}
            # ModelForm mutates its instance during validation. Reload under the
            # service transaction so change detection and audit snapshots are exact.
            owner = TelegramOwner.objects.get(pk=owner.pk, workspace=request.crm_workspace)
            owner = update_owner(owner=owner, actor=request.crm_user, cleaned_data=data, secrets=form.secret_values)
            messages.success(request, "Данные владельца обновлены.")
            return redirect("owners:detail", pk=owner.pk)
        return self._render(request, owner, form=form, status=400)

    def _render(self, request, owner, form=None, status=200):
        form = form or OwnerForm(instance=owner, workspace=request.crm_workspace)
        ctx = self.get_crm_context(request)
        ctx.update({"form": form, "owner": owner})
        return render(request, self.template_name, ctx, status=status)


class OwnerActionView(CRMOwnerMixin, View):
    def post(self, request, pk, action):
        owner = get_object_or_404(
            TelegramOwner, pk=pk, workspace=request.crm_workspace, deleted_at__isnull=True
        )
        if action == "archive" and not owner.archived_at:
            owner.archived_at = timezone.now()
            owner.save(update_fields=("archived_at", "updated_at"))
            log_event(owner, request.crm_user, "archived", "Владелец архивирован")
            messages.success(request, "Владелец перемещён в архив.")
        elif action == "restore" and owner.archived_at:
            owner.archived_at = None
            owner.save(update_fields=("archived_at", "updated_at"))
            log_event(owner, request.crm_user, "restored", "Владелец восстановлен из архива")
            messages.success(request, "Владелец восстановлен.")
        elif action == "delete":
            if owner.channels.filter(is_archived=False).exists():
                messages.error(request, "Нельзя удалить владельца с активными каналами. Сначала перенесите или архивируйте их.")
                return redirect("owners:detail", pk=owner.pk)
            owner.deleted_at = timezone.now()
            owner.archived_at = owner.archived_at or owner.deleted_at
            owner.save(update_fields=("deleted_at", "archived_at", "updated_at"))
            log_event(owner, request.crm_user, "deleted", "Владелец удалён из рабочих реестров")
            messages.success(request, f"Владелец «{owner.label}» удалён из рабочих реестров; аудит сохранён.")
            return redirect("owners:dashboard")
        else:
            raise Http404
        return redirect("owners:detail", pk=owner.pk)


class OwnerChannelCreateView(CRMOwnerMixin, View):
    def post(self, request, pk):
        owner = get_object_or_404(
            TelegramOwner, pk=pk, workspace=request.crm_workspace, deleted_at__isnull=True
        )
        form = ChannelForm(request.POST, workspace=request.crm_workspace)
        if form.is_valid():
            with transaction.atomic():
                channel = form.save(commit=False)
                channel.workspace = request.crm_workspace
                channel.owner = owner
                channel.save()
                OwnerChannelHistory.objects.create(
                    channel=channel, new_owner=owner, actor=request.crm_user,
                    actor_role=request.crm_user.get_role_display(), reason="Первичная привязка",
                )
                log_event(owner, request.crm_user, "channel_added", f"Добавлен канал «{channel.title}»")
                recalculate(owner, request.crm_user, "Добавлен канал")
            messages.success(request, "Канал привязан.")
        else:
            messages.error(request, "Не удалось привязать канал: проверьте уникальность Telegram ID.")
        return redirect("owners:detail", pk=owner.pk)


class OwnerChannelActionView(CRMOwnerMixin, View):
    def post(self, request, pk, channel_pk, action):
        owner = get_object_or_404(
            TelegramOwner, pk=pk, workspace=request.crm_workspace, deleted_at__isnull=True
        )
        channel = get_object_or_404(OwnerChannel, pk=channel_pk, owner=owner, workspace=request.crm_workspace)
        if action == "archive":
            channel.is_archived = True
            channel.save(update_fields=("is_archived", "updated_at"))
            log_event(owner, request.crm_user, "channel_archived", f"Архивирован канал «{channel.title}»")
            recalculate(owner, request.crm_user, "Архивирован канал")
        elif action == "transfer":
            target = get_object_or_404(
                TelegramOwner,
                pk=request.POST.get("target_owner"), workspace=request.crm_workspace,
                archived_at__isnull=True, deleted_at__isnull=True,
            )
            with transaction.atomic():
                channel = OwnerChannel.objects.select_for_update().get(pk=channel.pk)
                previous = channel.owner
                channel.owner = target
                channel.attached_at = timezone.now()
                channel.save(update_fields=("owner", "attached_at", "updated_at"))
                OwnerChannelHistory.objects.create(
                    channel=channel, previous_owner=previous, new_owner=target,
                    actor=request.crm_user, actor_role=request.crm_user.get_role_display(),
                    reason=request.POST.get("reason", "")[:300],
                )
                log_event(previous, request.crm_user, "channel_transferred", f"Канал «{channel.title}» передан владельцу {target.label}")
                log_event(target, request.crm_user, "channel_received", f"Получен канал «{channel.title}» от владельца {previous.label}")
                recalculate(previous, request.crm_user, "Передан канал")
                recalculate(target, request.crm_user, "Получен канал")
            messages.success(request, f"Канал передан владельцу {target.label}.")
        else:
            raise Http404
        return redirect("owners:detail", pk=owner.pk)


class OwnerActivityView(CRMOwnerMixin, View):
    template_name = "owners/activity.html"

    def get(self, request, pk):
        owner = get_object_or_404(
            TelegramOwner, pk=pk, workspace=request.crm_workspace, deleted_at__isnull=True
        )
        events = owner.activity.select_related("actor")
        query = request.GET.get("q", "").strip()
        if query:
            events = events.filter(
                Q(description__icontains=query) | Q(actor_username__icontains=query) | Q(event_type__icontains=query)
            )
        page = Paginator(events, 50).get_page(request.GET.get("page"))
        ctx = self.get_crm_context(request)
        ctx.update({"owner": owner, "page": page, "events": page.object_list})
        return render(request, self.template_name, ctx)


class OwnerStatusesView(CRMOwnerMixin, View):
    template_name = "owners/statuses.html"

    def get(self, request):
        return self._render(request, StatusForm(workspace=request.crm_workspace))

    def post(self, request):
        form = StatusForm(request.POST, workspace=request.crm_workspace)
        if form.is_valid():
            status = form.save(commit=False)
            status.workspace = request.crm_workspace
            status.save()
            messages.success(request, "Статус создан и добавлен в легенду.")
            return redirect("owners:statuses")
        return self._render(request, form, status=400)

    def _render(self, request, form, status=200):
        statuses = OwnerStatus.objects.filter(workspace=request.crm_workspace).annotate(owner_count=Count("owners"))
        ctx = self.get_crm_context(request)
        ctx.update({"form": form, "statuses": statuses})
        return render(request, self.template_name, ctx, status=status)


class OwnerStatusActionView(CRMOwnerMixin, View):
    def post(self, request, pk, action):
        status = get_object_or_404(OwnerStatus, pk=pk, workspace=request.crm_workspace)
        if action == "update":
            form = StatusForm(request.POST, instance=status, workspace=request.crm_workspace)
            if form.is_valid():
                form.save()
                messages.success(request, "Статус обновлён у всех владельцев.")
            else:
                messages.error(request, "Не удалось обновить статус: проверьте поля.")
        elif action == "delete":
            if status.is_system:
                messages.error(request, "Системный статус удалить нельзя.")
            else:
                owners = status.owners.filter(deleted_at__isnull=True)
                replacement_id = request.POST.get("replacement_status")
                if owners.exists() and not replacement_id:
                    messages.error(request, "Выберите новый статус для связанных владельцев.")
                else:
                    replacement = None
                    if replacement_id:
                        replacement = get_object_or_404(
                            OwnerStatus, pk=replacement_id, workspace=request.crm_workspace
                        )
                    with transaction.atomic():
                        for owner in owners.select_for_update():
                            owner.status = replacement
                            owner.save(update_fields=("status", "updated_at"))
                            log_event(
                                owner, request.crm_user, "status_changed",
                                f"Статус: {status.name} → {replacement.name if replacement else 'Без статуса'}",
                            )
                        status.delete()
                    messages.success(request, "Статус удалён, владельцы переназначены.")
        else:
            raise Http404
        return redirect("owners:statuses")


class SavedFilterActionView(CRMOwnerMixin, View):
    ALLOWED_KEYS = {"q", "status", "rank", "health", "state", "sort", "archive"}

    def post(self, request, action, pk=None):
        if action == "save":
            name = request.POST.get("name", "").strip()[:100]
            query = {key: request.POST.get(key, "") for key in self.ALLOWED_KEYS if request.POST.get(key)}
            if not name:
                messages.error(request, "Укажите название фильтра.")
            else:
                SavedOwnerFilter.objects.update_or_create(
                    workspace=request.crm_workspace, user=request.crm_user, name=name,
                    defaults={"query": query},
                )
                messages.success(request, "Набор фильтров сохранён.")
        elif action == "delete" and pk is not None:
            SavedOwnerFilter.objects.filter(
                pk=pk, workspace=request.crm_workspace, user=request.crm_user
            ).delete()
            messages.success(request, "Сохранённый фильтр удалён.")
        else:
            raise Http404
        return redirect("owners:dashboard")
