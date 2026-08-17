import { test, expect } from "@playwright/test";

test.describe("Gramly public experience", () => {
  test("landing is functional and has no horizontal overflow", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Вступил человек");
    await expect(page.getByRole("link", { name: /Запустить Gramly Welcome/ })).toBeVisible();
    await expect(page.locator("[data-delivery-state]")).toHaveText("Delivered");
    await expect(page.locator('[data-hero-step="crm"]')).toContainText("CRM synced");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("mobile navigation remains keyboard and touch accessible", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const toggle = page.getByRole("button", { name: "Открыть меню" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("link", { name: "Подключение" })).toBeVisible();
  });

  test("reduced motion keeps the static hero fallback", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.locator("[data-hero-product]")).toBeVisible();
    await expect(page.locator("[data-delivery-state]")).toHaveText("Delivered");
    await expect(page.locator('[data-hero-step="crm"]')).toBeVisible();
  });

  test("hero remains complete without JavaScript", async ({ browser }) => {
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Вступил человек");
    await expect(page.locator("[data-hero-product]")).toBeVisible();
    await expect(page.locator("[data-delivery-state]")).toHaveText("Delivered");
    await expect(page.locator('[data-hero-step="crm"]')).toContainText("CRM synced");
    await context.close();
  });

  for (const viewport of [{ width: 1280, height: 720 }, { width: 1366, height: 768 }]) {
    test(`desktop flow fits below the measured header at ${viewport.width}x${viewport.height}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.goto("/", { waitUntil: "domcontentloaded" });
      await page.evaluate(() => document.fonts.ready);
      const pinDocumentTop = await page.locator("[data-flow-pin]").evaluate((element) => element.getBoundingClientRect().top + scrollY);
      await page.evaluate((top) => scrollTo(0, top + innerHeight * 1.8), pinDocumentTop);
      await page.waitForTimeout(300);
      const geometry = await page.evaluate(() => {
        const header = document.querySelector(".landing-header").getBoundingClientRect();
        const pin = document.querySelector("[data-flow-pin]").getBoundingClientRect();
        return { headerBottom: header.bottom, pinTop: pin.top, pinBottom: pin.bottom, viewportHeight: innerHeight };
      });
      expect(Math.abs(geometry.pinTop - geometry.headerBottom)).toBeLessThanOrEqual(2);
      expect(geometry.pinBottom).toBeLessThanOrEqual(geometry.viewportHeight);
      expect(await page.locator("[data-flow-current]").textContent()).not.toBe("01");
    });
  }

  test("@visual captures supported responsive widths", async ({ page }, testInfo) => {
    for (const viewport of [{ width: 390, height: 844 }, { width: 430, height: 932 }, { width: 768, height: 1024 }, { width: 1280, height: 720 }, { width: 1366, height: 768 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
      await page.setViewportSize(viewport);
      await page.goto("/", { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(350);
      const screenshot = await page.screenshot({ fullPage: true });
      await testInfo.attach(`landing-${viewport.width}`, { body: screenshot, contentType: "image/png" });
    }
  });
});
