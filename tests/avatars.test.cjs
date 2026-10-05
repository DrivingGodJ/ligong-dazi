const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const functions = source.slice(source.indexOf("function avatarMarkup("), source.indexOf("function activityGenderSummary("));
const userId = "11111111-1111-1111-1111-111111111111";
const avatarUrl = `/api/v1/users/${userId}/avatar?v=22222222-2222-2222-2222-222222222222`;

function setup() {
  const requests = [], revoked = [], created = [], images = [];
  const state = {token: "one", user: {id: userId, display_name: "小周", avatar_url: avatarUrl}, avatarCache: new Map(), avatarGeneration: 0, avatarRevision: 0, avatarBusy: false};
  const node = () => ({textContent: "", innerHTML: "", value: "", disabled: false, attributes: {}, classList: {toggle() {}}, setAttribute(k,v) {this.attributes[k]=v;}, removeAttribute(k) {delete this.attributes[k];}});
  const elements = {profileAvatar: node(), avatarUpload: node(), avatarRemove: node(), avatarStatus: node(), avatarError: node(), avatarFile: {...node(), files:[{type:"image/png",size:100}]}};
  const context = vm.createContext({
    state, elements, API_ROOT: "/api/v1",
    escapeHtml: value => String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch])),
    URL: {createObjectURL() {const url=`blob:avatar-${created.length}`;created.push(url);return url;},revokeObjectURL(url) {revoked.push(url);}},
    document: {querySelectorAll(selector) {return selector === ".user-avatar img" ? images : []; }},
    FormData: class {append() {}},
    apiBlob: async path => {requests.push(path);return {};},
    api: async (path, options) => {requests.push({path, options});return {avatar_url: options.method === "DELETE" ? null : avatarUrl};},
    imageToCompressedBlob: async () => ({}),
    hideInlineError() {}, showInlineError(node, message) {node.textContent=message;},
  });
  vm.runInContext(functions, context);
  const image = () => {
    const fallback = {hidden:false};
    const img = {dataset:{avatarUrl},isConnected:true,hidden:true,parentElement:{querySelector(){return fallback;}},removeAttribute(name){delete this[name];},fallback};
    images.push(img);return img;
  };
  return {context,state,elements,requests,revoked,created,image};
}

test("avatar markup escapes names and IDs, permits only our authenticated versioned image endpoint", () => {
  const {context} = setup();
  const result=context.avatarMarkup({id:userId,display_name:"<script>",avatar_url:"https://external.invalid/tracker.png"});
  assert.ok(result.includes("&lt;"));
  assert.ok(!result.includes("<img"));
  assert.ok(!result.includes("https://"));
  assert.ok(context.avatarMarkup({id:userId,display_name:"🌱同学",avatar_url:avatarUrl},true).includes("🌱"));
  assert.ok(context.avatarMarkup({id:userId,display_name:"小周",avatar_url:avatarUrl}).includes('data-avatar-url="/api/v1/users/'));
});

test("simultaneous avatar renders share one fetch and show fallback until image decode succeeds", async () => {
  const env=setup(); let resolve;
  env.context.apiBlob=() => {env.requests.push("image");return new Promise(r=>{resolve=r;});};
  const a=env.image(),b=env.image();
  const first=env.context.loadAvatarImage(a), second=env.context.loadAvatarImage(b);
  assert.equal(env.requests.length,1);
  assert.equal(a.fallback.hidden,false);
  resolve({}); await Promise.all([first,second]);
  assert.equal(a.src,b.src);
  a.onload(); assert.equal(a.hidden,false);assert.equal(a.fallback.hidden,true);
  a.onerror();assert.equal(a.hidden,true);assert.equal(a.fallback.hidden,false);
});

test("image fetch failure keeps the default avatar and a later render can retry", async () => {
  const env=setup();env.context.apiBlob=async()=>{throw new Error("offline");};
  const a=env.image();await env.context.loadAvatarImage(a);
  assert.equal(a.fallback.hidden,false);assert.equal(env.state.avatarCache.size,0);
  env.context.apiBlob=async()=>({});const b=env.image();await env.context.loadAvatarImage(b);
  assert.equal(env.created.length,1);
});

test("logout revokes cached images and late responses cannot attach the previous account avatar", async () => {
  const env=setup();const loaded=env.image();await env.context.loadAvatarImage(loaded);
  let resolve;env.context.apiBlob=()=>new Promise(r=>{resolve=r;});
  const pending=env.image();pending.dataset.avatarUrl=avatarUrl.replace("22222222", "33333333");
  const waiting=env.context.loadAvatarImage(pending);
  env.state.token=null;env.context.clearAvatarImages();resolve({});await waiting;
  assert.equal(env.state.avatarCache.size,0);assert.equal(env.created.length,1);
  assert.equal(env.revoked.length,1);assert.equal(loaded.src,undefined);
  assert.equal(loaded.onload,null);assert.equal(pending.src,undefined);
});

test("failed avatar replacement retains the old avatar, resets picker and permits retry", async () => {
  const env=setup();env.context.api=async()=>{throw new Error("服务器暂时不可用");};
  await env.context.changeAvatar();
  assert.equal(env.state.user.avatar_url,avatarUrl);assert.equal(env.state.avatarBusy,false);
  assert.equal(env.elements.avatarFile.value,"");assert.match(env.elements.avatarError.textContent,/服务器/);
  assert.equal(env.elements.avatarUpload.disabled,false);
});

test("duplicate upload clicks cannot double submit and restoring default does not compress a file", async () => {
  const env=setup();let resolve;
  env.context.api=async(path,options)=>{env.requests.push(options.method);return new Promise(r=>{resolve=r;});};
  const first=env.context.changeAvatar();await Promise.resolve();
  await env.context.changeAvatar();assert.equal(env.requests.length,1);
  resolve({avatar_url:avatarUrl});await first;assert.equal(env.state.avatarBusy,false);
  env.context.api=async()=>({avatar_url:null});
  env.context.imageToCompressedBlob=async()=>{throw new Error("must not compress");};
  await env.context.changeAvatar(true);assert.equal(env.state.user.avatar_url,null);
});

test("avatar file input is independent of profile autosave and nav yellow is not profile-only", () => {
  const css=fs.readFileSync(path.join(__dirname,"../web/reference-ui.css"),"utf8");
  assert.match(source,/\["push-notifications-toggle", "profile-avatar-file"\]\.includes\(event.target.id\)/);
  assert.match(css,/body > \.main-nav \.nav-button\.is-active \{ background: #fff2ce; color: #6933b8/);
  assert.match(css,/body > \.main-nav \.nav-button\.is-active \{ color: #ead7ff; background: #443922/);
});
