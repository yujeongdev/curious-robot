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
  await expect(page.getByRole("heading", { name: "Observe, estimate, rebuild, validate, deploy" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Twin candidate fleet" })).toBeVisible();
  await expect(page.getByText("Observation boundary")).toBeVisible();
  await expect(page.getByText("Posterior → Twin patch")).toBeVisible();
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
