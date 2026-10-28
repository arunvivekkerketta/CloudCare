/* CloudCare — admin dashboard: API-driven tables and KPIs.
   All names/emails come from the API and are HTML-escaped with ccEscape.
   There are no invented charts: panels without a backend say so. */
const ccTeal = '#2fb8a4', ccCoral = '#ff6f59', ccGold = '#e0a53c';

/* ---------- overview KPIs (gate C5) ----------
   Real numbers only: role head-counts, the next-7-days appointment list and
   the invoice totals. Anything without an endpoint stays "—" in the HTML. */
async function ccLoadAdminKpis(){
  const set = (id, value) => { const el = document.getElementById(id); if(el) el.textContent = value; };
  try {
    const [summary, week, bills] = await Promise.all([
      ccApi('/api/users/summary'),
      ccApi('/api/admin/appointments?within_days=7'),
      ccApi('/api/bills?page_size=100'),
    ]);
    set('kpiPatients', String((summary && summary.by_role && summary.by_role.patient) || 0));
    set('kpiDoctors', String((summary && summary.by_role && summary.by_role.doctor) || 0));
    set('kpiAppts', String((week || []).length));
    const month = new Date().getMonth();
    const paidMtd = (bills || [])
      .filter(b => b.status === 'Paid' && new Date(b.created_at).getMonth() === month)
      .reduce((sum, b) => sum + Number(b.amount || 0), 0);
    const outstanding = (bills || [])
      .filter(b => b.status !== 'Paid')
      .reduce((sum, b) => sum + Number(b.amount || 0), 0);
    set('kpiRevenue', '₹' + paidMtd.toLocaleString('en-IN'));
    set('kpiCollected', '₹' + paidMtd.toLocaleString('en-IN'));
    set('kpiPending', '₹' + outstanding.toLocaleString('en-IN'));
  } catch(err){ /* KPIs keep their honest "—" */ }

  /* topbar status: a real liveness probe instead of "All systems normal" */
  const badge = document.getElementById('adminHealthBadge');
  if(badge){
    try {
      const health = await ccApi('/health');
      const ok = health && health.status === 'ok';
      badge.textContent = ok ? '● API healthy' : '● API degraded';
      badge.className = 'badge ' + (ok ? 'badge-mint' : 'badge-coral');
    } catch(err){
      badge.textContent = '● API unreachable';
      badge.className = 'badge badge-coral';
    }
    badge.hidden = false;
    badge.style.display = '';
  }
}
window.ccLoadAdminKpis = ccLoadAdminKpis;

/* ---------- patients panel (API) ---------- */
async function ccLoadAdminPatients(){
  const tbody = document.getElementById('patientTable');
  try {
    ccRenderState(tbody, 'loading', '', 6);
    const patients = await ccApi('/api/patients?page_size=100');
    if(!patients.length){ ccRenderState(tbody, 'empty', 'No patients registered yet.', 6); return; }
    tbody.innerHTML = patients.map(p => {
      const risk = p.risk_score === null || p.risk_score === undefined ? 0 : p.risk_score;
      return `<tr><td><div class="cell-flex"><span class="avatar-sm">${ccEscape(p.name.split(' ').map(w=>w[0]).slice(0,2).join(''))}</span>${ccEscape(p.name)}</div></td>
      <td>${p.age ?? '—'}</td><td>${ccEscape(p.condition || '—')}</td><td>${ccEscape(p.assigned_doctor_name || 'Unassigned')}</td>
      <td><div class="flex items-center gap-12" style="width:110px;"><div class="progress-track w-full"><div class="progress-fill" style="width:${risk}%;background:${risk>70?ccCoral:risk>40?ccGold:ccTeal};"></div></div><span class="mono" style="font-size:.75rem;">${risk}%</span></div></td>
      <td><span class="badge ${p.status==='Admitted'?'badge-coral':'badge-mint'}">${ccEscape(p.status)}</span></td></tr>`;
    }).join('');
  } catch(err){ ccRenderState(tbody, 'error', err.message, 6); }
}

/* ---------- doctors panel (API) ---------- */
async function ccLoadAdminDoctors(){
  const grid = document.getElementById('doctorGrid');
  try {
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:28px;color:var(--slate-400);">Loading…</div>';
    const doctors = await ccApi('/api/admin/users?role=doctor&page_size=100');
    if(!doctors.length){
      grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:28px;color:var(--slate-400);">No doctors registered.</div>';
      return;
    }
    grid.innerHTML = doctors.map(d => {
      const initials = (d.name.replace('Dr. ','').split(' ').map(w=>w[0]).slice(0,2).join('') || '?').toUpperCase();
      return `<div class="card">
        <div class="flex items-center gap-12">
          <div class="avatar">${ccEscape(initials)}</div>
          <div><strong style="display:block;">${ccEscape(d.name)}</strong><span style="font-size:.8rem;color:var(--slate-400);">${ccEscape(d.department || 'General')}</span></div>
        </div>
        <div class="flex items-center justify-between mt-16">
          <span style="font-size:.82rem;color:var(--slate-500);">${ccEscape(d.email)}</span>
        </div>
      </div>`;
    }).join('');
  } catch(err){
    grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:28px;color:#c9432c;">${ccEscape(err.message)}</div>`;
  }
}

/* ---------- appointments panel (API) ---------- */
const ccApptBadge = {
  Requested: 'badge-gold',
  Confirmed: 'badge-mint',
  Completed: 'badge-mint',
  Declined: 'badge-slate',
  Cancelled: 'badge-slate',
  Expired: 'badge-slate',
};

ccApptBadge.Completed = 'badge-mint';
ccApptBadge.Expired = 'badge-slate';

async function ccLoadAdminAppointments(){
  const tbody = document.getElementById('apptTable');
  try {
    ccRenderState(tbody, 'loading', '', 6);
    const filter = document.getElementById('apptStatusFilter');
    const status = filter && filter.value ? `&status=${encodeURIComponent(filter.value)}` : '';
    const appts = await ccApi(`/api/admin/appointments?within_days=14${status}`);
    if(!appts.length){
      ccRenderState(tbody, 'empty', filter && filter.value ? `No ${filter.value.toLowerCase()} appointments in the next 14 days.` : 'No appointments in the next 14 days.', 6);
      return;
    }
    tbody.innerHTML = appts.map(a => {
      const when = ccClinicDateTime(a.scheduled_at);
      const badge = ccApptBadge[a.status] || 'badge-slate';
      return `<tr><td class="mono" style="white-space:nowrap;">${ccEscape(when)}</td><td>${ccEscape(a.patient_name)}</td><td>${ccEscape(a.doctor_name)}</td><td>${ccEscape(a.department)}</td>
      <td>${ccEscape(a.mode)}</td><td><span class="badge ${badge}">${ccEscape(a.status)}</span></td></tr>`;
    }).join('');
  } catch(err){ ccRenderState(tbody, 'error', err.message, 6); }
}
window.ccLoadAdminAppointments = ccLoadAdminAppointments;

/* ---------- billing panel (API) ---------- */
async function ccLoadAdminBills(){
  const tbody = document.getElementById('billTable');
  try {
    ccRenderState(tbody, 'loading', '', 4);
    const bills = await ccApi('/api/bills?page_size=100');
    if(!bills.length){ ccRenderState(tbody, 'empty', 'No invoices recorded.', 4); return; }
    tbody.innerHTML = bills.map(b => {
      return `<tr><td class="mono">${ccEscape(b.invoice_no)}</td><td>${ccEscape(b.patient_name)}</td><td class="mono">₹${Number(b.amount).toLocaleString('en-IN')}</td>
      <td><span class="badge ${b.status==='Paid'?'badge-mint':b.status==='Overdue'?'badge-coral':'badge-gold'}">${ccEscape(b.status)}</span></td></tr>`;
    }).join('');
  } catch(err){ ccRenderState(tbody, 'error', err.message, 4); }
}

window.ccLoadAdminDashboard = function(){
  if(window.ccLoadAdminKpis) window.ccLoadAdminKpis();
  ccLoadAdminPatients();
  ccLoadAdminDoctors();
  ccLoadAdminAppointments();
  ccLoadAdminBills();
};

/* ---------- module-not-connected empty state (shared by AI console lists) ---------- */
function ccComingSoon(title, note){
  return `<div class="risk-row" style="padding:14px 0;"><div><strong style="font-size:.9rem;">${title}</strong><span style="display:block;font-size:.78rem;color:var(--slate-400);">${note}</span></div></div>`;
}

/* ---------- AI console: inventory ---------- */
document.getElementById('reorderList').innerHTML = ccComingSoon(
  'Inventory AI coming soon',
  'Reorder suggestions will appear here once the inventory module is connected.'
);

/* ---------- AI console: risk ---------- */
document.getElementById('riskList').innerHTML = ccComingSoon(
  'AI risk scoring coming soon',
  'Patient risk rankings will appear here once the analytics module is connected.'
);

/* ---------- AI console: resource forecast ---------- */
document.getElementById('resourceList').innerHTML = ccComingSoon(
  'Capacity forecasting coming soon',
  'Resource recommendations will appear here once the forecasting module is connected.'
);

