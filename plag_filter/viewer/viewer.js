// Компонент перегляду аркуша звіту Plag — PLAN_PLAG_FILTER_V2.md, §9.2 етап 8.
// Ліворуч зображення аркуша з наведенням, праворуч панель джерел.
// Жодних зовнішніх завантажень: усе малюється з переданих даних.

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const payload = data || {};
  const root = ensureRoot(parentElement);

  const send = (event) => setTriggerValue("event", event);
  render(root, payload, send);
  bindKeys(root, payload, send);
}

function ensureRoot(parentElement) {
  let root = parentElement.querySelector(".pv-root");
  if (!root) {
    root = document.createElement("div");
    root.className = "pv-root";
    parentElement.appendChild(root);
  }
  return root;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function render(root, payload, send) {
  root.textContent = "";

  const page = Number(payload.page) || 1;
  const pageCount = Number(payload.page_count) || 1;
  const overlay = Array.isArray(payload.overlay) ? payload.overlay : [];
  const sources = Array.isArray(payload.sources) ? payload.sources : [];
  const keepPages = Array.isArray(payload.keep_pages) ? payload.keep_pages : [];
  const showExcluded = Boolean(payload.show_excluded);

  const left = el("div", "pv-left");
  const panel = el("div", "pv-panel");
  root.appendChild(left);
  root.appendChild(panel);

  // --- навігація аркушами -------------------------------------------------
  const nav = el("div", "pv-nav");
  const goto = (value) => send({ type: "page", page: value });

  const prev = el("button", "pv-prev", "◀");
  prev.type = "button";
  prev.onclick = () => goto(page - 1);
  nav.appendChild(prev);

  const next = el("button", "pv-next", "▶");
  next.type = "button";
  next.onclick = () => goto(page + 1);
  nav.appendChild(next);

  const field = document.createElement("input");
  field.className = "pv-page-input";
  field.type = "number";
  field.min = "1";
  field.max = String(pageCount);
  field.value = String(page);
  field.setAttribute("aria-label", "Аркуш PDF");
  field.onchange = () => {
    const value = parseInt(field.value, 10);
    if (!Number.isNaN(value)) goto(value);
  };
  nav.appendChild(field);
  nav.appendChild(el("span", "pv-page-count", `з ${pageCount}`));

  const nextKeep = el("button", "pv-next-keep", "Наступний аркуш із залишеними джерелами ▶");
  nextKeep.type = "button";
  nextKeep.onclick = () => {
    const target = keepPages.find((item) => item > page);
    if (target !== undefined) goto(target);
    else if (keepPages.length) goto(keepPages[0]);
  };
  nav.appendChild(nextKeep);
  left.appendChild(nav);

  // --- зображення й наведення --------------------------------------------
  const canvas = el("div", "pv-canvas");
  const image = document.createElement("img");
  image.className = "pv-image";
  image.alt = `Аркуш PDF ${page}`;
  if (typeof payload.image === "string") image.src = payload.image;
  canvas.appendChild(image);
  left.appendChild(canvas);

  const shapesByNumber = new Map();
  const rowsByNumber = new Map();

  overlay.forEach((item) => {
    const number = Number(item.number);
    const shape = el("div", "pv-shape");
    shape.classList.add(item.kind === "marker" ? "pv-marker" : "pv-highlight");
    if (item.kind !== "marker") {
      shape.classList.add(item.color === "yellow" ? "pv-yellow" : "pv-pink");
    } else {
      shape.textContent = String(number);
    }
    shape.style.left = `${item.x0 * 100}%`;
    shape.style.top = `${item.y0 * 100}%`;
    shape.style.width = `${(item.x1 - item.x0) * 100}%`;
    shape.style.height = `${(item.y1 - item.y0) * 100}%`;
    shape.dataset.number = String(number);
    shape.dataset.excluded = item.excluded ? "1" : "0";
    if (item.excluded) {
      if (!showExcluded) shape.style.display = "none";
      else shape.classList.add("pv-faded");
    }
    if (item.kind === "marker") {
      shape.onclick = () => focusSource(number);
    }
    shape.onmouseenter = () => setActive(number, true);
    shape.onmouseleave = () => setActive(number, false);
    canvas.appendChild(shape);

    if (!shapesByNumber.has(number)) shapesByNumber.set(number, []);
    shapesByNumber.get(number).push(shape);
  });

  function setActive(number, on) {
    (shapesByNumber.get(number) || []).forEach((shape) => {
      shape.classList.toggle("pv-active", on);
    });
    const row = rowsByNumber.get(number);
    if (row) row.classList.toggle("pv-active", on);
  }

  function focusSource(number) {
    const row = rowsByNumber.get(number);
    if (!row) return;
    row.scrollIntoView({ block: "nearest" });
    setActive(number, true);
  }

  function applyLocalDecision(number, excluded) {
    (shapesByNumber.get(number) || []).forEach((shape) => {
      shape.dataset.excluded = excluded ? "1" : "0";
      if (excluded && !showExcluded) {
        shape.style.display = "none";
      } else {
        shape.style.display = "";
        shape.classList.toggle("pv-faded", excluded);
      }
    });
    const row = rowsByNumber.get(number);
    if (row) row.classList.toggle("pv-excluded", excluded);
  }

  // --- панель джерел ------------------------------------------------------
  panel.appendChild(el("h4", "pv-panel-title", `Джерела на аркуші ${page}`));

  sources.forEach((source) => {
    const number = Number(source.number);
    const row = el("div", "pv-source");
    row.dataset.number = String(number);
    if (source.decision === "exclude") row.classList.add("pv-excluded");

    row.appendChild(
      el(
        "div",
        "pv-source-title",
        `№ ${number} · ${source.label || ""} · ${source.percent_text || "?"}`
      )
    );
    row.appendChild(el("div", "pv-source-reason", source.reason_label || ""));
    if (source.evidence) {
      row.appendChild(el("div", "pv-source-evidence", source.evidence));
    }

    const actions = el("div", "pv-source-actions");
    const toggle = el("div", "pv-toggle");
    const keepButton = el("button", "pv-keep", "У звіті");
    keepButton.type = "button";
    const excludeButton = el("button", "pv-exclude", "Виключено");
    excludeButton.type = "button";

    const paint = (excluded) => {
      keepButton.classList.toggle("pv-on", !excluded);
      excludeButton.classList.toggle("pv-on", excluded);
    };
    paint(source.decision === "exclude");

    keepButton.onclick = () => {
      paint(false);
      applyLocalDecision(number, false);
      send({ type: "decision", number, manual: "keep" });
    };
    excludeButton.onclick = () => {
      paint(true);
      applyLocalDecision(number, true);
      send({ type: "decision", number, manual: "exclude" });
    };
    toggle.appendChild(keepButton);
    toggle.appendChild(excludeButton);
    actions.appendChild(toggle);

    if (source.manual) {
      const cancel = el("button", "pv-cancel", "Скасувати ручне рішення");
      cancel.type = "button";
      cancel.onclick = () => send({ type: "decision", number, manual: null });
      actions.appendChild(cancel);
    }

    if (source.url) {
      const link = el("a", "pv-open", "Відкрити");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      actions.appendChild(link);
    }

    row.appendChild(actions);
    row.onmouseenter = () => setActive(number, true);
    row.onmouseleave = () => setActive(number, false);
    panel.appendChild(row);
    rowsByNumber.set(number, row);
  });

  panel.appendChild(
    el("p", "pv-below", `Ще ${Number(payload.below_count) || 0} джерел нижче 0,1 % прибрано.`)
  );
}

function bindKeys(root, payload, send) {
  if (root.pvKeyHandler) {
    document.removeEventListener("keydown", root.pvKeyHandler);
  }
  const page = Number(payload.page) || 1;
  const handler = (event) => {
    if (!root.isConnected) {
      document.removeEventListener("keydown", handler);
      return;
    }
    const target = event.target;
    const tag = target && target.tagName ? target.tagName.toLowerCase() : "";
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (event.key === "ArrowLeft") {
      send({ type: "page", page: page - 1 });
    } else if (event.key === "ArrowRight") {
      send({ type: "page", page: page + 1 });
    }
  };
  root.pvKeyHandler = handler;
  document.addEventListener("keydown", handler);
}
