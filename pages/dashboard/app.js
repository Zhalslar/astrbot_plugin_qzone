const bridge = window.AstrBotPluginPage;
const BRIDGE_READY_TIMEOUT_MS = 5000;
const BRIDGE_REQUEST_TIMEOUT_MS = 20000;

const state = {
  context: null,
  status: null,
  scope: "friends",
  targetUin: "",
  cursor: "",
  hasMore: false,
  posts: [],
  selected: null,
  media: [],
  loading: false,
  pendingLikes: new Set(),
  knownAuthors: new Map(),
  replyTarget: null,
  replyDrafts: new Map(),
  pendingReplies: new Set(),
  localVersions: new Map(),
  detailRequestSeq: 0,
  detailLoadingId: "",
};

function queryOne(selector) {
  return typeof document.querySelector === "function" ? document.querySelector(selector) : null;
}

function queryAll(selector) {
  return typeof document.querySelectorAll === "function" ? [...document.querySelectorAll(selector)] : [];
}

const el = {
  statusText: document.getElementById("statusText"),
  accountName: document.getElementById("accountName"),
  accountMeta: document.getElementById("accountMeta"),
  accountAvatar: queryOne(".account .avatar"),
  tabs: queryAll(".tab"),
  targetForm: document.getElementById("targetForm"),
  targetUin: document.getElementById("targetUin"),
  publishForm: document.getElementById("publishForm"),
  publishContent: document.getElementById("publishContent"),
  mediaInput: document.getElementById("mediaInput"),
  mediaStrip: document.getElementById("mediaStrip"),
  publishButton: document.getElementById("publishButton"),
  refreshButton: document.getElementById("refreshButton"),
  feedTitle: document.getElementById("feedTitle"),
  feedMeta: document.getElementById("feedMeta"),
  notice: document.getElementById("notice"),
  feed: document.getElementById("feed"),
  moreButton: document.getElementById("moreButton"),
  detailPane: document.getElementById("detailPane"),
  detailEmpty: document.getElementById("detailEmpty"),
  detailContent: document.getElementById("detailContent"),
};

function text(value) {
  return String(value ?? "");
}

function getAvatarUrl(uin) {
  if (!uin) return "";
  return `https://q.qlogo.cn/headimg_dl?dst_uin=${encodeURIComponent(uin)}&spec=100`;
}

function getFallbackAvatarUrl(uin) {
  if (!uin) return "";
  return `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(uin)}&s=100`;
}

function normalizeAvatarUrl(url) {
  const value = text(url).trim();
  if (!value) return "";
  if (value.startsWith("//")) return `https:${value}`;
  if (value.startsWith("http://")) return `https://${value.slice("http://".length)}`;
  return value;
}

function cleanDisplayText(value) {
  return text(value)
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function authorKey(uin) {
  const value = Number(uin || 0);
  if (!Number.isFinite(value) || value <= 0) return "";
  return String(Math.trunc(value));
}

function isGenericNickname(value, uin = 0) {
  const name = text(value).trim();
  const key = authorKey(uin);
  return !name
    || name === "我"
    || name === "用户"
    || name === "QQ空间用户"
    || name === "QQ 空间用户"
    || (key && name === key)
    || /^QQ\s*\d{5,}$/i.test(name)
    || /^\d{5,}$/.test(name);
}

function rememberAuthor(author) {
  const key = authorKey(author?.uin);
  if (!key) return;
  const nickname = text(author?.nickname).trim();
  if (isGenericNickname(nickname, key)) return;
  const remembered = { ...(state.knownAuthors.get(key) || {}), ...author, uin: Number(key), nickname };
  state.knownAuthors.set(key, remembered);

  const login = state.status?.login;
  if (login && authorKey(login.uin) === key && isGenericNickname(login.nickname, key)) {
    login.nickname = nickname;
  }
}

function rememberPostAuthors(post) {
  if (!post) return;
  rememberAuthor(post.author);
  for (const comment of post.comments || []) {
    rememberAuthor(comment.author);
  }
}

function rememberPosts(posts) {
  for (const post of posts || []) {
    rememberPostAuthors(post);
  }
}

function loginDisplayName() {
  const login = state.status?.login || {};
  const key = authorKey(login.uin);
  const known = key ? state.knownAuthors.get(key) : null;
  if (!isGenericNickname(login.nickname, key)) return text(login.nickname).trim();
  if (known && !isGenericNickname(known.nickname, key)) return known.nickname;
  if (login.bound || key) return "我";
  return "未绑定";
}

function currentLoginAuthor() {
  const login = state.status?.login || {};
  return {
    uin: login.uin || 0,
    nickname: loginDisplayName(),
    avatar: login.avatar || "",
  };
}

function displayAuthorName(author, fallback = "QQ空间用户") {
  const key = authorKey(author?.uin);
  const known = key ? state.knownAuthors.get(key) : null;
  const login = state.status?.login || {};
  const loginKey = authorKey(login.uin);
  const name = text(author?.nickname).trim();

  if (key && loginKey && key === loginKey) {
    return loginDisplayName();
  }
  if (!isGenericNickname(name, key)) return name;
  if (known && !isGenericNickname(known.nickname, key)) return known.nickname;
  return fallback;
}

function mergeAuthor(base, patch) {
  const merged = { ...(base || {}), ...(patch || {}) };
  const key = authorKey(merged.uin || base?.uin || patch?.uin);
  if (isGenericNickname(merged.nickname, key) && !isGenericNickname(base?.nickname, key)) {
    merged.nickname = base.nickname;
  }
  if (!merged.avatar && base?.avatar) {
    merged.avatar = base.avatar;
  }
  return merged;
}

function commentKey(comment) {
  const id = text(comment?.id || comment?.commentid).trim();
  if (id) return `id:${id}`;
  return [
    "body",
    authorKey(comment?.author?.uin),
    text(comment?.content).trim(),
    text(comment?.created_at),
  ].join(":");
}

function mergeComment(base, patch) {
  const merged = { ...(base || {}), ...(patch || {}) };
  if (base?.author || patch?.author) {
    merged.author = mergeAuthor(base?.author, patch?.author);
  }
  if (!cleanDisplayText(merged.content) && cleanDisplayText(base?.content)) {
    merged.content = base.content;
  }
  return merged;
}

function mergeCommentsPreservingLocal(localComments = [], remoteComments = []) {
  const localByKey = new Map();
  for (const item of localComments || []) {
    localByKey.set(commentKey(item), item);
  }

  const merged = [];
  const seen = new Set();
  for (const remote of remoteComments || []) {
    const key = commentKey(remote);
    seen.add(key);
    merged.push(mergeComment(localByKey.get(key), remote));
  }
  for (const local of localComments || []) {
    const key = commentKey(local);
    if (!seen.has(key)) {
      merged.push(local);
    }
  }
  return merged;
}

function localVersion(id) {
  return Number(state.localVersions.get(id) || 0);
}

function markLocalChange(id) {
  if (!id) return;
  state.localVersions.set(id, localVersion(id) + 1);
}

function mergeRemotePost(id, remotePost, versionAtRequestStart) {
  const current = currentPostById(id || remotePost?.id);
  if (!current || localVersion(current.id) === versionAtRequestStart) {
    return remotePost;
  }

  const merged = mergePost(remotePost, {
    liked: current.liked,
    stats: current.stats,
    comments: mergeCommentsPreservingLocal(current.comments || [], remotePost.comments || []),
  });
  merged.id = remotePost.id;
  return merged;
}

function avatarUrlsFor(author) {
  const urls = [
    normalizeAvatarUrl(author?.avatar),
    getAvatarUrl(author?.uin),
    getFallbackAvatarUrl(author?.uin),
  ].filter(Boolean);
  return [...new Set(urls)];
}

function renderAvatar(target, author, fallbackName = "Q") {
  const initial = text(fallbackName || "Q").trim().slice(0, 1).toUpperCase() || "Q";
  const fallback = () => {
    target.classList.remove("has-image");
    target.replaceChildren();
    target.textContent = initial;
  };
  const urls = avatarUrlsFor(author);
  if (!urls.length) {
    fallback();
    return;
  }

  let index = 0;
  const img = document.createElement("img");
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  img.onerror = () => {
    index += 1;
    if (index < urls.length) {
      img.src = urls[index];
      return;
    }
    fallback();
  };
  target.textContent = "";
  target.classList.add("has-image");
  target.replaceChildren(img);
  img.src = urls[index];
}

function mergePost(base, patch) {
  const merged = { ...(base || {}), ...(patch || {}) };
  if (base?.author || patch?.author) {
    merged.author = mergeAuthor(base?.author, patch?.author);
  }
  if (base?.stats || patch?.stats) {
    merged.stats = { ...(base?.stats || {}), ...(patch?.stats || {}) };
  }
  return merged;
}

function updatePost(id, patch) {
  let updated = null;
  state.posts = state.posts.map((item) => {
    if (item.id !== id) return item;
    updated = mergePost(item, patch);
    return updated;
  });
  if (state.selected?.id === id) {
    state.selected = mergePost(state.selected, patch);
    updated = state.selected;
  }
  return updated;
}

function currentPostById(id, fallback = null) {
  return state.posts.find((item) => item.id === id)
    || (state.selected?.id === id ? state.selected : null)
    || fallback;
}

function renderSelectedIfNeeded(id) {
  if (state.selected?.id === id) {
    renderDetail(state.selected);
  }
}

function setNotice(message, tone = "info") {
  el.notice.hidden = !message;
  el.notice.textContent = message || "";
  el.notice.dataset.tone = tone;
  el.notice.className = "notice";
  if (message && tone) {
    el.notice.classList.add(tone);
  }
}

async function withTimeout(promise, timeoutMs, message) {
  let timeoutId;
  const timeout = new Promise((_, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function normalizeBridgeResult(result, fallbackMessage) {
  if (result && typeof result === "object") {
    if (result.status === "error") {
      throw new Error(result.message || fallbackMessage);
    }
    if (Object.prototype.hasOwnProperty.call(result, "ok")) {
      if (!result.ok) {
        throw new Error(result.error?.message || result.message || fallbackMessage);
      }
      return result.data || {};
    }
  }
  return result || {};
}

async function apiGet(endpoint, params = {}) {
  const result = await withTimeout(
    bridge.apiGet(endpoint, params),
    BRIDGE_REQUEST_TIMEOUT_MS,
    "请求 AstrBot WebUI 超时，请刷新页面后重试。"
  );
  return normalizeBridgeResult(result, "请求失败");
}

async function apiPost(endpoint, body = {}) {
  const result = await withTimeout(
    bridge.apiPost(endpoint, body),
    BRIDGE_REQUEST_TIMEOUT_MS,
    "请求 AstrBot WebUI 超时，请刷新页面后重试。"
  );
  return normalizeBridgeResult(result, "请求失败");
}

function formatTime(value) {
  const timestamp = Number(value || 0);
  if (!timestamp) return "未知时间";
  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return "未知时间";
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function scopeTitle() {
  if (state.scope === "self") return "我的空间";
  if (state.scope === "profile") return state.targetUin ? `${state.targetUin} 的空间` : "指定 QQ";
  return "好友动态";
}

function mediaLayoutClass(count) {
  const safeCount = Math.max(1, Math.min(Number(count || 0), 9));
  return `media-layout-${safeCount}`;
}



function renderStatus() {
  const login = state.status?.login || {};
  rememberAuthor(login);
  el.accountName.textContent = loginDisplayName();
  el.accountMeta.textContent = login.uin ? `QQ ${login.uin}` : "需要先绑定 Cookie";
  if (el.statusText) {
    const daemon = state.status?.daemon || {};
    el.statusText.textContent = daemon.state === "ready" ? "daemon 已就绪" : `daemon ${daemon.state || "未知"}`;
  }
  
  if (login.uin && el.accountAvatar) {
    renderAvatar(el.accountAvatar, login, loginDisplayName());
  }
}

function renderTabs() {
  for (const tab of el.tabs) {
    tab.classList.toggle("active", tab.dataset.scope === state.scope);
  }
}

function renderMedia() {
  el.mediaStrip.replaceChildren();
  for (const [index, item] of state.media.entries()) {
    const chip = document.createElement("div");
    chip.className = "media-chip";
    const name = document.createElement("span");
    name.textContent = item.name || `图片 ${index + 1}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "移除";
    remove.addEventListener("click", () => {
      state.media.splice(index, 1);
      renderMedia();
    });
    chip.append(name, remove);
    el.mediaStrip.append(chip);
  }
}

function renderFeed() {
  el.feedTitle.textContent = scopeTitle();
  el.feedMeta.textContent = state.loading ? "加载中..." : `${state.posts.length} 条`;
  el.moreButton.hidden = !state.hasMore;
  el.feed.replaceChildren();

  if (!state.posts.length && !state.loading) {
    const empty = document.createElement("div");
    empty.className = "empty-feed";
    empty.textContent = "还没有读取到说说。";
    el.feed.append(empty);
    return;
  }

  for (const post of state.posts) {
    el.feed.append(renderPostCard(post));
  }
}

function openLightbox(url) {
  let lightbox = document.getElementById("lightbox");
  if (!lightbox) {
    lightbox = document.createElement("div");
    lightbox.id = "lightbox";
    lightbox.className = "lightbox";
    
    const closeBtn = document.createElement("button");
    closeBtn.className = "lightbox-close";
    closeBtn.innerHTML = "×";
    closeBtn.onclick = () => {
      lightbox.classList.remove("visible");
      setTimeout(() => lightbox.remove(), 300);
    };
    
    lightbox.onclick = (e) => {
      if (e.target === lightbox) {
        lightbox.classList.remove("visible");
        setTimeout(() => lightbox.remove(), 300);
      }
    };
    
    document.body.appendChild(lightbox);
  }
  
  lightbox.innerHTML = "";
  
  const closeBtn = document.createElement("button");
  closeBtn.className = "lightbox-close";
  closeBtn.innerHTML = "×";
  closeBtn.onclick = () => {
    lightbox.classList.remove("visible");
    setTimeout(() => lightbox.remove(), 300);
  };
  
  let mediaElement;
  
  if (url.match(/\.(mp4|webm|ogg)$/i)) {
    mediaElement = document.createElement("video");
    mediaElement.src = url;
    mediaElement.controls = true;
    mediaElement.autoplay = true;
  } else {
    mediaElement = document.createElement("img");
    mediaElement.src = url;
  }
  
  lightbox.appendChild(closeBtn);
  lightbox.appendChild(mediaElement);
  
  // Force a reflow
  void lightbox.offsetWidth;
  lightbox.classList.add("visible");
}

function renderPostCard(post) {
  rememberPostAuthors(post);
  const card = document.createElement("article");
  card.className = "post";
  card.dataset.id = post.id;

  const head = document.createElement("button");
  head.className = "post-header";
  head.type = "button";
  head.addEventListener("click", () => openDetail(post.id));

  const avatar = document.createElement("span");
  avatar.className = "avatar";
  const authorName = displayAuthorName(post.author);
  renderAvatar(avatar, post.author, authorName);

  const meta = document.createElement("span");
  meta.className = "post-meta";
  const name = document.createElement("strong");
  name.textContent = authorName;
  const time = document.createElement("span");
  time.textContent = formatTime(post.created_at);
  meta.append(name, time);
  head.append(avatar, meta);

  card.append(head);

  const contentText = cleanDisplayText(post.content);
  if (contentText) {
    const body = document.createElement("p");
    body.className = "post-content";
    body.textContent = contentText;
    card.append(body);
  }

  if (post.images && post.images.length > 0) {
    const images = document.createElement("div");
    const mediaCount = post.images.length;
    images.className = `post-media media-grid ${mediaLayoutClass(mediaCount)}`;
    for (const url of post.images) {
      if (url.match(/\.(mp4|webm|ogg)$/i)) {
        const vid = document.createElement("video");
        vid.src = url;
        vid.className = "preview-video";
        vid.muted = true;
        vid.loop = true;
        vid.playsInline = true;
        vid.addEventListener("mouseenter", () => vid.play().catch(()=>{}));
        vid.addEventListener("mouseleave", () => {
          vid.pause();
          vid.currentTime = 0;
        });
        vid.addEventListener("click", (e) => {
          e.stopPropagation();
          openLightbox(url);
        });
        images.append(vid);
      } else {
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = "说说图片";
        img.src = url;
        img.addEventListener("click", (e) => {
          e.stopPropagation();
          openLightbox(url);
        });
        images.append(img);
      }
    }
    card.append(images);
  }

  const actions = document.createElement("div");
  actions.className = "post-actions";
  const like = document.createElement("button");
  like.type = "button";
  like.className = post.liked ? "liked" : "";
  like.disabled = state.pendingLikes.has(post.id);
  like.setAttribute?.("aria-busy", state.pendingLikes.has(post.id) ? "true" : "false");
  
  const likeIcon = document.createElement("span");
  likeIcon.innerHTML = post.liked ? "♥" : "♡";
  likeIcon.className = "action-icon";
  if (post.liked) likeIcon.style.color = "var(--danger)";
  
  const likeText = document.createElement("span");
  likeText.textContent = ` ${post.stats?.likes ?? 0}`;
  
  like.append(likeIcon, likeText);
  like.addEventListener("click", () => toggleLike(post));

  const comment = document.createElement("button");
  comment.type = "button";
  
  const commentIcon = document.createElement("span");
  commentIcon.innerHTML = "💬";
  commentIcon.className = "action-icon";
  
  const commentText = document.createElement("span");
  commentText.textContent = ` ${post.stats?.comments ?? 0}`;
  
  comment.append(commentIcon, commentText);
  comment.addEventListener("click", () => openDetail(post.id, true));

  const detail = document.createElement("button");
  detail.type = "button";
  detail.textContent = "查看详情";
  detail.addEventListener("click", () => openDetail(post.id));
  
  actions.append(like, comment, detail);
  card.append(actions);
  
  return card;
}

function renderDetail(post, options = {}) {
  state.selected = mergePost(state.selected?.id === post.id ? state.selected : {}, post);
  post = state.selected;
  rememberPostAuthors(post);
  const isLoading = Boolean(options.loading || state.detailLoadingId === post.id);
  el.detailEmpty.hidden = true;
  el.detailContent.hidden = false;
  el.detailPane.classList.add("has-selection");
  el.detailContent.setAttribute?.("aria-busy", isLoading ? "true" : "false");
  el.detailContent.replaceChildren();

  const title = document.createElement("div");
  title.className = "detail-title";
  
  const titleHead = document.createElement("div");
  titleHead.className = "detail-header";
  
  const titleAvatar = document.createElement("span");
  titleAvatar.className = "avatar";
  const authorName = displayAuthorName(post.author);
  renderAvatar(titleAvatar, post.author, authorName);
  
  const titleMeta = document.createElement("div");
  titleMeta.className = "detail-meta";
  const name = document.createElement("strong");
  name.textContent = authorName;
  const time = document.createElement("span");
  time.textContent = formatTime(post.created_at);
  titleMeta.append(name, time);
  
  titleHead.append(titleAvatar, titleMeta);
  title.append(titleHead);

  el.detailContent.append(title);

  const contentText = cleanDisplayText(post.content);
  if (contentText) {
    const content = document.createElement("p");
    content.className = "post-content detail-text";
    content.textContent = contentText;
    el.detailContent.append(content);
  }

  const actions = document.createElement("div");
    actions.className = "detail-actions";
    titleHead.append(actions);
  const like = document.createElement("button");
  like.type = "button";
  like.textContent = post.liked ? "取消点赞" : "点赞";
  like.disabled = state.pendingLikes.has(post.id);
  like.setAttribute?.("aria-busy", state.pendingLikes.has(post.id) ? "true" : "false");
  like.addEventListener("click", () => toggleLike(post));
  actions.append(like);
  if (post.can_delete) {
    const del = document.createElement("button");
    del.type = "button";
    del.className = "danger";
    del.textContent = "删除";
    del.addEventListener("click", () => deletePost(post));
    actions.append(del);
  }

  // // // el.detailContent.append(actions);

  const comments = document.createElement("div");
  comments.className = "comments";
  const commentsTitle = document.createElement("h2");
  commentsTitle.textContent = "全部评论";
  comments.append(commentsTitle);
  if (isLoading) {
    const loading = document.createElement("div");
    loading.className = "detail-loading";
    loading.textContent = "正在同步详情...";
    comments.append(loading);
  }
  for (const item of post.comments || []) {
    comments.append(renderComment(post, item));
  }
  if (!post.comments?.length && !isLoading) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "还没有人评论，快来抢沙发。";
    comments.append(empty);
  }

  const form = document.createElement("form");
  form.className = "comment-form";
  const input = document.createElement("textarea");
  input.rows = 3;
  input.placeholder = "写下你的评论...";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.textContent = "发送";
  form.append(input, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendComment(post, input.value);
    input.value = "";
  });

  el.detailContent.append(comments, form);
}

function renderComment(post, comment) {
  rememberAuthor(comment.author);
  const row = document.createElement("div");
  row.className = "comment";
  const targetKey = `${post.id}:${text(comment.id)}`;
  
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  const authorName = displayAuthorName(comment.author);
  renderAvatar(avatar, comment.author, authorName);
  
  const body = document.createElement("div");
  body.className = "comment-body";
  
  const name = document.createElement("strong");
  name.textContent = authorName;
  const content = document.createElement("p");
  content.textContent = cleanDisplayText(comment.content);
  body.append(name, content);
  row.append(avatar, body);
  if (comment.can_reply) {
    const reply = document.createElement("button");
    reply.type = "button";
    reply.className = "reply-btn";
    reply.textContent = "回复";
    reply.addEventListener("click", () => {
      const commentId = text(comment.id);
      const isSameTarget = state.replyTarget?.postId === post.id
        && state.replyTarget?.commentId === commentId;
      state.replyTarget = isSameTarget ? null : { postId: post.id, commentId };
      renderSelectedIfNeeded(post.id);
      if (!isSameTarget) {
        setTimeout(() => {
          el.detailContent.querySelector(".reply-form textarea")?.focus();
        }, 0);
      }
    });
    row.append(reply);
  }
  if (state.replyTarget?.postId === post.id && state.replyTarget?.commentId === text(comment.id)) {
    const form = document.createElement("form");
    form.className = "reply-form";
    const input = document.createElement("textarea");
    input.rows = 2;
    input.placeholder = `回复 ${authorName}`;
    input.value = state.replyDrafts.get(targetKey) || "";
    input.addEventListener("input", () => {
      state.replyDrafts.set(targetKey, input.value);
    });
    const actions = document.createElement("div");
    actions.className = "reply-form-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "ghost";
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => {
      state.replyDrafts.delete(targetKey);
      state.replyTarget = null;
      renderSelectedIfNeeded(post.id);
    });
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "primary";
    submit.textContent = "发送回复";
    actions.append(cancel, submit);
    form.append(input, actions);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      const sent = await sendReply(post, comment, input.value);
      if (sent) {
        state.replyDrafts.delete(targetKey);
      } else {
        submit.disabled = false;
      }
    });
    row.append(form);
  }
  return row;
}

async function loadStatus() {
  const data = await apiGet("page/status");
  state.status = data;
  renderStatus();
}

function feedParams(next = false) {
  const params = { limit: 10 };
  if (next && state.cursor) params.cursor = state.cursor;
  if (state.scope === "friends") params.scope = "friends";
  if (state.scope === "self") params.scope = "self";
  if (state.scope === "profile") {
    params.scope = "profile";
    params.hostuin = state.targetUin;
  }
  return params;
}

async function loadFeed({ append = false } = {}) {
  if (state.scope === "profile" && !state.targetUin) {
    state.posts = [];
    state.cursor = "";
    state.hasMore = false;
    setNotice("输入 QQ 号后查看指定空间。", "warn");
    renderFeed();
    return;
  }
  
  // Only show loading if we're completely empty, otherwise let the spinner handle it gracefully
  if (!append) {
    state.loading = true;
    renderFeed();
  } else {
    el.moreButton.textContent = "加载中...";
    el.moreButton.disabled = true;
  }
  
  try {
    const data = await apiGet("page/feed", feedParams(append));
    state.cursor = data.cursor || "";
    state.hasMore = Boolean(data.has_more);
    state.posts = append ? [...state.posts, ...(data.items || [])] : data.items || [];
    rememberPosts(state.posts);
    renderStatus();
    setNotice("");
  } catch (error) {
    setNotice(error.message || "动态加载失败", "error");
  } finally {
    state.loading = false;
    if (append) {
      el.moreButton.textContent = "加载更多";
      el.moreButton.disabled = false;
    }
    renderFeed();
  }
}

async function openDetail(id, focusComment = false) {
  const cached = currentPostById(id);
  const requestSeq = ++state.detailRequestSeq;
  const versionAtRequestStart = localVersion(id);
  if (state.selected?.id !== id) {
    state.replyTarget = null;
  }
  state.detailLoadingId = id;
  if (cached) {
    renderDetail(cached, { loading: true });
    renderFeed();
    if (focusComment) {
      setTimeout(() => {
        el.detailContent.querySelector(".comment-form textarea")?.focus();
      }, 0);
    }
  }
  try {
    const data = await apiGet("page/detail", { id });
    if (requestSeq !== state.detailRequestSeq) return;
    state.detailLoadingId = "";
    const incoming = mergeRemotePost(data.post.id, data.post, versionAtRequestStart);
    const merged = updatePost(incoming.id, incoming) || incoming;
    rememberPostAuthors(merged);
    renderDetail(merged);
    renderFeed();
    if (focusComment) {
      setTimeout(() => {
        el.detailContent.querySelector(".comment-form textarea")?.focus();
      }, 0);
    }
  } catch (error) {
    if (requestSeq !== state.detailRequestSeq) return;
    state.detailLoadingId = "";
    if (cached) {
      renderDetail(currentPostById(id, cached));
    }
    setNotice(error.message || "详情加载失败", "error");
  }
}

async function toggleLike(post) {
  if (post.liked) {
    setNotice("当前插件页面暂不支持取消点赞。", "warn");
    return;
  }
  if (state.pendingLikes.has(post.id)) return;
  state.pendingLikes.add(post.id);
  const current = state.posts.find((item) => item.id === post.id) || state.selected || post;
  const oldLiked = Boolean(current.liked);
  const oldStats = { ...(current.stats || { likes: 0, comments: 0 }) };
  const nextLiked = !oldLiked;
  const nextLikes = Math.max(0, Number(oldStats.likes || 0) + (nextLiked ? 1 : -1));
  updatePost(post.id, {
    liked: nextLiked,
    stats: { ...oldStats, likes: nextLikes },
  });
  markLocalChange(post.id);
  renderFeed();
  renderSelectedIfNeeded(post.id);
  try {
    const data = await apiPost("page/like", { id: post.id, unlike: false });
    const finalLiked = Boolean(data.liked);
    const finalLikes = finalLiked === nextLiked
      ? nextLikes
      : Math.max(0, nextLikes + (finalLiked ? 1 : -1));
    updatePost(post.id, {
      liked: finalLiked,
      stats: { ...oldStats, likes: finalLikes },
    });
    markLocalChange(post.id);
    if (data.verified === false || data.operation_status === "accepted_pending_verification") {
      setNotice("QQ空间已接受操作，读回状态可能稍后同步。", "warn");
    } else {
      setNotice("");
    }
  } catch (error) {
    updatePost(post.id, { liked: oldLiked, stats: oldStats });
    markLocalChange(post.id);
    setNotice(error.message || "点赞失败", "error");
  } finally {
    state.pendingLikes.delete(post.id);
    renderFeed();
    renderSelectedIfNeeded(post.id);
  }
}

async function sendComment(post, content) {
  const textValue = text(content).trim();
  if (!textValue) return;
  const postId = post.id;
  const current = currentPostById(postId, post);
  const comments = [...(current.comments || [])];
  const tempId = `pending_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  const optimisticComment = {
    id: tempId,
    author: currentLoginAuthor(),
    content: textValue,
    created_at: Math.floor(Date.now() / 1000),
    can_reply: false,
  };
  const oldStats = { ...(current.stats || { likes: 0, comments: 0 }) };
  updatePost(postId, {
    comments: [...comments, optimisticComment],
    stats: { ...oldStats, comments: Number(oldStats.comments || 0) + 1 },
  });
  markLocalChange(postId);
  renderFeed();
  renderSelectedIfNeeded(postId);
  try {
    const data = await apiPost("page/comment", { id: postId, content: textValue });
    const serverComment = data.comment || {};
    const author = mergeAuthor(optimisticComment.author, serverComment.author);
    rememberAuthor(author);
    const target = currentPostById(postId, post);
    const nextComments = [...(target?.comments || [])].map((item) => (
      item.id === tempId
        ? {
          ...item,
          id: serverComment.id || tempId,
          author,
          content: serverComment.content || item.content,
          can_reply: Boolean(serverComment.id),
        }
        : item
    ));
    updatePost(postId, { comments: nextComments });
    markLocalChange(postId);
    setNotice("评论已发送。", "success");
  } catch (error) {
    const target = currentPostById(postId, post);
    const currentComments = [...(target?.comments || [])];
    const hadTemp = currentComments.some((item) => item.id === tempId);
    const currentStats = { ...(target?.stats || oldStats) };
    updatePost(postId, {
      comments: currentComments.filter((item) => item.id !== tempId),
      stats: hadTemp
        ? { ...currentStats, comments: Math.max(0, Number(currentStats.comments || 0) - 1) }
        : currentStats,
    });
    markLocalChange(postId);
    setNotice(error.message || "评论失败", "error");
  } finally {
    renderFeed();
    renderSelectedIfNeeded(postId);
  }
}

async function sendReply(post, comment, content) {
  const replyText = text(content).trim();
  if (!replyText) return false;
  const postId = post.id;
  const pendingKey = `${postId}:${text(comment.id)}`;
  if (state.pendingReplies.has(pendingKey)) return false;
  state.pendingReplies.add(pendingKey);
  const current = currentPostById(postId, post);
  const comments = [...(current.comments || [])];
  const oldStats = { ...(current.stats || { likes: 0, comments: 0 }) };
  const tempId = `pending_reply_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  const targetName = displayAuthorName(comment.author, "评论");
  const previousReplyTarget = state.replyTarget;
  const optimisticReply = {
    id: tempId,
    author: currentLoginAuthor(),
    content: `回复 ${targetName}：${replyText}`,
    created_at: Math.floor(Date.now() / 1000),
    can_reply: false,
  };
  updatePost(postId, {
    comments: [...comments, optimisticReply],
    stats: { ...oldStats, comments: Number(oldStats.comments || 0) + 1 },
  });
  markLocalChange(postId);
  if (state.replyTarget?.postId === postId && state.replyTarget?.commentId === text(comment.id)) {
    state.replyTarget = null;
  }
  renderFeed();
  renderSelectedIfNeeded(postId);
  try {
    const data = await apiPost("page/reply", {
      id: postId,
      commentid: comment.id,
      comment_uin: comment.author?.uin,
      content: replyText,
    });
    const serverReply = data.reply || {};
    const author = mergeAuthor(optimisticReply.author, serverReply.author);
    rememberAuthor(author);
    const target = currentPostById(postId, post);
    const nextComments = [...(target?.comments || [])].map((item) => (
      item.id === tempId
        ? {
          ...item,
          id: serverReply.id || tempId,
          author,
          content: serverReply.content ? `回复 ${targetName}：${serverReply.content}` : item.content,
        }
        : item
    ));
    updatePost(postId, { comments: nextComments });
    markLocalChange(postId);
    setNotice("回复已发送。", "success");
    return true;
  } catch (error) {
    const target = currentPostById(postId, post);
    const currentComments = [...(target?.comments || [])];
    const hadTemp = currentComments.some((item) => item.id === tempId);
    const currentStats = { ...(target?.stats || oldStats) };
    updatePost(postId, {
      comments: currentComments.filter((item) => item.id !== tempId),
      stats: hadTemp
        ? { ...currentStats, comments: Math.max(0, Number(currentStats.comments || 0) - 1) }
        : currentStats,
    });
    markLocalChange(postId);
    state.replyTarget = previousReplyTarget;
    setNotice(error.message || "回复失败", "error");
  } finally {
    state.pendingReplies.delete(pendingKey);
    renderFeed();
    renderSelectedIfNeeded(postId);
  }
  return false;
}

async function deletePost(post) {
  if (!window.confirm("确定删除这条说说吗？")) return;
  try {
    await apiPost("page/delete", { id: post.id });
    state.posts = state.posts.filter((item) => item.id !== post.id);
    state.selected = null;
    el.detailContent.hidden = true;
    el.detailEmpty.hidden = false;
    el.detailPane.classList.remove("has-selection");
    setNotice("说说已删除。", "success");
    renderFeed();
  } catch (error) {
    setNotice(error.message || "删除失败", "error");
  }
}

async function publish(event) {
  event.preventDefault();
  const content = el.publishContent.value;
  if (!content.trim() && !state.media.length) {
    setNotice("写点文字或添加图片再发布。", "warn");
    return;
  }
  el.publishButton.disabled = true;
  const originalText = el.publishButton.textContent;
  el.publishButton.textContent = "发布中...";
  try {
    const data = await apiPost("page/publish", { content, media: state.media });
    el.publishContent.value = "";
    state.media = [];
    renderMedia();
    if (data.post) {
      data.post.author = mergeAuthor(currentLoginAuthor(), data.post.author);
      rememberPostAuthors(data.post);
      state.posts = [data.post, ...state.posts];
      renderFeed();
    } else {
      await loadFeed();
    }
    setNotice("说说已发布。", "success");
  } catch (error) {
    setNotice(error.message || "发布失败", "error");
  } finally {
    el.publishButton.disabled = false;
    el.publishButton.textContent = originalText;
  }
}

async function uploadFiles(files) {
  const maxImages = state.status?.limits?.images || 9;
  for (const file of files) {
    if (state.media.length >= maxImages) {
      setNotice(`最多只能添加 ${maxImages} 张图片。`, "warn");
      break;
    }
    try {
      const result = await withTimeout(
        bridge.upload("page/upload-media", file),
        BRIDGE_REQUEST_TIMEOUT_MS,
        "上传图片超时，请刷新页面后重试。"
      );
      const data = normalizeBridgeResult(result, "上传失败");
      if (!data.media) throw new Error("上传失败");
      state.media.push(data.media);
    } catch (error) {
      setNotice(error.message || "图片上传失败", "error");
    }
  }
  renderMedia();
}

function bindEvents() {
  for (const tab of el.tabs) {
    tab.addEventListener("click", async () => {
      state.scope = tab.dataset.scope;
      if (state.scope !== "profile") {
        state.targetUin = "";
        el.targetUin.value = "";
      }
      state.cursor = "";
      state.posts = [];
      renderTabs();
      await loadFeed();
    });
  }
  el.targetForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.targetUin = el.targetUin.value.trim();
    state.scope = "profile";
    state.cursor = "";
    state.posts = [];
    renderTabs();
    await loadFeed();
  });
  el.refreshButton.addEventListener("click", () => {
    state.cursor = "";
    state.posts = [];
    loadFeed();
  });
  el.moreButton.addEventListener("click", () => loadFeed({ append: true }));
  el.publishForm.addEventListener("submit", publish);
  el.mediaInput.addEventListener("change", async () => {
    await uploadFiles(el.mediaInput.files || []);
    el.mediaInput.value = "";
  });
}

async function init() {
  if (!bridge) {
    setNotice("没有检测到 AstrBot Pages bridge，请从 AstrBot WebUI 插件页面进入。", "error");
    return;
  }
  
  try {
    state.context = await withTimeout(
      bridge.ready(),
      BRIDGE_READY_TIMEOUT_MS,
      "初始化 AstrBot WebUI 桥接超时，请刷新页面后重试。"
    );
  } catch (error) {
    setNotice(error.message || "桥接初始化失败", "error");
    return;
  }

  bindEvents();
  renderTabs();
  
  try {
    await loadStatus();
    await loadFeed();
  } catch (error) {
    setNotice(error.message || "初始化失败", "error");
  }
}

init();
