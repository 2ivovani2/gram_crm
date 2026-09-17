import { test, expect } from "@playwright/test";
import { addEmoji, png } from "./welcome-editor-fixtures";
test.skip(
  !process.env.WELCOME_CATALOG_TEST_URL,
  "Local Welcome server required",
);

test.beforeEach(async ({ page }) => {
  await page.route("https://fonts.googleapis.com/**", (route) =>
    route.fulfill({ contentType: "text/css", body: "" }),
  );
});

for (const width of [390, 430, 1440])
  test(`admin wizard saves media and emoji, retries publication without a duplicate ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 932 });
    const items = [];
    let publishAttempts = 0,
      uploads = 0;
    await page.route("**/api/admin/v1/**", async (route) => {
      const request = route.request(),
        url = new URL(request.url()),
        path = url.pathname.replace("/api/admin/v1", "");
      if (path === "/catalog-media") {
        uploads++;
        return route.fulfill({
          json: {
            media_type: "photo",
            storage_key: `catalog/uploads/${String(uploads).repeat(64)}.png`,
            original_name: "media.png",
            mime_type: "image/png",
            size: 70,
            payload: {},
          },
        });
      }
      if (
        path.startsWith("/catalog-media/") ||
        path.startsWith("/editor/emoji/")
      )
        return route.fulfill({ contentType: "image/png", body: png });
      if (path === "/editor/emoji-sets")
        return route.fulfill({
          json: {
            name: "Test",
            title: "Тестовый набор",
            items: [{ id: "123456789", emoji: "⭐", format: "webp" }],
          },
        });
      if (path === "/catalog") {
        if (request.method() === "POST") {
          const payload = request.postDataJSON();
          expect(payload.expected_revision).toBe(0);
          expect(payload.entry.spec.steps[0].payload.text).toContain(
            "{name}, добро пожаловать!",
          );
          expect(
            payload.entry.spec.steps[0].payload.entities.some(
              (e) => e.custom_emoji_id === "123456789",
            ),
          ).toBe(true);
          expect(payload.entry.spec.steps[0].attachments).toHaveLength(1);
          expect(payload.entry.spec.steps[0].attachments[0].original_name).toBe(
            "second.png",
          );
          expect(payload.entry.spec.steps[0].keyboard.rows[0][0].value).toBe(
            "https://gramly.tech",
          );
          items.push({ ...payload.entry, id: 91, is_active: false });
          return route.fulfill({ json: items[0] });
        }
        return route.fulfill({ json: { items, total: items.length, page: 1 } });
      }
      if (path === "/catalog/91/publish") {
        publishAttempts++;
        if (publishAttempts === 1)
          return route.fulfill({
            status: 503,
            json: { detail: "Публикация временно недоступна" },
          });
        items[0].is_active = true;
        return route.fulfill({ json: items[0] });
      }
      if (path === "/catalog/91" && request.method() === "DELETE") {
        items[0].is_active = false;
        return route.fulfill({ json: items[0] });
      }
      return route.fulfill({ json: null });
    });
    await page.goto(`${process.env.WELCOME_CATALOG_TEST_URL}/admin.html`, {
      waitUntil: "domcontentloaded",
    });
    await page
      .getByRole("button", {
        name: "Шаблоны цепочек Каталог, версии и публикация",
      })
      .click();
    await page
      .getByRole("button", { name: "Добавить шаблон", exact: true })
      .click();
    await page
      .getByLabel("Название", { exact: true })
      .fill("Добро пожаловать в сообщество");
    await page
      .getByLabel("Описание для каталога", { exact: true })
      .fill("Тестовый шаблон — фикстура.");
    await page.getByRole("button", { name: "Далее: сообщения" }).click();
    await page
      .getByRole("textbox", { name: "Текст сообщения" })
      .fill("{name}, добро пожаловать!");
    await addEmoji(page);
    await page.getByRole("button", { name: "Добавить ряд кнопок" }).click();
    await page.getByLabel("Название кнопки").fill("Сайт");
    await page
      .getByLabel("Ссылка", { exact: true })
      .fill("https://gramly.tech");
    await page
      .getByLabel("Добавить файл", { exact: true })
      .setInputFiles({ name: "first.png", mimeType: "image/png", buffer: png });
    await expect(page.locator(".media-item")).toHaveCount(1);
    await page.getByLabel("Добавить файл", { exact: true }).setInputFiles({
      name: "second.png",
      mimeType: "image/png",
      buffer: png,
    });
    await expect(page.locator(".media-item")).toHaveCount(2);
    await page
      .getByRole("button", { name: "Раньше", exact: true })
      .nth(1)
      .click();
    await page
      .getByRole("button", { name: "Убрать файл", exact: true })
      .nth(1)
      .click();
    await expect(page.locator(".media-item")).toContainText("second.png");
    await page.getByRole("button", { name: "1. О шаблоне" }).click();
    await expect(page.getByLabel("Название", { exact: true })).toHaveValue(
      "Добро пожаловать в сообщество",
    );
    await page.getByRole("button", { name: "2. Сообщения" }).click();
    await expect(
      page.getByRole("textbox", { name: "Текст сообщения" }),
    ).toContainText("{name}, добро пожаловать!");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    if (process.env.CAPTURE_HELLO_FINISH)
      await page.screenshot({
        path: `data/ops/editor-qa/admin-messages-${width}.png`,
        fullPage: true,
        animations: "disabled",
      });
    // Sidebar navigation also protects unsaved content.
    await page
      .getByRole("button", {
        name: "Обзор продукта Ключевые показатели и здоровье продукта",
      })
      .click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Далее: проверка" }).click();
    await page
      .getByRole("button", { name: "Опубликовать", exact: true })
      .click();
    await expect(page.getByRole("alert")).toContainText(
      "Публикация временно недоступна",
    );
    expect(items).toHaveLength(1);
    expect(items[0].is_active).toBe(false);
    await page
      .getByRole("button", { name: "Опубликовать", exact: true })
      .click();
    await expect(
      page.getByText("Шаблон опубликован и доступен в Mini App."),
    ).toBeVisible();
    expect(items).toHaveLength(1);
    expect(items[0].is_active).toBe(true);
    if (process.env.CAPTURE_HELLO_FINISH)
      await page.screenshot({
        path: `data/ops/editor-qa/admin-review-${width}.png`,
        fullPage: true,
        animations: "disabled",
      });
    await page.getByRole("button", { name: "К каталогу", exact: true }).click();
    page.on("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Убрать из каталога" }).click();
    await expect(page.getByRole("status")).toContainText(
      "Клиентские копии сохранены",
    );
    expect(items[0].is_active).toBe(false);
  });

test("admin catalog ignores a late response from the previous search", async ({
  page,
}) => {
  let releaseOld;
  const oldResponse = new Promise((resolve) => {
    releaseOld = resolve;
  });
  await page.route("**/api/admin/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (!url.pathname.endsWith("/catalog"))
      return route.fulfill({ json: null });
    const q = url.searchParams.get("q");
    if (q === "old") await oldResponse;
    const items = q
      ? [
          {
            id: q === "old" ? 1 : 2,
            slug: q,
            revision: 1,
            title: q,
            kind: "welcome",
            description: "Test",
            author: "Gramly",
            tags: [],
            screenshots: [],
            is_active: false,
            spec: { first_delay_seconds: 0, steps: [] },
          },
        ]
      : [];
    return route.fulfill({ json: { items, total: items.length, page: 1 } });
  });
  await page.goto(`${process.env.WELCOME_CATALOG_TEST_URL}/admin.html`, {
    waitUntil: "domcontentloaded",
  });
  await page
    .getByRole("button", {
      name: "Шаблоны цепочек Каталог, версии и публикация",
    })
    .click();
  const oldRequest = page.waitForRequest(
    (request) => new URL(request.url()).searchParams.get("q") === "old",
  );
  await page.getByLabel("Поиск", { exact: true }).fill("old");
  await oldRequest;
  await page.getByLabel("Поиск", { exact: true }).fill("new");
  await expect(
    page.getByRole("heading", { name: "new", exact: true }),
  ).toBeVisible();
  const oldFinished = page.waitForResponse(
    (response) => new URL(response.url()).searchParams.get("q") === "old",
  );
  releaseOld();
  await oldFinished;
  await expect(
    page.getByRole("heading", { name: "new", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "old", exact: true }),
  ).toHaveCount(0);
});
