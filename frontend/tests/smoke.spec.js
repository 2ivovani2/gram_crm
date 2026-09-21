import { test, expect } from "@playwright/test";

test.describe("Gramly public welcome experience", () => {
  test("landing explains the welcome product and has no CRM entry points", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });

    await expect(page.getByRole("heading", { level: 1 })).toContainText("личное сообщение");
    await expect(page.getByRole("link", { name: /Создать приветствие/ })).toBeVisible();
    await expect(page.locator(".welcome-bubble")).toContainText("Мария");
    await expect(page.getByText("БАЗА И РАССЫЛКИ")).toBeVisible();
    await expect(page.getByRole("link", { name: /CRM/i })).toHaveCount(0);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("mobile navigation remains keyboard and touch accessible", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const toggle = page.getByRole("button", { name: "Открыть меню" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByLabel("Основная навигация").getByRole("link", { name: "Как работает" })).toBeVisible();
  });

  test("hero remains complete without JavaScript", async ({ browser }) => {
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { level: 1 })).toContainText("личное сообщение");
    await expect(page.locator(".welcome-phone")).toBeVisible();
    await expect(page.getByText("Приветствие доставлено")).toBeVisible();
    await context.close();
  });

  test("supported responsive widths have no horizontal overflow", async ({ page }) => {
    for (const viewport of [
      { width: 390, height: 844 },
      { width: 430, height: 932 },
      { width: 768, height: 1024 },
      { width: 1440, height: 900 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/", { waitUntil: "domcontentloaded" });
      await page.evaluate(() => document.fonts.ready);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `overflow at ${viewport.width}px`).toBeLessThanOrEqual(1);
    }
  });

  test("@visual captures supported responsive widths", async ({ page }, testInfo) => {
    for (const viewport of [
      { width: 390, height: 844 },
      { width: 430, height: 932 },
      { width: 1440, height: 900 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/", { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(350);
      const screenshot = await page.screenshot({ fullPage: true });
      await testInfo.attach(`landing-${viewport.width}`, { body: screenshot, contentType: "image/png" });
    }
  });
});
