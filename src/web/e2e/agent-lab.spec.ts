import { expect, test } from "@playwright/test";

test("quick fixture demonstrates the discovery loop", async ({ page }) => {
  await page.goto("/runs/new");
  await expect(page.getByRole("heading", { name: /turn contact into physical knowledge/i })).toBeVisible();
  await page.getByRole("button", { name: /preview recorded fixture/i }).click();
  await expect(page.getByRole("heading", { name: "Simulation" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Discovery phases" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Agent evidence" })).toBeVisible();
});

test("mobile companion keeps safe stop reachable", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile-only contract");
  await page.goto("/runs/demo?fixture=true");
  await expect(page.getByRole("button", { name: /safe stop/i }).last()).toBeVisible();
  await expect(page.getByText("Current phase")).toBeVisible();
  await page.getByRole("button", { name: "Telemetry" }).click();
  await expect(page.getByRole("dialog", { name: "Telemetry" })).toBeVisible();
});
