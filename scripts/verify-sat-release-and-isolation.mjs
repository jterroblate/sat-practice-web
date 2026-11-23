// Release switches, owner isolation, timers and page-state migration, checked in a real browser.
//
//   node scripts/verify-sat-release-and-isolation.mjs \
//     --release-root build/site --preview-root build/site-preview [--screenshots DIR] [--report FILE]
//
// build/site is what sat-release.json releases (python3 scripts/build-site.py); build/site-preview
// has every release feature on (python3 scripts/build-site.py --preview-all). Every account here is
// synthetic, every clock that matters is controlled, and nothing reads or writes real records.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const options = { releaseRoot: path.join(projectRoot, "build/site"), previewRoot: path.join(projectRoot, "build/site-preview"),
  screenshots: null, report: null };
for (let index = 2; index < process.argv.length; index += 1) {
  const [name, inline] = process.argv[index].split("=", 2);
  const key = { "--release-root": "releaseRoot", "--preview-root": "previewRoot", "--screenshots": "screenshots", "--report": "report" }[name];
  if (!key) throw new Error(`Unknown argument ${JSON.stringify(process.argv[index])}`);
  options[key] = path.resolve(inline ?? process.argv[++index]);
}
const roots = { release: options.releaseRoot, preview: options.previewRoot };
const manifests = {};
for (const [name, root] of Object.entries(roots)) {
  manifests[name] = JSON.parse(await readFile(path.join(root, "sat-practice-manifest.json"), "utf8"));
}
const FEATURES = ["expandedQuestionBank", "fixedMockExams", "wordExplanations", "walkthroughs"];
const results = {};

// ---------- static checks of the two builds
{
  const release = manifests.release, preview = manifests.preview;
  assert.deepEqual(release.features, Object.fromEntries(FEATURES.map(name => [name, false])), "The release build switches nothing on");
  assert.equal(release.uniqueQuestionCount, 1752, "The release build serves the reviewed 1752 questions");
  assert.equal(release.pageCount, 48, "The release build serves the reviewed 48 routes");
  assert.equal(release.pages.filter(page => page.examEligible).length, 0, "No mock exam opens in the release build");
  assert.deepEqual(release.vocabulary, [], "No AI-drafted word explanation is in the release build");
  assert.deepEqual(preview.features, Object.fromEntries(FEATURES.map(name => [name, true])));
  assert.ok(preview.uniqueQuestionCount > release.uniqueQuestionCount && preview.pageCount > release.pageCount);
  const previewIds = new Set(preview.questions.map(question => question.questionId));
  const previewRevision = new Map(preview.questions.map(question => [question.questionId, question.gradingRevision]));
  assert.ok(release.questions.every(question => previewIds.has(question.questionId)), "The preview keeps every released question id");
  assert.ok(release.questions.every(question => previewRevision.get(question.questionId) === question.gradingRevision),
    "The preview grades every released question the same way");
  const previewRoutes = new Set(preview.pages.map(page => page.route));
  assert.ok(release.pages.every(page => previewRoutes.has(page.route)), "The preview keeps every released route");
  results.builds = { release: [release.uniqueQuestionCount, release.pageCount], preview: [preview.uniqueQuestionCount, preview.pageCount] };
}

// ---------- browser harness
function findChrome() {
  const candidates = process.env.CHROME_PATH ? [process.env.CHROME_PATH] : [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium"];
  for (const candidate of candidates) {
    const result = spawnSync(candidate, ["--version"], { encoding: "utf8", timeout: 10000 });
    if (!result.error && result.status === 0) return candidate;
  }
  throw new Error("Chrome/Chromium is required. Install it or set CHROME_PATH to its executable.");
}
const mime = new Map([[".html", "text/html; charset=utf-8"], [".json", "application/json; charset=utf-8"], [".png", "image/png"], [".svg", "image/svg+xml"]]);
const server = createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    const [, prefix, ...rest] = pathname.split("/");
    const root = roots[prefix];
    if (!root) throw new Error("not found");
    const file = path.resolve(root, rest.join("/") || "index.html");
    if (!file.startsWith(root + path.sep) || !(await stat(file)).isFile()) throw new Error("not found");
    response.writeHead(200, { "content-type": mime.get(path.extname(file)) || "application/octet-stream" });
    response.end(await readFile(file));
  } catch {
    response.writeHead(404).end("Not found");
  }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const profile = await mkdtemp(path.join(os.tmpdir(), "sat-release-isolation-"));
const chrome = spawn(findChrome(), ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  `--user-data-dir=${profile}`, "--remote-debugging-port=0", "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
const endpoint = await new Promise((resolve, reject) => {
  let buffer = "";
  const timer = setTimeout(() => reject(new Error("Chrome DevTools endpoint timed out")), 15000);
  chrome.stderr.on("data", chunk => {
    buffer += chunk.toString();
    const match = buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
    if (match) { clearTimeout(timer); resolve(match[1]); }
  });
  chrome.once("exit", code => reject(new Error(`Chrome exited early (${code})`)));
});
const socket = new WebSocket(endpoint);
await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
let nextId = 0;
const pending = new Map();
socket.addEventListener("message", event => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message)); else resolve(message.result || {});
});
const send = (method, params = {}, sessionId) => {
  const id = ++nextId;
  socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
};

try {
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Page.enable", {}, sessionId);
  await send("Runtime.enable", {}, sessionId);
  const evaluate = async expression => {
    const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, sessionId);
    if (result.exceptionDetails) {
      throw new Error((result.exceptionDetails.exception && result.exceptionDetails.exception.description) || result.exceptionDetails.text);
    }
    return result.result?.value;
  };
  const navigate = async url => {
    await send("Page.navigate", { url }, sessionId);
    for (let attempt = 0; attempt < 200; attempt += 1) {
      if (await evaluate(`document.readyState === "complete" && location.href === ${JSON.stringify(url)} && Boolean(document.querySelector("#checkCurrentBtn, #app"))`).catch(() => false)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`Page did not become ready: ${url}`);
  };
  const viewport = (width, height) => send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 }, sessionId);
  const screenshot = async name => {
    if (!options.screenshots) return;
    await mkdir(options.screenshots, { recursive: true });
    await new Promise(resolve => setTimeout(resolve, 150));
    const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
    await writeFile(path.join(options.screenshots, name + ".png"), Buffer.from(data, "base64"));
  };
  const clearStorage = async prefix => { await navigate(`${origin}/${prefix}/index.html`); await evaluate("localStorage.clear()"); };
  // Synthetic sessions: a verified owner, and one whose authentication no longer matches.
  const sessionSource = `window.__session = (id, current = id, wait = null) => ({ profile: { userId: id }, user: { uid: id },
    auth: { getCurrentUser: async () => { if (wait) await wait; return { uid: current }; } }, callFunction: async () => ({}) });`;
  await viewport(1280, 800);

  // ===== 1. The release build shows none of the switched-off features
  await clearStorage("release");
  await navigate(`${origin}/release/index.html#home`);
  results.releaseIndex = await evaluate(`(() => {
    const links = [...document.querySelectorAll(".desktop-nav a, .mobile-nav a")].map(link => link.getAttribute("href"));
    const home = { examLink: links.includes("#exam"), vocabularyLink: links.includes("#vocabulary/list"),
      examCard: Boolean(document.querySelector(".hb-exam")), noExamLayout: Boolean(document.querySelector(".home.no-exam")) };
    location.hash = "#exam"; render();
    const exam = { home: Boolean(document.querySelector(".home")), center: /模考中心|MOCK EXAM/.test(document.getElementById("app").textContent) };
    location.hash = "#vocabulary/list"; render();
    const vocabulary = { home: Boolean(document.querySelector(".home")), list: Boolean(document.getElementById("glossaryQuery")) };
    location.hash = "#home"; render();
    return { home, exam, vocabulary };
  })()`);
  assert.deepEqual(results.releaseIndex, { home: { examLink: false, vocabularyLink: false, examCard: false, noExamLayout: true },
    exam: { home: true, center: false }, vocabulary: { home: true, list: false } }, "The release learning centre has no mock-exam or vocabulary entry");
  await screenshot("release-home-desktop");
  await viewport(390, 844);
  await screenshot("release-home-390");
  await viewport(1280, 800);

  const releasePractice = `${origin}/release/sets/13-2025-03-apac-a.html`;
  await navigate(releasePractice);
  results.releasePractice = await evaluate(`(() => {
    const question = QUESTIONS[current], wrong = question.choices.findIndex(choice => choice.label !== question.correct_answer);
    document.querySelectorAll(".choice")[wrong].click();
    document.getElementById("checkCurrentBtn").click();
    document.getElementById("revealBtn").click();
    return { features: FEATURES, words: document.querySelectorAll(".gloss[data-gloss]").length + Object.keys(PAGE_VOCABULARY).length,
      walkthroughData: QUESTIONS.filter(item => item.walkthrough || item.vocabulary).length,
      walkthroughShown: document.querySelectorAll("[data-walk]").length, vocabularyButton: !document.getElementById("vocabularyBtn").hidden,
      examButton: !document.getElementById("examModeBtn").hidden, explanation: document.getElementById("explanation").textContent.trim().length > 0 };
  })()`);
  assert.deepEqual(results.releasePractice, { features: Object.fromEntries(FEATURES.map(name => [name, false])), words: 0, walkthroughData: 0,
    walkthroughShown: 0, vocabularyButton: false, examButton: false, explanation: true }, "A released practice page carries no AI-drafted words or walkthroughs");
  await screenshot("release-practice-desktop");
  await viewport(390, 844);
  await screenshot("release-practice-390");
  await viewport(1280, 800);
  await evaluate(`learningStorage.setItem("sat-exam-history:v1", JSON.stringify({ version: 1, records: [{ id: "rec-release", pageId: PAGE_ID, modules: [] }] }))`);
  for (const query of ["?mode=exam", "?mode=exam&history=rec-release"]) {
    await navigate(releasePractice + query);
    const shell = await evaluate(`document.getElementById("examShell").textContent.replace(/\\s+/g, " ").trim()`);
    assert.match(shell, /模考暂未开放/, `${query}: a release page does not open mock exams or their history`);
    assert.doesNotMatch(shell, /模考记录|正确率/, `${query}: no record is read`);
  }

  // ===== 2. Old category page state comes along when the expanded bank is on
  const category = "categories/transitions.html";
  const oldIds = manifests.release.pages.find(page => page.route === category).questionIds;
  const newIds = manifests.preview.pages.find(page => page.route === category).questionIds;
  const moved = oldIds.find((id, index) => newIds.indexOf(id) !== index && index > 3);
  assert.ok(moved, "Some question changes position when the category page grows");
  const oldKey = "sat-practice:reading_writing:" + oldIds.join("|");
  await clearStorage("preview");
  await evaluate(`localStorage.setItem(${JSON.stringify("sat-owner:v1:anonymous-local:" + oldKey)}, JSON.stringify({
    current: ${oldIds.indexOf(moved)}, currentQuestionId: ${JSON.stringify(moved)}, currentUpdatedAt: "2026-11-20T10:00:00.000Z",
    lastVisitedAt: "2026-11-20T10:00:00.000Z", answers: { ${JSON.stringify(moved)}: { selected: "B", checked: true, submitted: true,
      updatedAt: "2026-11-20T10:00:00.000Z", submittedAt: "2026-11-20T10:00:00.000Z" } } }))`);
  await navigate(`${origin}/preview/${category}`);
  results.categoryMigration = await evaluate(`({ key: storageKey === ${JSON.stringify(oldKey)}, migrations: PAGE_MIGRATIONS.some(item => item.storageKey === ${JSON.stringify(oldKey)}),
    on: QUESTIONS[current].question_id, selected: state.answers[${JSON.stringify(moved)}]?.selected, submitted: state.answers[${JSON.stringify(moved)}]?.submitted,
    oldIndex: ${oldIds.indexOf(moved)}, newIndex: current })`);
  assert.deepEqual({ ...results.categoryMigration, oldIndex: undefined, newIndex: undefined },
    { key: false, migrations: true, on: moved, selected: "B", submitted: true, oldIndex: undefined, newIndex: undefined },
    "The grown category page reads the old page key and resumes on the same question, not the same index");
  assert.notEqual(results.categoryMigration.oldIndex, results.categoryMigration.newIndex);
  await navigate(`${origin}/release/${category}`);
  assert.equal(await evaluate(`storageKey === ${JSON.stringify(oldKey)}`), true, "The release build keeps the deployed page key");

  // ===== 3. Mock-exam history belongs to the account that opened it
  const examPage = manifests.preview.pages.find(page => page.examEligible && page.route === "sets/34-2026-06-apac-b-fixed-hard.html");
  assert.ok(examPage, "The preview offers the hand-made set 34 as a mock exam");
  const historyUrl = `${origin}/preview/${examPage.route}?mode=exam&history=rec-a`;
  await clearStorage("preview");
  await navigate(historyUrl);
  await evaluate(sessionSource);
  const shell = `document.getElementById("examShell").textContent.replace(/\\s+/g, " ").trim()`;
  results.history = {};
  results.history.start = await evaluate(shell);
  assert.match(results.history.start, /找不到这条模考记录/);
  results.history.recordA = await evaluate(`(() => {
    bindLearningIdentity(window.__session("owner-a"));
    const [first, second] = EXAM_CONFIG.modules ? Object.keys(EXAM_CONFIG.modules) : [];
    const ids = EXAM_CONFIG.modules[first].questionIds.slice(0, 2), q = ids.map(examQuestionById);
    const wrong = q[1].choices.find(choice => choice.label !== q[1].correct_answer).label;
    window.__recordA = { version: 1, id: "rec-a", pageId: PAGE_ID, title: document.title, variantId: "fixed-hard", completedAt: Date.now() - 86400000,
      modules: [{ key: first, label: "Module 1", questionIds: ids, answers: { [ids[0]]: q[0].correct_answer, [ids[1]]: wrong }, marked: [],
        score: { correct: 2, incorrect: 0, blank: 0, total: 2 }, revisions: { [ids[0]]: q[0].gradingRevision, [ids[1]]: "revision-before-correction" } }] };
    learningStorage.setItem(EXAM_HISTORY_KEY, JSON.stringify({ version: 1, records: [window.__recordA] }));
    window.__rawA = localStorage.getItem("sat-owner:v1:" + encodeURIComponent("user:owner-a") + ":" + EXAM_HISTORY_KEY);
    examHistoryView = null; render();
    return document.getElementById("examShell").textContent.replace(/\\s+/g, " ").trim();
  })()`);
  assert.match(results.history.recordA, /模考记录/);
  assert.match(results.history.recordA, /完成时成绩 · 2 \/ 2 题答对/, "The record leads with the score it had when it was completed");
  assert.match(results.history.recordA, /按当前题库重算：1 \/ 2（50%）。其中 1 题的答案或解析在完成后有更新/,
    "The recount against the current answer key is shown beside it and says why it differs");
  await screenshot("preview-history-desktop");
  await viewport(390, 844);
  await screenshot("preview-history-390");
  await viewport(1280, 800);
  await evaluate(`document.getElementById("examEnterReview").click()`);
  assert.equal(await evaluate(`examHistoryView.phase`), "review");
  results.history.switchToB = await evaluate(`(() => { bindLearningIdentity(window.__session("owner-b")); return ${shell}; })()`);
  assert.match(results.history.switchToB, /找不到这条模考记录/, "B sees no record of A");
  assert.doesNotMatch(results.history.switchToB, /完成时成绩|答案与解析/);
  assert.equal(await evaluate("examHistoryView"), null);
  results.history.signOut = await evaluate(`(() => { bindLearningIdentity({}); return ${shell}; })()`);
  assert.match(results.history.signOut, /正在确认账号/, "Without a verified owner no record is shown");
  results.history.backToA = await evaluate(`(() => { bindLearningIdentity(window.__session("owner-a")); return ${shell}; })()`);
  assert.match(results.history.backToA, /完成时成绩 · 2 \/ 2/, "A's own record comes back for A");
  results.history.identityFailure = await evaluate(`(async () => {
    let refused = false;
    try { await learningStorage.callOwned(window.__session("owner-a", "someone-else"), "getSatPracticeState", {}); } catch { refused = true; }
    return { refused, shell: ${shell}, view: examHistoryView };
  })()`);
  assert.equal(results.history.identityFailure.refused, true);
  assert.match(results.history.identityFailure.shell, /正在确认账号/, "A failed identity check clears the review");
  assert.equal(results.history.identityFailure.view, null);
  results.history.lateCallback = await evaluate(`(async () => {
    bindLearningIdentity(window.__session("owner-a"));
    let release; const wait = new Promise(resolve => { release = resolve; });
    const late = learningStorage.callOwned(window.__session("owner-a", "owner-a", wait), "getSatPracticeState", {}).then(() => "applied", () => "refused");
    bindLearningIdentity(window.__session("owner-b"));
    release();
    return { late: await late, shell: ${shell}, rawA: localStorage.getItem("sat-owner:v1:" + encodeURIComponent("user:owner-a") + ":" + EXAM_HISTORY_KEY) === window.__rawA };
  })()`);
  assert.equal(results.history.lateCallback.late, "refused", "A's late callback does not land in B's session");
  assert.match(results.history.lateCallback.shell, /找不到这条模考记录/);
  assert.equal(results.history.lateCallback.rawA, true, "A's stored record is never changed or cleared");

  // Old records: no revisions, a question that has since left the set
  results.history.oldRecord = await evaluate(`(() => {
    bindLearningIdentity(window.__session("owner-a"));
    const record = JSON.parse(JSON.stringify(window.__recordA));
    record.id = "rec-a"; delete record.modules[0].revisions;
    record.modules[0].questionIds.push("retired_question_x"); record.modules[0].score = { correct: 3, incorrect: 0, blank: 0, total: 3 };
    learningStorage.setItem(EXAM_HISTORY_KEY, JSON.stringify({ version: 1, records: [record] }));
    examHistoryView = null; render();
    const text = ${shell};
    return { text, stored: JSON.parse(learningStorage.getItem(EXAM_HISTORY_KEY)).records[0].modules[0].questionIds.length };
  })()`);
  assert.match(results.history.oldRecord.text, /完成时成绩 · 3 \/ 3/);
  assert.match(results.history.oldRecord.text, /早于题目版本标记.*1 题已不在当前题库，没有计入重算/);
  assert.equal(results.history.oldRecord.stored, 3, "Viewing never rewrites a record");
  await evaluate(`learningStorage.setItem(EXAM_HISTORY_KEY, window.__rawA.slice(0)); 0`).catch(() => 0);
  await evaluate(`localStorage.setItem("sat-owner:v1:" + encodeURIComponent("user:owner-a") + ":" + EXAM_HISTORY_KEY, window.__rawA)`);
  // The exam centre card states that its number is the score at completion.
  await navigate(`${origin}/preview/index.html#exam`);
  await evaluate(sessionSource);
  results.history.centre = await evaluate(`(() => { learningStorage.bind(window.__session("owner-a")); render();
    return document.querySelector(".exam-row .score")?.textContent.replace(/\\s+/g, " ").trim() || ""; })()`);
  assert.match(results.history.centre, /^完成时 100% 2\/2$/);

  // ===== 4. Study time: credited by the state each stretch had, and to its own owner only
  await navigate(`${origin}/preview/index.html`);
  results.studyClock = await evaluate(`(() => {
    let visibility = "visible";
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
    const show = value => { visibility = value; document.dispatchEvent(new Event("visibilitychange")); };
    function fakeStore(first) {
      const data = new Map(), listeners = [];
      let owner = first, generation = 0, failing = false;
      return { ready: () => Boolean(owner), token: () => owner + ":" + generation,
        isCurrent: token => Boolean(owner) && token === owner + ":" + generation,
        onOwnerChange: listener => listeners.push(listener),
        getItem: key => data.get(owner + "|" + key) ?? null,
        setItem: (key, value) => { if (failing) throw new Error("quota"); data.set(owner + "|" + key, value); },
        switchTo(next, settle = true) { if (owner) listeners.forEach(listener => listener({ token: owner + ":" + generation, settle })); owner = next; generation += 1; },
        fail(value) { failing = value; },
        seconds(who) { const raw = JSON.parse(data.get(who + "|" + SAT_STUDY_TIME_KEY) || "null"); return raw ? Object.values(raw.days).reduce((sum, value) => sum + value, 0) : 0; } };
    }
    const result = {};
    let time = 1e12;
    const now = () => time;
    const begin = owner => { show("visible"); const store = fakeStore(owner); return { store, clock: startSatStudyClock(store, { now, tickMs: 15000 }) }; };

    // six visits of ten seconds, each shorter than one tick
    let { store, clock } = begin("A");
    for (let visit = 0; visit < 6; visit += 1) { show("visible"); time += 10000; show("hidden"); }
    result.sixShortVisits = store.seconds("A"); clock.stop();

    // a minute in the background counts nothing; coming back starts afresh
    ({ store, clock } = begin("A"));
    time += 5000; show("hidden"); time += 60000; show("visible"); time += 3000; clock.flush();
    result.background = store.seconds("A"); clock.stop();

    // A for 10 s, then B in the same page for 5 s
    ({ store, clock } = begin("A"));
    time += 10000; store.switchTo("B"); time += 5000; clock.flush();
    result.switch = [store.seconds("A"), store.seconds("B")];
    // signing out settles B; a failed identity drops what was pending
    time += 7000; store.switchTo("C");
    time += 4000; store.switchTo("", false);
    result.signOut = [store.seconds("B"), store.seconds("C")]; clock.stop();

    // a failed write is retried for the same owner, and dropped when the owner changes first
    ({ store, clock } = begin("A"));
    store.fail(true); time += 10000; result.failedWrite = clock.flush();
    store.fail(false); clock.flush(); result.retried = store.seconds("A");
    store.fail(true); time += 10000; clock.flush(); store.switchTo("B"); store.fail(false);
    time += 3000; clock.flush();
    result.notMoved = [store.seconds("A"), store.seconds("B")]; clock.stop();

    // pagehide and visibilitychange together count the stretch once
    ({ store, clock } = begin("A"));
    time += 10000; window.dispatchEvent(new Event("pagehide")); show("hidden");
    result.once = store.seconds("A"); clock.stop();
    show("visible");
    delete document.visibilityState;
    return result;
  })()`);
  assert.deepEqual(results.studyClock, { sixShortVisits: 60, background: 8, switch: [10, 5], signOut: [12, 0],
    failedWrite: false, retried: 10, notMoved: [10, 3], once: 10 }, "Study time follows visible stretches and stays with its owner");

  // ===== 5. Per-question time: per owner, counted once across tabs, kept through failed writes
  await clearStorage("preview");
  await navigate(`${origin}/preview/sets/13-2025-03-apac-a.html`);
  await evaluate(sessionSource);
  results.questionTimer = await evaluate(`(() => {
    const realNow = Date.now;
    let time = realNow();
    Date.now = () => time;
    let visibility = "visible";
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
    const show = value => { visibility = value; document.dispatchEvent(new Event("visibilitychange")); };
    const raw = owner => JSON.parse(localStorage.getItem("sat-owner:v1:" + encodeURIComponent(owner) + ":" + QUESTION_TIME_KEY) || "null")?.times || {};
    const result = {};
    try {
      bindLearningIdentity(window.__session("owner-a"));
      const key = timerKey;
      holdQuestionTimer(); runQuestionTimer();
      time += 10000;
      bindLearningIdentity(window.__session("owner-b"));
      time += 5000; holdQuestionTimer(); runQuestionTimer();
      result.owners = [raw("user:owner-a")[key]?.ms, raw("user:owner-b")[key]?.ms];
      // another tab adds its own time while this one is open
      const other = raw("user:owner-b");
      other["q:written-by-another-tab"] = { ms: 7000, at: "" };
      other[key].ms += 2000;
      localStorage.setItem("sat-owner:v1:" + encodeURIComponent("user:owner-b") + ":" + QUESTION_TIME_KEY, JSON.stringify({ version: 1, times: other }));
      time += 1000; holdQuestionTimer(); runQuestionTimer();
      result.tabs = [raw("user:owner-b")[key].ms, raw("user:owner-b")["q:written-by-another-tab"]?.ms];
      // the stretch before the page is hidden is kept; the time hidden is not counted
      time += 4000; show("hidden"); result.hidden = raw("user:owner-b")[key].ms;
      time += 60000; show("visible"); time += 1000; holdQuestionTimer(); runQuestionTimer();
      result.back = raw("user:owner-b")[key].ms;
      // a failed write keeps the time for this owner and shows it; the next write stores it
      const setItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function (name, value) { if (String(name).endsWith(QUESTION_TIME_KEY)) throw new DOMException("quota", "QuotaExceededError"); return setItem.call(this, name, value); };
      time += 6000; holdQuestionTimer(); runQuestionTimer();
      result.failed = [raw("user:owner-b")[key].ms, questionTimerElapsed()];
      Storage.prototype.setItem = setItem;
      holdQuestionTimer(); runQuestionTimer();
      result.retried = raw("user:owner-b")[key].ms;
      // a failed write is never handed to the next owner
      Storage.prototype.setItem = function (name, value) { if (String(name).endsWith(QUESTION_TIME_KEY)) throw new DOMException("quota", "QuotaExceededError"); return setItem.call(this, name, value); };
      time += 9000; holdQuestionTimer(); runQuestionTimer();
      bindLearningIdentity(window.__session("owner-c"));
      Storage.prototype.setItem = setItem;
      time += 2000; holdQuestionTimer();
      result.notMoved = [raw("user:owner-b")[key].ms, raw("user:owner-c")[key]?.ms];
    } finally {
      Date.now = realNow;
      delete document.visibilityState;
    }
    return result;
  })()`);
  assert.deepEqual(results.questionTimer, { owners: [10000, 5000], tabs: [8000, 7000], hidden: 12000, back: 13000,
    failed: [13000, 19000], retried: 19000, notMoved: [19000, 2000] }, "Per-question time stays with its owner and adds up across tabs");

  // ===== 6. A note that could not be saved stays in the box, with a message, through a redraw
  await clearStorage("preview");
  await navigate(`${origin}/preview/index.html#vocabulary/all`);
  results.note = await evaluate(`(() => {
    saveSatVocabularyWord(learningStorage, { word: "lyric", custom: true }); render();
    const note = () => document.querySelector('[data-vocab-note="lyric"]');
    const status = () => note().parentElement.querySelector(".vocab-note-status").textContent;
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (name, value) { if (String(name).endsWith(SAT_VOCABULARY_KEY)) throw new DOMException("quota", "QuotaExceededError"); return setItem.call(this, name, value); };
    note().value = "first meaning: a short poem"; note().dispatchEvent(new Event("change"));
    const failed = { status: status(), invalid: note().getAttribute("aria-invalid") };
    render();
    const redrawn = { value: note().value, status: status() };
    Storage.prototype.setItem = setItem;
    note().value = "a short poem; the words of a song"; note().dispatchEvent(new Event("input")); note().dispatchEvent(new Event("change"));
    const saved = { stored: readSatVocabulary(learningStorage)[0].note, status: status(), invalid: note().getAttribute("aria-invalid") };
    return { failed, redrawn, saved };
  })()`);
  assert.match(results.note.failed.status, /没有保存成功，内容仍在框里/);
  assert.equal(results.note.failed.invalid, "true");
  assert.deepEqual(results.note.redrawn.value, "first meaning: a short poem", "The unsaved note survives a redraw");
  assert.match(results.note.redrawn.status, /没有保存成功/);
  assert.deepEqual(results.note.saved, { stored: "a short poem; the words of a song", status: "", invalid: null });
  if (options.screenshots) {
    await viewport(390, 844);
    await evaluate(`(() => {
      window.__setItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function (name, value) { if (String(name).endsWith(SAT_VOCABULARY_KEY)) throw new DOMException("quota", "QuotaExceededError"); return window.__setItem.call(this, name, value); };
      const card = document.querySelector('[data-vocab="lyric"]'); card.open = true;
      const note = card.querySelector("[data-vocab-note]"); note.value = "a short poem; the words of a song (edited)"; note.dispatchEvent(new Event("change"));
      card.scrollIntoView({ block: "start" }); window.scrollBy(0, -70);
    })()`);
    await screenshot("preview-note-not-saved-390");
    await evaluate("Storage.prototype.setItem = window.__setItem");
    await viewport(1280, 800);
  }
  await navigate(`${origin}/preview/index.html#home`);
  await screenshot("preview-home-desktop");

  console.log("Release and isolation checks passed: the release build serves "
    + `${results.builds.release.join(" questions / ")} routes with every switch off; old category page state migrates; `
    + "mock-exam history, study time and per-question time stay with their owner; notes that fail to save are kept.");
  if (options.report) {
    await mkdir(path.dirname(options.report), { recursive: true });
    await writeFile(options.report, JSON.stringify({ schema: "sat-release-isolation-v1", origin: "synthetic", results }, null, 2) + "\n");
  }
} finally {
  try { socket.close(); } catch {}
  chrome.kill();
  server.close();
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
