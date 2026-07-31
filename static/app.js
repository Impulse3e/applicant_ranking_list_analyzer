const form = document.querySelector("#search-form");
const codeInput = document.querySelector("#applicant-code");
const toggleCodeButton = document.querySelector("#toggle-code");
const searchButton = document.querySelector("#search-button");
const message = document.querySelector("#message");
const results = document.querySelector("#results");
const entriesContainer = document.querySelector("#entries");
const matchesValue = document.querySelector("#matches-value");
const activeValue = document.querySelector("#active-value");
const bestPositionValue = document.querySelector("#best-position-value");
const bestEffectiveValue = document.querySelector("#best-effective-value");
const freshness = document.querySelector("#freshness");
const entrySearch = document.querySelector("#entry-search");
const verdictFilters = document.querySelector("#verdict-filters");
const filterStatus = document.querySelector("#filter-status");

const VERDICT_LABELS = {
  guaranteed: "Гарантированно прохожу",
  likely: "Прохожу по предположению",
  possible: "Прохожу по предположению",
  no: "Не прохожу ни при каких сценариях",
  unknown: "Квота неизвестна",
};

const VERDICT_BADGE_CLASS = {
  guaranteed: "badge pass",
  likely: "badge active",
  possible: "badge active",
  no: "badge warn",
  unknown: "badge",
};

// Chip value -> verdicts it accepts.
const VERDICT_GROUPS = {
  all: null,
  guaranteed: ["guaranteed"],
  assumed: ["likely", "possible"],
  no: ["no"],
  unknown: ["unknown"],
};

let loadedEntries = [];
let activeVerdict = "all";

const text = (tagName, value, className) => {
  const element = document.createElement(tagName);
  element.textContent = value ?? "—";
  if (className) {
    element.className = className;
  }
  return element;
};

const formatDate = (value) => {
  if (!value) {
    return "время снимка не указано";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "long",
    timeStyle: "short",
    timeZone: "Europe/Moscow",
  }).format(parsed);
};

const showMessage = (value, isError = false) => {
  message.textContent = value;
  message.classList.toggle("error", isError);
  message.hidden = false;
};

const hideMessage = () => {
  message.textContent = "";
  message.hidden = true;
};

const addFact = (list, label, value) => {
  const wrapper = document.createElement("div");
  wrapper.append(text("dt", label));
  wrapper.append(text("dd", value ?? "—"));
  list.append(wrapper);
};

const seatLabel = (value) => {
  if (value === true) {
    return "да";
  }
  if (value === false) {
    return "нет";
  }
  return "квота не задана";
};

const confidenceLabel = (confidence) =>
  confidence === "likely" ? "вероятно" : "возможно";

const renderCascade = (cascade) => {
  const block = document.createElement("div");
  block.className = "cascade-block";
  block.append(text("h4", "Каскад выше вас"));

  const facts = document.createElement("dl");
  facts.className = "entry-facts cascade-facts";
  addFact(facts, "Конкурентов выше", cascade.competitors_above);
  addFact(facts, "Вероятно уйдут", cascade.likely_leavers);
  addFact(facts, "Возможно уйдут", cascade.possible_leavers);
  addFact(facts, "Эффект. позиция", cascade.effective_position_likely);
  addFact(
    facts,
    "Эффект. с «возможно»",
    cascade.effective_position_possible,
  );
  addFact(facts, "Мест в квоте", cascade.seats ?? "не задано");
  addFact(facts, "В квоте официально", seatLabel(cascade.within_seats_official));
  addFact(facts, "В квоте после каскада", seatLabel(cascade.within_seats_likely));
  addFact(
    facts,
    "В квоте при «возможно»",
    seatLabel(cascade.within_seats_possible),
  );
  block.append(facts);

  if (cascade.reasons?.length) {
    const list = document.createElement("ul");
    list.className = "cascade-reasons";
    for (const reason of cascade.reasons) {
      list.append(
        text(
          "li",
          `${reason.count} ${reason.label} (${confidenceLabel(reason.confidence)})`,
        ),
      );
    }
    block.append(list);
  }

  if (cascade.notes?.length) {
    for (const note of cascade.notes) {
      block.append(text("p", note, "cascade-note"));
    }
  }

  return block;
};

const renderOwnPriority = (items) => {
  if (!items?.length) {
    return null;
  }
  const block = document.createElement("div");
  block.className = "cascade-block own-priority";
  block.append(text("h4", "Ваш приоритет"));
  const list = document.createElement("ul");
  list.className = "cascade-reasons";
  for (const item of items) {
    list.append(
      text(
        "li",
        `Скорее уйдёте на ${item.destination} — ${item.label} (${confidenceLabel(item.confidence)})`,
      ),
    );
  }
  block.append(list);
  return block;
};

const renderEntry = (entry) => {
  const card = document.createElement("article");
  card.className = "entry-card";

  const identity = document.createElement("div");
  identity.append(text("h3", entry.source.program, "entry-program"));

  const metadata = document.createElement("div");
  metadata.className = "entry-meta";
  metadata.append(text("span", entry.source.list_type, "badge"));
  metadata.append(
    text(
      "span",
      entry.status,
      entry.status === "Участвуете в конкурсе" ? "badge active" : "badge",
    ),
  );
  const verdict = entry.pass_verdict ?? "unknown";
  metadata.append(
    text("span", VERDICT_LABELS[verdict], VERDICT_BADGE_CLASS[verdict]),
  );
  identity.append(metadata);
  identity.append(
    text(
      "p",
      `Снимок: ${formatDate(entry.source.snapshot_at)} · ${entry.source.file_name}`,
      "entry-source",
    ),
  );

  const details = document.createElement("div");
  details.className = "entry-details";

  const facts = document.createElement("dl");
  facts.className = "entry-facts";
  addFact(facts, "Позиция", entry.position);
  addFact(facts, "Приоритет", entry.priority);
  addFact(facts, "Сумма баллов", entry.total_score);
  addFact(facts, "Баллы за ВИ", entry.exam_scores);
  addFact(facts, "Баллы за ИД", entry.individual_score);
  addFact(facts, entry.confirmation_label, entry.confirmation_value);
  addFact(facts, "Выбор конкурсной группы", entry.selected_at);
  details.append(facts);

  if (entry.cascade) {
    details.append(renderCascade(entry.cascade));
  }
  const ownPriority = renderOwnPriority(entry.own_priority);
  if (ownPriority) {
    details.append(ownPriority);
  }

  card.append(identity, details);
  return card;
};

const matchesQuery = (entry, query) => {
  if (!query) {
    return true;
  }
  const haystack = [
    entry.source.program,
    entry.source.list_type,
    entry.source.file_name,
  ]
    .join(" ")
    .toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((token) => haystack.includes(token));
};

const applyFilters = () => {
  const query = entrySearch.value.trim();
  const allowed = VERDICT_GROUPS[activeVerdict];
  const visible = loadedEntries.filter(
    (entry) =>
      matchesQuery(entry, query) &&
      (allowed === null || allowed.includes(entry.pass_verdict ?? "unknown")),
  );

  entriesContainer.replaceChildren(...visible.map(renderEntry));
  filterStatus.textContent = `Показано ${visible.length} из ${loadedEntries.length} таблиц.`;

  if (!visible.length && loadedEntries.length) {
    entriesContainer.append(
      text(
        "p",
        "Под выбранные фильтры ничего не подошло. Смягчите условия или очистите поиск.",
        "empty-state",
      ),
    );
  }
};

const renderResults = (data) => {
  matchesValue.textContent = data.summary.matches;
  activeValue.textContent = data.summary.active;
  bestPositionValue.textContent = data.summary.best_position ?? "—";
  bestEffectiveValue.textContent = data.summary.best_effective_position ?? "—";
  freshness.textContent = `Последний найденный снимок: ${formatDate(
    data.summary.latest_snapshot,
  )}`;

  loadedEntries = data.entries;
  applyFilters();
  results.hidden = false;
  results.scrollIntoView({ behavior: "smooth", block: "start" });
};

entrySearch.addEventListener("input", applyFilters);

verdictFilters.addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) {
    return;
  }
  activeVerdict = chip.dataset.verdict;
  for (const button of verdictFilters.querySelectorAll(".chip")) {
    const isActive = button === chip;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  }
  applyFilters();
});

toggleCodeButton.addEventListener("click", () => {
  const isVisible = codeInput.type === "text";
  codeInput.type = isVisible ? "password" : "text";
  toggleCodeButton.textContent = isVisible ? "Показать" : "Скрыть";
  toggleCodeButton.setAttribute("aria-pressed", String(!isVisible));
  codeInput.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideMessage();
  results.hidden = true;
  entriesContainer.replaceChildren();

  let applicantCode = codeInput.value.trim();
  codeInput.value = "";
  codeInput.type = "password";
  toggleCodeButton.textContent = "Показать";
  toggleCodeButton.setAttribute("aria-pressed", "false");

  if (!/^\d{1,20}$/.test(applicantCode)) {
    applicantCode = "";
    showMessage("Код должен содержать от 1 до 20 цифр.", true);
    codeInput.focus();
    return;
  }

  const requestBody = JSON.stringify({ applicant_code: applicantCode });
  applicantCode = "";
  searchButton.disabled = true;
  searchButton.textContent = "Ищем…";

  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: requestBody,
      cache: "no-store",
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "Не удалось выполнить поиск.");
    }
    if (!data.found) {
      showMessage(
        "Код не найден в загруженных таблицах. Проверьте ввод и попробуйте ещё раз.",
      );
      return;
    }

    renderResults(data);
  } catch (error) {
    showMessage(
      error instanceof Error
        ? error.message
        : "Сервис временно недоступен. Повторите попытку позже.",
      true,
    );
  } finally {
    searchButton.disabled = false;
    searchButton.textContent = "Найти позиции";
  }
});
