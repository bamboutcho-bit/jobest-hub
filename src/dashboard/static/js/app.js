// Utilities
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const fmt = t => t ? new Date(t).toLocaleString() : 'Never';
    function formatAppDate(isoStr) {
      if (!isoStr) return '';
      try {
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return String(isoStr);
        return d.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) + ', ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
      } catch (_) {
        return String(isoStr);
      }
    }
    const debounce = (fn, delay) => { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); }; };

    function animateValue(id, endVal, duration = 600) {
      const el = document.getElementById(id);
      if (!el) return;
      if (endVal === null || endVal === undefined || endVal === '—' || endVal === '-') {
        endVal = 0;
      }
      const isPercent = String(endVal).includes('%');
      let startVal = parseInt(el.textContent.replace(/[^0-9]/g, '')) || 0;
      let end = parseInt(String(endVal).replace(/[^0-9]/g, '')) || 0;
      if (startVal === end) {
        el.textContent = end + (isPercent ? '%' : '');
        return;
      }
      let startTimestamp = null;
      const step = (timestamp) => {
        if (!startTimestamp) startTimestamp = timestamp;
        const progress = Math.min((timestamp - startTimestamp) / duration, 1);
        const current = Math.floor(progress * (end - startVal) + startVal);
        el.textContent = current + (isPercent ? '%' : '');
        if (progress < 1) window.requestAnimationFrame(step);
        else el.textContent = end + (isPercent ? '%' : '');
      };
      window.requestAnimationFrame(step);
    }

    async function parseApiError(res) {
      try {
        const text = await res.text();
        try {
          const j = JSON.parse(text);
          if (j && typeof j === 'object') {
            if (typeof j.detail === 'string') return j.detail;
            if (Array.isArray(j.detail)) return j.detail.map(d => d.msg || JSON.stringify(d)).join(', ');
            if (j.message) return j.message;
          }
        } catch (_) {}
        return text || `HTTP ${res.status}`;
      } catch (_) {
        return `HTTP ${res.status}`;
      }
    }

    async function apiGet(url, opt) {
      const res = await fetch(url, opt);
      if (!res.ok) throw new Error(await parseApiError(res));
      return res.json();
    }
    async function apiSend(url, method, body) {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined
      });
      if (!res.ok) throw new Error(await parseApiError(res));
      return res.json();
    }

    function showToast(msg, type = 'normal') {
      const container = document.getElementById('toastContainer');
      const toast = document.createElement('div');
      toast.className = `toast ${type}`;
      toast.innerHTML = `<span>${type === 'success' ? '✓' : type === 'error' ? '⚠' : 'ℹ'}</span><span>${esc(msg)}</span>`;
      container.appendChild(toast);
      setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
      }, 3500);
    }

    // App Navigation State
    let currentAppMode = 'jobs';
    let currentFreelanceTab = 'kanban';
    let currentJobsTab = 'postings';
    let syncIntervalId = null;
    let syncDelayMs = 3000;

    // Cache Data
    let allFreelanceLeads = [];
    let currentActiveLead = null;
    let currentActiveJob = null;
    let allJobsList = [];

    const STAGES = ['discovered', 'evaluated', 'pitched', 'in_discussion', 'offer_received', 'deal_won', 'lost'];
    const KANBAN_STAGES = [
      { id: 'evaluated', label: '⚡ Evaluated', icon: '⚡' },
      { id: 'pitched', label: '✉️ Pitched', icon: '✉️' },
      { id: 'in_discussion', label: '💬 In Discussion', icon: '💬' },
      { id: 'offer_received', label: '🎯 Offer Received', icon: '🎯' },
      { id: 'deal_won', label: '🏆 Deal Won', icon: '🏆' }
    ];

    const JOB_STAGES = [
      'discovered', 'evaluated_low', 'evaluated_match', 'applied',
      'response_received', 'interview_requested', 'interview_scheduled',
      'rejected', 'offer_received', 'offer_negotiating', 'accepted', 'withdrawn'
    ];

    // Initialization
    window.addEventListener('DOMContentLoaded', () => {
      // Zero localStorage policy enforced: all state is in-memory or session-cookie backed

      // Resolve initial mode from URL hash, path, or server-provided default
      const serverDefault = document.body?.dataset?.defaultTab;
      let initialMode = 'jobs';
      const hash = (window.location.hash || '').toLowerCase();
      const path = (window.location.pathname || '').toLowerCase();

      if (hash === '#freelance' || path.startsWith('/freelance')) {
        initialMode = 'freelance';
      } else if (hash === '#jobs' || path.startsWith('/jobs')) {
        initialMode = 'jobs';
      } else if (serverDefault === 'freelance' || serverDefault === 'jobs') {
        initialMode = serverDefault;
      }

      switchAppMode(initialMode, false);

      // Populate stage options
      const jSelect = document.getElementById('modalJobStageSelect');
      if (jSelect) {
        JOB_STAGES.forEach(s => jSelect.insertAdjacentHTML('beforeend', `<option value="${s}">${s}</option>`));
      }

      startAutoSync();
      initAuth();
      checkStripePaymentReturn();
      initNotificationsWebSocket();
      loadNotifications();
      checkAutoOpenPlanModal();

      // Close notification dropdown when clicking outside
      document.addEventListener('click', (e) => {
        const wrapper = document.getElementById('notifBellWrapper');
        const dropdown = document.getElementById('notifDropdown');
        if (wrapper && dropdown && !wrapper.contains(e.target)) {
          dropdown.style.display = 'none';
        }
      });
    });

    window.addEventListener('hashchange', () => {
      checkAutoOpenPlanModal();
    });

    function checkAutoOpenPlanModal() {
      try {
        const params = new URLSearchParams(window.location.search);
        let plan = params.get('plan');
        const hash = window.location.hash || '';
        if (!plan && hash.includes('plan=')) {
          const m = hash.match(/plan=([a-zA-Z0-9_-]+)/);
          if (m) plan = m[1];
        }
        if (plan && plan !== 'free') {
          setTimeout(() => {
            if (typeof openPaymentModal === 'function') {
              openPaymentModal(plan);
            }
          }, 450);
        }
      } catch (_) {}
    }

    function switchAppMode(mode, updateHash = true) {
      if (mode !== 'freelance' && mode !== 'jobs') mode = 'jobs';
      currentAppMode = mode;
      if (updateHash && window.location.hash !== '#' + mode) {
        if (history.replaceState) {
          history.replaceState(null, '', '#' + mode);
        } else {
          window.location.hash = mode;
        }
      }
      const freelanceEl = document.getElementById('freelanceView');
      const jobsEl = document.getElementById('jobsView');
      const btnFreelance = document.getElementById('modeFreelance');
      const btnJobs = document.getElementById('modeJobs');

      if (freelanceEl) freelanceEl.classList.toggle('active', mode === 'freelance');
      if (jobsEl) jobsEl.classList.toggle('active', mode === 'jobs');
      if (btnFreelance) btnFreelance.classList.toggle('active', mode === 'freelance');
      if (btnJobs) {
        btnJobs.classList.toggle('active', mode === 'jobs');
        btnJobs.classList.toggle('jobs-mode', mode === 'jobs');
      }
      refreshCurrentView();
    }

    function changeSyncInterval(val) {
      syncDelayMs = parseInt(val, 10);
      clearInterval(syncIntervalId);
      if (syncDelayMs > 0) {
        startAutoSync();
        document.getElementById('liveStatusText').textContent = 'Live Sync';
      } else {
        document.getElementById('liveStatusText').textContent = 'Paused';
      }
    }

    function startAutoSync() {
      if (syncDelayMs <= 0) return;
      syncIntervalId = setInterval(refreshCurrentView, syncDelayMs);
    }

    async function refreshCurrentView() {
      try {
        if (typeof fetchCurrentUser === 'function') {
          await fetchCurrentUser();
        } else if (typeof initAuth === 'function') {
          await initAuth();
        }
      } catch (err) {
        console.warn('User auth sync warning:', err);
      }

      try {
        if (currentAppMode === 'freelance') {
          await Promise.allSettled([loadFreelanceStats(), loadFreelanceLeads()]);
        } else {
          await Promise.allSettled([loadJobSummary(), loadJobs(), loadInbox(), loadOutbound(), loadPipelineRuns(), loadSystemInfo()]);
        }
      } catch (err) {
        console.error('Sync error:', err);
      }
    }

    async function manualRefresh() {
      const btn = document.getElementById('globalRefreshBtn');
      const icon = document.getElementById('globalRefreshIcon');
      const text = document.getElementById('globalRefreshText');
      if (btn) btn.disabled = true;
      if (icon) icon.style.animation = 'spinRefresh 0.7s linear infinite';
      if (text) text.textContent = 'Refreshing...';
      try {
        await refreshCurrentView();
        showToast('Dashboard refreshed successfully ✓', 'success');
      } catch (err) {
        showToast('Refresh error: ' + (err.message || err), 'error');
      } finally {
        if (btn) btn.disabled = false;
        if (icon) icon.style.animation = '';
        if (text) text.textContent = 'Refresh';
      }
    }

    // =========================================================================
    // FREELANCE ENGINE LOGIC
    // =========================================================================

    function switchFreelanceTab(tab) {
      currentFreelanceTab = tab;
      document.getElementById('fTabKanban').classList.toggle('active', tab === 'kanban');
      document.getElementById('fTabTable').classList.toggle('active', tab === 'table');
      document.getElementById('fTabAnalytics').classList.toggle('active', tab === 'analytics');

      document.getElementById('fKanbanSection').style.display = tab === 'kanban' ? 'block' : 'none';
      document.getElementById('fTableSection').style.display = tab === 'table' ? 'block' : 'none';
      document.getElementById('fAnalyticsSection').style.display = tab === 'analytics' ? 'block' : 'none';

      if (tab === 'kanban') renderKanbanBoard();
      else if (tab === 'table') renderFreelanceTable();
      else if (tab === 'analytics') renderAnalyticsFunnel();
    }

    let freelanceFollowUpFilterActive = false;

    async function loadFreelanceStats() {
      const stats = await apiGet('/api/freelance/stats');
      animateValue('fKpiTotal', stats.total_leads);
      animateValue('fKpiAvgScore', stats.average_score + '%');
      animateValue('fKpiPitched', stats.active_pitches);
      animateValue('fKpiDiscussion', stats.in_discussion);
      animateValue('fKpiOffers', stats.offers_received);
      animateValue('fKpiWon', stats.deals_won);
      const fuCount = stats.needs_follow_up || 0;
      animateValue('fKpiFollowUps', fuCount);
      animateValue('batchFollowUpBadge', fuCount);
      animateValue('fKpiFollowUpsBadge', fuCount);
    }

    async function loadFreelanceLeads() {
      const q = encodeURIComponent(document.getElementById('tableSearch')?.value || '');
      const stage = encodeURIComponent(document.getElementById('tableStageFilter')?.value || '');
      let url = `/api/freelance/leads?limit=350&q=${q}${stage ? '&stage=' + stage : ''}`;
      if (freelanceFollowUpFilterActive) {
        url += '&needs_follow_up=true';
      }
      allFreelanceLeads = await apiGet(url);
      if (currentFreelanceTab === 'kanban') renderKanbanBoard();
      else if (currentFreelanceTab === 'table') renderFreelanceTable();
      else if (currentFreelanceTab === 'analytics') renderAnalyticsFunnel();
    }

    function toggleFreelanceFollowUpFilter() {
      freelanceFollowUpFilterActive = !freelanceFollowUpFilterActive;
      const btn = document.getElementById('fTabFollowUps');
      if (btn) {
        btn.classList.toggle('active', freelanceFollowUpFilterActive);
        btn.style.background = freelanceFollowUpFilterActive ? 'rgba(245,158,11,0.25)' : '';
      }
      loadFreelanceLeads();
    }

    function renderKanbanBoard() {
      const board = document.getElementById('kanbanBoard');
      board.innerHTML = '';

      const query = (document.getElementById('kanbanSearch')?.value || '').toLowerCase();
      const platform = document.getElementById('kanbanPlatformFilter')?.value || '';
      const minScore = parseInt(document.getElementById('kanbanScoreSlider')?.value || '0', 10);

      // Filter leads
      const filtered = allFreelanceLeads.filter(l => {
        if (query && !((l.title||'').toLowerCase().includes(query) || (l.client_name||'').toLowerCase().includes(query) || (l.raw_description||'').toLowerCase().includes(query))) return false;
        if (platform && l.source_platform !== platform) return false;
        if (minScore > 0 && (l.match_score || 0) < minScore) return false;
        return true;
      });

      // Group by stage
      const grouped = {};
      KANBAN_STAGES.forEach(s => grouped[s.id] = []);
      filtered.forEach(l => {
        if (grouped[l.stage]) grouped[l.stage].push(l);
        else if (l.stage === 'discovered') grouped['evaluated']?.push(l);
      });

      KANBAN_STAGES.forEach(col => {
        const colDiv = document.createElement('div');
        colDiv.className = 'kanban-col';
        colDiv.dataset.stage = col.id;

        // Column drag & drop listeners
        colDiv.addEventListener('dragover', (e) => {
          e.preventDefault();
          colDiv.classList.add('drag-over');
        });
        colDiv.addEventListener('dragleave', () => colDiv.classList.remove('drag-over'));
        colDiv.addEventListener('drop', async (e) => {
          e.preventDefault();
          colDiv.classList.remove('drag-over');
          const leadId = e.dataTransfer.getData('text/plain');
          if (leadId) await dropLeadToStage(parseInt(leadId, 10), col.id);
        });

        const cards = grouped[col.id] || [];
        colDiv.innerHTML = `
          <div class="kanban-col-head">
            <div class="kanban-col-title">${col.label}</div>
            <span class="kanban-badge">${cards.length}</span>
          </div>
          <div class="kanban-cards-container">
            ${cards.map(l => createLeadCardHtml(l)).join('') || '<div style="color:var(--text-faint);font-size:12px;text-align:center;padding:24px 0">No leads in stage</div>'}
          </div>
        `;
        board.appendChild(colDiv);
      });

      // Re-bind card dragstart
      document.querySelectorAll('.lead-card').forEach(card => {
        card.addEventListener('dragstart', (e) => {
          e.dataTransfer.setData('text/plain', card.dataset.id);
          card.classList.add('dragging');
        });
        card.addEventListener('dragend', () => card.classList.remove('dragging'));
      });
    }

    function createLeadCardHtml(l) {
      const score = l.match_score != null ? l.match_score : 0;
      const scoreColor = score >= 75 ? 'var(--good)' : score >= 50 ? 'var(--warn)' : 'var(--bad)';
      const scoreBg = score >= 75 ? 'var(--good-bg)' : score >= 50 ? 'var(--warn-bg)' : 'var(--bad-bg)';
      const pClass = `tag-${l.source_platform || 'default'}`;

      return `
        <div class="lead-card" draggable="true" data-id="${l.id}" onclick="openLeadModal(${l.id})">
          <div class="lead-card-title">${esc(l.title || 'Untitled Project')}</div>
          <div class="lead-card-client">
            <span class="platform-tag ${pClass}">${esc(l.source_platform || 'web')}</span>
            <span>${esc(l.client_name || 'Individual / Founder')}</span>
          </div>
          <div class="lead-card-footer">
            <span class="lead-budget">${l.budget_estimate ? '💰 ' + esc(l.budget_estimate) : '—'}</span>
            <span class="lead-score-pill" style="color:${scoreColor};background:${scoreBg};border:1px solid ${scoreColor}40">
              ${score}% Fit
            </span>
          </div>
          ${l.follow_up_count ? `<div style="font-size:10px;color:var(--purple-light);margin-top:6px;font-weight:600">✉️ FU #${l.follow_up_count} active</div>` : ''}
        </div>
      `;
    }

    function filterFreelanceCards() {
      renderKanbanBoard();
    }

    async function dropLeadToStage(leadId, newStage) {
      const lead = allFreelanceLeads.find(x => x.id === leadId);
      if (!lead || lead.stage === newStage) return;

      // Optimistic update
      const oldStage = lead.stage;
      lead.stage = newStage;
      renderKanbanBoard();

      try {
        await apiSend(`/api/freelance/leads/${leadId}/stage`, 'PATCH', { stage: newStage });
        showToast(`Lead #${leadId} moved to ${newStage.replace('_', ' ')}!`, 'success');
        loadFreelanceStats();
      } catch (e) {
        lead.stage = oldStage;
        renderKanbanBoard();
        showToast(`Failed to update stage: ${e.message}`, 'error');
      }
    }

    function renderFreelanceTable() {
      const tbody = document.getElementById('freelanceTableBody');
      if (!allFreelanceLeads.length) {
        tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-muted);padding:30px">No leads found matching criteria.</td></tr>';
        return;
      }
      tbody.innerHTML = allFreelanceLeads.map(l => {
        const score = l.match_score != null ? l.match_score + '%' : '0%';
        const pClass = `tag-${l.source_platform || 'default'}`;
        return `
          <tr class="clickable" onclick="openLeadModal(${l.id})">
            <td style="font-family:'JetBrains Mono'">${l.id}</td>
            <td style="font-weight:600;max-width:280px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(l.title)}</td>
            <td>${esc(l.client_name || '—')}</td>
            <td><span class="platform-tag ${pClass}">${esc(l.source_platform)}</span></td>
            <td style="font-family:'JetBrains Mono';font-weight:700">${score}</td>
            <td style="font-family:'JetBrains Mono';color:#34d399">${esc(l.budget_estimate || '—')}</td>
            <td><span class="pill pill-stage-${l.stage}">${esc(l.stage)}</span></td>
            <td><span style="font-size:11px;color:var(--text-muted);font-weight:600">${esc(l.pitch_status || 'not_generated')}</span></td>
            <td>
              ${l.contact_email 
                ? `<span class="pill pill-stage-deal_won" style="font-weight:700;font-size:11px;background:rgba(16,185,129,0.18);color:#10b981;border:1px solid rgba(16,185,129,0.4)">✉️ Direct Email</span><div style="font-size:10px;font-family:'JetBrains Mono';color:var(--text-muted);max-width:160px;overflow:hidden;text-overflow:ellipsis;margin-top:2px" title="${esc(l.contact_email)}">${esc(l.contact_email)}</div>` 
                : `<span class="pill" style="font-weight:700;font-size:11px;background:rgba(6,182,212,0.15);color:#06b6d4;border:1px solid rgba(6,182,212,0.35)">💬 Platform DM</span><div style="font-size:10px;color:var(--text-muted);margin-top:2px">${esc(l.source_platform || 'Portal')}</div>`
              }
              ${(l.pitched_at || l.applied_at || l.last_contact_at) ? `<div style="font-size:10px;font-family:'JetBrains Mono';color:#93c5fd;font-weight:600;margin-top:3px">🕒 ${formatAppDate(l.pitched_at || l.applied_at || l.last_contact_at)}</div>` : ''}
            </td>
            <td>
              <button class="btn btn-sm" onclick="event.stopPropagation();openLeadModal(${l.id}, 'pitch')">✍️ Pitch</button>
            </td>
          </tr>
        `;
      }).join('');
    }

    let currentLeadSortField = null;
    let currentLeadSortAsc = true;

    function sortLeads(field) {
      if (currentLeadSortField === field) {
        currentLeadSortAsc = !currentLeadSortAsc;
      } else {
        currentLeadSortField = field;
        currentLeadSortAsc = true;
      }
      allFreelanceLeads.sort((a, b) => {
        let valA = a[field] ?? '';
        let valB = b[field] ?? '';
        if (typeof valA === 'number' && typeof valB === 'number') {
          return currentLeadSortAsc ? valA - valB : valB - valA;
        }
        valA = String(valA).toLowerCase();
        valB = String(valB).toLowerCase();
        return currentLeadSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
      });
      renderFreelanceTable();
    }

    function renderAnalyticsFunnel() {
      const counts = {};
      let totalEstValue = 0;
      STAGES.forEach(s => counts[s] = 0);
      allFreelanceLeads.forEach(l => {
        if (counts[l.stage] !== undefined) counts[l.stage]++;
        if (l.budget_estimate) {
          const match = l.budget_estimate.replace(/[^0-9]/g, ' ');
          const nums = match.trim().split(/\s+/).map(Number).filter(n => n > 50 && n < 200000);
          if (nums.length) totalEstValue += nums[0];
        }
      });

      document.getElementById('pipelineValueDisplay').textContent = '$' + totalEstValue.toLocaleString();

      const container = document.getElementById('funnelContainer');
      const maxCount = Math.max(1, ...Object.values(counts));
      container.innerHTML = STAGES.map(stg => {
        const cnt = counts[stg] || 0;
        const pct = Math.round((cnt / maxCount) * 100);
        return `
          <div>
            <div style="display:flex;justify-content:space-between;font-size:12px;font-weight:600;margin-bottom:4px">
              <span class="pill pill-stage-${stg}">${stg.replace('_', ' ').toUpperCase()}</span>
              <span style="font-family:'JetBrains Mono'">${cnt} leads</span>
            </div>
            <div class="progress-bar-wrap">
              <div class="progress-bar-fill" style="width:${pct}%"></div>
            </div>
          </div>
        `;
      }).join('');
    }

    let clientDiscoveryPolling = null;

    async function triggerClientDiscovery() {
      const btn = document.getElementById('btnDiscoverClients');
      const icon = document.getElementById('discoverIcon');
      btn.disabled = true;
      icon.textContent = '⏳';
      showToast('Scanning Hacker News, Reddit, RemoteOK & Jobicy for clients...', 'normal');

      try {
        const res = await apiSend('/api/freelance/discover', 'POST');
        showToast(res.message || 'Client discovery started in background!', 'success');
        if (clientDiscoveryPolling) clearInterval(clientDiscoveryPolling);
        clientDiscoveryPolling = setInterval(pollClientDiscoveryStatus, 3000);
      } catch (err) {
        showToast(`Discovery error: ${err.message}`, 'error');
        btn.disabled = false;
        icon.textContent = '🔍';
      }
    }

    async function pollClientDiscoveryStatus() {
      try {
        const st = await apiGet('/api/freelance/status');
        const btn = document.getElementById('btnDiscoverClients');
        const icon = document.getElementById('discoverIcon');
        if (st.status === 'running') {
          btn.disabled = true;
          icon.textContent = '⏳';
        } else {
          btn.disabled = false;
          icon.textContent = '🔍';
          clearInterval(clientDiscoveryPolling);
          clientDiscoveryPolling = null;
          if (st.status === 'completed') {
            showToast(st.message || 'Client discovery finished!', 'success');
            loadFreelanceStats();
            loadFreelanceLeads();
          } else if (st.status === 'error') {
            showToast('Discovery error: ' + st.message, 'error');
          }
        }
      } catch (e) {
        console.error('Error polling discovery:', e);
      }
    }

    async function triggerBatchEvaluateLeads() {
      const btn = document.getElementById('btnBatchEvaluateLeads');
      const icon = document.getElementById('evalLeadsIcon');
      btn.disabled = true;
      icon.textContent = '⏳';
      showToast('Evaluating unscored project leads...', 'normal');
      try {
        const res = await apiSend('/api/freelance/batch_evaluate', 'POST', { limit: 50 });
        const s = res.summary || {};
        showToast(`Evaluated ${s.evaluated ?? 0} leads (${s.spam ?? 0} spam filtered)!`, 'success');
        loadFreelanceStats();
        loadFreelanceLeads();
      } catch (e) {
        showToast('Lead evaluation failed: ' + e.message, 'error');
      } finally {
        btn.disabled = false;
        icon.textContent = '⚡';
      }
    }

    async function triggerBatchDraftPitches() {
      const btn = document.getElementById('btnBatchDraftPitches');
      const icon = document.getElementById('draftPitchesIcon');
      btn.disabled = true;
      icon.textContent = '⏳';
      showToast('Drafting high-converting proposals for top matching leads...', 'normal');
      try {
        const res = await apiSend('/api/freelance/batch_pitch', 'POST', { min_score: 65, limit: 10, angle: 'mvp_speed' });
        showToast(`Drafted ${res.pitched_count ?? 0} proposals ready for review! 🏆`, 'success');
        loadFreelanceStats();
        loadFreelanceLeads();
      } catch (e) {
        showToast('Batch drafting failed: ' + e.message, 'error');
      } finally {
        btn.disabled = false;
        icon.textContent = '✨';
      }
    }

    function recalcDealEstimate() {
      const days = parseInt(document.getElementById('mCalcDays')?.value || '10', 10);
      const rate = parseFloat(document.getElementById('mCalcDailyRate')?.value || '400');
      const curr = document.getElementById('mDealCurrency')?.value || 'EUR';
      const total = days * rate;
      const el = document.getElementById('mCalcTotal');
      if (el) el.textContent = `${total} ${curr}`;
    }

    function applyCalculatedDeal() {
      const days = parseInt(document.getElementById('mCalcDays')?.value || '10', 10);
      const rate = parseFloat(document.getElementById('mCalcDailyRate')?.value || '400');
      const total = days * rate;
      document.getElementById('mDealAmount').value = total;
      showToast(`Applied ${total} to Offer Amount!`, 'normal');
    }

    // =========================================================================
    // MODAL: PITCH STUDIO & LEAD DETAILS
    // =========================================================================

    async function openLeadModal(leadId, defaultTab = 'intel') {
      try {
        currentActiveLead = await apiGet(`/api/freelance/leads/${leadId}`);
        const l = currentActiveLead;

        document.getElementById('modalLeadId').textContent = `#${l.id}`;
        document.getElementById('modalLeadTitle').textContent = l.title || 'Untitled Project';
        document.getElementById('modalLeadClient').textContent = `${l.client_name || 'Unknown'} · ${l.client_type || 'Client'} · ${fmt(l.discovered_at)}`;
        document.getElementById('modalLeadPlatform').textContent = l.source_platform || 'web';
        document.getElementById('modalLeadPlatform').className = `platform-tag tag-${l.source_platform || 'default'}`;
        document.getElementById('modalLeadStage').textContent = l.stage;
        document.getElementById('modalLeadStage').className = `pill pill-stage-${l.stage}`;

        // Intel tab
        document.getElementById('mIntelScore').textContent = (l.match_score ?? 0) + '%';
        document.getElementById('mIntelBudget').textContent = l.budget_estimate || 'Not specified';
        document.getElementById('mIntelFollowUps').textContent = l.follow_up_count || '0';
        document.getElementById('mIntelReason').textContent = l.evaluation_reason || 'No evaluation reason recorded.';
        document.getElementById('mIntelDescription').textContent = l.description || 'No description.';
        document.getElementById('mIntelUrlWrap').innerHTML = l.source_url ? 
          `<a href="${esc(l.source_url)}" target="_blank" class="btn btn-sm" style="color:var(--cyan-light)">Open Original Post ↗</a>` : '';

        // Pitch tab
        document.getElementById('mPitchSubject').value = l.pitch_subject || '';
        document.getElementById('mPitchBody').value = l.pitch_body || '';
        updatePitchCounter();

        // Async prefetch all multi-channel formats
        currentPitchFormat = 'email';
        currentLeadFormats = null;
        switchPitchFormat('email');
        apiGet(`/api/freelance/leads/${leadId}/formats`).then(fmts => {
          if (fmts && fmts.ok) {
            currentLeadFormats = fmts;
            if (currentPitchFormat !== 'email') {
              switchPitchFormat(currentPitchFormat);
            }
          }
        }).catch(err => console.debug('Format load:', err));

        // Follow-up Cadence info in Pitch tab
        const fuBadge = document.getElementById('mFollowUpStatusBadge');
        if (fuBadge) fuBadge.textContent = `FU #${l.follow_up_count || 0}`;
        const nextDueEl = document.getElementById('mNextDueText');
        if (nextDueEl) {
          if (l.next_follow_up_due) {
            const dueDate = new Date(l.next_follow_up_due);
            const isDue = dueDate <= new Date();
            nextDueEl.textContent = `Next due: ${dueDate.toLocaleDateString()} ${isDue ? '⚠️ DUE NOW' : ''}`;
            nextDueEl.style.color = isDue ? 'var(--warn)' : 'var(--text-muted)';
          } else if (l.follow_up_count >= 2) {
            nextDueEl.textContent = 'Cadence complete (2/2 sent)';
            nextDueEl.style.color = 'var(--good)';
          } else {
            nextDueEl.textContent = 'Scheduled after pitch dispatch';
            nextDueEl.style.color = 'var(--text-muted)';
          }
        }

        // Timeline tab
        const mContainer = document.getElementById('mTimelineContainer');
        const msgs = l.messages || [];
        if (!msgs.length) {
          mContainer.innerHTML = '<div style="color:var(--text-faint);font-size:13px;padding:20px 0">No messages sent yet. Use the Pitch Studio to generate and dispatch your first proposal!</div>';
        } else {
          mContainer.innerHTML = msgs.map(m => `
            <div class="timeline-item">
              <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-muted);margin-bottom:4px">
                <span style="font-weight:700;text-transform:uppercase;color:var(--purple-light)">${esc(m.direction)} · ${esc(m.message_type)}</span>
                <span>${fmt(m.created_at)}</span>
              </div>
              <div style="font-weight:600;font-size:13px;margin-bottom:6px">${esc(m.subject || 'No subject')}</div>
              <div class="code-block" style="max-height:160px">${esc(m.body || '')}</div>
            </div>
          `).join('');
        }

        // Deal tab
        document.getElementById('mDealAmount').value = l.offer_amount || '';
        document.getElementById('mDealCurrency').value = l.currency || 'USD';
        document.getElementById('mDealTerms').value = l.offer_terms || '';
        document.getElementById('mDealNotes').value = l.counter_offer_notes || '';

        // Deal calculator default prefill from active profile
        const calcDaily = document.getElementById('mCalcDailyRate');
        if (calcDaily) {
          if (!window._activeCandidateProfile) {
            apiGet('/api/profiles/active').then(res => {
              if (res && res.profile) {
                window._activeCandidateProfile = res.profile;
                if (!calcDaily.value || calcDaily.value === '400') {
                  calcDaily.value = res.profile.freelance_daily_rate || res.profile.freelance_daily_eur || 400;
                  recalcDealEstimate();
                }
              }
            }).catch(() => {});
          } else if (!calcDaily.value || calcDaily.value === '400') {
            calcDaily.value = window._activeCandidateProfile.freelance_daily_rate || 400;
          }
          recalcDealEstimate();
        }

        switchModalTab(defaultTab);
        document.getElementById('leadModal').classList.add('open');
      } catch (err) {
        showToast('Error opening lead: ' + err.message, 'error');
      }
    }

    function closeLeadModal() {
      document.getElementById('leadModal').classList.remove('open');
    }

    function switchModalTab(tab) {
      document.getElementById('mTabIntel').classList.toggle('active', tab === 'intel');
      document.getElementById('mTabPitch').classList.toggle('active', tab === 'pitch');
      document.getElementById('mTabHistory').classList.toggle('active', tab === 'history');
      document.getElementById('mTabDeal').classList.toggle('active', tab === 'deal');

      document.getElementById('modalTabIntel').style.display = tab === 'intel' ? 'block' : 'none';
      document.getElementById('modalTabPitch').style.display = tab === 'pitch' ? 'block' : 'none';
      document.getElementById('modalTabHistory').style.display = tab === 'history' ? 'block' : 'none';
      document.getElementById('modalTabDeal').style.display = tab === 'deal' ? 'block' : 'none';
    }

    let currentLeadFormats = null;
    let currentPitchFormat = 'email';

    function switchPitchFormat(fmt) {
      currentPitchFormat = fmt;
      const buttons = {
        email: document.getElementById('fmtBtn_email'),
        linkedin: document.getElementById('fmtBtn_linkedin'),
        chat_dm: document.getElementById('fmtBtn_chat_dm'),
        follow_up_1: document.getElementById('fmtBtn_follow_up_1'),
        follow_up_2: document.getElementById('fmtBtn_follow_up_2')
      };

      Object.entries(buttons).forEach(([k, btn]) => {
        if (!btn) return;
        if (k === fmt) {
          btn.className = 'btn btn-sm btn-primary';
          btn.style.boxShadow = '0 0 10px rgba(56,189,248,0.3)';
        } else {
          btn.className = 'btn btn-sm';
          btn.style.boxShadow = 'none';
        }
      });

      const subjectWrap = document.getElementById('mPitchSubjectWrap');
      const angleWrap = document.getElementById('mAngleWrap');
      const bodyLabel = document.getElementById('mPitchBodyLabel');
      const sendBtn = document.getElementById('btnSendPitch');
      const hint = document.getElementById('mFormatHint');

      if (fmt === 'linkedin') {
        if (subjectWrap) subjectWrap.style.display = 'none';
        if (angleWrap) angleWrap.style.display = 'none';
        if (sendBtn) sendBtn.style.display = 'none';
        if (bodyLabel) bodyLabel.textContent = 'LinkedIn Connection Note (≤300 chars)';
        if (hint) hint.textContent = '💼 High-conversion connection request note for LinkedIn';
        const txt = currentLeadFormats?.linkedin_note?.body || '';
        if (txt) document.getElementById('mPitchBody').value = txt;
      } else if (fmt === 'chat_dm') {
        if (subjectWrap) subjectWrap.style.display = 'none';
        if (angleWrap) angleWrap.style.display = 'none';
        if (sendBtn) sendBtn.style.display = 'none';
        if (bodyLabel) bodyLabel.textContent = 'Direct Chat DM (Reddit, Twitter/X, Discord, Slack)';
        if (hint) hint.textContent = '💬 Casual 3-sentence DM with booking link for community chats';
        const txt = currentLeadFormats?.chat_dm?.body || '';
        if (txt) document.getElementById('mPitchBody').value = txt;
      } else if (fmt === 'follow_up_1') {
        if (subjectWrap) subjectWrap.style.display = 'block';
        if (angleWrap) angleWrap.style.display = 'none';
        if (sendBtn) sendBtn.style.display = 'inline-block';
        if (bodyLabel) bodyLabel.textContent = 'Follow-Up #1 Body (Technical Blueprints)';
        if (hint) hint.textContent = '⏰ Follow-up #1 (Day 3): Technical architecture roadmap value';
        const fu = currentLeadFormats?.follow_up_1;
        if (fu?.subject) document.getElementById('mPitchSubject').value = fu.subject;
        if (fu?.body) document.getElementById('mPitchBody').value = fu.body;
      } else if (fmt === 'follow_up_2') {
        if (subjectWrap) subjectWrap.style.display = 'block';
        if (angleWrap) angleWrap.style.display = 'none';
        if (sendBtn) sendBtn.style.display = 'inline-block';
        if (bodyLabel) bodyLabel.textContent = 'Follow-Up #2 Body (Streamlined Timeline)';
        if (hint) hint.textContent = '⏰ Follow-up #2 (Day 6): Streamlined recap & calendar link';
        const fu = currentLeadFormats?.follow_up_2;
        if (fu?.subject) document.getElementById('mPitchSubject').value = fu.subject;
        if (fu?.body) document.getElementById('mPitchBody').value = fu.body;
      } else { // email default
        if (subjectWrap) subjectWrap.style.display = 'block';
        if (angleWrap) angleWrap.style.display = 'flex';
        if (sendBtn) sendBtn.style.display = 'inline-block';
        if (bodyLabel) bodyLabel.textContent = 'Full Email Proposal / Pitch';
        if (hint) hint.textContent = '✉️ 3-sprint technical proposal with pricing & booking link';
        const em = currentLeadFormats?.email_proposal;
        if (em?.subject) document.getElementById('mPitchSubject').value = em.subject;
        else if (currentActiveLead?.pitch_subject) document.getElementById('mPitchSubject').value = currentActiveLead.pitch_subject;
        if (em?.body) document.getElementById('mPitchBody').value = em.body;
        else if (currentActiveLead?.pitch_body) document.getElementById('mPitchBody').value = currentActiveLead.pitch_body;
      }

      updatePitchCounter();
    }

    function updatePitchCounter() {
      const text = document.getElementById('mPitchBody')?.value || '';
      const words = text.trim() ? text.trim().split(/\s+/).length : 0;
      const chars = text.length;
      const readSec = Math.ceil(words / 3);
      const counterEl = document.getElementById('mPitchCounter');
      const alertEl = document.getElementById('mPitchLimitAlert');

      if (currentPitchFormat === 'linkedin') {
        if (counterEl) counterEl.textContent = `${chars}/300 chars · ${words} words`;
        if (alertEl) {
          alertEl.style.display = 'inline';
          if (chars > 300) {
            alertEl.style.color = 'var(--bad)';
            alertEl.textContent = `⚠️ EXCEEDS 300 CHARS (${chars - 300} over)`;
          } else {
            alertEl.style.color = 'var(--good)';
            alertEl.textContent = `✓ Within LinkedIn limit (${300 - chars} left)`;
          }
        }
      } else {
        if (counterEl) counterEl.textContent = `${words} words · ${chars} chars · ~${readSec}s read`;
        if (alertEl) alertEl.style.display = 'none';
      }
    }

    async function generateLeadPitch() {
      if (!currentActiveLead) return;
      const angle = document.getElementById('mPitchAngle')?.value || 'mvp_speed';
      const angleNames = {
        mvp_speed: '⚡ Speed / Fast MVP',
        architecture_quality: '🛡️ Robust Architecture',
        roi_advisory: '💡 ROI & Solution Advisory'
      };
      showToast(`Generating proposal (${angleNames[angle] || angle})...`, 'normal');
      try {
        const res = await apiSend(`/api/freelance/leads/${currentActiveLead.id}/pitch`, 'POST', { angle: angle });
        if (res.lead) {
          currentActiveLead = res.lead;
          try {
            currentLeadFormats = await apiGet(`/api/freelance/leads/${currentActiveLead.id}/formats`);
          } catch(e) {}
          switchPitchFormat(currentPitchFormat);
          showToast('Proposal generated successfully! 🏆', 'success');
        }
      } catch (e) {
        showToast('Failed to generate pitch: ' + e.message, 'error');
      }
    }

    async function saveLeadPitch() {
      if (!currentActiveLead) return;
      const subject = document.getElementById('mPitchSubject').value;
      const pitch_body = document.getElementById('mPitchBody').value;
      try {
        await apiSend(`/api/freelance/leads/${currentActiveLead.id}/pitch`, 'PATCH', { subject, pitch_body });
        showToast('Pitch draft saved!', 'success');
      } catch (e) {
        showToast('Failed to save pitch: ' + e.message, 'error');
      }
    }

    function copyPitchText() {
      const subject = document.getElementById('mPitchSubject')?.value || '';
      const body = document.getElementById('mPitchBody')?.value || '';
      let textToCopy = body;
      if (currentPitchFormat === 'email' || currentPitchFormat.startsWith('follow_up')) {
        textToCopy = subject ? `Subject: ${subject}\n\n${body}` : body;
      }

      navigator.clipboard.writeText(textToCopy);

      const copyBtn = document.getElementById('btnCopyPitch');
      if (copyBtn) {
        const orig = copyBtn.innerHTML;
        copyBtn.innerHTML = '✅ Copied to Clipboard!';
        copyBtn.classList.remove('btn-cyan');
        copyBtn.classList.add('btn-success');
        setTimeout(() => {
          copyBtn.innerHTML = orig;
          copyBtn.classList.remove('btn-success');
          copyBtn.classList.add('btn-cyan');
        }, 1800);
      }

      const formatNames = {
        email: 'Full Email Proposal',
        linkedin: 'LinkedIn Connection Note',
        chat_dm: 'Direct Chat DM',
        follow_up_1: 'Follow-Up #1',
        follow_up_2: 'Follow-Up #2'
      };
      showToast(`Copied ${formatNames[currentPitchFormat] || 'message'} to clipboard! ✓ Ready to paste.`, 'success');
    }

    async function sendLeadPitch() {
      if (!currentActiveLead) return;
      // Save any pending edits first
      await saveLeadPitch();
      showToast('Dispatching pitch outreach...', 'normal');
      try {
        const res = await apiSend(`/api/freelance/leads/${currentActiveLead.id}/send`, 'POST');
        if (res.ok) {
          showToast('Proposal dispatched successfully! ✓', 'success');
          await openLeadModal(currentActiveLead.id, 'history');
          loadFreelanceStats();
          loadFreelanceLeads();
        } else {
          showToast('Outreach: ' + res.status, 'normal');
        }
      } catch (e) {
        showToast('Send failed: ' + e.message, 'error');
      }
    }

    async function generateLeadFollowUp(num) {
      if (!currentActiveLead) return;
      showToast(`Drafting Follow-Up #${num}...`, 'normal');
      try {
        const res = await apiSend(`/api/freelance/leads/${currentActiveLead.id}/follow_up`, 'POST', { follow_up_number: num });
        if (res.lead) {
          currentActiveLead = res.lead;
          if (res.message) {
            document.getElementById('mPitchSubject').value = res.message.subject || '';
            document.getElementById('mPitchBody').value = res.message.body || '';
          } else {
            document.getElementById('mPitchSubject').value = res.lead.pitch_subject || '';
            document.getElementById('mPitchBody').value = res.lead.pitch_body || '';
          }
          updatePitchCounter();
          showToast(`Follow-Up #${num} drafted successfully! 🏆`, 'success');
          const fuBadge = document.getElementById('mFollowUpStatusBadge');
          if (fuBadge) fuBadge.textContent = `FU #${res.lead.follow_up_count || 0}`;
          const nextDueEl = document.getElementById('mNextDueText');
          if (nextDueEl && res.lead.next_follow_up_due) {
            const dueDate = new Date(res.lead.next_follow_up_due);
            nextDueEl.textContent = `Next due: ${dueDate.toLocaleDateString()}`;
          }
          loadFreelanceStats();
          loadFreelanceLeads();
        }
      } catch (e) {
        showToast('Failed to draft follow-up: ' + e.message, 'error');
      }
    }

    async function triggerBatchFollowUps() {
      const btn = document.getElementById('btnBatchFollowUps');
      const icon = document.getElementById('followUpIcon');
      if (btn) btn.disabled = true;
      if (icon) icon.textContent = '⏳';
      showToast('Processing automated follow-up cadence for due leads...', 'normal');
      try {
        const res = await apiSend('/api/freelance/batch_follow_up', 'POST');
        const s = res.summary || {};
        showToast(`Follow-up cadence complete: ${s.drafted ?? 0} drafted, ${s.follow_ups_sent ?? 0} sent!`, 'success');
        loadFreelanceStats();
        loadFreelanceLeads();
      } catch (e) {
        showToast('Batch follow-ups error: ' + e.message, 'error');
      } finally {
        if (btn) btn.disabled = false;
        if (icon) icon.textContent = '⏰';
      }
    }

    async function quickSetStage(stage) {
      if (!currentActiveLead) return;
      try {
        await apiSend(`/api/freelance/leads/${currentActiveLead.id}/stage`, 'PATCH', { stage });
        currentActiveLead.stage = stage;
        document.getElementById('modalLeadStage').textContent = stage;
        document.getElementById('modalLeadStage').className = `pill pill-stage-${stage}`;
        showToast(`Deal marked as ${stage}!`, 'success');
        refreshCurrentView();
      } catch (e) {
        showToast('Error setting stage: ' + e.message, 'error');
      }
    }

    async function saveDealDetails() {
      if (!currentActiveLead) return;
      const offer_amount = parseFloat(document.getElementById('mDealAmount').value) || null;
      const currency = document.getElementById('mDealCurrency').value || 'USD';
      const offer_terms = document.getElementById('mDealTerms').value;
      const counter_offer_notes = document.getElementById('mDealNotes').value;

      try {
        await apiSend(`/api/freelance/leads/${currentActiveLead.id}/deal`, 'PATCH', {
          offer_amount, currency, offer_terms, counter_offer_notes
        });
        showToast('Deal details saved!', 'success');
        refreshCurrentView();
      } catch (e) {
        showToast('Failed to save deal details: ' + e.message, 'error');
      }
    }

    // =========================================================================
    // JOB SEARCH AUTOMATION ENGINE LOGIC
    // =========================================================================

    function isUserProOrAdmin() {
      if (typeof currentUser === 'undefined' || !currentUser) return false;
      if (currentUser.role === 'admin') return true;
      const p = (currentUser.current_plan || '').toLowerCase();
      return ['pro', 'pro_499', 'ultra'].includes(p);
    }

    function updateHrDiscoveryAccess() {
      const isPro = isUserProOrAdmin();
      const banner = document.getElementById('hrProLockedBanner');
      if (banner) banner.style.display = isPro ? 'none' : 'block';

      const btnLookup = document.getElementById('btnHrLookup');
      if (btnLookup) {
        btnLookup.disabled = !isPro;
        btnLookup.style.opacity = isPro ? '1' : '0.55';
        btnLookup.style.cursor = isPro ? 'pointer' : 'not-allowed';
      }
      const btnBatch = document.getElementById('btnRunBatchHr');
      if (btnBatch) {
        btnBatch.disabled = !isPro;
        btnBatch.style.opacity = isPro ? '1' : '0.55';
        btnBatch.style.cursor = isPro ? 'pointer' : 'not-allowed';
      }
      const btnTopBatch = document.getElementById('btnBatchEnrichHr');
      if (btnTopBatch) {
        btnTopBatch.disabled = !isPro;
        btnTopBatch.style.opacity = isPro ? '1' : '0.55';
        btnTopBatch.style.cursor = isPro ? 'pointer' : 'not-allowed';
      }
    }

    function switchJobsTab(tab) {
      if (tab === 'system') {
        if (typeof currentUser === 'undefined' || !currentUser || currentUser.role !== 'admin') {
          showToast('System configuration is restricted to administrators', 'error');
          switchJobsTab('postings');
          return;
        }
      }
      if ((tab === 'admin' || tab === 'system') && (!currentUser || currentUser.role !== 'admin')) {
        showToast('🔒 Access Denied: Administrator privileges required.', 'error');
        if (currentJobsTab === 'admin' || currentJobsTab === 'system') {
          currentJobsTab = 'postings';
        }
        tab = 'postings';
      }
      currentJobsTab = tab;
      const tabs = [
        { id: 'postings', btn: 'jTabPostings', sec: 'jPostingsSection' },
        { id: 'hr_finder', btn: 'jTabHrFinder', sec: 'jHrFinderSection' },
        { id: 'profiles', btn: 'jTabProfiles', sec: 'jProfilesSection' },
        { id: 'live', btn: 'jTabLiveProgress', sec: 'jLiveSection' },
        { id: 'inbox', btn: 'jTabInbox', sec: 'jInboxSection' },
        { id: 'outbound', btn: 'jTabOutbound', sec: 'jOutboundSection' },
        { id: 'pricing', btn: 'jTabPricing', sec: 'jPricingSection' },
        { id: 'user_settings', btn: 'jTabUserSettings', sec: 'jUserSettingsSection' },
        { id: 'system', btn: 'jTabSystem', sec: 'jSystemSection' },
        { id: 'admin', btn: 'jTabAdmin', sec: 'jAdminSection' },
      ];
      tabs.forEach(t => {
        const btn = document.getElementById(t.btn);
        const sec = document.getElementById(t.sec);
        if (btn) {
          btn.classList.toggle('active', t.id === tab);
          btn.classList.toggle('jobs-tab', t.id === tab);
        }
        if (sec) sec.style.display = t.id === tab ? 'block' : 'none';
      });

      if (tab === 'postings') loadJobs();
      else if (tab === 'hr_finder') { loadHrStats(); updateHrDiscoveryAccess(); }
      else if (tab === 'profiles') { loadProfiles(); loadActiveMatrixPreview(); loadIntegrationsStatus(); }
      else if (tab === 'live') loadLivePipelineMonitor();
      else if (tab === 'inbox') loadInbox();
      else if (tab === 'outbound') loadOutbound();
      else if (tab === 'pricing') loadPricingSection();
      else if (tab === 'user_settings') loadUserSettings();
      else if (tab === 'system') loadSystemInfo();
      else if (tab === 'admin') loadAdminUsers();
    }

    async function loadHrStats() {
      try {
        const s = await apiGet('/api/hr/stats');
        animateValue('hrKpiTotal', s.total_jobs);
        animateValue('hrKpiWithEmail', s.jobs_with_hr_email);
        animateValue('hrKpiMatchesWithEmail', s.matched_with_hr_email);
        const pctEl = document.getElementById('hrKpiPercent');
        if (pctEl) pctEl.textContent = (s.percentage_enriched ?? 0) + '%';
      } catch (e) {
        console.error('Failed loading HR stats:', e);
      }
    }

    async function lookupCompanyHrEmails() {
      if (!isUserProOrAdmin()) {
        showToast('⭐ HR & Talent Acquisition Email Discovery is exclusively available on the Pro Plan (499 MAD). Please upgrade in Profiles.', 'error');
        return;
      }
      const company = document.getElementById('hrLookupCompany')?.value.trim();
      const domain = document.getElementById('hrLookupDomain')?.value.trim();
      if (!company && !domain) {
        showToast('Please enter a company name or domain to search', 'error');
        return;
      }
      const btn = document.getElementById('btnHrLookup');
      const icon = document.getElementById('hrLookupIcon');
      if (btn) btn.disabled = true;
      if (icon) icon.textContent = '⏳';
      showToast(`Searching HR & Recruiter contacts for ${company || domain}...`, 'normal');

      try {
        const res = await apiSend('/api/hr/discover', 'POST', {
          company_name: company,
          domain: domain,
        });

        const resWrap = document.getElementById('hrLookupResults');
        if (resWrap) resWrap.style.display = 'block';
        const domEl = document.getElementById('hrResDomain');
        if (domEl) domEl.textContent = res.resolved_domain || 'None';
        const mxBadge = document.getElementById('hrResMxBadge');
        if (mxBadge) {
          if (res.has_mx) {
            mxBadge.textContent = 'MX Validated ✓';
            mxBadge.className = 'pill pill-stage-deal_won';
          } else {
            mxBadge.textContent = 'MX Unconfirmed ⚠️';
            mxBadge.className = 'pill pill-stage-lost';
          }
        }

        const listEl = document.getElementById('hrContactsList');
        if (listEl) {
          if (!res.contacts || !res.contacts.length) {
            listEl.innerHTML = '<div style="padding:14px;color:var(--text-muted);text-align:center">No verified recruiter emails found for this domain yet.</div>';
          } else {
            listEl.innerHTML = res.contacts.map((c) => `
              <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 12px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:var(--radius-sm);flex-wrap:wrap;gap:8px">
                <div>
                  <div style="display:flex;align-items:center;gap:8px">
                    <strong style="font-family:'JetBrains Mono';font-size:13px;color:#fff">${esc(c.email)}</strong>
                    <span class="pill pill-stage-${c.confidence >= 80 ? 'deal_won' : 'pitched'}" style="font-size:10px">${c.confidence}% Match</span>
                    <span class="pill" style="font-size:10px;background:rgba(255,255,255,0.06)">${esc(c.source)}</span>
                  </div>
                  ${c.title ? `<div style="font-size:11px;color:var(--text-muted);margin-top:2px">${esc(c.title)}</div>` : ''}
                </div>
                <div style="display:flex;gap:6px">
                  <button class="btn btn-sm btn-cyan" onclick="navigator.clipboard.writeText('${esc(c.email)}');showToast('Copied ${esc(c.email)} to clipboard! ✓', 'success')" style="font-size:11px;padding:2px 8px">📋 Copy</button>
                  <a href="mailto:${esc(c.email)}" class="btn btn-sm" style="font-size:11px;padding:2px 8px;border-color:var(--cyan);color:var(--cyan-light)">✉️ Email</a>
                </div>
              </div>
            `).join('');
          }
        }
        showToast(`Discovered ${res.total_contacts} recruiter contacts!`, 'success');
      } catch (e) {
        showToast('HR discovery error: ' + e.message, 'error');
      } finally {
        if (btn) btn.disabled = false;
        if (icon) icon.textContent = '🎯';
      }
    }

    async function triggerBatchHrEnrichment() {
      if (!isUserProOrAdmin()) {
        showToast('⭐ Batch HR Email Discovery is exclusively available on the Pro Plan (499 MAD). Please upgrade in Profiles.', 'error');
        return;
      }
      const limit = parseInt(document.getElementById('hrBatchLimit')?.value || '25', 10);
      const btn = document.getElementById('btnRunBatchHr');
      const icon = document.getElementById('batchHrIcon');
      const topBtn = document.getElementById('btnBatchEnrichHr');
      const topIcon = document.getElementById('enrichHrIcon');
      if (btn) btn.disabled = true;
      if (icon) icon.textContent = '⏳';
      if (topBtn) topBtn.disabled = true;
      if (topIcon) topIcon.textContent = '⏳';

      showToast('Starting background HR email discovery across matching jobs...', 'normal');
      const statusBadge = document.getElementById('hrBatchStatusBadge');
      const msgEl = document.getElementById('hrBatchMsg');
      const barWrap = document.getElementById('hrProgressBarWrap');
      const bar = document.getElementById('hrProgressBar');
      if (statusBadge) { statusBadge.textContent = 'Running'; statusBadge.className = 'pill pill-stage-pitched'; }
      if (barWrap) barWrap.style.display = 'block';

      try {
        const res = await apiSend(`/api/hr/enrich_batch?limit=${limit}`, 'POST');
        showToast(res.message || 'HR enrichment started!', 'success');
        const poll = setInterval(async () => {
          const st = await apiGet('/api/hr/enrich_status');
          if (msgEl) msgEl.textContent = st.message;
          if (bar && st.total > 0) {
            const pct = Math.round((st.processed / st.total) * 100);
            bar.style.width = pct + '%';
          }
          if (!st.running) {
            clearInterval(poll);
            if (btn) btn.disabled = false;
            if (icon) icon.textContent = '🚀';
            if (topBtn) topBtn.disabled = false;
            if (topIcon) topIcon.textContent = '🎯';
            if (statusBadge) { statusBadge.textContent = 'Completed'; statusBadge.className = 'pill pill-stage-deal_won'; }
            showToast(st.message || 'Batch HR discovery completed!', 'success');
            loadHrStats();
            loadJobs();
          }
        }, 2500);
      } catch (e) {
        showToast('Batch HR error: ' + e.message, 'error');
        if (btn) btn.disabled = false;
        if (icon) icon.textContent = '🚀';
        if (topBtn) topBtn.disabled = false;
        if (topIcon) topIcon.textContent = '🎯';
        if (statusBadge) { statusBadge.textContent = 'Error'; statusBadge.className = 'pill pill-stage-lost'; }
      }
    }

    async function enrichModalJobHr() {
      if (!currentActiveJob) return;
      if (!isUserProOrAdmin()) {
        showToast('⭐ Recruiter discovery is a Pro Plan feature (499 MAD). Upgrade to unlock direct HR emails.', 'error');
        return;
      }
      const btn = document.getElementById('btnModalEnrichHr');
      const icon = document.getElementById('modalHrIcon');
      if (btn) btn.disabled = true;
      if (icon) icon.textContent = '⏳';
      showToast(`Discovering HR contacts for ${currentActiveJob.company}...`, 'normal');
      try {
        const res = await apiSend(`/api/hr/enrich_job/${currentActiveJob.id}`, 'POST');
        if (res.ok && res.application_emails) {
          showToast(`Discovered HR contacts: ${res.application_emails}`, 'success');
          const elEmails = document.getElementById('mJobAppEmails');
          const elEmail = document.getElementById('mJobAppEmail');
          if (elEmails) elEmails.textContent = res.application_emails;
          if (elEmail) elEmail.textContent = res.top_email || res.application_emails.split(',')[0].trim();
          currentActiveJob.application_emails = res.application_emails;
          loadJobs();
        } else {
          showToast(`No verified HR contacts found for ${currentActiveJob.company}`, 'normal');
        }
      } catch (e) {
        showToast('HR enrichment error: ' + e.message, 'error');
      } finally {
        if (btn) btn.disabled = false;
        if (icon) icon.textContent = '🎯';
      }
    }

    async function loadJobSummary() {
      const s = await apiGet('/api/summary');
      animateValue('jKpiTotal', s.jobs.total);
      animateValue('jKpiEval', s.jobs.evaluated);
      animateValue('jKpiMatches', s.jobs.good_matches);
      animateValue('jKpiApps', s.applications_today);
      if (s.is_admin) {
        const lbl = document.getElementById('jKpiAiCallsLabel');
        const sub = document.getElementById('jKpiAiCallsSub');
        if (lbl) lbl.textContent = 'AI Calls Today';
        if (sub) sub.textContent = 'Quota usage';
        animateValue('jKpiAiCalls', s.quota?.ai_calls_today ?? 0);
      } else {
        const lbl = document.getElementById('jKpiAiCallsLabel');
        const sub = document.getElementById('jKpiAiCallsSub');
        if (lbl) lbl.textContent = 'Daily Limit Left';
        if (sub) sub.textContent = 'Applications remaining';
        const rem = s.user_quota?.applications_remaining ?? s.quota?.applications_remaining ?? 0;
        animateValue('jKpiAiCalls', rem);
      }
      animateValue('jKpiReplies', s.replies_today);

      // Keep top nav plan badge live synced with applications today
      const planBadge = document.getElementById('navPlanBadge');
      if (planBadge) {
        const u = (typeof currentUser !== 'undefined') ? currentUser : null;
        const limit = (u && u.daily_apply_limit !== undefined && u.daily_apply_limit !== null) ? u.daily_apply_limit : (u && u.role === 'admin' ? 200 : 5);
        const sent = (u && u.role === 'admin') ? s.applications_today : (u?.applications_sent_today ?? s.applications_today ?? 0);
        planBadge.textContent = `${sent}/${limit} apps today`;
      }

      // Render the rich Live Pipeline Telemetry
      renderLivePipelineTelemetry(s);
    }

    let livePipelinePoller = null;
    let isLivePipelinePolling = false;

    function formatDuration(seconds) {
      if (seconds === null || seconds === undefined || isNaN(seconds)) return '—';
      if (seconds < 60) return `${seconds}s`;
      const mins = Math.floor(seconds / 60);
      const remSecs = seconds % 60;
      return `${mins}m ${remSecs < 10 ? '0' : ''}${remSecs}s`;
    }

    const fmtTime = t => {
      if (!t) return '—';
      try {
        const d = new Date(t);
        return isNaN(d.getTime()) ? String(t) : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      } catch (_) {
        return String(t);
      }
    };

    function clearLiveEventStreamView() {
      const stream = document.getElementById('pipelineEventStream');
      if (stream) {
        stream.innerHTML = '<div style="color:var(--text-faint);text-align:center;padding:20px">Event stream cleared. New incoming events will appear here.</div>';
      }
    }

    async function loadLivePipelineMonitor(isManual = false) {
      if (isManual) {
        showToast('Refreshing live pipeline telemetry...', 'normal');
      }
      try {
        const s = await apiGet('/api/summary');
        renderLivePipelineTelemetry(s);
        if (isManual) {
          showToast('Live pipeline refreshed ✓', 'success');
        }
      } catch (err) {
        console.error('Failed to load live pipeline monitor:', err);
        if (isManual) {
          showToast('Telemetry sync error: ' + err.message, 'error');
        }
      }
    }

    async function manualRefreshLiveMonitor() {
      await loadLivePipelineMonitor(true);
    }

    function renderLivePipelineTelemetry(s) {
      if (!s) return;
      const run = s.latest_run;
      const prog = run?.progress || {};
      const status = (run?.status || 'IDLE').toLowerCase();
      const phase = (run?.phase || prog.stage || (status === 'success' ? 'complete' : 'idle')).toLowerCase();
      const pct = (run?.progress_pct !== null && run?.progress_pct !== undefined)
        ? run.progress_pct
        : (prog.progress_percent !== undefined ? prog.progress_percent : (status === 'success' ? 100 : 0));

      // 1. Top Badges & Buttons
      const statusBadge = document.getElementById('pipelineStatusBadge');
      if (statusBadge) {
        statusBadge.textContent = status.toUpperCase();
        statusBadge.className = `pill pill-stage-${status === 'running' ? 'pitched' : status === 'success' ? 'deal_won' : status === 'failed' ? 'lost' : 'discovered'}`;
      }

      const liveIndicator = document.getElementById('pipelineLiveIndicator');
      if (liveIndicator) {
        if (status === 'running') {
          liveIndicator.style.display = 'inline-flex';
          liveIndicator.innerHTML = '<span class="pulse-dot" style="background:var(--cyan)"></span> Live Running';
          liveIndicator.style.borderColor = 'rgba(6,182,212,0.4)';
          liveIndicator.style.color = 'var(--cyan-light)';
        } else {
          liveIndicator.style.display = 'inline-flex';
          liveIndicator.innerHTML = '<span class="pulse-dot" style="background:var(--good)"></span> Telemetry Synced';
          liveIndicator.style.borderColor = 'rgba(16,185,129,0.3)';
          liveIndicator.style.color = 'var(--good)';
        }
      }

      const btnRun = document.getElementById('btnTriggerPipelineLive');
      const iconRun = document.getElementById('liveRunIcon');
      if (btnRun) {
        btnRun.disabled = (status === 'running');
        if (iconRun) iconRun.textContent = (status === 'running') ? '⏳' : '⚡';
      }

      // 2. Cockpit Status Banner
      const cockpitRadar = document.getElementById('cockpitRadar');
      const cockpitRadarIcon = document.getElementById('cockpitRadarIcon');
      if (cockpitRadar) {
        cockpitRadar.className = `cockpit-radar ${status === 'running' ? 'running' : ''}`;
      }
      if (cockpitRadarIcon) {
        cockpitRadarIcon.textContent = status === 'running' ? '⚙️' : status === 'success' ? '✓' : status === 'failed' ? '⚠️' : '⚡';
      }

      const taskTitle = document.getElementById('pipelineCurrentTask');
      if (taskTitle) {
        if (run?.current_task) {
          taskTitle.textContent = run.current_task;
        } else if (status === 'success') {
          taskTitle.textContent = 'Pipeline execution completed successfully.';
        } else if (status === 'failed') {
          taskTitle.textContent = `Pipeline failed: ${run?.error || 'Unknown error'}`;
        } else {
          taskTitle.textContent = 'Ready — click "Run Discovery Pipeline" to start execution cycle.';
        }
      }

      const phaseBadge = document.getElementById('pipelinePhaseBadge');
      if (phaseBadge) {
        phaseBadge.textContent = phase.toUpperCase().replace('_', ' ');
        phaseBadge.className = `pill pill-stage-${status === 'running' ? 'pitched' : status === 'success' ? 'deal_won' : 'discovered'}`;
      }

      const profBadge = document.getElementById('pipelineProfileBadge');
      if (profBadge) {
        profBadge.textContent = `👤 Profile: ${s.active_profile?.name || 'Default'}`;
      }

      const kwPreview = document.getElementById('pipelineKeywordsPreview');
      if (kwPreview) {
        const kws = s.active_profile?.keywords || [];
        if (kws.length > 0) {
          kwPreview.textContent = `🎯 ${kws.slice(0, 4).join(', ')}`;
          kwPreview.style.display = 'inline-block';
        } else {
          kwPreview.style.display = 'none';
        }
      }

      const elapsedTimer = document.getElementById('pipelineElapsedTimer');
      if (elapsedTimer) {
        elapsedTimer.textContent = `⏱ Duration: ${formatDuration(run?.duration_seconds)}`;
      }

      const timestampMeta = document.getElementById('pipelineTimestampMeta');
      if (timestampMeta) {
        if (run?.started_at) {
          timestampMeta.textContent = `Started: ${fmtTime(run.started_at)}`;
        } else {
          timestampMeta.textContent = '';
        }
      }

      const pctText = document.getElementById('pipelineProgressPctText');
      if (pctText) pctText.textContent = `${pct}%`;

      const progressBar = document.getElementById('pipelineProgressBar');
      if (progressBar) progressBar.style.width = `${pct}%`;

      // 3. Multi-Stage Visual Stepper
      const stepScraping = document.getElementById('stepScraping');
      const stepDedup = document.getElementById('stepDedup');
      const stepAiEval = document.getElementById('stepAiEval');
      const stepHrEnrich = document.getElementById('stepHrEnrich');
      const stepOutbound = document.getElementById('stepOutbound');

      const statusScraping = document.getElementById('stepStatusScraping');
      const statusDedup = document.getElementById('stepStatusDedup');
      const statusAiEval = document.getElementById('stepStatusAiEval');
      const statusHrEnrich = document.getElementById('stepStatusHrEnrich');
      const statusOutbound = document.getElementById('stepStatusOutbound');

      function setStepState(stepEl, statusEl, state, label) {
        if (!stepEl || !statusEl) return;
        stepEl.className = `pipeline-step ${state}`;
        statusEl.textContent = label;
        if (state === 'active') {
          statusEl.style.color = 'var(--cyan-light)';
        } else if (state === 'completed') {
          statusEl.style.color = 'var(--good)';
        } else {
          statusEl.style.color = 'var(--text-faint)';
        }
      }

      if (status === 'idle') {
        setStepState(stepScraping, statusScraping, 'pending', 'WAITING');
        setStepState(stepDedup, statusDedup, 'pending', 'WAITING');
        setStepState(stepAiEval, statusAiEval, 'pending', 'WAITING');
        setStepState(stepHrEnrich, statusHrEnrich, 'pending', 'WAITING');
        setStepState(stepOutbound, statusOutbound, 'pending', 'WAITING');
      } else if (phase === 'scraping') {
        setStepState(stepScraping, statusScraping, 'active', 'SCRAPING');
        setStepState(stepDedup, statusDedup, 'pending', 'WAITING');
        setStepState(stepAiEval, statusAiEval, 'pending', 'WAITING');
        setStepState(stepHrEnrich, statusHrEnrich, 'pending', 'WAITING');
        setStepState(stepOutbound, statusOutbound, 'pending', 'WAITING');
      } else if (phase === 'deduplicating') {
        setStepState(stepScraping, statusScraping, 'completed', 'DONE');
        setStepState(stepDedup, statusDedup, 'active', 'RANKING');
        setStepState(stepAiEval, statusAiEval, 'pending', 'WAITING');
        setStepState(stepHrEnrich, statusHrEnrich, 'pending', 'WAITING');
        setStepState(stepOutbound, statusOutbound, 'pending', 'WAITING');
      } else if (phase === 'evaluating' || phase === 'evaluating_backlog') {
        setStepState(stepScraping, statusScraping, 'completed', 'DONE');
        setStepState(stepDedup, statusDedup, 'completed', 'DONE');
        setStepState(stepAiEval, statusAiEval, 'active', 'SCORING');
        setStepState(stepHrEnrich, statusHrEnrich, 'pending', 'WAITING');
        setStepState(stepOutbound, statusOutbound, 'pending', 'WAITING');
      } else if (phase === 'enriching') {
        setStepState(stepScraping, statusScraping, 'completed', 'DONE');
        setStepState(stepDedup, statusDedup, 'completed', 'DONE');
        setStepState(stepAiEval, statusAiEval, 'completed', 'DONE');
        setStepState(stepHrEnrich, statusHrEnrich, 'active', 'ENRICHING');
        setStepState(stepOutbound, statusOutbound, 'pending', 'WAITING');
      } else if (phase === 'applying') {
        setStepState(stepScraping, statusScraping, 'completed', 'DONE');
        setStepState(stepDedup, statusDedup, 'completed', 'DONE');
        setStepState(stepAiEval, statusAiEval, 'completed', 'DONE');
        setStepState(stepHrEnrich, statusHrEnrich, 'completed', 'DONE');
        setStepState(stepOutbound, statusOutbound, 'active', 'DISPATCHING');
      } else if (status === 'success' || phase === 'complete') {
        setStepState(stepScraping, statusScraping, 'completed', 'DONE');
        setStepState(stepDedup, statusDedup, 'completed', 'DONE');
        setStepState(stepAiEval, statusAiEval, 'completed', 'DONE');
        setStepState(stepHrEnrich, statusHrEnrich, 'completed', 'DONE');
        setStepState(stepOutbound, statusOutbound, 'completed', 'DONE');
      } else if (status === 'failed') {
        setStepState(stepScraping, statusScraping, phase === 'scraping' ? 'active' : 'completed', phase === 'scraping' ? 'FAILED' : 'DONE');
        setStepState(stepDedup, statusDedup, phase === 'deduplicating' ? 'active' : 'pending', phase === 'deduplicating' ? 'FAILED' : 'WAITING');
        setStepState(stepAiEval, statusAiEval, phase.startsWith('eval') ? 'active' : 'pending', phase.startsWith('eval') ? 'FAILED' : 'WAITING');
        setStepState(stepHrEnrich, statusHrEnrich, 'pending', 'WAITING');
        setStepState(stepOutbound, statusOutbound, 'pending', 'WAITING');
      }

      // 4. Expanded 8-Card Telemetry Grid
      const queryCompleted = prog.query_completed ?? run?.completed_queries ?? 0;
      const queryTotal = prog.query_total ?? run?.total_queries ?? 0;
      const rawScraped = prog.raw_scraped ?? run?.raw_scraped ?? 0;
      const newPostings = prog.new_postings ?? run?.new_postings ?? 0;
      const evaluated = prog.evaluated ?? run?.evaluated ?? 0;
      const scrapeErrors = prog.scrape_errors ?? run?.scrape_errors ?? 0;
      const appsSent = run?.applications_sent ?? s.applications_today ?? 0;
      const highMatches = s.jobs?.good_matches ?? 0;

      const detailsEl = document.getElementById('pipelineProgressDetails');
      if (detailsEl) {
        detailsEl.innerHTML = `
          <div class="kpi-card">
            <div class="kpi-label">Progress</div>
            <div class="kpi-value">${pct}%</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${esc(status.toUpperCase())}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Queries Completed</div>
            <div class="kpi-value">${queryCompleted} / ${queryTotal}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">Search Matrix</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Gross Scraped</div>
            <div class="kpi-value">${rawScraped}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">Total Feed Listings</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Fresh Postings</div>
            <div class="kpi-value" style="color:var(--purple-light)">${newPostings}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">Unique After Dedup</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">AI Evaluated</div>
            <div class="kpi-value" style="color:var(--cyan-light)">${evaluated}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">Profile Alignment</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">High Matches (≥70%)</div>
            <div class="kpi-value" style="color:var(--good)">${highMatches}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">Qualified Candidates</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Apps Dispatched</div>
            <div class="kpi-value" style="color:var(--cyan)">${appsSent}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${s.user_quota?.applications_remaining ?? 0} quota left</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Scrape Errors</div>
            <div class="kpi-value" style="color:${scrapeErrors > 0 ? 'var(--bad)' : 'var(--good)'}">${scrapeErrors}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${scrapeErrors === 0 ? 'All Queries Clean' : 'Feed Errors'}</div>
          </div>
        `;
      }

      // 5. Sources & Search Matrix Breakdown Table
      const sources = prog.source_results || [];
      const sourcesTable = document.getElementById('pipelineSourcesTable');
      const queryCountBadge = document.getElementById('pipelineQueryCountBadge');
      if (queryCountBadge) {
        queryCountBadge.textContent = `${sources.length || queryTotal} Queries`;
      }
      if (sourcesTable) {
        if (sources.length > 0) {
          sourcesTable.innerHTML = sources.map(x => `
            <tr>
              <td style="font-weight:600;color:#fff">${esc(x.term || x.id || '—')}</td>
              <td>${(x.sites || []).map(s => `<span class="pill" style="font-size:10px;background:rgba(255,255,255,0.06);color:var(--text-muted);margin-right:4px">${esc(s)}</span>`).join('') || '<span style="color:var(--text-faint)">All Boards</span>'}</td>
              <td style="font-family:'JetBrains Mono';font-weight:700;color:var(--cyan-light)">${x.rows ?? 0}</td>
              <td><span class="pill pill-stage-${x.status === 'failed' ? 'lost' : x.status === 'done' ? 'deal_won' : 'evaluated'}">${esc(x.status || 'done')}</span></td>
              <td style="font-family:'JetBrains Mono';font-size:11px">${x.duration_seconds ?? 0}s</td>
              <td style="font-size:11px;color:${x.error ? 'var(--bad)' : 'var(--good)'}">${esc(x.error || 'Clean ✓')}</td>
            </tr>
          `).join('');
        } else {
          sourcesTable.innerHTML = '<tr><td colspan="6" style="color:var(--text-faint);text-align:center;padding:20px">No task query results recorded yet. Start a pipeline run to observe source breakdown.</td></tr>';
        }
      }

      // 6. Live Milestone & Activity Event Stream
      const events = run?.events || [];
      const eventStream = document.getElementById('pipelineEventStream');
      const eventCountBadge = document.getElementById('pipelineEventCountBadge');
      if (eventCountBadge) {
        eventCountBadge.textContent = `${events.length} Events`;
      }
      if (eventStream) {
        if (events.length > 0) {
          eventStream.innerHTML = events.map(ev => {
            const timeStr = ev.created_at ? fmtTime(ev.created_at) : '—';
            const phaseClass = `phase-${(ev.phase || 'scraping').toLowerCase().replace(' ', '_')}`;
            let detailsHtml = '';
            if (ev.details && typeof ev.details === 'object') {
              const entries = Object.entries(ev.details)
                .filter(([k]) => !['status', 'message', 'source_results'].includes(k))
                .slice(0, 4);
              if (entries.length > 0) {
                detailsHtml = `
                  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:2px">
                    ${entries.map(([k, v]) => `<span class="event-details-pill"><strong>${esc(k)}</strong>: ${esc(String(v))}</span>`).join('')}
                  </div>
                `;
              }
            }
            return `
              <div class="pipeline-event-card ${phaseClass}">
                <div class="event-top-row">
                  <span class="event-time">${timeStr}</span>
                  <span class="pill pill-stage-${ev.status === 'error' ? 'lost' : ev.status === 'success' || ev.status === 'sent' ? 'deal_won' : 'discovered'}" style="font-size:9px;padding:1px 6px">
                    ${esc((ev.phase || 'EVENT').toUpperCase())}
                  </span>
                  <span style="font-size:10px;color:var(--text-faint)">[${esc(ev.status || 'info')}]</span>
                </div>
                <div class="event-msg">${esc(ev.message || '')}</div>
                ${detailsHtml}
              </div>
            `;
          }).join('');
          // Auto-scroll to bottom of stream
          eventStream.scrollTop = eventStream.scrollHeight;
        } else {
          eventStream.innerHTML = `
            <div style="color:var(--text-faint);text-align:center;margin:auto;padding:20px">
              <div style="font-size:24px;margin-bottom:8px">📡</div>
              <div>Ready for live pipeline events.</div>
              <div style="font-size:11px;margin-top:4px;color:var(--text-faint)">Start a run or refresh to view live engine activity.</div>
            </div>
          `;
        }
      }

      // 7. Recent Pipeline Executions History
      const histRuns = s.recent_runs || [];
      const historyBody = document.getElementById('pipelineHistoryBody');
      if (historyBody) {
        if (histRuns.length > 0) {
          historyBody.innerHTML = histRuns.map(r => `
            <tr>
              <td style="font-family:'JetBrains Mono';font-weight:700">#${r.id}</td>
              <td><span class="pill pill-stage-${r.status === 'running' ? 'pitched' : r.status === 'success' ? 'deal_won' : 'lost'}">${esc((r.status || 'done').toUpperCase())}</span></td>
              <td style="font-size:12px;color:var(--text-muted)">${r.started_at ? fmt(r.started_at) : '—'}</td>
              <td style="font-family:'JetBrains Mono';font-size:12px">${formatDuration(r.duration_seconds)}</td>
              <td style="font-family:'JetBrains Mono'">${r.raw_scraped}</td>
              <td style="font-family:'JetBrains Mono';color:var(--purple-light)">${r.new_postings}</td>
              <td style="font-family:'JetBrains Mono';color:var(--cyan-light)">${r.evaluated}</td>
              <td style="font-family:'JetBrains Mono';color:var(--good)">${r.applications_sent}</td>
              <td style="font-size:11px;color:${r.error ? 'var(--bad)' : 'var(--good)'}">${esc(r.error ? r.error.slice(0, 45) + '…' : 'Clean')}</td>
            </tr>
          `).join('');
        } else {
          historyBody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-faint)">No previous runs found.</td></tr>';
        }
      }

      // 8. Auto-polling management
      if (status === 'running' && !isLivePipelinePolling) {
        isLivePipelinePolling = true;
        if (livePipelinePoller) clearInterval(livePipelinePoller);
        livePipelinePoller = setInterval(async () => {
          try {
            const nextSummary = await apiGet('/api/summary');
            renderLivePipelineTelemetry(nextSummary);
            if (nextSummary.latest_run?.status !== 'running') {
              clearInterval(livePipelinePoller);
              livePipelinePoller = null;
              isLivePipelinePolling = false;
              showToast('Pipeline execution finished!', 'success');
              loadJobs();
            }
          } catch (e) {
            console.warn('Pipeline polling error:', e);
          }
        }, 2500);
      } else if (status !== 'running' && isLivePipelinePolling) {
        if (livePipelinePoller) clearInterval(livePipelinePoller);
        livePipelinePoller = null;
        isLivePipelinePolling = false;
      }
    }

    function getContinentBadge(continent, location, isRemote) {
      const c = continent || 'Europe';
      const flagMap = {
        'Europe': '🇪🇺',
        'Global Remote': '🌐',
        'MENA': '🇲🇦',
        'North America': '🇺🇸',
        'Asia-Pacific': '🌏'
      };
      const flag = flagMap[c] || '🌍';
      const locStr = location ? esc(location) : (isRemote ? 'Remote' : 'Onsite');
      return `<span class="pill" style="background:rgba(56,189,248,0.12);color:var(--cyan-light);font-size:11px">${flag} ${esc(c)}</span><div style="font-size:11px;color:var(--text-muted);margin-top:2px">${locStr}</div>`;
    }

    function getExpBadge(level, years) {
      const l = level || 'entry';
      const yStr = years !== null && years !== undefined ? `${years}y` : '';
      if (l === 'entry') return `<span class="pill pill-stage-deal_won" style="font-size:10px">🟢 Entry (0-2y${yStr ? ' · ' + yStr : ''})</span>`;
      if (l === 'mid') return `<span class="pill" style="font-size:10px;background:rgba(59,130,246,0.2);color:#93c5fd;border:1px solid rgba(59,130,246,0.4)">🔵 Mid (${yStr || '3-5y'})</span>`;
      if (l === 'senior') return `<span class="pill" style="font-size:10px;background:rgba(249,115,22,0.2);color:#fdba74;border:1px solid rgba(249,115,22,0.4)">🟠 Senior (${yStr || '5+y'})</span>`;
      return `<span class="pill" style="font-size:10px;background:rgba(168,85,247,0.2);color:#d8b4fe;border:1px solid rgba(168,85,247,0.4)">🟣 Lead (${yStr || '8+y'})</span>`;
    }

    function getDegreeBadge(deg) {
      const d = deg || 'none';
      if (d === 'phd') return `<span class="pill" style="font-size:10px;background:rgba(168,85,247,0.15);color:#d8b4fe">🎓 PhD</span>`;
      if (d === 'master') return `<span class="pill" style="font-size:10px;background:rgba(59,130,246,0.15);color:#93c5fd">🎓 Master</span>`;
      if (d === 'bachelor') return `<span class="pill" style="font-size:10px;background:rgba(16,185,129,0.15);color:#6ee7b7">🎓 Bachelor</span>`;
      return `<span class="pill" style="font-size:10px;background:rgba(255,255,255,0.06);color:var(--text-muted)">🎓 Flexible</span>`;
    }

    function getVisaBadge(cat, reloc) {
      const c = cat || 'unspecified';
      const relocIcon = reloc ? ' 📦' : '';
      if (c === 'sponsored') return `<span class="pill pill-stage-deal_won" style="font-size:10px">✈️ Sponsored${relocIcon}</span>`;
      if (c === 'relocation') return `<span class="pill pill-stage-deal_won" style="font-size:10px">📦 Relocation</span>`;
      if (c === 'remote_global') return `<span class="pill" style="font-size:10px;background:rgba(6,182,212,0.2);color:#67e8f9;border:1px solid rgba(6,182,212,0.4)">🌐 Global Remote</span>`;
      if (c === 'restricted') return `<span class="pill pill-stage-lost" style="font-size:10px">⚠️ Local Auth Req.</span>`;
      return `<span class="pill" style="font-size:10px;background:rgba(255,255,255,0.06);color:var(--text-muted)">❓ Unspecified</span>`;
    }

    function getApplicationBadge(j) {
      const isApplied = j.stage === 'applied' || j.application_status === 'sent' || j.application_status === 'submitted' || Boolean(j.applied_at);
      const method = (j.application_method || '').toLowerCase();
      const status = (j.application_status || '').toLowerCase();
      const targetEmail = j.application_email || (j.application_emails ? j.application_emails.split(',')[0].trim() : '');
      const appDateBadge = j.applied_at ? `
        <div style="display:flex;align-items:center;gap:4px;margin-top:2px;font-size:10px;font-family:'JetBrains Mono';color:#93c5fd;font-weight:600" title="Application timestamp: ${esc(j.applied_at)}">
          <span>🕒</span>
          <span>${formatAppDate(j.applied_at)}</span>
        </div>
      ` : '';

      if (isApplied) {
        if (method.includes('email') || status === 'sent' || (targetEmail && !method.includes('portal') && !method.includes('linkedin'))) {
          return `
            <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">
              <span class="pill pill-stage-deal_won" style="font-weight:800;font-size:11px;background:#059669;color:#ffffff;border:1px solid #10b981;padding:3px 9px;border-radius:6px;box-shadow:0 0 8px rgba(16,185,129,0.3)" title="Application successfully sent by Email">
                ✉️ EMAIL SENT
              </span>
              ${targetEmail ? `<span style="font-size:11px;font-family:'JetBrains Mono';color:#a7f3d0;font-weight:600;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="Recipient: ${esc(targetEmail)}">To: ${esc(targetEmail)}</span>` : ''}
              ${appDateBadge}
            </div>
          `;
        } else if (status === 'submitted') {
          return `
            <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">
              <span class="pill" style="font-weight:800;font-size:11px;background:#0891b2;color:#ffffff;border:1px solid #06b6d4;padding:3px 9px;border-radius:6px;box-shadow:0 0 8px rgba(6,182,212,0.3)" title="Applied via Online Web Portal / ATS - Confirmed">
                🌐 WEB PORTAL
              </span>
              <span style="font-size:11px;color:#a5f3fc;font-weight:600">
                Confirmed Submitted ✅
              </span>
              ${appDateBadge}
            </div>
          `;
        } else if (status === 'submitted_unverified') {
          return `
            <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">
              <span class="pill" style="font-weight:800;font-size:11px;background:rgba(234,179,8,0.22);color:#eab308;border:1px solid rgba(234,179,8,0.45);padding:3px 9px;border-radius:6px" title="Portal form clicked/submitted, but confirmation text was not detected on final page">
                ⚠️ PORTAL (UNVERIFIED)
              </span>
              <span style="font-size:10px;color:var(--text-muted);font-weight:600">
                Click row for proof 📸
              </span>
              ${appDateBadge}
            </div>
          `;
        } else if (method.includes('portal') || method.includes('web') || method.includes('browser')) {
          return `
            <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">
              <span class="pill" style="font-weight:800;font-size:11px;background:#0891b2;color:#ffffff;border:1px solid #06b6d4;padding:3px 9px;border-radius:6px" title="Applied via Online Web Portal / ATS">
                🌐 WEB PORTAL
              </span>
              <span style="font-size:11px;color:#a5f3fc;font-weight:600">
                ${esc(j.application_status || 'Submitted')}
              </span>
              ${appDateBadge}
            </div>
          `;
        } else if (method.includes('linkedin')) {
          return `
            <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">
              <span class="pill" style="font-weight:800;font-size:11px;background:#0284c7;color:#ffffff;border:1px solid #38bdf8;padding:3px 9px;border-radius:6px" title="Manual Application via LinkedIn">
                🔗 LINKEDIN
              </span>
              <span style="font-size:11px;color:#bae6fd;font-weight:600">
                Manual Apply
              </span>
              ${appDateBadge}
            </div>
          `;
        } else {
          return `
            <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">
              <span class="pill pill-stage-deal_won" style="font-weight:800;font-size:11px;background:#059669;color:#ffffff;padding:3px 9px;border-radius:6px">
                ✓ Applied (${esc(j.application_method || 'direct')})
              </span>
              ${appDateBadge}
            </div>
          `;
        }
      }


      // Not applied yet (discovered, evaluated_match, etc.)
      const stageClass = j.stage === 'evaluated_match' ? 'deal_won' : j.stage === 'evaluated_low' ? 'lost' : 'discovered';

      if (targetEmail) {
        return `
          <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">
            <span class="pill" style="font-size:11px;font-weight:700;background:rgba(168,85,247,0.22);color:#c084fc;border:1px solid rgba(168,85,247,0.45);padding:2px 8px;border-radius:6px" title="Application will be dispatched via direct recruiter email">
              ✉️ Via Email
            </span>
            <span style="font-size:10px;font-family:'JetBrains Mono';color:var(--text-muted);max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(targetEmail)}">
              ${esc(targetEmail)}
            </span>
          </div>
        `;
      } else if (j.application_url) {
        return `
          <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">
            <span class="pill" style="font-size:11px;font-weight:700;background:rgba(6,182,212,0.18);color:#22d3ee;border:1px solid rgba(6,182,212,0.4);padding:2px 8px;border-radius:6px" title="Application will be submitted via online company ATS portal">
              🌐 Via Web Portal
            </span>
            <span style="font-size:10px;color:var(--text-faint)">
              ATS Form
            </span>
          </div>
        `;
      }

      return `<span class="pill pill-stage-${stageClass}">${esc(j.stage)}</span>`;
    }

    function setJobQuickFilter(type) {
      const stageSelect = document.getElementById('jobsStageFilter');
      const hrSelect = document.getElementById('jobsHrFilter');
      ['qfAll', 'qfApplied', 'qfEmail', 'qfPortal'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.classList.remove('btn-cyan');
      });
      const activeBtn = document.getElementById(type === 'all' ? 'qfAll' : type === 'applied' ? 'qfApplied' : type === 'email' ? 'qfEmail' : 'qfPortal');
      if (activeBtn) activeBtn.classList.add('btn-cyan');

      if (type === 'all') {
        if (stageSelect) stageSelect.value = '';
        if (hrSelect) hrSelect.value = '';
      } else if (type === 'applied') {
        if (stageSelect) stageSelect.value = 'applied';
        if (hrSelect) hrSelect.value = '';
      } else if (type === 'email') {
        if (hrSelect) hrSelect.value = 'has_hr';
      } else if (type === 'portal') {
        if (hrSelect) hrSelect.value = 'web_form';
      }
      loadJobs();
    }

    async function loadJobs() {
      const q = encodeURIComponent(document.getElementById('jobsSearch')?.value || '');
      const stage = encodeURIComponent(document.getElementById('jobsStageFilter')?.value || '');
      const continent = encodeURIComponent(document.getElementById('jobsContinentFilter')?.value || '');
      const exp = encodeURIComponent(document.getElementById('jobsExperienceFilter')?.value || '');
      const deg = encodeURIComponent(document.getElementById('jobsDegreeFilter')?.value || '');
      const visa = encodeURIComponent(document.getElementById('jobsVisaFilter')?.value || '');
      const minScore = encodeURIComponent(document.getElementById('jobsMinScoreFilter')?.value || '');
      const hrFilter = document.getElementById('jobsHrFilter')?.value || '';
      const sortBy = encodeURIComponent(document.getElementById('jobsSortFilter')?.value || 'latest');

      let url = `/api/jobs?limit=300&q=${q}&sort_by=${sortBy}`;
      if (stage) url += `&stage=${stage}`;
      if (continent) url += `&continent=${continent}`;
      if (exp) url += `&experience=${exp}`;
      if (deg) url += `&degree=${deg}`;
      if (visa) url += `&visa=${visa}`;
      if (minScore) url += `&min_score=${minScore}`;

      allJobsList = await apiGet(url);

      if (hrFilter === 'has_hr') {
        allJobsList = allJobsList.filter(j => (j.application_emails && j.application_emails.trim() !== '') || (j.application_email && j.application_email.trim() !== '') || (j.application_method && j.application_method.includes('email')));
      } else if (hrFilter === 'web_form') {
        allJobsList = allJobsList.filter(j => (!j.application_emails || j.application_emails.trim() === '') && (!j.application_email || j.application_email.trim() === '') && (!j.application_method || !j.application_method.includes('email')));
      }

      const appliedCount = allJobsList.filter(j => j.stage === 'applied' || j.application_status === 'sent' || j.application_status === 'submitted' || Boolean(j.applied_at)).length;
      const emailCount = allJobsList.filter(j => (j.application_email && j.application_email.trim()) || (j.application_emails && j.application_emails.trim()) || (j.application_method && j.application_method.includes('email'))).length;
      const statsEl = document.getElementById('jobsQuickStats');
      if (statsEl) {
        statsEl.textContent = `${allJobsList.length} jobs • ${appliedCount} applied • ${emailCount} email channel`;
      }

      renderJobsTable();
      renderJobsCards();
    }

    function renderJobsTable() {
      const tbody = document.getElementById('jobsTableBody');
      if (!allJobsList.length) {
        tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-faint);padding:24px">No job postings found matching your precision filters.</td></tr>';
        return;
      }
      tbody.innerHTML = allJobsList.map(j => {
        const directUrl = j.application_url || j.job_url || '';
        const applyBtn = directUrl ? `<a href="${esc(directUrl)}" target="_blank" rel="noopener" class="btn btn-sm btn-cyan" onclick="event.stopPropagation()" style="padding:2px 8px;font-size:11px">Direct Apply ↗</a>` : '';
        const skillsPreview = (j.skills || []).slice(0, 3).map(s => `<span class="pill" style="font-size:10px;padding:1px 5px;background:rgba(255,255,255,0.06)">${esc(s)}</span>`).join(' ');
        
        const contBadge = getContinentBadge(j.continent, j.location, j.is_remote);
        const expBadge = getExpBadge(j.experience_level, j.experience_years_required);
        const degBadge = getDegreeBadge(j.degree_required);
        const visaBadge = getVisaBadge(j.visa_category, j.relocation_detected);
        const hrBadge = j.application_emails ? `<div style="margin-top:3px"><span class="pill pill-stage-deal_won" style="font-size:10px;padding:1px 6px;background:rgba(168,85,247,0.15);color:var(--purple-light);border:1px solid rgba(168,85,247,0.35)" title="${esc(j.application_emails)}">🎯 ${esc(j.application_emails.split(',')[0].trim())}</span></div>` : '';

        return `
        <tr class="clickable" onclick="openJobModal(${j.id})">
          <td style="font-family:'JetBrains Mono'">${j.id}</td>
          <td style="font-weight:600;max-width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
            ${esc(j.title)}
            <div style="margin-top:2px">${skillsPreview}</div>
          </td>
          <td style="font-weight:500">
            ${esc(j.company)}
            ${hrBadge}
          </td>
          <td>${contBadge}</td>
          <td>${expBadge}</td>
          <td>${degBadge}</td>
          <td>${visaBadge}</td>
          <td style="font-family:'JetBrains Mono';font-weight:700;color:${j.score >= 70 ? 'var(--good)' : j.score >= 50 ? 'var(--warn)' : 'var(--text)'}">${j.score ?? 0}%</td>
          <td>${getApplicationBadge(j)}</td>
          <td><div style="display:flex;align-items:center;gap:6px">${applyBtn}</div></td>
        </tr>
      `}).join('');
    }

    function renderJobsCards() {
      const container = document.getElementById('jobsCardsContainer');
      container.innerHTML = allJobsList.map(j => {
        const directUrl = j.application_url || j.job_url || '';
        const applyBtn = directUrl ? `<a href="${esc(directUrl)}" target="_blank" rel="noopener" class="btn btn-sm btn-cyan" onclick="event.stopPropagation()" style="padding:3px 9px;font-size:11px">Direct Apply ↗</a>` : '';
        const methodBadge = j.evaluation_method ? `<span class="pill" style="font-size:10px;background:rgba(139,92,246,0.15);color:var(--purple-light)">${esc(j.evaluation_method)}</span>` : '';
        const expBadge = getExpBadge(j.experience_level, j.experience_years_required);
        const degBadge = getDegreeBadge(j.degree_required);
        const visaBadge = getVisaBadge(j.visa_category, j.relocation_detected);
        const flag = j.continent === 'Europe' ? '🇪🇺' : j.continent === 'MENA' ? '🇲🇦' : j.continent === 'North America' ? '🇺🇸' : j.continent === 'Asia-Pacific' ? '🌏' : '🌐';
        const hrBadge = j.application_emails ? `<div style="margin-top:4px"><span class="pill pill-stage-deal_won" style="font-size:10px;background:rgba(168,85,247,0.15);color:var(--purple-light);border:1px solid rgba(168,85,247,0.35)">🎯 HR: ${esc(j.application_emails.split(',')[0].trim())}</span></div>` : '';

        return `
        <div class="kpi-card" style="cursor:pointer" onclick="openJobModal(${j.id})">
          <div class="kpi-label">
            <span>${flag} ${esc(j.company)}</span>
            <span style="font-family:'JetBrains Mono';color:${j.score >= 70 ? 'var(--good)' : 'var(--cyan-light)'};font-weight:800">${j.score ?? 0}%</span>
          </div>
          <div style="font-size:15px;font-weight:700;margin:6px 0;line-height:1.3">${esc(j.title)}</div>
          <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px">
            ${esc(j.location || 'Remote')} · ${j.is_remote ? '🌐 Remote' : '🏢 Onsite'}
            ${hrBadge}
          </div>
          
          <div style="display:flex;flex-wrap:gap:4px;margin-bottom:8px">
            ${expBadge}
            ${degBadge}
            ${visaBadge}
          </div>

          <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px">
            ${(j.skills||[]).slice(0,4).map(s => `<span class="pill" style="font-size:10px;background:rgba(255,255,255,0.06)">${esc(s)}</span>`).join('')}
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;padding-top:8px;border-top:1px solid rgba(255,255,255,0.05)">
            <div style="display:flex;align-items:center;gap:6px">
              ${getApplicationBadge(j)}
              ${methodBadge}
            </div>
            ${applyBtn}
          </div>
        </div>
      `}).join('') || '<div style="color:var(--text-faint)">No jobs to display matching filters.</div>';
    }

    function toggleJobsView(mode) {
      document.getElementById('jobsTableContainer').style.display = mode === 'table' ? 'block' : 'none';
      document.getElementById('jobsCardsContainer').style.display = mode === 'cards' ? 'grid' : 'none';
      document.getElementById('jobsViewTableBtn').classList.toggle('btn-cyan', mode === 'table');
      document.getElementById('jobsViewCardsBtn').classList.toggle('btn-cyan', mode === 'cards');
    }

    async function triggerBatchReEvaluateJobs() {
      const btn = document.getElementById('btnReEvalJobs');
      if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Evaluating...';
      }
      try {
        const res = await apiSend('/api/jobs/re_evaluate_batch', 'POST', { force_all: true });
        showToast(`Re-evaluated ${res.updated || 0} jobs with precision filters! ✓`, 'success');
        await loadJobs();
        await loadJobSummary();
      } catch (err) {
        showToast('Batch evaluation error: ' + err.message, 'error');
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = '⚡ Re-evaluate All';
        }
      }
    }

    async function openJobModal(jobId) {
      try {
        currentActiveJob = await apiGet(`/api/jobs/${jobId}`);
        const j = currentActiveJob;

        document.getElementById('modalJobId').textContent = `#${j.id}`;
        document.getElementById('modalJobTitle').textContent = j.title;
        document.getElementById('modalJobMeta').textContent = `${j.company} · ${j.location || 'Remote'} · Scraped ${fmt(j.scraped_at)}`;
        document.getElementById('modalJobStage').textContent = j.stage;
        document.getElementById('modalJobVisa').textContent = j.visa === true ? 'Visa Sponsored' : j.visa === false ? 'No Visa' : 'Visa Unknown';

        document.getElementById('modalJobStageSelect').value = j.stage;

        document.getElementById('mJobScore').textContent = (j.score ?? 0) + '%';
        document.getElementById('mJobAiScore').textContent = j.ai_relevance_score ?? 0;
        document.getElementById('mJobAppliedAt').textContent = j.applied_at ? formatAppDate(j.applied_at) : 'Not Applied';
        document.getElementById('mJobFollowUps').textContent = j.follow_up_count || '0';

        // Precision Fit fields in modal
        const continentNames = {
          'Europe': '🇪🇺 Europe',
          'Global Remote': '🌐 Global Remote',
          'MENA': '🇲🇦 MENA (Morocco/Middle East)',
          'North America': '🇺🇸 North America',
          'Asia-Pacific': '🌏 Asia-Pacific'
        };
        const expNames = {
          'entry': '🟢 Entry-Level / Junior (0-2y)',
          'mid': '🔵 Mid-Level (3-5y)',
          'senior': '🟠 Senior (5+y)',
          'lead': '🟣 Lead / Principal (8+y)'
        };
        const degNames = {
          'none': '🎓 Flexible / No Degree Required',
          'bachelor': '🎓 Bachelor / Licence / Bac+3',
          'master': '🎓 Master / Ingénieur / Bac+5',
          'phd': '🎓 PhD / Doctorate'
        };
        const visaCatNames = {
          'sponsored': '✈️ Visa Sponsorship Confirmed',
          'relocation': '📦 Relocation Package Offered',
          'remote_global': '🌐 Global Remote Contract',
          'restricted': '⚠️ Local Work Auth Required (No Visa)',
          'unspecified': '❓ Unspecified / Inquire'
        };

        const yStr = j.experience_years_required !== null && j.experience_years_required !== undefined ? ` · ${j.experience_years_required} years req.` : '';
        document.getElementById('mJobContinent').textContent = `${continentNames[j.continent] || j.continent || 'Europe'} (${j.is_remote ? 'Remote' : j.location || 'Onsite'})`;
        document.getElementById('mJobExp').textContent = (expNames[j.experience_level] || j.experience_level || 'entry') + yStr;
        document.getElementById('mJobDegree').textContent = degNames[j.degree_required] || j.degree_required || 'Flexible';
        document.getElementById('mJobVisaCat').textContent = (visaCatNames[j.visa_category] || j.visa_category || 'Unspecified') + (j.relocation_detected ? ' (+ Relocation)' : '');
        
        const fitBadge = document.getElementById('mJobFitBadge');
        if (j.experience_level === 'entry') {
          fitBadge.textContent = '🟢 Entry-Level Match ✓';
          fitBadge.className = 'pill pill-stage-deal_won';
        } else if (j.experience_level === 'senior' || j.experience_level === 'lead') {
          fitBadge.textContent = '⚠️ High Seniority Penalty';
          fitBadge.className = 'pill pill-stage-lost';
        } else {
          fitBadge.textContent = '🔵 Mid-Level';
          fitBadge.className = 'pill pill-stage-pitched';
        }

        const reasonEl = document.getElementById('mJobEvaluationReason');
        reasonEl.textContent = j.evaluation_reason || 'Evaluated via precision multi-dimensional rules.';

        // Enhanced Source & Scraping Info
        const elSourceSite = document.getElementById('mJobSourceSite');
        if (elSourceSite) elSourceSite.textContent = j.source_site || j.source || 'Direct';
        const elDatePosted = document.getElementById('mJobDatePosted');
        if (elDatePosted) elDatePosted.textContent = j.posted_at ? fmt(j.posted_at) : 'Unspecified';
        const elScrapedAt = document.getElementById('mJobScrapedAt');
        if (elScrapedAt) elScrapedAt.textContent = j.scraped_at ? fmt(j.scraped_at) : 'Unspecified';
        const elEvalMethod = document.getElementById('mJobEvalMethod');
        if (elEvalMethod) elEvalMethod.textContent = j.evaluation_method || 'rule_based';

        // Enhanced Application & Contact Details
        const elAppEmail = document.getElementById('mJobAppEmail');
        if (elAppEmail) elAppEmail.textContent = j.application_email || 'None';
        const elAppEmails = document.getElementById('mJobAppEmails');
        if (elAppEmails) elAppEmails.textContent = j.application_emails || (j.contact_emails || []).join(', ') || 'None';
        const elCompUrl = document.getElementById('mJobCompanyUrl');
        if (elCompUrl) {
          elCompUrl.innerHTML = j.company_url ? `<a href="${esc(j.company_url)}" target="_blank" style="color:var(--cyan-light);text-decoration:underline">${esc(j.company_url)} ↗</a>` : '—';
        }
        const elAppStatus = document.getElementById('mJobAppStatus');
        if (elAppStatus) elAppStatus.textContent = j.application_status || 'not_applied';
        const elAppMethod = document.getElementById('mJobAppMethod');
        if (elAppMethod) {
          const m = (j.application_method || '').toLowerCase();
          if (m.includes('email') || j.application_status === 'sent') {
            elAppMethod.innerHTML = `<span class="pill pill-stage-deal_won" style="font-size:11px">✉️ Recruiter Email</span> ${j.application_email ? `<span style="font-size:11px;color:var(--text-muted);font-family:'JetBrains Mono'">(${esc(j.application_email)})</span>` : ''}`;
          } else if (m.includes('portal') || m.includes('web') || m.includes('browser') || j.application_status === 'submitted') {
            elAppMethod.innerHTML = `<span class="pill" style="font-size:11px;background:rgba(6,182,212,0.18);color:#06b6d4;border:1px solid rgba(6,182,212,0.4)">🌐 Web Portal / ATS</span>`;
          } else if (m.includes('linkedin')) {
            elAppMethod.innerHTML = `<span class="pill" style="font-size:11px;background:rgba(10,102,194,0.18);color:#38bdf8;border:1px solid rgba(10,102,194,0.35)">🔗 LinkedIn (Manual)</span>`;
          } else {
            elAppMethod.textContent = j.application_method || 'not_applied';
          }
        }
        const elThreadSubj = document.getElementById('mJobThreadSubject');
        if (elThreadSubj) elThreadSubj.textContent = j.email_thread_subject || 'None';

        // ATS Audit & Screenshot Proof Card
        const atsSec = document.getElementById('mJobAtsAuditSection');
        const atsBadge = document.getElementById('mJobAtsVerifyBadge');
        const atsNote = document.getElementById('mJobAtsErrorNote');
        const screenWrap = document.getElementById('mJobScreenshotWrap');
        const screenImg = document.getElementById('mJobScreenshotImg');
        const screenLink = document.getElementById('mJobScreenshotLink');

        const isWebAttempt = (j.application_method && (j.application_method.includes('portal') || j.application_method.includes('web') || j.application_method.includes('manual'))) || j.application_status === 'submitted' || j.application_status === 'submitted_unverified' || j.has_screenshot;

        if (atsSec) {
          if (isWebAttempt) {
            atsSec.style.display = 'block';
            if (j.application_status === 'submitted') {
              atsBadge.textContent = '✅ Verified Employer Confirmation';
              atsBadge.className = 'pill pill-stage-deal_won';
              atsNote.textContent = j.application_error || 'Form was filled and submitted. Confirmation text was verified on the landing page.';
            } else if (j.application_status === 'submitted_unverified') {
              atsBadge.textContent = '⚠️ Unverified (No Confirmation Text)';
              atsBadge.className = 'pill pill-stage-pitched';
              atsNote.textContent = j.application_error || 'Form was submitted via browser, but explicit confirmation text was not detected on the final landing page. Inspect the screenshot proof below.';
            } else if (j.application_status === 'manual_security_challenge') {
              atsBadge.textContent = '🛡️ Cloudflare / CAPTCHA Challenge';
              atsBadge.className = 'pill pill-stage-lost';
              atsNote.textContent = j.application_error || 'A bot/security challenge was detected on the portal; browser stopped safely without bypassing it.';
            } else if (j.application_status === 'manual_required_fields') {
              atsBadge.textContent = '🛑 Unanswered Mandatory Fields';
              atsBadge.className = 'pill pill-stage-lost';
              atsNote.textContent = j.application_error || 'Form contained custom required screening questions that could not be filled automatically.';
            } else {
              atsBadge.textContent = j.application_status || 'Attempted';
              atsBadge.className = 'pill';
              atsNote.textContent = j.application_error || 'Browser portal interaction recorded.';
            }

            if (j.has_screenshot) {
              screenWrap.style.display = 'block';
              const sUrl = `/api/jobs/${j.id}/screenshot`;
              screenImg.src = sUrl;
              screenLink.href = sUrl;
            } else {
              screenWrap.style.display = 'none';
            }
          } else {
            atsSec.style.display = 'none';
          }
        }

        // ML Vector Fit & Skill Gap Analysis
        const secMl = document.getElementById('mJobMlFitSection');
        if (secMl) {
          apiGet(`/api/jobs/${j.id}/ml_match`).then(res => {
            if (res.ok && res.ml_match) {
              const m = res.ml_match;
              secMl.style.display = 'block';
              const cosEl = document.getElementById('mJobMlCosineSim');
              if (cosEl) cosEl.textContent = m.cosine_similarity;
              const scoreEl = document.getElementById('mJobMlScoreBadge');
              if (scoreEl) {
                scoreEl.textContent = `${m.match_score}% Match`;
                scoreEl.className = `pill pill-stage-${m.match_score >= 70 ? 'deal_won' : m.match_score >= 50 ? 'pitched' : 'lost'}`;
              }
              const matchEl = document.getElementById('mJobMlMatchingSkills');
              if (matchEl) {
                matchEl.innerHTML = (m.matching_skills || []).map(s => `<span class="pill pill-stage-deal_won" style="font-size:10px">${esc(s)}</span>`).join('') || '<span style="font-size:11px;color:var(--text-faint)">None directly matched</span>';
              }
              const missEl = document.getElementById('mJobMlMissingSkills');
              if (missEl) {
                missEl.innerHTML = (m.missing_skills || []).map(s => `<span class="pill pill-stage-lost" style="font-size:10px">${esc(s)}</span>`).join('') || '<span style="font-size:11px;color:var(--text-faint)">No major gaps</span>';
              }
              const noteEl = document.getElementById('mJobMlFitNote');
              if (noteEl) {
                noteEl.textContent = `${m.summary || ''} · Seniority: ${m.seniority_fit?.note || 'Aligned'} · Degree: ${m.degree_fit?.candidate_degree || 'Master/Ingénieur'}`;
              }
            }
          }).catch(err => {
            console.debug('ML match fetch error:', err);
          });
        }

        document.getElementById('mJobSkills').innerHTML = (j.skills || []).map(s => `<span class="pill" style="background:#1e293b">${esc(s)}</span>`).join('') || 'None listed';
        document.getElementById('mJobVisaNotes').textContent = j.visa_status_notes || 'No visa notes.';
        document.getElementById('mJobPitchEn').textContent = j.pitch_en || 'No pitch generated.';
        const elPitchFr = document.getElementById('mJobPitchFr');
        if (elPitchFr) elPitchFr.textContent = j.pitch_fr || 'No French pitch generated.';
        document.getElementById('mJobDesc').textContent = j.description || 'No description.';

        // Links & Actions
        const isApplied = j.stage === 'applied' || j.application_status === 'sent' || j.application_status === 'submitted';
        const applyBtn = isApplied
          ? `<span class="pill pill-stage-deal_won" style="padding:4px 10px;font-size:12px">✓ Applied (${esc(j.application_method || 'sent')})</span>`
          : `<button class="btn btn-sm btn-primary" id="btnModalApplySingle" onclick="applySingleJob(${j.id})">🚀 Auto-Apply to This Job</button>`;

        const linksWrap = document.getElementById('modalJobLinks');
        linksWrap.innerHTML = `
          ${applyBtn}
          <a href="/api/jobs/${j.id}/motivation_letter/download" target="_blank" download class="btn btn-sm" style="background:rgba(59,130,246,0.18);border:1px solid rgba(59,130,246,0.4);color:#60a5fa;display:inline-flex;align-items:center;gap:5px" title="Download dedicated company motivation letter (.docx)">
            <span>📝</span> Motivation Letter (.docx)
          </a>
          ${j.job_url ? `<a href="${esc(j.job_url)}" target="_blank" class="btn btn-sm">Original Post ↗</a>` : ''}
          ${j.application_url ? `<a href="${esc(j.application_url)}" target="_blank" class="btn btn-sm btn-cyan">Apply URL ↗</a>` : ''}
        `;

        // Motivation Letter (.docx) Preview
        const btnCover = document.getElementById('btnDownloadModalCoverLetter');
        if (btnCover) btnCover.href = `/api/jobs/${j.id}/motivation_letter/download`;

        const prevCoverEl = document.getElementById('mJobMotivationLetterPreview');
        if (prevCoverEl) {
          prevCoverEl.textContent = 'Generating tailored company letter preview... ⏳';
          apiGet(`/api/jobs/${j.id}/motivation_letter/preview`).then(res => {
            if (res && res.ok && res.letter) {
              const l = res.letter;
              prevCoverEl.textContent = `${l.salutation}\n\n${(l.paragraphs || []).join('\n\n')}\n\n${l.closing}\n${l.candidate_name}`;
            } else {
              prevCoverEl.textContent = j.pitch_en || j.pitch_fr || 'Company-dedicated motivation letter will be generated on apply.';
            }
          }).catch(() => {
            prevCoverEl.textContent = j.pitch_en || j.pitch_fr || 'Company-dedicated motivation letter will be generated on apply.';
          });
        }

        // Email thread
        const emailsWrap = document.getElementById('mJobEmails');
        const evts = j.email_events || [];
        if (!evts.length) {
          emailsWrap.innerHTML = '<div style="color:var(--text-faint);font-size:12px;padding:12px 0">No email outreach records for this job.</div>';
        } else {
          emailsWrap.innerHTML = evts.map(e => `
            <div class="timeline-item">
              <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-muted);margin-bottom:4px">
                <span style="font-weight:700">${esc(e.direction)} · ${esc(e.email_type)}</span>
                <span>${fmt(e.created_at)}</span>
              </div>
              <div style="font-weight:600;font-size:13px;margin-bottom:4px">${esc(e.subject)}</div>
              <div class="code-block" style="max-height:140px">${esc(e.body || '')}</div>
            </div>
          `).join('');
        }

        document.getElementById('jobModal').classList.add('open');
      } catch (err) {
        showToast('Error opening job details: ' + err.message, 'error');
      }
    }

    async function applySingleJob(jobId) {
      const btn = document.getElementById('btnModalApplySingle');
      if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Applying...';
      }
      showToast('Dispatching application...', 'normal');
      try {
        const res = await apiSend(`/api/jobs/${jobId}/apply`, 'POST');
        if (res.applied) {
          showToast(`Application successfully sent! Method: ${res.method}`, 'success');
        } else {
          const toastType = (res.status === 'sent' || res.status === 'submitted') ? 'success' : 'warn';
          showToast(`Application status: ${res.status}${res.error ? ' (' + res.error + ')' : ''}`, toastType);
        }
        await openJobModal(jobId);
        loadJobs();
      } catch (err) {
        showToast('Apply failed: ' + err.message, 'error');
        if (btn) {
          btn.disabled = false;
          btn.textContent = '🚀 Auto-Apply to This Job';
        }
      }
    }

    function closeJobModal() {
      document.getElementById('jobModal').classList.remove('open');
    }

    async function saveJobStage() {
      if (!currentActiveJob) return;
      const stage = document.getElementById('modalJobStageSelect').value;
      try {
        await apiSend(`/api/jobs/${currentActiveJob.id}/stage`, 'PATCH', { stage });
        currentActiveJob.stage = stage;
        document.getElementById('modalJobStage').textContent = stage;
        showToast('Job stage updated!', 'success');
        loadJobs();
      } catch (e) {
        showToast('Error updating stage: ' + e.message, 'error');
      }
    }

    async function triggerJobPipeline() {
      const btn = document.getElementById('btnRunJobPipeline');
      const icon = document.getElementById('jobPipelineIcon');
      btn.disabled = true;
      icon.textContent = '⏳';
      showToast('Starting Job Discovery Pipeline...', 'normal');
      try {
        const res = await apiSend('/api/pipeline/run', 'POST');
        showToast(res.message || 'Pipeline started!', 'success');
        switchJobsTab('live');
        loadLivePipelineMonitor();
      } catch (e) {
        showToast('Pipeline trigger error: ' + e.message, 'error');
      } finally {
        btn.disabled = false;
        icon.textContent = '⚡';
      }
    }


    async function triggerApplyPendingMatches() {
      const btn = document.getElementById('btnApplyMatches');
      const icon = document.getElementById('applyMatchesIcon');
      if (btn) btn.disabled = true;
      if (icon) icon.textContent = '⏳';
      showToast('Starting batch auto-application for all matching jobs...', 'normal');
      try {
        const res = await apiSend('/api/actions/apply_pending_matches', 'POST');
        showToast(res.message || 'Auto-apply started!', 'success');
        const poll = setInterval(async () => {
          const st = await apiGet('/api/actions/apply_status');
          if (st.message) {
            document.getElementById('liveStatusText').textContent = st.message.slice(0, 30);
          }
          if (!st.running) {
            clearInterval(poll);
            if (btn) btn.disabled = false;
            if (icon) icon.textContent = '🚀';
            document.getElementById('liveStatusText').textContent = 'Live Sync';
            showToast(st.message || 'Auto-apply complete!', 'success');
            refreshCurrentView();
          }
        }, 3000);
      } catch (e) {
        showToast('Auto-apply error: ' + e.message, 'error');
        if (btn) btn.disabled = false;
        if (icon) icon.textContent = '🚀';
      }
    }

    async function triggerCheckInbox() {
      showToast('Checking email inbox for responses...', 'normal');
      try {
        const res = await apiSend('/api/actions/check_inbox', 'POST');
        showToast('Inbox checked! Matched: ' + (res.summary?.matched ?? 0), 'success');
        refreshCurrentView();
      } catch (e) {
        showToast('Inbox check error: ' + e.message, 'error');
      }
    }

    function toggleExportMenu() {
      const drop = document.getElementById('exportDropdown');
      if (drop) {
        drop.style.display = drop.style.display === 'block' ? 'none' : 'block';
      }
    function triggerBatchApply() {
      return triggerApplyPendingMatches();
    }

    let _cachedInbox = [];
    let _cachedOutbound = [];
    let _activeInboxModalItem = null;
    let _activeOutboundModalItem = null;

    function setInboxFilter(filter) {
      const select = document.getElementById('inboxStatusFilter');
      if (select) select.value = filter;
      const chips = [
        { id: 'inboxChipAll', val: '' },
        { id: 'inboxChipInterview', val: 'interview' },
        { id: 'inboxChipMatched', val: 'matched' },
        { id: 'inboxChipRejection', val: 'rejection' },
        { id: 'inboxChipBounce', val: 'bounce' },
      ];
      chips.forEach(c => {
        const btn = document.getElementById(c.id);
        if (btn) {
          if (c.val === filter) {
            btn.classList.add('btn-cyan');
          } else {
            btn.classList.remove('btn-cyan');
          }
        }
      });
      loadInbox();
    }

    async function loadInbox() {
      const st = encodeURIComponent(document.getElementById('inboxStatusFilter')?.value || '');
      const search = encodeURIComponent(document.getElementById('inboxSearchInput')?.value || '');
      let url = `/api/inbox?limit=250`;
      if (st) url += `&status_filter=${st}`;
      if (search) url += `&search=${search}`;

      apiGet('/api/inbox/stats').then(stats => {
        if (!stats) return;
        if (document.getElementById('inboxKpiTotal')) document.getElementById('inboxKpiTotal').textContent = stats.total_replies ?? 0;
        if (document.getElementById('inboxKpiInterviews')) document.getElementById('inboxKpiInterviews').textContent = stats.interviews ?? 0;
        if (document.getElementById('inboxKpiAction')) document.getElementById('inboxKpiAction').textContent = stats.action_required ?? 0;
        if (document.getElementById('inboxKpiRejections')) document.getElementById('inboxKpiRejections').textContent = stats.rejections ?? 0;
        if (document.getElementById('inboxKpiBounces')) document.getElementById('inboxKpiBounces').textContent = stats.bounces ?? 0;
      }).catch(() => {});

      const rows = await apiGet(url);
      _cachedInbox = Array.isArray(rows) ? rows : [];
      
      const tbody = document.getElementById('inboxTableBody');
      if (!tbody) return;

      if (!_cachedInbox.length) {
        tbody.innerHTML = `
          <tr>
            <td colspan="6" style="padding:32px 16px;text-align:center;color:var(--text-faint)">
              <div style="font-size:24px;margin-bottom:6px">📬</div>
              <div style="font-size:14px;font-weight:600;color:var(--text)">No replies found matching this filter</div>
              <div style="font-size:12px;margin-top:4px">When hiring managers or recruiters respond to your applications, their emails appear here in real-time.</div>
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = _cachedInbox.map((x, idx) => {
        const intent = x.intent || 'reply';
        let intentPill = `<span class="pill pill-stage-discovered">💬 Recruiter Reply</span>`;
        if (intent === 'interview_request') {
          intentPill = `<span class="pill pill-stage-deal_won" style="font-weight:700">🎯 Interview Request</span>`;
        } else if (intent === 'offer') {
          intentPill = `<span class="pill pill-stage-deal_won" style="background:rgba(234,179,8,0.2);color:#fde047;font-weight:700">🏆 Job Offer!</span>`;
        } else if (intent === 'rejection') {
          intentPill = `<span class="pill pill-stage-lost">⛔ Rejection</span>`;
        } else if (intent === 'bounce' || x.status === 'bounced') {
          intentPill = `<span class="pill" style="background:rgba(245,158,11,0.2);color:#fbbf24">⚠️ Mailer Bounce</span>`;
        } else if (intent === 'screening' || intent === 'info_request') {
          intentPill = `<span class="pill" style="background:rgba(56,189,248,0.2);color:#38bdf8">📝 Info Needed</span>`;
        }

        const scoreBadge = x.match_score ? `<span class="pill" style="font-size:10px;padding:1px 6px;background:rgba(168,85,247,0.15);color:var(--purple-light)">${x.match_score}%</span>` : '';
        const jobLink = x.job_id ? `<a href="javascript:void(0)" onclick="openJobModal(${x.job_id})" style="font-weight:600;color:var(--text);text-decoration:none" title="Open Job Posting Details">${esc(x.job_title || 'View Role')}</a>` : `<span style="font-weight:600">${esc(x.job_title || 'General Inbound')}</span>`;

        return `
          <tr>
            <td style="font-size:12px;white-space:nowrap;color:var(--text-muted)">
              <div>${fmt(x.created_at)}</div>
            </td>
            <td>
              <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">
                <strong style="color:var(--cyan-light);font-size:13px">${esc(x.company || 'Direct Contact')}</strong>
                ${scoreBadge}
              </div>
              <div style="font-size:12px">${jobLink}</div>
            </td>
            <td style="font-size:12px">
              <span style="font-family:'JetBrains Mono';font-size:11px;color:var(--text)" title="${esc(x.sender_email || '')}">${esc(x.sender_email || '—')}</span>
            </td>
            <td>${intentPill}</td>
            <td>
              <div style="font-weight:600;font-size:13px;color:var(--text);margin-bottom:2px">${esc(x.subject || 'No Subject')}</div>
              <div style="font-size:12px;color:var(--text-muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(x.snippet || x.reason || '')}</div>
            </td>
            <td style="text-align:right;white-space:nowrap">
              <div style="display:inline-flex;gap:6px">
                <button class="btn btn-sm btn-cyan" onclick="openInboxModal(${idx})" title="Read incoming email and AI action points">
                  <span>👁️</span> Read
                </button>
                ${x.job_id ? `<button class="btn btn-sm" onclick="openJobModal(${x.job_id})" title="View original job posting"><span>💼</span></button>` : ''}
              </div>
            </td>
          </tr>
        `;
      }).join('');
    }

    function openInboxModal(idx) {
      const item = _cachedInbox[idx];
      if (!item) return;
      _activeInboxModalItem = item;

      const intent = item.intent || 'reply';
      const badge = document.getElementById('inboxModalIntentBadge');
      if (badge) {
        badge.className = intent === 'interview_request' || intent === 'offer' ? 'pill pill-stage-deal_won' : (intent === 'rejection' ? 'pill pill-stage-lost' : 'pill pill-stage-discovered');
        badge.textContent = intent === 'interview_request' ? '🎯 Interview Request' : (intent === 'offer' ? '🏆 Job Offer!' : (intent === 'rejection' ? '⛔ Rejection' : (intent === 'bounce' ? '⚠️ Delivery Failure' : '💬 Recruiter Reply')));
      }

      if (document.getElementById('inboxModalDate')) document.getElementById('inboxModalDate').textContent = fmt(item.created_at);
      if (document.getElementById('inboxModalSubject')) document.getElementById('inboxModalSubject').textContent = item.subject || 'Recruiter Response';
      if (document.getElementById('inboxModalSender')) document.getElementById('inboxModalSender').textContent = item.sender_email || 'Direct Recruiter';
      if (document.getElementById('inboxModalTargetJob')) document.getElementById('inboxModalTargetJob').textContent = `${item.company} · ${item.job_title}`;

      const summaryCard = document.getElementById('inboxModalSummaryCard');
      const summaryText = document.getElementById('inboxModalSummaryText');
      if (summaryCard && summaryText) {
        if (intent === 'interview_request' || item.interview_notes) {
          summaryCard.style.display = 'block';
          summaryCard.style.background = 'rgba(16,185,129,0.1)';
          summaryCard.style.borderColor = 'rgba(16,185,129,0.3)';
          summaryText.innerHTML = `<strong>Great news!</strong> The employer wants to schedule a discussion. Check their message below and reply promptly.`;
        } else if (intent === 'rejection') {
          summaryCard.style.display = 'block';
          summaryCard.style.background = 'rgba(239,68,68,0.1)';
          summaryCard.style.borderColor = 'rgba(239,68,68,0.3)';
          summaryText.innerHTML = `The employer has decided not to proceed with this role. Your stage has been updated automatically.`;
        } else if (intent === 'bounce') {
          summaryCard.style.display = 'block';
          summaryCard.style.background = 'rgba(245,158,11,0.1)';
          summaryCard.style.borderColor = 'rgba(245,158,11,0.3)';
          summaryText.innerHTML = `Delivery failure from mail server. Recruiter email has been automatically suppressed to protect sender reputation.`;
        } else {
          summaryCard.style.display = 'none';
        }
      }

      if (document.getElementById('inboxModalBodyText')) {
        document.getElementById('inboxModalBodyText').textContent = item.body || item.snippet || item.reason || 'No email body text available.';
      }

      const mailtoBtn = document.getElementById('inboxModalReplyMailto');
      if (mailtoBtn && item.sender_email) {
        const replySub = encodeURIComponent(item.subject?.startsWith('Re:') ? item.subject : `Re: ${item.subject || ''}`);
        mailtoBtn.href = `mailto:${encodeURIComponent(item.sender_email)}?subject=${replySub}`;
      }

      const modal = document.getElementById('inboxMessageModal');
      if (modal) modal.style.display = 'flex';
    }

    function closeInboxModal() {
      const modal = document.getElementById('inboxMessageModal');
      if (modal) modal.style.display = 'none';
      _activeInboxModalItem = null;
    }

    function copyInboxSender() {
      if (_activeInboxModalItem && _activeInboxModalItem.sender_email) {
        navigator.clipboard.writeText(_activeInboxModalItem.sender_email);
        showToast(`Copied ${_activeInboxModalItem.sender_email} to clipboard!`, 'success');
      }
    }

    function setOutboundFilter(filter) {
      const select = document.getElementById('outboundStatusFilter');
      if (select) select.value = filter;
      const chips = [
        { id: 'outboundChipAll', val: '' },
        { id: 'outboundChipSent', val: 'sent' },
        { id: 'outboundChipDraft', val: 'draft' },
        { id: 'outboundChipEmail', val: 'email' },
        { id: 'outboundChipPortal', val: 'portal' },
        { id: 'outboundChipBlocked', val: 'blocked' },
      ];
      chips.forEach(c => {
        const btn = document.getElementById(c.id);
        if (btn) {
          if (c.val === filter) {
            btn.classList.add('btn-cyan');
          } else {
            btn.classList.remove('btn-cyan');
          }
        }
      });
      loadOutbound();
    }

    async function loadOutbound() {
      const st = encodeURIComponent(document.getElementById('outboundStatusFilter')?.value || '');
      const search = encodeURIComponent(document.getElementById('outboundSearchInput')?.value || '');
      let url = `/api/outbound?limit=250`;
      if (st) url += `&status_filter=${st}`;
      if (search) url += `&search=${search}`;

      apiGet('/api/outbound/stats').then(stats => {
        if (!stats) return;
        if (document.getElementById('outboundKpiTotal')) document.getElementById('outboundKpiTotal').textContent = stats.total_applications ?? 0;
        if (document.getElementById('outboundKpiSent')) document.getElementById('outboundKpiSent').textContent = stats.sent_emails ?? 0;
        if (document.getElementById('outboundKpiWeb')) document.getElementById('outboundKpiWeb').textContent = stats.web_submitted ?? 0;
        if (document.getElementById('outboundKpiDrafts')) document.getElementById('outboundKpiDrafts').textContent = stats.drafts_ready ?? 0;
        if (document.getElementById('outboundKpiBlocked')) document.getElementById('outboundKpiBlocked').textContent = stats.blocked ?? 0;
      }).catch(() => {});

      const rows = await apiGet(url);
      _cachedOutbound = Array.isArray(rows) ? rows : [];

      const tbody = document.getElementById('outboundTableBody');
      if (!tbody) return;

      if (!_cachedOutbound.length) {
        tbody.innerHTML = `
          <tr>
            <td colspan="8" style="padding:32px 16px;text-align:center;color:var(--text-faint)">
              <div style="font-size:24px;margin-bottom:6px">📤</div>
              <div style="font-size:14px;font-weight:600;color:var(--text)">No outreach records found</div>
              <div style="font-size:12px;margin-top:4px">When applications and pitches are dispatched by email or browser automation, they appear here with full audit evidence.</div>
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = _cachedOutbound.map((x, idx) => {
        let stBadge = `<span class="pill pill-stage-discovered">${esc(x.status)}</span>`;
        if (x.status === 'sent' || x.status === 'submitted') {
          stBadge = `<span class="pill pill-stage-deal_won" style="font-weight:700">✓ ${x.status === 'submitted' ? 'Submitted' : 'Sent'}</span>`;
        } else if (x.status === 'draft') {
          stBadge = `<span class="pill" style="background:rgba(56,189,248,0.15);color:var(--cyan-light);font-weight:600">📝 Draft Ready</span>`;
        } else if (x.status === 'blocked' || x.status === 'recruiter_email_blocked') {
          stBadge = `<span class="pill" style="background:rgba(245,158,11,0.2);color:#fbbf24">⚠️ Blocked</span>`;
        } else if (x.status === 'failed') {
          stBadge = `<span class="pill pill-stage-lost">❌ Failed</span>`;
        }

        let chanBadge = `<span class="pill" style="background:#1e293b;border:1px solid var(--border)">${esc(x.channel_label || x.channel || 'Outbound')}</span>`;
        if (x.channel === 'email') {
          chanBadge = `<span class="pill" style="background:rgba(56,189,248,0.15);color:var(--cyan-light);border:1px solid rgba(56,189,248,0.3)">✉️ Direct Email</span>`;
        } else if (x.channel === 'linkedin') {
          chanBadge = `<span class="pill" style="background:rgba(10,102,194,0.2);color:#60a5fa;border:1px solid rgba(10,102,194,0.4)">💼 LinkedIn</span>`;
        } else if (x.channel === 'indeed') {
          chanBadge = `<span class="pill" style="background:rgba(37,99,235,0.2);color:#93c5fd;border:1px solid rgba(37,99,235,0.4)">🔵 Indeed</span>`;
        } else if (x.channel === 'ats' || x.channel === 'web_portal') {
          chanBadge = `<span class="pill" style="background:rgba(168,85,247,0.15);color:var(--purple-light);border:1px solid rgba(168,85,247,0.3)">🌐 Web Portal</span>`;
        }

        const scoreBadge = x.match_score ? `<span class="pill" style="font-size:10px;padding:1px 6px;background:rgba(168,85,247,0.15);color:var(--purple-light)">${x.match_score}%</span>` : '';
        const jobLink = x.job_id ? `<a href="javascript:void(0)" onclick="openJobModal(${x.job_id})" style="font-weight:600;color:var(--text);text-decoration:none" title="Open Job Details">${esc(x.job_title || 'Position')}</a>` : `<span style="font-weight:600">${esc(x.job_title || 'Position')}</span>`;

        const isUrl = x.recipient && (x.recipient.startsWith('http://') || x.recipient.startsWith('https://'));
        const recipRender = isUrl
          ? `<a href="${esc(x.recipient)}" target="_blank" style="color:var(--cyan-light);text-decoration:none;font-size:11px" title="${esc(x.recipient)}">${esc(x.recipient.length > 25 ? x.recipient.substring(0, 25) + '...' : x.recipient)} ↗</a>`
          : `<span style="font-family:'JetBrains Mono';font-size:11px;color:var(--text)" title="${esc(x.recipient || '')}">${esc(x.recipient || '—')}</span>`;

        const attachHtml = `
          <div style="display:flex;align-items:center;gap:4px">
            <span class="pill" style="font-size:10px;padding:1px 5px;background:rgba(16,185,129,0.15);color:var(--good)" title="CV PDF Attached">📄 CV</span>
            <span class="pill" style="font-size:10px;padding:1px 5px;background:rgba(59,130,246,0.15);color:#93c5fd" title="Tailored Motivation Letter (.docx) Generated">📎 Letter</span>
          </div>
        `;

        return `
          <tr>
            <td style="font-size:12px;white-space:nowrap;color:var(--text-muted)">
              <div>${fmt(x.sent_at || x.created_at)}</div>
            </td>
            <td>
              <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">
                <strong style="color:var(--cyan-light);font-size:13px">${esc(x.company || 'Hiring Company')}</strong>
                ${scoreBadge}
              </div>
              <div style="font-size:12px">${jobLink}</div>
            </td>
            <td>${chanBadge}</td>
            <td>${recipRender}</td>
            <td>${stBadge}</td>
            <td>${attachHtml}</td>
            <td>
              <div style="font-weight:600;font-size:12px;color:var(--text);margin-bottom:2px">${esc(x.subject || 'Application')}</div>
              <div style="font-size:11px;color:var(--text-muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(x.failure_reason || x.snippet || '')}</div>
            </td>
            <td style="text-align:right;white-space:nowrap">
              <div style="display:inline-flex;gap:6px">
                <button class="btn btn-sm btn-cyan" onclick="openOutboundModal(${idx})" title="Preview full application and motivation letter">
                  <span>👁️</span> View
                </button>
                ${x.job_id && (x.status === 'draft' || x.status === 'failed' || x.status === 'blocked') ? `
                  <button class="btn btn-sm" onclick="resendJobApplication(${x.job_id})" title="Dispatch application now">
                    <span>⚡</span>
                  </button>
                ` : ''}
              </div>
            </td>
          </tr>
        `;
      }).join('');

      // Load suppressions
      const sups = await apiGet('/api/suppressions');
      document.getElementById('suppressionList').innerHTML = sups.map(s => `
        <span class="pill" style="background:#1e293b;border:1px solid var(--border);display:inline-flex;align-items:center;gap:6px">
          ${esc(s.email || s.domain)} (${esc(s.reason)})
          <button onclick="removeSuppression(${s.id})" style="background:none;border:none;color:var(--text-faint);cursor:pointer;font-size:14px;padding:0 2px;line-height:1" title="Remove suppression">×</button>
        </span>
      `).join('') || '<span style="color:var(--text-faint);font-size:12px">No active suppressions</span>';
    }

    async function addSuppression() {
      const email = document.getElementById('supEmailInput').value.trim() || null;
      const domain = document.getElementById('supDomainInput').value.trim() || null;
      const reason = document.getElementById('supReasonInput').value.trim() || 'manual';
      if (!email && !domain) {
        showToast('Please provide an email or domain', 'error');
        return;
      }
      try {
        await apiSend('/api/suppressions', 'POST', { email, domain, reason });
        document.getElementById('supEmailInput').value = '';
        document.getElementById('supDomainInput').value = '';
        showToast('Suppression added!', 'success');
        loadOutbound();
      } catch (e) {
        showToast('Failed to add suppression: ' + e.message, 'error');
      }
    }

    async function removeSuppression(id) {
      try {
        await apiSend(`/api/suppressions/${id}`, 'DELETE');
        showToast('Suppression removed', 'success');
        loadOutbound();
      } catch (e) {
        showToast('Failed to remove: ' + e.message, 'error');
      }
    }

    function openOutboundModal(idx) {
      const item = _cachedOutbound[idx];
      if (!item) return;
      _activeOutboundModalItem = item;

      const stBadge = document.getElementById('outboundModalStatusBadge');
      if (stBadge) {
        stBadge.className = (item.status === 'sent' || item.status === 'submitted') ? 'pill pill-stage-deal_won' : (item.status === 'failed' ? 'pill pill-stage-lost' : 'pill pill-stage-discovered');
        stBadge.textContent = item.status === 'sent' ? '✓ Sent' : (item.status === 'submitted' ? '✓ Submitted' : (item.status === 'draft' ? '📝 Draft Ready' : item.status));
      }

      if (document.getElementById('outboundModalChannelBadge')) {
        document.getElementById('outboundModalChannelBadge').textContent = item.channel_label || item.channel || 'Outbound';
      }
      if (document.getElementById('outboundModalMatchBadge')) {
        document.getElementById('outboundModalMatchBadge').textContent = item.match_score ? `${item.match_score}% Match` : 'Evaluated Match';
      }
      if (document.getElementById('outboundModalRoleTitle')) {
        document.getElementById('outboundModalRoleTitle').textContent = `${item.job_title} at ${item.company}`;
      }
      if (document.getElementById('outboundModalRecipient')) {
        document.getElementById('outboundModalRecipient').textContent = item.recipient || 'Direct Hiring Team';
      }
      if (document.getElementById('outboundModalTimestamp')) {
        document.getElementById('outboundModalTimestamp').textContent = fmt(item.sent_at || item.created_at);
      }

      const errBanner = document.getElementById('outboundModalErrorBanner');
      const errText = document.getElementById('outboundModalErrorText');
      if (errBanner && errText) {
        if (item.failure_reason) {
          errBanner.style.display = 'block';
          errText.textContent = item.failure_reason;
        } else {
          errBanner.style.display = 'none';
        }
      }

      if (document.getElementById('outboundModalSubject')) {
        document.getElementById('outboundModalSubject').textContent = item.subject || 'Application';
      }
      if (document.getElementById('outboundModalBodyText')) {
        document.getElementById('outboundModalBodyText').textContent = item.full_body || item.snippet || 'No application body text was generated.';
      }

      const openJobBtn = document.getElementById('btnOutboundModalOpenJob');
      if (openJobBtn) {
        if (item.job_url || item.application_url) {
          openJobBtn.style.display = 'inline-flex';
          openJobBtn.href = item.application_url || item.job_url;
        } else {
          openJobBtn.style.display = 'none';
        }
      }

      const resendBtn = document.getElementById('btnOutboundModalResend');
      if (resendBtn) {
        resendBtn.style.display = (item.status === 'draft' || item.status === 'failed' || item.status === 'blocked') ? 'inline-flex' : 'none';
      }

      const modal = document.getElementById('outboundDetailModal');
      if (modal) modal.style.display = 'flex';
    }

    function closeOutboundModal() {
      const modal = document.getElementById('outboundDetailModal');
      if (modal) modal.style.display = 'none';
      _activeOutboundModalItem = null;
    }

    async function retryOutboundModalApplication() {
      if (!_activeOutboundModalItem || !_activeOutboundModalItem.job_id) {
        showToast('No job associated with this record', 'error');
        return;
      }
      await resendJobApplication(_activeOutboundModalItem.job_id);
      closeOutboundModal();
    }

    async function resendJobApplication(jobId) {
      showToast('Dispatching application...', 'normal');
      try {
        const res = await apiSend('/api/actions/resend_application', 'POST', { job_id: jobId });
        if (res.ok) {
          showToast('Application dispatched successfully!', 'success');
          loadOutbound();
          if (typeof loadJobs === 'function') loadJobs();
        } else {
          showToast(res.error || 'Failed to dispatch application', 'error');
        }
      } catch (err) {
        showToast('Application error: ' + err.message, 'error');
      }
    }

    async function loadPipelineRuns() {
      // Background helper for runs data
    }

    async function loadSystemInfo() {
      const [config, summary] = await Promise.all([apiGet('/api/config'), apiGet('/api/summary')]);
      document.getElementById('systemConfigJson').textContent = JSON.stringify(config, null, 2);

      const provs = summary.providers || [];
      document.getElementById('systemProviders').innerHTML = provs.map(p => `
        <div class="kpi-card" style="padding:12px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span style="font-weight:700">${esc(p.provider)}</span>
            <span class="pill ${p.status === 'ready' ? 'pill-stage-deal_won' : 'pill-stage-lost'}">${esc(p.status)}</span>
          </div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:4px">
            Failures: ${p.failure_count} · Cooldown: ${fmt(p.cooldown_until)}<br>
            ${esc(p.last_error || 'Operating normally')}
          </div>
        </div>
      `).join('') || '<div style="color:var(--text-faint)">No AI providers registered.</div>';
    }

    // =========================================================================
    // CANDIDATE PROFILE & KEYWORD MANAGEMENT
    // =========================================================================
    let allProfilesList = [];
    let activeProfileId = null;
    let currentProfileData = {
      target_titles: [],
      core_stack: [],
      keywords: [],
      negative_keywords: [],
      target_locations: []
    };

    async function loadProfiles() {
      try {
        allProfilesList = await apiGet('/api/profiles');
        const select = document.getElementById('profileSelect');
        select.innerHTML = allProfilesList.map(p => `
          <option value="${p.id}">${esc(p.name)} ${p.is_active ? '★ (Active)' : ''}</option>
        `).join('');

        const active = allProfilesList.find(p => p.is_active);
        if (active) {
          activeProfileId = active.id;
          selectProfile(active.id);
        } else if (allProfilesList.length > 0) {
          selectProfile(allProfilesList[0].id);
        }
      } catch (e) {
        showToast('Error loading candidate profiles: ' + e.message, 'error');
      }
    }

    function selectProfile(profileId) {
      const p = allProfilesList.find(x => x.id == profileId);
      if (!p) return;
      document.getElementById('profileSelect').value = p.id;
      document.getElementById('profName').value = p.name || '';
      document.getElementById('profHeadline').value = p.headline || '';
      document.getElementById('profLocation').value = p.location || '';
      document.getElementById('profExpYears').value = p.experience_years ?? 4;
      document.getElementById('profVisa').value = p.visa_notes || '';
      document.getElementById('profResumeText').value = p.resume_text || '';
      document.getElementById('profRateHourly').value = p.freelance_hourly_usd ?? 50;
      document.getElementById('profRateDaily').value = p.freelance_daily_eur ?? 400;
      document.getElementById('profCalendarUrl').value = p.calendar_url || '';
      if (document.getElementById('profDegreeLevel')) {
        document.getElementById('profDegreeLevel').value = p.degree_level || 'bachelor';
      }
      if (document.getElementById('profTargetExpLevel')) {
        document.getElementById('profTargetExpLevel').value = p.target_experience_level || 'entry';
      }

      // Active state badge & button
      const badge = document.getElementById('profileActiveBadge');
      const btnAct = document.getElementById('btnActivateProfile');
      if (p.is_active) {
        badge.style.display = 'inline-block';
        btnAct.style.display = 'none';
      } else {
        badge.style.display = 'none';
        btnAct.style.display = 'inline-block';
      }

      // Real-time CV Status Banner update
      const cvStatusIcon = document.getElementById('cvStatusIcon');
      const cvStatusTitle = document.getElementById('cvStatusTitle');
      const cvStatusSub = document.getElementById('cvStatusSub');
      const btnDownloadBanner = document.getElementById('btnDownloadCvBanner');

      if (cvStatusIcon && cvStatusTitle && cvStatusSub) {
        if (p.resume_path) {
          const fn = p.resume_path.split(/[\\/]/).pop();
          cvStatusIcon.textContent = '📄';
          cvStatusTitle.textContent = `Active CV: ${fn}`;
          cvStatusSub.textContent = `PDF file ready. Attached automatically to applications & outbound outreach.`;
          if (btnDownloadBanner) btnDownloadBanner.style.display = 'inline-flex';
        } else if (p.resume_text && p.resume_text.trim().length > 30) {
          cvStatusIcon.textContent = '📝';
          cvStatusTitle.textContent = `Resume text configured (${p.resume_text.trim().length} chars)`;
          cvStatusSub.textContent = `Click "⚡ Generate ATS PDF" to create your ATS-compliant PDF resume from this profile.`;
          if (btnDownloadBanner) btnDownloadBanner.style.display = 'none';
        } else {
          cvStatusIcon.textContent = '📁';
          cvStatusTitle.textContent = `No CV uploaded or generated yet`;
          cvStatusSub.textContent = `Upload your PDF or generate one from this profile to activate automatic job applications.`;
          if (btnDownloadBanner) btnDownloadBanner.style.display = 'none';
        }
      }

      currentProfileData = {
        id: p.id,
        target_titles: Array.isArray(p.target_titles) ? [...p.target_titles] : [],
        core_stack: Array.isArray(p.core_stack) ? [...p.core_stack] : [],
        keywords: Array.isArray(p.keywords) ? [...p.keywords] : [],
        negative_keywords: Array.isArray(p.negative_keywords) ? [...p.negative_keywords] : [],
        target_locations: Array.isArray(p.target_locations) ? [...p.target_locations] : []
      };

      renderTags('target_titles', 'listTitles');
      renderTags('core_stack', 'listStack');
      renderTags('keywords', 'listKeywords');
      renderTags('negative_keywords', 'listNegatives');
      renderTags('target_locations', 'listLocations');
    }

    function renderTags(field, containerId) {
      const container = document.getElementById(containerId);
      if (!container) return;
      const tags = currentProfileData[field] || [];
      container.innerHTML = tags.map((t, idx) => `
        <span class="pill" style="background:rgba(255,255,255,0.08);border:1px solid var(--border);padding:3px 8px;display:inline-flex;align-items:center;gap:6px">
          <span>${esc(t)}</span>
          <span onclick="removeTag('${field}', '${containerId}', ${idx})" style="cursor:pointer;color:var(--bad);font-weight:bold;font-size:12px">×</span>
        </span>
      `).join('');
    }

    function addTag(field, inputId, containerId) {
      const input = document.getElementById(inputId);
      const val = input.value.trim();
      if (!val) return;
      if (!currentProfileData[field]) currentProfileData[field] = [];
      if (!currentProfileData[field].includes(val)) {
        currentProfileData[field].push(val);
        renderTags(field, containerId);
      }
      input.value = '';
      input.focus();
    }

    function removeTag(field, containerId, index) {
      if (currentProfileData[field]) {
        currentProfileData[field].splice(index, 1);
        renderTags(field, containerId);
      }
    }

    async function saveCurrentProfile() {
      const sel = document.getElementById('profileSelect');
      const profileId = sel.value;
      if (!profileId) {
        showToast('Please select or create a profile first.', 'error');
        return;
      }

      const payload = {
        name: document.getElementById('profName').value.trim() || 'Untitled Profile',
        headline: document.getElementById('profHeadline').value.trim(),
        location: document.getElementById('profLocation').value.trim(),
        experience_years: parseInt(document.getElementById('profExpYears').value, 10) || 0,
        visa_notes: document.getElementById('profVisa').value.trim(),
        resume_text: document.getElementById('profResumeText').value.trim(),
        freelance_hourly_usd: parseFloat(document.getElementById('profRateHourly').value) || 0,
        freelance_daily_eur: parseFloat(document.getElementById('profRateDaily').value) || 0,
        freelance_currency: document.getElementById('profCurrency').value || 'EUR',
        calendar_url: (document.getElementById('profCalendarUrl')?.value || '').trim(),
        degree_level: (document.getElementById('profDegreeLevel')?.value || 'bachelor').trim(),
        target_experience_level: (document.getElementById('profTargetExpLevel')?.value || 'entry').trim(),
        target_titles: currentProfileData.target_titles || [],
        core_stack: currentProfileData.core_stack || [],
        keywords: currentProfileData.keywords || [],
        negative_keywords: currentProfileData.negative_keywords || [],
        target_locations: currentProfileData.target_locations || []
      };

      try {
        const res = await apiSend(`/api/profiles/${profileId}`, 'PUT', payload);
        showToast('Candidate profile updated successfully! ✓', 'success');
        await loadProfiles();
        selectProfile(res.id);
        loadActiveMatrixPreview();
      } catch (e) {
        showToast('Failed to update profile: ' + e.message, 'error');
      }
    }

    async function createNewProfile() {
      const name = prompt('Enter a name for the new profile (e.g. AI & Python Engineer):');
      if (!name) return;
      try {
        const payload = {
          name: name.trim(),
          headline: `${name.trim()} Specialist`,
          target_titles: [name.trim()],
          core_stack: ['Python', 'Docker'],
          keywords: ['rest api', 'backend'],
          target_locations: ['Remote', 'Morocco', 'France'],
          experience_years: 3
        };
        const created = await apiSend('/api/profiles', 'POST', payload);
        showToast(`Profile "${created.name}" created!`, 'success');
        await loadProfiles();
        selectProfile(created.id);
      } catch (e) {
        showToast('Error creating profile: ' + e.message, 'error');
      }
    }

    async function cloneCurrentProfile() {
      const sel = document.getElementById('profileSelect');
      const profileId = sel.value;
      const current = allProfilesList.find(x => x.id == profileId);
      if (!current) return;

      const newName = prompt(`Enter name for clone:`, `${current.name} (Copy)`);
      if (!newName) return;

      const payload = {
        name: newName,
        headline: current.headline,
        location: current.location,
        experience_years: current.experience_years,
        visa_notes: current.visa_notes,
        resume_text: current.resume_text,
        target_titles: current.target_titles,
        core_stack: current.core_stack,
        keywords: current.keywords,
        negative_keywords: current.negative_keywords,
        target_locations: current.target_locations,
        freelance_hourly_rate: current.freelance_hourly_rate,
        freelance_daily_rate: current.freelance_daily_rate,
        freelance_currency: current.freelance_currency,
        calendar_url: current.calendar_url || ''
      };

      try {
        const cloned = await apiSend('/api/profiles', 'POST', payload);
        showToast(`Cloned profile as "${cloned.name}"!`, 'success');
        await loadProfiles();
        selectProfile(cloned.id);
      } catch (e) {
        showToast('Error cloning profile: ' + e.message, 'error');
      }
    }

    async function activateCurrentProfile() {
      const sel = document.getElementById('profileSelect');
      const profileId = sel.value;
      if (!profileId) return;

      try {
        await apiSend(`/api/profiles/${profileId}/activate`, 'POST');
        showToast('Active candidate profile switched! All scrapers and matching will now use this profile. ✓', 'success');
        await loadProfiles();
        selectProfile(profileId);
        loadActiveMatrixPreview();
      } catch (e) {
        showToast('Error activating profile: ' + e.message, 'error');
      }
    }

    async function deleteCurrentProfile() {
      const sel = document.getElementById('profileSelect');
      const profileId = sel.value;
      const current = allProfilesList.find(x => x.id == profileId);
      if (!current) return;

      if (!confirm(`Are you sure you want to delete profile "${current.name}"?`)) return;

      try {
        await apiSend(`/api/profiles/${profileId}`, 'DELETE');
        showToast(`Profile deleted.`, 'normal');
        await loadProfiles();
      } catch (e) {
        showToast('Error deleting profile: ' + e.message, 'error');
      }
    }

    async function loadActiveMatrixPreview() {
      const tbody = document.getElementById('previewMatrixBody');
      if (!tbody) return;
      tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-faint);text-align:center">Loading query matrix...</td></tr>';
      try {
        const res = await apiGet('/api/profiles/active/matrix');
        const matrix = Array.isArray(res) ? res : (res.matrix || []);
        if (!matrix.length) {
          tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-faint);text-align:center">No search queries configured in active profile.</td></tr>';
          return;
        }
        tbody.innerHTML = matrix.map(m => `
          <tr>
            <td style="font-family:'JetBrains Mono';font-size:11px;color:var(--purple-light)">${esc(m.id)}</td>
            <td style="font-weight:600">${esc(m.term || m.search_term)}</td>
            <td>${esc(m.location || 'Remote / Global')}</td>
            <td><span class="pill ${m.is_remote ? 'pill-stage-deal_won' : ''}">${m.is_remote ? 'Remote' : 'Onsite'}</span></td>
            <td style="font-family:'JetBrains Mono';font-size:11px;color:var(--text-muted)">${esc(m.google || m.google_search_query || '—')}</td>
          </tr>
        `).join('');
      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" style="color:var(--bad)">Failed to load matrix: ${esc(e.message)}</td></tr>`;
      }
    }

    // =========================================================================
    // CANDIDATE CV MACHINE LEARNING ANALYSIS & PROFILE SYNC
    // =========================================================================
    async function runMlCvAnalysis() {
      const icon = document.getElementById('mlCvAnalyzeIcon');
      const btn = document.getElementById('btnRunMlAnalysis');
      if (btn) btn.disabled = true;
      if (icon) icon.textContent = '⏳';
      showToast('Extracting CV features with pure Python NLP & TF-IDF vectors...', 'normal');
      try {
        const res = await apiGet('/api/candidate/cv/analyze');
        if (res.ok && res.analysis) {
          const a = res.analysis;
          const panel = document.getElementById('mlCvAnalysisPanel');
          if (panel) panel.style.display = 'block';

          const nameEl = document.getElementById('mlCvName');
          if (nameEl) nameEl.textContent = a.name || 'Candidate';

          const emailEl = document.getElementById('mlCvEmail');
          if (emailEl) emailEl.textContent = a.email || '—';

          const phoneEl = document.getElementById('mlCvPhone');
          if (phoneEl) phoneEl.textContent = a.phone || '—';

          const locEl = document.getElementById('mlCvLocation');
          if (locEl) locEl.textContent = a.location || '—';

          const degEl = document.getElementById('mlCvDegree');
          if (degEl) degEl.textContent = (a.education && a.education[0]) || a.degree_level || '—';

          const instEl = document.getElementById('mlCvInstitution');
          if (instEl) instEl.textContent = (a.education && a.education[1]) || '—';

          const expEl = document.getElementById('mlCvExperienceSpan');
          if (expEl) expEl.textContent = a.experience_years ? `💼 ${a.experience_years} Years Professional Span` : '💼 Experience Not Specified';

          const linksEl = document.getElementById('mlCvSocialLinks');
          if (linksEl) {
            let linksHtml = '';
            if (a.linkedin) linksHtml += `<a href="${esc(a.linkedin)}" target="_blank" class="pill" style="font-size:10px;background:#0a66c2;color:#fff">LinkedIn ↗</a>`;
            if (a.github) linksHtml += `<a href="${esc(a.github)}" target="_blank" class="pill" style="font-size:10px;background:#24292e;color:#fff">GitHub ↗</a>`;
            linksEl.innerHTML = linksHtml;
          }

          const rolesEl = document.getElementById('mlCvTargetRoles');
          if (rolesEl) {
            rolesEl.innerHTML = (a.target_roles || []).map(r => `<span class="pill" style="font-size:11px;background:rgba(168,85,247,0.18);color:#d8b4fe">${esc(r)}</span>`).join('');
          }

          const skillsEl = document.getElementById('mlCvSkillsWrap');
          if (skillsEl) {
            skillsEl.innerHTML = (a.top_skills || []).map(s => {
              const wt = (a.skill_weights && a.skill_weights[s.toLowerCase()]) || 1.0;
              return `<span class="pill pill-stage-deal_won" style="font-size:11px;padding:3px 8px;font-family:'JetBrains Mono'" title="TF-IDF Weight: ${wt}">⚡ ${esc(s)} <small style="opacity:0.7">(${wt})</small></span>`;
            }).join('');
          }

          showToast('CV successfully analyzed with ML algorithms! ✓', 'success');
        } else {
          showToast('Failed to analyze CV: ' + (res.error || 'Unknown error'), 'error');
        }
      } catch (err) {
        showToast('ML Analysis failed: ' + err.message, 'error');
      } finally {
        if (btn) btn.disabled = false;
        if (icon) icon.textContent = '⚡';
      }
    }

    async function syncExtractedCvToProfile() {
      const btn = document.getElementById('btnSyncCvToProfile');
      if (btn) btn.disabled = true;
      showToast('Synchronizing extracted CV data into active profile...', 'normal');
      try {
        const res = await apiSend('/api/candidate/cv/sync_profile', 'POST');
        if (res.ok) {
          showToast('Active profile successfully updated from CV! ✓', 'success');
          await loadProfiles();
        } else {
          showToast('Sync failed: ' + (res.message || 'Unknown error'), 'error');
        }
      } catch (err) {
        showToast('Failed to sync profile: ' + err.message, 'error');
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function uploadCvFromStudio(event) {
      const file = event.target.files && event.target.files[0];
      if (!file) return;

      const sel = document.getElementById('profileSelect');
      const profileId = sel ? sel.value : null;

      showToast(`Uploading ${file.name}...`, 'normal');
      const reader = new FileReader();
      reader.onload = async function(e) {
        try {
          const b64 = e.target.result;
          const res = await apiSend('/api/candidate/cv/upload', 'POST', {
            filename: file.name,
            file_base64: b64,
            profile_id: profileId ? parseInt(profileId, 10) : null
          });
          showToast(res.message || 'CV uploaded and linked to profile! ✓', 'success');
          await loadProfiles();
          if (profileId) selectProfile(profileId);
        } catch (err) {
          showToast('Failed to upload CV: ' + err.message, 'error');
        } finally {
          event.target.value = '';
        }
      };
      reader.readAsDataURL(file);
    }

    async function generateCvFromStudio() {
      const sel = document.getElementById('profileSelect');
      const profileId = sel ? sel.value : null;
      const btn = document.getElementById('btnGenerateCvStudio');
      if (btn) btn.disabled = true;

      showToast('Generating ATS-compliant PDF resume from profile...', 'normal');
      try {
        const res = await apiSend('/api/candidate/cv/generate', 'POST', {
          profile_id: profileId ? parseInt(profileId, 10) : null
        });
        showToast(res.message || 'ATS Resume PDF generated successfully! ✓', 'success');
        await loadProfiles();
        if (profileId) selectProfile(profileId);
      } catch (err) {
        showToast('Failed to generate CV PDF: ' + err.message, 'error');
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    // =========================================================================
    // TOP ACTIONS: EVALUATE BACKLOG, CHECK INBOX, EXPORT CSV
    // =========================================================================
    let evalPollingInterval = null;

    async function triggerBacklogEvaluation() {
      const btn = document.getElementById('btnEvaluateBacklog');
      const icon = document.getElementById('evalBacklogIcon');
      if (btn) btn.disabled = true;
      if (icon) icon.textContent = '⏳';
      showToast('Checking un-evaluated backlog...', 'normal');

      try {
        const res = await apiSend('/api/actions/evaluate_backlog', 'POST', { limit: 50 });
        if (!res.ok) {
          showToast(res.message || 'No pending jobs to evaluate', 'info');
          if (btn) btn.disabled = false;
          if (icon) icon.textContent = '🧠';
          return;
        }
        showToast(res.message || 'Backlog evaluation started!', 'success');
        
        if (evalPollingInterval) clearInterval(evalPollingInterval);
        evalPollingInterval = setInterval(pollEvalStatus, 2500);
      } catch (e) {
        showToast('Evaluation failed: ' + e.message, 'error');
        if (btn) btn.disabled = false;
        if (icon) icon.textContent = '🧠';
      }
    }

    async function pollEvalStatus() {
      try {
        const st = await apiGet('/api/actions/evaluate_status');
        const btn = document.getElementById('btnEvaluateBacklog');
        const icon = document.getElementById('evalBacklogIcon');

        if (st.running) {
          if (btn) btn.disabled = true;
          if (icon) icon.textContent = '⏳';
          if (st.message) {
            const liveTxt = document.getElementById('liveStatusText');
            if (liveTxt) liveTxt.textContent = st.message.slice(0, 32);
          }
        } else {
          if (btn) btn.disabled = false;
          if (icon) icon.textContent = '🧠';
          clearInterval(evalPollingInterval);
          evalPollingInterval = null;
          showToast(st.message || 'Evaluation finished!', 'success');
          loadJobSummary();
          loadJobs();
        }
      } catch (e) {
        console.error('Error polling eval status:', e);
      }
    }

    async function triggerCheckInbox() {
      const btn = document.getElementById('btnCheckInbox');
      btn.disabled = true;
      showToast('Polling email inbox for recruiter replies...', 'normal');
      try {
        const res = await apiSend('/api/actions/check_inbox', 'POST');
        showToast(`Inbox checked! Processed ${res.processed ?? 0} messages.`, 'success');
        loadInbox();
        loadJobSummary();
      } catch (e) {
        showToast('Inbox check failed: ' + e.message, 'error');
      } finally {
        btn.disabled = false;
      }
    }

    function toggleExportMenu() {
      const menu = document.getElementById('exportDropdown');
      menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
    }

    document.addEventListener('click', (e) => {
      const menu = document.getElementById('exportDropdown');
      if (menu && !e.target.closest('#jobsView') && menu.style.display === 'block') {
        menu.style.display = 'none';
      }
    });

    // =========================================================================
    // RBAC AUTHENTICATION, MOROCCAN SUBSCRIPTIONS & ADMIN OPERATIONS
    // =========================================================================

    let currentUser = null;

    async function fetchCurrentUser() {
      return await initAuth();
    }

    async function initAuth() {
      try {
        const res = await fetch('/api/auth/me');
        if (!res.ok) {
          if (res.status === 401) {
            window.location.href = '/';
            return;
          }
          return;
        }
        const data = await res.json();
        currentUser = data.user;

        // Top Navigation User Badge
        const nameEl = document.getElementById('navUserName');
        if (nameEl) nameEl.textContent = currentUser.full_name || currentUser.email;

        const roleEl = document.getElementById('navRoleBadge');
        if (roleEl) {
          roleEl.textContent = (currentUser.role || 'USER').toUpperCase();
          if (currentUser.role === 'admin') {
            roleEl.style.background = 'rgba(168,85,247,0.2)';
            roleEl.style.color = 'var(--purple-light)';
            roleEl.style.border = '1px solid rgba(168,85,247,0.4)';
          } else {
            roleEl.style.background = 'rgba(56,189,248,0.15)';
            roleEl.style.color = 'var(--cyan-light)';
          }
        }

        const navPlanNameEl = document.getElementById('navPlanNameBadge');
        const pRaw = currentUser.current_plan || 'free';
        const p = pRaw.toLowerCase();
        if (navPlanNameEl) {
          if (p === 'ultra') {
            navPlanNameEl.textContent = 'ULTRA 🚀';
            navPlanNameEl.style.background = 'linear-gradient(135deg, rgba(234, 179, 8, 0.28) 0%, rgba(249, 115, 22, 0.28) 100%)';
            navPlanNameEl.style.color = '#facc15';
            navPlanNameEl.style.border = '1px solid rgba(234, 179, 8, 0.65)';
            navPlanNameEl.style.boxShadow = '0 0 10px rgba(234, 179, 8, 0.35)';
            navPlanNameEl.title = 'Active Plan: Ultra Agency (Unlimited applications). Click to change plan or manage billing.';
          } else if (p === 'pro' || p === 'pro_499') {
            navPlanNameEl.textContent = 'PRO ⭐';
            navPlanNameEl.style.background = 'rgba(168, 85, 247, 0.22)';
            navPlanNameEl.style.color = '#c084fc';
            navPlanNameEl.style.border = '1px solid rgba(168, 85, 247, 0.55)';
            navPlanNameEl.style.boxShadow = '0 0 10px rgba(168, 85, 247, 0.25)';
            navPlanNameEl.title = 'Active Plan: Pro Hunter (150 apps/day + HR discovery). Click to change plan or manage billing.';
          } else if (p === 'starter' || p === 'starter_99') {
            navPlanNameEl.textContent = 'STARTER ⚡';
            navPlanNameEl.style.background = 'rgba(6, 182, 212, 0.2)';
            navPlanNameEl.style.color = '#22d3ee';
            navPlanNameEl.style.border = '1px solid rgba(6, 182, 212, 0.55)';
            navPlanNameEl.style.boxShadow = '0 0 10px rgba(6, 182, 212, 0.25)';
            navPlanNameEl.title = 'Active Plan: Starter Hunter (50 apps/day). Click to change plan or manage billing.';
          } else {
            navPlanNameEl.textContent = 'FREE TIER';
            navPlanNameEl.style.background = 'rgba(255, 255, 255, 0.06)';
            navPlanNameEl.style.color = 'var(--text-muted)';
            navPlanNameEl.style.border = '1px solid var(--border)';
            navPlanNameEl.style.boxShadow = 'none';
            navPlanNameEl.title = 'Free Tier (5 apps/day). Click to upgrade.';
          }
        }

        const planBadge = document.getElementById('navPlanBadge');
        if (planBadge) {
          const sent = currentUser.role === 'admin'
            ? (data.applications_today ?? data.applications_sent_today ?? currentUser.applications_sent_today ?? 0)
            : (currentUser.applications_sent_today ?? data.applications_sent_today ?? data.applications_today ?? 0);
          const limit = (currentUser.daily_apply_limit !== undefined && currentUser.daily_apply_limit !== null) ? currentUser.daily_apply_limit : (currentUser.role === 'admin' ? 200 : 5);
          planBadge.textContent = `${sent}/${limit} apps today`;
        }

        // Reveal Admin Tab only for Admin role
        const adminTabBtn = document.getElementById('jTabAdmin');
        if (adminTabBtn) {
          adminTabBtn.style.display = (currentUser.role === 'admin') ? 'inline-block' : 'none';
        }

        // Reveal System & Config Tab only for Admin role
        const systemTabBtn = document.getElementById('jTabSystem');
        if (systemTabBtn) {
          systemTabBtn.style.display = (currentUser.role === 'admin') ? 'inline-block' : 'none';
        }

        // If user is not admin, prevent staying on admin or system tabs
        if (currentUser.role !== 'admin' && (currentJobsTab === 'admin' || currentJobsTab === 'system')) {
          switchJobsTab('postings');
        }

        // Update HR Discovery Access for Pro Plan / Admin
        updateHrDiscoveryAccess();

        // Update Subscription Card in Profiles and Pricing Section
        const planTitleEl = document.getElementById('userPlanTitle');
        const pricingPlanTitleEl = document.getElementById('pricingActivePlanTitle');
        let planLabel = 'Free Tier';
        if (p === 'ultra') planLabel = 'Executive & Agency (Ultra)';
        else if (p === 'pro' || p === 'pro_499') planLabel = 'Pro Hunter & Freelancer';
        else if (p === 'starter' || p === 'starter_99') planLabel = 'Starter Hunter';
        if (planTitleEl) planTitleEl.textContent = planLabel;
        if (pricingPlanTitleEl) pricingPlanTitleEl.textContent = planLabel;

        const limit = (currentUser.daily_apply_limit !== undefined && currentUser.daily_apply_limit !== null) ? currentUser.daily_apply_limit : (currentUser.role === 'admin' ? 200 : 5);
        const quotaPill = document.getElementById('userQuotaPill');
        if (quotaPill) quotaPill.textContent = `${limit} Applications / Day`;

        const pricingBadge = document.getElementById('pricingActivePlanBadge');
        if (pricingBadge) pricingBadge.textContent = limit >= 9999 ? 'Unlimited apps/day' : `${limit} apps/day`;

        const sent = currentUser.role === 'admin'
          ? (data.applications_today ?? data.applications_sent_today ?? currentUser.applications_sent_today ?? 0)
          : (currentUser.applications_sent_today ?? data.applications_sent_today ?? data.applications_today ?? 0);
        const remaining = Math.max(0, limit - sent);

        const quotaProg = document.getElementById('userQuotaProgress');
        if (quotaProg) {
          quotaProg.textContent = `${sent} of ${limit} applications sent today (${remaining} remaining)`;
        }
        const pricingQuotaSub = document.getElementById('pricingActiveQuotaSub');
        if (pricingQuotaSub) {
          const expText = currentUser.plan_expires_at ? ` · Renews/Expires: ${formatAppDate(currentUser.plan_expires_at)}` : '';
          pricingQuotaSub.textContent = `Usage: ${sent} of ${limit} applications sent today (${remaining} remaining)${expText}`;
        }

        // Dynamic action buttons in Active Membership Banner
        const pricingActiveBtns = document.getElementById('pricingActiveActionButtons');
        if (pricingActiveBtns) {
          if (p === 'ultra') {
            pricingActiveBtns.innerHTML = `
              <button type="button" class="btn btn-cyan" onclick="openPaymentModal('ultra')" style="font-weight:700;padding:9px 18px">
                🔄 Renew / Extend Ultra
              </button>
              <button type="button" class="btn" onclick="scrollToPricingCards()" style="font-weight:700;padding:9px 18px;border-color:rgba(255,255,255,0.2)">
                ⚡ Switch / Change Plan ▾
              </button>
            `;
          } else if (p === 'pro' || p === 'pro_499') {
            pricingActiveBtns.innerHTML = `
              <button type="button" class="btn btn-primary" onclick="openPaymentModal('pro')" style="font-weight:700;padding:9px 18px">
                🔄 Renew / Extend Pro
              </button>
              <button type="button" class="btn btn-cyan" onclick="openPaymentModal('ultra')" style="font-weight:700;padding:9px 18px">
                🚀 Upgrade to Ultra Agency
              </button>
              <button type="button" class="btn" onclick="scrollToPricingCards()" style="font-weight:700;padding:9px 18px;border-color:rgba(255,255,255,0.2)">
                ⚡ Switch Plan ▾
              </button>
            `;
          } else if (p === 'starter' || p === 'starter_99') {
            pricingActiveBtns.innerHTML = `
              <button type="button" class="btn btn-cyan" onclick="openPaymentModal('starter')" style="font-weight:700;padding:9px 18px">
                🔄 Renew / Extend Starter
              </button>
              <button type="button" class="btn btn-primary" onclick="openPaymentModal('pro')" style="font-weight:700;padding:9px 18px">
                ⭐ Upgrade to Pro Hunter
              </button>
              <button type="button" class="btn" onclick="scrollToPricingCards()" style="font-weight:700;padding:9px 18px;border-color:rgba(255,255,255,0.2)">
                ⚡ All Plans ▾
              </button>
            `;
          } else {
            pricingActiveBtns.innerHTML = `
              <button type="button" class="btn btn-sm btn-cyan" onclick="openPaymentModal('starter')" style="font-weight:700;padding:9px 16px">
                ⚡ Get Starter (25 apps/day)
              </button>
              <button type="button" class="btn btn-primary" onclick="openPaymentModal('pro')" style="font-weight:700;padding:9px 18px">
                ⭐ Upgrade to Pro Hunter
              </button>
              <button type="button" class="btn" onclick="openPaymentModal('ultra')" style="font-weight:700;padding:9px 16px;border-color:rgba(168,85,247,0.5);color:var(--purple-light)">
                🚀 Ultra Agency
              </button>
            `;
          }
        }

        // Dynamic action buttons in Candidate Profiles banner
        const profilesActiveBtns = document.getElementById('profilesPlanActionButtons');
        if (profilesActiveBtns) {
          if (p === 'ultra') {
            profilesActiveBtns.innerHTML = `
              <button type="button" class="btn btn-sm btn-cyan" onclick="openPaymentModal('ultra')" style="font-weight:700">
                🔄 Renew Ultra
              </button>
              <button type="button" class="btn btn-sm" onclick="switchJobsTab('pricing')" style="border-color:rgba(16,185,129,0.4);color:var(--good);font-weight:700">
                💎 Change Plan / Billing
              </button>
            `;
          } else if (p === 'pro' || p === 'pro_499') {
            profilesActiveBtns.innerHTML = `
              <button type="button" class="btn btn-sm btn-primary" onclick="openPaymentModal('pro')" style="font-weight:700">
                🔄 Renew Pro
              </button>
              <button type="button" class="btn btn-sm btn-cyan" onclick="openPaymentModal('ultra')" style="font-weight:700">
                🚀 Upgrade to Ultra
              </button>
              <button type="button" class="btn btn-sm" onclick="switchJobsTab('pricing')" style="border-color:rgba(16,185,129,0.4);color:var(--good);font-weight:700">
                💎 Change Plan / Billing
              </button>
            `;
          } else if (p === 'starter' || p === 'starter_99') {
            profilesActiveBtns.innerHTML = `
              <button type="button" class="btn btn-sm btn-cyan" onclick="openPaymentModal('starter')" style="font-weight:700">
                🔄 Renew Starter
              </button>
              <button type="button" class="btn btn-sm btn-primary" onclick="openPaymentModal('pro')" style="font-weight:700">
                ⭐ Upgrade to Pro
              </button>
              <button type="button" class="btn btn-sm" onclick="switchJobsTab('pricing')" style="border-color:rgba(16,185,129,0.4);color:var(--good);font-weight:700">
                💎 Change Plan / Billing
              </button>
            `;
          } else {
            profilesActiveBtns.innerHTML = `
              <button type="button" class="btn btn-sm btn-cyan" onclick="openPaymentModal('starter')" style="font-weight:700">
                ⚡ Upgrade to Starter
              </button>
              <button type="button" class="btn btn-sm btn-primary" onclick="openPaymentModal('pro')" style="font-weight:700">
                🚀 Upgrade to Pro
              </button>
              <button type="button" class="btn btn-sm" onclick="switchJobsTab('pricing')" style="border-color:rgba(16,185,129,0.4);color:var(--good);font-weight:700">
                💎 All Plans & Options
              </button>
            `;
          }
        }

        // Dynamic Subscription & Quota Card in User Settings
        const usPlanTitle = document.getElementById('userSettingsPlanTitle');
        const usQuotaBadge = document.getElementById('userSettingsQuotaBadge');
        const usPlanSub = document.getElementById('userSettingsPlanSub');
        const usPlanBtns = document.getElementById('userSettingsPlanButtons');
        if (usPlanTitle) usPlanTitle.textContent = planLabel;
        if (usQuotaBadge) usQuotaBadge.textContent = limit >= 9999 ? 'Unlimited apps/day' : `${limit} apps/day`;
        if (usPlanSub) {
          const expText = currentUser.plan_expires_at ? ` · Renews/Expires: ${formatAppDate(currentUser.plan_expires_at)}` : '';
          usPlanSub.textContent = `Today: ${sent} of ${limit} applications sent (${remaining} remaining)${expText}`;
        }
        if (usPlanBtns) {
          const isPaid = (p === 'starter' || p === 'starter_99' || p === 'pro' || p === 'pro_499' || p === 'ultra');
          usPlanBtns.innerHTML = `
            <button type="button" class="btn btn-sm ${isPaid ? 'btn-cyan' : 'btn-primary'}" onclick="openPaymentModal('${isPaid ? p : 'pro'}')" style="font-weight:700">
              ${isPaid ? '🔄 Renew / Extend' : '🚀 Upgrade Subscription'}
            </button>
            <button type="button" class="btn btn-sm" onclick="switchJobsTab('pricing')" style="border-color:rgba(16,185,129,0.4);color:var(--good);font-weight:700">
              💎 Change Plan & Billing
            </button>
          `;
        }

        // Check Setup & Onboarding Status for first-time / incomplete users
        checkUserOnboardingStatus();
      } catch (err) {
        console.error('Failed to initialize user session:', err);
      }
    }

    async function handleLogout() {
      try {
        await fetch('/api/auth/logout', { method: 'POST' });
      } catch (_) {}
      window.location.href = '/';
    }

    function switchAdminSubTab(sub) {
      const tabs = [
        { id: 'users', btn: 'adminSubTabUsers', panel: 'adminPanelUsers' },
        { id: 'payments', btn: 'adminSubTabPayments', panel: 'adminPanelPayments' },
        { id: 'plans', btn: 'adminSubTabPlans', panel: 'adminPanelPlans' },
        { id: 'gateways', btn: 'adminSubTabGateways', panel: 'adminPanelGateways' },
        { id: 'chat', btn: 'adminSubTabChat', panel: 'adminPanelChat' },
        { id: 'logs', btn: 'adminSubTabLogs', panel: 'adminPanelLogs' }
      ];
      tabs.forEach(t => {
        const btn = document.getElementById(t.btn);
        const p = document.getElementById(t.panel);
        if (btn) {
          btn.classList.toggle('btn-cyan', t.id === sub);
        }
        if (p) {
          p.style.display = t.id === sub ? 'block' : 'none';
        }
      });
      if (sub === 'users') loadAdminUsers();
      else if (sub === 'payments') loadAdminPayments();
      else if (sub === 'plans') loadAdminPlans();
      else if (sub === 'gateways') loadAdminGateways();
      else if (sub === 'chat') loadAdminChatConversations();
      else if (sub === 'logs') loadAdminLogs();
    }

    // Admin cached state for users and platform plans
    let adminCachedPlans = [];
    let adminCachedUsers = [];

    function resolvePlanSlug(slug) {
      const s = (slug || 'free').toString().trim().toLowerCase();
      if (s === 'starter_99' || s === 'starter') return 'starter';
      if (s === 'pro_499' || s === 'pro') return 'pro';
      return s;
    }

    async function loadAdminUsers() {
      const tbody = document.getElementById('adminUsersTable');
      if (!tbody) return;
      try {
        const res = await apiGet('/api/admin/users');
        const users = (res && res.users) ? res.users : (Array.isArray(res) ? res : []);
        adminCachedUsers = users;
        if (res && res.plans && Array.isArray(res.plans)) {
          adminCachedPlans = res.plans;
        }

        // Update total count badge
        const countBadge = document.getElementById('adminUsersCountBadge');
        if (countBadge) {
          countBadge.textContent = `${users.length} Users`;
        }

        // Populate plan filter dropdown
        populateAdminUserPlanFilter();

        // Populate Add User modal plan options
        populateAdminCreateUserPlanOptions();

        // Render table
        renderAdminUsersTable(users);
      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:20px;color:#f87171">Error loading users: ${esc(e.message)}</td></tr>`;
      }
    }

    function populateAdminUserPlanFilter() {
      const filterSel = document.getElementById('adminUserPlanFilter');
      if (!filterSel) return;
      const cur = filterSel.value;
      const plans = adminCachedPlans || [];
      const opts = ['<option value="">All Plans</option>'];
      plans.forEach(p => {
        opts.push(`<option value="${esc(p.slug)}" ${cur === p.slug ? 'selected' : ''}>${esc(p.name)}</option>`);
      });
      filterSel.innerHTML = opts.join('');
    }

    function renderAdminUsersTable(users) {
      const tbody = document.getElementById('adminUsersTable');
      if (!tbody) return;
      if (!users || !users.length) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:20px;color:var(--text-muted)">No registered users found.</td></tr>';
        return;
      }

      const plansList = (adminCachedPlans && adminCachedPlans.length) ? adminCachedPlans : [
        { slug: 'free', name: 'Free Tier', daily_apply_limit: 5 },
        { slug: 'starter', name: 'Starter Hunter', daily_apply_limit: 25 },
        { slug: 'pro', name: 'Pro Hunter & Freelancer', daily_apply_limit: 50 },
        { slug: 'ultra', name: 'Executive & Agency', daily_apply_limit: 100 }
      ];

      tbody.innerHTML = users.map(u => {
        const planSlug = (u.current_plan || 'free').toLowerCase();
        const normSlug = resolvePlanSlug(planSlug);
        const isPro = normSlug === 'pro' || normSlug === 'ultra';
        const isStarter = normSlug === 'starter';
        const planColor = isPro ? 'var(--purple-light)' : (isStarter ? 'var(--cyan-light)' : 'var(--text-muted)');
        const planBadge = u.plan_badge ? `<span style="font-size:9.5px;background:rgba(139,92,246,0.18);color:var(--purple-light);padding:1px 6px;border-radius:4px;margin-left:4px;font-weight:700">${esc(u.plan_badge)}</span>` : '';

        const isSynced = u.is_quota_synced !== false;
        const limitDisplay = isSynced
          ? `<span class="pill pill-stage-deal_won" title="Synchronized with ${esc(u.plan_name || u.current_plan)} default quota">${u.daily_apply_limit} / d ✓</span>`
          : `<span class="pill" style="border-color:#f59e0b;color:#facc15" title="Custom limit (Plan default: ${u.plan_daily_limit || '—'}/d)">${u.daily_apply_limit} / d (custom)</span>`;

        // Generate dynamic plan options for Actions dropdown (exact match first, then resolved alias)
        const exactMatchIndex = plansList.findIndex(p => p.slug.toLowerCase() === planSlug);
        const resolvedMatchIndex = exactMatchIndex !== -1 ? exactMatchIndex : plansList.findIndex(p => resolvePlanSlug(p.slug) === normSlug);

        const planOptionsHtml = plansList.map((p, idx) => {
          const isSelected = (idx === resolvedMatchIndex);
          const quotaStr = p.daily_apply_limit >= 9999 ? '∞' : `${p.daily_apply_limit}/d`;
          return `<option value="${esc(p.slug)}" ${isSelected ? 'selected' : ''}>${esc(p.name)} (${quotaStr})</option>`;
        }).join('');

        const hasAnyMatch = resolvedMatchIndex !== -1;
        const customOpt = (!hasAnyMatch && u.current_plan)
          ? `<option value="${esc(u.current_plan)}" selected>${esc(u.current_plan)} (custom)</option>`
          : '';

        const syncBtn = !isSynced
          ? `<button class="btn btn-sm" onclick="syncUserWithPlan(${u.id})" style="font-size:10px;padding:2px 6px;border-color:rgba(168,85,247,0.6);color:var(--purple-light);font-weight:700" title="Sync quota with plan default (${u.plan_daily_limit || 5} apps/day)">⚡ Sync</button>`
          : '';

        return `
          <tr>
            <td style="font-family:'JetBrains Mono';font-size:12px;color:var(--cyan-light)">#${u.id}</td>
            <td><strong>${esc(u.full_name || '—')}</strong></td>
            <td>
              ${esc(u.email)}
              ${u.auth_provider === 'google' ? '<span class="pill" style="font-size:10px;background:rgba(239,68,68,0.15);color:#f87171;margin-left:4px">Google</span>' : ''}
            </td>
            <td>
              <span class="pill ${u.role === 'admin' ? 'pill-stage-deal_won' : 'pill-stage-discovered'}">
                ${esc(u.role)}
              </span>
            </td>
            <td>
              <strong style="color:${planColor}">
                ${esc(u.plan_name || u.current_plan)}
              </strong>
              ${planBadge}
            </td>
            <td>${limitDisplay}</td>
            <td>
              <span class="pill ${u.is_active ? 'pill-stage-deal_won' : 'pill-stage-lost'}">
                ${u.is_active ? 'Active' : 'Disabled'}
              </span>
            </td>
            <td style="font-size:12px;color:var(--text-muted)">${formatAppDate(u.created_at)}</td>
            <td>
              <div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">
                <select onchange="updateUserPlan(${u.id}, this.value)" style="font-size:11px;padding:2px 4px;background:#0d1527;border-color:var(--border);color:#fff;border-radius:4px" title="Change subscription plan (auto-syncs daily quota to plan)">
                  ${customOpt}
                  ${planOptionsHtml}
                </select>
                ${syncBtn}
                <button class="btn btn-sm" onclick="editUserDailyLimit(${u.id}, ${u.daily_apply_limit})" style="font-size:10px;padding:2px 6px" title="Set custom daily applications quota">
                  Quota
                </button>
                <button class="btn btn-sm" onclick="toggleUserActive(${u.id}, ${u.is_active})" style="font-size:10px;padding:2px 6px" title="${u.is_active ? 'Disable user' : 'Activate user'}">
                  ${u.is_active ? 'Disable' : 'Enable'}
                </button>
                <button class="btn btn-sm" onclick="toggleUserRole(${u.id}, '${u.role}')" style="font-size:10px;padding:2px 6px" title="Change role">
                  ${u.role === 'admin' ? 'Demote' : 'Promote'}
                </button>
                <button class="btn btn-sm" onclick="deleteUser(${u.id}, '${esc(u.email)}')" style="font-size:10px;padding:2px 6px;color:#f87171;border-color:rgba(239,68,68,0.3)" title="Delete user">
                  ✕
                </button>
              </div>
            </td>
          </tr>
        `;
      }).join('');
    }

    function filterAdminUsersTable() {
      if (!adminCachedUsers) return;
      const search = (document.getElementById('adminUserSearchInput')?.value || '').trim().toLowerCase();
      const planFilter = (document.getElementById('adminUserPlanFilter')?.value || '').trim().toLowerCase();

      const filtered = adminCachedUsers.filter(u => {
        const matchesSearch = !search ||
          (u.email && u.email.toLowerCase().includes(search)) ||
          (u.full_name && u.full_name.toLowerCase().includes(search)) ||
          String(u.id).includes(search);

        const uPlan = (u.current_plan || 'free').toLowerCase();
        const matchesPlan = !planFilter ||
          uPlan === planFilter ||
          resolvePlanSlug(uPlan) === resolvePlanSlug(planFilter);

        return matchesSearch && matchesPlan;
      });

      renderAdminUsersTable(filtered);
    }

    async function toggleUserActive(userId, currentStatus) {
      const nextStatus = !currentStatus;
      const action = nextStatus ? 'activate' : 'disable';
      if (!confirm(`Are you sure you want to ${action} user #${userId}?`)) return;
      try {
        await apiSend(`/api/admin/users/${userId}`, 'PATCH', { is_active: nextStatus });
        showToast(`User #${userId} ${nextStatus ? 'activated' : 'disabled'} ✓`, 'success');
        loadAdminUsers();
      } catch (e) {
        showToast('Failed to update user status: ' + e.message, 'error');
      }
    }

    async function updateUserPlan(userId, newPlan) {
      try {
        // Look up target plan from dynamic cached plans
        const planObj = (adminCachedPlans || []).find(p => p.slug.toLowerCase() === (newPlan || '').toLowerCase() || resolvePlanSlug(p.slug) === resolvePlanSlug(newPlan));
        const targetLimit = planObj ? planObj.daily_apply_limit : undefined;
        const planName = planObj ? planObj.name : newPlan;

        await apiSend(`/api/admin/users/${userId}`, 'PATCH', {
          current_plan: newPlan,
          daily_apply_limit: targetLimit
        });
        showToast(`User #${userId} plan updated to ${planName} (${targetLimit !== undefined ? targetLimit + ' apps/day synced' : ''}) ✓`, 'success');
        loadAdminUsers();
        initAuth();
      } catch (e) {
        showToast('Failed to update plan: ' + e.message, 'error');
      }
    }

    async function syncUserWithPlan(userId) {
      try {
        const res = await apiSend(`/api/admin/users/${userId}/sync-plan`, 'POST');
        showToast(res.message || `User #${userId} quota synced with plan ✓`, 'success');
        loadAdminUsers();
      } catch (e) {
        showToast('Failed to sync quota: ' + e.message, 'error');
      }
    }

    async function syncAllUsersWithPlans() {
      if (!confirm('Synchronize all users daily application quotas to match their assigned platform plan defaults?')) return;
      try {
        const res = await apiSend('/api/admin/users/sync-all', 'POST');
        showToast(res.message || 'All user quotas synchronized to plans ✓', 'success');
        loadAdminUsers();
      } catch (e) {
        showToast('Failed to sync users: ' + e.message, 'error');
      }
    }

    async function editUserDailyLimit(userId, currentLimit) {
      const input = prompt(`Enter new daily application limit for User #${userId}:`, currentLimit);
      if (input === null) return;
      const num = parseInt(input.trim(), 10);
      if (isNaN(num) || num < 1) {
        showToast('Please enter a valid positive number for daily applications quota', 'error');
        return;
      }
      try {
        const res = await apiSend(`/api/admin/users/${userId}/daily_limit`, 'PATCH', { daily_apply_limit: num });
        showToast(res.message || `Daily limit updated to ${num}/day ✓`, 'success');
        loadAdminUsers();
      } catch (e) {
        showToast('Failed to update quota: ' + e.message, 'error');
      }
    }

    async function deleteUser(userId, email) {
      if (!confirm(`Are you sure you want to permanently delete user ${email} (ID #${userId}) and all associated profiles, applications, and settings? This action CANNOT be undone.`)) return;
      try {
        const res = await apiSend(`/api/admin/users/${userId}`, 'DELETE');
        showToast(res.message || `User #${userId} deleted ✓`, 'success');
        loadAdminUsers();
      } catch (e) {
        showToast('Failed to delete user: ' + e.message, 'error');
      }
    }

    function populateAdminCreateUserPlanOptions() {
      const sel = document.getElementById('acuPlan');
      if (!sel || !adminCachedPlans || !adminCachedPlans.length) return;
      const curVal = sel.value;
      sel.innerHTML = adminCachedPlans.filter(p => p.is_active !== false).map(p => {
        const limitStr = (p.daily_apply_limit >= 9999) ? 'Unlimited' : `${p.daily_apply_limit} apps/day`;
        return `<option value="${esc(p.slug)}">${esc(p.name)} (${limitStr})</option>`;
      }).join('');
      if (curVal && Array.from(sel.options).some(o => o.value === curVal)) {
        sel.value = curVal;
      } else if (sel.options.length) {
        sel.value = sel.options[0].value;
      }
      onAdminCreatePlanChange(sel.value);
    }

    function openAdminCreateUserModal() {
      const m = document.getElementById('adminCreateUserModal');
      if (m) {
        populateAdminCreateUserPlanOptions();
        m.style.display = 'flex';
        (window.requestAnimationFrame || setTimeout)(() => m.classList.add('open'), 16);
        setTimeout(() => document.getElementById('acuEmail')?.focus(), 80);
      }
    }

    function closeAdminCreateUserModal() {
      const m = document.getElementById('adminCreateUserModal');
      if (m) {
        m.classList.remove('open');
        setTimeout(() => {
          if (!m.classList.contains('open')) m.style.display = 'none';
        }, 220);
      }
    }

    function onAdminCreatePlanChange(plan) {
      const input = document.getElementById('acuDailyLimit');
      if (!input) return;
      const planObj = (adminCachedPlans || []).find(p => p.slug.toLowerCase() === (plan || '').toLowerCase() || resolvePlanSlug(p.slug) === resolvePlanSlug(plan));
      if (planObj && planObj.daily_apply_limit !== undefined) {
        input.value = planObj.daily_apply_limit;
      } else if (plan === 'starter_99' || plan === 'starter') {
        input.value = 25;
      } else if (plan === 'pro_499' || plan === 'pro') {
        input.value = 50;
      } else if (plan === 'ultra') {
        input.value = 100;
      } else {
        input.value = 5;
      }
    }

    async function handleAdminCreateUser(e) {
      e.preventDefault();
      const email = document.getElementById('acuEmail')?.value.trim();
      const fullName = document.getElementById('acuFullName')?.value.trim();
      const password = document.getElementById('acuPassword')?.value.trim();
      const role = document.getElementById('acuRole')?.value || 'user';
      const plan = document.getElementById('acuPlan')?.value || 'free';
      const dailyLimit = parseInt(document.getElementById('acuDailyLimit')?.value || '5', 10);

      if (!email) {
        showToast('Email address is required', 'error');
        return;
      }

      try {
        const payload = {
          email,
          full_name: fullName,
          role,
          current_plan: plan,
          daily_apply_limit: dailyLimit,
        };
        if (password) payload.password = password;
        const res = await apiSend('/api/admin/users', 'POST', payload);
        showToast(res.message || `User ${email} created successfully ✓`, 'success');
        if (res.generated_password) {
          alert(`Account created! Generated temporary password: ${res.generated_password}`);
        }
        closeAdminCreateUserModal();
        document.getElementById('adminCreateUserForm')?.reset();
        loadAdminUsers();
      } catch (err) {
        showToast('Failed to create user: ' + err.message, 'error');
      }
    }

    async function toggleUserRole(userId, currentRole) {
      const newRole = currentRole === 'admin' ? 'user' : 'admin';
      if (!confirm(`Are you sure you want to change user #${userId} role to ${newRole.toUpperCase()}?`)) return;
      try {
        await apiSend(`/api/admin/users/${userId}`, 'PATCH', { role: newRole });
        showToast(`User #${userId} role updated to ${newRole} ✓`, 'success');
        loadAdminUsers();
      } catch (e) {
        showToast('Failed to change role: ' + e.message, 'error');
      }
    }

    async function loadAdminPayments() {
      const tbody = document.getElementById('adminPaymentsTable');
      if (!tbody) return;
      try {
        const res = await apiGet('/api/admin/payments');
        const payments = (res && res.payments) ? res.payments : (Array.isArray(res) ? res : []);
        const badge = document.getElementById('adminPendingBadge');
        const pendingCount = (payments || []).filter(p => p.status === 'pending').length;
        if (badge) {
          badge.textContent = pendingCount;
          badge.style.display = pendingCount > 0 ? 'inline-block' : 'none';
        }
        if (!payments || !payments.length) {
          tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:20px;color:var(--text-muted)">No subscription payment records found.</td></tr>';
          return;
        }
        tbody.innerHTML = payments.map(p => {
          const curr = p.currency || 'USD';
          const amtStr = curr === 'MAD' ? `${p.amount_mad || 0} MAD` : (curr === 'USD' ? `$${p.amount_usd || p.amount || 0} USD` : `${p.amount_usd || p.amount || 0} ${curr}`);
          const methodNames = {
            usdt_trc20: '₮ USDT (TRC-20)',
            usdt_polygon: '🟣 USDT (Polygon)',
            solana: '⚡ Solana (SOL)',
            btc: '₿ Bitcoin (BTC)',
            paypal: '🅿️ PayPal',
            card_kofi: '☕ Card (Ko-fi)',
            wise_revolut: '🌐 Wise / Revolut',
            morocco: '🇲🇦 CIH / Attijari',
            cih_wire: '🇲🇦 CIH Bank',
            attijari_wire: '🇲🇦 Attijariwafa',
          };
          const methodLabel = methodNames[p.payment_method] || p.payment_method || 'Direct';
          const receiptBtn = p.has_receipt
            ? `<button class="btn btn-sm btn-cyan" onclick="openReceiptModal('${esc(p.receipt_url || `/api/payments/receipt/${p.id}`)}')" style="font-size:11px;padding:3px 8px">📷 View Proof</button>`
            : '<span style="font-size:12px;color:var(--text-muted)">—</span>';

          return `
          <tr>
            <td style="font-family:'JetBrains Mono';font-size:12px;color:var(--cyan-light)">#${p.id}</td>
            <td><strong>${esc(p.user_email || `User #${p.user_id}`)}</strong></td>
            <td><span class="pill" style="font-weight:700">${esc(p.plan || p.plan_name)}</span></td>
            <td><strong style="color:#34d399">${esc(amtStr)}</strong></td>
            <td><span style="font-size:12px;color:#fff">${esc(methodLabel)}</span></td>
            <td style="font-family:'JetBrains Mono';font-size:12px;color:#facc15"><code>${esc(p.reference_code)}</code></td>
            <td>${receiptBtn}</td>
            <td>
              <span class="pill ${p.status === 'approved' ? 'pill-stage-deal_won' : (p.status === 'rejected' ? 'pill-stage-lost' : 'pill-stage-discovered')}">
                ${esc((p.status || 'pending').toUpperCase())}
              </span>
            </td>
            <td style="font-size:12px;color:var(--text-muted)">${formatAppDate(p.created_at)}</td>
            <td>
              <div style="display:flex;gap:4px;align-items:center">
                ${p.status === 'pending' ? `
                  <button class="btn btn-sm btn-cyan" onclick="approvePayment(${p.id})" style="font-size:11px;padding:3px 8px">✓ Approve</button>
                  <button class="btn btn-sm" onclick="rejectPayment(${p.id})" style="font-size:11px;padding:3px 8px;color:#f87171;border-color:rgba(239,68,68,0.3)">✕ Reject</button>
                ` : `<span style="font-size:11px;color:var(--text-muted)">Processed</span>`}
                <button class="btn btn-sm" onclick="deletePayment(${p.id})" style="font-size:10px;padding:2px 6px;color:#f87171;border-color:rgba(239,68,68,0.3)" title="Delete payment record">🗑️</button>
              </div>
            </td>
          </tr>
        `;
        }).join('');
      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:20px;color:#f87171">Error loading payments: ${esc(e.message)}</td></tr>`;
      }
    }

    async function approvePayment(id) {
      try {
        const res = await apiSend(`/api/admin/payments/${id}/approve`, 'POST');
        showToast(res.message || 'Payment approved & plan upgraded! 🚀', 'success');
        loadAdminPayments();
        initAuth();
      } catch (e) {
        showToast('Failed to approve payment: ' + e.message, 'error');
      }
    }

    async function rejectPayment(id) {
      const reason = prompt('Optional rejection reason:', 'Transaction reference not found in bank statement');
      if (reason === null) return;
      try {
        const res = await apiSend(`/api/admin/payments/${id}/reject`, 'POST', { reason });
        showToast(res.message || 'Payment rejected', 'normal');
        loadAdminPayments();
      } catch (e) {
        showToast('Failed to reject payment: ' + e.message, 'error');
      }
    }

    async function deletePayment(id) {
      if (!confirm(`Are you sure you want to delete payment record #${id}?`)) return;
      try {
        const res = await apiSend(`/api/admin/payments/${id}`, 'DELETE');
        showToast(res.message || `Payment #${id} deleted ✓`, 'success');
        loadAdminPayments();
      } catch (e) {
        showToast('Failed to delete payment: ' + e.message, 'error');
      }
    }

    // =========================================================================
    // Admin Plans, Dynamic Pricing & Quotas Management Handlers
    // =========================================================================

    async function loadAdminPlans() {
      const grid = document.getElementById('adminPlansGrid');
      if (!grid) return;
      try {
        const res = await apiGet('/api/admin/plans');
        const plans = res?.plans || [];
        adminCachedPlans = plans;
        populateAdminCreateUserPlanOptions();
        populateAdminUserPlanFilter();


        if (!plans.length) {
          grid.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-muted);grid-column:1/-1">No plans configured in database. Click "Create New Plan" to add one.</div>';
          return;
        }

        grid.innerHTML = plans.map(p => {
          const feats = Array.isArray(p.features) ? p.features : [];
          const isRec = Boolean(p.is_recommended);
          const isAct = Boolean(p.is_active);

          return `
            <div style="background:rgba(15,23,42,0.85);border:1px solid ${isRec ? 'rgba(139,92,246,0.6)' : 'var(--border)'};border-radius:14px;padding:20px;display:flex;flex-direction:column;position:relative;box-shadow:${isRec ? '0 0 25px rgba(139,92,246,0.2)' : 'none'}">
              ${isRec ? '<span style="position:absolute;top:-10px;right:18px;background:linear-gradient(135deg,#8b5cf6,#06b6d4);color:#fff;font-size:10px;font-weight:800;padding:2px 10px;border-radius:20px;text-transform:uppercase;letter-spacing:0.05em">★ Featured Plan</span>' : ''}
              
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
                <div>
                  <h4 style="font-size:17px;font-weight:800;color:#fff;margin:0 0 2px">${esc(p.name)}</h4>
                  <div style="font-family:'JetBrains Mono';font-size:11px;color:var(--text-muted)">slug: <strong>${esc(p.slug)}</strong></div>
                </div>
                <span class="pill ${isAct ? 'pill-stage-deal_won' : 'pill-stage-lost'}" style="font-size:10px">
                  ${isAct ? 'Active' : 'Disabled'}
                </span>
              </div>

              ${p.badge ? `<div style="margin-bottom:10px"><span class="brand-badge" style="background:rgba(6,182,212,0.15);color:var(--cyan-light);font-size:11px">${esc(p.badge)}</span></div>` : ''}

              <div style="font-size:12px;color:var(--text-muted);margin-bottom:14px;min-height:34px;line-height:1.4">
                ${esc(p.description || 'No description set.')}
              </div>

              <!-- Price Box -->
              <div style="background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.06);border-radius:10px;padding:12px;margin-bottom:14px">
                <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
                  <div style="font-size:22px;font-weight:900;color:var(--good)">
                    ${p.price_mad} <span style="font-size:13px;color:#fff">MAD</span>
                  </div>
                  <div style="font-size:14px;font-weight:700;color:var(--cyan-light)">
                    $${p.price_usd} USD <span style="font-size:11px;color:var(--text-muted)">${esc(p.billing_interval || '/ month')}</span>
                  </div>
                </div>
                <div style="display:flex;gap:12px;font-size:11px;color:var(--text-muted)">
                  <span>EUR: <strong>€${p.price_eur}</strong></span>
                  <span>USDT: <strong>₮${p.price_usdt}</strong></span>
                </div>
              </div>

              <!-- Quota & Metrics Box -->
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px">
                <div style="background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:8px;padding:8px 10px;text-align:center">
                  <div style="font-size:10px;color:var(--text-muted);text-transform:uppercase">Daily Apply Quota</div>
                  <div style="font-size:16px;font-weight:800;color:#fff">
                    ${p.daily_apply_limit >= 9999 ? 'Unlimited' : p.daily_apply_limit} <span style="font-size:10px;color:var(--text-muted)">/day</span>
                  </div>
                </div>
                <div style="background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:8px;padding:8px 10px;text-align:center">
                  <div style="font-size:10px;color:var(--text-muted);text-transform:uppercase">Subscribers</div>
                  <div style="font-size:16px;font-weight:800;color:var(--purple-light)">
                    ${p.subscriber_count || 0} <span style="font-size:10px;color:var(--text-muted)">users</span>
                  </div>
                </div>
              </div>

              <!-- Features Summary -->
              <div style="flex:1;margin-bottom:16px">
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase">Entitlements:</div>
                <div style="display:flex;flex-direction:column;gap:4px">
                  ${p.can_access_freelance ? '<div style="font-size:11.5px;color:var(--good)">✓ Freelance Deal Client Engine Included</div>' : '<div style="font-size:11.5px;color:var(--text-muted)">✕ Freelance Deals Excluded</div>'}
                  ${feats.slice(0, 4).map(f => `<div style="font-size:11.5px;color:#cbd5e1;display:flex;align-items:center;gap:6px"><span style="color:var(--cyan-light)">•</span> ${esc(f)}</div>`).join('')}
                  ${feats.length > 4 ? `<div style="font-size:10.5px;color:var(--text-muted)">+ ${feats.length - 4} more features</div>` : ''}
                </div>
              </div>

              <!-- Actions -->
              <div style="display:flex;gap:6px;flex-wrap:wrap;border-top:1px solid var(--border);padding-top:12px">
                <button type="button" class="btn btn-sm btn-cyan" onclick="openAdminPlanModal(${p.id})" style="flex:1;font-weight:700">
                  ✏️ Edit Plan & Quotas
                </button>
                <button type="button" class="btn btn-sm" onclick="syncPlanQuotas(${p.id})" title="Force re-sync of this quota to all current subscribers" style="border-color:rgba(168,85,247,0.4);color:var(--purple-light)">
                  ⚡ Sync
                </button>
                <button type="button" class="btn btn-sm" onclick="deleteAdminPlan(${p.id}, '${esc(p.name)}')" title="Delete or deactivate plan" style="color:#f87171;border-color:rgba(239,68,68,0.3)">
                  🗑️
                </button>
              </div>
            </div>
          `;
        }).join('');
      } catch (err) {
        console.error('Failed to load admin plans:', err);
        grid.innerHTML = `<div style="text-align:center;padding:30px;color:#f87171;grid-column:1/-1">Failed to load plans: ${esc(err.message)}</div>`;
      }
    }

    function openAdminPlanModal(planId = null) {
      const modal = document.getElementById('adminPlanModal');
      if (!modal) return;

      const p = (planId && adminCachedPlans) ? adminCachedPlans.find(x => x.id === planId) : null;
      document.getElementById('adminPlanModalTitle').textContent = p ? `Edit Plan: ${p.name}` : '➕ Create New Subscription Plan';
      document.getElementById('apmPlanId').value = p ? p.id : '';

      const slugEl = document.getElementById('apmSlug');
      slugEl.value = p ? p.slug : '';
      slugEl.disabled = Boolean(p);

      document.getElementById('apmName').value = p ? p.name : '';
      document.getElementById('apmBadge').value = p ? (p.badge || '') : '';
      document.getElementById('apmBilling').value = p ? (p.billing_interval || '/ month') : '/ month';
      document.getElementById('apmDescription').value = p ? (p.description || '') : '';

      document.getElementById('apmPriceMad').value = p ? p.price_mad : 150;
      document.getElementById('apmPriceUsd').value = p ? p.price_usd : 15.0;
      document.getElementById('apmPriceEur').value = p ? p.price_eur : 14.0;
      document.getElementById('apmPriceUsdt').value = p ? p.price_usdt : 15.0;

      document.getElementById('apmDailyLimit').value = p ? p.daily_apply_limit : 50;
      document.getElementById('apmMaxAiCalls').value = p ? (p.max_ai_calls_per_day || 50) : 50;

      document.getElementById('apmIsActive').checked = p ? Boolean(p.is_active) : true;
      document.getElementById('apmIsRecommended').checked = p ? Boolean(p.is_recommended) : false;
      document.getElementById('apmCanFreelance').checked = p ? Boolean(p.can_access_freelance) : false;
      document.getElementById('apmSyncUsers').checked = true;

      const feats = (p && Array.isArray(p.features)) ? p.features.join('\n') : '';
      document.getElementById('apmFeatures').value = feats;

      modal.style.display = 'flex';
      (window.requestAnimationFrame || setTimeout)(() => modal.classList.add('open'), 16);
    }

    function closeAdminPlanModal() {
      const modal = document.getElementById('adminPlanModal');
      if (modal) {
        modal.classList.remove('open');
        setTimeout(() => {
          if (!modal.classList.contains('open')) modal.style.display = 'none';
        }, 220);
      }
    }

    async function handleAdminPlanSave(event) {
      event.preventDefault();
      const planId = document.getElementById('apmPlanId').value;
      const slug = document.getElementById('apmSlug').value.trim();
      const name = document.getElementById('apmName').value.trim();
      const badge = document.getElementById('apmBadge').value.trim();
      const billing = document.getElementById('apmBilling').value.trim();
      const description = document.getElementById('apmDescription').value.trim();

      const priceMad = parseInt(document.getElementById('apmPriceMad').value) || 0;
      const priceUsd = parseFloat(document.getElementById('apmPriceUsd').value) || 0.0;
      const priceEur = parseFloat(document.getElementById('apmPriceEur').value) || 0.0;
      const priceUsdt = parseFloat(document.getElementById('apmPriceUsdt').value) || 0.0;

      const dailyLimit = parseInt(document.getElementById('apmDailyLimit').value) || 5;
      const maxAiCalls = parseInt(document.getElementById('apmMaxAiCalls').value) || 10;

      const isActive = document.getElementById('apmIsActive').checked;
      const isRecommended = document.getElementById('apmIsRecommended').checked;
      const canFreelance = document.getElementById('apmCanFreelance').checked;
      const syncUsers = document.getElementById('apmSyncUsers').checked;

      const featuresRaw = document.getElementById('apmFeatures').value;
      const features = featuresRaw.split('\n').map(s => s.trim()).filter(Boolean);

      const payload = {
        name,
        badge,
        billing_interval: billing,
        description,
        price_mad: priceMad,
        price_usd: priceUsd,
        price_eur: priceEur,
        price_usdt: priceUsdt,
        daily_apply_limit: dailyLimit,
        max_ai_calls_per_day: maxAiCalls,
        is_active: isActive,
        is_recommended: isRecommended,
        can_access_freelance: canFreelance,
        sync_users: syncUsers,
        features
      };

      try {
        let res;
        if (planId) {
          res = await apiSend(`/api/admin/plans/${planId}`, 'PUT', payload);
        } else {
          payload.slug = slug;
          res = await apiSend('/api/admin/plans', 'POST', payload);
        }

        showToast(res.message || 'Plan saved successfully! Quotas updated in real time.', 'success');
        closeAdminPlanModal();
        await loadAdminPlans();
        loadAdminUsers();
        populateAdminCreateUserPlanOptions();

        // Refresh global client pricing data immediately
        try {
          const pubRes = await apiGet('/api/payments/methods');
          if (pubRes && pubRes.ok) {
            pricingPlansData = pubRes.plans || [];
            pricingMethodsData = pubRes.methods || {};
            renderPricingCards();
          }
        } catch (_) {}
      } catch (err) {
        showToast('Failed to save plan: ' + err.message, 'error');
      }
    }

    async function syncPlanQuotas(planId) {
      try {
        const res = await apiSend(`/api/admin/plans/${planId}/sync-quotas`, 'POST');
        showToast(res.message || 'Quotas synchronized to all users! ⚡', 'success');
        await loadAdminPlans();
        loadAdminUsers();
      } catch (err) {
        showToast('Sync failed: ' + err.message, 'error');
      }
    }

    async function syncAllPlansQuotas() {
      if (!adminCachedPlans || !adminCachedPlans.length) {
        await loadAdminPlans();
      }
      let totalSynced = 0;
      for (const p of adminCachedPlans) {
        try {
          const res = await apiSend(`/api/admin/plans/${p.id}/sync-quotas`, 'POST');
          totalSynced += (res.synced_users || 0);
        } catch (_) {}
      }
      showToast(`⚡ Synchronized quotas for all plans! (${totalSynced} candidate accounts updated)`, 'success');
      await loadAdminPlans();
      loadAdminUsers();
    }

    async function deleteAdminPlan(planId, planName) {
      if (!confirm(`Are you sure you want to delete or deactivate plan "${planName}"?`)) return;
      try {
        const res = await apiSend(`/api/admin/plans/${planId}`, 'DELETE');
        showToast(res.message || 'Plan updated', 'normal');
        await loadAdminPlans();
        loadAdminUsers();
        populateAdminCreateUserPlanOptions();
      } catch (err) {
        showToast('Action failed: ' + err.message, 'error');
      }
    }

    // =========================================================================
    // Admin Payment Gateways Configuration Handlers
    // =========================================================================

    let adminCachedGateways = [];

    async function loadAdminGateways() {
      const container = document.getElementById('adminGatewaysCards');
      if (!container) return;
      try {
        const res = await apiGet('/api/admin/gateways');
        const gws = res?.gateways || [];
        adminCachedGateways = gws;

        if (!gws.length) {
          container.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-muted)">No gateways configured.</div>';
          return;
        }

        const stripeGw = gws.find(g => g.gateway_key === 'stripe') || {};
        const paypalGw = gws.find(g => g.gateway_key === 'paypal') || {};
        const moroccoGw = gws.find(g => g.gateway_key === 'morocco_banks') || {};
        const kofiGw = gws.find(g => g.gateway_key === 'card_kofi') || {};
        const wiseGw = gws.find(g => g.gateway_key === 'wise_revolut') || {};
        const trc20Gw = gws.find(g => g.gateway_key === 'crypto_usdt_trc20') || {};
        const polyGw = gws.find(g => g.gateway_key === 'crypto_usdt_polygon') || {};
        const solGw = gws.find(g => g.gateway_key === 'crypto_solana') || {};
        const btcGw = gws.find(g => g.gateway_key === 'crypto_btc') || {};

        container.innerHTML = `
          <!-- 1. STRIPE GATEWAY CARD -->
          <div style="background:rgba(15,23,42,0.85);border:1px solid rgba(99,102,241,0.4);border-radius:14px;padding:20px;box-shadow:0 0 20px rgba(99,102,241,0.15)">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px">
              <div style="display:flex;align-items:center;gap:10px">
                <span style="font-size:24px">💳</span>
                <div>
                  <h4 style="font-size:16px;font-weight:800;color:#fff;margin:0">Stripe Card & Apple/Google Pay (Automated Instant Upgrades)</h4>
                  <div style="font-size:11.5px;color:var(--text-muted)">Accept Visa, MasterCard, Amex, Apple Pay and Google Pay via official Stripe Checkout.</div>
                </div>
              </div>
              <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:700;color:#fff;cursor:pointer;background:rgba(255,255,255,0.05);padding:6px 12px;border-radius:8px">
                <input type="checkbox" id="agwStripeEnabled" ${stripeGw.is_enabled ? 'checked' : ''}>
                <span>Enable Stripe Checkout</span>
              </label>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px">
              <div class="form-group">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Stripe Environment Mode</label>
                <select id="agwStripeMode" style="width:100%;font-size:12px">
                  <option value="test" ${stripeGw.config?.mode === 'test' ? 'selected' : ''}>Test Mode (Sandbox)</option>
                  <option value="live" ${stripeGw.config?.mode === 'live' ? 'selected' : ''}>Live Mode (Production)</option>
                </select>
              </div>
              <div class="form-group" style="grid-column:span 2">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Stripe Publishable Key (pk_test_... or pk_live_...)</label>
                <input type="text" id="agwStripePk" value="${esc(stripeGw.config?.publishable_key || '')}" placeholder="pk_test_..." style="width:100%;font-family:'JetBrains Mono';font-size:11.5px">
              </div>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">
              <div class="form-group">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Stripe Secret Key (sk_test_... or sk_live_...)</label>
                <input type="password" id="agwStripeSk" value="${esc(stripeGw.config?.secret_key || '')}" placeholder="sk_test_..." style="width:100%;font-family:'JetBrains Mono';font-size:11.5px">
              </div>
              <div class="form-group">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Stripe Webhook Secret (whsec_...)</label>
                <input type="password" id="agwStripeWhSec" value="${esc(stripeGw.config?.webhook_secret || '')}" placeholder="whsec_..." style="width:100%;font-family:'JetBrains Mono';font-size:11.5px">
              </div>
            </div>

            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
              <button type="button" class="btn btn-sm" onclick="testAdminStripeConnection()" style="border-color:rgba(99,102,241,0.5);color:#a5b4fc;font-weight:700">
                ⚡ Test Stripe Connection
              </button>
              <button type="button" class="btn btn-sm btn-primary" onclick="saveAdminStripeGateway()" style="padding:7px 18px;font-weight:700">
                💾 Save Stripe Settings
              </button>
            </div>
          </div>

          <!-- 2. PAYPAL GATEWAY CARD -->
          <div style="background:rgba(15,23,42,0.85);border:1px solid rgba(56,189,248,0.3);border-radius:14px;padding:20px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px">
              <div style="display:flex;align-items:center;gap:10px">
                <span style="font-size:24px">🅿️</span>
                <div>
                  <h4 style="font-size:16px;font-weight:800;color:#fff;margin:0">PayPal & Global Cards</h4>
                  <div style="font-size:11.5px;color:var(--text-muted)">PayPal.me links and direct PayPal transfers.</div>
                </div>
              </div>
              <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:700;color:#fff;cursor:pointer;background:rgba(255,255,255,0.05);padding:6px 12px;border-radius:8px">
                <input type="checkbox" id="agwPaypalEnabled" ${paypalGw.is_enabled ? 'checked' : ''}>
                <span>Enable PayPal</span>
              </label>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">
              <div class="form-group">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">PayPal.me Checkout URL</label>
                <input type="text" id="agwPaypalMe" value="${esc(paypalGw.config?.paypal_me_url || '')}" placeholder="https://paypal.me/YourBrand" style="width:100%">
              </div>
              <div class="form-group">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">PayPal Account Email</label>
                <input type="email" id="agwPaypalEmail" value="${esc(paypalGw.config?.paypal_email || '')}" placeholder="payments@example.com" style="width:100%">
              </div>
            </div>

            <div style="display:flex;justify-content:flex-end">
              <button type="button" class="btn btn-sm btn-cyan" onclick="saveAdminPaypalGateway()" style="font-weight:700;padding:7px 18px">
                💾 Save PayPal Settings
              </button>
            </div>
          </div>

          <!-- 3. MOROCCO LOCAL BANK WIRE & CASHPLUS -->
          <div style="background:rgba(15,23,42,0.85);border:1px solid rgba(239,68,68,0.3);border-radius:14px;padding:20px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px">
              <div style="display:flex;align-items:center;gap:10px">
                <span style="font-size:24px">🇲🇦</span>
                <div>
                  <h4 style="font-size:16px;font-weight:800;color:#fff;margin:0">Morocco Local Wire & CashPlus (MAD)</h4>
                  <div style="font-size:11.5px;color:var(--text-muted)">CIH Bank RIB, Attijariwafa Bank RIB, and CashPlus / Wafacash branch payment info.</div>
                </div>
              </div>
              <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:700;color:#fff;cursor:pointer;background:rgba(255,255,255,0.05);padding:6px 12px;border-radius:8px">
                <input type="checkbox" id="agwMoroccoEnabled" ${moroccoGw.is_enabled ? 'checked' : ''}>
                <span>Enable Morocco Bank Wire</span>
              </label>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
              <div class="form-group">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">CIH Bank RIB (24 digits)</label>
                <input type="text" id="agwMoroccoCih" value="${esc(moroccoGw.config?.cih_rib || '')}" placeholder="230 780 0000000000000000 00" style="width:100%;font-family:'JetBrains Mono'">
              </div>
              <div class="form-group">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Attijariwafa Bank RIB (24 digits)</label>
                <input type="text" id="agwMoroccoAttijari" value="${esc(moroccoGw.config?.attijari_rib || '')}" placeholder="007 780 0000000000000000 00" style="width:100%;font-family:'JetBrains Mono'">
              </div>
            </div>

            <div class="form-group" style="margin-bottom:14px">
              <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">CashPlus / Wafacash Agency Details & Beneficiary CIN</label>
              <input type="text" id="agwMoroccoCashplus" value="${esc(moroccoGw.config?.cashplus_info || '')}" placeholder="Hamza Oukhouya (Casablanca, Morocco - CIN: ...)" style="width:100%">
            </div>

            <div style="display:flex;justify-content:flex-end">
              <button type="button" class="btn btn-sm btn-cyan" onclick="saveAdminMoroccoGateway()" style="font-weight:700;padding:7px 18px">
                💾 Save Morocco Bank Settings
              </button>
            </div>
          </div>

          <!-- 4. CRYPTO WALLETS -->
          <div style="background:rgba(15,23,42,0.85);border:1px solid rgba(16,185,129,0.3);border-radius:14px;padding:20px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px">
              <div style="display:flex;align-items:center;gap:10px">
                <span style="font-size:24px">🪙</span>
                <div>
                  <h4 style="font-size:16px;font-weight:800;color:#fff;margin:0">Crypto Payment Wallets (USDT, Solana, BTC)</h4>
                  <div style="font-size:11.5px;color:var(--text-muted)">Global peer-to-peer crypto transfers with instant TXID submission.</div>
                </div>
              </div>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
              <div class="form-group">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                  <label style="font-size:11px;color:var(--text-muted)">USDT (TRC-20 Tron)</label>
                  <label style="font-size:11px;cursor:pointer"><input type="checkbox" id="agwTrc20Enabled" ${trc20Gw.is_enabled ? 'checked' : ''}> Enable</label>
                </div>
                <input type="text" id="agwTrc20Addr" value="${esc(trc20Gw.config?.address || '')}" placeholder="T..." style="width:100%;font-family:'JetBrains Mono';font-size:11.5px">
              </div>
              <div class="form-group">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                  <label style="font-size:11px;color:var(--text-muted)">USDT / USDC (Polygon)</label>
                  <label style="font-size:11px;cursor:pointer"><input type="checkbox" id="agwPolyEnabled" ${polyGw.is_enabled ? 'checked' : ''}> Enable</label>
                </div>
                <input type="text" id="agwPolyAddr" value="${esc(polyGw.config?.address || '')}" placeholder="0x..." style="width:100%;font-family:'JetBrains Mono';font-size:11.5px">
              </div>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">
              <div class="form-group">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                  <label style="font-size:11px;color:var(--text-muted)">Solana (SOL / USDC)</label>
                  <label style="font-size:11px;cursor:pointer"><input type="checkbox" id="agwSolEnabled" ${solGw.is_enabled ? 'checked' : ''}> Enable</label>
                </div>
                <input type="text" id="agwSolAddr" value="${esc(solGw.config?.address || '')}" placeholder="7x..." style="width:100%;font-family:'JetBrains Mono';font-size:11.5px">
              </div>
              <div class="form-group">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                  <label style="font-size:11px;color:var(--text-muted)">Bitcoin (BTC)</label>
                  <label style="font-size:11px;cursor:pointer"><input type="checkbox" id="agwBtcEnabled" ${btcGw.is_enabled ? 'checked' : ''}> Enable</label>
                </div>
                <input type="text" id="agwBtcAddr" value="${esc(btcGw.config?.address || '')}" placeholder="bc1q..." style="width:100%;font-family:'JetBrains Mono';font-size:11.5px">
              </div>
            </div>

            <div style="display:flex;justify-content:flex-end">
              <button type="button" class="btn btn-sm btn-cyan" onclick="saveAdminCryptoGateways()" style="font-weight:700;padding:7px 18px">
                💾 Save Crypto Wallets
              </button>
            </div>
          </div>

          <!-- 5. WISE, REVOLUT & KO-FI -->
          <div style="background:rgba(15,23,42,0.85);border:1px solid rgba(245,158,11,0.3);border-radius:14px;padding:20px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px">
              <div style="display:flex;align-items:center;gap:10px">
                <span style="font-size:24px">🌐</span>
                <div>
                  <h4 style="font-size:16px;font-weight:800;color:#fff;margin:0">Wise, Revolut & Ko-fi Direct Cards</h4>
                  <div style="font-size:11.5px;color:var(--text-muted)">Alternative checkout methods with zero incorporation hurdles.</div>
                </div>
              </div>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:14px">
              <div class="form-group">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Wise Account Email</label>
                <input type="email" id="agwWiseEmail" value="${esc(wiseGw.config?.wise_email || '')}" placeholder="wise@example.com" style="width:100%">
              </div>
              <div class="form-group">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Revolut Tag</label>
                <input type="text" id="agwRevolutTag" value="${esc(wiseGw.config?.revolut_tag || '')}" placeholder="@yourtag" style="width:100%">
              </div>
              <div class="form-group">
                <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px">Ko-fi Checkout URL</label>
                <input type="text" id="agwKofiUrl" value="${esc(kofiGw.config?.checkout_url || '')}" placeholder="https://ko-fi.com/yourpage" style="width:100%">
              </div>
            </div>

            <div style="display:flex;justify-content:flex-end">
              <button type="button" class="btn btn-sm btn-cyan" onclick="saveAdminP2pGateways()" style="font-weight:700;padding:7px 18px">
                💾 Save P2P & Ko-fi Settings
              </button>
            </div>
          </div>
        `;
      } catch (err) {
        console.error('Failed to load admin gateways:', err);
        container.innerHTML = `<div style="text-align:center;padding:30px;color:#f87171">Failed to load gateways: ${esc(err.message)}</div>`;
      }
    }

    async function saveAdminStripeGateway() {
      const isEnabled = document.getElementById('agwStripeEnabled')?.checked || false;
      const mode = document.getElementById('agwStripeMode')?.value || 'test';
      const pk = document.getElementById('agwStripePk')?.value.trim() || '';
      const sk = document.getElementById('agwStripeSk')?.value.trim() || '';
      const whSec = document.getElementById('agwStripeWhSec')?.value.trim() || '';

      try {
        const res = await apiSend('/api/admin/gateways/stripe', 'PUT', {
          is_enabled: isEnabled,
          config: {
            publishable_key: pk,
            secret_key: sk,
            webhook_secret: whSec,
            mode: mode
          }
        });
        showToast(res.message || 'Stripe configuration saved successfully!', 'success');
        refreshClientPaymentMethods();
      } catch (err) {
        showToast('Failed to save Stripe: ' + err.message, 'error');
      }
    }

    async function testAdminStripeConnection() {
      const sk = document.getElementById('agwStripeSk')?.value.trim() || '';
      showToast('Testing Stripe connection...', 'normal');
      try {
        const res = await apiSend('/api/admin/gateways/test-stripe', 'POST', { secret_key: sk });
        if (res.ok) {
          alert(`✅ Stripe Connection Succeeded!\n\n${res.message}`);
          showToast('Stripe verified successfully!', 'success');
        } else {
          alert(`❌ Stripe Connection Failed:\n\n${res.message}`);
          showToast('Stripe test failed: ' + res.message, 'error');
        }
      } catch (err) {
        alert(`❌ Stripe Test Error:\n\n${err.message}`);
        showToast('Stripe test error: ' + err.message, 'error');
      }
    }

    async function saveAdminPaypalGateway() {
      const isEnabled = document.getElementById('agwPaypalEnabled')?.checked || false;
      const meUrl = document.getElementById('agwPaypalMe')?.value.trim() || '';
      const email = document.getElementById('agwPaypalEmail')?.value.trim() || '';

      try {
        const res = await apiSend('/api/admin/gateways/paypal', 'PUT', {
          is_enabled: isEnabled,
          config: { paypal_me_url: meUrl, paypal_email: email }
        });
        showToast(res.message || 'PayPal settings saved!', 'success');
        refreshClientPaymentMethods();
      } catch (err) {
        showToast('Failed to save PayPal: ' + err.message, 'error');
      }
    }

    async function saveAdminMoroccoGateway() {
      const isEnabled = document.getElementById('agwMoroccoEnabled')?.checked || false;
      const cih = document.getElementById('agwMoroccoCih')?.value.trim() || '';
      const attijari = document.getElementById('agwMoroccoAttijari')?.value.trim() || '';
      const cashplus = document.getElementById('agwMoroccoCashplus')?.value.trim() || '';

      try {
        const res = await apiSend('/api/admin/gateways/morocco_banks', 'PUT', {
          is_enabled: isEnabled,
          config: { cih_rib: cih, attijari_rib: attijari, cashplus_info: cashplus }
        });
        showToast(res.message || 'Moroccan banking details saved!', 'success');
        refreshClientPaymentMethods();
      } catch (err) {
        showToast('Failed to save Morocco banking: ' + err.message, 'error');
      }
    }

    async function saveAdminCryptoGateways() {
      const trc20Enabled = document.getElementById('agwTrc20Enabled')?.checked || false;
      const trc20Addr = document.getElementById('agwTrc20Addr')?.value.trim() || '';
      const polyEnabled = document.getElementById('agwPolyEnabled')?.checked || false;
      const polyAddr = document.getElementById('agwPolyAddr')?.value.trim() || '';
      const solEnabled = document.getElementById('agwSolEnabled')?.checked || false;
      const solAddr = document.getElementById('agwSolAddr')?.value.trim() || '';
      const btcEnabled = document.getElementById('agwBtcEnabled')?.checked || false;
      const btcAddr = document.getElementById('agwBtcAddr')?.value.trim() || '';

      try {
        await apiSend('/api/admin/gateways/crypto_usdt_trc20', 'PUT', { is_enabled: trc20Enabled, config: { address: trc20Addr, network: 'Tron Network (TRC-20)' } });
        await apiSend('/api/admin/gateways/crypto_usdt_polygon', 'PUT', { is_enabled: polyEnabled, config: { address: polyAddr, network: 'Polygon Network (POL / MATIC)' } });
        await apiSend('/api/admin/gateways/crypto_solana', 'PUT', { is_enabled: solEnabled, config: { address: solAddr, network: 'Solana (SPL)' } });
        await apiSend('/api/admin/gateways/crypto_btc', 'PUT', { is_enabled: btcEnabled, config: { address: btcAddr, network: 'Bitcoin Mainnet' } });
        showToast('All crypto wallet addresses saved successfully!', 'success');
        refreshClientPaymentMethods();
      } catch (err) {
        showToast('Failed to save crypto wallets: ' + err.message, 'error');
      }
    }

    async function saveAdminP2pGateways() {
      const wiseEmail = document.getElementById('agwWiseEmail')?.value.trim() || '';
      const revolutTag = document.getElementById('agwRevolutTag')?.value.trim() || '';
      const kofiUrl = document.getElementById('agwKofiUrl')?.value.trim() || '';

      try {
        await apiSend('/api/admin/gateways/wise_revolut', 'PUT', { is_enabled: true, config: { wise_email: wiseEmail, revolut_tag: revolutTag } });
        await apiSend('/api/admin/gateways/card_kofi', 'PUT', { is_enabled: true, config: { checkout_url: kofiUrl } });
        showToast('P2P & Ko-fi settings saved successfully!', 'success');
        refreshClientPaymentMethods();
      } catch (err) {
        showToast('Failed to save P2P: ' + err.message, 'error');
      }
    }

    async function refreshClientPaymentMethods() {
      try {
        const pubRes = await apiGet('/api/payments/methods');
        if (pubRes && pubRes.ok) {
          pricingPlansData = pubRes.plans || [];
          pricingMethodsData = pubRes.methods || {};
          syncPaymentChannelsContent();
          renderPricingCards();
        }
      } catch (_) {}
    }

    // =========================================================================
    // Automated Stripe Card Checkout
    // =========================================================================

    async function initiateStripeCheckout() {
      const planSlug = document.getElementById('payPlanName')?.value || 'pro';
      const btn = document.getElementById('btnStripeCheckoutAction');
      if (btn) {
        btn.disabled = true;
        btn.textContent = 'Preparing Stripe Checkout... ⏳';
      }

      try {
        const res = await apiSend('/api/payments/stripe/create-checkout-session', 'POST', {
          plan_slug: planSlug
        });
        if (res && res.checkout_url) {
          showToast('Redirecting to official Stripe Checkout...', 'normal');
          window.location.href = res.checkout_url;
        } else {
          throw new Error(res.message || 'No checkout URL returned from Stripe');
        }
      } catch (err) {
        alert('Stripe Checkout Error:\n' + err.message);
        showToast('Stripe Checkout failed: ' + err.message, 'error');
        if (btn) {
          btn.disabled = false;
          btn.textContent = '🚀 Pay with Stripe Checkout →';
        }
      }
    }

    async function checkStripePaymentReturn() {
      const urlParams = new URLSearchParams(window.location.search);
      const sessionId = urlParams.get('session_id');
      const hash = window.location.hash || '';

      if (sessionId || hash.includes('payment_success')) {
        const targetSessionId = sessionId || (hash.includes('session_id=') ? hash.split('session_id=')[1].split('&')[0] : '');
        if (targetSessionId) {
          try {
            const res = await apiGet(`/api/payments/stripe/verify-session/${targetSessionId}`);
            if (res && res.ok) {
              showToast(res.message || 'Subscription upgraded via Stripe! 🎉', 'success');
              window.history.replaceState({}, document.title, window.location.pathname + '#pricing');
              await initAuth();
              renderPricingCards();
            }
          } catch (_) {}
        }
      }
    }

    // -------------------------------------------------------------------------
    // User Settings & Custom Environment Variables Handlers
    // -------------------------------------------------------------------------

    async function loadUserSettings() {
      try {
        const res = await apiGet('/api/user/settings');
        const s = res?.settings || {};
        const setVal = (id, val) => {
          const el = document.getElementById(id);
          if (el && val !== undefined && val !== null) el.value = val;
        };

        setVal('usSenderName', s.sender_name || '');
        setVal('usSenderEmail', s.sender_email || '');
        setVal('usSmtpHost', s.smtp_host || '');
        setVal('usSmtpPort', s.smtp_port || 587);
        setVal('usImapHost', s.imap_host || '');
        setVal('usImapPort', s.imap_port || 993);
        setVal('usAlertEmail', s.alert_email || '');
        setVal('usTelegramChatId', s.telegram_chat_id || '');
        setVal('usPhoneNumber', s.phone_number || '');
        setVal('usPortfolioUrl', s.portfolio_url || '');
        setVal('usLinkedinUrl', s.linkedin_url || '');
        setVal('usGithubUrl', s.github_url || '');
        setVal('usMinMatchScore', s.min_match_score || 65);
        if (s.auto_apply_mode) setVal('usAutoApplyMode', s.auto_apply_mode);

        setVal('usLinkedinCookie', s.linkedin_cookie || (s.custom_env && s.custom_env.LINKEDIN_COOKIE) || '');
        setVal('usIndeedCookie', s.indeed_cookie || (s.custom_env && s.custom_env.INDEED_COOKIE) || '');
        if (document.getElementById('usAutoApplyLinkedin')) {
          document.getElementById('usAutoApplyLinkedin').checked = s.auto_apply_linkedin_enabled !== false;
        }
        if (document.getElementById('usAutoApplyIndeed')) {
          document.getElementById('usAutoApplyIndeed').checked = s.auto_apply_indeed_enabled !== false;
        }

        // Render custom environment variables table
        renderEnvVars(s.custom_env || {});

        try {
          await loadIntegrationsStatus();
        } catch (_) {}
      } catch (err) {
        console.error('Failed to load user settings:', err);
      }
    }

    function renderEnvVars(envObj) {
      const tbody = document.getElementById('userEnvTableBody');
      if (!tbody) return;
      const entries = Object.entries(envObj);
      if (!entries.length) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:16px;color:var(--text-muted)">No custom environment variables configured. Click "+ Add Variable" to add one.</td></tr>';
        return;
      }
      tbody.innerHTML = entries.map(([k, v]) => `
        <tr class="env-var-row">
          <td><input type="text" class="env-key form-control" value="${esc(k)}" placeholder="VAR_NAME" style="width:100%;font-family:'JetBrains Mono'"></td>
          <td><input type="text" class="env-val form-control" value="${esc(String(v))}" placeholder="Value" style="width:100%"></td>
          <td><button class="btn btn-sm btn-icon" onclick="removeEnvRow(this)" style="color:#f87171" title="Remove">✕</button></td>
        </tr>
      `).join('');
    }

    function addEnvRow(key = '', val = '') {
      const tbody = document.getElementById('userEnvTableBody');
      if (!tbody) return;
      // If table is showing empty placeholder row, clear it
      if (tbody.querySelector('td[colspan="3"]')) {
        tbody.innerHTML = '';
      }
      const tr = document.createElement('tr');
      tr.className = 'env-var-row';
      tr.innerHTML = `
        <td><input type="text" class="env-key form-control" value="${esc(key)}" placeholder="VAR_NAME" style="width:100%;font-family:'JetBrains Mono'"></td>
        <td><input type="text" class="env-val form-control" value="${esc(val)}" placeholder="Value" style="width:100%"></td>
        <td><button class="btn btn-sm btn-icon" onclick="removeEnvRow(this)" style="color:#f87171" title="Remove">✕</button></td>
      `;
      tbody.appendChild(tr);
    }

    function removeEnvRow(btn) {
      const row = btn.closest('tr');
      if (row) row.remove();
      const tbody = document.getElementById('userEnvTableBody');
      if (tbody && !tbody.children.length) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:16px;color:var(--text-muted)">No custom environment variables configured. Click "+ Add Variable" to add one.</td></tr>';
      }
    }

    async function saveCustomEnvVars() {
      const rows = document.querySelectorAll('#userEnvTableBody tr.env-var-row');
      const envObj = {};
      rows.forEach(r => {
        const k = r.querySelector('.env-key')?.value.trim();
        const v = r.querySelector('.env-val')?.value.trim() ?? '';
        if (k) envObj[k] = v;
      });

      try {
        const res = await apiSend('/api/user/settings/env', 'PUT', { env_vars: envObj });
        showToast(res.message || 'Custom environment variables saved! ✓', 'success');
      } catch (err) {
        showToast('Failed to save environment variables: ' + err.message, 'error');
      }
    }

    async function saveUserSettings() {
      const payload = {};
      const getVal = (id) => document.getElementById(id)?.value?.trim();

      const senderName = getVal('usSenderName');
      if (senderName) payload.sender_name = senderName;

      const senderEmail = getVal('usSenderEmail');
      if (senderEmail) payload.sender_email = senderEmail;

      const smtpHost = getVal('usSmtpHost');
      if (smtpHost) payload.smtp_host = smtpHost;

      const smtpPort = parseInt(getVal('usSmtpPort') || '587', 10);
      if (smtpPort) payload.smtp_port = smtpPort;

      const smtpPassword = getVal('usSmtpPassword');
      if (smtpPassword) payload.smtp_password = smtpPassword;

      const imapHost = getVal('usImapHost');
      if (imapHost) payload.imap_host = imapHost;

      const imapPort = parseInt(getVal('usImapPort') || '993', 10);
      if (imapPort) payload.imap_port = imapPort;

      const imapPassword = getVal('usImapPassword');
      if (imapPassword) payload.imap_password = imapPassword;

      const alertEmail = getVal('usAlertEmail');
      if (alertEmail) payload.alert_email = alertEmail;

      const tgToken = getVal('usTelegramBotToken');
      if (tgToken) payload.telegram_bot_token = tgToken;

      const tgChatId = getVal('usTelegramChatId');
      if (tgChatId) payload.telegram_chat_id = tgChatId;

      const phone = getVal('usPhoneNumber');
      if (phone) payload.phone_number = phone;

      const portfolio = getVal('usPortfolioUrl');
      if (portfolio) payload.portfolio_url = portfolio;

      const linkedin = getVal('usLinkedinUrl');
      if (linkedin) payload.linkedin_url = linkedin;

      const github = getVal('usGithubUrl');
      if (github) payload.github_url = github;

      const minScore = parseInt(getVal('usMinMatchScore') || '65', 10);
      if (!isNaN(minScore)) payload.min_match_score = minScore;

      const autoMode = document.getElementById('usAutoApplyMode')?.value;
      if (autoMode) payload.auto_apply_mode = autoMode;

      const liCookie = getVal('usLinkedinCookie');
      if (liCookie && liCookie !== '••••••••') payload.linkedin_cookie = liCookie;

      const indCookie = getVal('usIndeedCookie');
      if (indCookie && indCookie !== '••••••••') payload.indeed_cookie = indCookie;

      const liEnabled = document.getElementById('usAutoApplyLinkedin')?.checked;
      if (liEnabled !== undefined) payload.auto_apply_linkedin_enabled = liEnabled;

      const indEnabled = document.getElementById('usAutoApplyIndeed')?.checked;
      if (indEnabled !== undefined) payload.auto_apply_indeed_enabled = indEnabled;

      // Also gather custom env vars into JSON
      const rows = document.querySelectorAll('#userEnvTableBody tr.env-var-row');
      const envObj = {};
      rows.forEach(r => {
        const k = r.querySelector('.env-key')?.value.trim();
        const v = r.querySelector('.env-val')?.value.trim() ?? '';
        if (k) envObj[k] = v;
      });
      payload.custom_env_json = JSON.stringify(envObj);

      try {
        const res = await apiSend('/api/user/settings', 'PUT', payload);
        showToast(res.message || 'User settings saved successfully! ✓', 'success');
        try {
          await loadIntegrationsStatus();
        } catch (_) {}
      } catch (err) {
        showToast('Failed to save settings: ' + err.message, 'error');
      }
    }

    // ---------------------------------------------------------------------------
    // Candidate Portal Integrations (LinkedIn Easy Apply & Indeed Apply)
    // ---------------------------------------------------------------------------

    async function loadIntegrationsStatus(notify = false) {
      try {
        const res = await apiGet('/api/user/integrations');
        if (!res || !res.ok) return;

        const updateBadges = (ids, isConnected) => {
          ids.forEach(id => {
            const b = document.getElementById(id);
            if (!b) return;
            if (isConnected) {
              b.textContent = 'Connected ✓';
              b.style.background = 'rgba(16,185,129,0.15)';
              b.style.color = '#10b981';
              b.style.border = '1px solid rgba(16,185,129,0.3)';
            } else {
              b.textContent = 'Not Linked';
              b.style.background = 'rgba(239,68,68,0.1)';
              b.style.color = '#ef4444';
              b.style.border = '1px solid rgba(239,68,68,0.2)';
            }
          });
        };

        const updateVisibility = (ids, show) => {
          ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.style.display = show ? 'inline-block' : 'none';
          });
        };

        const updateTexts = (ids, text) => {
          ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = text;
          });
        };

        const updateChecks = (ids, checked) => {
          ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.checked = checked;
          });
        };

        // 1. LinkedIn UI Sync (Settings & Candidate Profile)
        const li = res.linkedin || {};
        updateBadges(['liStatusBadge', 'profLiStatusBadge'], li.connected);
        updateVisibility(['btnDisconnectLi', 'profBtnDisconnectLi'], li.connected);

        let liVer = li.connected ? 'Active' : 'Never';
        if (li.verified_at) {
          try {
            liVer = new Date(li.verified_at).toLocaleDateString() + ' ' + new Date(li.verified_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
          } catch (_) { liVer = li.verified_at; }
        }
        updateTexts(['liVerifiedAt', 'profLiVerifiedAt'], liVer);
        updateTexts(['liCookiePreview', 'profLiCookiePreview'], li.cookie_preview || (li.connected ? '••••••••' : 'None'));

        if (li.auto_apply_enabled !== undefined) {
          updateChecks(['usAutoApplyLinkedin', 'profAutoApplyLinkedin'], li.auto_apply_enabled !== false);
        }

        ['usLinkedinCookie', 'profLinkedinCookie'].forEach(id => {
          const inp = document.getElementById(id);
          if (inp && li.connected && !inp.value) {
            inp.placeholder = 'Connected (enter new li_at to overwrite)';
          }
        });

        // 2. Indeed UI Sync (Settings & Candidate Profile)
        const ind = res.indeed || {};
        updateBadges(['indStatusBadge', 'profIndStatusBadge'], ind.connected);
        updateVisibility(['btnDisconnectInd', 'profBtnDisconnectInd'], ind.connected);

        let indVer = ind.connected ? 'Active' : 'Never';
        if (ind.verified_at) {
          try {
            indVer = new Date(ind.verified_at).toLocaleDateString() + ' ' + new Date(ind.verified_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
          } catch (_) { indVer = ind.verified_at; }
        }
        updateTexts(['indVerifiedAt', 'profIndVerifiedAt'], indVer);
        updateTexts(['indCookiePreview', 'profIndCookiePreview'], ind.cookie_preview || (ind.connected ? '••••••••' : 'None'));

        if (ind.auto_apply_enabled !== undefined) {
          updateChecks(['usAutoApplyIndeed', 'profAutoApplyIndeed'], ind.auto_apply_enabled !== false);
        }

        ['usIndeedCookie', 'profIndeedCookie'].forEach(id => {
          const inp = document.getElementById(id);
          if (inp && ind.connected && !inp.value) {
            inp.placeholder = 'Connected (enter new cookie to overwrite)';
          }
        });

        if (notify) {
          showToast('Portal connection status updated ✓', 'success');
        }
      } catch (err) {
        console.error('Failed to load portal integration statuses:', err);
      }
    }

    async function connectPortal(platform, scope = '') {
      platform = (platform || '').toLowerCase().trim();
      const isLi = platform === 'linkedin';
      const isProf = scope === 'prof';

      const inputId = isProf 
        ? (isLi ? 'profLinkedinCookie' : 'profIndeedCookie') 
        : (isLi ? 'usLinkedinCookie' : 'usIndeedCookie');
      const fallbackInputId = isProf 
        ? (isLi ? 'usLinkedinCookie' : 'usIndeedCookie')
        : (isLi ? 'profLinkedinCookie' : 'profIndeedCookie');

      const autoId = isProf
        ? (isLi ? 'profAutoApplyLinkedin' : 'profAutoApplyIndeed')
        : (isLi ? 'usAutoApplyLinkedin' : 'usAutoApplyIndeed');

      const btnId = isProf
        ? (isLi ? 'profBtnConnectLi' : 'profBtnConnectInd')
        : (isLi ? 'btnConnectLi' : 'btnConnectInd');

      const feedbackId = isProf
        ? (isLi ? 'profLiFeedbackBox' : 'profIndFeedbackBox')
        : (isLi ? 'liFeedbackBox' : 'indFeedbackBox');

      const cookieVal = (document.getElementById(inputId)?.value || document.getElementById(fallbackInputId)?.value || '').trim();
      const autoVal = document.getElementById(autoId)?.checked !== false;
      const btn = document.getElementById(btnId);
      const feedback = document.getElementById(feedbackId);

      if (!cookieVal) {
        showToast(`Please enter your ${isLi ? 'LinkedIn li_at' : 'Indeed session'} cookie.`, 'error');
        return;
      }

      if (btn) {
        btn.disabled = true;
        btn.textContent = 'Connecting...';
      }

      try {
        const payload = {
          cookie: cookieVal,
          auto_apply: autoVal,
        };
        if (isLi) {
          payload.profile_url = (document.getElementById('usLinkedinUrl')?.value || '').trim();
        }

        const res = await apiSend(`/api/user/integrations/${platform}/connect`, 'POST', payload);
        showToast(res.message || `${platform.toUpperCase()} connected successfully! ✓`, 'success');

        const allFeedbacks = isLi 
          ? [document.getElementById('liFeedbackBox'), document.getElementById('profLiFeedbackBox')]
          : [document.getElementById('indFeedbackBox'), document.getElementById('profIndFeedbackBox')];

        allFeedbacks.forEach(fb => {
          if (fb) {
            fb.style.display = 'block';
            fb.style.background = 'rgba(16,185,129,0.15)';
            fb.style.color = '#10b981';
            fb.style.border = '1px solid rgba(16,185,129,0.3)';
            fb.textContent = res.message || 'Connected successfully! Ready for auto-apply.';
          }
        });

        // Clear input to not show raw cookie
        const inputIds = isLi 
          ? ['usLinkedinCookie', 'profLinkedinCookie'] 
          : ['usIndeedCookie', 'profIndeedCookie'];
        inputIds.forEach(id => {
          const inp = document.getElementById(id);
          if (inp) inp.value = '';
        });

        await loadIntegrationsStatus();
      } catch (err) {
        showToast(`Failed to connect ${platform}: ` + err.message, 'error');
        if (feedback) {
          feedback.style.display = 'block';
          feedback.style.background = 'rgba(239,68,68,0.15)';
          feedback.style.color = '#ef4444';
          feedback.style.border = '1px solid rgba(239,68,68,0.3)';
          feedback.textContent = err.message || 'Connection failed';
        }
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = '🔗 Save & Connect';
        }
      }
    }

    async function disconnectPortal(platform) {
      platform = (platform || '').toLowerCase().trim();
      const isLi = platform === 'linkedin';
      const name = isLi ? 'LinkedIn' : 'Indeed';
      if (!confirm(`Are you sure you want to disconnect your ${name} account? Auto-apply on ${name} will be paused.`)) {
        return;
      }

      try {
        const res = await apiSend(`/api/user/integrations/${platform}/disconnect`, 'POST', {});
        showToast(res.message || `${name} account disconnected.`, 'info');

        const inputIds = isLi 
          ? ['usLinkedinCookie', 'profLinkedinCookie'] 
          : ['usIndeedCookie', 'profIndeedCookie'];
        inputIds.forEach(id => {
          const inp = document.getElementById(id);
          if (inp) inp.value = '';
        });

        const feedbackIds = isLi 
          ? ['liFeedbackBox', 'profLiFeedbackBox'] 
          : ['indFeedbackBox', 'profIndFeedbackBox'];
        feedbackIds.forEach(id => {
          const fb = document.getElementById(id);
          if (fb) fb.style.display = 'none';
        });

        await loadIntegrationsStatus();
      } catch (err) {
        showToast(`Failed to disconnect ${name}: ` + err.message, 'error');
      }
    }

    async function testPortalConnection(platform, scope = '') {
      platform = (platform || '').toLowerCase().trim();
      const isLi = platform === 'linkedin';
      const isProf = scope === 'prof';

      const inputId = isProf 
        ? (isLi ? 'profLinkedinCookie' : 'profIndeedCookie') 
        : (isLi ? 'usLinkedinCookie' : 'usIndeedCookie');
      const fallbackInputId = isProf 
        ? (isLi ? 'usLinkedinCookie' : 'usIndeedCookie')
        : (isLi ? 'profLinkedinCookie' : 'profIndeedCookie');

      const btnId = isProf
        ? (isLi ? 'profBtnTestLi' : 'profBtnTestInd')
        : (isLi ? 'btnTestLi' : 'btnTestInd');

      const feedbackId = isProf
        ? (isLi ? 'profLiFeedbackBox' : 'profIndFeedbackBox')
        : (isLi ? 'liFeedbackBox' : 'indFeedbackBox');

      const cookieVal = (document.getElementById(inputId)?.value || document.getElementById(fallbackInputId)?.value || '').trim();
      const btn = document.getElementById(btnId);
      const feedback = document.getElementById(feedbackId);

      if (btn) {
        btn.disabled = true;
        btn.textContent = 'Verifying...';
      }
      if (feedback) {
        feedback.style.display = 'block';
        feedback.style.background = 'rgba(255,255,255,0.05)';
        feedback.style.color = 'var(--text-muted)';
        feedback.style.border = '1px solid var(--border)';
        feedback.textContent = `Testing session validity for ${isLi ? 'LinkedIn' : 'Indeed'}...`;
      }

      try {
        const res = await apiSend('/api/user/integrations/verify', 'POST', {
          platform: platform,
          cookie: cookieVal || undefined,
        });

        const allFeedbacks = isLi 
          ? [document.getElementById('liFeedbackBox'), document.getElementById('profLiFeedbackBox')]
          : [document.getElementById('indFeedbackBox'), document.getElementById('profIndFeedbackBox')];

        if (res.connected && res.ok) {
          showToast(res.message, 'success');
          allFeedbacks.forEach(fb => {
            if (fb) {
              fb.style.display = 'block';
              fb.style.background = 'rgba(16,185,129,0.15)';
              fb.style.color = '#10b981';
              fb.style.border = '1px solid rgba(16,185,129,0.3)';
              fb.textContent = res.message;
            }
          });
        } else {
          showToast(res.message, 'warning');
          allFeedbacks.forEach(fb => {
            if (fb) {
              fb.style.display = 'block';
              fb.style.background = 'rgba(239,68,68,0.15)';
              fb.style.color = '#ef4444';
              fb.style.border = '1px solid rgba(239,68,68,0.3)';
              fb.textContent = res.message;
            }
          });
        }
      } catch (err) {
        showToast('Verification check error: ' + err.message, 'error');
        if (feedback) {
          feedback.style.background = 'rgba(239,68,68,0.15)';
          feedback.style.color = '#ef4444';
          feedback.style.border = '1px solid rgba(239,68,68,0.3)';
          feedback.textContent = err.message;
        }
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = '⚡ Test Session';
        }
      }
    }

    function toggleGuide(guideId) {
      const el = document.getElementById(guideId);
      if (!el) return;
      el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }

    function togglePasswordVisibility(inputId, btnEl) {
      const inp = document.getElementById(inputId);
      if (!inp) return;
      if (inp.type === 'password') {
        inp.type = 'text';
        if (btnEl) btnEl.textContent = '🙈';
      } else {
        inp.type = 'password';
        if (btnEl) btnEl.textContent = '👁️';
      }
    }

    async function loadAdminLogs() {
      const consoleEl = document.getElementById('adminLogConsole');
      if (!consoleEl) return;
      try {
        const level = document.getElementById('adminLogLevel')?.value || 'ALL';
        const q = document.getElementById('adminLogSearch')?.value || '';
        const res = await apiGet(`/api/admin/logs?level=${encodeURIComponent(level)}&q=${encodeURIComponent(q)}&limit=150`);
        if (!res || !res.logs || !res.logs.length) {
          consoleEl.textContent = 'No matching operational logs in buffer.';
          return;
        }
        consoleEl.innerHTML = res.logs.map(log => {
          let color = '#94a3b8';
          if (log.level === 'ERROR') color = '#f87171';
          else if (log.level === 'WARNING') color = '#facc15';
          else if (log.level === 'INFO') color = '#38bdf8';
          return `<div style="margin-bottom:3px"><span style="color:var(--text-muted)">[${esc(log.time)}]</span> <span style="font-weight:700;color:${color}">[${esc(log.level)}]</span> <span style="color:var(--purple-light)">[${esc(log.logger)}]</span> ${esc(log.message)}</div>`;
        }).join('');
        consoleEl.scrollTop = consoleEl.scrollHeight;
      } catch (e) {
        consoleEl.textContent = 'Error streaming system logs: ' + e.message;
      }
    }

    // =========================================================================
    // Universal Multi-Currency Pricing & Payment Engine (No-LTD Required)
    // =========================================================================
    let currentPricingCurrency = 'USD';
    let pricingPlansData = null;
    let pricingMethodsData = null;
    let activePayChannel = 'crypto';
    let activeCryptoNetwork = 'usdt_trc20';
    let uploadedReceiptBase64 = null;

    function setPricingCurrency(curr) {
      currentPricingCurrency = curr;
      const btns = {
        'USD': 'currBtnUSD',
        'MAD': 'currBtnMAD',
        'EUR': 'currBtnEUR',
        'USDT': 'currBtnUSDT'
      };
      Object.keys(btns).forEach(k => {
        const b = document.getElementById(btns[k]);
        if (b) {
          if (k === curr) {
            b.className = 'btn btn-sm btn-cyan';
          } else {
            b.className = 'btn btn-sm';
          }
        }
      });
      renderPricingCards();
      const modal = document.getElementById('paymentModal');
      if (modal && modal.style.display === 'flex') {
        const planSlug = document.getElementById('payPlanName')?.value || 'pro';
        selectModalPlan(planSlug);
      }
    }

    function scrollToPricingCards() {
      const el = document.getElementById('pricingCardsGrid');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    async function loadPricingSection() {
      try {
        const res = await apiGet('/api/payments/methods');
        if (res && res.ok) {
          pricingPlansData = res.plans || [];
          pricingMethodsData = res.methods || {};
        }
      } catch (e) {
        console.error('Failed to load payment methods:', e);
      }
      renderPricingCards();
      loadUserPaymentHistory();
      if (typeof initAuth === 'function') initAuth();
    }

    function formatPlanPrice(plan, curr) {
      if (!plan) return { num: 0, symbol: '', display: 'Free' };
      const isFree = (plan.slug === 'free') || (
        Number(plan.price_usd || 0) === 0 &&
        Number(plan.price_mad || 0) === 0 &&
        Number(plan.price_eur || 0) === 0
      );
      if (isFree) {
        return { num: 0, symbol: '', display: 'Free' };
      }
      const getVal = (val, def) => (val !== undefined && val !== null && !isNaN(Number(val))) ? Number(val) : def;
      if (curr === 'MAD') {
        const val = getVal(plan.price_mad, 150);
        return { num: val, symbol: 'MAD', display: `${val} MAD` };
      } else if (curr === 'EUR') {
        const val = getVal(plan.price_eur, 14);
        return { num: val, symbol: '€', display: `€${val}` };
      } else if (curr === 'USDT') {
        const val = getVal(plan.price_usdt, 15);
        return { num: val, symbol: '₮', display: `${val} USDT` };
      }
      const val = getVal(plan.price_usd, 15);
      return { num: val, symbol: '$', display: `$${val}` };
    }

    async function switchToFreePlan() {
      if (!confirm('Are you sure you want to downgrade to the Free Tier? Your daily limit will return to 5 applications/day.')) {
        return;
      }
      try {
        const res = await apiSend('/api/user/switch-free', 'POST');
        if (res && res.ok) {
          showToast(res.message || 'Switched to Free Tier (5 apps/day).', 'success');
          if (typeof currentUser !== 'undefined' && currentUser) {
            currentUser.current_plan = 'free';
            currentUser.daily_apply_limit = res.daily_apply_limit || 5;
          }
          renderPricingCards();
          if (typeof initAuth === 'function') initAuth();
        } else {
          showToast(res?.detail || res?.message || 'Failed to switch plan', 'error');
        }
      } catch (err) {
        showToast('Failed to switch to Free Tier: ' + err.message, 'error');
      }
    }

    function renderPricingCards() {
      const container = document.getElementById('pricingCardsGrid');
      if (!container) return;

      const defaultPlans = [
        {
          slug: 'free',
          name: 'Free Tier',
          badge: 'Trial / Basic',
          price_usd: 0,
          price_mad: 0,
          price_eur: 0,
          price_usdt: 0,
          billing: 'Forever',
          daily_limit: 5,
          description: '100% Free entry-level automated job search. Includes daily discovery, resume match scoring, and safe automated applications.',
          features: [
            '5 Automated Applications / Day (100% Free Forever)',
            'Multi-Continent Discovery (Europe, US, Global Remote)',
            'Pure ML CV Vector Similarity Scoring',
            'Direct Matching (0-2y, Degree, Visa & Relocation)',
            'Email & Telegram Notifications',
            'Application History & ATS Status Tracking',
            'Zero Credit Card or Payment Required'
          ],
          recommended: false,
        },
        {
          slug: 'starter',
          name: 'Starter Hunter',
          badge: 'Entry / Graduate',
          price_usd: 15,
          price_mad: 150,
          price_eur: 14,
          price_usdt: 15,
          billing: '/ month',
          daily_limit: 25,
          description: 'Ideal for active job seekers targeting European, US, and Global Remote roles.',
          features: [
            '25 Automated Applications / Day (100% Gmail Safe)',
            'Multi-Continent Discovery (Europe, US, Remote)',
            '0-2y Experience & Degree Matching',
            'Visa Sponsorship & Relocation Filter',
            'Live Sync & Telegram Alerts'
          ],
          recommended: false,
        },
        {
          slug: 'pro',
          name: 'Pro Hunter & Freelancer',
          badge: 'Most Popular ⭐',
          price_usd: 35,
          price_mad: 350,
          price_eur: 32,
          price_usdt: 35,
          billing: '/ month',
          daily_limit: 50,
          description: 'Full-stack job hunt + automated freelance client deal acquisition.',
          features: [
            '50 Automated Applications / Day (High Deliverability)',
            'Automated Freelance Deal Finder (HN, Reddit, RemoteOK)',
            'AI Proposal & Pitch Generator + Smart Follow-ups',
            'Direct Recruiter & HR Contact Discovery',
            'Preserved Email Threading & Interview Auto-Reply',
            'Priority Match Scoring Engine'
          ],
          recommended: true,
        },
        {
          slug: 'ultra',
          name: 'Executive & Agency',
          badge: 'Maximum Power 🚀',
          price_usd: 69,
          price_mad: 690,
          price_eur: 65,
          price_usdt: 69,
          billing: '/ month',
          daily_limit: 100,
          description: 'Unlimited scale, 24/7 background automation, and dedicated outreach.',
          features: [
            '100 Automated Applications / Day (Maximum Safe Volume)',
            '24/7 Autonomous Daemon Engine',
            'Custom Domain Outreach & Multiple Mailboxes',
            'Unlimited Freelance Pitches & Deal Closing',
            'Dedicated 1-on-1 VIP Strategy Support'
          ],
          recommended: false,
        }
      ];

      const plans = (pricingPlansData && pricingPlansData.length) ? pricingPlansData : defaultPlans;
      const currentPlan = (currentUser && currentUser.current_plan) ? currentUser.current_plan.toLowerCase() : 'free';
      const tierWeights = { free: 0, starter: 1, starter_99: 1, pro: 2, pro_499: 2, ultra: 3 };
      const userWeight = tierWeights[currentPlan] || 0;

      container.innerHTML = plans.map(p => {
        const priceInfo = formatPlanPrice(p, currentPricingCurrency);
        const isCurrent = (currentPlan === p.slug || (currentPlan === 'pro_499' && p.slug === 'pro') || (currentPlan === 'starter_99' && p.slug === 'starter'));
        const targetWeight = tierWeights[p.slug] || 0;
        const isUpgrade = targetWeight > userWeight;
        const isRec = !isCurrent && (p.recommended || p.slug === 'pro');
        const isFreePlan = (p.slug === 'free') || (Number(p.price_usd || 0) === 0 && Number(p.price_mad || 0) === 0);

        const priceDisplay = isFreePlan ? 'Free' : priceInfo.display;
        const billingDisplay = isFreePlan ? 'Forever' : esc(p.billing || '/ month');
        const defaultMatch = defaultPlans.find(d => d.slug === p.slug);
        const planDesc = esc(p.description || defaultMatch?.description || '');
        const planFeatures = (p.features && p.features.length) ? p.features : (defaultMatch?.features || []);

        let cardBorder = 'border:1px solid var(--border);';
        let cardBg = 'background:rgba(15,23,42,0.75);';
        let badgeHtml = '';
        let btnHtml = '';

        if (isCurrent) {
          cardBorder = 'border:2px solid #10b981;box-shadow:0 0 28px rgba(16,185,129,0.35);';
          cardBg = 'background:radial-gradient(ellipse at top, rgba(16,185,129,0.18) 0%, rgba(15,23,42,0.96) 75%);';
          badgeHtml = `<div style="position:absolute;top:-13px;right:20px;background:linear-gradient(135deg, #10b981 0%, #059669 100%);color:#fff;font-size:11px;font-weight:800;padding:3px 12px;border-radius:20px;box-shadow:0 4px 12px rgba(16,185,129,0.4);letter-spacing:0.04em">✓ YOUR CURRENT PLAN</div>`;
          if (isFreePlan) {
            btnHtml = `
              <div style="display:flex;flex-direction:column;gap:8px">
                <div style="width:100%;padding:11px;font-weight:700;font-size:13px;border-radius:10px;justify-content:center;display:flex;align-items:center;gap:6px;background:rgba(16,185,129,0.15);border:1px solid rgba(16,185,129,0.4);color:#a7f3d0">
                  <span>✓ Active (Free Forever)</span>
                </div>
                <button type="button" class="btn btn-primary" onclick="openPaymentModal('starter')" style="width:100%;padding:11px;font-weight:800;font-size:13px;border-radius:10px;justify-content:center;display:flex;align-items:center;gap:6px">
                  <span>⚡ Upgrade to Starter (25 apps/day)</span>
                </button>
              </div>
              <div style="text-align:center;font-size:11px;color:var(--text-muted);margin-top:6px;font-weight:500">5 applications/day included · No credit card required</div>
            `;
          } else {
            btnHtml = `
              <button class="btn btn-sm btn-cyan" onclick="openPaymentModal('${esc(p.slug)}', '${esc(currentPricingCurrency)}')" style="width:100%;padding:11px;font-weight:800;font-size:13px;border-radius:10px;justify-content:center;display:flex;align-items:center;gap:6px;background:rgba(16,185,129,0.22);border:1px solid #10b981;color:#fff">
                <span>🔄 Extend / Renew Plan</span>
              </button>
              <div style="text-align:center;font-size:11px;color:var(--good);margin-top:6px;font-weight:600">✓ Active membership · Click to renew</div>
            `;
          }
        } else if (isUpgrade) {
          if (isRec) {
            cardBorder = 'border:2px solid rgba(139,92,246,0.6);box-shadow:0 0 30px rgba(139,92,246,0.25);';
            cardBg = 'background:radial-gradient(ellipse at top, rgba(30,27,75,0.7) 0%, rgba(15,23,42,0.95) 70%);';
            badgeHtml = `<div style="position:absolute;top:-13px;right:20px;background:linear-gradient(135deg, #8b5cf6 0%, #06b6d4 100%);color:#fff;font-size:11px;font-weight:800;padding:3px 12px;border-radius:20px;box-shadow:0 4px 12px rgba(139,92,246,0.5);letter-spacing:0.04em">${esc(p.badge || 'RECOMMENDED')}</div>`;
          } else if (p.slug === 'ultra') {
            badgeHtml = `<div style="position:absolute;top:-13px;right:20px;background:linear-gradient(135deg, #f59e0b 0%, #ea580c 100%);color:#fff;font-size:11px;font-weight:800;padding:3px 12px;border-radius:20px;box-shadow:0 4px 12px rgba(245,158,11,0.4);letter-spacing:0.04em">${esc(p.badge || 'MAX POWER')}</div>`;
          }
          btnHtml = `
            <button class="btn ${p.slug === 'ultra' ? 'btn-cyan' : 'btn-primary'}" onclick="openPaymentModal('${esc(p.slug)}', '${esc(currentPricingCurrency)}')" style="width:100%;padding:12px;font-weight:800;font-size:13.5px;border-radius:10px;justify-content:center;display:flex;align-items:center;gap:6px">
              <span>🚀 Upgrade to ${esc(p.name)}</span>
            </button>
          `;
        } else {
          if (isFreePlan) {
            btnHtml = `
              <button class="btn" onclick="switchToFreePlan()" style="width:100%;padding:12px;font-weight:700;font-size:13px;border-radius:10px;justify-content:center;display:flex;align-items:center;gap:6px;border-color:rgba(255,255,255,0.2);color:#cbd5e1">
                <span>⬇️ Switch to Free Plan</span>
              </button>
              <div style="text-align:center;font-size:11px;color:var(--text-muted);margin-top:6px">100% Free · 5 applications/day included</div>
            `;
          } else {
            btnHtml = `
              <button class="btn" onclick="openPaymentModal('${esc(p.slug)}', '${esc(currentPricingCurrency)}')" style="width:100%;padding:12px;font-weight:700;font-size:13px;border-radius:10px;justify-content:center;display:flex;align-items:center;gap:6px;border-color:rgba(255,255,255,0.2);color:#cbd5e1">
                <span>🔄 Switch to ${esc(p.name)}</span>
              </button>
            `;
          }
        }

        return `
          <div class="panel" style="${cardBorder}${cardBg}display:flex;flex-direction:column;justify-content:space-between;border-radius:16px;padding:24px;position:relative;transition:transform 0.2s ease, box-shadow 0.2s ease">
            ${badgeHtml}
            <div>
              <div style="font-size:12px;font-weight:700;color:${isCurrent ? 'var(--good)' : (isRec ? 'var(--cyan-light)' : 'var(--text-muted)')};text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">${isCurrent ? 'ACTIVE PLAN' : esc(p.badge || 'PLAN')}</div>
              <h3 style="font-size:20px;font-weight:800;color:#fff;margin-bottom:8px">${esc(p.name)}</h3>
              <div style="font-size:12px;color:var(--text-muted);min-height:36px;margin-bottom:16px;line-height:1.4">${planDesc}</div>

              <div style="display:flex;align-items:baseline;gap:6px;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid rgba(255,255,255,0.08)">
                <span style="font-size:32px;font-weight:900;color:${isFreePlan ? 'var(--good)' : '#fff'};letter-spacing:-0.03em">${priceDisplay}</span>
                <span style="font-size:13px;color:var(--text-muted)">${billingDisplay}</span>
              </div>

              <div style="margin-bottom:20px">
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:10px">Included Features:</div>
                <div style="display:flex;flex-direction:column;gap:9px">
                  ${planFeatures.map(f => `
                    <div style="display:flex;align-items:flex-start;gap:8px;font-size:12.5px;color:#e2e8f0;line-height:1.4">
                      <span style="color:var(--good);font-weight:800">✓</span>
                      <span>${esc(f)}</span>
                    </div>
                  `).join('')}
                </div>
              </div>
            </div>

            <div style="margin-top:16px">
              ${btnHtml}
            </div>
          </div>
        `;
      }).join('');
    }

    async function loadUserPaymentHistory() {
      const tbody = document.getElementById('userPaymentsTableBody');
      if (!tbody) return;
      try {
        const res = await apiGet('/api/payments/my-payments');
        const payments = (res && res.payments) ? res.payments : [];

        if (!payments.length) {
          tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--text-muted)">No payment submissions yet. Click any plan button above to upgrade!</td></tr>';
          return;
        }

        const methodNames = {
          usdt_trc20: '₮ USDT (TRC-20)',
          usdt_polygon: '🟣 USDT (Polygon)',
          solana: '⚡ Solana (SOL)',
          btc: '₿ Bitcoin (BTC)',
          paypal: '🅿️ PayPal',
          card_kofi: '☕ Card (Ko-fi)',
          wise_revolut: '🌐 Wise / Revolut',
          morocco: '🇲🇦 CIH / Attijari',
          cih_wire: '🇲🇦 CIH Bank',
          attijari_wire: '🇲🇦 Attijariwafa',
        };

        tbody.innerHTML = payments.map(p => {
          const curr = p.currency || 'USD';
          const amtStr = curr === 'MAD' ? `${p.amount_mad || 0} MAD` : (curr === 'USD' ? `$${p.amount_usd || 0} USD` : `${p.amount_usd || 0} ${curr}`);
          const statusPill = p.status === 'approved'
            ? '<span class="pill pill-stage-deal_won">✓ APPROVED</span>'
            : (p.status === 'rejected' ? '<span class="pill pill-stage-lost">✕ REJECTED</span>' : '<span class="pill pill-stage-discovered">⏳ PENDING VERIFICATION</span>');

          const receiptBtn = p.has_receipt
            ? `<button type="button" class="btn btn-sm btn-cyan" onclick="openReceiptModal('${esc(p.receipt_url || `/api/payments/receipt/${p.id}`)}')" style="font-size:11px;padding:3px 8px">📷 View Proof</button>`
            : '<span style="color:var(--text-muted);font-size:12px">—</span>';

          return `
            <tr>
              <td style="font-family:'JetBrains Mono';font-size:12px;color:var(--cyan-light)">#${p.id}</td>
              <td><span class="pill" style="font-weight:700">${esc(p.plan || p.plan_name)}</span></td>
              <td><strong style="color:var(--good)">${esc(amtStr)}</strong></td>
              <td><span style="font-size:12px;color:#fff">${esc(methodNames[p.payment_method] || p.payment_method)}</span></td>
              <td style="font-family:'JetBrains Mono';font-size:12px;color:#facc15"><code>${esc(p.reference_code)}</code></td>
              <td>${receiptBtn}</td>
              <td>${statusPill}</td>
              <td style="font-size:12px;color:var(--text-muted)">${formatAppDate(p.created_at)}</td>
              <td style="font-size:12px;color:var(--text-muted)">${esc(p.admin_notes || p.receipt_note || '—')}</td>
            </tr>
          `;
        }).join('');
      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:24px;color:#f87171">Error loading payments: ${esc(e.message)}</td></tr>`;
      }
    }

    function selectModalPlan(planSlug) {
      const defaultPlans = [
        { slug: 'starter', name: 'Starter Hunter', price_usd: 15, price_mad: 150, price_eur: 14, price_usdt: 15, daily_limit: 25 },
        { slug: 'pro', name: 'Pro Hunter & Freelancer', price_usd: 35, price_mad: 350, price_eur: 32, price_usdt: 35, daily_limit: 50 },
        { slug: 'ultra', name: 'Executive & Agency', price_usd: 69, price_mad: 690, price_eur: 65, price_usdt: 69, daily_limit: 100 },
      ];
      const plans = (pricingPlansData && pricingPlansData.length) ? pricingPlansData : defaultPlans;
      let cleanSlug = planSlug || 'pro';
      if (cleanSlug === 'pro_499') cleanSlug = 'pro';
      else if (cleanSlug === 'starter_99') cleanSlug = 'starter';
      else if (cleanSlug === 'free') cleanSlug = 'starter';

      const plan = plans.find(p => p.slug === cleanSlug) || plans.find(p => p.slug === 'pro') || plans[0];
      const priceInfo = formatPlanPrice(plan, currentPricingCurrency);

      // Dynamically populate or update interactive plan selector pills in modal (purchasable plans only)
      const switchGroup = document.getElementById('modalPlanSwitchGroup');
      const purchasable = plans.filter(p => p.slug !== 'free' && (p.price_mad > 0 || p.price_usd > 0));
      const displayPlans = purchasable.length ? purchasable : plans;
      if (switchGroup && displayPlans.length) {
        switchGroup.innerHTML = displayPlans.map(p => {
          const isSelected = p.slug === cleanSlug;
          const pInfo = formatPlanPrice(p, currentPricingCurrency);
          const rawLimit = p.daily_limit !== undefined ? p.daily_limit : (p.daily_apply_limit !== undefined ? p.daily_apply_limit : 5);
          const limitTxt = rawLimit >= 9999 ? 'Unlimited apps' : `${rawLimit} apps/day`;
          const activeBorder = p.slug === 'ultra' ? '#facc15' : (p.slug === 'pro' ? '#8b5cf6' : '#06b6d4');
          const activeBg = p.slug === 'ultra' ? 'rgba(234,179,8,0.22)' : (p.slug === 'pro' ? 'rgba(139,92,246,0.28)' : 'rgba(6,182,212,0.25)');
          const activeShadow = p.slug === 'ultra' ? 'rgba(234,179,8,0.35)' : (p.slug === 'pro' ? 'rgba(139,92,246,0.4)' : 'rgba(6,182,212,0.35)');

          return `
            <button type="button" id="modalPlanBtn_${esc(p.slug)}" class="btn btn-sm" onclick="selectModalPlan('${esc(p.slug)}')" style="border-radius:10px;padding:9px 6px;display:flex;flex-direction:column;align-items:center;gap:3px;cursor:pointer;transition:all 0.2s ease;background:${isSelected ? activeBg : 'rgba(255,255,255,0.03)'};border:1px solid ${isSelected ? activeBorder : 'var(--border)'};box-shadow:${isSelected ? `0 0 16px ${activeShadow}` : 'none'}">
              <span style="font-weight:700;font-size:12px;color:#fff">${esc(p.name)}</span>
              <span id="modalPlanPrice_${esc(p.slug)}" style="font-size:11px;font-family:'JetBrains Mono';color:var(--cyan-light);font-weight:700">${pInfo.display}/mo</span>
              <span style="font-size:9.5px;color:var(--text-muted)">${limitTxt}</span>
            </button>
          `;
        }).join('');
      }

      const planInput = document.getElementById('payPlanName');
      const amtInput = document.getElementById('payAmountVal');
      const currInput = document.getElementById('payCurrencyVal');
      const planTitleEl = document.getElementById('payModalPlanTitle');
      const planBadgeEl = document.getElementById('payModalPlanBadge');
      const sumNameEl = document.getElementById('paySummaryPlanName');
      const sumPriceEl = document.getElementById('paySummaryPrice');
      const curIndicator = document.getElementById('modalCurrentPlanIndicator');

      if (planInput) planInput.value = plan.slug;
      if (amtInput) amtInput.value = priceInfo.num;
      if (currInput) currInput.value = currentPricingCurrency;

      // Update Stripe charge display if present
      const stripeAmtEl = document.getElementById('stripeChargeAmountDisplay');
      if (stripeAmtEl) {
        const usdVal = plan.price_usd !== undefined ? plan.price_usd : 35;
        stripeAmtEl.textContent = `$${Number(usdVal).toFixed(2)} USD`;
      }

      const currentPlan = (currentUser && currentUser.current_plan) ? currentUser.current_plan.toLowerCase() : 'free';
      const isCurrent = (currentPlan === plan.slug || (currentPlan === 'pro_499' && plan.slug === 'pro') || (currentPlan === 'starter_99' && plan.slug === 'starter'));

      if (isCurrent) {
        if (planTitleEl) planTitleEl.textContent = `🔄 Renew / Extend ${plan.name}`;
        if (planBadgeEl) {
          planBadgeEl.textContent = 'Active Plan Renewal ✓';
          planBadgeEl.style.background = 'rgba(16,185,129,0.2)';
          planBadgeEl.style.color = 'var(--good)';
        }
        if (curIndicator) curIndicator.innerHTML = '<span style="color:var(--good);font-weight:700">✓ Your Current Active Tier</span>';
      } else {
        const tierWeights = { free: 0, starter: 1, starter_99: 1, pro: 2, pro_499: 2, ultra: 3 };
        const curWeight = tierWeights[currentPlan] || 0;
        const targetWeight = tierWeights[plan.slug] || 1;
        const isUpgrade = targetWeight > curWeight;

        if (planTitleEl) planTitleEl.textContent = isUpgrade ? `🚀 Upgrade to ${plan.name}` : `🔄 Switch to ${plan.name}`;
        if (planBadgeEl) {
          planBadgeEl.textContent = isUpgrade ? 'Instant Access 🚀' : 'Plan Change 🔄';
          planBadgeEl.style.background = isUpgrade ? 'rgba(139,92,246,0.2)' : 'rgba(56,189,248,0.2)';
          planBadgeEl.style.color = isUpgrade ? 'var(--purple-light)' : 'var(--cyan-light)';
        }
        if (curIndicator) curIndicator.textContent = '';
      }

      const effectiveLimit = plan.daily_limit !== undefined ? plan.daily_limit : (plan.daily_apply_limit !== undefined ? plan.daily_apply_limit : 5);
      if (sumNameEl) sumNameEl.textContent = `${plan.name} (${effectiveLimit >= 9999 ? 'Unlimited' : effectiveLimit + ' apps/day'})`;
      if (sumPriceEl) sumPriceEl.innerHTML = `${priceInfo.display} <span style="font-size:12px;color:var(--text-muted)">/ month</span>`;

      syncPaymentChannelsContent();
    }

    async function openPaymentModal(planSlug = 'pro', currency = null) {
      const modal = document.getElementById('paymentModal');
      if (!modal) return;

      if (currency) currentPricingCurrency = currency;
      if (planSlug === 'free') {
        planSlug = 'starter';
      }

      if (!pricingPlansData || !pricingMethodsData) {
        try {
          const res = await apiGet('/api/payments/methods');
          if (res && res.ok) {
            pricingPlansData = res.plans || [];
            pricingMethodsData = res.methods || {};
          }
        } catch (_) {}
      }

      selectModalPlan(planSlug);

      const refInput = document.getElementById('payRefCode');
      const notesInput = document.getElementById('payNotes');
      if (refInput) refInput.value = '';
      if (notesInput) notesInput.value = '';
      removeReceiptFile();

      // Smart default payment channel prioritization
      if (pricingMethodsData?.stripe?.is_enabled) {
        switchPayChannel('stripe');
      } else if (currentPricingCurrency === 'MAD' && pricingMethodsData?.morocco?.is_enabled !== false) {
        switchPayChannel('morocco');
      } else if (pricingMethodsData?.paypal?.is_enabled !== false) {
        switchPayChannel('paypal');
      } else if (pricingMethodsData?.card_kofi?.is_enabled !== false) {
        switchPayChannel('card');
      } else {
        switchPayChannel('crypto');
        switchCryptoNetwork('usdt_trc20');
      }

      modal.style.display = 'flex';
      (window.requestAnimationFrame || setTimeout)(() => modal.classList.add('open'), 16);
    }

    function closePaymentModal() {
      const modal = document.getElementById('paymentModal');
      if (modal) {
        modal.classList.remove('open');
        setTimeout(() => {
          if (!modal.classList.contains('open')) modal.style.display = 'none';
        }, 220);
      }
    }

    function syncPaymentChannelsContent() {
      const m = pricingMethodsData || {};
      const usdtTrc20 = m.usdt_trc20?.address || 'TJY3cExxxxxxxxxxxxxxxxxxxxxxxxxxxxx';
      const paypalMe = m.paypal?.paypal_me_url || 'https://paypal.me/AutoHuntAI';
      const paypalEmail = m.paypal?.paypal_email || 'payments@autohunt.ai';
      const cardCheckout = m.card_kofi?.checkout_url || 'https://ko-fi.com/autohunt';
      const wiseEmail = m.wise_revolut?.wise_email || 'billing@autohunt.ai';
      const revolutTag = m.wise_revolut?.revolut_tag || '@autohunt';
      const cihRib = m.morocco?.cih_rib || '230 780 0000000000000000 00';
      const attijariRib = m.morocco?.attijari_rib || '007 780 0000000000000000 00';
      const cashplusInfo = m.morocco?.cashplus_info || 'Dépôt agence CashPlus / Wafacash';

      // Read current plan and formatted price for active currency
      const planSlug = document.getElementById('payPlanName')?.value || 'pro';
      const plans = (pricingPlansData && pricingPlansData.length) ? pricingPlansData : [];
      const planObj = plans.find(p => p.slug === planSlug) || { price_usd: 35, price_mad: 350, price_eur: 32, price_usdt: 35, name: 'Pro Hunter & Freelancer' };
      const priceInfo = formatPlanPrice(planObj, currentPricingCurrency);

      // Toggle tab visibility according to enabled gateways
      const stripeTab = document.getElementById('payTabStripe');
      if (stripeTab) stripeTab.style.display = m.stripe?.is_enabled ? 'inline-block' : 'none';

      const cryptoTab = document.getElementById('payTabCrypto');
      if (cryptoTab) {
        const anyCrypto = (m.usdt_trc20?.is_enabled !== false) || (m.usdt_polygon?.is_enabled !== false) || (m.solana?.is_enabled !== false) || (m.btc?.is_enabled !== false);
        cryptoTab.style.display = anyCrypto ? 'inline-block' : 'none';
      }

      const paypalTab = document.getElementById('payTabPaypal');
      if (paypalTab) paypalTab.style.display = (m.paypal?.is_enabled !== false) ? 'inline-block' : 'none';

      const cardTab = document.getElementById('payTabCard');
      if (cardTab) cardTab.style.display = (m.card_kofi?.is_enabled !== false) ? 'inline-block' : 'none';

      const wiseTab = document.getElementById('payTabWise');
      if (wiseTab) wiseTab.style.display = (m.wise_revolut?.is_enabled !== false) ? 'inline-block' : 'none';

      const moroccoTab = document.getElementById('payTabMorocco');
      if (moroccoTab) moroccoTab.style.display = (m.morocco?.is_enabled !== false) ? 'inline-block' : 'none';

      // Direct gateway action links populated with exact plan amount
      const pMeLink = document.getElementById('paypalMeLink');
      if (pMeLink) {
        const baseMe = (m.paypal?.paypal_me_url || 'https://paypal.me/AutoHuntAI').replace(/\/+$/, '');
        const currCode = (currentPricingCurrency || 'USD').toUpperCase();
        pMeLink.href = `${baseMe}/${priceInfo.num}${currCode}`;
        pMeLink.innerHTML = `<span>🅿️</span> Pay ${priceInfo.display} with PayPal.me ↗`;
      }
      const pEmail = document.getElementById('paypalEmailVal');
      if (pEmail) pEmail.value = paypalEmail;

      const cardLink = document.getElementById('cardCheckoutLink');
      if (cardLink) {
        cardLink.href = cardCheckout;
        cardLink.innerHTML = `<span>💳</span> Pay ${priceInfo.display} with Card (Ko-fi) ↗`;
      }

      const stripeChargeBtn = document.getElementById('btnStripeCheckoutAction');
      if (stripeChargeBtn) {
        const usdPrice = planObj.price_usd !== undefined ? planObj.price_usd : 35;
        stripeChargeBtn.textContent = `🚀 Pay $${Number(usdPrice).toFixed(2)} USD with Stripe Checkout →`;
      }

      const waLink = document.getElementById('moroccoWhatsappLink');
      if (waLink) {
        const userEmail = (currentUser && currentUser.email) ? currentUser.email : '';
        const msg = encodeURIComponent(`Bonjour AutoHunt, je viens de payer pour le plan ${planObj.name || planSlug} (${priceInfo.display}). Mon email est: ${userEmail}`);
        waLink.href = `https://wa.me/212600000000?text=${msg}`;
      }

      const wEmail = document.getElementById('wiseEmailVal');
      if (wEmail) wEmail.value = wiseEmail;
      const rTag = document.getElementById('revolutTagVal');
      if (rTag) rTag.value = revolutTag;

      const cRib = document.getElementById('cihRibVal');
      if (cRib) cRib.value = cihRib;
      const aRib = document.getElementById('attijariRibVal');
      if (aRib) aRib.value = attijariRib;
      const cpVal = document.getElementById('cashplusInfoVal');
      if (cpVal) cpVal.textContent = cashplusInfo;
    }

    function switchPayChannel(channel) {
      activePayChannel = channel;
      const tabs = {
        stripe: 'payTabStripe',
        crypto: 'payTabCrypto',
        paypal: 'payTabPaypal',
        card: 'payTabCard',
        wise: 'payTabWise',
        morocco: 'payTabMorocco'
      };
      const panes = {
        stripe: 'payPaneStripe',
        crypto: 'payPaneCrypto',
        paypal: 'payPanePaypal',
        card: 'payPaneCard',
        wise: 'payPaneWise',
        morocco: 'payPaneMorocco'
      };

      Object.keys(tabs).forEach(k => {
        const tb = document.getElementById(tabs[k]);
        const pn = document.getElementById(panes[k]);
        if (tb) tb.classList.toggle('active', k === channel);
        if (pn) pn.style.display = k === channel ? 'block' : 'none';
      });

      // Toggle manual verification section (Stripe is fully automated, no manual proof required)
      const verifSection = document.getElementById('payVerificationSection');
      if (verifSection) {
        verifSection.style.display = (channel === 'stripe') ? 'none' : 'block';
      }

      const methodInput = document.getElementById('payMethodSelected');
      const refInput = document.getElementById('payRefCode');

      if (channel === 'stripe') {
        if (methodInput) methodInput.value = 'stripe';
        if (refInput) refInput.placeholder = 'Stripe Checkout Session ID (e.g. cs_live_... / cs_test_...)';
      } else if (channel === 'crypto') {
        if (methodInput) methodInput.value = activeCryptoNetwork;
        if (refInput) refInput.placeholder = 'Paste USDT / SOL / BTC Transaction Hash (TxHash)...';
      } else if (channel === 'paypal') {
        if (methodInput) methodInput.value = 'paypal';
        if (refInput) refInput.placeholder = 'PayPal Transaction ID, Order Number, or Payer Email...';
      } else if (channel === 'card') {
        if (methodInput) methodInput.value = 'card_kofi';
        if (refInput) refInput.placeholder = 'Card Order Confirmation ID / Email on invoice...';
      } else if (channel === 'wise') {
        if (methodInput) methodInput.value = 'wise_revolut';
        if (refInput) refInput.placeholder = 'Wise / Revolut Transfer Reference or Sender Name...';
      } else if (channel === 'morocco') {
        if (methodInput) methodInput.value = 'morocco';
        if (refInput) refInput.placeholder = 'Numéro de virement (CIH / Attijari) ou reçu CashPlus...';
      }
    }

    function switchCryptoNetwork(netKey) {
      activeCryptoNetwork = netKey;
      const btns = {
        usdt_trc20: 'cryptoNetTrc20',
        usdt_polygon: 'cryptoNetPolygon',
        solana: 'cryptoNetSolana',
        btc: 'cryptoNetBtc'
      };
      Object.keys(btns).forEach(k => {
        const b = document.getElementById(btns[k]);
        if (b) {
          if (k === netKey) b.className = 'btn btn-sm btn-cyan';
          else b.className = 'btn btn-sm';
        }
      });

      const m = pricingMethodsData || {};
      const netInfo = m[netKey] || {};
      const addr = netInfo.address || (netKey === 'usdt_trc20' ? 'TJY3cExxxxxxxxxxxxxxxxxxxxxxxxxxxxx' : (netKey === 'solana' ? '7EcXxxxxxxxxxxxxxxxxxxxxxxxxxxxx' : '0x71Cxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'));
      const netLabel = netInfo.network || (netKey === 'usdt_trc20' ? 'Tron Network (TRC-20)' : (netKey === 'solana' ? 'Solana Mainnet' : 'Polygon Mainnet'));
      const feeNote = netInfo.fee_note || 'Instant confirmation';

      const addrEl = document.getElementById('cryptoWalletAddr');
      if (addrEl) addrEl.value = addr;
      const labelEl = document.getElementById('cryptoNetLabel');
      if (labelEl) labelEl.textContent = netLabel;
      const feeEl = document.getElementById('cryptoFeeNote');
      if (feeEl) feeEl.textContent = feeNote;

      const qrImg = document.getElementById('cryptoQrImg');
      if (qrImg) {
        qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(addr)}`;
      }

      const methodInput = document.getElementById('payMethodSelected');
      if (methodInput) methodInput.value = netKey;
    }

    async function copyText(elementIdOrText, btnId) {
      let text = elementIdOrText;
      const el = document.getElementById(elementIdOrText);
      if (el) text = el.value || el.textContent || '';
      text = String(text).trim();

      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.left = '-999999px';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        showToast('Copied to clipboard! ✓', 'success');
        const btn = document.getElementById(btnId);
        if (btn) {
          const orig = btn.innerHTML;
          btn.innerHTML = '✓ Copied!';
          btn.style.color = '#10b981';
          setTimeout(() => {
            btn.innerHTML = orig;
            btn.style.color = '';
          }, 2000);
        }
      } catch (err) {
        showToast('Could not auto-copy. Please select and copy manually.', 'error');
      }
    }

    function handleReceiptFileSelect(event) {
      const file = event.target?.files?.[0];
      if (!file) return;

      if (!file.type.startsWith('image/')) {
        showToast('Please select a valid image file (PNG, JPG, WEBP)', 'error');
        return;
      }
      if (file.size > 10 * 1024 * 1024) {
        showToast('Receipt image must be smaller than 10MB', 'error');
        return;
      }

      const reader = new FileReader();
      reader.onload = function(e) {
        uploadedReceiptBase64 = e.target.result;
        const previewWrap = document.getElementById('receiptPreviewWrap');
        const dropContent = document.getElementById('receiptDropContent');
        const previewImg = document.getElementById('receiptPreviewImg');
        const fileNameEl = document.getElementById('receiptFileName');
        const fileSizeEl = document.getElementById('receiptFileSize');

        if (previewImg) previewImg.src = uploadedReceiptBase64;
        if (fileNameEl) fileNameEl.textContent = file.name;
        if (fileSizeEl) fileSizeEl.textContent = `${Math.round(file.size / 1024)} KB`;
        if (dropContent) dropContent.style.display = 'none';
        if (previewWrap) previewWrap.style.display = 'flex';
      };
      reader.readAsDataURL(file);
    }

    function removeReceiptFile() {
      uploadedReceiptBase64 = null;
      const fileInput = document.getElementById('receiptFileInput');
      if (fileInput) fileInput.value = '';
      const previewWrap = document.getElementById('receiptPreviewWrap');
      const dropContent = document.getElementById('receiptDropContent');
      const previewImg = document.getElementById('receiptPreviewImg');
      if (previewImg) previewImg.src = '';
      if (previewWrap) previewWrap.style.display = 'none';
      if (dropContent) dropContent.style.display = 'block';
    }

    async function handlePaymentSubmit(e) {
      e.preventDefault();
      const plan_name = document.getElementById('payPlanName')?.value || 'pro';
      const amount = parseFloat(document.getElementById('payAmountVal')?.value || '35');
      const currency = document.getElementById('payCurrencyVal')?.value || currentPricingCurrency || 'USD';
      const payment_method = document.getElementById('payMethodSelected')?.value || 'usdt_trc20';
      const reference_code = document.getElementById('payRefCode')?.value.trim();
      const receipt_note = document.getElementById('payNotes')?.value.trim();

      if (!reference_code) {
        showToast('Please enter your transaction reference code or TxHash', 'error');
        return;
      }

      const btn = document.getElementById('btnSubmitPayment');
      const origText = btn ? btn.innerHTML : '';
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span>⏳</span> Submitting Payment Proof...';
      }

      try {
        const payload = {
          plan_name,
          amount,
          amount_mad: currency === 'MAD' ? Math.round(amount) : Math.round(amount * 10),
          amount_usd: currency === 'MAD' ? Math.round(amount / 10) : amount,
          currency,
          payment_method,
          reference_code,
          receipt_note,
          receipt_image_base64: uploadedReceiptBase64
        };

        const res = await apiSend('/api/payments/checkout', 'POST', payload);
        showToast(res.message || 'Payment reference submitted! An admin will verify shortly.', 'success');
        closePaymentModal();
        removeReceiptFile();
        loadUserPaymentHistory();
        if (typeof initAuth === 'function') initAuth();
      } catch (err) {
        showToast('Payment submission failed: ' + err.message, 'error');
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = origText;
        }
      }
    }

    function openReceiptModal(url) {
      const modal = document.getElementById('receiptModal');
      const img = document.getElementById('receiptModalImg');
      if (!modal || !img) return;
      img.src = url;
      modal.style.display = 'flex';
      (window.requestAnimationFrame || setTimeout)(() => modal.classList.add('open'), 16);
    }

    function closeReceiptModal() {
      const modal = document.getElementById('receiptModal');
      const img = document.getElementById('receiptModalImg');
      if (modal) {
        modal.classList.remove('open');
        setTimeout(() => {
          if (!modal.classList.contains('open')) modal.style.display = 'none';
        }, 220);
      }
      if (img) img.src = '';
    }

    // =========================================================================
    // Real-Time Notification System (Message Broker WebSocket + Bell Dropdown)
    // =========================================================================
    let notifWs = null;
    let notifReconnectTimer = null;
    let notifHeartbeatTimer = null;
    let allNotifications = [];

    const NOTIF_ICONS = {
      job_match: '🎯',
      app_sent: '✉️',
      inbox_reply: '📬',
      applications_done: '🎯',
      critical_security: '🚨',
      system_maintenance: '⚠️',
      user_registered: '👤',
      user_created: '👤',
      payment_approved: '🎉',
      payment_rejected: '⚠️',
      pipeline_started: '🚀',
      pipeline_completed: '✅',
      quota_warning: '⚠️',
      quota_updated: '⚙️',
      system: '🔔'
    };

    function initNotificationsWebSocket() {
      if (notifWs && (notifWs.readyState === WebSocket.OPEN || notifWs.readyState === WebSocket.CONNECTING)) {
        return;
      }
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws/notifications`;

      try {
        notifWs = new WebSocket(wsUrl);

        notifWs.onopen = () => {
          console.log('[NotificationWS] Connected to real-time broker hub');
          const dot = document.getElementById('notifStatusDot');
          if (dot) dot.style.background = '#10b981';
          if (notifReconnectTimer) {
            clearTimeout(notifReconnectTimer);
            notifReconnectTimer = null;
          }
          // Start keepalive ping every 25s
          if (notifHeartbeatTimer) clearInterval(notifHeartbeatTimer);
          notifHeartbeatTimer = setInterval(() => {
            if (notifWs && notifWs.readyState === WebSocket.OPEN) {
              notifWs.send('ping');
            }
          }, 25000);
        };

        notifWs.onmessage = (event) => {
          if (event.data === 'pong') return;
          try {
            const data = JSON.parse(event.data);
            if (data.event === 'support_chat_message') {
              handleIncomingChatMessage(data.message);
              return;
            }
            if (data.event === 'admin_chat_event') {
              handleIncomingAdminChatEvent(data);
              return;
            }
            handleIncomingNotification(data);
          } catch (e) {
            console.debug('[NotificationWS] Non-JSON payload received:', event.data);
          }
        };

        notifWs.onclose = () => {
          console.debug('[NotificationWS] Connection closed. Retrying in 5s…');
          const dot = document.getElementById('notifStatusDot');
          if (dot) dot.style.background = '#f59e0b';
          if (notifHeartbeatTimer) clearInterval(notifHeartbeatTimer);
          if (!notifReconnectTimer) {
            notifReconnectTimer = setTimeout(initNotificationsWebSocket, 5000);
          }
        };

        notifWs.onerror = () => {
          const dot = document.getElementById('notifStatusDot');
          if (dot) dot.style.background = '#ef4444';
          try { notifWs.close(); } catch (_) {}
        };
      } catch (err) {
        console.debug('[NotificationWS] Connection error:', err);
        if (!notifReconnectTimer) {
          notifReconnectTimer = setTimeout(initNotificationsWebSocket, 8000);
        }
      }
    }

    function handleIncomingNotification(notif) {
      // Security & Isolation: Reject admin-only notifications if current user is not admin
      const isAdmin = (typeof currentUser !== 'undefined' && currentUser && currentUser.role === 'admin');
      const adminOnly = notif.admin_only || notif.data?.admin_only || ['user_registered', 'user_created', 'critical_security', 'system_maintenance'].includes(notif.event_type);
      if (adminOnly && !isAdmin) {
        return;
      }

      // Isolation: If notification has a target user_id and it does not match current user, ignore
      if (notif.user_id && typeof currentUser !== 'undefined' && currentUser && currentUser.id && notif.user_id !== currentUser.id) {
        return;
      }

      // 1. Show floating toast
      showNotificationToast(notif);

      // 2. Prepend to in-memory notification list (STRICT NEWEST-FIRST)
      allNotifications.unshift(notif);

      // 3. Render in dropdown
      renderNotificationList();

      // 4. Update badge counter
      fetchUnreadNotificationCount();

      // 5. If this is a high job match, trigger job refresh if viewing jobs tab
      if (notif.event_type === 'job_match' || notif.event_type === 'pipeline_completed') {
        if (currentJobsTab === 'postings') {
          loadJobs();
        }
      }

      // 6. If a new user registers or payments change, refresh admin users / payments list
      if (notif.event_type === 'user_registered' || notif.event_type === 'user_created') {
        const adminSec = document.getElementById('jAdminSection');
        if (adminSec && adminSec.style.display !== 'none') {
          loadAdminUsers();
        }
      } else if (notif.event_type === 'quota_updated' || notif.event_type === 'quota_warning' || notif.event_type === 'payment_approved') {
        initAuth();
      }
    }

    async function loadNotifications() {
      try {
        const res = await apiGet('/api/notifications?limit=50');
        if (res && res.notifications) {
          allNotifications = res.notifications;
          renderNotificationList();
        }
        await fetchUnreadNotificationCount();
      } catch (err) {
        console.debug('Failed to load notifications:', err);
      }
    }

    async function fetchUnreadNotificationCount() {
      try {
        const res = await apiGet('/api/notifications/unread_count');
        const badge = document.getElementById('notifUnreadBadge');
        if (!badge) return;
        const count = res?.unread_count || 0;
        if (count > 0) {
          badge.textContent = count > 99 ? '99+' : count;
          badge.style.display = 'inline-block';
        } else {
          badge.style.display = 'none';
        }
      } catch (err) {
        console.debug('Failed to fetch unread count:', err);
      }
    }

    function toggleNotificationDropdown() {
      const dropdown = document.getElementById('notifDropdown');
      if (!dropdown) return;
      const isOpen = dropdown.style.display === 'flex';
      dropdown.style.display = isOpen ? 'none' : 'flex';
      if (!isOpen) {
        loadNotifications();
      }
    }

    function renderNotificationList() {
      const container = document.getElementById('notifListContainer');
      if (!container) return;

      if (!allNotifications || allNotifications.length === 0) {
        container.innerHTML = '<div style="padding:28px 16px;text-align:center;color:var(--text-muted);font-size:12px">No notifications yet. You will see real-time matches and events here.</div>';
        return;
      }

      container.innerHTML = allNotifications.map(n => {
        const icon = NOTIF_ICONS[n.event_type] || '🔔';
        const unreadStyle = !n.is_read ? 'background:rgba(56,189,248,0.06);border-left:3px solid var(--cyan-light);' : 'background:transparent;border-left:3px solid transparent;';
        const timeAgo = formatTimeAgo(n.created_at);

        return `
          <div style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.05);display:flex;gap:10px;align-items:flex-start;${unreadStyle};transition:background 0.2s" onclick="onNotificationClick(${n.id}, '${esc(n.link || '')}')" role="button">
            <div style="font-size:18px;line-height:1;margin-top:2px">${icon}</div>
            <div style="flex:1;min-width:0">
              <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:2px">
                <div style="font-size:12px;font-weight:${!n.is_read ? '700' : '500'};color:${!n.is_read ? '#f8fafc' : 'var(--text-muted)'};white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(n.title)}</div>
                <div style="font-size:10px;color:var(--text-faint);margin-left:6px">${timeAgo}</div>
              </div>
              <div style="font-size:11px;color:var(--text-muted);line-height:1.35;word-break:break-word">${esc(n.message)}</div>
            </div>
            ${!n.is_read ? `<button onclick="event.stopPropagation();markNotificationRead(${n.id})" title="Mark as read" style="background:none;border:none;color:var(--text-faint);cursor:pointer;font-size:12px;padding:2px">●</button>` : ''}
          </div>
        `;
      }).join('');
    }

    async function onNotificationClick(id, link) {
      await markNotificationRead(id);
      if (link && link.startsWith('#')) {
        const tab = link.substring(1);
        if ((tab === 'admin' || tab === 'system') && (!currentUser || currentUser.role !== 'admin')) {
          showToast('🔒 Access Denied: Administrator privileges required.', 'error');
          return;
        }
        if (typeof switchJobsTab === 'function') {
          switchJobsTab(tab);
        }
      }
    }

    async function markNotificationRead(id) {
      try {
        await apiSend(`/api/notifications/${id}/read`, 'POST');
        const notif = allNotifications.find(n => n.id === id);
        if (notif) notif.is_read = true;
        renderNotificationList();
        fetchUnreadNotificationCount();
      } catch (err) {
        console.debug('Failed to mark notification read:', err);
      }
    }

    async function markAllNotificationsRead() {
      try {
        await apiSend('/api/notifications/mark_all_read', 'POST');
        allNotifications.forEach(n => n.is_read = true);
        renderNotificationList();
        fetchUnreadNotificationCount();
        showToast('All notifications marked as read', 'success');
      } catch (err) {
        showToast('Failed to mark all as read: ' + err.message, 'error');
      }
    }

    async function triggerTestNotification() {
      try {
        const res = await apiSend('/api/notifications/test', 'POST');
        showToast('Test notification triggered via broker!', 'success');
      } catch (err) {
        showToast('Test trigger failed: ' + err.message, 'error');
      }
    }

    async function triggerAdminTestAlert(level) {
      try {
        const title = level === 'security'
          ? '🚨 Critical Security Alert: Suspicious Activity Detected'
          : '⚠️ Critical Maintenance Alert: Background Service Degraded';
        const message = level === 'security'
          ? 'Security firewall detected repeated unauthorized attempts to access admin resources.'
          : 'System orchestrator detected high latency or timeout in container microservice pool.';
        const res = await apiSend('/api/admin/test_alert', 'POST', {
          level: level,
          title: title,
          message: message,
        });
        if (res && res.ok) {
          showToast(`Admin alert (${level}) dispatched to administrators!`, 'success');
        }
      } catch (err) {
        showToast('Failed to dispatch alert: ' + err.message, 'error');
      }
    }

    function showNotificationToast(notif) {
      const container = document.getElementById('notifToastContainer');
      if (!container) return;

      const icon = NOTIF_ICONS[notif.event_type] || '🔔';
      const toast = document.createElement('div');
      toast.style.cssText = `
        pointer-events: auto;
        display: flex;
        align-items: flex-start;
        gap: 12px;
        background: #0f172a;
        color: #f8fafc;
        border: 1px solid rgba(56, 189, 248, 0.4);
        border-radius: 10px;
        padding: 12px 16px;
        width: 320px;
        max-width: 90vw;
        box-shadow: 0 10px 25px rgba(0,0,0,0.5);
        animation: slideInRight 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        transition: all 0.3s ease;
      `;

      toast.innerHTML = `
        <div style="font-size: 20px; line-height: 1;">${icon}</div>
        <div style="flex: 1; min-width: 0;">
          <div style="font-size: 13px; font-weight: 700; color: #38bdf8; margin-bottom: 2px;">${esc(notif.title)}</div>
          <div style="font-size: 11px; color: #cbd5e1; line-height: 1.35;">${esc(notif.message)}</div>
        </div>
        <button style="background:none;border:none;color:#64748b;font-size:14px;cursor:pointer;padding:0;line-height:1">✕</button>
      `;

      const closeBtn = toast.querySelector('button');
      closeBtn.onclick = () => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        setTimeout(() => toast.remove(), 250);
      };

      container.appendChild(toast);

      setTimeout(() => {
        if (toast.parentNode) {
          toast.style.opacity = '0';
          toast.style.transform = 'translateY(10px)';
          setTimeout(() => toast.remove(), 250);
        }
      }, 6500);
    }

    function formatTimeAgo(isoStr) {
      if (!isoStr) return '';
      try {
        const diffMs = Date.now() - new Date(isoStr).getTime();
        const diffSec = Math.floor(diffMs / 1000);
        if (diffSec < 60) return `${Math.max(1, diffSec)}s ago`;
        const diffMin = Math.floor(diffSec / 60);
        if (diffMin < 60) return `${diffMin}m ago`;
        const diffHrs = Math.floor(diffMin / 60);
        if (diffHrs < 24) return `${diffHrs}h ago`;
        return `${Math.floor(diffHrs / 24)}d ago`;
      } catch (_) {
        return '';
      }
    }

    // =========================================================================
    // Multilingual Chatbot & Admin Live Support System
    // =========================================================================
    let chatbotOpen = false;
    let chatbotUnreadCount = 0;
    let chatbotHistoryLoaded = false;
    let activeAdminChatUserId = null;
    let allAdminChatConversations = [];

    function formatChatMarkdown(text) {
      if (!text) return '';
      let s = esc(text);
      s = s.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      s = s.replace(/(?:^|\n)[•\-]\s+(.+)/g, '<div style="display:flex;gap:6px;margin:2px 0 2px 8px"><span style="color:var(--cyan-light)">•</span><span>$1</span></div>');
      s = s.replace(/\n\n/g, '<div style="height:8px"></div>');
      s = s.replace(/\n/g, '<br>');
      return s;
    }

    function toggleChatbot(forceState) {
      const widget = document.getElementById('chatbotWidget');
      const launcher = document.getElementById('chatbotLauncherIcon');
      const badge = document.getElementById('chatbotUnreadBadge');
      if (!widget) return;

      if (typeof forceState === 'boolean') {
        chatbotOpen = forceState;
      } else {
        chatbotOpen = !chatbotOpen;
      }

      if (chatbotOpen) {
        widget.classList.remove('collapsed');
        if (launcher) launcher.textContent = '✕';
        chatbotUnreadCount = 0;
        if (badge) {
          badge.style.display = 'none';
          badge.textContent = '0';
        }
        if (!chatbotHistoryLoaded) {
          loadChatHistory();
        }
        const input = document.getElementById('chatbotInput');
        if (input) setTimeout(() => input.focus(), 150);
      } else {
        widget.classList.add('collapsed');
        if (launcher) launcher.textContent = '🤖';
      }
    }

    async function loadChatHistory() {
      const container = document.getElementById('chatbotMessages');
      if (!container) return;
      try {
        const res = await apiGet('/api/chat/history');
        const msgs = (res && res.messages) ? res.messages : [];
        if (msgs.length > 0) {
          container.innerHTML = '';
          msgs.forEach(m => renderChatMessage(m, container, false));
          container.scrollTop = container.scrollHeight;
        }
        chatbotHistoryLoaded = true;
      } catch (err) {
        console.debug('Failed loading chat history:', err);
      }
    }

    function renderChatMessage(msg, containerEl, scrollToEnd = true) {
      if (typeof containerEl === 'string') {
        containerEl = document.getElementById(containerEl);
      }
      if (!containerEl) return;

      const role = msg.sender_role || 'user';
      const isUser = role === 'user';
      const isAdmin = role === 'admin';

      const wrap = document.createElement('div');
      wrap.className = `chat-bubble-wrap ${role}`;

      let senderIcon = isUser ? '👤' : (isAdmin ? '🛡️' : '🤖');
      let senderTitle = msg.sender_name || (isUser ? 'You' : (isAdmin ? 'Platform Support' : 'AutoHunt Assistant'));
      let adminBadgeHtml = isAdmin ? '<span style="background:rgba(16,185,129,0.2);color:#34d399;font-size:9px;font-weight:700;padding:1px 5px;border-radius:4px;text-transform:uppercase">Support Admin</span>' : '';

      wrap.innerHTML = `
        <div class="chat-sender-label">
          <span>${senderIcon} ${esc(senderTitle)}</span>
          ${adminBadgeHtml}
        </div>
        <div class="chat-bubble ${role}">
          ${formatChatMarkdown(msg.message)}
        </div>
        <div class="chat-time">${formatTimeAgo(msg.created_at) || 'Just now'}</div>
      `;

      containerEl.appendChild(wrap);
      if (scrollToEnd) {
        containerEl.scrollTop = containerEl.scrollHeight;
      }
    }

    function handleChipClick(text) {
      const input = document.getElementById('chatbotInput');
      if (!input) return;
      input.value = text;
      handleChatSubmit(null);
    }

    async function handleChatSubmit(event) {
      if (event && event.preventDefault) event.preventDefault();
      const input = document.getElementById('chatbotInput');
      const sendBtn = document.getElementById('chatbotSendBtn');
      const typing = document.getElementById('chatbotTyping');
      const container = document.getElementById('chatbotMessages');
      if (!input) return;

      const text = input.value.trim();
      if (!text) return;

      // Append user message immediately
      const userMsgObj = {
        sender_role: 'user',
        sender_name: (typeof currentUser !== 'undefined' && currentUser && currentUser.full_name) ? currentUser.full_name : 'You',
        message: text,
        created_at: new Date().toISOString()
      };
      renderChatMessage(userMsgObj, container, true);
      input.value = '';
      input.style.height = 'auto';

      if (sendBtn) sendBtn.disabled = true;
      if (typing) typing.style.display = 'block';
      if (container) container.scrollTop = container.scrollHeight;

      try {
        const res = await apiSend('/api/chat/send', 'POST', { message: text });
        if (typing) typing.style.display = 'none';

        if (res && res.bot_message) {
          renderChatMessage(res.bot_message, container, true);
        }
        if (res && res.needs_admin) {
          showToast('💬 Admin notified: An administrator has been notified and can chat with you directly in this thread.', 'info');
        }
      } catch (err) {
        if (typing) typing.style.display = 'none';
        renderChatMessage({
          sender_role: 'bot',
          sender_name: 'AutoHunt Assistant',
          message: '⚠️ Sorry, I encountered an issue connecting. Please try again or visit Settings.',
          created_at: new Date().toISOString()
        }, container, true);
      } finally {
        if (sendBtn) sendBtn.disabled = false;
        input.focus();
      }
    }

    function handleIncomingChatMessage(msg) {
      if (!msg) return;
      if (typeof currentUser !== 'undefined' && currentUser && msg.user_id && msg.user_id !== currentUser.id) {
        return;
      }

      const container = document.getElementById('chatbotMessages');
      if (container) {
        renderChatMessage(msg, container, true);
      }

      if (!chatbotOpen) {
        chatbotUnreadCount += 1;
        const badge = document.getElementById('chatbotUnreadBadge');
        if (badge) {
          badge.textContent = chatbotUnreadCount;
          badge.style.display = 'inline-block';
        }
        showNotificationToast({
          title: msg.sender_name || '💬 AutoHunt Support',
          message: (msg.message || '').slice(0, 100),
          event_type: 'support_reply',
          link: '#chat'
        });
      }
    }

    function handleIncomingAdminChatEvent(data) {
      const isAdmin = (typeof currentUser !== 'undefined' && currentUser && currentUser.role === 'admin');
      if (!isAdmin) return;

      const badge = document.getElementById('adminChatUnreadBadge');
      if (badge && data.needs_admin) {
        const currentCount = parseInt(badge.textContent || '0', 10);
        badge.textContent = currentCount + 1;
        badge.style.display = 'inline-block';
      }

      if (data.needs_admin) {
        showToast(`💬 [Live Support Alert] ${data.user_name || 'A user'} needs support: "${(data.user_message?.message || '').slice(0, 50)}..."`, 'info');
      }

      if (activeAdminChatUserId === data.user_id) {
        const stream = document.getElementById('adminChatMessagesStream');
        if (stream && data.user_message) {
          renderChatMessage(data.user_message, stream, true);
        }
        if (stream && data.admin_reply) {
          renderChatMessage(data.admin_reply, stream, true);
        }
      }

      const panel = document.getElementById('adminPanelChat');
      if (panel && panel.style.display !== 'none') {
        loadAdminChatConversations(false);
      }
    }

    // --- Admin Live Support Functions ---

    async function loadAdminChatConversations(showLoading = true) {
      const listEl = document.getElementById('adminChatThreadsList');
      if (!listEl) return;
      if (showLoading) {
        listEl.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-muted);font-size:12px">Loading conversations...</div>';
      }

      try {
        const res = await apiGet('/api/chat/admin/conversations');
        allAdminChatConversations = (res && res.conversations) ? res.conversations : [];
        renderAdminChatThreads(allAdminChatConversations);

        const totalUnread = allAdminChatConversations.reduce((sum, c) => sum + (c.unread_count || 0), 0);
        const badge = document.getElementById('adminChatUnreadBadge');
        if (badge) {
          badge.textContent = totalUnread;
          badge.style.display = totalUnread > 0 ? 'inline-block' : 'none';
        }
      } catch (err) {
        console.error('Failed loading admin chat conversations:', err);
        listEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--bad);font-size:12px">Error loading conversations.</div>';
      }
    }

    function renderAdminChatThreads(convos) {
      const listEl = document.getElementById('adminChatThreadsList');
      if (!listEl) return;
      if (!convos.length) {
        listEl.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-muted);font-size:12px">No customer chat threads yet.</div>';
        return;
      }

      listEl.innerHTML = convos.map(c => {
        const isActive = c.user_id === activeAdminChatUserId;
        const planClass = `pill-plan-${c.current_plan || 'free'}`;
        const initial = (c.user_name || c.user_email || 'U').charAt(0).toUpperCase();
        return `
          <div class="admin-chat-thread-card ${isActive ? 'active' : ''}" onclick="openAdminChatConversation(${c.user_id})">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px">
              <div style="display:flex;align-items:center;gap:8px">
                <div style="width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,var(--purple),var(--cyan));display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff">${initial}</div>
                <div>
                  <div style="font-size:13px;font-weight:700;color:#fff">${esc(c.user_name)}</div>
                  <div style="font-size:11px;color:var(--text-faint)">${esc(c.user_email)}</div>
                </div>
              </div>
              <span class="pill ${planClass}" style="font-size:10px">${esc(c.current_plan)}</span>
            </div>
            <div style="font-size:12px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:4px">
              ${c.last_message_role === 'user' ? '👤 ' : (c.last_message_role === 'admin' ? '🛡️ You: ' : '🤖 ')}${esc(c.last_message || 'Started chat')}
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;font-size:10px;color:var(--text-faint)">
              <span>${formatTimeAgo(c.last_message_at) || 'Recently'}</span>
              ${c.unread_count > 0 ? `<span class="brand-badge" style="background:#06b6d4;color:#fff">${c.unread_count} new</span>` : ''}
            </div>
          </div>
        `;
      }).join('');
    }

    function filterAdminChatThreads(searchTerm) {
      if (!searchTerm) {
        renderAdminChatThreads(allAdminChatConversations);
        return;
      }
      const needle = searchTerm.toLowerCase();
      const filtered = allAdminChatConversations.filter(c =>
        (c.user_name && c.user_name.toLowerCase().includes(needle)) ||
        (c.user_email && c.user_email.toLowerCase().includes(needle)) ||
        (c.last_message && c.last_message.toLowerCase().includes(needle))
      );
      renderAdminChatThreads(filtered);
    }

    async function openAdminChatConversation(userId) {
      activeAdminChatUserId = userId;
      renderAdminChatThreads(allAdminChatConversations);

      const emptyState = document.getElementById('adminChatEmptyState');
      const activeView = document.getElementById('adminChatActiveView');
      const stream = document.getElementById('adminChatMessagesStream');
      if (emptyState) emptyState.style.display = 'none';
      if (activeView) activeView.style.display = 'flex';
      if (stream) stream.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:12px">Loading chat history...</div>';

      try {
        const res = await apiGet(`/api/chat/admin/conversation/${userId}`);
        const user = res.user || {};
        const msgs = res.messages || [];

        const nameEl = document.getElementById('adminChatTargetName');
        const emailEl = document.getElementById('adminChatTargetEmail');
        const planPill = document.getElementById('adminChatTargetPlanPill');
        const limitEl = document.getElementById('adminChatTargetLimit');
        const avatarEl = document.getElementById('adminChatTargetAvatar');

        if (nameEl) nameEl.textContent = user.full_name || user.email;
        if (emailEl) emailEl.textContent = user.email;
        if (planPill) {
          planPill.textContent = user.current_plan;
          planPill.className = `pill pill-plan-${user.current_plan || 'free'}`;
        }
        if (limitEl) limitEl.textContent = `${user.daily_apply_limit} apps/day`;
        if (avatarEl) avatarEl.textContent = (user.full_name || user.email || 'U').charAt(0).toUpperCase();

        if (stream) {
          stream.innerHTML = '';
          if (msgs.length === 0) {
            stream.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:12px">No messages in this conversation yet. Send the first message below!</div>';
          } else {
            msgs.forEach(m => renderChatMessage(m, stream, false));
            stream.scrollTop = stream.scrollHeight;
          }
        }

        const convo = allAdminChatConversations.find(c => c.user_id === userId);
        if (convo) convo.unread_count = 0;
        const totalUnread = allAdminChatConversations.reduce((sum, c) => sum + (c.unread_count || 0), 0);
        const badge = document.getElementById('adminChatUnreadBadge');
        if (badge) {
          badge.textContent = totalUnread;
          badge.style.display = totalUnread > 0 ? 'inline-block' : 'none';
        }

        const replyInput = document.getElementById('adminChatReplyText');
        if (replyInput) replyInput.focus();
      } catch (err) {
        console.error('Failed opening conversation:', err);
        showToast('Error opening chat: ' + err.message, 'error');
      }
    }

    function insertAdminCanned(text) {
      const textarea = document.getElementById('adminChatReplyText');
      if (!textarea) return;
      textarea.value = text;
      textarea.focus();
    }

    async function sendAdminChatReply() {
      if (!activeAdminChatUserId) {
        showToast('Please select a conversation first', 'warning');
        return;
      }
      const textarea = document.getElementById('adminChatReplyText');
      const btn = document.getElementById('adminChatSendBtn');
      const stream = document.getElementById('adminChatMessagesStream');
      if (!textarea) return;

      const text = textarea.value.trim();
      if (!text) return;

      if (btn) btn.disabled = true;
      try {
        const res = await apiSend('/api/chat/admin/reply', 'POST', {
          target_user_id: activeAdminChatUserId,
          message: text
        });

        textarea.value = '';
        if (res && res.message && stream) {
          renderChatMessage(res.message, stream, true);
        }
        showToast('Reply dispatched to user ✓', 'success');

        const convo = allAdminChatConversations.find(c => c.user_id === activeAdminChatUserId);
        if (convo) {
          convo.last_message = text;
          convo.last_message_role = 'admin';
          convo.last_message_at = new Date().toISOString();
          renderAdminChatThreads(allAdminChatConversations);
        }
      } catch (err) {
        showToast('Failed sending admin reply: ' + err.message, 'error');
      } finally {
        if (btn) btn.disabled = false;
        if (textarea) textarea.focus();
      }
    }

    // Expose Admin & Modal Functions Globally
    window.openAdminCreateUserModal = openAdminCreateUserModal;
    window.closeAdminCreateUserModal = closeAdminCreateUserModal;
    window.handleAdminCreateUser = handleAdminCreateUser;
    window.onAdminCreatePlanChange = onAdminCreatePlanChange;
    window.loadAdminUsers = loadAdminUsers;
    window.syncUserWithPlan = syncUserWithPlan;
    window.syncAllUsersWithPlans = syncAllUsersWithPlans;
    window.filterAdminUsersTable = filterAdminUsersTable;
    window.populateAdminUserPlanFilter = populateAdminUserPlanFilter;
    window.populateAdminCreateUserPlanOptions = populateAdminCreateUserPlanOptions;
    window.toggleUserActive = toggleUserActive;
    window.updateUserPlan = updateUserPlan;
    window.editUserDailyLimit = editUserDailyLimit;
    window.deleteUser = deleteUser;
    window.toggleUserRole = toggleUserRole;
    window.switchAdminSubTab = switchAdminSubTab;
    window.loadAdminPayments = loadAdminPayments;
    window.approvePayment = approvePayment;
    window.rejectPayment = rejectPayment;
    window.deletePayment = deletePayment;
    window.openPaymentModal = openPaymentModal;
    window.closePaymentModal = closePaymentModal;
    window.handlePaymentSubmit = handlePaymentSubmit;
    window.setPricingCurrency = setPricingCurrency;
    window.loadPricingSection = loadPricingSection;
    window.loadUserPaymentHistory = loadUserPaymentHistory;
    window.switchPayChannel = switchPayChannel;
    window.switchCryptoNetwork = switchCryptoNetwork;
    window.copyText = copyText;
    window.handleReceiptFileSelect = handleReceiptFileSelect;
    window.removeReceiptFile = removeReceiptFile;
    window.openReceiptModal = openReceiptModal;
    window.closeReceiptModal = closeReceiptModal;
    window.selectModalPlan = selectModalPlan;
    window.scrollToPricingCards = scrollToPricingCards;
    window.switchToFreePlan = switchToFreePlan;

    // Expose Admin Plans & Gateways Functions Globally
    window.openAdminPlanModal = openAdminPlanModal;
    window.closeAdminPlanModal = closeAdminPlanModal;
    window.handleAdminPlanSave = handleAdminPlanSave;
    window.syncPlanQuotas = syncPlanQuotas;
    window.syncAllPlansQuotas = syncAllPlansQuotas;
    window.deleteAdminPlan = deleteAdminPlan;
    window.loadAdminPlans = loadAdminPlans;
    window.loadAdminGateways = loadAdminGateways;
    window.saveAdminStripeGateway = saveAdminStripeGateway;
    window.testAdminStripeConnection = testAdminStripeConnection;
    window.saveAdminPaypalGateway = saveAdminPaypalGateway;
    window.saveAdminMoroccoGateway = saveAdminMoroccoGateway;
    window.saveAdminCryptoGateways = saveAdminCryptoGateways;
    window.saveAdminP2pGateways = saveAdminP2pGateways;
    window.initiateStripeCheckout = initiateStripeCheckout;

    // Expose Chatbot & Support Functions Globally
    window.toggleChatbot = toggleChatbot;
    window.loadChatHistory = loadChatHistory;
    window.handleChipClick = handleChipClick;
    window.handleChatSubmit = handleChatSubmit;
    window.loadAdminChatConversations = loadAdminChatConversations;
    window.filterAdminChatThreads = filterAdminChatThreads;
    window.openAdminChatConversation = openAdminChatConversation;
    window.insertAdminCanned = insertAdminCanned;
    window.sendAdminChatReply = sendAdminChatReply;

    // ===================================================================
    // FIRST-TIME USER SETUP & ONBOARDING ASSISTANT
    // ===================================================================
    let currentOnbStep = 1;
    let onbState = {
      titles: [],
      stack: [],
      locations: ['Remote'],
      resume_text: '',
      completion_percent: 0,
    };

    async function checkUserOnboardingStatus() {
      try {
        const res = await apiGet('/api/user/setup_status');
        if (!res || !res.ok) return;

        onbState.completion_percent = res.completion_percent || 0;

        // Topbar Setup Button
        const topBtn = document.getElementById('topbarSetupBtn');
        if (topBtn) {
          topBtn.style.display = 'inline-flex';
          if (res.completion_percent < 100) {
            topBtn.style.background = 'rgba(245,158,11,0.18)';
            topBtn.style.borderColor = 'rgba(245,158,11,0.5)';
            topBtn.style.color = '#facc15';
            topBtn.innerHTML = `<span>⚡ Setup:</span> <span>${res.completion_percent}%</span>`;
            topBtn.title = 'Complete required information & environments to start using AutoHunt';
          } else {
            topBtn.style.background = 'rgba(16,185,129,0.15)';
            topBtn.style.borderColor = 'rgba(16,185,129,0.4)';
            topBtn.style.color = '#34d399';
            topBtn.innerHTML = `<span>✓ Setup:</span> <span>100% Ready</span>`;
            topBtn.title = 'Setup is 100% complete! Click to review or adjust your profile & automation settings';
          }
        }

        // Setup Alert Banner
        const banner = document.getElementById('setupAlertBanner');
        const bannerPct = document.getElementById('bannerSetupPercent');
        const isBannerDismissed = sessionStorage.getItem('setup_banner_dismissed') === 'true';
        if (banner && bannerPct) {
          bannerPct.textContent = `${res.completion_percent}%`;
          if (res.completion_percent < 100 && !isBannerDismissed) {
            banner.style.display = 'flex';
          } else {
            banner.style.display = 'none';
          }
        }

        // Auto-Trigger Onboarding Modal if incomplete and not dismissed for this session
        const modalDismissed = sessionStorage.getItem('onboarding_modal_shown') === 'true';
        if ((res.is_first_time || res.completion_percent < 80) && !modalDismissed) {
          openOnboardingModal(res);
        }
      } catch (err) {
        console.debug('Setup status check error:', err);
      }
    }

    async function openOnboardingModal(setupData = null) {
      const modal = document.getElementById('onboardingModal');
      if (!modal) return;

      try {
        if (!setupData) {
          setupData = await apiGet('/api/user/setup_status');
        }
      } catch (e) {
        console.error('Failed to fetch setup status:', e);
      }

      const prof = (setupData && setupData.profile) ? setupData.profile : {};
      const sett = (setupData && setupData.settings) ? setupData.settings : {};
      const score = (setupData && setupData.completion_percent !== undefined) ? setupData.completion_percent : 25;
      const chk = (setupData && setupData.checklist) ? setupData.checklist : {};

      // Populate State
      onbState.titles = Array.isArray(prof.target_titles) && prof.target_titles.length ? [...prof.target_titles] : ['Fullstack Developer'];
      onbState.stack = Array.isArray(prof.core_stack) && prof.core_stack.length ? [...prof.core_stack] : ['Python', 'JavaScript', 'React'];
      onbState.locations = Array.isArray(prof.target_locations) && prof.target_locations.length ? [...prof.target_locations] : ['Remote'];
      onbState.resume_text = prof.resume_text || '';
      onbState.completion_percent = score;
      onbState.checklist = chk;
      onbState.has_smtp_password = Boolean(sett.has_smtp_password || chk.smtp);

      // Populate Inputs
      const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el && val !== undefined && val !== null) el.value = val;
      };

      setVal('onbFullName', prof.name || (currentUser && currentUser.full_name) || '');
      setVal('onbHeadline', prof.headline || 'Software Engineer');
      setVal('onbExpYears', prof.experience_years ?? 3);
      setVal('onbResumeText', onbState.resume_text);

      setVal('onbSenderName', sett.sender_name || (currentUser && currentUser.full_name) || '');
      setVal('onbSenderEmail', sett.sender_email || (currentUser && currentUser.email) || '');
      setVal('onbSmtpHost', sett.smtp_host || 'smtp.gmail.com');
      setVal('onbSmtpPort', sett.smtp_port || 587);
      if (sett.has_smtp_password && document.getElementById('onbSmtpPass')) {
        document.getElementById('onbSmtpPass').placeholder = '•••••••• (Configured via system env)';
      }
      setVal('onbAlertEmail', sett.alert_email || (currentUser && currentUser.email) || '');
      setVal('onbMinScoreSlider', sett.min_match_score || 65);
      if (document.getElementById('onbScoreValueDisplay')) {
        document.getElementById('onbScoreValueDisplay').textContent = `${sett.min_match_score || 65}%`;
      }
      setVal('onbAutoApplyMode', sett.auto_apply_mode || 'draft');

      // Update Readiness Meter
      updateOnbReadinessDisplay(score, chk);

      // Render Tags
      renderOnbTitles();
      renderOnbStack();
      renderOnbLocations();

      // Reset to Step 1
      switchOnbStep(1);

      // Open Modal
      modal.style.display = 'flex';
      setTimeout(() => modal.classList.add('open'), 10);
    }

    function closeOnboardingModal(isManualDismiss = false) {
      const modal = document.getElementById('onboardingModal');
      if (!modal) return;
      modal.classList.remove('open');
      setTimeout(() => { modal.style.display = 'none'; }, 220);
      if (isManualDismiss) {
        sessionStorage.setItem('onboarding_modal_shown', 'true');
      }
    }

    function dismissSetupBanner() {
      const banner = document.getElementById('setupAlertBanner');
      if (banner) banner.style.display = 'none';
      sessionStorage.setItem('setup_banner_dismissed', 'true');
    }

    function updateOnbReadinessDisplay(score, chk = null) {
      const badge = document.getElementById('onbScoreBadge');
      const bar = document.getElementById('onbProgressBarFill');
      const summary = document.getElementById('onbMissingSummary');

      if (!chk || Object.keys(chk).length === 0) {
        chk = onbState.checklist || {};
      } else {
        onbState.checklist = chk;
      }

      if (bar) bar.style.width = `${Math.max(10, Math.min(100, score))}%`;
      if (badge) {
        badge.textContent = `${score}% (${score >= 100 ? 'Ready! ✓' : 'Incomplete'})`;
        badge.style.background = score >= 100 ? 'rgba(16,185,129,0.2)' : (score >= 75 ? 'rgba(6,182,212,0.2)' : 'rgba(245,158,11,0.2)');
        badge.style.color = score >= 100 ? '#34d399' : (score >= 75 ? '#22d3ee' : '#facc15');
      }

      // Tab icons
      const c1 = document.getElementById('onbCheck1');
      const c2 = document.getElementById('onbCheck2');
      const c3 = document.getElementById('onbCheck3');
      const c4 = document.getElementById('onbCheck4');
      if (c1) c1.textContent = (onbState.titles && onbState.titles.length) ? '✓' : '⚠️';
      if (c2) c2.textContent = (onbState.stack && onbState.stack.length) ? '✓' : '⚠️';
      const resumeText = document.getElementById('onbResumeText')?.value || onbState.resume_text || '';
      if (c3) c3.textContent = (resumeText.length > 25) ? '✓' : '⚠️';
      const smtpPass = document.getElementById('onbSmtpPass')?.value;
      const isSmtpReady = Boolean(chk.smtp || onbState.has_smtp_password || (smtpPass && smtpPass.length > 3));
      if (c4) c4.textContent = isSmtpReady ? '✓' : '⚠️';

      if (summary) {
        let missingCount = 0;
        if (!onbState.titles.length) missingCount++;
        if (!onbState.stack.length) missingCount++;
        if (resumeText.length <= 25) missingCount++;
        if (!isSmtpReady) missingCount++;
        summary.textContent = missingCount === 0 ? 'All essential setup complete!' : `${missingCount} required items remaining`;
      }
    }

    function switchOnbStep(stepNumber) {
      currentOnbStep = stepNumber;
      for (let i = 1; i <= 5; i++) {
        const panel = document.getElementById(`onbStep${i}`);
        const tabBtn = document.getElementById(`onbTabBtn${i}`);
        if (panel) panel.classList.toggle('active', i === stepNumber);
        if (tabBtn) tabBtn.classList.toggle('active', i === stepNumber);
      }

      const btnPrev = document.getElementById('onbBtnPrev');
      const btnNext = document.getElementById('onbBtnNext');
      const btnFinish = document.getElementById('onbBtnFinish');

      if (btnPrev) btnPrev.style.display = (stepNumber > 1) ? 'inline-block' : 'none';
      if (btnNext) btnNext.style.display = (stepNumber < 5) ? 'inline-block' : 'none';
      if (btnFinish) btnFinish.style.display = (stepNumber === 5) ? 'inline-block' : 'none';

      // Re-evaluate check marks
      updateOnbReadinessDisplay(onbState.completion_percent, onbState.checklist);
    }

    function navigateOnbStep(delta) {
      const next = Math.max(1, Math.min(5, currentOnbStep + delta));
      switchOnbStep(next);
    }

    function toggleOnbTitle(title, btn) {
      const idx = onbState.titles.indexOf(title);
      if (idx > -1) {
        onbState.titles.splice(idx, 1);
        if (btn) btn.classList.remove('selected');
      } else {
        onbState.titles.push(title);
        if (btn) btn.classList.add('selected');
      }
      renderOnbTitles();
    }

    function addOnbCustomTitle() {
      const input = document.getElementById('onbCustomTitleInput');
      const val = (input?.value || '').trim();
      if (!val) return;
      if (!onbState.titles.includes(val)) {
        onbState.titles.push(val);
        renderOnbTitles();
      }
      input.value = '';
    }

    function removeOnbTitle(idx) {
      onbState.titles.splice(idx, 1);
      renderOnbTitles();
    }

    function renderOnbTitles() {
      const list = document.getElementById('onbSelectedTitlesList');
      if (!list) return;
      list.innerHTML = onbState.titles.map((t, idx) => `
        <span class="pill" style="background:rgba(6,182,212,0.15);border:1px solid rgba(6,182,212,0.4);color:#22d3ee;display:inline-flex;align-items:center;gap:6px;font-size:11px;padding:3px 8px">
          ${esc(t)}
          <span onclick="removeOnbTitle(${idx})" style="cursor:pointer;font-weight:800;color:#f87171" title="Remove">✕</span>
        </span>
      `).join('');

      // Sync pill buttons
      document.querySelectorAll('#onbStep1 .onb-pill-btn').forEach(btn => {
        const text = btn.textContent.replace(/^\+\s*/, '').trim();
        btn.classList.toggle('selected', onbState.titles.includes(text));
      });
    }

    function toggleOnbStack(skill, btn) {
      const idx = onbState.stack.indexOf(skill);
      if (idx > -1) {
        onbState.stack.splice(idx, 1);
        if (btn) btn.classList.remove('selected');
      } else {
        onbState.stack.push(skill);
        if (btn) btn.classList.add('selected');
      }
      renderOnbStack();
    }

    function addOnbCustomStack() {
      const input = document.getElementById('onbCustomStackInput');
      const val = (input?.value || '').trim();
      if (!val) return;
      if (!onbState.stack.includes(val)) {
        onbState.stack.push(val);
        renderOnbStack();
      }
      input.value = '';
    }

    function removeOnbStack(idx) {
      onbState.stack.splice(idx, 1);
      renderOnbStack();
    }

    function renderOnbStack() {
      const list = document.getElementById('onbSelectedStackList');
      if (!list) return;
      list.innerHTML = onbState.stack.map((s, idx) => `
        <span class="pill" style="background:rgba(168,85,247,0.15);border:1px solid rgba(168,85,247,0.4);color:#c084fc;display:inline-flex;align-items:center;gap:6px;font-size:11px;padding:3px 8px">
          ${esc(s)}
          <span onclick="removeOnbStack(${idx})" style="cursor:pointer;font-weight:800;color:#f87171" title="Remove">✕</span>
        </span>
      `).join('');

      // Sync skill pill buttons
      document.querySelectorAll('#onbStep2 .onb-pill-btn').forEach(btn => {
        const text = btn.textContent.trim();
        btn.classList.toggle('selected', onbState.stack.includes(text));
      });
    }

    function toggleOnbLocation(loc, btn) {
      const idx = onbState.locations.indexOf(loc);
      if (idx > -1) {
        if (onbState.locations.length > 1) {
          onbState.locations.splice(idx, 1);
          if (btn) btn.classList.remove('selected');
        }
      } else {
        onbState.locations.push(loc);
        if (btn) btn.classList.add('selected');
      }
      renderOnbLocations();
    }

    function renderOnbLocations() {
      const list = document.getElementById('onbSelectedLocationsList');
      if (!list) return;
      list.innerHTML = onbState.locations.map(l => `
        <span class="pill" style="font-size:10px;background:rgba(255,255,255,0.06);border:1px solid var(--border)">${esc(l)}</span>
      `).join('');

      document.querySelectorAll('#onbStep1 .onb-pill-btn').forEach(btn => {
        const text = btn.textContent.replace(/^[^\w]+/, '').trim();
        btn.classList.toggle('selected', onbState.locations.some(l => l.includes(text)));
      });
    }

    async function handleOnbCvFileSelect(event) {
      const file = event.target.files?.[0];
      if (!file) return;

      const titleEl = document.getElementById('onbDropzoneTitle');
      if (titleEl) titleEl.textContent = `Analyzing ${file.name}... ⏳`;

      const reader = new FileReader();
      reader.onload = async (e) => {
        try {
          const base64Data = e.target.result;
          const res = await apiSend('/api/candidate/cv/quick_parse', 'POST', {
            file_base64: base64Data,
            filename: file.name
          });

          if (titleEl) titleEl.textContent = `Uploaded: ${file.name} ✓`;

          if (res && res.extracted_text) {
            document.getElementById('onbResumeText').value = res.extracted_text;
            onbState.resume_text = res.extracted_text;
          }

          if (res && res.analysis) {
            const an = res.analysis;
            if (an.name && !document.getElementById('onbFullName')?.value) {
              document.getElementById('onbFullName').value = an.name;
            }
            if (Array.isArray(an.top_skills) && an.top_skills.length) {
              an.top_skills.slice(0, 8).forEach(sk => {
                const norm = sk.charAt(0).toUpperCase() + sk.slice(1);
                if (!onbState.stack.includes(norm)) onbState.stack.push(norm);
              });
              renderOnbStack();
            }
            if (Array.isArray(an.target_roles) && an.target_roles.length) {
              an.target_roles.slice(0, 3).forEach(ro => {
                if (!onbState.titles.includes(ro)) onbState.titles.push(ro);
              });
              renderOnbTitles();
            }
          }

          showToast(`CV analysis completed! Extracted accomplishments & skills ✓`, 'success');
          updateOnbReadinessDisplay(Math.min(100, onbState.completion_percent + 25));
        } catch (err) {
          if (titleEl) titleEl.textContent = `Error analyzing CV: ${err.message}`;
          showToast(`Failed to parse CV: ${err.message}`, 'error');
        }
      };
      reader.readAsDataURL(file);
    }

    async function triggerOnbMlAnalysis() {
      const text = document.getElementById('onbResumeText')?.value || '';
      if (!text || text.length < 20) {
        showToast('Please paste your CV text or upload a document first', 'warning');
        return;
      }

      const btn = document.getElementById('btnOnbMlExtract');
      if (btn) btn.textContent = 'Analyzing... ⏳';

      try {
        const res = await apiSend('/api/candidate/cv/quick_parse', 'POST', { raw_text: text });
        if (res && res.analysis) {
          const an = res.analysis;
          if (Array.isArray(an.top_skills) && an.top_skills.length) {
            an.top_skills.slice(0, 8).forEach(sk => {
              const norm = sk.charAt(0).toUpperCase() + sk.slice(1);
              if (!onbState.stack.includes(norm)) onbState.stack.push(norm);
            });
            renderOnbStack();
          }
          showToast(`Auto-detected ${an.top_skills?.length || 0} skills from CV text ✓`, 'success');
        }
      } catch (err) {
        showToast('ML analysis error: ' + err.message, 'error');
      } finally {
        if (btn) btn.textContent = '⚡ Auto-Detect Skills from CV';
      }
    }

    async function submitOnboardingAndLaunch() {
      const btnFinish = document.getElementById('onbBtnFinish');
      if (btnFinish) {
        btnFinish.disabled = true;
        btnFinish.textContent = 'Saving setup... ⏳';
      }

      const getVal = id => document.getElementById(id)?.value?.trim() || '';

      const payload = {
        name: getVal('onbFullName') || (currentUser && currentUser.full_name) || 'My Candidate Profile',
        headline: getVal('onbHeadline') || 'Software Engineer',
        target_titles: onbState.titles.length ? onbState.titles : ['Fullstack Developer'],
        core_stack: onbState.stack.length ? onbState.stack : ['Python', 'JavaScript'],
        target_locations: onbState.locations.length ? onbState.locations : ['Remote'],
        experience_years: parseInt(getVal('onbExpYears') || '3', 10),
        resume_text: getVal('onbResumeText') || onbState.resume_text,
        sender_name: getVal('onbSenderName') || getVal('onbFullName'),
        sender_email: getVal('onbSenderEmail'),
        smtp_host: getVal('onbSmtpHost') || 'smtp.gmail.com',
        smtp_port: parseInt(getVal('onbSmtpPort') || '587', 10),
        smtp_password: getVal('onbSmtpPass'),
        alert_email: getVal('onbAlertEmail') || getVal('onbSenderEmail'),
        min_match_score: parseInt(getVal('onbMinScoreSlider') || '65', 10),
        auto_apply_mode: document.getElementById('onbAutoApplyMode')?.value || 'draft',
      };

      try {
        const res = await apiSend('/api/user/quick_onboard', 'POST', payload);
        showToast(res.message || 'Onboarding setup complete! 🚀', 'success');

        // Dismiss modal and banner
        sessionStorage.setItem('onboarding_modal_shown', 'true');
        sessionStorage.setItem('setup_banner_dismissed', 'true');
        closeOnboardingModal(false);

        // Refresh user session & profile
        await initAuth();
        await loadProfiles();
        await loadUserSettings();

        // Trigger immediate discovery pipeline to WOW the user!
        showToast('Initiating first automated job discovery run... ⚡', 'info');
        switchAppMode('jobs');
        switchJobsTab('postings');
        triggerJobPipeline();
      } catch (err) {
        showToast('Failed to save setup: ' + err.message, 'error');
        if (btnFinish) {
          btnFinish.disabled = false;
          btnFinish.textContent = '⚡ Save & Launch My First Discovery!';
        }
      }
    }

    // Expose CV Studio & Profile Functions Globally
    window.uploadCvFromStudio = uploadCvFromStudio;
    window.generateCvFromStudio = generateCvFromStudio;
    window.runMlCvAnalysis = runMlCvAnalysis;
    window.syncExtractedCvToProfile = syncExtractedCvToProfile;
    window.activateCurrentProfile = activateCurrentProfile;
    window.saveCurrentProfile = saveCurrentProfile;
    window.createNewProfile = createNewProfile;
    window.cloneCurrentProfile = cloneCurrentProfile;
    window.deleteCurrentProfile = deleteCurrentProfile;

    // Expose Onboarding Functions Globally
    window.checkUserOnboardingStatus = checkUserOnboardingStatus;
    window.openOnboardingModal = openOnboardingModal;
    window.closeOnboardingModal = closeOnboardingModal;
    window.dismissSetupBanner = dismissSetupBanner;
    window.switchOnbStep = switchOnbStep;
    window.navigateOnbStep = navigateOnbStep;
    window.toggleOnbTitle = toggleOnbTitle;
    window.addOnbCustomTitle = addOnbCustomTitle;
    window.removeOnbTitle = removeOnbTitle;
    window.toggleOnbStack = toggleOnbStack;
    window.addOnbCustomStack = addOnbCustomStack;
    window.removeOnbStack = removeOnbStack;
    window.toggleOnbLocation = toggleOnbLocation;
    window.handleOnbCvFileSelect = handleOnbCvFileSelect;
    window.triggerOnbMlAnalysis = triggerOnbMlAnalysis;
    window.submitOnboardingAndLaunch = submitOnboardingAndLaunch;

    // Expose Portal Integration Functions Globally
    window.loadIntegrationsStatus = loadIntegrationsStatus;
    window.connectPortal = connectPortal;
    window.disconnectPortal = disconnectPortal;
    window.testPortalConnection = testPortalConnection;
    window.toggleGuide = toggleGuide;
    window.togglePasswordVisibility = togglePasswordVisibility;



