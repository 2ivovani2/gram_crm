export const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1cAAAAASUVORK5CYII=",
  "base64",
);
export const step = () => ({
  id: 11,
  payload: {
    type: "text",
    text: "👋 {name}, добро пожаловать!",
    entities: [{ type: "bold", offset: 3, length: 6 }],
  },
  attachments: [],
  keyboard: null,
  delay_after_seconds: 0,
});
export async function ownerSetup(
  page,
  {
    theme = "dark",
    conflict = false,
    failFirstSave = false,
    noChannels = false,
    rotateSessionBeforeInstall = false,
    maxSteps = 5,
    initialSteps = 1,
  } = {},
) {
  await page.route("https://fonts.googleapis.com/**", (route) =>
    route.fulfill({ contentType: "text/css", body: "" }),
  );
  const saves = [],
    launches = [];
  const mutations = [];
  let sessionNumber = 0;
  async function rotateSession() {
    const value = `session-${++sessionNumber}`;
    await page.context().addCookies([
      {
        name: "gramly_hello_csrf",
        value,
        url: process.env.WELCOME_CATALOG_TEST_URL,
        sameSite: "Strict",
      },
      {
        name: "test_session",
        value,
        url: process.env.WELCOME_CATALOG_TEST_URL,
        httpOnly: true,
        sameSite: "Strict",
      },
    ]);
    return value;
  }
  let doc = {
    flow: {
      id: 100,
      name: "Знакомство с сообществом",
      kind: "welcome",
      bot_id: 8,
      bot_username: "gramly_test",
      channel_ids: [],
      max_steps: maxSteps,
    },
    version: {
      id: 101,
      edit_revision: 1,
      first_delay_seconds: 0,
      button_style: null,
      auto_approve: true,
    },
    steps: initialSteps === 1 ? [step()] : Array.from({ length: initialSteps }, (_, index) => ({
      ...step(), id: 11 + index, payload: { ...step().payload, text: `Сообщение ${index + 1}` },
      delay_after_seconds: index < initialSteps - 1 ? 10 : 0,
    })),
    channels: noChannels
      ? []
      : [{ id: 10, title: "Тестовый канал", can_invite_users: true }],
  };
  await page.addInitScript((theme) => {
    localStorage.setItem("gramly-hello-theme", theme);
    window.Telegram = {
      WebApp: {
        initData: "synthetic",
        ready() {},
        expand() {},
        enableClosingConfirmation() {},
        disableClosingConfirmation() {},
      },
    };
  }, theme);
  await page.route("https://telegram.org/**", (r) =>
    r.fulfill({ body: "", contentType: "application/javascript" }),
  );
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname.replace("/api/v1", "");
    let body = {};
    if (path !== "/session/telegram" && request.method() !== "GET") {
      const headers = await request.allHeaders();
      const expected = `session-${sessionNumber}`;
      mutations.push({ path, token: headers["x-csrf-token"], expected });
      if (headers["x-csrf-token"] !== expected)
        return route.fulfill({
          status: 403,
          json: { detail: "Invalid CSRF token" },
        });
    }
    if (path === "/session/telegram")
      body = { csrf_token: await rotateSession() };
    else if (path === "/me")
      body = { owner: { first_name: "Тест" }, access: { plan_name: "Free" } };
    else if (path === "/bots")
      body = {
        bots: [{ id: 8, username: "gramly_test", display_name: "Test" }],
      };
    else if (path === "/plans") body = { plans: [] };
    else if (path.endsWith("/flows"))
      body = {
        flows: [
          { ...doc.flow, versions: [{ id: 101, number: 1, status: "draft" }] },
        ],
      };
    else if (path === "/catalog")
      body = {
        items: [
          {
            id: 1,
            title: doc.flow.name,
            description: "Фикстура",
            author: "Gramly",
            tags: [],
            screenshots: [],
            requires_business: false,
            step_count: 1,
            kind: "welcome",
            steps: [
              {
                text: step().payload.text,
                delay_after_seconds: 0,
                media_types: [],
              },
            ],
          },
        ],
        total: 1,
        tags: [],
      };
    else if (path.endsWith("/install"))
      body = {
        flow_id: 100,
        version_id: 101,
        name: doc.flow.name,
        kind: "welcome",
      };
    else if (path === "/editor/flows/100") body = doc;
    else if (path === "/editor/drafts/101" && request.method() === "PUT") {
      const data = request.postDataJSON();
      saves.push({ key: request.headers()["idempotency-key"], data });
      if (conflict)
        return route.fulfill({
          status: 409,
          json: {
            detail: "Цепочка изменена в боте. Загрузите актуальную версию.",
          },
        });
      if (failFirstSave && saves.length === 1)
        return route.fulfill({
          status: 503,
          json: { detail: "Сеть временно недоступна" },
        });
      doc = {
        ...doc,
        version: {
          ...doc.version,
          edit_revision: doc.version.edit_revision + 1,
          first_delay_seconds: data.first_delay_seconds,
          button_style: data.button_style,
        },
        steps: data.steps.map((s, i) => ({ ...s, id: i + 20 })),
      };
      body = doc;
    } else if (path.endsWith("/launch")) {
      launches.push({
        key: request.headers()["idempotency-key"],
        data: request.postDataJSON(),
      });
      body = { status: "published" };
    } else if (path === "/editor/media")
      body = {
        media_id: "a001",
        media_type: "photo",
        storage_key: "editor/test.png",
        original_name: "test.png",
        mime_type: "image/png",
        size: 70,
        payload: {},
        preview_url: "/api/v1/editor/media/a001",
      };
    else if (path.startsWith("/editor/media/"))
      return route.fulfill({ contentType: "image/png", body: png });
    else if (path === "/editor/emoji-sets")
      body = {
        name: "Test",
        title: "Тестовый набор",
        items: [{ id: "123456789", emoji: "⭐", format: "webp" }],
      };
    else if (path.startsWith("/editor/emoji/"))
      return route.fulfill({ contentType: "image/png", body: png });
    return route.fulfill({ json: body });
  });
  await page.goto(
    `${process.env.WELCOME_CATALOG_TEST_URL}/app.html?section=catalog&bot_id=8`,
    { waitUntil: "domcontentloaded" },
  );
  await page.getByRole("button", { name: "Посмотреть сообщения" }).click();
  if (rotateSessionBeforeInstall) await rotateSession();
  await page
    .getByRole("button", { name: "Использовать шаблон", exact: true })
    .click();
  return { saves, launches, mutations, rotateSession };
}
export async function addEmoji(page) {
  await page.getByRole("textbox", { name: "Текст сообщения" }).press("End");
  await page.getByRole("button", { name: "Эмодзи", exact: true }).click();
  await page
    .getByLabel("Ссылка на набор эмодзи")
    .fill("https://t.me/addemoji/Test");
  await page.getByRole("button", { name: "Открыть набор" }).click();
  await page.getByRole("button", { name: "Вставить ⭐" }).click();
}
