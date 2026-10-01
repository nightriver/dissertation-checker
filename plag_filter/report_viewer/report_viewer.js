// Вкладка «Звіт» у вигляді Plag — PLAN_PLAG_VIEW.md, §7 етап 3.
// Ліворуч картка з текстом аркуша, праворуч закріплена панель джерел.
// Жодних зовнішніх завантажень: усе малюється з переданих даних.

const GAUGE_RADIUS = 26;
const GAUGE_LENGTH = 2 * Math.PI * GAUGE_RADIUS;

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const payload = data || {};
  const root = ensureRoot(parentElement);
  root.prSend = (event) => setTriggerValue("event", event);

  // Ті самі дані — DOM не чіпаємо: виділення й прокрутка лишаються на місці.
  const signature = String(payload.signature || "");
  if (root.prSignature === signature && root.childElementCount) return;

  const previousPage = root.prPage;
  root.prSignature = signature;
  root.prPage = Number(payload.page) || 1;
  render(root, payload);
  if (previousPage !== undefined && previousPage !== root.prPage) scrollToTop(root);
}

function ensureRoot(parentElement) {
  let root = parentElement.querySelector(".pr-root");
  if (!root) {
    root = document.createElement("div");
    root.className = "pr-root";
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

function send(root, event) {
  if (typeof root.prSend === "function") root.prSend(event);
}

// Відступ — `scroll-margin-top` із CSS: під шапкою Streamlit картку не видно.
function scrollToTop(root) {
  const margin = parseFloat(getComputedStyle(root).scrollMarginTop) || 0;
  if (root.getBoundingClientRect().top < margin) root.scrollIntoView({ block: "start" });
}

// Нумерація як у Plag: перші 3, останні 3, поточний ±1, розриви — «…».
function pageItems(pages, current) {
  const count = pages.length;
  const keep = new Set();
  for (let i = 0; i < Math.min(3, count); i += 1) keep.add(i);
  for (let i = Math.max(0, count - 3); i < count; i += 1) keep.add(i);
  const index = pages.indexOf(current);
  if (index >= 0) {
    for (let i = index - 1; i <= index + 1; i += 1) {
      if (i >= 0 && i < count) keep.add(i);
    }
  }
  const items = [];
  let previous = -1;
  [...keep]
    .sort((a, b) => a - b)
    .forEach((i) => {
      if (previous >= 0 && i > previous + 1) items.push(null);
      items.push(pages[i]);
      previous = i;
    });
  return items;
}

function render(root, payload) {
  root.textContent = "";

  const page = Number(payload.page) || 1;
  const pages = Array.isArray(payload.pages) ? payload.pages.map(Number) : [];
  const paragraphs = Array.isArray(payload.paragraphs) ? payload.paragraphs : [];
  const sources = Array.isArray(payload.sources) ? payload.sources : [];
  const scores = payload.scores || {};
  let showExcluded = Boolean(payload.show_excluded);
  root.classList.toggle("pr-show-excluded", showExcluded);

  const fragsByNumber = new Map();
  const rowsByNumber = new Map();
  let hovered = null;
  let pinned = null;

  const register = (number, node) => {
    if (!fragsByNumber.has(number)) fragsByNumber.set(number, []);
    fragsByNumber.get(number).push(node);
  };

  const paint = () => {
    fragsByNumber.forEach((nodes, number) => {
      const on = number === hovered || number === pinned;
      nodes.forEach((node) => node.classList.toggle("pr-active", on));
    });
    rowsByNumber.forEach((row, number) => {
      row.classList.toggle("pr-active", number === hovered || number === pinned);
    });
  };

  const card = el("div", "pr-card");
  const panel = el("aside", "pr-panel");
  root.appendChild(card);
  root.appendChild(panel);

  // --- шапка --------------------------------------------------------------
  const head = el("div", "pr-head");
  head.appendChild(el("div", "pr-filename", payload.filename || ""));
  const toggle = el("button", "pr-toggle-excluded");
  toggle.type = "button";
  const toggleLabel = () => {
    toggle.textContent = showExcluded ? "Приховати виключені" : "Показати виключені";
  };
  toggleLabel();
  toggle.onclick = () => {
    showExcluded = !showExcluded;
    root.classList.toggle("pr-show-excluded", showExcluded);
    toggleLabel();
    send(root, { type: "show_excluded", value: showExcluded });
  };
  head.appendChild(toggle);
  card.appendChild(head);
  card.appendChild(renderScores(scores));

  // --- нумерація й текст --------------------------------------------------
  const goto = (value) => send(root, { type: "page", page: value });
  card.appendChild(renderPages(pages, page, goto));

  const text = el("div", "pr-text");
  if (!paragraphs.length) {
    text.appendChild(el("div", "pr-empty", "На цьому аркуші немає тексту дисертації"));
  }
  paragraphs.forEach((segments) => {
    text.appendChild(renderParagraph(Array.isArray(segments) ? segments : [], register));
  });
  card.appendChild(text);
  card.appendChild(renderPages(pages, page, goto));

  fragsByNumber.forEach((nodes, number) => {
    nodes.forEach((node) => {
      node.onmouseenter = () => {
        hovered = number;
        paint();
      };
      node.onmouseleave = () => {
        hovered = null;
        paint();
      };
      node.onclick = () => {
        // Клік, яким експерт закінчує виділення, не перемикає джерело.
        const selection = window.getSelection ? window.getSelection() : null;
        if (selection && selection.toString()) return;
        pinned = pinned === number ? null : number;
        paint();
        const row = rowsByNumber.get(number);
        if (row && pinned !== null) {
          panel.scrollTop = Math.max(0, row.offsetTop - panel.clientHeight / 2);
        }
      };
    });
  });

  // --- панель джерел ------------------------------------------------------
  panel.appendChild(el("div", "pr-panel-title", `Джерела на аркуші ${page}`));
  if (!sources.length) {
    panel.appendChild(el("div", "pr-below", "На аркуші немає джерел від 0,1 %."));
  }
  sources.forEach((source) => {
    const number = Number(source.number);
    const row = renderRow(root, source, fragsByNumber, () => {
      const first = (fragsByNumber.get(number) || [])[0];
      if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
      pinned = number;
      paint();
    });
    row.onmouseenter = () => {
      hovered = number;
      paint();
    };
    row.onmouseleave = () => {
      hovered = null;
      paint();
    };
    panel.appendChild(row);
    rowsByNumber.set(number, row);
  });
  panel.appendChild(
    el("div", "pr-below", `Ще ${Number(payload.below_count) || 0} джерел нижче 0,1 % прибрано.`)
  );
}

function renderScores(scores) {
  const box = el("div", "pr-scores");

  const caption = el("div", "pr-score-caption");
  caption.appendChild(el("small", "", "Оцінка схожості"));
  caption.appendChild(document.createTextNode(`Ризик плагіату: ${scores.risk || "—"}`));
  box.appendChild(caption);

  const value = parseFloat(scores.similarity);
  const gauge = el("div", "pr-gauge");
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("width", "56");
  svg.setAttribute("height", "56");
  svg.setAttribute("viewBox", "0 0 56 56");
  svg.setAttribute("aria-hidden", "true");
  const track = document.createElementNS(ns, "circle");
  track.setAttribute("cx", "28");
  track.setAttribute("cy", "28");
  track.setAttribute("r", String(GAUGE_RADIUS));
  track.setAttribute("fill", "none");
  track.setAttribute("stroke", "var(--pr-gauge-track)");
  track.setAttribute("stroke-width", "4");
  svg.appendChild(track);
  if (!Number.isNaN(value)) {
    const share = Math.max(0, Math.min(100, value)) / 100;
    const arc = document.createElementNS(ns, "circle");
    arc.setAttribute("cx", "28");
    arc.setAttribute("cy", "28");
    arc.setAttribute("r", String(GAUGE_RADIUS));
    arc.setAttribute("fill", "none");
    arc.setAttribute("stroke", "var(--pr-gauge-arc)");
    arc.setAttribute("stroke-width", "4");
    arc.setAttribute("stroke-linecap", "round");
    arc.setAttribute("stroke-dasharray", String(GAUGE_LENGTH));
    arc.setAttribute("stroke-dashoffset", String(GAUGE_LENGTH * (1 - share)));
    arc.setAttribute("transform", "rotate(-90 28 28)");
    svg.appendChild(arc);
  }
  gauge.appendChild(svg);
  gauge.appendChild(
    el("div", "pr-gauge-value", Number.isNaN(value) ? "—" : `${scores.similarity}%`)
  );
  box.appendChild(gauge);

  const rows = el("div", "pr-score-rows");
  [
    ["Перефразування", scores.paraphrase],
    ["Неправильне цитування", scores.wrong_citation],
    ["Збіги тексту", scores.text_matches],
  ].forEach(([label, value]) => {
    rows.appendChild(el("div", "", label));
    rows.appendChild(el("div", "pr-pill", value || "—"));
  });
  box.appendChild(rows);
  return box;
}

function renderPages(pages, current, goto) {
  const nav = el("nav", "pr-pages");
  nav.setAttribute("aria-label", "Аркуші PDF");
  pageItems(pages, current).forEach((item) => {
    if (item === null) {
      nav.appendChild(el("span", "pr-gap", "…"));
      return;
    }
    const button = el("button", "pr-page", item);
    button.type = "button";
    if (item === current) {
      button.classList.add("pr-on");
      button.setAttribute("aria-current", "page");
    } else {
      button.onclick = () => goto(item);
    }
    nav.appendChild(button);
  });
  return nav;
}

// Маркер не створює вузла: він ставить плашку на наступний фрагмент того
// самого джерела; якщо такого нема — порожній вузол нульової ширини.
function renderParagraph(segments, register) {
  const paragraph = el("p");
  let pending = null;

  const decorate = (node, segment) => {
    node.dataset.n = String(segment.number);
    node.dataset.c = String(segment.color);
    if (segment.excluded) node.classList.add("pr-excluded");
  };

  const flushBadge = () => {
    if (!pending) return;
    const badge = el("span", "pr-badge-only");
    decorate(badge, pending);
    badge.dataset.badge = String(pending.number);
    paragraph.appendChild(badge);
    pending = null;
  };

  segments.forEach((segment) => {
    if (segment.marker) {
      flushBadge();
      pending = segment;
      return;
    }
    if (segment.number === null || segment.number === undefined) {
      flushBadge();
      paragraph.appendChild(document.createTextNode(segment.text || ""));
      return;
    }
    const number = Number(segment.number);
    const frag = el("span", "pr-frag", segment.text || "");
    decorate(frag, segment);
    if (pending && Number(pending.number) === number) {
      frag.dataset.badge = String(number);
      pending = null;
    } else {
      flushBadge();
    }
    paragraph.appendChild(frag);
    register(number, frag);
  });
  flushBadge();
  return paragraph;
}

function link(className, href, text, title) {
  const node = el("a", className, text);
  node.href = href;
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  if (title) node.title = title;
  return node;
}

function renderRow(root, source, fragsByNumber, focus) {
  const number = Number(source.number);
  let excluded = source.decision === "exclude";

  const row = el("div", "pr-row");
  row.dataset.c = String(source.color);
  row.classList.toggle("pr-excluded", excluded);

  const main = el("div", "pr-row-main");
  const no = el("button", "pr-no", number);
  no.type = "button";
  no.title = "Показати фрагмент на аркуші";
  no.onclick = focus;
  main.appendChild(no);

  const meta = el("div", "pr-meta");
  if (source.url) meta.appendChild(link("pr-url", source.url, source.label || source.url));
  else meta.appendChild(el("div", "pr-url", source.label || ""));
  const type = el("div", "pr-type", source.reason_label || "");
  if (source.evidence) type.title = source.evidence;
  meta.appendChild(type);
  main.appendChild(meta);

  const similarity = source.url
    ? link("pr-similarity", source.url, source.percent_text || "?")
    : el("div", "pr-similarity", source.percent_text || "?");
  similarity.appendChild(el("span", "pr-chevron", "›"));
  main.appendChild(similarity);
  row.appendChild(main);

  const actions = el("div", "pr-actions");
  const decide = el("button", "");
  decide.type = "button";
  const label = () => {
    decide.textContent = excluded ? "Повернути" : "Виключити";
  };
  label();
  decide.onclick = () => {
    excluded = !excluded;
    label();
    row.classList.toggle("pr-excluded", excluded);
    (fragsByNumber.get(number) || []).forEach((node) => {
      node.classList.toggle("pr-excluded", excluded);
    });
    root.querySelectorAll(`.pr-badge-only[data-n="${number}"]`).forEach((node) => {
      node.classList.toggle("pr-excluded", excluded);
    });
    send(root, { type: "decision", number, manual: excluded ? "exclude" : "keep" });
  };
  actions.appendChild(decide);

  if (source.manual) {
    const cancel = el("button", "", "Скасувати ручне рішення");
    cancel.type = "button";
    cancel.onclick = () => send(root, { type: "decision", number, manual: null });
    actions.appendChild(cancel);
  }
  if (source.document_url) {
    actions.appendChild(link("", source.document_url, "Документ", "Документ, який отримав застосунок"));
  }
  if (source.archive_url) {
    actions.appendChild(link("", source.archive_url, "Архів", "Усі знімки цієї адреси у Web Archive"));
  }
  row.appendChild(actions);
  return row;
}
