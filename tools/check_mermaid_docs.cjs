/**
 * 校验 Markdown 里的 Mermaid 块能否**真渲染**（不是只检查语法）。
 *
 * 为什么要有这个工具：结构文档里的图是给人和机器读的，语法体检只证明括号配对，
 * 不证明 mermaid 能解析。曾经就是因为没人真渲染，图里的节点数写错了很久没被发现。
 *
 * 用法：
 *   node tools/check_mermaid_docs.cjs [文件.md ...]
 * 默认校验 docs/visioncraft-diagrams.md。任何一块渲染失败 → 退出码 1。
 *
 * 输出每块渲染后的**尺寸 / 节点数 / 边数**，便于把数字写进文档时可直接复核。
 * 注意：块号 ≠ 图号——一张图可能含多个块，引用尺寸时必须写明"块 N"。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const ROOT = path.join(__dirname, "..");
const DEFAULT_DOCS = ["docs/visioncraft-diagrams.md"];
const MERMAID_BUNDLE = path.join(ROOT, ".playwright-cli", "node_modules", "mermaid", "dist", "mermaid.min.js");

function extractBlocks(markdown) {
  const blocks = [];
  const lines = markdown.split(/\r?\n/);
  let current = null;
  lines.forEach((line, index) => {
    if (/^\s*```mermaid\s*$/.test(line)) {
      current = { startLine: index + 1, code: [] };
      return;
    }
    if (current && /^\s*```\s*$/.test(line)) {
      blocks.push({ ...current, code: current.code.join("\n") });
      current = null;
      return;
    }
    if (current) current.code.push(line);
  });
  if (current) throw new Error("有未闭合的 ```mermaid 块");
  return blocks;
}

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

async function main() {
  const args = process.argv.slice(2).filter((item) => !item.startsWith("-"));
  const docs = args.length ? args : DEFAULT_DOCS;
  if (!fs.existsSync(MERMAID_BUNDLE)) {
    throw new Error(`找不到 mermaid 发行包：${MERMAID_BUNDLE}（先运行 tools/*_browser.py 装的 playwright harness）`);
  }
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
  let failures = 0;
  let totalBlocks = 0;
  try {
    await page.setContent("<!doctype html><html><body><div id='host'></div></body></html>");
    await page.addScriptTag({ path: MERMAID_BUNDLE });
    await page.evaluate(() => {
      window.mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });
    });
    for (const doc of docs) {
      const full = path.isAbsolute(doc) ? doc : path.join(ROOT, doc);
      const markdown = fs.readFileSync(full, "utf8");
      const blocks = extractBlocks(markdown);
      console.log(`== ${doc} —— ${blocks.length} 个 mermaid 块`);
      for (let index = 0; index < blocks.length; index += 1) {
        totalBlocks += 1;
        const block = blocks[index];
        const id = `blk${index + 1}`;
        let outcome;
        try {
          outcome = await page.evaluate(
            async ({ code, id }) => {
              try {
                const { svg } = await window.mermaid.render(id, code);
                const host = document.getElementById("host");
                host.innerHTML = svg;
                const root = host.querySelector("svg");
                const box = root.getBoundingClientRect();
                return {
                  ok: true,
                  // 尺寸随视口与 mermaid 配置变，跨探针不可比：这里固定 viewport 1600×1200，
                  // 引用数字时要连口径一起写。
                  width: Math.round(box.width),
                  height: Math.round(box.height),
                  // 三类分开数，避免"节点数"含义随实现漂移：
                  // g.node = 图形节点，g.cluster = 子图边框，边的路径按 mermaid 的类名取。
                  nodes: host.querySelectorAll("g.node").length,
                  clusters: host.querySelectorAll("g.cluster").length,
                  edges: host.querySelectorAll(".flowchart-link, .edgePath path, path.relation").length,
                  // 时序图的"参与者/消息"不是 g.node，用 flowchart 的口径去数会得到 0，
                  // 那不是"空图"。这两列单独给，免得把度量口径的差异读成图有问题。
                  actors: host.querySelectorAll(".actor").length,
                  messages: host.querySelectorAll(".messageLine0, .messageLine1").length,
                };
              } catch (error) {
                return { ok: false, message: String(error && error.message ? error.message : error) };
              }
            },
            { code: block.code, id }
          );
        } catch (error) {
          outcome = { ok: false, message: String(error.message || error) };
        }
        if (outcome.ok) {
          const sequence = /^\s*sequenceDiagram/.test(block.code);
          console.log(
            `  块 ${index + 1}（第 ${block.startLine} 行）PASS  ` +
              `${outcome.width}×${outcome.height} / ` +
              (sequence
                ? `参与者 ${outcome.actors} / 消息 ${outcome.messages}（时序图）`
                : `节点 ${outcome.nodes} / 子图 ${outcome.clusters} / 边 ${outcome.edges}`)
          );
        } else {
          failures += 1;
          console.log(`  块 ${index + 1}（第 ${block.startLine} 行）FAIL  ${outcome.message.split("\n")[0]}`);
        }
      }
    }
    console.log(failures ? `MERMAID CHECK FAILED: ${failures}/${totalBlocks} 块不可渲染` : `MERMAID CHECK PASSED: ${totalBlocks}/${totalBlocks} 块可渲染`);
  } finally {
    await browser.close();
  }
  if (failures) process.exit(1);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
