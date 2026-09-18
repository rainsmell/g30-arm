/*!
 * model.js —— 机械臂数字孪生：关节值 -> 角度 -> 正运动学
 *
 * 纯函数，无 DOM/WebGL 依赖，可在 node 里单测：
 *     node tests/test_model.js
 *
 * 输入是服务端 GET /api/model 返回的配置（webui/model_config.py），
 * 因此改模型几何不需要动这个文件。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory(require('./mat4.js'));
  } else {
    root.ArmModel = factory(root.Mat4);
  }
})(typeof self !== 'undefined' ? self : this, function (Mat4) {
  'use strict';

  // ---------------------------------------------------------------- 角度提取

  /** 把一个关节的实时状态换算成模型角度（度）。 */
  function jointAngle(joint, status) {
    if (!status || status.online === false) return null;
    if (joint.feedback === 'count') {
      const deg = status.deg;
      if (typeof deg !== 'number') return null;
      // home_deg = 传感器读到 0 时模型应该是多少度（把"收拢姿态"对齐到模型零位）
      const home = typeof joint.home_deg === 'number' ? joint.home_deg : 0;
      return home + deg;
    }
    if (joint.feedback === 'zone') {
      const zone = status.zone !== undefined ? status.zone
                 : status.pos !== undefined ? status.pos
                 : status.code;
      if (zone === undefined || zone === null) return null;
      const map = joint.zone_to_deg || {};
      // zone 型关节也支持 home_deg（整体 +180 之类的校正）
      const home = typeof joint.home_deg === 'number' ? joint.home_deg : 0;
      if (map[String(zone)] !== undefined) return home + map[String(zone)];
      // 未知分区值：按 max 线性外推，方便发现异常
      const keys = Object.keys(map).map(Number).sort(function (a, b) { return a - b; });
      if (!keys.length) return null;
      const lo = keys[0], hi = keys[keys.length - 1];
      const t = (zone - lo) / (hi - lo || 1);
      return home + map[String(lo)] + t * (map[String(hi)] - map[String(lo)]);
    }
    return null;
  }

  /** 夹爪开合比 0..1（0 = 全闭，1 = 全开）。 */
  function jawRatio(status) {
    if (!status || status.online === false) return 0;
    const code = status.code !== undefined ? status.code
               : status.pos !== undefined ? status.pos : 0;
    return Mat4.clamp(code / 90, 0, 1);
  }

  /**
   * 从整张状态快照算出每个关节的显示角度。
   * @returns {Object} {1: deg|null, ..., 6: deg|null}
   */
  function anglesFromStatus(model, status) {
    const out = {};
    (model.joints || []).forEach(function (joint) {
      const st = status ? status['j' + joint.id] : null;
      out[joint.id] = jointAngle(joint, st);
    });
    return out;
  }

  // ---------------------------------------------------------------- 正运动学

  /**
   * 计算整条运动链的世界矩阵。
   *
   * @param {Object} model   来自 /api/model 的配置
   * @param {Object} angles  {关节id: 度}，缺省或 null 视为 0
   * @param {Object} [opt]   {jawRatio: 0..1}
   * @returns {{frames: Array, parts: Array, tip: number[], joints: Object}}
   *          frames[i] 与 model.chain[i] 一一对应
   */
  function forward(model, angles, opt) {
    opt = opt || {};
    const jaw = typeof opt.jawRatio === 'number' ? opt.jawRatio : 0;
    const jawCfg = model.jaw || { open_mm: 42, closed_mm: 6 };
    const gap = jawCfg.closed_mm + (jawCfg.open_mm - jawCfg.closed_mm) * jaw;

    let M = Mat4.identity();
    const frames = [];
    const parts = [];
    const jointInfo = {};

    (model.chain || []).forEach(function (seg, index) {
      // 平移到位姿
      M = Mat4.multiply(M, Mat4.translation(seg.pivot[0], seg.pivot[1], seg.pivot[2]));

      // 旋转
      let theta = 0;
      if (seg.joint !== null && seg.joint !== undefined && seg.axis) {
        theta = angles[seg.joint];
        if (theta === null || theta === undefined) theta = 0;
        theta *= Mat4.DEG;
        M = Mat4.multiply(M, Mat4.rotationAxis(
          seg.axis[0], seg.axis[1], seg.axis[2], theta));
      }
      frames.push(M);

      // 关节轴在世界坐标下的位置与方向（用于高亮/标注）
      if (seg.axis) {
        const origin = Mat4.transformPoint(M, [0, 0, 0]);
        const dir = Mat4.transformDir(M, seg.axis);
        const len = Math.hypot(dir[0], dir[1], dir[2]) || 1;
        jointInfo[seg.joint] = {
          index: index,
          origin: origin,
          axis: [dir[0] / len, dir[1] / len, dir[2] / len],
          deg: (angles[seg.joint] === null || angles[seg.joint] === undefined)
            ? null : angles[seg.joint],
        };
      }

      // 图元
      (seg.parts || []).forEach(function (part) {
        parts.push({ seg: index, joint: seg.joint, part: part,
                     matrix: M, jawGap: gap });
      });
    });

    // 工具尖端：最后一段坐标系里沿 +X 再走"最后一个图元的外缘"
    const last = frames[frames.length - 1] || M;
    const toolLen = _toolLength(model);
    const tip = Mat4.transformPoint(last, [toolLen, 0, 0]);

    return { frames: frames, parts: parts, tip: tip, joints: jointInfo,
             jawGap: gap };
  }

  function _toolLength(model) {
    const chain = model.chain || [];
    const lastSeg = chain[chain.length - 1];
    if (!lastSeg) return 0;
    let maxX = 0;
    (lastSeg.parts || []).forEach(function (p) {
      const half = (p.size && p.size[0] ? p.size[0] : 0) / 2;
      const cx = (p.at && p.at[0]) || 0;
      maxX = Math.max(maxX, cx + half);
    });
    return maxX;
  }

  /**
   * 计算一个图元的世界变换（模型矩阵），已处理夹爪左右爪的偏置。
   * @returns {{matrix: Float32Array, size: number[], color: string, prim: string}}
   */
  function partTransform(entry, model) {
    const p = entry.part;
    let local = Mat4.translation(p.at[0], p.at[1], p.at[2]);

    if (p.jaw === 'left' || p.jaw === 'right') {
      const sign = p.jaw === 'left' ? 1 : -1;
      local = Mat4.multiply(Mat4.translation(0, sign * entry.jawGap / 2, 0), local);
    }

    let m = Mat4.multiply(entry.matrix, local);

    if (p.prim === 'box') {
      m = Mat4.multiply(m, Mat4.scaling(p.size[0], p.size[1], p.size[2]));
    } else if (p.prim === 'cylinder') {
      // 单位圆柱沿 +Z，先按 axis 转到目标方向，再缩放
      const rot = { x: [0, 1, 0], y: [-1, 0, 0], z: [0, 0, 0] }[p.axis || 'z'];
      if (rot) {
        m = Mat4.multiply(m, Mat4.rotationDeg(rot[0], rot[1], rot[2], 90));
      }
      m = Mat4.multiply(m, Mat4.scaling(p.radius, p.radius, p.height));
    }
    return { matrix: m, prim: p.prim || 'box', color: p.color || '#888888',
             joint: entry.joint, indexed: p.jaw ? p.jaw : null };
  }

  /** 估算整条臂的外形包围盒（给相机自动取景用）。 */
  function bounds(model, samples) {
    const lo = [Infinity, Infinity, Infinity];
    const hi = [-Infinity, -Infinity, -Infinity];
    function grow(p) {
      for (let i = 0; i < 3; i++) {
        if (p[i] < lo[i]) lo[i] = p[i];
        if (p[i] > hi[i]) hi[i] = p[i];
      }
    }
    (samples || []).forEach(function (angles) {
      const res = forward(model, angles, { jawRatio: 0.5 });
      res.parts.forEach(function (entry) {
        const t = partTransform(entry, model);
        // 用图元的 8 个角点近似
        [-0.5, 0.5].forEach(function (x) {
          [-0.5, 0.5].forEach(function (y) {
            [-0.5, 0.5].forEach(function (z) {
              grow(Mat4.transformPoint(t.matrix, [x, y, z]));
            });
          });
        });
      });
      grow(res.tip);
    });
    return { lo: lo, hi: hi };
  }

  return {
    jointAngle: jointAngle,
    jawRatio: jawRatio,
    anglesFromStatus: anglesFromStatus,
    forward: forward,
    partTransform: partTransform,
    bounds: bounds,
  };
});
