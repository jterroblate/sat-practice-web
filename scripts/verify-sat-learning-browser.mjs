import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const defaultRoot = path.join(projectRoot, "build", "site");

function positiveInteger(value, label) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 1) {
    throw new Error(`${label} must be a positive integer, received ${JSON.stringify(value)}`);
  }
  return parsed;
}

function parseOptions(argv) {
  const options = {
    expectedUnique: null,
    expectedPages: null,
    report: null,
    root: defaultRoot,
  };
  const aliases = new Map([
    ["--expected-unique", "expectedUnique"],
    ["--expected-pages", "expectedPages"],
    ["--report", "report"],
    ["--root", "root"],
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      console.log("Usage: node scripts/verify-sat-learning-browser.mjs [--root DIR] [--expected-unique N] [--expected-pages N] [--report FILE]");
      process.exit(0);
    }
    const [name, inlineValue] = argument.split("=", 2);
    const key = aliases.get(name);
    if (!key) throw new Error(`Unknown argument ${JSON.stringify(argument)}`);
    const value = inlineValue ?? argv[++index];
    if (value === undefined) throw new Error(`${name} requires a value`);
    options[key] = ["report", "root"].includes(key)
      ? value
      : positiveInteger(value, name);
  }
  return options;
}

const options = parseOptions(process.argv.slice(2));
const root = path.resolve(options.root);
// HTTP localhost has no authentication gate and deliberately uses this owner.
// Raw legacy keys are never fixtures for an authenticated/current owner.
const LOCAL_OWNER = "anonymous-local";
const FIXTURE_USER = "sat-browser-regression-fixture";
const ownerStorageKey = (owner, key) => `sat-owner:v1:${encodeURIComponent(owner)}:${key}`;

const manifest = JSON.parse(await readFile(path.join(root, "sat-practice-manifest.json"), "utf8"));
assert.ok(Array.isArray(manifest.pages), "Manifest pages must be an array");
assert.ok(Array.isArray(manifest.questions), "Manifest questions must be an array");
const derivedUniqueCount = new Set(manifest.questions.map(question => question.questionId)).size;
const derivedPageCount = manifest.pages.length;
const expectedUniqueCount = options.expectedUnique ?? derivedUniqueCount;
const expectedPageCount = options.expectedPages ?? derivedPageCount;
assert.equal(manifest.uniqueQuestionCount, derivedUniqueCount, "Manifest uniqueQuestionCount must match its question records");
assert.equal(manifest.pageCount, derivedPageCount, "Manifest pageCount must match its page records");
assert.equal(derivedUniqueCount, expectedUniqueCount, `Expected ${expectedUniqueCount} unique manifest questions`);
assert.equal(derivedPageCount, expectedPageCount, `Expected ${expectedPageCount} manifest pages`);
assert.equal(new Set(manifest.pages.map(page => page.route)).size, derivedPageCount, "Manifest routes must be unique");
const manifestSetPages = manifest.pages.filter(page => page.kind === "set");
assert.ok(manifestSetPages.length > 0, "Manifest must contain at least one set route");
const expectedSkillCount = new Set(manifest.questions.map(question => question.skill)).size;
function findChrome() {
  const candidates = process.env.CHROME_PATH ? [process.env.CHROME_PATH] : [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ...(process.env.PROGRAMFILES ? [path.join(process.env.PROGRAMFILES, "Google", "Chrome", "Application", "chrome.exe")] : []),
    ...(process.env.LOCALAPPDATA ? [path.join(process.env.LOCALAPPDATA, "Google", "Chrome", "Application", "chrome.exe")] : []),
  ];
  for (const candidate of candidates) {
    const result = spawnSync(candidate, ["--version"], { encoding: "utf8", timeout: 10000 });
    if (!result.error && result.status === 0) return candidate;
  }
  throw new Error("Chrome/Chromium is required. Install it or set CHROME_PATH to its executable.");
}
const chromePath = findChrome();
const profile = await mkdtemp(path.join(os.tmpdir(), "sat-learning-browser-"));
const mime = new Map([[".html", "text/html; charset=utf-8"], [".json", "application/json; charset=utf-8"], [".png", "image/png"]]);

const server = createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    const relative = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
    const file = path.resolve(root, relative);
    if (!file.startsWith(root + path.sep) || !(await stat(file)).isFile()) throw new Error("not found");
    response.writeHead(200, { "content-type": mime.get(path.extname(file)) || "application/octet-stream" });
    response.end(await readFile(file));
  } catch {
    response.writeHead(404).end("Not found");
  }
});

await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;

async function auditManifestRoutes() {
  const manifestRouteHtml = new Map();
  await Promise.all(manifest.pages.map(async page => {
    assert.equal(typeof page.route, "string", `Manifest page ${JSON.stringify(page.pageId)} must have a route`);
    const routeUrl = new URL(page.route, `${origin}/`);
    assert.equal(routeUrl.origin, origin, `Manifest route must stay on the local origin: ${page.route}`);
    const response = await fetch(routeUrl);
    assert.ok(response.ok, `Manifest ${page.kind || "unknown"} route is not reachable (${response.status}): ${page.route}`);
    manifestRouteHtml.set(page.route, await response.text());
  }));
  assert.ok(
    manifestSetPages.every(page => manifestRouteHtml.has(page.route)),
    "Every manifest set route must be reachable",
  );

  const loadedChartReferences = new Map();
  const canonicalChartReferences = new Map();
  const chartFieldPattern = /"(?:chart_image_data_uri|image_data_uri|material_panel_crop_data_uri)"\s*:\s*"([^"\n]*chart_images\/[A-Za-z0-9][A-Za-z0-9._/-]*\.(?:png|jpe?g|webp|svg))"/gi;
  const chartImagePattern = /<img\b[^>]*\bsrc=["']([^"'\n]*chart_images\/[A-Za-z0-9][A-Za-z0-9._/-]*\.(?:png|jpe?g|webp|svg))["']/gi;
  const canonicalChartPattern = /chart_images\/([A-Za-z0-9][A-Za-z0-9._/-]*\.(?:png|jpe?g|webp|svg))/gi;
  for (const [route, html] of manifestRouteHtml) {
    for (const pattern of [chartFieldPattern, chartImagePattern]) {
      for (const match of html.matchAll(pattern)) {
        const assetUrl = new URL(match[1], new URL(route, `${origin}/`));
        assert.equal(assetUrl.origin, origin, `Chart asset must stay on the local origin: ${match[1]}`);
        if (!loadedChartReferences.has(assetUrl.href)) loadedChartReferences.set(assetUrl.href, []);
        loadedChartReferences.get(assetUrl.href).push(route);
      }
    }
    for (const match of html.matchAll(canonicalChartPattern)) {
      const assetUrl = new URL(`categories/chart_images/${match[1]}`, `${origin}/`);
      if (!canonicalChartReferences.has(assetUrl.href)) canonicalChartReferences.set(assetUrl.href, []);
      canonicalChartReferences.get(assetUrl.href).push(route);
    }
  }
  assert.ok(loadedChartReferences.size > 0, "Manifest routes must load at least one external chart asset");
  assert.ok(canonicalChartReferences.size > 0, "Manifest routes must reference at least one canonical chart asset");
  await Promise.all([...loadedChartReferences].map(async ([assetUrl, routes]) => {
    const response = await fetch(assetUrl);
    assert.ok(response.ok, `Browser-loaded chart asset is missing (${response.status}): ${assetUrl} from ${routes.join(", ")}`);
  }));
  await Promise.all([...canonicalChartReferences].map(async ([assetUrl, routes]) => {
    const response = await fetch(assetUrl);
    assert.ok(response.ok, `Canonical chart asset is missing (${response.status}): ${assetUrl} from ${routes.join(", ")}`);
  }));

  const quantitativeHtml = manifestRouteHtml.get("categories/command-of-evidence-quantitative.html");
  assert.equal(typeof quantitativeHtml, "string", "Manifest must include the quantitative evidence category route");
  // An inlined chart costs tens of kilobytes per question; text, table data and
  // explanations stay well under this budget however many questions the category holds.
  const quantitativeQuestions = manifest.pages
    .find(page => page.route === "categories/command-of-evidence-quantitative.html").questionIds.length;
  assert.ok(
    Buffer.byteLength(quantitativeHtml) < 300_000 + 6_000 * quantitativeQuestions,
    "Quantitative practice page should keep charts outside the HTML payload",
  );
  assert.doesNotMatch(quantitativeHtml, /"chart_image_data_uri":"data:/);
  return { loadedChartReferences, canonicalChartReferences };
}

const chrome = spawn(chromePath, [
  "--headless=new",
  "--disable-gpu",
  "--no-first-run",
  "--no-default-browser-check",
  `--user-data-dir=${profile}`,
  "--remote-debugging-port=0",
  "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });

function devtoolsUrl() {
  return new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error("Chrome DevTools endpoint timed out")), 15000);
    chrome.stderr.on("data", chunk => {
      buffer += chunk.toString();
      const match = buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (!match) return;
      clearTimeout(timer);
      resolve(match[1]);
    });
    chrome.once("exit", code => reject(new Error(`Chrome exited early (${code})`)));
    chrome.once("error", reject);
  });
}

class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 0;
    this.pending = new Map();
  }
  async open() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", event => {
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result || {});
    });
  }
  send(method, params = {}, sessionId) {
    const id = ++this.nextId;
    this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
}

let cdp;
let loadedChartReferences = new Map();
let canonicalChartReferences = new Map();
try {
  const [routeAudit, endpoint] = await Promise.all([auditManifestRoutes(), devtoolsUrl()]);
  ({ loadedChartReferences, canonicalChartReferences } = routeAudit);
  cdp = new Cdp(endpoint);
  await cdp.open();
  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
  await cdp.send("Page.enable", {}, sessionId);
  await cdp.send("Runtime.enable", {}, sessionId);

  const evaluate = async expression => {
    const result = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, sessionId);
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Browser evaluation failed");
    return result.result?.value;
  };
  const waitReady = async selector => {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const ready = await evaluate(`document.readyState === "complete" && Boolean(document.querySelector(${JSON.stringify(selector)}))`);
      if (ready) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`Page did not become ready: ${selector}`);
  };
  const installFixtureHelpers = () => evaluate(`(() => {
      window.__satFixtureRawKey = key => {
        const token = learningStorage.token(), owner = token.slice(0, token.lastIndexOf(":"));
        if (!owner || !learningStorage.ready()) throw new Error("Fixture requires a verified local owner");
        return "sat-owner:v1:" + encodeURIComponent(owner) + ":" + key;
      };
      window.__satFixtureFlush = async () => {
        if (typeof flushGlobalState === "function") flushGlobalState();
        // Wait behind the real queued owner lock; do not write fixture data to
        // force a result or assume the 120ms debounce has already completed.
        await learningStorage.coordinateGlobal(() => true);
      };
      window.__satFixtureSession = callFunction => ({
        profile: { userId: ${JSON.stringify(FIXTURE_USER)} },
        user: { uid: ${JSON.stringify(FIXTURE_USER)} },
        auth: { getCurrentUser: async () => ({ uid: ${JSON.stringify(FIXTURE_USER)} }) },
        callFunction,
      });
      if (learningStorage.token().split(":").slice(0, -1).join(":") !== ${JSON.stringify(LOCAL_OWNER)}) {
        throw new Error("Local browser fixture must start in anonymous-local scope");
      }
    })()`);
  const navigate = async url => {
    await cdp.send("Page.navigate", { url }, sessionId);
    await waitReady("#checkCurrentBtn, #app");
    await installFixtureHelpers();
  };
  const reload = async () => {
    await cdp.send("Page.reload", {}, sessionId);
    await waitReady("#checkCurrentBtn, #app");
    await installFixtureHelpers();
  };
  const waitForEvaluation = async (expression, label) => {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      if (await evaluate(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`Timed out waiting for ${label}`);
  };
  const snapshot = () => evaluate(`(async () => {
    await window.__satFixtureFlush();
    const page = JSON.parse(localStorage.getItem(window.__satFixtureRawKey(storageKey)) || "null");
    const global = JSON.parse(localStorage.getItem(window.__satFixtureRawKey(GLOBAL_KEY)) || "{}");
    return {
      feedback: document.getElementById("feedback")?.textContent || "",
      revealDisabled: document.getElementById("revealBtn")?.disabled,
      correctChoices: document.querySelectorAll(".choice.correct").length,
      incorrectChoices: document.querySelectorAll(".choice.incorrect").length,
      selectedChoices: document.querySelectorAll(".choice.selected").length,
      explanation: document.getElementById("explanation")?.textContent.trim() || "",
      page,
      global,
    };
  })()`);

  const practiceUrl = `${origin}/sets/13-2025-03-apac-a.html`;
  await navigate(`${origin}/index.html`);
  await evaluate("localStorage.clear()");
  await navigate(practiceUrl);
  const question = await evaluate("({id:QUESTIONS[0].question_id,answer:QUESTIONS[0].correct_answer,gradingRevision:QUESTIONS[0].gradingRevision})");
  assert.match(question.gradingRevision, /^[0-9a-f]{64}$/, "Fixtures require the current canonical grading revision");
  const schedule = await evaluate(`(() => {
    const early=defaultQuestionState(); early.nextDueAt=addDays(nowIso(),1); updateReviewSchedule(early,true,true);
    const due=defaultQuestionState(); due.nextDueAt=new Date(Date.now()-1000).toISOString(); updateReviewSchedule(due,true,true);
    const firstDays=Math.round((new Date(due.nextDueAt)-Date.now())/86400000);
    due.nextDueAt=new Date(Date.now()-1000).toISOString(); updateReviewSchedule(due,true,true);
    const secondDays=Math.round((new Date(due.nextDueAt)-Date.now())/86400000);
    due.nextDueAt=new Date(Date.now()-1000).toISOString(); updateReviewSchedule(due,true,true);
    return {earlyStage:early.reviewStage,firstDays,secondDays,finalStage:due.reviewStage,mastered:due.mastered,nextDueAt:due.nextDueAt};
  })()`);
  assert.deepEqual(schedule, { earlyStage: 0, firstDays: 3, secondDays: 7, finalStage: 3, mastered: true, nextDueAt: null });
  const resilientCloudSync = await evaluate(`(async () => {
    let releases = 0;
    const calls = [];
    window.satCloudbaseReleaseGate = () => { releases += 1; };
    await bootstrapCloudLearning(window.__satFixtureSession(async name => {
      calls.push(name);
      if (name === "getSatLearningOverview" || name === "saveSatQuestionReflection") {
        throw new Error("optional SAT learning service unavailable");
      }
      return {};
    }));
    await syncOverviewInBackground();
    const optionalReflectionSaved = await saveCurrentReflectionBestEffort();
    const syncStatus = document.getElementById("syncStatus").textContent;
    cloudSession = null;
    delete window.satCloudbaseReleaseGate;
    return { releases, syncStatus, optionalReflectionSaved,
      pageRead: calls.includes("getSatPracticeState"), pageSaved: calls.includes("saveSatPracticeState"),
      overviewAttempted: calls.includes("getSatLearningOverview"), reflectionAttempted: calls.includes("saveSatQuestionReflection") };
  })()`);
  assert.deepEqual(resilientCloudSync, { releases: 1, syncStatus: "进度已同步", optionalReflectionSaved: false,
    pageRead: true, pageSaved: true, overviewAttempted: true, reflectionAttempted: true });
  const delayedBootstrap = await evaluate(`(async () => {
    cloudBootstrapPromise = null;
    cloudSaveInFlight = null;
    cloudSavePending = false;
    locallyChangedQuestionIds.clear();
    localRevision = 0;
    let resolvePage;
    const delayedPage = new Promise(resolve => { resolvePage = resolve; });
    const session = window.__satFixtureSession(async name => {
      if (name === "getSatPracticeState") return delayedPage;
      return {};
    });
    const stimulusNode = document.getElementById("stimulus").firstChild;
    const choiceNode = document.querySelector(".choice");
    const bootstrap = bootstrapCloudLearning(session);
    await new Promise(resolve => setTimeout(resolve, 0));
    const question = QUESTIONS[current];
    const choiceIndex = question.choices.findIndex(choice => choice.label !== question.correct_answer);
    document.querySelectorAll(".choice")[choiceIndex].click();
    const locallySelected = activeState(question).selected;
    resolvePage({ state: {
      current: Math.min(QUESTIONS.length - 1, current + 1),
      currentUpdatedAt: "2099-01-01T00:00:00.000Z",
      answers: { [question.question_id]: {
        selected: question.correct_answer,
        updatedAt: "2099-01-01T00:00:00.000Z"
      } }
    } });
    await bootstrap;
    await new Promise(resolve => setTimeout(resolve, 0));
    const result = {
      selected: activeState(question).selected,
      locallySelected,
      currentQuestionId: currentQuestion().question_id,
      expectedQuestionId: question.question_id,
      sameStimulus: stimulusNode === document.getElementById("stimulus").firstChild,
      sameChoice: choiceNode === document.querySelector(".choice")
    };
    cloudSession = null;
    cloudBootstrapPromise = null;
    if (cloudSaveTimer) clearTimeout(cloudSaveTimer);
    cloudSaveTimer = null;
    return result;
  })()`);
  assert.equal(delayedBootstrap.selected, delayedBootstrap.locallySelected, JSON.stringify(delayedBootstrap));
  assert.equal(delayedBootstrap.currentQuestionId, delayedBootstrap.expectedQuestionId, JSON.stringify(delayedBootstrap));
  assert.equal(delayedBootstrap.sameStimulus, true, "Late cloud bootstrap must not rebuild the stimulus after local interaction");
  assert.equal(delayedBootstrap.sameChoice, true, "Selecting an answer must preserve the existing choice DOM nodes");
  const storageFailure = await evaluate(`(() => {
    const original = Storage.prototype.setItem;
    let threw = false;
    Storage.prototype.setItem = function(key, value) {
      if (key === window.__satFixtureRawKey(storageKey)) throw new DOMException("quota", "QuotaExceededError");
      return original.call(this, key, value);
    };
    try { persistLocalState(); } catch { threw = true; }
    Storage.prototype.setItem = original;
    return { threw, status: document.getElementById("syncStatus").textContent };
  })()`);
  assert.deepEqual(storageFailure, { threw: false, status: "本机存储空间不足，请保持联网" });

  const malformedOutboxFixtures = [
    { label: "legacy object", raw: JSON.stringify({ legacy: true }) },
    { label: "legacy string", raw: JSON.stringify("legacy queue") },
    { label: "malformed JSON", raw: "{broken" },
    { label: "invalid array entries", raw: JSON.stringify([null, "bad", { attemptId: "" }]) },
  ];
  for (const fixture of malformedOutboxFixtures) {
    await navigate(`${origin}/index.html`);
    await evaluate(`localStorage.clear(); localStorage.setItem(${JSON.stringify(ownerStorageKey(LOCAL_OWNER, "sat-learning:attempt-outbox:v2"))}, ${JSON.stringify(fixture.raw)})`);
    await navigate(practiceUrl);
    const result = await evaluate(`(() => {
      const errors = [];
      const onError = event => errors.push(String(event.error || event.message || "unknown error"));
      window.addEventListener("error", onError);
      const question = currentQuestion();
      const index = question.choices.findIndex(choice => choice.label !== question.correct_answer);
      document.querySelectorAll(".choice")[index].click();
      document.getElementById("checkCurrentBtn").click();
      const beforeReveal = {
        submitted: activeState(question).submitted,
        feedback: document.getElementById("feedback").textContent,
        revealDisabled: document.getElementById("revealBtn").disabled,
        correctChoices: document.querySelectorAll(".choice.correct").length,
        explanation: document.getElementById("explanation").textContent.trim()
      };
      document.getElementById("revealBtn").click();
      const outbox = JSON.parse(localStorage.getItem(window.__satFixtureRawKey(OUTBOX_KEY)) || "[]");
      window.removeEventListener("error", onError);
      return {
        beforeReveal,
        errors,
        outboxIsArray: Array.isArray(outbox),
        outboxLength: outbox.length,
        latestRevealed: outbox.at(-1)?.revealed,
        correctChoicesAfterReveal: document.querySelectorAll(".choice.correct").length
      };
    })()`);
    assert.deepEqual(result.beforeReveal, {
      submitted: true,
      feedback: "回答错误",
      revealDisabled: false,
      correctChoices: 0,
      explanation: "",
    }, `${fixture.label}: ${JSON.stringify(result)}`);
    assert.deepEqual(result.errors, [], `${fixture.label}: ${JSON.stringify(result)}`);
    assert.equal(result.outboxIsArray, true, `${fixture.label}: ${JSON.stringify(result)}`);
    assert.equal(result.outboxLength, 1, `${fixture.label}: ${JSON.stringify(result)}`);
    assert.equal(result.latestRevealed, true, `${fixture.label}: ${JSON.stringify(result)}`);
    assert.equal(result.correctChoicesAfterReveal, 1, `${fixture.label}: ${JSON.stringify(result)}`);
  }

  const malformedGlobalFixtures = [
    {
      label: "invalid global containers",
      raw: JSON.stringify({ questions: [], pages: "legacy", attempts: {} }),
    },
    { label: "malformed global JSON", raw: "{broken" },
  ];
  for (const fixture of malformedGlobalFixtures) {
    await navigate(`${origin}/index.html`);
    await evaluate(`localStorage.clear(); localStorage.setItem(${JSON.stringify(ownerStorageKey(LOCAL_OWNER, "sat-learning:v2"))}, ${JSON.stringify(fixture.raw)})`);
    await navigate(practiceUrl);
    const result = await evaluate(`(async () => {
      const errors = [];
      const onError = event => errors.push(String(event.error || event.message || "unknown error"));
      window.addEventListener("error", onError);
      const question = currentQuestion();
      const index = question.choices.findIndex(choice => choice.label !== question.correct_answer);
      document.querySelectorAll(".choice")[index].click();
      document.getElementById("checkCurrentBtn").click();
      await window.__satFixtureFlush();
      const saved = JSON.parse(localStorage.getItem(window.__satFixtureRawKey(GLOBAL_KEY)) || "{}");
      window.removeEventListener("error", onError);
      return {
        submitted: activeState(question).submitted,
        feedback: document.getElementById("feedback").textContent,
        revealDisabled: document.getElementById("revealBtn").disabled,
        errors,
        questionsAreRecord: Boolean(saved.questions) && typeof saved.questions === "object" && !Array.isArray(saved.questions),
        pagesAreRecord: Boolean(saved.pages) && typeof saved.pages === "object" && !Array.isArray(saved.pages),
        attemptsAreArray: Array.isArray(saved.attempts),
        gradingRevision: saved.questions?.[question.question_id]?.gradingRevision,
      };
    })()`);
    assert.deepEqual(result, {
      submitted: true,
      feedback: "回答错误",
      revealDisabled: false,
      errors: [],
      questionsAreRecord: true,
      pagesAreRecord: true,
      attemptsAreArray: true,
      gradingRevision: question.gradingRevision,
    }, `${fixture.label}: ${JSON.stringify(result)}`);
  }

  await navigate(`${origin}/index.html`);
  await evaluate("localStorage.clear()");
  await navigate(practiceUrl);
  await evaluate(`(async () => {
    await bootstrapCloudLearning(window.__satFixtureSession(async () => ({})));
    cloudSession = null;
    if (cloudSaveTimer) clearTimeout(cloudSaveTimer);
    cloudSaveTimer = null;
  })()`);
  const outboxWriteFailure = await evaluate(`(() => {
    const question = currentQuestion();
    const index = question.choices.findIndex(choice => choice.label !== question.correct_answer);
    document.querySelectorAll(".choice")[index].click();
    const original = Storage.prototype.setItem;
    const errors = [];
    const onError = event => errors.push(String(event.error || event.message || "unknown error"));
    window.addEventListener("error", onError);
    Storage.prototype.setItem = function(key, value) {
      if (key === window.__satFixtureRawKey(OUTBOX_KEY)) throw new DOMException("quota", "QuotaExceededError");
      return original.call(this, key, value);
    };
    document.getElementById("checkCurrentBtn").click();
    Storage.prototype.setItem = original;
    window.removeEventListener("error", onError);
    return {
      submitted: activeState(question).submitted,
      feedback: document.getElementById("feedback").textContent,
      revealDisabled: document.getElementById("revealBtn").disabled,
      syncStatus: document.getElementById("syncStatus").textContent,
      volatileOutboxLength: volatileAttemptOutbox.length,
      errors
    };
  })()`);
  assert.deepEqual(outboxWriteFailure, {
    submitted: true,
    feedback: "回答错误",
    revealDisabled: false,
    syncStatus: "作答结果已显示，保存将在稍后重试",
    volatileOutboxLength: 1,
    errors: [],
  });
  const volatileRetry = await evaluate(`(async () => {
    const synced = [];
    cloudSession = window.__satFixtureSession(async (name, payload) => {
        if (name === "saveSatPracticeAttempt") synced.push(payload.attempt.attemptId);
        return {};
    });
    await processAttemptOutbox();
    cloudSession = null;
    return {
      synced: synced.length,
      volatileOutboxLength: volatileAttemptOutbox.length,
      persistedOutboxLength: JSON.parse(localStorage.getItem(window.__satFixtureRawKey(OUTBOX_KEY)) || "[]").length
    };
  })()`);
  assert.deepEqual(volatileRetry, {
    synced: 1,
    volatileOutboxLength: 0,
    persistedOutboxLength: 0,
  });

  await navigate(`${origin}/index.html`);
  await evaluate("localStorage.clear()");
  await navigate(practiceUrl);
  const totalStorageFailure = await evaluate(`(() => {
    const question = currentQuestion();
    const index = question.choices.findIndex(choice => choice.label !== question.correct_answer);
    document.querySelectorAll(".choice")[index].click();
    const original = Storage.prototype.setItem;
    const errors = [];
    const onError = event => errors.push(String(event.error || event.message || "unknown error"));
    window.addEventListener("error", onError);
    Storage.prototype.setItem = function() {
      throw new DOMException("quota", "QuotaExceededError");
    };
    try {
      document.getElementById("checkCurrentBtn").click();
    } finally {
      Storage.prototype.setItem = original;
      window.removeEventListener("error", onError);
    }
    return {
      submitted: activeState(question).submitted,
      feedback: document.getElementById("feedback").textContent,
      revealDisabled: document.getElementById("revealBtn").disabled,
      syncStatus: document.getElementById("syncStatus").textContent,
      volatileOutboxLength: volatileAttemptOutbox.length,
      errors
    };
  })()`);
  assert.deepEqual(totalStorageFailure, {
    submitted: true,
    feedback: "回答错误",
    revealDisabled: false,
    syncStatus: "作答结果已显示，保存将在稍后重试",
    volatileOutboxLength: 1,
    errors: [],
  });

  await navigate(`${origin}/index.html`);
  await evaluate("localStorage.clear()");
  await navigate(practiceUrl);
  let view = await snapshot();
  assert.equal(view.revealDisabled, true);
  assert.equal(view.correctChoices, 0);
  assert.equal(view.explanation, "");

  await evaluate(`(() => { const index=QUESTIONS[0].choices.findIndex(choice=>choice.label!==QUESTIONS[0].correct_answer); document.querySelectorAll(".choice")[index].click(); })()`);
  const afterChoice = await snapshot();
  assert.equal(afterChoice.selectedChoices, 1, `Selecting an answer should update the rendered question: ${JSON.stringify(afterChoice.page)}`);
  await evaluate(`(() => {
    window.__satStimulusBeforeSubmit = document.getElementById("stimulus").firstChild;
    window.__satChoiceBeforeSubmit = document.querySelector(".choice");
  })()`);
  await evaluate('document.getElementById("checkCurrentBtn").click()');
  view = await snapshot();
  assert.equal(view.feedback, "回答错误");
  assert.equal(view.revealDisabled, false);
  assert.equal(view.correctChoices, 0);
  assert.equal(view.incorrectChoices, 0);
  assert.equal(view.explanation, "");
  assert.equal(view.page.answers[question.id].revealed, false);
  assert.equal(await evaluate(`window.__satStimulusBeforeSubmit === document.getElementById("stimulus").firstChild`), true);
  assert.equal(await evaluate(`window.__satChoiceBeforeSubmit === document.querySelector(".choice")`), true);

  await reload();
  view = await snapshot();
  assert.equal(view.feedback, "回答错误");
  assert.equal(view.correctChoices, 0);
  assert.equal(view.explanation, "");

  await evaluate(`window.__satStimulusBeforeReveal = document.getElementById("stimulus").firstChild`);
  await evaluate('document.getElementById("revealBtn").click()');
  view = await snapshot();
  assert.equal(view.correctChoices, 1);
  assert.match(view.explanation, /正确答案/);
  assert.match(view.explanation, new RegExp(`正确答案\\s*${question.answer}`));
  assert.equal(view.page.answers[question.id].revealed, true);
  assert.equal(await evaluate(`window.__satStimulusBeforeReveal === document.getElementById("stimulus").firstChild`), true);

  await evaluate(`window.__satDocumentMarker = { createdAt: Date.now() }`);
  await evaluate('document.getElementById("retryBtn").click()');
  assert.equal(await evaluate(`Boolean(window.__satDocumentMarker)`), true, "Blank redo must not reload the document");
  assert.equal(await evaluate(`new URL(location.href).searchParams.get("redo")`), question.id);
  view = await snapshot();
  assert.equal(view.selectedChoices, 0);
  assert.equal(view.correctChoices, 0);
  assert.equal(view.explanation, "");
  assert.match(await evaluate('document.getElementById("modeBanner").textContent'), /空白重做 · 原来的作答和解析已隐藏/);
  assert.equal(await evaluate('document.getElementById("reflectionPanel").hidden'), true);
  await evaluate(`(() => { const index=QUESTIONS[0].choices.findIndex(choice=>choice.label!==QUESTIONS[0].correct_answer); document.querySelectorAll(".choice")[index].click(); })()`);
  await evaluate('document.getElementById("checkCurrentBtn").click()');
  view = await snapshot();
  assert.equal(view.feedback, "回答错误");
  assert.equal(view.correctChoices, 0);
  await evaluate('document.getElementById("revealBtn").click()');
  const redoAttempt = await evaluate(`JSON.parse(localStorage.getItem(window.__satFixtureRawKey(OUTBOX_KEY))||"[]").findLast(item=>item.mode==="blank_redo")`);
  assert.equal(redoAttempt.revealed, true);
  assert.ok(redoAttempt.revealedAt);

  // The builder's persistent session must be blank even when its original
  // occurrence already has a revealed answer, and refresh must resume only the
  // session-specific attempt. Opening the original remains a separate action.
  const originalAttemptExpression = `(() => {const saved=JSON.parse(learningStorage.getItem(storageKey)).answers[${JSON.stringify(question.id)}];return Object.fromEntries(["selected","submitted","checked","correct","revealed","submittedAt","revealedAt"].map(key=>[key,saved[key]]))})()`;
  const originalBeforeToday = await evaluate(originalAttemptExpression);
  const todayId = "browser-persistent-today-fixture";
  await navigate(`${origin}/index.html`);
  await evaluate(`learningStorage.setItem(SESSION_KEY, JSON.stringify({id:${JSON.stringify(todayId)},createdAt:new Date().toISOString(),sources:["all"],answers:{},questions:[{questionId:${JSON.stringify(question.id)},route:"sets/13-2025-03-apac-a.html"}]}))`);
  const todayUrl = `${practiceUrl}?session=${encodeURIComponent(todayId)}&focus=${encodeURIComponent(question.id)}`;
  await navigate(todayUrl);
  let today = await snapshot();
  assert.equal(today.selectedChoices, 0);
  assert.equal(today.correctChoices, 0);
  assert.equal(today.explanation, "");
  assert.equal(await evaluate('document.getElementById("reflectionPanel").hidden'), true);
  await evaluate(`(() => { const q=currentQuestion(),index=q.choices.findIndex(choice=>choice.label!==q.correct_answer);document.querySelectorAll(".choice")[index].click();document.getElementById("checkCurrentBtn").click(); })()`);
  const savedToday = await evaluate(`JSON.parse(learningStorage.getItem(SESSION_KEY)).answers[${JSON.stringify(question.id)}]`);
  assert.equal(savedToday.submitted, true);
  assert.equal(savedToday.revealed, false);
  assert.equal(savedToday.gradingRevision, question.gradingRevision);
  await evaluate('document.getElementById("starBtn").click()');
  // The shared record is written under a lock, so it lands a moment after the click.
  await waitForEvaluation(`JSON.parse(learningStorage.getItem(GLOBAL_KEY)).questions[${JSON.stringify(question.id)}].starred === true`,
    "the review star saved from a practice session");
  const todayReview = await evaluate(`(() => {
    const record = JSON.parse(learningStorage.getItem(GLOBAL_KEY)).questions[${JSON.stringify(question.id)}];
    const session = JSON.parse(learningStorage.getItem(SESSION_KEY));
    return {wrongListed:Boolean(record.firstWrongAt), starred:record.starred, starShown:!document.getElementById("starBtn").hidden,
      label:document.getElementById("starBtn").textContent, resumeAt:session.lastQuestionId};
  })()`);
  assert.deepEqual(todayReview, {wrongListed:true, starred:true, starShown:true, label:"★ 已加入复习", resumeAt:question.id},
    "A wrong answer in a practice session joins the wrong-answer list, and the review star works there after submitting");
  await reload();
  today = await snapshot();
  assert.equal(today.selectedChoices, 1);
  assert.equal(today.feedback, "回答错误");
  assert.equal(today.correctChoices, 0);
  assert.equal(today.explanation, "");
  await evaluate('document.getElementById("revealBtn").click()');
  assert.equal((await snapshot()).correctChoices, 1);
  await reload();
  assert.equal((await snapshot()).correctChoices, 1, "Today's reveal may resume only after this session's submit/reveal");
  const todayNavigator = await evaluate(`(() => {
    const entries = navigatorEntries(), session = JSON.parse(learningStorage.getItem(SESSION_KEY));
    return {entries:entries.length, session:session.questions.length, inSession:entries.every(entry => entry.inSession),
      wrong:entries.filter(entry => entry.checked && !entry.correct).length,
      listButton:document.getElementById("questionListBtn").hidden, answerSheet:document.getElementById("overviewBtn").hidden};
  })()`);
  assert.equal(todayNavigator.entries, todayNavigator.session, "A practice session lists its own questions");
  assert.deepEqual({inSession:todayNavigator.inSession, wrong:todayNavigator.wrong, listButton:todayNavigator.listButton, answerSheet:todayNavigator.answerSheet},
    {inSession:true, wrong:1, listButton:false, answerSheet:false}, "The question list and answer sheet stay available in a practice session");
  assert.deepEqual(await evaluate(originalAttemptExpression), originalBeforeToday, "Today practice may update shared review history but must not overwrite the original occurrence's attempt");
  await navigate(`${origin}/index.html#home`);
  const resume = await evaluate(`(() => { const link = document.querySelector(".hero-actions a.primary"); return {label:link.textContent, href:link.getAttribute("href")}; })()`);
  assert.equal(resume.label, "回看上次练习");
  assert.ok(resume.href.includes(`session=${encodeURIComponent(todayId)}`) && resume.href.includes(`focus=${encodeURIComponent(question.id)}`),
    `Continue must return to the generated practice session, not to the question's set: ${resume.href}`);
  await navigate(`${practiceUrl}?focus=${encodeURIComponent(question.id)}`);
  assert.equal(await evaluate('Boolean(practiceSession) || redoMode'), false, "Open original must not reuse today's blank-redo mode");

  await navigate(`${origin}/index.html`);
  await evaluate("localStorage.clear()");
  await navigate(practiceUrl);
  await evaluate(`(() => {const q=QUESTIONS[current],index=q.choices.findIndex(choice=>choice.label!==q.correct_answer);document.querySelectorAll(".choice")[index].click()})()`);
  await evaluate('document.getElementById("nextBtn").click()');
  await evaluate(`(() => {const q=QUESTIONS[current],index=q.choices.findIndex(choice=>choice.label!==q.correct_answer);document.querySelectorAll(".choice")[index].click()})()`);
  const timerRunning = await evaluate(`(() => {
    const box = document.getElementById("questionTimer"), toggle = document.getElementById("questionTimerToggle");
    const running = {hidden:box.hidden, clock:/^00:0\\d$/.test(document.getElementById("questionTimerValue").textContent), toggle:toggle.textContent, counting:Boolean(timerStartedAt)};
    toggle.click();
    const paused = {paused:box.classList.contains("paused"), toggle:toggle.textContent, counting:Boolean(timerStartedAt)};
    toggle.click();
    readQuestionTimes()[timerKey] = {ms:65000, at:""};
    return {running, paused, resumed:Boolean(timerStartedAt), hint:document.getElementById("feedback").textContent, saved:document.getElementById("syncStatus").textContent};
  })()`);
  assert.deepEqual(timerRunning, {running:{hidden:false, clock:true, toggle:"暂停", counting:true}, paused:{paused:true, toggle:"继续", counting:false}, resumed:true, hint:"", saved:""},
    "The per-question timer runs, pauses and resumes; no filler text sits under the choices or in the footer");
  await evaluate('document.getElementById("checkAllBtn").click()');
  assert.deepEqual(await evaluate(`({clock:document.getElementById("questionTimerValue").textContent, toggleHidden:document.getElementById("questionTimerToggle").hidden, counting:Boolean(timerStartedAt)})`),
    {clock:"用时 01:05", toggleHidden:true, counting:false}, "Submitting freezes the question's timer");
  const batch = await evaluate(`(() => { const page=JSON.parse(localStorage.getItem(window.__satFixtureRawKey(storageKey))); return {states:QUESTIONS.slice(0,2).map(q=>page.answers[q.question_id]),feedback:document.getElementById("feedback").textContent,correct:document.querySelectorAll(".choice.correct").length,explanation:document.getElementById("explanation").textContent.trim(),items:reviewItems()}; })()`);
  assert.ok(batch.states.every(item => item.submitted && !item.revealed), JSON.stringify(batch.states));
  assert.equal(batch.feedback, "回答错误");
  assert.equal(batch.correct, 0);
  assert.equal(batch.explanation, "");
  assert.ok(batch.items.every(item => !item.correct_answer && !item.explanation && !item.answer_source && Object.keys(item.choice_analysis).length === 0));

  await evaluate('document.getElementById("resetBtn").click()');
  assert.deepEqual(await evaluate(`({hidden:document.getElementById("resetConfirm").hidden, focused:document.activeElement.id, role:document.getElementById("resetConfirm").getAttribute("role")})`), { hidden: false, focused: "resetConfirmCancel", role: "alertdialog" });
  await evaluate('document.getElementById("resetConfirmCancel").click()');
  assert.equal(await evaluate('document.getElementById("resetConfirm").hidden'), true);
  assert.equal((await snapshot()).page.answers[question.id].submitted, true, "Cancelling reset must preserve answers");
  await evaluate('document.getElementById("resetBtn").click(); document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape", bubbles:true}))');
  assert.equal(await evaluate('document.getElementById("resetConfirm").hidden'), true, "Escape must cancel reset");
  await evaluate('document.getElementById("resetBtn").click(); document.getElementById("resetConfirmApply").click()');
  const afterReset = await snapshot();
  assert.ok(afterReset.page.resetAt);
  assert.equal(Object.values(afterReset.page.answers).some(item => item.submitted), false);
  assert.ok(Object.values(afterReset.global.questions).some(item => item.firstWrongAt));

  await evaluate(`(() => { const q=QUESTIONS[current],index=q.choices.findIndex(choice=>choice.label===q.correct_answer);document.querySelectorAll(".choice")[index].click();document.getElementById("checkCurrentBtn").click(); })()`);
  assert.equal(await evaluate(`document.getElementById("feedback").textContent`), "回答正确");
  assert.equal(await evaluate(`document.getElementById("retryBtn").hidden`), false, "Correct submissions must also allow blank redo");
  await evaluate(`document.getElementById("retryBtn").click()`);
  assert.equal(await evaluate(`document.querySelectorAll(".choice.selected").length`), 0);
  assert.equal(await evaluate(`document.getElementById("reflectionPanel").hidden`), true);

  await navigate(`${practiceUrl}?redo=missing_question_id`);
  assert.match(await evaluate(`document.getElementById("modeBanner").textContent`), /无法定位重做题目/);
  assert.equal(await evaluate(`document.getElementById("reflectionPanel").hidden`), false);

  await navigate(`${origin}/index.html`);
  const dashboard = await evaluate(`({unique:MANIFEST.uniqueQuestionCount,pages:MANIFEST.pageCount,skills:new Set(MANIFEST.questions.map(q=>q.skill)).size,body:document.getElementById("app").textContent})`);
  assert.equal(dashboard.unique, expectedUniqueCount);
  assert.equal(dashboard.pages, expectedPageCount);
  assert.equal(dashboard.skills, expectedSkillCount);
  assert.match(dashboard.body, /周学习报告/);
  assert.match(dashboard.body, /最近练习/);

  const legacyRaw = JSON.stringify({questions:{[question.id]:{selected:question.answer,submitted:true,revealed:true,correct:true,gradingRevision:question.gradingRevision,note:"unassigned legacy sentinel"}},pages:{},attempts:[]});
  await evaluate(`localStorage.clear();localStorage.setItem("sat-learning:v2",${JSON.stringify(legacyRaw)})`);
  await navigate(practiceUrl);
  assert.deepEqual(await evaluate(`({selected:activeState(currentQuestion()).selected,submitted:activeState(currentQuestion()).submitted,revealed:activeState(currentQuestion()).revealed,noticeHidden:document.getElementById("satLegacyBackupNotice").hidden,raw:localStorage.getItem("sat-learning:v2")})`), {
    selected:"",submitted:false,revealed:false,noticeHidden:false,raw:legacyRaw,
  }, "Unowned legacy history must remain available for explicit local backup without being claimed by anonymous/current owner");
  await navigate(`${origin}/index.html`);

  const zenAssets = await evaluate(`Promise.all(["background.svg", "texture.svg", "pattern.svg"].map(name => fetch("assets/zen-interface/" + name).then(response => response.ok)))`);
  assert.ok(zenAssets.every(Boolean), JSON.stringify(zenAssets));
  const categoryLibrary = await evaluate(`(() => {
    document.querySelector('[data-resource-kind="category"]').click();
    const before = {selected:document.querySelector('[data-resource-kind="category"]').getAttribute("aria-selected"), expanded:document.getElementById("resourceExpand").getAttribute("aria-expanded"), cards:document.querySelectorAll("#resourceLibrary .resource-card").length, first:document.querySelector("#resourceLibrary .resource-card h3")?.textContent, firstEnglish:document.querySelector("#resourceLibrary .resource-card-subtitle")?.textContent, translated:document.querySelectorAll("#resourceLibrary .resource-card-subtitle").length};
    document.getElementById("resourceExpand").click();
    const collapsed = {expanded:document.getElementById("resourceExpand").getAttribute("aria-expanded"), cards:document.querySelectorAll("#resourceLibrary .resource-card").length};
    document.querySelector('[data-resource-kind="set"]').click();
    const sets = {expanded:document.getElementById("resourceExpand").getAttribute("aria-expanded"), cards:document.querySelectorAll("#resourceLibrary .resource-card").length};
    return {...before, collapsed, sets};
  })()`);
  assert.deepEqual(categoryLibrary, { selected: "true", expanded: "true", cards: 11, first: "句子边界", firstEnglish: "Boundaries", translated: 11, collapsed: { expanded: "false", cards: 6 }, sets: { expanded: "false", cards: 6 } });

  // Mock exams live on their own page; the home page only links to it.
  await evaluate(`location.hash = "#exam"`);
  await new Promise(resolve => setTimeout(resolve, 20));
  const examCenter = await evaluate(`(() => ({
    heading: document.querySelector("h1")?.textContent,
    rows: document.querySelectorAll(".exam-list .exam-row").length,
    titles: [...document.querySelectorAll(".exam-list .exam-row h3")].map(node => node.textContent.trim()),
    onHome: document.querySelectorAll("#examLibrary").length,
  }))()`);
  assert.deepEqual(examCenter, {
    heading: "模考中心",
    rows: manifestSetPages.filter(page => page.examEligible === true).length,
    titles: manifestSetPages.filter(page => page.examEligible === true).map(page => page.title),
    onHome: 0,
  }, "the mock exam centre must list exactly the exam-eligible sets");

  const routeViews = {};
  for (const [hash, heading] of [["#review/wrong", "我的复习"], ["#diagnosis", "学习诊断"], ["#practice", "生成今日练习"], ["#search", "全库搜索"]]) {
    await evaluate(`location.hash = ${JSON.stringify(hash)}`);
    await new Promise(resolve => setTimeout(resolve, 20));
    routeViews[hash] = await evaluate(`document.querySelector("h1")?.textContent === ${JSON.stringify(heading)}`);
    if (hash === "#practice") {
      const basis = await evaluate(`(() => ({hidden:document.getElementById("builderBasisGroup").hidden,
        options:[...document.querySelectorAll("#builderBases input")].map(input => input.value),
        withBasis:MANIFEST.questions.some(question => question.difficultyBasis && question.difficultyLevel)}))()`);
      assert.deepEqual(basis.options, ["stated", "estimated"]);
      const classified = await evaluate(`(() => {
        const values = id => [...document.querySelectorAll("#" + id + " input")].map(input => input.value);
        const taxonomy = MANIFEST.taxonomy, before = {tasksHidden:document.getElementById("builderTaskGroup").hidden};
        const skill = document.querySelector("#builderSkills input");
        if (skill) skill.click();
        return {before, passages:values("builderGenres"), importance:values("builderImportance"),
          importanceHidden:document.getElementById("builderImportanceGroup").hidden,
          tasks:values("builderTasks"), tasksHidden:document.getElementById("builderTaskGroup").hidden,
          expectedTasks:skill ? taxonomy.tasks.filter(item => item.skill === skill.value).map(item => item.id) : [],
          expectedPassages:taxonomy.passageTypes.map(item => item.id), expectedImportance:taxonomy.importance.map(item => item.id),
          unclassified:MANIFEST.questions.filter(item => !item.task || !item.passageType || !item.importance).length};
      })()`);
      assert.equal(classified.before.tasksHidden, true, "Tasks appear only after a skill is chosen");
      if (classified.expectedPassages.length) {
        assert.equal(classified.unclassified, 0, "Every question carries a task, a passage type and an importance level");
        assert.deepEqual(classified.passages, classified.expectedPassages);
        assert.deepEqual({importance:classified.importance, hidden:classified.importanceHidden}, {importance:classified.expectedImportance, hidden:false});
        assert.deepEqual({tasks:classified.tasks, hidden:classified.tasksHidden}, {tasks:classified.expectedTasks, hidden:false});
      }
      assert.equal(basis.hidden, !basis.withBasis, "The difficulty-basis filter shows only when the data says where a difficulty comes from");
    }
  }
  await evaluate(`(() => {
    const search = document.querySelector('input[type="search"]');
    search.value = "inference";
    search.dispatchEvent(new Event("input", {bubbles:true}));
  })()`);
  await waitForEvaluation('document.querySelectorAll("#searchResults .review-card").length > 0', "debounced search results");
  routeViews.searchResults = await evaluate('document.querySelectorAll("#searchResults .review-card").length');
  assert.ok(Object.values(routeViews).slice(0, 4).every(Boolean), JSON.stringify(routeViews));
  assert.ok(routeViews.searchResults > 0, JSON.stringify(routeViews));

  // Word explanations and the vocabulary notebook, on a page whose first question has explained words.
  await navigate(`${origin}/index.html`);
  await evaluate("localStorage.clear()");
  await navigate(`${origin}/sets/15-2025-10-apac-b.html`);
  const glossStart = await evaluate(`(() => {
    const question = currentQuestion(), links = question.vocabulary || [];
    const marked = [...document.querySelectorAll("#stimulus .gloss")].map(node => node.textContent);
    return {
      passage: links.filter(link => link.field === "passage").map(link => link.surface).sort().join(),
      marked: [...new Set(marked)].sort().join(),
      choices: links.filter(link => link.field === "choices").length,
      inChoices: document.querySelectorAll("#choices .gloss").length,
      panelHidden: document.getElementById("glossPanel").hidden,
      popoverHidden: document.getElementById("glossPopover").hidden,
      text: document.getElementById("stimulus").textContent === (() => { const copy = document.getElementById("stimulus").cloneNode(true); copy.querySelectorAll(".gloss").forEach(node => node.replaceWith(node.textContent)); return copy.textContent; })(),
    };
  })()`);
  assert.ok(glossStart.passage && glossStart.choices > 0, JSON.stringify(glossStart));
  assert.deepEqual({marked:glossStart.marked, inChoices:glossStart.inChoices, panelHidden:glossStart.panelHidden, popoverHidden:glossStart.popoverHidden, text:glossStart.text},
    {marked:glossStart.passage, inChoices:0, panelHidden:true, popoverHidden:true, text:true},
    "Before submitting, the passage's hard words can be opened and nothing about the answer choices is shown");
  const glossCard = await evaluate(`(() => {
    const anchor = document.querySelector("#stimulus .gloss"), entry = PAGE_VOCABULARY[anchor.dataset.gloss];
    anchor.click();
    const card = document.getElementById("glossPopover"), layers = card.querySelectorAll("details.gloss-layer");
    const first = {open:!card.hidden, expanded:anchor.getAttribute("aria-expanded"), word:card.querySelector(".gloss-word").textContent === entry.word,
      english:card.querySelector(".gloss-english").textContent === entry.english, example:layers[0].open && layers[0].textContent.includes(entry.example),
      chineseFolded:!layers[1].open && layers[1].querySelector("summary").textContent === "中文解释", chinese:layers[1].textContent.includes(entry.chinese) && layers[1].textContent.includes(entry.exampleChinese),
      inView:card.getBoundingClientRect().top >= 0 && card.getBoundingClientRect().bottom <= innerHeight};
    card.querySelector("[data-gloss-save]").click();
    const saved = readSatVocabulary(learningStorage);
    const afterSave = {count:saved.length, word:saved[0]?.word === entry.word, english:saved[0]?.english === entry.english, question:saved[0]?.questionId === currentQuestion().question_id,
      context:Boolean(saved[0]?.context), marked:anchor.classList.contains("is-saved"), button:card.querySelector("[data-gloss-save]").getAttribute("aria-pressed")};
    document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape", bubbles:true}));
    return {first, afterSave, closed:card.hidden, focusBack:document.activeElement === anchor};
  })()`);
  assert.deepEqual(glossCard.first, {open:true, expanded:"true", word:true, english:true, example:true, chineseFolded:true, chinese:true, inView:true},
    "A word opens with its English definition and example; the Chinese layer is there but folded");
  assert.deepEqual(glossCard.afterSave, {count:1, word:true, english:true, question:true, context:true, marked:true, button:"true"}, "The card adds its word to the notebook");
  assert.deepEqual({closed:glossCard.closed, focusBack:glossCard.focusBack}, {closed:true, focusBack:true}, "Escape closes the card and returns to the word");
  await evaluate(`(() => { const q = currentQuestion(), index = q.choices.findIndex(choice => choice.label === q.correct_answer); document.querySelectorAll(".choice")[index].click(); document.getElementById("checkCurrentBtn").click(); })()`);
  const glossPanel = await evaluate(`(() => {
    const panel = document.getElementById("glossPanel"), rows = [...panel.querySelectorAll(".gloss-row")];
    const links = currentQuestion().vocabulary || [], ids = new Set(links.map(link => link.id));
    const choiceRow = rows.find(row => row.querySelector(".gloss-tag"));
    choiceRow.open = true;
    choiceRow.querySelector("[data-gloss-save]").click();
    return {hidden:panel.hidden, rows:rows.length, expected:ids.size, tagged:rows.filter(row => row.querySelector(".gloss-tag")).length > 0,
      rowHasLayers:choiceRow.querySelectorAll("details.gloss-layer").length, saved:readSatVocabulary(learningStorage).length,
      savedShown:panel.querySelectorAll('[data-gloss-save][aria-pressed="true"]').length};
  })()`);
  assert.deepEqual(glossPanel, {hidden:false, rows:glossPanel.expected, expected:glossPanel.expected, tagged:true, rowHasLayers:2, saved:2, savedShown:2},
    "After submitting, every explained word of the question is listed, the choice words included, and can be added to the notebook");
  // On a wide screen the bottom bar must sit below the panes, not on top of their last lines.
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }, sessionId);
  const paneEnds = await evaluate(`(() => {
    document.querySelector("#glossPanel .gloss-list").open = true;
    if (!document.getElementById("explanation").classList.contains("visible")) document.getElementById("revealBtn").click();
    const bar = document.getElementById("practiceNav"), top = bar.getBoundingClientRect().top;
    const panes = [...document.querySelectorAll("#practiceLayout > .pane, #practiceLayout > .question-list:not([hidden])")];
    const covered = panes.map(pane => {
      pane.scrollTop = pane.scrollHeight;
      const last = [...pane.children].filter(child => child.offsetParent !== null).pop();
      return Math.max(0, Math.round(Math.max(pane.getBoundingClientRect().bottom, last.getBoundingClientRect().bottom) - top));
    });
    return {panes:panes.length >= 2, scrolls:panes.some(pane => pane.scrollHeight > pane.clientHeight), covered:covered.join(), barInFlow:getComputedStyle(bar).position === "static",
      barAtBottom:Math.round(bar.getBoundingClientRect().bottom) === innerHeight, pageScrolls:document.documentElement.scrollHeight > innerHeight};
  })()`);
  await cdp.send("Emulation.clearDeviceMetricsOverride", {}, sessionId);
  assert.deepEqual(paneEnds, {panes:true, scrolls:true, covered:paneEnds.covered.split(",").map(() => "0").join(), barInFlow:true, barAtBottom:true, pageScrolls:false},
    "Scrolled to the end, no pane has anything left under the bottom bar");
  await evaluate(`(() => {
    const prompt = document.getElementById("prompt"), range = document.createRange(), node = prompt.firstChild;
    const word = /[A-Za-z]{5,}/.exec(node.nodeValue);
    range.setStart(node, word.index); range.setEnd(node, word.index + word[0].length);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    document.getElementById("vocabularyBtn").click();
  })()`);
  const ownWord = await evaluate(`(() => { const words = readSatVocabulary(learningStorage); return {count:words.length, custom:words.filter(item => item.custom).length, flashed:document.getElementById("vocabularyBtn").textContent.startsWith("已加入")}; })()`);
  assert.deepEqual(ownWord, {count:3, custom:1, flashed:true}, "Any selected word can be added to the notebook");
  // A saved copy that has gone stale: the notebook must show the list's current wording instead.
  await evaluate(`(() => { const word = readSatVocabulary(learningStorage).find(item => item.vocabId && item.questionId); updateSatVocabularyWord(learningStorage, word.word, {english:"stale wording"}); })()`);
  await navigate(`${origin}/index.html#vocabulary`);
  const notebook = await evaluate(`(() => {
    const cards = [...document.querySelectorAll(".vocab-card")], explained = cards.find(card => card.querySelector("details.vocab-layer"));
    const active = document.querySelector(".desktop-nav a.active")?.textContent;
    const layers = explained.querySelectorAll("details.vocab-layer");
    const stale = cards.map(card => card.querySelector(".vocab-english")?.textContent).filter(text => text === "stale wording").length;
    const current = cards.filter(card => MANIFEST.vocabulary.some(item => item.english === card.querySelector(".vocab-english")?.textContent)).length;
    const shape = {cards:cards.length, active, folded:cards.every(card => !card.open), example:layers[0].open, chineseFolded:!layers[1].open, stale, current,
      back:[...cards].some(card => card.querySelector(".vocab-context a")?.getAttribute("href").includes("focus=")), meta:document.querySelector(".section-meta").textContent};
    explained.querySelector("[data-vocab-mastered]").click();
    return shape;
  })()`);
  assert.deepEqual(notebook, {cards:3, active:"词汇", folded:true, example:true, chineseFolded:true, stale:0, current:2, back:true, meta:"3 个词 · 已掌握 0"},
    "The notebook lists the saved words folded, English and example first, in the word list's current wording, with a way back to the question");
  const notebookAfter = await evaluate(`(() => {
    const learning = document.querySelectorAll(".vocab-card").length, meta = document.querySelector(".section-meta").textContent;
    document.getElementById("vocabAddInput").value = "ephemeral";
    document.getElementById("vocabAddForm").requestSubmit();
    const added = document.querySelectorAll(".vocab-card").length;
    document.getElementById("vocabAddInput").value = "12345";
    document.getElementById("vocabAddForm").requestSubmit();
    const refused = document.getElementById("vocabStatus").textContent;
    document.querySelector('[data-vocab-remove="ephemeral"]').click();
    return {learning, meta, added, refused:refused.startsWith("请输入"), afterRemove:document.querySelectorAll(".vocab-card").length, stored:readSatVocabulary(learningStorage).length};
  })()`);
  assert.deepEqual(notebookAfter, {learning:2, meta:"3 个词 · 已掌握 1", added:3, refused:true, afterRemove:2, stored:3},
    "Words can be marked as mastered, added by hand and removed");
  await evaluate(`document.querySelector('[data-vocab-view="list"]').click()`);
  await waitForEvaluation('location.hash === "#vocabulary/list" && Boolean(document.getElementById("glossaryList"))', "the word list");
  const glossary = await evaluate(`(() => {
    const pick = (id, value) => { const control = document.getElementById(id); control.value = value; control.dispatchEvent(new Event(control.tagName === "INPUT" ? "input" : "change", {bubbles:true})); };
    const words = () => [...document.querySelectorAll("#glossaryList .vocab-card")].map(card => MANIFEST.vocabulary.find(item => item.id === card.dataset.glossary));
    const sorted = (rows, key, direction) => rows.every((row, index) => index === 0 || direction * (rows[index - 1][key] - row[key]) >= 0);
    const result = {total:MANIFEST.vocabulary.length, shown:words().length, moreShown:!document.getElementById("glossaryMoreRow").hidden,
      byQuestions:sorted(words(), "questions", 1), counts:MANIFEST.vocabulary.every(item => item.questions >= 1 && item.realExamQuestions <= item.questions && item.questions <= item.occurrences)};
    document.getElementById("glossaryMore").click();
    result.afterMore = words().length;
    pick("glossarySort", "real"); result.byReal = sorted(words(), "realExamQuestions", 1);
    pick("glossarySort", "rare"); result.byRare = sorted(words(), "questions", -1);
    pick("glossarySort", "alpha"); result.byAlpha = words().every((row, index, rows) => index === 0 || rows[index - 1].word.localeCompare(row.word, "en", {sensitivity:"base"}) <= 0);
    pick("glossaryPart", "adv."); result.adverbs = words().length > 0 && words().every(row => row.partOfSpeech.split("/").includes("adv."));
    pick("glossaryPart", "all");
    const target = MANIFEST.vocabulary.find(item => !hasSatVocabularyWord(learningStorage, item.word));
    pick("glossaryQuery", target.word);
    const card = document.querySelector('#glossaryList .vocab-card[data-glossary="' + target.id + '"]'), layers = card.querySelectorAll("details.vocab-layer");
    result.found = Boolean(card); result.folded = !card.open; result.example = layers[0].open; result.chineseFolded = !layers[1].open;
    result.usage = card.querySelector(".vocab-usage").textContent.includes(String(target.questions));
    const before = readSatVocabulary(learningStorage).length;
    card.querySelector("[data-glossary-save]").click();
    result.added = readSatVocabulary(learningStorage).length - before;
    pick("glossarySaved", "saved"); pick("glossaryQuery", "");
    result.savedOnly = words().length === readSatVocabulary(learningStorage).filter(item => !item.custom).length;
    document.querySelector('#glossaryList [data-glossary-save="' + target.id + '"]').click();
    result.removed = before - readSatVocabulary(learningStorage).length;
    pick("glossarySaved", "all");
    return result;
  })()`);
  assert.ok(glossary.total > 1000, `The word list holds every explained word: ${glossary.total}`);
  assert.deepEqual({...glossary, total:0}, {total:0, shown:60, moreShown:true, byQuestions:true, counts:true, afterMore:120, byReal:true, byRare:true, byAlpha:true, adverbs:true,
    found:true, folded:true, example:true, chineseFolded:true, usage:true, added:1, savedOnly:true, removed:0},
    "The word list shows every explained word, sorts by how often it occurs, by real-exam use and by letter, filters, searches, and feeds the notebook");

  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: false }, sessionId);
  await navigate(`${origin}/index.html#home`);
  const mobile = await evaluate(`(() => {
    const button = document.getElementById("mobileMenuBtn"), nav = document.getElementById("mobileNav");
    const before = {overflow:document.documentElement.scrollWidth > innerWidth, desktopNav:getComputedStyle(document.querySelector(".desktop-nav")).display, menu:getComputedStyle(button).display, hidden:nav.hidden, expanded:button.getAttribute("aria-expanded")};
    button.click();
    const open = {hidden:nav.hidden, expanded:button.getAttribute("aria-expanded"), classOpen:nav.classList.contains("open")};
    document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape", bubbles:true}));
    const closed = {hidden:nav.hidden, expanded:button.getAttribute("aria-expanded"), classOpen:nav.classList.contains("open")};
    const cta = [...document.querySelectorAll("a, button")].find(element => element.textContent.includes("生成今日练习"));
    document.querySelector('[data-resource-kind="category"]').click();
    const category = {cards:document.querySelectorAll("#resourceLibrary .resource-card").length, translated:document.querySelectorAll("#resourceLibrary .resource-card-subtitle").length, overflow:document.documentElement.scrollWidth > innerWidth, clipped:[...document.querySelectorAll("#resourceLibrary .resource-card")].some(card => card.scrollWidth > card.clientWidth)};
    return {before, open, closed, category, ctaVisible:cta.getBoundingClientRect().top < innerHeight};
  })()`);
  assert.deepEqual(mobile.before, { overflow: false, desktopNav: "none", menu: "block", hidden: true, expanded: "false" });
  assert.deepEqual(mobile.open, { hidden: false, expanded: "true", classOpen: true });
  assert.deepEqual(mobile.closed, { hidden: true, expanded: "false", classOpen: false });
  assert.deepEqual(mobile.category, { cards: 11, translated: 11, overflow: false, clipped: false });
  assert.equal(mobile.ctaVisible, true);

  if (options.report) {
    const reportPath = path.resolve(process.cwd(), options.report);
    await mkdir(path.dirname(reportPath), { recursive: true });
    await writeFile(reportPath, `${JSON.stringify({
      schemaVersion: "sat-development-browser-v1",
      scope: "local development; synthetic account and sync fixtures",
      status: "PASS",
      set_routes: manifestSetPages.length,
      category_routes: expectedSkillCount,
      page_routes: expectedPageCount,
      unique_questions: expectedUniqueCount,
      canonical_chart_assets: canonicalChartReferences.size,
      browser_loaded_chart_urls: loadedChartReferences.size,
      checks: [
        "two_step_feedback",
        "persistence",
        "blank_redo",
        "owner_scoped_local_fixtures",
        "unowned_legacy_not_claimed",
        "today_session_isolation_and_refresh",
        "delayed_cloud_race_protection",
        "storage_recovery",
        "batch_submit",
        "export_gating",
        "reset_history",
        "dashboard",
        "all_manifest_routes",
        "search",
        "chart_assets",
        "mobile_menu",
        "word_explanations_and_notebook",
        "word_list_sorting",
        "bottom_bar_clear_of_panes",
      ],
    }, null, 2)}\n`, "utf8");
  }

  console.log(`SAT learning browser verification passed: ${manifestSetPages.length} set routes, ${canonicalChartReferences.size} canonical chart assets, and ${loadedChartReferences.size} browser-loaded chart URLs reachable; ${expectedUniqueCount} unique questions across ${expectedPageCount} pages; two-step feedback, persistence, blank redo, delayed-cloud race protection, DOM-stable choice selection, malformed outbox/global-state recovery, total storage failure handling, batch submit, export gating, reset history, Zen dashboard, routes, search, assets, and mobile menu.`);
} finally {
  cdp?.socket.close();
  const chromeExited = chrome.exitCode !== null
    ? Promise.resolve()
    : new Promise(resolve => chrome.once("exit", resolve));
  chrome.kill("SIGTERM");
  server.close();
  await Promise.race([chromeExited, new Promise(resolve => setTimeout(resolve, 1500))]);
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
