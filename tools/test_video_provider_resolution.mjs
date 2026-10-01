/**
 * 无费用 fixture 测试：镜头编辑器的 provider 自动修正。
 * 运行：node tools/test_video_provider_resolution.mjs
 *
 * 背景（这是本文件存在的原因）：`syncVideoDraft` 原先用
 * `options.some((item) => item.id === provider)` 判断"要不要修正 provider"，
 * 而 `videoProviderOptions()` 返回的是**全部** provider（不支持的只标 `disabled`）。
 * 于是只要 provider 是个已知 id，判断就恒为真，修正永不触发：
 * 切到「参考图约束」时仍留着 minimax，模型下拉被过滤成空，提交才报
 * UNSUPPORTED_MODE_FOR_MODEL。
 *
 * fixture 与 `backend/providers/capabilities.py` 的三家 supported_modes 一致；
 * （ark 于 2026-09-29 退役，能力表不再列它，fixture 随之去掉。）
 * 真实能力表本身由 Python 侧 `tools/test_provider_capabilities.py` 钉着。
 */
import { resolveVideoProvider } from "../frontend/js/render.js";

function assert(condition, message) {
  if (!condition) throw new Error(`断言失败：${message}`);
}

/** 模拟 videoProviderOptions(videoMode) 的真实形状：全部 provider 都在，不支持的只标 disabled。 */
function optionsFor(videoMode) {
  const table = [
    { id: "dashscope", modes: ["t2v", "i2v", "keyframes", "reference"], liveReady: true },
    { id: "minimax", modes: ["t2v", "i2v", "keyframes"], liveReady: true },
    { id: "siliconflow", modes: ["t2v"], liveReady: false },
  ];
  return table.map((row) => {
    const supportsMode = row.modes.includes(videoMode);
    return {
      id: row.id,
      label: row.liveReady ? row.id : `${row.id}（未配置）`,
      disabled: !supportsMode,
    };
  });
}

// --------------------------------------------------------------------------- //
// 1. 先把旧缺陷的形状钉住：存在性判断在参考图模式下恒为真
// --------------------------------------------------------------------------- //
{
  const options = optionsFor("reference");
  const provider = "minimax"; // minimax 没有 reference 能力
  const oldPredicateWouldCorrect = !options.some((item) => item.id === provider);
  assert(
    oldPredicateWouldCorrect === false,
    "旧写法（存在性判断）在该 fixture 上应恒为真，这正是不修正的成因"
  );
  assert(
    options.find((item) => item.id === provider).disabled === true,
    "minimax 在参考图模式下应被标为不可用"
  );
  assert(
    resolveVideoProvider(options, provider, "dashscope") === "dashscope",
    "参考图模式下选着 minimax 时应修正到默认的 dashscope"
  );
  console.log("PASS: 参考图模式下 minimax 会被自动修正（旧的存在性判断不会）");
}

// --------------------------------------------------------------------------- //
// 2. 默认值自己也不支持该模式 → 退到第一个可用的，而不是留在不可用项上
// --------------------------------------------------------------------------- //
{
  const options = optionsFor("reference");
  assert(
    resolveVideoProvider(options, "minimax", "minimax") === "dashscope",
    "默认值也不支持时应退到第一个可用 provider"
  );
  assert(
    resolveVideoProvider(options, "minimax", "") === "dashscope",
    "没有默认值时同样应退到第一个可用 provider"
  );
  console.log("PASS: 默认值也不支持该模式时退到第一个可用 provider");
}

// --------------------------------------------------------------------------- //
// 3. 用户选的那个本身可用 → 必须保留，不能被默认值抢走
// --------------------------------------------------------------------------- //
{
  // 换到 t2v 上考：参考图模式下只剩 dashscope 可用，而它恰好就是默认值，
  // 那样就考不出"默认值会不会抢走用户的选择"了。
  const options = optionsFor("t2v");
  assert(
    resolveVideoProvider(options, "minimax", "dashscope") === "minimax",
    "用户选的 provider 支持该模式时应原样保留"
  );
  console.log("PASS: 用户的选择可用时不被默认值覆盖");
}

// --------------------------------------------------------------------------- //
// 4. 没有一家支持该模式 → 原样返回，把问题暴露给界面，不猜一个
// --------------------------------------------------------------------------- //
{
  const options = [{ id: "minimax", label: "MiniMax H3", disabled: true }];
  assert(
    resolveVideoProvider(options, "minimax", "dashscope") === "minimax",
    "无可用 provider 时应原样返回而不是凭空选一个"
  );
  assert(
    resolveVideoProvider([], "minimax", "dashscope") === "minimax",
    "选项为空时同样原样返回"
  );
  console.log("PASS: 无可用 provider 时原样返回，不猜");
}

// --------------------------------------------------------------------------- //
// 5. 默认值切到 dashscope 后，四种模式都不再需要修正
// --------------------------------------------------------------------------- //
{
  for (const mode of ["t2v", "i2v", "keyframes", "reference"]) {
    const options = optionsFor(mode);
    assert(
      resolveVideoProvider(options, "dashscope", "dashscope") === "dashscope",
      `${mode} 模式下 dashscope 都应可用`
    );
    assert(
      options.find((item) => item.id === "dashscope").disabled === false,
      `${mode} 模式下 dashscope 不应被标为不可用`
    );
  }
  console.log("PASS: dashscope 作默认值时四种模式全部可用");
}

// --------------------------------------------------------------------------- //
// 6. 「未配置」不等于「不支持」：未配置但支持该模式的仍算可用
//    （是否拦在提交前由后端闸门决定，不在这里替用户做主）
// --------------------------------------------------------------------------- //
{
  const options = optionsFor("t2v");
  assert(
    options.find((item) => item.id === "siliconflow").disabled === false,
    "siliconflow 对 t2v 是支持的，只是未配置，不应被标 disabled"
  );
  assert(
    resolveVideoProvider(options, "siliconflow", "dashscope") === "siliconflow",
    "未配置但支持该模式的 provider 应被保留，不静默替换"
  );
  console.log("PASS: 「未配置」与「不支持」区分对待");
}

console.log("ALL VIDEO PROVIDER RESOLUTION TESTS PASSED");
