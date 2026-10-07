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
  if(!/^dashboard-[a-z]+\.html/.test(next) && !/^(index|about|reviews|login|register|404)\.html/.test(next)) return '';
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

async function ccApi(path, options = {}){
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
    ccClearSession();
    const next = encodeURIComponent(ccCurrentReturnTarget());
    window.location.href = `login.html?expired=1${next ? `&next=${next}` : ''}`;
    throw new Error('Your session has expired. Please sign in again.');
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
    throw new Error(ccErrorMessage(body, `Request failed (${response.status}).`));
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

/* ---------- list/table render states ---------- */
function ccRenderState(tbody, state, message, colspan){
  const span = colspan || 1;
  if(state === 'loading'){
    tbody.innerHTML = `<tr><td colspan="${span}" style="text-align:center;padding:28px;color:var(--slate-400);">Loading…</td></tr>`;
  } else if(state === 'empty'){
    tbody.innerHTML = `<tr><td colspan="${span}" style="text-align:center;padding:28px;color:var(--slate-400);">${ccEscape(message || 'Nothing here yet.')}</td></tr>`;
  } else if(state === 'error'){
    tbody.innerHTML = `<tr><td colspan="${span}" style="text-align:center;padding:28px;color:#c9432c;">${ccEscape(message || 'Something went wrong.')}</td></tr>`;
  }
}
