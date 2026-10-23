import { expect, test } from "@playwright/test";

const taskCatalog = [
  {
    task_id: "push-cube-v1",
    objective: "Move an object into the target region",
    object_label: "Known 50 mm cube",
    requires_yaw: false,
    candidate_count: 2,
    default_material_bounds: { mass_kg: [0.08, 0.45] },
  },
  {
    task_id: "push-t-bar-v1",
    objective: "Match the target planar pose",
    object_label: "Known compound T-bar",
    requires_yaw: true,
    candidate_count: 3,
    default_material_bounds: { mass_kg: [0.08, 0.45] },
  },
];

test("synthetic preview identifies its illustrative provenance", async ({ page }, testInfo) => {
  let startedRuns = 0;
  await page.route("**/api/tasks", (route) => route.fulfill({ json: taskCatalog }));
  await page.route("**/api/runs", (route) => {
    if (route.request().method() === "POST") startedRuns += 1;
    return route.fulfill({ status: 409, json: { detail: "Task loading must not start a run" } });
  });
  await page.goto("/runs/new?task=push-t-bar-v1");
  await expect(page.getByRole("heading", { name: /turn contact into physical knowledge/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run discovery demo" })).toBeEnabled();
  await expect(page.getByLabel("Schematic preview for Known compound T-bar")).toBeVisible();
  expect(startedRuns).toBe(0);
  await page.getByText("Configure run").click();
  const task = page.getByLabel("Task");
  await expect(task).toHaveValue("push-t-bar-v1");
  await task.selectOption("push-cube-v1");
  await expect(page.getByLabel("Schematic preview for Known 50 mm cube")).toBeVisible();
  await task.selectOption("push-t-bar-v1");
  expect(startedRuns).toBe(0);
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
  await expect(page.getByRole("list", { name: "Action graph branch edges" })).toContainText("Safe stop");
  await expect(page.locator("body")).not.toContainText(/hidden_mass|ground_truth|truth_mu_static/i);
  await page.getByRole("tab", { name: "Replay", exact: true }).click();
  await expect(page.getByRole("note", { name: "Preview provenance" })).toBeVisible();
  expect(startedRuns).toBe(0);

  await page.getByLabel("Replay sequence").fill("67");
  await expect(page.locator('.action-graph-window [aria-current="step"]')).toContainText("contact push");
  await page.getByRole("tab", { name: "Preview", exact: true }).click();
  await expect(page.locator(".action-graph-summary")).toContainText("Complete");
});

test("tablet presents one readable physics world at a time", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "desktop engine with tablet viewport");
  await page.setViewportSize({ width: 1100, height: 900 });
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
