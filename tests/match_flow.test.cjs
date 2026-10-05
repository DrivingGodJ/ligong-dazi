const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const start = source.indexOf("function handleCandidateSelection(");
const end = source.indexOf("async function confirmMatch(", start);
assert.ok(start >= 0 && end > start);
const selection = source.slice(start, end);

function setup() {
  const state = {selectedUsers: new Set(), selectedActivity: null, preview: {requestedCount: 2}};
  let renders = 0;
  let opened = null;
  const context = vm.createContext({
    state,
    updateSelectionSummary() {renders++;},
    showToast() {},
    openUserProfile(id) {opened = id;},
    openActivityParticipants(id) {opened = id;},
  });
  vm.runInContext(selection, context);
  const click = (id, {input = false, type = "user", profile = false} = {}) => {
    let prevented = false;
    const card = {dataset: {id, type}};
    const target = {
      matches(selector) {return selector === "input" && input;},
      closest(selector) {
        if (selector === "[data-user-profile]" && profile) return {dataset: {userProfile: id}};
        return selector === ".candidate-card" ? card : null;
      },
    };
    context.handleCandidateSelection({target, preventDefault() {prevented = true;}});
    return prevented;
  };
  return {state, click, renders: () => renders, opened: () => opened};
}

test("native checkbox clicks are not cancelled, preserving checked state and keyboard Space", () => {
  const env = setup();
  assert.equal(env.click("one", {input: true}), false);
  assert.equal(env.state.selectedUsers.has("one"), true);
  assert.equal(env.click("one", {input: true}), false);
  assert.equal(env.state.selectedUsers.size, 0);
  assert.equal(env.renders(), 2);
});

test("label/card clicks select once, while profile inspection never selects a person", () => {
  const env = setup();
  assert.equal(env.click("one"), true);
  assert.equal(env.state.selectedUsers.has("one"), true);
  assert.equal(env.click("two", {profile: true}), false);
  assert.equal(env.opened(), "two");
  assert.equal(env.state.selectedUsers.size, 1);
});

test("person limit and mutually exclusive existing-activity selection remain intact", () => {
  const env = setup();
  env.click("one"); env.click("two"); env.click("three");
  assert.equal(env.state.selectedUsers.size, 2);
  env.click("activity", {type: "activity"});
  assert.equal(env.state.selectedUsers.size, 0);
  assert.equal(env.state.selectedActivity, "activity");
  env.click("one");
  assert.equal(env.state.selectedActivity, null);
  assert.equal(env.state.selectedUsers.size, 1);
});

test("every role mascot reuses the confirmed silhouette and palette", () => {
  const base = fs.readFileSync(path.join(__dirname, "../web/mascot-bloom.svg"), "utf8");
  const body = base.slice(base.indexOf("<defs>"), base.indexOf("</svg>")).trim();
  for (const file of ["mascot-square.svg", "mascot-activities.svg", "mascot-invitations.svg", "thinking-flower.svg"]) {
    const variant = fs.readFileSync(path.join(__dirname, "../web", file), "utf8");
    assert.ok(variant.includes(body), `${file} changed the brand flower`);
    assert.match(variant, /viewBox="0 0 260 240"/);
  }
});

function passwordSetup() {
  const inputs = Array.from({length: 5}, () => ({type: "password", value: "unchanged-password", id: "", form: {resets: [], addEventListener(_, fn) {this.resets.push(fn);}}, before(wrapper) {this.wrapper = wrapper;}}));
  const node = () => ({children: [], attributes: {}, listeners: {}, append(child) {this.children.push(child);}, setAttribute(key, value) {this.attributes[key] = value;}, addEventListener(name, fn) {this.listeners[name] = fn;}});
  const context = vm.createContext({document: {querySelectorAll() {return inputs;}, createElement: node}});
  vm.runInContext(source.slice(source.indexOf("function initializePasswordControls("), source.indexOf("function initializeHumanChallenges(")), context);
  context.initializePasswordControls();
  return inputs;
}

test("login, registration and reset-password controls toggle without changing the password", () => {
  const inputs = passwordSetup();
  for (const input of inputs) {
    const button = input.wrapper.children[1];
    assert.equal(button.type, "button");
    assert.equal(button.attributes["aria-controls"], input.id);
    button.listeners.click();
    assert.equal(input.type, "text");
    assert.equal(button.attributes["aria-pressed"], "true");
    button.listeners.click();
    assert.equal(input.type, "password");
    assert.equal(button.attributes["aria-label"], "显示密码");
    assert.equal(input.value, "unchanged-password");
  }
});

test("resetting any password form restores the hidden state", () => {
  for (const input of passwordSetup()) {
    const button = input.wrapper.children[1];
    button.listeners.click();
    input.form.resets.forEach(fn => fn());
    assert.equal(input.type, "password");
    assert.equal(button.textContent, "显示");
    assert.equal(button.attributes["aria-pressed"], "false");
  }
});

test("result screens hide the long search form without discarding its values", () => {
  const node = () => {
    const classes = new Set();
    return {classList: {add(x) {classes.add(x);}, remove(x) {classes.delete(x);}, contains(x) {return classes.has(x);}, toggle(x, on) {on ? classes.add(x) : classes.delete(x);}}, setAttribute() {}, scrollIntoView() {}};
  };
  const request = node(), heading = node();
  const form = {value: "preserved", closest() {return request;}};
  const elements = {matchForm: form};
  for (const name of ["resultEmpty", "resultRunning", "resultContent", "resultSuccess", "confirmBar", "resultPanel"]) elements[name] = node();
  const context = vm.createContext({elements, state: {}, window: {clearTimeout() {}, setTimeout() {return 1;}}, document: {body: node(), querySelector() {return heading;}}});
  vm.runInContext(source.slice(source.indexOf("function setResultView("), source.indexOf("function startRunningProgress(")), context);
  context.setResultView("content");
  assert.equal(request.classList.contains("is-hidden"), true);
  assert.equal(heading.classList.contains("is-hidden"), true);
  assert.equal(elements.resultContent.classList.contains("is-hidden"), false);
  context.setResultView("empty");
  assert.equal(request.classList.contains("is-hidden"), false);
  assert.equal(heading.classList.contains("is-hidden"), false);
  assert.equal(form.value, "preserved");
});

function confirmationSetup({users = [], activity = null, policy = "open", location = "体育馆", fail = false} = {}) {
  const state = {
    selectedUsers: new Set(users), selectedActivity: activity, matchConfirmPending: false,
    preview: {match_request_id: "preview", requestedLocation: location, candidates: activity
      ? [{candidate_type: "activity", candidate_id: activity, activity: {join_policy: policy}}] : []},
  };
  const node = () => ({classList: {add() {}, remove() {}}, focus() {this.focused = true;}});
  const elements = {confirmLocation: {...node(), value: ""}, confirmLocationError: node(), confirmButton: {disabled: false}, matchError: node(), successTitle: {}, successCopy: {}};
  const requests = [], errors = [];
  const context = vm.createContext({
    state, elements, document: {querySelector() {return node();}},
    hideInlineError() {}, showInlineError(_, message) {errors.push(message);},
    setButtonLoading(button, loading) {button.disabled = loading;},
    updateSelectionSummary() {elements.confirmButton.disabled = state.matchConfirmPending;},
    setResultView(view) {state.view = view;}, loadInvitations() {}, loadActivities() {},
    async api(url, options) {
      const body = JSON.parse(options.body);
      requests.push({url, body});
      if (fail) throw new Error("网络错误，请重试");
      return {status: body.existing_activity_id ? (policy === "approval" ? "applied" : "joined") : "confirmed", activity: {title: "验收活动"}, invitations: body.candidate_user_ids.map(id => ({id}))};
    },
  });
  vm.runInContext(source.slice(source.indexOf("function getMatchConfirmationAction("), source.indexOf("function updateSelectionSummary(")), context);
  vm.runInContext(source.slice(source.indexOf("async function confirmMatch("), source.indexOf("function resetMatchResult(")), context);
  return {context, state, elements, requests, errors};
}

for (const scenario of [
  {label: "直接发布", users: [], solo: true},
  {label: "发送邀请并发布", users: ["one", "two"], solo: false},
  {label: "申请参与活动", activity: "existing", policy: "approval", solo: false},
  {label: "直接加入活动", activity: "existing", policy: "open", solo: false},
]) {
  test(`one primary action derives the correct label and exclusive payload: ${scenario.label}`, async () => {
    const env = confirmationSetup(scenario);
    assert.equal(env.context.getMatchConfirmationAction().label, scenario.label);
    await env.context.confirmMatch();
    assert.equal(env.requests.length, 1);
    const {body} = env.requests[0];
    assert.deepEqual(body.candidate_user_ids, scenario.users || []);
    assert.equal(body.existing_activity_id, scenario.activity || null);
    assert.equal(body.create_solo_activity, scenario.solo);
    assert.equal("join_policy" in body, false, "new publications use the existing server default");
    assert.equal(body.location, scenario.activity ? null : "体育馆");
    assert.equal(env.elements.confirmButton.disabled, false);
    assert.equal(env.state.view, "success");
  });
}

test("joining never includes invitation targets, even with inconsistent cached selection", async () => {
  const env = confirmationSetup({activity: "existing", users: ["one"]});
  await env.context.confirmMatch();
  assert.deepEqual(env.requests[0].body.candidate_user_ids, []);
  assert.equal(env.requests[0].body.existing_activity_id, "existing");
});

test("one action cannot double-submit, and failure preserves selection and permits retry", async () => {
  const env = confirmationSetup({users: ["one"], fail: true});
  await Promise.all([env.context.confirmMatch(), env.context.confirmMatch()]);
  assert.equal(env.requests.length, 1);
  assert.equal(env.state.matchConfirmPending, false);
  assert.equal(env.elements.confirmButton.disabled, false);
  assert.deepEqual([...env.state.selectedUsers], ["one"]);
  assert.ok(env.errors.includes("网络错误，请重试"));
  await env.context.confirmMatch();
  assert.equal(env.requests.length, 2);
});

test("direct publication still requires a location, while joining uses the existing activity", async () => {
  const env = confirmationSetup({location: ""});
  await env.context.confirmMatch();
  assert.equal(env.requests.length, 0);
  assert.equal(env.elements.confirmLocation.focused, true);
  env.elements.confirmLocation.value = "补填体育馆";
  await env.context.confirmMatch();
  assert.equal(env.requests[0].body.location, "补填体育馆");
  const joining = confirmationSetup({activity: "existing", location: ""});
  await joining.context.confirmMatch();
  assert.equal(joining.requests[0].body.location, null);
});

test("publication screen has one primary action and no join-permission dropdown", () => {
  const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
  assert.equal((html.match(/id="confirm-match-button"/g) || []).length, 1);
  assert.doesNotMatch(html, /create-solo-button|match-join-policy|publish-solo-option/);
  assert.doesNotMatch(source, /create-solo-button|match-join-policy|invite-publish-copy/);
});
