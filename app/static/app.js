// Brand colors for the default platforms; anything user-added gets a stable hashed hue.
const PLATFORM_COLORS = {
  steam: "#1b2838",
  playstation: "#003791",
  xbox: "#107c10",
  nintendo: "#e60012",
};

let platforms = [];
let statuses = [];
let currentStatus = "";
let currentPlatform = "";
let currentSearch = "";

const list = document.getElementById("game-list");
const dialog = document.getElementById("game-dialog");
const form = document.getElementById("game-form");
const countEl = document.getElementById("count");
const tabsEl = document.getElementById("status-tabs");
const platformFilter = document.getElementById("platform-filter");
const suggestionsEl = document.getElementById("suggestions");
const titleInput = document.getElementById("title");
const coverPreview = document.getElementById("cover-preview");
const coverPreviewImg = document.getElementById("cover-preview-img");
const dupeWarning = document.getElementById("dupe-warning");

async function checkDuplicate(title) {
  const editingId = document.getElementById("game-id").value;
  let games;
  try {
    games = await (await fetch("/api/games")).json();
  } catch {
    return;
  }
  const matches = games.filter(
    (g) => g.title.toLowerCase() === title.toLowerCase() && String(g.id) !== editingId
  );
  if (matches.length === 0) {
    dupeWarning.hidden = true;
    return;
  }
  const where = matches
    .map((g) => `${labelFor(platforms, g.platform)} · ${labelFor(statuses, g.status)}`)
    .join(", ");
  dupeWarning.textContent = `Already in your list: ${where}`;
  dupeWarning.hidden = false;
}

// Cover selection state for the open dialog
let selectedCoverId = null;
let coverDirty = false;

function showCoverPreview(src) {
  coverPreviewImg.src = src;
  coverPreview.hidden = false;
}

function hideCoverPreview() {
  coverPreview.hidden = true;
  coverPreviewImg.src = "";
}

let searchTimer = null;
let searchAvailable = true;

function hideSuggestions() {
  suggestionsEl.hidden = true;
  suggestionsEl.innerHTML = "";
}

async function runSearch(q) {
  if (!searchAvailable || q.length < 3) {
    hideSuggestions();
    return;
  }
  let results;
  try {
    const res = await fetch(`/api/search-external?q=${encodeURIComponent(q)}`);
    if (!res.ok) {
      // 503 = no API keys configured; stop asking for this page load
      if (res.status === 503) searchAvailable = false;
      hideSuggestions();
      return;
    }
    results = await res.json();
  } catch {
    hideSuggestions();
    return;
  }
  // Ignore stale responses and clear the list when nothing matched
  if (results.length === 0 || titleInput.value.trim() !== q) {
    hideSuggestions();
    return;
  }
  suggestionsEl.innerHTML = "";
  for (const r of results) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "suggestion";
    row.innerHTML = `
      ${r.thumb ? `<img src="${r.thumb}" alt="">` : `<span class="no-thumb"></span>`}
      <span class="suggestion-name"></span>
      <span class="suggestion-year">${r.year ?? ""}</span>
    `;
    row.querySelector(".suggestion-name").textContent = r.name;
    row.addEventListener("click", () => {
      titleInput.value = r.name;
      selectedCoverId = r.cover_image_id || null;
      coverDirty = true;
      hideSuggestions();
      if (r.thumb) showCoverPreview(r.thumb);
      else hideCoverPreview();
      checkDuplicate(r.name);
    });
    suggestionsEl.appendChild(row);
  }
  suggestionsEl.hidden = false;
}

titleInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  dupeWarning.hidden = true;
  const q = titleInput.value.trim();
  if (q.length < 3) {
    hideSuggestions();
    return;
  }
  searchTimer = setTimeout(() => runSearch(q), 300);
});

document.getElementById("clear-cover-btn").addEventListener("click", () => {
  selectedCoverId = null;
  coverDirty = true;
  hideCoverPreview();
});

function platformColor(value) {
  if (PLATFORM_COLORS[value]) return PLATFORM_COLORS[value];
  let h = 0;
  for (const c of value) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 45% 35%)`;
}

function labelFor(items, value) {
  const item = items.find((i) => i.value === value);
  return item ? item.label : value;
}

async function loadLookups() {
  [platforms, statuses] = await Promise.all([
    fetch("/api/platforms").then((r) => r.json()),
    fetch("/api/statuses").then((r) => r.json()),
  ]);

  tabsEl.innerHTML = "";
  const allTab = document.createElement("button");
  allTab.className = "tab" + (currentStatus === "" ? " active" : "");
  allTab.textContent = "All";
  allTab.dataset.status = "";
  tabsEl.appendChild(allTab);
  for (const s of statuses) {
    const tab = document.createElement("button");
    tab.className = "tab" + (currentStatus === s.value ? " active" : "");
    tab.textContent = s.label;
    tab.dataset.status = s.value;
    tabsEl.appendChild(tab);
  }
  tabsEl.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      tabsEl.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      currentStatus = tab.dataset.status;
      render();
    });
  });

  platformFilter.innerHTML = `<option value="">All</option>`;
  for (const p of platforms) {
    platformFilter.appendChild(new Option(p.label, p.value));
  }
  platformFilter.value = currentPlatform;

  const platformSelect = document.getElementById("platform");
  platformSelect.innerHTML = "";
  for (const p of platforms) platformSelect.appendChild(new Option(p.label, p.value));

  const statusSelect = document.getElementById("status");
  statusSelect.innerHTML = "";
  for (const s of statuses) statusSelect.appendChild(new Option(s.label, s.value));
}

async function fetchGames() {
  const params = new URLSearchParams();
  if (currentStatus) params.set("status", currentStatus);
  if (currentPlatform) params.set("platform", currentPlatform);
  if (currentSearch) params.set("q", currentSearch);
  const res = await fetch(`/api/games?${params}`);
  return res.json();
}

async function render() {
  const games = await fetchGames();
  countEl.textContent = `${games.length} game${games.length === 1 ? "" : "s"}`;
  list.innerHTML = "";

  if (games.length === 0) {
    list.innerHTML = `<p class="empty">Nothing here yet.</p>`;
    return;
  }

  for (const game of games) {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.id = game.id;
    card.innerHTML = `
      <span class="drag-handle" title="Drag to reorder">&#x2630;</span>
      ${game.cover ? `<img class="cover" src="${game.cover}" alt="" loading="lazy">` : ""}
      <div class="card-body">
        <div class="card-main">
          <span class="game-title"></span>
          <span class="badge"></span>
          ${game.rating ? `<span class="rating">★ ${game.rating}/10</span>` : ""}
        </div>
        ${game.notes ? `<p class="notes"></p>` : ""}
        <div class="card-actions">
          <select class="status-select">
            ${statuses
              .map((s) => `<option value="${s.value}" ${s.value === game.status ? "selected" : ""}>${s.label}</option>`)
              .join("")}
          </select>
          <button class="edit-btn">Edit</button>
          <button class="delete-btn">Delete</button>
        </div>
      </div>
    `;
    card.querySelector(".game-title").textContent = game.title;
    const badge = card.querySelector(".badge");
    badge.textContent = labelFor(platforms, game.platform);
    badge.style.background = platformColor(game.platform);
    if (game.notes) card.querySelector(".notes").textContent = game.notes;

    // A game can hold a status that was since deleted; show it rather than lie.
    const statusSelect = card.querySelector(".status-select");
    if (!statuses.some((s) => s.value === game.status)) {
      statusSelect.appendChild(new Option(game.status, game.status, true, true));
    }

    statusSelect.addEventListener("change", async (e) => {
      await fetch(`/api/games/${game.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: e.target.value }),
      });
      render();
    });

    card.querySelector(".edit-btn").addEventListener("click", () => openDialog(game));

    card.querySelector(".delete-btn").addEventListener("click", async () => {
      if (!confirm(`Delete "${game.title}"?`)) return;
      await fetch(`/api/games/${game.id}`, { method: "DELETE" });
      render();
    });

    // Draggable only while grabbed by the handle, so selects/buttons stay usable
    const handle = card.querySelector(".drag-handle");
    handle.addEventListener("mousedown", () => (card.draggable = true));
    card.addEventListener("dragstart", (e) => {
      draggedCard = card;
      orderBeforeDrag = cardOrder().join(",");
      card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });
    card.addEventListener("dragend", async () => {
      card.classList.remove("dragging");
      card.draggable = false;
      draggedCard = null;
      const ids = cardOrder();
      if (ids.join(",") === orderBeforeDrag) return;
      const res = await fetch("/api/games/reorder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      if (!res.ok) render();
    });

    list.appendChild(card);
  }
}

let draggedCard = null;
let orderBeforeDrag = "";

function cardOrder() {
  return [...list.querySelectorAll(".card")].map((c) => Number(c.dataset.id));
}

list.addEventListener("dragover", (e) => {
  if (!draggedCard) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  const cards = [...list.querySelectorAll(".card:not(.dragging)")];
  const after = cards.find((c) => {
    const rect = c.getBoundingClientRect();
    return e.clientY < rect.top + rect.height / 2;
  });
  if (after) list.insertBefore(draggedCard, after);
  else list.appendChild(draggedCard);
});

list.addEventListener("drop", (e) => e.preventDefault());

function openDialog(game = null) {
  selectedCoverId = null;
  coverDirty = false;
  clearTimeout(searchTimer);
  hideSuggestions();
  dupeWarning.hidden = true;
  if (game && game.cover) showCoverPreview(game.cover);
  else hideCoverPreview();
  document.getElementById("dialog-title").textContent = game ? "Edit Game" : "Add Game";
  document.getElementById("game-id").value = game ? game.id : "";
  document.getElementById("title").value = game ? game.title : "";
  const defaultStatus = statuses.some((s) => s.value === "want_to_play")
    ? "want_to_play"
    : statuses[0]?.value || "";
  document.getElementById("platform").value = game ? game.platform : platforms[0]?.value || "";
  document.getElementById("status").value = game ? game.status : currentStatus || defaultStatus;
  document.getElementById("rating").value = game && game.rating ? game.rating : "";
  document.getElementById("notes").value = game ? game.notes : "";
  dialog.showModal();
  // Editing a game with no art yet: offer covers for its title right away
  if (game && !game.cover) runSearch(game.title.trim());
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("game-id").value;
  const rating = document.getElementById("rating").value;
  const payload = {
    title: document.getElementById("title").value.trim(),
    platform: document.getElementById("platform").value,
    status: document.getElementById("status").value,
    rating: rating ? Number(rating) : null,
    notes: document.getElementById("notes").value,
  };
  if (coverDirty) payload.cover_image_id = selectedCoverId;
  if (!payload.title) return;

  await fetch(id ? `/api/games/${id}` : "/api/games", {
    method: id ? "PATCH" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  dialog.close();
  render();
});

document.getElementById("add-btn").addEventListener("click", () => openDialog());
document.getElementById("cancel-btn").addEventListener("click", () => dialog.close());

platformFilter.addEventListener("change", (e) => {
  currentPlatform = e.target.value;
  render();
});

let listSearchTimer = null;
document.getElementById("search-input").addEventListener("input", (e) => {
  clearTimeout(listSearchTimer);
  listSearchTimer = setTimeout(() => {
    currentSearch = e.target.value.trim();
    render();
  }, 250);
});

loadLookups().then(render);
