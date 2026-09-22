const API_ROOT = "/api/v1";
const TOKEN_KEY = "ligong_dazi_access_token";

const state = {
  token: localStorage.getItem(TOKEN_KEY),
  user: null,
  preview: null,
  selectedUsers: new Set(),
  selectedActivity: null,
  activities: [],
  squareItems: [],
  squareOffset: 0,
  squareHasMore: false,
  activityFilter: "upcoming",
  pendingLeave: null,
  activeActivity: null,
  timeVotes: [],
  photoObjectUrls: [],
  peerTasks: [],
  applications: [],
  runningTimer: null,
  summaryTimer: null,
  toastTimer: null,
  skillRowCounter: 0,
  profileSaveTimer: null,
  profileSaveInFlight: false,
  profileSavedSnapshot: "",
  profileEditRevision: 0,
};

const LEVEL_LABELS = ["", "小白", "入门", "熟练", "擅长", "精通"];

const elements = {
  authView: document.querySelector("#auth-view"),
  appView: document.querySelector("#app-view"),
  mainNav: document.querySelector("#main-nav"),
  notificationButton: document.querySelector("#notification-button"),
  notificationBadge: document.querySelector("#notification-badge"),
  notificationsDialog: document.querySelector("#notifications-dialog"),
  notificationsList: document.querySelector("#notifications-list"),
  applicationsSection: document.querySelector("#applications-section"),
  applicationsList: document.querySelector("#applications-list"),
  accountArea: document.querySelector("#account-area"),
  accountName: document.querySelector("#account-name"),
  authError: document.querySelector("#auth-error"),
  loginForm: document.querySelector("#login-form"),
  registerForm: document.querySelector("#register-form"),
  matchForm: document.querySelector("#match-form"),
  matchSubmit: document.querySelector("#match-submit"),
  resultEmpty: document.querySelector("#result-empty"),
  resultRunning: document.querySelector("#result-running"),
  resultContent: document.querySelector("#result-content"),
  resultSuccess: document.querySelector("#result-success"),
  matchError: document.querySelector("#match-error"),
  runningTitle: document.querySelector("#running-title"),
  progressBar: document.querySelector("#progress-bar"),
  resultSummary: document.querySelector("#result-summary"),
  agentTrace: document.querySelector("#agent-trace"),
  personalizationReport: document.querySelector("#personalization-report"),
  candidateList: document.querySelector("#candidate-list"),
  confirmBar: document.querySelector("#confirm-bar"),
  selectionSummary: document.querySelector("#selection-summary"),
  selectionNote: document.querySelector("#selection-note"),
  confirmLocationField: document.querySelector("#confirm-location-field"),
  confirmLocation: document.querySelector("#confirm-location"),
  confirmLocationError: document.querySelector("#confirm-location-error"),
  confirmButton: document.querySelector("#confirm-match-button"),
  successTitle: document.querySelector("#success-title"),
  successCopy: document.querySelector("#success-copy"),
  invitationList: document.querySelector("#invitation-list"),
  inviteBadge: document.querySelector("#invite-badge"),
  activityBadge: document.querySelector("#activity-badge"),
  activityList: document.querySelector("#activity-list"),
  squareFilterForm: document.querySelector("#square-filter-form"),
  squareList: document.querySelector("#square-list"),
  squareLoadMore: document.querySelector("#square-load-more"),
  peerReviewSection: document.querySelector("#peer-review-section"),
  peerReviewList: document.querySelector("#peer-review-list"),
  peerTaskCount: document.querySelector("#peer-task-count"),
  profileForm: document.querySelector("#profile-form"),
  profileCredit: document.querySelector("#profile-credit"),
  profileStatus: document.querySelector("#profile-status"),
  profileRetry: document.querySelector("#profile-retry"),
  campusMigrationNote: document.querySelector("#campus-migration-note"),
  aiSummaryCopy: document.querySelector("#ai-summary-copy"),
  aiSummaryCooldown: document.querySelector("#ai-summary-cooldown"),
  generateSummaryButton: document.querySelector("#generate-summary-button"),
  hobbySkillList: document.querySelector("#hobby-skill-list"),
  addHobbySkillButton: document.querySelector("#add-hobby-skill"),
  skillMarks: document.querySelector("#skill-marks"),
  agentModePill: document.querySelector("#agent-mode-pill"),
  agentModeLabel: document.querySelector("#agent-mode-label"),
  leaveDialog: document.querySelector("#leave-dialog"),
  leaveDialogTitle: document.querySelector("#leave-dialog-title"),
  leaveDialogCopy: document.querySelector("#leave-dialog-copy"),
  leavePenaltyCard: document.querySelector("#leave-penalty-card"),
  confirmLeaveButton: document.querySelector("#confirm-leave-button"),
  feedbackDialog: document.querySelector("#feedback-dialog"),
  feedbackForm: document.querySelector("#feedback-form"),
  feedbackDialogTitle: document.querySelector("#feedback-dialog-title"),
  feedbackError: document.querySelector("#feedback-error"),
  feedbackSkillFields: document.querySelector("#feedback-skill-fields"),
  peerReviewDialog: document.querySelector("#peer-review-dialog"),
  peerReviewForm: document.querySelector("#peer-review-form"),
  peerDialogTitle: document.querySelector("#peer-dialog-title"),
  peerDialogContext: document.querySelector("#peer-dialog-context"),
  peerReviewError: document.querySelector("#peer-review-error"),
  timeVoteDialog: document.querySelector("#time-vote-dialog"),
  timeVoteDialogTitle: document.querySelector("#time-vote-dialog-title"),
  timeVoteForm: document.querySelector("#time-vote-form"),
  timeVoteList: document.querySelector("#time-vote-list"),
  timeVoteCount: document.querySelector("#time-vote-count"),
  timeVoteError: document.querySelector("#time-vote-error"),
  photoDialog: document.querySelector("#activity-photo-dialog"),
  photoDialogTitle: document.querySelector("#activity-photo-dialog-title"),
  photoForm: document.querySelector("#activity-photo-form"),
  photoList: document.querySelector("#activity-photo-list"),
  photoError: document.querySelector("#activity-photo-error"),
  activityParticipantsDialog: document.querySelector("#activity-participants-dialog"),
  activityParticipantsTitle: document.querySelector("#activity-participants-title"),
  activityParticipantsSummary: document.querySelector("#activity-participants-summary"),
  activityParticipantsList: document.querySelector("#activity-participants-list"),
  userProfileDialog: document.querySelector("#user-profile-dialog"),
  userProfileContent: document.querySelector("#user-profile-content"),
  toast: document.querySelector("#toast"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatApiError(payload, fallback) {
  if (!payload) return fallback;
  if (typeof payload.detail === "string") return payload.detail;
  if (typeof payload.detail?.message === "string") return payload.detail.message;
  if (Array.isArray(payload.detail)) {
    return payload.detail
      .map((item) => item.msg || "输入内容有误")
      .filter(Boolean)
      .join("；");
  }
  return fallback;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_ROOT}${path}`, { ...options, headers });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/")) {
      logout(false);
    }
    const error = new Error(formatApiError(payload, `请求失败（${response.status}）`));
    error.status = response.status;
    error.code = payload?.detail?.code || null;
    throw error;
  }
  return payload;
}

async function apiBlob(path) {
  const response = await fetch(`${API_ROOT}${path}`, {
    headers: { Authorization: `Bearer ${state.token}` },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(formatApiError(payload, `请求失败（${response.status}）`));
  }
  return response.blob();
}

function setButtonLoading(button, loading, loadingText) {
  if (loading) {
    button.dataset.originalText = button.textContent.trim();
    button.textContent = loadingText;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("is-visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    elements.toast.classList.remove("is-visible");
  }, 2800);
}

function showInlineError(element, message) {
  element.textContent = message;
  element.classList.remove("is-hidden");
}

function setAgentModeLabel(mode, model) {
  const modelName = String(model || "").trim();
  elements.agentModeLabel.textContent =
    (mode === "llm" || mode === "openai_compatible") && modelName
      ? `${modelName} 为你服务`
      : "规则匹配为你服务";
  elements.agentModePill.title =
    mode === "deterministic" ? "当前使用规则匹配，没有调用 AI 模型" : "当前匹配方式";
}

async function loadAgentMode() {
  try {
    const response = await fetch("/health/ready", { cache: "no-store" });
    if (!response.ok) throw new Error("匹配服务未就绪");
    const status = await response.json();
    if (!state.preview) setAgentModeLabel(status.agent_mode, status.agent_model);
  } catch {
    if (!state.preview) elements.agentModeLabel.textContent = "匹配方式暂不可查";
  }
}

function hideInlineError(element) {
  element.textContent = "";
  element.classList.add("is-hidden");
}

function switchAuthPanel(panel) {
  const loginActive = panel === "login";
  document.querySelector("#login-panel").classList.toggle("is-hidden", !loginActive);
  document.querySelector("#register-panel").classList.toggle("is-hidden", loginActive);
  document.querySelector("#login-tab").classList.toggle("is-active", loginActive);
  document.querySelector("#register-tab").classList.toggle("is-active", !loginActive);
  document.querySelector("#login-tab").setAttribute("aria-selected", String(loginActive));
  document.querySelector("#register-tab").setAttribute("aria-selected", String(!loginActive));
  hideInlineError(elements.authError);
  if (!loginActive) {
    showRegisterStep("account");
    const ios = /iPhone|iPad|iPod/i.test(navigator.userAgent);
    const android = /Android/i.test(navigator.userAgent);
    document.querySelector("#ios-install-guide").classList.toggle("is-hidden", !ios || window.matchMedia("(display-mode: standalone)").matches);
    document.querySelector("#android-install-guide").classList.toggle("is-hidden", !android);
  }
}

function showRegisterStep(step) {
  document.querySelectorAll("[data-register-step]").forEach((panel) => {
    panel.classList.toggle("is-hidden", panel.dataset.registerStep !== step);
  });
  document.querySelectorAll("[data-register-dot]").forEach((dot) => {
    const active = dot.dataset.registerDot === step;
    dot.classList.toggle("is-active", active);
    dot.classList.toggle("is-done", step === "profile" && dot.dataset.registerDot === "account");
  });
}

function continueRegistration() {
  const fields = ["display_name", "email", "password"].map(
    (name) => elements.registerForm.elements[name],
  );
  const invalid = fields.find((field) => !field.checkValidity());
  if (invalid) {
    invalid.reportValidity();
    return;
  }
  showRegisterStep("profile");
  elements.registerForm.elements.campus.focus();
}

function showAuthenticatedShell() {
  document.body.classList.add("has-app");
  elements.authView.classList.add("is-hidden");
  elements.appView.classList.remove("is-hidden");
  elements.mainNav.classList.remove("is-hidden");
  elements.accountArea.classList.remove("is-hidden");
  elements.accountName.textContent = state.user.display_name;
  populateProfileForm();
  updatePeopleNeededOutput();
  loadInvitations(true);
  loadActivities(true);
  loadNotifications();
  refreshPushStatus();
}

function showAuthShell() {
  document.body.classList.remove("has-app");
  elements.authView.classList.remove("is-hidden");
  elements.appView.classList.add("is-hidden");
  elements.mainNav.classList.add("is-hidden");
  elements.accountArea.classList.add("is-hidden");
}

async function logout(showMessage = true) {
  if ("serviceWorker" in navigator) {
    try {
      const registration = await navigator.serviceWorker.getRegistration();
      const subscription = await registration?.pushManager?.getSubscription();
      if (subscription) {
        if (showMessage && state.token) await api(`/push/subscriptions?endpoint=${encodeURIComponent(subscription.endpoint)}`, {method: "DELETE"});
        await subscription.unsubscribe();
      }
    } catch { /* Local sign-out still completes even if push is unavailable. */ }
  }
  window.clearTimeout(state.profileSaveTimer);
  state.profileEditRevision += 1;
  state.token = null;
  state.user = null;
  state.preview = null;
  state.selectedUsers.clear();
  state.selectedActivity = null;
  localStorage.removeItem(TOKEN_KEY);
  showAuthShell();
  switchAuthPanel("login");
  if (showMessage) showToast("已安全退出");
}

async function loadCurrentUser() {
  state.user = await api("/users/me");
  showAuthenticatedShell();
}

async function handleLogin(event) {
  event.preventDefault();
  hideInlineError(elements.authError);
  const button = event.submitter;
  setButtonLoading(button, true, "正在登录…");
  const form = new FormData(elements.loginForm);
  try {
    const result = await api("/auth/token", {
      method: "POST",
      body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
    });
    state.token = result.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    await loadCurrentUser();
    switchTab(state.user.campus ? "match" : "profile");
  } catch (error) {
    showInlineError(elements.authError, error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function handleRegister(event) {
  event.preventDefault();
  hideInlineError(elements.authError);
  const button = event.submitter;
  setButtonLoading(button, true, "正在创建账号…");
  const form = new FormData(elements.registerForm);
  const interests = form.getAll("interests");
  const skillName = String(form.get("register_skill_name") || "").trim();
  if (!interests.length) {
    showInlineError(elements.authError, "至少选一个平时喜欢的活动，这样 Agent 才知道从哪里开始。 ");
    document.querySelector(".onboarding-choices").scrollIntoView({ behavior: "smooth", block: "center" });
    setButtonLoading(button, false);
    return;
  }
  try {
    const result = await api("/auth/register", {
      method: "POST",
      body: JSON.stringify({
        display_name: String(form.get("display_name")).trim(),
        email: form.get("email"),
        password: form.get("password"),
        university: "南京理工大学",
        campus: String(form.get("campus")).trim(),
        department: String(form.get("department")).trim(),
        grade_year: Number(form.get("grade_year")),
        gender: form.get("gender"),
        interests,
        hobby_skills: skillName
          ? [{ name: skillName, level: Number(form.get("register_skill_level")) }]
          : [],
        preferred_locations: splitList(form.get("preferred_locations")),
        social_style: form.get("social_style"),
      }),
    });
    state.token = result.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    await loadCurrentUser();
    switchTab(state.user.campus ? "match" : "profile");
    showToast("画像已就位，去发起第一场搭子局吧");
  } catch (error) {
    showInlineError(elements.authError, error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function switchTab(tabName) {
  if (state.user && !["南京", "江阴"].includes(state.user.campus) && tabName !== "profile") {
    tabName = "profile";
    showToast("先选择南京或江阴校区，才能查看活动和搭子");
  }
  document.querySelectorAll(".nav-button[data-tab]").forEach((button) => {
    const active = button.dataset.tab === tabName;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("is-hidden", panel.dataset.panel !== tabName);
  });
  if (tabName === "invitations") loadInvitations();
  if (tabName === "activities") loadActivities();
  if (tabName === "square") loadSquare(false);
  if (tabName === "match" && !state.preview) loadAgentMode();
  if (tabName === "invitations" || tabName === "activities") loadNotifications();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function toLocalInputValue(date) {
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16);
}

function initializeDates() {
  const now = new Date();
  const start = new Date(now.getTime() + 2 * 60 * 60 * 1000);
  start.setMinutes(start.getMinutes() < 30 ? 30 : 60, 0, 0);
  const end = new Date(start.getTime() + 2 * 60 * 60 * 1000);
  document.querySelector("#starts-at").value = toLocalInputValue(start);
  document.querySelector("#ends-at").value = toLocalInputValue(end);
}

function setResultView(view) {
  elements.resultEmpty.classList.toggle("is-hidden", view !== "empty");
  elements.resultRunning.classList.toggle("is-hidden", view !== "running");
  elements.resultContent.classList.toggle("is-hidden", view !== "content");
  elements.resultSuccess.classList.toggle("is-hidden", view !== "success");
  elements.confirmBar.classList.toggle("is-hidden", view !== "content");
}

function startRunningProgress() {
  const stepIds = ["step-activities", "step-users", "step-score"];
  const titles = ["正在查询可加入的活动", "正在寻找时间合适的用户", "正在计算可解释匹配度"];
  let index = 0;
  stepIds.forEach((id) => document.querySelector(`#${id}`).classList.remove("is-current", "is-done"));
  document.querySelector(`#${stepIds[0]}`).classList.add("is-current");
  elements.runningTitle.textContent = titles[0];
  elements.progressBar.style.width = "24%";
  window.clearInterval(state.runningTimer);
  state.runningTimer = window.setInterval(() => {
    if (index >= 2) return;
    document.querySelector(`#${stepIds[index]}`).classList.remove("is-current");
    document.querySelector(`#${stepIds[index]}`).classList.add("is-done");
    index += 1;
    document.querySelector(`#${stepIds[index]}`).classList.add("is-current");
    elements.runningTitle.textContent = titles[index];
    elements.progressBar.style.width = `${54 + index * 22}%`;
  }, 650);
}

function finishRunningProgress() {
  window.clearInterval(state.runningTimer);
  document.querySelectorAll(".agent-steps li").forEach((item) => {
    item.classList.remove("is-current");
    item.classList.add("is-done");
  });
  elements.progressBar.style.width = "100%";
}

function getCategory(form) {
  const selected = String(form.get("category") || "");
  return selected === "__custom__"
    ? String(form.get("custom_category") || "").trim()
    : selected;
}

function syncCategoryChoice() {
  const customSelected = elements.matchForm.elements.category.value === "__custom__";
  const field = document.querySelector(".other-category-input");
  field.classList.toggle("is-hidden", !customSelected);
  elements.matchForm.elements.custom_category.required = customSelected;
}

function updatePeopleNeededOutput() {
  const count = Number(elements.matchForm.elements.people_needed.value);
  document.querySelector("#people-needed-output").textContent = `${count} 人`;
  const total = count + 1;
  const hint = document.querySelector("#people-needed-hint");
  if (state.user) {
    const min = state.user.preferred_group_min;
    const max = state.user.preferred_group_max;
    const fit = min <= total && total <= max;
    hint.textContent = `共 ${total} 人（含你）· 画像偏好 ${min}—${max} 人${fit ? "，刚好符合" : "，这次可以灵活调整"}`;
  } else {
    hint.textContent = `共 ${total} 人（含你），最多再找 9 位搭子`;
  }
  elements.matchForm.elements.people_needed.setAttribute("aria-valuetext", `还需要 ${count} 人`);
}

function clearMatchRequestAfterAgentError() {
  state.preview = null;
  loadAgentMode();
  state.selectedUsers.clear();
  state.selectedActivity = null;
  elements.matchForm.reset();
  elements.matchForm.querySelectorAll("input[name='category']").forEach((input) => {
    input.checked = false;
  });
  elements.matchForm.elements.custom_category.value = "";
  elements.matchForm.elements.location.value = "";
  elements.matchForm.elements.people_needed.value = "2";
  updatePeopleNeededOutput();
  elements.matchForm.elements.title.value = "";
  elements.matchForm.elements.personal_requirement.value = "";
  elements.matchForm.elements.same_gender_only.checked = false;
  syncCategoryChoice();
  initializeDates();
  setResultView("empty");
  elements.matchForm.querySelector("input[name='category']")?.focus();
}

async function handleMatch(event) {
  event.preventDefault();
  const form = new FormData(elements.matchForm);
  if (!getCategory(form)) {
    showInlineError(elements.matchError, "先选择活动类型；选“其他活动”后还需要写下名称。");
    (form.get("category") === "__custom__"
      ? elements.matchForm.elements.custom_category
      : elements.matchForm.querySelector("input[name='category']"))?.focus();
    return;
  }
  state.preview = null;
  loadAgentMode();
  hideInlineError(elements.matchError);
  state.selectedUsers.clear();
  state.selectedActivity = null;
  setResultView("running");
  startRunningProgress();
  setButtonLoading(elements.matchSubmit, true, "Agent 正在匹配…");
  const payload = {
    category: getCategory(form),
    starts_at: new Date(form.get("starts_at")).toISOString(),
    ends_at: new Date(form.get("ends_at")).toISOString(),
    location: String(form.get("location")).trim(),
    people_needed: Number(form.get("people_needed")),
    title: String(form.get("title") || "").trim() || null,
    personal_requirement: String(form.get("personal_requirement") || "").trim() || null,
    same_gender_only: form.has("same_gender_only"),
  };
  try {
    const preview = await api("/matches/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const run = await api(`/agent-runs/${preview.agent_run_id}`).catch(() => null);
    finishRunningProgress();
    state.preview = {
      ...preview,
      requestedCount: payload.people_needed,
      requestedLocation: payload.location,
      requestedStartsAt: payload.starts_at,
      requestedEndsAt: payload.ends_at,
      run,
    };
    elements.confirmLocation.value = "";
    elements.confirmLocationError.classList.add("is-hidden");
    renderMatchResult();
    window.setTimeout(() => setResultView("content"), 180);
  } catch (error) {
    window.clearInterval(state.runningTimer);
    if (error.code === "agent_output_error") {
      clearMatchRequestAfterAgentError();
      showToast("Agent 没有完成匹配，刚才填写的内容已清空");
    } else {
      setResultView("empty");
    }
    showInlineError(elements.matchError, error.message);
  } finally {
    setButtonLoading(elements.matchSubmit, false);
  }
}

function renderAgentTrace() {
  const toolLabels = {
    search_activities: ["查询已有活动", "检查同类活动、邻近时段与空余名额"],
    search_users: ["寻找可用用户", "过滤冲突、拉黑与低信用候选"],
    calculate_match: ["计算匹配度", "按七项权重生成解释"],
  };
  const trace = (state.preview.run?.trace || []).filter((item) => item.tool && toolLabels[item.tool]);
  const uniqueTrace = [...new Map(trace.map((item) => [item.tool, item])).values()];
  elements.agentTrace.innerHTML = uniqueTrace
    .map((item) => {
      const [title, detail] = toolLabels[item.tool];
      const count = Number.isFinite(item.result_count) ? ` · ${item.result_count} 条结果` : "";
      return `<div class="trace-item"><strong>${escapeHtml(title)}</strong>${escapeHtml(detail + count)}</div>`;
    })
    .join("");
}

function renderPersonalization() {
  const report = state.preview.personalization;
  const notices = [];
  if (report.applied.length) {
    notices.push(`<div class="notice"><strong>已采用：</strong>${escapeHtml(report.applied.join("；"))}</div>`);
  }
  if (report.ignored_for_safety.length) {
    notices.push(
      `<div class="notice notice-warning"><strong>未用于排序：</strong>${escapeHtml(report.ignored_for_safety.join("；"))}</div>`,
    );
  }
  if (report.unresolved.length) {
    notices.push(
      `<div class="notice notice-warning"><strong>暂未识别：</strong>${escapeHtml(report.unresolved.join("；"))}</div>`,
    );
  }
  elements.personalizationReport.innerHTML = notices.join("");
}

function formatDate(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function genderInfo(gender) {
  return {
    male: { symbol: "♂", label: "男", className: "gender-male" },
    female: { symbol: "♀", label: "女", className: "gender-female" },
    undisclosed: { symbol: "—", label: "不公开", className: "gender-undisclosed" },
  }[gender] || { symbol: "—", label: "不公开", className: "gender-undisclosed" };
}

function genderBadge(gender) {
  const info = genderInfo(gender);
  return `<span class="gender-badge ${info.className}"><b aria-hidden="true">${info.symbol}</b>${info.label}</span>`;
}

function personProfileTag(user, compact = false) {
  const info = genderInfo(user.gender);
  return `<button class="person-profile-tag ${info.className} ${compact ? "is-compact" : ""}" type="button" data-user-profile="${escapeHtml(user.id)}" aria-label="查看${escapeHtml(user.display_name)}的个人主页">
    <span class="person-symbol" aria-hidden="true">${info.symbol}</span>
    <span><strong>${escapeHtml(user.display_name)}</strong><small>${escapeHtml(info.label)} · 查看主页</small></span>
  </button>`;
}

function activityGenderSummary(activity, interactive = true) {
  const counts = activity.gender_counts || {};
  const undisclosedCount = Number(counts.undisclosed || 0);
  const content = `<span class="gender-count gender-male"><b aria-hidden="true">♂</b> 男 ${Number(counts.male || 0)}</span>
    <span class="gender-count gender-female"><b aria-hidden="true">♀</b> 女 ${Number(counts.female || 0)}</span>
    ${undisclosedCount ? `<span class="gender-count gender-undisclosed"><b aria-hidden="true">—</b> 不公开 ${undisclosedCount}</span>` : ""}
    ${interactive ? "<small>查看成员 →</small>" : ""}`;
  if (!interactive) return content;
  return `<button class="activity-gender-summary" type="button" data-activity-participants="${escapeHtml(activity.id)}" data-activity-title="${escapeHtml(activity.title)}" aria-label="查看${escapeHtml(activity.title)}的参与者">${content}</button>`;
}

function userCandidateCopy(candidate) {
  const user = candidate.user;
  const meta = [user.department, user.grade_year ? `${user.grade_year} 年级` : null, `信用 ${user.credit_score}`]
    .filter(Boolean)
    .join(" · ");
  return {
    title: user.display_name,
    kind: "候选搭子",
    meta,
  };
}

function activityCandidateCopy(candidate) {
  const activity = candidate.activity;
  return {
    title: activity.title,
    kind: "已有活动",
    meta: `${formatDate(activity.starts_at)} 至 ${formatDate(activity.ends_at)} · ${activity.location} · ${activity.participant_count}/${activity.capacity} 人`,
  };
}

function activityTimeDiffers(activity) {
  if (!activity || !state.preview) return false;
  return Math.abs(new Date(activity.starts_at) - new Date(state.preview.requestedStartsAt)) >= 60_000
    || Math.abs(new Date(activity.ends_at) - new Date(state.preview.requestedEndsAt)) >= 60_000;
}

function renderCandidates() {
  const candidates = state.preview.candidates || [];
  if (!candidates.length) {
    elements.candidateList.innerHTML = `
      <div class="empty-list">
        <div><strong>暂时没有刚刚好的候选</strong><br />可以调整条件重试，也可以先把活动发布出去，等同学主动加入。</div>
      </div>`;
    return;
  }
  elements.candidateList.innerHTML = candidates
    .map((candidate) => {
      const copy = candidate.candidate_type === "user" ? userCandidateCopy(candidate) : activityCandidateCopy(candidate);
      const inputType = candidate.candidate_type === "activity" ? "radio" : "checkbox";
      const inputName = candidate.candidate_type === "activity" ? "activity-candidate" : "user-candidate";
      const reasons = candidate.explanation
        .slice(0, 4)
        .map((reason) => `<span>${escapeHtml(reason)}</span>`)
        .join("");
      const skillMarks = (candidate.user?.skill_marks || [])
        .slice(0, 2)
        .map(
          (mark) => `<span class="candidate-system-mark">系统提醒 · ${escapeHtml(mark.name)}：${escapeHtml(mark.label)}</span>`,
        )
        .join("");
      const userHeading = candidate.user
        ? personProfileTag(candidate.user)
        : `<div class="candidate-title-row"><strong>${escapeHtml(copy.title)}</strong><span class="candidate-kind">${escapeHtml(copy.kind)}</span></div>`;
      const genderSummary = candidate.activity
        ? activityGenderSummary(candidate.activity)
        : "";
      const genderRule = candidate.activity?.same_gender_only
        ? `<span class="same-gender-tag">仅同性加入</span>` : "";
      const timeNote = activityTimeDiffers(candidate.activity)
        ? `<div class="candidate-time-note">时间与你填写的不完全一致，请核对实际时段</div>` : "";
      return `
        <article class="candidate-card" data-type="${candidate.candidate_type}" data-id="${escapeHtml(candidate.candidate_id)}">
          <input type="${inputType}" name="${inputName}" value="${escapeHtml(candidate.candidate_id)}" aria-label="选择${escapeHtml(copy.title)}" />
          <div class="candidate-main">
            ${userHeading}
            <p class="candidate-meta">${escapeHtml(copy.meta)}</p>
            ${genderSummary}
            ${genderRule}
            ${timeNote}
            <div class="candidate-reasons">${reasons}</div>
            ${skillMarks ? `<div class="candidate-system-marks">${skillMarks}</div>` : ""}
          </div>
          <div class="candidate-score">${escapeHtml(candidate.score)}<small>匹配分</small></div>
        </article>`;
    })
    .join("");
}

function renderMatchResult() {
  elements.resultSummary.textContent = state.preview.summary;
  setAgentModeLabel(state.preview.agent_mode, state.preview.agent_model);
  renderAgentTrace();
  renderPersonalization();
  renderCandidates();
  updateSelectionSummary();
}

function updateSelectionSummary() {
  document.querySelectorAll(".candidate-card").forEach((card) => {
    const selected =
      card.dataset.type === "activity"
        ? state.selectedActivity === card.dataset.id
        : state.selectedUsers.has(card.dataset.id);
    card.classList.toggle("is-selected", selected);
    card.querySelector("input").checked = selected;
  });
  const needsLocation = !state.preview.requestedLocation;
  elements.confirmLocationField.classList.toggle("is-hidden", !needsLocation);
  elements.confirmBar.classList.toggle("needs-location", needsLocation);
  elements.resultContent.classList.toggle("needs-location", needsLocation);
  if (state.selectedActivity) {
    const activity = state.preview.candidates.find(
      (item) => item.candidate_type === "activity" && item.candidate_id === state.selectedActivity,
    )?.activity;
    elements.selectionSummary.textContent = "已选择加入 1 个已有活动";
    elements.selectionNote.textContent = activity
      ? `将按现有活动的时间与地点加入：${formatDate(activity.starts_at)} 至 ${formatDate(activity.ends_at)}`
      : "将按现有活动的实际时间和地点加入";
    elements.confirmButton.textContent = "确认加入活动";
    elements.confirmButton.disabled = false;
  } else if (state.selectedUsers.size) {
    elements.selectionSummary.textContent = `已选择 ${state.selectedUsers.size} 位搭子`;
    elements.selectionNote.textContent = needsLocation
      ? "邀请前先填写下方地点，活动创建后才会发出邀请"
      : "确认后将创建活动并发出邀请";
    elements.confirmButton.textContent = "确认创建并邀请";
    elements.confirmButton.disabled = false;
  } else {
    elements.selectionSummary.textContent = "尚未选择候选";
    elements.selectionNote.textContent = needsLocation
      ? "可以先填地点发布活动，也可以选择上方现有活动"
      : "没挑中也没关系，可以先发布活动等人加入";
    elements.confirmButton.textContent = "确认并执行";
    elements.confirmButton.disabled = true;
  }
}

function handleCandidateSelection(event) {
  const profileButton = event.target.closest("[data-user-profile]");
  if (profileButton) {
    openUserProfile(profileButton.dataset.userProfile);
    return;
  }
  const participantButton = event.target.closest("[data-activity-participants]");
  if (participantButton) {
    openActivityParticipants(
      participantButton.dataset.activityParticipants,
      participantButton.dataset.activityTitle,
    );
    return;
  }
  const card = event.target.closest(".candidate-card");
  if (!card) return;
  const candidateId = card.dataset.id;
  if (card.dataset.type === "activity") {
    state.selectedActivity = state.selectedActivity === candidateId ? null : candidateId;
    state.selectedUsers.clear();
  } else {
    state.selectedActivity = null;
    if (state.selectedUsers.has(candidateId)) {
      state.selectedUsers.delete(candidateId);
    } else if (state.selectedUsers.size < state.preview.requestedCount) {
      state.selectedUsers.add(candidateId);
    } else {
      showToast(`本次最多选择 ${state.preview.requestedCount} 位搭子`);
    }
  }
  updateSelectionSummary();
}

async function confirmMatch(createSoloActivity = false) {
  if (!state.preview) return;
  if (!createSoloActivity && !state.selectedActivity && !state.selectedUsers.size) return;
  hideInlineError(elements.matchError);
  const joiningExisting = !createSoloActivity && Boolean(state.selectedActivity);
  const location = state.preview.requestedLocation || elements.confirmLocation.value.trim();
  if (!joiningExisting && !location) {
    elements.confirmLocationError.classList.remove("is-hidden");
    elements.confirmLocation.focus();
    return;
  }
  elements.confirmLocationError.classList.add("is-hidden");
  const actionButton = createSoloActivity
    ? document.querySelector("#create-solo-button")
    : elements.confirmButton;
  setButtonLoading(actionButton, true, createSoloActivity ? "正在发布…" : "正在执行…");
  try {
    const result = await api(`/matches/${state.preview.match_request_id}/confirm`, {
      method: "POST",
      body: JSON.stringify({
        candidate_user_ids: createSoloActivity ? [] : [...state.selectedUsers],
        existing_activity_id: createSoloActivity ? null : state.selectedActivity,
        create_solo_activity: createSoloActivity,
        location: joiningExisting ? null : location,
        join_policy: document.querySelector("#match-join-policy").value,
      }),
    });
    elements.successTitle.textContent =
      result.status === "applied" ? "申请已送出" : result.status === "joined" ? "已加入活动" : createSoloActivity ? "活动已发布" : "搭子局已创建";
    elements.successCopy.textContent = result.status === "applied"
      ? `正在等待“${result.activity.title}”的现有成员逐一同意，消息会在站内提醒你。`
      : createSoloActivity
      ? `“${result.activity.title}”现在由你先占一席，其他同学可以在匹配时加入。`
      : result.invitations.length
        ? `“${result.activity.title}”已创建，并向 ${result.invitations.length} 位候选发送邀请。`
        : `你已加入“${result.activity.title}”，可以在“我的活动”查看安排。`;
    setResultView("success");
    loadInvitations(true);
    loadActivities(true);
  } catch (error) {
    showInlineError(elements.matchError, error.message);
  } finally {
    setButtonLoading(actionButton, false);
  }
}

function resetMatchResult() {
  state.preview = null;
  loadAgentMode();
  state.selectedUsers.clear();
  state.selectedActivity = null;
  hideInlineError(elements.matchError);
  setResultView("empty");
  elements.matchForm.querySelector("textarea[name='personal_requirement']").focus();
}

async function loadSquare(append = false) {
  if (!state.token) return;
  const form = new FormData(elements.squareFilterForm);
  const params = new URLSearchParams({
    offset: String(append ? state.squareOffset : 0),
    limit: "12",
  });
  const category = String(form.get("category") || "").trim();
  const search = String(form.get("search") || "").trim();
  const date = String(form.get("date") || "").trim();
  if (category) params.set("category", category);
  if (search) params.set("search", search);
  if (date) params.set("date", date);
  if (!append) {
    elements.squareList.innerHTML = `<div class="empty-list">正在把活动摊开给你看…</div>`;
  }
  elements.squareLoadMore.disabled = true;
  try {
    const page = await api(`/activities/square?${params.toString()}`);
    const categorySelect = elements.squareFilterForm.elements.category;
    categorySelect.innerHTML = `<option value="">全部类型</option>${page.categories
      .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`)
      .join("")}`;
    if (category && !page.categories.includes(category)) {
      categorySelect.value = "";
      await loadSquare(false);
      return;
    }
    categorySelect.value = category;
    state.squareItems = append ? [...state.squareItems, ...page.items] : page.items;
    state.squareOffset = page.next_offset ?? state.squareItems.length;
    state.squareHasMore = page.has_more;
    renderSquare();
  } catch (error) {
    if (!append) elements.squareList.innerHTML = `<div class="empty-list">${escapeHtml(error.message)}</div>`;
  } finally {
    elements.squareLoadMore.disabled = false;
  }
}

function renderSquare() {
  elements.squareLoadMore.classList.toggle("is-hidden", !state.squareHasMore);
  if (!state.squareItems.length) {
    elements.squareList.innerHTML = `<div class="empty-list empty-list-playful"><strong>暂时没有符合筛选条件的活动，换个地点或类型看看吧。</strong></div>`;
    return;
  }
  elements.squareList.innerHTML = state.squareItems
    .map((item) => {
      const activity = item.activity;
      const reasons = item.recommendation_reasons
        .map((reason) => `<span>${escapeHtml(reason)}</span>`)
        .join("");
      const action = item.joined
        ? `<button class="button button-quiet" type="button" disabled>已经加入</button>`
        : item.application_status === "pending"
          ? `<button class="button button-quiet" type="button" disabled>申请等待全员同意</button>`
        : item.joinable
          ? `<button class="button button-primary" type="button" data-join-square="${escapeHtml(activity.id)}">${activity.join_policy === "approval" ? "申请加入" : "加入这场"}</button>`
          : `<button class="button button-quiet" type="button" disabled>${escapeHtml(item.join_reason || "暂不能加入")}</button>`;
      return `<article class="square-card">
        <div class="square-card-top"><span class="activity-role">${escapeHtml(activity.category)}</span><span class="square-score">适合度 ${escapeHtml(item.recommendation_score)}</span></div>
        ${activity.same_gender_only ? `<span class="same-gender-tag">仅同性加入</span>` : ""}
        <span class="same-gender-tag">${activity.join_policy === "approval" ? "入局需全员同意" : "可自由加入"}</span>
        <h2>${escapeHtml(activity.title)}</h2>
        <p class="square-time">${escapeHtml(formatDate(activity.starts_at))}</p>
        <p class="square-location">${escapeHtml(activity.location)} · ${activity.participant_count}/${activity.capacity} 人</p>
        ${activityGenderSummary(activity)}
        ${activity.description ? `<p class="square-description">${escapeHtml(activity.description)}</p>` : ""}
        <div class="candidate-reasons">${reasons}</div>
        <div class="square-card-action">${action}</div>
      </article>`;
    })
    .join("");
}

async function joinSquareActivity(button) {
  setButtonLoading(button, true, "正在处理…");
  try {
    const result = await api(`/activities/${button.dataset.joinSquare}/join`, { method: "POST" });
    showToast(result.message);
    await Promise.all([loadSquare(false), loadActivities(true)]);
  } catch (error) {
    showToast(error.message);
    setButtonLoading(button, false);
  }
}

function invitationStatusLabel(status) {
  const labels = {
    pending: ["待处理", ""],
    accepted: ["已接受", "is-success"],
    rejected: ["已拒绝", "is-muted"],
    expired: ["已过期", "is-muted"],
  };
  return labels[status] || [status, "is-muted"];
}

async function loadInvitations(silent = false) {
  if (!state.token) return;
  if (!silent) elements.invitationList.innerHTML = `<div class="empty-list">正在读取邀请…</div>`;
  try {
    const invitations = await api("/invitations");
    const pendingCount = invitations.filter((item) => item.status === "pending").length;
    elements.inviteBadge.textContent = String(pendingCount);
    elements.inviteBadge.classList.toggle("is-hidden", pendingCount === 0);
    renderInvitations(invitations);
  } catch (error) {
    if (!silent) elements.invitationList.innerHTML = `<div class="empty-list">${escapeHtml(error.message)}</div>`;
  }
}

function renderInvitations(invitations) {
  if (!invitations.length) {
    elements.invitationList.innerHTML = `<div class="empty-list">目前没有收到邀请。发起一次匹配，或去活动广场看看。</div>`;
    return;
  }
  elements.invitationList.innerHTML = invitations
    .map((invitation) => {
      const [statusText, statusClass] = invitationStatusLabel(invitation.status);
      const actions =
        invitation.status === "pending"
          ? `<div class="list-card-actions">
              <button class="button button-quiet" type="button" data-invitation-id="${escapeHtml(invitation.id)}" data-decision="rejected">拒绝</button>
              <button class="button button-primary" type="button" data-invitation-id="${escapeHtml(invitation.id)}" data-decision="accepted">接受邀请</button>
            </div>`
          : `<span class="status-label ${statusClass}">${escapeHtml(statusText)}</span>`;
      return `<article class="list-card">
        <div>
          <div class="candidate-title-row">
            <h2>${escapeHtml(invitation.activity.title)}</h2>
            <span class="status-label ${statusClass}">${escapeHtml(statusText)}</span>
          </div>
          <p>${escapeHtml(invitation.inviter.display_name)} 邀请你 · ${escapeHtml(formatDate(invitation.activity.starts_at))}</p>
          <p>${escapeHtml(invitation.activity.location)} · ${escapeHtml(invitation.activity.category)}</p>
          ${activityGenderSummary(invitation.activity)}
        </div>
        ${actions}
      </article>`;
    })
    .join("");
}

async function respondInvitation(event) {
  const participantButton = event.target.closest("[data-activity-participants]");
  if (participantButton) {
    openActivityParticipants(
      participantButton.dataset.activityParticipants,
      participantButton.dataset.activityTitle,
    );
    return;
  }
  const button = event.target.closest("button[data-invitation-id]");
  if (!button) return;
  setButtonLoading(button, true, button.dataset.decision === "accepted" ? "正在接受…" : "正在拒绝…");
  try {
    await api(`/invitations/${button.dataset.invitationId}/respond`, {
      method: "POST",
      body: JSON.stringify({ decision: button.dataset.decision }),
    });
    showToast(button.dataset.decision === "accepted" ? "已接受邀请" : "已拒绝邀请");
    await Promise.all([loadInvitations(), loadActivities(true)]);
  } catch (error) {
    showToast(error.message);
    setButtonLoading(button, false);
  }
}

function activityStatusLabel(status, membershipStatus) {
  if (membershipStatus === "withdrawn") return ["已退出", "is-muted"];
  const labels = {
    open: ["等搭子加入", ""],
    formed: ["搭子已就位", "is-success"],
    completed: ["已结束", "is-muted"],
    cancelled: ["已取消", "is-muted"],
  };
  return labels[status] || [status, "is-muted"];
}

async function loadActivities(silent = false) {
  if (!state.token) return;
  if (!silent) elements.activityList.innerHTML = `<div class="empty-list">正在翻开你的活动本…</div>`;
  try {
    const [activities, peerTasks] = await Promise.all([
      api("/activities/mine"),
      api("/feedback/peer-review-tasks"),
    ]);
    state.activities = activities;
    state.peerTasks = peerTasks;
    const reviewCount = activities.filter((item) => item.needs_feedback).length;
    elements.activityBadge.textContent = String(reviewCount);
    elements.activityBadge.classList.toggle("is-hidden", reviewCount === 0);
    renderActivities();
    renderPeerReviewTasks();
    await loadApplications();
  } catch (error) {
    if (!silent) elements.activityList.innerHTML = `<div class="empty-list">${escapeHtml(error.message)}</div>`;
  }
}

function filteredActivities() {
  const now = Date.now();
  if (state.activityFilter === "review") {
    return state.activities.filter((item) => item.needs_feedback);
  }
  if (state.activityFilter === "history") {
    return state.activities.filter(
      (item) => new Date(item.activity.ends_at).getTime() <= now || item.membership_status !== "confirmed",
    );
  }
  return state.activities.filter(
    (item) => new Date(item.activity.ends_at).getTime() > now && item.membership_status === "confirmed",
  );
}

function renderActivities() {
  const activities = filteredActivities();
  if (!activities.length) {
    const emptyCopy = {
      upcoming: "接下来还没有安排。去“找搭子”发起一场，给日程添点新鲜事。",
      review: "没有欠下的评价，干干净净。活动结束后，这里会提醒你写几句。",
      history: "活动足迹还是空的，第一场正在等你。",
    }[state.activityFilter];
    elements.activityList.innerHTML = `<div class="empty-list empty-list-playful"><strong>${escapeHtml(emptyCopy)}</strong></div>`;
    return;
  }
  elements.activityList.innerHTML = activities
    .map((item) => {
      const activity = item.activity;
      const [statusText, statusClass] = activityStatusLabel(activity.status, item.membership_status);
      const roleText = item.role === "owner" ? "我发起的" : "我参加的";
      const leaveAction = item.can_leave
        ? `<button class="button button-text-danger" type="button" data-leave-activity="${escapeHtml(activity.id)}">退出活动</button>`
        : "";
      const feedbackActions = item.feedback_targets
        .map(
          (target) => `<button class="button button-accent" type="button" data-feedback-activity="${escapeHtml(activity.id)}" data-feedback-user="${escapeHtml(target.id)}">评价 ${escapeHtml(target.display_name)}</button>`,
        )
        .join("");
      const beforeStart = Date.now() < new Date(activity.starts_at).getTime();
      const beforeEnd = Date.now() < new Date(activity.ends_at).getTime();
      const activityTools = item.membership_status === "confirmed"
        ? `<button class="button button-quiet" type="button" data-calendar-activity="${escapeHtml(activity.id)}">导入日历</button>
           ${beforeStart ? `<button class="button button-quiet" type="button" data-time-vote-activity="${escapeHtml(activity.id)}">商量改时间</button>` : ""}
           ${beforeEnd ? `<button class="button button-quiet" type="button" data-photo-activity="${escapeHtml(activity.id)}">集合照片</button>` : ""}`
        : "";
      const policy = item.can_leave
        ? `<p class="leave-hint">${escapeHtml(item.leave_policy_message)}</p>`
        : "";
      return `<article class="activity-card ${item.needs_feedback ? "needs-review" : ""}">
        <div class="activity-date-tile" aria-hidden="true">
          <strong>${new Date(activity.starts_at).getDate()}</strong>
          <span>${new Intl.DateTimeFormat("zh-CN", { month: "short" }).format(new Date(activity.starts_at))}</span>
        </div>
        <div class="activity-card-main">
          <div class="candidate-title-row">
            <div><span class="activity-role">${roleText}</span><h2>${escapeHtml(activity.title)}</h2></div>
            <span class="status-label ${statusClass}">${escapeHtml(statusText)}</span>
          </div>
          <p class="activity-meta"><b>${escapeHtml(formatDate(activity.starts_at))}</b><span>${escapeHtml(activity.location)}</span><span>${escapeHtml(activity.category)} · ${activity.participant_count}/${activity.capacity} 人</span></p>
          ${activityGenderSummary(activity)}
          ${activity.same_gender_only ? `<span class="same-gender-tag">仅同性加入</span>` : ""}
          <span class="same-gender-tag">${activity.join_policy === "approval" ? "入局需全员同意" : "可自由加入"}</span>
          ${policy}
          ${item.needs_feedback ? `<div class="review-callout"><strong>趁记忆还热，给搭子留一句真实反馈</strong><span>审核 Agent 会先检查，不会直接凭一条评价重罚。</span></div>` : ""}
          <div class="activity-actions">${feedbackActions}${activityTools}${leaveAction}</div>
        </div>
      </article>`;
    })
    .join("");
}

function openLeaveDialog(activityId) {
  const item = state.activities.find((entry) => entry.activity.id === activityId);
  if (!item) return;
  state.pendingLeave = item;
  elements.leaveDialogTitle.textContent = `退出“${item.activity.title}”？`;
  elements.leaveDialogCopy.textContent = item.leave_policy_message;
  elements.leavePenaltyCard.classList.toggle("is-zero", item.leave_penalty === 0);
  elements.leavePenaltyCard.innerHTML = item.leave_penalty
    ? `<strong>-${item.leave_penalty}</strong><span>搭子信用分</span>`
    : `<strong>0</strong><span>现在退出不扣分</span>`;
  elements.leaveDialog.showModal();
}

async function confirmLeaveActivity() {
  if (!state.pendingLeave) return;
  setButtonLoading(elements.confirmLeaveButton, true, "正在退出…");
  try {
    const result = await api(`/activities/${state.pendingLeave.activity.id}/leave`, {
      method: "POST",
      body: JSON.stringify({ confirm_penalty: true }),
    });
    elements.leaveDialog.close();
    state.pendingLeave = null;
    showToast(result.message);
    await Promise.all([loadCurrentUser(), loadActivities()]);
  } catch (error) {
    showToast(error.message);
  } finally {
    setButtonLoading(elements.confirmLeaveButton, false);
  }
}

async function importActivityCalendar(activityId) {
  try {
    const blob = await apiBlob(`/activities/${activityId}/calendar.ics`);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "搭子活动.ics";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast("日历文件已生成，打开它就能加入手机日历");
  } catch (error) {
    showToast(error.message);
  }
}

async function openTimeVoteDialog(activityId) {
  const item = state.activities.find((entry) => entry.activity.id === activityId);
  if (!item) return;
  state.activeActivity = item;
  elements.timeVoteForm.reset();
  hideInlineError(elements.timeVoteError);
  elements.timeVoteForm.elements.activity_id.value = activityId;
  elements.timeVoteForm.elements.starts_at.value = toLocalInputValue(new Date(item.activity.starts_at));
  elements.timeVoteForm.elements.ends_at.value = toLocalInputValue(new Date(item.activity.ends_at));
  elements.timeVoteDialogTitle.textContent = `“${item.activity.title}”改到几点？`;
  elements.timeVoteList.innerHTML = `<div class="empty-list">正在看看大家的方案…</div>`;
  elements.timeVoteDialog.showModal();
  await loadTimeVotes(activityId);
}

async function loadTimeVotes(activityId) {
  try {
    state.timeVotes = await api(`/activities/${activityId}/time-votes`);
    renderTimeVotes();
  } catch (error) {
    elements.timeVoteList.innerHTML = `<div class="empty-list">${escapeHtml(error.message)}</div>`;
  }
}

function renderTimeVotes() {
  elements.timeVoteCount.textContent = `${state.timeVotes.length} 项`;
  if (!state.timeVotes.length) {
    elements.timeVoteList.innerHTML = `<div class="empty-list">还没有改期方案，你可以提出第一个。</div>`;
    return;
  }
  const statusLabels = {
    pending: "等待表态",
    approved: "已通过",
    rejected: "未通过",
    superseded: "已有其他方案通过",
  };
  elements.timeVoteList.innerHTML = state.timeVotes
    .map((vote) => {
      const isMine = vote.proposer.id === state.user.id;
      const canRespond = vote.status === "pending" && !isMine && !vote.my_decision;
      const progress = vote.required_approvals === 0
        ? "无需等待其他人"
        : `已同意 ${vote.approvals}/${vote.required_approvals}`;
      return `<article class="time-vote-card">
        <div><strong>${escapeHtml(vote.proposer.display_name)} 提议</strong><span class="status-label ${vote.status === "approved" ? "is-success" : ""}">${escapeHtml(statusLabels[vote.status] || vote.status)}</span></div>
        <p>${escapeHtml(formatDate(vote.proposed_starts_at))} — ${escapeHtml(formatDate(vote.proposed_ends_at))}</p>
        <small>${escapeHtml(progress)}${vote.my_decision ? ` · 你已${vote.my_decision === "approved" ? "同意" : "不同意"}` : ""}</small>
        ${canRespond ? `<div class="activity-actions"><button class="button button-quiet" type="button" data-vote-id="${escapeHtml(vote.id)}" data-vote-decision="rejected">不同意</button><button class="button button-primary" type="button" data-vote-id="${escapeHtml(vote.id)}" data-vote-decision="approved">同意改期</button></div>` : ""}
      </article>`;
    })
    .join("");
}

async function loadApplications() {
  const upcoming = state.activities.filter((item) => item.membership_status === "confirmed" && item.activity.status === "open" && new Date(item.activity.starts_at).getTime() > Date.now());
  const results = await Promise.allSettled(upcoming.map((item) => api(`/activities/${item.activity.id}/applications`)));
  state.applications = results.flatMap((result, index) => result.status === "fulfilled"
    ? result.value.map((application) => ({ ...application, activityTitle: upcoming[index].activity.title })) : []);
  elements.applicationsSection.classList.toggle("is-hidden", state.applications.length === 0);
  elements.applicationsList.innerHTML = state.applications.map((application) => `<article class="list-card">
    <div><strong>${escapeHtml(application.activityTitle)}</strong>
      <p><button class="button button-quiet" type="button" data-user-profile="${escapeHtml(application.applicant.id)}">查看 ${escapeHtml(application.applicant.display_name)} 的档案</button></p>
      <small>已有 ${application.approvals}/${application.required_approvals} 位同意；必须全员同意才会加入。</small></div>
    ${application.my_decision
      ? `<span class="status-label is-success">你已同意，等其他人</span>`
      : `<div class="list-card-actions"><button class="button button-quiet" data-application-id="${escapeHtml(application.id)}" data-application-decision="rejected">不同意</button>
         <button class="button button-primary" data-application-id="${escapeHtml(application.id)}" data-application-decision="approved">同意加入</button></div>`}
  </article>`).join("");
}

async function respondApplication(button) {
  const application = state.applications.find((item) => item.id === button.dataset.applicationId);
  if (!application) return;
  setButtonLoading(button, true, "正在表态…");
  try {
    await api(`/activities/${application.activity_id}/applications/${application.id}/respond`, {
      method: "POST", body: JSON.stringify({ decision: button.dataset.applicationDecision }),
    });
    showToast(button.dataset.applicationDecision === "approved" ? "已同意，等其余成员表态" : "已拒绝申请");
    await Promise.all([loadApplications(), loadSquare(false), loadNotifications()]);
  } catch (error) { showToast(error.message); setButtonLoading(button, false); }
}

async function loadNotifications() {
  if (!state.token) return;
  try {
    const notifications = await api("/notifications");
    const unread = notifications.filter((item) => !item.read_at).length;
    elements.notificationBadge.textContent = String(unread);
    elements.notificationBadge.classList.toggle("is-hidden", unread === 0);
    elements.notificationsList.innerHTML = notifications.length ? notifications.map((item) => `<article class="notification-item ${item.read_at ? "" : "is-unread"}">
      <strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.body)}</p>
      <button type="button" class="button button-quiet" data-notification-id="${escapeHtml(item.id)}" data-notification-url="${escapeHtml(item.url)}">查看详情</button>
    </article>`).join("") : `<div class="empty-list">暂时没有新消息。下一次搭子动态会在这里出现。</div>`;
  } catch { /* A failed refresh must not interrupt the primary task. */ }
}

function isIos() { return /iPhone|iPad|iPod/i.test(navigator.userAgent); }
async function refreshPushStatus() {
  const status = document.querySelector("#push-status");
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    status.textContent = "当前浏览器不支持手机推送；站内消息仍可查看。";
    return;
  }
  if (isIos() && !window.matchMedia("(display-mode: standalone)").matches && !navigator.standalone) {
    status.textContent = "iPhone 请先在 Safari 点分享 → 添加到主屏幕，再从桌面打开并点下方按钮授权。";
    return;
  }
  status.textContent = Notification.permission === "granted" ? "手机通知已授权；邀请和活动动态将尝试送达。" : "点下方按钮授权手机通知；站内消息始终可用。";
}

function decodeVapidKey(value) {
  const padded = (value + "=".repeat((4 - value.length % 4) % 4)).replaceAll("-", "+").replaceAll("_", "/");
  return Uint8Array.from(atob(padded), (letter) => letter.charCodeAt(0));
}

async function enablePush() {
  const button = document.querySelector("#enable-push-button");
  if (isIos() && !window.matchMedia("(display-mode: standalone)").matches && !navigator.standalone) {
    showToast("请用 Safari 添加到主屏幕，然后从桌面打开搭子局");
    return;
  }
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    showToast("当前浏览器不支持推送，请使用站内消息");
    return;
  }
  setButtonLoading(button, true, "正在连接通知…");
  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("暂未获得通知权限，可在系统设置中重新允许");
    const registration = await navigator.serviceWorker.ready;
    const { public_key: publicKey } = await api("/push/public-key");
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: decodeVapidKey(publicKey),
    });
    const json = subscription.toJSON();
    await api("/push/subscriptions", {
      method: "POST", body: JSON.stringify({endpoint: json.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth}),
    });
    showToast("手机通知已打开！");
    await refreshPushStatus();
  } catch (error) { showToast(error.message); }
  finally { setButtonLoading(button, false); }
}

async function checkAndroidRelease() {
  if (!/Android/i.test(navigator.userAgent)) return;
  try {
    const response = await fetch("/api/v1/app/version", {cache: "no-store"});
    if (!response.ok) return;
    const release = await response.json();
    if (!release.available) return;
    document.querySelectorAll("#android-download-link, #profile-apk-link").forEach((link) => {
      link.href = release.download_url;
      link.classList.remove("is-hidden");
    });
    const installedVersion = Number(localStorage.getItem("dazi_android_app_version") || 0);
    if (installedVersion && release.version_code > installedVersion) {
      document.querySelector("#profile-apk-link").textContent = `有新版 ${release.version_name}，点此下载安装`;
      if (sessionStorage.getItem("dazi_android_notice") !== String(release.version_code)) {
        showToast("安卓应用有新版本，到我的画像下载更新");
        sessionStorage.setItem("dazi_android_notice", String(release.version_code));
      }
    }
  } catch { /* Download link remains hidden until a release is available. */ }
}

async function submitTimeVote(event) {
  event.preventDefault();
  hideInlineError(elements.timeVoteError);
  const button = event.submitter;
  const form = new FormData(elements.timeVoteForm);
  const activityId = form.get("activity_id");
  setButtonLoading(button, true, "正在发起…");
  try {
    await api(`/activities/${activityId}/time-votes`, {
      method: "POST",
      body: JSON.stringify({
        starts_at: new Date(form.get("starts_at")).toISOString(),
        ends_at: new Date(form.get("ends_at")).toISOString(),
      }),
    });
    showToast("改期方案已发出，等其他搭子表态");
    await Promise.all([loadTimeVotes(activityId), loadActivities(true)]);
  } catch (error) {
    showInlineError(elements.timeVoteError, error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function respondTimeVote(button) {
  const activityId = state.activeActivity?.activity.id;
  if (!activityId) return;
  setButtonLoading(button, true, button.dataset.voteDecision === "approved" ? "正在同意…" : "正在提交…");
  try {
    const vote = await api(
      `/activities/${activityId}/time-votes/${button.dataset.voteId}/respond`,
      { method: "POST", body: JSON.stringify({ decision: button.dataset.voteDecision }) },
    );
    showToast(vote.status === "approved" ? "全员同意，活动时间已经更新" : "你的选择已记录");
    await Promise.all([loadTimeVotes(activityId), loadActivities(true)]);
  } catch (error) {
    showToast(error.message);
    setButtonLoading(button, false);
  }
}

function revokePhotoUrls() {
  state.photoObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  state.photoObjectUrls = [];
}

async function openPhotoDialog(activityId) {
  const item = state.activities.find((entry) => entry.activity.id === activityId);
  if (!item) return;
  state.activeActivity = item;
  elements.photoForm.reset();
  hideInlineError(elements.photoError);
  elements.photoForm.elements.activity_id.value = activityId;
  elements.photoDialogTitle.textContent = `“${item.activity.title}”集合照片`;
  const opensAt = new Date(item.activity.starts_at).getTime() - 15 * 60 * 1000;
  const uploadButton = elements.photoForm.querySelector("button[type='submit']");
  const tooEarly = Date.now() < opensAt;
  elements.photoForm.elements.photo.disabled = tooEarly;
  uploadButton.disabled = tooEarly;
  if (tooEarly) {
    showInlineError(elements.photoError, `上传入口将在 ${formatDate(new Date(opensAt).toISOString())} 开放。`);
  }
  elements.photoList.innerHTML = `<div class="empty-list">正在加载搭子们看到的照片…</div>`;
  elements.photoDialog.showModal();
  await loadActivityPhotos(activityId);
}

async function loadActivityPhotos(activityId) {
  revokePhotoUrls();
  try {
    const photos = await api(`/activities/${activityId}/photos`);
    if (!photos.length) {
      elements.photoList.innerHTML = `<div class="empty-list">还没有照片。接近集合时间时，拍一张周围环境给搭子看吧。</div>`;
      return;
    }
    const cards = await Promise.all(
      photos.map(async (photo) => {
        const blob = await apiBlob(photo.content_url.replace(API_ROOT, ""));
        const url = URL.createObjectURL(blob);
        state.photoObjectUrls.push(url);
        return `<figure class="activity-photo"><img src="${url}" alt="${escapeHtml(photo.uploader.display_name)} 上传的集合环境照片" /><figcaption>${escapeHtml(photo.uploader.display_name)} · ${escapeHtml(formatDate(photo.uploaded_at))}</figcaption></figure>`;
      }),
    );
    elements.photoList.innerHTML = cards.join("");
  } catch (error) {
    elements.photoList.innerHTML = `<div class="empty-list">${escapeHtml(error.message)}</div>`;
  }
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("照片读取失败，请重新选择"));
    reader.readAsDataURL(file);
  });
}

async function uploadActivityPhoto(event) {
  event.preventDefault();
  hideInlineError(elements.photoError);
  const button = event.submitter;
  const file = elements.photoForm.elements.photo.files[0];
  const activityId = elements.photoForm.elements.activity_id.value;
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    showInlineError(elements.photoError, "单张图片不能超过 5 MB。 ");
    return;
  }
  setButtonLoading(button, true, "正在上传…");
  try {
    const dataUrl = await fileToDataUrl(file);
    await api(`/activities/${activityId}/photos`, {
      method: "POST",
      body: JSON.stringify({ data_url: dataUrl }),
    });
    elements.photoForm.elements.photo.value = "";
    showToast("照片已同步给这场活动的搭子");
    await loadActivityPhotos(activityId);
  } catch (error) {
    showInlineError(elements.photoError, error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function openFeedbackDialog(activityId, userId) {
  const item = state.activities.find((entry) => entry.activity.id === activityId);
  const target = item?.feedback_targets.find((user) => user.id === userId);
  if (!item || !target) return;
  elements.feedbackForm.reset();
  hideInlineError(elements.feedbackError);
  elements.feedbackForm.elements.activity_id.value = activityId;
  elements.feedbackForm.elements.reviewee_id.value = userId;
  elements.feedbackForm.elements.skill_name.value = item.activity.category || "";
  elements.feedbackForm.elements.skill_level.value = "3";
  updateLevelOutput(elements.feedbackForm.elements.skill_level);
  toggleFeedbackSkillFields(false);
  syncAttendanceFacts();
  elements.feedbackDialogTitle.textContent = `这次和 ${target.display_name} 搭得怎么样？`;
  elements.feedbackDialog.showModal();
}

function syncAttendanceFacts() {
  const noShow = elements.feedbackForm.elements.attendance.value === "no_show";
  elements.feedbackForm.querySelectorAll(
    'input[name="incident_tags"][value="punctual"], input[name="incident_tags"][value="late"]',
  ).forEach((input) => {
    input.disabled = noShow;
    if (noShow) input.checked = false;
  });
}

async function submitFeedback(event) {
  event.preventDefault();
  hideInlineError(elements.feedbackError);
  const button = event.submitter;
  const form = new FormData(elements.feedbackForm);
  const payload = {
    activity_id: form.get("activity_id"),
    reviewee_id: form.get("reviewee_id"),
    attendance: form.get("attendance"),
    rating: Number(form.get("rating")),
    comment: String(form.get("comment") || "").trim() || null,
    skill_name: form.get("evaluate_skill")
      ? String(form.get("skill_name") || "").trim() || null
      : null,
    skill_level: form.get("evaluate_skill") ? Number(form.get("skill_level")) : null,
    personality_tags: form.getAll("personality_tags"),
    incident_tags: form.getAll("incident_tags"),
  };
  setButtonLoading(button, true, "Agent 正在审核…");
  try {
    const result = await api("/feedback", { method: "POST", body: JSON.stringify(payload) });
    elements.feedbackDialog.close();
    showToast(result.requires_peer_review ? "已提交，正在等待同场第三人复核" : result.ai_summary);
    await Promise.all([loadCurrentUser(), loadActivities()]);
  } catch (error) {
    showInlineError(elements.feedbackError, error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function renderPeerReviewTasks() {
  elements.peerReviewSection.classList.toggle("is-hidden", state.peerTasks.length === 0);
  elements.peerTaskCount.textContent = `${state.peerTasks.length} 条`;
  elements.peerReviewList.innerHTML = state.peerTasks
    .map(
      (task) => `<article class="peer-task-card">
        <div><span class="activity-role">${escapeHtml(task.activity.title)}</span><h3>${escapeHtml(task.author.display_name)} 对 ${escapeHtml(task.subject.display_name)} 的评价</h3></div>
        <blockquote>“${escapeHtml(task.comment || "没有填写文字说明") }”</blockquote>
        ${task.skill_name ? `<p class="peer-skill-note">水平评价：${escapeHtml(task.skill_name)} · ${escapeHtml(LEVEL_LABELS[task.skill_level] || task.skill_level)}</p>` : ""}
        <p>${escapeHtml(task.ai_summary)}</p>
        <button class="button button-primary" type="button" data-peer-task="${escapeHtml(task.feedback_id)}">我来补充现场情况</button>
      </article>`,
    )
    .join("");
}

function openPeerReviewDialog(feedbackId) {
  const task = state.peerTasks.find((item) => item.feedback_id === feedbackId);
  if (!task) return;
  elements.peerReviewForm.reset();
  hideInlineError(elements.peerReviewError);
  elements.peerReviewForm.elements.feedback_id.value = feedbackId;
  elements.peerDialogTitle.textContent = `${task.activity.title} · 帮忙还原情况`;
  const skillCopy = task.skill_name
    ? `同时认为其“${task.skill_name}”水平为${LEVEL_LABELS[task.skill_level] || task.skill_level}。`
    : "";
  elements.peerDialogContext.textContent = `${task.author.display_name} 给 ${task.subject.display_name} 打了 ${task.rating} 分。${skillCopy}${task.ai_summary}`;
  elements.peerReviewDialog.showModal();
}

async function submitPeerReview(event) {
  event.preventDefault();
  hideInlineError(elements.peerReviewError);
  const button = event.submitter;
  const form = new FormData(elements.peerReviewForm);
  const feedbackId = form.get("feedback_id");
  const payload = {
    verdict: form.get("verdict"),
    true_parts: String(form.get("true_parts") || "").trim() || null,
    false_parts: String(form.get("false_parts") || "").trim() || null,
    comment: String(form.get("comment") || "").trim() || null,
  };
  setButtonLoading(button, true, "Agent 正在综合判断…");
  try {
    const result = await api(`/feedback/${feedbackId}/peer-review`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    elements.peerReviewDialog.close();
    showToast(result.ai_summary);
    await Promise.all([loadCurrentUser(), loadActivities()]);
  } catch (error) {
    showInlineError(elements.peerReviewError, error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function profileChipList(items, emptyCopy = "暂未填写") {
  if (!items.length) return `<p class="profile-empty-copy">${escapeHtml(emptyCopy)}</p>`;
  return `<div class="profile-chip-list">${items
    .map((item) => `<span>${escapeHtml(item)}</span>`)
    .join("")}</div>`;
}

function renderUserProfilePage(page) {
  const user = page.user;
  const system = page.system_profile;
  const gradeLabels = ["", "大一", "大二", "大三", "大四", "研一", "研二", "研三", "博士生"];
  const styleLabels = { quiet: "偏安静", balanced: "都可以", outgoing: "偏外向" };
  const attendanceLabels = {
    attended: "正常参加",
    cancelled_early: "没有出现",
    late_cancel: "没有出现",
    no_show: "没有出现",
  };
  const incidentLabels = {
    punctual: "准时到场",
    helpful: "乐于帮忙",
    clear_communication: "沟通清楚",
    late: "有迟到",
    cancelled: "临时取消",
    no_show: "没有出现",
    unsafe_behavior: "存在安全风险",
    skill_level_mismatch: "爱好水平存在争议",
    suspected_smurfing: "疑似高手低报",
  };
  const skills = (user.hobby_skills || []).map(
    (skill) => `${skill.name} · ${levelLabel(skill.level)}`,
  );
  const activitySignals = (system.activity_signals || []).map(
    (item) => `${item.category} ${item.count} 次`,
  );
  const personalitySignals = (system.personality_signals || []).map(
    (item) => `${item.label} · ${item.count} 人次反馈`,
  );
  const attendanceSignals = Object.entries(system.attendance_signals || {})
    .filter(([, count]) => count)
    .map(([key, count]) => `${attendanceLabels[key] || key} ${count} 次`);
  const incidentSignals = Object.entries(system.incident_signals || {})
    .filter(([, count]) => count)
    .map(([key, count]) => `${incidentLabels[key] || key} ${count} 次`);
  const systemMarks = (system.skill_marks || [])
    .map((mark) => `<div class="profile-system-alert"><strong>${escapeHtml(mark.name)}</strong><span>${escapeHtml(mark.label)} · ${escapeHtml(mark.feedback_count)} 条有效反馈</span></div>`)
    .join("");
  elements.userProfileContent.innerHTML = `
    <header class="profile-dialog-hero">
      <div>
        <div class="profile-identity-row"><h2>${escapeHtml(user.display_name)}</h2>${genderBadge(user.gender)}</div>
        <p>${escapeHtml([user.department, gradeLabels[user.grade_year], user.campus].filter(Boolean).join(" · ") || user.university)}</p>
      </div>
      <div class="profile-credit-badge"><span>搭子信用</span><strong>${escapeHtml(user.credit_score)}</strong></div>
    </header>
    <section class="profile-page-section">
      <div class="profile-section-heading"><span>本人填写</span><h3>TA 介绍的自己</h3></div>
      <p class="profile-bio">${escapeHtml(user.bio || "TA 还没有写自我介绍。")}</p>
      <div class="profile-fact-grid">
        <div><small>兴趣</small>${profileChipList(user.interests || [])}</div>
        <div><small>爱好与特长水平</small>${profileChipList(skills)}</div>
        <div><small>常去地点</small>${profileChipList(user.preferred_locations || [])}</div>
        <div><small>相处偏好</small><p>${escapeHtml(styleLabels[user.social_style] || "未填写")} · 喜欢 ${escapeHtml(user.preferred_group_min)}–${escapeHtml(user.preferred_group_max)} 人的小组</p></div>
      </div>
    </section>
    <section class="profile-page-section system-profile-section">
      <div class="profile-section-heading"><span>系统画像</span><h3>活动与评价形成的印象</h3></div>
      <p class="profile-system-note">这些内容由活动记录和通过审核的评价自动整理，TA 不能直接修改。</p>
      <div class="profile-metrics">
        <div><strong>${escapeHtml(system.completed_activity_count)}</strong><span>已完成活动</span></div>
        <div><strong>${system.average_rating == null ? "—" : escapeHtml(system.average_rating)}</strong><span>平均评分</span></div>
        <div><strong>${escapeHtml(system.finalized_feedback_count)}</strong><span>有效评价</span></div>
      </div>
      <blockquote class="profile-system-summary">${escapeHtml(system.summary)}</blockquote>
      <div class="profile-fact-grid">
        <div><small>常参加的活动</small>${profileChipList(activitySignals, "还没有稳定记录")}</div>
        <div><small>他人印象</small>${profileChipList(personalitySignals, "评价还不够多")}</div>
        <div><small>到场记录</small>${profileChipList(attendanceSignals, "暂时没有到场反馈")}</div>
        <div><small>事实反馈</small>${profileChipList(incidentSignals, "暂时没有特别记录")}</div>
      </div>
      ${systemMarks ? `<div class="profile-system-alerts">${systemMarks}</div>` : ""}
      <p class="profile-review-integrity">评价可信记录：${escapeHtml(system.review_integrity.supported_feedback_count || 0)} 条获同场复核支持，${escapeHtml(system.review_integrity.unsupported_serious_feedback_count || 0)} 条严重评价被判定缺乏依据。</p>
    </section>`;
}

async function openUserProfile(userId) {
  elements.userProfileContent.innerHTML = `<div class="profile-dialog-loading">正在翻开这位搭子的主页…</div>`;
  if (!elements.userProfileDialog.open) elements.userProfileDialog.showModal();
  try {
    const page = await api(`/users/${userId}/profile`);
    renderUserProfilePage(page);
  } catch (error) {
    elements.userProfileContent.innerHTML = `<div class="profile-dialog-loading"><strong>主页暂时打不开</strong><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function openActivityParticipants(activityId, activityTitle = "这场活动") {
  elements.activityParticipantsTitle.textContent = `“${activityTitle || "这场活动"}”的参与者`;
  elements.activityParticipantsSummary.innerHTML = "";
  elements.activityParticipantsList.innerHTML = `<div class="profile-dialog-loading">正在读取参与者…</div>`;
  if (!elements.activityParticipantsDialog.open) {
    elements.activityParticipantsDialog.showModal();
  }
  try {
    const users = await api(`/activities/${activityId}/participants`);
    const genderCounts = { male: 0, female: 0, undisclosed: 0 };
    users.forEach((user) => {
      const key = Object.hasOwn(genderCounts, user.gender) ? user.gender : "undisclosed";
      genderCounts[key] += 1;
    });
    elements.activityParticipantsSummary.innerHTML = activityGenderSummary(
      { gender_counts: genderCounts },
      false,
    );
    elements.activityParticipantsList.innerHTML = users.length
      ? users.map((user) => `<article class="participant-profile-card">
          ${personProfileTag(user, true)}
          <p>${escapeHtml([user.department, user.grade_year ? `${user.grade_year} 年级` : null].filter(Boolean).join(" · ") || "暂未填写院系与年级")}</p>
          ${profileChipList((user.hobby_skills || []).slice(0, 3).map((skill) => `${skill.name} · ${levelLabel(skill.level)}`), "还没有填写爱好水平")}
        </article>`).join("")
      : `<div class="profile-dialog-loading">暂时还没有参与者。</div>`;
  } catch (error) {
    elements.activityParticipantsList.innerHTML = `<div class="profile-dialog-loading"><strong>参与者列表暂时打不开</strong><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function populateProfileForm() {
  if (!state.user) return;
  window.clearTimeout(state.profileSaveTimer);
  const form = elements.profileForm.elements;
  form.display_name.value = state.user.display_name || "";
  form.campus.value = ["南京", "江阴"].includes(state.user.campus) ? state.user.campus : "";
  elements.campusMigrationNote.classList.toggle("is-hidden", Boolean(form.campus.value));
  elements.campusMigrationNote.textContent = form.campus.value
    ? ""
    : `旧资料${state.user.campus ? `“${state.user.campus}”` : ""}无法确定属于哪个校区，请选择南京或江阴；选择后会自动保存。`;
  form.department.value = state.user.department || "";
  form.grade_year.value = state.user.grade_year || "";
  form.gender.value = state.user.gender || "undisclosed";
  form.bio.value = state.user.bio || "";
  form.interests.value = (state.user.interests || []).join("，");
  form.preferred_locations.value = (state.user.preferred_locations || []).join("，");
  form.social_style.value = state.user.social_style || "balanced";
  form.preferred_group_min.value = state.user.preferred_group_min || 2;
  form.preferred_group_max.value = state.user.preferred_group_max || 6;
  updateGroupRangeOutputs();
  elements.profileCredit.textContent = String(state.user.credit_score);
  renderHobbySkillEditor(state.user.hobby_skills || []);
  renderSkillMarks(state.user.skill_marks || []);
  renderAiSummary();
  elements.profileStatus.textContent = "修改后会自动保存";
  elements.profileRetry.classList.add("is-hidden");
  state.profileSavedSnapshot = JSON.stringify(profilePayload());
  syncSameGenderChoice();
}

function updateGroupRangeOutputs(changed = null) {
  const form = elements.profileForm.elements;
  let min = Number(form.preferred_group_min.value);
  let max = Number(form.preferred_group_max.value);
  if (min > max) {
    if (changed === "preferred_group_max") min = max;
    else max = min;
    form.preferred_group_min.value = String(min);
    form.preferred_group_max.value = String(max);
  }
  document.querySelector("#group-min-output").textContent = `${min} 人`;
  document.querySelector("#group-max-output").textContent = `${max} 人`;
  form.preferred_group_min.setAttribute("aria-valuetext", `最少 ${min} 人`);
  form.preferred_group_max.setAttribute("aria-valuetext", `最多 ${max} 人`);
}

function syncSameGenderChoice() {
  const checkbox = elements.matchForm.elements.same_gender_only;
  const knownGender = ["male", "female"].includes(state.user?.gender);
  checkbox.disabled = !knownGender;
  if (!knownGender) checkbox.checked = false;
  checkbox.closest(".same-gender-choice").classList.toggle("is-unavailable", !knownGender);
}

function levelLabel(level) {
  return LEVEL_LABELS[Number(level)] || "未填写";
}

function updateLevelOutput(input) {
  if (!input) return;
  const output = input.closest(".level-control")?.querySelector("[data-level-output]");
  if (output) output.textContent = levelLabel(input.value);
  input.setAttribute("aria-valuetext", levelLabel(input.value));
}

function hobbySkillRow(skill = { name: "", level: 1 }) {
  state.skillRowCounter += 1;
  const rowId = `hobby-skill-${state.skillRowCounter}`;
  const level = Math.max(1, Math.min(5, Number(skill.level) || 1));
  return `<div class="hobby-skill-row" data-skill-row>
    <label for="${rowId}-name">爱好或特长<input id="${rowId}-name" data-skill-name type="text" maxlength="40" value="${escapeHtml(skill.name || "")}" placeholder="例如：羽毛球" /></label>
    <label class="level-control" for="${rowId}-level">
      <span>水平 <output data-level-output>${escapeHtml(levelLabel(level))}</output></span>
      <input id="${rowId}-level" data-skill-level data-level-slider type="range" min="1" max="5" value="${level}" step="1" aria-valuetext="${escapeHtml(levelLabel(level))}" />
      <span class="level-scale" aria-hidden="true"><small>小白</small><small>入门</small><small>熟练</small><small>擅长</small><small>精通</small></span>
    </label>
    <button class="skill-remove" type="button" data-remove-skill aria-label="删除${escapeHtml(skill.name || "这一项")}">×</button>
  </div>`;
}

function renderHobbySkillEditor(skills) {
  if (!skills.length) {
    elements.hobbySkillList.innerHTML = `<p class="skill-empty">还没填写。可以从最常参加的一项开始，水平以后随时能改。</p>`;
    return;
  }
  elements.hobbySkillList.innerHTML = skills.map(hobbySkillRow).join("");
}

function addHobbySkill(skill = { name: "", level: 1 }) {
  elements.hobbySkillList.querySelector(".skill-empty")?.remove();
  elements.hobbySkillList.insertAdjacentHTML("beforeend", hobbySkillRow(skill));
  elements.hobbySkillList.lastElementChild?.querySelector("[data-skill-name]")?.focus();
  elements.profileStatus.textContent = "填写项目名称后会自动保存";
}

function collectHobbySkills() {
  return [...elements.hobbySkillList.querySelectorAll("[data-skill-row]")]
    .map((row) => ({
      name: row.querySelector("[data-skill-name]").value.trim(),
      level: Number(row.querySelector("[data-skill-level]").value),
    }))
    .filter((skill) => skill.name);
}

function renderSkillMarks(marks) {
  elements.skillMarks.classList.toggle("is-hidden", marks.length === 0);
  elements.skillMarks.innerHTML = marks.length
    ? `<strong>系统根据多人反馈给出的提醒</strong>${marks
      .map(
        (mark) => `<div class="skill-mark ${mark.flag_type === "possible_smurfing" ? "is-smurfing" : ""}"><span>${escapeHtml(mark.name)}</span><p>${escapeHtml(mark.label)}（${escapeHtml(mark.feedback_count)} 条有效反馈）</p></div>`,
      )
      .join("")}`
    : "";
}

function toggleFeedbackSkillFields(show) {
  elements.feedbackSkillFields.classList.toggle("is-hidden", !show);
  elements.feedbackSkillFields.querySelectorAll("input").forEach((input) => {
    input.disabled = !show;
  });
}

function renderAiSummary() {
  if (!state.user) return;
  window.clearTimeout(state.summaryTimer);
  elements.aiSummaryCopy.textContent = state.user.ai_summary
    || "点击生成后，AI 会综合你的资料和已有活动反馈，写一段只给你看的总结。";
  const generatedAt = state.user.ai_summary_generated_at
    ? new Date(state.user.ai_summary_generated_at).getTime()
    : 0;
  const remaining = Math.ceil((generatedAt + 5 * 60 * 1000 - Date.now()) / 1000);
  if (remaining > 0) {
    elements.generateSummaryButton.disabled = true;
    elements.aiSummaryCooldown.textContent = `${Math.ceil(remaining / 60)} 分钟后可以再次总结`;
    state.summaryTimer = window.setTimeout(renderAiSummary, Math.min(remaining * 1000, 60_000));
  } else {
    elements.generateSummaryButton.disabled = false;
    elements.aiSummaryCooldown.textContent = "每 5 分钟可以重新总结一次";
  }
}

async function generateAiSummary() {
  setButtonLoading(elements.generateSummaryButton, true, "AI 正在整理…");
  try {
    const result = await api("/users/me/ai-summary", { method: "POST" });
    state.user.ai_summary = result.summary;
    state.user.ai_summary_generated_at = result.generated_at;
    elements.aiSummaryCopy.textContent = result.summary;
    showToast("新的综合形象已经写好");
  } catch (error) {
    showToast(error.message);
  } finally {
    setButtonLoading(elements.generateSummaryButton, false);
    renderAiSummary();
  }
}

function splitList(value) {
  return String(value || "")
    .split(/[，,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function profilePayload() {
  const form = new FormData(elements.profileForm);
  return {
    display_name: String(form.get("display_name")).trim(),
    campus: String(form.get("campus") || "").trim() || null,
    department: String(form.get("department") || "").trim() || null,
    grade_year: form.get("grade_year") ? Number(form.get("grade_year")) : null,
    gender: form.get("gender"),
    bio: String(form.get("bio") || "").trim() || null,
    interests: splitList(form.get("interests")),
    hobby_skills: collectHobbySkills(),
    preferred_locations: splitList(form.get("preferred_locations")),
    social_style: form.get("social_style"),
    preferred_group_min: Number(form.get("preferred_group_min")),
    preferred_group_max: Number(form.get("preferred_group_max")),
  };
}

function queueProfileSave(immediate = false) {
  if (!state.token) return;
  state.profileEditRevision += 1;
  window.clearTimeout(state.profileSaveTimer);
  elements.profileRetry.classList.add("is-hidden");
  elements.profileStatus.textContent = "修改待保存…";
  state.profileSaveTimer = window.setTimeout(flushProfileSave, immediate ? 0 : 700);
}

async function flushProfileSave() {
  if (!state.token || state.profileSaveInFlight) return;
  window.clearTimeout(state.profileSaveTimer);
  if (!elements.profileForm.checkValidity()) {
    elements.profileStatus.textContent = "请先填写昵称并选择校区，完成后会自动保存";
    return;
  }
  const payload = profilePayload();
  const snapshot = JSON.stringify(payload);
  if (snapshot === state.profileSavedSnapshot) {
    elements.profileStatus.textContent = "画像已自动保存";
    return;
  }
  const savedRevision = state.profileEditRevision;
  const token = state.token;
  state.profileSaveInFlight = true;
  elements.profileStatus.textContent = "正在自动保存…";
  try {
    const savedUser = await api("/users/me", {
      method: "PATCH",
      body: snapshot,
      keepalive: true,
    });
    if (state.token !== token) return;
    state.user = savedUser;
    state.profileSavedSnapshot = snapshot;
    elements.accountName.textContent = state.user.display_name;
    elements.profileCredit.textContent = String(state.user.credit_score);
    renderSkillMarks(state.user.skill_marks || []);
    syncSameGenderChoice();
    updatePeopleNeededOutput();
    elements.profileStatus.textContent = "画像已自动保存";
  } catch (error) {
    if (state.token === token) {
      elements.profileStatus.textContent = `保存失败：${error.message}`;
      elements.profileRetry.classList.remove("is-hidden");
    }
  } finally {
    state.profileSaveInFlight = false;
    if (state.token === token && state.profileEditRevision > savedRevision) {
      queueProfileSave();
    }
  }
}

function bindEvents() {
  if (!["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname)) {
    document.querySelector(".demo-login")?.remove();
  }
  document.querySelector("#login-tab").addEventListener("click", () => switchAuthPanel("login"));
  document.querySelector("#register-tab").addEventListener("click", () => switchAuthPanel("register"));
  elements.loginForm.addEventListener("submit", handleLogin);
  elements.registerForm.addEventListener("submit", handleRegister);
  document.querySelector("#register-next").addEventListener("click", continueRegistration);
  document.querySelector("#register-back").addEventListener("click", () => showRegisterStep("account"));
  document.querySelector("#logout-button").addEventListener("click", () => logout());
  elements.notificationButton.addEventListener("click", async () => {
    await loadNotifications();
    elements.notificationsDialog.showModal();
  });
  elements.notificationsList.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-notification-id]");
    if (!button) return;
    try { await api(`/notifications/${button.dataset.notificationId}/read`, {method: "POST"}); }
    catch (error) { showToast(error.message); return; }
    elements.notificationsDialog.close();
    await loadNotifications();
    const tab = new URL(button.dataset.notificationUrl, window.location.origin).searchParams.get("tab");
    if (tab) switchTab(tab);
  });
  elements.applicationsList.addEventListener("click", (event) => {
    const profileButton = event.target.closest("[data-user-profile]");
    if (profileButton) { openUserProfile(profileButton.dataset.userProfile); return; }
    const decisionButton = event.target.closest("[data-application-id]");
    if (decisionButton) respondApplication(decisionButton);
  });
  document.querySelector("#enable-push-button").addEventListener("click", enablePush);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.token) loadNotifications();
    if (!document.hidden) checkAndroidRelease();
  });

  document.querySelectorAll(".demo-account").forEach((button) => {
    button.addEventListener("click", () => {
      elements.loginForm.elements.email.value = button.dataset.email;
      elements.loginForm.elements.password.value = "demo-password-123";
      elements.loginForm.elements.password.focus();
    });
  });

  document.querySelectorAll(".nav-button[data-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  document.querySelectorAll("[data-go-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.goTab));
  });
  document.querySelectorAll("[data-refresh]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.refresh === "invitations") loadInvitations();
      if (button.dataset.refresh === "activities") loadActivities();
      if (button.dataset.refresh === "square") loadSquare(false);
    });
  });

  elements.squareFilterForm.addEventListener("submit", (event) => {
    event.preventDefault();
    loadSquare(false);
  });
  elements.squareLoadMore.addEventListener("click", () => loadSquare(true));
  elements.squareList.addEventListener("click", (event) => {
    const participantButton = event.target.closest("[data-activity-participants]");
    if (participantButton) {
      openActivityParticipants(
        participantButton.dataset.activityParticipants,
        participantButton.dataset.activityTitle,
      );
      return;
    }
    const button = event.target.closest("[data-join-square]");
    if (button) joinSquareActivity(button);
  });

  elements.matchForm.addEventListener("submit", handleMatch);
  elements.matchForm.querySelector("#category-choices").addEventListener("change", syncCategoryChoice);
  elements.matchForm.elements.people_needed.addEventListener("input", updatePeopleNeededOutput);
  elements.confirmLocation.addEventListener("input", () => {
    if (elements.confirmLocation.value.trim()) elements.confirmLocationError.classList.add("is-hidden");
  });
  document.querySelector("#new-match-button").addEventListener("click", resetMatchResult);
  document.querySelector("#start-another-button").addEventListener("click", resetMatchResult);
  elements.candidateList.addEventListener("click", handleCandidateSelection);
  elements.confirmButton.addEventListener("click", () => confirmMatch(false));
  document.querySelector("#create-solo-button").addEventListener("click", () => confirmMatch(true));
  elements.invitationList.addEventListener("click", respondInvitation);
  elements.activityList.addEventListener("click", (event) => {
    const participantButton = event.target.closest("[data-activity-participants]");
    if (participantButton) {
      openActivityParticipants(
        participantButton.dataset.activityParticipants,
        participantButton.dataset.activityTitle,
      );
      return;
    }
    const leaveButton = event.target.closest("[data-leave-activity]");
    if (leaveButton) openLeaveDialog(leaveButton.dataset.leaveActivity);
    const feedbackButton = event.target.closest("[data-feedback-activity]");
    if (feedbackButton) {
      openFeedbackDialog(feedbackButton.dataset.feedbackActivity, feedbackButton.dataset.feedbackUser);
    }
    const calendarButton = event.target.closest("[data-calendar-activity]");
    if (calendarButton) importActivityCalendar(calendarButton.dataset.calendarActivity);
    const timeVoteButton = event.target.closest("[data-time-vote-activity]");
    if (timeVoteButton) openTimeVoteDialog(timeVoteButton.dataset.timeVoteActivity);
    const photoButton = event.target.closest("[data-photo-activity]");
    if (photoButton) openPhotoDialog(photoButton.dataset.photoActivity);
  });
  elements.confirmLeaveButton.addEventListener("click", confirmLeaveActivity);
  elements.timeVoteForm.addEventListener("submit", submitTimeVote);
  elements.timeVoteList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-vote-id]");
    if (button) respondTimeVote(button);
  });
  elements.photoForm.addEventListener("submit", uploadActivityPhoto);
  elements.feedbackForm.addEventListener("submit", submitFeedback);
  elements.peerReviewList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-peer-task]");
    if (button) openPeerReviewDialog(button.dataset.peerTask);
  });
  elements.peerReviewForm.addEventListener("submit", submitPeerReview);
  elements.profileForm.addEventListener("submit", (event) => {
    event.preventDefault();
    queueProfileSave(true);
  });
  elements.profileForm.addEventListener("input", (event) => {
    if (event.target.name === "preferred_group_min" || event.target.name === "preferred_group_max") {
      updateGroupRangeOutputs(event.target.name);
    }
    queueProfileSave();
  });
  elements.profileForm.addEventListener("change", (event) => {
    if (event.target.name === "campus") {
      elements.campusMigrationNote.classList.toggle("is-hidden", Boolean(event.target.value));
    }
    queueProfileSave(true);
  });
  elements.profileRetry.addEventListener("click", () => queueProfileSave(true));
  elements.generateSummaryButton.addEventListener("click", generateAiSummary);
  elements.userProfileDialog.addEventListener("close", () => {
    elements.userProfileContent.innerHTML = "";
  });
  elements.activityParticipantsList.addEventListener("click", (event) => {
    const profileButton = event.target.closest("[data-user-profile]");
    if (profileButton) openUserProfile(profileButton.dataset.userProfile);
  });
  elements.addHobbySkillButton.addEventListener("click", () => addHobbySkill());
  elements.hobbySkillList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-skill]");
    if (!button) return;
    button.closest("[data-skill-row]").remove();
    if (!elements.hobbySkillList.children.length) renderHobbySkillEditor([]);
    queueProfileSave(true);
  });
  elements.feedbackForm.elements.evaluate_skill.addEventListener("change", (event) => {
    toggleFeedbackSkillFields(event.target.checked);
  });
  elements.feedbackForm.elements.attendance.addEventListener("change", syncAttendanceFacts);
  document.addEventListener("input", (event) => {
    if (event.target.matches("[data-level-slider]")) updateLevelOutput(event.target);
  });

  document.querySelectorAll("[data-activity-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activityFilter = button.dataset.activityFilter;
      document.querySelectorAll("[data-activity-filter]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      renderActivities();
    });
  });

  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector(`#${button.dataset.closeDialog}`).close();
      if (button.dataset.closeDialog === "activity-photo-dialog") revokePhotoUrls();
    });
  });
  document.querySelectorAll(".tag-field input[type='checkbox']").forEach((input) => {
    input.addEventListener("change", () => {
      const group = input.name === "incident_tags"
        ? elements.feedbackForm
        : input.closest(".tag-field");
      const checked = group.querySelectorAll(`input[name='${input.name}']:checked`);
      if (checked.length > 4) {
        input.checked = false;
        showToast("每组最多选 4 个，挑最有代表性的就好");
      }
    });
  });

  syncCategoryChoice();
  updatePeopleNeededOutput();
}

async function initialize() {
  bindEvents();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  }
  const launch = new URLSearchParams(window.location.search);
  if (launch.get("source") === "android-app" && /^\d+$/.test(launch.get("version") || "")) {
    localStorage.setItem("dazi_android_app_version", launch.get("version"));
  }
  checkAndroidRelease();
  initializeDates();
  elements.confirmButton.disabled = true;
  loadAgentMode();
  if (!state.token) {
    showAuthShell();
    return;
  }
  try {
    await loadCurrentUser();
    const initialTab = launch.get("tab");
    if (initialTab && ["match", "square", "invitations", "activities", "profile"].includes(initialTab)) switchTab(initialTab);
    else if (!["南京", "江阴"].includes(state.user.campus)) switchTab("profile");
  } catch {
    logout(false);
  }
}

initialize();
