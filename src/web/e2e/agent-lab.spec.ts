import { expect, test } from "@playwright/test";

test("synthetic preview identifies its illustrative provenance", async ({ page }, testInfo) => {
  await page.goto("/runs/new");
  await expect(page.getByRole("heading", { name: /turn contact into physical knowledge/i })).toBeVisible();
  await expect(page.getByLabel(/preview of franka robot and unknown t-bar/i)).toBeVisible();
  await page.getByText("Configure run").click();
  const task = page.getByLabel("Task");
  await expect(task).toHaveValue("push-t-bar-v1");
  await task.selectOption("push-cube-v1");
  await expect(page.getByLabel(/preview of franka robot and unknown cube/i)).toBeVisible();
  await task.selectOption("push-t-bar-v1");
  await page.getByRole("button", { name: /preview synthetic example/i }).click();
  await expect(page.getByRole("note", { name: "Preview provenance" })).toHaveText(/Synthetic preview.*No Newton episode was executed/i);
  await expect(page.getByRole("tab", { name: "Preview", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "Reality Stand-in" })).toBeVisible();
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Learned Twin" }).click();
  }
  await expect(page.getByRole("heading", { name: "Learned Newton Twin" })).toBeVisible();
  await expect(page.getByLabel("Reality and learned Twin comparison")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Action graph" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Discovery phases" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Agent evidence" })).toBeVisible();
  await expect(page.locator(".action-graph").getByText("Safe stop")).toBeVisible({ timeout: 25_000 });
  await page.getByRole("button", { name: /inspect full graph/i }).click();
  await expect(page.getByText("Operational node → Safe stop")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/hidden_mass|ground_truth|truth_mu_static/i);
  await page.getByRole("tab", { name: "Replay", exact: true }).click();
  await expect(page.getByRole("note", { name: "Preview provenance" })).toBeVisible();
  await page.getByLabel("Replay sequence").fill("67");
  await expect(page.locator('.action-graph-window [aria-current="step"]')).toContainText("contact push");
  await page.getByRole("tab", { name: "Preview", exact: true }).click();
  await expect(page.locator(".action-graph-summary")).toContainText("Complete");
});

test("tablet presents one readable physics world at a time", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "desktop engine with tablet viewport");
  await page.setViewportSize({ width: 900, height: 900 });
  await page.goto("/runs/demo?fixture=true");
  await expect(page.getByRole("button", { name: "Reality stand-in" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Reality Stand-in" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Learned Newton Twin" })).not.toBeVisible();
  await page.getByRole("button", { name: "Learned Twin" }).click();
  await expect(page.getByRole("heading", { name: "Learned Newton Twin" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("mobile companion keeps safe stop reachable", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile-only contract");
  await page.goto("/runs/demo?fixture=true");
  await expect(page.getByRole("button", { name: /safe stop/i }).last()).toBeVisible();
  await expect(page.getByText("Current phase")).toBeVisible();
  await page.getByRole("button", { name: "Plan & evidence" }).click();
  await expect(page.getByRole("dialog", { name: "Plan and evidence" })).toBeVisible();
  await expect(page.getByRole("button", { name: /safe stop/i }).last()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
