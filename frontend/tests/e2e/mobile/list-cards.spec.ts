import { test, expect } from "@playwright/test";
import { login } from "../helpers";

interface DetectorListItem {
  id: string;
  display_name: string;
}

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
    const sortTrigger = page.getByRole("combobox", {
      name: /sort by|排序依據/i,
    });
    await expect(sortTrigger).toBeVisible();
  });

  test("Jobs list: choosing a sort column reorders the cards", async ({
    page,
    request,
  }) => {
    await login(page);

    // Need at least 2 jobs to observe a reorder. Skip cleanly if cluster is
    // too sparse.
    const apiResp = await request.get("/api/v1/jobs?limit=2");
    expect(apiResp.ok()).toBe(true);
    const json = (await apiResp.json()) as { items: { id: string }[] };
    test.skip(
      json.items.length < 2,
      "fewer than 2 jobs in cluster; cannot exercise sort reorder",
    );

    await page.goto("/jobs");
    const cards = page.getByTestId("card-list-row");
    // If the page exposes data-testid="card-list-row" on each row, capture
    // ordering. Otherwise fall back to first-card title text.
    const firstBefore = await cards.first().textContent();

    const sortTrigger = page.getByRole("combobox", {
      name: /sort by|排序依據/i,
    });
    await sortTrigger.click();
    // Pick "Submitted" — every Jobs row has this column, sortable, and at
    // least 2 rows differ.
    const submittedOption = page.getByRole("option", {
      name: /submitted/i,
    });
    await submittedOption.click();

    // After sort applies, the first card should differ from the unsorted
    // first card. (If by coincidence the same row remains first — both
    // unsorted and sorted-by-submitted-asc — bump the test to compare a
    // larger window, but for a non-trivial Jobs list this signals reorder.)
    const firstAfter = await cards.first().textContent();
    expect(firstAfter).not.toEqual(firstBefore);
  });

  test("Detectors list: tapping a card navigates to detail", async ({
    page,
    request,
  }) => {
    await login(page);

    // Skip the test if the cluster has no detectors registered.
    const apiResp = await request.get("/api/v1/detectors?limit=1");
    expect(apiResp.ok()).toBe(true);
    const list = (await apiResp.json()) as { items: DetectorListItem[] };
    test.skip(
      list.items.length === 0,
      "no detectors registered in cluster; cannot exercise card-tap navigation",
    );

    await page.goto("/detectors");
    await expect(page.locator("table")).toHaveCount(0);

    // At least one card must render; otherwise the no-table check is a
    // vacuous pass on an empty list.
    const cards = page.getByTestId("card-list-row");
    await expect(cards.first()).toBeVisible();

    const first = list.items[0]!;
    // Scope the click target to the card-list region so a same-text match in
    // TopBar / Breadcrumb / Sidebar can't mis-target.
    const firstCard = cards.filter({ hasText: first.display_name }).first();
    await firstCard.click();

    await page.waitForURL(/\/detectors\/[a-f0-9-]+/);
    expect(page.url()).toMatch(new RegExp(`/detectors/${first.id}`));
  });
});
