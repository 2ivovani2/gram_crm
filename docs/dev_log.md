# Dev Log — spambotcontrol

---

## 2026-04-12 — Fix: CRM owner workspace access bug

**Причина:** `Workspace.created_by` — информационный ForeignKey, не создаёт `WorkspaceMembership`. Весь контроль доступа в `CRMLoginMixin._resolve_workspace_and_membership` работает только через `WorkspaceMembership`. Пользователь ставил себя `created_by` в Django admin → membership не создавался → `crm_is_owner=False` → ограниченный экран.

**Изменения:**

`apps/crm/admin.py` — `WorkspaceAdmin.save_model`:
- При создании workspace с `created_by` автоматически создаётся `WorkspaceMembership(role=OWNER)` для этого пользователя

`apps/crm/management/commands/setup_crm.py`:
- `--fix-created-by` — сканирует все workspace, создаёт OWNER membership там где `created_by` не имеет membership (для фикса существующих данных)
- `--workspace <slug>` — указать конкретный workspace вместо default `gramly`

**Как проверить:**
```bash
# Фикс существующих workspace (один раз на проде):
python manage.py setup_crm --fix-created-by

# Добавить owner вручную по telegram_id:
python manage.py setup_crm --add-owner <telegram_id>

# Проверить состав участников:
python manage.py setup_crm
```

**Сценарии доступа:**
- Нет membership → ограниченный dashboard (no_workspace экран)
- OWNER → полный доступ ко всем страницам
- Несколько workspace → переключение через `/crm/switch-workspace/` (уже реализовано)
- Разные роли → каждый workspace независим

---

## 2026-04-12 — Production domain migration: gramly.tech + Let's Encrypt

### Что изменено

**`nginx/prod.conf`** — полный рерайт:
- `server_name gramly.tech www.gramly.tech`
- HTTP → HTTPS редирект + ACME challenge passthrough
- TLS через Let's Encrypt: `/etc/letsencrypt/live/gramly.tech/fullchain.pem`
- `Strict-Transport-Security` header
- Удалён self-signed cert (VPS IP-based подход упразднён)

**`nginx/prod-init.conf`** (новый) — временный HTTP-only конфиг для первичного получения сертификата

**`docker-compose.yml`**:
- nginx монтирует `letsencrypt` и `certbot_www` volumes вместо `./ssl`
- Добавлен сервис `certbot` (certbot/certbot) с авто-обновлением каждые 12h

**`scripts/prod_up.sh`** — полный рерайт:
- Читает `DOMAIN` из .env вместо `VPS_IP`
- Если сертификат отсутствует: запускает nginx в HTTP-mode, получает LE cert, перезагружает nginx с HTTPS
- Webhook регистрируется по `https://gramly.tech/bot/webhook/` без `--certificate` параметра

**`config/settings/prod.py`**:
- `CSRF_TRUSTED_ORIGINS = ["https://gramly.tech", "https://www.gramly.tech"]`
- `SECURE_CROSS_ORIGIN_OPENER_POLICY = None` (Telegram Login Widget)
- Читает `DOMAIN` из env динамически

**`.env.example`**:
- Добавлены `DOMAIN=gramly.tech`, `CERTBOT_EMAIL`
- Убрал `VPS_IP`
- Обновлены все примерные URLs на gramly.tech

**`Makefile`**:
- Добавлены `webhook-info-prod`, `crm-setup-prod`, `prod-renew-cert`

---

## 2026-04-12 — Financial logic revision: WorkLink history model

### Проблема
Старая модель хранила `User.attracted_count` как единственное поле. При замене ссылки admin сбрасывал счётчик → `recalculate_balance()` пересчитывал balance в 0 → выплаченные ранее деньги "исчезали" из системы. Риск двойной выплаты при параллельных заявках на вывод.

### Что изменено

**`apps/users/models.py`** — добавлена модель `WorkLink`:
- Хранит историю всех рабочих ссылок пользователя с замороженными счётчиками
- Ровно одна `is_active=True` запись на пользователя
- Архивные ссылки никогда не обнуляются — исторические начисления сохраняются

**Финансовая модель (задокументирована в коде и CLAUDE.md):**
- `personal_earned = SUM(link.attracted_count for ALL work_links) × personal_rate`
- `referral_earned = SUM(SUM(ref_link.attracted_count) × referral_rate for direct_referrals)`
- `balance = max(0, personal_earned + referral_earned − approved_withdrawals)`

**`apps/users/services.py`** — полный рерайт:
- `recalculate_balance`: агрегирует ВСЕ WorkLinks (не только активную)
- `replace_work_link`: архивирует старую ссылку (замораживает счётчик), создаёт новую с 0
- `set_attracted_count`: обновляет только АКТИВНУЮ WorkLink
- `get_earnings_breakdown`: возвращает полный разбор: personal/referral/gross/withdrawn/balance/attracted breakdown

**`apps/withdrawals/services.py`**:
- Проверка `amount ≤ user.balance` при создании заявки
- Блокировка повторной заявки если уже есть PENDING (защита от двойной выплаты)

**`apps/telegram_bot/handlers/worker/profile.py`** — воркер видит полный разбор начислений

**`apps/telegram_bot/handlers/admin/users.py`** — карточка пользователя показывает earnings breakdown

**`apps/telegram_bot/handlers/admin/settings.py`** — `set_work_url` заменён полным FSM `replace_work_link`:
1. Показывает текущую ссылку + архивную историю
2. Предупреждает о сбросе счётчика (старые начисления сохранятся)
3. Принимает новый URL или `-` для удаления
4. Экран подтверждения → вызов `UserService.replace_work_link()`

**`apps/users/admin.py`**:
- `WorkLinkInline` — полная история ссылок в карточке пользователя Django admin
- `earnings_breakdown_display` — readonly таблица с разбором начислений

**Миграции:**
- `0006_worklink.py` — схема таблицы `WorkLink`
- `0007_worklink_seed_from_users.py` — data migration: создаёт начальные WorkLink для всех пользователей у которых есть `work_url` или `attracted_count > 0`

**`apps/telegram_bot/states.py`** — добавлен `AdminReplaceWorkLinkState`

---

## 2026-04-11 — Landing page: premium redesign + CRM accessibility

### Проблемы старого лендинга
- CRM отсутствовал в nav и hero-блоке — единственная кнопка была закопана секцией #8
- Inline-стили в CRM-блоке делали его инородным элементом (не часть дизайн-системы)
- Все секции одного визуального веса — отсутствие иерархии
- Фиксированные padding значения ломали мобильный вид
- Нет единой системы кнопок для разных levels of prominence

### Что переделано (`templates/landing.html` — полный редизайн)

**Nav (sticky, frosted glass):**
- Добавлена кнопка `CRM` (фиолетовая, border-style) — всегда видна в шапке
- `Admin Panel` — primary cyan кнопка рядом
- `padding: clamp()` — адаптивен

**Hero block:**
- **Primary CTA изменён** с "Admin Panel" на "Открыть CRM" (фиолетовая кнопка с glow)
- "Admin Panel" переведён в secondary (border-cyan)
- 4 уровня кнопок: `btn-crm`, `btn-admin`, `btn-ghost`, `btn-primary`

**CRM Section (новая позиция — ДО features):**
- Полноценная two-column секция с mock-up CRM интерфейса справа
- Реальные данные в мок (Cash Flow, заявки, статус отчёта)
- Дизайн-система: класс `crm-*`, без inline-стилей
- CTA "Войти в CRM" прямо в секции

**CRM buttons count:** 4 на странице (nav, hero, crm-section, final CTA)

**Дизайн-система:**
- `clamp()` для всех отступов и font-size — работает на любом экране
- Убраны все inline-стили
- Единый grid-gap и border-radius по CSS vars
- `@media` breakpoints: 900px, 640px, 400px

**Анимации:** `fadeUp`, `shimmer`, `pulse`, `floatA`/`floatB` для pills

### Где теперь кнопки CRM
1. **Nav** (sticky) — виден на любом скролле
2. **Hero** — первая кнопка до fold, primary purple
3. **CRM Section** — dedicated секция с мокапом
4. **Final CTA** — closing section

---

## 2026-04-11 — Fix: Telegram Login Widget broken (silent redirect loop)

### Симптом
Пользователь нажимал виджет, подтверждал вход, popup закрывался, но браузер возвращал обратно на `/crm/login/`. Callback `/crm/auth/callback/` никогда не вызывался.

### Точная причина
В `templates/crm/login.html` был относительный `data-auth-url`:
```html
data-auth-url="{% url 'crm:auth_callback' %}"
```
Рендерилось как `/crm/auth/callback/`. Telegram виджет отправляет этот URL в `oauth.telegram.org` как параметр `return_to`. Сервер `oauth.telegram.org` трактует относительный путь как относительный к своему домену → редирект на `https://oauth.telegram.org/crm/auth/callback/` → 404 → popup закрывается → пользователь остаётся на login.

### Дополнительная проблема
Без `SECURE_PROXY_SSL_HEADER` Django не читает `X-Forwarded-Proto: https` от ngrok → `request.build_absolute_uri()` возвращал `http://` вместо `https://`. Telegram Widget принимает только HTTPS callback.

### Исправления
1. `config/settings/dev.py` — добавлено:
   ```python
   SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
   ```
2. `apps/crm/views.py` — `LoginView.get()` теперь передаёт абсолютный URL в контекст:
   ```python
   auth_callback_url = request.build_absolute_uri(reverse("crm:auth_callback"))
   ```
3. `templates/crm/login.html` — изменено:
   ```html
   data-auth-url="{{ auth_callback_url }}"
   ```
   Теперь рендерится как `https://chigger-robust-unicorn.ngrok-free.app/crm/auth/callback/`.

### Результат проверки
```
$ curl -sf https://chigger-robust-unicorn.ngrok-free.app/crm/login/ | grep data-auth-url
data-auth-url="https://chigger-robust-unicorn.ngrok-free.app/crm/auth/callback/"
```
Callback теперь попадает на правильный домен. Telegram корректно редиректит popup, session устанавливается, пользователь попадает на `/crm/dashboard/`.

---

## 2026-04-11 — CRM: redesign access control model

### Проблема
Старая модель: вход в CRM требовал `WorkspaceMembership`. `CRMOwnerMixin` выполнял view-метод до проверки роли (критический баг). Роли FINANCE/APPLICATIONS усложняли проверки без нужды.

### Новая access-control модель

**Уровни доступа:**
- `Authenticated` (любой User из БД): dashboard с welcome-экраном, без финансовых данных
- `Owner` (WorkspaceMembership.role == OWNER): полный доступ

**Ключевые изменения:**
- `TelegramAuthCallbackView`: убрана авто-регистрация пользователей + убрана проверка membership при входе. Любой существующий в БД User может войти.
- `CRMLoginMixin`: добавлен hook `check_crm_permissions()` — вызывается МЕЖДУ установкой атрибутов и запуском view-метода. Устанавливает `request.crm_is_owner`.
- `CRMOwnerMixin`: переопределяет хук вместо `super().dispatch()` pattern — исправлен баг, при котором view выполнялся до проверки роли.
- `DashboardView`: ветвится на `is_owner` — owner видит полные данные, остальные — welcome-screen без финансовых данных.
- `HistoryView`, `ReportDetailView`, `ExportView`: переведены на `CRMOwnerMixin`.
- `FinanceEntryView`, `ApplicationEntryView`: убраны `_check_access()` методы, перешли на `CRMOwnerMixin`.
- Шаблоны: вся навигация sidebar использует `is_owner` вместо `membership.can_*`.

**Защита:** backend блокирует owner-only endpoints до выполнения view — прямой HTTP-запрос к закрытому URL вернёт 403 независимо от UI.

---

Chronological record of architectural decisions, changes, and rationale.
Updated after every significant step.

---

## 2026-04-11 — GRAMLY CRM: новый веб-сервис в монорепе

### Что сделано

Добавлен полноценный CRM-модуль `apps/crm/` — отдельный, архитектурно изолированный веб-сервис внутри того же Django-проекта.

### Архитектурное решение

**Авторизация — Telegram Login Widget:**
- Используется официальный Telegram Login Widget (HMAC-SHA256 с bot_token как ключом)
- Никаких паролей — пользователь кликает "Войти через Telegram", подтверждает в боте
- Криптографически верифицировано, данные не старше 24ч (`auth_date` проверяется)
- Сессия сохраняется в Django session (`crm_user_id`)
- Пользователь должен быть участником хотя бы одного Workspace с `is_active=True`

**Workspace (multi-tenant) архитектура:**
- `Workspace` — верхний уровень изоляции. Все данные привязаны к workspace.
- `WorkspaceMembership` — роль пользователя в конкретном workspace (один User может иметь разные роли в разных Workspace)
- Роли: `OWNER`, `FINANCE`, `APPLICATIONS`, `VIEWER`
- Текущий проект GRAMLY — первый Workspace (slug=`gramly`)

**Модели:**
- `Workspace` — рабочее пространство / тенант
- `WorkspaceMembership` — участник пространства с ролью
- `WeeklyPlan` — план ПП/Привата на неделю (ISO week, Mon-based)
- `FinanceEntry` — ежедневные финансовые данные (unique per workspace+date)
- `ApplicationEntry` — ежедневные данные по заявкам (unique per workspace+date)
- `DailySummaryReport` — авто-генерируемый сводный отчёт (после двух entries)
- `DeadlineMiss` — фиксация пропуска дедлайна с указанием какой блок не внесён

**Авто-генерация отчёта:**
- После каждого `save_finance_entry` / `save_application_entry` → `_try_generate_report()`
- Если оба entry есть → `ReportService.generate()` → `DailySummaryReport.update_or_create()`
- Отчёт денормализован (snapshot полей) для стабильности истории
- После генерации → Celery task → Telegram уведомление OWNER-ам

**Celery tasks (CRM):**
- `crm_check_deadline_task` — 00:05 МСК: проверяет все Workspace, создаёт `DeadlineMiss`, уведомляет OWNER-ов
- `crm_weekly_report_task` — каждый понедельник 08:00 МСК: отправляет недельную сводку
- `send_crm_report_notification_task` — триггерится после генерации отчёта

**Excel export:** `ExportService.export_to_excel()` через `openpyxl`. Цветные заголовки, авто-ширина колонок.

### Файлы созданы

| Файл | Назначение |
|------|------------|
| `apps/crm/__init__.py` | |
| `apps/crm/apps.py` | AppConfig |
| `apps/crm/models.py` | Все модели CRM |
| `apps/crm/services.py` | Вся бизнес-логика (TelegramAuth, WorkspaceService, WeeklyPlanService, EntryService, ReportService, DashboardService, DeadlineService, ExportService) |
| `apps/crm/forms.py` | Django forms (FinanceEntryForm, ApplicationEntryForm, WeeklyPlanForm, MemberRoleForm, AddMemberForm, DateRangeForm) |
| `apps/crm/views.py` | Web views с CRMLoginMixin / CRMOwnerMixin |
| `apps/crm/urls.py` | URL routing (namespace=crm, prefix=/crm/) |
| `apps/crm/tasks.py` | Celery tasks |
| `apps/crm/admin.py` | Django admin (Unfold) |
| `apps/crm/migrations/0001_initial.py` | Initial migration |
| `templates/crm/base.html` | Sidebar layout, dark SaaS UI |
| `templates/crm/login.html` | Страница входа (Telegram Widget) |
| `templates/crm/dashboard.html` | Дашборд |
| `templates/crm/entry_finance.html` | Форма Cash Flow |
| `templates/crm/entry_applications.html` | Форма Заявки |
| `templates/crm/history.html` | История + фильтр по датам |
| `templates/crm/report_detail.html` | Детальный отчёт |
| `templates/crm/admin/index.html` | Admin panel (OWNER) |
| `templates/crm/admin/members.html` | Управление участниками |
| `templates/crm/admin/plans.html` | Недельные планы |
| `templates/crm/no_workspace.html` | Страница "нет доступа" |
| `templates/crm/403.html` | Страница 403 |

### Файлы изменены

| Файл | Что изменено |
|------|--------------|
| `config/settings/base.py` | `apps.crm` в INSTALLED_APPS; MEDIA_URL/MEDIA_ROOT; CRM в Celery routes/beat; CRM секция в Unfold sidebar |
| `config/urls.py` | `include("apps.crm.urls")` под `/crm/`; media serving в DEBUG |
| `pyproject.toml` | Добавлен `openpyxl>=3.1.0` |
| `templates/landing.html` | CRM promo block, кнопка "Открыть CRM" в nav и CTA |

### UI/UX подход

Использован качественный hand-crafted fallback (21st.dev Magic MCP и ui-ux-pro-max skill не проверялись на доступность в данной сессии, использован самостоятельно написанный dark SaaS дашборд):
- Тёмная тема (идентичный стиль с лендингом: `#07090f`, `--accent: #00d4ff`, `--accent2: #7c3aed`)
- Sidebar layout с навигацией по ролям (видны только доступные разделы)
- KPI карточки, прогресс-бары плана, таблицы с hover
- Адаптивность (мобильный — sidebar скрывается)
- Realtime-preview в формах (сальдо, средний заработок за заявку)

### Как запустить / проверить

```bash
# 1. Пересобрать контейнеры (добавлена зависимость openpyxl)
make dev   # или docker compose up -d --build

# 2. Применить миграцию CRM
docker compose exec web python manage.py migrate

# 3. Создать первый Workspace и добавить себя как OWNER
docker compose exec web python manage.py shell
>>> from apps.crm.services import WorkspaceService
>>> ws = WorkspaceService.get_or_create_default()
>>> from apps.users.models import User
>>> user = User.objects.get(telegram_id=YOUR_TG_ID)
>>> WorkspaceService.add_member(ws, user, role='owner')

# 4. Открыть /crm/login/ → Telegram Login Widget → войти
```

### Сценарии проверки

1. **Вход:** `/crm/login/` → кнопка Telegram → подтвердить → попасть на дашборд
2. **Finance entry:** `/crm/entry/finance/` → заполнить все поля → Submit → видеть данные на дашборде
3. **App entry:** `/crm/entry/apps/` → заполнить → Submit → видеть `✅ Все данные внесены` + сводный отчёт
4. **Отчёт:** Кликнуть "Полный отчёт" → страница report_detail с KPI, прогресс-барами, текстом
5. **История:** `/crm/history/` → выбрать диапазон дат → таблица
6. **Admin:** `/crm/admin/` → видно только при роли OWNER
7. **Планы:** `/crm/admin/plans/` → создать план → обновить отчёт → увидеть % выполнения
8. **Участники:** `/crm/admin/members/` → добавить по Telegram ID → сменить роль → отозвать
9. **Export:** `/crm/export/?start=2026-04-01&end=2026-04-11` → скачать .xlsx

---

## 2026-04-11 — Subscription gate: "Check subscription" button + redirect to main menu

### What was done

Improved the subscription gate UX. Previously: user got a message with only a "Subscribe" link and had to manually retry their action. Now:

**Keyboard:**
```
[ 📢 Подписаться на канал ]  ← URL button
[ ✅ Проверить подписку   ]  ← callback button
```

**"Check subscription" flow:**
1. User clicks "✅ Проверить подписку"
2. `SubscriptionMiddleware` runs (as always):
   - Still not subscribed → middleware blocks, sends gate message again (no extra code needed)
   - Now subscribed → middleware lets through to `cb_check_subscription`
3. `cb_check_subscription` shows the appropriate main menu based on role (admin → admin panel, curator → curator menu, worker → worker menu)

The middleware itself handles the "not subscribed" retry — zero code duplication.

**Files changed:**
- `apps/telegram_bot/callbacks.py`: added `SubscriptionCallback(prefix="sub", action: str)`
- `apps/telegram_bot/subscription.py`: updated keyboard (`_build_gate_keyboard`), updated text, added `router` + `cb_check_subscription` handler
- `apps/telegram_bot/router.py`: included `subscription_router` first (before all other routers)

---

## 2026-04-11 — Reminder window fix, MissedDay model, backdated entry, stats periods

### What was done

#### 1. Fixed reminder window (23:01–01:00 МСК)

`check_missing_daily_report_task` previously spammed admins until 06:00 МСК and had a bug: after midnight `timezone.localdate()` returns the next day, so the check was looking for the wrong date.

New logic:
- `control_date` = yesterday when `hour < 2`, today otherwise
- **Reminder window 23:01–00:59 МСК**: send urgent reminder if no report for `control_date`
- **Mark-missed window 01:00–01:59 МСК**: if still no report → `MissedDay.get_or_create(date=control_date)` (idempotent via unique constraint, runs 4× but only creates once)
- **Outside both windows**: no-op

#### 2. New model: `MissedDay`

`apps/stats/models.py` — new model tracking days without a DailyReport:
- `date` (unique) — the missed calendar day
- `detected_at` — when the task created the record
- `filled_at` / `filled_by` — populated when admin later submits data backdated to that date

Migration: `apps/stats/migrations/0005_missedday.py`

Registered in Django admin (`MissedDayAdmin`) and UNFOLD sidebar ("Пропущенные дни").

#### 3. Backdated entry in Telegram bot

`handlers/admin/daily.py` now supports entering data for past dates:

Entry flow:
1. Tap "📋 Ввод данных" → see today's status
2. If there are unfilled recent days (last 7 excl. today) → button "📅 Внести за другой день" appears
3. Date picker shows last 7 days without a DailyReport; missed days highlighted with 🔴
4. Select date → same 4-step FSM form, date stored in FSM state as ISO string
5. On confirm: `DailyReportService.create_report(date=selected_date, ...)` + auto-marks MissedDay as filled

Date is shown throughout the form ("Ввод данных за 10.04.2025 (задним числом)").
`DailyReportService.create_report()` now calls `MissedDay.objects.filter(...).update(filled_at=...)` after saving.

Fixed bug: was using `datetime.date.today()` instead of `timezone.localdate()` throughout.

#### 4. Stats period selector in Telegram bot

`handlers/admin/stats.py` now accepts `period` parameter: `today | week | last_week | month`

New `AdminStatsCallback` field: `period: str = "week"`.
New handlers: `cb_stats_period` (switches period), `cb_stats_refresh` (refreshes current period).

Keyboard (`get_stats_keyboard(period)`) shows 2×2 period buttons with active period marked "▶".
Stats text includes: period-specific bar chart or totals, missed days count for the period.
Fixed: timestamp now shows "МСК" not "UTC" (using `timezone.localtime()`).

#### 5. Web dashboard: date range filter

`apps/stats/views.py` now supports:
- `?preset=today|week|last_week|month` — quick presets
- `?start=YYYY-MM-DD&end=YYYY-MM-DD` — custom range

Context includes `missed_days` queryset, `missed_count`, `missed_filled_count`, `chart_missed` (array for annotation).
`period_financial_summary` is added for the selected range (separate from week-based summary).

#### 6. `DailyReportService` additions

- `get_reports_for_period(start, end)` — generic range query
- `get_date_range_for_period(period)` — returns (start, end) for named period
- `get_unfilled_recent_dates(days=7)` — dates without DailyReport in last N days
- `get_missed_dates_set(dates)` — which of the given dates have unfilled MissedDay
- `count_missed_days(start, end)` — count for stats display
- `build_period_financial_summary(reports)` — financial summary for any report list
- `build_weekly_bar_chart(reports, week_start=None)` — now accepts explicit week_start for last_week support

#### 7. Bugs fixed

- `check_missing_daily_report_task`: wrong date after midnight (now uses control_date)
- `check_missing_daily_report_task`: window was 23:01–06:00, now correctly 23:01–00:59
- `daily.py`: `datetime.date.today()` → `timezone.localdate()`
- `stats.py`: timestamp showed "UTC" → fixed to "МСК" via `timezone.localtime()`
- `webhook.py`: `dp.feed_update()` unhandled exceptions caused 500 → Telegram retry storms; now wrapped in try/except, always returns 200

### Files changed

| File | Change |
|------|--------|
| `apps/stats/models.py` | Add `MissedDay` model |
| `apps/stats/migrations/0005_missedday.py` | New migration |
| `apps/stats/services.py` | New slice/missed-day methods, `create_report` marks MissedDay |
| `apps/stats/tasks.py` | Fix window, mark-missed logic, extracted `_send_to_admins` helper |
| `apps/stats/admin.py` | Register `MissedDay` |
| `apps/stats/views.py` | Date range filter, missed days in context |
| `apps/telegram_bot/callbacks.py` | `AdminStatsCallback.period`, `AdminDailyCallback.date_str` |
| `apps/telegram_bot/admin_keyboards.py` | Period selector, date picker, entry menu keyboards |
| `apps/telegram_bot/handlers/admin/daily.py` | Backdated entry, date picker, `timezone.localdate()` fix |
| `apps/telegram_bot/handlers/admin/stats.py` | Period selection, MСК timestamp, missed count |
| `apps/telegram_bot/subscription.py` | Remove debug logs, fix FIFO middleware order doc |
| `apps/telegram_bot/webhook.py` | Wrap feed_update in try/except → always return 200 |
| `config/settings/base.py` | Add MissedDay to UNFOLD sidebar |

---

## 2026-04-05 — Referral system rework + withdrawal mechanism

### What was done

Replaced the global `ReferralSettings.rate_percent` model with per-user rates. Added full withdrawal request flow (worker → admin notifications → approve/reject → balance recalculation).

#### Balance calculation
`balance = attracted_count × personal_rate + Σ(ref.attracted_count × referral_rate for ref in direct referrals) − Σ(approved withdrawals)`

Recalculated automatically when admin changes `attracted_count`, `personal_rate`, or `referral_rate` of any user. Also recalculates the referrer's balance when a referral's `attracted_count` changes.

#### New app: `apps/withdrawals/`
- `WithdrawalRequest` model: `user`, `amount`, `method` (cryptobot/usdt_trc20), `details`, `status` (pending/approved/rejected), `processed_by`, `processed_at`, `admin_notifications` (JSONField — list of `{telegram_id, message_id}`)
- `WithdrawalService`: create, approve (deducts balance), reject, save_admin_notifications, get_list
- `WithdrawalRequestAdmin` (Django admin, unfold)

#### Worker withdrawal flow
1. Button "💸 Вывод средств" on main menu and profile
2. Choose method: CryptoBot or USDT TRC20
3. Enter details (validated: @username regex or TRC20 address regex `^T[a-zA-Z0-9]{33}$`)
4. Request saved; user gets confirmation; all admins notified via bot message with approve/reject buttons

#### Admin withdrawal handling
- "💸 Выводы" section in admin main menu
- List with pagination, card view
- Approve: balance recalculated (deducts amount), user notified, all other admin notification messages edited to "Обработано {admin}"
- Reject: status updated, user notified, other admin messages edited
- First admin to act locks the request — others see "already processed" on callback

#### User model changes
- Added `personal_rate` (Decimal, default 0) — rate per direct subscriber
- Added `referral_rate` (Decimal, default 0) — rate per referral's subscriber
- `attracted_count` help_text updated

#### User card (admin bot)
- Shows `personal_rate`, `referral_rate`
- Two new buttons: "💰 Личная ставка", "🤝 Ставка за рефералов" → FSM to set per-user rates

#### Migrations
- `users/0004_user_personal_rate_user_referral_rate_and_more.py`
- `withdrawals/0001_initial.py`

#### Design decisions
- Per-user rates (not global) — each worker can have individual economics
- Balance recalculation is always deterministic (counts × rates − withdrawn), never cumulative increments — prevents drift from manual edits
- `admin_notifications` as JSONField on WithdrawalRequest — avoids extra table, safe for concurrent admin edits
- Withdrawal only possible if `balance > 0` — checked at entry point and before save

---

## 2026-04-04 — Production VPS deployment + user guides

### What was done

Prepared a complete one-command production deployment for VPS `45.135.164.155` (no domain, HTTPS via self-signed SSL certificate). Added usage documentation for the bot and Django admin panel.

#### Problem
Telegram requires HTTPS for webhooks. Without a domain, a standard Let's Encrypt certificate is not possible. Solution: self-signed certificate — Telegram explicitly supports this via the `certificate` parameter in `setWebhook`.

#### SSL approach
- `openssl req -newkey rsa:2048 -subj "/CN=<VPS_IP>"` generates a 10-year self-signed cert
- Nginx serves HTTPS on port 443 with this cert
- `setup_webhook --certificate /app/ssl/webhook.pem` uploads the cert to Telegram at registration time
- Cert is stored in `ssl/` directory (gitignored — never commit private keys)
- If VPS_IP changes, the cert is automatically regenerated on next `make prod`

#### Files created/modified

- **`nginx/prod.conf`** — HTTPS nginx config (listens on 443 with self-signed cert, HTTP:80 → HTTPS redirect)
- **`docker-compose.yml`** — nginx now mounts `nginx/prod.conf` and `./ssl:/etc/ssl/bot:ro`
- **`scripts/prod_up.sh`** — one-command prod startup: validates BOT_ENV=prod → reads VPS_IP → generates SSL cert → builds images → starts all services → waits for web health (exec curl inside container) → registers webhook with cert
- **`scripts/prod_down.sh`** — stops prod stack + deletes webhook
- **`apps/telegram_bot/management/commands/setup_webhook.py`** — added `--certificate <path>` argument; when provided, uploads PEM file to Telegram via `FSInputFile`
- **`Makefile`** — added `make prod`, `make prod-down`, `make logs-prod`
- **`.env.example`** — added `VPS_IP` variable with explanation
- **`.gitignore`** — added `ssl/` entry
- **`docs/bot_guide.md`** — full usage guide for workers and admins (bot commands, flows, all sections)
- **`docs/django_admin_guide.md`** — full guide for Django admin panel (all models, common operations)
- **`CLAUDE.md`** — updated commands, env vars, files table, added "Production deployment" section

#### Design decisions

- **Self-signed cert vs nip.io + Let's Encrypt**: self-signed chosen for simplicity — no external dependencies, works offline, no rate limits, valid 10 years.
- **Health check via `docker exec`** instead of external HTTP: in prod, no ports are exposed from the `web` container (only nginx exposes 80/443), so health check uses `docker-compose exec -T web curl http://localhost:8000/health/`.
- **No docker-compose.prod.yml overlay**: base `docker-compose.yml` IS the prod config. Dev uses `docker-compose.dev.yml` overlay on top.

---

## 2026-04-03 — Webhook-only architecture + dual-bot setup

### What was done

Implemented webhook-only Telegram bot infrastructure with separate test and prod bots.
This replaces the single-token approach and adds ngrok support for local development.

#### Files modified

- **`config/settings/base.py`**
  - Replaced `TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")` with BOT_ENV-based selection:
    - `BOT_ENV=dev` → reads `TEST_BOT_TOKEN`
    - `BOT_ENV=prod` → reads `PROD_BOT_TOKEN`
  - All downstream code (`bot.py`, `tasks.py`, `webhook.py`) unchanged — they read `settings.TELEGRAM_BOT_TOKEN`

- **`.env.example`**
  - Added: `BOT_ENV`, `TEST_BOT_TOKEN`, `PROD_BOT_TOKEN`, `NGROK_AUTHTOKEN`
  - Removed: `TELEGRAM_BOT_TOKEN` (old single-token approach)
  - Documented: all vars with comments explaining dev vs prod usage

- **`.env`**
  - Migrated from `TELEGRAM_BOT_TOKEN` to `TEST_BOT_TOKEN` + `PROD_BOT_TOKEN` + `BOT_ENV=dev`
  - Added `NGROK_AUTHTOKEN` placeholder

- **`apps/telegram_bot/management/commands/setup_webhook.py`**
  - Added: prints active `BOT_ENV` and first 10 chars of token before each operation
  - Fixed: removed "switch to polling" from `--delete` help text (polling is forbidden)
  - Improved: error message for missing `TELEGRAM_WEBHOOK_URL` now gives actionable hints

#### Files created

- **`docker-compose.ngrok.yml`**
  - Defines `ngrok` service using `ngrok/ngrok:latest` image
  - Tunnels `web:8000` to a public HTTPS URL
  - Exposes ngrok inspector UI on `localhost:4040`
  - Used as third override: `docker-compose -f ...yml -f ...dev.yml -f ...ngrok.yml up`

- **`scripts/update_ngrok_webhook.sh`**
  - Reads current ngrok tunnel URL via `localhost:4040/api/tunnels`
  - Updates `TELEGRAM_WEBHOOK_URL` in `.env` in-place (macOS + Linux compatible)
  - Calls `python manage.py setup_webhook` inside the web container
  - Must be re-run each time ngrok restarts (URL changes on free plan)

- **`docs/dev_log.md`** (this file)
- **`docs/`** directory created

### Why this was done

**Requirement:** Webhook-only mode (no polling in any environment).
**Requirement:** Two separate Telegram bots — test and prod — to avoid cross-contamination of data and webhooks.
**Requirement:** Local development must also use webhook, not polling.

Key design decisions:
1. **Single `BOT_ENV` switch** instead of separate `.env` files per environment — simpler, less duplication.
2. **ngrok as a Docker service** (not installed on host) — keeps dev environment fully containerized and reproducible.
3. **`update_ngrok_webhook.sh` script** — automates the most error-prone step (getting ngrok URL and registering it with Telegram). Manual copy-paste of ngrok URLs is a common source of mistakes.
4. **Token selection in `base.py`** not in `dev.py`/`prod.py` — because `TELEGRAM_BOT_TOKEN` is consumed by base-level code that both environments share. If it were in `dev.py`, prod would fail to load it.
5. **Kept `settings.TELEGRAM_BOT_TOKEN` as the internal name** — zero changes to `bot.py`, `tasks.py`, `webhook.py`, Celery tasks. Selection happens once at settings load time.

### What to do next

- Fill in real `TEST_BOT_TOKEN` and `PROD_BOT_TOKEN` in `.env` from @BotFather
- Get `NGROK_AUTHTOKEN` from `dashboard.ngrok.com` and add to `.env`
- Run `docker-compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.ngrok.yml up -d`
- Run `bash scripts/update_ngrok_webhook.sh`
- Verify with `python manage.py setup_webhook --info`
- Consider ngrok static domain (paid plan) to avoid re-registering webhook on every restart

---

---

## 2026-04-03 — One-command dev flow (make dev)

### What was done

Replaced the multi-step manual dev startup with a single `make dev` command that fully
orchestrates the local webhook-only dev environment.

#### Files created

- **`Makefile`** — targets: `dev`, `dev-down`, `logs`, `webhook-info`
- **`scripts/dev_up.sh`** — full orchestration:
  1. Guard against `BOT_ENV=prod` being used in dev
  2. `docker-compose up -d` for all services except nginx
  3. Poll `localhost:4040/api/tunnels` until ngrok tunnel is up (max 40s)
  4. Poll `localhost:8000/health/` until web is healthy (max 90s)
  5. Call `python manage.py setup_webhook --url <ngrok_url>/bot/webhook/`
  6. Print final status box
- **`scripts/dev_down.sh`** — stop stack + best-effort webhook removal from test bot
- **`scripts/wait_for_http.sh`** — generic HTTP readiness poller with timeout

#### Files modified

- **`apps/telegram_bot/management/commands/setup_webhook.py`**
  - Added `--url` parameter: allows passing webhook URL directly, bypasses `settings.TELEGRAM_WEBHOOK_URL`
  - This is the key that makes one-command flow work without runtime env file injection

- **`docker-compose.dev.yml`**
  - Added explicit comments about nginx exclusion in dev startup command

- **`docker-compose.ngrok.yml`**
  - Removed `depends_on: web` — ngrok starts independently of web; it queues requests until target is available

#### Files deleted

- **`scripts/update_ngrok_webhook.sh`** — superseded by `scripts/dev_up.sh`

### Why this was done

**Requirement:** All of startup, ngrok tunnel, and webhook registration in one command.

Key design decision: **`--url` parameter on `setup_webhook`**, not runtime env file injection.

Two alternatives were considered:
1. **Runtime env file** (`dev.runtime.env`): generate file with ngrok URL, restart web container
   - Rejected: requires container restart; breaks running state; adds file lifecycle complexity
2. **`--url` parameter**: pass URL directly to management command running inside already-live container
   - Accepted: zero container restarts; idempotent; clean; containers don't need to know the URL at startup

The key insight: `TELEGRAM_WEBHOOK_URL` is only needed by the `setup_webhook` management command,
not by the running application. Telegram sends updates to whatever URL is registered — the app
just validates the secret token and processes the update, without knowing its own URL.

### What to do next

- `cp .env.example .env` and fill: `TEST_BOT_TOKEN`, `NGROK_AUTHTOKEN`, `TELEGRAM_WEBHOOK_SECRET`
- `make dev` — should work end-to-end

## 2026-04-08 — Feature pack: curator role, daily report, reminders, rate config, stats

### What was done

Complete implementation of 9 features requested in one session.

#### Curator role

- `UserRole.CURATOR` added to `users.User`
- `IsCurator` aiogram filter; `IsActivatedWorker` updated to accept curators
- `CuratorCallback(prefix="cur")` — separate namespace for curator callbacks
- Curator main menu: My referrals, My invite codes, Stats, Withdrawal
- Curator can list, view, toggle, and create own invite keys (with ownership check — cannot access other curators' or admins' keys)
- On activation via curator's key: `referred_by` auto-set to curator
- `/start` branches correctly: admin → admin menu, curator → curator menu, worker → worker menu
- Admin can change role via user card: "🎓 Назначить куратором" / "👷 Назначить воркером"

#### Daily report + broadcast

- `DailyReport` model: unique per date, stores link/client_nick/client_rate/total_applications + computed rates
- `RateConfig` singleton: `worker_share`, `referral_share` Decimals, `compute(client_rate)` → dict
- 4-step FSM (link → client_nick → client_rate → total_applications → confirm)
- After confirm: `send_daily_broadcast_task.delay(report.id)` → Celery sends to all active workers + curators
- `broadcast_sent` flag prevents double-send

#### Admin reminders (Celery beat)

- 13:00 МСК (10:00 UTC) and 20:00 МСК (17:00 UTC): gentle reminder if no report yet
- Every 15 min after 23:01 МСК: urgent ВНИМАНИЕ! if no report yet
- All implemented with `zoneinfo.ZoneInfo("Europe/Moscow")` (no pytz)

#### Enhanced admin stats

- ASCII weekly bar chart (Mon → Sun, 8 `█` blocks scaled to max)
- Financial summary: income/debts per day and per week
- Workers + curators counts; top-1 by attracted_count

#### Withdrawal minimum

- `MIN_WITHDRAWAL_AMOUNT = Decimal("700")` in `WithdrawalService`
- Check in `WithdrawalService.create()` (service layer) AND at bot entry point (handler layer)
- User sees alert with current balance if below minimum

#### "База каналов" button

- `CHANNELS_DB_URL` setting with default Google Sheets URL
- URL button in worker main menu, worker profile, curator main menu

#### Settings → RateConfig

- Removed legacy global `rate_percent` FSM
- New `AdminSetRateConfigState` two-step FSM (worker_share% → referral_share%), with validation that sum ≤ 100%

#### Activation notifications

- On worker activation: all admins + key creator (if curator) receive bot notification
- Shows user name, telegram_id, username, and curator name

#### Migrations

- `users/0005_curator_role.py` — adds `curator` to choices
- `stats/0003_rateconfig_dailyreport.py` — creates `RateConfig` and `DailyReport`

#### Bug fixes applied during audit

- `start.py`: `send_curator_main_menu` was called with extra `channels_url` arg (TypeError) — fixed
- `keyboards.py`: curator withdrawal button used `CuratorCallback` (no handler) — fixed to `WorkerCallback`
- `tasks.py`: `pytz` not in dependencies — replaced with Python 3.11 `zoneinfo`
- `profile.py`: `get_profile_keyboard()` called without `channels_db_url` — fixed
- `curator/invites.py`: view/toggle/activations buttons used `AdminInviteCallback` with `IsAdmin()` handlers → curators couldn't interact — added full curator key management handlers with ownership check

---

## 2026-04-10 — Bug fixes, timezone, rate_percent removal, web stats dashboard

### What was done

#### Bug fixes

- **`stats/tasks.py`**: `check_missing_daily_report_task` referenced undefined `_URGENT_CACHE_KEY` → runtime `NameError` every 15 min. Removed the dead variable reference (per-design, sends repeat every 15 min without cache lock).
- **`broadcasts/tasks.py`**: Synchronous Django ORM calls (`BroadcastService.log_delivery`, `UserService.mark_blocked_bot`) were made directly inside `async def _deliver()`. In a sync Celery task using `asyncio.run()` this blocks the event loop and can cause connection pool issues. Refactored to collect results as plain tuples inside the async function and perform all ORM writes synchronously after `asyncio.run()` completes.
- **`stats/models.py`**: `DailyReport.date` default was `datetime.date.today` (OS local date = UTC in Docker). Changed to `timezone.localdate` so new reports default to Moscow date.

#### Moscow timezone everywhere

- `config/settings/base.py`: `TIME_ZONE = "UTC"` → `"Europe/Moscow"`, `CELERY_TIMEZONE = "UTC"` → `"Europe/Moscow"`
- Beat schedule: crontab hours updated from UTC offsets (10, 17) to Moscow time (13, 20) — simpler, correct with `CELERY_TIMEZONE = "Europe/Moscow"`
- `stats/services.py`: All `datetime.date.today()` calls replaced with `timezone.localdate()` (respects Django `TIME_ZONE`)
- `stats/models.py`: Same fix for the `DailyReport.date` field default

#### Removed `rate_percent` (ReferralSettings cleanup)

The `ReferralSettings` model with `rate_percent` was replaced by per-user `personal_rate`/`referral_rate` on the User model in the 2026-04-05 session. The old model was left in the DB.

- `apps/referrals/models.py`: Removed `ReferralSettings` class entirely
- `apps/referrals/services.py`: Removed `get_settings()` and `set_rate()` methods (only `ReferralLink`-related methods remain)
- `apps/referrals/admin.py`: Removed `ReferralSettingsAdmin`
- `apps/referrals/migrations/0002_remove_referralsettings.py`: Migration drops the table

#### Web stats dashboard

New URL: `/stats/` — protected by `staff_member_required` (Django superuser login at `/django-admin/`).

- `apps/stats/views.py`: `StatsDashboardView` — aggregates last 30 days of `DailyReport`, user counts, top workers, financial summary
- `templates/stats_dashboard.html`: Dark-theme dashboard with two Chart.js line charts (applications + finance), recent reports table, top-10 workers list, KPI strip
- `config/urls.py`: Added `path("stats/", StatsDashboardView.as_view(), ...)`

---

## 2026-04-10 (2) — Broadcast fixes, queue fix, landing redesign v2

### Audit findings and fixes

#### Broadcast bugs — root cause found and fixed

**Bug 1: `IntegrityError` → broadcast stuck in RUNNING forever**
- `BroadcastDeliveryLog` has `unique_together = [["broadcast", "user"]]`
- `BroadcastService.log_delivery()` used `objects.create()` — on any retry or duplicate run, this raises `IntegrityError`
- In PostgreSQL, an `IntegrityError` corrupts the active transaction → subsequent `Broadcast.objects.update(status=DONE)` also fails
- Result: broadcast stuck in `RUNNING` state, never completing
- Fix: replaced `objects.create()` with `objects.update_or_create()` in `apps/broadcasts/services.py:log_delivery()`

**Bug 2: Stats/reminder tasks never run — wrong Celery queue name**
- `CELERY_TASK_ROUTES` was routing `apps.stats.tasks.*` → `"celery"` (which IS the Celery default queue name)
- `docker-compose.yml` celery_worker command: `-Q default,broadcasts` — does NOT listen to the `"celery"` named queue
- Result: 13:00/20:00/23:01 reminder tasks piled up in `celery` queue, never consumed
- Fix: changed route to `"default"` and added `celery` to worker's `-Q` flag: `-Q celery,default,broadcasts`

**Bug 3: Dead link in Django admin sidebar**
- UNFOLD sidebar had link to `/django-admin/referrals/referralsettings/` — table was dropped in migration 0002
- Fix: removed the dead nav item from `config/settings/base.py` UNFOLD config

#### Landing page — v2 redesign

Complete rewrite of `templates/landing.html`:
- **Split-screen hero**: text column (left) + floating dashboard mockup (right)
- Dashboard mockup shows: KPI strip (заявки/доход/воркеры), bar chart, workers list with avatars and badges
- Two floating animated pills on top of mockup: "20 сообщений/сек" (green dot) and "248 получателей" (top-right)
- `/stats/` link added in 4 places: navbar (highlighted blue), hero buttons, dedicated stats-promo block, CTA section
- Stats-promo block: full-width call-to-action for the `/stats/` dashboard with description
- Adaptive: mockup hidden on mobile (<1024px), hero goes single-column, stats strip goes 2×2 grid
- `skill ui-ux-pro-max` — NOT available (Unknown skill). `Magic MCP / 21st.dev` — NOT configured. Full manual premium implementation used as fallback.

#### Files changed
- `apps/broadcasts/services.py` — `log_delivery`: `create` → `update_or_create`
- `config/settings/base.py` — stats task route `"celery"` → `"default"`; removed dead referralsettings UNFOLD link
- `docker-compose.yml` — celery worker `-Q default,broadcasts` → `-Q celery,default,broadcasts`
- `templates/landing.html` — full v2 redesign with split hero, dashboard mockup, stats links

#### How to verify broadcasts work
1. `docker-compose up -d`
2. In bot: create broadcast, confirm, launch
3. `docker logs spambotcontrol-celery_worker-1 | grep -E "Broadcast|completed|error"`
4. Should see: `"Broadcast N completed: N sent"`
5. Check `BroadcastDeliveryLog` in Django admin → entries with status `sent`

---

## 2026-04-10 (3) — Channel subscription gate (middleware)

### What was done

Added a global channel subscription check that blocks all non-admin users from using the bot unless they are subscribed to the required channel.

#### Architecture

Implemented as `SubscriptionMiddleware` in `apps/telegram_bot/subscription.py` — an aiogram `BaseMiddleware` registered as an **outer** middleware on the Dispatcher. This ensures:
- Every `message` and `callback_query` passes through the gate
- No handler-level copy-paste needed
- Admins bypass transparently (role check via `db_user.role == UserRole.ADMIN`)

#### Middleware execution order (LIFO)

aiogram outer middlewares execute in LIFO order (last registered = first executed).
Registration order in `bot.py`:

```
dp.message.outer_middleware(SubscriptionMiddleware())  ← 1st registered → runs 2nd
dp.message.outer_middleware(UserMiddleware())           ← 2nd registered → runs 1st
```

Actual call chain: `UserMiddleware → SubscriptionMiddleware → Filters → Handler`

This guarantees `db_user` is already set in `data` when `SubscriptionMiddleware` runs.

#### Member status handling

| Telegram status | Treated as |
|----------------|-----------|
| `creator` | subscribed ✅ |
| `administrator` | subscribed ✅ |
| `member` | subscribed ✅ |
| `restricted` | subscribed ✅ (still in channel) |
| `left` | not subscribed ❌ |
| `kicked` | not subscribed ❌ |
| unknown status | not subscribed ❌ (safe default) |
| API error | **fail-open** — user allowed, error logged |

#### Fail-open policy

If `getChatMember` returns a `TelegramAPIError` (bot not admin in channel, rate limit, etc.), the middleware **allows** the user through and logs an error. This prevents a misconfigured bot from locking out all users silently.

#### Configuration

Two new env vars:

```
SUBSCRIPTION_CHANNEL_ID=-1001234567890   # numeric ID, bot must be member of channel
SUBSCRIPTION_CHANNEL_URL=https://t.me/+srQfQzCb_6gyY2Rh  # shown in inline button
```

If `SUBSCRIPTION_CHANNEL_ID` is empty, the gate is disabled entirely (backward compat).

#### Files changed

- `apps/telegram_bot/subscription.py` — created: `check_channel_membership()` + `SubscriptionMiddleware`
- `apps/telegram_bot/bot.py` — registered `SubscriptionMiddleware` in correct LIFO order
- `config/settings/base.py` — added `SUBSCRIPTION_CHANNEL_ID` and `SUBSCRIPTION_CHANNEL_URL` settings
- `.env.example` — documented the two new vars with instructions on how to get numeric channel ID

#### Manual verification

**User not subscribed:**
1. Set `SUBSCRIPTION_CHANNEL_ID` in `.env`, restart
2. Leave the channel with a test account
3. Send any message or tap any button → should see "🔒 Доступ ограничен" with "📢 Подписаться на канал" button
4. Handler must NOT execute

**User subscribed:**
1. Join the channel with the test account
2. Send any message or tap any button → normal bot response, no gate

**Admin not subscribed:**
1. Leave the channel with the admin account
2. Perform any admin action → should work normally (gate bypassed)

**API error (bot not in channel):**
1. Remove bot from the channel
2. Any user action → bot should still work (fail-open), ERROR logged in `celery_worker` / `web` logs

<!-- Add new entries above this line in the same format -->
