import { test, expect } from "@playwright/test";

const qaEnabled = process.env.GRAMLY_CRM_QA === "1";
const qaUser = process.env.GRAMLY_CRM_QA_USER || "";
const qaPassword = process.env.GRAMLY_CRM_QA_PASSWORD || "";

test.describe("Gramly CRM editorial workspace", () => {
  test.skip(!qaEnabled, "Requires the isolated CRM QA database");

  test.beforeEach(async ({ page }) => {
    await page.goto("/django-admin/login/");
    await page.locator("#id_username").fill(qaUser);
    await page.locator("#id_password").fill(qaPassword);
    await Promise.all([
      page.waitForURL(/django-admin/),
      page.locator("button[type=submit], input[type=submit]").click(),
    ]);
  });

  for (const route of [
    "/crm/dashboard/",
    "/crm/entry/finance/",
    "/crm/history/",
    "/crm/control/",
    "/crm/control/reports/",
    "/crm/control/employees/",
    "/crm/control/withdrawals/",
    "/crm/owners/",
  ]) {
    test(`${route} renders without overflow or browser errors`, async ({ page }) => {
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("requestfailed", (request) => {
        const reason = request.failure()?.errorText || "";
        if (!/(ABORTED|cancelled)/i.test(reason) && request.url().startsWith(test.info().project.use.baseURL)) {
          errors.push(`${request.url()} ${reason}`);
        }
      });
      page.on("response", (response) => {
        if (response.status() >= 400 && response.url().startsWith(test.info().project.use.baseURL)) {
          errors.push(`${response.status()} ${response.url()}`);
        }
      });
      await page.goto(route, { waitUntil: "networkidle" });
      await expect(page.locator(".crm-layout")).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(1);
      expect(errors).toEqual([]);
    });
  }

  test("mobile navigation is a keyboard-safe drawer", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/crm/dashboard/", { waitUntil: "networkidle" });
    const toggle = page.getByRole("button", { name: "Открыть меню" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator("#sidebar")).toHaveClass(/open/);
    await page.keyboard.press("Escape");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle).toBeFocused();
  });

  test("mobile data tables expose their column labels", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/crm/control/withdrawals/", { waitUntil: "networkidle" });
    const firstCell = page.locator("tbody td[data-label]").first();
    await expect(firstCell).toBeVisible();
    await expect(firstCell).not.toHaveAttribute("data-label", "");
  });

  test("@visual captures CRM control viewports", async ({ page }, testInfo) => {
    const captures = [
      { name: "dashboard", path: "/crm/dashboard/" },
      { name: "control", path: "/crm/control/" },
      { name: "finance", path: "/crm/entry/finance/" },
      { name: "employees", path: "/crm/control/employees/" },
      { name: "reports", path: "/crm/control/reports/" },
      { name: "withdrawals", path: "/crm/control/withdrawals/" },
      { name: "owners", path: "/crm/owners/" },
    ];
    for (const viewport of [
      { width: 390, height: 844 },
      { width: 768, height: 1024 },
      { width: 1440, height: 900 },
    ]) {
      await page.setViewportSize(viewport);
      for (const capture of captures) {
        await page.goto(capture.path, { waitUntil: "networkidle" });
        const screenshot = await page.screenshot({ fullPage: true });
        await testInfo.attach(`${capture.name}-${viewport.width}`, {
          body: screenshot,
          contentType: "image/png",
        });
        if (
          (viewport.width === 1440 && ["dashboard", "control", "finance"].includes(capture.name)) ||
          (viewport.width === 390 && ["dashboard", "employees", "withdrawals"].includes(capture.name)) ||
          (viewport.width === 768 && capture.name === "reports")
        ) {
          await page.screenshot({
            path: `/private/tmp/gramly-crm-${capture.name}-${viewport.width}x${viewport.height}.png`,
            fullPage: true,
          });
        }
      }
    }
  });
});
