/* CloudCare — admin ops (loads AFTER dashboard-admin.js).
   Owns: doctors table + shifts/leaves manage panel, specialties CRUD,
   review moderation, appointments status filter, and the consolidated
   window.ccLoadAdminDashboard. All dynamic HTML is escaped with ccEscape. */
(function () {
  'use strict';

  let ccOpsDoctorId = null;
  const ccOpsDoctorFees = {};

  /* ================= doctors table + manage panel ================= */

  async function ccOpsLoadDoctors() {
    const tbody = document.getElementById('adminDoctorTable');
    const sel = document.getElementById('docManageSelect');
    try {
      ccRenderState(tbody, 'loading', '', 6);
      const [users, pub] = await Promise.all([
        ccApi('/api/admin/users?role=doctor&page_size=100'),
        ccApi('/api/doctors'),
      ]);
      const pubById = {};
      (pub || []).forEach((d) => { pubById[d.id] = d; });
      users.forEach((u) => { ccOpsDoctorFees[u.id] = u.consultation_fee; });

      if (sel) {
        sel.innerHTML = '<option value="">Choose a doctor…</option>' +
          users.map((u) => `<option value="${ccEscape(u.id)}">${ccEscape(u.name)}</option>`).join('');
        if (ccOpsDoctorId) sel.value = ccOpsDoctorId;
      }

      if (!users.length) {
        ccRenderState(tbody, 'empty', 'No doctors registered yet.', 6);
        return;
      }
      tbody.innerHTML = users.map((u) => {
        const d = pubById[u.id] || {};
        const shifts = (d.shifts || [])
          .map((s) => `${ccEscape(s.name)} ${ccEscape(s.start)}–${ccEscape(s.end)}`)
          .join('<br>') || 'Clinic default';
        const rating = d.average_rating != null
          ? `★ ${d.average_rating} <span style="font-size:.75rem;color:var(--slate-400);">(${d.review_count || 0})</span>`
          : '<span style="color:var(--slate-400);">—</span>';
        return `<tr>
          <td><strong>${ccEscape(u.name)}</strong></td>
          <td>${ccEscape(u.department || 'General')}</td>
          <td style="font-size:.82rem;color:var(--slate-500);">${ccEscape(u.email)}</td>
          <td style="font-size:.8rem;">${shifts}</td>
          <td>${rating}</td>
          <td style="text-align:right;">
            <button class="btn btn-ghost btn-sm" data-manage="${ccEscape(u.id)}" data-name="${ccEscape(u.name)}">Manage</button>
            <button class="btn btn-ghost btn-sm" data-del-doc="${ccEscape(u.id)}" data-name="${ccEscape(u.name)}" style="color:#c9432c;">Delete</button>
          </td>
        </tr>`;
      }).join('');
      tbody.querySelectorAll('[data-manage]').forEach((btn) => {
        btn.addEventListener('click', () => openDocManage(btn.dataset.manage, btn.dataset.name));
      });
      tbody.querySelectorAll('[data-del-doc]').forEach((btn) => {
        btn.addEventListener('click', () => onDeleteDoctor(btn.dataset.delDoc, btn.dataset.name));
      });
    } catch (err) {
      ccRenderState(tbody, 'error', err.message, 6);
    }
  }

  function openDocManage(id, name) {
    ccOpsDoctorId = id;
    const grid = document.getElementById('doctorGrid');
    if (grid) grid.style.display = 'none';
    document.getElementById('docManagePanel').style.display = '';
    document.getElementById('docManageTitle').textContent = `Manage shifts & leaves — ${name}`;
    const sel = document.getElementById('docManageSelect');
    if (sel && sel.value !== id) sel.value = id;
    loadDocManage(id);
  }

  async function loadDocManage(id) {
    const box = document.getElementById('docManageContent');
    box.innerHTML = '<p style="color:var(--slate-400);font-size:.85rem;">Loading…</p>';
    try {
      const [shifts, leaves] = await Promise.all([
        ccApi(`/api/admin/doctors/${id}/shifts`),
        ccApi(`/api/admin/doctors/${id}/leaves`),
      ]);
      renderDocManage(id, shifts || [], leaves || []);
    } catch (err) {
      box.innerHTML = `<p style="color:#c9432c;font-size:.85rem;">${ccEscape(err.message)}</p>`;
    }
  }

  function renderDocManage(id, shifts, leaves) {
    const box = document.getElementById('docManageContent');
    const fee = ccOpsDoctorFees[id] == null ? 500 : ccOpsDoctorFees[id];
    const morning = shifts.find((s) => s.name === 'Morning') || { start: '06:00', end: '14:00' };
    const evening = shifts.find((s) => s.name === 'Evening') || { start: '14:00', end: '22:00' };
    const leaveRows = leaves.length
      ? leaves.map((l) => `
        <div class="flex items-center justify-between" style="padding:8px 0;border-bottom:1px solid var(--cloud-100);">
          <span style="font-size:.85rem;">${ccEscape(String(l.leave_date))} — ${l.shift_name ? `${ccEscape(l.shift_name)} shift` : 'Whole day'}</span>
          <button type="button" class="btn btn-ghost btn-sm" data-del-leave="${ccEscape(l.id)}">Remove</button>
        </div>`).join('')
      : '<p style="font-size:.85rem;color:var(--slate-400);">No upcoming leaves.</p>';

    box.innerHTML = `
      <div class="grid grid-2" style="align-items:start;">
        <form id="opsShiftForm">
          <h4 style="margin-bottom:14px;">Shift hours (IST)</h4>
          <div class="grid grid-2" style="gap:12px;">
            <div class="field"><label for="opsMStart">Morning start</label><input id="opsMStart" type="time" value="${ccEscape(morning.start)}" step="900" required></div>
            <div class="field"><label for="opsMEnd">Morning end</label><input id="opsMEnd" type="time" value="${ccEscape(morning.end)}" step="900" required></div>
            <div class="field"><label for="opsEStart">Evening start</label><input id="opsEStart" type="time" value="${ccEscape(evening.start)}" step="900" required></div>
            <div class="field"><label for="opsEEnd">Evening end</label><input id="opsEEnd" type="time" value="${ccEscape(evening.end)}" step="900" required></div>
          </div>
          <button type="submit" class="btn btn-primary btn-sm mt-8">Save shifts</button>
          <p class="mb-0" style="font-size:.78rem;color:var(--slate-400);margin-top:12px;">Replaces both rules; times align to 15-minute boundaries and must overlap the 05:00–22:00 booking window.</p>
        </form>
        <form id="opsFeeForm" class="mt-16">
          <h4 style="margin-bottom:14px;">Consultation fee</h4>
          <div class="field"><label for="opsFee">Fee per visit (INR)</label>
            <input id="opsFee" type="number" min="0" max="1000000" step="1" value="${ccEscape(String(fee))}" required>
            <span class="field-hint">Billed automatically when an appointment is completed.</span>
          </div>
          <button type="submit" class="btn btn-primary btn-sm">Save fee</button>
        </form>
        <div>
          <h4 style="margin-bottom:14px;">Upcoming leaves</h4>
          <div id="opsLeaveList">${leaveRows}</div>
          <form id="opsLeaveForm" class="mt-16">
            <div class="grid grid-2" style="gap:12px;">
              <div class="field"><label for="opsLeaveDate">Leave date</label><input id="opsLeaveDate" type="date" required></div>
              <div class="field"><label for="opsLeaveShift">Scope</label>
                <select id="opsLeaveShift">
                  <option value="">Whole day</option>
                  <option value="Morning">Morning shift</option>
                  <option value="Evening">Evening shift</option>
                </select>
              </div>
            </div>
            <button type="submit" class="btn btn-primary btn-sm">Add leave</button>
          </form>
        </div>
      </div>`;

    document.getElementById('opsLeaveDate').min = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date());

    document.getElementById('opsShiftForm').addEventListener('submit', onShiftSubmit);
    document.getElementById('opsFeeForm').addEventListener('submit', onFeeSubmit);
    document.getElementById('opsLeaveForm').addEventListener('submit', onLeaveSubmit);
    box.querySelectorAll('[data-del-leave]').forEach((btn) => {
      btn.addEventListener('click', () => onDeleteLeave(btn.dataset.delLeave, id));
    });
  }

  async function onShiftSubmit(ev) {
    ev.preventDefault();
    if (!ccOpsDoctorId) return;
    const val = (id) => document.getElementById(id).value;
    const payload = {
      shifts: [
        { name: 'Morning', start: val('opsMStart'), end: val('opsMEnd') },
        { name: 'Evening', start: val('opsEStart'), end: val('opsEEnd') },
      ],
    };
    try {
      const saved = await ccApi(`/api/admin/doctors/${ccOpsDoctorId}/shifts`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      ccToast('Shift hours saved.');
      let leaves = [];
      try { leaves = await ccApi(`/api/admin/doctors/${ccOpsDoctorId}/leaves`); } catch (e) { /* non-fatal */ }
      renderDocManage(ccOpsDoctorId, saved || [], leaves || []);
      ccOpsLoadDoctors();
    } catch (err) {
      ccToast(err.message);
    }
  }

  async function onFeeSubmit(ev) {
    ev.preventDefault();
    if (!ccOpsDoctorId) return;
    const value = Number(document.getElementById('opsFee').value);
    try {
      await ccApi(`/api/admin/users/${ccOpsDoctorId}`, {
        method: 'PATCH',
        body: JSON.stringify({ consultation_fee: value }),
      });
      ccOpsDoctorFees[ccOpsDoctorId] = value;
      ccToast('Consultation fee saved.');
      ccOpsLoadDoctors();
    } catch (err) {
      ccToast(err.message);
    }
  }

  async function onLeaveSubmit(ev) {
    ev.preventDefault();
    if (!ccOpsDoctorId) return;
    const payload = {
      leave_date: document.getElementById('opsLeaveDate').value,
      shift_name: document.getElementById('opsLeaveShift').value || null,
    };
    try {
      const created = await ccApi(`/api/admin/doctors/${ccOpsDoctorId}/leaves`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      const cancelled = (created && created[0] && created[0].cancelled_appointments) || 0;
      ccToast(`Leave saved. ${cancelled} appointment(s) were cancelled and patients notified.`);
      loadDocManage(ccOpsDoctorId);
      ccOpsLoadDoctors();
    } catch (err) {
      ccToast(err.message);
    }
  }

  async function onDeleteLeave(leaveId, doctorId) {
    try {
      await ccApi(`/api/admin/leaves/${leaveId}`, { method: 'DELETE' });
      ccToast('Leave removed.');
      loadDocManage(doctorId);
    } catch (err) {
      ccToast(err.message);
    }
  }

  /* ================= specialties ================= */

  async function ccOpsLoadSpecialties() {
    const tbody = document.getElementById('specialtyTable');
    try {
      ccRenderState(tbody, 'loading', '', 2);
      const rows = await ccApi('/api/admin/specialties');
      if (!rows.length) {
        ccRenderState(tbody, 'empty', 'No specialties yet — add the first one.', 2);
        return;
      }
      tbody.innerHTML = rows.map((s) => `
        <tr>
          <td>${ccEscape(s.name)}</td>
          <td style="text-align:right;"><button class="btn btn-ghost btn-sm" data-del-spec="${ccEscape(s.id)}" data-name="${ccEscape(s.name)}">Delete</button></td>
        </tr>`).join('');
      tbody.querySelectorAll('[data-del-spec]').forEach((btn) => {
        btn.addEventListener('click', () => onDeleteSpecialty(btn.dataset.delSpec, btn.dataset.name));
      });
    } catch (err) {
      ccRenderState(tbody, 'error', err.message, 2);
    }
  }

  function onDeleteSpecialty(id, name) {
    /* gate C9: shared .cc-modal-head / -body / -foot structure + red btn-danger */
    ccModalOpen(`
      <div class="cc-modal-head">
        <div><h3>Delete specialty?</h3><p>This cannot be undone.</p></div>
        <button class="cc-modal-close" data-modal-close aria-label="Close">✕</button>
      </div>
      <div class="cc-modal-body">
        <div class="cc-modal-summary"><strong>${ccEscape(name)}</strong> will be removed permanently. Doctors keep their records; this only removes it from the booking dropdown.</div>
      </div>
      <div class="cc-modal-foot">
        <button type="button" class="btn btn-ghost" id="opsSpecNo">Cancel</button>
        <button type="button" class="btn btn-danger" id="opsSpecYes">Delete specialty</button>
      </div>`);
    document.getElementById('opsSpecYes').addEventListener('click', async () => {
      try {
        await ccApi(`/api/admin/specialties/${id}`, { method: 'DELETE' });
        ccModalClose();
        ccToast('Specialty deleted.');
        ccOpsLoadSpecialties();
      } catch (err) {
        ccModalClose();
        ccToast(err.message);
      }
    });
    document.getElementById('opsSpecNo').addEventListener('click', ccModalClose);
  }

  /* ================= review moderation ================= */

  async function ccOpsLoadReviews() {
    const tbody = document.getElementById('adminReviewTable');
    try {
      ccRenderState(tbody, 'loading', '', 5);
      const page = await ccApi('/api/admin/reviews?page_size=50');
      const items = (page && page.items) || [];
      if (!items.length) {
        ccRenderState(tbody, 'empty', 'No reviews to moderate.', 5);
        return;
      }
      tbody.innerHTML = items.map((r) => {
        const stars = '★'.repeat(r.rating) + '☆'.repeat(5 - r.rating);
        const when = new Date(r.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
        return `<tr>
          <td>${ccEscape(r.doctor_name)}</td>
          <td>${ccEscape(r.author_name)}<br><span class="mono" style="font-size:.72rem;color:var(--slate-400);">${ccEscape(when)}</span></td>
          <td style="color:#e0a53c;white-space:nowrap;">${stars}</td>
          <td style="max-width:320px;font-size:.85rem;">${ccEscape(r.text)}</td>
          <td style="text-align:right;"><button class="btn btn-ghost btn-sm" data-del-rev="${ccEscape(r.id)}">Delete</button></td>
        </tr>`;
      }).join('');
      tbody.querySelectorAll('[data-del-rev]').forEach((btn) => {
        btn.addEventListener('click', () => onDeleteReview(btn.dataset.delRev));
      });
    } catch (err) {
      ccRenderState(tbody, 'error', err.message, 5);
    }
  }

  function onDeleteReview(reviewId) {
    ccModalOpen(`
      <div class="cc-modal-head">
        <div><h3>Delete this review?</h3><p>Removal is permanent.</p></div>
        <button class="cc-modal-close" data-modal-close aria-label="Close">✕</button>
      </div>
      <div class="cc-modal-body">
        <div class="cc-modal-summary">The patient would have to write it again — there is no undo and no notification.</div>
      </div>
      <div class="cc-modal-foot">
        <button type="button" class="btn btn-ghost" id="opsRevNo">Cancel</button>
        <button type="button" class="btn btn-danger" id="opsRevYes">Delete review</button>
      </div>`);
    document.getElementById('opsRevYes').addEventListener('click', async () => {
      try {
        await ccApi(`/api/admin/reviews/${reviewId}`, { method: 'DELETE' });
        ccModalClose();
        ccToast('Review removed.');
        ccOpsLoadReviews();
      } catch (err) {
        ccModalClose();
        ccToast(err.message);
      }
    });
    document.getElementById('opsRevNo').addEventListener('click', ccModalClose);
  }

  /* ================= wiring ================= */

  const docManageToggle = document.getElementById('docManageToggle');
  if (docManageToggle) {
    docManageToggle.addEventListener('click', () => {
      const panel = document.getElementById('docManagePanel');
      const show = panel.style.display === 'none' || panel.style.display === '';
      panel.style.display = show ? '' : 'none';
      if (show) {
        const sel = document.getElementById('docManageSelect');
        if (sel.value) loadDocManage(sel.value);
        else if (ccOpsDoctorId) loadDocManage(ccOpsDoctorId);
      }
    });
  }
  const docManageClose = document.getElementById('docManageClose');
  if (docManageClose) {
    docManageClose.addEventListener('click', () => {
      document.getElementById('docManagePanel').style.display = 'none';
    });
  }
  const docManageSelect = document.getElementById('docManageSelect');
  if (docManageSelect) {
    docManageSelect.addEventListener('change', () => {
      const id = docManageSelect.value;
      if (!id) return;
      ccOpsDoctorId = id;
      document.getElementById('docManagePanel').style.display = '';
      document.getElementById('docManageTitle').textContent =
        `Manage shifts & leaves — ${docManageSelect.options[docManageSelect.selectedIndex].text}`;
      loadDocManage(id);
    });
  }

  const specialtyForm = document.getElementById('specialtyForm');
  if (specialtyForm) {
    specialtyForm.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const input = document.getElementById('specialtyName');
      try {
        await ccApi('/api/admin/specialties', {
          method: 'POST',
          body: JSON.stringify({ name: input.value.trim() }),
        });
        input.value = '';
        ccToast('Specialty added.');
        ccOpsLoadSpecialties();
      } catch (err) {
        ccToast(err.message);
      }
    });
  }

  const apptStatusFilter = document.getElementById('apptStatusFilter');
  if (apptStatusFilter) {
    apptStatusFilter.addEventListener('change', () => {
      if (window.ccLoadAdminAppointments) window.ccLoadAdminAppointments();
    });
  }

  /* ================= safe doctor delete (gate 22) ================= */

  function onDeleteDoctor(doctorId, name) {
    ccModalOpen(`
      <div class="cc-modal-head">
        <div><h3>Delete ${ccEscape(name)}?</h3><p>Their shifts, leaves and reviews go with the account.</p></div>
        <button class="cc-modal-close" data-modal-close aria-label="Close">✕</button>
      </div>
      <div class="cc-modal-body">
        <div class="cc-modal-summary">Removes the account, their shifts, leaves and reviews. Their appointment records are removed with the account; assigned patients keep their records (unassigned).</div>
      </div>
      <div class="cc-modal-foot">
        <button type="button" class="btn btn-ghost" id="opsDocNo">Cancel</button>
        <button type="button" class="btn btn-danger" id="opsDocYes">Delete doctor</button>
      </div>`);
    document.getElementById('opsDocYes').addEventListener('click', async () => {
      try {
        await ccApi(`/api/admin/doctors/${doctorId}`, { method: 'DELETE' });
        ccModalClose();
        ccToast('Doctor deleted.');
        afterDoctorDelete(doctorId);
      } catch (err) {
        /* 409: the doctor still has active appointments. The server message
           carries the counts; offer the explicit cancel=true escalation. */
        ccModalClose();
        ccModalOpen(`
          <div class="cc-modal-head">
            <div><h3>Active appointments</h3><p>The doctor cannot be deleted while visits are scheduled.</p></div>
            <button class="cc-modal-close" data-modal-close aria-label="Close">✕</button>
          </div>
          <div class="cc-modal-body">
            <div class="cc-modal-summary">${ccEscape(err.message)}</div>
          </div>
          <div class="cc-modal-foot">
            <button type="button" class="btn btn-ghost" id="opsDocKeep">Keep doctor</button>
            <button type="button" class="btn btn-danger" id="opsDocForce">Cancel their appointments &amp; delete</button>
          </div>`);
        document.getElementById('opsDocForce').addEventListener('click', async () => {
          try {
            await ccApi(`/api/admin/doctors/${doctorId}?cancel=true`, { method: 'DELETE' });
            ccModalClose();
            ccToast('Doctor deleted; their active appointments were cancelled.');
            afterDoctorDelete(doctorId);
          } catch (err2) {
            ccModalClose();
            ccToast(err2.message);
          }
        });
        document.getElementById('opsDocKeep').addEventListener('click', ccModalClose);
      }
    });
    document.getElementById('opsDocNo').addEventListener('click', ccModalClose);
  }

  function afterDoctorDelete(doctorId) {
    if (ccOpsDoctorId === doctorId) {
      ccOpsDoctorId = null;
      document.getElementById('docManagePanel').style.display = 'none';
    }
    ccOpsLoadDoctors();
  }

  /* ================= accounts (gate 25b) ================= */

  const CC_ROLES = ['patient', 'doctor', 'nurse', 'pharmacist', 'lab', 'admin'];
  const CC_ROLE_LABEL = { patient: 'Patient', doctor: 'Doctor', nurse: 'Nurse', pharmacist: 'Pharmacist', lab: 'Lab', admin: 'Admin' };

  /* gate S3: a role change is confirmed before it hits the API; cancelling
     reverts the select to the value it had before the modal opened. */
  function confirmRoleChange(sel, userId, name, role) {
    const previous = sel.dataset.previous || sel.value;
    /* Revert the <select> immediately: the UI only shows the new role after
       the admin confirms — cancel (or ✕ / Escape / overlay click) therefore
       needs no cleanup, the old role is already displayed. */
    sel.value = previous;
    ccModalOpen(`
      <div class="cc-modal-head">
        <div><h3>Change role?</h3><p>Permissions update immediately.</p></div>
        <button class="cc-modal-close" data-modal-close aria-label="Close">✕</button>
      </div>
      <div class="cc-modal-body">
        <div class="cc-modal-summary"><strong>${ccEscape(name)}</strong> will become <strong>${ccEscape(CC_ROLE_LABEL[role] || role)}</strong>.</div>
      </div>
      <div class="cc-modal-foot">
        <button type="button" class="btn btn-ghost" id="roleNo">Cancel</button>
        <button type="button" class="btn btn-primary" id="roleYes">Change role</button>
      </div>`);
    document.getElementById('roleNo').addEventListener('click', () => {
      ccModalClose(); // select already shows `previous` — nothing was sent
    });
    document.getElementById('roleYes').addEventListener('click', async function(){
      this.disabled = true;
      try {
        await ccApi(`/api/admin/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ role }),
        });
        ccModalClose();
        ccToast(`${name} is now ${CC_ROLE_LABEL[role] || role}.`);
        sel.dataset.previous = role;
      } catch (err) {
        ccModalClose();
        ccToast(err.message); // e.g. "You cannot change your own role."
        sel.value = previous;
      }
      ccOpsLoadAccounts();
    });
  }

  async function ccOpsLoadAccounts() {
    const tbody = document.getElementById('accountTable');
    try {
      ccRenderState(tbody, 'loading', '', 5);
      const filter = document.getElementById('accountRoleFilter');
      const role = filter && filter.value ? `&role=${encodeURIComponent(filter.value)}` : '';
      const users = await ccApi(`/api/admin/users?page_size=100${role}`);
      if (!users.length) {
        ccRenderState(tbody, 'empty', 'No accounts match this filter.', 5);
        return;
      }
      tbody.innerHTML = users.map((u) => {
        const opts = CC_ROLES.map((r) =>
          `<option value="${r}"${r === u.role ? ' selected' : ''}>${CC_ROLE_LABEL[r]}</option>`).join('');
        const roleSel = `<select data-role-for="${ccEscape(u.id)}" data-name="${ccEscape(u.name)}" data-previous="${ccEscape(u.role)}" style="padding:6px 10px;border-radius:8px;border:1.5px solid var(--line);font-size:.8rem;">${opts}</select>`;
        return `<tr>
          <td><strong>${ccEscape(u.name)}</strong></td>
          <td style="font-size:.82rem;color:var(--slate-500);">${ccEscape(u.email)}</td>
          <td>${roleSel}</td>
          <td>${ccEscape(u.department || '—')}</td>
          <td style="text-align:right;"><button class="btn btn-ghost btn-sm" data-del-user="${ccEscape(u.id)}" data-name="${ccEscape(u.name)}" style="color:#c9432c;">Delete</button></td>
        </tr>`;
      }).join('');
      tbody.querySelectorAll('select[data-role-for]').forEach((sel) => {
        sel.addEventListener('change', () => confirmRoleChange(sel, sel.dataset.roleFor, sel.dataset.name, sel.value));
      });
      tbody.querySelectorAll('[data-del-user]').forEach((btn) => {
        btn.addEventListener('click', () => onDeleteUser(btn.dataset.delUser, btn.dataset.name));
      });
    } catch (err) {
      ccRenderState(tbody, 'error', err.message, 5);
    }
  }

  function onDeleteUser(userId, name) {
    ccModalOpen(`
      <div class="cc-modal-head">
        <div><h3>Delete account?</h3><p>This cannot be undone.</p></div>
        <button class="cc-modal-close" data-modal-close aria-label="Close">✕</button>
      </div>
      <div class="cc-modal-body">
        <div class="cc-modal-summary"><strong>${ccEscape(name)}</strong>'s account and their patient/clinical records will be removed.</div>
      </div>
      <div class="cc-modal-foot">
        <button type="button" class="btn btn-ghost" id="opsUserNo">Cancel</button>
        <button type="button" class="btn btn-danger" id="opsUserYes">Delete account</button>
      </div>`);
    document.getElementById('opsUserYes').addEventListener('click', async () => {
      try {
        await ccApi(`/api/admin/users/${userId}`, { method: 'DELETE' });
        ccModalClose();
        ccToast('Account deleted.');
        ccOpsLoadAccounts();
      } catch (err) {
        ccModalClose();
        ccToast(err.message); // e.g. "You cannot delete your own account."
      }
    });
    document.getElementById('opsUserNo').addEventListener('click', ccModalClose);
  }

  const accountForm = document.getElementById('accountForm');
  if (accountForm) {
    accountForm.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      try {
        await ccApi('/api/admin/users', {
          method: 'POST',
          body: JSON.stringify({
            name: document.getElementById('accName').value.trim(),
            email: document.getElementById('accEmail').value.trim(),
            password: document.getElementById('accPassword').value,
            role: document.getElementById('accRole').value,
            department: document.getElementById('accDept').value.trim() || null,
          }),
        });
        accountForm.reset();
        ccToast('Account created.');
        ccOpsLoadAccounts();
      } catch (err) {
        ccToast(err.message);
      }
    });
  }
  const accountRoleFilter = document.getElementById('accountRoleFilter');
  if (accountRoleFilter) {
    accountRoleFilter.addEventListener('change', ccOpsLoadAccounts);
  }

  /* Consolidated dashboard loader — replaces the one from dashboard-admin.js
     (this file loads after it, and the guard's async call resolves later). */
  window.ccLoadAdminDashboard = function () {
    if (window.ccLoadAdminKpis) window.ccLoadAdminKpis(); /* gate C5 */
    if (window.ccLoadAdminPatients) window.ccLoadAdminPatients();
    if (window.ccLoadAdminAppointments) window.ccLoadAdminAppointments();
    if (window.ccLoadAdminBills) window.ccLoadAdminBills();
    ccOpsLoadDoctors();
    ccOpsLoadSpecialties();
    ccOpsLoadReviews();
    ccOpsLoadAccounts();
  };
})();
