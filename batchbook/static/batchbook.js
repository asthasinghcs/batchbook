"use strict";

const newOrder = document.querySelector("[data-new-order]");
const syncPanel = document.querySelector("#sync-status");

function setupOrder(form) {
  const list = form.querySelector("[data-order-lines]");
  const template = form.querySelector("[data-line-template]");
  const add = form.querySelector("[data-add-line]");
  const subtotal = document.querySelector("#order-estimate");
  const currency = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
  function estimate() {
    const rows = [...list.querySelectorAll("[data-order-line]")];
    let amount = 0;
    let filled = false;
    for (const row of rows) {
      const product = row.querySelector("[name=product_id]").selectedOptions[0];
      const count = Number(row.querySelector("[name=count]").value);
      const pack = row.querySelector("[name=unit]").value === "carton" ? Number(product?.dataset.pack || 1) : 1;
      if (product?.value && count > 0) {
        amount += Number(product.dataset.packetPrice) * count * pack;
        filled = true;
      }
      row.querySelector("[data-remove-line]").hidden = rows.length === 1;
    }
    subtotal.textContent = filled ? currency.format(amount / 100) : "—";
    add.disabled = rows.length >= 50;
  }
  list.addEventListener("input", estimate);
  list.addEventListener("change", estimate);
  list.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove-line]");
    if (remove) {
      remove.closest("[data-order-line]").remove();
      estimate();
      add.focus();
    }
  });
  add.hidden = false;
  add.addEventListener("click", () => {
    list.append(template.content.cloneNode(true));
    list.lastElementChild.querySelector("select").focus();
    estimate();
  });
  estimate();
}

function setupSync(panel, form) {
  const account = Number(panel.dataset.account);
  const key = "batchbook-orders-" + account;
  const message = panel.querySelector("[data-sync-message]");
  const retry = panel.querySelector("[data-sync-now]");
  const discard = panel.querySelector("[data-clear-queue]");
  let syncing = false;

  function queued() {
    return JSON.parse(localStorage.getItem(key) || "[]");
  }

  function notify(text, count = 0) {
    panel.hidden = !text;
    message.textContent = text;
    retry.hidden = count === 0;
    discard.hidden = count === 0;
  }

  function uuid() {
    const data = crypto.getRandomValues(new Uint8Array(16));
    data[6] = (data[6] & 15) | 64;
    data[8] = (data[8] & 63) | 128;
    const hex = [...data].map((value) => value.toString(16).padStart(2, "0")).join("");
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
  }

  async function post(order, token) {
    const response = await fetch(panel.dataset.sync, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      body: JSON.stringify(order),
      signal: AbortSignal.timeout(8000),
    });
    if (response.status >= 500) throw new Error("Factory connection unavailable");
    return response.json();
  }

  function save(order) {
    try {
      const orders = queued();
      if (!orders.some((entry) => entry.client_ref === order.client_ref)) {
        if (orders.length >= 100) throw new Error("Queue full");
        orders.push(order);
        localStorage.setItem(key, JSON.stringify(orders));
      }
      form.reset();
      form.elements.client_ref.value = uuid();
      form.querySelector("[name=customer_id]").dispatchEvent(new Event("change", { bubbles: true }));
      const lines = form.querySelector("[data-order-lines]");
      lines.replaceChildren(form.querySelector("[data-line-template]").content.cloneNode(true));
      lines.dispatchEvent(new Event("change", { bubbles: true }));
      notify("Order saved on this device. It will sync when the factory is reachable.", orders.length);
    } catch {
      notify("This order could not be saved on the device. Keep the form open and retry.");
    }
  }

  async function sync() {
    let pending;
    try {
      pending = queued();
    } catch {
      return;
    }
    if (syncing || !pending.length) return;
    if (!navigator.onLine) {
      notify("Offline orders waiting to sync: " + pending.length, pending.length);
      return;
    }
    syncing = true;
    try {
      const tokenResponse = await fetch(panel.dataset.token, { cache: "no-store", signal: AbortSignal.timeout(8000) });
      if (tokenResponse.redirected || !tokenResponse.ok) {
        notify("Sign in again as the original salesperson to sync saved orders.", pending.length);
        return;
      }
      const session = await tokenResponse.json();
      if (session.account !== account) return;
      let problem = "";
      for (const order of pending) {
        const result = await post(order, session.csrf);
        if (result.order) {
          const remaining = queued().filter((entry) => entry.client_ref !== order.client_ref);
          localStorage.setItem(key, JSON.stringify(remaining));
        } else {
          problem = result.error;
        }
      }
      const left = queued().length;
      if (left) notify(problem || "Some orders are waiting to sync.", left);
      else {
        notify("Offline orders synced with the factory.");
        if (!form) {
          const reload = document.createElement("a");
          reload.href = window.location.href;
          reload.textContent = " Refresh orders.";
          message.append(reload);
        }
      }
    } catch {
      notify("Factory connection unavailable. Saved orders will be retried.", queued().length);
    } finally {
      syncing = false;
    }
  }

  retry.addEventListener("click", sync);
  discard.addEventListener("click", () => {
    if (window.confirm("Discard the unsent orders stored on this device?")) {
      localStorage.removeItem(key);
      notify("Offline queue cleared.");
    }
  });
  window.addEventListener("online", sync);
  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("[data-order-submit]");
      const data = new FormData(form);
      const order = {
        account,
        customer_id: data.get("customer_id"),
        client_ref: data.get("client_ref"),
        lines: data.getAll("product_id").map((product, index) =>
          [product, data.getAll("count")[index], data.getAll("unit")[index]]),
      };
      button.disabled = true;
      try {
        if (!navigator.onLine) save(order);
        else {
          const result = await post(order, data.get("csrf"));
          if (result.url) window.location.assign(result.url);
          else notify(result.error || "The factory could not accept this order.");
        }
      } catch {
        save(order);
      } finally {
        button.disabled = false;
      }
    });
  }
  sync();
}

if (newOrder) setupOrder(newOrder);
if (syncPanel) setupSync(syncPanel, newOrder);

for (const form of document.querySelectorAll("[data-unit-form]")) {
  const item = form.querySelector("[data-unit-item]");
  const unit = form.querySelector("[data-entry-unit]");
  const update = () => {
    const choices = item.selectedOptions[0]?.dataset.units?.split(",") || [];
    const oldUnit = unit.value;
    if (choices.length) {
      unit.replaceChildren(...choices.map((value) => new Option(value, value)));
      unit.value = choices.includes(oldUnit) ? oldUnit : choices[0];
    }
  };
  item.addEventListener("change", update);
  update();
}

const run = document.querySelector("[data-run-form]");
if (run) {
  const batches = run.elements.batches;
  const display = run.querySelector("[data-planned-output]");
  function showExpected() {
    const expected = Math.round(Math.round(Number(batches.value) * 1000) * Number(run.dataset.planned) / 1000);
    display.textContent = expected ? String(expected) : "—";
  }
  batches.addEventListener("input", showExpected);
  showExpected();
}

const kind = document.querySelector("[data-item-kind]");
if (kind) {
  const unit = document.querySelector("[data-master-unit]");
  const fields = document.querySelector("[data-finished-fields]");
  function showUnits() {
    const units = { raw: ["kg", "L", "piece"], packaging: ["piece"], finished: ["packet"] }[kind.value];
    const before = unit.value;
    unit.replaceChildren(...units.map((value) => new Option(value, value)));
    unit.value = units.includes(before) ? before : units[0];
    fields.hidden = kind.value !== "finished";
  }
  kind.addEventListener("change", showUnits);
  showUnits();
}
