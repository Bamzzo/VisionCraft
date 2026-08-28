import { beginObservation, createObserverContext, isLiveSession, rememberJobEvent, shouldWatchProject, stopObservation } from "../frontend/js/jobObserver.js";

function timers() {
  const intervals = new Set();
  return {
    intervals,
    setInterval(fn, ms) {
      const id = { fn, ms };
      intervals.add(id);
      return id;
    },
    clearInterval(id) {
      intervals.delete(id);
    },
  };
}

function closedSource() {
  return { closed: false, close() { this.closed = true; } };
}

const bag = timers();
const ctx = createObserverContext();
ctx.eventSource = closedSource();
ctx.pollTimer = bag.setInterval(() => {}, 1000);
ctx.remoteRefreshTimer = bag.setInterval(() => {}, 2000);

const tokenA = beginObservation(ctx, "project-a", bag);
ctx.eventSource = closedSource();
ctx.pollTimer = bag.setInterval(() => {}, 1000);
if (tokenA !== 1) throw new Error("token should start at 1");
if (bag.intervals.size !== 1) throw new Error("beginObservation should clear previous timers");

const tokenB = beginObservation(ctx, "project-b", bag);
if (tokenB !== 2) throw new Error("token should increment");
if (!isLiveSession(ctx, tokenB, "project-b")) throw new Error("live session B expected");
if (isLiveSession(ctx, tokenA, "project-a")) throw new Error("session A must be dead");

const fromA = rememberJobEvent(ctx, { id: 11, project_id: "project-a", message: "来自 A" }, tokenA);
if (fromA.applied) throw new Error("A events must not apply to B");

const fromB = rememberJobEvent(ctx, { id: 12, project_id: "project-b", shot_id: "shot-1", message: "来自 B" }, tokenB);
if (!fromB.applied) throw new Error("B events should apply");
const dup = rememberJobEvent(ctx, { id: 12, project_id: "project-b", message: "重复" }, tokenB);
if (dup.applied || dup.reason !== "duplicate") throw new Error("duplicate events must be ignored");

stopObservation(ctx, bag);
if (ctx.eventSource || ctx.pollTimer || ctx.remoteRefreshTimer) throw new Error("timers/source must be cleared");
if (ctx.jobEvents.length !== 1) throw new Error("stopObservation must keep persisted event list");

const watch = shouldWatchProject({ jobs: [{ status: "completed" }], shots: [] });
if (watch.watch) throw new Error("idle project should not keep watchers");

console.log("PASS: 切换项目后旧事件不能污染新会话，监听会被清理且事件不重复");
