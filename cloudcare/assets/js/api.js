/* CloudCare — shared API helpers: fetch wrapper, escaping, session utils.
   Load order: config.js -> api.js -> main.js -> auth.js -> page scripts. */

/* ---------- HTML escaping (ALL API data rendered via innerHTML) ---------- */
function ccEscape(value){
  if(value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ---------- clinic time (Asia/Kolkata) ---------- */
/* Every appointment time is rendered in clinic time with an explicit IST
   label, so a patient travelling or with a different device time zone still
   sees the same wall-clock time they booked. */
const CC_CLINIC_TZ = 'Asia/Kolkata';

function ccClinicFormat(value, options){
  const date = value instanceof Date ? value : new Date(value);
  return date.toLocaleString('en-IN', Object.assign({ timeZone: CC_CLINIC_TZ }, options || {}));
}

function ccClinicDateTime(value){
  return ccClinicFormat(value, {
    weekday:'short', day:'numeric', month:'short', hour:'numeric', minute:'2-digit',
  }) + ' IST';
}

function ccClinicTime(value, options){
  return ccClinicFormat(value, Object.assign({ hour:'numeric', minute:'2-digit' }, options || {})) + ' IST';
}

function ccClinicDate(value){
  return ccClinicFormat(value, { day:'numeric', month:'short' });
}

function ccClinicStamp(value){
  return ccClinicFormat(value, {
    day:'numeric', month:'short', year:'numeric', hour:'numeric', minute:'2-digit',
  }) + ' IST';
}

/* en-CA gives YYYY-MM-DD, convenient for comparing clinic days. */
function ccClinicDateKey(value){
  const date = value instanceof Date ? value : new Date(value);
  return new Intl.DateTimeFormat('en-CA', { timeZone: CC_CLINIC_TZ }).format(date);
}

/* ---------- session storage ---------- */
function ccGetSession(){
  try { return JSON.parse(localStorage.getItem('cloudcare_session')); }
  catch(e){ return null; }
}

function ccSetToken(token, expiresIn){
  localStorage.setItem('cloudcare_token', token);
  localStorage.setItem('cloudcare_token_exp', String(Date.now() + expiresIn * 1000));
}

/* Client-side expiry check pre-fetch: an expired JWT redirects instantly
   without needing a 401 round-trip (30-minute sessions per spec). */
function ccSessionExpired(){
  const exp = Number(localStorage.getItem('cloudcare_token_exp') || 0);
  if(!exp) return false;
  return Date.now() >= exp;
}

/* Return-to target for post-login redirect: current page + hash, so
   "Book Appointment" (with #panel-book selected) resumes after re-auth. */
function ccCurrentReturnTarget(){
  const page = window.location.pathname.split('/').pop() || '';
  return page ? page + window.location.search + window.location.hash : '';
}

function ccSafeNext(next){
  if(!next) return '';
  if(!/^dashboard-[a-z]+\.html/.test(next) && !/^(index|about|reviews|login|register|reset-password|404)\.html/.test(next)) return '';
  return next;
}

function ccClearSession(){
  localStorage.removeItem('cloudcare_token');
  localStorage.removeItem('cloudcare_token_exp');
  localStorage.removeItem('cloudcare_session');
  localStorage.removeItem('cloudcare_profile');
}

/* ---------- API fetch wrapper ---------- */
/* Login/register failures also return 401 — they must NOT trigger the
   session-expired redirect, just surface the error message. */
const CC_NO_AUTH_REDIRECT = ['/api/auth/login', '/api/auth/register'];

/* Single-flight silent refresh: when the stored token has < 5 minutes left
   one refresh request is shared by every concurrent ccApi call. A failed
   refresh is ignored here — the normal 401 flow then takes over. */
const CC_NO_REFRESH = ['/api/auth/login', '/api/auth/register', '/api/auth/refresh'];
let ccRefreshing = null;

async function ccMaybeRefreshToken(path){
  if(CC_NO_REFRESH.some(p => path.startsWith(p))) return;
  const token = localStorage.getItem('cloudcare_token');
  if(!token) return;
  const exp = Number(localStorage.getItem('cloudcare_token_exp') || 0);
  if(!exp || exp - Date.now() > 5 * 60 * 1000) return;

  if(!ccRefreshing){
    ccRefreshing = fetch(`${CC_API_URL}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    }).then(async (res) => {
      if(!res.ok) return null;
      const body = await res.json().catch(() => null);
      if(body && body.access_token) ccSetToken(body.access_token, body.expires_in || 1800);
      return body;
    }).catch(() => null).finally(() => { ccRefreshing = null; });
  }
  await ccRefreshing;
}

async function ccApi(path, options = {}){
  await ccMaybeRefreshToken(path);
  const token = localStorage.getItem('cloudcare_token');
  const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
  if(token) headers['Authorization'] = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${CC_API_URL}${path}`, { ...options, headers });
  } catch(networkError){
    throw new Error('Cannot reach the CloudCare API. Is the server running?');
  }

  if(response.status === 401 && !CC_NO_AUTH_REDIRECT.some(p => path.startsWith(p))){
    /* gate C16: public pages pass {noRedirect:true} — clear the dead session
       but stay on the page (visiting index.html with a revoked token must not
       bounce to the login screen). */
    ccClearSession();
    if(!options.noRedirect){
      const next = encodeURIComponent(ccCurrentReturnTarget());
      window.location.href = `login.html?expired=1${next ? `&next=${next}` : ''}`;
    }
    throw new Error('Your session has expired. Please sign in again.');
  }

  if(response.status === 429){
    throw new Error('Too many attempts. Please wait a minute and try again.');
  }

  if(response.status === 403){
    let message = 'You do not have permission to do that.';
    try {
      const body = await response.json();
      if(typeof body?.detail === 'string') message = body.detail;
    } catch(e){}
    ccToast(message);
    throw new Error(message);
  }

  let body = null;
  const text = await response.text();
  if(text){
    try { body = JSON.parse(text); } catch(e){ body = null; }
  }

  if(!response.ok){
    const error = new Error(ccErrorMessage(body, `Request failed (${response.status}).`));
    /* gate S12: callers need the status (409 slot-taken vs 500 etc.). */
    error.status = response.status;
    throw error;
  }
  return body;
}

function ccErrorMessage(body, fallback){
  if(typeof body?.detail === 'string') return body.detail;
  if(body?.detail?.message) return body.detail.message;
  if(Array.isArray(body?.detail)){
    return body.detail.map(item => item.msg || 'Invalid input').join('. ');
  }
  return fallback;
}

/* ---------- inline form errors ---------- */
/* Auth forms show one persistent message under the form, announced by
   role="alert", and move focus to the field that needs attention. The old
   brief toast alone disappeared before many people could read it. */
function ccClearFormError(form){
  const box = form && form.querySelector('[data-form-error]');
  if(!box) return;
  box.textContent = '';
  box.hidden = true;
  form.querySelectorAll('[aria-invalid="true"]').forEach(el => el.removeAttribute('aria-invalid'));
}

function ccShowFormError(form, message, field){
  const box = form && form.querySelector('[data-form-error]');
  if(!box) return;
  box.textContent = message;
  box.hidden = false;
  form.querySelectorAll('[aria-invalid="true"]').forEach(el => el.removeAttribute('aria-invalid'));
  if(field){
    field.setAttribute('aria-invalid', 'true');
    field.focus();
  }
  if(!form.dataset.formErrorBound){
    form.dataset.formErrorBound = '1';
    form.addEventListener('input', () => ccClearFormError(form));
  }
}

/* ---------- list/table render states ---------- */
/* gate C8: state rows are plain centred cells (class-driven, not inline
   styles) so the colspan spans every column and centres cleanly — the old
   td:first-child flex layout made them shrink to one column. */
function ccRenderState(tbody, state, message, colspan){
  const span = colspan || 1;
  if(state === 'loading'){
    tbody.innerHTML = `<tr><td colspan="${span}" class="cc-state-cell">Loading…</td></tr>`;
  } else if(state === 'empty'){
    tbody.innerHTML = `<tr><td colspan="${span}" class="cc-state-cell">${ccEscape(message || 'Nothing here yet.')}</td></tr>`;
  } else if(state === 'error'){
    tbody.innerHTML = `<tr><td colspan="${span}" class="cc-state-cell cc-state-error"><span>${ccEscape(message || 'Something went wrong.')}</span><button type="button" class="btn btn-ghost btn-sm mt-8" data-cc-retry>Try again</button></td></tr>`;
  }
}

/* Pages register their table's loader once. The shared error row asks the
   nearest tbody which loader to run; an unregistered table falls back to a
   page reload rather than leaving a dead button. */
window.ccRetryTables = window.ccRetryTables || {};
function ccRegisterRetry(tableId, loader){
  if(tableId && typeof loader === 'function') window.ccRetryTables[tableId] = loader;
}
document.addEventListener('click', (event) => {
  const button = event.target && event.target.closest && event.target.closest('[data-cc-retry]');
  if(!button) return;
  const tbody = button.closest('tbody');
  const loader = tbody && window.ccRetryTables[tbody.id];
  if(typeof loader === 'function') loader();
  else window.location.reload();
});
