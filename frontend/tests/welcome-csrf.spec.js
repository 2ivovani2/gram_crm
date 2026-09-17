import { test, expect } from "@playwright/test";

test.skip(
  !process.env.WELCOME_CATALOG_TEST_URL,
  "Local Welcome server required",
);

for (const scenario of [
  "rotated",
  "unchanged",
  "permission",
  "rotated-twice",
]) {
  test(`CSRF rejection ${scenario}: bounded retry preserves the operation`, async ({
    page,
  }) => {
    const origin = process.env.WELCOME_CATALOG_TEST_URL;
    await page.route(`${origin}/csrf-test`, (route) =>
      route.fulfill({ contentType: "text/html", body: "<!doctype html>" }),
    );
    await page.goto(`${origin}/csrf-test`);
    await page
      .context()
      .addCookies([
        { name: "gramly_hello_csrf", value: "initial", url: origin },
      ]);
    const attempts = [];
    await page.route("**/api/v1/editor/drafts/101", async (route) => {
      const request = route.request();
      attempts.push({
        headers: await request.allHeaders(),
        body: request.postData(),
      });
      const rejected = attempts.length === 1 || scenario === "rotated-twice";
      if (rejected) {
        // Another window signs in while this request is in flight.
        if (scenario !== "unchanged")
          await page
            .context()
            .addCookies([
              {
                name: "gramly_hello_csrf",
                value: `fresh-${attempts.length}`,
                url: origin,
              },
            ]);
        return route.fulfill({
          status: 403,
          json: {
            detail:
              scenario === "permission"
                ? "Channel access denied"
                : "Invalid CSRF token",
          },
        });
      }
      return route.fulfill({ json: { saved: true } });
    });
    const result = await page.evaluate(async () => {
      const { api, setCsrfToken } = await import("/src/api.ts");
      setCsrfToken("stale-memory-token");
      try {
        return await api("/editor/drafts/101", {
          method: "PUT",
          body: JSON.stringify({ text: "Несохранённые правки ⭐" }),
        });
      } catch (error) {
        return { status: error.status, detail: error.detail };
      }
    });
    expect(attempts[0].headers["x-csrf-token"]).toBe("initial");
    const retries = scenario === "rotated" || scenario === "rotated-twice";
    expect(attempts).toHaveLength(retries ? 2 : 1);
    if (retries) {
      expect(attempts[1].headers["x-csrf-token"]).toBe("fresh-1");
      expect(attempts[1].headers["idempotency-key"]).toBe(
        attempts[0].headers["idempotency-key"],
      );
      expect(attempts[1].body).toBe(attempts[0].body);
    }
    if (scenario === "rotated") expect(result).toEqual({ saved: true });
    else expect(result.status).toBe(403);
  });
}
