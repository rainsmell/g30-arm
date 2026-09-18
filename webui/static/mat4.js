/*!
 * mat4.js —— 4x4 矩阵 / 向量工具（列主序，与 WebGL 一致）
 *
 * 无任何依赖，既能在浏览器里用（挂到 window.Mat4），
 * 也能在 node 里 require 出来做单元测试。
 *
 * 约定：m[col*4 + row]，即
 *      m[0] m[4] m[8]  m[12]
 *      m[1] m[5] m[9]  m[13]
 *      m[2] m[6] m[10] m[14]
 *      m[3] m[7] m[11] m[15]
 * 右手坐标系，Z 向上（机械臂模型用 Z-up）。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Mat4 = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const TAU = Math.PI * 2;
  const DEG = Math.PI / 180;

  function identity() {
    return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  }

  function clone(a) { return new Float32Array(a); }

  /** out = a · b（先应用 b，再应用 a） */
  function multiply(a, b) {
    const out = new Float32Array(16);
    for (let c = 0; c < 4; c++) {
      const b0 = b[c * 4], b1 = b[c * 4 + 1], b2 = b[c * 4 + 2], b3 = b[c * 4 + 3];
      out[c * 4]     = a[0] * b0 + a[4] * b1 + a[8]  * b2 + a[12] * b3;
      out[c * 4 + 1] = a[1] * b0 + a[5] * b1 + a[9]  * b2 + a[13] * b3;
      out[c * 4 + 2] = a[2] * b0 + a[6] * b1 + a[10] * b2 + a[14] * b3;
      out[c * 4 + 3] = a[3] * b0 + a[7] * b1 + a[11] * b2 + a[15] * b3;
    }
    return out;
  }

  function multiplyAll() {
    let out = arguments[0];
    for (let i = 1; i < arguments.length; i++) out = multiply(out, arguments[i]);
    return out;
  }

  function translation(x, y, z) {
    const m = identity();
    m[12] = x; m[13] = y; m[14] = z;
    return m;
  }

  function scaling(x, y, z) {
    const m = identity();
    m[0] = x; m[5] = y === undefined ? x : y; m[10] = z === undefined ? x : z;
    return m;
  }

  /**
   * 右手法则绕任意轴旋转 angle 弧度。
   * axis 会被归一化；零向量返回单位矩阵。
   */
  function rotationAxis(ax, ay, az, angle) {
    const len = Math.hypot(ax, ay, az);
    if (!len || !isFinite(len)) return identity();
    const x = ax / len, y = ay / len, z = az / len;
    const c = Math.cos(angle), s = Math.sin(angle), t = 1 - c;
    const m = identity();
    m[0]  = t * x * x + c;
    m[1]  = t * x * y + s * z;
    m[2]  = t * x * z - s * y;
    m[4]  = t * x * y - s * z;
    m[5]  = t * y * y + c;
    m[6]  = t * y * z + s * x;
    m[8]  = t * x * z + s * y;
    m[9]  = t * y * z - s * x;
    m[10] = t * z * z + c;
    return m;
  }

  function rotationDeg(ax, ay, az, degrees) {
    return rotationAxis(ax, ay, az, degrees * DEG);
  }

  function perspective(fovYDeg, aspect, near, far) {
    const f = 1 / Math.tan((fovYDeg * DEG) / 2);
    const nf = 1 / (near - far);
    const m = new Float32Array(16);
    m[0] = f / aspect;
    m[5] = f;
    m[10] = (far + near) * nf;
    m[11] = -1;
    m[14] = 2 * far * near * nf;
    return m;
  }

  function lookAt(eye, center, up) {
    let zx = eye[0] - center[0], zy = eye[1] - center[1], zz = eye[2] - center[2];
    let l = Math.hypot(zx, zy, zz) || 1;
    zx /= l; zy /= l; zz /= l;

    let xx = up[1] * zz - up[2] * zy;
    let xy = up[2] * zx - up[0] * zz;
    let xz = up[0] * zy - up[1] * zx;
    l = Math.hypot(xx, xy, xz);
    if (!l) { xx = 1; xy = 0; xz = 0; } else { xx /= l; xy /= l; xz /= l; }

    const yx = zy * xz - zz * xy;
    const yy = zz * xx - zx * xz;
    const yz = zx * xy - zy * xx;

    const m = new Float32Array(16);
    m[0] = xx; m[1] = yx; m[2] = zx; m[3] = 0;
    m[4] = xy; m[5] = yy; m[6] = zy; m[7] = 0;
    m[8] = xz; m[9] = yz; m[10] = zz; m[11] = 0;
    m[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2]);
    m[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2]);
    m[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2]);
    m[15] = 1;
    return m;
  }

  /** 变换一个点（w = 1），返回新数组 */
  function transformPoint(m, p) {
    const x = p[0], y = p[1], z = p[2];
    return [
      m[0] * x + m[4] * y + m[8] * z + m[12],
      m[1] * x + m[5] * y + m[9] * z + m[13],
      m[2] * x + m[6] * y + m[10] * z + m[14],
    ];
  }

  /** 变换一个方向（w = 0） */
  function transformDir(m, p) {
    const x = p[0], y = p[1], z = p[2];
    return [
      m[0] * x + m[4] * y + m[8] * z,
      m[1] * x + m[5] * y + m[9] * z,
      m[2] * x + m[6] * y + m[10] * z,
    ];
  }

  /** 3x3 逆（返回 9 元素行主序数组），奇异时返回单位阵 */
  function invert3(m00, m01, m02, m10, m11, m12, m20, m21, m22) {
    const det = m00 * (m11 * m22 - m12 * m21)
              - m01 * (m10 * m22 - m12 * m20)
              + m02 * (m10 * m21 - m11 * m20);
    if (Math.abs(det) < 1e-12) return [1, 0, 0, 0, 1, 0, 0, 0, 1];
    const id = 1 / det;
    return [
      (m11 * m22 - m12 * m21) * id, (m02 * m21 - m01 * m22) * id, (m01 * m12 - m02 * m11) * id,
      (m12 * m20 - m10 * m22) * id, (m00 * m22 - m02 * m20) * id, (m02 * m10 - m00 * m12) * id,
      (m10 * m21 - m11 * m20) * id, (m01 * m20 - m00 * m21) * id, (m00 * m11 - m01 * m10) * id,
    ];
  }

  /**
   * 由模型矩阵取法线矩阵 = (M3x3)^-T，列主序 9 元素。
   * 支持非等比缩放（长方体图元会用到）。
   */
  function normalMatrix(m) {
    const inv = invert3(
      m[0], m[4], m[8],
      m[1], m[5], m[9],
      m[2], m[6], m[10]
    );
    // inv 是行主序的逆矩阵，转置后按列主序返回
    return new Float32Array([
      inv[0], inv[3], inv[6],
      inv[1], inv[4], inv[7],
      inv[2], inv[5], inv[8],
    ]);
  }

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  return {
    TAU, DEG,
    identity, clone, multiply, multiplyAll,
    translation, scaling, rotationAxis, rotationDeg,
    perspective, lookAt,
    transformPoint, transformDir, normalMatrix, invert3, clamp,
  };
});
