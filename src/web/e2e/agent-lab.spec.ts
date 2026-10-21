import { expect, test } from "@playwright/test";

test("synthetic preview identifies its illustrative provenance", async ({ page }, testInfo) => {
  await page.goto("/runs/new");
  await expect(page.getByRole("heading", { name: /turn contact into physical knowledge/i })).toBeVisible();
  await page.getByRole("button", { name: /preview synthetic example/i }).click();
  await expect(page.getByRole("note", { name: "Preview provenance" })).toHaveText(/Synthetic preview.*No Newton episode was executed/i);
  await expect(page.getByRole("tab", { name: "Preview", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "Simulated Reality" })).toBeVisible();
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Learned Twin" }).click();
  }
  await expect(page.getByRole("heading", { name: "Learned Newton Twin" })).toBeVisible();
  await expect(page.getByLabel("Reality and learned Twin comparison")).toBeVisible();
  await expect(page.getByLabel("Reality to Twin to Reality workflow")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Discovery phases" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Agent evidence" })).toBeVisible();
  await page.getByRole("tab", { name: "Replay", exact: true }).click();
  await expect(page.getByRole("note", { name: "Preview provenance" })).toBeVisible();
});

test("mobile companion keeps safe stop reachable", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile-only contract");
  await page.goto("/runs/demo?fixture=true");
  await expect(page.getByRole("button", { name: /safe stop/i }).last()).toBeVisible();
  await expect(page.getByText("Current phase")).toBeVisible();
  await page.getByRole("button", { name: "Telemetry" }).click();
  await expect(page.getByRole("dialog", { name: "Telemetry" })).toBeVisible();
});
