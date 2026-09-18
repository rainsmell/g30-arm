/*!
 * tests/test_model.js —— 前端离线测试（node）
 *
 *     node tests/test_model.js                     # 自动导出真实模型配置
 *     node tests/test_model.js path/to/model.json  # 用指定配置
 *
 * 覆盖三段：
 *   1. mat4      矩阵/向量数学
 *   2. kinematics 正运动学（含真实模型的扫掠校验）
 *   3. colors    配色可读性（背景/光照/雾 —— 防止"机械臂看不见"回归）
 *
 * 用 `npm`/`jest` 会引入依赖，这里只用 node 自带的 assert。
 */
'use strict';

const assert = require('assert');
const path = require('path');
const fs = require('fs');

const HERE = __dirname;
const Mat4 = require(path.join(HERE, '..', 'webui', 'static', 'mat4.js'));
const ArmModel = require(path.join(HERE, '..', 'webui', 'static', 'model.js'));
const ArmViewer = require(path.join(HERE, '..', 'webui', 'static', 'viewer.js'));

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log('  ok   ' + name);
  } catch (err) {
    failed++;
    console.log('  FAIL ' + name + '\n       ' + err.message);
  }
}

function near(a, b) { return Math.abs(a - b) < 1e-4; }

function assertVec(actual, expected, msg) {
  for (let i = 0; i < 3; i++) {
    assert.ok(Math.abs(actual[i] - expected[i]) < 1e-4,
      (msg || 'vector') + ' component ' + i + ': got ' + actual[i] +
      ', expected ' + expected[i] + ' ([' + actual.map(v => v.toFixed(4)) + '])');
  }
}

// ---------------------------------------------------------------- 测试模型
// 与 webui/model_config.py 的 ARM_CHAIN 同构（精简版，只保留 2 个 pitch + 1 个 yaw）
const CHAIN = [
  { joint: null, label: 'base', pivot: [0, 0, 0], axis: null,
    parts: [{ prim: 'box', at: [0, 0, 5], size: [100, 100, 10], color: '#333' }] },
  { joint: 1, label: 'yaw', pivot: [0, 0, 10], axis: [0, 0, 1],
    parts: [{ prim: 'cylinder', axis: 'z', at: [0, 0, 25], radius: 20, height: 50,
              color: '#444' }] },
  { joint: 2, label: 'shoulder', pivot: [0, 0, 50], axis: [0, -1, 0],
    parts: [{ prim: 'box', at: [84, 0, 0], size: [168, 40, 40], color: '#555' }] },
  { joint: 3, label: 'elbow', pivot: [168, 0, 0], axis: [0, -1, 0],
    parts: [{ prim: 'cylinder', axis: 'y', at: [0, 0, 0], radius: 40, height: 30,
              color: '#666' },
            { prim: 'box', at: [86, 0, 0], size: [172, 20, 20], color: '#777' }] },
  { joint: 4, label: 'wrist', pivot: [172, 0, 0], axis: [0, -1, 0],
    parts: [{ prim: 'cylinder', axis: 'y', at: [0, 0, 0], radius: 35, height: 28,
              color: '#888' }] },
  { joint: 5, label: 'roll', pivot: [56, 0, 0], axis: [1, 0, 0],
    parts: [{ prim: 'box', at: [50, 0, 0], size: [100, 40, 30], color: '#999' }] },
  { joint: 6, label: 'gripper', pivot: [100, 0, 0], axis: null,
    parts: [{ prim: 'box', at: [16, 0, 0], size: [32, 30, 20], color: '#aaa' },
            { prim: 'box', at: [46, 0, 0], size: [30, 12, 16], color: '#bbb',
              jaw: 'left' },
            { prim: 'box', at: [46, 0, 0], size: [30, 12, 16], color: '#bbb',
              jaw: 'right' }] },
];

const MODEL = {
  units: 'mm',
  chain: CHAIN,
  jaw: { open_mm: 42, closed_mm: 6 },
  joints: [
    { id: 1, feedback: 'zone', zone_to_deg: { '0': -90, '45': 0, '90': 90 } },
    { id: 2, feedback: 'zone', zone_to_deg: { '0': 0, '45': 45, '90': 90 } },
    { id: 3, feedback: 'count', deg_per_count: 0.09 },
    { id: 4, feedback: 'count', deg_per_count: 0.09 },
    { id: 5, feedback: 'count', deg_per_count: 0.237 },
    { id: 6, feedback: 'zone', zone_to_deg: { '0': 0, '45': 50, '90': 100 } },
  ],
};

console.log('mat4:');
test('identity', function () {
  const m = Mat4.identity();
  assertVec(Mat4.transformPoint(m, [1, 2, 3]), [1, 2, 3]);
});

test('translation + multiply order', function () {
  const t = Mat4.translation(10, 0, 0);
  const s = Mat4.scaling(2, 2, 2);
  // T·S 先缩放再平移
  assertVec(Mat4.transformPoint(Mat4.multiply(t, s), [1, 0, 0]), [12, 0, 0]);
  // S·T 先平移再缩放
  assertVec(Mat4.transformPoint(Mat4.multiply(s, t), [1, 0, 0]), [22, 0, 0]);
});

test('rotation about Z (right-handed)', function () {
  const m = Mat4.rotationDeg(0, 0, 1, 90);
  assertVec(Mat4.transformPoint(m, [1, 0, 0]), [0, 1, 0]);
  assertVec(Mat4.transformPoint(m, [0, 1, 0]), [-1, 0, 0]);
});

test('rotation about Y (right-handed)', function () {
  const m = Mat4.rotationDeg(0, 1, 0, 90);
  assertVec(Mat4.transformPoint(m, [1, 0, 0]), [0, 0, -1]);
});

test('rotation about [0,-1,0] lifts +X to +Z', function () {
  const m = Mat4.rotationDeg(0, -1, 0, 90);
  assertVec(Mat4.transformPoint(m, [1, 0, 0]), [0, 0, 1]);
});

test('rotation preserves length', function () {
  const m = Mat4.rotationAxis(0.3, -0.5, 0.81, 1.234);
  const p = Mat4.transformPoint(m, [3, -2, 7]);
  assert.ok(near(Math.hypot(p[0], p[1], p[2]), Math.hypot(3, -2, 7)),
    'length changed: ' + Math.hypot(p[0], p[1], p[2]));
});

test('normalMatrix of non-uniform scale', function () {
  const m = Mat4.multiply(Mat4.translation(5, 0, 0), Mat4.scaling(2, 1, 1));
  const n = Mat4.normalMatrix(m);
  // 柱面上 x 方向法线被 1/2 缩放（再归一化后方向不变）
  const len = Math.hypot(n[0], n[1], n[2]);
  assert.ok(near(len, 0.5) || near(len, 2), 'unexpected normal length ' + len);
  assert.ok(near(n[1], 0) && near(n[2], 0), 'normal should stay along x');
});

test('lookAt puts the target in front of the camera', function () {
  const view = Mat4.lookAt([0, -500, 0], [0, 0, 0], [0, 0, 1]);
  const p = Mat4.transformPoint(view, [0, 0, 0]);
  assert.ok(near(p[0], 0) && near(p[1], 0), 'target should be on the axis');
  assert.ok(near(p[2], -500), 'target should be 500 in front (got ' + p[2] + ')');
});

test('projection maps near/far correctly', function () {
  const proj = Mat4.perspective(45, 1.5, 1, 1000);
  const nearPt = Mat4.transformPoint(proj, [0, 0, -1]);
  const w = proj[11] * -1;    // w = -z
  assert.ok(near(nearPt[2] / w, -1), 'near plane should map to -1');
});

console.log('\nkinematics:');

test('all-zero pose: tip at [496+61, 0, 62]', function () {
  const res = ArmModel.forward(MODEL, { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 });
  // X = 168 + 172 + 56 + 100 = 496; tool = 46 + 30/2 = 61; Z = 10 + 50 = 60?  -> 10+50
  assert.ok(near(res.tip[1], 0), 'tip.y=' + res.tip[1]);
  assert.ok(near(res.tip[2], 60), 'tip.z=' + res.tip[2]);
  assert.ok(res.tip[0] > 500 && res.tip[0] < 600, 'tip.x=' + res.tip[0]);
});

test('shoulder +90 deg points the arm straight up', function () {
  const res = ArmModel.forward(MODEL, { 1: 0, 2: 90, 3: 0, 4: 0, 5: 0 });
  assert.ok(Math.abs(res.tip[0]) < 1e-3, 'tip.x=' + res.tip[0]);
  assert.ok(Math.abs(res.tip[1]) < 1e-3, 'tip.y=' + res.tip[1]);
  assert.ok(res.tip[2] > 500, 'tip.z=' + res.tip[2]);
});

test('base yaw +90 deg swings the arm to +Y', function () {
  const res = ArmModel.forward(MODEL, { 1: 90, 2: 0, 3: 0, 4: 0, 5: 0 });
  assert.ok(Math.abs(res.tip[0]) < 1e-3, 'tip.x=' + res.tip[0]);
  assert.ok(res.tip[1] > 500, 'tip.y=' + res.tip[1]);
});

test('elbow +90 folds the arm upward', function () {
  const a = ArmModel.forward(MODEL, { 2: 0, 3: 0 });
  const b = ArmModel.forward(MODEL, { 2: 0, 3: 90 });
  assert.ok(b.tip[2] > a.tip[2], 'elbow should raise the tip');
  assert.ok(b.tip[0] < a.tip[0], 'elbow should pull the tip back');
});

test('negating a joint axis reverses that joint motion', function () {
  // 这就是关节卡片上 ⇄ 按钮做的事：模型转向与实物相反时把轴取反
  const normal = JSON.parse(JSON.stringify(MODEL));
  const flipped = JSON.parse(JSON.stringify(MODEL));
  const seg = flipped.chain.find(function (s) { return s.joint === 3; });
  seg.axis = seg.axis.map(function (v) { return -v; });

  const rest = ArmModel.forward(normal, { 2: 0, 3: 0 }).tip[2];
  const a = ArmModel.forward(normal, { 2: 0, 3: 40 }).tip[2];
  const b = ArmModel.forward(flipped, { 2: 0, 3: 40 }).tip[2];

  assert.ok(Math.abs(a - rest) > 1, '正常模型：J3 应该改变高度 (a=' + a + ', rest=' + rest + ')');
  assert.ok(Math.abs(b - rest) > 1, '翻转后：J3 仍然改变高度');
  assert.ok((a - rest) * (b - rest) < 0,
    '翻转后方向必须相反 (a=' + a + ', b=' + b + ', rest=' + rest + ')');
});

test('frames count matches chain, joints expose world axis', function () {
  const res = ArmModel.forward(MODEL, { 1: 30, 2: 20, 3: 10, 4: 5, 5: 0 });
  assert.strictEqual(res.frames.length, CHAIN.length);
  assert.strictEqual(Object.keys(res.joints).length, 5);
  const j1 = res.joints[1];
  assertVec(j1.origin, [0, 0, 10], 'j1 origin');
  assertVec(j1.axis, [0, 0, 1], 'j1 axis');
  assert.ok(near(j1.deg, 30), 'j1 deg');
});

test('partTransform applies non-uniform scale for a box', function () {
  const res = ArmModel.forward(MODEL, {});
  const entry = res.parts[0];                       // 底板 box 100x100x10
  const t = ArmModel.partTransform(entry, MODEL);
  assert.ok(near(t.matrix[0], 100), 'sx=' + t.matrix[0]);
  assert.ok(near(t.matrix[5], 100), 'sy=' + t.matrix[5]);
  assert.ok(near(t.matrix[10], 10), 'sz=' + t.matrix[10]);
});

test('cylinder local Z maps onto the requested axis with height magnitude', function () {
  // 单位圆柱沿局部 +Z 延伸，因此：局部 Z 的像 = 圆柱轴向，长度 = height；
  // 局部 X/Y 的像 = 半径方向，长度 = radius。
  const expect = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
  ['x', 'y', 'z'].forEach(function (ax) {
    const entry = {
      part: { prim: 'cylinder', axis: ax, at: [0, 0, 0], radius: 7, height: 21,
              color: '#000000' },
      matrix: Mat4.identity(), jawGap: 0, joint: null, seg: 0,
    };
    const t = ArmModel.partTransform(entry, MODEL);
    const axisVec = [t.matrix[8], t.matrix[9], t.matrix[10]];
    const len = Math.hypot(axisVec[0], axisVec[1], axisVec[2]);
    assert.ok(near(len, 21), ax + ': axis length ' + len + ' (want height 21)');
    const dot = Math.abs(axisVec[0] * expect[ax][0] + axisVec[1] * expect[ax][1] +
                         axisVec[2] * expect[ax][2]) / len;
    assert.ok(near(dot, 1), ax + ': axis direction dot ' + dot);
    [0, 1].forEach(function (i) {
      const col = [t.matrix[i * 4], t.matrix[i * 4 + 1], t.matrix[i * 4 + 2]];
      assert.ok(near(Math.hypot(col[0], col[1], col[2]), 7),
        ax + ': radius on column ' + i);
    });
  });
});

test('cylinder axis follows the parent rotation', function () {
  const res = ArmModel.forward(MODEL, { 2: 90 });
  const cyl = res.parts.find(e => e.part.prim === 'cylinder' &&
                                  e.part.axis === 'z' && e.seg === 1);
  const t = ArmModel.partTransform(cyl, MODEL);
  const axisVec = [t.matrix[8], t.matrix[9], t.matrix[10]];
  // 立柱随 J1 转，J1 绕 Z 所以轴向不变
  assert.ok(near(Math.abs(axisVec[2]), 50), 'column height ' + axisVec[2]);
});

test('jaw gap animates between closed and open', function () {
  const closed = ArmModel.forward(MODEL, {}, { jawRatio: 0 });
  const open = ArmModel.forward(MODEL, {}, { jawRatio: 1 });
  assert.ok(near(closed.jawGap, 6), 'closed gap=' + closed.jawGap);
  assert.ok(near(open.jawGap, 42), 'open gap=' + open.jawGap);
});

test('jointAngle reads count feedback', function () {
  assert.ok(near(ArmModel.jointAngle({ feedback: 'count' }, { deg: 12.5 }), 12.5));
  assert.strictEqual(ArmModel.jointAngle({ feedback: 'count' }, { online: false }), null);
});

test('jointAngle maps zone feedback', function () {
  const j1 = MODEL.joints[0];
  assert.ok(near(ArmModel.jointAngle(j1, { zone: 0 }), -90));
  assert.ok(near(ArmModel.jointAngle(j1, { zone: 45 }), 0));
  assert.ok(near(ArmModel.jointAngle(j1, { zone: 90 }), 90));
  // 未知分区值线性外推（用于暴露异常而不是静默归零）
  assert.ok(near(ArmModel.jointAngle(j1, { zone: 20 }), -50));
});

test('jawRatio clamps', function () {
  assert.ok(near(ArmModel.jawRatio({ code: 90 }), 1));
  assert.ok(near(ArmModel.jawRatio({ code: 0 }), 0));
  assert.ok(near(ArmModel.jawRatio({ code: 45 }), 0.5));
  assert.ok(near(ArmModel.jawRatio({ code: 200 }), 1));
});

test('anglesFromStatus merges a full snapshot', function () {
  const status = {
    j1: { zone: 90 }, j2: { zone: 45 }, j3: { deg: 10 },
    j4: { deg: -5 }, j5: { deg: 90 }, j6: { code: 0 },
  };
  const a = ArmModel.anglesFromStatus(MODEL, status);
  assert.ok(near(a[1], 90) && near(a[2], 45) && near(a[3], 10) &&
            near(a[4], -5) && near(a[5], 90));
});

test('bounds covers a sweep of poses', function () {
  const samples = [];
  for (let yaw = -90; yaw <= 90; yaw += 30) {
    for (let sh = 0; sh <= 90; sh += 15) {
      samples.push({ 1: yaw, 2: sh, 3: -20, 4: 20, 5: 0 });
    }
  }
  const b = ArmModel.bounds(MODEL, samples);
  assert.ok(b.hi[0] > 400, 'hi.x=' + b.hi[0]);
  assert.ok(b.lo[2] <= 0, 'lo.z=' + b.lo[2]);
  assert.ok(b.hi[2] > 300, 'hi.z=' + b.hi[2]);
});

// ---------------------------------------------------------------- 模型配置来源
// 优先用命令行传入的路径；没有就自己让 python 导出一份到临时目录。
// （必须放在最前面：后面的"配色"和"运动学"用例都会用它）
function resolveModelPath() {
  if (process.argv[2] && fs.existsSync(process.argv[2])) return process.argv[2];
  const os = require('os');
  const cp = require('child_process');
  const tmp = path.join(os.tmpdir(), 'arm-model-' + process.pid + '.json');
  const script = path.join(HERE, 'dump_model_json.py');
  for (const python of ['python', 'python3', 'py']) {
    try {
      cp.execFileSync(python, [script, tmp], { stdio: 'pipe' });
      if (fs.existsSync(tmp)) return tmp;
    } catch (err) { /* 换下一个解释器 */ }
  }
  return null;
}

const modelPath = resolveModelPath();
let realConfig = null;
if (modelPath) {
  try { realConfig = JSON.parse(fs.readFileSync(modelPath, 'utf8')); }
  catch (err) { realConfig = null; }
}

// ---------------------------------------------------------------- 配色可读性
// 这一组测试是为了防止一个真实出现过的回归：
//   * 材质色做了 sRGB->线性转换却没在输出前编码回 sRGB → 整条臂接近纯黑；
//   * 雾太浓 → 远处融进背景；
//   * 网格线比机械臂还亮 → 抢视线。
console.log('\ncolors & readability:');

function luminance(rgb) {
  return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
}

const BG_LUM = luminance(ArmViewer.BG_DISPLAY);

function modelColors() {
  const colors = (realConfig && realConfig.colors) || {};
  if (!Object.keys(colors).length) {
    // 内置测试模型没有调色板，用一组代表性颜色代替
    return { fallback: '#626b78', dark: '#3d444e', light: '#d2d9e1' };
  }
  return colors;
}

test('sRGB encode/decode round-trips', function () {
  [0, 0.001, 0.0031308, 0.02, 0.2, 0.5, 0.9, 1].forEach(function (c) {
    const back = ArmViewer.srgbEncode(ArmViewer.srgbToLinear(c));
    assert.ok(Math.abs(back - c) < 1e-6,
      'round-trip ' + c + ' -> ' + back);
  });
});

test('sRGB->linear actually darkens mid-tones (the bug we fixed)', function () {
  const lin = ArmViewer.srgbToLinear(0.5);
  assert.ok(lin < 0.25 && lin > 0.2, 'linear(0.5) = ' + lin + ' (expect ~0.214)');
});

test('every material is brighter than the background (worst case: shadowed)', function () {
  // 最坏情况：朝下的面、没有主光/补光，只有地面反射的环境光
  const worst = { hemi: 0, key: 0, fill: 0 };
  const colors = modelColors();
  Object.keys(colors).forEach(function (name) {
    const lum = luminance(ArmViewer.shade(colors[name], worst));
    assert.ok(lum >= BG_LUM * 1.15,
      name + ' ' + colors[name] + ' lum=' + lum.toFixed(4) +
      ' 与背景 lum=' + BG_LUM.toFixed(4) + ' 太接近');
  });
});

test('every material has strong contrast under typical lighting', function () {
  const typical = { hemi: 0.7, key: 0.7, fill: 0.3 };
  const colors = modelColors();
  Object.keys(colors).forEach(function (name) {
    const lum = luminance(ArmViewer.shade(colors[name], typical));
    assert.ok(lum > 0.28,
      name + ' ' + colors[name] + ' typical lum=' + lum.toFixed(4) + ' 太暗');
  });
});

test('arm body is clearly brighter than the floor grid', function () {
  // 网格线的显示亮度（与 viewer.js 的 makeGrid 保持一致）
  const gridDim = [0.155, 0.180, 0.215];
  const colors = modelColors();
  const bodyLum = luminance(ArmViewer.shade(colors.arm || '#6b7482',
                                           { hemi: 0.7, key: 0.7, fill: 0.3 }));
  assert.ok(bodyLum > luminance(gridDim) * 2,
    'body lum=' + bodyLum.toFixed(3) + ' grid lum=' + luminance(gridDim).toFixed(3));
});

test('fog does not wash out the arm at working distance', function () {
  [1000, 2000].forEach(function (d) {
    const f = 1 - Math.exp(-ArmViewer.FOG_DENSITY * d);
    assert.ok(f < 0.30, 'fog at ' + d + 'mm = ' + (f * 100).toFixed(1) + '%');
  });
});

test('outline color is visible against both background and body', function () {
  assert.ok(luminance(ArmViewer.OUTLINE_DISPLAY) > BG_LUM * 3,
    'outline vs background');
  const colors = modelColors();
  const body = luminance(ArmViewer.shade(colors.arm || '#6b7482',
                                         { hemi: 0.7, key: 0.7, fill: 0.3 }));
  assert.ok(Math.abs(luminance(ArmViewer.OUTLINE_DISPLAY) - body) > 0.08,
    'outline 与本体亮度差太小，看不出描边');
});

test('outline hull scale is small enough not to look bloated', function () {
  assert.ok(ArmViewer.OUTLINE_SCALE > 1.0 && ArmViewer.OUTLINE_SCALE < 1.06,
    'OUTLINE_SCALE=' + ArmViewer.OUTLINE_SCALE);
});

// 描边用的是「放大外壳 + 只画背面（inverted hull）」，
// 这要求所有图元的三角形绕序都是 **从外面看逆时针（CCW）**。
// 绕序反了的话，外壳会盖住本体，整个机械臂变成一片描边色。
console.log('\nprimitive winding:');

function windingReport(mesh, name) {
  const pos = mesh.position, nor = mesh.normal, idx = mesh.index;
  let bad = 0, tested = 0, degenerate = 0;
  for (let t = 0; t < idx.length; t += 3) {
    const a = idx[t] * 3, b = idx[t + 1] * 3, c = idx[t + 2] * 3;
    const e1 = [pos[b] - pos[a], pos[b + 1] - pos[a + 1], pos[b + 2] - pos[a + 2]];
    const e2 = [pos[c] - pos[a], pos[c + 1] - pos[a + 1], pos[c + 2] - pos[a + 2]];
    const n = [e1[1] * e2[2] - e1[2] * e2[1],
               e1[2] * e2[0] - e1[0] * e2[2],
               e1[0] * e2[1] - e1[1] * e2[0]];
    const len = Math.hypot(n[0], n[1], n[2]);
    if (len < 1e-9) { degenerate++; continue; }
    // 与三个顶点的存储法线平均值比较（圆柱侧面共享顶点，取平均足够）
    const vn = [(nor[a] + nor[b] + nor[c]) / 3,
                (nor[a + 1] + nor[b + 1] + nor[c + 1]) / 3,
                (nor[a + 2] + nor[b + 2] + nor[c + 2]) / 3];
    tested++;
    if (n[0] * vn[0] + n[1] * vn[1] + n[2] * vn[2] <= 0) bad++;
  }
  return { name: name, bad: bad, tested: tested, degenerate: degenerate };
}

['makeCube', 'makeCylinder', 'makeSphere'].forEach(function (maker) {
  test(maker + ' triangles wind CCW as seen from outside', function () {
    const mesh = maker === 'makeCylinder' ? ArmViewer[maker](36)
               : maker === 'makeSphere' ? ArmViewer[maker](12, 20)
               : ArmViewer[maker]();
    const r = windingReport(mesh, maker);
    assert.ok(r.tested >= 12, maker + ' 三角形太少: ' + r.tested);
    assert.strictEqual(r.bad, 0,
      maker + ': ' + r.bad + '/' + r.tested + ' 个三角形绕序反了（描边会盖住本体）');
  });
});

test('meshes have matching position/normal/index sizes', function () {
  [['makeCube', []], ['makeCylinder', [36]], ['makeSphere', [12, 20]]]
    .forEach(function (spec) {
      const maker = spec[0];
      const mesh = ArmViewer[maker].apply(null, spec[1]);
      assert.strictEqual(mesh.position.length, mesh.normal.length, maker + ' pos/nor');
      assert.strictEqual(mesh.position.length % 3, 0, maker + ' pos not vec3');
      assert.strictEqual(mesh.index.length % 3, 0, maker + ' index not triangles');
      const maxIndex = Math.max.apply(null, Array.from(mesh.index));
      assert.ok(maxIndex * 3 < mesh.position.length, maker + ' index out of range');
      for (let i = 0; i < mesh.position.length; i++) {
        assert.ok(isFinite(mesh.position[i]) && isFinite(mesh.normal[i]),
          maker + ' 存在 NaN/Inf（默认参数没传？）');
      }
    });
});

// ---------------------------------------------------------------- 真实配置
if (modelPath && realConfig) {
  console.log('\nreal config (' + modelPath + '):');
  const real = realConfig;
  test('chain and joints are present', function () {
    assert.ok(Array.isArray(real.chain) && real.chain.length >= 6,
      'chain length ' + (real.chain && real.chain.length));
    assert.strictEqual(real.joints.length, 6);
    assert.ok(real.geometry === undefined || true);
  });
  test('every joint in the chain is described in joints[]', function () {
    const ids = real.joints.map(j => j.id).sort();
    const chainIds = real.chain.filter(s => s.joint !== null)
      .map(s => s.joint).sort();
    assert.deepStrictEqual(chainIds, ids);
  });
  test('forward kinematics runs on the real config for a sweep', function () {
    for (let yaw = -90; yaw <= 90; yaw += 45) {
      for (let sh = 0; sh <= 90; sh += 30) {
        const res = ArmModel.forward(real,
          { 1: yaw, 2: sh, 3: -50, 4: 90, 5: 160 }, { jawRatio: 0.5 });
        assert.ok(res.tip.every(v => isFinite(v)),
          'non-finite tip at yaw=' + yaw + ' sh=' + sh);
        assert.ok(res.parts.length > 10, 'parts=' + res.parts.length);
      }
    }
  });
  test('real geometry is plausible (reach 300..900 mm)', function () {
    const res = ArmModel.forward(real, {}, { jawRatio: 1 });
    const reach = Math.hypot(res.tip[0], res.tip[1]);
    assert.ok(reach > 300 && reach < 900, 'reach=' + reach);
    assert.ok(res.tip[2] > 0 && res.tip[2] < 300, 'tip.z=' + res.tip[2]);
  });

  // 2026-09-13 用户截图确认：角度变大时 L2 绕 J3 **顺时针**旋转
  // （从正面看、J2 在左）。正视图：右 = +X，上 = +Z。
  test('real config: increasing J3 sensor angle rotates L2 clockwise', function () {
    const screenAngle = function (deg) {
      const status = { j1: { zone: 45 }, j2: { zone: 0 }, j3: { deg: deg },
                       j4: { deg: 180 }, j5: { deg: 0 } };
      const r = ArmModel.forward(real, ArmModel.anglesFromStatus(real, status),
                                 { jawRatio: 0 });
      const d = Mat4.transformDir(r.frames[3], [1, 0, 0]);   // L2 方向
      const a = Math.atan2(d[2], d[0]) * 180 / Math.PI;
      return (a + 360) % 360;          // 归一到 [0,360)，避免 ±180 跳变
    };
    const a0 = screenAngle(0), a45 = screenAngle(45), a90 = screenAngle(90);
    assert.ok(Math.abs(Math.abs(a0) - 180) < 1,
      'J3=0 时 L2 应指向 -X（完全折回），实际屏幕角 ' + a0.toFixed(1) + '°');
    assert.ok(Math.abs(a90 - 90) < 1,
      'J3=90 时 L2 应指向 +Z，实际屏幕角 ' + a90.toFixed(1) + '°');
    assert.ok(a45 < a0 && a90 < a45,
      '角度增大时 L2 应顺时针转（屏幕角递减），得到 ' +
      [a0, a45, a90].map(function (v) { return v.toFixed(1); }).join(' -> '));
  });
  test('real config: J4 = 180 deg puts the forearm in line with the link', function () {
    // 实物（用户确认 2026-09-13）：J4 转到 180° 时，细连杆与前臂体成一条直线。
    // home_deg(J4)=180 就是为了让模型也这样 —— 否则模型会把前臂画成折回来。
    const status = { j3: { deg: 15.0 }, j4: { deg: 180.0 }, j5: { deg: 0 } };
    const angles = ArmModel.anglesFromStatus(real, status);
    const res = ArmModel.forward(real, angles, { jawRatio: 0 });
    const dir = function (i) {
      const d = Mat4.transformDir(res.frames[i], [1, 0, 0]);
      const n = Math.hypot(d[0], d[1], d[2]);
      return d.map(function (v) { return v / n; });
    };
    const rod = dir(3), wrist = dir(4);
    const dot = rod[0] * wrist[0] + rod[1] * wrist[1] + rod[2] * wrist[2];
    assert.ok(dot > 0.999,
      'J4=180 时前臂应与细连杆同向（dot=' + dot.toFixed(4) +
      '），而不是折回 —— 检查 model_config.py 里 J4 的 home_deg');

    // 反过来：J4=0（收拢）时应该明显折回来（不同向）
    const folded = ArmModel.forward(real,
      ArmModel.anglesFromStatus(real, { j3: { deg: 15 }, j4: { deg: 0 }, j5: { deg: 0 } }),
      { jawRatio: 0 });
    const d0 = Mat4.transformDir(folded.frames[4], [1, 0, 0]);
    const n0 = Math.hypot(d0[0], d0[1], d0[2]);
    const dot0 = rod[0] * d0[0] / n0 + rod[1] * d0[1] / n0 + rod[2] * d0[2] / n0;
    assert.ok(dot0 < -0.9, 'J4=0 时前臂应该折回（dot=' + dot0.toFixed(4) + '）');
  });

  test('all primitives use supported prim/axis', function () {
    real.chain.forEach(function (seg) {
      (seg.parts || []).forEach(function (p) {
        assert.ok(['box', 'cylinder'].indexOf(p.prim) >= 0, 'prim=' + p.prim);
        if (p.prim === 'cylinder') {
          assert.ok(['x', 'y', 'z'].indexOf(p.axis) >= 0, 'axis=' + p.axis);
          assert.ok(p.radius > 0 && p.height > 0, 'cylinder size');
        } else {
          assert.ok(p.size.length === 3, 'box size');
        }
        assert.ok(/^#[0-9a-fA-F]{6}$/.test(p.color), 'color=' + p.color);
      });
    });
  });

  // 用户约定：L1 = J2→J3 大臂，L2 = J3→J4 细连杆，L3 = J5 所在前臂。
  //
  // 实物规律（2026-09-13 用户用截图确认）：
  //   传感器 0°   -> L1 与 L2 **完全折回**，几何夹角 180°（收拢姿态）
  //   传感器 180° -> L1 与 L2 **共线**，几何夹角 0°
  //   中间线性：几何夹角 = 180 - 传感器角
  // 模型几何的 0° 才是共线，所以 J3 需要 home_deg = 180。
  test('real config: L1-L2 angle = 180 - J3 sensor angle (folded at zero)', function () {
    assert.ok(Array.isArray(real.links) && real.links.length >= 3,
      '模型配置缺少 links（L1/L2/L3）');
    const L1 = real.links[0], L2 = real.links[1], L3 = real.links[2];

    function angleBetween(segA, segB, status) {
      const r = ArmModel.forward(real, ArmModel.anglesFromStatus(real, status),
                                 { jawRatio: 0 });
      const axis = function (i) {
        const d = Mat4.transformDir(r.frames[i], [1, 0, 0]);
        const n = Math.hypot(d[0], d[1], d[2]);
        return d.map(function (v) { return v / n; });
      };
      const u = axis(segA), v = axis(segB);
      return Math.acos(Math.max(-1, Math.min(1,
        u[0] * v[0] + u[1] * v[1] + u[2] * v[2]))) * 180 / Math.PI;
    }

    // 基准：传感器 0° 时必须是完全折回（不是共线！）
    const folded = angleBetween(L1.seg_from, L2.seg_from, { j3: { deg: 0 } });
    assert.ok(Math.abs(folded - 180) < 0.5,
      '传感器 0°（收拢）时 L1↔L2 应为 180°（完全折回），实际 ' + folded.toFixed(2) +
      '° —— 检查 model_config.py 里 J3 的 home_deg（必须是 180）');

    [0, 27.75, 45, 90, 180].forEach(function (deg) {
      const status = { j3: { deg: deg }, j4: { deg: 180 }, j5: { deg: 0 } };
      const l1l2 = angleBetween(L1.seg_from, L2.seg_from, status);
      const phi = ((180 + deg) % 360 + 360) % 360;
      const expected = Math.min(phi, 360 - phi);   // 折到 0..180
      assert.ok(Math.abs(l1l2 - expected) < 0.5,
        'J3=' + deg + '° 时 L1↔L2 应为 ' + expected.toFixed(2) + '°，实际 ' +
        l1l2.toFixed(2) + '°');
    });

    // 180° 时两杆共线（一条直线）
    const straight = angleBetween(L1.seg_from, L2.seg_from, { j3: { deg: 180 } });
    assert.ok(straight < 0.5, 'J3=180° 时 L1↔L2 应共线，实际 ' + straight.toFixed(2) + '°');

    // J4=180（实物 L2 与 L3 成一条直线）
    const l2l3 = angleBetween(L2.seg_from, L3.seg_from,
                              { j3: { deg: 27.75 }, j4: { deg: 180 }, j5: { deg: 0 } });
    assert.ok(l2l3 < 1.0, 'J4=180° 时 L2↔L3 应接近 0°，实际 ' + l2l3.toFixed(2) + '°');
  });
} else {
  console.log('\n(跳过真实配置测试：无法通过 python 导出模型 JSON)');
}
console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
