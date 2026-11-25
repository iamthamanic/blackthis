(() => {
  'use strict';

  /* --- Zentraler Anwendungszustand --- */
  const state = {
    selectedFile: null,
    inspectedMeta: null,
    session: null,
    filter: 'review',
    localRenderLimit: 150,
    renderedRowCount: 0,
    serverCells: [],
    serverContexts: {},
    serverTotal: 0,
    serverHasMore: false,
    serverLoading: false,
    tableViewMode: 'redacted',
    tableHeaders: [],
    tableScannedCols: new Set(),
    tableRows: [],
    tableTotal: 0,
    tableHasMore: false,
    tableRenderedCount: 0,
    tableLoading: false,
    pdfPreviewMode: 'redacted',
    pendingSelection: null,
    popoverOpenScrollY: 0,
    activeReviewIndex: -1,
    reviewDockDismissed: false,
    searchTimer: null,
    replacementTimer: null,
    toastTimer: null,
    scanElapsedTimer: null,
  };

  const DEFAULT_CHECKED_IDS = [
    'cat_names', 'cat_addresses', 'cat_house_numbers', 'cat_postal_codes',
    'cat_locations', 'cat_bank', 'cat_job_titles', 'cat_amounts',
    'cat_phones', 'cat_emails', 'cat_websites', 'cat_ip_addresses',
    'cat_birthdates', 'cat_license_plates', 'cat_tax_ids', 'redactUncertain', 'useNer',
  ];

  const PROFILE_CAT_IDS = DEFAULT_CHECKED_IDS.slice(0, 15);
  const $ = id => document.getElementById(id);

  /* --- Hilfsfunktionen --- */
  function escapeHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function showToast(msg) {
    const el = $('toast');
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'block';
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => { el.style.display = 'none'; }, 2800);
  }

  function showError(msg) {
    const box = $('errorBox');
    if (!box) return;
    if (!msg) { box.style.display = 'none'; return; }
    box.textContent = msg;
    box.style.display = 'block';
  }

  function formatElapsed(ms) {
    const totalTenths = Math.floor(ms / 100);
    const tenths = totalTenths % 10;
    const totalSec = Math.floor(totalTenths / 10);
    const mins = String(Math.floor(totalSec / 60)).padStart(2, '0');
    const secs = String(totalSec % 60).padStart(2, '0');
    return `${mins}:${secs}.${tenths}`;
  }

  function isPdfSession() {
    return Boolean(state.session && state.session.file_type === 'pdf');
  }

  function formatPdfImageWarning(pdfInfo) {
    if (!pdfInfo || !Array.isArray(pdfInfo.image_only_pages) || !pdfInfo.image_only_pages.length) return '';
    const pages = pdfInfo.image_only_pages;
    const listTxt = pages.length <= 10
      ? pages.join(', ')
      : `${pages.slice(0, 10).join(', ')} … (${pages.length} Seiten)`;
    if (pdfInfo.pages_with_text === 0) {
      return `⚠️ <strong>Reiner Bild-Scan erkannt:</strong> Keine der ${pdfInfo.page_count} Seiten enthält durchsuchbaren Text (z. B. gescanntes Dokument ohne OCR). Automatische Text-Schwärzung findet hier keine Treffer – bitte das PDF zuerst mit OCR durchsuchbar machen.`;
    }
    return `⚠️ <strong>Hinweis zu gescannten Seiten:</strong> Seite ${escapeHtml(listTxt)} enthält keinen durchsuchbaren Text (reiner Bild-Scan) und kann ohne OCR nicht automatisch geschwärzt werden. Alle übrigen ${pdfInfo.pages_with_text} Text-Seiten werden wie gewohnt geschwärzt.`;
  }

  function useServerPaging() {
    return Boolean(state.session && (state.filter === 'all' || state.session.server_paginated));
  }

  function getSelectedColumns() {
    if (state.inspectedMeta?.file_type === 'pdf') return ['Text'];
    return Array.from(document.querySelectorAll('#columnCheckboxes input:checked')).map(cb => cb.value);
  }

  /* --- Datei-Upload & PII-Spaltenwarnung --- */
  function updatePiiColumnWarning() {
    const box = $('piiColWarning');
    if (!box || !state.inspectedMeta) return;
    const warnings = state.inspectedMeta.pii_warning_columns || {};
    const selected = new Set(getSelectedColumns());
    const uncheckedPii = Object.entries(warnings).filter(([col]) => !selected.has(col));
    if (!uncheckedPii.length) {
      box.style.display = 'none';
      box.innerHTML = '';
      return;
    }
    const items = uncheckedPii
      .map(([col, reason]) => `<strong>${escapeHtml(col)}</strong> (${escapeHtml(reason)})`)
      .join(', ');
    box.innerHTML = `⚠️ <strong>Warnung – mögliche personenbezogene Daten abgewählt:</strong> ${items}. Diese Spalten bleiben im Export im Klartext, solange sie nicht angehakt sind.`;
    box.style.display = 'block';
  }

  async function handleFile(file) {
    showError('');
    state.selectedFile = file;
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await fetch('/api/inspect', { method: 'POST', body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Datei konnte nicht gelesen werden.');
      state.inspectedMeta = data;

      const info = $('fileInfo');
      const pdfWarnEl = $('pdfScanWarning');
      if (data.file_type === 'pdf' && data.pdf_info) {
        const pi = data.pdf_info;
        info.innerHTML = `<strong>Geladen (PDF):</strong> ${escapeHtml(data.filename)} · <strong>${pi.page_count}</strong> ${pi.page_count === 1 ? 'Seite' : 'Seiten'} · Text auf <strong>${pi.pages_with_text}/${pi.page_count}</strong> Seiten · Echte In-Place-Schwärzung (Original-Layout bleibt erhalten)`;
        info.style.display = 'block';

        const warnHtml = formatPdfImageWarning(pi);
        if (pdfWarnEl) {
          pdfWarnEl.innerHTML = warnHtml;
          pdfWarnEl.style.display = warnHtml ? 'block' : 'none';
        }
        $('columnCheckboxes').innerHTML = '';
        $('piiColWarning').style.display = 'none';
        $('inspectPreviewWrap').style.display = 'none';
        $('columnPickerWrap').style.display = 'none';
        $('scanBtn').disabled = false;
      } else {
        if (pdfWarnEl) { pdfWarnEl.style.display = 'none'; pdfWarnEl.innerHTML = ''; }
        const delimLabel = data.delimiter === '\t' ? 'Tabulator' : `„${data.delimiter}“`;
        info.innerHTML = `<strong>Geladen:</strong> ${escapeHtml(data.filename)} · <strong>${data.row_count}</strong> Zeilen · ${data.headers.length} Spalten · Trennzeichen ${delimLabel} · Encoding ${escapeHtml(data.encoding)}`;
        info.style.display = 'block';

        const colBox = $('columnCheckboxes');
        colBox.innerHTML = '';
        const recSet = new Set(data.recommended_columns);
        const frag = document.createDocumentFragment();
        for (const h of data.headers) {
          const lbl = document.createElement('label');
          lbl.className = 'check-chip';
          const cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.value = h;
          cb.checked = recSet.has(h);
          cb.addEventListener('change', updatePiiColumnWarning);
          lbl.appendChild(cb);
          lbl.appendChild(document.createTextNode(' ' + h));
          frag.appendChild(lbl);
        }
        colBox.appendChild(frag);
        updatePiiColumnWarning();
        renderInspectPreview(data.headers, data.preview_rows || [], recSet);
        $('columnPickerWrap').style.display = 'block';
        $('scanBtn').disabled = false;
      }
    } catch (err) {
      showError(err.message);
      $('scanBtn').disabled = true;
    }
  }

  function renderInspectPreview(headers, previewRows, recSet) {
    const wrap = $('inspectPreviewWrap');
    const thead = $('inspectPreviewHead');
    const tbody = $('inspectPreviewBody');
    if (!wrap || !thead || !tbody || !headers.length || !previewRows.length) {
      if (wrap) wrap.style.display = 'none';
      return;
    }
    thead.innerHTML = '<tr><th>Zeile</th>' + headers.map(h => {
      const scanned = recSet.has(h);
      return `<th class="${scanned ? 'th-scanned' : ''}">${scanned ? '🔒 ' : ''}${escapeHtml(h)}</th>`;
    }).join('') + '</tr>';

    tbody.innerHTML = previewRows.map((row, idx) => {
      const tds = headers.map(h => {
        const val = String(row[h] ?? '');
        const clipped = val.length > 140 ? val.slice(0, 140) + '…' : val;
        return `<td class="${recSet.has(h) ? 'td-wide' : 'td-narrow'}">${escapeHtml(clipped)}</td>`;
      }).join('');
      return `<tr><td class="td-rownum">${idx + 2}</td>${tds}</tr>`;
    }).join('');
    wrap.style.display = 'block';
  }

  /* --- Einstellungen & Schnellprofile --- */
  function toggleAdvanced() {
    const el = $('advancedWrap');
    el.style.display = el.style.display === 'none' ? 'grid' : 'none';
  }

  const TYPED_DISPLAY_LABEL = 'Kategorie ([NAME], [IBAN])';

  function updatePresetButtonStates(val, typed) {
    let activeKey = '';
    if (typed) activeKey = 'typed';
    else if (val === '██████') activeKey = 'bar';
    else if (val === '[GESCHWÄRZT]') activeKey = 'geschwaerzt';
    else if (val === '[SECRET]') activeKey = 'secret';

    document.querySelectorAll('.preset-btn[data-preset]').forEach(btn => {
      btn.classList.toggle('preset-active', btn.dataset.preset === activeKey);
    });
    const replEl = $('replacement');
    if (replEl) replEl.classList.toggle('is-typed-mode', Boolean(typed));
  }

  function syncReplacementInputs(val, typed = false) {
    const shown = typed ? TYPED_DISPLAY_LABEL : val;
    const r1 = $('replacement');
    if (r1 && r1.value !== shown) r1.value = shown;
    updatePresetButtonStates(val, typed);
  }

  function getActualReplacementValue() {
    const typed = Boolean($('useTyped')?.checked);
    if (typed) return '██████';
    const raw = $('replacement')?.value || '██████';
    return raw === TYPED_DISPLAY_LABEL ? '██████' : raw;
  }

  function refreshAllVisibleRedactedPreviews() {
    if (!state.session) return;
    const cellMap = new Map();
    for (const c of state.session.cells || []) cellMap.set(`${c.row_index}:${c.column}`, c);
    for (const c of state.serverCells || []) cellMap.set(`${c.row_index}:${c.column}`, c);
    document.querySelectorAll('#reviewBody tr[data-row-index]').forEach(tr => {
      const cell = cellMap.get(`${tr.dataset.rowIndex}:${tr.dataset.column}`);
      if (!cell) return;
      const tdRed = tr.querySelector('td.col-red');
      if (tdRed) tdRed.innerHTML = buildHighlightedRedacted(cell);
    });
    if (state.filter === 'table') {
      renderFullTable(false);
    } else if (state.filter === 'pdf-preview') {
      renderPdfPreview();
    } else {
      numberReviewBadges();
    }
  }

  async function applyLiveReplacement(val, typed, silent = false) {
    const cleanVal = val || '██████';
    if (!state.session) return;
    try {
      const res = await fetch(`/api/session/${state.session.session_id}/replacement`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ replacement: cleanVal, use_typed_placeholders: Boolean(typed) }),
      });
      if (!res.ok) return;
      const data = await res.json().catch(() => ({}));
      if (typeof data.revision === 'number') state.session.revision = data.revision;
      else state.session.revision = (state.session.revision || 1) + 1;
      refreshAllVisibleRedactedPreviews();
      if (!silent) {
        showToast(typed
          ? 'Schwärzungs-Text live umgestellt auf Kategorien ([NAME], [IBAN] …)'
          : `Schwärzungs-Text live geändert auf: ${cleanVal}`);
      }
    } catch { /* offline-safe */ }
  }

  function onReplacementInput(val) {
    $('useTyped').checked = false;
    syncReplacementInputs(val, false);
    if (!state.session) return;
    clearTimeout(state.replacementTimer);
    state.replacementTimer = setTimeout(() => {
      applyLiveReplacement(val || '██████', false, true);
      showToast(`Schwärzungs-Text live aktualisiert: ${val || '██████'}`);
    }, 220);
  }

  function setReplacement(val, typed) {
    $('useTyped').checked = typed;
    syncReplacementInputs(val, typed);
    if (state.session) {
      applyLiveReplacement(val, typed, false);
    } else {
      showToast(typed
        ? 'Kategorie-Platzhalter aktiviert: z. B. [NAME], [IBAN], [ADRESSE]'
        : `Schwärzungs-Text gesetzt auf: ${val}`);
    }
  }

  function applyProfile(mode) {
    for (const id of PROFILE_CAT_IDS) {
      const el = $(id);
      if (el) el.checked = true;
    }
    const compEl = $('cat_companies');
    const uncEl = $('redactUncertain');
    if (uncEl) uncEl.checked = true;
    if (mode === 'max') {
      if (compEl) compEl.checked = true;
      showToast('Profil „Maximal schwärzen“ aktiviert (inkl. Firmennamen & Sicherheitsmodus).');
    } else {
      if (compEl) compEl.checked = false;
      showToast('Profil „Art.-15-Auskunft“ aktiviert (alle Dritte-PII aktiv, Firmennamen optional).');
    }
  }

  /* --- Live-Scan & Fortschrittspanel --- */
  function updateWorkPanelUI(prog, startedAtPerf) {
    const panel = $('scanWorkPanel');
    const pct = Math.max(2, Math.min(100, Number(prog.percent || 0)));
    const isDone = prog.status === 'done';

    panel.classList.toggle('is-done', isDone);
    $('workPhaseLabel').textContent = isDone ? '✓ APPROVED – Scan abgeschlossen!' : (prog.phase_label || 'Scanne lokal…');
    $('workDetail').textContent = prog.detail || '100 % lokal auf deinem Gerät';
    $('workPercent').textContent = `${pct} %`;
    $('workProgressFill').style.width = `${pct}%`;

    const elapsedMs = typeof prog.elapsed_ms === 'number' ? prog.elapsed_ms : Math.max(0, performance.now() - startedAtPerf);
    $('workElapsed').textContent = formatElapsed(elapsedMs);

    const phases = ['loading', 'ner', 'rules', 'finalizing'];
    const activeIdx = isDone ? phases.length : phases.indexOf(prog.phase || 'loading');
    phases.forEach((p, idx) => {
      const el = $(`step_${p}`);
      if (!el) return;
      el.classList.remove('active', 'done');
      const icon = el.querySelector('.phase-icon');
      if (idx < activeIdx) {
        el.classList.add('done');
        if (icon) icon.textContent = '✓';
      } else {
        if (idx === activeIdx) el.classList.add('active');
        if (icon) icon.textContent = String(idx + 1);
      }
    });

    if (prog.total > 0) {
      const isPdf = state.inspectedMeta?.file_type === 'pdf';
      const unit = prog.phase === 'ner' ? 'Texte' : (isPdf ? 'Seiten' : 'Zeilen');
      $('teleProcessed').textContent = `${Number(prog.processed || 0).toLocaleString('de-DE')} / ${Number(prog.total).toLocaleString('de-DE')} ${unit}`;
    } else {
      $('teleProcessed').textContent = 'Datei wird gelesen…';
    }
    $('teleRedactions').textContent = `${Number(prog.redactions_so_far || 0).toLocaleString('de-DE')} Schwärzungen`;

    const etaEl = $('teleEta');
    if (isDone) {
      etaEl.textContent = 'Fertig';
    } else if (pct >= 12 && elapsedMs >= 600) {
      const remSec = Math.max(1, Math.round(((elapsedMs / pct) * (100 - pct)) / 1000));
      etaEl.textContent = remSec < 60 ? `ca. ${remSec} Sek.` : `ca. ${Math.floor(remSec / 60)} Min. ${remSec % 60} Sek.`;
    } else {
      etaEl.textContent = 'wird berechnet…';
    }
  }

  async function startScan() {
    if (!state.selectedFile) return;
    showError('');
    const cols = getSelectedColumns();
    if (!cols.length) {
      showError('Bitte wähle mindestens eine Spalte zum Scannen aus.');
      return;
    }

    const btn = $('scanBtn');
    const panel = $('scanWorkPanel');
    btn.disabled = true;
    btn.textContent = 'BLACKTHIS arbeitet lokal…';

    const startedAtPerf = performance.now();
    panel.classList.remove('is-done');
    panel.classList.add('visible');
    updateWorkPanelUI({
      status: 'running',
      phase: 'loading',
      phase_label: 'Lese Datei ein & starte lokale Engine…',
      percent: 4,
      processed: 0,
      total: state.inspectedMeta ? state.inspectedMeta.row_count : 0,
      redactions_so_far: 0,
      detail: state.selectedFile.name,
    }, startedAtPerf);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    clearInterval(state.scanElapsedTimer);
    state.scanElapsedTimer = setInterval(() => {
      $('workElapsed').textContent = formatElapsed(performance.now() - startedAtPerf);
    }, 150);

    const options = {
      applicant_first_name: $('firstName').value.trim(),
      applicant_last_name: $('lastName').value.trim(),
      replacement: getActualReplacementValue(),
      use_typed_placeholders: $('useTyped').checked,
      redact_names: $('cat_names').checked,
      redact_addresses: $('cat_addresses').checked,
      redact_house_numbers: $('cat_house_numbers').checked,
      redact_postal_codes: $('cat_postal_codes').checked,
      redact_locations: $('cat_locations').checked,
      redact_bank: $('cat_bank').checked,
      redact_job_titles: $('cat_job_titles').checked,
      redact_amounts: $('cat_amounts').checked,
      redact_phones: $('cat_phones').checked,
      redact_emails: $('cat_emails').checked,
      redact_websites: $('cat_websites').checked,
      redact_ip_addresses: $('cat_ip_addresses').checked,
      redact_birthdates: $('cat_birthdates').checked,
      redact_license_plates: $('cat_license_plates').checked,
      redact_tax_ids: $('cat_tax_ids').checked,
      redact_companies: $('cat_companies').checked,
      redact_uncertain_by_default: $('redactUncertain').checked,
      use_ner: $('useNer').checked,
      extra_whitelist: $('extraWhitelist').value,
      extra_blacklist: $('extraBlacklist').value,
      columns: cols,
    };

    const fd = new FormData();
    fd.append('file', state.selectedFile);
    fd.append('options_json', JSON.stringify(options));

    try {
      const startRes = await fetch('/api/scan/start', { method: 'POST', body: fd });
      const startData = await startRes.json();
      if (!startRes.ok) throw new Error(startData.detail || 'Fehler beim Starten des Scans.');

      let finalData = null;
      while (true) {
        await new Promise(r => setTimeout(r, 150));
        const progRes = await fetch(`/api/scan/progress/${startData.job_id}`);
        const prog = await progRes.json();
        if (!progRes.ok) throw new Error(prog.detail || 'Fortschritt konnte nicht abgefragt werden.');
        if (prog.status === 'error') throw new Error(prog.error || 'Fehler beim Scannen.');
        updateWorkPanelUI(prog, startedAtPerf);
        if (prog.status === 'done' && prog.result) {
          finalData = prog.result;
          break;
        }
      }

      clearInterval(state.scanElapsedTimer);
      state.session = finalData;
      state.localRenderLimit = 150;
      state.renderedRowCount = 0;
      state.serverCells = [];
      state.serverContexts = {};
      state.serverTotal = 0;
      state.serverHasMore = false;
      state.tableRows = [];
      state.tableTotal = 0;
      state.tableHasMore = false;
      state.tableRenderedCount = 0;
      state.activeReviewIndex = -1;
      state.reviewDockDismissed = false;
      hideSelectionPopover();
      hideReviewDock(false);

      state.filter = finalData.summary.review_findings > 0 ? 'review' : 'findings';
      if (state.session.server_paginated) {
        state.serverCells = finalData.cells || [];
        state.serverContexts = finalData.row_contexts || {};
        state.serverTotal = state.filter === 'review' ? finalData.summary.review_findings : (finalData.findings_count ?? finalData.cells.length);
        state.serverHasMore = state.serverCells.length >= 100;
      }

      renderResults();
      $('resultsSection').style.display = 'block';
      await new Promise(r => setTimeout(r, 360));
      panel.classList.remove('visible');
      $('resultsSection').scrollIntoView({ behavior: 'smooth' });
    } catch (err) {
      clearInterval(state.scanElapsedTimer);
      panel.classList.remove('visible');
      showError(err.message);
    } finally {
      clearInterval(state.scanElapsedTimer);
      btn.disabled = false;
      btn.textContent = 'Erneut lokal scannen & prüfen';
    }
  }

  /* --- Statistik, Tabellen-Renderer (inkrementell + Event-Delegation) --- */
  function renderStats() {
    const s = state.session.summary;
    const isPdf = isPdfSession();
    const hasReviews = s.review_findings > 0;
    const totalLbl = isPdf ? 'PDF-Seiten gesamt' : 'CSV-Zeilen gesamt';
    const findingsRowLbl = isPdf ? 'Seiten mit Fundstellen' : 'Zeilen mit Fundstellen';
    $('statsGrid').innerHTML = `
      <div class="stat-box"><div class="val">${s.total_rows}</div><div class="lbl">${totalLbl}</div></div>
      <div class="stat-box"><div class="val">${s.rows_with_findings}</div><div class="lbl">${findingsRowLbl}</div></div>
      <div class="stat-box"><div class="val" style="color:#dc2626">${s.total_redactions}</div><div class="lbl">Aktive Schwärzungen</div></div>
      <div class="stat-box ${hasReviews ? 'stat-box-review' : ''}">
        <div class="val ${hasReviews ? 'stat-val-jump' : ''}" style="color:#d97706" ${hasReviews ? 'onclick="jumpToCurrentReview()" title="Klicken zum Springen zum aktuellen / 1. gelben Prüffall"' : ''}>${s.review_findings}</div>
        <div class="lbl">Gelbe Prüffälle</div>
        <div class="review-nav-inline">
          <button type="button" class="rnav-btn" onclick="stepReview(-1)" ${hasReviews ? '' : 'disabled'} title="Vorheriger gelber Prüffall">← Zurück</button>
          <button type="button" class="rnav-btn rnav-check" onclick="startReviewCheck()" ${hasReviews ? '' : 'disabled'} title="Zum 1. gelben Prüffall springen">Check</button>
          <button type="button" class="rnav-btn" onclick="stepReview(1)" ${hasReviews ? '' : 'disabled'} title="Nächster gelber Prüffall">Vor →</button>
        </div>
      </div>
      <div class="stat-box"><div class="val" style="color:#16a34a">${s.protected_applicant_hits}</div><div class="lbl">Antragsteller geschützt</div></div>
    `;

    const pdfResWarn = $('pdfResultWarning');
    if (pdfResWarn) {
      const warnHtml = isPdf ? formatPdfImageWarning(state.session.pdf_info) : '';
      pdfResWarn.innerHTML = warnHtml;
      pdfResWarn.style.display = warnHtml ? 'block' : 'none';
    }

    const pills = Object.entries(s.by_category || {})
      .map(([k, v]) => `<span class="cat-pill">${escapeHtml(k)}: <strong>${v}</strong></span>`)
      .join('');
    $('catPills').innerHTML = pills || '<span class="hint">Keine aktiven Schwärzungen.</span>';

    const resBox = $('residualCheckBox');
    const rc = state.session.residual_check;
    if (resBox && rc) {
      resBox.style.display = 'block';
      if (rc.count === 0) {
        resBox.className = 'residual-banner ok';
        resBox.innerHTML = '✓ <strong>2. Kontroll-Durchlauf (Rest-PII-Check):</strong> 0 verdächtige Reste in der geschwärzten Ausgabe gefunden.';
      } else {
        resBox.className = 'residual-banner warn';
        const sampleTxt = (rc.samples || []).slice(0, 5)
          .map(it => `${isPdf ? `Seite ${Math.max(1, it.row - 1)}` : `Zeile ${it.row}`} (${escapeHtml(it.category)}: „${escapeHtml(it.text)}“)`)
          .join(' · ');
        resBox.innerHTML = `⚠️ <strong>2. Kontroll-Durchlauf (Rest-PII-Check): ${rc.count} mögliche Rest-Treffer</strong> — bitte kurz prüfen: ${sampleTxt}`;
      }
    }

    const bulkLbl = $('bulkBarLabel');
    if (bulkLbl) bulkLbl.textContent = isPdf ? 'Schnell-Korrektur für gesamtes PDF:' : 'Schnell-Korrektur für ganze CSV:';

    const totalAllCells = state.session.total_scanned_cells ?? s.total_rows;
    const findingsCount = state.session.findings_count ?? state.session.cells.length;
    $('tabReview').textContent = `⚠️ Nur unsichere Fälle (${s.review_findings})`;
    $('tabFindings').textContent = isPdf ? `Seiten mit Treffern (${findingsCount})` : `Alle Zellen mit Treffern (${findingsCount})`;
    $('tabAll').textContent = isPdf ? `Alle Seiten (${totalAllCells})` : `Alle Zeilen (${totalAllCells})`;
    const colCount = (state.session.headers || []).length;
    if ($('tabTable')) {
      $('tabTable').style.display = isPdf ? 'none' : '';
      $('tabTable').textContent = `📊 Alle Spalten (${colCount} Spalten)`;
    }
    if ($('tabPdfPreview')) {
      $('tabPdfPreview').style.display = isPdf ? '' : 'none';
      const pageCnt = state.session.pdf_info?.page_count || s.total_rows;
      $('tabPdfPreview').textContent = `📄 PDF-Vorschau (${pageCnt} ${pageCnt === 1 ? 'Seite' : 'Seiten'})`;
    }
    $('tabReview').classList.toggle('active', state.filter === 'review');
    $('tabFindings').classList.toggle('active', state.filter === 'findings');
    $('tabAll').classList.toggle('active', state.filter === 'all');
    if ($('tabTable')) $('tabTable').classList.toggle('active', state.filter === 'table');
    if ($('tabPdfPreview')) $('tabPdfPreview').classList.toggle('active', state.filter === 'pdf-preview');
  }

  function buildHighlightedOriginal(cell) {
    const text = cell.original || '';
    const matches = [...(cell.matches || [])].sort((a, b) => a.start - b.start);
    let out = '';
    let cursor = 0;
    for (const m of matches) {
      if (m.start < cursor) continue;
      out += escapeHtml(text.slice(cursor, m.start));
      const seg = escapeHtml(text.slice(m.start, m.end));
      let cls = 'hl-high';
      let stateTxt = 'Sicher geschwärzt – Klick zum Freigeben';
      let displayTag = m.category_label;
      if (m.protected) {
        cls = 'hl-protected';
        stateTxt = 'Antragsteller geschützt – Klick zum trotzdem Schwärzen';
        displayTag = '👤 Antragsteller';
      } else if (!m.redacted) {
        cls = 'hl-released';
        stateTxt = 'Freigegeben – Klick zum wieder Schwärzen';
      } else if (m.category === 'MANUELL') {
        if (String(m.rule || '').toLowerCase().includes('maus')) {
          cls = 'hl-manual';
          stateTxt = 'Manuell nur hier geschwärzt – Klick zum Rückgängigmachen / Freigeben';
          displayTag = 'Manuell (hier)';
        } else {
          cls = 'hl-manual-global';
          stateTxt = 'Manuelle Regel überall – Klick zum Freigeben';
          displayTag = 'Manuelle Regel überall';
        }
      } else if (m.confidence === 'REVIEW') {
        cls = 'hl-review';
        stateTxt = 'Unsicher (im Zweifel geschwärzt) – Klick zum Freigeben';
      }
      const tip = `${stateTxt}\nKategorie: ${m.category_label}\nGrund: ${(m.reasons || []).join(' | ')}`;
      const isOpenReview = (!m.protected && m.redacted && m.confidence === 'REVIEW');
      const isReviewAttr = isOpenReview ? ' data-is-review="1"' : '';
      // Kein inline onclick -> Event-Delegation auf #reviewBody spart ~35 % DOM-String-Größe!
      out += `<mark class="hl ${cls}" data-match-id="${escapeHtml(m.id)}" data-redacted="${m.redacted ? '1' : '0'}"${isReviewAttr} title="${escapeHtml(tip)}">${seg}<span class="cat-tag">${escapeHtml(displayTag)}</span></mark>`;
      cursor = m.end;
    }
    out += escapeHtml(text.slice(cursor));
    return out;
  }

  function buildHighlightedRedacted(cell) {
    const text = cell.original || '';
    const matches = [...(cell.matches || [])].sort((a, b) => a.start - b.start);
    if (!matches.length) return escapeHtml(cell.redacted || '');

    const repl = getActualReplacementValue();
    const useTyped = Boolean($('useTyped')?.checked);
    let out = '';
    let cursor = 0;
    for (const m of matches) {
      if (m.start < cursor) continue;
      out += escapeHtml(text.slice(cursor, m.start));
      if (m.protected) {
        out += `<mark class="hl-applicant-out" title="Antragsteller (bleibt im Export als Klartext erhalten)">👤 ${escapeHtml(text.slice(m.start, m.end))}</mark>`;
      } else if (m.redacted) {
        const tokenTxt = useTyped ? `[${m.category}]` : repl;
        const inner = (tokenTxt === '[GESCHWÄRZT]' || tokenTxt === '[SECRET]' || useTyped)
          ? `<strong class="redacted-token">${escapeHtml(tokenTxt)}</strong>`
          : escapeHtml(tokenTxt);
        out += `<span class="redacted-span" data-match-id="${escapeHtml(m.id)}">${inner}</span>`;
      } else {
        out += escapeHtml(text.slice(m.start, m.end));
      }
      cursor = m.end;
    }
    out += escapeHtml(text.slice(cursor));
    return out;
  }

  function hasOpenReviewMatches(cell) {
    return (cell.matches || []).some(m => !m.protected && m.redacted && m.confidence === 'REVIEW');
  }

  function buildCellRowHtml(cell, ctx) {
    const hasReview = hasOpenReviewMatches(cell);
    const isPdf = isPdfSession();
    const metaHtml = isPdf
      ? `<strong>PDF-Seite ${cell.row_index + 1}</strong><div class="meta-kv">Original-Layout aktiv</div>`
      : (() => {
          const ctxHtml = Object.entries(ctx || {})
            .filter(([, v]) => v)
            .map(([k, v]) => `<div class="meta-kv"><strong>${escapeHtml(k)}:</strong> ${escapeHtml(v)}</div>`)
            .join('');
          return `<strong>CSV-Zeile ${cell.row_index + 2}</strong><div class="meta-kv">Spalte: ${escapeHtml(cell.column)}</div>${ctxHtml}`;
        })();

    return `<tr class="${hasReview ? 'is-review' : ''}" data-row-index="${cell.row_index}" data-column="${escapeHtml(cell.column)}">
      <td class="col-meta">${metaHtml}</td>
      <td class="col-orig" data-row-index="${cell.row_index}" data-column="${escapeHtml(cell.column)}">${buildHighlightedOriginal(cell)}</td>
      <td class="col-red">${buildHighlightedRedacted(cell)}</td>
    </tr>`;
  }

  function getLocalFilteredCells() {
    const q = ($('searchInput').value || '').trim().toLowerCase();
    const isPdf = isPdfSession();
    const out = [];
    for (const cell of state.session.cells) {
      if (state.filter === 'review' && !hasOpenReviewMatches(cell)) continue;
      if (q) {
        const numLabel = isPdf ? `seite ${cell.row_index + 1}` : String(cell.row_index + 2);
        const hay = `${cell.original} ${cell.redacted} ${cell.column} ${numLabel}`.toLowerCase();
        if (!hay.includes(q)) continue;
      }
      out.push(cell);
    }
    return out;
  }

  function renderTable(appendOnly = false) {
    const tbody = $('reviewBody');
    const loadMoreWrap = $('loadMoreWrap');
    const loadMoreBtn = $('loadMoreBtn');

    if (useServerPaging()) {
      if (!state.serverCells.length) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:#64748b; padding:24px;">Keine passenden Zeilen gefunden.</td></tr>';
        state.renderedRowCount = 0;
        loadMoreWrap.style.display = 'none';
        return;
      }
      const startAt = appendOnly ? state.renderedRowCount : 0;
      const slice = state.serverCells.slice(startAt);
      const html = slice.map(cell => {
        const ctx = state.serverContexts[String(cell.row_index)] || state.session.row_contexts[String(cell.row_index)] || {};
        return buildCellRowHtml(cell, ctx);
      }).join('');

      if (appendOnly && startAt > 0) {
        tbody.insertAdjacentHTML('beforeend', html);
      } else {
        tbody.innerHTML = html;
      }
      state.renderedRowCount = state.serverCells.length;

      if (state.serverHasMore) {
        loadMoreWrap.style.display = 'block';
        const totalTxt = state.serverTotal ? ` von ${state.serverTotal}` : '';
        loadMoreBtn.textContent = `Weitere Zeilen laden (${state.serverCells.length}${totalTxt} angezeigt)`;
      } else {
        loadMoreWrap.style.display = 'none';
      }
      numberReviewBadges();
      return;
    }

    const filtered = getLocalFilteredCells();
    if (!filtered.length) {
      tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:#64748b; padding:24px;">Keine passenden Einträge in diesem Filter.</td></tr>';
      state.renderedRowCount = 0;
      loadMoreWrap.style.display = 'none';
      return;
    }

    const visible = filtered.slice(0, state.localRenderLimit);
    const startAt = appendOnly ? state.renderedRowCount : 0;
    const slice = visible.slice(startAt);
    const html = slice.map(cell => buildCellRowHtml(cell, state.session.row_contexts[String(cell.row_index)] || {})).join('');

    if (appendOnly && startAt > 0) {
      tbody.insertAdjacentHTML('beforeend', html);
    } else {
      tbody.innerHTML = html;
    }
    state.renderedRowCount = visible.length;

    if (filtered.length > visible.length) {
      loadMoreWrap.style.display = 'block';
      loadMoreBtn.textContent = `Weitere Zeilen anzeigen (${visible.length} von ${filtered.length} angezeigt)`;
    } else {
      loadMoreWrap.style.display = 'none';
    }
    numberReviewBadges();
  }

  function syncTableVisibility() {
    const isFull = state.filter === 'table';
    const isPdfPrev = state.filter === 'pdf-preview';
    if ($('diffTableShell')) $('diffTableShell').style.display = (isFull || isPdfPrev) ? 'none' : 'block';
    if ($('fullTableShell')) $('fullTableShell').style.display = isFull ? 'block' : 'none';
    if ($('tableModeBar')) $('tableModeBar').style.display = isFull ? 'flex' : 'none';
    if ($('pdfPreviewShell')) $('pdfPreviewShell').style.display = isPdfPrev ? 'flex' : 'none';
    if ($('pdfPreviewBar')) $('pdfPreviewBar').style.display = isPdfPrev ? 'flex' : 'none';
    if ($('reviewLegend')) $('reviewLegend').style.display = (isPdfPrev || (isFull && state.tableViewMode === 'redacted')) ? 'none' : 'flex';
    if ($('tblModeRedacted')) $('tblModeRedacted').classList.toggle('active', state.tableViewMode === 'redacted');
    if ($('tblModeOriginal')) $('tblModeOriginal').classList.toggle('active', state.tableViewMode === 'original');
    if ($('pdfModeRedacted')) $('pdfModeRedacted').classList.toggle('active', state.pdfPreviewMode === 'redacted');
    if ($('pdfModeSplit')) $('pdfModeSplit').classList.toggle('active', state.pdfPreviewMode === 'split');
  }

  function renderPdfPreview() {
    syncTableVisibility();
    const shell = $('pdfPreviewShell');
    const loadMoreWrap = $('loadMoreWrap');
    if (loadMoreWrap) loadMoreWrap.style.display = 'none';
    if (!shell || !state.session || !state.session.pdf_info) return;

    const sid = state.session.session_id;
    const rev = state.session.revision || 1;
    const pageCount = state.session.pdf_info.page_count || 0;
    const scanSet = new Set(state.session.pdf_info.image_only_pages || []);
    const isSplit = state.pdfPreviewMode === 'split';

    const redactionCounts = new Map();
    for (const c of state.session.cells || []) {
      const activeCnt = (c.matches || []).filter(m => m.redacted && !m.protected).length;
      redactionCounts.set(c.row_index + 1, activeCnt);
    }

    const cards = [];
    for (let p = 1; p <= pageCount; p++) {
      const isScan = scanSet.has(p);
      const cnt = redactionCounts.get(p) || 0;
      const badgeHtml = isScan
        ? '<span class="pdf-page-badge is-scan">⚠️ Reiner Bild-Scan (kein durchsuchbarer Text)</span>'
        : cnt > 0
          ? `<span class="pdf-page-badge has-red">🔒 ${cnt} ${cnt === 1 ? 'Schwärzung' : 'Schwärzungen'}</span>`
          : '<span class="pdf-page-badge">Keine Schwärzungen</span>';
      const redUrl = `/api/session/${sid}/pdf-page/${p}?mode=redacted&v=${rev}`;
      const origUrl = `/api/session/${sid}/pdf-page/${p}?mode=original&v=1`;
      const panesHtml = isSplit
        ? `<div class="pdf-page-pane"><span class="pdf-page-pane-lbl">Original</span><img class="pdf-page-img" src="${origUrl}" alt="PDF Seite ${p} Original" loading="lazy"></div>
           <div class="pdf-page-pane"><span class="pdf-page-pane-lbl">Geschwärzt (Export-Vorschau)</span><img class="pdf-page-img" src="${redUrl}" alt="PDF Seite ${p} Geschwärzt" loading="lazy"></div>`
        : `<div class="pdf-page-pane"><img class="pdf-page-img" src="${redUrl}" alt="PDF Seite ${p} Geschwärzt" loading="lazy"></div>`;
      cards.push(`<div class="pdf-page-card">
        <div class="pdf-page-head"><span>Seite ${p} von ${pageCount}</span>${badgeHtml}</div>
        <div class="pdf-page-grid ${isSplit ? 'is-split' : ''}">${panesHtml}</div>
      </div>`);
    }
    shell.innerHTML = cards.join('');
  }

  function setPdfPreviewMode(mode) {
    state.pdfPreviewMode = mode === 'split' ? 'split' : 'redacted';
    renderPdfPreview();
  }

  function buildFullTableRowHtml(row) {
    const tds = state.tableHeaders.map(h => {
      const isScanned = state.tableScannedCols.has(h);
      const cell = row.cells?.[h];
      const cls = isScanned ? 'td-wide' : 'td-narrow';
      if (state.tableViewMode === 'original') {
        if (cell) {
          return `<td class="${cls} col-orig" data-row-index="${row.row_index}" data-column="${escapeHtml(h)}">${buildHighlightedOriginal(cell)}</td>`;
        }
        return `<td class="${cls} ${isScanned ? 'col-orig' : ''}" data-row-index="${row.row_index}" data-column="${escapeHtml(h)}">${escapeHtml(row.original[h] ?? '')}</td>`;
      }
      if (cell) {
        return `<td class="${cls}">${buildHighlightedRedacted(cell)}</td>`;
      }
      return `<td class="${cls}">${escapeHtml(row.redacted[h] ?? '')}</td>`;
    }).join('');
    return `<tr data-row-index="${row.row_index}"><td class="td-rownum">${row.row_index + 2}</td>${tds}</tr>`;
  }

  function renderFullTable(appendOnly = false) {
    syncTableVisibility();
    const thead = $('fullTableHead');
    const tbody = $('fullTableBody');
    const loadMoreWrap = $('loadMoreWrap');
    const loadMoreBtn = $('loadMoreBtn');

    if (!appendOnly) {
      thead.innerHTML = '<tr><th>Zeile</th>' + state.tableHeaders.map(h => {
        const scanned = state.tableScannedCols.has(h);
        return `<th class="${scanned ? 'th-scanned' : ''}" title="${scanned ? 'Gescannte Spalte' : 'Nicht gescannte Spalte (Original)'}">${scanned ? '🔒 ' : ''}${escapeHtml(h)}</th>`;
      }).join('') + '</tr>';
    }

    if (!state.tableRows.length) {
      tbody.innerHTML = `<tr><td colspan="${state.tableHeaders.length + 1}" style="text-align:center; color:#64748b; padding:24px;">Keine passenden Zeilen gefunden.</td></tr>`;
      state.tableRenderedCount = 0;
      loadMoreWrap.style.display = 'none';
      return;
    }

    const startAt = appendOnly ? state.tableRenderedCount : 0;
    const html = state.tableRows.slice(startAt).map(buildFullTableRowHtml).join('');
    if (appendOnly && startAt > 0) {
      tbody.insertAdjacentHTML('beforeend', html);
    } else {
      tbody.innerHTML = html;
    }
    state.tableRenderedCount = state.tableRows.length;

    if (state.tableHasMore) {
      loadMoreWrap.style.display = 'block';
      loadMoreBtn.textContent = `Weitere Zeilen laden (${state.tableRows.length} von ${state.tableTotal} angezeigt)`;
    } else {
      loadMoreWrap.style.display = 'none';
    }
  }

  async function fetchFullTableRows(reset = true) {
    if (!state.session || state.tableLoading) return;
    state.tableLoading = true;
    const q = ($('searchInput').value || '').trim();
    const offset = reset ? 0 : state.tableRows.length;
    if (reset) {
      syncTableVisibility();
      $('fullTableBody').innerHTML = '<tr><td colspan="99" style="text-align:center; color:#64748b; padding:24px;">Lade alle Spalten…</td></tr>';
      state.tableRenderedCount = 0;
    }
    try {
      const params = new URLSearchParams({ q, offset: String(offset), limit: '100' });
      const res = await fetch(`/api/session/${state.session.session_id}/table?${params.toString()}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Tabellenzeilen konnten nicht geladen werden.');
      state.tableHeaders = data.headers || state.session.headers || [];
      state.tableScannedCols = new Set(data.scanned_columns || state.session.summary?.scanned_columns || []);
      state.tableRows = reset ? (data.rows || []) : state.tableRows.concat(data.rows || []);
      state.tableTotal = data.total || 0;
      state.tableHasMore = Boolean(data.has_more);
      renderFullTable(!reset);
    } catch (err) {
      showToast('Fehler: ' + err.message);
    } finally {
      state.tableLoading = false;
    }
  }

  function setTableViewMode(mode) {
    state.tableViewMode = mode === 'original' ? 'original' : 'redacted';
    renderFullTable(false);
  }

  function renderResults() {
    renderStats();
    syncTableVisibility();
    if (state.filter === 'table') fetchFullTableRows(true);
    else if (state.filter === 'pdf-preview') renderPdfPreview();
    else renderTable(false);
  }

  async function fetchServerTabCells(reset = true) {
    if (!state.session || state.serverLoading) return;
    state.serverLoading = true;
    const q = ($('searchInput').value || '').trim();
    const offset = reset ? 0 : state.serverCells.length;
    if (reset) {
      $('reviewBody').innerHTML = '<tr><td colspan="3" style="text-align:center; color:#64748b; padding:24px;">Lade Zeilen…</td></tr>';
      state.renderedRowCount = 0;
    }
    try {
      const params = new URLSearchParams({ filter: state.filter, q, offset: String(offset), limit: '100' });
      const res = await fetch(`/api/session/${state.session.session_id}/cells?${params.toString()}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Zeilen konnten nicht geladen werden.');
      if (reset) {
        state.serverCells = data.cells || [];
        state.serverContexts = data.row_contexts || {};
      } else {
        state.serverCells = state.serverCells.concat(data.cells || []);
        Object.assign(state.serverContexts, data.row_contexts || {});
      }
      state.serverTotal = data.total || 0;
      state.serverHasMore = Boolean(data.has_more);
      renderTable(!reset);
    } catch (err) {
      showToast('Fehler: ' + err.message);
    } finally {
      state.serverLoading = false;
    }
  }

  function setFilter(f) {
    state.filter = f;
    state.localRenderLimit = 150;
    state.renderedRowCount = 0;
    hideSelectionPopover();
    renderStats();
    syncTableVisibility();
    if (f === 'table') fetchFullTableRows(true);
    else if (f === 'pdf-preview') renderPdfPreview();
    else if (useServerPaging()) fetchServerTabCells(true);
    else renderTable(false);
  }

  function onSearchInput() {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => {
      state.localRenderLimit = 150;
      state.renderedRowCount = 0;
      hideSelectionPopover();
      if (state.filter === 'table') fetchFullTableRows(true);
      else if (state.filter === 'pdf-preview') renderPdfPreview();
      else if (useServerPaging()) fetchServerTabCells(true);
      else renderTable(false);
    }, 180);
  }

  function loadMoreRows() {
    if (state.filter === 'table') {
      fetchFullTableRows(false);
    } else if (useServerPaging()) {
      fetchServerTabCells(false);
    } else {
      state.localRenderLimit += 150;
      renderTable(true);
    }
  }

  function upsertCellInState(updatedCell) {
    const idx = state.session.cells.findIndex(c => c.row_index === updatedCell.row_index && c.column === updatedCell.column);
    if (idx !== -1) state.session.cells[idx] = updatedCell;
    else if (!state.session.server_paginated) {
      state.session.cells.push(updatedCell);
      state.session.cells.sort((a, b) => (a.row_index - b.row_index) || a.column.localeCompare(b.column));
    }
    const sIdx = state.serverCells.findIndex(c => c.row_index === updatedCell.row_index && c.column === updatedCell.column);
    if (sIdx !== -1) state.serverCells[sIdx] = updatedCell;
    const tRow = state.tableRows.find(r => r.row_index === updatedCell.row_index);
    if (tRow) {
      tRow.redacted[updatedCell.column] = updatedCell.redacted;
      tRow.cells[updatedCell.column] = updatedCell;
    }
  }

  function patchSingleRowInDom(updatedCell) {
    const tr = document.querySelector(`#reviewBody tr[data-row-index="${updatedCell.row_index}"][data-column="${CSS.escape(updatedCell.column)}"]`);
    if (!tr) return false;
    const hasReview = hasOpenReviewMatches(updatedCell);
    // Im Tab „Nur unsichere Fälle“ sofort aus der Liste entfernen, sobald alle gelben Fälle der Zeile erledigt sind
    if (state.filter === 'review' && !hasReview) {
      tr.remove();
      state.renderedRowCount = Math.max(0, state.renderedRowCount - 1);
      state.serverCells = state.serverCells.filter(c => !(c.row_index === updatedCell.row_index && c.column === updatedCell.column));
      if (!document.querySelector('#reviewBody tr')) {
        $('reviewBody').innerHTML = '<tr><td colspan="3" style="text-align:center; color:#16a34a; padding:24px; font-weight:600;">✓ Alle gelben Prüffälle abgearbeitet!</td></tr>';
      }
      numberReviewBadges();
      return true;
    }
    tr.classList.toggle('is-review', hasReview);
    const tdOrig = tr.querySelector('td.col-orig');
    const tdRed = tr.querySelector('td.col-red');
    if (tdOrig) tdOrig.innerHTML = buildHighlightedOriginal(updatedCell);
    if (tdRed) tdRed.innerHTML = buildHighlightedRedacted(updatedCell);
    numberReviewBadges();
    return true;
  }

  async function toggleMatch(matchId, newRedacted) {
    if (!state.session) return;
    const sel = window.getSelection ? window.getSelection() : null;
    if (sel && !sel.isCollapsed && sel.toString().trim().length > 0) return;
    hideSelectionPopover();
    const dockWasVisible = $('reviewDock')?.classList.contains('visible');
    try {
      const res = await fetch(`/api/session/${state.session.session_id}/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ match_id: matchId, redacted: newRedacted }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Umschalten fehlgeschlagen.');
      state.session.summary = data.summary;
      if (typeof data.findings_count === 'number') state.session.findings_count = data.findings_count;
      if (typeof data.revision === 'number') state.session.revision = data.revision;
      upsertCellInState(data.cell);
      renderStats();
      if (state.filter === 'table') renderFullTable(false);
      else if (!patchSingleRowInDom(data.cell)) renderTable(false);

      if (dockWasVisible && !newRedacted) {
        let remaining = getLoadedReviewMarks();
        if (!remaining.length && useServerPaging() && state.serverHasMore) {
          await fetchServerTabCells(false);
          remaining = getLoadedReviewMarks();
        }
        if (remaining.length) {
          const nextIdx = Math.min(Math.max(0, state.activeReviewIndex), remaining.length - 1);
          focusReviewMarkAt(nextIdx);
          showToast('Freigegeben – weiter zum nächsten Prüffall.');
        } else {
          hideReviewDock();
          state.activeReviewIndex = -1;
          showToast('✓ Alle gelben Prüffälle abgearbeitet!');
        }
        return;
      }
      showToast(newRedacted ? 'Stelle geschwärzt' : 'Stelle freigegeben');
    } catch (err) {
      showToast('Fehler: ' + err.message);
    }
  }

  /* --- Maus-Markierung (nachträglich schwärzen / freigeben) --- */
  function hideSelectionPopover() {
    const pop = $('selectionPopover');
    if (pop) pop.classList.remove('visible');
    state.pendingSelection = null;
  }

  function isInsideCatTag(node, root) {
    let cur = node;
    while (cur && cur !== root) {
      if (cur.nodeType === Node.ELEMENT_NODE && cur.classList?.contains('cat-tag')) return true;
      cur = cur.parentNode;
    }
    return false;
  }

  function getOriginalOffsetsInCell(tdEl, range) {
    function resolvePoint(container, containerOffset) {
      let acc = 0;
      let found = -1;
      function countText(node) {
        if (node.nodeType === Node.ELEMENT_NODE && node.classList?.contains('cat-tag')) return;
        if (node.nodeType === Node.TEXT_NODE) { acc += node.nodeValue.length; return; }
        for (const child of node.childNodes) countText(child);
      }
      function walk(node) {
        if (found !== -1) return;
        if (node.nodeType === Node.ELEMENT_NODE && node.classList?.contains('cat-tag')) return;
        if (node === container) {
          if (node.nodeType === Node.TEXT_NODE) { found = acc + containerOffset; return; }
          for (let i = 0; i < containerOffset && i < node.childNodes.length; i++) countText(node.childNodes[i]);
          found = acc;
          return;
        }
        if (node.nodeType === Node.TEXT_NODE) { acc += node.nodeValue.length; return; }
        for (const child of node.childNodes) { walk(child); if (found !== -1) return; }
      }
      walk(tdEl);
      return found;
    }
    const startOffset = resolvePoint(range.startContainer, range.startOffset);
    const endOffset = resolvePoint(range.endContainer, range.endOffset);
    return (startOffset === -1 || endOffset === -1 || startOffset >= endOffset) ? null : { start: startOffset, end: endOffset };
  }

  function extractCleanRangeText(range) {
    const frag = range.cloneContents();
    frag.querySelectorAll('.cat-tag').forEach(el => el.remove());
    return (frag.textContent || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  }

  function getCleanCellTextLength(tdEl) {
    let len = 0;
    (function count(node) {
      if (node.nodeType === Node.ELEMENT_NODE && node.classList?.contains('cat-tag')) return;
      if (node.nodeType === Node.TEXT_NODE) { len += node.nodeValue.length; return; }
      for (const child of node.childNodes) count(child);
    })(tdEl);
    return len;
  }

  function handleOriginalSelection() {
    if (!state.session) return;
    const sel = window.getSelection ? window.getSelection() : null;
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) { hideSelectionPopover(); return; }

    const range = sel.getRangeAt(0);
    const startNode = range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer : range.startContainer.parentElement;
    const endNode = range.endContainer.nodeType === Node.ELEMENT_NODE ? range.endContainer : range.endContainer.parentElement;
    const startTd = startNode ? startNode.closest('td.col-orig') : null;
    let endTd = endNode ? endNode.closest('td.col-orig') : null;

    let effectiveRange = range;
    if (startTd && (!endTd || endTd !== startTd) && range.endOffset === 0) {
      effectiveRange = range.cloneRange();
      effectiveRange.setEnd(startTd, startTd.childNodes.length);
      endTd = startTd;
    }
    if (!startTd || !endTd || startTd !== endTd) { hideSelectionPopover(); return; }
    if (isInsideCatTag(effectiveRange.startContainer, startTd) && isInsideCatTag(effectiveRange.endContainer, startTd)) {
      hideSelectionPopover();
      return;
    }

    const offsets = getOriginalOffsetsInCell(startTd, effectiveRange);
    const domSlice = extractCleanRangeText(effectiveRange);
    const trimmed = domSlice.trim();
    if (!trimmed) { hideSelectionPopover(); return; }

    const lead = domSlice.length - domSlice.trimStart().length;
    const trail = domSlice.length - domSlice.trimEnd().length;
    state.pendingSelection = {
      rowIndex: Number(startTd.dataset.rowIndex),
      column: startTd.dataset.column,
      start: offsets ? (offsets.start + lead) : 0,
      end: offsets ? (offsets.end - trail) : Math.min(getCleanCellTextLength(startTd), trimmed.length),
      text: trimmed,
    };
    state.popoverOpenScrollY = window.scrollY;

    const rect = effectiveRange.getBoundingClientRect();
    const pop = $('selectionPopover');
    pop.classList.add('visible');
    const popRect = pop.getBoundingClientRect();
    let top = rect.top - popRect.height - 10;
    if (top < 10) top = rect.bottom + 10;
    const left = Math.max(10, Math.min(rect.left + (rect.width / 2) - (popRect.width / 2), window.innerWidth - popRect.width - 10));
    pop.style.top = `${Math.round(top)}px`;
    pop.style.left = `${Math.round(left)}px`;
  }

  async function applyPopoverLocal() {
    if (!state.session || !state.pendingSelection) return;
    const selData = { ...state.pendingSelection };
    hideSelectionPopover();
    window.getSelection?.().removeAllRanges();
    const shortTxt = selData.text.length > 35 ? selData.text.slice(0, 35) + '…' : selData.text;
    showToast(`Schwärze „${shortTxt}“…`);
    try {
      const res = await fetch(`/api/session/${state.session.session_id}/manual-redact`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          row_index: selData.rowIndex,
          column: selData.column,
          start: selData.start,
          end: selData.end,
          text: selData.text,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Manuelle Schwärzung fehlgeschlagen.');
      state.session.summary = data.summary;
      if (typeof data.findings_count === 'number') state.session.findings_count = data.findings_count;
      if (typeof data.revision === 'number') state.session.revision = data.revision;
      upsertCellInState(data.cell);
      renderStats();
      if (state.filter === 'table') renderFullTable(false);
      else if (!patchSingleRowInDom(data.cell)) renderTable(false);
      showToast(`„${shortTxt}“ nur an dieser Stelle geschwärzt (lila markiert).`);
    } catch (err) {
      showToast('Fehler: ' + err.message);
    }
  }

  async function runBulkWord(word, action) {
    if (!state.session) return;
    const clean = (word || '').trim();
    if (!clean) { showToast('Bitte zuerst ein Wort eingeben.'); return; }
    const shortTxt = clean.length > 35 ? clean.slice(0, 35) + '…' : clean;
    showToast(action === 'redact' ? `Schwärze „${shortTxt}“ überall…` : `Gebe „${shortTxt}“ überall frei…`);
    try {
      const res = await fetch(`/api/session/${state.session.session_id}/bulk-word`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ word: clean, action }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Aktion fehlgeschlagen.');
      const affected = typeof data.affected_count === 'number' ? data.affected_count : null;
      state.session = data;
      $('bulkWordInput').value = '';
      if (state.filter === 'table') {
        renderStats();
        await fetchFullTableRows(true);
      } else if (state.filter === 'pdf-preview') {
        renderStats();
        renderPdfPreview();
      } else if (useServerPaging()) {
        renderStats();
        await fetchServerTabCells(true);
      } else {
        renderResults();
      }
      const countInfo = affected !== null ? ` (${affected} Stellen)` : '';
      const termsCount = typeof data.terms_count === 'number' ? data.terms_count : 1;
      const labelTxt = termsCount > 1 ? `${termsCount} Begriffe` : `„${shortTxt}“`;
      showToast(action === 'redact'
        ? `${labelTxt} jetzt überall geschwärzt${countInfo}.`
        : `${labelTxt} jetzt überall freigegeben${countInfo}.`);
    } catch (err) {
      showToast('Fehler: ' + err.message);
    }
  }

  async function applyPopoverBulk(action) {
    if (!state.session || !state.pendingSelection) return;
    const word = state.pendingSelection.text;
    hideSelectionPopover();
    window.getSelection?.().removeAllRanges();
    await runBulkWord(word, action);
  }

  async function bulkWord(action) {
    await runBulkWord($('bulkWordInput').value.trim(), action);
  }

  /* --- Gelbe-Prüffälle-Navigator (Check / Vor / Zurück + Auto-Einblendung beim Scrollen) --- */
  function hideReviewDock(userDismissed = true) {
    if (userDismissed) state.reviewDockDismissed = true;
    $('reviewDock')?.classList.remove('visible');
    document.querySelectorAll('mark.hl.review-active, .redacted-span.review-active').forEach(el => {
      el.classList.remove('review-active', 'review-flash');
    });
  }

  function syncReviewDockOnScroll() {
    const dock = $('reviewDock');
    const lbl = $('reviewDockCount');
    if (!dock || !lbl || !state.session) return;
    const reviewTotal = Number(state.session.summary?.review_findings || 0);
    if (reviewTotal <= 0) {
      dock.classList.remove('visible');
      return;
    }
    if (state.reviewDockDismissed) return;
    const statsEl = $('statsGrid');
    if (!statsEl) return;
    const statsRect = statsEl.getBoundingClientRect();
    // Sobald man über die Statistik-Kacheln hinweg runter zur Prüfliste scrollt, Dock automatisch einblenden
    if (statsRect.bottom < 95) {
      const loadedCount = getLoadedReviewMarks().length;
      const total = Math.max(loadedCount, reviewTotal);
      lbl.textContent = state.activeReviewIndex >= 0
        ? `⚠️ Prüffall ${Math.min(state.activeReviewIndex + 1, total)} / ${total}`
        : `⚠️ ${total} Prüffälle`;
      dock.classList.add('visible');
    } else if (state.activeReviewIndex < 0) {
      dock.classList.remove('visible');
    }
  }

  function getLoadedReviewMarks() {
    return Array.from(document.querySelectorAll('#reviewBody mark.hl.hl-review[data-is-review="1"]'));
  }

  function numberReviewBadges() {
    // Schwebende Nummern (#1, #2, …) über jedem offenen gelben Prüffall links & dem zugehörigen Block rechts setzen
    document.querySelectorAll('#reviewBody [data-review-num]').forEach(el => {
      delete el.dataset.reviewNum;
    });
    const marks = getLoadedReviewMarks();
    marks.forEach((mark, idx) => {
      const label = `#${idx + 1}`;
      mark.dataset.reviewNum = label;
      const tr = mark.closest('tr');
      const matchId = mark.dataset.matchId;
      if (tr && matchId) {
        const paired = tr.querySelector(`.redacted-span[data-match-id="${CSS.escape(matchId)}"]`);
        if (paired) paired.dataset.reviewNum = label;
      }
    });
  }

  async function jumpToCurrentReview() {
    if (!(await ensureReviewTabReady())) return;
    numberReviewBadges();
    const marks = getLoadedReviewMarks();
    if (!marks.length) {
      showToast('Keine gelben Prüffälle in dieser Ansicht gefunden.');
      return;
    }
    const targetIdx = state.activeReviewIndex >= 0
      ? Math.min(state.activeReviewIndex, marks.length - 1)
      : 0;
    focusReviewMarkAt(targetIdx);
  }

  function updateReviewDockLabel(idx, loadedCount) {
    const dock = $('reviewDock');
    const lbl = $('reviewDockCount');
    if (!dock || !lbl || !state.session) return;
    state.reviewDockDismissed = false;
    const total = Math.max(loadedCount, Number(state.session.summary?.review_findings || 0));
    lbl.textContent = `⚠️ Prüffall ${idx + 1} / ${total}`;
    dock.classList.add('visible');
  }

  function focusReviewMarkAt(index) {
    const marks = getLoadedReviewMarks();
    if (!marks.length) return false;
    const safeIdx = Math.max(0, Math.min(index, marks.length - 1));
    state.activeReviewIndex = safeIdx;
    const target = marks[safeIdx];

    document.querySelectorAll('#reviewBody mark.hl.review-active, #reviewBody .redacted-span.review-active').forEach(el => {
      el.classList.remove('review-active', 'review-flash');
    });
    document.querySelectorAll('#reviewBody tr.review-focus-row').forEach(tr => tr.classList.remove('review-focus-row'));

    target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
    target.classList.add('review-active');
    void target.offsetWidth;
    target.classList.add('review-flash');

    const tr = target.closest('tr');
    if (tr) {
      void tr.offsetWidth;
      tr.classList.add('review-focus-row');
      // Zugehörigen schwarzen Schwärzungsblock in der rechten Spalte synchron hervorheben & aufleuchten lassen
      const matchId = target.dataset.matchId;
      if (matchId) {
        const paired = tr.querySelector(`.redacted-span[data-match-id="${CSS.escape(matchId)}"]`);
        if (paired) {
          paired.classList.add('review-active');
          void paired.offsetWidth;
          paired.classList.add('review-flash');
        }
      }
    }
    updateReviewDockLabel(safeIdx, marks.length);
    return true;
  }

  async function ensureReviewTabReady() {
    if (!state.session || !state.session.summary || state.session.summary.review_findings <= 0) {
      showToast('Keine gelben Prüffälle vorhanden.');
      return false;
    }
    const searchEl = $('searchInput');
    const hadSearch = Boolean(searchEl && searchEl.value.trim());
    if (hadSearch) searchEl.value = '';
    if (state.filter !== 'review' || hadSearch) {
      state.filter = 'review';
      state.localRenderLimit = 150;
      state.renderedRowCount = 0;
      hideSelectionPopover();
      renderStats();
      syncTableVisibility();
      if (useServerPaging()) await fetchServerTabCells(true);
      else renderTable(false);
    }
    return true;
  }

  async function startReviewCheck() {
    if (!(await ensureReviewTabReady())) return;
    state.activeReviewIndex = 0;
    if (!focusReviewMarkAt(0)) showToast('Keine gelben Prüffälle in dieser Ansicht gefunden.');
  }

  async function stepReview(delta) {
    if (!(await ensureReviewTabReady())) return;
    let marks = getLoadedReviewMarks();
    if (!marks.length) { showToast('Keine gelben Prüffälle gefunden.'); return; }
    if (state.activeReviewIndex < 0) {
      focusReviewMarkAt(delta >= 0 ? 0 : marks.length - 1);
      return;
    }

    let nextIdx = state.activeReviewIndex + delta;
    if (nextIdx >= marks.length) {
      if (useServerPaging() && state.serverHasMore) {
        await fetchServerTabCells(false);
        marks = getLoadedReviewMarks();
      } else if (!useServerPaging()) {
        const totalReviewCells = (state.session.cells || []).filter(hasOpenReviewMatches).length;
        if (state.localRenderLimit < totalReviewCells) {
          state.localRenderLimit += 150;
          renderTable(true);
          marks = getLoadedReviewMarks();
        }
      }
      if (nextIdx >= marks.length) {
        nextIdx = 0;
        showToast('Ende erreicht – wieder bei Prüffall 1.');
      }
    } else if (nextIdx < 0) {
      nextIdx = marks.length - 1;
      showToast(`Sprung zum letzten geladenen Prüffall (${marks.length}).`);
    }
    focusReviewMarkAt(nextIdx);
  }

  async function releaseCurrentReviewEverywhere() {
    const marks = getLoadedReviewMarks();
    if (!marks.length) return;
    const idx = Math.max(0, Math.min(state.activeReviewIndex < 0 ? 0 : state.activeReviewIndex, marks.length - 1));
    const target = marks[idx];
    const clone = target.cloneNode(true);
    clone.querySelectorAll('.cat-tag').forEach(el => el.remove());
    const word = (clone.textContent || '').trim();
    if (!word) return;
    await runBulkWord(word, 'release');
    const remaining = getLoadedReviewMarks();
    if (remaining.length) {
      focusReviewMarkAt(Math.min(idx, remaining.length - 1));
    } else {
      hideReviewDock();
      state.activeReviewIndex = -1;
    }
  }

  /* --- Export & Reset --- */
  function openExportModal() {
    if (!state.session) return;
    const isPdf = isPdfSession();
    const pdfCard = $('exportChoicePdf');
    if (pdfCard) pdfCard.classList.toggle('export-choice-primary', isPdf);
    if ($('exportPdfTitle')) {
      $('exportPdfTitle').textContent = isPdf ? 'Original-PDF geschwärzt (.pdf)' : 'Als PDF (.pdf)';
    }
    if ($('exportPdfDesc')) {
      $('exportPdfDesc').textContent = isPdf
        ? 'Original-Layout, Tabellen & Briefkopf bleiben 1:1 erhalten · geschwärzter Text & Links werden unwiderruflich gelöscht.'
        : 'Druckfertiges DIN-A4-Dokument mit echten schwarzen Schwärzungsbalken – ideal zum Versenden.';
    }
    if ($('exportCsvDesc')) {
      $('exportCsvDesc').textContent = isPdf
        ? 'Optionaler seitenweiser Text-Auszug als CSV (ohne Original-Layout).'
        : 'Tabellendatei (UTF-8 mit BOM) für Excel, Numbers & weitere Datenverarbeitung.';
    }
    if ($('exportXlsxDesc')) {
      $('exportXlsxDesc').textContent = isPdf
        ? 'Optionaler seitenweiser Text-Auszug als Excel-Arbeitsmappe (.xlsx).'
        : 'Sauber formatierte Excel-Arbeitsmappe mit fixierter Kopfzeile & optimalen Spaltenbreiten.';
    }
    $('exportModal').classList.add('visible');
  }

  function closeExportModal() {
    $('exportModal')?.classList.remove('visible');
  }

  function downloadExport(fmt) {
    if (!state.session) return;
    window.location.href = `/api/session/${state.session.session_id}/export/${fmt}`;
  }

  function confirmExport(fmt) {
    closeExportModal();
    const msgs = {
      pdf: 'Erstelle geschwärzte PDF-Datei…',
      csv: 'Lade geschwärzte CSV herunter…',
      xlsx: 'Erstelle geschwärzte Excel-Datei (.xlsx)…',
    };
    showToast(msgs[fmt] || 'Starte Download…');
    downloadExport(fmt);
  }

  function resetApp() {
    clearInterval(state.scanElapsedTimer);
    if (state.session?.session_id) {
      fetch(`/api/session/${state.session.session_id}`, { method: 'DELETE' }).catch(() => { });
    }
    Object.assign(state, {
      selectedFile: null,
      inspectedMeta: null,
      session: null,
      filter: 'review',
      localRenderLimit: 150,
      renderedRowCount: 0,
      serverCells: [],
      serverContexts: {},
      serverTotal: 0,
      serverHasMore: false,
      serverLoading: false,
      pendingSelection: null,
      activeReviewIndex: -1,
      reviewDockDismissed: false,
    });
    hideSelectionPopover();
    hideReviewDock(false);
    closeExportModal();
    showError('');

    $('fileInput').value = '';
    $('fileInfo').style.display = 'none';
    $('fileInfo').innerHTML = '';
    if ($('pdfScanWarning')) { $('pdfScanWarning').style.display = 'none'; $('pdfScanWarning').innerHTML = ''; }
    if ($('pdfResultWarning')) { $('pdfResultWarning').style.display = 'none'; $('pdfResultWarning').innerHTML = ''; }
    if ($('pdfPreviewShell')) { $('pdfPreviewShell').style.display = 'none'; $('pdfPreviewShell').innerHTML = ''; }
    if ($('pdfPreviewBar')) $('pdfPreviewBar').style.display = 'none';
    if ($('tabPdfPreview')) $('tabPdfPreview').style.display = 'none';
    state.pdfPreviewMode = 'redacted';
    $('columnPickerWrap').style.display = 'none';
    $('columnCheckboxes').innerHTML = '';
    const warnEl = $('piiColWarning');
    if (warnEl) { warnEl.style.display = 'none'; warnEl.innerHTML = ''; }

    $('firstName').value = '';
    $('lastName').value = '';
    $('useTyped').checked = false;
    syncReplacementInputs('██████', false);
    for (const id of DEFAULT_CHECKED_IDS) {
      const el = $(id);
      if (el) el.checked = true;
    }
    if ($('cat_companies')) $('cat_companies').checked = false;

    $('extraWhitelist').value = '';
    $('extraBlacklist').value = '';
    $('bulkWordInput').value = '';
    $('searchInput').value = '';
    $('reviewBody').innerHTML = '';
    if ($('fullTableHead')) $('fullTableHead').innerHTML = '';
    if ($('fullTableBody')) $('fullTableBody').innerHTML = '';
    if ($('inspectPreviewWrap')) $('inspectPreviewWrap').style.display = 'none';
    state.tableRows = [];
    state.tableHeaders = [];
    state.tableTotal = 0;
    state.tableHasMore = false;
    state.tableRenderedCount = 0;
    $('loadMoreWrap').style.display = 'none';
    $('scanWorkPanel').classList.remove('visible', 'is-done');
    $('resultsSection').style.display = 'none';

    const scanBtn = $('scanBtn');
    scanBtn.disabled = true;
    scanBtn.textContent = 'Jetzt lokal scannen & prüfen';
    window.scrollTo({ top: 0, behavior: 'smooth' });
    showToast('Alle Eingaben und Ergebnisse zurückgesetzt.');
  }

  /* --- Initialisierung & globale Event-Listener --- */
  function init() {
    updatePresetButtonStates('██████', false);
    const dropzone = $('dropzone');
    const fileInput = $('fileInput');
    dropzone.addEventListener('click', () => fileInput.click());
    dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', e => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files.length) handleFile(fileInput.files[0]);
    });

    // Event-Delegation auf #reviewBody und #fullTableBody für 1-Klick-Toggle & Maus-Markierung
    for (const tbodyEl of [$('reviewBody'), $('fullTableBody')]) {
      if (!tbodyEl) continue;
      tbodyEl.addEventListener('click', ev => {
        const mark = ev.target.closest('mark.hl[data-match-id]');
        if (!mark) return;
        ev.stopPropagation();
        toggleMatch(mark.dataset.matchId, mark.dataset.redacted !== '1');
      });
      tbodyEl.addEventListener('mouseup', () => setTimeout(handleOriginalSelection, 10));
      tbodyEl.addEventListener('keyup', () => setTimeout(handleOriginalSelection, 10));
    }

    document.addEventListener('mousedown', ev => {
      const pop = $('selectionPopover');
      if (pop && !pop.contains(ev.target) && !ev.target.closest?.('td.col-orig')) {
        hideSelectionPopover();
      }
    });
    document.addEventListener('keydown', ev => {
      if (ev.key === 'Escape') {
        hideSelectionPopover();
        closeExportModal();
      }
    });
    window.addEventListener('scroll', () => {
      if (state.pendingSelection && Math.abs(window.scrollY - state.popoverOpenScrollY) > 24) {
        hideSelectionPopover();
      }
      syncReviewDockOnScroll();
    }, { passive: true });
  }

  /* --- Gezielte öffentliche Handler für HTML-Buttons --- */
  Object.assign(window, {
    setReplacement,
    onReplacementInput,
    applyProfile,
    toggleAdvanced,
    startScan,
    resetApp,
    openExportModal,
    closeExportModal,
    confirmExport,
    downloadExport,
    bulkWord,
    setFilter,
    setTableViewMode,
    setPdfPreviewMode,
    onSearchInput,
    loadMoreRows,
    applyPopoverLocal,
    applyPopoverBulk,
    startReviewCheck,
    jumpToCurrentReview,
    stepReview,
    releaseCurrentReviewEverywhere,
    hideReviewDock,
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
