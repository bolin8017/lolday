import { test, expect } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { login } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

test("upload dataset and see stats", async ({ page }) => {
  await login(page);
  await page.goto("/datasets/new");
  await page.getByLabel(/^Name$/).fill(`e2e-${Date.now()}`);
  await page.getByRole("tab", { name: /file picker/i }).click();
  await page.setInputFiles('input[type="file"]', path.resolve(__dirname, "fixtures/small-dataset.csv"));
  await expect(page.getByText(/Preview \(3 of 3 rows\)/)).toBeVisible();
  await page.getByRole("button", { name: /upload dataset/i }).click();
  await page.waitForURL(/\/datasets\/[0-9a-f-]+/);
  await expect(page.getByText(/Label distribution/)).toBeVisible();
});
