/*!
 * app.js —— 控制台前端逻辑
 *
 *   连接管理 / 关节控制 / 状态表 / 日志 / Debug / 模型标定 / SSE 事件流
 *
 * 依赖：mat4.js, model.js, viewer.js（都在本目录，无第三方库）
 */
(function () {
  'use strict';

  // ================================================================ 状态
  const S = {
    model: null,          // 服务器下发的模型配置（可被"模型"面板修改）
    baseline: null,       // 原始配置，用于"重置"
    status: {},           // 最近一次 /api/refresh 的快照
    angles: {},           // 3D 用的关节角度（离线关节保留上次值）
    connection: { connected: false, busy: false, port: null, dry_run: false,
                  queued: 0, subscribers: 0, last_error: null, current_job: null },
    locked: true,
    selected: null,
    traceEnabled: false,
    logFilter: { info: true, warn: true, error: true, sdk: false, debug: false },
    autoRefresh: true,
    maxLogLines: 1200,
    maxTraceLines: 800,
    //: 用户输入的目标角度（仅伺服关节；未编辑时不存在）
    targets: {},
    //: 已被用户编辑过的关节（编辑后就不再随实际位置自动同步）
    targetTouched: {},
    //: 已保存模型的指纹，用来判断“有未保存的改动”
    savedFingerprint: '',
  };

  const $ = function (id) { return document.getElementById(id); };
  const $$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function fmtTime(ts) {
    const d = ts ? new Date(ts * 1000) : new Date();
    const p = function (n, w) { return String(n).padStart(w || 2, '0'); };
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds()) +
           '.' + p(d.getMilliseconds(), 3);
  }

  function num(v, digits) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    return Number(v).toFixed(digits === undefined ? 2 : digits);
  }

  // ================================================================ Toast
  function toast(message, kind, ms) {
    const box = $('toasts');
    const node = document.createElement('div');
    node.className = 'toast ' + (kind || '');
    node.textContent = message;
    box.appendChild(node);
    setTimeout(function () {
      node.style.transition = 'opacity .25s';
      node.style.opacity = '0';
      setTimeout(function () { node.remove(); }, 260);
    }, ms || 3600);
  }

  // ================================================================ API
  async function api(path, body) {
    const init = { method: body === undefined ? 'GET' : 'POST' };
    if (body !== undefined) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(body);
    }
    let res, data;
    try {
      res = await fetch(path, init);
    } catch (err) {
      return { status: 0, data: { ok: false, error: '网络错误: ' + err.message } };
    }
    try {
      data = await res.json();
    } catch (err) {
      data = { ok: false, error: 'HTTP ' + res.status + '（响应不是 JSON）' };
    }
    if (data && data.ok === undefined) data.ok = res.ok;
    return { status: res.status, data: data };
  }

  async function motion(path, body, label) {
    if (S.locked) {
      toast('运动已锁定：先点右上角解锁', 'warn');
      return null;
    }
    if (!S.connection.connected) {
      toast('尚未连接串口', 'warn');
      return null;
    }
    const payload = Object.assign({ confirm: true }, body || {});
    const out = await api(path, payload);
    if (!out.data.ok) {
      toast(label + ' 失败：' + (out.data.error || ('HTTP ' + out.status)), 'error', 6000);
    } else if (S.connection.dry_run) {
      // 不提醒的话，用户会以为"点了没反应"是坏了
      toast('DRY-RUN：命令已记录但**未下发**，电机不会动。\n'
            + '取消勾选顶部的 dry-run 并重新连接才能真实控制。', 'warn', 8000);
    }
    scheduleRefresh(200);
    return out.data;
  }

  // ================================================================ 3D 视图
  let viewer = null;

  function initViewer() {
    const canvas = $('view-canvas');
    try {
      viewer = ArmViewer.create(canvas);
    } catch (err) {
      $('gl-error').classList.remove('hidden');
      $('gl-error').innerHTML =
        '无法初始化 WebGL：' + esc(err.message) +
        '<br><br>请用支持 WebGL 的浏览器（Chrome / Edge / Firefox）打开本页面。' +
        '<br>控制与日志功能仍然可用。';
      return false;
    }
    canvas.addEventListener('click', function (ev) {
      const r = canvas.getBoundingClientRect();
      const hit = viewer.pickJoint(ev.clientX - r.left, ev.clientY - r.top);
      selectJoint(hit, true);
    });
    return true;
  }

  function rebuildScene() {
    // 首次加载 / 重置时用：带自动取景
    refreshModel();
    if (!viewer || !S.model) return;
    // 自动取景：扫一遍姿态求包围盒
    const samples = [];
    for (let yaw = -60; yaw <= 60; yaw += 30) {
      for (let sh = 0; sh <= 90; sh += 30) {
        samples.push({ 1: yaw, 2: sh, 3: -30, 4: 60, 5: 0 });
      }
    }
    try {
      const b = ArmModel.bounds(S.model, samples);
      const reach = Math.max(
        Math.hypot(b.hi[0], b.hi[1]), Math.hypot(b.lo[0], b.lo[1]), b.hi[2]);
      viewer.frame(Math.max(500, reach));
    } catch (err) {
      viewer.frame(800);
    }
  }

  /** 只把模型送进渲染器并重算姿态（不动相机）—— 改轴/连杆时用 */
  function refreshModel() {
    if (!viewer || !S.model) return;
    viewer.setModel(S.model);
    applyPose();
    buildLabels();
  }

  function applyPose() {
    if (!viewer || !S.model) return;
    const fresh = ArmModel.anglesFromStatus(S.model, S.status);
    Object.keys(fresh).forEach(function (id) {
      if (fresh[id] !== null && fresh[id] !== undefined) S.angles[id] = fresh[id];
      else if (S.angles[id] === undefined) S.angles[id] = 0;
    });
    const jaw = ArmModel.jawRatio(S.status.j6);
    viewer.setPose(S.angles, jaw);
    viewer.setSelected(S.selected);
    updateTipReadout();
    updateLinkReadout();
    syncTargetFields();
    computeGhost();
  }

  /** 直接用连杆方向算夹角 —— 数值说话，不靠眼睛 */
  function updateLinkReadout() {
    const box = $('link-readout');
    if (!box || !viewer) return;
    const links = S.model && S.model.links;
    if (!links || links.length < 3) { box.textContent = '—'; return; }
    const a = viewer.linkAngle(links[0].seg_from, links[1].seg_from);  // L1 vs L2
    const b = viewer.linkAngle(links[1].seg_from, links[2].seg_from);  // L2 vs L3
    box.textContent = num(a, 2) + '° / ' + num(b, 2) + '°';
  }

  // ---- 预期姿态（半透明预览）-------------------------------------------

  /**
   * 未被用户编辑的伺服关节，目标输入框跟随实际位置。
   *
   * 坐标系约定（很容易弄错，写在这里）：
   *   S.angles[id]  = **模型角度**（含 home_deg 偏移，直接喂给 3D）
   *   S.targets[id] = **传感器角度**（= SDK 的 deg，= 输入框里的值）
   * 两者差一个 home_deg（传感器读 0 时模型应该是多少度）。
   */
  function jointHome(j) {
    return typeof j.home_deg === 'number' ? j.home_deg : 0;
  }

  function syncTargetFields() {
    if (!S.model) return;
    S.model.joints.forEach(function (j) {
      if (j.control !== 'servo' || S.targetTouched[j.id]) return;
      const live = S.angles[j.id];
      if (live === null || live === undefined) return;
      const sensor = live - jointHome(j);
      S.targets[j.id] = sensor;
      const card = cardOf(j.id);
      if (!card) return;
      const soft = j.soft_limits_deg || j.range_deg;
      const shown = num(Math.max(soft[0], Math.min(soft[1], sensor)), 1);
      const deg = card.querySelector('.in-deg');
      const sl = card.querySelector('.sl-deg');
      if (deg && document.activeElement !== deg) deg.value = shown;
      if (sl && document.activeElement !== sl) sl.value = shown;
    });
  }

  /** 计算预览姿态并推给渲染器；与实际姿态一致时自动隐藏（避免重叠双影） */
  function computeGhost() {
    if (!viewer) return;
    if (!viewer.state.options.ghost || !S.model) {
      viewer.setGhost(null);
      updateGhostReadout(null, true);
      return;
    }
    const angles = {};
    let changed = false;
    let valid = true;
    let worst = null;

    S.model.joints.forEach(function (j) {
      const live = S.angles[j.id];
      if (j.control !== 'servo') { angles[j.id] = live; return; }
      const raw = S.targets[j.id];
      if (raw === null || raw === undefined || !isFinite(raw)) {
        angles[j.id] = live;
        return;
      }
      const lo = j.range_deg[0], hi = j.range_deg[1];
      const soft = j.soft_limits_deg;
      const clo = soft ? soft[0] : lo;
      const chi = soft ? soft[1] : hi;
      // 超出可接受范围：夹到边界上显示（再远就没意义了），并用橙色告警
      const shown = Math.max(clo, Math.min(chi, raw));
      if (Math.abs(shown - raw) > 0.05) { valid = false; worst = j; }
      if (soft && (raw < soft[0] - 0.05 || raw > soft[1] + 0.05)) { valid = false; worst = j; }
      // 传感器角度 -> 模型角度
      angles[j.id] = jointHome(j) + shown;
      if (live === null || live === undefined ||
          Math.abs(angles[j.id] - live) > 0.4) changed = true;
    });

    if (!changed) {
      viewer.setGhost(null);
      updateGhostReadout(null, true);
      return;
    }
    viewer.setGhost(angles, ArmModel.jawRatio(S.status.j6), valid);
    updateGhostReadout(viewer.ghostTip(), false, valid, worst);
  }

  function updateGhostReadout(tip, hidden, valid, worst) {
    const box = $('ghost-readout');
    const warn = $('ghost-warn');
    if (hidden || !tip) {
      box.textContent = '— 与实际姿态一致';
      warn.classList.add('hidden');
      return;
    }
    box.textContent = num(tip[0], 0) + ', ' + num(tip[1], 0) + ', ' + num(tip[2], 0);
    if (valid) {
      warn.classList.add('hidden');
    } else {
      warn.classList.remove('hidden');
      warn.textContent = '⚠ ' + (worst ? worst.name + ' ' : '') +
        '目标超出软限位，发下去会被拒绝（需要放宽 limit）';
    }
  }

  function updateTipReadout() {
    const tip = viewer && viewer.state.pose.tip;
    if (!tip) return;
    $('tip-readout').textContent =
      num(tip[0], 0) + ', ' + num(tip[1], 0) + ', ' + num(tip[2], 0);
  }

  function updateSelReadout() {
    const box = $('sel-readout');
    if (!S.selected || !S.model) { box.textContent = '—'; return; }
    const spec = S.model.joints.find(function (j) { return j.id === S.selected; });
    const st = S.status['j' + S.selected];
    const ang = S.angles[S.selected];
    box.textContent = (spec ? spec.name + ' ' + spec.label : 'J' + S.selected) +
      '  ' + (ang === null || ang === undefined ? '—' : num(ang, 1) + '°') +
      (st && st.online ? '' : '  (离线)');
  }

  // ---- 关节标签（HTML overlay）
  let labelNodes = {};
  let linkLabelNodes = {};

  function buildLabels() {
    const host = $('labels');
    host.innerHTML = '';
    labelNodes = {};
    linkLabelNodes = {};
    if (!S.model) return;

    // 关节标签 J1..J6
    S.model.chain.forEach(function (seg) {
      if (seg.joint === null || seg.joint === undefined) return;
      const node = document.createElement('div');
      node.className = 'jlabel';
      node.textContent = 'J' + seg.joint;
      node.addEventListener('click', function () { selectJoint(seg.joint, true); });
      node.style.pointerEvents = 'auto';
      node.style.cursor = 'pointer';
      host.appendChild(node);
      labelNodes[seg.joint] = node;
    });

    // 连杆标签 L1/L2/L3
    // 用户约定：L1 = J2→J3 大臂，L2 = J3→J4 细连杆，L3 = J5 所在前臂
    (S.model.links || []).forEach(function (link) {
      const node = document.createElement('div');
      node.className = 'jlabel linklabel';
      node.textContent = link.label;
      node.title = link.desc || '';
      node.style.pointerEvents = 'auto';
      node.style.cursor = 'help';
      host.appendChild(node);
      linkLabelNodes[link.id] = { node: node, link: link };
    });
  }

  function updateLabels() {
    if (!viewer) return;
    const showJoints = viewer.state.options.markers;
    Object.keys(labelNodes).forEach(function (id) {
      const node = labelNodes[id];
      const info = viewer.state.pose.joints[id];
      if (!showJoints || !info) { node.style.display = 'none'; return; }
      const p = viewer.project(info.origin);
      if (!p) { node.style.display = 'none'; return; }
      node.style.display = '';
      node.style.left = p[0] + 'px';
      node.style.top = (p[1] - 24) + 'px';
      node.classList.toggle('sel', Number(id) === S.selected);
    });

    const showLinks = viewer.state.options.links !== false;
    Object.keys(linkLabelNodes).forEach(function (id) {
      const entry = linkLabelNodes[id];
      const node = entry.node;
      if (!showLinks) { node.style.display = 'none'; return; }
      const mid = viewer.segmentMidpoint(entry.link.seg_from, entry.link.seg_to);
      const p = mid ? viewer.project(mid) : null;
      if (!p) { node.style.display = 'none'; return; }
      node.style.display = '';
      node.style.left = p[0] + 'px';
      node.style.top = p[1] + 'px';
    });
  }

  function tick() {
    if (viewer && viewer.needsRender) viewer.render();
    updateLabels();
    requestAnimationFrame(tick);
  }

  // ================================================================ 选择关节
  function selectJoint(id, scrollIntoView) {
    S.selected = id || null;
    if (viewer) viewer.setSelected(S.selected);
    $$('.jcard').forEach(function (card) {
      const isSel = Number(card.dataset.joint) === S.selected;
      card.classList.toggle('selected', isSel);
      if (isSel && scrollIntoView) card.scrollIntoView({ block: 'nearest' });
    });
    $$('#status-table tbody tr').forEach(function (tr) {
      tr.classList.toggle('sel', Number(tr.dataset.joint) === S.selected);
    });
    updateSelReadout();
  }

  // ================================================================ 关节卡片
  function buildJointCards() {
    const host = $('joint-cards');
    host.innerHTML = '';
    if (!S.model) return;

    S.model.joints.forEach(function (j) {
      const card = document.createElement('div');
      card.className = 'jcard';
      card.dataset.joint = j.id;
      card.innerHTML = cardHtml(j);
      card.addEventListener('click', function (ev) {
        if (ev.target.closest('button, input, select')) return;
        selectJoint(j.id);
      });
      host.appendChild(card);
    });
  }

  function axisLabel(id) {
    const seg = S.model.chain.find(function (s) { return s.joint === id; });
    if (!seg || !seg.axis) return '';
    const names = { '1,0,0': 'X', '-1,0,0': '-X', '0,1,0': 'Y', '0,-1,0': '-Y',
                    '0,0,1': 'Z', '0,0,-1': '-Z' };
    return names[seg.axis.join(',')] || seg.axis.join(',');
  }

  function cardHtml(j) {
    const lo = j.range_deg[0], hi = j.range_deg[1];
    let controls = '';

    if (j.control === 'servo') {
      // 注意：滑条/输入框里是**传感器角度**，而 S.angles 是**模型角度**（差一个 home_deg）
      const sl = j.soft_limits_deg;
      const lo = sl ? sl[0] : j.range_deg[0];
      const hi = sl ? sl[1] : j.range_deg[1];
      const home = jointHome(j);
      const modelLive = S.angles[j.id];
      const sensorLive = (modelLive === null || modelLive === undefined)
        ? lo : (modelLive - home);
      const cur = num(Math.max(lo, Math.min(hi, sensorLive)), 1);
      const slHint = sl
        ? '<div class="meta">SDK 软限位 ' + num(sl[0], 1) + ' .. ' + num(sl[1], 1) +
          '°（计数 ' + Math.round(sl[0] / j.deg_per_count) + ' .. ' +
          Math.round(sl[1] / j.deg_per_count) + '）—— 可在「模型」页放宽</div>'
        : '';
      controls =
        '<div class="jrow">' +
          '<span class="lbl">目标</span>' +
          '<input type="number" class="in-deg" step="0.5" min="' + lo + '" max="' + hi +
            '" value="' + cur + '">' +
          '<span class="muted">度</span>' +
          '<input type="range" class="sl-deg" min="' + lo + '" max="' + hi +
            '" step="0.5" value="' + cur + '">' +
        '</div>' +
        '<div class="jrow">' +
          '<button class="btn tiny" data-act="rel" data-v="-10">-10</button>' +
          '<button class="btn tiny" data-act="rel" data-v="-1">-1</button>' +
          '<button class="btn tiny" data-act="rel" data-v="1">+1</button>' +
          '<button class="btn tiny" data-act="rel" data-v="10">+10</button>' +
          '<span class="lbl" style="margin-left:auto">速度</span>' +
          '<input type="number" class="in-speed" value="' + (j.speed_default || 250) +
            '" min="1" max="' + (j.speed_max || 732) + '" style="width:56px">' +
        '</div>' +
        '<div class="jrow">' +
          '<button class="btn tiny primary" data-act="move">移动到目标</button>' +
          '<button class="btn tiny danger" data-act="stop">停止(失力)</button>' +
          '<button class="btn tiny ghost" data-act="enable">使能</button>' +
        '</div>' +
        slHint;
    } else if (j.control === 'pulse') {
      const opts = j.directions.map(function (d) {
        return '<option value="' + d[0] + '">' + esc(d[1]) + '</option>';
      }).join('');
      controls =
        '<div class="jrow">' +
          '<span class="lbl">方向</span>' +
          '<select class="in-dir">' + opts + '</select>' +
          '<span class="lbl">时长</span>' +
          '<input type="number" class="in-sec" step="0.1" min="0.1" max="' + j.pulse_max +
            '" value="' + j.pulse_default + '" style="width:64px">' +
          '<span class="muted">s</span>' +
        '</div>' +
        '<div class="jrow">' +
          '<button class="btn tiny primary" data-act="pulse">执行脉冲</button>' +
          '<button class="btn tiny danger" data-act="stop">停止</button>' +
        '</div>';
    } else if (j.control === 'gripper') {
      controls =
        '<div class="jrow">' +
          '<button class="btn tiny primary" data-act="open">全开</button>' +
          '<button class="btn tiny primary" data-act="close">全闭</button>' +
          '<button class="btn tiny danger" data-act="stop">停止(松开)</button>' +
          '<span class="lbl" style="margin-left:auto">速度</span>' +
          '<input type="number" class="in-speed" value="' + (j.speed_default || 100) +
            '" min="1" max="732" style="width:56px">' +
        '</div>' +
        '<div class="jrow">' +
          '<span class="lbl">定时</span>' +
          '<input type="number" class="in-sec" step="0.25" min="0.1" max="10" value="1.5" style="width:64px">' +
          '<span class="muted">秒</span>' +
          '<button class="btn tiny" data-act="openfor">开</button>' +
          '<button class="btn tiny" data-act="closefor">合</button>' +
        '</div>' +
        '<div class="meta">中间开度只能靠<b>时长</b>控：<code>reg0x01</code> 只有 0/45/90 三态'
        + '（45 = 在途中，不是“半开”）。全行程约 3.5 s @ 速度 100。</div>';
    }

    return '' +
      '<div class="jcard-head">' +
        '<span class="jcard-name">' + esc(j.name) + '</span>' +
        '<span class="jcard-label">' + esc(j.label) + '</span>' +
        '<span class="jcard-axis">' + esc(j.feedback === 'count' ? 'count' : 'zone') +
          (axisLabel(j.id) ? ' · ' + axisLabel(j.id) : '') + '</span>' +
      '</div>' +
      '<div class="jcard-tools">' +
        '<span class="lbl">模型标定</span>' +
        '<button class="btn tiny axis-flip" data-act="flipaxis"' +
          ' title="反转该关节的旋转【方向】：θ -> -θ（动起来方向和实物相反时用）">⇄ 反向</button>' +
        '<button class="btn tiny axis-flip" data-act="spin180"' +
          ' title="把该关节的模型角度整体 +180°：θ -> θ+180（方向对但整体反了 180° 时用）">↻ 转180°</button>' +
      '</div>' +
      '<div class="jcard-val" data-role="val">—</div>' +
      '<div class="jbar"><div class="jbar-fill" data-role="fill"></div></div>' +
      controls +
      (j.hazard ? '<div class="hazard">' + esc(j.hazard) + '</div>' : '') +
      (j.note ? '<div class="meta">' + esc(j.note) + '</div>' : '');
  }

  function cardOf(jointId) {
    return $('joint-cards').querySelector('.jcard[data-joint="' + jointId + '"]');
  }

  function onCardInput(ev) {
    const card = ev.target.closest('.jcard');
    if (!card) return;
    const id = Number(card.dataset.joint);
    if (ev.target.classList.contains('sl-deg')) {
      card.querySelector('.in-deg').value = ev.target.value;
    } else if (ev.target.classList.contains('in-deg')) {
      const sl = card.querySelector('.sl-deg');
      if (sl) sl.value = ev.target.value;
    }
    if (ev.target.classList.contains('in-deg') || ev.target.classList.contains('sl-deg')) {
      S.targets[id] = Number(ev.target.value);
      S.targetTouched[id] = true;
      computeGhost();
    }
    updateValueCells(id);
  }

  async function onCardClick(ev) {
    const btn = ev.target.closest('button[data-act]');
    if (!btn) return;
    const card = btn.closest('.jcard');
    if (!card) return;
    const id = Number(card.dataset.joint);
    const j = S.model.joints.find(function (x) { return x.id === id; });
    const act = btn.dataset.act;
    selectJoint(id);

    const speed = (function () {
      const input = card.querySelector('.in-speed');
      return input ? Number(input.value) || null : null;
    })();

    if (act === 'move') {
      const deg = Number(card.querySelector('.in-deg').value);
      if (!isFinite(deg)) return toast('目标角度无效', 'warn');
      const out = await motion('/api/move',
                               { joint: id, mode: 'abs', value: deg, speed: speed },
                               j.name + ' 移动');
      if (out && out.ok) { delete S.targetTouched[id]; scheduleRefresh(400); }
    } else if (act === 'rel') {
      const delta = Number(btn.dataset.v);
      S.targets[id] = Number(card.querySelector('.in-deg').value);
      S.targetTouched[id] = true;
      const out = await motion('/api/move',
                               { joint: id, mode: 'rel', value: delta, speed: speed },
                               j.name + ' 相对移动');
      if (out && out.ok) { delete S.targetTouched[id]; scheduleRefresh(400); }
    } else if (act === 'stop') {
      if (id === 1) await motion('/api/j1/stop', {}, 'J1 停止');
      else if (id === 2) await motion('/api/j2/stop', {}, 'J2 停止');
      else if (id === 6) await motion('/api/gripper/stop', {}, 'J6 停止');
      else await motion('/api/servo/stop', { joint: id }, j.name + ' 停止');
    } else if (act === 'enable') {
      await motion('/api/servo/enable', { joint: id }, j.name + ' 使能');
    } else if (act === 'pulse') {
      const direction = card.querySelector('.in-dir').value;
      const seconds = Number(card.querySelector('.in-sec').value);
      if (id === 1) await motion('/api/j1/rotate', { direction: direction, seconds: seconds },
                                 'J1 旋转');
      else await motion('/api/j2/pulse', { direction: direction, seconds: seconds },
                        'J2 ' + direction);
    } else if (act === 'flipaxis') {
      // 模型里这个关节的转向与实物相反 -> 把旋转轴取反（θ -> -θ）
      const seg = S.model.chain.find(function (s) { return s.joint === id; });
      if (!seg || !seg.axis) {
        toast(j.name + ' 是固定段，没有旋转轴', 'warn');
      } else {
        seg.axis = seg.axis.map(function (v) { return -v; });
        refreshModel();
        buildJointCards();
        buildModelEditor();
        setLocked(S.locked);
        updateAllCards();
        selectJoint(id);
        markModelDirty();
        toast(j.name + ' 模型转向已反转（轴 ' + seg.axis.join(',') +
              '）；改完记得在「模型」页导出 JSON 保存', 'ok', 5000);
      }
    } else if (act === 'spin180') {
      // 方向对、但整体反了 180° -> 把“模型零位偏移”加 180（θ -> θ+180）
      const seg180 = S.model.chain.find(function (s) { return s.joint === id; });
      if (!seg180 || !seg180.axis) {
        toast(j.name + ' 是固定段，没有旋转轴', 'warn');
      } else {
        const spec = S.model.joints.find(function (x) { return x.id === id; });
        let h = (Number(spec.home_deg) || 0) + 180;
        if (h > 360) h -= 720;
        spec.home_deg = h;
        refreshModel();
        buildJointCards();
        buildModelEditor();
        setLocked(S.locked);
        updateAllCards();
        selectJoint(id);
        markModelDirty();
        toast(j.name + ' 模型零位偏移 = ' + spec.home_deg +
              '°；改完记得在「模型」页导出 JSON 保存', 'ok', 5000);
      }
    } else if (act === 'open' || act === 'close') {
      await motion('/api/gripper', { action: act, speed: speed }, '夹爪 ' + act);
    } else if (act === 'openfor' || act === 'closefor') {
      const sec = Number(card.querySelector('.in-sec').value);
      if (!(sec > 0)) return toast('时长无效', 'warn');
      await motion('/api/gripper',
                   { action: act === 'openfor' ? 'open_for' : 'close_for',
                     seconds: sec, speed: speed },
                   '夹爪定时' + (act === 'openfor' ? '开' : '合') + ' ' + sec + 's');
    }
  }

  /** 刷新某张卡片上的实时数值与进度条 */
  function updateValueCells(id) {
    const card = cardOf(id);
    if (!card || !S.model) return;
    const j = S.model.joints.find(function (x) { return x.id === id; });
    const st = S.status['j' + id];
    const valBox = card.querySelector('[data-role="val"]');
    const fill = card.querySelector('[data-role="fill"]');

    card.classList.toggle('offline', !(st && st.online));

    if (!st || !st.online) {
      valBox.innerHTML = '<span class="dim">离线</span>';
      fill.style.width = '0%';
      return;
    }

    let text = '';
    let ratio = 0;
    const lo = j.range_deg[0], hi = j.range_deg[1];

    if (j.feedback === 'count') {
      text = num(st.deg, 2) + '° <small>· ' + (st.pos === undefined ? '?' : st.pos) + ' count</small>';
      ratio = (st.deg - lo) / (hi - lo || 1);
    } else if (j.control === 'gripper') {
      const code = st.code === undefined ? st.pos : st.code;
      text = (code === 0 ? '闭合' : code === 90 ? '张开' : '运动中') +
             ' <small>· code ' + code + '</small>';
      ratio = code / 90;
    } else {
      const zone = st.zone === undefined ? st.pos : st.zone;
      const ang = S.angles[id];
      text = (ang === null || ang === undefined ? '?' : num(ang, 0) + '°') +
             ' <small>· zone ' + zone + '</small>';
      ratio = (ang - lo) / (hi - lo || 1);
    }
    if (st.err !== undefined && Math.abs(st.err) > 500) {
      text += ' <small style="color:var(--warn)" title="reg0x02 是力矩/电流类反馈；'
            + '运动刚结束时和保位对抗重力时都会偏高。是否真的卡住要看【位置偏差】，'
            + '不是看这个数">负载 ' + st.err + '</small>';
    }
    if (st.fault) text += ' <small style="color:var(--err)">fault ' + st.fault + '</small>';
    valBox.innerHTML = text;
    fill.style.width = Math.max(0, Math.min(1, ratio)) * 100 + '%';
  }

  function updateAllCards() {
    if (!S.model) return;
    S.model.joints.forEach(function (j) { updateValueCells(j.id); });
  }

  // ================================================================ 状态表
  function updateStatusTable() {
    const tbody = $('status-table').querySelector('tbody');
    if (!S.model) return;
    const rows = S.model.joints.map(function (j) {
      const st = S.status['j' + j.id] || {};
      const online = st.online !== false && st.online !== undefined;
      const sel = Number(j.id) === S.selected;
      let pos = '—', ang = '—';
      if (online) {
        if (j.feedback === 'count') {
          pos = st.pos;
          ang = num(st.deg, 2) + '°';
        } else if (j.control === 'gripper') {
          const code = st.code === undefined ? st.pos : st.code;
          pos = code;
          ang = code === 0 ? '闭合' : code === 90 ? '张开' : '运动';
        } else {
          const zone = st.zone === undefined ? st.pos : st.zone;
          pos = 'zone ' + zone;
          ang = num(S.angles[j.id], 0) + '°';
        }
      }
      // reg0x02 是负载/力矩类反馈，不是“错误”也不是静态位置误差 —— 用琥珀色提示
      const errCls = (st.err !== undefined && Math.abs(st.err) > 500) ? 'warnc' : 'dim';
      const errTitle = 'reg0x02 负载/力矩类反馈：静载约 10，顶限位约 400，运动刚结束或保位对抗重力可达 900+';
      return '<tr data-joint="' + j.id + '" class="' + (sel ? 'sel' : '') + '">' +
        '<td class="name">' + esc(j.name) + ' ' + esc(j.label) + '</td>' +
        '<td class="' + (online ? 'ok' : 'bad') + '">' + (online ? '在线' : '离线') + '</td>' +
        '<td>' + esc(pos) + '</td>' +
        '<td>' + esc(ang) + '</td>' +
        '<td class="' + errCls + '" title="' + esc(errTitle) + '">' +
          (st.err === undefined ? '—' : st.err) + '</td>' +
        '<td class="' + (st.enabled || st.run ? 'ok' : 'dim') + '">' +
          (st.enabled === undefined ? (st.run === undefined ? '—' : st.run) : (st.enabled ? '是' : '否')) +
        '</td>' +
        '<td class="' + (st.fault ? 'bad' : 'dim') + '">' + (st.fault === undefined ? '—' : st.fault) + '</td>' +
      '</tr>';
    });
    tbody.innerHTML = rows.join('');
    $('status-updated').textContent = '更新于 ' + fmtTime();
  }

  $('status-table').addEventListener('click', function (ev) {
    const tr = ev.target.closest('tr[data-joint]');
    if (tr) selectJoint(Number(tr.dataset.joint));
  });

  // ================================================================ 日志
  function appendLog(entry) {
    if (!S.logFilter[entry.level]) return;
    const list = $('log-list');
    const stuck = list.scrollTop + list.clientHeight >= list.scrollHeight - 30;
    const node = document.createElement('div');
    node.className = 'log-line ' + entry.level;
    let html = '<span class="ts">' + fmtTime(entry.ts) + '</span>' +
               '<span class="lv">' + esc(entry.level) + '</span>' +
               '<span class="msg">' + esc(entry.message) + '</span>';
    if (entry.data && entry.data.detail) {
      html += '<span class="detail">' + esc(entry.data.detail) + '</span>';
    }
    node.innerHTML = html;
    list.appendChild(node);
    while (list.childElementCount > S.maxLogLines) list.removeChild(list.firstChild);
    if (stuck) list.scrollTop = list.scrollHeight;
  }

  function rerenderLogs() {
    const list = $('log-list');
    list.innerHTML = '';
    logCache.forEach(appendLog);
  }

  const logCache = [];

  function pushLog(entry) {
    logCache.push(entry);
    if (logCache.length > S.maxLogLines) logCache.shift();
    appendLog(entry);
  }

  function clearLogs() {
    logCache.length = 0;
    $('log-list').innerHTML = '';
  }

  function exportLogs() {
    const text = logCache.map(function (e) {
      return fmtTime(e.ts) + '\t' + e.level + '\t' + e.message +
        (e.data && e.data.detail ? '\t' + e.data.detail : '');
    }).join('\n');
    download('arm-log-' + Date.now() + '.txt', text);
  }

  $$('.log-filter').forEach(function (box) {
    box.addEventListener('change', function () {
      S.logFilter[box.value] = box.checked;
      rerenderLogs();
    });
  });

  // ================================================================ Trace
  function appendTrace(entry) {
    if (!S.traceEnabled) return;
    const list = $('trace-list');
    const stuck = list.scrollTop + list.clientHeight >= list.scrollHeight - 30;
    const node = document.createElement('div');
    node.className = 'trace-line ' + entry.data.dir;
    node.innerHTML = '<span class="ts">' + fmtTime(entry.ts) + '</span>' +
      '<span class="dir">' + (entry.data.dir === 'tx' ? 'TX' : 'RX') + '</span>' +
      '<span class="hex">' + esc(entry.data.hex) + '</span>';
    list.appendChild(node);
    while (list.childElementCount > S.maxTraceLines) list.removeChild(list.firstChild);
    if (stuck) list.scrollTop = list.scrollHeight;
  }

  // ================================================================ 事件流
  function handleEvent(ev) {
    switch (ev.type) {
      case 'log':
        pushLog(ev);
        break;
      case 'trace':
        appendTrace(ev);
        break;
      case 'status':
        S.status = ev.data || {};
        updateStatusTable();
        updateAllCards();
        applyPose();
        updateSelReadout();
        break;
      case 'state':
        updateConnectionUI(ev.data);
        break;
      default:
        break;
    }
  }

  function connectEvents() {
    const es = new EventSource('/api/events');
    es.onmessage = function (msg) {
      let ev;
      try { ev = JSON.parse(msg.data); } catch (err) { return; }
      handleEvent(ev);
    };
    es.onerror = function () {
      // EventSource 会自动重连；这里只做提示
      setConnDot('err', '事件流断开，重连中…');
    };
    return es;
  }

  // ================================================================ 连接 UI
  function setConnDot(kind, text) {
    const dot = $('conn-dot');
    dot.className = 'dot ' + kind;
    $('conn-text').textContent = text;
  }

  function updateConnectionUI(conn) {
    if (!conn) return;
    S.connection = conn;
    const c = conn.connected;
    if (c) {
      if (conn.busy) setConnDot('busy', '忙碌：' + (conn.current_job || '…'));
      else setConnDot('on', '已连接 ' + conn.port);
    } else {
      setConnDot('off', '未连接');
    }
    const dry = !!conn.dry_run;
    $('dryrun-badge').classList.toggle('hidden', !dry);
    $('dryrun-banner').classList.toggle('hidden', !dry);
    $('chk-dryrun').checked = dry || (conn.default_dry_run !== undefined
                                      ? !!conn.default_dry_run
                                      : $('chk-dryrun').checked);
    $('btn-connect').disabled = c;
    $('btn-disconnect').disabled = !c;
    $('port-select').disabled = c;
    $('baud-input').disabled = c;
    $('chk-dryrun').disabled = c;

    $('sb-conn').textContent = c
      ? ('串口 ' + conn.port + ' @ ' + (conn.baud || 115200) + (conn.dry_run ? ' [dry-run]' : ''))
      : '未连接';
    $('sb-busy').textContent = conn.busy ? ('执行中: ' + (conn.current_job || '')) : '空闲';
    $('sb-queue').textContent = '队列 ' + (conn.queued || 0);
    $('sb-clients').textContent = '订阅 ' + (conn.subscribers || 0);
    $('sb-error').textContent = conn.last_error || '';
    $('sb-error').className = conn.last_error ? 'err' : '';
  }

  // ================================================================ 锁定
  function setLocked(locked) {
    S.locked = locked;
    const btn = $('lock-toggle');
    btn.classList.toggle('locked', locked);
    btn.classList.toggle('unlocked', !locked);
    $('lock-icon').textContent = locked ? '🔒' : '🔓';
    $('lock-text').textContent = locked ? '已锁定' : '已解锁';
    $$('[data-act]').forEach(function (b) {
      const act = b.dataset.act;
      if (act === 'stop') return;              // 停止/急停永远可用
      b.disabled = locked;
    });
    if (!locked) {
      toast('运动已解锁：点击关节卡片上的按钮会真的让机械臂动作', 'warn', 5000);
    }
  }

  // ================================================================ 连接操作
  async function doConnect() {
    const port = $('port-select').value;
    const baud = Number($('baud-input').value) || 115200;
    const dryRun = $('chk-dryrun').checked;
    setConnDot('busy', '连接中…');
    const out = await api('/api/connect', { port: port, baud: baud, dry_run: dryRun });
    if (!out.data.ok) {
      setConnDot('err', '连接失败');
      toast('连接失败：' + (out.data.error || ''), 'error', 7000);
    } else {
      const n = out.data.result ? out.data.result.online : '?';
      toast('已连接 ' + port + '，在线关节 ' + n + '/6', n ? 'ok' : 'warn');
      if (!n) toast('总线无应答：机械臂可能未上电', 'warn', 7000);
      await refresh();
    }
  }

  async function doDisconnect() {
    setConnDot('busy', '断开中…');
    const out = await api('/api/disconnect', {});
    if (!out.data.ok) toast('断开失败：' + (out.data.error || ''), 'error');
  }

  async function refresh() {
    if (!S.connection.connected) return;
    const out = await api('/api/refresh', { tries: 1 });
    if (out.data.ok && out.data.result) {
      S.status = out.data.result;
      updateStatusTable();
      updateAllCards();
      applyPose();
      updateSelReadout();
    }
  }

  let refreshTimer = null;
  function scheduleRefresh(delay) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, delay === undefined ? 400 : delay);
  }

  async function doVerify() {
    setConnDot('busy', '校验身份…');
    const out = await api('/api/verify', {});
    if (!out.data.ok) {
      toast('校验失败：' + (out.data.error || ''), 'error', 7000);
    } else {
      const results = out.data.result || {};
      const bad = Object.keys(results).filter(function (k) { return !results[k].ok; });
      if (bad.length) toast('身份校验：' + bad.join(', ') + ' 不通过', 'error', 7000);
      else toast('身份校验通过：6/6', 'ok');
      await refresh();
    }
  }

  async function doEstop() {
    const out = await api('/api/estop', { confirm: true });
    if (out.data.ok) {
      const r = out.data.result || {};
      const bad = Object.keys(r).filter(function (k) { return !r[k]; });
      toast('急停已下发' + (bad.length ? '（' + bad.join(',') + ' 未确认）' : ''), 'warn', 5000);
    } else {
      toast('急停失败：' + (out.data.error || ''), 'error', 8000);
    }
    scheduleRefresh(200);
  }

  // ================================================================ Debug
  async function doRawRead() {
    const slave = Number($('dbg-slave').value);
    const start = parseInt($('dbg-start').value, 0);
    const count = parseInt($('dbg-count').value, 0);
    const box = $('dbg-result');
    box.innerHTML = '<span class="muted">读取中…</span>';
    const out = await api('/api/read', { slave: slave, start: start, count: count });
    if (!out.data.ok) {
      box.innerHTML = '<span class="bad">' + esc(out.data.error || '读取失败') + '</span>';
      return;
    }
    const r = out.data.result;
    let html = '<div class="muted" style="margin-bottom:6px">从站 ' + r.slave +
      (r.joint ? ' (' + esc(r.joint) + ')' : '') + '  寄存器 0x' +
      r.start.toString(16).padStart(2, '0') + ' .. 0x' +
      (r.start + r.count - 1).toString(16).padStart(2, '0') + '</div><div class="reg-grid">';
    r.words.forEach(function (w, i) {
      const signed = w >= 0x8000 ? w - 0x10000 : w;
      html += '<div class="reg-cell"><div class="r">0x' +
        (r.start + i).toString(16).padStart(2, '0').toUpperCase() + '</div>' +
        '<div class="v">0x' + w.toString(16).padStart(4, '0').toUpperCase() +
        ' <span class="dim">' + signed + '</span></div></div>';
    });
    box.innerHTML = html + '</div>';
  }

  // ================================================================ 模型面板
  const AXIS_OPTIONS = [
    ['none', '固定段'], ['1,0,0', 'X'], ['-1,0,0', '-X'],
    ['0,1,0', 'Y'], ['0,-1,0', '-Y'], ['0,0,1', 'Z'], ['0,0,-1', '-Z'],
  ];

  function buildModelEditor() {
    const host = $('model-editor');
    if (!S.model) { host.innerHTML = ''; return; }
    let html = '<div class="msec">运动链（pivot = 相对父段的轴心偏移，mm）</div>';

    S.model.chain.forEach(function (seg, i) {
      const axisValue = seg.axis ? seg.axis.join(',') : 'none';
      html += '<div class="mrow" data-seg="' + i + '">' +
        '<span class="lbl">' + (seg.joint === null ? '底座' : 'J' + seg.joint) +
          ' ' + esc(seg.label || '') + '</span>' +
        '<input type="number" class="mp" data-k="0" value="' + seg.pivot[0] + '" step="1">' +
        '<input type="number" class="mp" data-k="1" value="' + seg.pivot[1] + '" step="1">' +
        '<input type="number" class="mp" data-k="2" value="' + seg.pivot[2] + '" step="1">' +
        '<select class="ma">' +
          AXIS_OPTIONS.map(function (o) {
            return '<option value="' + o[0] + '"' +
              (o[0] === axisValue ? ' selected' : '') + '>' + o[1] + '</option>';
          }).join('') +
        '</select>' +
      '</div>';
    });

    html += '<div class="msec">夹爪</div>' +
      '<div class="mrow"><span class="lbl">全开间距</span>' +
        '<input type="number" class="mj" data-k="open_mm" value="' + S.model.jaw.open_mm + '">' +
        '<span class="muted">mm</span></div>' +
      '<div class="mrow"><span class="lbl">全闭间距</span>' +
        '<input type="number" class="mj" data-k="closed_mm" value="' + S.model.jaw.closed_mm + '">' +
        '<span class="muted">mm</span></div>';

    html += '<div class="msec">伺服零位与行程</div>';
    S.model.joints.filter(function (j) { return j.control === 'servo'; })
      .forEach(function (j) {
        html += '<div class="mrow" data-home="' + j.id + '">' +
          '<span class="lbl">' + esc(j.name) + ' 模型零位偏移</span>' +
          '<input type="number" class="mh" step="1" value="' + jointHome(j) + '">' +
          '<span class="muted">度（传感器读 0 时模型应该是多少度）</span>' +
        '</div>';
      });

    // 伺服软限位：这是 SDK **实际会拒绝**的边界，所以它就是滑条范围。
    // 注意：限位是相对“上电零点”的计数，换个上电姿态必须重新量。
    html += '<div class="msec">伺服软限位（SDK 强制，单位 count）</div>';
    S.model.joints.filter(function (j) { return j.control === 'servo'; })
      .forEach(function (j) {
        const soft = j.soft_limits_deg || [0, 0];
        const dpc = j.deg_per_count || 1;
        html += '<div class="mrow" data-limits="' + j.id + '">' +
          '<span class="lbl">' + esc(j.name) + '</span>' +
          '<input type="number" class="slo" value="' + Math.round(soft[0] / dpc) + '">' +
          '<input type="number" class="shi" value="' + Math.round(soft[1] / dpc) + '">' +
          '<span class="muted">' + num(soft[0], 1) + ' .. ' + num(soft[1], 1) + '°</span>' +
          '<button class="btn tiny" data-act="setlimits" data-joint="' + j.id +
            '">应用</button>' +
        '</div>';
      });

    host.innerHTML = html;
  }

  $('model-editor').addEventListener('input', function (ev) {
    const row = ev.target.closest('.mrow');
    if (!row || !S.model) return;
    if (row.dataset.seg !== undefined) {
      const seg = S.model.chain[Number(row.dataset.seg)];
      if (ev.target.classList.contains('mp')) {
        seg.pivot[Number(ev.target.dataset.k)] = Number(ev.target.value) || 0;
      } else if (ev.target.classList.contains('ma')) {
        seg.axis = ev.target.value === 'none' ? null : ev.target.value.split(',').map(Number);
      }
      // 轴/连杆改了：重建标签与关节面板（轴名会变）
      refreshModel();
      buildJointCards();
      setLocked(S.locked);
      updateAllCards();
      markModelDirty();
    } else if (ev.target.classList.contains('mj')) {
      S.model.jaw[ev.target.dataset.k] = Number(ev.target.value) || 1;
      applyPose();
    } else if (ev.target.classList.contains('mh')) {
      const homeRow = ev.target.closest('[data-home]');
      const spec = S.model.joints.find(function (x) {
        return x.id === Number(homeRow.dataset.home);
      });
      spec.home_deg = Number(ev.target.value) || 0;
      applyPose();
    }
  });

  function download(name, text) {
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(function () { a.remove(); URL.revokeObjectURL(url); }, 500);
  }

  // ---- 模型的保存 / 重置 / 脏标记 -----------------------------------------
  /** 模型指纹，用来判断“有未保存的改动” */
  function modelFingerprint() {
    if (!S.model) return '';
    return JSON.stringify({
      chain: (S.model.chain || []).map(function (s) { return [s.joint, s.pivot, s.axis]; }),
      home: (S.model.joints || []).map(function (j) { return [j.id, j.home_deg, j.soft_limits_deg]; }),
      jaw: S.model.jaw,
    });
  }

  function markModelDirty() {
    const dirty = modelFingerprint() !== S.savedFingerprint;
    const btn = $('btn-model-save');
    btn.textContent = dirty ? '保存为默认 *' : '保存为默认';
    btn.classList.toggle('primary', dirty);
    btn.title = dirty
      ? '有未保存的改动，点击写入默认模型参数'
      : '当前参数已保存';
    const note = $('model-path-note');
    const saved = (S.model && S.model.saved) || {};
    const lines = [];
    if (dirty) lines.push('<b>有未保存的改动</b>');
    lines.push('保存位置：<code>' + esc(saved.path || 'webui/model_overrides.json') + '</code>');
    if (saved.exists) lines.push('覆盖文件存在（保存于 ' + esc(saved.saved_at || '?') + '）');
    else lines.push('当前用的是 <code>model_config.py</code> 的出厂值');
    note.innerHTML = lines.join('　·　');
  }

  async function saveModel() {
    if (!S.model) return;
    const out = await api('/api/model/save', { model: S.model, confirm: true });
    if (!out.data.ok) {
      return toast('保存失败：' + (out.data.error || ('HTTP ' + out.status)), 'error', 8000);
    }
    S.model = out.data.model;
    S.baseline = JSON.parse(JSON.stringify(out.data.model));
    S.savedFingerprint = modelFingerprint();
    rebuildScene(); buildJointCards(); buildModelEditor();
    setLocked(S.locked); updateAllCards(); markModelDirty();
    toast('已保存为默认：' + ((out.data.model.saved || {}).path || ''), 'ok', 6000);
  }

  async function resetModel() {
    const out = await api('/api/model/reset', { confirm: true });
    if (!out.data.ok) {
      return toast('重置失败：' + (out.data.error || ('HTTP ' + out.status)), 'error', 7000);
    }
    S.model = out.data.model;
    S.baseline = JSON.parse(JSON.stringify(out.data.model));
    S.savedFingerprint = modelFingerprint();
    rebuildScene(); buildJointCards(); buildModelEditor();
    setLocked(S.locked); updateAllCards(); markModelDirty();
    toast('已重置为出厂模型参数' + (out.data.removed ? '（覆盖文件已删除）' : ''), 'ok', 5000);
  }

  // ================================================================ 启动
  async function boot() {
    // 事件委托只需绑一次（卡片会被反复重建）
    $('joint-cards').addEventListener('click', onCardClick);
    $('joint-cards').addEventListener('input', onCardInput);

    // 3D
    const ok = initViewer();

    // 模型
    const modelOut = await api('/api/model');
    if (modelOut.data.ok) {
      S.model = modelOut.data.model;
      S.baseline = JSON.parse(JSON.stringify(S.model));
    } else {
      toast('无法加载模型配置：' + (modelOut.data.error || ''), 'error', 8000);
      return;
    }
    if (ok) rebuildScene();
    buildJointCards();
    buildModelEditor();
    setLocked(true);
    S.savedFingerprint = modelFingerprint();
    markModelDirty();

    // 帮助文字
    $('help-notes').innerHTML = '<b>控制须知</b><br>' +
      (S.model.notes || []).map(function (n) { return '· ' + esc(n); }).join('<br>');

    // 串口列表
    const portsOut = await api('/api/ports');
    const select = $('port-select');
    select.innerHTML = '';
    const ports = (portsOut.data.ports || []);
    const preferred = S.model.defaults.serial_port;
    ports.forEach(function (p) {
      const opt = document.createElement('option');
      opt.value = p.device;
      opt.textContent = p.device + (p.description ? ' — ' + p.description : '');
      select.appendChild(opt);
    });
    if (!ports.some(function (p) { return p.device === preferred; })) {
      const opt = document.createElement('option');
      opt.value = preferred;
      opt.textContent = preferred + '（未检测到，可能未插入）';
      select.appendChild(opt);
    }
    select.value = preferred;
    $('baud-input').value = S.model.defaults.baud || 115200;

    // 初次状态
    const stateOut = await api('/api/state');
    if (stateOut.data.ok) {
      updateConnectionUI(stateOut.data.state);
      // 服务端用 --dry-run 启动时，默认把勾选框打上，避免误以为"只是看看"
      if (stateOut.data.state && stateOut.data.state.default_dry_run) {
        $('chk-dryrun').checked = true;
        toast('服务端以 --dry-run 启动：写命令不会真正下发', 'warn', 6000);
      }
    }
    const logsOut = await api('/api/logs?limit=300');
    if (logsOut.data.ok) (logsOut.data.logs || []).forEach(pushLog);

    // 事件流
    connectEvents();

    // 轮询
    setInterval(function () {
      if (S.autoRefresh && S.connection.connected && !S.connection.busy) refresh();
    }, 5000);

    if (ok) requestAnimationFrame(tick);
    else updateLabels();
  }

  // ================================================================ 事件绑定
  $('btn-connect').addEventListener('click', doConnect);
  $('btn-disconnect').addEventListener('click', doDisconnect);
  $('btn-leave-dryrun').addEventListener('click', async function () {
    $('chk-dryrun').checked = false;
    toast('正在以真实模式（非 dry-run）重连 ...', 'warn', 4000);
    await doDisconnect();
    await doConnect();
  });
  $('btn-estop').addEventListener('click', doEstop);
  $('btn-verify').addEventListener('click', doVerify);
  $('btn-refresh').addEventListener('click', refresh);
  $('lock-toggle').addEventListener('click', function () { setLocked(!S.locked); });

  $('chk-auto-refresh').addEventListener('change', function (ev) {
    S.autoRefresh = ev.target.checked;
  });
  $('chk-trace').addEventListener('change', function (ev) {
    S.traceEnabled = ev.target.checked;
    if (!S.traceEnabled) $('trace-list').innerHTML = '';
  });
  $('btn-clear-log').addEventListener('click', clearLogs);
  $('btn-export-log').addEventListener('click', exportLogs);
  $('btn-clear-trace').addEventListener('click', function () {
    $('trace-list').innerHTML = '';
  });
  $('btn-dbg-read').addEventListener('click', doRawRead);

  $('chk-grid').addEventListener('change', function (ev) {
    if (viewer) viewer.setOption('grid', ev.target.checked);
  });
  $('chk-axes').addEventListener('change', function (ev) {
    if (viewer) viewer.setOption('axes', ev.target.checked);
  });
  $('chk-markers').addEventListener('change', function (ev) {
    if (viewer) viewer.setOption('markers', ev.target.checked);
    updateLabels();
  });
  $('chk-trail').addEventListener('change', function (ev) {
    if (viewer) viewer.setOption('trail', ev.target.checked);
  });
  $('chk-ghost').addEventListener('change', function (ev) {
    if (viewer) viewer.setOption('ghost', ev.target.checked);
    computeGhost();
  });
  $('chk-outline').addEventListener('change', function (ev) {
    if (viewer) viewer.setOption('outline', ev.target.checked);
  });
  $('chk-fog').addEventListener('change', function (ev) {
    if (viewer) viewer.setOption('fog', ev.target.checked);
  });
  $('chk-links').addEventListener('change', function (ev) {
    if (viewer) viewer.setOption('links', ev.target.checked);
    updateLabels();
  });
  $('btn-clear-trail').addEventListener('click', function () {
    if (viewer) viewer.clearTrail();
  });
  $$('#view-presets button').forEach(function (b) {
    b.addEventListener('click', function () {
      if (viewer) viewer.presetView(b.dataset.view);
    });
  });

  $('btn-model-reset').addEventListener('click', function () {
    resetModel();
  });
  $('btn-model-export').addEventListener('click', function () {
    download('arm-model.json', JSON.stringify(S.model, null, 2));
  });
  $('btn-model-save').addEventListener('click', function () { saveModel(); });
  $('btn-model-import').addEventListener('click', function () {
    $('model-file').click();
  });
  $('model-file').addEventListener('change', function (ev) {
    const file = ev.target.files && ev.target.files[0];
    ev.target.value = '';                 // 允许重复选同一个文件
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function () {
      let data;
      try {
        data = JSON.parse(String(reader.result));
      } catch (err) {
        return toast('JSON 解析失败：' + err.message, 'error', 7000);
      }
      const model = data && data.model ? data.model : data;   // 兼容 /api/model 的响应格式
      if (!model || !Array.isArray(model.chain) || !Array.isArray(model.joints)) {
        return toast('这个 JSON 不是模型文件（缺少 chain / joints）', 'error', 7000);
      }
      S.model = model;
      markModelDirty();
      rebuildScene();
      buildJointCards();
      buildModelEditor();
      setLocked(S.locked);
      updateAllCards();
      toast('已导入模型（' + file.name + '）。确认无误后点「保存为默认」才会持久化',
            'ok', 6000);
    };
    reader.readAsText(file);
  });

  $('model-editor').addEventListener('click', async function (ev) {
    const btn = ev.target.closest('button[data-act="setlimits"]');
    if (!btn) return;
    const row = btn.closest('.mrow');
    const id = Number(btn.dataset.joint);
    const lo = Number(row.querySelector('.slo').value);
    const hi = Number(row.querySelector('.shi').value);
    if (!isFinite(lo) || !isFinite(hi) || lo >= hi) {
      return toast('软限位必须是 lo < hi', 'warn');
    }
    const out = await motion('/api/servo/limits', { joint: id, lo: lo, hi: hi },
                             '设置软限位');
    if (out && out.ok) {
      const spec = S.model.joints.find(function (x) { return x.id === id; });
      spec.soft_limits_deg = out.result.limits_deg;
      buildJointCards();
      buildModelEditor();
      setLocked(S.locked);
      updateAllCards();
      computeGhost();
      markModelDirty();
      toast(spec.name + ' 软限位已改为 ' + out.result.limits_deg[0] + ' .. ' +
            out.result.limits_deg[1] + '°', 'ok', 5000);
    }
  });

  $$('.tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      $$('.tab').forEach(function (t) { t.classList.remove('active'); });
      $$('.tabpane').forEach(function (p) { p.classList.remove('active'); });
      tab.classList.add('active');
      $('tab-' + tab.dataset.tab).classList.add('active');
      if (viewer) viewer.resize();
    });
  });

  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') setLocked(true);
  });

  window.addEventListener('resize', function () { if (viewer) viewer.resize(); });

  window.ARM_UI = { state: S, get viewer() { return viewer; } };

  boot().catch(function (err) {
    toast('初始化失败：' + err.message, 'error', 9000);
    console.error(err);
  });
})();
