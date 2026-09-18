/*!
 * viewer.js —— 自写 WebGL 3D 渲染器（无第三方依赖）
 *
 * 功能：
 *   - 透视相机 + 轨道控制（左键旋转 / 右键或 Shift 平移 / 滚轮缩放）
 *   - 单位立方体 / 圆柱 / 球三种图元，按 webui/model_config.py 的图元描述绘制
 *   - 半球环境光 + 主光 + 补光 + 边缘光，**线性空间做光照、输出前编码回 sRGB**
 *   - 反向外壳描边（inverted hull），保证机械臂在深色背景上边界清晰
 *   - 关节轴标记、选中高亮、工具尖端轨迹、地面网格、伪接触阴影
 *   - 关节标签的屏幕投影（供 HTML overlay 定位）
 *
 * 按需渲染：只有相机/姿态/选项变化时才重绘，避免页面挂后台时白烧 GPU。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory(require('./mat4.js'), require('./model.js'));
  } else {
    root.ArmViewer = factory(root.Mat4, root.ArmModel);
  }
})(typeof self !== 'undefined' ? self : this, function (Mat4, ArmModel) {
  'use strict';

  // 背景用**显示空间**的颜色。比之前的 #0b0e12 略亮但仍属暗色，
  // 而所有机件颜色都比它亮 —— 保证轮廓一定看得出来。
  const BG_DISPLAY = [0.059, 0.071, 0.094];      // ~ #0f1218
  const FOG_DISPLAY = [0.086, 0.102, 0.129];     // 略亮于背景，远处"发灰"而不是消失
  const OUTLINE_DISPLAY = [0.42, 0.72, 0.92];    // 描边色（亮青灰）

  // ---- 光照常量：在 JS 侧定义后注入着色器，
  //      这样 tests/test_model.js 能拿同一组数做"可读性/对比度"校验。
  const LIGHT = {
    KEY_DIR: [0.48, -0.78, 0.85],
    FILL_DIR: [-0.72, 0.34, 0.22],
    KEY_COL: [1.00, 0.97, 0.92],
    FILL_COL: [0.55, 0.68, 0.90],
    SKY_COL: [0.38, 0.46, 0.58],
    GND_COL: [0.18, 0.19, 0.23],
    KEY_GAIN: 0.95,
    FILL_GAIN: 0.28,
    RIM_GAIN: 0.60,
  };

  function v3(arr) {
    return 'vec3(' + arr.map(function (v) { return v.toFixed(4); }).join(', ') + ')';
  }

  function srgbToLinear(c) {
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  }

  function srgbEncode(c) {
    c = c < 0 ? 0 : c;
    return c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
  }

  //: 雾密度：只做轻微景深提示（1 m 处约 15% 混合），不把机械臂洗掉
  const FOG_DENSITY = 0.00016;
  //: 描边外壳放大比例（描边粗细）
  const OUTLINE_SCALE = 1.028;

  // ---- "预期姿态"（半透明预览）用色
  const GHOST_OK_DISPLAY = '#2ec5ff';    // 目标在软限位内
  const GHOST_BAD_DISPLAY = '#ff8a2b';   // 目标超出软限位（发下去会被拒）
  const GHOST_ALPHA = 0.36;
  const GHOST_TINT = 0.72;               // 向预览色混合的比例

  // ================================================================ 着色器
  const MESH_VS = `
attribute vec3 aPosition;
attribute vec3 aNormal;
uniform mat4 uViewProj;
uniform mat4 uModel;
uniform mat3 uNormalMat;
varying vec3 vNormal;
varying vec3 vWorld;
void main() {
  vec4 world = uModel * vec4(aPosition, 1.0);
  vWorld = world.xyz;
  vNormal = uNormalMat * aNormal;
  gl_Position = uViewProj * world;
}`;

  const MESH_FS = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
varying vec3 vNormal;
varying vec3 vWorld;

uniform vec3  uCamera;
uniform vec3  uColor;        // 线性空间
uniform vec3  uFogColor;     // 线性空间
uniform vec3  uRimColor;     // 线性空间
uniform float uHighlight;    // 0..1 选中/悬停
uniform float uAlpha;
uniform float uFog;          // 雾密度（0 = 关闭）
uniform float uFlat;         // 1 = 不做光照（描边/阴影用）

const vec3 KEY_DIR  = ${v3(LIGHT.KEY_DIR)};
const vec3 FILL_DIR = ${v3(LIGHT.FILL_DIR)};
const vec3 KEY_COL  = ${v3(LIGHT.KEY_COL)};
const vec3 FILL_COL = ${v3(LIGHT.FILL_COL)};
const vec3 SKY_COL  = ${v3(LIGHT.SKY_COL)};
const vec3 GND_COL  = ${v3(LIGHT.GND_COL)};

vec3 srgbEncode(vec3 c) {
  c = max(c, vec3(0.0));
  return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055,
             step(vec3(0.0031308), c));
}

void main() {
  vec3 V = normalize(uCamera - vWorld);
  float dist = length(uCamera - vWorld);

  vec3 col;
  if (uFlat > 0.5) {
    col = uColor;
  } else {
    vec3 N = normalize(vNormal);
    if (dot(N, V) < 0.0) N = -N;                  // 双面光照（不做背面剔除）

    // 半球环境光：朝上偏"天光"，朝下偏"地面反射"，让圆柱/长方体有体积感
    float hemi = clamp(N.z * 0.5 + 0.5, 0.0, 1.0);
    vec3 ambient = mix(GND_COL, SKY_COL, hemi);

    float key  = max(dot(N, normalize(KEY_DIR)), 0.0);
    float fill = max(dot(N, normalize(FILL_DIR)), 0.0);
    float rim  = pow(1.0 - max(dot(N, V), 0.0), 2.6);

    vec3 base = mix(uColor, vec3(0.20, 0.88, 1.0), uHighlight * 0.55);
    col  = base * (ambient + KEY_COL * key * ${LIGHT.KEY_GAIN.toFixed(4)}
                            + FILL_COL * fill * ${LIGHT.FILL_GAIN.toFixed(4)});
    col += rim * mix(uRimColor, vec3(0.30, 0.98, 1.0), uHighlight) * ${LIGHT.RIM_GAIN.toFixed(4)};
  }

  if (uFog > 0.0) {
    float f = clamp(1.0 - exp(-dist * uFog), 0.0, 1.0);
    col = mix(col, uFogColor, f);
  }

  // ★ 关键：光照在线性空间做，输出前编码回 sRGB，否则整体会暗一大截
  gl_FragColor = vec4(srgbEncode(col), uAlpha);
}`;

  const LINE_VS = `
attribute vec3 aPosition;
attribute vec3 aColor;
uniform mat4 uViewProj;
uniform mat4 uModel;
varying vec3 vColor;
void main() {
  vColor = aColor;
  gl_Position = uViewProj * uModel * vec4(aPosition, 1.0);
}`;

  // 线框直接使用显示空间颜色（与页面 CSS 一致），不做光照
  const LINE_FS = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
varying vec3 vColor;
uniform float uAlpha;
void main() {
  gl_FragColor = vec4(vColor, uAlpha);
}`;

  // ================================================================ 图元
  function makeCube() {
    const p = [], n = [], idx = [];
    const faces = [
      [[0, 0, 1], [[-0.5, -0.5, 0.5], [0.5, -0.5, 0.5], [0.5, 0.5, 0.5], [-0.5, 0.5, 0.5]]],
      [[0, 0, -1], [[0.5, -0.5, -0.5], [-0.5, -0.5, -0.5], [-0.5, 0.5, -0.5], [0.5, 0.5, -0.5]]],
      [[1, 0, 0], [[0.5, -0.5, 0.5], [0.5, -0.5, -0.5], [0.5, 0.5, -0.5], [0.5, 0.5, 0.5]]],
      [[-1, 0, 0], [[-0.5, -0.5, -0.5], [-0.5, -0.5, 0.5], [-0.5, 0.5, 0.5], [-0.5, 0.5, -0.5]]],
      [[0, 1, 0], [[-0.5, 0.5, 0.5], [0.5, 0.5, 0.5], [0.5, 0.5, -0.5], [-0.5, 0.5, -0.5]]],
      [[0, -1, 0], [[-0.5, -0.5, -0.5], [0.5, -0.5, -0.5], [0.5, -0.5, 0.5], [-0.5, -0.5, 0.5]]],
    ];
    faces.forEach(function (f) {
      const base = p.length / 3;
      f[1].forEach(function (v) {
        p.push(v[0], v[1], v[2]);
        n.push(f[0][0], f[0][1], f[0][2]);
      });
      idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
    });
    return { position: new Float32Array(p), normal: new Float32Array(n),
             index: new Uint16Array(idx) };
  }

  function makeCylinder(segments) {
    const p = [], n = [], idx = [];
    const seg = segments || 32;
    const hz = 0.5;
    for (let i = 0; i <= seg; i++) {
      const a = (i / seg) * Mat4.TAU;
      const cx = Math.cos(a), sy = Math.sin(a);
      p.push(cx, sy, -hz); n.push(cx, sy, 0);
      p.push(cx, sy, hz);  n.push(cx, sy, 0);
    }
    for (let i = 0; i < seg; i++) {
      const b = i * 2;
      // 绕序必须是从外面看逆时针（CCW）—— 描边的 inverted hull 靠 cullFace(FRONT) 取背面
      idx.push(b, b + 3, b + 1, b, b + 2, b + 3);
    }
    [[hz, 1], [-hz, -1]].forEach(function (cap) {
      const z = cap[0], nz = cap[1];
      const center = p.length / 3;
      p.push(0, 0, z); n.push(0, 0, nz);
      for (let i = 0; i <= seg; i++) {
        const a = (i / seg) * Mat4.TAU;
        p.push(Math.cos(a), Math.sin(a), z);
        n.push(0, 0, nz);
      }
      for (let i = 0; i < seg; i++) {
        if (nz > 0) idx.push(center, center + 1 + i, center + 2 + i);
        else idx.push(center, center + 2 + i, center + 1 + i);
      }
    });
    return { position: new Float32Array(p), normal: new Float32Array(n),
             index: new Uint16Array(idx) };
  }

  function makeSphere(lat, lon) {
    const p = [], n = [], idx = [];
    for (let i = 0; i <= lat; i++) {
      const theta = (i / lat) * Math.PI;
      const st = Math.sin(theta), ct = Math.cos(theta);
      for (let j = 0; j <= lon; j++) {
        const phi = (j / lon) * Mat4.TAU;
        const x = st * Math.cos(phi), y = st * Math.sin(phi), z = ct;
        p.push(x, y, z); n.push(x, y, z);
      }
    }
    for (let i = 0; i < lat; i++) {
      for (let j = 0; j < lon; j++) {
        const a = i * (lon + 1) + j, b = a + lon + 1;
        idx.push(a, b, a + 1, a + 1, b, b + 1);
      }
    }
    return { position: new Float32Array(p), normal: new Float32Array(n),
             index: new Uint16Array(idx) };
  }

  /** 地面网格：主轴线略亮，其余压暗（比机械臂暗，避免抢视线） */
  function makeGrid(halfSize, step) {
    const p = [], c = [];
    const dim   = [0.155, 0.180, 0.215];
    const major = [0.225, 0.260, 0.310];
    for (let v = -halfSize; v <= halfSize; v += step) {
      const col = (Math.abs(v) < 1e-6) ? major : dim;
      p.push(v, -halfSize, 0, v, halfSize, 0);
      c.push(col[0], col[1], col[2], col[0], col[1], col[2]);
      p.push(-halfSize, v, 0, halfSize, v, 0);
      c.push(col[0], col[1], col[2], col[0], col[1], col[2]);
    }
    return { position: new Float32Array(p), color: new Float32Array(c) };
  }

  /** 世界坐标轴，整体压暗到约 50%，不与机械臂抢色 */
  function makeAxes(length) {
    const k = 0.62;
    const p = [0, 0, 0, length, 0, 0, 0, 0, 0, 0, length, 0, 0, 0, 0, 0, 0, length];
    const c = [0.85 * k, 0.29 * k, 0.34 * k, 0.85 * k, 0.29 * k, 0.34 * k,
               0.36 * k, 0.78 * k, 0.36 * k, 0.36 * k, 0.78 * k, 0.36 * k,
               0.35 * k, 0.58 * k, 0.95 * k, 0.35 * k, 0.58 * k, 0.95 * k];
    return { position: new Float32Array(p), color: new Float32Array(c) };
  }

  // ================================================================ WebGL 辅助
  function compile(gl, type, src) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(sh);
      gl.deleteShader(sh);
      throw new Error('shader compile failed: ' + log);
    }
    return sh;
  }

  function program(gl, vsSrc, fsSrc) {
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, vsSrc));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, fsSrc));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error('program link failed: ' + gl.getProgramInfoLog(prog));
    }
    return prog;
  }

  function uploadMesh(gl, mesh) {
    const vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.position, gl.STATIC_DRAW);
    const nbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, nbo);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.normal, gl.STATIC_DRAW);
    const ibo = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.index, gl.STATIC_DRAW);
    return { vbo: vbo, nbo: nbo, ibo: ibo, count: mesh.index.length };
  }

  function uploadLines(gl, lines) {
    const vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.bufferData(gl.ARRAY_BUFFER, lines.position, gl.STATIC_DRAW);
    const cbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, cbo);
    gl.bufferData(gl.ARRAY_BUFFER, lines.color, gl.STATIC_DRAW);
    return { vbo: vbo, cbo: cbo, count: lines.position.length / 3 };
  }

  /** sRGB 显示色 -> 线性空间（光照用） */
  function parseColor(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
    if (!m) return [0.6, 0.6, 0.6];
    const v = parseInt(m[1], 16);
    return [srgbToLinear(((v >> 16) & 255) / 255),
            srgbToLinear(((v >> 8) & 255) / 255),
            srgbToLinear((v & 255) / 255)];
  }

  // ================================================================ 渲染器
  function create(canvas, opts) {
    opts = opts || {};
    const gl = canvas.getContext('webgl', { antialias: true, alpha: false,
                                            depth: true, preserveDrawingBuffer: false })
            || canvas.getContext('experimental-webgl', { antialias: true, alpha: false });
    if (!gl) throw new Error('WebGL unavailable');

    const meshProg = program(gl, MESH_VS, MESH_FS);
    const lineProg = program(gl, LINE_VS, LINE_FS);

    const loc = {
      mesh: {
        aPosition: gl.getAttribLocation(meshProg, 'aPosition'),
        aNormal: gl.getAttribLocation(meshProg, 'aNormal'),
        uViewProj: gl.getUniformLocation(meshProg, 'uViewProj'),
        uModel: gl.getUniformLocation(meshProg, 'uModel'),
        uNormalMat: gl.getUniformLocation(meshProg, 'uNormalMat'),
        uColor: gl.getUniformLocation(meshProg, 'uColor'),
        uFogColor: gl.getUniformLocation(meshProg, 'uFogColor'),
        uRimColor: gl.getUniformLocation(meshProg, 'uRimColor'),
        uHighlight: gl.getUniformLocation(meshProg, 'uHighlight'),
        uAlpha: gl.getUniformLocation(meshProg, 'uAlpha'),
        uCamera: gl.getUniformLocation(meshProg, 'uCamera'),
        uFog: gl.getUniformLocation(meshProg, 'uFog'),
        uFlat: gl.getUniformLocation(meshProg, 'uFlat'),
      },
      line: {
        aPosition: gl.getAttribLocation(lineProg, 'aPosition'),
        aColor: gl.getAttribLocation(lineProg, 'aColor'),
        uViewProj: gl.getUniformLocation(lineProg, 'uViewProj'),
        uModel: gl.getUniformLocation(lineProg, 'uModel'),
        uAlpha: gl.getUniformLocation(lineProg, 'uAlpha'),
      },
    };

    const geo = {
      cube: uploadMesh(gl, makeCube()),
      cylinder: uploadMesh(gl, makeCylinder(36)),
      sphere: uploadMesh(gl, makeSphere(12, 20)),
      grid: uploadLines(gl, makeGrid(600, 50)),
      axes: uploadLines(gl, makeAxes(180)),
    };
    const trail = { vbo: gl.createBuffer(), cbo: gl.createBuffer(), count: 0 };
    const ghostLine = { vbo: gl.createBuffer(), cbo: gl.createBuffer(), count: 0 };

    const state = {
      model: { chain: [], joints: [], colors: {} },
      pose: { angles: {}, jawRatio: 0, tip: [0, 0, 0], parts: [], joints: {} },
      //: 预期姿态（半透明预览）：{parts, tip, valid, angles} 或 null
      ghost: null,
      selected: null,
      hover: null,
      options: {
        grid: true,
        axes: true,
        markers: true,
        links: true,
        trail: true,
        outline: true,
        fog: true,
        ghost: true,
      },
      camera: {
        target: [0, 0, 150],
        distance: 1050,
        azimuth: -62 * Mat4.DEG,
        elevation: 24 * Mat4.DEG,
        fov: 40,
        near: 5,
        far: 9000,
      },
    };

    const rimColor = parseColor('#8fb6d8');
    const fogColorLinear = parseColor('#191e26');
    const outlineColor = parseColor('#6fb8de');
    const shadowColor = parseColor('#05070a');
    const ghostOk = parseColor(GHOST_OK_DISPLAY);
    const ghostBad = parseColor(GHOST_BAD_DISPLAY);

    let dirty = true;
    let width = 1, height = 1;
    let eye = [0, -1000, 300];
    let viewProj = Mat4.identity();
    const trailPoints = [];
    const MAX_TRAIL = 400;

    // ---------------------------------------------------------------- 相机
    function cameraEye() {
      const c = state.camera;
      const ce = Math.cos(c.elevation);
      return [
        c.target[0] + c.distance * ce * Math.cos(c.azimuth),
        c.target[1] + c.distance * ce * Math.sin(c.azimuth),
        c.target[2] + c.distance * Math.sin(c.elevation),
      ];
    }

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(1, Math.floor(canvas.clientWidth * dpr));
      const h = Math.max(1, Math.floor(canvas.clientHeight * dpr));
      if (w !== width || h !== height) {
        width = w; height = h;
        canvas.width = w; canvas.height = h;
        dirty = true;
      }
    }

    // ---------------------------------------------------------------- 输入
    const pointers = new Map();
    let mode = null;
    let last = null;

    function localPos(ev) {
      const r = canvas.getBoundingClientRect();
      return [ev.clientX - r.left, ev.clientY - r.top];
    }

    canvas.addEventListener('pointerdown', function (ev) {
      canvas.setPointerCapture(ev.pointerId);
      pointers.set(ev.pointerId, localPos(ev));
      mode = (ev.button === 2 || ev.shiftKey || ev.button === 1) ? 'pan' : 'orbit';
      last = localPos(ev);
      canvas.style.cursor = mode === 'pan' ? 'grabbing' : 'move';
    });

    canvas.addEventListener('pointermove', function (ev) {
      const pos = localPos(ev);
      if (!pointers.has(ev.pointerId)) {
        const hit = pickJoint(pos[0], pos[1]);
        if (hit !== state.hover) { state.hover = hit; dirty = true; }
        return;
      }
      pointers.set(ev.pointerId, pos);
      const dx = pos[0] - last[0];
      const dy = pos[1] - last[1];
      last = pos;
      const c = state.camera;
      if (mode === 'pan') {
        const scale = c.distance * 0.0022;
        const ce = Math.cos(c.elevation), se = Math.sin(c.elevation);
        const fwd = [-ce * Math.cos(c.azimuth), -ce * Math.sin(c.azimuth), -se];
        const right = [-Math.sin(c.azimuth), Math.cos(c.azimuth), 0];
        const up = [
          right[1] * fwd[2] - right[2] * fwd[1],
          right[2] * fwd[0] - right[0] * fwd[2],
          right[0] * fwd[1] - right[1] * fwd[0],
        ];
        for (let i = 0; i < 3; i++) {
          c.target[i] += (-dx * right[i] + dy * up[i]) * scale;
        }
      } else {
        c.azimuth -= dx * 0.0075;
        c.elevation = Mat4.clamp(c.elevation + dy * 0.0075,
                                 -85 * Mat4.DEG, 85 * Mat4.DEG);
      }
      dirty = true;
    });

    function endPointer(ev) {
      pointers.delete(ev.pointerId);
      if (pointers.size === 0) { mode = null; canvas.style.cursor = 'grab'; }
    }
    canvas.addEventListener('pointerup', endPointer);
    canvas.addEventListener('pointercancel', endPointer);
    canvas.addEventListener('contextmenu', function (ev) { ev.preventDefault(); });
    canvas.addEventListener('wheel', function (ev) {
      ev.preventDefault();
      const c = state.camera;
      c.distance = Mat4.clamp(c.distance * Math.exp(ev.deltaY * 0.0011), 180, 5200);
      dirty = true;
    }, { passive: false });

    // ---------------------------------------------------------------- 拾取/投影
    function pickJoint(sx, sy) {
      if (!state.options.markers) return null;
      let best = null, bestD = 46;
      Object.keys(state.pose.joints).forEach(function (id) {
        const info = state.pose.joints[id];
        const p = project(info.origin);
        if (!p) return;
        const d = Math.hypot(p[0] - sx, p[1] - sy);
        if (d < bestD) { bestD = d; best = Number(id); }
      });
      return best;
    }

    function project(point) {
      const v = worldToClip(point);
      if (!v) return null;
      const dpr = width / Math.max(1, canvas.clientWidth);
      return [((v[0] * 0.5 + 0.5) * width) / dpr,
              ((0.5 - v[1] * 0.5) * height) / dpr];
    }

    function worldToClip(point) {
      const view = Mat4.lookAt(eye, state.camera.target, [0, 0, 1]);
      const proj = Mat4.perspective(state.camera.fov,
                                    width / Math.max(1, height),
                                    state.camera.near, state.camera.far);
      const vp = Mat4.multiply(proj, view);
      const p = point;
      const x = vp[0] * p[0] + vp[4] * p[1] + vp[8] * p[2] + vp[12];
      const y = vp[1] * p[0] + vp[5] * p[1] + vp[9] * p[2] + vp[13];
      const z = vp[2] * p[0] + vp[6] * p[1] + vp[10] * p[2] + vp[14];
      const w = vp[3] * p[0] + vp[7] * p[1] + vp[11] * p[2] + vp[15];
      if (w <= 0.0001) return null;
      return [x / w, y / w, z / w];
    }

    // ---------------------------------------------------------------- 绘制
    function bindMesh(handle) {
      gl.bindBuffer(gl.ARRAY_BUFFER, handle.vbo);
      gl.enableVertexAttribArray(loc.mesh.aPosition);
      gl.vertexAttribPointer(loc.mesh.aPosition, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, handle.nbo);
      gl.enableVertexAttribArray(loc.mesh.aNormal);
      gl.vertexAttribPointer(loc.mesh.aNormal, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, handle.ibo);
    }

    function drawMesh(handle, matrix, color, highlight, alpha, flat) {
      bindMesh(handle);
      gl.uniformMatrix4fv(loc.mesh.uModel, false, matrix);
      if (!flat) {
        gl.uniformMatrix3fv(loc.mesh.uNormalMat, false, Mat4.normalMatrix(matrix));
      }
      gl.uniform3fv(loc.mesh.uColor, color);
      gl.uniform1f(loc.mesh.uHighlight, highlight || 0);
      gl.uniform1f(loc.mesh.uAlpha, alpha === undefined ? 1 : alpha);
      gl.uniform1f(loc.mesh.uFlat, flat ? 1 : 0);
      gl.drawElements(gl.TRIANGLES, handle.count, gl.UNSIGNED_SHORT, 0);
    }

    function drawLines(handle, matrix, alpha) {
      gl.bindBuffer(gl.ARRAY_BUFFER, handle.vbo);
      gl.enableVertexAttribArray(loc.line.aPosition);
      gl.vertexAttribPointer(loc.line.aPosition, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, handle.cbo);
      gl.enableVertexAttribArray(loc.line.aColor);
      gl.vertexAttribPointer(loc.line.aColor, 3, gl.FLOAT, false, 0, 0);
      gl.uniformMatrix4fv(loc.line.uModel, false, matrix || Mat4.identity());
      gl.uniform1f(loc.line.uAlpha, alpha === undefined ? 1 : alpha);
      gl.drawArrays(gl.LINES, 0, handle.count);
    }

    function updateTrail() {
      if (!state.options.trail) return;
      const tip = state.pose.tip;
      const lastPt = trailPoints[trailPoints.length - 1];
      if (!lastPt || Math.hypot(tip[0] - lastPt[0], tip[1] - lastPt[1],
                                tip[2] - lastPt[2]) > 3) {
        trailPoints.push(tip.slice());
        if (trailPoints.length > MAX_TRAIL) trailPoints.shift();
        const p = [], c = [];
        for (let i = 0; i < trailPoints.length - 1; i++) {
          const t = i / Math.max(1, trailPoints.length - 2);
          const col = [0.16 + 0.20 * t, 0.86 - 0.34 * t, 0.96];
          p.push(trailPoints[i][0], trailPoints[i][1], trailPoints[i][2]);
          p.push(trailPoints[i + 1][0], trailPoints[i + 1][1], trailPoints[i + 1][2]);
          c.push(col[0], col[1], col[2], col[0], col[1], col[2]);
        }
        gl.bindBuffer(gl.ARRAY_BUFFER, trail.vbo);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(p), gl.DYNAMIC_DRAW);
        gl.bindBuffer(gl.ARRAY_BUFFER, trail.cbo);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(c), gl.DYNAMIC_DRAW);
        trail.count = p.length / 3;
      }
    }

    /** 伪接触阴影：三层扁圆盘叠加，越靠外越淡 */
    function drawGroundShadow() {
      const rings = [
        { r: 300, a: 0.34, z: 0.30 },
        { r: 205, a: 0.30, z: 0.40 },
        { r: 120, a: 0.30, z: 0.50 },
      ];
      gl.depthMask(false);
      rings.forEach(function (ring) {
        drawMesh(geo.cylinder,
                 Mat4.multiply(Mat4.translation(0, 0, ring.z),
                               Mat4.scaling(ring.r, ring.r, 0.12)),
                 shadowColor, 0, ring.a, true);
      });
      gl.depthMask(true);
    }

    /** 当前末端 -> 预期末端的虚线引导线 */
    function rebuildGhostLine() {
      const g = state.ghost;
      if (!g) { ghostLine.count = 0; return; }
      const a = state.pose.tip, b = g.tip;
      const dashes = 14;
      const p = [], c = [];
      const col = g.valid ? [0.18, 0.78, 1.0] : [1.0, 0.54, 0.17];
      for (let i = 0; i < dashes; i++) {
        const t0 = i / dashes;
        const t1 = Math.min(1, t0 + 0.55 / dashes);
        for (const t of [t0, t1]) {
          p.push(a[0] + (b[0] - a[0]) * t,
                 a[1] + (b[1] - a[1]) * t,
                 a[2] + (b[2] - a[2]) * t);
          c.push(col[0], col[1], col[2]);
        }
      }
      gl.bindBuffer(gl.ARRAY_BUFFER, ghostLine.vbo);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(p), gl.DYNAMIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, ghostLine.cbo);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(c), gl.DYNAMIC_DRAW);
      ghostLine.count = p.length / 3;
    }

    /** 画预期姿态：半透明本体 + 目标尖端 + 引导虚线 */
    function drawGhost(bodyColor) {
      const g = state.ghost;
      if (!g) return;
      const tint = g.valid ? ghostOk : ghostBad;

      gl.depthMask(true);
      for (let i = 0; i < g.parts.length; i++) {
        const entry = g.parts[i];
        const t = ArmModel.partTransform(entry, state.model);
        const base = bodyColor(t.color);
        const col = [
          base[0] * (1 - GHOST_TINT) + tint[0] * GHOST_TINT,
          base[1] * (1 - GHOST_TINT) + tint[1] * GHOST_TINT,
          base[2] * (1 - GHOST_TINT) + tint[2] * GHOST_TINT,
        ];
        drawMesh(geo[t.prim] || geo.cube, t.matrix, col, 0, GHOST_ALPHA, false);
      }

      // 引导虚线（被本体挡住就看不见，符合空间直觉）
      if (ghostLine.count) {
        gl.useProgram(lineProg);
        gl.uniformMatrix4fv(loc.line.uViewProj, false, viewProj);
        drawLines(ghostLine, Mat4.identity(), 0.95);
        gl.useProgram(meshProg);
        gl.uniformMatrix4fv(loc.mesh.uViewProj, false, viewProj);
        gl.uniform3fv(loc.mesh.uCamera, eye);
        gl.uniform1f(loc.mesh.uFog, state.options.fog ? FOG_DENSITY : 0);
      }

      // 目标尖端：关掉深度测试，任何时候都看得到
      gl.disable(gl.DEPTH_TEST);
      drawMesh(geo.sphere,
               Mat4.multiply(Mat4.translation(g.tip[0], g.tip[1], g.tip[2]),
                             Mat4.scaling(10, 10, 10)),
               tint, 0, g.valid ? 0.95 : 1.0, true);
      gl.enable(gl.DEPTH_TEST);
    }

    function render() {
      resize();
      eye = cameraEye();
      const view = Mat4.lookAt(eye, state.camera.target, [0, 0, 1]);
      const proj = Mat4.perspective(state.camera.fov, width / Math.max(1, height),
                                    state.camera.near, state.camera.far);
      viewProj = Mat4.multiply(proj, view);

      gl.viewport(0, 0, width, height);
      gl.clearColor(BG_DISPLAY[0], BG_DISPLAY[1], BG_DISPLAY[2], 1);
      gl.enable(gl.DEPTH_TEST);
      gl.depthFunc(gl.LEQUAL);
      gl.disable(gl.CULL_FACE);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

      // --- 线框层（网格 / 坐标轴 / 轨迹）---
      gl.useProgram(lineProg);
      gl.uniformMatrix4fv(loc.line.uViewProj, false, viewProj);
      if (state.options.grid) drawLines(geo.grid, Mat4.identity());
      if (state.options.axes) drawLines(geo.axes, Mat4.identity());
      if (state.options.trail && trail.count) drawLines(trail, Mat4.identity(), 0.95);

      // --- 实体层 ---
      gl.useProgram(meshProg);
      gl.uniformMatrix4fv(loc.mesh.uViewProj, false, viewProj);
      gl.uniform3fv(loc.mesh.uCamera, eye);
      gl.uniform3fv(loc.mesh.uFogColor, fogColorLinear);
      gl.uniform3fv(loc.mesh.uRimColor, rimColor);
      gl.uniform1f(loc.mesh.uFog, state.options.fog ? FOG_DENSITY : 0);

      const colorCache = {};
      const bodyColor = function (hex) {
        if (!colorCache[hex]) colorCache[hex] = parseColor(hex);
        return colorCache[hex];
      };

      const entries = state.pose.parts;

      if (state.options.grid || state.options.axes) drawGroundShadow();

      // (1) 描边层：放大外壳 + 只画背面（inverted hull）
      if (state.options.outline) {
        gl.enable(gl.CULL_FACE);
        gl.cullFace(gl.FRONT);
        gl.depthMask(true);
        for (let i = 0; i < entries.length; i++) {
          const t = ArmModel.partTransform(entries[i], state.model);
          const m = Mat4.multiply(
            t.matrix, Mat4.scaling(OUTLINE_SCALE, OUTLINE_SCALE, OUTLINE_SCALE));
          drawMesh(geo[t.prim] || geo.cube, m, outlineColor, 0, 1, true);
        }
        gl.disable(gl.CULL_FACE);
      }

      // (2) 本体层
      for (let i = 0; i < entries.length; i++) {
        const entry = entries[i];
        const t = ArmModel.partTransform(entry, state.model);
        const handle = geo[t.prim] || geo.cube;
        const selected = entry.joint !== null && entry.joint === state.selected;
        const hovered = entry.joint !== null && entry.joint === state.hover;
        const hl = selected ? 1 : (hovered ? 0.5 : 0);
        const alpha = (state.selected !== null && entry.joint !== null &&
                       entry.joint !== state.selected) ? 0.9 : 1.0;
        drawMesh(handle, t.matrix, bodyColor(t.color), hl, alpha, false);
      }

      // (3) 关节标记球
      if (state.options.markers) {
        Object.keys(state.pose.joints).forEach(function (id) {
          const info = state.pose.joints[id];
          const isSel = Number(id) === state.selected;
          const isHover = Number(id) === state.hover;
          const m = Mat4.multiply(
            Mat4.translation(info.origin[0], info.origin[1], info.origin[2]),
            Mat4.scaling(11, 11, 11));
          const col = isSel ? [0.18, 0.95, 1.0]
                    : isHover ? [0.55, 0.92, 1.0] : [0.86, 0.92, 0.99];
          drawMesh(geo.sphere, m, col, isSel ? 1 : 0, isSel ? 1 : 0.7, false);
        });
      }

      // (4) 工具尖端
      const tip = state.pose.tip;
      drawMesh(geo.sphere,
               Mat4.multiply(Mat4.translation(tip[0], tip[1], tip[2]),
                             Mat4.scaling(7, 7, 7)),
               [0.18, 0.95, 0.60], 0, 1, false);

      // (5) 预期姿态（半透明）—— 画在本体之后，深度测试保证前后关系
      if (state.options.ghost && state.ghost) drawGhost(bodyColor);

      dirty = false;
    }

    const api = {
      canvas: canvas,
      gl: gl,
      state: state,

      setModel: function (model) {
        state.model = model;
        state.pose.angles[1] = 0;
        state.pose.angles[2] = 42;
        state.pose.angles[3] = -18;
        state.pose.angles[4] = 46;
        state.pose.angles[5] = 0;
        dirty = true;
      },

      setPose: function (angles, jawRatio) {
        state.pose = ArmModel.forward(state.model, angles,
                                      { jawRatio: jawRatio || 0 });
        state.pose.angles = angles;
        updateTrail();
        rebuildGhostLine();
        dirty = true;
      },

      /**
       * 设置“预期姿态”（半透明预览）。
       *
       * @param {Object|null} angles 预期关节角；null = 不显示预览
       * @param {number} jawRatio    预期的夹爪开合比
       * @param {boolean} valid      false = 超出软限位（发下去会被拒），用橙色警示
       */
      setGhost: function (angles, jawRatio, valid) {
        if (!angles) {
          if (state.ghost) { state.ghost = null; ghostLine.count = 0; dirty = true; }
          return;
        }
        const pose = ArmModel.forward(state.model, angles,
                                      { jawRatio: jawRatio || 0 });
        state.ghost = { parts: pose.parts, tip: pose.tip, joints: pose.joints,
                        angles: angles, valid: valid !== false };
        rebuildGhostLine();
        dirty = true;
      },

      setSelected: function (id) {
        if (state.selected === id) return;
        state.selected = id;
        dirty = true;
      },

      setOption: function (key, value) {
        state.options[key] = value;
        if (key === 'trail' && !value) trailPoints.length = 0;
        dirty = true;
      },

      clearTrail: function () { trailPoints.length = 0; trail.count = 0; dirty = true; },

      /** 预期姿态的末端坐标（没开预览时返回 null） */
      ghostTip: function () {
        return state.ghost ? state.ghost.tip : null;
      },

      frame: function (radius, target) {
        const c = state.camera;
        c.target = target ? target.slice() : [0, 0, radius * 0.42];
        c.distance = Mat4.clamp(radius * 2.4, 300, 5200);
        dirty = true;
      },

      presetView: function (name) {
        const c = state.camera;
        const presets = {
          iso: [-62, 24], front: [-90, 8], side: [0, 8],
          top: [-90, 84], back: [90, 12],
        };
        const p = presets[name] || presets.iso;
        c.azimuth = p[0] * Mat4.DEG;
        c.elevation = p[1] * Mat4.DEG;
        dirty = true;
      },

      project: project,

      /** 某一段坐标系的轴心在世界坐标下的位置 */
      segmentOrigin: function (index) {
        const f = state.pose.frames;
        if (!f || !f[index]) return null;
        return Mat4.transformPoint(f[index], [0, 0, 0]);
      },

      /** 两段轴心的中点（连杆标注用） */
      segmentMidpoint: function (from, to) {
        const a = api.segmentOrigin(from);
        const b = api.segmentOrigin(to);
        if (!a || !b) return null;
        return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2];
      },

      /** 某一段坐标系的 +X 方向（= 该连杆的指向，单位向量） */
      segmentAxis: function (index) {
        const f = state.pose.frames;
        if (!f || !f[index]) return null;
        const d = Mat4.transformDir(f[index], [1, 0, 0]);
        const n = Math.hypot(d[0], d[1], d[2]) || 1;
        return [d[0] / n, d[1] / n, d[2] / n];
      },

      /** 两根连杆的夹角（度，0..180） */
      linkAngle: function (segA, segB) {
        const u = api.segmentAxis(segA);
        const v = api.segmentAxis(segB);
        if (!u || !v) return null;
        const dot = Math.max(-1, Math.min(1, u[0] * v[0] + u[1] * v[1] + u[2] * v[2]));
        return Math.acos(dot) * 180 / Math.PI;
      },
      markDirty: function () { dirty = true; },
      get needsRender() { return dirty; },
      render: render,
      resize: function () { dirty = true; resize(); },
      pickJoint: pickJoint,

      dispose: function () {
        Object.keys(geo).forEach(function (k) {
          const g = geo[k];
          gl.deleteBuffer(g.vbo); gl.deleteBuffer(g.ibo); gl.deleteBuffer(g.cbo);
        });
        gl.deleteProgram(meshProg);
        gl.deleteProgram(lineProg);
      },
    };

    canvas.style.cursor = 'grab';
    return api;
  }

  return {
    create: create,
    parseColor: parseColor,
    srgbToLinear: srgbToLinear,
    srgbEncode: srgbEncode,
    BG_DISPLAY: BG_DISPLAY,
    FOG_DISPLAY: FOG_DISPLAY,
    OUTLINE_DISPLAY: OUTLINE_DISPLAY,
    LIGHT: LIGHT,
    FOG_DENSITY: FOG_DENSITY,
    OUTLINE_SCALE: OUTLINE_SCALE,
    //: 图元生成器（给测试验证绕序用；描边的 inverted hull 依赖 CCW 朝外）
    makeCube: makeCube,
    makeCylinder: makeCylinder,
    makeSphere: makeSphere,
    /** 给测试用：离线算出某个材质在指定光照下的最终屏幕颜色（sRGB 0..1） */
    shade: function (hex, opts) {
      opts = opts || {};
      const hemi = opts.hemi === undefined ? 0.7 : opts.hemi;
      const key = opts.key === undefined ? 0.7 : opts.key;
      const fill = opts.fill === undefined ? 0.3 : opts.fill;
      const lin = parseColor(hex);
      const out = [];
      for (let i = 0; i < 3; i++) {
        const ambient = LIGHT.GND_COL[i] + (LIGHT.SKY_COL[i] - LIGHT.GND_COL[i]) * hemi;
        const lit = lin[i] * (ambient + LIGHT.KEY_COL[i] * key * LIGHT.KEY_GAIN +
                              LIGHT.FILL_COL[i] * fill * LIGHT.FILL_GAIN);
        out.push(srgbEncode(lit));
      }
      return out;
    },
  };
});
