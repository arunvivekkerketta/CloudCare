/* CloudCare — admin dashboard: API-driven tables + demo charts.
   All names/emails come from the API and are HTML-escaped with ccEscape. */
Chart.defaults.font.family = "'Manrope', sans-serif";
Chart.defaults.color = '#5b7370';
const ccTeal = '#2fb8a4', ccCoral = '#ff6f59', ccGold = '#e0a53c';

/* ---------- overview charts ---------- */
new Chart(document.getElementById('admissionsChart'), {
  type:'line',
  data:{ labels:['Wk1','Wk2','Wk3','Wk4','Wk5','Wk6','Wk7','Wk8'],
    datasets:[{ label:'Admissions', data:[210,224,198,240,255,238,262,281], borderColor:ccTeal, backgroundColor:'rgba(47,184,164,.12)', fill:true, tension:.4 }]},
  options:{ plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true}} }
});
new Chart(document.getElementById('deptChart'), {
  type:'doughnut',
  data:{ labels:['Cardiology','General','Pediatrics','Orthopedics','Neurology'],
    datasets:[{ data:[28,24,18,16,14], backgroundColor:[ccTeal,ccCoral,ccGold,'#7f938f','#8fe3d8'] }]},
  options:{ plugins:{legend:{position:'bottom', labels:{boxWidth:10, padding:14}}}, cutout:'62%' }
});

/* ---------- patients panel (API) ---------- */
async function ccLoadAdminPatients(){
  const tbody = document.getElementById('patientTable');
  try {
    ccRenderState(tbody, 'loading', '', 6);
    const patients = await ccApi('/api/patients?page_size=100');
    if(!patients.length){ ccRenderState(tbody, 'empty', 'No patients registered yet.', 6); return; }
    tbody.innerHTML = patients.map(p => {
      const risk = p.risk_score === null || p.risk_score === undefined ? 0 : p.risk_score;
      return `<tr><td><span class="avatar-sm">${ccEscape(p.name.split(' ').map(w=>w[0]).slice(0,2).join(''))}</span>${ccEscape(p.name)}</td>
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
  Declined: 'badge-slate',
  Cancelled: 'badge-slate',
};

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
      const when = new Date(a.scheduled_at).toLocaleString('en-IN', {
        day:'numeric', month:'short', hour:'numeric', minute:'2-digit', hour12:true,
      });
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
new Chart(document.getElementById('inventoryChart'), {
  type:'bar',
  data:{ labels:['Insulin','Amlodipine','Furosemide','Salbutamol','Oxygen (cyl.)'],
    datasets:[{ label:'Days of stock left', data:[4,6,7,9,11], backgroundColor:(c)=>c.raw<7?ccCoral:ccGold, borderRadius:6 }]},
  options:{ plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true}} }
});
document.getElementById('reorderList').innerHTML = ccComingSoon(
  'Inventory AI coming soon',
  'Reorder suggestions will appear here once the inventory module is connected.'
);

/* ---------- AI console: risk ---------- */
new Chart(document.getElementById('riskChart'), {
  type:'doughnut',
  data:{ labels:['Low','Medium','High'], datasets:[{ data:[62,26,12], backgroundColor:[ccTeal,ccGold,ccCoral] }]},
  options:{ plugins:{legend:{position:'bottom', labels:{boxWidth:10, padding:14}}}, cutout:'62%' }
});
document.getElementById('riskList').innerHTML = ccComingSoon(
  'AI risk scoring coming soon',
  'Patient risk rankings will appear here once the analytics module is connected.'
);

/* ---------- AI console: resource forecast ---------- */
new Chart(document.getElementById('resourceChart'), {
  type:'line',
  data:{ labels:['Day 2','Day 4','Day 6','Day 8','Day 10','Day 12','Day 14'],
    datasets:[{ label:'ICU occupancy %', data:[71,74,79,84,88,90,93], borderColor:ccCoral, backgroundColor:'rgba(255,111,89,.12)', fill:true, tension:.4 }]},
  options:{ plugins:{legend:{display:false}}, scales:{y:{min:50,max:100}} }
});
document.getElementById('resourceList').innerHTML = ccComingSoon(
  'Capacity forecasting coming soon',
  'Resource recommendations will appear here once the forecasting module is connected.'
);

/* ---------- reports panel ---------- */
new Chart(document.getElementById('reportChart'), {
  type:'bar',
  data:{ labels:['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug'],
    datasets:[
      { label:'Revenue (₹L)', data:[32,35,31,38,40,37,42,41.2], backgroundColor:ccTeal, borderRadius:6 },
      { label:'Expenses (₹L)', data:[24,25,23,27,28,26,29,28.5], backgroundColor:'#cfe8e3', borderRadius:6 },
    ]},
  options:{ plugins:{legend:{position:'bottom'}}, scales:{y:{beginAtZero:true}} }
});
