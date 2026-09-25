import React from "react";

type DashboardData = {
  as_of: string;
  range_days: number;
  owners: { total: number; new: number };
  bots: { active: number; inactive: number };
  owners_with_bots: number;
  high_activity_owners: { count: number; threshold: number; window_start: string };
  audience: { unique_people: number; bot_subscriptions: number; all_contacts: number };
  payment_metrics: {
    paid_count: number; gross_rub: string; paying_owners: number; refund_count: number;
    refund_rub: string; net_rub: string;
    by_provider: Record<string, { count: number; gross_rub: string }>;
  };
  activation_funnel: {
    owners: number; with_bot: number; with_channel: number;
    with_published_welcome: number; with_completed_delivery: number;
  };
  delivery_health: { completed: number; partial: number; failed: number; send_failed?: number; unreachable?: number; cancelled?: number; expired_join_windows: number };
  growth_series: {
    payments: Array<{ date: string; count: number; gross_rub: string }>;
    new_consents: Array<{ date: string; count: number }>;
  };
};

const formatNumber = (value: number) => new Intl.NumberFormat("ru-RU").format(value);
const formatRub = (value: string | number) => `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(Number(value))} ₽`;
const providerName: Record<string, string> = { crypto_pay: "Crypto Pay", telegram_stars: "Telegram Stars", manual: "Ручные" };

function Kpi({ label, value, detail, meaning, onOpen }: {
  label: string; value: string; detail: string; meaning: string; onOpen?: () => void;
}) {
  return <article className="dashboard-kpi" title={meaning}>
    <span>{label}</span><strong>{value}</strong><small>{detail}</small>
    {onOpen && <button type="button" onClick={onOpen}>Открыть список <span aria-hidden="true">↗</span></button>}
    <p className="dashboard-definition">{meaning}</p>
  </article>;
}

function Trend({ label, items, value }: {
  label: string; items: Array<{ date: string; count: number }>; value: (item: { date: string; count: number }) => string;
}) {
  const max = Math.max(1, ...items.map(item => item.count));
  return <div className="dashboard-trend"><h3>{label}</h3>
    {items.length ? <div className="dashboard-bars" role="img" aria-label={`${label}: ${items.map(item => `${item.date} — ${value(item)}`).join(", ")}`}>
      {items.map(item => <span key={item.date} title={`${item.date}: ${value(item)}`} style={{ height: `${Math.max(4, item.count / max * 100)}%` }} />)}
    </div> : <p>За этот период событий нет.</p>}
  </div>;
}

export function AdminDashboard({ data, days, setDays, busy, error, retry, openOwners, openInvoices }: {
  data: DashboardData | null; days: number; setDays(value: number): void; busy: boolean;
  error: string; retry(): void; openOwners(segment: string): void; openInvoices(status: string): void;
}) {
  const payments = data?.payment_metrics;
  const funnel = data?.activation_funnel;
  const health = data?.delivery_health;
  const audience = data?.audience;
  return <section className="admin-dashboard" aria-busy={busy}>
    <div className="dashboard-toolbar">
      <div><b>Показатели продукта</b><small>Текущие значения и события за выбранный период</small></div>
      <div className="period-control" role="group" aria-label="Период аналитики">
        {[7, 30, 90].map(value => <button type="button" key={value} aria-pressed={days === value} className={days === value ? "active" : ""} onClick={() => setDays(value)}>{value} дней</button>)}
      </div>
    </div>
    {error && <div className="dashboard-error" role="alert"><span>Не удалось обновить аналитику: {error}</span><button type="button" onClick={retry}>Повторить</button></div>}
    {!data ? <div className="dashboard-loading" role="status">{busy ? "Загружаем показатели…" : "Показатели пока недоступны."}</div> : <>
      <div className="dashboard-asof" aria-live="polite">{busy ? "Обновляем… · " : ""}На сегодня · обновлено {new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" }).format(new Date(data.as_of))}</div>
      <div className="dashboard-kpis">
        <Kpi label="Подключённые боты" value={formatNumber(data.bots.active)} detail={`${formatNumber(data.bots.inactive)} отключено · на сегодня`} meaning="Боты с активным подключением. Отключённые показаны отдельно." onOpen={() => openOwners("with_bot")}/>
        <Kpi label="Владельцы с ботом" value={formatNumber(data.owners_with_bots)} detail={`из ${formatNumber(data.owners.total)} владельцев · на сегодня`} meaning="Уникальные владельцы хотя бы одного активного бота." onOpen={() => openOwners("with_bot")}/>
        <Kpi label="100+ контактов за неделю" value={formatNumber(data.high_activity_owners.count)} detail="Скользящие 7 суток" meaning="Владельцы, получившие согласие минимум от 100 уникальных людей за последние семь суток. Человек учитывается один раз у каждого владельца." onOpen={() => openOwners("high_activity")}/>
        <Kpi label="Оплаты" value={formatNumber(payments?.paid_count || 0)} detail={`${formatRub(payments?.gross_rub || 0)} · ${formatNumber(payments?.paying_owners || 0)} плательщиков за ${days} дней`} meaning="Подтверждённые платежи, включая впоследствии возвращённые. Бесплатный Business не считается оплатой." onOpen={() => openInvoices("settled")}/>
        <Kpi label="Доступная аудитория" value={formatNumber(audience?.unique_people || 0)} detail={`${formatNumber(audience?.bot_subscriptions || 0)} подписок на ботов · на сегодня`} meaning="Уникальные люди с действующим согласием у активного клиентского бота. Gramly не может писать им от своего имени." onOpen={() => openOwners("with_audience")}/>
      </div>
      <div className="dashboard-sections">
        <article className="dashboard-panel dashboard-funnel"><header><span>01 · Активация</span><h2>Путь к первому приветствию</h2><p>Текущий статус владельцев, не историческая когорта</p></header>
          {[
            ["Регистрация", funnel?.owners || 0], ["Подключили бота", funnel?.with_bot || 0],
            ["Добавили канал", funnel?.with_channel || 0], ["Опубликовали приветствие", funnel?.with_published_welcome || 0],
            ["Первая успешная доставка", funnel?.with_completed_delivery || 0],
          ].map(([label, count]) => <div className="dashboard-funnel-row" key={label}><span>{label}</span><b>{formatNumber(Number(count))}</b><i style={{ width: `${Number(count) / Math.max(1, funnel?.owners || 0) * 100}%` }} /></div>)}
          <button type="button" className="dashboard-panel-link" onClick={() => openOwners("all")}>Посмотреть владельцев ↗</button>
        </article>
        <article className="dashboard-panel"><header><span>02 · Динамика</span><h2>Оплаты и новые согласия</h2><p>События за {days} дней. Новые согласия не равны размеру текущей базы.</p></header>
          <Trend label="Оплаченные счета по дням" items={data.growth_series.payments} value={item => `${item.count} оплат`}/>
          <Trend label="Подтверждённые согласия по дням" items={data.growth_series.new_consents} value={item => `${item.count} человек`}/>
        </article>
        <article className="dashboard-panel"><header><span>03 · Качество</span><h2>Доставка приветствий</h2><p>Цепочки, созданные за {days} дней</p></header>
          <div className="dashboard-health"><div><b>{formatNumber(health?.completed || 0)}</b><span>Успешно</span></div><div><b>{formatNumber(health?.partial || 0)}</b><span>Часть отправлена</span></div><div className="alert"><b>{formatNumber((health?.send_failed ?? health?.failed) || 0)}</b><span>Ошибка отправки</span></div><div><b>{formatNumber(health?.unreachable || 0)}</b><span>Нельзя написать первым</span></div><div><b>{formatNumber(health?.cancelled || 0)}</b><span>Отменено</span></div><div className="alert"><b>{formatNumber(health?.expired_join_windows || 0)}</b><span>Истекло окно заявки</span></div></div>
          <p className="dashboard-footnote">«Нельзя написать первым» — у бота нет адреса для отправки: обычно человек вступил без заявки и не запускал бота. Истёкшее окно заявки может также входить в частично отправленные или отменённые цепочки.</p>
        </article>
      </div>
      <div className="dashboard-detail-row">
        <article><span>База контактов</span><b>{formatNumber(audience?.all_contacts || 0)}</b><small>Все записи, включая заявки без согласия</small></article>
        <article><span>Возвраты за период</span><b>{formatNumber(payments?.refund_count || 0)}</b><small>{formatRub(payments?.refund_rub || 0)}</small></article>
        <article><span>Чистая сумма</span><b>{formatRub(payments?.net_rub || 0)}</b><small>Оплаты минус возвраты за период; RUB-эквивалент. Может быть отрицательной при возврате старой оплаты.</small></article>
        <article><span>Способы оплаты</span><div>{Object.entries(payments?.by_provider || {}).length ? Object.entries(payments?.by_provider || {}).map(([provider, item]) => <small key={provider}>{providerName[provider] || provider}: {item.count} · {formatRub(item.gross_rub)}</small>) : <small>За период оплат нет</small>}</div></article>
      </div>
    </>}
  </section>;
}
