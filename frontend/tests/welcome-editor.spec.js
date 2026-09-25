import { test, expect } from "@playwright/test";
import { ownerSetup, addEmoji, png } from "./welcome-editor-fixtures";
test.skip(
  !process.env.WELCOME_CATALOG_TEST_URL,
  "Local Welcome server required",
);

test("Business editor saves ten welcome messages and prevents an eleventh", async ({ page }) => {
  const { saves } = await ownerSetup(page, { maxSteps: 10 });
  for (let index = 2; index <= 10; index += 1) {
    await page.getByRole("button", { name: new RegExp(`Добавить сообщение · ${index - 1}/10`) }).click();
    await page.getByRole("textbox", { name: "Текст сообщения" }).fill(`Шаг ${index}`);
  }
  await expect(page.getByRole("button", { name: "Добавить сообщение · 10/10" })).toBeDisabled();
  await page.getByRole("button", { name: "Сохранить черновик", exact: true }).click();
  expect(saves).toHaveLength(1);
  expect(saves[0].data.steps).toHaveLength(10);
});

test("Free owner sees all saved steps but cannot republish a long version", async ({ page }) => {
  await ownerSetup(page, { maxSteps: 5, initialSteps: 10 });
  await expect(page.getByText(/Business закончился: новые приветствия/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Добавить сообщение · 10/5" })).toBeDisabled();
  await page.getByRole("button", { name: "Далее: предпросмотр" }).click();
  await expect(page.getByRole("alert")).toContainText("от 1 до 5 сообщений");
});

test("another Mini App session does not break template installation, uploads, saving or launch", async ({
  page,
}) => {
  const { saves, launches, mutations, rotateSession } = await ownerSetup(page, {
    rotateSessionBeforeInstall: true,
  });
  await expect(
    page.getByText("Настройте сообщения под свой канал."),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Текст сообщения" })
    .fill("Мой независимый черновик");
  await rotateSession();
  await page
    .getByLabel("Добавить файлы", { exact: true })
    .setInputFiles({ name: "test.png", mimeType: "image/png", buffer: png });
  await expect(page.locator(".media-item")).toHaveCount(1);
  await rotateSession();
  await page
    .getByRole("button", { name: "Сохранить черновик", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Далее: предпросмотр" }),
  ).toBeEnabled();
  expect(saves).toHaveLength(1);
  expect(saves[0].data.steps[0].payload.text).toBe("Мой независимый черновик");
  await page.getByRole("button", { name: "Далее: предпросмотр" }).click();
  await page.getByLabel("Тестовый канал").check();
  await rotateSession();
  await page
    .getByRole("button", { name: "Запустить на выбранных каналах" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Цепочка запущена" }),
  ).toBeVisible();
  expect(launches).toHaveLength(1);
  expect(mutations.length).toBeGreaterThanOrEqual(4);
  expect(mutations.every(({ token, expected }) => token === expected)).toBe(
    true,
  );
});

test("user edits a copy, adds media and emoji, saves explicitly and launches on selected channels", async ({
  page,
}) => {
  const { saves, launches } = await ownerSetup(page, { failFirstSave: true });
  await expect(
    page.getByText("Настройте сообщения под свой канал."),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Текст сообщения" }).locator("strong"),
  ).toHaveText("{name}");
  await addEmoji(page);
  await page
    .getByLabel("Добавить файлы", { exact: true })
    .setInputFiles({ name: "test.png", mimeType: "image/png", buffer: png });
  await expect(page.locator(".media-item")).toHaveCount(1);
  await page.getByRole("button", { name: "Добавить ряд кнопок" }).click();
  await page.getByLabel("Название кнопки").fill("Подробнее");
  await page.getByLabel("Ссылка", { exact: true }).fill("https://gramly.tech");
  await page.getByRole("radio", { name: /Зелёная/ }).click();
  await page.getByRole("button", { name: "Копировать сообщение" }).click();
  await page.getByRole("button", { name: "Выше", exact: true }).nth(1).click();
  await page
    .getByRole("group", { name: "Пауза до следующего сообщения" })
    .getByLabel("Минуты")
    .fill("15");
  expect(saves).toHaveLength(0);
  expect(launches).toHaveLength(0);
  await page
    .getByRole("button", { name: "Сохранить черновик", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "Сеть временно недоступна",
  );
  await page
    .getByRole("button", { name: "Сохранить черновик", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Далее: предпросмотр" }),
  ).toBeEnabled();
  expect(saves).toHaveLength(2);
  expect(saves[0].key).toBe(saves[1].key);
  const saved = saves[1].data;
  expect(saved.steps).toHaveLength(2);
  expect(saved.steps[0].delay_after_seconds).toBe(900);
  expect(saved.button_style).toBeNull();
  for (const step of saved.steps) {
    expect(
      step.payload.entities.some((e) => e.custom_emoji_id === "123456789"),
    ).toBe(true);
    expect(step.payload.entities.some((e) => e.type === "bold")).toBe(true);
    expect(step.attachments).toHaveLength(1);
    expect(step.keyboard.rows[0][0].value).toBe("https://gramly.tech");
    expect(step.keyboard.rows[0][0].style).toBe("success");
  }
  await page.getByRole("button", { name: "Далее: предпросмотр" }).click();
  await expect(
    page.getByRole("button", { name: "Запустить на выбранных каналах" }),
  ).toBeDisabled();
  await page.getByLabel("Тестовый канал").check();
  await expect(page.getByText(/Premium администратора каталога/)).toBeVisible();
  await page
    .getByRole("button", { name: "Запустить на выбранных каналах" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Цепочка запущена" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Готово", exact: true }).click();
  await expect(page.locator(".flow-workspace")).toHaveCount(0);
  expect(launches).toHaveLength(1);
  expect(launches[0].data.channel_ids).toEqual([10]);
  expect(launches[0].data.expected_revision).toBe(2);
});

for (const theme of ["dark", "light"])
  for (const width of [390, 430, 1440])
    test(`four buttons keep individual colors · ${theme} · ${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 932 });
      const { saves } = await ownerSetup(page, { theme });
      await page.getByRole("button", { name: "Добавить ряд кнопок" }).click();
      await page.getByRole("button", { name: "Добавить рядом" }).click();
      await page.getByRole("button", { name: "Добавить рядом" }).click();
      await page.getByRole("button", { name: "Добавить ряд кнопок" }).click();

      const cards = page.locator(".link-button-fields");
      const styles = ["Стандартная", "Синяя", "Зелёная", "Красная"];
      for (let index = 0; index < styles.length; index += 1) {
        const card = cards.nth(index);
        await card.getByLabel("Название кнопки").fill(`Кнопка ${index + 1}`);
        await card.getByLabel("Ссылка", { exact: true }).fill(`https://gramly.tech/${index + 1}`);
        await card.getByRole("radio", { name: new RegExp(styles[index]) }).click();
      }

      for (const style of ["default", "primary", "success", "danger"])
        await expect(page.locator(`[data-color="${style}"]`)).toHaveCount(1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.getByRole("button", { name: "Сохранить черновик", exact: true }).click();
      expect(saves[0].data.steps[0].keyboard.rows.flat().map(button => button.style)).toEqual([
        "default",
        "primary",
        "success",
        "danger",
      ]);
    });

test("conflict preserves local text, and leaving uses a keyboard accessible save/discard dialog", async ({
  page,
}) => {
  await ownerSetup(page, { conflict: true });
  await page
    .getByRole("textbox", { name: "Текст сообщения" })
    .fill("Локальные правки");
  await page
    .getByRole("button", { name: "Сохранить черновик", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("изменена в боте");
  await expect(
    page.getByRole("textbox", { name: "Текст сообщения" }),
  ).toHaveText("Локальные правки");
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "Несохранённые изменения" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Продолжить редактирование" }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("dialog", { name: "Несохранённые изменения" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("textbox", { name: "Текст сообщения" }),
  ).toHaveText("Локальные правки");
});

test("UTF16 adapter preserves multiline marks, quotes, code, links, emoji and empty lines", async ({
  page,
}) => {
  await ownerSetup(page);
  const value = {
    text: "👋 {name}\n\n⭐ текст",
    entities: [
      { type: "bold", offset: 3, length: 15 },
      { type: "blockquote", offset: 0, length: 18 },
      { type: "custom_emoji", offset: 11, length: 1, custom_emoji_id: "12345" },
      { type: "text_link", offset: 13, length: 5, url: "https://gramly.tech" },
    ],
  };
  // Use the actual editor adapter served by Vite, then verify its document in Tiptap too.
  const result = await page.evaluate(async (value) => {
    const adapter = await import("/src/editor/entities.ts");
    return adapter.fromDocument(adapter.toDocument(value, "owner"));
  }, value);
  expect(result.text).toBe(value.text);
  expect(result.entities).toEqual(expect.arrayContaining(value.entities));
});

test("quote toolbar saves a Telegram expandable quote without overlapping quote entities", async ({
  page,
}) => {
  const { saves } = await ownerSetup(page);
  const input = page.getByRole("textbox", { name: "Текст сообщения" });
  await input.fill("Важная цитата");
  await input.selectText();
  await page.getByRole("button", { name: "Цитата", exact: true }).click();
  await expect(input.locator(".telegram-quote")).toHaveText("Важная цитата");

  await input.selectText();
  await page
    .getByRole("button", { name: "Сворачиваемая цитата", exact: true })
    .click();
  await expect(input.locator(".telegram-quote--expandable")).toHaveText(
    "Важная цитата",
  );
  await expect(
    page.getByRole("button", { name: "Цитата", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");

  await page
    .getByRole("button", { name: "Сохранить черновик", exact: true })
    .click();
  const entities = saves[0].data.steps[0].payload.entities;
  expect(entities).toEqual(
    expect.arrayContaining([
      { type: "expandable_blockquote", offset: 0, length: 13 },
    ]),
  );
  expect(entities.some(({ type }) => type === "blockquote")).toBe(false);
});

for (const theme of ["dark", "light"])
  for (const width of [390, 430, 1440])
    test(`editor ${theme} ${width} layout and empty channels`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 932 });
      await ownerSetup(page, { theme, noChannels: true });
      const input = page.getByRole("textbox", { name: "Текст сообщения" });
      await expect(input).toBeVisible();
      await input.press("End");
      await input.press("Enter");
      await input.pressSequentially("Вторая строка");
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await expect(page.locator("html")).toHaveAttribute(
        "data-gramly-theme",
        theme,
      );
      if (process.env.CAPTURE_HELLO_FINISH) {
        await page.locator(".modal").evaluate((el) => (el.scrollTop = 0));
        await page.screenshot({
          path: `data/ops/editor-qa/editor-${theme}-${width}.png`,
          fullPage: true,
          animations: "disabled",
        });
      }
      if (width < 500) {
        await page.setViewportSize({ width, height: 500 });
        await expect(input).toBeFocused();
        await page
          .getByRole("button", { name: "Сохранить черновик", exact: true })
          .click();
        await page.setViewportSize({ width, height: 932 });
      }
      await page.getByRole("button", { name: "Далее: предпросмотр" }).click();
      await expect(
        page.getByText("У бота пока нет каналов.", { exact: false }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Запустить на выбранных каналах" }),
      ).toBeDisabled();
      await expect(
        page.getByRole("button", { name: "Сохранить черновик", exact: true }),
      ).toBeEnabled();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      if (process.env.CAPTURE_HELLO_FINISH)
        await page.screenshot({
          path: `data/ops/editor-qa/review-${theme}-${width}.png`,
          fullPage: true,
          animations: "disabled",
        });
    });

test("removing the last legacy photo keeps its caption and emoji as editable text", async ({
  page,
}) => {
  await ownerSetup(page);
  const result = await page.evaluate(async () => {
    const { withMedia } = await import("/src/editor/types.ts");
    return withMedia(
      {
        payload: { type: "photo", caption: "Old fallback" },
        attachments: [
          {
            media_type: "photo",
            payload: {
              caption: "⭐ Caption",
              caption_entities: [
                {
                  type: "custom_emoji",
                  offset: 0,
                  length: 1,
                  custom_emoji_id: "12345",
                },
              ],
            },
          },
        ],
        keyboard: null,
        delay_after_seconds: 0,
      },
      [],
    );
  });
  expect(result.payload).toEqual({
    type: "text",
    text: "⭐ Caption",
    entities: [
      { type: "custom_emoji", offset: 0, length: 1, custom_emoji_id: "12345" },
    ],
  });
  expect(result.attachments).toEqual([]);
});
