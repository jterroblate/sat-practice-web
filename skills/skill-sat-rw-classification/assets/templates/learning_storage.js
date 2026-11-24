/* Shared inline runtime: maintained once, embedded by the deterministic builder. */
// These are the union of the partial page's legacy IDs and canonical alias
// targets. Cloud overview may already have merged their provenance, so a
// different/missing pageId is not sufficient evidence to trust the aggregate.
const SAT_UNVERIFIED_CLOUD_PARTIAL_PAGE = "sets/06-2025-06-apac-a-partial";
const SAT_UNVERIFIED_CLOUD_PARTIAL_IDS = new Set(Array.from({ length: 15 }, (_, index) =>
  "jun2025_apac_a_" + String(index + 1).padStart(2, "0")));
function isUnverifiedSatCloudPage(pageId) {
  return typeof pageId === "string" && pageId.replace(/^\/?sat-practice\//, "")
    .replace(/^\/+/, "").replace(/\.html$/, "") === SAT_UNVERIFIED_CLOUD_PARTIAL_PAGE;
}
function canMergeSatCloudQuestion(incoming) {
  return Boolean(incoming && typeof incoming.questionId === "string" && incoming.questionId
    && !isUnverifiedSatCloudPage(incoming.pageId)
    && !SAT_UNVERIFIED_CLOUD_PARTIAL_IDS.has(incoming.questionId));
}
function canUploadSatCloudAttempt(attempt) {
  return Boolean(attempt && typeof attempt.pageId === "string"
    && /^(sets|collections|categories)\/[a-z0-9][a-z0-9-]*(?:\.html)?$/.test(attempt.pageId)
    && !isUnverifiedSatCloudPage(attempt.pageId));
}
function isExactSatLegacyBackupKey(key) {
  return typeof key === "string" && key.length <= 100000 && (
    ["sat-learning:v2", "sat-learning:attempt-outbox:v2", "sat-learning:today-session:v2"].includes(key)
    || /^sat-practice:(reading_writing|math):[A-Za-z0-9._:|/-]+$/.test(key)
    || /^sat-exam:v1:(sets|collections|categories)\/[a-z0-9][a-z0-9-]*$/.test(key));
}
function installSatLegacyBackupNotice(store, knownKeys, { pageOnly = false } = {}) {
  const notice = document.getElementById("satLegacyBackupNotice");
  const summary = document.getElementById("satLegacyBackupSummary");
  const action = document.getElementById("satLegacyBackupDownload");
  const status = document.getElementById("satLegacyBackupStatus");
  if (!notice || !summary || !action || !status) return () => {};
  function refresh() {
    notice.hidden = true;
    if (!store.ready()) return;
    try {
      const keys = store.findLegacyKeys(knownKeys());
      if (!keys.length) return;
      summary.textContent = `检测到 ${keys.length} 项未归属的旧 SAT 本机记录`;
      status.textContent = pageOnly ? "本页备份范围：当前页、已知旧题号迁移及统一记录；首页可备份全部已登记 SAT 页面。" : "备份仅包含本项目已登记的旧键和值，不包含其他项目或账号认证数据。";
      action.disabled = false;
      notice.hidden = false;
      if (location.hash === "#legacy-backup") notice.scrollIntoView({ block: "nearest" });
    } catch {
      summary.textContent = "旧 SAT 本机记录暂时无法检查";
      status.textContent = "原数据未删除或合并。请检查浏览器存储权限后重试。";
      action.disabled = true;
      notice.hidden = false;
    }
  }
  action.addEventListener("click", () => {
    try {
      const token = store.token();
      const payload = store.collectLegacyBackup(knownKeys());
      if (!payload.records.length) { refresh(); return; }
      if (!store.isCurrent(token)) throw new Error("Learning owner changed before local backup");
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "sat-legacy-local-backup-" + new Date().toISOString().slice(0, 10) + ".json";
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.textContent = "已发起本地备份下载；请确认文件已保存。原记录仍保留在本机，未上传、未合并到当前账号。";
    } catch {
      status.textContent = "备份未完成；原记录仍保留，未上传或合并。请检查浏览器下载和存储权限。";
    }
  });
  return refresh;
}
function createSatLearningStore() {
  let backing = null;
  try { backing = window.localStorage; } catch {}
  const guarded = typeof window.satCloudbaseReleaseGate === "function"
    || document.documentElement.classList.contains("sat-cloudbase-auth-pending")
    || (location.protocol === "https:" && location.pathname.includes("/sat-practice/"));
  let owner = guarded ? "" : "anonymous-local";
  let generation = 0;
  const prefix = () => "sat-owner:v1:" + encodeURIComponent(owner) + ":";
  const requireOwner = () => { if (!owner || !backing) throw new Error("Learning storage is not available for a verified owner"); };
  // Told before the owner changes, while the old owner's storage is still the current one.
  // settle is true when the old owner was verified until now (a switch, a sign-out) and its
  // pending writes may go to its own records; false when its identity just failed a check,
  // and anything pending must be dropped rather than written anywhere.
  const ownerListeners = [];
  const announce = settle => {
    if (!owner) return;
    const token = owner + ":" + generation;
    for (const listener of ownerListeners) { try { listener({ token, settle }); } catch {} }
  };
  return {
    ready: () => Boolean(owner),
    token: () => owner + ":" + generation,
    isCurrent: token => Boolean(owner) && token === owner + ":" + generation,
    onOwnerChange(listener) { if (typeof listener === "function") ownerListeners.push(listener); },
    bind(session) {
      const profileId = typeof session?.profile?.userId === "string" ? session.profile.userId : "";
      const userId = typeof session?.user?.uid === "string" ? session.user.uid : "";
      if (!profileId || profileId !== profileId.trim() || profileId.length > 256 || /[\u0000-\u001f]/.test(profileId) || (userId && userId !== profileId)) {
        announce(true);
        owner = ""; generation += 1;
        throw new Error("Learning owner identity is missing or inconsistent");
      }
      const verifiedOwner = "user:" + profileId;
      const changed = owner !== verifiedOwner;
      if (changed) { announce(true); owner = verifiedOwner; generation += 1; }
      return changed;
    },
    getItem(key) { return owner && backing ? backing.getItem(prefix() + key) : null; },
    setItem(key, value) { requireOwner(); backing.setItem(prefix() + key, value); },
    removeItem(key) { requireOwner(); backing.removeItem(prefix() + key); },
    key(index) {
      if (!owner || !backing) return null;
      const keys = [];
      for (let i = 0; i < backing.length; i += 1) {
        const key = backing.key(i);
        if (key?.startsWith(prefix())) keys.push(key.slice(prefix().length));
      }
      return keys[index] || null;
    },
    get length() {
      if (!owner || !backing) return 0;
      let count = 0;
      for (let i = 0; i < backing.length; i += 1) if (backing.key(i)?.startsWith(prefix())) count += 1;
      return count;
    },
    eventKey(event) { return owner && event.key?.startsWith(prefix()) ? event.key.slice(prefix().length) : null; },
    findLegacyKeys(knownKeys) {
      if (!owner || !backing) return [];
      // Deliberately do not enumerate all origin storage or parse legacy values.
      return [...new Set(knownKeys)].filter(isExactSatLegacyBackupKey)
        .filter(key => backing.getItem(key) !== null);
    },
    collectLegacyBackup(knownKeys) {
      requireOwner();
      const records = this.findLegacyKeys(knownKeys).map(key => ({ key, value: backing.getItem(key) }))
        .filter(record => record.value !== null);
      return { schema: "sat-unowned-local-backup-v1", ownership: "unassigned-legacy",
        generatedAt: new Date().toISOString(), imported: false, uploaded: false, records };
    },
    async callOwned(session, name, data, token = this.token()) {
      if (!this.isCurrent(token)) throw new Error("Learning owner changed before request");
      if (typeof session?.auth?.getCurrentUser === "function") {
        const current = await session.auth.getCurrentUser();
        const currentId = current?.uid || current?.userId;
        if (!this.isCurrent(token)) throw new Error("Learning owner changed during authentication lookup");
        if (!currentId || currentId !== session.profile.userId) {
          announce(false);
          owner = ""; generation += 1;
          if (typeof window.dispatchEvent === "function" && typeof CustomEvent === "function") {
            window.dispatchEvent(new CustomEvent("sat-learning-owner-invalid"));
          }
          throw new Error("Current authentication no longer matches this learning owner");
        }
      }
      if (!this.isCurrent(token)) throw new Error("Learning owner changed before request");
      return session.callFunction(name, data);
    },
    mergeGlobal(current, normalize, mergeQuestion) {
      let stored = {};
      try { stored = JSON.parse(this.getItem("sat-learning:v2") || "{}"); } catch {}
      const left = stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
      const right = current && typeof current === "object" && !Array.isArray(current) ? current : {};
      const questions = { ...(left.questions || {}) }, pages = { ...(left.pages || {}) };
      for (const [id, value] of Object.entries(right.questions || {})) questions[id] = mergeQuestion(questions[id], value);
      for (const [id, value] of Object.entries(right.pages || {})) {
        const time = item => Date.parse(item?.lastVisitedAt || item?.updatedAt || "") || 0;
        if (!pages[id] || time(value) >= time(pages[id])) pages[id] = value;
      }
      const attempts = new Map();
      for (const item of [...(Array.isArray(left.attempts) ? left.attempts : []), ...(Array.isArray(right.attempts) ? right.attempts : [])]) {
        if (!item?.attemptId) continue;
        const previous = attempts.get(item.attemptId);
        attempts.set(item.attemptId, { ...previous, ...item, revealed: Boolean(previous?.revealed || item.revealed), revealedAt: previous?.revealedAt || item.revealedAt || null });
      }
      return normalize({ ...left, ...right, questions, pages, attempts: [...attempts.values()].slice(-1000) });
    },
    // Serialize global read/merge/write across cooperating tabs when available.
    coordinateGlobal(write) {
      const token = this.token();
      if (typeof navigator !== "undefined" && navigator.locks?.request && owner) {
        return navigator.locks.request(prefix() + "global-write", () => this.isCurrent(token) ? write() : false);
      }
      return Promise.resolve(this.isCurrent(token) ? write() : false);
    },
  };
}

/* Study time: seconds of active practice per local day, kept in the owner-scoped
   store on this device only. It is not part of the cloud DTO and is never merged. */
const SAT_STUDY_TIME_KEY = "sat-learning:study-time:v1";
function satStudyDayKey(date = new Date()) {
  const pad = value => String(value).padStart(2, "0");
  return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
}
function readSatStudyDays(store) {
  const days = {};
  try {
    const raw = JSON.parse(store.getItem(SAT_STUDY_TIME_KEY) || "null");
    for (const [key, value] of Object.entries(raw && typeof raw.days === "object" && raw.days ? raw.days : {})) {
      if (/^\d{4}-\d{2}-\d{2}$/.test(key) && Number.isFinite(value) && value > 0) days[key] = Math.floor(value);
    }
  } catch {}
  return days;
}
function summarizeSatStudyTime(store, now = new Date()) {
  const days = readSatStudyDays(store), week = new Set();
  for (let offset = 0; offset < 7; offset += 1) {
    week.add(satStudyDayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - offset)));
  }
  let total = 0, weekTotal = 0;
  for (const [key, value] of Object.entries(days)) { total += value; if (week.has(key)) weekTotal += value; }
  return { today: days[satStudyDayKey(now)] || 0, week: weekTotal, total };
}
function formatSatStudyTime(seconds) {
  const total = Math.max(0, Number(seconds) || 0), minutes = Math.floor(total / 60);
  if (minutes < 1) return total >= 1 ? "<1 分钟" : "0 分钟";
  if (minutes < 60) return minutes + " 分钟";
  return Math.floor(minutes / 60) + " 小时" + (minutes % 60 ? " " + (minutes % 60) + " 分" : "");
}
// Counts time only while the page is visible and the learner has interacted recently.
// Each stretch is credited by the state it had: a stretch that ends because the page is
// hidden still counts, the time spent hidden never does, and coming back starts afresh.
// Unwritten time belongs to the owner it was spent under: an owner change writes it to
// that owner first (or drops it when the identity failed), so it never moves to another.
// `now` is a clock for tests; the page uses the real one.
function startSatStudyClock(store, { idleMs = 180000, tickMs = 15000, now = () => Date.now() } = {}) {
  const shown = () => document.visibilityState === "visible";
  let visible = shown(), lastActivity = now(), lastTick = now();
  let pending = 0, pendingToken = "";
  const touch = () => { lastActivity = now(); };
  for (const type of ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"]) {
    window.addEventListener(type, touch, { passive: true, capture: true });
  }
  document.addEventListener("scroll", touch, { passive: true, capture: true });
  // Credit the stretch since the last tick, as far as it was visible and not idle.
  function accrue(at) {
    const start = lastTick;
    lastTick = at;
    if (!visible || !store.ready()) return;
    const end = Math.min(at, lastActivity + idleMs);
    const span = Math.min(Math.max(0, end - start), tickMs * 2);
    const token = store.token();
    if (token !== pendingToken) { pending = 0; pendingToken = token; }
    pending += span;
  }
  // Write whole seconds for the owner they belong to; a failed write is retried later for that owner only.
  function write() {
    if (!pendingToken || !store.isCurrent(pendingToken)) { pending = 0; pendingToken = ""; return false; }
    const seconds = Math.floor(pending / 1000);
    if (seconds < 1) return true;
    try {
      const days = readSatStudyDays(store), key = satStudyDayKey(new Date(now()));
      days[key] = (days[key] || 0) + seconds;
      const kept = Object.keys(days).sort().slice(-400);
      store.setItem(SAT_STUDY_TIME_KEY, JSON.stringify({ version: 1, days: Object.fromEntries(kept.map(day => [day, days[day]])) }));
      pending -= seconds * 1000;
      return true;
    } catch { return false; }
  }
  function flush() { accrue(now()); return write(); }
  const timer = setInterval(flush, tickMs);
  document.addEventListener("visibilitychange", () => {
    const at = now();
    accrue(at);
    visible = shown();
    if (visible) touch();
    write();
  });
  window.addEventListener("pagehide", flush);
  if (typeof store.onOwnerChange === "function") {
    store.onOwnerChange(({ settle }) => {
      accrue(now());
      if (settle) write();
      pending = 0; pendingToken = "";
      lastTick = now();
    });
  }
  return { flush, stop() { clearInterval(timer); }, pendingMs: () => pending };
}

/* Where a question comes from. Pages show this only after the question has been
   answered. Recalled exam papers are labelled as such and never called official. */
function describeSatSource(refs, fallback = {}) {
  const ref = Array.isArray(refs) && refs[0] && typeof refs[0] === "object" ? refs[0] : null;
  const label = String((ref && ref.label) || fallback.label || "").trim();
  if (!label) return null;
  const parts = [label];
  if (ref && ref.year && !label.includes(String(ref.year))) parts.push(ref.year + " 年");
  if (ref && ref.module) parts.push(String(ref.module));
  const number = ref ? ref.number : fallback.number;
  if (number !== null && number !== undefined && number !== "") parts.push("第 " + number + " 题");
  const kind = String((ref && ref.kind) || fallback.kind || "");
  return { text: parts.join(" · "), note: kind === "real_exam" ? "回忆版真题，来源未经认证" : kind === "practice" ? "模拟题" : "" };
}

/* Vocabulary notebook: the words a learner chose to keep, in the owner-scoped
   store on this device only. It is not part of the cloud DTO and is never
   merged. Every change re-reads the stored list first, so two open pages do
   not overwrite each other's words. */
const SAT_VOCABULARY_KEY = "sat-learning:vocabulary:v1";
const SAT_VOCABULARY_LIMIT = 2000;
const SAT_VOCABULARY_TEXTS = { word: 80, partOfSpeech: 24, english: 400, chinese: 200, example: 400, exampleChinese: 400,
  note: 1000, context: 400, questionId: 120, route: 160, vocabId: 80 };
function satVocabularyKey(word) {
  return String(word || "").replace(/’/g, "'").trim().replace(/\s+/g, " ").toLowerCase().slice(0, 80);
}
function normalizeSatVocabularyWord(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const item = {};
  for (const [field, limit] of Object.entries(SAT_VOCABULARY_TEXTS)) {
    item[field] = typeof raw[field] === "string" ? raw[field].trim().slice(0, limit) : "";
  }
  if (!satVocabularyKey(item.word)) return null;
  const stamp = value => (typeof value === "string" && Number.isFinite(Date.parse(value)) ? value : "");
  item.custom = raw.custom === true;
  item.addedAt = stamp(raw.addedAt) || new Date().toISOString();
  item.mastered = raw.mastered === true;
  item.masteredAt = item.mastered ? stamp(raw.masteredAt) || item.addedAt : "";
  return item;
}
// Newest first. Unreadable or foreign values are ignored, never repaired in place.
function readSatVocabulary(store) {
  const words = new Map();
  try {
    const raw = JSON.parse(store.getItem(SAT_VOCABULARY_KEY) || "null");
    for (const value of Array.isArray(raw?.words) ? raw.words : []) {
      const item = normalizeSatVocabularyWord(value);
      if (item && !words.has(satVocabularyKey(item.word))) words.set(satVocabularyKey(item.word), item);
    }
  } catch {}
  return [...words.values()].sort((left, right) => Date.parse(right.addedAt) - Date.parse(left.addedAt));
}
function writeSatVocabulary(store, words) {
  if (!store.ready()) return false;
  try {
    store.setItem(SAT_VOCABULARY_KEY, JSON.stringify({ version: 1, words: words.slice(0, SAT_VOCABULARY_LIMIT) }));
    return true;
  } catch { return false; }
}
function hasSatVocabularyWord(store, word) {
  const key = satVocabularyKey(word);
  return Boolean(key) && readSatVocabulary(store).some(item => satVocabularyKey(item.word) === key);
}
// Adds a word, or fills in what an earlier entry for the same word lacks. The
// learner's own note, the date and the mastered mark of that entry are kept.
function saveSatVocabularyWord(store, entry) {
  const incoming = normalizeSatVocabularyWord(entry);
  if (!incoming) return false;
  const key = satVocabularyKey(incoming.word), words = readSatVocabulary(store);
  const index = words.findIndex(item => satVocabularyKey(item.word) === key);
  if (index < 0) return writeSatVocabulary(store, [incoming, ...words]);
  const previous = words[index], merged = { ...previous };
  for (const field of Object.keys(SAT_VOCABULARY_TEXTS)) {
    if (field !== "note" && incoming[field] && (!previous[field] || !incoming.custom)) merged[field] = incoming[field];
  }
  merged.custom = previous.custom && incoming.custom;
  words[index] = merged;
  return writeSatVocabulary(store, words);
}
function updateSatVocabularyWord(store, word, patch) {
  const key = satVocabularyKey(word), words = readSatVocabulary(store);
  const index = words.findIndex(item => satVocabularyKey(item.word) === key);
  if (index < 0) return false;
  const next = normalizeSatVocabularyWord({ ...words[index], ...patch, word: words[index].word, addedAt: words[index].addedAt });
  if (!next) return false;
  words[index] = next;
  return writeSatVocabulary(store, words);
}
function removeSatVocabularyWord(store, word) {
  const key = satVocabularyKey(word), words = readSatVocabulary(store);
  const kept = words.filter(item => satVocabularyKey(item.word) !== key);
  return kept.length !== words.length && writeSatVocabulary(store, kept);
}

/* ---------- Dictionaries ----------
   The site carries no dictionary's text: a publisher's entries are its own. A page
   links to the publisher's page for the word, and an app that embeds the site may
   hand over what the device's own dictionaries say through a "satDictionary" message
   handler that answers { entries: [{ dictionary, text }], missing: [names] }. */
function satOxfordUrl(word) {
  return "https://www.oxfordlearnersdictionaries.com/search/english/?q=" + encodeURIComponent(String(word || "").trim().slice(0, 80));
}
function satDeviceDictionary() {
  const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.satDictionary;
  return handler && typeof handler.postMessage === "function" ? handler : null;
}
// What the device's dictionaries say about a word, or null where the page has no such app around it.
async function satDictionaryLookup(word) {
  const handler = satDeviceDictionary(), text = String(word || "").trim().slice(0, 80);
  if (!handler || !text) return null;
  try {
    const reply = await handler.postMessage({ word: text });
    const entries = (Array.isArray(reply && reply.entries) ? reply.entries : [])
      .filter(item => item && typeof item.dictionary === "string" && typeof item.text === "string" && item.text.trim())
      .map(item => ({ dictionary: item.dictionary.slice(0, 80), text: item.text.slice(0, 6000) }));
    const missing = (Array.isArray(reply && reply.missing) ? reply.missing : [])
      .filter(name => typeof name === "string").map(name => name.slice(0, 80));
    return { entries, missing };
  } catch { return { entries: [], missing: [] }; }
}
// The folded "牛津词典" layer of a word card; it is filled when first opened.
function satDictionaryLayerHtml(word, escape, layerClass) {
  return `<details class="${layerClass} sat-dictionary" data-dictionary-word="${escape(word)}"><summary>牛津词典</summary><div class="sat-dictionary-body"></div></details>`;
}
async function satFillDictionaryLayer(layer, escape) {
  const body = layer.querySelector(".sat-dictionary-body"), word = layer.dataset.dictionaryWord || "";
  if (!body || !layer.open || layer.dataset.filled) return;
  layer.dataset.filled = "1";
  const link = `<p class="sat-dictionary-link"><a href="${escape(satOxfordUrl(word))}" target="_blank" rel="noopener noreferrer">在牛津学习词典网站查看 ${escape(word)}</a></p>`;
  if (!satDeviceDictionary()) {
    body.innerHTML = `<p class="sat-dictionary-note">牛津词典的条目归出版方所有，这里不转载，只给出官网链接。</p>${link}`;
    return;
  }
  body.innerHTML = `<p class="sat-dictionary-note">正在查本机词典…</p>`;
  const result = await satDictionaryLookup(word);
  const entries = result ? result.entries : [], missing = result ? result.missing : [];
  body.innerHTML = entries.map(item => `<div class="sat-dictionary-entry"><p class="sat-dictionary-source">${escape(item.dictionary)}</p><p class="sat-dictionary-text">${escape(item.text)}</p></div>`).join("")
    + (entries.length ? "" : `<p class="sat-dictionary-note">本机词典里没有查到这个词。</p>`)
    + (missing.length ? `<p class="sat-dictionary-note">这台 Mac 还没有下载《${escape(missing.join("》《"))}》。打开“词典”App 的设置，勾选后会自动下载，之后这里直接显示。</p>` : "")
    + link;
}

/* ---------- 错题本 ----------
   A question is in the mistake book when the learner added it by hand (`starred`),
   or answered it wrong and has not taken it out since. Three timestamps decide the
   second case, and each only ever moves forward, so records merged from two places
   agree: `lastWrongAt` is the latest wrong answer (records older than that field
   only have `firstWrongAt`), `bookRemovedAt` is when the learner last took the
   question out, and `bookRestoredAt` is when a removal was last undone. A later
   wrong answer, or an undo, brings the question back. Both pages decide membership
   here and nowhere else. */
const SAT_ERROR_TAGS = [["vocabulary", "词汇"], ["comprehension", "文章理解"], ["evidence", "证据定位"], ["logic", "逻辑推断"],
  ["grammar", "语法规则"], ["careless", "粗心"], ["time", "时间不足"]];
function satInMistakeBook(record) {
  const value = record && typeof record === "object" && !Array.isArray(record) ? record : {};
  if (value.starred) return true;
  if (!value.firstWrongAt) return false;
  const stamp = text => Date.parse(text || "") || 0;
  return Math.max(stamp(value.lastWrongAt), stamp(value.firstWrongAt), stamp(value.bookRestoredAt)) > stamp(value.bookRemovedAt);
}
// The later of two timestamps, for fields that only ever move forward.
function satLaterStamp(left, right) {
  const stamp = text => Date.parse(text || "") || 0;
  return (stamp(left) >= stamp(right) ? left || right : right) || null;
}
// "Now", but later than every timestamp given: two events in the same millisecond keep their order.
function satStampAfter(...stamps) {
  const latest = Math.max(0, ...stamps.map(text => Date.parse(text || "") || 0));
  return new Date(Math.max(Date.now(), latest + 1)).toISOString();
}
// What changes on a record when a question is taken out of the mistake book, and when that is undone.
function satMistakeRemoval(record) {
  return { starred: false, bookRemovedAt: satStampAfter(record.lastWrongAt, record.firstWrongAt, record.bookRestoredAt, record.bookRemovedAt) };
}
function satMistakeRestore(record, starred) {
  return { starred: Boolean(starred), bookRestoredAt: satStampAfter(record.bookRemovedAt, record.bookRestoredAt) };
}
// The button on a practice page: take the question out, or put it in by hand. Changes the record it is given.
function toggleSatMistakeBook(record) {
  if (satInMistakeBook(record)) Object.assign(record, satMistakeRemoval(record));
  else record.starred = true;
  return record;
}

/* What the learner was looking at in the mistake book: the filters, and the
   questions that matched, in order. The learning centre writes it when a question
   is opened; the practice page walks that list and notes the question it is on, so
   the book can come back to the same filters and the same place. It lives in the
   owner-scoped store on this device only and is not part of the cloud DTO. */
const SAT_MISTAKE_VIEW_KEY = "sat-learning:mistake-book-view:v1";
const SAT_MISTAKE_VIEW_LIMIT = 2000;
const SAT_MISTAKE_FILTERS = ["status", "origin", "tags", "skills", "levels"];
function normalizeSatMistakeView(raw) {
  const value = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  const text = (item, limit) => (typeof item === "string" ? item.slice(0, limit) : "");
  const list = items => [...new Set((Array.isArray(items) ? items : []).filter(item => typeof item === "string" && item && item.length <= 120))].slice(0, 40);
  const filters = {}, source = value.filters && typeof value.filters === "object" && !Array.isArray(value.filters) ? value.filters : null;
  for (const name of SAT_MISTAKE_FILTERS) filters[name] = list(source && source[name]);
  const questions = [], seen = new Set();
  for (const item of Array.isArray(value.questions) ? value.questions : []) {
    if (!item || typeof item.questionId !== "string" || !item.questionId || seen.has(item.questionId)) continue;
    if (typeof item.route !== "string" || !/^(sets|collections|categories)\/[A-Za-z0-9._-]+\.html$/.test(item.route)) continue;
    seen.add(item.questionId);
    questions.push({ questionId: item.questionId.slice(0, 120), route: item.route });
    if (questions.length >= SAT_MISTAKE_VIEW_LIMIT) break;
  }
  return {
    version: 1,
    // No stored filters at all means the book has not been used yet: the page then starts from its defaults.
    hasFilters: Boolean(source),
    filters, query: text(value.query, 80), sort: text(value.sort, 20),
    questions, startedAt: text(value.startedAt, 40), lastQuestionId: text(value.lastQuestionId, 120)
  };
}
function readSatMistakeView(store) {
  try { return normalizeSatMistakeView(JSON.parse(store.getItem(SAT_MISTAKE_VIEW_KEY) || "null")); }
  catch { return normalizeSatMistakeView(null); }
}
// Re-reads the stored view first, so the learning centre and a practice page change only their own part.
function updateSatMistakeView(store, patch) {
  if (!store.ready()) return false;
  try {
    const current = readSatMistakeView(store), merged = { ...current, ...patch };
    if (!current.hasFilters && !(patch && patch.filters)) delete merged.filters;
    const { hasFilters, ...view } = normalizeSatMistakeView(merged);
    if (!hasFilters) delete view.filters;
    store.setItem(SAT_MISTAKE_VIEW_KEY, JSON.stringify(view));
    return true;
  } catch { return false; }
}
