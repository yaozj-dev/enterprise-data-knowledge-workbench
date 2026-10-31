import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";
const executablePath = process.env.E2E_CHROME_PATH;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  timeout: 90_000,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // System Chrome can run the suite without Playwright's optional ffmpeg download.
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(executablePath ? { launchOptions: { executablePath } } : {}),
      },
    },
  ],
});
