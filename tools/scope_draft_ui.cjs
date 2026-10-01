/**
 * 「未保存的章节/事件勾选不跨重绘丢失」浏览器验收。禁止真实付费 API。
 *
 * 缺陷形状：勾选只活在被重绘覆盖的 DOM 里。后台任务事件会触发 renderAll()，
 * 而 renderAll() 是**整块替换** stageWorkspace 的 innerHTML（一次长文本分析实测 13 次），
 * 于是落在重绘窗口里的勾选就没了 —— 用户接着点「保存范围」，提交上去的是重绘后的
 * 空范围。危害不是"勾看起来消失了"，而是**保存了错误的范围**。
 *
 * 用例刻意分两层，缺一层就不成立：
 *  ① 用户可见层：重绘之后勾还在，且「尚未保存」提示出现；
 *  ② 真正结算层：此时点「保存范围」，服务端落库的 chapter_ids 必须等于用户勾的那一节。
 * 另加一条**非空虚保证**：重绘前给元素打一个内存标记，重绘后该标记必须消失。
 * 没有这一条，一个"根本没重绘"的环境也能让 ① 通过，用例就失去分辨力了。
 * 重绘走 #refreshBtn（refreshProject → renderAll）—— 与后台任务事件驱动的是同一个函数、
 * 同一条整块替换路径。
 */
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const LONG_ID = process.env.SCOPE_DRAFT_PROJECT_ID;
const OTHER_ID = process.env.SCOPE_DRAFT_OTHER_ID;
const EXPECT_CHAPTERS = Number(process.env.SCOPE_DRAFT_CHAPTER_COUNT || "0");

function pass(msg) {
  console.log(`PASS: ${msg}`);
}

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

async function clearUnsaved(page) {
  const visible = await page.evaluate(() => {
    const modal = document.querySelector("#unsavedModal");
    return Boolean(modal && !modal.classList.contains("hidden"));
  });
  if (!visible) return;
  await page.locator("#unsavedDiscardBtn").click({ force: true });
  await page.waitForFunction(
    () => document.querySelector("#unsavedModal")?.classList.contains("hidden"),
    null,
    { timeout: 8000 }
  );
}

async function selectProject(page, id) {
  await clearUnsaved(page);
  await page.waitForSelector(`#projectList .project-item[data-project-id="${id}"]`, { timeout: 20000 });
  await page.click(`#projectList .project-item[data-project-id="${id}"]`);
  await clearUnsaved(page);
  await page.waitForFunction(
    (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
    id,
    { timeout: 20000 }
  );
}

async function openStage(page, stageId) {
  await clearUnsaved(page);
  await page.click(`[data-stage-id="${stageId}"]`);
  await clearUnsaved(page);
  await page.waitForFunction(
    (id) => document.querySelector(`[data-stage-id="${id}"].active, [data-stage-id="${id}"].current`),
    stageId,
    { timeout: 10000 }
  ).catch(() => {});
}

async function chapterIds(page) {
  return page.evaluate(() => [...document.querySelectorAll("[data-chapter-check]")].map((box) => box.value));
}

async function checkedChapterIds(page) {
  return page.evaluate(() =>
    [...document.querySelectorAll("[data-chapter-check]:checked")].map((box) => box.value)
  );
}

async function fetchScope(id) {
  const response = await fetch(`${BASE}/api/projects/${id}`);
  if (!response.ok) throw new Error(`读取项目失败：HTTP ${response.status}`);
  const body = await response.json();
  return (body.adaptation_scope || {}).chapter_ids || [];
}

async function main() {
  if (!LONG_ID) throw new Error("缺少 SCOPE_DRAFT_PROJECT_ID");
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(25000);
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await selectProject(page, LONG_ID);
    await openStage(page, "storyline");
    await page.waitForSelector("[data-chapter-check]", { timeout: 25000 });

    const all = await chapterIds(page);
    if (EXPECT_CHAPTERS && all.length !== EXPECT_CHAPTERS) {
      throw new Error(`章节树应有 ${EXPECT_CHAPTERS} 节，实际渲染出 ${all.length}`);
    }
    const initiallyChecked = await checkedChapterIds(page);
    if (initiallyChecked.length) {
      throw new Error(`用例前提不成立：尚未保存任何范围，却有 ${initiallyChecked.length} 节已勾选`);
    }
    pass(`长文本章节树渲染出 ${all.length} 节，初始未选任何一节`);

    // 挑中间一节（挑第 1 节会让"是不是恰好等于前缀"这类假通过有机会混进来）。
    const pickedId = all[Math.floor(all.length / 2)];

    // 非空虚保证的前半：往元素上挂一个内存标记。重绘会重建元素，标记随之消失。
    await page.evaluate((id) => {
      const box = document.querySelector(`[data-chapter-check][value="${id}"]`);
      box.__vcMark = "before";
      box.closest("label").__vcMark = "before";
    }, pickedId);

    await page.check(`[data-chapter-check][value="${pickedId}"]`);
    const justChecked = await page.evaluate(
      (id) => document.querySelector(`[data-chapter-check][value="${id}"]`)?.checked === true,
      pickedId
    );
    if (!justChecked) throw new Error("点击未能勾上目标章节，用例前提不成立");
    pass("勾选一节后进入未保存态");

    // 触发整块重绘（与后台任务事件同一条 renderAll 路径）。
    await page.click("#refreshBtn");
    let rebuilt = true;
    try {
      await page.waitForFunction(
        (id) => {
          const box = document.querySelector(`[data-chapter-check][value="${id}"]`);
          return !box || box.__vcMark !== "before";
        },
        pickedId,
        { timeout: 15000 }
      );
    } catch {
      rebuilt = false;
    }
    if (!rebuilt) {
      throw new Error(
        "重绘没有发生（元素标记仍在）：这条用例此刻分辨不出修复有没有生效，不能算通过"
      );
    }
    pass("刷新触发了整块重绘（元素已被重建，标记消失）");

    const after = await page.evaluate(
      (id) => {
        const box = document.querySelector(`[data-chapter-check][value="${id}"]`);
        const workspace = document.querySelector("#stageWorkspace");
        return {
          exists: Boolean(box),
          checked: Boolean(box?.checked),
          stalePreview: /尚未保存/.test(workspace?.innerText || ""),
          claimsHandedOver: /系统将把以下选中范围交给后续改编/.test(workspace?.innerText || ""),
        };
      },
      pickedId
    );
    if (!after.exists) throw new Error("重绘后这一节从章节树里消失了");
    if (!after.checked) {
      throw new Error("重绘后未保存的勾选丢了——这正是要修的缺陷");
    }
    pass("重绘之后未保存的勾选仍在");
    if (!after.stalePreview) throw new Error("勾选已改动却没提示未保存，界面在按旧范围讲故事");
    if (after.claimsHandedOver) throw new Error("勾选已改动，界面仍宣称旧的 scoped_text 就是要交给改编的范围");
    pass("勾选改动后如实提示未保存，不再拿旧的 scoped_text 冒充当前范围");

    // 真正结算层：点保存，服务端落库的必须是用户勾的那一节。
    await page.click("[data-adapt='save-medium-scope']");
    const deadline = Date.now() + 20000;
    let saved = [];
    while (Date.now() < deadline) {
      saved = await fetchScope(LONG_ID);
      if (saved.length) break;
      await page.waitForTimeout(300);
    }
    if (saved.length !== 1 || saved[0] !== pickedId) {
      throw new Error(
        `保存后服务端落库的章节不是用户勾的那一节：期望 [${pickedId}]，实际 [${saved.join(", ")}]`
      );
    }
    pass("重绘之后点保存，落库范围正是用户勾的那一节");

    // 保存成功后草稿必须清掉，否则它会一直压着服务端的权威状态。
    //
    // 这里必须先等「保存之后的那一次重绘」落地再断言：落库发生在服务端，而 renderAll()
    // 在它之后才跑。上一版一看到落库就去读 DOM，读到的是保存前的旧 DOM，于是把
    // "用例读太早"误报成"草稿没清掉"——失败信息于是指向了错误的地方。
    // 等的是正向信号（界面开始按已落库的 scoped_text 说话），不是干等。
    let settled = true;
    try {
      await page.waitForFunction(
        () => /系统将把以下选中范围交给后续改编/.test(document.querySelector("#stageWorkspace")?.innerText || ""),
        null,
        { timeout: 15000 }
      );
    } catch {
      settled = false;
    }
    const afterSave = await page.evaluate(
      (id) => {
        const box = document.querySelector(`[data-chapter-check][value="${id}"]`);
        const workspace = document.querySelector("#stageWorkspace");
        return {
          checked: Boolean(box?.checked),
          stalePreview: /尚未保存/.test(workspace?.innerText || ""),
          tail: (workspace?.innerText || "").replace(/\s+/g, " ").slice(-260),
        };
      },
      pickedId
    );
    if (!settled) {
      throw new Error(`保存后界面一直没有回到服务端权威范围。当前末尾文案：${afterSave.tail}`);
    }
    if (!afterSave.checked) throw new Error("保存后已落库的勾选反而掉了");
    if (afterSave.stalePreview) throw new Error(`保存成功后仍显示未保存态，草稿没有被清掉。当前末尾文案：${afterSave.tail}`);
    pass("保存成功后草稿清空，界面回到服务端权威状态");

    // 切换项目时草稿清空：A 里再勾一节不保存 → 切到 B → 切回 A，那一节不该还在。
    if (OTHER_ID) {
      const extraId = all.filter((id) => id !== pickedId)[Math.floor(all.length / 3)];
      await page.check(`[data-chapter-check][value="${extraId}"]`);
      const dirty = await checkedChapterIds(page);
      if (dirty.length !== 2) {
        throw new Error(`勾第二处后应有 2 节勾选，实际 ${dirty.length}`);
      }
      await selectProject(page, OTHER_ID);
      await openStage(page, "storyline");
      await page.waitForSelector("[data-chapter-check]", { timeout: 25000 });
      await selectProject(page, LONG_ID);
      await openStage(page, "storyline");
      await page.waitForSelector("[data-chapter-check]", { timeout: 25000 });
      // 同样的读太早风险：等界面按已落库的 scope 说话，再读勾选。
      await page
        .waitForFunction(
          () => /系统将把以下选中范围交给后续改编/.test(document.querySelector("#stageWorkspace")?.innerText || ""),
          null,
          { timeout: 15000 }
        )
        .catch(() => {});
      const back = await checkedChapterIds(page);
      if (back.includes(extraId)) {
        throw new Error("切换项目回来后仍带着上一个项目的未保存勾选，草稿没有随项目切换清空");
      }
      if (back.length !== 1 || back[0] !== pickedId) {
        throw new Error(`切换回来应只保留已落库的那一节，实际 [${back.join(", ")}]`);
      }
      pass("切换项目清空未保存勾选，已落库的选择不受影响");
    }

    const shotPath = path.join(__dirname, "..", "output", "playwright", "scope-draft-1440.png");
    await page.screenshot({ path: shotPath, fullPage: true });
    console.log("ALL SCOPE DRAFT UI CHECKS PASSED");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
