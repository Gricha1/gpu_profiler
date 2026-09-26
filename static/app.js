    const grid = document.getElementById("grid");
    const updatedEl = document.getElementById("updated");
    const vpnBadge = document.getElementById("vpnBadge");
    const vpnActiveSub = document.getElementById("vpnActiveSub");
    const vpnNote = document.getElementById("vpnNote");
    const sshVpnOnBtn = document.getElementById("sshVpnOnBtn");
    const sshVpnOffBtn = document.getElementById("sshVpnOffBtn");
    const modeAmnezia = document.getElementById("modeAmnezia");
    const modeProxy = document.getElementById("modeProxy");
    const modeNetbird = document.getElementById("modeNetbird");
    const pillAmnezia = document.getElementById("pillAmnezia");
    const pillProxy = document.getElementById("pillProxy");
    const pillNetbird = document.getElementById("pillNetbird");
    const pillMeshWatcher = document.getElementById("pillMeshWatcher");
    const modeMeshWatcher = document.getElementById("modeMeshWatcher");
    const meshWatcherStartBtn = document.getElementById("meshWatcherStartBtn");
    const mwStatus = document.getElementById("mwStatus");
    const mwTask = document.getElementById("mwTask");
    const mwPid = document.getElementById("mwPid");
    const mwLastCheck = document.getElementById("mwLastCheck");
    const mwLastFix = document.getElementById("mwLastFix");
    const mwNetbirdRoute = document.getElementById("mwNetbirdRoute");
    const mwZerotierRoute = document.getElementById("mwZerotierRoute");
    const mwH200Route = document.getElementById("mwH200Route");
    const mwLanGw = document.getElementById("mwLanGw");
    const mwVkRoute = document.getElementById("mwVkRoute");
    const mwYaRoute = document.getElementById("mwYaRoute");
    const mwGwChange = document.getElementById("mwGwChange");
    const pillZerotier = document.getElementById("pillZerotier");
    const modeZerotier = document.getElementById("modeZerotier");
    const netCurrentIp = document.getElementById("netCurrentIp");
    const netBaseIp = document.getElementById("netBaseIp");
    const netLocation = document.getElementById("netLocation");
    const netOrg = document.getElementById("netOrg");
    const netVpnStatus = document.getElementById("netVpnStatus");
    const netMode = document.getElementById("netMode");
    const ztBox = document.getElementById("ztBox");
    const ztRow = document.getElementById("ztRow");
    const userModal = document.getElementById("userModal");
    const userModalTitle = document.getElementById("userModalTitle");
    const userSwitchBtn = document.getElementById("userSwitchBtn");
    const debugUiBtn = document.getElementById("debugUiBtn");
    const userNameInput = document.getElementById("userNameInput");
    const userNameField = document.getElementById("userNameField");
    const adminPasswordField = document.getElementById("adminPasswordField");
    const adminPasswordInput = document.getElementById("adminPasswordInput");
    const userLoginSubmit = document.getElementById("userLoginSubmit");
    const userLoginBack = document.getElementById("userLoginBack");
    const userLoginError = document.getElementById("userLoginError");
    let currentUser = null;

    function updateDebugUiButton() {
      const isAdmin = Boolean(currentUser?.is_admin);
      debugUiBtn.hidden = !isAdmin;
      if (isAdmin) {
        debugUiBtn.href = "/developer/";
      }
    }
    let firstPaint = true;
    let projectsByHost = {};
    const selectedByHost = {};
    let ramTopScroll = 0;
    const vramTopScrollByKey = {};
    let ramTopList = [];
    const vramTopListByKey = {};


    const browseModal = document.getElementById("browseModal");
    const browseTitle = document.getElementById("browseTitle");
    const browsePath = document.getElementById("browsePath");
    const browseTree = document.getElementById("browseTree");
    const browseOpen = document.getElementById("browseOpen");
    const browseMsg = document.getElementById("browseMsg");
    const browseClose = document.getElementById("browseClose");
    let browseHost = null;
    let browseSelPath = null;
    const treeCache = {};
    const treeExpanded = {};

    const agentDock = document.getElementById("agentDock");
    const agentDockMeta = document.getElementById("agentDockMeta");
    const agentChat = document.getElementById("agentChat");
    const agentInput = document.getElementById("agentInput");
    const agentSend = document.getElementById("agentSend");
    let agentSession = null; // {host, path, uri}
    let agentBusy = false;
    let vpnBusyAction = null;
    const VPN_BTN_LABELS = {
      sshVpnOnBtn: "Start",
      sshVpnOffBtn: "Stop",
      meshWatcherStartBtn: "Start",
    };

    function setModeRow(row, pill, on) {
      if (!row || !pill) return;
      row.classList.toggle("on", !!on);
      pill.className = "vpn-mode-pill " + (on ? "on" : "off");
      pill.textContent = on ? "ВКЛ" : "ВЫКЛ";
    }

    function setVpnBtnState(btn, busy, label) {
      if (!btn) return;
      btn.classList.toggle("busy", !!busy);
      btn.disabled = !!busy;
      btn.textContent = label || VPN_BTN_LABELS[btn.id] || btn.textContent;
    }

    function resetVpnButtons() {
      Object.entries(VPN_BTN_LABELS).forEach(([id, label]) => {
        const btn = document.getElementById(id);
        if (!btn) return;
        btn.classList.remove("busy");
        btn.textContent = label;
      });
    }

    function fmtGiB(bytes) {
      if (bytes == null) return "—";
      const tib = bytes / (1024 ** 4);
      if (tib >= 1) return tib.toFixed(1) + " TiB";
      return (bytes / (1024 ** 3)).toFixed(1) + " GiB";
    }

    function fmtMiB(mib) {
      return (mib / 1024).toFixed(1) + " GiB";
    }

    function bar(pct, kind) {
      return `<div class="track"><div class="fill ${kind}" style="width:${Math.min(pct, 100)}%"></div></div>`;
    }

    function updateHomeDisplay(selectEl) {
      const host = selectEl.dataset.host;
      const username = selectEl.value;
      const detailsDiv = document.getElementById(`home-details-${host}`);
      if (!detailsDiv) return;
      
      // Save selection to localStorage
      localStorage.setItem(`home_usage_${host}`, username);
      
      // Find the server data
      const servers = (window._lastMetricsData && window._lastMetricsData.servers) || [];
      const server = servers.find(s => s.host === host);
      if (!server || !server.all_homes) return;
      
      const selected = server.all_homes.find(h => h.username === username);
      if (!selected) return;
      
      const homePct = selected.disk_pct != null ? selected.disk_pct : 0;
      detailsDiv.innerHTML = `<div class="row-label"><span title="${escapeHtml(selected.path || "")}">${fmtGiB(selected.used_bytes)}${selected.disk_pct != null ? ` · ${selected.disk_pct}% диска` : ""}</span></div>${bar(Math.min(homePct, 100), "home")}`;
    }

    function escapeHtml(s) {
      return String(s)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function cursorBox(host, opts) {
      const options = opts || {};
      const showAgent = options.agent !== false;
      const projects = projectsByHost[host] || [];
      const selected = selectedByHost[host] || (projects[0] && projects[0].path) || "";
      let projectBlock = "";
      if (!projects.length) {
        projectBlock = `<div class="empty">нет известных проектов — открой через New или допиши в projects.json</div>`;
      } else {
        const optsHtml = projects.map(p => {
          const sel = p.path === selected ? " selected" : "";
          const uri = p.uri ? ` data-uri="${escapeHtml(p.uri)}"` : "";
          return `<option value="${escapeHtml(p.path)}"${uri}${sel}>${escapeHtml(p.label)} — ${escapeHtml(p.path)}</option>`;
        }).join("");
        projectBlock = `<div class="cursor-row">
          <select data-host="${escapeHtml(host)}" class="proj-select">${optsHtml}</select>
          <button type="button" data-host="${escapeHtml(host)}" class="proj-open">Open</button>
          ${showAgent ? `<button type="button" data-host="${escapeHtml(host)}" class="proj-agent">Agent</button>` : ""}
        </div>`;
      }

      return `<div class="cursor-box">
        <div class="cursor-label">Cursor project</div>
        ${projectBlock}
        <button type="button" class="browse-btn" data-host="${escapeHtml(host)}">New Cursor Project…</button>
        <div class="cursor-status" data-status-for="${escapeHtml(host)}"></div>
      </div>`;
    }

    function procRowHtml(proc, valueHtml, titleExtra) {
      return `<div class="ram-top-row" title="${escapeHtml(titleExtra || ((proc.user || '') + ' · pid ' + proc.pid))}">
        <span class="pid">${proc.pid}</span>
        <span class="name">${escapeHtml(proc._label || proc.name || proc.process || "?")}</span>
        <span class="rss">${valueHtml}</span>
      </div>`;
    }

    function renderScrollList(opts) {
      const {
        idPrefix,
        title,
        list,
        valueFn,
        labelFn,
        extraClass,
        dataKeyAttr,
        emptyText,
        scrollTop,
      } = opts;
      const rows = (list || []).map(p => {
        const item = { ...p, _label: labelFn ? labelFn(p) : (p.name || "?") };
        return procRowHtml(item, valueFn(p));
      }).join("") || `<div class="empty" style="padding:0.45rem 0.65rem">${emptyText || "пусто"}</div>`;
      const keyAttr = dataKeyAttr ? ` ${dataKeyAttr}` : "";
      return `<div class="ram-top ${extraClass || ""}" id="${idPrefix}Box"${keyAttr}>
        <div class="ram-top-head"><span>${title}</span></div>
        <div class="ram-scroll" id="${idPrefix}Scroll" data-scroll-restore="${scrollTop || 0}">${rows}</div>
      </div>`;
    }

    function restoreScrollPositions() {
      document.querySelectorAll(".ram-scroll[data-scroll-restore]").forEach(el => {
        const y = Number(el.getAttribute("data-scroll-restore") || 0);
        el.scrollTop = y;
      });
    }

    function renderRamTopSlider(list) {
      ramTopList = list || [];
      return renderScrollList({
        idPrefix: "ramTop",
        title: "RAM top",
        list: ramTopList,
        valueFn: (p) => fmtGiB(p.rss_bytes),
        labelFn: (p) => p.name || "?",
        scrollTop: ramTopScroll,
      });
    }

    function vramProcLabel(proc) {
      return proc.name || proc.process || proc.user || "?";
    }

    function renderVramTopSlider(host, gpuIndex, list) {
      const key = `${host}:${gpuIndex}`;
      const sorted = (list || []).slice().sort((a, b) => {
        const am = a.mem_mib == null ? -1 : a.mem_mib;
        const bm = b.mem_mib == null ? -1 : b.mem_mib;
        return bm - am;
      });
      vramTopListByKey[key] = sorted;
      const idSafe = key.replace(/[^a-zA-Z0-9_-]/g, "_");
      return renderScrollList({
        idPrefix: "vram_" + idSafe,
        title: "VRAM top",
        list: sorted,
        valueFn: (p) => (p.mem_mib == null ? "n/a" : fmtMiB(p.mem_mib)),
        labelFn: vramProcLabel,
        extraClass: "vram-top",
        dataKeyAttr: `data-vram-key="${escapeHtml(key)}"`,
        emptyText: "нет процессов в nvidia-smi",
        scrollTop: vramTopScrollByKey[key] || 0,
      });
    }

    function hostOnline(s) {
      if (!s) return false;
      if (s.connection_ok != null) return !!s.connection_ok;
      return !!(s.ok || s.reachable);
    }

    function pathChipLabel(p) {
      const name = p.label || p.id || "?";
      let addr = "";
      if (p.kind === "anydesk") {
        addr = p.ip || p.anydesk_id || "";
      } else if (p.ip) {
        const port = (p.port && Number(p.port) !== 22) ? `:${p.port}` : "";
        addr = `${p.ip}${port}`;
      } else if (p.host_name) {
        addr = p.host_name;
      }
      const via = p.via ? ` via ${p.via}` : "";
      if (addr) return `${name} · ${addr}${via}`;
      return `${name}${via}`;
    }

    function renderPaths(paths) {
      if (!paths || !paths.length) return "";
      const chips = paths.map((p) => {
        const ok = !!p.ok;
        const ms = p.ms != null ? `<span class="ms">${p.ms} ms</span>` : "";
        const tip = escapeHtml([p.detail, p.ssh_target, p.ip, p.port, p.via, p.note].filter(Boolean).join(" · "));
        const lock = p.protected ? " · prot" : "";
        return `<span class="path-chip ${ok ? "ok" : "bad"}" title="${tip}">${escapeHtml(pathChipLabel(p))}${lock}${ms}</span>`;
      }).join("");
      return `<div class="path-list" title="Пути до хоста (IP / порт)">${chips}</div>`;
    }

    function renderServer(s) {
      const isLocal = !!s.local || s.host === "local";
      const title = isLocal
        ? (s.display_name ? `${s.display_name} · ${s.label || s.host}` : `этот ПК · ${s.label || s.host}`)
        : (s.display_name || s.host);
      const online = s.connection_ok == null ? hostOnline(s) : !!s.connection_ok;
      const hasMetrics = !!s.ok;
      const via = s.ssh_via && s.ssh_via !== s.host ? ` via ${s.ssh_via}` : "";
      const badge = s.polling && !hasMetrics
        ? `<span class="badge">loading</span>`
        : s.stale
          ? `<span class="badge down">stale · ${s.latency_ms ?? "—"} ms</span>`
          : online
        ? `<span class="badge ok">${s.ok ? "online" : "reachable"}${via} · ${s.latency_ms ?? "—"} ms</span>`
        : `<span class="badge down">offline</span>`;

      let body = "";
      if (!hasMetrics) {
        body += `<div class="err">${escapeHtml(s.error || "метрики недоступны, но путь живой")}</div>`;
      } else {
        if (s.stale) {
          const age = s.last_success_at ? Math.max(0, Math.round(Date.now() / 1000 - s.last_success_at)) : null;
          body += `<div class="err">устаревшие данные${age == null ? "" : ` · ${age} с`} · ${escapeHtml(s.error || "SSH недоступен")}</div>`;
        }
        if (s.ram) {
          const cpu = (s.cpu_pct != null) ? ` · CPU ${s.cpu_pct}%` : "";
          body += `
            <div class="ram">
              <div class="row-label"><span>RAM</span><span>${fmtGiB(s.ram.used_bytes)} / ${fmtGiB(s.ram.total_bytes)} · ${s.ram.used_pct}%${cpu}</span></div>
              ${bar(s.ram.used_pct, "ram")}
            </div>`;
        } else {
          body += `<div class="empty">RAM: нет данных</div>`;
        }

        if (isLocal && s.ram_top && s.ram_top.length) {
          body += renderRamTopSlider(s.ram_top);
        }

        if (!isLocal) {
          const diskList = (s.disks && s.disks.length)
            ? s.disks
            : (s.disk ? [s.disk] : []);
          if (diskList.length) {
            body += diskList.map(d => {
              const label = d.mount ? `DISK ${d.mount}` : "DISK";
              return `
              <div class="ram">
                <div class="row-label"><span>${escapeHtml(label)}</span><span>свободно ${fmtGiB(d.available_bytes)} · занято ${fmtGiB(d.used_bytes)} / ${fmtGiB(d.total_bytes)} · ${d.used_pct}%</span></div>
                ${bar(d.used_pct, "disk")}
              </div>`;
            }).join("");
          } else {
            body += `<div class="empty">DISK: нет данных</div>`;
          }

          if (s.all_homes && s.all_homes.length) {
            const savedSelection = localStorage.getItem(`home_usage_${s.host}`);
            const defaultUsername = savedSelection && s.all_homes.some(h => h.username === savedSelection) 
              ? savedSelection 
              : s.all_homes[0].username;
            const selectedHome = s.all_homes.find(h => h.username === defaultUsername) || s.all_homes[0];
            const homePct = selectedHome.disk_pct != null ? selectedHome.disk_pct : 0;
            
            body += `
              <div class="ram">
                <div class="row-label">
                  <span>HOME USAGE</span>
                  <select class="home-user-select" data-host="${escapeHtml(s.host)}" onchange="updateHomeDisplay(this)">
                    ${s.all_homes.map(h => `<option value="${escapeHtml(h.username)}"${h.username === defaultUsername ? " selected" : ""}>${escapeHtml(h.username)} — ${fmtGiB(h.used_bytes)}${h.disk_pct != null ? ` · ${h.disk_pct}%` : ""}</option>`).join("")}
                  </select>
                </div>
                <div class="home-details" id="home-details-${escapeHtml(s.host)}">
                  <div class="row-label"><span title="${escapeHtml(selectedHome.path || "")}">${fmtGiB(selectedHome.used_bytes)}${selectedHome.disk_pct != null ? ` · ${selectedHome.disk_pct}% диска` : ""}</span></div>${bar(Math.min(homePct, 100), "home")}
                </div>
              </div>`;
          } else if (s.home_disk) {
            const homePct = s.home_disk.disk_pct != null ? s.home_disk.disk_pct : 0;
            const homeLabel = s.home_disk.disk_pct != null
              ? `${fmtGiB(s.home_disk.used_bytes)} · ${s.home_disk.disk_pct}% диска`
              : fmtGiB(s.home_disk.used_bytes);
            body += `
              <div class="ram">
                <div class="row-label"><span>MY HOME</span><span title="${escapeHtml(s.home_disk.path || "")}">${homeLabel}</span></div>
                ${bar(Math.min(homePct, 100), "home")}
              </div>`;
          } else {
            body += `<div class="empty">HOME: нет данных</div>`;
          }
        }

        if (!s.gpus || !s.gpus.length) {
          body += `<div class="empty">GPU: нет карт / nvidia-smi недоступен</div>`;
        } else {
          body += `<div class="gpus">` + s.gpus.map(g => {
            const users = g.users || [];
            let usersHtml;
            let vramBlock = "";
            if (isLocal) {
              const procs = (g.processes || []).slice().sort((a, b) => {
                const am = a.mem_mib == null ? -1 : a.mem_mib;
                const bm = b.mem_mib == null ? -1 : b.mem_mib;
                return bm - am;
              });
              const busy = (g.util_pct || 0) > 1 || (g.mem_pct || 0) > 1 || procs.length > 0;
              if (procs.length) {
                usersHtml = procs.slice(0, 6).map(p => {
                  const label = p.name || p.process || p.user || "?";
                  const memLabel = p.mem_mib == null ? "n/a" : (p.mem_mib/1024).toFixed(1) + "G";
                  const chipTitle = `${label} · pid ${p.pid} · ${p.mem_mib == null ? "VRAM n/a (WDDM)" : fmtMiB(p.mem_mib)}`;
                  return `<span class="user-chip" title="${escapeHtml(chipTitle)}">${escapeHtml(label)} <span class="mem">${memLabel}</span></span>`;
                }).join("");
              } else if (busy) {
                usersHtml = `<span class="users-empty">занята (процессы не видны / WDDM)</span>`;
              } else {
                usersHtml = `<span class="users-empty">idle</span>`;
              }
              vramBlock = renderVramTopSlider(s.host, g.index, procs);
            } else {
              usersHtml = users.length
                ? users.map(u => {
                    const chipTitle = `${u.user} · ${fmtMiB(u.mem_mib)}`;
                    return `<span class="user-chip" title="${escapeHtml(chipTitle)}">${escapeHtml(u.user)} <span class="mem">${(u.mem_mib/1024).toFixed(1)}G</span></span>`;
                  }).join("")
                : `<span class="users-empty">idle</span>`;
            }
            return `
            <div class="gpu">
              <div class="gpu-top">
                <strong>GPU ${g.index}</strong>
                <span class="gpu-name" title="${escapeHtml(g.name)}">${escapeHtml(g.name)}</span>
              </div>
              <div class="users">${usersHtml}</div>
              <div class="metrics">
                <div>
                  <div class="row-label"><span>VRAM</span><span>${fmtMiB(g.mem_used_mib)} / ${fmtMiB(g.mem_total_mib)} · ${g.mem_pct}%</span></div>
                  ${bar(g.mem_pct, "vram")}
                </div>
                <div>
                  <div class="row-label"><span>UTIL</span><span>${g.util_pct}%</span></div>
                  ${bar(g.util_pct, "util")}
                </div>
              </div>
              ${vramBlock}
            </div>`;
          }).join("") + `
            <button type="button" class="gpu-stats-btn" data-host="${escapeHtml(s.host)}">GPU stats</button>
          </div>`;
        }
      }

      return `<section class="server server-${escapeHtml(s.host)}" data-host="${escapeHtml(s.host)}"${firstPaint ? "" : ' style="animation:none"'}>
        <div class="server-head">
          <div class="host">${escapeHtml(title)}</div>
          <div style="display:flex;align-items:center;gap:0.5rem;">
            ${badge}
            ${s.can_delete ? `<div class="card-settings">
              <button type="button" class="card-settings-btn" data-host="${escapeHtml(s.host)}" title="Опции">&#9881;</button>
              <div class="card-settings-menu" data-host="${escapeHtml(s.host)}">
                ${s.can_rename ? `<button type="button" class="card-settings-item" data-action="rename" data-host="${escapeHtml(s.host)}">&#9998; Переименовать</button>` : ""}
                <button type="button" class="card-settings-item danger" data-action="delete" data-host="${escapeHtml(s.host)}">&#10005; Удалить</button>
              </div>
            </div>` : ""}
          </div>
        </div>
        ${body}
      </section>`;
    }

    function renderVpn(data) {
      const amneziaOn = !!(data && (data.amnezia_on ?? data.running));
      const proxyOn = !!(data && (data.proxy_on ?? data.ssh_vpn_running));
      const netbirdOn = !!(data && data.netbird_on);
      const activeLabel = (data && data.active_label) || (proxyOn ? "ChatGPT Proxy" : (amneziaOn ? "Amnezia" : "direct"));

      setModeRow(modeProxy, pillProxy, proxyOn);
      setModeRow(modeAmnezia, pillAmnezia, amneziaOn);
      if (pillNetbird) {
        pillNetbird.className = "vpn-mode-pill " + (netbirdOn ? "on" : "off");
        pillNetbird.textContent = netbirdOn ? "NetBird ON" : "NetBird off";
      }
      if (modeNetbird) modeNetbird.classList.toggle("on", !!netbirdOn);

      vpnActiveSub.textContent = activeLabel;
      if (proxyOn || amneziaOn || netbirdOn) {
        vpnBadge.className = "vpn-badge ok";
        vpnBadge.textContent = activeLabel;
      } else {
        vpnBadge.className = "vpn-badge bad";
        vpnBadge.textContent = "direct";
      }

      vpnNote.textContent = (data && data.note) || "Amnezia / NetBird / ZeroTier — только статус";
      vpnNote.className = "vpn-note";

      if (!vpnBusyAction) {
        resetVpnButtons();
        sshVpnOnBtn.disabled = proxyOn;
        sshVpnOffBtn.disabled = !proxyOn;
      }
    }

    function renderPublicIp(data) {
      if (!data) return;
      if (netCurrentIp) netCurrentIp.textContent = data.current_ip || "—";
      if (netBaseIp) {
        netBaseIp.textContent = data.base_ip_note ? data.base_ip_note : (data.base_ip || "—");
      }
      if (netLocation) netLocation.textContent = data.location || "—";
      if (netOrg) netOrg.textContent = data.org || "—";
      if (netVpnStatus) {
        netVpnStatus.textContent = data.vpn_status || "—";
        const changed = String(data.vpn_status || "").includes("CHANGED") && !String(data.vpn_status || "").includes("NOT CHANGED");
        netVpnStatus.style.color = changed ? "var(--ok)" : "";
      }
      if (netMode) netMode.textContent = data.mode_label || data.mode || "—";
      if (data.amnezia_on != null) setModeRow(modeAmnezia, pillAmnezia, !!data.amnezia_on);
      if (data.proxy_on != null) setModeRow(modeProxy, pillProxy, !!data.proxy_on);
    }

    async function fetchJson(url, opts = {}, timeoutMs = 15000) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs);
      try {
        const res = await fetch(url, { ...opts, signal: ctrl.signal });
        const data = await res.json();
        if (!res.ok) {
          const err = new Error((data && data.detail) || ("HTTP " + res.status));
          err.status = res.status;
          throw err;
        }
        return data;
      } finally {
        clearTimeout(timer);
      }
    }

    async function loadVpnStatus() {
      if (vpnBusyAction === "sshVpnOffBtn") return;
      try {
        const data = await fetchJson("/api/vpn/status", { cache: "no-store" }, 12000);
        renderVpn(data);
      } catch (e) {
        renderVpn({ note: "не удалось опросить network status" });
      }
    }

    async function loadPublicIp() {
      try {
        const data = await fetchJson("/api/network/public-ip", { cache: "no-store" }, 10000);
        renderPublicIp(data);
      } catch (e) {
        if (netVpnStatus) netVpnStatus.textContent = "lookup failed / offline";
      }
    }

    function setMeshPill(pill, row, status) {
      if (!pill) return;
      const st = String(status || "—").toUpperCase();
      const ok = st === "OK";
      const blocked = st === "BLOCKED";
      pill.className = "vpn-mode-pill " + (ok ? "on" : "off");
      pill.textContent = st;
      pill.style.color = ok ? "var(--ok)" : (blocked ? "var(--bad)" : "");
      if (row) row.classList.toggle("on", ok);
    }

    async function loadMeshHealth() {
      try {
        const data = await fetchJson("/api/network/mesh-health", { cache: "no-store" }, 10000);
        const nb = (data && data.summary && data.summary.netbird) || (data && data.netbird && data.netbird.status) || "—";
        const zt = (data && data.summary && data.summary.zerotier) || (data && data.zerotier && data.zerotier.status) || "—";
        setMeshPill(pillNetbird, modeNetbird, nb);
        setMeshPill(pillZerotier, modeZerotier, zt);
        if (data && data.netbird && data.netbird.hint && nb === "BLOCKED" && vpnNote) {
          vpnNote.textContent = data.netbird.hint;
          vpnNote.className = "vpn-note warn";
        }
      } catch (e) {
        setMeshPill(pillNetbird, modeNetbird, "ERR");
        setMeshPill(pillZerotier, modeZerotier, "ERR");
      }
    }

    function routeColor(v) {
      const s = String(v || "").toUpperCase();
      if (s === "OK" || s === "RUNNING") return "var(--ok)";
      if (s === "HIJACKED" || s === "STALE" || s === "ERROR") return "var(--bad)";
      if (s === "MISSING" || s === "UNKNOWN") return "var(--warn)";
      return "";
    }

    function setMeshWatcherStartVisible(st, canStart) {
      if (!meshWatcherStartBtn) return;
      const show = canStart === true || st === "ERROR" || st === "STALE" || st === "STOPPED" || st === "ERR";
      meshWatcherStartBtn.hidden = !show;
      meshWatcherStartBtn.disabled = vpnBusyAction === "meshWatcherStartBtn";
    }

    async function loadMeshWatcher() {
      try {
        const data = await fetchJson("/api/mesh/watcher-status", { cache: "no-store" }, 8000);
        const st = String((data && data.status) || "—").toUpperCase();
        if (pillMeshWatcher) {
          const ok = st === "RUNNING";
          const bad = st === "ERROR" || st === "STALE";
          pillMeshWatcher.className = "vpn-mode-pill " + (ok ? "on" : "off");
          pillMeshWatcher.textContent = st;
          pillMeshWatcher.style.color = ok ? "var(--ok)" : (bad ? "var(--bad)" : "");
        }
        if (modeMeshWatcher) modeMeshWatcher.classList.toggle("on", st === "RUNNING");
        setMeshWatcherStartVisible(st, data && data.can_start);
        if (mwStatus) { mwStatus.textContent = st; mwStatus.style.color = routeColor(st === "RUNNING" ? "OK" : st); }
        if (mwTask) mwTask.textContent = (data && data.task_scheduler) || "—";
        if (mwPid) mwPid.textContent = (data && data.pid) ? String(data.pid) : "—";
        if (mwLastCheck) mwLastCheck.textContent = (data && data.last_check) || "—";
        if (mwLastFix) mwLastFix.textContent = (data && data.last_fix) || "—";
        if (mwNetbirdRoute) {
          mwNetbirdRoute.textContent = (data && data.netbird_route) || "—";
          mwNetbirdRoute.style.color = routeColor(data && data.netbird_route);
        }
        if (mwZerotierRoute) {
          mwZerotierRoute.textContent = (data && data.zerotier_route) || "—";
          mwZerotierRoute.style.color = routeColor(data && data.zerotier_route);
        }
        if (mwH200Route) {
          mwH200Route.textContent = (data && data.h200_route) || "—";
          mwH200Route.style.color = routeColor(data && data.h200_route);
        }
        if (mwLanGw) {
          const gw = (data && data.lan_gateway) || "—";
          const iface = (data && data.lan_iface) ? ` · ${data.lan_iface}` : "";
          mwLanGw.textContent = gw === "—" ? "—" : `${gw}${iface}`;
        }
        if (mwVkRoute) {
          mwVkRoute.textContent = (data && data.vk_route) || "—";
          mwVkRoute.style.color = routeColor(data && data.vk_route);
        }
        if (mwYaRoute) {
          mwYaRoute.textContent = (data && data.yandex_route) || "—";
          mwYaRoute.style.color = routeColor(data && data.yandex_route);
        }
        if (mwGwChange) mwGwChange.textContent = (data && data.last_gateway_change) || "—";
      } catch (e) {
        if (pillMeshWatcher) {
          pillMeshWatcher.className = "vpn-mode-pill off";
          pillMeshWatcher.textContent = "ERR";
        }
        if (mwStatus) mwStatus.textContent = "ERR";
        setMeshWatcherStartVisible("ERR", true);
      }
    }

    async function vpnAction(endpoint, btnId, busyLabel) {
      if (vpnBusyAction) return;
      const btn = document.getElementById(btnId);
      vpnBusyAction = btnId;
      setVpnBtnState(btn, true, busyLabel);
      vpnNote.textContent = busyLabel + "…";
      try {
        const timeoutMs = endpoint.includes("/off")
          ? 25000
          : (endpoint.includes("watcher-start") ? 35000 : 12000);
        const data = await fetchJson(endpoint, { method: "POST" }, timeoutMs);
        if (data && data.ok === false) {
          vpnNote.textContent = data.error || "ошибка действия";
        } else {
          vpnNote.textContent = (data && data.message) || "готово";
          if (endpoint.includes("/ssh/on") && data && data.proxy_on) {
            setModeRow(modeProxy, pillProxy, true);
            vpnBadge.className = "vpn-badge ok";
            vpnBadge.textContent = data.active_label || "ChatGPT Proxy";
            vpnActiveSub.textContent = data.active_label || "ChatGPT Proxy";
            sshVpnOnBtn.disabled = true;
            sshVpnOffBtn.disabled = false;
          }
          if (endpoint.includes("/ssh/off")) {
            setModeRow(modeProxy, pillProxy, false);
            sshVpnOnBtn.disabled = false;
            sshVpnOffBtn.disabled = true;
          }
        }
      } catch (e) {
        vpnNote.textContent = endpoint.includes("/off")
          ? "таймаут OFF — проверь :10808 / sing-box-chatgpt.pid"
          : "таймаут / сеть";
      } finally {
        vpnBusyAction = null;
        resetVpnButtons();
        loadVpnStatus();
        loadPublicIp();
        if (endpoint.includes("/ssh/")) {
          setTimeout(loadVpnStatus, 1500);
          setTimeout(loadVpnStatus, 4000);
        }
      }
    }

    async function loadProjects() {
      try {
        const res = await fetch("/api/projects", { cache: "no-store" });
        const data = await res.json();
        projectsByHost = data.servers || {};
      } catch (e) {
        projectsByHost = {};
      }
    }

    function appendAgentBubble(type, text) {
      if (!text && type !== "assistant") return;
      const el = document.createElement("div");
      el.className = "bubble " + (type || "status");
      el.textContent = text || "";
      agentChat.appendChild(el);
      agentChat.scrollTop = agentChat.scrollHeight;
      return el;
    }

    function renderAgentHistory(history) {
      agentChat.innerHTML = "";
      (history || []).forEach((m) => appendAgentBubble(m.role || m.type, m.text || ""));
      if (!history || !history.length) {
        appendAgentBubble(
          "status",
          "Сессия SDK готова. Пиши сюда — агент ходит на сервер через ssh_run / ssh_read / ssh_write."
        );
      }
    }

    function showAgentDock(session, history) {
      agentSession = session;
      agentDock.classList.add("open");
      agentDock.setAttribute("aria-hidden", "false");
      agentDockMeta.innerHTML =
        `<b>${escapeHtml(session.host)}</b> · ${escapeHtml(session.path)}`;
      renderAgentHistory(history || []);
      agentInput.focus();
    }

    function hideAgentDock() {
      agentDock.classList.remove("open");
      agentDock.setAttribute("aria-hidden", "true");
    }

    async function sendAgentMessage() {
      if (!agentSession || agentBusy) return;
      const message = (agentInput.value || "").trim();
      if (!message) return;
      agentBusy = true;
      agentSend.disabled = true;
      agentInput.value = "";
      let assistantEl = null;
      try {
        const res = await fetch("/api/agent/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            host: agentSession.host,
            path: agentSession.path,
            message,
          }),
        });
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop() || "";
          for (const chunk of parts) {
            const line = chunk.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            let ev;
            try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
            if (ev.type === "user") {
              appendAgentBubble("user", ev.text);
            } else if (ev.type === "assistant") {
              if (!assistantEl) assistantEl = appendAgentBubble("assistant", "");
              assistantEl.textContent += ev.text || "";
              agentChat.scrollTop = agentChat.scrollHeight;
            } else if (ev.type === "error") {
              appendAgentBubble("error", ev.text || "ошибка");
            } else if (ev.type === "done") {
              appendAgentBubble("status", "готово: " + (ev.text || ""));
            } else {
              appendAgentBubble(ev.type || "status", ev.text || "");
            }
          }
        }
      } catch (e) {
        appendAgentBubble("error", "сеть / сервер: " + (e && e.message ? e.message : e));
      } finally {
        agentBusy = false;
        agentSend.disabled = false;
        agentInput.focus();
      }
    }

    document.getElementById("agentDockClose").addEventListener("click", hideAgentDock);
    document.getElementById("agentDockIde").addEventListener("click", () => {
      if (!agentSession) return;
      openCursorPath(agentSession.host, agentSession.path, null, agentSession.uri, "ide");
    });
    agentSend.addEventListener("click", sendAgentMessage);
    agentInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendAgentMessage();
      }
    });

    async function openCursorPath(host, path, statusEl, uri, mode) {
      if (!path) return;
      const endpoint = mode === "agent" ? "/api/open-agent" : "/api/open-cursor";
      if (statusEl) {
        statusEl.className = "cursor-status";
        statusEl.textContent = mode === "agent" ? "открываю агента…" : "открываю…";
      }
      try {
        const payload = { host, path };
        if (uri) payload.uri = uri;
        const res = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (statusEl) {
          if (data.ok) {
            statusEl.className = "cursor-status ok";
            statusEl.textContent = mode === "agent" ? "агент SDK готов" : "открыто в новом окне";
          } else {
            statusEl.className = "cursor-status bad";
            statusEl.textContent = data.error || "ошибка";
          }
        }
        if (data.ok && mode === "agent") {
          showAgentDock(
            {
              host,
              path,
              uri: data.uri || uri || "",
            },
            data.history || []
          );
        }
        return data;
      } catch (e) {
        if (statusEl) {
          statusEl.className = "cursor-status bad";
          statusEl.textContent = "сеть / сервер недоступен";
        }
        return { ok: false };
      }
    }

    async function openProject(host, mode) {
      const select = grid.querySelector(`select.proj-select[data-host="${host}"]`);
      const status = grid.querySelector(`[data-status-for="${host}"]`);
      const path = select ? select.value : selectedByHost[host];
      if (!path) return;
      selectedByHost[host] = path;
      const opt = select ? select.selectedOptions[0] : null;
      const uri = opt && opt.dataset ? opt.dataset.uri : undefined;
      await openCursorPath(host, path, status, uri, mode || "ide");
    }

    function setBrowseMsg(text, bad) {
      browseMsg.textContent = text || "";
      browseMsg.className = bad ? "modal-msg bad" : "modal-msg";
    }

    function setBrowseSelection(path) {
      browseSelPath = path;
      browsePath.textContent = path || "выбери папку в дереве";
      browseOpen.disabled = !path;
      browseTree.querySelectorAll(".tree-row").forEach(el => {
        el.classList.toggle("selected", el.dataset.path === path);
      });
    }

    async function fetchList(host, path) {
      const key = `${host}|${path || ""}`;
      if (treeCache[key]) return treeCache[key];
      const q = path
        ? `/api/fs/list?host=${encodeURIComponent(host)}&path=${encodeURIComponent(path)}`
        : `/api/fs/list?host=${encodeURIComponent(host)}`;
      const res = await fetch(q, { cache: "no-store" });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "list failed");
      treeCache[key] = data;
      return data;
    }

    function renderTreeNode(host, entry, depth) {
      const key = `${host}|${entry.path}`;
      const expanded = !!treeExpanded[key];
      const twisty = entry.has_children ? (expanded ? "▾" : "▸") : "·";
      const git = entry.is_git ? `<span class="git">git</span>` : "";
      let html = `<div class="tree-row" data-host="${escapeHtml(host)}" data-path="${escapeHtml(entry.path)}" data-has-children="${entry.has_children ? "1" : "0"}" style="padding-left:${0.45 + depth * 0.55}rem">
        <span class="twisty" data-toggle="1">${twisty}</span>
        <span class="name">${escapeHtml(entry.name)}</span>
        ${git}
      </div>`;
      if (expanded) {
        const cached = treeCache[`${host}|${entry.path}`];
        if (cached && cached.entries) {
          html += `<div class="tree-children">` +
            cached.entries.map(ch => renderTreeNode(host, ch, depth + 1)).join("") +
            `</div>`;
        } else {
          html += `<div class="tree-children"><div class="tree-empty">…</div></div>`;
        }
      }
      return html;
    }

    async function paintBrowseTree(host, rootData) {
      if (!browseSelPath) {
        browsePath.textContent = rootData.path || "выбери папку в дереве";
      }
      const rows = (rootData.entries || []).map(e => renderTreeNode(host, e, 0)).join("");
      browseTree.innerHTML = `
        <div class="tree-row" data-host="${escapeHtml(host)}" data-path="${escapeHtml(rootData.path)}" data-has-children="1">
          <span class="twisty">★</span>
          <span class="name">.</span>
        </div>
        ${rows || `<div class="tree-empty">пусто</div>`}
      `;
      if (browseSelPath) setBrowseSelection(browseSelPath);
    }

    async function openBrowseModal(host) {
      browseHost = host;
      browseSelPath = null;
      browseTitle.textContent = `New Cursor Project · ${host}`;
      browseOpen.disabled = true;
      browsePath.textContent = "выбери папку в дереве";
      browseTree.innerHTML = `<div class="tree-empty">загрузка…</div>`;
      setBrowseMsg("");
      browseModal.classList.add("open");
      browseModal.setAttribute("aria-hidden", "false");
      try {
        const data = await fetchList(host, null);
        await paintBrowseTree(host, data);
      } catch (e) {
        setBrowseMsg(String(e.message || e), true);
        browseTree.innerHTML = "";
      }
    }

    function closeBrowseModal() {
      browseModal.classList.remove("open");
      browseModal.setAttribute("aria-hidden", "true");
    }

    browseClose.addEventListener("click", closeBrowseModal);
    browseModal.addEventListener("click", (e) => {
      if (e.target === browseModal) closeBrowseModal();
    });

    browseTree.addEventListener("click", async (e) => {
      const row = e.target.closest(".tree-row");
      if (!row || !browseHost) return;
      const path = row.dataset.path;
      setBrowseSelection(path);
      const key = `${browseHost}|${path}`;
      if (e.target.closest("[data-toggle]") && row.dataset.hasChildren === "1") {
        treeExpanded[key] = !treeExpanded[key];
        setBrowseMsg(treeExpanded[key] ? "загрузка…" : "");
        try {
          if (treeExpanded[key]) await fetchList(browseHost, path);
          const root = await fetchList(browseHost, null);
          await paintBrowseTree(browseHost, root);
          setBrowseMsg("");
        } catch (err) {
          treeExpanded[key] = false;
          setBrowseMsg(String(err.message || err), true);
        }
      }
    });

    browseOpen.addEventListener("click", async () => {
      if (!browseHost || !browseSelPath) return;
      browseOpen.disabled = true;
      setBrowseMsg("открываю…");
      const status = grid.querySelector(`[data-status-for="${browseHost}"]`);
      const data = await openCursorPath(browseHost, browseSelPath, status, undefined, "ide");
      if (data && data.ok) {
        const list = projectsByHost[browseHost] || (projectsByHost[browseHost] = []);
        if (!list.some(p => p.path === browseSelPath)) {
          const parts = browseSelPath.split(/[/\\]/).filter(Boolean);
          list.push({ path: browseSelPath, label: parts.slice(-2).join("/") || browseSelPath });
          list.sort((a, b) => a.path.localeCompare(b.path));
        }
        selectedByHost[browseHost] = browseSelPath;
        closeBrowseModal();
      } else {
        setBrowseMsg((data && data.error) || "ошибка", true);
        browseOpen.disabled = false;
      }
    });

    function updateOnlineBadges(servers) {
      // Keep 5s "pulse" (online/latency) without rebuilding the card DOM.
      (servers || []).forEach((s) => {
        const card = grid.querySelector(`.server-${CSS.escape(s.host)}`);
        if (!card) return;
        const head = card.querySelector(".server-head");
        if (!head) return;
        let badge = head.querySelector(".badge");
        if (!badge) {
          badge = document.createElement("span");
          head.appendChild(badge);
        }
        if (hostOnline(s)) {
          badge.className = "badge ok";
          const via = s.ssh_via && s.ssh_via !== s.host ? ` via ${s.ssh_via}` : "";
          badge.textContent = `${s.ok ? "online" : "reachable"}${via} · ${s.latency_ms ?? "—"} ms`;
        } else {
          badge.className = "badge down";
          badge.textContent = "offline";
        }
      });
    }

    function isProjectPickerBusy() {
      if (browseModal.classList.contains("open")) return true;
      // Never replace or move a card while its settings popup is open.
      // Removing the focused gear button makes browsers scroll its replacement
      // into view, which used to jump the right-hand page/slider to the top.
      if (grid.querySelector(".card-settings-menu.open")) return true;
      const active = document.activeElement;
      return !!(active && active.classList && (active.classList.contains("proj-select") || active.classList.contains("home-user-select")));
    }

    grid.addEventListener("change", (e) => {
      const t = e.target;
      if (t && t.classList && t.classList.contains("proj-select")) {
        selectedByHost[t.dataset.host] = t.value;
      }
    });

    grid.addEventListener("scroll", (e) => {
      const t = e.target;
      if (!t || !t.classList || !t.classList.contains("ram-scroll")) return;
      if (t.id === "ramTopScroll") {
        ramTopScroll = t.scrollTop;
        return;
      }
      const box = t.closest(".vram-top");
      if (box && box.dataset.vramKey) {
        vramTopScrollByKey[box.dataset.vramKey] = t.scrollTop;
      }
    }, true);

    grid.addEventListener("click", (e) => {
      const openBtn = e.target.closest(".proj-open");
      if (openBtn) {
        openProject(openBtn.dataset.host, "ide");
        return;
      }
      const agentBtn = e.target.closest(".proj-agent");
      if (agentBtn) {
        openProject(agentBtn.dataset.host, "agent");
        return;
      }
      const browseBtn = e.target.closest(".browse-btn");
      if (browseBtn) openBrowseModal(browseBtn.dataset.host);
      const statsBtn = e.target.closest(".gpu-stats-btn");
      if (statsBtn) {
        showGpuStats(statsBtn.dataset.host);
      }
    });

    const cardSignatures = new Map();
    function renderGrid() {
      const data = window._lastMetricsData;
      if (!data) return;
      const ts = new Date((data.updated_at || 0) * 1000);
      updatedEl.textContent = ts.toLocaleTimeString();
      if (isProjectPickerBusy()) {
        updateOnlineBadges(data.servers || []);
        return;
      }
      const viewportX = window.scrollX;
      const viewportY = window.scrollY;
      const gridScrollLeft = grid.scrollLeft;
      const gridScrollTop = grid.scrollTop;
      // The local probe is not a regular fleet card. The backend exposes it as
      // `controller` only to admin; retain that card while ignoring raw local.
      const servers = (data.servers || []).filter(s => s.host !== "local");
      const wanted = new Set(servers.map(s => s.host));
      grid.querySelectorAll("section.server[data-host]").forEach(card => {
        if (!wanted.has(card.dataset.host)) {
          cardSignatures.delete(card.dataset.host);
          card.remove();
        }
      });
      let nextPosition = grid.firstElementChild;
      for (const server of servers) {
        const signature = JSON.stringify(server);
        let card = grid.querySelector(`section.server[data-host="${CSS.escape(server.host)}"]`);
        if (!card || cardSignatures.get(server.host) !== signature) {
          const template = document.createElement("template");
          template.innerHTML = renderServer(server).trim();
          const replacement = template.content.firstElementChild;
          const menuWasOpen = !!card?.querySelector(".card-settings-menu.open");
          if (menuWasOpen) replacement.querySelector(".card-settings-menu")?.classList.add("open");
          if (card) {
            const occupiedNextPosition = card === nextPosition;
            card.replaceWith(replacement);
            if (occupiedNextPosition) nextPosition = replacement;
          }
          else grid.insertBefore(replacement, nextPosition);
          card = replacement;
          cardSignatures.set(server.host, signature);
        }
        // Preserve existing nodes when the order is already correct. The old
        // unconditional appendChild moved every card on every polling tick.
        if (card !== nextPosition) grid.insertBefore(card, nextPosition);
        nextPosition = card.nextElementSibling;
      }
      let addCard = grid.querySelector("#addServerCard");
      if (!addCard) {
        addCard = document.createElement("div");
        addCard.className = "add-card";
        addCard.id = "addServerCard";
        addCard.title = "Добавить сервер";
        addCard.innerHTML = '<div class="add-card-icon">+</div><div class="add-card-label">Добавить сервер</div>';
      }
      if (grid.lastElementChild !== addCard) grid.appendChild(addCard);
      restoreScrollPositions();
      grid.scrollLeft = gridScrollLeft;
      grid.scrollTop = gridScrollTop;
      window.scrollTo(viewportX, viewportY);
      
      // Restore home usage selections from localStorage
      document.querySelectorAll('.home-user-select').forEach(select => {
        const host = select.dataset.host;
        const saved = localStorage.getItem(`home_usage_${host}`);
        if (saved && Array.from(select.options).some(opt => opt.value === saved)) {
          select.value = saved;
        }
      });
      
      firstPaint = false;
    }

    let _tickSeq = 0;
    let _tickRunning = false;
    let _serverMutationVersion = 0;
    const LOCAL_ADDED_HOSTS_KEY = "gpu_monitor_locally_added_hosts_v1";
    const LOCAL_DELETED_HOSTS_KEY = "gpu_monitor_locally_deleted_hosts_v1";
    const LAST_HOST_ERRORS_KEY = "gpu_monitor_last_host_errors_v1";

    function loadHostNames(key) {
      try {
        const value = JSON.parse(localStorage.getItem(key) || "[]");
        return Array.isArray(value)
          ? value.filter(host => typeof host === "string" && /^[a-zA-Z0-9_-]+$/.test(host))
          : [];
      } catch (_) {
        return [];
      }
    }

    const locallyAddedHosts = new Map(loadHostNames(LOCAL_ADDED_HOSTS_KEY).map(host => [host, null]));
    const locallyDeletedHosts = new Set(loadHostNames(LOCAL_DELETED_HOSTS_KEY));
    let lastHostErrors = {};
    try {
      const storedErrors = JSON.parse(localStorage.getItem(LAST_HOST_ERRORS_KEY) || "{}");
      if (storedErrors && typeof storedErrors === "object" && !Array.isArray(storedErrors)) {
        lastHostErrors = storedErrors;
      }
    } catch (_) {}

    function persistLocalHostMutations() {
      localStorage.setItem(LOCAL_ADDED_HOSTS_KEY, JSON.stringify([...locallyAddedHosts.keys()]));
      localStorage.setItem(LOCAL_DELETED_HOSTS_KEY, JSON.stringify([...locallyDeletedHosts]));
    }

    function hostPlaceholder(host) {
      return {
        host,
        label: host,
        ok: false,
        reachable: false,
        connection_ok: false,
        polling: true,
        error: "подключение…",
        latency_ms: null,
        gpus: [],
        ram: null,
        paths: [],
      };
    }

    function isLoadingPlaceholder(row) {
      return !!row && !row.ok && /загруз|подключ|prob|loading/i.test(row.error || "");
    }

    function rememberHostError(row) {
      if (!row?.host || row.ok || !row.error || isLoadingPlaceholder(row)) return;
      lastHostErrors[row.host] = {
        host: row.host,
        ok: false,
        reachable: !!row.reachable,
        connection_ok: false,
        polling: false,
        stale: false,
        error: row.error,
        latency_ms: row.latency_ms ?? null,
        last_attempt_at: row.last_attempt_at || Date.now() / 1000,
        gpus: [],
        ram: null,
        paths: row.paths || [],
      };
    }

    function persistLastHostErrors() {
      localStorage.setItem(LAST_HOST_ERRORS_KEY, JSON.stringify(lastHostErrors));
    }

    function retainLastKnownMetrics(data) {
      const previousRows = window._lastMetricsData?.servers || [];
      const previousByHost = new Map(previousRows.map(row => [row.host, row]));
      const rows = (data.servers || []).filter(next => !locallyDeletedHosts.has(next.host)).map(next => {
        const previous = previousByHost.get(next.host);
        const hasPreviousMetrics = previous && previous.ok && (
          (previous.gpus && previous.gpus.length) || previous.ram || previous.disk
        );
        if (next.ok) {
          if (lastHostErrors[next.host]) delete lastHostErrors[next.host];
          return next;
        }
        const previousConcreteError = previous && !previous.ok && previous.error && !isLoadingPlaceholder(previous)
          ? previous
          : lastHostErrors[next.host];
        if (isLoadingPlaceholder(next) && previousConcreteError && !hasPreviousMetrics) {
          return {
            ...next,
            ...previousConcreteError,
            polling: false,
            last_attempt_at: next.last_attempt_at || previousConcreteError.last_attempt_at,
          };
        }
        if (!isLoadingPlaceholder(next)) rememberHostError(next);
        if (!hasPreviousMetrics) return next;
        return {
          ...previous,
          connection_ok: false,
          reachable: !!next.reachable,
          stale: true,
          polling: false,
          error: isLoadingPlaceholder(next)
            ? (previousConcreteError?.error || previous.error || "обновление метрик…")
            : (next.error || previous.error || "ошибка соединения"),
          last_attempt_at: next.last_attempt_at || Date.now() / 1000,
          paths: (next.paths && next.paths.length) ? next.paths : previous.paths,
        };
      });
      const responseByHost = new Map(rows.map(row => [row.host, row]));
      for (const [host, optimistic] of locallyAddedHosts) {
        if (locallyDeletedHosts.has(host)) continue;
        const current = responseByHost.get(host);
        if (current) {
          // Remember the strongest backend acknowledgement. If a later stale
          // aggregate snapshot omits the host, its card still remains.
          if (isLoadingPlaceholder(current) && optimistic && !isLoadingPlaceholder(optimistic)) {
            const index = rows.indexOf(current);
            if (index >= 0) rows[index] = optimistic;
            responseByHost.set(host, optimistic);
          } else {
            locallyAddedHosts.set(host, current);
          }
          continue;
        }
        const retained = optimistic || previousByHost.get(host) || hostPlaceholder(host);
        rows.push(retained);
        responseByHost.set(host, retained);
      }
      persistLastHostErrors();
      data.servers = rows;
      return data;
    }

    async function tick() {
      if (!currentUser) return;
      if (_tickRunning) return;
      _tickRunning = true;
      const seq = ++_tickSeq;
      const mutationVersion = _serverMutationVersion;
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), 12000);
      try {
        const res = await fetch("/api/metrics", { cache: "no-store", signal: ctrl.signal });
        if (res.status === 401) {
          openUserModal(true);
          return;
        }
        if (seq !== _tickSeq || mutationVersion !== _serverMutationVersion) return;
        const data = retainLastKnownMetrics(await res.json());
        if (seq !== _tickSeq || mutationVersion !== _serverMutationVersion) return;
        window._lastMetricsData = data;
        renderGrid();
      } catch (e) {
        if (seq !== _tickSeq || mutationVersion !== _serverMutationVersion) return;
        updatedEl.textContent = "ошибка опроса";
      } finally {
        clearTimeout(timeout);
        _tickRunning = false;
      }
      loadQuotas();
    }

    // -------- AI Coding Quotas panel --------
    const quotasList = document.getElementById("quotasList");
    const quotasSub = document.getElementById("quotasSub");
    const quotasRefreshBtn = document.getElementById("quotasRefreshBtn");
    const quotasSettingsBtn = document.getElementById("quotasSettingsBtn");
    const quotasSettings = document.getElementById("quotasSettings");
    const quotasSettingsSaveBtn = document.getElementById("quotasSettingsSaveBtn");
    const quotasSettingsCancelBtn = document.getElementById("quotasSettingsCancelBtn");
    const quotasSettingsMsg = document.getElementById("quotasSettingsMsg");
    const quotasMinimaxKeyInput = document.getElementById("quotaMinimaxKey");

    const QUOTA_STATUS_LABEL = {
      ok: "OK",
      not_configured: "Not configured",
      not_logged_in: "Not logged in",
      cli_not_found: "CLI not found",
      api_unavailable: "API unavailable",
      rate_limited: "Rate limited",
      stale: "Stale",
      error: "Error",
    };

    function fmtAgo(iso) {
      if (!iso) return "—";
      const t = Date.parse(iso);
      if (!Number.isFinite(t)) return "—";
      const s = Math.max(0, Math.round((Date.now() - t) / 1000));
      if (s < 60) return `${s}s ago`;
      const m = Math.round(s / 60);
      if (m < 60) return `${m}m ago`;
      const h = Math.floor(m / 60);
      const mr = m % 60;
      return `${h}h ${mr.toString().padStart(2, "0")}m ago`;
    }

    function fmtCountdown(resetIso) {
      if (!resetIso) return null;
      const t = Date.parse(resetIso);
      if (!Number.isFinite(t)) return null;
      let sec = Math.max(0, Math.round((t - Date.now()) / 1000));
      const d = Math.floor(sec / 86400); sec -= d * 86400;
      const h = Math.floor(sec / 3600);  sec -= h * 3600;
      const m = Math.floor(sec / 60);
      if (d > 0) return `${d}d ${h.toString().padStart(2, "0")}h`;
      if (h > 0) return `${h}h ${m.toString().padStart(2, "0")}m`;
      return `${m}m`;
    }

    function barClass(used) {
      if (used < 60) return "low";
      if (used < 85) return "mid";
      return "hi";
    }

    function pillClass(status, stale) {
      if (stale) return "stale";
      if (status === "ok") return "ok";
      if (status === "rate_limited" || status === "api_unavailable" || status === "error") return "bad";
      return "";
    }

    function pillText(status, stale) {
      if (stale) return "STALE";
      return QUOTA_STATUS_LABEL[status] || status || "—";
    }

    // Map a provider window to one of the standard UI rows by
    // window_seconds. Returns the row key or null if the window
    // doesn't fit a standard row.
    function rowKeyForWindow(w) {
      const ws = Number(w.window_seconds) || 0;
      if (ws === 18000) return "5h";
      if (ws === 604800) return "7d";
      if (ws === 2592000 || ws === 2678400 || ws === 2419200) return "monthly";
      return null;
    }

    // All windows matching a row key (e.g. Kimi has 2 monthly windows).
    function pickWindows(windows, key) {
      if (!Array.isArray(windows)) return [];
      return windows.filter((w) => rowKeyForWindow(w) === key);
    }

    function renderStandardRow(rowKey, label, w, extraClass = "") {
      if (!w) {
        return `
          <div class="quota-row ${extraClass}" data-row="${escapeHtml(rowKey)}">
            <div class="quota-row-label">${escapeHtml(label)}</div>
            <div class="quota-bar"><div class="quota-bar-fill" style="width:0%; background: rgba(148,163,184,0.20)"></div></div>
            <div class="quota-row-meta">
              <div class="quota-pct">Not provided by provider</div>
            </div>
          </div>`;
      }
      const used = Number(w.used_percent);
      const remaining = Number(w.remaining_percent);
      const cls = barClass(Number.isFinite(used) ? used : 0);
      const cd = w.reset_in_seconds != null
        ? fmtCountdownSec(w.reset_in_seconds)
        : fmtCountdown(w.reset_at);
      const usedStr = Number.isFinite(used) ? `${used.toFixed(0)}%` : "—";
      const remStr = Number.isFinite(remaining) ? `${remaining.toFixed(0)}%` : "—";
      const resetTxt = cd ? `reset in ${cd}` : "";
      return `
        <div class="quota-row ${extraClass}" data-row="${escapeHtml(rowKey)}">
          <div class="quota-row-label">${escapeHtml(label)}</div>
          <div class="quota-bar"><div class="quota-bar-fill ${cls}" style="width:${Math.max(2, Number.isFinite(used) ? used : 0).toFixed(1)}%"></div></div>
          <div class="quota-row-meta">
            <div class="quota-pct"><strong>${usedStr} used</strong> · ${remStr} remaining</div>
            ${resetTxt ? `<div class="quota-row-sub">${resetTxt}</div>` : ""}
          </div>
        </div>`;
    }

    function renderExtraRow(w) {
      const used = Number(w.used_percent);
      const remaining = Number(w.remaining_percent);
      const cls = barClass(Number.isFinite(used) ? used : 0);
      const usedStr = Number.isFinite(used) ? `${used.toFixed(0)}%` : "—";
      const remStr = Number.isFinite(remaining) ? `${remaining.toFixed(0)}%` : "—";
      const cd = w.reset_in_seconds != null
        ? fmtCountdownSec(w.reset_in_seconds)
        : fmtCountdown(w.reset_at);
      const lbl = w.label && w.label !== "Window" ? w.label : (w.id && w.id !== "window" ? w.id : "Window");
      return `
        <div class="quota-row quota-row-extra" data-row="${escapeHtml(w.id || 'extra')}">
          <div class="quota-row-label">${escapeHtml(lbl)}</div>
          <div class="quota-bar"><div class="quota-bar-fill ${cls}" style="width:${Math.max(2, Number.isFinite(used) ? used : 0).toFixed(1)}%"></div></div>
          <div class="quota-row-meta">
            <div class="quota-pct"><strong>${usedStr} used</strong> · ${remStr} remaining</div>
            ${cd ? `<div class="quota-row-sub">reset in ${cd}</div>` : ""}
          </div>
        </div>`;
    }

    function renderExtrasRows(windows) {
      if (!Array.isArray(windows)) return "";
      const extras = windows.filter((w) => !rowKeyForWindow(w));
      return extras.map(renderExtraRow).join("");
    }

    function fmtCountdownSec(sec) {
      sec = Math.max(0, Math.round(sec));
      const d = Math.floor(sec / 86400); sec -= d * 86400;
      const h = Math.floor(sec / 3600);  sec -= h * 3600;
      const m = Math.floor(sec / 60);
      if (d > 0) return `${d}d ${h.toString().padStart(2, "0")}h`;
      if (h > 0) return `${h}h ${m.toString().padStart(2, "0")}m`;
      return `${m}m`;
    }

    function renderProviderCard(p, prov) {
      const status = prov.status || "error";
      const stale = !!prov.stale;
      const pill = pillClass(status, stale);
      const label = prov.display_name || p;
      const plan = prov.plan_type ? ` · ${escapeHtml(prov.plan_type)}` : "";
      const windows = Array.isArray(prov.windows) ? prov.windows : [];

      // 5h / 7d — first matching window only.
      const w5 = pickWindows(windows, "5h")[0] || null;
      const w7 = pickWindows(windows, "7d")[0] || null;
      // Monthly — show ALL matching windows (Kimi has Monthly total +
      // Monthly code). Always render the section, even when empty.
      const monthly = pickWindows(windows, "monthly");

      let rowsHtml = "";
      rowsHtml += renderStandardRow("5h", "5h", w5);
      rowsHtml += renderStandardRow("7d", "7d", w7);
      if (monthly.length === 0) {
        rowsHtml += renderStandardRow("monthly", "Monthly", null);
      } else {
        monthly.forEach((w, i) => {
          const sub = monthly.length > 1 ? ` (${i + 1}/${monthly.length})` : "";
          const lbl = (w.label && w.label !== "Window") ? w.label + sub : "Monthly" + sub;
          rowsHtml += renderStandardRow(`monthly_${i}`, lbl, w);
        });
      }
      // Non-standard extras (code_review_window, etc.).
      rowsHtml += renderExtrasRows(windows);

      const metaBits = [`updated ${fmtAgo(prov.updated_at || prov.last_success_iso)}`];
      if (stale) metaBits.push("stale · using last successful result");
      if (prov.source) metaBits.push(escapeHtml(prov.source));
      const errHtml = prov.last_error
        ? `<div class="quota-err">${escapeHtml(prov.last_error)}</div>`
        : "";

      return `
        <div class="quota" data-provider="${escapeHtml(p)}">
          <div class="quota-head">
            <div class="quota-name">${escapeHtml(label)}${plan}</div>
            <div class="quota-pill ${pill}">${escapeHtml(pillText(status, stale))}</div>
          </div>
          <div class="quota-rows">
            ${rowsHtml}
          </div>
          <div class="quota-meta">${metaBits.join(" · ")}</div>
          ${errHtml}
        </div>`;
    }

    function renderQuotas(snapshot) {
      if (!quotasList) return;
      const providers = (snapshot && snapshot.providers) || {};
      const order = ["minimax", "kimi", "codex"];
      const cards = order
        .filter((p) => providers[p])
        .map((p) => renderProviderCard(p, providers[p]))
        .join("");
      quotasList.innerHTML = cards || '<div class="quota-meta">No data yet.</div>';

      const anyOk = order.some((p) => providers[p] && providers[p].status === "ok" && !providers[p].stale);
      const anyStale = order.some((p) => providers[p] && providers[p].stale);
      const cfg = (snapshot && snapshot.config) || {};
      const refreshTxt = cfg.refresh_sec ? `auto-refresh ≈ every ${Math.round(cfg.refresh_sec)}s` : "auto-refresh";
      if (quotasSub) {
        if (anyOk) quotasSub.textContent = refreshTxt;
        else if (anyStale) quotasSub.textContent = "stale · waiting for next refresh";
        else quotasSub.textContent = "providers unavailable — see status";
      }
    }

    async function loadQuotas(opts = {}) {
      if (!quotasList) return;
      const force = !!opts.force;
      const url = force ? "/api/quotas/refresh" : "/api/quotas";
      try {
        const res = await fetchJson(url, { method: force ? "POST" : "GET", cache: "no-store" }, 12000);
        if (!res || !res.ok) {
          if (quotasSub) quotasSub.textContent = "fetch error";
          return;
        }
        renderQuotas(res);
      } catch (e) {
        if (quotasSub) quotasSub.textContent = "fetch error: " + (e && e.message || "");
      }
    }

    if (quotasRefreshBtn) {
      quotasRefreshBtn.addEventListener("click", async () => {
        quotasRefreshBtn.disabled = true;
        try {
          await loadQuotas({ force: true });
        } finally {
          quotasRefreshBtn.disabled = false;
        }
      });
    }

    if (quotasSettingsBtn) {
      quotasSettingsBtn.addEventListener("click", () => {
        quotasSettings.hidden = !quotasSettings.hidden;
        if (!quotasSettings.hidden) {
          quotasMinimaxKeyInput.value = "";
          quotasSettingsMsg.textContent = "Key is stored only in .env (gitignored) and never sent back.";
        }
      });
    }
    if (quotasSettingsCancelBtn) {
      quotasSettingsCancelBtn.addEventListener("click", () => {
        quotasSettings.hidden = true;
      });
    }
    if (quotasSettingsSaveBtn) {
      quotasSettingsSaveBtn.addEventListener("click", async () => {
        quotasSettingsSaveBtn.disabled = true;
        quotasSettingsMsg.textContent = "saving…";
        try {
          const api_key = quotasMinimaxKeyInput.value.trim() || null;
          if (!api_key) {
            quotasSettingsMsg.textContent = "enter the MiniMax API key";
            return;
          }
          const res = await fetchJson("/api/quotas/config",
            { method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ api_key }) }, 8000);
          if (res && res.ok) {
            quotasSettingsMsg.textContent = "saved — refreshing…";
            quotasSettings.hidden = true;
            await loadQuotas({ force: true });
          } else {
            quotasSettingsMsg.textContent = (res && res.error) || "save failed";
          }
        } catch (e) {
          quotasSettingsMsg.textContent = "save error: " + (e && e.message || "");
        } finally {
          quotasSettingsSaveBtn.disabled = false;
        }
      });
    }

    // Reset-countdown is local — recompute every minute without a fetch.
    setInterval(() => {
      const card = document.getElementById("quotasCard");
      if (!card) return;
      // Force a re-render by re-fetching the snapshot (cache hit, fast).
      loadQuotas();
    }, 30000);

    loadProjects();
    loadQuotas();
    // Initial metrics load starts only after the IP-bound user is resolved.
    setInterval(tick, 5000);

    // -------- GPU Stats Chart --------
    let gpuVramChartInstance = null;
    let gpuUtilChartInstance = null;
    const chartModal = document.getElementById("chartModal");
    const chartClose = document.getElementById("chartClose");
    const chartTitle = document.getElementById("chartTitle");

    async function showGpuStats(host) {
      try {
        const res = await fetch(`/api/gpu-history?host=${encodeURIComponent(host)}&hours=336`);
        const data = await res.json();
        if (!data.ok) {
          alert("Error loading history: " + (data.error || "unknown"));
          return;
        }

        const gpus = data.gpus || {};
        const gpuIndices = Object.keys(gpus).map(k => parseInt(k, 10)).sort((a, b) => a - b);

        chartTitle.textContent = `GPU Stats — ${host}`;
        chartModal.classList.add("open");
        chartModal.setAttribute("aria-hidden", "false");

        // Destroy existing charts
        if (gpuVramChartInstance) {
          gpuVramChartInstance.destroy();
          gpuVramChartInstance = null;
        }
        if (gpuUtilChartInstance) {
          gpuUtilChartInstance.destroy();
          gpuUtilChartInstance = null;
        }

        if (gpuIndices.length === 0) {
          // Show empty charts
          const vramCtx = document.getElementById("gpuVramChart").getContext("2d");
          const utilCtx = document.getElementById("gpuUtilChart").getContext("2d");
          gpuVramChartInstance = new Chart(vramCtx, {
            type: "line",
            data: { labels: [], datasets: [] },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: {
                legend: {
                  display: true,
                  position: "top",
                  labels: {
                    color: "#cbd5e1",
                    font: { color: "#cbd5e1" },
                    usePointStyle: false,
                    boxWidth: 20,
                    boxHeight: 10,
                    padding: 15
                  }
                }
              },
              scales: {
                x: {
                  type: "time",
                  time: { unit: "day", displayFormats: { day: "MMM dd", hour: "MMM dd HH:mm" } },
                  ticks: { display: true, color: "#94a3b8", maxRotation: 45 },
                  title: { display: true, text: "Date", color: "#cbd5e1" }
                },
                y: { type: "linear", beginAtZero: true, max: 100, title: { display: true, text: "Percentage %" } }
              }
            }
          });
          gpuUtilChartInstance = new Chart(utilCtx, {
            type: "line",
            data: { labels: [], datasets: [] },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: {
                legend: {
                  display: true,
                  position: "top",
                  labels: {
                    color: "#cbd5e1",
                    font: { color: "#cbd5e1" },
                    usePointStyle: false,
                    boxWidth: 20,
                    boxHeight: 10,
                    padding: 15
                  }
                }
              },
              scales: {
                x: {
                  type: "time",
                  time: { unit: "day", displayFormats: { day: "MMM dd", hour: "MMM dd HH:mm" } },
                  ticks: { display: true, color: "#94a3b8", maxRotation: 45 },
                  title: { display: true, text: "Date", color: "#cbd5e1" }
                },
                y: { type: "linear", beginAtZero: true, max: 100, title: { display: true, text: "Percentage %" } }
              }
            }
          });
          return;
        }

        // Collect all timestamps
        const allTimestamps = new Set();
        gpuIndices.forEach(idx => {
          gpus[idx].forEach(h => allTimestamps.add(h.timestamp));
        });
        const sortedTimestamps = Array.from(allTimestamps).sort((a, b) => a - b);
        const labels = sortedTimestamps.map(ts => new Date(ts * 1000));

        // Build datasets for each GPU
        const vramDatasets = [];
        const utilDatasets = [];
        const colors = [
          "rgb(96, 165, 250)",
          "rgb(248, 113, 113)",
          "rgb(52, 211, 153)",
          "rgb(251, 191, 36)",
          "rgb(192, 132, 252)",
          "rgb(244, 114, 182)",
          "rgb(45, 212, 191)",
          "rgb(253, 186, 116)",
        ];

        gpuIndices.forEach((idx, i) => {
          const color = colors[i % colors.length];
          const gpuData = gpus[idx];
          const timestampMap = new Map(gpuData.map(h => [h.timestamp, h]));

          const vramData = sortedTimestamps.map(ts => {
            const point = timestampMap.get(ts);
            return point ? point.vram_pct : null;
          });
          const utilData = sortedTimestamps.map(ts => {
            const point = timestampMap.get(ts);
            return point ? point.util_pct : null;
          });

          vramDatasets.push({
            label: `GPU ${idx}`,
            data: vramData,
            borderColor: color,
            backgroundColor: color.replace("rgb", "rgba").replace(")", ", 0.1)"),
            tension: 0.3,
            pointRadius: 0,
          });
          utilDatasets.push({
            label: `GPU ${idx}`,
            data: utilData,
            borderColor: color,
            backgroundColor: color.replace("rgb", "rgba").replace(")", ", 0.1)"),
            tension: 0.3,
            pointRadius: 0,
          });
        });

        const chartOptions = {
          responsive: true,
          maintainAspectRatio: false,
          interaction: {
            mode: "index",
            intersect: false,
          },
          plugins: {
            legend: {
              display: true,
              position: "top",
              labels: {
                color: "#cbd5e1",
                font: { color: "#cbd5e1" },
                usePointStyle: false,
                boxWidth: 20,
                boxHeight: 10,
                padding: 15,
                generateLabels: function(chart) {
                  const datasets = chart.data.datasets;
                  return datasets.map((dataset, i) => ({
                    text: dataset.label,
                    fillStyle: dataset.borderColor,
                    strokeStyle: dataset.borderColor,
                    lineWidth: 0,
                    color: "#cbd5e1",
                    fontColor: "#cbd5e1",
                    hidden: !chart.isDatasetVisible(i),
                    datasetIndex: i
                  }));
                }
              }
            },
            tooltip: {
              callbacks: {
                title: function(context) {
                  const date = new Date(context[0].parsed.x);
                  return date.toLocaleString();
                }
              }
            }
          },
          scales: {
            x: {
              type: "time",
              time: {
                unit: "day",
                displayFormats: {
                  day: "MMM dd",
                  hour: "MMM dd HH:mm"
                }
              },
              ticks: {
                display: true,
                color: "#94a3b8",
                maxRotation: 45,
                autoSkip: true,
                maxTicksLimit: 14
              },
              title: {
                display: true,
                text: "Date",
                color: "#cbd5e1"
              }
            },
            y: {
              type: "linear",
              beginAtZero: true,
              max: 100,
              title: {
                display: true,
                text: "Percentage %"
              }
            }
          }
        };

        const vramCtx = document.getElementById("gpuVramChart").getContext("2d");
        const utilCtx = document.getElementById("gpuUtilChart").getContext("2d");
        
        gpuVramChartInstance = new Chart(vramCtx, {
          type: "line",
          data: { labels: labels, datasets: vramDatasets },
          options: chartOptions
        });
        
        gpuUtilChartInstance = new Chart(utilCtx, {
          type: "line",
          data: { labels: labels, datasets: utilDatasets },
          options: chartOptions
        });
      } catch (e) {
        alert("Error loading GPU stats: " + (e.message || e));
      }
    }

    function closeChartModal() {
      chartModal.classList.remove("open");
      chartModal.setAttribute("aria-hidden", "true");
      if (gpuVramChartInstance) {
        gpuVramChartInstance.destroy();
        gpuVramChartInstance = null;
      }
      if (gpuUtilChartInstance) {
        gpuUtilChartInstance.destroy();
        gpuUtilChartInstance = null;
      }
    }

    chartClose.addEventListener("click", closeChartModal);
    chartModal.addEventListener("click", (e) => {
      if (e.target === chartModal) closeChartModal();
    });

    // --- Add Server modal ---
    const addServerModal = document.getElementById("addServerModal");
    const addServerClose = document.getElementById("addServerClose");
    const addSshList = document.getElementById("addSshList");
    const addHostname = document.getElementById("addHostname");
    const addPort = document.getElementById("addPort");
    const addIp = document.getElementById("addIp");
    const addMsg = document.getElementById("addMsg");
    const addSubmit = document.getElementById("addSubmit");
    const addRefreshPeers = document.getElementById("addRefreshPeers");

    function openUserModal(required = false) {
      userModal.classList.add("open");
      userModal.setAttribute("aria-hidden", "false");
      userModal.dataset.required = required ? "1" : "0";
      userLoginError.textContent = "";
      userNameInput.value = required ? "" : (currentUser?.username || "");
      adminPasswordInput.value = "";
      showUsernameStep();
      setTimeout(() => userNameInput.focus(), 0);
    }

    function showUsernameStep() {
      userModalTitle.textContent = "Выбор пользователя";
      userNameField.hidden = false;
      adminPasswordField.hidden = true;
      userLoginBack.hidden = true;
      userLoginSubmit.textContent = "Продолжить";
      userLoginError.textContent = "";
    }

    function showAdminPasswordStep() {
      userModalTitle.textContent = "Пароль администратора";
      userNameField.hidden = true;
      adminPasswordField.hidden = false;
      userLoginBack.hidden = false;
      userLoginSubmit.textContent = "Войти";
      userLoginError.textContent = "";
      adminPasswordInput.value = "";
      setTimeout(() => adminPasswordInput.focus(), 0);
    }

    function closeUserModal() {
      if (userModal.dataset.required === "1") return;
      userModal.classList.remove("open");
      userModal.setAttribute("aria-hidden", "true");
    }

    async function loadSession() {
      const res = await fetch("/api/session", { cache: "no-store" });
      if (res.status === 404) {
        currentUser = { username: "legacy", is_admin: false };
        updateDebugUiButton();
        userSwitchBtn.textContent = "Пользователи: нужен перезапуск";
        await tick();
        return;
      }
      const data = await res.json();
      if (!data.user) return openUserModal(true);
      currentUser = data.user;
      updateDebugUiButton();
      userSwitchBtn.textContent = `Пользователь: ${currentUser.username}`;
      locallyAddedHosts.clear();
      locallyDeletedHosts.clear();
      persistLocalHostMutations();
      window._lastMetricsData = null;
      await tick();
    }

    userLoginBack.addEventListener("click", () => {
      showUsernameStep();
      setTimeout(() => userNameInput.focus(), 0);
    });
    userSwitchBtn.addEventListener("click", () => openUserModal(false));
    userModal.addEventListener("click", e => { if (e.target === userModal) closeUserModal(); });
    userLoginSubmit.addEventListener("click", async () => {
      const username = userNameInput.value.trim();
      if (!username) return;
      if (adminPasswordField.hidden && username.toLowerCase() === "admin") {
        showAdminPasswordStep();
        return;
      }
      userLoginSubmit.disabled = true;
      userLoginError.textContent = "";
      try {
        const res = await fetch("/api/session", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password: adminPasswordInput.value }),
        });
        const data = await res.json();
        if (res.status === 404) throw new Error("Перезапустите backend для включения пользователей");
        if (!res.ok) throw new Error(data.detail || "Ошибка входа");
        currentUser = data.user;
        updateDebugUiButton();
        userSwitchBtn.textContent = `Пользователь: ${currentUser.username}`;
        userModal.dataset.required = "0";
        closeUserModal();
        locallyAddedHosts.clear();
        locallyDeletedHosts.clear();
        persistLocalHostMutations();
        window._lastMetricsData = null;
        await tick();
      } catch (error) {
        userLoginError.textContent = error.message || "Ошибка входа";
      } finally {
        userLoginSubmit.disabled = false;
      }
    });
    [userNameInput, adminPasswordInput].forEach(input => input.addEventListener("keydown", e => {
      if (e.key === "Enter") userLoginSubmit.click();
    }));
    loadSession().catch(() => openUserModal(true));

    function openAddServer() {
      addServerModal.classList.add("open");
      addServerModal.setAttribute("aria-hidden", "false");
      addMsg.textContent = "";
      addMsg.className = "add-msg";
      loadSshHosts();
    }

    function closeAddServer() {
      addServerModal.classList.remove("open");
      addServerModal.setAttribute("aria-hidden", "true");
    }

    function validateAddForm() {
      const hostname = addHostname.value.trim();
      const ip = addIp.value.trim();
      const port = parseInt(addPort.value, 10);
      const valid = hostname && /^[a-zA-Z0-9_-]+$/.test(hostname) &&
                    ip.length > 0 &&
                    port >= 1 && port <= 65535;
      addSubmit.disabled = !valid;
    }

    async function loadSshHosts() {
      addSshList.innerHTML = '<span style="color: var(--muted); font-size: 0.78rem;">загрузка…</span>';
      try {
        const res = await fetch("/api/ssh-hosts");
        const data = await res.json();
        renderSshHosts(data.hosts || []);
      } catch (e) {
        addSshList.innerHTML = '<span style="color: var(--bad); font-size: 0.78rem;">ошибка загрузки</span>';
      }
    }

    function renderSshHosts(hosts) {
      if (!hosts.length) {
        addSshList.innerHTML = '<span style="color: var(--muted); font-size: 0.78rem;">нет доступных SSH хостов</span>';
        return;
      }
      addSshList.innerHTML = "";
      hosts.forEach((h) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "add-ssh-item";
        const alias = h.alias || "";
        const hostname = h.hostname || "";
        const user = h.user || "";
        btn.dataset.sshAlias = alias;
        btn.innerHTML = `<span class="ssh-alias">${escapeHtml(alias)}</span>` +
          (hostname ? `<span class="ssh-hostname">${escapeHtml(hostname)}</span>` : "") +
          (user ? `<span class="ssh-user">${escapeHtml(user)}</span>` : "");
        btn.addEventListener("click", () => {
          addHostname.value = alias;
          addIp.value = hostname || alias;
          if (h.port) addPort.value = h.port;
          addSshList.querySelectorAll(".add-ssh-item").forEach(c => c.classList.remove("selected"));
          btn.classList.add("selected");
          validateAddForm();
        });
        addSshList.appendChild(btn);
      });
    }

    grid.addEventListener("click", (e) => {
      const card = e.target.closest("#addServerCard");
      if (card) openAddServer();
    });

    grid.addEventListener("click", (e) => {
      const gearBtn = e.target.closest(".card-settings-btn");
      if (gearBtn) {
        e.stopPropagation();
        const menu = gearBtn.parentElement.querySelector(".card-settings-menu");
        const wasOpen = menu.classList.contains("open");
        document.querySelectorAll(".card-settings-menu.open").forEach(m => m.classList.remove("open"));
        if (!wasOpen) menu.classList.add("open");
        return;
      }
      const deleteBtn = e.target.closest('.card-settings-item[data-action="delete"]');
      if (deleteBtn) {
        e.stopPropagation();
        const host = deleteBtn.dataset.host;
        document.getElementById("deleteModalHost").textContent = host;
        document.getElementById("deleteModal").classList.add("open");
        document.getElementById("deleteModal").setAttribute("aria-hidden", "false");
        document.querySelectorAll(".card-settings-menu.open").forEach(m => m.classList.remove("open"));
        deleteModalTarget = host;
        return;
      }
      const renameBtn = e.target.closest('.card-settings-item[data-action="rename"]');
      if (renameBtn) {
        e.stopPropagation();
        const host = renameBtn.dataset.host;
        const server = window._lastMetricsData?.servers?.find(row => row.host === host);
        const value = window.prompt("Новое название карточки", server?.display_name || host);
        document.querySelectorAll(".card-settings-menu.open").forEach(m => m.classList.remove("open"));
        if (!value || !value.trim()) return;
        fetch(`/api/hosts/${encodeURIComponent(host)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: value.trim() }),
        }).then(async response => {
          const data = await response.json();
          if (!response.ok || !data.ok) throw new Error(data.detail || data.error || "Ошибка переименования");
          if (server) server.display_name = data.display_name;
          renderGrid();
        }).catch(error => window.alert(error.message || "Ошибка переименования"));
        return;
      }
    });

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".card-settings")) {
        document.querySelectorAll(".card-settings-menu.open").forEach(m => m.classList.remove("open"));
      }
    });
    addServerClose.addEventListener("click", closeAddServer);
    addServerModal.addEventListener("click", (e) => {
      if (e.target === addServerModal) closeAddServer();
    });
    addRefreshPeers.addEventListener("click", loadSshHosts);
    addHostname.addEventListener("input", validateAddForm);
    addIp.addEventListener("input", validateAddForm);
    addPort.addEventListener("input", validateAddForm);

    addSubmit.addEventListener("click", async () => {
      const body = {
        hostname: addHostname.value.trim(),
        ip: addIp.value.trim(),
        port: parseInt(addPort.value, 10),
      };
      const selectedSshAlias = addSshList.querySelector(".add-ssh-item.selected")?.dataset.sshAlias;
      if (selectedSshAlias) body.ssh_target = selectedSshAlias;
      addMsg.textContent = "добавление…";
      addMsg.className = "add-msg";
      try {
        const res = await fetch("/api/hosts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.ok) {
          _serverMutationVersion++;
          addMsg.textContent = `сервер «${body.hostname}» добавлен`;
          addMsg.className = "add-msg ok";
          addHostname.value = "";
          addIp.value = "";
          addSubmit.disabled = true;
          
          // Update cached data so renderGrid() includes the new server
          if (!window._lastMetricsData) window._lastMetricsData = { servers: [], updated_at: Date.now() / 1000 };
          if (!window._lastMetricsData.servers) window._lastMetricsData.servers = [];
          // Remove any existing entry for this host (shouldn't happen, but just in case)
          window._lastMetricsData.servers = window._lastMetricsData.servers.filter(s => s.host !== body.hostname);
          const placeholder = hostPlaceholder(body.hostname);
          window._lastMetricsData.servers.push(placeholder);
          locallyDeletedHosts.delete(body.hostname);
          locallyAddedHosts.set(body.hostname, placeholder);
          persistLocalHostMutations();
          
          // Call renderGrid() to show the new server immediately with correct structure
          renderGrid();
          
          setTimeout(() => { closeAddServer(); }, 400);
          // Don't call tick() here - let auto-refresh (5s) pick up fresh data after background cache update
        } else {
          addMsg.textContent = data.error || "ошибка";
          addMsg.className = "add-msg bad";
        }
      } catch (e) {
        addMsg.textContent = "ошибка сети";
        addMsg.className = "add-msg bad";
      }
    });

    // --- Delete confirmation modal ---
    const deleteModal = document.getElementById("deleteModal");
    const deleteModalClose = document.getElementById("deleteModalClose");
    const deleteModalCancel = document.getElementById("deleteModalCancel");
    const deleteModalConfirm = document.getElementById("deleteModalConfirm");
    let deleteModalTarget = "";

    function closeDeleteModal() {
      deleteModal.classList.remove("open");
      deleteModal.setAttribute("aria-hidden", "true");
      deleteModalTarget = "";
      const errEl = document.getElementById("deleteModalError");
      if (errEl) {
        errEl.style.display = "none";
        errEl.textContent = "";
      }
    }

    function showDeleteError(msg) {
      const errEl = document.getElementById("deleteModalError");
      if (errEl) {
        errEl.textContent = msg;
        errEl.style.display = "block";
      }
    }

    deleteModalClose.addEventListener("click", closeDeleteModal);
    deleteModalCancel.addEventListener("click", closeDeleteModal);
    deleteModal.addEventListener("click", (e) => {
      if (e.target === deleteModal) closeDeleteModal();
    });
    deleteModalConfirm.addEventListener("click", () => {
      const host = deleteModalTarget;
      if (!host) return;
      const confirmBtn = document.getElementById("deleteModalConfirm");
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Удаление…";
      fetch(`/api/hosts/${encodeURIComponent(host)}`, { method: "DELETE" })
        .then(r => r.json())
        .then(data => {
          if (data.ok) {
            _serverMutationVersion++;
            locallyAddedHosts.delete(host);
            locallyDeletedHosts.add(host);
            if (lastHostErrors[host]) delete lastHostErrors[host];
            persistLocalHostMutations();
            persistLastHostErrors();
            // Update cached data so renderGrid() doesn't recreate it
            if (window._lastMetricsData && window._lastMetricsData.servers) {
              window._lastMetricsData.servers = window._lastMetricsData.servers.filter(s => s.host !== host);
            }
            // Call renderGrid() to update UI immediately
            renderGrid();
            closeDeleteModal();
            // Don't call tick() here - let auto-refresh (5s) pick up fresh data after background cache update
          } else {
            showDeleteError(data.error || "Ошибка удаления");
          }
        })
        .catch(() => showDeleteError("Ошибка сети"))
        .finally(() => {
          confirmBtn.disabled = false;
          confirmBtn.textContent = "Удалить";
        });
    });
