import { visibleStageRect, observeStage } from "./embedded-stage.js";
import { isObsidianHost } from "./task-open.js";

let initialized = false;
let lastBounds = "";

function hostMessage(payload) {
  window.parent.postMessage({ type: "torch:dida", ...payload }, "*");
}

export function selectListModule(value, { scroll = true } = {}) {
  const dida = value === "dida";
  document.querySelector("#workbench-list-module").value = dida ? "dida" : "tasks";
  document.querySelector("#workbench-time").hidden = dida;
  document.querySelector("#time-dida-page").hidden = !dida;
  if (dida && isObsidianHost()) {
    const stage = document.querySelector("#time-dida-stage");
    const controls = document.querySelector("#workbench-list-module").closest(".workbench-controls");
    const navBottom = document.querySelector(".panel-nav")?.getBoundingClientRect().bottom || 0;
    const offset = controls.getBoundingClientRect().top - navBottom - 8;
    if (scroll && offset > 0 && innerHeight - stage.getBoundingClientRect().top < Math.min(740, stage.offsetHeight)) {
      window.scrollBy({ top: offset, behavior: "instant" });
    }
    showDidaPage();
  }
}

function showDidaPage() {
  if (!isObsidianHost() || initialized) return;
  initialized = true;
  const stage = document.querySelector("#time-dida-stage");
  const status = document.querySelector("#time-dida-message");
  const reload = document.querySelector("#time-dida-reload");
  const external = document.querySelector("#time-dida-external");
  status.textContent = "正在连接桌面内嵌浏览器…";
  reload.hidden = false;
  external.addEventListener("click", event => {
    event.preventDefault();
    window.parent.postMessage({ type: "torch:open-url", url: external.href }, "*");
  });
  window.addEventListener("message", event => {
    if (event.source === window.parent && event.data?.type === "torch:dida-state") {
      status.textContent = event.data.message;
    }
  });

  function reportBounds() {
    const rect = visibleStageRect(stage);
    const key = JSON.stringify(rect);
    if (key !== lastBounds) {
      lastBounds = key;
      hostMessage({ action: "bounds", rect });
    }
  }
  reload.addEventListener("click", () => {
    hostMessage({ action: "reload" });
    lastBounds = "";
    reportBounds();
  });
  observeStage(stage, reportBounds);
  reportBounds();
}
