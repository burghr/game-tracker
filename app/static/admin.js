async function apiCall(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    alert(message);
    return null;
  }
  return res.status === 204 ? true : res.json();
}

function setupSection(name, listId, formId, inputId) {
  const listEl = document.getElementById(listId);
  const endpoint = `/api/${name}`;

  async function refresh() {
    const items = await apiCall(endpoint);
    if (!items) return;
    listEl.innerHTML = "";
    for (const item of items) {
      const li = document.createElement("li");
      li.innerHTML = `
        <span class="item-label"></span>
        <button class="rename-btn">Rename</button>
        <button class="delete-btn">Delete</button>
      `;
      li.querySelector(".item-label").textContent = item.label;

      li.querySelector(".rename-btn").addEventListener("click", async () => {
        const label = prompt(`Rename "${item.label}" to:`, item.label);
        if (!label || label.trim() === "" || label.trim() === item.label) return;
        const updated = await apiCall(`${endpoint}/${item.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label: label.trim() }),
        });
        if (updated) refresh();
      });

      li.querySelector(".delete-btn").addEventListener("click", async () => {
        if (!confirm(`Delete "${item.label}"?`)) return;
        const ok = await apiCall(`${endpoint}/${item.id}`, { method: "DELETE" });
        if (ok) refresh();
      });

      listEl.appendChild(li);
    }
  }

  document.getElementById(formId).addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById(inputId);
    const label = input.value.trim();
    if (!label) return;
    const created = await apiCall(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    });
    if (created) {
      input.value = "";
      refresh();
    }
  });

  refresh();
}

setupSection("platforms", "platform-list", "platform-form", "platform-input");
setupSection("statuses", "status-list", "status-form", "status-input");
