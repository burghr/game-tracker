// Wishlist: games you want, released or not. Search goes straight to IGDB;
// nothing is stored until you actually add something.

let platforms = [];
let statuses = [];
let currentPlatform = "";

const listEl = document.getElementById("calendar-list");
const countEl = document.getElementById("cal-count");
const platformFilter = document.getElementById("cal-platform");
const searchInput = document.getElementById("release-search");
const searchResults = document.getElementById("search-results");
const searchHint = document.getElementById("search-hint");
const syncStatus = document.getElementById("sync-status");

const releaseDialog = document.getElementById("release-dialog");
const releaseForm = document.getElementById("release-form");
const releaseTitle = document.getElementById("release-title");
const releaseSuggestions = document.getElementById("release-suggestions");
const releaseCoverId = document.getElementById("release-cover-id");
const libraryDialog = document.getElementById("library-dialog");
const libraryForm = document.getElementById("library-form");

const MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function monthKey(iso) {
  const [y, m] = iso.split("-").map(Number);
  return `${MONTHS[m - 1]} ${y}`;
}

// A year-only IGDB date sorts to Dec 31, which would file it under December.
// Give those their own heading so the list doesn't claim a month it doesn't know.
function groupKey(r) {
  if (!r.precise && /^\d{4}$/.test(r.human)) return `${r.human} · date TBA`;
  return monthKey(r.date);
}

function shortDate(iso) {
  const [, m, d] = iso.split("-").map(Number);
  return `${MONTHS[m - 1].slice(0, 3)} ${d}`;
}

async function loadLookups() {
  [platforms, statuses] = await Promise.all([
    fetch("/api/platforms").then((r) => r.json()),
    fetch("/api/statuses").then((r) => r.json()),
  ]);

  platformFilter.innerHTML = `<option value="">All</option>`;
  for (const p of platforms) platformFilter.appendChild(new Option(p.label, p.value));
  platformFilter.value = currentPlatform;

  const libPlatform = document.getElementById("library-platform");
  libPlatform.innerHTML = "";
  for (const p of platforms) libPlatform.appendChild(new Option(p.label, p.value));

  const libStatus = document.getElementById("library-status");
  libStatus.innerHTML = "";
  for (const s of statuses) libStatus.appendChild(new Option(s.label, s.value));
}

// --- Search: straight to IGDB ----------------------------------------------

let searchTimer = null;

function searchRow(r) {
  const row = document.createElement("div");
  row.className = "search-row";
  row.innerHTML = `
    ${r.thumb ? `<img src="${r.thumb}" alt="" loading="lazy">` : `<span class="no-thumb"></span>`}
    <span class="search-row-body">
      <span class="search-row-title"></span>
      <span class="search-row-meta"></span>
    </span>
    <button class="track-btn">${r.on_list ? "★ On list" : "+ Add"}</button>
  `;
  row.querySelector(".search-row-title").textContent = r.title;
  row.querySelector(".search-row-meta").textContent =
    `${r.human}${r.platforms ? ` · ${r.platforms}` : ""}`;

  const btn = row.querySelector(".track-btn");
  if (r.on_list) {
    btn.classList.add("tracked");
    btn.disabled = true;
  }
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const res = await fetch("/api/releases/wishlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ igdb_game_id: r.igdb_game_id }),
    });
    if (!res.ok) {
      btn.disabled = false;
      alert("Could not add that game.");
      return;
    }
    r.on_list = true;
    btn.textContent = "★ On list";
    btn.classList.add("tracked");
    renderWishlist();
  });
  return row;
}

async function runSearch(q) {
  let results;
  try {
    const res = await fetch(`/api/releases/search?q=${encodeURIComponent(q)}`);
    if (!res.ok) throw new Error();
    results = await res.json();
  } catch {
    searchResults.hidden = true;
    searchHint.textContent = "Search failed.";
    searchHint.hidden = false;
    return;
  }
  // A slower earlier request must not overwrite a newer query's results
  if (searchInput.value.trim() !== q) return;

  searchResults.innerHTML = "";
  if (results.length === 0) {
    searchResults.hidden = true;
    searchHint.textContent =
      `Nothing on IGDB matches "${q}". Use + Manual entry to add it yourself.`;
    searchHint.hidden = false;
    return;
  }
  searchHint.hidden = true;
  for (const r of results) searchResults.appendChild(searchRow(r));
  searchResults.hidden = false;
}

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = searchInput.value.trim();
  if (q.length < 2) {
    searchResults.hidden = true;
    searchHint.hidden = true;
    return;
  }
  searchTimer = setTimeout(() => runSearch(q), 250);
});

// --- The wishlist -----------------------------------------------------------

function wishlistCard(r) {
  const card = document.createElement("article");
  card.className = "card release-card";
  const out = r.date !== null && r.date <= todayIso();

  // IGDB pins vague dates to the end of their period ("2027" becomes Dec 31),
  // so show its own wording rather than a day it doesn't actually claim.
  let when;
  if (r.date === null) when = "TBA";
  else if (out) when = r.date.slice(0, 4);
  else when = r.precise ? shortDate(r.date) : r.human;

  card.innerHTML = `
    ${r.thumb ? `<img class="cover" src="${r.thumb}" alt="" loading="lazy">` : `<span class="cover no-thumb"></span>`}
    <div class="card-body">
      <div class="card-main">
        <span class="release-date ${out ? "out" : ""} ${r.precise ? "" : "approx"}">${when}</span>
        <span class="game-title"></span>
        ${r.source === "manual" ? `<span class="badge manual">Manual</span>` : ""}
        ${r.in_library ? `<span class="badge in-library"></span>` : ""}
      </div>
      <div class="platform-line"></div>
      <p class="notes" hidden></p>
      <div class="card-actions">
        <button class="untrack-btn">Remove</button>
        <button class="library-btn" ${r.in_library ? "disabled" : ""}>Add to library</button>
        <button class="edit-btn">Edit</button>
      </div>
    </div>
  `;

  card.querySelector(".game-title").textContent = r.title;
  card.querySelector(".platform-line").textContent = r.platforms || "Platform TBA";
  const libraryBadge = card.querySelector(".in-library");
  if (libraryBadge) {
    // textContent, not innerHTML: status labels are user-editable in /admin
    libraryBadge.textContent = r.library_status
      ? `In library · ${r.library_status}`
      : "In library";
  }
  if (r.notes) {
    const notes = card.querySelector(".notes");
    notes.textContent = r.notes;
    notes.hidden = false;
  }

  card.querySelector(".untrack-btn").addEventListener("click", async () => {
    await fetch(`/api/releases/${r.id}`, { method: "DELETE" });
    renderWishlist();
    // Keep any open search results in sync with the change
    const q = searchInput.value.trim();
    if (q.length >= 2) runSearch(q);
  });

  card.querySelector(".library-btn").addEventListener("click", () => {
    document.getElementById("library-release-id").value = r.id;
    document.getElementById("library-game-title").textContent = r.title;
    libraryDialog.showModal();
  });

  const editBtn = card.querySelector(".edit-btn");
  if (editBtn) editBtn.addEventListener("click", () => openReleaseDialog(r));

  return card;
}

function appendSection(label, rows) {
  if (rows.length === 0) return;
  const heading = document.createElement("h2");
  heading.className = "day-heading";
  heading.textContent = label;
  listEl.appendChild(heading);
  for (const r of rows) listEl.appendChild(wishlistCard(r));
}

async function renderWishlist() {
  listEl.innerHTML = `<p class="empty">Loading…</p>`;
  const params = new URLSearchParams();
  if (currentPlatform) params.set("platform", currentPlatform);

  let releases;
  try {
    const res = await fetch(`/api/releases?${params}`);
    if (!res.ok) throw new Error();
    releases = await res.json();
  } catch {
    listEl.innerHTML = `<p class="empty">Could not load your wishlist.</p>`;
    countEl.textContent = "";
    return;
  }

  countEl.textContent = `${releases.length} game${releases.length === 1 ? "" : "s"}`;
  listEl.innerHTML = "";

  if (releases.length === 0) {
    listEl.innerHTML = `<p class="empty">
      Nothing on the wishlist yet. Search above for anything on IGDB, out or not,
      or use + Manual entry for something it doesn't list.
    </p>`;
    return;
  }

  const today = todayIso();
  const upcoming = releases.filter((r) => r.date !== null && r.date > today);
  // Already out: most recent first, so this year's games beat 1998's
  const out = releases.filter((r) => r.date !== null && r.date <= today).reverse();
  const undated = releases.filter((r) => r.date === null);

  // Bucket upcoming by month first, then render. Grouping inline would emit a
  // duplicate heading whenever a precise and a TBA row share the same day.
  const groups = new Map();
  for (const r of upcoming) {
    const key = groupKey(r);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  for (const [key, rows] of groups) appendSection(key, rows);

  appendSection("Out now", out);
  appendSection("No date yet", undated);
}

// --- Manual entries ---------------------------------------------------------
// IGDB has no physical/digital axis, so a boxed edition of an already-released
// game has to be typed in here.

function openReleaseDialog(release) {
  // IGDB owns the title, date and platforms on its rows and rewrites them on
  // every refresh, so those stay read-only. Your notes are always yours.
  const igdbOwned = release !== null && release.source === "igdb";
  editingIgdbRow = igdbOwned;

  document.getElementById("release-dialog-title").textContent =
    release ? (igdbOwned ? "Edit note" : "Edit release") : "Add release";
  document.getElementById("release-id").value = release ? release.id : "";
  releaseTitle.value = release ? release.title : "";
  document.getElementById("release-date").value = release ? release.date ?? "" : "";
  document.getElementById("release-platforms").value = release ? release.platforms : "";
  document.getElementById("release-notes").value = release ? release.notes : "";
  releaseCoverId.value = release ? release.cover_image_id : "";
  document.getElementById("release-delete-btn").hidden = !release;

  for (const id of ["release-title", "release-date", "release-platforms"]) {
    document.getElementById(id).disabled = igdbOwned;
  }
  document.getElementById("igdb-owned-hint").hidden = !igdbOwned;

  releaseSuggestions.hidden = true;
  releaseSuggestions.innerHTML = "";
  releaseDialog.showModal();
}

let titleTimer = null;
let igdbSearchAvailable = true;
let editingIgdbRow = false;

async function suggestTitles(q) {
  if (!igdbSearchAvailable) return;
  let results;
  try {
    const res = await fetch(`/api/search-external?q=${encodeURIComponent(q)}`);
    if (!res.ok) {
      if (res.status === 503) igdbSearchAvailable = false;
      releaseSuggestions.hidden = true;
      return;
    }
    results = await res.json();
  } catch {
    releaseSuggestions.hidden = true;
    return;
  }
  if (results.length === 0 || releaseTitle.value.trim() !== q) {
    releaseSuggestions.hidden = true;
    return;
  }
  releaseSuggestions.innerHTML = "";
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
      releaseTitle.value = r.name;
      releaseCoverId.value = r.cover_image_id || "";
      releaseSuggestions.hidden = true;
    });
    releaseSuggestions.appendChild(row);
  }
  releaseSuggestions.hidden = false;
}

releaseTitle.addEventListener("input", () => {
  clearTimeout(titleTimer);
  if (editingIgdbRow) return;
  const q = releaseTitle.value.trim();
  if (q.length < 3) {
    releaseSuggestions.hidden = true;
    return;
  }
  titleTimer = setTimeout(() => suggestTitles(q), 300);
});

releaseForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("release-id").value;
  const notes = document.getElementById("release-notes").value.trim();
  // The server rejects any other field on an IGDB row, so send notes alone
  const payload = editingIgdbRow
    ? { notes }
    : {
        // An empty date input yields "", which isn't a valid date server-side
        release_date: document.getElementById("release-date").value || null,
        title: releaseTitle.value.trim(),
        platforms: document.getElementById("release-platforms").value.trim(),
        notes,
      };
  if (!id) payload.cover_image_id = releaseCoverId.value || null;
  const res = await fetch(id ? `/api/releases/${id}` : "/api/releases", {
    method: id ? "PATCH" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    alert("Could not save that release.");
    return;
  }
  releaseDialog.close();
  renderWishlist();
});

document.getElementById("release-delete-btn").addEventListener("click", async () => {
  const id = document.getElementById("release-id").value;
  if (!id || !confirm("Delete this manual entry?")) return;
  await fetch(`/api/releases/${id}`, { method: "DELETE" });
  releaseDialog.close();
  renderWishlist();
});

libraryForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("library-release-id").value;
  const res = await fetch(`/api/releases/${id}/add-to-library`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      platform: document.getElementById("library-platform").value,
      status: document.getElementById("library-status").value,
    }),
  });
  if (!res.ok) {
    alert("Could not add that game.");
    return;
  }
  libraryDialog.close();
  renderWishlist();
});

document.getElementById("release-cancel-btn").addEventListener("click", () => releaseDialog.close());
document.getElementById("library-cancel-btn").addEventListener("click", () => libraryDialog.close());
document.getElementById("add-manual-btn").addEventListener("click", () => openReleaseDialog(null));

platformFilter.addEventListener("change", () => {
  currentPlatform = platformFilter.value;
  renderWishlist();
});

document.getElementById("sync-btn").addEventListener("click", async (e) => {
  e.target.disabled = true;
  syncStatus.textContent = "Syncing…";
  try {
    const res = await fetch("/api/releases/refresh", { method: "POST" });
    const data = await res.json();
    syncStatus.textContent = res.ok ? `Refreshed ${data.refreshed} games.` : "Refresh failed.";
  } catch {
    syncStatus.textContent = "Refresh failed.";
  }
  e.target.disabled = false;
  renderWishlist();
});

loadLookups().then(renderWishlist);
