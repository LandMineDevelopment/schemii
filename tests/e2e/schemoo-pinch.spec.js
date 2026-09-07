import { test, expect } from "@playwright/test";

test("mobile native touch pinch zooms the canvas without editing the model", async ({ page, request, context }, testInfo) => {
  test.skip(testInfo.project.name !== "android-chromium", "Native mobile touch gesture");
  const { models } = await (await request.get("/api/v1/schemoo/models")).json();
  expect(models.length).toBeGreaterThan(0);
  await page.goto(`/schemoo?model=${models[0].id}`);
  await expect(page.locator(".sc-node").first()).toBeVisible();
  if (await page.locator("#inspector").isVisible()) await page.getByRole("button", {name:"Close inspector",exact:true}).click();
  await page.getByRole("button", {name:"Fit model",exact:true}).click();
  const status = await page.locator("#draft-status").textContent();
  const stage = page.locator(".sc-stage");
  const matrix = () => stage.evaluate(el => {
    const m = new DOMMatrix(getComputedStyle(el).transform);
    return {zoom:m.a,x:m.e,y:m.f};
  });
  const before = await matrix();
  const box = await page.locator("#canvas-host").boundingBox();
  const x = box.x + box.width / 2, y = box.y + 65;
  const cdp = await context.newCDPSession(page);
  const points = distance => [{id:1,x:x-distance,y},{id:2,x:x+distance,y}];
  await cdp.send("Input.dispatchTouchEvent", {type:"touchStart",touchPoints:points(40)});
  await cdp.send("Input.dispatchTouchEvent", {type:"touchMove",touchPoints:points(80)});
  await cdp.send("Input.dispatchTouchEvent", {type:"touchEnd",touchPoints:[]});
  const after = await matrix();
  expect(after.zoom).toBeCloseTo(before.zoom * 2, 4);
  // CSS matrices round serialized coefficients; require subpixel anchoring.
  expect(Math.abs(after.x + (x-box.x-before.x)/before.zoom * after.zoom - (x-box.x))).toBeLessThan(0.1);
  expect(Math.abs(after.y + (y-box.y-before.y)/before.zoom * after.zoom - (y-box.y))).toBeLessThan(0.1);
  await expect(page.locator("#draft-status")).toHaveText(status);
  expect(await page.evaluate(() => window.visualViewport.scale)).toBe(1);
  await page.screenshot({path:"artifacts/schemoo-mobile-pinch.png"});
  await cdp.detach();
});
