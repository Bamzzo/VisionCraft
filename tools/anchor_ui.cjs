/**
 * 角色/场景视觉锚点界面验收（真实浏览器，mock 后端，无付费 API）。
 * 由 tools/test_anchor_ui_browser.py 通过 npx playwright 调用。
 *
 * 锚点控件长在 Bible 阶段的角色/场景卡片里（`bibleStageHtml` 是表单式编辑器，
 * 不是通用素材网格），因此选择器都挂在 `#stageWorkspace .anchor-block` 上。
 *
 * 覆盖：
 *  1. Bible 阶段角色卡片出现锚点区块（未挂载态，无解除按钮）；
 *  2. 上传锚点图 → 区块变为已挂载、缩略图出现、接口落到 characters.asset_id；
 *  3. 刷新页面后锚点仍在（证明落在库里，而不是只活在内存）；
 *  4. 解除锚点时未选中任何镜头也能生效——这条专门盯住"必须先选镜头"的守卫，
 *     锚点属于角色/场景，若把分支写在守卫之后，点击会被静默吞掉；
 *  5. 解除后素材文件仍留在项目里（只清外键）。
 */
const path = require("path");
const zlib = require("zlib");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");

const SAMPLE =
  "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。" +
  "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。" +
  "但是族中长老已经设下阻碍，他只能选择冒险一搏。" +
  "最终他停在山门前，留下未说完的话。";

function pass(message) {
  console.log(`PASS: ${message}`);
}

/* ---------- 最小合法 PNG（避免为验收引入图像库） ---------- */
const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c;
  }
  return table;
})();

function crc32(buffer) {
  let c = 0xffffffff;
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function pngBytes(width = 6, height = 6) {
  const chunk = (tag, data) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length, 0);
    const body = Buffer.concat([Buffer.from(tag, "ascii"), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body), 0);
    return Buffer.concat([length, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  const rows = [];
  for (let y = 0; y < height; y += 1) {
    const row = Buffer.alloc(1 + width * 3);
    for (let x = 0; x < width; x += 1) {
      row[1 + x * 3] = 0x30;
      row[2 + x * 3] = 0x80;
      row[3 + x * 3] = 0xc0;
    }
    rows.push(row);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/* ---------- API 辅助（仅用于布置项目状态，断言仍走界面） ---------- */
async function api(page, method, p, data) {
  const options = {};
  if (data !== undefined) options.data = data;
  const res = await page.request[method](`${BASE}${p}`, options);
  if (!res.ok()) throw new Error(`${method.toUpperCase()} ${p} -> ${res.status()} ${await res.text()}`);
  return res.json().catch(() => ({}));
}

async function waitFor(page, fn, label, timeout = 20000, arg = undefined) {
  const start = Date.now();
  for (;;) {
    if (await page.evaluate(fn, arg).catch(() => false)) return;
    if (Date.now() - start > timeout) throw new Error(`超时等待：${label}`);
    await page.waitForTimeout(200);
  }
}

async function openProject(page, projectId, title) {
  await page.waitForSelector("#projectList .project-item", { timeout: 20000 });
  const item = page.locator(`#projectList [data-project-id="${projectId}"]`);
  if (await item.count()) {
    await item.first().click();
  } else {
    await page.locator("#projectList .project-item", { hasText: title }).first().click();
  }
  // 列表里还有夹具项目，必须确认选中的确实是本项目，而不是"第一个"。
  await waitFor(
    page,
    (t) => (document.querySelector("#summaryFields")?.innerText || "").includes(t),
    `项目摘要显示「${title}」`,
    20000,
    title,
  );
}

async function openBibleStage(page) {
  await page.click('#stageNav [data-stage-id="bible"]');
  await page.waitForSelector("#stageWorkspace .anchor-block", { timeout: 20000 });
}

/**
 * 应用是固定高度外壳 + 内部滚动区，fullPage 截不到工作区里的锚点块。
 * 必须先把块滚入视野，再各拍一张整页与一张块级特写，否则"证据"里根本没有锚点。
 */
async function shot(page, name) {
  const block = page.locator("#stageWorkspace .anchor-block").first();
  await block.scrollIntoViewIfNeeded().catch(() => {});
  await page.waitForTimeout(150);
  await page.screenshot({ path: path.join(OUT, `${name}-1440.png`) });
  await block.screenshot({ path: path.join(OUT, `${name}-block.png`) }).catch(() => {});
}

async function anchorState(page) {
  return page.evaluate(() => {
    const block = document.querySelector("#stageWorkspace .anchor-block");
    if (!block) return null;
    const upload = block.querySelector("[data-asset-upload]");
    const media = block.querySelector("img, video");
    return {
      name: block.dataset.anchorName || "",
      kind: block.dataset.anchorKind || "",
      text: block.innerText || "",
      uploadRole: upload?.dataset.assetUpload || "",
      uploadAnchorName: upload?.dataset.anchorName || "",
      hasClear: Boolean(block.querySelector("[data-anchor-clear]")),
      mediaSrc: media?.getAttribute("src") || "",
      blockCount: document.querySelectorAll("#stageWorkspace .anchor-block").length,
    };
  });
}

async function main() {
  const browser = await launchBrowser();
  const created = [];
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  try {
    await page.goto(BASE);

    const project = await api(page, "post", "/api/projects", {
      title: "锚点界面验收",
      source_text: SAMPLE,
      duration_seconds: 5,
      shot_count_mode: "auto",
    });
    created.push(project.id);
    await api(page, "post", `/api/projects/${project.id}/run`);
    // /run 是后台作业，方案不会立刻出现——必须轮询到就绪，不能取一次就断言。
    let options = [];
    for (let attempt = 0; attempt < 40 && !options.length; attempt += 1) {
      if (attempt) await page.waitForTimeout(500);
      options = (await api(page, "get", `/api/projects/${project.id}/adaptation/options`)).items || [];
    }
    if (!options.length) throw new Error("改编方案未生成");
    await api(page, "post", `/api/projects/${project.id}/adaptation/options/${options[0].id}/select`);
    await api(page, "post", `/api/projects/${project.id}/adaptation/scope/confirm`, { option_id: options[0].id });
    await api(page, "post", `/api/projects/${project.id}/adaptation/bible/confirm`);

    await page.reload();
    await openProject(page, project.id, project.title);
    await openBibleStage(page);

    let state = await anchorState(page);
    if (!state) throw new Error("Bible 阶段没有锚点区块");
    if (state.uploadRole !== "character_anchor" || !state.uploadAnchorName) {
      throw new Error(`锚点上传入口缺失或角色错误：${JSON.stringify(state)}`);
    }
    if (!state.text.includes("未挂载")) throw new Error(`初始应为未挂载态，实际：${state.text}`);
    if (state.hasClear) throw new Error("未挂载时不应出现解除按钮");
    if (state.mediaSrc) throw new Error(`未挂载时不应有缩略图：${state.mediaSrc}`);
    await shot(page, "anchor-01-empty");
    pass("Bible 阶段角色卡片出现锚点区块，初始为未挂载态且无解除按钮");

    const name = state.name;
    const beforeUpload = (await api(page, "get", `/api/projects/${project.id}`)).assets.length;

    await page
      .locator('#stageWorkspace .anchor-block [data-asset-upload="character_anchor"]')
      .first()
      .setInputFiles({ name: "anchor.png", mimeType: "image/png", buffer: pngBytes() });
    await waitFor(
      page,
      () => (document.querySelector("#stageWorkspace .anchor-block")?.innerText || "").includes("已挂载"),
      "锚点上传后显示已挂载",
    );

    const anchors = await api(page, "get", `/api/projects/${project.id}/anchors`);
    const mine = anchors.items.find((item) => item.kind === "character" && item.name === name);
    if (!mine || !mine.asset_id) throw new Error(`接口未记录锚点：${JSON.stringify(anchors.items)}`);
    if (!(mine.file_path || "").startsWith("/assets/")) throw new Error(`锚点缺可预览路径：${mine.file_path}`);

    state = await anchorState(page);
    if (!state.mediaSrc.includes("/assets/")) throw new Error(`锚点区块未显示缩略图：${state.mediaSrc}`);
    if (!state.hasClear) throw new Error("已挂载时应出现解除按钮");
    // 上传应"恰好"新增一条素材；这个数也是后面"解除不删素材"的基准。
    const afterUpload = (await api(page, "get", `/api/projects/${project.id}`)).assets.length;
    if (afterUpload !== beforeUpload + 1) {
      throw new Error(`上传锚点应新增一条素材：${beforeUpload} -> ${afterUpload}`);
    }
    await shot(page, "anchor-02-attached");
    pass("上传锚点后区块变为已挂载、缩略图出现，接口落到 characters.asset_id");

    await page.reload();
    await openProject(page, project.id, project.title);
    await openBibleStage(page);
    state = await anchorState(page);
    if (!state.text.includes("已挂载")) throw new Error(`刷新后锚点丢失：${state.text}`);
    if (!state.mediaSrc.includes("/assets/")) throw new Error("刷新后缩略图丢失");
    pass("刷新页面后锚点仍在（已落到 characters.asset_id）");

    await page.locator("#stageWorkspace .anchor-block [data-anchor-clear]").first().click();
    await waitFor(
      page,
      () => (document.querySelector("#stageWorkspace .anchor-block")?.innerText || "").includes("未挂载"),
      "解除锚点后回到未挂载态",
    );
    const cleared = (await api(page, "get", `/api/projects/${project.id}/anchors`)).items.find(
      (item) => item.kind === "character" && item.name === name,
    );
    if (cleared.asset_id) throw new Error("解除后接口仍记录锚点");
    const projectAfter = await api(page, "get", `/api/projects/${project.id}`);
    if (projectAfter.assets.length !== afterUpload) {
      throw new Error(`解除锚点不应删除素材：${afterUpload} -> ${projectAfter.assets.length}`);
    }
    await shot(page, "anchor-03-cleared");
    pass("未选中镜头也能解除锚点（守卫顺序正确），且素材记录未被删除");

    console.log("ALL ANCHOR UI TESTS PASSED");
  } finally {
    for (const id of created.filter(Boolean)) {
      try {
        const res = await page.request.delete(`${BASE}/api/projects/${id}`);
        if (res.ok()) console.log(`CLEANED: ${id}`);
      } catch (error) {
        console.error(error);
      }
    }
    await browser.close();
  }
}

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
