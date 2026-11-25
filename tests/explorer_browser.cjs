const assert = require("node:assert/strict");
const {resolve} = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

async function check(browser, filename) {
  const context = await browser.newContext({offline: true, viewport: {width: 1440, height: 960}, hasTouch: true});
  const page = await context.newPage();
  const errors = [], network = [];
  page.on("pageerror", err => errors.push(err.message));
  page.on("request", req => { if (/^https?:/.test(req.url())) network.push(req.url()); });
  const start = performance.now();
  await page.goto(pathToFileURL(resolve(filename)).href);
  await page.waitForFunction(() => document.querySelector("#count").textContent !== "");
  const ready = Math.round(performance.now() - start);
  const data = await page.locator("#explorer-data").textContent().then(JSON.parse);
  const svg = page.locator("#view > svg");
  const isFixture = data.nodes.some(n => n.node_id === "node_old");
  const relation = data.edges[0];
  const target = data.nodes.find(n => n.node_id === (isFixture ? "node_old" : relation?.source_node_id)) || data.nodes[0];
  const search = page.locator("#search");
  const selector = `#results button[data-target="${target.node_id}"]`;
  await search.fill(isFixture ? "ｆｉｘｔｕｒｅ 2" : target.node_id);
  assert.equal(await page.locator(selector).count(), 1);
  await page.locator(selector).click();
  await page.waitForFunction(() => !!document.querySelector("#view .selected"));
  assert.equal(await page.locator("#content h2").textContent(), target.label);
  if (process.env.EXPLORER_SCREENSHOT && filename.includes("/python/")) {
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({path: process.env.EXPLORER_SCREENSHOT});
  }
  if (isFixture) {
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#detail").isVisible(), false);
    await page.locator(`#view [data-node-id="${target.node_id}"] > rect`).click({position: {x: 16, y: 16}});
    assert.equal(await page.locator("#content h2").textContent(), target.label);
    assert.match(await page.locator("#detail").innerText(), /出力 → 仮説/);
    assert.match(await page.locator("#detail").innerText(), /架空の仮説接続/);
    assert.equal(await page.locator('#detail a[href="https://example.org/fixture"]').count() > 0, true);
    await page.locator("#detail button", {hasText: "新しい概念"}).click();
    assert.match(await page.locator("#detail").innerText(), /入力 ← 仮説/);
    assert.equal(await page.locator("[data-edge-id].incoming").count(), 1);
  }
  await page.locator("#close").click();
  assert.equal(await page.locator("#view .dimmed").count(), 0);
  await page.locator("#kind").selectOption(target.type);
  const filtered = data.nodes.filter(n => n.type === target.type).length;
  assert.equal(await page.locator("#view [data-node-id]:not(.filtered)").count(), filtered);
  if (relation) {
    await page.locator("#reset").click();
    await page.locator("#relation").selectOption(relation.relation_type);
    assert.equal(await page.locator("#view [data-edge-id]:not(.filtered)").count(),
      data.edges.filter(e => e.relation_type === relation.relation_type).length);
  }
  if (isFixture) {
    await page.locator("#reset").click();
    await page.locator("#from").fill("1900");
    await page.locator("#assessment").selectOption("hypothetical");
    assert.equal(await page.locator("#view [data-node-id]:not(.filtered)").count(), 0);
  }
  await page.locator("#reset").click();
  assert.equal(await page.locator("#view [data-node-id]:not(.filtered)").count(), data.nodes.length);
  assert.equal(await page.locator("#view [data-edge-id]:not(.filtered)").count(), data.edges.length);
  await page.locator("#fit").click();
  await page.evaluate(() => new Promise(requestAnimationFrame));
  const original = await svg.getAttribute("viewBox");
  const width = box => +box.split(" ")[2];
  await page.locator("#zoom-in").click();
  await page.waitForFunction(previous => document.querySelector("#view svg").getAttribute("viewBox") !== previous, original);
  assert.ok(width(await svg.getAttribute("viewBox")) < width(original));
  const view = await page.locator("#view").boundingBox();
  const x = view.x + view.width / 2, y = view.y + view.height / 2;
  await page.mouse.move(x, y);
  const beforeWheel = await svg.getAttribute("viewBox");
  await page.mouse.wheel(0, -380);
  await page.waitForFunction(previous => document.querySelector("#view svg").getAttribute("viewBox") !== previous, beforeWheel);
  assert.ok(width(await svg.getAttribute("viewBox")) < width(beforeWheel));
  const beforeDrag = await svg.getAttribute("viewBox");
  await page.mouse.down();
  await page.mouse.move(x - 65, y - 50, {steps: 8});
  await page.mouse.up();
  await page.waitForFunction(previous => document.querySelector("#view svg").getAttribute("viewBox") !== previous, beforeDrag);
  const cdp = await context.newCDPSession(page);
  const beforePinch = await svg.getAttribute("viewBox");
  const touch = (type, dx) => cdp.send("Input.dispatchTouchEvent", {type, touchPoints: dx == null ? [] : [
    {x:x-dx, y, id:1}, {x:x+dx, y, id:2},
  ]});
  await touch("touchStart", 30);
  await touch("touchMove", 75);
  await touch("touchEnd");
  await page.waitForFunction(previous => document.querySelector("#view svg").getAttribute("viewBox") !== previous, beforePinch);
  assert.ok(width(await svg.getAttribute("viewBox")) < width(beforePinch));
  await page.locator("#fit").click();
  await page.waitForFunction(previous => document.querySelector("#view svg").getAttribute("viewBox") === previous, original);
  if (isFixture && data.nodes.length === 3) {
    await page.setViewportSize({width: 430, height: 850});
    await search.fill("Fixture 2");
    await page.locator(selector).tap();
    assert.equal(await page.locator("#content h2").textContent(), target.label);
    assert.equal(await page.locator("#detail").isVisible(), true);
    const detail = await page.locator("#detail").boundingBox();
    assert.ok(detail.x >= 0 && detail.x + detail.width <= 430);
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(network, []);
  console.log(`${filename}: ${data.nodes.length} nodes, ${data.edges.length} edges, offline ready in ${ready} ms`);
  await context.close();
}

(async () => {
  assert.ok(process.argv.length > 2, "Pass one or more map.html files");
  const browser = await chromium.launch({executablePath: process.env.EXPLORER_CHROMIUM_EXECUTABLE});
  try {
    for (const filename of process.argv.slice(2)) await check(browser, filename);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
