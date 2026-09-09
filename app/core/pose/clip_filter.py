"""CLIP 服装分类过滤：零样本二分类（紧身下装 vs 宽松/其他下装）。

pose 命中帧整帧送 CLIP 做图文相似度打分（正/负 prompt 组类均值对比）；
视频级聚合正例占比低于阈值时整段丢弃（不切割不合并，原视频走既有删除
路径）。无成功分类帧（模型加载失败）按保留处理，宁可漏删不错删。

不裁腿部区域：实测 8 段视频整帧的正例帧 47/56，优于髋→踝裁剪的 35/56
——CLIP 在网页图上预训练，擅长整图语义；裁剪反而丢失场景上下文，还会
把器械护垫等碎片框进画面稀释服装信号。

纯逻辑（打分、聚合、报告）不依赖 torch，可单测；模型加载与推理封装在
ClipFilter 里，open_clip 惰性导入——未开启过滤的任务完全不碰重依赖。
"""

from __future__ import annotations

import base64
import hashlib
import html as html_mod
import json
import logging
import math
import os
from typing import Any, Optional

from .pose_params import (
    DEFAULT_NEGATIVE_PROMPTS,
    DEFAULT_POSITIVE_PROMPTS,
    MODELS_DIR,
)

logger = logging.getLogger("video_pose")

# CLIP 权重缓存目录（镜像内 /app/app/core/pose/models/clip，重建镜像后重下）
CLIP_CACHE_DIR = str(MODELS_DIR / "clip")


# 紧身修饰词命中的 prompt 权重：tightness 是本分类的核心信号，长裤/短裤
# 只是 garment 类型——穿着短裤的人会让 4 条长裤正类 prompt 全部走低，把类
# 均值拖向负类。实测（12 主播逐帧）：加权后判定不变、宽松样本的拒绝更强
# （5/12→4/12）；而能翻转边界帧的 max/子类取优策略会把唯一宽松样本翻成
# 全通过，不可用。
_TIGHT_KEYWORD = "tight"
_TIGHT_PROMPT_WEIGHT = 3.0


def _prompt_weight(prompt: str) -> float:
    return _TIGHT_PROMPT_WEIGHT if _TIGHT_KEYWORD in prompt.lower() else 1.0


def _weighted_mean(values: list[tuple[float, float]]) -> float:
    total = sum(v * w for v, w in values)
    return total / sum(w for _, w in values)


def score_frame(
    sims: dict[str, float],
    positive_prompts: list[str],
    negative_prompts: list[str],
    logit_scale: float = 100.0,
) -> Optional[dict[str, Any]]:
    """CLIP 零样本打分：正/负 prompt 组各取相似度加权均值后二分类。

    逐 prompt 全体 softmax 会把正类概率质量分摊到多条 prompt 上——负类一条
    强 prompt（紧身短裤帧上的 bare legs）就能压过整体更强的正类组；类均值
    才是「组对组」的证据比较（零样本分类的标准 prompt-ensemble 做法）。
    组内再对含 tight 修饰词的 prompt 加权（见 _TIGHT_PROMPT_WEIGHT）。
    sims 为 {prompt: 余弦相似度}；某条 prompt 缺失时从对应组均值中剔除。
    """
    pos_vals = [
        (float(sims[p]), _prompt_weight(p)) for p in positive_prompts if p and p in sims
    ]
    neg_vals = [
        (float(sims[p]), _prompt_weight(p)) for p in negative_prompts if p and p in sims
    ]
    if not pos_vals or not neg_vals:
        return None
    m_pos = _weighted_mean(pos_vals)
    m_neg = _weighted_mean(neg_vals)
    # 等价于 [m_pos, m_neg] * logit_scale 的 2-way softmax，取 sigmoid 形式防溢出
    z = math.exp(min((m_pos - m_neg) * logit_scale, 60.0))
    pos_prob = z / (1.0 + z)
    return {
        "pos_prob": round(pos_prob, 4),
        "pos_mean": round(m_pos, 4),
        "neg_mean": round(m_neg, 4),
        "is_positive": pos_prob > 0.5,
        "prompt_sims": {
            p: round(float(sims.get(p, 0.0)), 4)
            for p in list(positive_prompts) + list(negative_prompts)
            if p
        },
    }


def aggregate_verdict(
    frame_scores: list[dict[str, Any]], min_positive_ratio: float
) -> dict[str, Any]:
    """视频级判定。无成功分类帧 → insufficient（按保留处理，fail-open）。"""
    classified = [s for s in frame_scores if s]
    if not classified:
        return {
            "verdict": "insufficient",
            "classified": 0,
            "positive": 0,
            "ratio": None,
            "threshold": round(float(min_positive_ratio), 4),
        }
    positive = sum(1 for s in classified if s.get("is_positive"))
    ratio = positive / len(classified)
    verdict = "pass" if ratio >= min_positive_ratio else "reject"
    return {
        "verdict": verdict,
        "classified": len(classified),
        "positive": positive,
        "ratio": round(ratio, 4),
        "threshold": round(float(min_positive_ratio), 4),
    }


class ClipFilter:
    """open_clip 零样本分类器（CPU 推理）。prompt 文本特征只编码一次。"""

    def __init__(
        self,
        model_name: str = "ViT-B-32",
        pretrained: str = "openai",
        positive_prompts: Optional[list[str]] = None,
        negative_prompts: Optional[list[str]] = None,
        cache_dir: Optional[str] = None,
    ):
        self.model_name = model_name or "ViT-B-32"
        self.pretrained = pretrained or "openai"
        self.positive_prompts = [p for p in (positive_prompts or []) if p] or list(
            DEFAULT_POSITIVE_PROMPTS
        )
        self.negative_prompts = [p for p in (negative_prompts or []) if p] or list(
            DEFAULT_NEGATIVE_PROMPTS
        )
        self.cache_dir = cache_dir or CLIP_CACHE_DIR
        self.load_error: Optional[str] = None
        self._model = None
        self._preprocess = None
        self._text_features = None

    @property
    def available(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        """加载模型并预编码 prompt。失败置 load_error 返回 False（闸门失效，按保留处理）。"""
        if self._model is not None:
            return True
        try:
            import open_clip
            import torch

            model, _, preprocess = open_clip.create_model_and_transforms(
                self.model_name, pretrained=self.pretrained, cache_dir=self.cache_dir
            )
            model.eval()
            tokenizer = open_clip.get_tokenizer(self.model_name)
            prompts = self.positive_prompts + self.negative_prompts
            with torch.no_grad():
                tokens = tokenizer(prompts)
                text_features = model.encode_text(tokens)
                text_features = text_features / text_features.norm(
                    dim=-1, keepdim=True
                )
            self._model = model
            self._preprocess = preprocess
            self._text_features = text_features
            logger.info(
                f"CLIP 模型加载完成: {self.model_name}/{self.pretrained}，"
                f"正/负 prompt 各 {len(self.positive_prompts)}/{len(self.negative_prompts)} 条"
            )
            return True
        except Exception as e:
            self.load_error = str(e)
            logger.error(f"CLIP 模型加载失败，服装分类闸门本次失效（按保留处理）: {e}")
            return False

    def classify(self, frame_bgr) -> Optional[dict[str, Any]]:
        """整帧（BGR ndarray）分类，返回 score_frame 结果；模型未就绪返回 None。"""
        if not self.available:
            return None
        import numpy as np
        import torch
        from PIL import Image

        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        image = self._preprocess(Image.fromarray(rgb)).unsqueeze(0)
        with torch.no_grad():
            features = self._model.encode_image(image)
            features = features / features.norm(dim=-1, keepdim=True)
            sims = (features @ self._text_features.T).squeeze(0).tolist()
        prompt_order = self.positive_prompts + self.negative_prompts
        return score_frame(dict(zip(prompt_order, sims)), self.positive_prompts, self.negative_prompts)


class ClipReport:
    """逐视频落盘分类明细：<report_dir>/<视频名_hash>/crops/*.jpg + report.json + report.html。

    html 内嵌 base64 crop，单文件可直接打开人工核对。report_dir 为 None 时
    所有方法均为空操作。
    """

    def __init__(self, report_dir: Optional[str], save_crops: bool = True):
        self.report_dir = report_dir
        self.save_crops = save_crops
        self._video_dir: Optional[str] = None
        self._video_path: Optional[str] = None
        self._frames: list[dict[str, Any]] = []
        self._skipped: list[dict[str, Any]] = []

    def start_video(self, video_path: str) -> None:
        if not self.report_dir:
            return
        stem = os.path.splitext(os.path.basename(video_path))[0]
        # 同名不同目录的视频会撞报告目录，用全路径短哈希区分
        digest = hashlib.md5(os.path.abspath(video_path).encode("utf-8")).hexdigest()[:6]
        self._video_dir = os.path.join(self.report_dir, f"{stem}_{digest}")
        os.makedirs(os.path.join(self._video_dir, "crops"), exist_ok=True)
        self._video_path = video_path
        self._frames = []
        self._skipped = []

    def add_frame(self, ts: float, score: dict[str, Any], frame_bgr) -> None:
        if self._video_dir is None:
            return
        record = {"t": round(ts, 1), **score}
        if self.save_crops and frame_bgr is not None:
            name = f"{len(self._frames) + 1:04d}_t{ts:.1f}s.jpg"
            path = os.path.join(self._video_dir, "crops", name)
            try:
                import cv2

                h = frame_bgr.shape[0]
                scale = 480.0 / h if h > 480 else 1.0
                thumb = (
                    cv2.resize(frame_bgr, None, fx=scale, fy=scale)
                    if scale < 1.0
                    else frame_bgr
                )
                cv2.imwrite(path, thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                record["crop"] = f"crops/{name}"
            except Exception as e:
                logger.warning(f"保存 CLIP 报告帧失败: {e}")
        self._frames.append(record)

    def add_skipped(self, ts: float, reason: str) -> None:
        if self._video_dir is not None:
            self._skipped.append({"t": round(ts, 1), "reason": reason})

    def finish_video(
        self, verdict: dict[str, Any], extra: Optional[dict[str, Any]] = None
    ) -> None:
        if self._video_dir is None:
            return
        report = {
            "video": self._video_path,
            "verdict": verdict,
            **(extra or {}),
            "frames": self._frames,
            "skipped": self._skipped,
        }
        with open(os.path.join(self._video_dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        _write_html(os.path.join(self._video_dir, "report.html"), report)
        logger.info(f"CLIP 分类报告已写入: {self._video_dir}")
        self._video_dir = None


def _embedded_jpeg(video_dir: str, rel_path: Optional[str]) -> str:
    if not rel_path:
        return ""
    path = os.path.join(video_dir, rel_path)
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return ""
    return f'<img src="data:image/jpeg;base64,{data}" alt="{html_mod.escape(rel_path)}" style="height:240px">'


_VERDICT_LABEL = {"pass": "通过（保留）", "reject": "拒绝（整段丢弃）", "insufficient": "证据不足（保留）"}


def _write_html(path: str, report: dict[str, Any]) -> None:
    verdict = report.get("verdict") or {}
    rows = []
    for frame in report.get("frames", []):
        crop_html = _embedded_jpeg(os.path.dirname(path), frame.get("crop"))
        sims = sorted(
            (frame.get("prompt_sims") or {}).items(), key=lambda kv: -kv[1]
        )
        top = "; ".join(f"{html_mod.escape(k)}={v:.3f}" for k, v in sims[:4])
        means = (
            f"正类均值 {frame.get('pos_mean', 0):.3f} / 负类均值 {frame.get('neg_mean', 0):.3f}"
        )
        badge = (
            '<span style="color:#16a34a">正</span>'
            if frame.get("is_positive")
            else '<span style="color:#dc2626">负</span>'
        )
        ratio = f"{frame.get('pos_prob', 0):.3f}"
        rows.append(
            f"<tr><td>{frame.get('t')}s</td><td>{crop_html}</td>"
            f"<td>{ratio}</td><td>{badge}</td>"
            f"<td style='font-size:12px;color:#666'>{means}<br>{top}</td></tr>"
        )
    skipped = report.get("skipped") or []
    skipped_html = ""
    if skipped:
        items = "; ".join(f"{s['t']}s({s['reason']})" for s in skipped[:50])
        skipped_html = f"<p style='color:#888'>跳过帧 {len(skipped)} 个: {items}</p>"
    label = _VERDICT_LABEL.get(verdict.get("verdict", ""), verdict.get("verdict", ""))
    ratio = verdict.get("ratio")
    ratio_txt = f"{ratio:.0%}" if ratio is not None else "—"

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>CLIP 服装分类报告</title></head>
<body style="font-family:sans-serif;max-width:900px;margin:20px auto">
<h2>CLIP 服装分类报告</h2>
<p>视频: {html_mod.escape(str(report.get('video') or ''))}</p>
<p>判定: <b>{label}</b>｜正例帧 {verdict.get('positive', 0)}/{verdict.get('classified', 0)}
（{ratio_txt}，阈值 {verdict.get('threshold', '—')}）</p>
{skipped_html}
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
<tr><th>时间</th><th>采样帧</th><th>正类概率</th><th>判定</th><th>prompt 相似度（前 4）</th></tr>
{"".join(rows)}
</table>
</body></html>"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as e:
        logger.warning(f"写 CLIP html 报告失败: {e}")
