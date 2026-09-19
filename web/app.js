const API_ROOT = "/api/v1";
const TOKEN_KEY = "ligong_dazi_access_token";

const state = {
  token: localStorage.getItem(TOKEN_KEY),
  user: null,
  preview: null,
  selectedUsers: new Set(),
  selectedActivity: null,
  activities: [],
  activityFilter: "upcoming",
  pendingLeave: null,
  peerTasks: [],
  runningTimer: null,
  toastTimer: null,
};

const elements = {
  authView: document.querySelector("#auth-view"),
  appView: document.querySelector("#app-view"),
  mainNav: document.querySelector("#main-nav"),
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
  confirmButton: document.querySelector("#confirm-match-button"),
  successTitle: document.querySelector("#success-title"),
  successCopy: document.querySelector("#success-copy"),
  invitationList: document.querySelector("#invitation-list"),
  inviteBadge: document.querySelector("#invite-badge"),
  activityBadge: document.querySelector("#activity-badge"),
  activityList: document.querySelector("#activity-list"),
  peerReviewSection: document.querySelector("#peer-review-section"),
  peerReviewList: document.querySelector("#peer-review-list"),
  peerTaskCount: document.querySelector("#peer-task-count"),
  profileForm: document.querySelector("#profile-form"),
  profileCredit: document.querySelector("#profile-credit"),
  profileStatus: document.querySelector("#profile-status"),
  agentModePill: document.querySelector("#agent-mode-pill"),
  leaveDialog: document.querySelector("#leave-dialog"),
  leaveDialogTitle: document.querySelector("#leave-dialog-title"),
  leaveDialogCopy: document.querySelector("#leave-dialog-copy"),
  leavePenaltyCard: document.querySelector("#leave-penalty-card"),
  confirmLeaveButton: document.querySelector("#confirm-leave-button"),
  feedbackDialog: document.querySelector("#feedback-dialog"),
  feedbackForm: document.querySelector("#feedback-form"),
  feedbackDialogTitle: document.querySelector("#feedback-dialog-title"),
  feedbackError: document.querySelector("#feedback-error"),
  peerReviewDialog: document.querySelector("#peer-review-dialog"),
  peerReviewForm: document.querySelector("#peer-review-form"),
  peerDialogTitle: document.querySelector("#peer-dialog-title"),
  peerDialogContext: document.querySelector("#peer-dialog-context"),
  peerReviewError: document.querySelector("#peer-review-error"),
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
    throw new Error(formatApiError(payload, `请求失败（${response.status}）`));
  }
  return payload;
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
  if (!loginActive) showRegisterStep("account");
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
  elements.authView.classList.add("is-hidden");
  elements.appView.classList.remove("is-hidden");
  elements.mainNav.classList.remove("is-hidden");
  elements.accountArea.classList.remove("is-hidden");
  elements.accountName.textContent = state.user.display_name;
  populateProfileForm();
  loadInvitations(true);
  loadActivities(true);
}

function showAuthShell() {
  elements.authView.classList.remove("is-hidden");
  elements.appView.classList.add("is-hidden");
  elements.mainNav.classList.add("is-hidden");
  elements.accountArea.classList.add("is-hidden");
}

function logout(showMessage = true) {
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
    switchTab("match");
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
        interests,
        preferred_locations: splitList(form.get("preferred_locations")),
        social_style: form.get("social_style"),
      }),
    });
    state.token = result.access_token;
    localStorage.setItem(TOKEN_KEY, state.token);
    await loadCurrentUser();
    switchTab("match");
    showToast("画像已就位，去发起第一场搭子局吧");
  } catch (error) {
    showInlineError(elements.authError, error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function switchTab(tabName) {
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
  if (tabName === "profile") populateProfileForm();
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
  const custom = String(form.get("custom_category") || "").trim();
  return custom || form.get("category");
}

async function handleMatch(event) {
  event.preventDefault();
  hideInlineError(elements.matchError);
  state.selectedUsers.clear();
  state.selectedActivity = null;
  setResultView("running");
  startRunningProgress();
  setButtonLoading(elements.matchSubmit, true, "Agent 正在匹配…");
  const form = new FormData(elements.matchForm);
  const payload = {
    category: getCategory(form),
    starts_at: new Date(form.get("starts_at")).toISOString(),
    ends_at: new Date(form.get("ends_at")).toISOString(),
    location: String(form.get("location")).trim(),
    people_needed: Number(form.get("people_needed")),
    title: String(form.get("title") || "").trim() || null,
    personal_requirement: String(form.get("personal_requirement") || "").trim() || null,
  };
  try {
    const preview = await api("/matches/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const run = await api(`/agent-runs/${preview.agent_run_id}`).catch(() => null);
    finishRunningProgress();
    state.preview = { ...preview, requestedCount: payload.people_needed, run };
    renderMatchResult();
    window.setTimeout(() => setResultView("content"), 180);
  } catch (error) {
    window.clearInterval(state.runningTimer);
    setResultView("empty");
    showInlineError(elements.matchError, error.message);
  } finally {
    setButtonLoading(elements.matchSubmit, false);
  }
}

function renderAgentTrace() {
  const toolLabels = {
    search_activities: ["查询已有活动", "检查同类活动与空余名额"],
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
    meta: `${formatDate(activity.starts_at)} · ${activity.location} · ${activity.participant_count}/${activity.capacity} 人`,
  };
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
      return `
        <label class="candidate-card" data-type="${candidate.candidate_type}" data-id="${escapeHtml(candidate.candidate_id)}">
          <input type="${inputType}" name="${inputName}" value="${escapeHtml(candidate.candidate_id)}" />
          <div class="candidate-main">
            <div class="candidate-title-row">
              <strong>${escapeHtml(copy.title)}</strong>
              <span class="candidate-kind">${escapeHtml(copy.kind)}</span>
            </div>
            <p class="candidate-meta">${escapeHtml(copy.meta)}</p>
            <div class="candidate-reasons">${reasons}</div>
          </div>
          <div class="candidate-score">${escapeHtml(candidate.score)}<small>匹配分</small></div>
        </label>`;
    })
    .join("");
}

function renderMatchResult() {
  elements.resultSummary.textContent = state.preview.summary;
  elements.agentModePill.lastChild.textContent =
    state.preview.agent_mode === "openai_compatible" ? " 模型 Agent" : " 规则 Agent";
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
  if (state.selectedActivity) {
    elements.selectionSummary.textContent = "已选择加入 1 个已有活动";
    elements.confirmButton.textContent = "确认加入活动";
    elements.confirmButton.disabled = false;
  } else if (state.selectedUsers.size) {
    elements.selectionSummary.textContent = `已选择 ${state.selectedUsers.size} 位搭子`;
    elements.confirmButton.textContent = "确认创建并邀请";
    elements.confirmButton.disabled = false;
  } else {
    elements.selectionSummary.textContent = "尚未选择候选";
    elements.confirmButton.textContent = "确认并执行";
    elements.confirmButton.disabled = true;
  }
}

function handleCandidateSelection(event) {
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
      }),
    });
    elements.successTitle.textContent =
      result.status === "joined" ? "已加入活动" : createSoloActivity ? "活动已发布" : "搭子局已创建";
    elements.successCopy.textContent = createSoloActivity
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
  state.selectedUsers.clear();
  state.selectedActivity = null;
  hideInlineError(elements.matchError);
  setResultView("empty");
  elements.matchForm.querySelector("textarea[name='personal_requirement']").focus();
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
    elements.invitationList.innerHTML = `<div class="empty-list">目前没有收到邀请。发起一次匹配，或切换另一个演示账号试试。</div>`;
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
        </div>
        ${actions}
      </article>`;
    })
    .join("");
}

async function respondInvitation(event) {
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
          ${policy}
          ${item.needs_feedback ? `<div class="review-callout"><strong>趁记忆还热，给搭子留一句真实反馈</strong><span>审核 Agent 会先检查，不会直接凭一条评价重罚。</span></div>` : ""}
          <div class="activity-actions">${feedbackActions}${leaveAction}</div>
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

function openFeedbackDialog(activityId, userId) {
  const item = state.activities.find((entry) => entry.activity.id === activityId);
  const target = item?.feedback_targets.find((user) => user.id === userId);
  if (!item || !target) return;
  elements.feedbackForm.reset();
  hideInlineError(elements.feedbackError);
  elements.feedbackForm.elements.activity_id.value = activityId;
  elements.feedbackForm.elements.reviewee_id.value = userId;
  elements.feedbackDialogTitle.textContent = `这次和 ${target.display_name} 搭得怎么样？`;
  elements.feedbackDialog.showModal();
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
  elements.peerDialogContext.textContent = `${task.author.display_name} 给 ${task.subject.display_name} 打了 ${task.rating} 分。${task.ai_summary}`;
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

function populateProfileForm() {
  if (!state.user) return;
  const form = elements.profileForm.elements;
  form.display_name.value = state.user.display_name || "";
  form.campus.value = state.user.campus || "";
  form.department.value = state.user.department || "";
  form.grade_year.value = state.user.grade_year || "";
  form.bio.value = state.user.bio || "";
  form.interests.value = (state.user.interests || []).join("，");
  form.preferred_locations.value = (state.user.preferred_locations || []).join("，");
  form.social_style.value = state.user.social_style || "balanced";
  form.preferred_group_min.value = state.user.preferred_group_min || 2;
  form.preferred_group_max.value = state.user.preferred_group_max || 6;
  elements.profileCredit.textContent = String(state.user.credit_score);
}

function splitList(value) {
  return String(value || "")
    .split(/[，,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function saveProfile(event) {
  event.preventDefault();
  const button = event.submitter;
  setButtonLoading(button, true, "正在保存…");
  elements.profileStatus.textContent = "";
  const form = new FormData(elements.profileForm);
  const payload = {
    display_name: String(form.get("display_name")).trim(),
    campus: String(form.get("campus") || "").trim() || null,
    department: String(form.get("department") || "").trim() || null,
    grade_year: form.get("grade_year") ? Number(form.get("grade_year")) : null,
    bio: String(form.get("bio") || "").trim() || null,
    interests: splitList(form.get("interests")),
    preferred_locations: splitList(form.get("preferred_locations")),
    social_style: form.get("social_style"),
    preferred_group_min: Number(form.get("preferred_group_min")),
    preferred_group_max: Number(form.get("preferred_group_max")),
  };
  try {
    state.user = await api("/users/me", { method: "PATCH", body: JSON.stringify(payload) });
    elements.accountName.textContent = state.user.display_name;
    elements.profileCredit.textContent = String(state.user.credit_score);
    elements.profileStatus.textContent = "画像已保存";
    showToast("画像已更新，下一次匹配会使用新信息");
  } catch (error) {
    elements.profileStatus.textContent = error.message;
  } finally {
    setButtonLoading(button, false);
  }
}

function bindEvents() {
  document.querySelector("#login-tab").addEventListener("click", () => switchAuthPanel("login"));
  document.querySelector("#register-tab").addEventListener("click", () => switchAuthPanel("register"));
  elements.loginForm.addEventListener("submit", handleLogin);
  elements.registerForm.addEventListener("submit", handleRegister);
  document.querySelector("#register-next").addEventListener("click", continueRegistration);
  document.querySelector("#register-back").addEventListener("click", () => showRegisterStep("account"));
  document.querySelector("#logout-button").addEventListener("click", () => logout());

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
    });
  });

  elements.matchForm.addEventListener("submit", handleMatch);
  document.querySelector("#new-match-button").addEventListener("click", resetMatchResult);
  document.querySelector("#start-another-button").addEventListener("click", resetMatchResult);
  elements.candidateList.addEventListener("click", handleCandidateSelection);
  elements.confirmButton.addEventListener("click", () => confirmMatch(false));
  document.querySelector("#create-solo-button").addEventListener("click", () => confirmMatch(true));
  elements.invitationList.addEventListener("click", respondInvitation);
  elements.activityList.addEventListener("click", (event) => {
    const leaveButton = event.target.closest("[data-leave-activity]");
    if (leaveButton) openLeaveDialog(leaveButton.dataset.leaveActivity);
    const feedbackButton = event.target.closest("[data-feedback-activity]");
    if (feedbackButton) {
      openFeedbackDialog(feedbackButton.dataset.feedbackActivity, feedbackButton.dataset.feedbackUser);
    }
  });
  elements.confirmLeaveButton.addEventListener("click", confirmLeaveActivity);
  elements.feedbackForm.addEventListener("submit", submitFeedback);
  elements.peerReviewList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-peer-task]");
    if (button) openPeerReviewDialog(button.dataset.peerTask);
  });
  elements.peerReviewForm.addEventListener("submit", submitPeerReview);
  elements.profileForm.addEventListener("submit", saveProfile);

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
    button.addEventListener("click", () => document.querySelector(`#${button.dataset.closeDialog}`).close());
  });
  document.querySelectorAll(".tag-field input[type='checkbox']").forEach((input) => {
    input.addEventListener("change", () => {
      const group = input.closest(".tag-field");
      const checked = group.querySelectorAll("input:checked");
      if (checked.length > 4) {
        input.checked = false;
        showToast("每组最多选 4 个，挑最有代表性的就好");
      }
    });
  });

  document.querySelector("input[name='custom_category']").addEventListener("input", (event) => {
    if (event.target.value.trim()) {
      document.querySelectorAll("input[name='category']").forEach((input) => {
        input.checked = false;
      });
    }
  });
  document.querySelectorAll("input[name='category']").forEach((input) => {
    input.addEventListener("change", () => {
      document.querySelector("input[name='custom_category']").value = "";
    });
  });
}

async function initialize() {
  bindEvents();
  initializeDates();
  elements.confirmButton.disabled = true;
  if (!state.token) {
    showAuthShell();
    return;
  }
  try {
    await loadCurrentUser();
  } catch {
    logout(false);
  }
}

initialize();
