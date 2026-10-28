/* CloudCare — in-app notifications: topbar bell, dropdown and 60s poll.
   Load AFTER auth.js on the patient and doctor dashboards.

   There is no email transport, so every update the API queued for this user
   is delivered here: the unread count on the bell, a list of the newest 30
   and a toast for anything that arrived since the previous poll. */
(function () {
  'use strict';

  const POLL_MS = 60000;

  let ccNotifItems = [];
  let ccNotifSeen = null; // ids from the previous poll; null = first load
  let ccNotifLoading = false;

  function byId(id) {
    return document.getElementById(id);
  }

  function whenLabel(value) {
    const date = new Date(value);
    if (isNaN(date.getTime())) return '';
    if (typeof ccClinicStamp === 'function') return ccClinicStamp(date);
    return date.toLocaleString('en-IN', {
      day: 'numeric',
      month: 'short',
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  function render() {
    const list = byId('ccNotifyList');
    const count = byId('ccNotifyCount');
    if (!list) return;

    const unread = ccNotifItems.filter((n) => !n.read).length;
    if (count) {
      count.textContent = String(unread);
      count.hidden = unread === 0;
    }
    if (!ccNotifItems.length) {
      list.innerHTML = '<div class="cc-notify-item">No notifications yet.</div>';
      return;
    }
    list.innerHTML = ccNotifItems.map((n) => {
      return `<div class="cc-notify-item${n.read ? '' : ' unread'}" data-notif-id="${ccEscape(n.id)}">
        ${ccEscape(n.message)}
        <span>${ccEscape(whenLabel(n.created_at))}</span>
      </div>`;
    }).join('');
  }

  async function load(showToasts) {
    if (ccNotifLoading) return;
    ccNotifLoading = true;
    try {
      const items = await ccApi('/api/notifications?limit=30', { noRedirect: true });
      const fresh = ccNotifSeen === null
        ? []
        : items.filter((n) => !ccNotifSeen.has(n.id) && !n.read);
      ccNotifItems = items;
      ccNotifSeen = new Set(items.map((n) => n.id));
      render();
      if (showToasts) {
        fresh.slice(0, 3).forEach((n) => ccToast(n.message));
      }
    } catch (err) {
      /* Signed out or offline: leave the bell exactly as it was. */
    } finally {
      ccNotifLoading = false;
    }
  }

  async function markRead(payload) {
    try {
      await ccApi('/api/notifications/read', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      await load(false);
    } catch (err) {
      ccToast(err.message || 'Could not update notifications.');
    }
  }

  function init() {
    const root = byId('ccNotify');
    if (!root) return;

    const trigger = root.querySelector('.cc-notify-trigger');
    const readAll = byId('ccNotifyReadAll');

    if (trigger) {
      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = root.classList.toggle('open');
        trigger.setAttribute('aria-expanded', String(open));
      });
    }
    if (readAll) {
      readAll.addEventListener('click', (e) => {
        e.stopPropagation();
        markRead({ all: true });
      });
    }

    document.addEventListener('click', (e) => {
      if (!root.contains(e.target)) {
        root.classList.remove('open');
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        root.classList.remove('open');
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
      }
    });

    const list = byId('ccNotifyList');
    if (list) {
      list.addEventListener('click', (e) => {
        const item = e.target.closest('[data-notif-id]');
        if (!item) return;
        const row = ccNotifItems.find((n) => n.id === item.dataset.notifId);
        if (row && !row.read) markRead({ ids: [row.id] });
      });
    }

    load(false);
    setInterval(() => {
      if (!document.hidden) load(true);
    }, POLL_MS);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) load(true);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
