/* CloudCare — shared front-end behaviour */

/* ---------- theme (light / dark) — gate 26 ----------
   • No FOUC: each page applies the saved theme in an inline <head> script
     before the stylesheet paints.
   • Transition: a .theme-transition class on <html> enables a 250ms
     ease transition on background/color ONLY while switching, then is
     removed — so scrolling, hover and panel switches never animate.
   • prefers-reduced-motion: the transition is disabled entirely. */
(function initTheme(){
  const KEY = 'cloudcare_theme';
  const root = document.documentElement;

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const TRANSITION_MS = 250;
  let transitionTimer = null;

  function apply(theme, animate){
    if(animate && !reduceMotion){
      root.classList.add('theme-transition');
      clearTimeout(transitionTimer);
      transitionTimer = setTimeout(() => root.classList.remove('theme-transition'), TRANSITION_MS + 60);
    } else {
      root.classList.remove('theme-transition');
    }
    root.setAttribute('data-theme', theme);
    localStorage.setItem(KEY, theme);
    document.querySelectorAll('.theme-toggle .label').forEach(l => {
      l.textContent = theme === 'dark' ? 'Light' : 'Dark';
    });
  }

  function makeToggle(){
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'theme-toggle';
    btn.innerHTML = '<span class="dot"></span><span class="label">Dark</span>';
    // Keep a module-level handle: auth.js re-mounts the marketing nav's
    // account menu and must re-attach the same toggle node afterwards.
    window.__ccThemeToggle = btn;
    btn.addEventListener('click', () => {
      apply(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark', true);
    });
    return btn;
  }

  // apply saved (or preferred) theme immediately — initial paint never animates
  const saved = localStorage.getItem(KEY);
  apply(saved || 'light', false);

  // place a toggle in the marketing nav
  const navCta = document.querySelector('.nav-cta');
  if(navCta) navCta.prepend(makeToggle());

  // place a toggle in the dashboard topbar
  const topbarActions = document.querySelector('.dash-topbar > div:last-child');
  if(topbarActions) topbarActions.prepend(makeToggle());

  // sync label if theme was just applied before toggles existed
  document.querySelectorAll('.theme-toggle .label').forEach(l => {
    l.textContent = root.getAttribute('data-theme') === 'dark' ? 'Light' : 'Dark';
  });
})();

/* ---------- hero pulse-line: a traveling heartbeat sweep ---------- */
(function initPulseLine(){
  const svg = document.querySelector('svg.pulse-line');
  if(!svg) return;
  const sweep = svg.querySelector('.pulse-sweep');
  const dot = svg.querySelector('.pulse-dot');
  if(!sweep || typeof sweep.getTotalLength !== 'function') return;

  const total = sweep.getTotalLength();
  const segment = total * 0.18; // length of the bright traveling blip
  sweep.style.strokeDasharray = `${segment} ${total - segment}`;

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(reduceMotion) return; // CSS hides .pulse-sweep/.pulse-dot in this case

  const DURATION = 2800; // ms per lap
  let start = null;

  function frame(ts){
    if(start === null) start = ts;
    const elapsed = (ts - start) % DURATION;
    const progress = elapsed / DURATION;

    sweep.style.strokeDashoffset = String(total - progress * total);

    if(dot){
      const point = sweep.getPointAtLength(progress * total);
      dot.setAttribute('cx', point.x);
      dot.setAttribute('cy', point.y);
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();

/* ---------- modal engine (shared by any interface that needs a dialog) ---------- */
let ccModalOverlay = null;

function ccModalOpen(innerHTML){
  if(!ccModalOverlay){
    ccModalOverlay = document.createElement('div');
    ccModalOverlay.className = 'cc-modal-overlay';
    document.body.appendChild(ccModalOverlay);
    ccModalOverlay.addEventListener('click', (e) => {
      if(e.target === ccModalOverlay) ccModalClose();
    });
    document.addEventListener('keydown', (e) => {
      if(e.key === 'Escape' && ccModalOverlay.classList.contains('show')) ccModalClose();
    });
  }
  ccModalOverlay.innerHTML = `<div class="cc-modal" role="dialog" aria-modal="true">${innerHTML}</div>`;
  ccModalOverlay.querySelectorAll('[data-modal-close]').forEach(el => {
    el.addEventListener('click', (e) => { e.preventDefault(); ccModalClose(); });
  });
  document.body.style.overflow = 'hidden';
  requestAnimationFrame(() => ccModalOverlay.classList.add('show'));
  return ccModalOverlay;
}

function ccModalClose(){
  if(!ccModalOverlay) return;
  ccModalOverlay.classList.remove('show');
  document.body.style.overflow = '';
}

/* ---------- change password (used from every dashboard's Profile panel) ---------- */
function ccOpenChangePassword(){
  ccModalOpen(`
    <div class="cc-modal-head">
      <div><h3>Change password</h3><p>Enter your current password and choose a new one.</p></div>
      <button class="cc-modal-close" data-modal-close aria-label="Close">✕</button>
    </div>
    <div class="cc-modal-body">
      <form id="pwForm">
        <div class="field"><label for="pwCurrent">Current password</label><input id="pwCurrent" type="password" placeholder="••••••••" required></div>
        <div class="field"><label for="pwNew">New password</label><input id="pwNew" type="password" placeholder="At least 8 characters" minlength="8" required></div>
        <div class="field"><label for="pwConfirm">Confirm new password</label><input id="pwConfirm" type="password" placeholder="Re-enter new password" required></div>
      </form>
      <div class="cc-modal-note">🔒 Passwords are hashed with Argon2id on the server.</div>
    </div>
    <div class="cc-modal-foot">
      <button class="btn btn-ghost" data-modal-close>Cancel</button>
      <button class="btn btn-primary" id="pwConfirmBtn">Update password</button>
    </div>
  `);

  const newInput = document.getElementById('pwNew');
  const confirmInput = document.getElementById('pwConfirm');
  function syncMatch(){
    confirmInput.setCustomValidity(confirmInput.value && confirmInput.value !== newInput.value ? "Passwords don't match" : '');
  }
  newInput.addEventListener('input', syncMatch);
  confirmInput.addEventListener('input', syncMatch);

  document.getElementById('pwConfirmBtn').addEventListener('click', async function(){
    const form = document.getElementById('pwForm');
    syncMatch();
    if(!form.reportValidity()) return;
    const btn = this;
    btn.disabled = true;
    btn.textContent = 'Updating…';
    try {
      await ccApi('/api/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: document.getElementById('pwCurrent').value,
          new_password: newInput.value,
        }),
      });
      ccModalClose();
      ccToast('Password updated');
    } catch(err){
      btn.disabled = false;
      btn.textContent = 'Update password';
      ccToast(err.message || 'Could not update password.');
    }
  });
}

/* ---------- forgot password (login page) ---------- */
function ccOpenForgotPassword(){
  ccModalOpen(`
    <div class="cc-modal-head">
      <div><h3>Reset your password</h3><p>Enter your account email and we'll send you a reset link.</p></div>
      <button class="cc-modal-close" data-modal-close aria-label="Close">✕</button>
    </div>
    <div class="cc-modal-body">
      <form id="forgotForm">
        <div class="field"><label for="forgotEmail">Email</label><input id="forgotEmail" type="email" placeholder="you@cloudcare.demo" required></div>
      </form>
      <div class="cc-modal-note">📩 Email reset is not available yet — please contact an administrator to reset your password.</div>
    </div>
    <div class="cc-modal-foot">
      <button class="btn btn-ghost" data-modal-close>Cancel</button>
      <button class="btn btn-primary" id="forgotConfirmBtn">Contact support</button>
    </div>
  `);

  document.getElementById('forgotConfirmBtn').addEventListener('click', function(){
    ccModalClose();
    ccToast('Password reset by email is not available yet. Please contact an administrator.');
  });
}

/* ---------- toast ---------- */
function ccToast(message){
  let el = document.querySelector('.cc-toast');
  if(!el){
    el = document.createElement('div');
    el.className = 'cc-toast';
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 2600);
}

/* ---------- show-password toggle (login form) ---------- */
const CC_EYE_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
const CC_EYE_OFF_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.5 10.5 0 0 1 12 19c-7 0-11-7-11-7a19.8 19.8 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.9 9.9 0 0 1 12 5c7 0 11 7 11 7a19.9 19.9 0 0 1-3.22 4.31"/><path d="M14.12 14.12A3 3 0 1 1 9.88 9.88"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

/* Wraps the input in .pw-wrap and adds an eye button that flips
   input.type between password/text. Safe to call twice per input. */
function ccAttachPasswordToggle(input){
  if(!input || input.dataset.pwToggleBound) return;
  input.dataset.pwToggleBound = '1';

  const wrap = document.createElement('div');
  wrap.className = 'pw-wrap';
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'pw-toggle';
  btn.innerHTML = CC_EYE_SVG;
  btn.setAttribute('aria-label', 'Show password');
  btn.setAttribute('aria-pressed', 'false');
  btn.addEventListener('click', () => {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.innerHTML = show ? CC_EYE_OFF_SVG : CC_EYE_SVG;
    btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    btn.setAttribute('aria-pressed', String(show));
    input.focus({ preventScroll: true });
  });
  wrap.appendChild(btn);
}

/* ---------- reveal on scroll ---------- */
(function initReveal(){
  const items = document.querySelectorAll('.reveal');
  if(!items.length) return;
  const io = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if(e.isIntersecting){
        e.target.classList.add('in');
        io.unobserve(e.target);
      }
    });
  }, { threshold:.12, rootMargin:'0px 0px -40px 0px' });
  items.forEach(el => io.observe(el));
})();

/* ---------- marketing nav (mobile) — gate C13 ----------
   Class-driven (.nav-open on .site-nav): no inline style is ever written,
   so resizing back to desktop restores the normal nav untouched. */
(function initNavToggle(){
  const toggle = document.querySelector('.nav-toggle');
  const nav = document.querySelector('.site-nav');
  if(!toggle || !nav) return;

  const setOpen = (open) => {
    nav.classList.toggle('nav-open', open);
    toggle.setAttribute('aria-expanded', String(open));
  };
  setOpen(false);

  toggle.addEventListener('click', () => setOpen(!nav.classList.contains('nav-open')));
  /* close when a link is chosen or Escape is pressed */
  nav.querySelectorAll('.nav-links a, .nav-cta a').forEach(a => {
    a.addEventListener('click', () => setOpen(false));
  });
  document.addEventListener('keydown', (e) => {
    if(e.key === 'Escape' && nav.classList.contains('nav-open')){
      setOpen(false);
      toggle.focus();
    }
  });
})();

/* gate S19: scroll helper that honours prefers-reduced-motion. */
function ccScrollBehavior(){
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
}
window.ccScrollBehavior = ccScrollBehavior;

/* ---------- dashboard: sidebar panel switching ---------- */
(function initDashboardNav(){
  const sideNav = document.querySelector('.dash-sidebar .side-nav');
  if(!sideNav) return;

  const links = sideNav.querySelectorAll('a[data-panel]');
  const panels = document.querySelectorAll('.panel-view');
  const title = document.getElementById('topbarTitle');

  function activate(name){
    links.forEach(l => {
      const isActive = l.dataset.panel === name;
      l.classList.toggle('active', isActive);
      /* gate C11: real links + aria-current for assistive tech */
      if(isActive) l.setAttribute('aria-current', 'page');
      else l.removeAttribute('aria-current');
    });
    panels.forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
    if(title){
      const active = [...links].find(l => l.dataset.panel === name);
      /* gate S2: the label lives in <span class="lbl"> — never the emoji icon */
      const label = active && (active.querySelector('.lbl') || active);
      if(label) title.textContent = label.textContent.trim();
    }
    document.querySelector('.dash-sidebar')?.classList.remove('open');
    window.scrollTo({ top:0, behavior: ccScrollBehavior() });
  }

  /* links carry href="#panel-…" now — JS still owns the switch */
  links.forEach(l => l.addEventListener('click', (e) => {
    e.preventDefault();
    activate(l.dataset.panel);
  }));

  /* honour the initial hash (and mark the starting link) */
  const initial = [...links].find(l => l.classList.contains('active'));
  if(initial) initial.setAttribute('aria-current', 'page');
  const hashPanel = (window.location.hash || '').replace('#panel-', '');
  if(hashPanel && [...links].some(l => l.dataset.panel === hashPanel)) activate(hashPanel);

  document.querySelectorAll('[data-panel-link]').forEach(l => {
    l.addEventListener('click', (e) => {
      e.preventDefault();
      activate(l.dataset.panelLink);
    });
  });

  // simple mobile sidebar toggle, injected next to the topbar title
  const topbar = document.querySelector('.dash-topbar');
  if(topbar && window.matchMedia('(max-width: 980px)')){
    const btn = document.createElement('button');
    btn.textContent = '☰';
    btn.setAttribute('aria-label','Toggle menu');
    btn.style.cssText = 'display:none;border:none;background:none;font-size:1.3rem;cursor:pointer;margin-right:14px;';
    topbar.firstElementChild?.prepend(btn);
    btn.addEventListener('click', () => document.querySelector('.dash-sidebar')?.classList.toggle('open'));
    const mq = window.matchMedia('(max-width: 980px)');
    const sync = () => btn.style.display = mq.matches ? 'inline-block' : 'none';
    sync(); mq.addEventListener('change', sync);
  }
})();

/* ---------- ai agent console tabs (admin dashboard) ---------- */
(function initAgentTabs(){
  const tabs = document.querySelectorAll('.chip-tab[data-agent]');
  if(!tabs.length) return;
  const views = document.querySelectorAll('[data-agent-view]');
  tabs.forEach(t => t.addEventListener('click', () => {
    tabs.forEach(x => x.classList.toggle('active', x === t));
    views.forEach(v => v.style.display = (v.dataset.agentView === t.dataset.agent) ? '' : 'none');
  }));
})();
