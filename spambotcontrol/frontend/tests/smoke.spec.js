import { test, expect } from "@playwright/test";

test.describe("Gramly public experience", () => {
  test("landing is functional and has no horizontal overflow", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Первое касание");
    await expect(page.getByRole("link", { name: /Запустить Gramly Welcome/ })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("mobile navigation remains keyboard and touch accessible", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const toggle = page.getByRole("button", { name: "Открыть меню" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("link", { name: "Возможности" })).toBeVisible();
  });

  test("reduced motion keeps the static hero fallback", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.locator(".core-static")).toBeVisible();
    await expect(page.locator(".hero__visual")).not.toHaveClass(/is-webgl/);
  });

  test("@visual captures supported responsive widths", async ({ page }, testInfo) => {
    for (const viewport of [{ width: 390, height: 844 }, { width: 768, height: 1024 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
      await page.setViewportSize(viewport);
      await page.goto("/", { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(350);
      const screenshot = await page.screenshot({ fullPage: true });
      await testInfo.attach(`landing-${viewport.width}`, { body: screenshot, contentType: "image/png" });
    }
  });
});
