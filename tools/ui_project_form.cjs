/**
 * 驱动「新建项目」表单的共享浏览器辅助。
 *
 * 抽出来的原因：同一个竞态咬过三个脚本（ui_workbench、live_2shot_create_guard、
 * local_keyframe_ui），各写一份就意味着第四个脚本还会再踩一次。
 *
 * app.js 的 init() 是异步的，且**收尾那一步**才把表单模式定为「有项目则摘要态」并
 * 重新渲染。若在它收尾前点「新建」，表单会先显示、随即被隐藏；而 Playwright 的
 * fill(..., { force: true }) 在隐藏元素上**静默失败**——值没写进去，提交前的校验
 * 挡下请求，现象是「等响应超时」而不是「收到错误响应」，极难归因。
 *
 * 只做界面交互，不碰后端。
 */
"use strict";

async function formVisible(page) {
  return page.locator("#projectForm").isVisible().catch(() => false);
}

/**
 * 反复尝试直到新建表单**稳定可见**（连续两次采样都可见才认）。
 *
 * 注意：空库时「新建项目」按钮是**故意禁用**的（render.js 里
 * `newBtn.disabled = mode === "create" && !hasProject`），所以先等一次项目列表渲染；
 * 此时表单本就可见，函数会立即返回。
 */
async function openCreateForm(page, options = {}) {
  const attempts = options.attempts ?? 12;
  const settle = options.settle ?? 250;
  await page.waitForSelector("#projectList .project-item", { timeout: 15000 }).catch(() => {});
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (await formVisible(page)) {
      await page.waitForTimeout(settle);
      if (await formVisible(page)) return true;
      continue;
    }
    const newBtn = page.locator("#newProjectBtn");
    if (await newBtn.isEnabled().catch(() => false)) {
      await newBtn.click({ timeout: 5000 }).catch(() => {});
    }
    await page.waitForTimeout(settle);
  }
  throw new Error("无法进入新建表单态：表单始终不可见（异步 init 可能在反复重置表单模式）");
}

/**
 * 填入表单并**读回校验**。
 *
 * 读回这一步不能省：静默失效时后续断言会变成「等某个元素出现」的超时，而真正的原因
 * 藏在几步之前。
 */
async function fillProjectForm(page, fields) {
  for (const [selector, value] of Object.entries(fields)) {
    await page.fill(selector, value);
    const readBack = await page.locator(selector).inputValue();
    if (readBack !== value) {
      throw new Error(
        `表单字段未写入：${selector} 期望 ${JSON.stringify(value)}，读到 ${JSON.stringify(readBack)}`
      );
    }
  }
  if (!(await formVisible(page))) {
    throw new Error("填入后表单已不可见：异步 init 可能在填表期间重置了表单模式");
  }
}

module.exports = { openCreateForm, fillProjectForm, formVisible };
