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
  return {
    ready: () => Boolean(owner),
    token: () => owner + ":" + generation,
    isCurrent: token => Boolean(owner) && token === owner + ":" + generation,
    bind(session) {
      const profileId = typeof session?.profile?.userId === "string" ? session.profile.userId : "";
      const userId = typeof session?.user?.uid === "string" ? session.user.uid : "";
      if (!profileId || profileId !== profileId.trim() || profileId.length > 256 || /[\u0000-\u001f]/.test(profileId) || (userId && userId !== profileId)) {
        owner = ""; generation += 1;
        throw new Error("Learning owner identity is missing or inconsistent");
      }
      const verifiedOwner = "user:" + profileId;
      const changed = owner !== verifiedOwner;
      if (changed) { owner = verifiedOwner; generation += 1; }
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
