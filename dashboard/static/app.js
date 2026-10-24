// CareerFlow Job Discovery & Application Management — Client Application

let currentJobs = [];
let currentTracker = [];
let sortOrderDesc = true;
let selectedJobKey = null;
let activeProfileData = null;

// Initialize on DOM Ready
document.addEventListener("DOMContentLoaded", () => {
  setupNavigation();
  setupFilters();
  setupModals();
  setupSearchForm();
  setupProfileForm();
  loadProfile();
  loadOverview();
  loadJobs();
  loadTracker();
  loadAuditLog();
  loadSearchPresets();
});

// Toast notification helper
function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  const icon = type === "success" ? "✅" : type === "error" ? "❌" : "ℹ️";
  toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 200);
  }, 4000);
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function sanitizeHttpUrl(url) {
  if (!url || typeof url !== "string") return null;
  const trimmed = url.trim();
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  return null;
}

// Tab Navigation
function setupNavigation() {
  const tabs = document.querySelectorAll(".nav-tab");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      const targetId = `tab-${tab.dataset.tab}`;
      const targetPane = document.getElementById(targetId);
      if (targetPane) targetPane.classList.add("active");

      // Reload data when entering specific tabs
      if (tab.dataset.tab === "overview") loadOverview();
      if (tab.dataset.tab === "explorer") loadJobs();
      if (tab.dataset.tab === "verification") { loadJobs(); loadAuditLog(); }
      if (tab.dataset.tab === "tracker") loadTracker();
      if (tab.dataset.tab === "search") loadSearchPresets();
      if (tab.dataset.tab === "profile") loadProfile();
    });
  });

  const headerBadge = document.getElementById("headerProfileBadge");
  if (headerBadge) {
    headerBadge.style.cursor = "pointer";
    headerBadge.addEventListener("click", () => {
      const profileTabBtn = document.querySelector('.nav-tab[data-tab="profile"]');
      if (profileTabBtn) profileTabBtn.click();
    });
  }
}

// 1. OVERVIEW DATA
async function loadOverview() {
  try {
    const res = await fetch("/api/overview");
    if (!res.ok) throw new Error("Failed to load overview data");
    const data = await res.json();
    const m = data.metrics || {};

    document.getElementById("statDiscovered").textContent = m.total_discovered || 0;
    document.getElementById("statShortlist").textContent = m.shortlist_ready || 0;
    document.getElementById("statQueue").textContent = m.verification_queue || 0;
    document.getElementById("statExcluded").textContent = m.excluded || 0;
    document.getElementById("statTracker").textContent = m.total_tracked_applications || 0;

    document.getElementById("badgeTotalJobs").textContent = m.total_discovered || 0;
    document.getElementById("badgeQueue").textContent = m.verification_queue || 0;
    document.getElementById("badgeTracker").textContent = m.total_tracked_applications || 0;

    // Roles breakdown
    const rolesList = document.getElementById("roleBreakdownList");
    rolesList.innerHTML = "";
    const totalRoles = Object.values(data.roles_breakdown || {}).reduce((a, b) => a + b, 0) || 1;
    for (const [role, count] of Object.entries(data.roles_breakdown || {})) {
      const pct = Math.round((count / totalRoles) * 100);
      rolesList.innerHTML += `
        <div class="bar-item">
          <div class="bar-meta">
            <span>${escapeHtml(role)}</span>
            <span class="text-muted">${count} roles (${pct}%)</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill" style="width: ${pct}%"></div>
          </div>
        </div>
      `;
    }
    if (Object.keys(data.roles_breakdown || {}).length === 0) {
      rolesList.innerHTML = `<div class="text-muted p-2">No roles discovered yet. Use Live Search to discover opportunities.</div>`;
    }

    // Top skills cloud
    const skillsCloud = document.getElementById("skillsPillCloud");
    skillsCloud.innerHTML = "";
    (data.top_skills || []).forEach(([skill, count]) => {
      skillsCloud.innerHTML += `
        <div class="skill-pill">
          <span>${escapeHtml(skill)}</span>
          <span class="count">${count}</span>
        </div>
      `;
    });
    if ((data.top_skills || []).length === 0) {
      skillsCloud.innerHTML = `<div class="text-muted p-2">No skills matched yet.</div>`;
    }

    // Pipeline funnel
    const funnel = document.getElementById("pipelineFunnel");
    const funnelOrder = ["drafted", "applied", "interview", "offer", "hired", "rejected", "withdrawn"];
    const statusCounts = data.tracker_status_counts || {};
    funnel.innerHTML = "";
    funnelOrder.forEach(statusKey => {
      const count = statusCounts[statusKey] || 0;
      funnel.innerHTML += `
        <div class="funnel-step">
          <div class="funnel-count">${count}</div>
          <div class="funnel-label">${statusKey.replace('_', ' ')}</div>
        </div>
      `;
    });
  } catch (err) {
    console.error("loadOverview error:", err);
  }
}

// 2. JOB EXPLORER
function setupFilters() {
  const inputs = ["filterQuery", "filterTier", "filterClassification", "filterEligibility", "filterMinScore", "sortField"];
  inputs.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => loadJobs());
    if (el && el.tagName === "INPUT") el.addEventListener("input", debounce(() => loadJobs(), 300));
  });

  const btnSort = document.getElementById("btnSortOrder");
  if (btnSort) {
    btnSort.addEventListener("click", () => {
      sortOrderDesc = !sortOrderDesc;
      btnSort.textContent = sortOrderDesc ? "⬇️" : "⬆️";
      loadJobs();
    });
  }

  const btnReset = document.getElementById("btnResetFilters");
  if (btnReset) {
    btnReset.addEventListener("click", () => {
      document.getElementById("filterQuery").value = "";
      document.getElementById("filterTier").value = "ALL";
      document.getElementById("filterClassification").value = "ALL";
      document.getElementById("filterEligibility").value = "ALL";
      document.getElementById("filterMinScore").value = "";
      loadJobs();
    });
  }
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

async function loadJobs() {
  const q = document.getElementById("filterQuery").value.trim();
  const tier = document.getElementById("filterTier").value;
  const classification = document.getElementById("filterClassification").value;
  const eligibility = document.getElementById("filterEligibility").value;
  const minScore = document.getElementById("filterMinScore").value;
  const sortBy = document.getElementById("sortField").value;
  const order = sortOrderDesc ? "desc" : "asc";

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (tier !== "ALL") params.set("tier", tier);
  if (classification !== "ALL") params.set("classification", classification);
  if (eligibility !== "ALL") params.set("eligibility", eligibility);
  if (minScore) params.set("min_score", minScore);
  params.set("sort_by", sortBy);
  params.set("order", order);

  try {
    const res = await fetch(`/api/jobs?${params.toString()}`);
    if (!res.ok) throw new Error("Failed to load jobs");
    const data = await res.json();
    currentJobs = data.results || [];
    renderJobsTable(currentJobs);
    renderQueueTable(currentJobs);
  } catch (err) {
    console.error("loadJobs error:", err);
  }
}

function renderJobsTable(jobs) {
  const tbody = document.getElementById("jobsTableBody");
  document.getElementById("tableResultCount").textContent = `Showing ${jobs.length} jobs`;
  if (!jobs.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center p-4 text-muted">No vacancies match the selected filters.</td></tr>`;
    return;
  }

  tbody.innerHTML = jobs.map(j => {
    const scoreClass = j.relevance_score >= 70 ? "score-high" : (j.relevance_score >= 50 ? "score-mid" : "score-low");
    const tierBadge = getTierBadge(j.shortlist_tier);
    const eligBadge = (j.eligibility_status === "eligible_2027" || j.eligibility_status === "eligible")
      ? `<span class="badge elig-2027">Verified Batch</span>`
      : (j.eligibility_status === "eligible_general"
        ? `<span class="badge tier-ready">Eligible</span>`
        : `<span class="badge elig-unknown">${escapeHtml(j.eligibility_status || "unknown")}</span>`);
    const appBadge = `<span class="badge ${j.application_status === 'open' ? 'tier-ready' : 'elig-unknown'}">${escapeHtml(j.application_status || 'unknown')}</span>`;

    return `
      <tr>
        <td><span class="badge-score ${scoreClass}">${j.relevance_score}</span></td>
        <td>${tierBadge}</td>
        <td>
          <div style="font-weight: 700; color: #f1f5f9;">${escapeHtml(j.title)}</div>
          <div class="text-muted text-xs">${escapeHtml(j.company)} &bull; ${escapeHtml(j.portal || j.source || 'portal')}</div>
        </td>
        <td><span class="text-xs">${escapeHtml(j.location || 'Remote')}</span></td>
        <td>${eligBadge}</td>
        <td>${appBadge}</td>
        <td><span class="badge elig-unknown">${escapeHtml(j.verification_confidence || 'LOW')}</span></td>
        <td>
          <button class="btn btn-secondary btn-sm btn-job-detail" data-key="${escapeHtml(j.key)}">Details</button>
        </td>
      </tr>
    `;
  }).join("");
}

function getTierBadge(tier) {
  if (tier === "SHORTLIST_READY") return `<span class="badge tier-ready">Shortlist Ready</span>`;
  if (tier === "VERIFICATION_QUEUE") return `<span class="badge tier-queue">Verification Queue</span>`;
  if (tier === "EXCLUDED") return `<span class="badge tier-excluded">Excluded</span>`;
  return `<span class="badge elig-unknown">${escapeHtml(tier || "")}</span>`;
}

// 3. VERIFICATION QUEUE
function renderQueueTable(allJobs) {
  const queueJobs = allJobs.filter(j => j.shortlist_tier === "VERIFICATION_QUEUE" || j.classification === "NEEDS_VERIFICATION");
  const tbody = document.getElementById("queueTableBody");
  document.getElementById("queueCountBadge").textContent = `${queueJobs.length} Pending`;

  if (!queueJobs.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center p-4 text-muted">✅ Verification queue is clean! All eligible jobs are verified.</td></tr>`;
    return;
  }

  tbody.innerHTML = queueJobs.map(j => {
    const scoreClass = j.relevance_score >= 70 ? "score-high" : "score-mid";
    return `
      <tr>
        <td><span class="badge-score ${scoreClass}">${j.relevance_score}</span></td>
        <td>
          <div style="font-weight: 700;">${escapeHtml(j.title)}</div>
          <div class="text-muted text-xs">${escapeHtml(j.company)}</div>
        </td>
        <td>
          <span class="badge tier-queue">${escapeHtml(j.eligibility_status || 'unknown')}</span>
        </td>
        <td>
          <div class="text-xs" style="color: #fbbf24;">${escapeHtml(j.verification_notes || 'Pending eligibility confirmation')}</div>
        </td>
        <td><span class="text-xs text-muted">${escapeHtml(j.verification_source || 'search_card')}</span></td>
        <td>
          <button class="btn btn-warning btn-sm btn-verify-job" data-key="${escapeHtml(j.key)}">Verify Now</button>
        </td>
      </tr>
    `;
  }).join("");
}

async function loadAuditLog() {
  try {
    const res = await fetch("/api/audit-log");
    if (!res.ok) return;
    const data = await res.json();
    const tbody = document.getElementById("auditTableBody");
    if (!data.entries.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-center p-3 text-muted">No manual verifications recorded yet.</td></tr>`;
      return;
    }
    tbody.innerHTML = data.entries.map(e => `
      <tr>
        <td><span class="text-xs font-mono text-muted">${escapeHtml(e.timestamp ? e.timestamp.slice(0, 19).replace('T', ' ') : '')}</span></td>
        <td><strong>${escapeHtml(e.job_company)}</strong> - ${escapeHtml(e.job_title)}</td>
        <td><span class="badge elig-2027">${escapeHtml(e.new_eligibility)}</span></td>
        <td><span class="badge tier-ready">${escapeHtml(e.new_application_status)}</span></td>
        <td><span class="text-xs">${escapeHtml(e.evidence)}</span></td>
      </tr>
    `).join("");
  } catch (err) {
    console.error("loadAuditLog error:", err);
  }
}

// 4. APPLICATION TRACKER
async function loadTracker() {
  try {
    const res = await fetch("/api/tracker");
    if (!res.ok) throw new Error("Failed to load tracker rows");
    const data = await res.json();
    currentTracker = data.applications || [];

    document.getElementById("trackerRowCount").textContent = `${currentTracker.length} tracked applications`;
    const tbody = document.getElementById("trackerTableBody");

    if (!currentTracker.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-center p-4 text-muted">No applications tracked yet in job_search_tracker.csv. Click '+ Track Application' above to add one.</td></tr>`;
      return;
    }

    tbody.innerHTML = currentTracker.map((r, idx) => {
      const statusColor = r.status === 'offer' || r.status === 'hired' ? 'tier-ready' : (r.status === 'rejected' ? 'tier-excluded' : 'tier-queue');
      return `
        <tr>
          <td><span class="text-xs font-mono">${escapeHtml(r.date || '-')}</span></td>
          <td><strong>${escapeHtml(r.company)}</strong></td>
          <td>${escapeHtml(r.role)}</td>
          <td><span class="badge ${statusColor}">${escapeHtml(r.status)}</span></td>
          <td><span class="text-xs text-muted">${escapeHtml(r.deadline || '-')}</span></td>
          <td><span class="text-xs">${escapeHtml(r.fit_rating || 'Fit')}</span></td>
          <td><span class="text-xs">${escapeHtml(r.notes || '')}</span></td>
          <td>
            <button class="btn btn-secondary btn-sm btn-edit-tracker" data-index="${idx}">Edit</button>
          </td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    console.error("loadTracker error:", err);
  }
}

function openAddTrackerModal(company = "", role = "") {
  document.getElementById("trackerModalTitle").textContent = "Add Application to Tracker";
  document.getElementById("trackCompany").value = company;
  document.getElementById("trackRole").value = role;
  document.getElementById("trackStatus").value = "applied";
  document.getElementById("trackDate").value = new Date().toISOString().slice(0, 10);
  document.getElementById("trackDeadline").value = "";
  const defSector = (activeProfileData && activeProfileData.target_industries && activeProfileData.target_industries[0]) || "Technology";
  document.getElementById("trackSector").value = defSector;
  document.getElementById("trackNotes").value = "";
  document.getElementById("trackerModal").classList.remove("hidden");
}

function editTrackerEntryByIndex(idx) {
  const entry = currentTracker[idx];
  if (!entry) return;
  document.getElementById("trackerModalTitle").textContent = "Update Application Tracker Entry";
  document.getElementById("trackCompany").value = entry.company || "";
  document.getElementById("trackRole").value = entry.role || "";
  document.getElementById("trackStatus").value = entry.status || "applied";
  document.getElementById("trackDate").value = entry.date || "";
  document.getElementById("trackDeadline").value = entry.deadline || "";
  document.getElementById("trackSector").value = entry.sector || "Technology";
  document.getElementById("trackChannel").value = entry.channel || "direct";
  document.getElementById("trackContact").value = entry.contact_person || "";
  document.getElementById("trackNotes").value = entry.notes || "";
  document.getElementById("trackerModal").classList.remove("hidden");
}

function editTrackerEntry(company, role) {
  const idx = currentTracker.findIndex(r => (r.company || "").toLowerCase() === (company || "").toLowerCase() && (r.role || "").toLowerCase() === (role || "").toLowerCase());
  if (idx >= 0) {
    editTrackerEntryByIndex(idx);
  }
}

// 5. SEARCH CONTROLS & DYNAMIC PRESETS
async function loadSearchPresets() {
  const container = document.getElementById("searchPresetsRow");
  if (!container) return;
  try {
    const res = await fetch("/api/profile/search-presets");
    if (!res.ok) return;
    const data = await res.json();
    container.innerHTML = "";
    (data.presets || []).forEach(p => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.dataset.kw = p.query;
      chip.dataset.loc = p.location;
      chip.textContent = p.label;
      chip.addEventListener("click", () => {
        document.getElementById("searchKeywords").value = p.query;
        if (p.location) {
          document.getElementById("searchLocation").value = p.location;
        }
      });
      container.appendChild(chip);
    });
  } catch (err) {
    console.error("loadSearchPresets error:", err);
  }
}

function setupSearchForm() {
  const form = document.getElementById("searchForm");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const keywords = document.getElementById("searchKeywords").value.trim();
    const location = document.getElementById("searchLocation").value.trim();
    const portal = document.getElementById("searchPortal").value;
    const limit = parseInt(document.getElementById("searchLimit").value, 10) || 3;
    const enrich = document.getElementById("searchEnrichLinkedin").checked;

    const spinner = document.getElementById("searchSpinner");
    const btnText = document.getElementById("btnSearchText");
    const consoleBox = document.getElementById("searchConsoleOutput");
    const statusTag = document.getElementById("searchConsoleStatus");

    spinner.classList.remove("hidden");
    btnText.textContent = "Searching & Scoring...";
    statusTag.textContent = "Running";
    consoleBox.textContent = `[${new Date().toLocaleTimeString()}] Executing targeted search:\nKeywords: "${keywords}"\nLocation: "${location}"\nPortal: ${portal}\nLimit: ${limit}\nEnrichment: ${enrich}\nConnecting to CLI search providers...\n`;

    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keywords, location, portal, limit, enrich_linkedin: enrich }),
      });
      const data = await res.json();
      spinner.classList.add("hidden");
      btnText.textContent = "Run Targeted Search";
      statusTag.textContent = data.status || "Completed";

      let logMsg = `[${new Date().toLocaleTimeString()}] Search completed with status: ${data.status}\n`;
      logMsg += `Total fetched from portals: ${data.total_fetched || 0}\n`;
      logMsg += `Unique opportunities evaluated: ${data.unique_evaluated || 0}\n`;
      logMsg += `Persisted into seen_jobs.json: ${data.added_to_seen || 0}\n\n`;

      if (data.errors && data.errors.length) {
        logMsg += `Portal Diagnostics & Warnings:\n${data.errors.join("\n")}\n\n`;
      }

      if (data.results && data.results.length) {
        logMsg += `Evaluated shortlists:\n` + data.results.map(j => ` - [Score: ${j.relevance_score}] ${j.employer}: ${j.title} (${j.shortlist_tier})`).join("\n");
      }
      consoleBox.textContent = logMsg;

      showToast(`Search complete: Added ${data.added_to_seen || 0} unique jobs!`, "success");
      loadOverview();
      loadJobs();
    } catch (err) {
      spinner.classList.add("hidden");
      btnText.textContent = "Run Targeted Search";
      statusTag.textContent = "Error";
      consoleBox.textContent += `\nSearch failed: ${err.message}`;
      showToast("Search failed. Check portal CLI logs.", "error");
    }
  });
}

// 6. MODALS LOGIC
function setupModals() {
  document.getElementById("btnCloseJobModal").addEventListener("click", () => {
    document.getElementById("jobDetailModal").classList.add("hidden");
  });

  document.getElementById("btnCloseVerifyModal").addEventListener("click", () => {
    document.getElementById("verifyModal").classList.add("hidden");
  });
  document.getElementById("btnCancelVerify").addEventListener("click", () => {
    document.getElementById("verifyModal").classList.add("hidden");
  });

  document.getElementById("btnOpenAddTracker").addEventListener("click", () => openAddTrackerModal());
  document.getElementById("btnCloseTrackerModal").addEventListener("click", () => {
    document.getElementById("trackerModal").classList.add("hidden");
  });
  document.getElementById("btnCancelTracker").addEventListener("click", () => {
    document.getElementById("trackerModal").classList.add("hidden");
  });

  document.getElementById("verifyForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = document.getElementById("verifyKey").value;
    const elig = document.getElementById("verifyEligibility").value;
    const appSt = document.getElementById("verifyAppStatus").value;
    const evidence = document.getElementById("verifyEvidence").value.trim();
    const notes = document.getElementById("verifyNotes").value.trim();

    if (!evidence || evidence.length < 5) {
      showToast("Explicit supporting evidence is required (minimum 5 characters).", "error");
      return;
    }

    try {
      const res = await fetch("/api/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          key: key,
          eligibility_status: elig || null,
          application_status: appSt || null,
          evidence: evidence,
          notes: notes,
        }),
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Verification failed");
      }

      showToast("Manual verification saved and safeguards applied!", "success");
      document.getElementById("verifyModal").classList.add("hidden");
      loadJobs();
      loadOverview();
      loadAuditLog();
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  document.getElementById("trackerForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const company = document.getElementById("trackCompany").value.trim();
    const role = document.getElementById("trackRole").value.trim();
    const status = document.getElementById("trackStatus").value;
    const date = document.getElementById("trackDate").value;
    const deadline = document.getElementById("trackDeadline").value;
    const sector = document.getElementById("trackSector").value.trim();
    const channel = document.getElementById("trackChannel").value.trim();
    const contact = document.getElementById("trackContact").value.trim();
    const notes = document.getElementById("trackNotes").value.trim();

    try {
      const res = await fetch("/api/tracker", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          company, role, status, date, deadline, sector, channel, contact_person: contact, notes
        }),
      });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Failed to save tracker entry");
      }
      showToast(`Saved ${company} (${role}) to tracker!`, "success");
      document.getElementById("trackerModal").classList.add("hidden");
      loadTracker();
      loadOverview();
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  document.getElementById("modalOpenVerifyBtn").addEventListener("click", () => {
    document.getElementById("jobDetailModal").classList.add("hidden");
    if (selectedJobKey) openVerifyModal(selectedJobKey);
  });

  document.getElementById("modalTrackBtn").addEventListener("click", () => {
    document.getElementById("jobDetailModal").classList.add("hidden");
    const job = currentJobs.find(j => j.key === selectedJobKey);
    if (job) openAddTrackerModal(job.company || job.employer, job.title);
  });

  // Delegated table event handlers (safe against user-supplied company/role/key escaping)
  const jobsBody = document.getElementById("jobsTableBody");
  if (jobsBody) {
    jobsBody.addEventListener("click", (e) => {
      const btn = e.target.closest(".btn-job-detail");
      if (btn && btn.dataset.key) {
        openJobDetail(btn.dataset.key);
      }
    });
  }

  const queueBody = document.getElementById("queueTableBody");
  if (queueBody) {
    queueBody.addEventListener("click", (e) => {
      const btn = e.target.closest(".btn-verify-job");
      if (btn && btn.dataset.key) {
        openVerifyModal(btn.dataset.key);
      }
    });
  }

  const trackerBody = document.getElementById("trackerTableBody");
  if (trackerBody) {
    trackerBody.addEventListener("click", (e) => {
      const btn = e.target.closest(".btn-edit-tracker");
      if (btn && btn.dataset.index !== undefined) {
        editTrackerEntryByIndex(parseInt(btn.dataset.index, 10));
      }
    });
  }
}

async function openJobDetail(key) {
  let job = currentJobs.find(j => j.key === key);
  if (!job) {
    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(key)}`);
      if (res.ok) {
        job = await res.json();
      }
    } catch (err) {
      console.error("Failed to load job details:", err);
    }
  }
  if (!job) return;
  selectedJobKey = key;

  document.getElementById("modalJobTitle").textContent = job.title;
  document.getElementById("modalJobCompany").textContent = `${job.company || job.employer} • ${job.location || 'Remote'}`;

  const searchUrlBtn = document.getElementById("modalSearchUrlBtn");
  const safeSearchUrl = sanitizeHttpUrl(job.source_url || job.url);
  if (safeSearchUrl) {
    searchUrlBtn.href = safeSearchUrl;
    searchUrlBtn.classList.remove("hidden");
  } else {
    searchUrlBtn.classList.add("hidden");
  }

  const appUrlBtn = document.getElementById("modalAppUrlBtn");
  const safeAppUrl = sanitizeHttpUrl(job.application_url);
  if (safeAppUrl && job.application_url_type !== "third_party_aggregator") {
    appUrlBtn.href = safeAppUrl;
    appUrlBtn.classList.remove("hidden");
  } else {
    appUrlBtn.classList.add("hidden");
  }

  const rb = job.relevance_breakdown || {};
  const skillsList = (job.matched_skills || []).map(s => `<span class="skill-pill">${escapeHtml(s)}</span>`).join("");
  const rolesList = (job.matched_roles || []).map(r => `<span class="badge elig-2027">${escapeHtml(r)}</span>`).join(" ");

  document.getElementById("modalJobBody").innerHTML = `
    <div style="display: flex; gap: 1rem; margin-bottom: 1rem; align-items: center;">
      <div class="badge-score ${job.relevance_score >= 70 ? 'score-high' : 'score-mid'}" style="font-size: 1.5rem; width: 50px;">
        ${escapeHtml(job.relevance_score)}
      </div>
      <div>
        <div style="font-weight: 700;">Relevance Score: ${escapeHtml(job.relevance_score)}/100</div>
        <div class="text-xs text-muted">Role: ${escapeHtml(rb.role || 0)} | Seniority: ${escapeHtml(rb.seniority || 0)} | Skills: ${escapeHtml(rb.skills || 0)} | Location: ${escapeHtml(rb.location || 0)}</div>
      </div>
      <div class="ml-auto">
        ${getTierBadge(job.shortlist_tier)}
      </div>
    </div>

    <div class="notice-banner info mb-3">
      <div>
        <strong>Verification Notes:</strong> ${escapeHtml(job.verification_notes || 'No specific notes')}
      </div>
    </div>

    <div style="margin-bottom: 1rem;">
      <div class="text-xs font-mono text-muted mb-1">MATCHED CATEGORIES</div>
      <div>${rolesList || '<span class="text-muted">None</span>'}</div>
    </div>

    <div style="margin-bottom: 1rem;">
      <div class="text-xs font-mono text-muted mb-1">TECHNICAL SKILLS DETECTED</div>
      <div class="pill-cloud">${skillsList || '<span class="text-muted">None specified in card</span>'}</div>
    </div>

    <div style="margin-bottom: 1rem;">
      <div class="text-xs font-mono text-muted mb-1">FULL JOB DESCRIPTION & EVIDENCE</div>
      <div style="max-height: 220px; overflow-y: auto; background: #0c1222; border: 1px solid #1f293d; border-radius: 6px; padding: 0.85rem; font-size: 0.8rem; white-space: pre-wrap; color: #cbd5e1;">
        ${escapeHtml(job.description || 'Description snippet only. Run search with LinkedIn detail enrichment to fetch full post.')}
      </div>
    </div>

    <div class="text-xs text-muted">
      <strong>Source Provider:</strong> ${escapeHtml(job.portal || job.source || 'Aggregator')} &bull;
      <strong>Verified At:</strong> ${escapeHtml(job.last_verified_at ? job.last_verified_at.slice(0, 19).replace('T', ' ') : '-')} &bull;
      <strong>Confidence:</strong> ${escapeHtml(job.verification_confidence || 'LOW')}
    </div>
  `;

  document.getElementById("jobDetailModal").classList.remove("hidden");
}

async function openVerifyModal(key) {
  let job = currentJobs.find(j => j.key === key);
  if (!job) {
    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(key)}`);
      if (res.ok) {
        job = await res.json();
      }
    } catch (err) {
      console.error("Failed to load job for verification:", err);
    }
  }
  if (!job) return;
  document.getElementById("verifyKey").value = key;
  document.getElementById("verifyModalSubtitle").textContent = `${job.company || job.employer}: ${job.title}`;
  document.getElementById("verifyEligibility").value = job.eligibility_status || "";
  document.getElementById("verifyAppStatus").value = job.application_status || "";
  document.getElementById("verifyEvidence").value = "";
  document.getElementById("verifyNotes").value = "";
  document.getElementById("verifyModal").classList.remove("hidden");
}

// 7. PROFILE & SETTINGS
async function loadProfile() {
  try {
    const res = await fetch("/api/profile");
    if (!res.ok) throw new Error("Failed to load profile");
    activeProfileData = await res.json();
    populateProfileForm(activeProfileData);
    updateHeaderProfile(activeProfileData);
  } catch (err) {
    console.error("loadProfile error:", err);
  }
}

function updateHeaderProfile(profile) {
  const badgeText = document.getElementById("headerProfileText");
  if (!badgeText) return;
  const headline = profile.headline || (profile.preferred_roles && profile.preferred_roles[0]) || "Targeting Opportunities";
  const loc = (profile.locations && profile.locations[0]) || "Remote";
  badgeText.innerHTML = `Target: <strong>${escapeHtml(headline)}</strong> &bull; ${escapeHtml(loc)}`;
}

function populateProfileForm(p) {
  if (!p) return;
  if (document.getElementById("profileCandidateName")) document.getElementById("profileCandidateName").value = p.candidate_name || "";
  if (document.getElementById("profileHeadline")) document.getElementById("profileHeadline").value = p.headline || "";
  if (document.getElementById("profileRoles")) document.getElementById("profileRoles").value = (p.preferred_roles || []).join(", ");
  if (document.getElementById("profileIndustries")) document.getElementById("profileIndustries").value = (p.target_industries || []).join(", ");
  if (document.getElementById("profileSkills")) document.getElementById("profileSkills").value = (p.skills || []).join(", ");

  const edu = p.education || {};
  if (document.getElementById("profileEducationDegree")) document.getElementById("profileEducationDegree").value = edu.degree || "";
  if (document.getElementById("profileEducationLevel")) document.getElementById("profileEducationLevel").value = edu.level || "Bachelor's Degree";
  if (document.getElementById("profileGradYear")) document.getElementById("profileGradYear").value = edu.graduation_year || "";
  if (document.getElementById("profileRequireGradYear")) document.getElementById("profileRequireGradYear").checked = edu.require_grad_year_match !== false;

  const exps = p.experience_levels || [];
  if (document.getElementById("profileExpInternship")) document.getElementById("profileExpInternship").checked = exps.includes("internship");
  if (document.getElementById("profileExpFresher")) document.getElementById("profileExpFresher").checked = exps.includes("fresher");
  if (document.getElementById("profileExpEntry")) document.getElementById("profileExpEntry").checked = exps.includes("entry_level");
  if (document.getElementById("profileExpMid")) document.getElementById("profileExpMid").checked = exps.includes("mid_level");
  if (document.getElementById("profileExpSenior")) document.getElementById("profileExpSenior").checked = exps.includes("senior");

  if (document.getElementById("profileLocations")) document.getElementById("profileLocations").value = (p.locations || []).join(", ");

  const arrs = p.work_arrangements || [];
  if (document.getElementById("profileArrRemote")) document.getElementById("profileArrRemote").checked = arrs.includes("remote");
  if (document.getElementById("profileArrHybrid")) document.getElementById("profileArrHybrid").checked = arrs.includes("hybrid");
  if (document.getElementById("profileArrOnsite")) document.getElementById("profileArrOnsite").checked = arrs.includes("onsite");

  const emps = p.employment_types || [];
  if (document.getElementById("profileEmpIntern")) document.getElementById("profileEmpIntern").checked = emps.includes("internship");
  if (document.getElementById("profileEmpFullTime")) document.getElementById("profileEmpFullTime").checked = emps.includes("full_time");
  if (document.getElementById("profileEmpPartTime")) document.getElementById("profileEmpPartTime").checked = emps.includes("part_time");
  if (document.getElementById("profileEmpContract")) document.getElementById("profileEmpContract").checked = emps.includes("contract");
}

function setupProfileForm() {
  const form = document.getElementById("profileForm");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const candidate_name = document.getElementById("profileCandidateName").value.trim();
    const headline = document.getElementById("profileHeadline").value.trim();
    const preferred_roles = document.getElementById("profileRoles").value.split(",").map(s => s.trim()).filter(Boolean);
    const target_industries = document.getElementById("profileIndustries").value.split(",").map(s => s.trim()).filter(Boolean);
    const skills = document.getElementById("profileSkills").value.split(",").map(s => s.trim().toLowerCase()).filter(Boolean);

    const gradYearVal = document.getElementById("profileGradYear").value.trim();
    const education = {
      degree: document.getElementById("profileEducationDegree").value.trim(),
      level: document.getElementById("profileEducationLevel").value,
      graduation_year: gradYearVal ? parseInt(gradYearVal, 10) : null,
      require_grad_year_match: document.getElementById("profileRequireGradYear").checked,
    };

    const experience_levels = [];
    if (document.getElementById("profileExpInternship").checked) experience_levels.push("internship");
    if (document.getElementById("profileExpFresher").checked) experience_levels.push("fresher");
    if (document.getElementById("profileExpEntry").checked) experience_levels.push("entry_level");
    if (document.getElementById("profileExpMid").checked) experience_levels.push("mid_level");
    if (document.getElementById("profileExpSenior").checked) experience_levels.push("senior");

    const locations = document.getElementById("profileLocations").value.split(",").map(s => s.trim()).filter(Boolean);

    const work_arrangements = [];
    if (document.getElementById("profileArrRemote").checked) work_arrangements.push("remote");
    if (document.getElementById("profileArrHybrid").checked) work_arrangements.push("hybrid");
    if (document.getElementById("profileArrOnsite").checked) work_arrangements.push("onsite");

    const employment_types = [];
    if (document.getElementById("profileEmpIntern").checked) employment_types.push("internship");
    if (document.getElementById("profileEmpFullTime").checked) employment_types.push("full_time");
    if (document.getElementById("profileEmpPartTime").checked) employment_types.push("part_time");
    if (document.getElementById("profileEmpContract").checked) employment_types.push("contract");

    const payload = {
      candidate_name,
      headline,
      preferred_roles,
      target_industries,
      skills,
      education,
      experience_levels,
      locations,
      work_arrangements,
      employment_types,
    };

    try {
      const res = await fetch("/api/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Failed to save profile");
      }
      const data = await res.json();
      activeProfileData = data.profile;
      updateHeaderProfile(activeProfileData);
      showToast("Profile preferences updated successfully!", "success");
      loadOverview();
      loadJobs();
      loadSearchPresets();
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  const btnReset = document.getElementById("btnResetProfile");
  if (btnReset) {
    btnReset.addEventListener("click", () => {
      if (confirm("Reset profile settings to sensible defaults?")) {
        const defaultProf = {
          candidate_name: "Default Candidate",
          headline: "Technology & Professional Roles",
          preferred_roles: ["Software Engineer", "Product Manager", "Data Analyst", "Security Analyst"],
          target_industries: ["Information Technology", "Finance & FinTech", "Consulting"],
          skills: ["python", "sql", "apis", "git", "docker"],
          education: {
            degree: "Bachelor's Degree",
            level: "Bachelor's Degree",
            graduation_year: 2027,
            require_grad_year_match: false,
          },
          experience_levels: ["internship", "entry_level"],
          locations: ["India", "Remote"],
          work_arrangements: ["remote", "hybrid", "onsite"],
          employment_types: ["internship", "full_time"],
        };
        populateProfileForm(defaultProf);
        showToast("Form reset to defaults. Click 'Save Profile' to commit.", "info");
      }
    });
  }
}
