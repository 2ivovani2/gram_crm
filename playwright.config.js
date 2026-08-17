import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "frontend/tests",
  timeout: 30_000,
  use: {
    baseURL: process.env.GRAMLY_TEST_URL || "http://127.0.0.1:8000",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
