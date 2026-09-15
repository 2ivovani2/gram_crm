import { test, expect } from "@playwright/test";

test.skip(!process.env.WELCOME_CATALOG_TEST_URL, "Run against the Welcome dev server, not CRM");
const entries = Array.from({length: 6}, (_, index) => ({
  id: index + 1, title: `Шаблон ${index + 1}`, kind: "welcome", author: "Gramly",
  description: "Синтетические данные браузерного теста.", tags: ["Текст"], screenshots: [],
  conversion: null, requires_business: false, step_count: 1, first_delay_seconds: 0,
  steps: [{text: "{name}, добро пожаловать!", delay_after_seconds: 0, media_types: []}],
}));

async function setup(page, { theme = "dark", failFirstInstall = false } = {}) {
  await page.addInitScript(theme => {
    localStorage.setItem("gramly-hello-theme", theme);
    window.Telegram = {WebApp: {initData: "local-synthetic-session", ready(){}, expand(){}, openTelegramLink(){}}};
  }, theme);
  await page.route("https://telegram.org/**", route => route.fulfill({body: "", contentType: "application/javascript"}));
  const installs = [];
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    let body = {};
    if (path === "/session/telegram") body = {csrf_token: "test"};
    else if (path === "/me") body = {owner: {first_name: "Тест"}, access: {plan_name: "Free"}};
    else if (path === "/bots") body = {bots: [{id: 2, username: "first_test", display_name: "First"}, {id: 8, username: "selected_test", display_name: "Selected"}]};
    else if (path === "/plans") body = {plans: []};
    else if (path.endsWith("/flows")) body = {flows: []};
    else if (path === "/catalog") {
      const pageNumber = Number(url.searchParams.get("page") || 1);
      body = {items: entries.slice((pageNumber - 1) * 5, pageNumber * 5), total: entries.length, tags: ["Текст"], page: pageNumber};
    } else if (path.endsWith("/install")) {
      installs.push({key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON()});
      if (failFirstInstall && installs.length === 1) return route.fulfill({status: 503, json: {detail: "Временно недоступно"}});
      body = {flow_id: 100, version_id: 101, name: "Новый черновик", kind: "welcome"};
    } else if (path === "/flows/100/draft") body = {version: {id: 101, first_delay_seconds: 0, timeline_seconds: 0, join_request_compatible: true, channels_missing_invite_rights: []}, steps: []};
    await route.fulfill({json: body});
  });
  await page.goto(`${process.env.WELCOME_CATALOG_TEST_URL}/app.html?section=catalog&bot_id=8`);
  await expect(page.getByRole("heading", {name: "Шаблоны сообщений"})).toBeVisible();
  return installs;
}

test("catalog preserves bot selection, pages five, retries with the same idempotency key", async ({page}) => {
  const installs = await setup(page, {failFirstInstall: true});
  await expect(page.getByLabel("Для бота")).toHaveValue("8");
  await expect(page.getByRole("button", {name: "Посмотреть сообщения"})).toHaveCount(5);
  await page.getByRole("button", {name: "Далее", exact: true}).click();
  await expect(page.getByRole("button", {name: "Посмотреть сообщения"})).toHaveCount(1);
  await page.getByRole("button", {name: "Посмотреть сообщения"}).click();
  await expect(page.getByRole("heading", {name: "Шаблон 6"})).toBeFocused();
  await page.getByRole("button", {name: "Назад к шаблонам"}).click();
  await expect(page.getByRole("button", {name: "Посмотреть сообщения"})).toBeFocused();
  await page.getByRole("button", {name: "Посмотреть сообщения"}).click();
  await expect(page.getByText("Автор: Gramly · Конверсия: Нет данных")).toBeVisible();
  await page.getByRole("button", {name: "Добавить цепочку", exact: true}).click();
  await expect(page.getByRole("alert")).toContainText("Временно недоступно");
  await page.getByRole("button", {name: "Добавить цепочку", exact: true}).click();
  await expect(page.getByRole("heading", {name: "Новый черновик"})).toBeVisible();
  expect(installs).toHaveLength(2);
  expect(installs[0].key).toBe(installs[1].key);
  expect(installs[0].body.bot_id).toBe(8);
});

for (const theme of ["dark", "light"]) {
  for (const width of [390, 430, 1440]) {
    test(`catalog ${theme} ${width} remains within viewport`, async ({page}) => {
      await page.setViewportSize({width, height: 932});
      await setup(page, {theme});
      await page.getByRole("button", {name: "Посмотреть сообщения"}).first().click();
      await expect(page.getByRole("button", {name: "Добавить цепочку", exact: true})).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await expect(page.locator("html")).toHaveAttribute("data-gramly-theme", theme);
    });
  }
}

for (const theme of ["dark", "light"]) for (const width of [390, 1440]) {
test(`draft color and support ${theme} ${width}`, async ({page}) => {
  await page.setViewportSize({width,height:932});
  await setup(page, {theme});
  await page.getByRole("button", {name:"Посмотреть сообщения"}).first().click();
  await page.getByRole("button", {name:"Добавить цепочку",exact:true}).click();
  await page.getByLabel("Цвет всех кнопок этой цепочки").selectOption("success");
  const saved=page.waitForRequest(request=>request.url().endsWith("/drafts/101/button-style")&&request.method()==="POST");
  await page.getByRole("button", {name:"Сохранить цвет"}).click();
  expect((await saved).postDataJSON()).toEqual({style:"success"});
  if(process.env.CAPTURE_HELLO_FINISH)await page.screenshot({path:`data/ops/style-qa/color-${theme}-${width}.png`,fullPage:true});
  await page.getByRole("button", {name:"Закрыть",exact:true}).click();
  await page.getByRole("button", {name:"Ещё",exact:true}).click();
  await expect(page.getByRole("link", {name:"Написать в поддержку"})).toHaveAttribute("href","https://t.me/gramlymanager");
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  if(process.env.CAPTURE_HELLO_FINISH)await page.screenshot({path:`data/ops/style-qa/support-${theme}-${width}.png`,fullPage:true,animations:"disabled"});
});
}
