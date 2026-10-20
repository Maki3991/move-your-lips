import "./style.css";

type TaskState = "uploading" | "queued" | "processing" | "succeeded" | "failed";
type RecordItem = {
  attempt: string;
  token?: string;
  id?: string;
  size: number;
  created: number;
  state: TaskState;
  text?: string;
  code?: string;
  position?: number;
  expires?: number;
};
type Health = {
  state: "online" | "offline" | "preparing" | "full";
  queued: number;
  processing: number;
  uploading: number;
  limits: { maxBytes: number; maxSeconds: number; waiting: number };
};
type Stage =
  | "off"
  | "opening"
  | "live"
  | "recording"
  | "review"
  | "submitting"
  | "waiting";
const PREFIX = "myl.web.task.v1.";
const $ = <T extends HTMLElement>(id: string) =>
  document.getElementById(id) as T;
const video = $<HTMLVideoElement>("video");
const dialog = $<HTMLDialogElement>("clear-dialog");
let stage: Stage = "off",
  stream: MediaStream | undefined,
  recorder: MediaRecorder | undefined;
let blob: Blob | undefined,
  objectUrl: string | undefined,
  chunks: Blob[] = [],
  health: Health | undefined;
let records: RecordItem[] = [],
  elapsed = 0,
  recordingStart = 0,
  pollBusy = false,
  destroyed = false;
let recordTimer: ReturnType<typeof setInterval> | undefined,
  toastTimer: ReturnType<typeof setTimeout> | undefined;
let currentAttempt: string | undefined,
  progress = 0;
let actionSignature = "",
  historySignature = "",
  systemNotice = false;
const messages: Record<string, string> = {
  offline: "识别服务离线，请稍后再来。",
  preparing: "模型准备中，请稍候。",
  queue_full: "等待名额已满，请稍后提交。",
  uploads_full: "上传名额已满，请稍后提交。",
  storage_full: "服务暂时无法接收视频，请稍后再试。",
  rate_limited: "操作过于频繁，请一分钟后再试。",
  no_mouth: "未检测到清晰嘴部。请正对镜头、改善光线后重录。",
  empty_output: "模型未识别出文字，请重新录制。",
  model_error: "本次识别失败，请重新录制。",
  processing_timeout: "识别超时，请重新录制。",
  queue_expired: "等待已超时，请重新录制。",
  upload_expired: "上传已过期，请重新录制。",
  service_restarted: "服务重启，本次任务已结束。请重新录制。",
  upload_failed: "上传失败。视频仍保留在当前页面，可再次提交。",
  upload_incomplete: "视频上传不完整，请再次提交。",
  upload_in_progress: "正在接收视频，请稍候。",
  video_only: "请录制不含声音的视频。",
  resolution_invalid: "视频分辨率过高，请换一个浏览器后重录。",
  format_invalid: "无法读取视频，请重新录制或换一个浏览器。",
  duration_invalid: "请录制不超过 10 秒的视频。",
  video_too_short: "视频太短，请至少录制 1 秒。",
  file_too_large: "视频超过 10 MB，请缩短后重录。",
  download_failed: "视频传输失败，请重新录制。",
  not_found: "任务已过期或清理，请重新录制。",
  network: "网络连接中断，请检查网络。",
  local_storage: "浏览器无法保存任务，请允许本站存储后再提交。",
  worker_stopped: "本次识别已中断，请重新录制。",
};
function friendly(code: string) {
  return messages[code] || "本次操作未完成，请稍后再试。";
}
function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text?: string,
  className?: string,
) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function toast(text: string) {
  const node = $("toast");
  node.textContent = text;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), 2300);
}
function notice(text = "", error = false) {
  systemNotice = false;
  const node = $("notice");
  node.textContent = text;
  node.classList.toggle("error", error);
  node.hidden = !text;
}
function loadRecords() {
  const loaded: RecordItem[] = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key?.startsWith(PREFIX)) {
        try {
          const r = JSON.parse(localStorage.getItem(key) || "null");
          if (
            r &&
            r.attempt === key.slice(PREFIX.length) &&
            typeof r.created === "number" &&
            typeof r.state === "string"
          )
            loaded.push(r);
        } catch {
          /* Ignore corrupt entries. */
        }
      }
    }
  } catch {
    notice(friendly("local_storage"), true);
  }
  records = loaded.sort((a, b) => b.created - a.created);
}
function save(record: RecordItem) {
  try {
    localStorage.setItem(PREFIX + record.attempt, JSON.stringify(record));
    return true;
  } catch {
    notice("本机存储不可用，请复制结果。服务端临时结果最多保留 1 小时。", true);
    return false;
  }
}
function random() {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), (v) =>
    v.toString(16).padStart(2, "0"),
  ).join("");
}
function pending() {
  return records.filter(
    (r) =>
      r.state === "uploading" ||
      r.state === "queued" ||
      r.state === "processing",
  );
}
function cameraOff() {
  stream?.getTracks().forEach((t) => t.stop());
  stream = undefined;
  video.srcObject = null;
}
function releaseVideo() {
  blob = undefined;
  chunks = [];
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = undefined;
  video.removeAttribute("src");
  video.load();
}
function button(
  label: string,
  action: () => void | Promise<void>,
  primary = false,
  disabled = false,
) {
  const b = el("button", label, primary ? "primary" : "");
  b.disabled = disabled;
  b.addEventListener("click", () => {
    void action();
  });
  return b;
}
function update() {
  const online = health?.state === "online";
  const badge = $("status");
  badge.textContent =
    stage === "recording"
      ? "Recording"
      : health
        ? {
            online: "Online",
            offline: "Offline",
            preparing: "准备中",
            full: "已满",
          }[health.state]
        : "连接中";
  badge.className = "badge " + (health?.state || "offline");
  $("camera-placeholder").hidden = !!stream || !!objectUrl;
  video.hidden = !stream && !objectUrl;
  video.controls = stage === "review";
  video.classList.toggle("mirrored", !!stream);
  const timer = $("timer");
  timer.hidden = stage !== "recording" && stage !== "review";
  timer.classList.toggle("recording", stage === "recording");
  timer.textContent =
    stage === "recording"
      ? `${elapsed.toFixed(1)} / 10 秒`
      : `${elapsed.toFixed(1)} 秒 · 预览`;
  const actions = $("actions");
  const signature = JSON.stringify([
    stage,
    online,
    pending().length > 0,
    elapsed < 1,
  ]);
  if (actionSignature !== signature) {
    actionSignature = signature;
    actions.replaceChildren();
    if (stage === "off")
      actions.append(
        button(
          "开启摄像头",
          openCamera,
          false,
          !online || pending().length > 0,
        ),
      );
    if (stage === "opening")
      actions.append(button("正在开启…", () => {}, false, true));
    if (stage === "live")
      actions.append(
        button("开始录制", startRecording, true, !online),
        button("关闭", () => {
          cameraOff();
          stage = "off";
          update();
        }),
      );
    if (stage === "recording")
      actions.append(button("停止录制", stopRecording, true, elapsed < 1));
    if (stage === "review")
      actions.append(
        button("重新录制", () => {
          releaseVideo();
          stage = "off";
          void openCamera();
        }),
        button("提交识别", submit, true, !online || pending().length > 0),
      );
    if (stage === "submitting")
      actions.append(button("正在上传…", () => {}, false, true));
    if (stage === "waiting")
      actions.append(button("等待识别完成", () => {}, false, true));
  }
  const active =
    records.find((r) => r.attempt === currentAttempt) || pending()[0];
  const task = $("task");
  task.hidden =
    !active || !["uploading", "queued", "processing"].includes(active.state);
  task.replaceChildren();
  if (!task.hidden && active) {
    const title = {
      uploading: `上传中 ${progress}%`,
      queued: `排队中 · 第 ${active.position || 1} 位`,
      processing: "正在识别",
    }[active.state as "uploading" | "queued" | "processing"];
    task.append(el("strong", title));
    task.append(
      el("p", active.id ? `#${active.id.slice(0, 8)}` : "正在申请名额"),
    );
    if (active.state === "uploading") {
      const bar = el("div", undefined, "progress");
      const fill = el("progress");
      fill.max = 100;
      fill.value = progress;
      fill.setAttribute("aria-label", "上传进度");
      bar.append(fill);
      task.append(bar);
    } else
      task.append(
        el(
          "p",
          health?.state === "offline"
            ? "后端暂时离线，已提交任务会保留等待。"
            : "结果会自动返回此页面。",
        ),
      );
  }
  const clear = $<HTMLButtonElement>("clear");
  clear.hidden = records.length === 0;
  clear.disabled =
    pending().length > 0 ||
    records.some((r) => !!r.token) ||
    stage === "submitting";
  clear.title = clear.disabled ? "任务完成并交接结果后可清空" : "";
  renderHistory();
  if (
    !online &&
    stage !== "recording" &&
    stage !== "submitting" &&
    stage !== "waiting" &&
    ($("notice").hidden || systemNotice)
  ) {
    notice(
      health?.state === "full"
        ? "服务名额已满。已录视频会保留，可稍后提交。"
        : health?.state === "preparing"
          ? messages.preparing
          : health?.state === "offline"
            ? messages.offline
            : "正在连接识别服务…",
    );
    systemNotice = true;
  } else if (online && systemNotice) {
    notice();
  }
}
function renderHistory() {
  const signature = JSON.stringify(
    records.map((r) => [r.id, r.state, r.text, r.code, r.created]),
  );
  if (signature === historySignature) return;
  historySignature = signature;
  const list = $("history-list");
  list.replaceChildren();
  const finished = records.filter(
    (r) => r.state === "succeeded" || r.state === "failed",
  );
  if (!finished.length) list.append(el("p", "暂无识别记录", "empty"));
  for (const record of finished) {
    const card = el("article", undefined, "result-card");
    card.append(
      el(
        "p",
        record.state === "succeeded"
          ? record.text || "未识别出文字"
          : friendly(record.code || "model_error"),
        `result-text ${record.state === "failed" ? "failed" : ""}`,
      ),
    );
    if (record.state === "succeeded") {
      const copy = el("button", undefined, "copy");
      copy.setAttribute("aria-label", "复制识别结果");
      const icon = el("img");
      icon.src = "/assets/copy.svg";
      icon.alt = "";
      copy.append(icon);
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(record.text || "");
          toast("已复制");
        } catch {
          notice("复制失败，请长按或选中文字复制。", true);
        }
      });
      card.append(copy);
    }
    card.append(
      el(
        "p",
        `#${record.id?.slice(0, 8) || record.attempt.slice(0, 8)} · ${new Date(record.created * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}`,
        "meta",
      ),
    );
    list.append(card);
  }
}
async function api<T>(
  path: string,
  body?: unknown,
  token?: string,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch("/api" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ code: "network" }));
      throw new Error(error.code || "network");
    }
    return (response.status === 204 ? undefined : await response.json()) as T;
  } catch (error) {
    if (error instanceof Error && messages[error.message]) throw error;
    throw new Error("network");
  } finally {
    clearTimeout(timeout);
  }
}
async function openCamera() {
  notice();
  stage = "opening";
  update();
  try {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
      throw new Error("insecure");
    const acquired = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: "user",
        width: { ideal: 640, max: 1280 },
        height: { ideal: 480, max: 720 },
        frameRate: { ideal: 25, max: 30 },
      },
    });
    if (destroyed) {
      acquired.getTracks().forEach((t) => t.stop());
      return;
    }
    stream = acquired;
    video.srcObject = stream;
    await video.play();
    stage = "live";
    notice();
    stream.getVideoTracks()[0].addEventListener("ended", () => {
      if (stage === "recording") stopRecording();
      if (stage === "live") {
        cameraOff();
        stage = "off";
        notice("摄像头已断开，请重新开启。", true);
        update();
      }
    });
  } catch (error) {
    cameraOff();
    stage = "off";
    const name = error instanceof Error ? error.name : "";
    notice(
      name === "NotAllowedError"
        ? "未获得摄像头权限。请在浏览器地址栏的站点设置中允许使用摄像头。"
        : error instanceof Error && error.message === "insecure"
          ? "请通过 localhost 或 HTTPS 打开网页以使用摄像头。"
          : "无法开启摄像头，请检查设备是否被其他应用占用。",
      true,
    );
  }
  update();
}
function startRecording() {
  if (!stream) return;
  notice();
  chunks = [];
  try {
    const mimeType = ["video/webm;codecs=vp8", "video/mp4", "video/webm"].find(
      (t) => MediaRecorder.isTypeSupported(t),
    );
    recorder = new MediaRecorder(stream, {
      ...(mimeType ? { mimeType } : {}),
      videoBitsPerSecond: 1_200_000,
    });
    let bytes = 0;
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) {
        chunks.push(event.data);
        bytes += event.data.size;
        if (bytes > 10_000_000) stopRecording();
      }
    });
    recorder.addEventListener("error", () => {
      stopRecording();
      notice("录制失败，请重新录制。", true);
    });
    recorder.addEventListener("stop", () => {
      clearInterval(recordTimer);
      elapsed = Math.min(10, (performance.now() - recordingStart) / 1000);
      blob = new Blob(chunks, { type: recorder?.mimeType || "video/webm" });
      chunks = [];
      cameraOff();
      if (destroyed) {
        releaseVideo();
        return;
      }
      if (elapsed < 0.8 || blob.size > 10_000_000 || !blob.size) {
        releaseVideo();
        stage = "off";
        notice(
          elapsed < 0.8 ? messages.video_too_short : messages.file_too_large,
          true,
        );
      } else {
        objectUrl = URL.createObjectURL(blob);
        video.src = objectUrl;
        stage = "review";
        void video.play().catch(() => {});
      }
      update();
    });
    recordingStart = performance.now();
    elapsed = 0;
    recorder.start(250);
    stage = "recording";
    recordTimer = setInterval(() => {
      elapsed = Math.min(10, (performance.now() - recordingStart) / 1000);
      if (elapsed >= 10) stopRecording();
      update();
    }, 100);
  } catch {
    notice("此浏览器不支持视频录制，请使用新版 Chrome、Edge 或 Safari。", true);
  }
  update();
}
function stopRecording() {
  if (recorder?.state === "recording") {
    clearInterval(recordTimer);
    recorder.stop();
  }
}
function upload(record: RecordItem, data: Blob) {
  return new Promise<RecordItem>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/tasks/${record.id}/video`);
    xhr.setRequestHeader("Authorization", `Bearer ${record.token}`);
    xhr.setRequestHeader(
      "Content-Type",
      data.type || "application/octet-stream",
    );
    xhr.timeout = 175000;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        progress = Math.round((event.loaded / event.total) * 100);
        update();
      }
    };
    xhr.onload = () => {
      let result;
      try {
        result = JSON.parse(xhr.responseText);
      } catch {
        reject(new Error("network"));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(result);
      else reject(new Error(result.code || "upload_failed"));
    };
    xhr.onerror = () => reject(new Error("network"));
    xhr.ontimeout = () => reject(new Error("upload_failed"));
    xhr.send(data);
  });
}
async function submit() {
  if (!blob || stage !== "review" || pending().length) return;
  const record: RecordItem = {
    attempt: random(),
    token: random(),
    size: blob.size,
    created: Date.now() / 1000,
    state: "uploading",
  };
  if (!save(record)) {
    notice(messages.local_storage, true);
    return;
  }
  records.unshift(record);
  currentAttempt = record.attempt;
  stage = "submitting";
  progress = 0;
  notice();
  update();
  try {
    Object.assign(
      record,
      await api<RecordItem>("/tasks", {
        attempt: record.attempt,
        token: record.token,
        size: record.size,
      }),
    );
    save(record);
    Object.assign(record, await upload(record, blob));
    save(record);
    cameraOff();
    releaseVideo();
    stage = "waiting";
    notice();
  } catch (error) {
    const code = error instanceof Error ? error.message : "network";
    if (
      !record.id &&
      [
        "offline",
        "preparing",
        "uploads_full",
        "queue_full",
        "storage_full",
        "rate_limited",
        "file_too_large",
      ].includes(code)
    ) {
      localStorage.removeItem(PREFIX + record.attempt);
      records = records.filter((r) => r !== record);
      currentAttempt = undefined;
      stage = "review";
    } else {
      // Query a possibly accepted upload before deciding whether the task failed.
      stage = "waiting";
      notice("正在确认上传状态，请保持页面打开。");
      if (record.id)
        try {
          Object.assign(
            record,
            await api<RecordItem>(
              `/tasks/${record.id}`,
              undefined,
              record.token,
            ),
          );
          save(record);
        } catch {
          /* Poll will recover. */
        }
    }
    notice(friendly(code), true);
  }
  update();
  void poll();
}
async function acknowledge(record: RecordItem) {
  if (!record.token || !record.id || !save(record)) return;
  try {
    await api(`/tasks/${record.id}/ack`, {}, record.token);
  } catch (error) {
    if (!(error instanceof Error && error.message === "not_found")) return;
  }
  delete record.token;
  save(record);
}
async function poll() {
  if (pollBusy) return;
  pollBusy = true;
  try {
    try {
      health = await api<Health>("/status");
    } catch {
      health = {
        state: "offline",
        queued: 0,
        processing: 0,
        uploading: 0,
        limits: { maxBytes: 10_000_000, maxSeconds: 10, waiting: 10 },
      };
    }
    for (const record of records) {
      if (!record.token) continue;
      if (["succeeded", "failed"].includes(record.state)) {
        await acknowledge(record);
        continue;
      }
      if (stage === "submitting" && record.attempt === currentAttempt) continue;
      if (!record.id && Date.now() / 1000 - record.created > 180) {
        record.state = "failed";
        record.code = "upload_expired";
        delete record.token;
        save(record);
        stage = blob ? "review" : "off";
        currentAttempt = undefined;
        continue;
      }
      try {
        if (!record.id)
          Object.assign(
            record,
            await api<RecordItem>("/tasks", {
              attempt: record.attempt,
              token: record.token,
              size: record.size,
            }),
          );
        else
          Object.assign(
            record,
            await api<RecordItem>(
              `/tasks/${record.id}`,
              undefined,
              record.token,
            ),
          );
        if (
          record.state === "uploading" &&
          blob &&
          record.attempt === currentAttempt
        ) {
          // A lost upload response is recoverable. Never repeat model inference.
          stage = "submitting";
          Object.assign(record, await upload(record, blob));
          if (record.state !== "uploading") {
            releaseVideo();
            stage = "waiting";
          }
        }
        if (
          ["queued", "processing", "succeeded"].includes(record.state) &&
          record.attempt === currentAttempt &&
          blob
        ) {
          releaseVideo();
          stage = "waiting";
        }
        save(record);
        if (record.state === "succeeded" || record.state === "failed") {
          await acknowledge(record);
          if (record.attempt === currentAttempt || stage === "waiting") {
            stage = blob ? "review" : "off";
            currentAttempt = undefined;
            notice();
          }
        }
      } catch (error) {
        if (stage === "submitting") stage = "waiting";
        if (error instanceof Error && error.message === "not_found") {
          record.state = "failed";
          record.code = "not_found";
          delete record.token;
          save(record);
          stage = blob ? "review" : "off";
          currentAttempt = undefined;
        }
      }
    }
    update();
  } finally {
    pollBusy = false;
  }
}
$("clear").addEventListener("click", () => {
  if (!pending().length && !records.some((r) => r.token)) dialog.showModal();
});
$("cancel-clear").addEventListener("click", () => dialog.close());
$("confirm-clear").addEventListener("click", () => {
  if (pending().length || records.some((r) => r.token)) {
    dialog.close();
    return;
  }
  try {
    for (const r of records) localStorage.removeItem(PREFIX + r.attempt);
    records = [];
    dialog.close();
    update();
    toast("本机记录已清空");
  } catch {
    notice("清空失败，请在浏览器设置中清除此站点数据。", true);
  }
});
window.addEventListener("storage", (event) => {
  if (event.key?.startsWith(PREFIX) || event.key === null) {
    loadRecords();
    update();
    void poll();
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (stage === "recording") stopRecording();
    else if (stage === "live") {
      cameraOff();
      stage = "off";
      update();
    }
  } else void poll();
});
window.addEventListener("pagehide", () => {
  destroyed = true;
  clearInterval(recordTimer);
  stopRecording();
  cameraOff();
  releaseVideo();
});
window.addEventListener("pageshow", () => {
  if (destroyed) {
    destroyed = false;
    stage = pending().length ? "waiting" : "off";
    update();
    void poll();
  }
});
loadRecords();
if (pending().length) {
  stage = "waiting";
  currentAttempt = pending()[0].attempt;
}
update();
void poll();
setInterval(() => void poll(), 2000);
