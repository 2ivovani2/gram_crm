const money = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 0,
});

const asNumber = (input) => Number.parseFloat(input.value);
const asSlots = (input) => Number.parseInt(input.value, 10);

export function initAdCalculator() {
  const root = document.querySelector("[data-ad-calculator]");
  if (!root) return;

  const weeklyTarget = root.querySelector("[name=weekly_target]");
  const averagePrice = root.querySelector("[name=average_price]");
  const slotInputs = ["paid_slots", "vp_slots", "repayment_slots"].map((name) => root.querySelector(`[name=${name}]`));
  const results = root.querySelector("[data-slot-results]");
  const error = root.querySelector("[data-calculator-error]");
  const strip = root.querySelector("[data-slot-strip]");
  if (!weeklyTarget || !averagePrice || slotInputs.some((input) => !input) || !results) return;

  root.classList.add("is-enhanced");

  const setText = (selector, value) => {
    const node = root.querySelector(selector);
    if (node) node.textContent = value;
  };

  const validate = () => {
    const target = asNumber(weeklyTarget);
    const price = asNumber(averagePrice);
    const slots = slotInputs.map(asSlots);
    let message = "";
    if (!Number.isFinite(target) || !Number.isFinite(price) || target < 0 || price < 0) {
      message = "Цель и цена должны быть неотрицательными числами.";
    } else if (slots.some((value) => !Number.isInteger(value) || value < 0 || value > 7)) {
      message = "В каждой категории укажите целое число от 0 до 7.";
    } else if (slots.reduce((sum, value) => sum + value, 0) !== 7) {
      message = "Распределите ровно 7 слотов между рекламой, ВП и отбивом.";
    }
    error.hidden = !message;
    error.textContent = message;
    return message ? null : { target, price, slots };
  };

  const paintSlots = (slots) => {
    const types = [
      ...Array(slots[0]).fill("paid"),
      ...Array(slots[1]).fill("vp"),
      ...Array(slots[2]).fill("repayment"),
    ];
    strip.innerHTML = types.map((type, index) => `<span class="slot-strip__item slot-strip__item--${type}"><i>${index + 1}</i></span>`).join("");
  };

  const update = () => {
    const total = slotInputs.reduce((sum, input) => sum + (asSlots(input) || 0), 0);
    setText("[data-slot-total]", `${total} / 7`);
    const values = validate();
    if (!values) return;

    const [paid] = values.slots;
    const dailyTarget = values.target / 7;
    const actualDaily = paid * values.price;
    const actualWeekly = actualDaily * 7;
    const deviation = actualWeekly - values.target;
    const maximumWeekly = 49 * values.price;
    const achievable = values.target <= maximumWeekly;
    const required = values.price === 0 ? (values.target === 0 ? 0 : null) : Math.ceil(dailyTarget / values.price);

    results.classList.toggle("is-impossible", !achievable);
    setText("[data-achievable-label]", achievable ? "Цель достижима" : "Цель выше ёмкости");
    setText("[data-actual-weekly]", money.format(actualWeekly));
    setText("[data-daily-target]", money.format(dailyTarget));
    setText("[data-actual-daily]", money.format(actualDaily));
    setText("[data-required-slots]", required === null ? "—" : String(required));
    setText("[data-maximum-weekly]", money.format(maximumWeekly));
    setText("[data-capacity-note]", achievable
      ? "Текущая цена позволяет достичь цели в пределах 7 слотов в день."
      : "Даже семь платных размещений в день не закрывают цель. Увеличьте среднюю цену или скорректируйте план.");
    const deviationNode = root.querySelector("[data-deviation]");
    deviationNode.textContent = `${deviation > 0 ? "+" : ""}${money.format(deviation)} к цели`;
    deviationNode.classList.toggle("is-positive", deviation >= 0);
    deviationNode.classList.toggle("is-negative", deviation < 0);
    paintSlots(values.slots);
  };

  root.querySelectorAll("[data-step]").forEach((button) => button.addEventListener("click", () => {
    const input = button.parentElement.querySelector("input");
    const index = slotInputs.indexOf(input);
    const delta = Number(button.dataset.step);
    const current = asSlots(input) || 0;
    if ((delta > 0 && current >= 7) || (delta < 0 && current <= 0)) return;
    const candidates = slotInputs.filter((_, candidateIndex) => candidateIndex !== index);
    const counterpart = delta > 0
      ? candidates.find((candidate) => asSlots(candidate) > 0)
      : candidates.find((candidate) => asSlots(candidate) < 7);
    if (!counterpart) return;
    input.value = String(current + delta);
    counterpart.value = String(asSlots(counterpart) - delta);
    update();
  }));

  [weeklyTarget, averagePrice, ...slotInputs].forEach((input) => input.addEventListener("input", update));
  update();
}
