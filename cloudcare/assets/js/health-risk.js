/* CloudCare — patient "Health risk" tab.
   Sends the patient's own readings to the bundled Patient Health Risk model
   and renders an explainable, plain-language screening estimate. No result
   is invented: the panel shows a clear empty/loading/error state until the
   model answers. */
(function(){
  const form = document.getElementById('healthRiskForm');
  const result = document.getElementById('healthRiskResult');
  if(!form || !result) return;

  const submitBtn = document.getElementById('healthRiskSubmit');
  const resetBtn = document.getElementById('healthRiskReset');
  const badge = document.getElementById('healthRiskResultBadge');
  const resultBody = result.querySelector('.panel-body');
  const bmiPreview = document.getElementById('hrBmiPreview');
  const initialResult = resultBody ? resultBody.innerHTML : '';
  let profileLoaded = false;

  const DISEASE_LABELS = {
    diabetes_risk: 'Type 2 diabetes',
    heart_disease_risk: 'Heart disease',
    kidney_disease_risk: 'Chronic kidney disease',
    stroke_risk: 'Stroke',
  };

  const LEVEL_BADGE = {
    High: 'badge-coral',
    Moderate: 'badge-gold',
    Low: 'badge-mint',
  };

  function num(id){
    const el = document.getElementById(id);
    if(!el || el.value === '') return null;
    const value = Number(el.value);
    return Number.isFinite(value) ? value : null;
  }

  function checked(id){
    const el = document.getElementById(id);
    return el && el.checked ? 1 : 0;
  }

  function bmi(){
    const height = num('hrHeight');
    const weight = num('hrWeight');
    if(!height || !weight || height < 100 || weight < 20) return null;
    return Math.round((weight / Math.pow(height / 100, 2)) * 10) / 10;
  }

  function updateBmi(){
    const value = bmi();
    if(!bmiPreview) return;
    bmiPreview.textContent = value
      ? `Your body mass index (BMI) is ${value}. This is calculated from your height and weight.`
      : 'Your body mass index (BMI) will appear here as you type.';
  }

  ['hrHeight', 'hrWeight'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', updateBmi);
  });

  function payload(){
    const body = {
      age: num('hrAge'),
      sex: num('hrSex'),
      bmi: bmi(),
      systolic_bp: num('hrSystolic'),
      diastolic_bp: num('hrDiastolic'),
      total_cholesterol: num('hrTotalChol'),
      hdl_cholesterol: num('hrHdl'),
      ldl_cholesterol: num('hrLdl'),
      fasting_glucose: num('hrFasting'),
      waist_circumference: num('hrWaist'),
      smoker: Number(document.getElementById('hrSmoker')?.value || 0),
      alcohol_use: Number(document.getElementById('hrAlcohol')?.value || 0),
      physical_activity_score: Number(document.getElementById('hrActivity')?.value || 2),
      diet_quality: Number(document.getElementById('hrDiet')?.value || 2),
      stress_level: Number(document.getElementById('hrStress')?.value || 1),
      family_history_diabetes: checked('hrFamDiabetes'),
      family_history_heart: checked('hrFamHeart'),
      family_history_kidney: checked('hrFamKidney'),
      family_history_stroke: checked('hrFamStroke'),
      on_antihypertensives: checked('hrMedBp'),
      on_statins: checked('hrMedStatin'),
      on_diabetes_meds: checked('hrMedDiabetes'),
    };

    const optional = {
      triglycerides: num('hrTriglycerides'),
      hba1c: num('hrHba1c'),
      creatinine: num('hrCreatinine'),
      egfr: num('hrEgfr'),
      resting_heart_rate: num('hrRestingHr'),
      spo2: num('hrSpo2'),
      sleep_hours: num('hrSleep'),
    };
    Object.keys(optional).forEach(key => {
      if(optional[key] !== null) body[key] = optional[key];
    });
    return body;
  }

  function renderLoading(){
    if(badge){
      badge.textContent = 'Checking…';
      badge.className = 'badge badge-slate';
    }
    resultBody.innerHTML = '<div class="cc-result-empty">Checking your readings against the risk model…</div>';
  }

  function renderError(message){
    if(badge){
      badge.textContent = 'Could not check';
      badge.className = 'badge badge-slate';
    }
    resultBody.innerHTML = `
      <div class="cc-load-error">
        <span>${ccEscape(message)}</span>
        <div><button class="btn btn-ghost btn-sm" type="button" id="healthRiskRetry">Try again</button></div>
      </div>`;
    document.getElementById('healthRiskRetry')?.addEventListener('click', () => {
      if(form.reportValidity()) submit();
    });
  }

  function factorList(factors){
    if(!Array.isArray(factors) || !factors.length) return '';
    return `<ul>${factors.slice(0, 2).map(factor =>
      `<li><strong>${ccEscape(factor.label || 'Reading')}</strong> ${ccEscape(factor.explanation || '')}</li>`
    ).join('')}</ul>`;
  }

  function recommendations(report){
    const items = Array.isArray(report.general_recommendations)
      ? report.general_recommendations
      : (report.action_plan && Array.isArray(report.action_plan.urgent_actions)
        ? report.action_plan.urgent_actions
        : []);
    if(!items.length) return '';
    return `
      <h4 class="cc-form-section-title" style="margin-top:20px;">What to discuss with your doctor</h4>
      <ul class="cc-rec-list">
        ${items.slice(0, 4).map(item => `
          <li class="cc-rec-item">
            <strong>${ccEscape(item.category || 'Next step')}</strong>
            ${ccEscape(item.message || '')}
            ${item.timeline ? `<div class="cc-rec-meta"><span class="badge badge-slate">${ccEscape(item.timeline)}</span></div>` : ''}
          </li>`).join('')}
      </ul>`;
  }

  function renderReport(report){
    const score = report.composite_score || {};
    const value = Number(score.cardiometabolic_burden_score || 0);
    const tier = score.burden_tier || 'Screening result';
    const risks = report.risks || {};

    if(badge){
      badge.textContent = tier;
      badge.className = 'badge ' + (tier === 'High Burden' ? 'badge-coral' : tier === 'Moderate Burden' ? 'badge-gold' : 'badge-mint');
    }

    const riskCards = Object.keys(DISEASE_LABELS).map(key => {
      const risk = risks[key];
      if(!risk) return '';
      return `
        <div class="cc-risk-card">
          <div class="cc-risk-card-top">
            <strong>${ccEscape(DISEASE_LABELS[key])}</strong>
            <span class="badge ${LEVEL_BADGE[risk.level] || 'badge-slate'}">${ccEscape(risk.level)}</span>
          </div>
          <span class="cc-risk-value">${Number(risk.risk_percent || 0).toFixed(1)}%</span>
          ${factorList(risk.top_factors)}
        </div>`;
    }).join('');

    const vascular = score.estimated_vascular_age
      ? `<span>Estimated heart and blood-vessel age: ${ccEscape(String(score.estimated_vascular_age))} years, compared with your age of ${ccEscape(String(score.chronological_age))}.</span>`
      : '';
    const saved = report.saved_to_profile
      ? '<span>This score is now saved in your health record.</span>'
      : '';

    const anomalies = Array.isArray(report.lab_anomalies) && report.lab_anomalies.length
      ? `<div class="cc-disclaimer"><strong>Readings to check with your doctor:</strong> ${ccEscape(report.lab_anomalies.map(a => a.biomarker || a.message || 'A reading').join(', '))}.</div>`
      : '';

    resultBody.innerHTML = `
      <div class="cc-score-wrap">
        <div class="cc-score-ring"><strong>${value.toFixed(0)}</strong><span>of 100</span></div>
        <div class="cc-score-copy">
          <strong>${ccEscape(tier)}</strong>
          <span>Highest estimated risk: ${ccEscape(report.highest_risk_condition || '—')} (${ccEscape(report.overall_flag || '')}).</span>
          ${vascular}
          ${saved}
        </div>
      </div>
      <div class="cc-risk-grid">${riskCards}</div>
      ${recommendations(report)}
      ${anomalies}
      <div class="cc-disclaimer">${ccEscape(report.disclaimer || 'This is a screening estimate, not a diagnosis. Talk with a doctor about what it means for you.')}</div>
      <div class="cc-form-actions" style="margin-top:16px;">
        <a class="btn btn-primary" href="#panel-book" data-panel-link="book">Book an appointment</a>
        <a class="btn btn-ghost" href="#healthRiskForm" id="healthRiskAgain">Check again</a>
      </div>`;

    document.getElementById('healthRiskAgain')?.addEventListener('click', (event) => {
      event.preventDefault();
      form.scrollIntoView({ behavior: 'smooth', block: 'start' });
      document.getElementById('hrAge')?.focus({ preventScroll: true });
    });

    const heading = document.getElementById('riskScoreHeading');
    if(heading) heading.textContent = `Last saved score: ${value.toFixed(0)}% — ${tier}`;
  }

  async function submit(){
    const invalid = form.querySelector(':invalid');
    const bmiValue = bmi();
    if(invalid || !bmiValue){
      ccShowFormError(form, 'Please fill in the highlighted fields. If a number is outside the range shown, change it and try again.', invalid || document.getElementById('hrHeight'));
      invalid?.focus?.();
      return;
    }
    if(bmiValue < 10 || bmiValue > 70){
      ccShowFormError(
        form,
        `These height and weight values give a body mass index of ${bmiValue}, which is outside the range this check supports. Please check the numbers.`,
        document.getElementById('hrHeight')
      );
      return;
    }

    ccClearFormError(form);
    submitBtn.disabled = true;
    submitBtn.textContent = 'Checking your risk…';
    renderLoading();
    try {
      const report = await ccApi('/api/health-risk/predict', {
        method: 'POST',
        body: JSON.stringify(payload()),
      });
      renderReport(report);
      if(window.ccToast) ccToast('Your health risk check is ready.');
    } catch(err){
      renderError(err && err.message ? err.message : 'The health risk check could not be completed. Please try again.');
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Check my risk';
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    submit();
  });

  form.addEventListener('input', () => ccClearFormError(form));

  resetBtn?.addEventListener('click', () => {
    form.reset();
    ccClearFormError(form);
    updateBmi();
    resultBody.innerHTML = initialResult;
    if(badge){
      badge.textContent = 'Not checked yet';
      badge.className = 'badge badge-slate';
    }
    const heading = document.getElementById('riskScoreHeading');
    if(heading) heading.textContent = 'Last saved score: —';
  });

  /* Prefill age from the patient profile the first time the tab is opened. */
  const riskLink = document.querySelector('.side-nav a[data-panel="risk"]');
  riskLink?.addEventListener('click', async () => {
    if(profileLoaded || num('hrAge')) return;
    profileLoaded = true;
    try {
      const profile = await ccApi('/api/patients/me');
      if(profile && profile.age && !num('hrAge')){
        document.getElementById('hrAge').value = profile.age;
      }
    } catch(e){ /* the form still works without a prefilled age */ }
  });
})();
