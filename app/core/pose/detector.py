"""人体检测器：YOLOv8 检测 + 姿态识别（移植自 video_pose/app/detector.py）。

改造点：构造参数改为 PoseParams 注入，不再依赖模块级 ConfigManager 单例。
"""

from __future__ import annotations

import logging
from math import acos, degrees

import numpy as np

from .pose_params import PoseParams

logger = logging.getLogger("video_pose")

# COCO 关键点索引：肩 5/6、髋 11/12、膝 13/14、踝 15/16
_SHOULDER = (5, 6)
_HIP = (11, 12)
_KNEE = (13, 14)
_ANKLE = (15, 16)

# 用于人物确认的目标关键点（双髋）
_DEFAULT_KEYPOINT_INDICES = (11, 12)


def _weighted_midpoint(keypoints, pair, conf_threshold):
    """两侧关键点的置信度加权中点（x, y）。

    一侧低于阈值时退化为另一侧单点；两侧都不达标返回 None。
    """
    pts = []
    for idx in pair:
        x, y, conf = keypoints[idx]
        if conf >= conf_threshold:
            pts.append((x, y, conf))
    if not pts:
        return None
    if len(pts) == 1:
        return float(pts[0][0]), float(pts[0][1])
    (x1, y1, c1), (x2, y2, c2) = pts
    total = c1 + c2
    return (x1 * c1 + x2 * c2) / total, (y1 * c1 + y2 * c2) / total


def _vector_angle(v1, v2):
    """两向量夹角（度）；零向量返回 None"""
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return None
    cos = float(np.dot(v1, v2) / (n1 * n2))
    return degrees(acos(max(-1.0, min(1.0, cos))))


def is_standing(keypoints, conf_threshold, standing_angle=45.0):
    """基于躯干-大腿夹角判断是否站立。

    Args:
        keypoints: (17,3) 数组，COCO 关键点（x, y, conf）
        conf_threshold: 关键点置信度阈值
        standing_angle: 躯干-大腿夹角阈值（度），小于等于视为站立

    Returns:
        (bool | None, dict): None 表示关键点不足无法判定
    """
    shoulder = _weighted_midpoint(keypoints, _SHOULDER, conf_threshold)
    hip = _weighted_midpoint(keypoints, _HIP, conf_threshold)
    knee = _weighted_midpoint(keypoints, _KNEE, conf_threshold)
    if shoulder is None or hip is None or knee is None:
        return None, {}

    torso = np.array(hip) - np.array(shoulder)
    thigh = np.array(knee) - np.array(hip)
    angle = _vector_angle(torso, thigh)
    if angle is None:
        return None, {}

    torso_uprightness = _vector_angle(torso, np.array([0.0, 1.0]))

    # 深蹲修正：躯干前倾导致躯干-大腿角超阈值，但躯干仍接近竖直且膝盖
    # 伸直（髋膝踝接近直线）时仍是站立。躺姿靠躯干垂直度排除。
    knee_angle = None
    ankle = _weighted_midpoint(keypoints, _ANKLE, conf_threshold)
    if ankle is not None:
        upper = np.array(hip) - np.array(knee)
        lower = np.array(ankle) - np.array(knee)
        knee_angle = _vector_angle(upper, lower)

    standing = angle <= standing_angle
    if (
        not standing
        and knee_angle is not None
        and knee_angle >= 150
        and torso_uprightness is not None
        and torso_uprightness <= 45
    ):
        standing = True

    return standing, {
        "angle": round(angle, 1),
        "knee_angle": round(knee_angle, 1) if knee_angle is not None else None,
        "torso_uprightness": round(torso_uprightness, 1) if torso_uprightness is not None else None,
    }


class Detector:
    def __init__(self, params: PoseParams):
        self.params = params
        self.model_path = params.model_path
        self.pose_model_path = params.pose_model_path
        self.enable_pose_detection = params.enable_pose_detection
        self.batch_size = max(1, int(params.batch_size))
        self.inference_threads = max(0, int(params.inference_threads))
        self.imgsz = int(params.imgsz)
        self.conf_threshold = float(params.confidence_threshold)
        self.person_min_ratio = float(params.person_min_ratio)
        self.keypoint_indices = list(_DEFAULT_KEYPOINT_INDICES)
        self.pose_filter = params.pose_filter
        self.standing_angle = float(params.standing_angle)
        self.model = None

    def load_models(self):
        """加载检测模型。姿态模型本身同时输出边界框与关键点，启用姿态检测时只加载它。"""
        from ultralytics import YOLO

        # 限制 torch 推理线程数（0=不限制）。CPU 推理默认占满所有核，
        # 与录制/合并抢 CPU；限制后速度换余量。
        if self.inference_threads > 0:
            import torch

            torch.set_num_threads(self.inference_threads)
            logger.info(f"推理线程数限制为: {self.inference_threads}")

        if self.enable_pose_detection:
            logger.info(f"加载姿态模型（单模型模式）: {self.pose_model_path}")
            self.model = YOLO(self.pose_model_path)
        else:
            logger.info(f"加载检测模型: {self.model_path}")
            self.model = YOLO(self.model_path)

        logger.info(f"推理输入尺寸 imgsz: {self.imgsz}, 批处理大小: {self.batch_size}")
        return self.model

    def check_person(self, result):
        """检查是否检测到人物并分析姿态。

        Returns:
            tuple: (是否检测到人, 图像, 最大边界框占比, 最大边界框坐标, 关键点数据)
        """
        has_person = bool(result.summary())
        if not has_person:
            return False, result.orig_img, 0, None, None

        img = result.orig_img
        img_area = img.shape[0] * img.shape[1]
        boxes = result.boxes

        if len(boxes) == 0:
            return False, img, 0, None, None

        box_coords = boxes.xyxy.cpu().numpy()
        areas = (box_coords[:, 2] - box_coords[:, 0]) * (box_coords[:, 3] - box_coords[:, 1])
        max_box_idx = int(np.argmax(areas))
        max_box_area = areas[max_box_idx]
        max_box_ratio = max_box_area / img_area
        max_box_coords = box_coords[max_box_idx]

        if max_box_coords is None:
            return False, img, max_box_ratio, None, None
        if max_box_ratio < self.person_min_ratio:
            return False, img, max_box_ratio, max_box_coords, None

        if not self.enable_pose_detection:
            return True, img, max_box_ratio, max_box_coords, None

        kp_data = getattr(result, "keypoints", None)
        kp_data = kp_data.data if kp_data is not None else None
        if kp_data is None or len(kp_data) <= max_box_idx:
            logger.debug("  - 姿态模型未检测到关键点，跳过此帧")
            return False, img, max_box_ratio, max_box_coords, None

        keypoints = kp_data.cpu().numpy()[max_box_idx]

        target_confs = keypoints[self.keypoint_indices, 2]
        if not np.any(target_confs >= self.conf_threshold):
            logger.debug("  - 未检测到目标关键点，跳过此帧")
            return False, img, max_box_ratio, max_box_coords, keypoints

        if self.pose_filter != "none":
            standing, pose_detail = is_standing(keypoints, self.conf_threshold, self.standing_angle)
            if standing is None:
                logger.debug("  - 姿态无法判定（关键点不足），按不命中处理")
                return False, img, max_box_ratio, max_box_coords, keypoints
            if (self.pose_filter == "standing") != standing:
                logger.debug(f"  - 姿态过滤({self.pose_filter})未命中: {pose_detail}")
                return False, img, max_box_ratio, max_box_coords, keypoints
            logger.debug(f"  - 姿态过滤命中({self.pose_filter}): {pose_detail}")

        logger.debug(f"  - 命中人物，边界框占比: {max_box_ratio:.2%}")
        return True, img, max_box_ratio, max_box_coords, keypoints
