import { test, expect } from "@playwright/test";
import { login } from "../helpers";

test.describe("mobile list pages render as cards", () => {
  test("Jobs list shows cards (no <table>) on mobile viewport", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/jobs");

    // On mobile the DataTable dispatcher renders <CardList>, which uses a
    // div-based layout — no <table> should be in the DOM.
    await expect(page.locator("table")).toHaveCount(0);

    // The MobileSortBar trigger should be present (Sort by ▾).
    const sortTrigger = page.getByRole("combobox", { name: /sort by/i });
    await expect(sortTrigger).toBeVisible();
  });

  test("Detectors list: tapping a card navigates to detail", async ({
    page,
    request,
  }) => {
    await login(page);

    // Skip the test if the cluster has no detectors registered.
    const apiResp = await request.get("/api/v1/detectors?limit=1");
    expect(apiResp.ok()).toBe(true);
    const list = (await apiResp.json()) as
      | { items?: Array<{ id: string; display_name: string }> }
      | Array<{ id: string; display_name: string }>;
    const items = Array.isArray(list) ? list : (list.items ?? []);
    test.skip(
      items.length === 0,
      "no detectors registered in cluster; cannot exercise card-tap navigation",
    );

    await page.goto("/detectors");
    await expect(page.locator("table")).toHaveCount(0);

    const firstName = items[0]!.display_name;
    const firstCard = page.getByText(firstName).first();
    await firstCard.click();

    await page.waitForURL(/\/detectors\/[a-f0-9-]+/);
    expect(page.url()).toMatch(new RegExp(`/detectors/${items[0]!.id}`));
  });
});
