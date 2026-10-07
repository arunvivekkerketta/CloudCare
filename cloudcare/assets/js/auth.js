/* CloudCare authentication — real JWT session against the API.
   Load order: config.js -> api.js -> main.js -> auth.js */
const CC_ROLES = [
  { id:'patient', label:'Patient', ic:'🧑‍⚕️' },
  { id:'doctor', label:'Doctor', ic:'🩺' },
  { id:'nurse', label:'Nurse', ic:'💉' },
  { id:'pharmacist', label:'Pharmacist', ic:'💊' },
  { id:'lab', label:'Lab Tech', ic:'🧪' },
  { id:'admin', label:'Admin', ic:'🛡️' },
];

function ccSetSession(user){
  localStorage.setItem('cloudcare_session', JSON.stringify({
    role: user.role, name: user.name, email: user.email, id: user.id
  }));
}

function ccHandleLogin(formSel){
  const form = document.querySelector(formSel);
  if(!form) return;
  const passwordInput = document.getElementById('loginPassword');
  if(passwordInput) ccAttachPasswordToggle(passwordInput);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('loginEmail').value.trim();
    const password = document.getElementById('loginPassword').value;
    await ccSubmitLogin(form, { email, password });
  });
}

function ccHandleRegister(formSel){
  const form = document.querySelector(formSel);
  if(!form) return;
  const passwordInput = document.getElementById('regPassword');
  const confirmInput = document.getElementById('regConfirmPassword');
  if(passwordInput) ccAttachPasswordToggle(passwordInput);
  if(confirmInput) ccAttachPasswordToggle(confirmInput);

  /* Confirm-password mismatch validation (same pattern as the change-
     password modal): invalid state clears as soon as the values match. */
  function syncConfirmMatch(){
    if(!confirmInput || !passwordInput) return;
    confirmInput.setCustomValidity(
      confirmInput.value && confirmInput.value !== passwordInput.value
        ? "Passwords don't match"
        : ''
    );
  }
  if(passwordInput) passwordInput.addEventListener('input', syncConfirmMatch);
  if(confirmInput) confirmInput.addEventListener('input', syncConfirmMatch);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    syncConfirmMatch();
    if(!form.reportValidity()){
      ccToast("Passwords don't match.");
      return;
    }
    const name = document.getElementById('regName').value.trim();
    const email = document.getElementById('regEmail').value.trim();
    const password = document.getElementById('regPassword').value;
    await ccSubmitRegister(form, { name, email, password });
  });
}

async function ccSubmitLogin(form, payload){
  const button = form.querySelector('button[type="submit"]');
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = 'Connecting…';
  try {
    const body = await ccApi('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    ccSetToken(body.access_token, body.expires_in || 1800);
    ccSetSession(body.user);

    /* A safe ?next= target wins (validated allow-list); otherwise the
       server's role is authoritative and they land on their own dashboard. */
    const next = ccSafeNext(new URLSearchParams(window.location.search).get('next') || '');
    window.location.href = next || `dashboard-${body.user.role}.html`;
  } catch(error) {
    button.disabled = false;
    button.textContent = originalText;
    ccToast(error.message || 'Unable to connect to CloudCare API.');
  }
}

async function ccSubmitRegister(form, payload){
  const button = form.querySelector('button[type="submit"]');
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = 'Creating account…';
  try {
    await ccApi('/api/auth/register', { method: 'POST', body: JSON.stringify(payload) });
    localStorage.setItem('cloudcare_login_email', payload.email);
    window.location.href = 'login.html?registered=1';
  } catch(error) {
    button.disabled = false;
    button.textContent = originalText;
    ccToast(error.message || 'Unable to connect to CloudCare API.');
  }
}

/* login.html bootstrap: expired notice, saved email (gate 10b).
   ?next= is consumed by ccSubmitLogin after a successful sign-in. */
(function ccLoginBootstrap(){
  const params = new URLSearchParams(window.location.search);
  if(params.get('registered') === '1'){
    setTimeout(() => ccToast('Account created successfully. Please sign in.'), 0);
    const savedEmail = localStorage.getItem('cloudcare_login_email');
    const emailInput = document.getElementById('loginEmail');
    if(savedEmail && emailInput) emailInput.value = savedEmail;
    localStorage.removeItem('cloudcare_login_email');
  }
  if(params.get('expired') === '1'){
    setTimeout(() => ccToast('Your session has expired. Please sign in again.'), 0);
  }
})();

/* Dashboard guard: validates the JWT via /api/auth/me. No demo fallback —
   a missing, invalid or role-mismatched session goes straight to login. */
async function ccGuardDashboard(role){
  const token = localStorage.getItem('cloudcare_token');
  if(!token){
    window.location.href = 'login.html';
    return null;
  }
  /* Pre-fetch client-side expiry check: expired JWT redirects immediately. */
  if(ccSessionExpired()){
    ccClearSession();
    window.location.href = 'login.html?expired=1&next=' +
      encodeURIComponent(ccCurrentReturnTarget());
    return null;
  }

  let user = null;
  try {
    user = await ccApi('/api/auth/me');
  } catch(error){
    // 401 was already handled (session cleared + redirect). Other failures:
    localStorage.removeItem('cloudcare_token');
    window.location.href = 'login.html';
    return null;
  }

  if(user.role !== role){
    /* The server role is authoritative — send them to their own dashboard. */
    window.location.href = `dashboard-${user.role}.html`;
    return null;
  }

  ccSetSession(user);
  ccRefreshUserIdentity(user);
  ccInitProfileForm(user);

  /* Server-side logout (gate 11b): POST /api/auth/logout bumps the server's
     token_version, so the stored token is dead everywhere, then the client
     clears storage and returns to the sign-in page. */
  document.querySelectorAll('[data-logout]').forEach(el => {
    if(el.dataset.logoutBound) return;
    el.dataset.logoutBound = '1';
    el.addEventListener('click', async (e) => {
      e.preventDefault();
      try { await ccApi('/api/auth/logout', { method: 'POST' }); } catch(err){}
      ccClearSession();
      window.location.href = 'login.html';
    });
  });

  return user;
}

/* ---------- shared identity fill (sidebar + profile panel) ----------
   One source of truth for showing the signed-in user's real details —
   used by the dashboard guard and again after a profile save. */
function ccRefreshUserIdentity(user){
  document.querySelectorAll('[data-user-name]').forEach(el => el.textContent = user.name);
  document.querySelectorAll('[data-user-avatar]').forEach(el => {
    el.textContent = user.name.trim().split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
  });
  document.querySelectorAll('[data-user-role]').forEach(el => {
    const roleInfo = CC_ROLES.find(r => r.id === user.role);
    if(roleInfo) el.textContent = roleInfo.label;
  });
  document.querySelectorAll('[data-profile-name]').forEach(el => el.textContent = user.name);
  document.querySelectorAll('[data-profile-name-input]').forEach(el => el.value = user.name);
  document.querySelectorAll('[data-profile-email-input]').forEach(el => el.value = user.email);
  document.querySelectorAll('[data-profile-role]').forEach(el => {
    const roleInfo = CC_ROLES.find(r => r.id === user.role);
    el.textContent = roleInfo ? roleInfo.label : user.role;
  });
}

/* ---------- marketing-nav account menu (gates 12b, 1b, 12) ----------
   On public pages, replaces "Sign in / Get started" with the user's name +
   account menu. gate S1: the menu is painted INSTANTLY from ccGetSession(),
   then verified against /api/auth/me in the background. gate C16: that check
   runs with {noRedirect:true} — a revoked token clears the session and keeps
   the signed-out nav instead of bouncing the visitor to login.html. */
function ccInitPublicNav(){
  const navCta = document.querySelector('.nav-cta');
  if(!navCta) return;

  /* remember the page's own signed-out CTA so we can always put it back */
  if(!navCta.dataset.ctaOriginal) navCta.dataset.ctaOriginal = navCta.innerHTML;

  const restoreSignedOutCta = () => {
    navCta.innerHTML = navCta.dataset.ctaOriginal;
    if(window.__ccThemeToggle) navCta.prepend(window.__ccThemeToggle);
    ccPaintHeroCtas(null);
  };

  const token = localStorage.getItem('cloudcare_token');
  const session = ccGetSession();
  if(!token || !session){
    restoreSignedOutCta();
    return;
  }
  if(ccSessionExpired()){
    ccClearSession();
    restoreSignedOutCta(); // stale: signed-out CTA, no dead menu
    return;
  }

  /* 1) paint immediately from the cached session (no await, no flash) */
  ccPaintAccountMenu(session);
  ccPaintHeroCtas(session.role);

  /* 2) verify in the background — no redirect on 401 */
  ccApi('/api/auth/me', { noRedirect: true }).then(user => {
    ccSetSession(user);
    ccPaintAccountMenu(user);
    ccPaintHeroCtas(user.role);
  }).catch(err => {
    /* 401: session already cleared by ccApi -> fall back to signed-out nav.
       Network errors: keep the menu painted from the cached session. */
    if(!localStorage.getItem('cloudcare_token')) restoreSignedOutCta();
  });
}

/* gate S1: index.html hero CTAs swap to dashboard actions when signed in. */
let ccHeroCtaOriginal = null;
function ccPaintHeroCtas(role){
  const hero = document.getElementById('heroCtas');
  if(!hero) return;
  if(ccHeroCtaOriginal === null) ccHeroCtaOriginal = hero.innerHTML;
  if(!role){ hero.innerHTML = ccHeroCtaOriginal; return; }
  const dash = `dashboard-${role}.html`;
  /* patients book from their own panel; other roles land on their dashboard */
  const bookHref = role === 'patient' ? `${dash}#panel-book` : dash;
  hero.innerHTML = `
    <a href="${dash}" class="btn btn-accent">Go to dashboard</a>
    <a href="${bookHref}" class="btn btn-ghost">Book an appointment</a>`;
}

function ccPaintAccountMenu(user){
  const navCta = document.querySelector('.nav-cta');
  if(!navCta) return;

  const dash = `dashboard-${user.role}.html`;
  const initials = user.name.trim().split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
  navCta.innerHTML = `
    <div class="cc-account-menu">
      <button class="btn btn-ghost btn-sm cc-account-trigger" type="button" aria-haspopup="true" aria-expanded="false">
        <span class="avatar-sm">${ccEscape(initials)}</span>
        <span class="cc-account-name">${ccEscape(user.name)}</span>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1 3l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
      </button>
      <div class="cc-account-dropdown" role="menu">
        <div class="cc-account-head">
          <strong>${ccEscape(user.name)}</strong>
          <span>${ccEscape(user.email)}</span>
        </div>
        <a href="${dash}">My dashboard</a>
        <a href="${dash}#panel-appointments">My Appointments</a>
        <a href="#book" data-menu-book>Book appointment</a>
        <a href="#" data-menu-logout>Sign out</a>
      </div>
    </div>`;

  const menu = navCta.querySelector('.cc-account-menu');
  /* Re-attach the theme toggle that innerHTML replacement removed. */
  if(window.__ccThemeToggle) navCta.prepend(window.__ccThemeToggle);

  const trigger = navCta.querySelector('.cc-account-trigger');
  trigger.addEventListener('click', () => {
    const open = menu.classList.toggle('open');
    trigger.setAttribute('aria-expanded', String(open));
  });

  /* "Book appointment": patients jump to their booking panel; other roles
     just go to the dashboard (they cannot book). */
  navCta.querySelector('[data-menu-book]').addEventListener('click', (e) => {
    e.preventDefault();
    menu.classList.remove('open');
    trigger.setAttribute('aria-expanded', 'false');
    if(user.role === 'patient'){
      window.location.href = 'dashboard-patient.html#panel-book';
    } else {
      window.location.href = dash;
    }
  });

  const logoutLink = navCta.querySelector('[data-menu-logout]');
  logoutLink.addEventListener('click', async (e) => {
    e.preventDefault();
    try { await ccApi('/api/auth/logout', { method: 'POST' }); } catch(err){}
    ccClearSession();
    window.location.href = 'login.html';
  });

  /* Close the menu on any outside click / Escape. Bound once per page, but
     always resolved against the CURRENT menu node (the /me repaint swaps it). */
  if(navCta.dataset.menuBound) return;
  navCta.dataset.menuBound = '1';
  const currentMenu = () => navCta.querySelector('.cc-account-menu');
  const closeMenu = () => {
    const m = currentMenu();
    if(!m) return;
    m.classList.remove('open');
    const t = navCta.querySelector('.cc-account-trigger');
    if(t) t.setAttribute('aria-expanded', 'false');
  };
  document.addEventListener('click', (e) => {
    const m = currentMenu();
    if(m && !m.contains(e.target)) closeMenu();
  });
  document.addEventListener('keydown', (e) => {
    if(e.key === 'Escape') closeMenu();
  });
}

/* ---------- profile panel: Save / Cancel ----------
   Save PATCHes /api/auth/me — the real account update. Cancel restores
   the session values. Bound once per dashboard by ccGuardDashboard. */
function ccInitProfileForm(user){
  const saveBtn = document.querySelector('[data-profile-save]');
  const cancelBtn = document.querySelector('[data-profile-cancel]');
  if(!saveBtn || saveBtn.dataset.profileBound) return;
  saveBtn.dataset.profileBound = '1';

  const nameInput = document.querySelector('[data-profile-name-input]');
  const emailInput = document.querySelector('[data-profile-email-input]');

  /* gate S11: Cancel restores the LATEST SAVED values — the local copy is
     replaced on every successful save, not just at page load. */
  let currentUser = user;

  if(cancelBtn){
    cancelBtn.dataset.profileBound = '1';
    cancelBtn.addEventListener('click', () => {
      ccRefreshUserIdentity(currentUser);
      ccToast('Changes discarded');
    });
  }

  saveBtn.addEventListener('click', async function(){
    const name = (nameInput?.value || '').trim();
    const email = (emailInput?.value || '').trim();
    if(name.length < 2){
      ccToast('Name must be at least 2 characters.');
      nameInput?.focus();
      return;
    }
    if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)){
      ccToast('Please enter a valid email address.');
      emailInput?.focus();
      return;
    }
    const btn = this;
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = 'Saving…';
    try {
      const updated = await ccApi('/api/auth/me', {
        method: 'PATCH',
        body: JSON.stringify({ name, email }),
      });
      currentUser = updated;   /* gate S11: Cancel now restores THIS */
      ccSetSession(updated);
      ccRefreshUserIdentity(updated);
      ccToast('Profile updated');
    } catch(err){
      ccToast(err.message || 'Could not update your profile.');
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  });
}

/* On pages WITH a public nav but WITHOUT a dashboard guard, mount the
   account menu / signed-out CTA. Dashboards call ccGuardDashboard instead. */
if(typeof document !== 'undefined' && document.querySelector('.site-nav')){
  document.addEventListener('DOMContentLoaded', () => { ccInitPublicNav(); });
}
