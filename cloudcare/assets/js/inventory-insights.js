/* CloudCare — admin Inventory tab.
   The Smart Inventory Management System runs on the API (admin-only) and
   reads the live stock table plus audited stock movements. This file renders
   its forecasts, reorder drafts and expiry warnings, and records real stock
   movements through the existing audited endpoints. */
(function(){
  const table = document.getElementById('inventoryTable');
  if(!table) return;

  const recommendations = document.getElementById('inventoryRecommendations');
  const expiring = document.getElementById('inventoryExpiring');
  const modelBadge = document.getElementById('inventoryModelBadge');
  const refreshBtn = document.getElementById('inventoryRefresh');
  const addBtn = document.getElementById('inventoryAddBtn');
  const link = document.querySelector('.side-nav a[data-panel="inventory"]');
  ccRegisterRetry('inventoryTable', () => load());
  let loaded = false;
  let loading = false;

  const STATUS_BADGE = {
    'Expired stock': 'badge-coral',
    'Reorder now': 'badge-coral',
    'Use stock before expiry': 'badge-gold',
    Monitor: 'badge-gold',
    Healthy: 'badge-mint',
  };

  function money(value){
    return '₹' + Number(value || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  }

  function setKpi(id, value){
    const el = document.getElementById(id);
    if(el) el.textContent = value;
  }

  function statusBadge(status){
    return `<span class="badge ${STATUS_BADGE[status] || 'badge-slate'}">${ccEscape(status)}</span>`;
  }

  function emptyBox(message){
    return `<div class="cc-empty-box">${ccEscape(message)}</div>`;
  }

  function errorBox(message){
    return `<div class="cc-load-error"><span>${ccEscape(message)}</span>
      <div><button class="btn btn-ghost btn-sm" type="button" data-inventory-retry>Try again</button></div></div>`;
  }

  function bindRetry(button, handler){
    if(button) button.addEventListener('click', handler);
  }

  function renderRecommendations(data){
    if(!recommendations) return;
    const drafts = Array.isArray(data.reorder_drafts) ? data.reorder_drafts : [];
    if(!drafts.length){
      recommendations.innerHTML = emptyBox('Nothing needs ordering today. Keep recording stock use so the forecast stays current.');
      return;
    }
    recommendations.innerHTML = `
      <div class="cc-inline-head">
        <h4>${drafts.length} item${drafts.length === 1 ? '' : 's'} need attention</h4>
        <span class="cc-forecast-meta">Updated ${ccEscape(ccClinicStamp(data.generated_at))}</span>
      </div>
      <ul class="cc-rec-list">
        ${drafts.slice(0, 5).map(row => `
          <li class="cc-rec-item">
            <div class="cc-inline-head" style="margin-bottom:4px;">
              <strong>${ccEscape(row.name)}</strong>
              ${statusBadge(row.status)}
            </div>
            <p style="margin:0 0 6px;">${ccEscape(row.reason)}</p>
            <div class="cc-rec-meta">
              ${row.recommended_order_quantity > 0 ? `<span class="badge badge-slate">Draft order: ${row.recommended_order_quantity} units</span>` : ''}
              <span class="badge badge-slate">About ${money(row.recommended_order_quantity * row.unit_cost)}</span>
              <button class="btn btn-ghost btn-sm" type="button" data-adjust-id="${row.item_id}">Record stock movement</button>
            </div>
          </li>`).join('')}
      </ul>`;
    recommendations.querySelectorAll('[data-adjust-id]').forEach(button => {
      const id = Number(button.dataset.adjustId);
      button.addEventListener('click', () => {
        const row = (window.__ccInventoryRows || []).find(item => item.item_id === id);
        if(row) openAdjust(row);
      });
    });
  }

  function renderExpiring(data){
    if(!expiring) return;
    const rows = Array.isArray(data.expiring) ? data.expiring : [];
    if(!rows.length){
      expiring.innerHTML = emptyBox('No stock is expired or expiring in the next 30 days.');
      return;
    }
    expiring.innerHTML = `<ul class="cc-rec-list">${rows.slice(0, 6).map(row => {
      const days = Number(row.days_to_expiry);
      const timing = days < 0
        ? `Expired ${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'} ago`
        : days === 0 ? 'Expires today' : `Expires in ${days} day${days === 1 ? '' : 's'}`;
      return `<li class="cc-rec-item">
        <div class="cc-inline-head" style="margin-bottom:4px;">
          <strong>${ccEscape(row.name)}</strong>
          <span class="badge ${days < 0 ? 'badge-coral' : 'badge-gold'}">${ccEscape(timing)}</span>
        </div>
        <span class="cc-forecast-meta">${row.quantity} unit(s) in stock</span>
      </li>`;
    }).join('')}</ul>`;
  }

  function renderTable(data){
    const rows = Array.isArray(data.forecasts) ? data.forecasts : [];
    window.__ccInventoryRows = rows;
    if(!rows.length){
      ccRenderState(table, 'empty', 'No stock items yet. Add the first item to start forecasting.', 6);
      return;
    }
    table.innerHTML = rows.map(row => {
      const forecast = row.average_daily_use > 0
        ? `${row.predicted_30_day_demand} units <span class="cc-forecast-meta">(${Math.round(row.confidence * 100)}% confidence)</span>`
        : '<span class="cc-forecast-meta">No use recorded yet</span>';
      return `<tr>
        <td data-label="Item"><span class="cc-inventory-name">${ccEscape(row.name)}</span><span class="cc-inventory-sub">${ccEscape(row.sku)} · ${ccEscape(row.category)}</span></td>
        <td data-label="In stock" class="mono">${row.quantity}</td>
        <td data-label="Reorder at" class="mono">${row.reorder_threshold}</td>
        <td data-label="Next 30 days" title="${ccEscape(row.method)}. ${ccEscape(row.trend)}">${forecast}</td>
        <td data-label="Status">${statusBadge(row.status)}</td>
        <td data-label="Actions"><div class="cc-inventory-actions">
          <button class="btn btn-ghost btn-sm" type="button" data-adjust-id="${row.item_id}">Update stock</button>
        </div></td>
      </tr>`;
    }).join('');
    table.querySelectorAll('[data-adjust-id]').forEach(button => {
      const id = Number(button.dataset.adjustId);
      const row = rows.find(item => item.item_id === id);
      button.addEventListener('click', () => { if(row) openAdjust(row); });
    });
  }

  function renderAiConsole(data){
    const badge = document.getElementById('inventoryForecastBadge');
    if(badge){
      badge.textContent = 'Live forecast';
      badge.className = 'badge badge-mint';
    }
    const summary = document.getElementById('inventoryForecastSummary');
    if(summary){
      const rows = (data.forecasts || []).slice(0, 4);
      summary.innerHTML = rows.length
        ? rows.map(row => `
          <div class="risk-row">
            <div>
              <strong style="display:block;font-size:.9rem;">${ccEscape(row.name)}</strong>
              <span style="display:block;font-size:.76rem;color:var(--slate-400);">${row.predicted_30_day_demand} units over 30 days · ${Math.round(row.confidence * 100)}% confidence</span>
            </div>
            ${statusBadge(row.status)}
          </div>`).join('')
        : emptyBox('No stock items yet. Add an item in the Inventory tab to start forecasting.');
    }

    const list = document.getElementById('reorderList');
    if(!list) return;
    const drafts = Array.isArray(data.reorder_drafts) ? data.reorder_drafts : [];
    if(!drafts.length){
      list.innerHTML = emptyBox('No reorder drafts right now. Stock is at a comfortable level.');
      return;
    }
    list.innerHTML = drafts.slice(0, 4).map(row => `
      <div class="risk-row">
        <div>
          <strong style="display:block;font-size:.9rem;">${ccEscape(row.name)}</strong>
          <span style="display:block;font-size:.76rem;color:var(--slate-400);">${ccEscape(row.reason)}</span>
        </div>
        <span class="badge ${STATUS_BADGE[row.status] || 'badge-slate'}">${row.recommended_order_quantity > 0 ? `${row.recommended_order_quantity} units` : ccEscape(row.status)}</span>
      </div>`).join('');
  }

  function fallbackError(message){
    ccRenderState(table, 'error', message, 6);
    if(recommendations){
      recommendations.innerHTML = errorBox(message);
      bindRetry(recommendations.querySelector('[data-inventory-retry]'), load);
    }
    if(expiring){
      expiring.innerHTML = errorBox('Could not load expiry dates.');
      bindRetry(expiring.querySelector('[data-inventory-retry]'), load);
    }
    setKpi('invKpiItems', '—');
    setKpi('invKpiValue', '—');
    setKpi('invKpiLow', '—');
    setKpi('invKpiExpiring', '—');
  }

  async function load(){
    if(loading) return;
    loading = true;
    if(modelBadge){
      modelBadge.textContent = 'Loading…';
      modelBadge.className = 'badge badge-slate';
    }
    ccRenderState(table, 'loading', '', 6);
    try {
      const data = await ccApi('/api/inventory/insights');
      loaded = true;
      const summary = data.summary || {};
      setKpi('invKpiItems', String(summary.total_items || 0));
      setKpi('invKpiValue', money(summary.total_stock_value));
      setKpi('invKpiLow', String(summary.low_stock || 0));
      setKpi('invKpiExpiring', String((summary.expiring_soon || 0) + (summary.expired || 0)));
      const overviewValue = document.getElementById('invOverviewValue');
      const overviewBadge = document.getElementById('invOverviewBadge');
      const overviewNote = document.getElementById('invOverviewNote');
      if(overviewValue) overviewValue.textContent = `${summary.reorder_now || 0} to reorder`;
      if(overviewBadge){
        overviewBadge.textContent = summary.reorder_now ? 'Needs attention' : 'Stock healthy';
        overviewBadge.className = 'badge ' + (summary.reorder_now ? 'badge-coral' : 'badge-mint');
      }
      if(overviewNote){
        overviewNote.textContent = summary.reorder_now
          ? `${summary.reorder_now} item(s) at or below the reorder level. Open the console for the draft orders.`
          : 'No item needs ordering today. The forecast uses recorded stock use.';
      }
      if(modelBadge){
        modelBadge.textContent = 'Live forecast';
        modelBadge.className = 'badge badge-mint';
      }
      renderRecommendations(data);
      renderExpiring(data);
      renderTable(data);
      renderAiConsole(data);
    } catch(err){
      fallbackError(err && err.message ? err.message : 'Could not load inventory forecasts. Please try again.');
      if(modelBadge){
        modelBadge.textContent = 'Not available';
        modelBadge.className = 'badge badge-slate';
      }
      const forecastBadge = document.getElementById('inventoryForecastBadge');
      if(forecastBadge){
        forecastBadge.textContent = 'Not available';
        forecastBadge.className = 'badge badge-slate';
      }
      const forecastSummary = document.getElementById('inventoryForecastSummary');
      if(forecastSummary){
        forecastSummary.innerHTML = errorBox('Could not load the stock forecast.');
        bindRetry(forecastSummary.querySelector('[data-inventory-retry]'), load);
      }
    } finally {
      loading = false;
    }
  }

  /* ---------- add an item ---------- */
  function openAdd(){
    const overlay = ccModalOpen(`
      <div class="cc-modal-head">
        <div><h3>Add a stock item</h3><p>Record what the clinic keeps on the shelf.</p></div>
        <button class="cc-modal-close" data-modal-close aria-label="Close">✕</button>
      </div>
      <form id="inventoryAddForm" novalidate>
        <div class="cc-modal-body">
          <p class="form-error" data-form-error role="alert" hidden></p>
          <div class="field"><label for="invName">Item name</label><input id="invName" type="text" required minlength="2" maxlength="200" placeholder="e.g. Surgical gloves"></div>
          <div class="cc-field-row">
            <div class="field"><label for="invCategory">Category</label><input id="invCategory" type="text" required minlength="2" maxlength="100" placeholder="e.g. PPE"></div>
            <div class="field"><label for="invSku">Stock code (SKU)</label><input id="invSku" type="text" required minlength="3" maxlength="50" placeholder="e.g. PPE-001"></div>
          </div>
          <div class="cc-field-row">
            <div class="field"><label for="invQuantity">Quantity in stock</label><input id="invQuantity" type="number" inputmode="numeric" min="0" step="1" required placeholder="e.g. 40"></div>
            <div class="field"><label for="invThreshold">Reorder level</label><input id="invThreshold" type="number" inputmode="numeric" min="0" step="1" value="10" required><p class="field-hint">The assistant suggests an order when stock reaches this number.</p></div>
          </div>
          <div class="cc-field-row">
            <div class="field"><label for="invCost">Unit cost (₹)</label><input id="invCost" type="number" inputmode="decimal" min="0" step="0.01" required placeholder="e.g. 2.50"></div>
            <div class="field"><label for="invPrice">Selling price (₹)</label><input id="invPrice" type="number" inputmode="decimal" min="0" step="0.01" required placeholder="e.g. 5.00"></div>
          </div>
          <div class="field"><label for="invSupplier">Supplier</label><input id="invSupplier" type="text" required minlength="2" maxlength="200" placeholder="e.g. MedSupply"></div>
          <div class="cc-field-row">
            <div class="field"><label for="invExpiry">Expiry date (optional)</label><input id="invExpiry" type="date"></div>
            <div class="field"><label for="invLocation">Storage location</label><input id="invLocation" type="text" minlength="2" maxlength="200" value="Main Pharmacy"></div>
          </div>
          <div class="field"><label for="invNotes">Notes (optional)</label><textarea id="invNotes" maxlength="500" rows="2" placeholder="Anything the team should know"></textarea></div>
        </div>
        <div class="cc-modal-foot">
          <button class="btn btn-ghost" type="button" data-modal-close>Cancel</button>
          <button class="btn btn-primary" type="submit" id="invAddSubmit">Add item</button>
        </div>
      </form>`);
    const form = overlay.querySelector('#inventoryAddForm');
    const submit = overlay.querySelector('#invAddSubmit');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if(!form.reportValidity()){
        ccShowFormError(form, 'Please fill in the highlighted fields, then try again.', form.querySelector(':invalid'));
        return;
      }
      const payload = {
        name: overlay.querySelector('#invName').value.trim(),
        category: overlay.querySelector('#invCategory').value.trim(),
        sku: overlay.querySelector('#invSku').value.trim(),
        quantity: Number(overlay.querySelector('#invQuantity').value),
        unit_cost: Number(overlay.querySelector('#invCost').value),
        selling_price: Number(overlay.querySelector('#invPrice').value),
        supplier: overlay.querySelector('#invSupplier').value.trim(),
        reorder_threshold: Number(overlay.querySelector('#invThreshold').value),
        expiry_date: overlay.querySelector('#invExpiry').value || null,
        location: overlay.querySelector('#invLocation').value.trim() || 'Main Pharmacy',
        notes: overlay.querySelector('#invNotes').value.trim() || null,
      };
      submit.disabled = true;
      submit.textContent = 'Adding…';
      try {
        await ccApi('/api/inventory/items', { method: 'POST', body: JSON.stringify(payload) });
        ccModalClose();
        ccToast('Stock item added. The forecast will update now.');
        load();
      } catch(err){
        ccShowFormError(form, err.message || 'Could not add this item. Please try again.');
        submit.disabled = false;
        submit.textContent = 'Add item';
      }
    });
  }

  /* ---------- record a stock movement ---------- */
  function openAdjust(row){
    const overlay = ccModalOpen(`
      <div class="cc-modal-head">
        <div><h3>Update stock for ${ccEscape(row.name)}</h3><p>${row.quantity} unit(s) in stock now. The change is saved with your name.</p></div>
        <button class="cc-modal-close" data-modal-close aria-label="Close">✕</button>
      </div>
      <form id="inventoryAdjustForm" novalidate>
        <div class="cc-modal-body">
          <p class="form-error" data-form-error role="alert" hidden></p>
          <div class="field">
            <label for="invAdjustType">What happened?</label>
            <select id="invAdjustType">
              <option value="usage">Stock was used</option>
              <option value="addition">New stock arrived</option>
              <option value="return">Stock was returned to the shelf</option>
              <option value="discard">Stock was damaged or expired</option>
            </select>
          </div>
          <div class="field">
            <label for="invAdjustQty">How many units?</label>
            <input id="invAdjustQty" type="number" inputmode="numeric" min="1" step="1" required placeholder="e.g. 5">
          </div>
          <div class="field">
            <label for="invAdjustReason">Reason</label>
            <input id="invAdjustReason" type="text" required minlength="2" maxlength="500" placeholder="e.g. Used in the ward today">
            <p class="field-hint">This note appears in the stock history.</p>
          </div>
        </div>
        <div class="cc-modal-foot">
          <button class="btn btn-ghost" type="button" data-modal-close>Cancel</button>
          <button class="btn btn-primary" type="submit" id="invAdjustSubmit">Save change</button>
        </div>
      </form>`);
    const form = overlay.querySelector('#inventoryAdjustForm');
    const submit = overlay.querySelector('#invAdjustSubmit');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if(!form.reportValidity()){
        ccShowFormError(form, 'Please fill in the highlighted fields, then try again.', form.querySelector(':invalid'));
        return;
      }
      const type = overlay.querySelector('#invAdjustType').value;
      const quantity = Number(overlay.querySelector('#invAdjustQty').value);
      const signed = type === 'usage' || type === 'discard' ? -Math.abs(quantity) : Math.abs(quantity);
      submit.disabled = true;
      submit.textContent = 'Saving…';
      try {
        await ccApi('/api/inventory/adjustments', {
          method: 'POST',
          body: JSON.stringify({
            item_id: row.item_id,
            adjustment_type: type,
            quantity_change: signed,
            reason: overlay.querySelector('#invAdjustReason').value.trim(),
          }),
        });
        ccModalClose();
        ccToast('Stock updated. The forecast will refresh now.');
        load();
      } catch(err){
        ccShowFormError(form, err.message || 'Could not save this stock change. Please try again.');
        submit.disabled = false;
        submit.textContent = 'Save change';
      }
    });
  }

  refreshBtn?.addEventListener('click', load);
  addBtn?.addEventListener('click', openAdd);

  /* Load when the tab is first opened; keep it lazy so other panels stay fast. */
  link?.addEventListener('click', () => { if(!loaded) load(); });
  const aiLink = document.querySelector('.side-nav a[data-panel="ai-console"]');
  aiLink?.addEventListener('click', () => { if(!loaded) load(); });

  load();
})();
