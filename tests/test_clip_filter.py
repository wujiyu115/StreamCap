"""CLIP 服装分类闸门的纯逻辑测试。

跑法: .venv/bin/python -m pytest tests/test_clip_filter.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.pose.clip_filter import (
    aggregate_verdict,
    score_frame,
    weights_available,
)
from app.core.pose.pose_params import DEFAULTS, PoseParams


# ── 外置权重探测 ──────────────────────────────────────────


class TestWeightsAvailable:
    def test_missing_dir(self, tmp_path):
        assert weights_available(str(tmp_path / "nope")) is False

    def test_empty_dir(self, tmp_path):
        assert weights_available(str(tmp_path)) is False

    def test_hub_cache_layout(self, tmp_path):
        (tmp_path / "models--timm--vit_base_patch32_clip_224.openai" / "blobs").mkdir(parents=True)
        assert weights_available(str(tmp_path)) is True

    def test_legacy_bin_layout(self, tmp_path):
        (tmp_path / "open_clip_pytorch_model.bin").touch()
        assert weights_available(str(tmp_path)) is True

    def test_only_locks_or_tags_not_weights(self, tmp_path):
        (tmp_path / ".locks").mkdir()
        (tmp_path / "CACHEDIR.TAG").touch()
        assert weights_available(str(tmp_path)) is False


# ── prompt 打分 ───────────────────────────────────────────


class TestScoreFrame:
    POS = ["a person in leggings", "a person in yoga pants"]
    NEG = ["a person in jeans", "a person in a skirt"]

    def test_positive_group_stronger(self):
        # 单条负 prompt 最高，但正类组均值更高（组对组证据比较）
        sims = {
            "a person in leggings": 0.30,
            "a person in yoga pants": 0.28,
            "a person in jeans": 0.32,
            "a person in a skirt": 0.15,
        }
        score = score_frame(sims, self.POS, self.NEG)
        assert score is not None
        assert score["is_positive"] is True
        assert score["pos_mean"] == pytest.approx(0.29)
        assert score["neg_mean"] == pytest.approx(0.235)
        assert score["pos_prob"] > 0.5

    def test_negative_group_stronger(self):
        sims = {
            "a person in leggings": 0.15,
            "a person in yoga pants": 0.16,
            "a person in jeans": 0.32,
            "a person in a skirt": 0.30,
        }
        score = score_frame(sims, self.POS, self.NEG)
        assert score is not None
        assert score["is_positive"] is False
        assert score["pos_prob"] < 0.5

    def test_missing_prompt_excluded_from_mean(self):
        # 缺失的 prompt 从组均值剔除，不影响判定方向
        sims = {
            "a person in leggings": 0.30,
            "a person in jeans": 0.20,
            "a person in a skirt": 0.18,
        }
        score = score_frame(sims, self.POS, self.NEG)
        assert score is not None
        assert score["pos_mean"] == pytest.approx(0.30)
        assert score["is_positive"] is True

    def test_one_side_entirely_missing_returns_none(self):
        sims = {"a person in jeans": 0.2}
        assert score_frame(sims, self.POS, self.NEG) is None

    def test_empty_prompts_returns_none(self):
        assert score_frame({}, [], []) is None

    def test_boundary_equal_means_is_negative(self):
        # 类均值完全相同 → pos_prob=0.5，按未命中（> 0.5 才算正）
        sims = {
            "a person in leggings": 0.25,
            "a person in yoga pants": 0.25,
            "a person in jeans": 0.25,
            "a person in a skirt": 0.25,
        }
        score = score_frame(sims, self.POS, self.NEG)
        assert score["pos_prob"] == pytest.approx(0.5)
        assert score["is_positive"] is False

    def test_large_margin_saturates_without_overflow(self):
        sims = {
            "a person in leggings": 0.90,
            "a person in yoga pants": 0.90,
            "a person in jeans": 0.01,
            "a person in a skirt": 0.01,
        }
        score = score_frame(sims, self.POS, self.NEG)
        assert score["pos_prob"] == pytest.approx(1.0)
        assert score["is_positive"] is True


class TestTightWeighting:
    """含 tight 修饰词的 prompt 权重更重（tightness 是核心信号）。"""

    POS = ["tight leggings", "yoga pants"]
    NEG = ["loose pants", "a skirt"]

    def test_tight_prompt_dominates_group_mean(self):
        # 非加权均值 0.20 < 0.25 为负；tight prompt 0.30 × 3 拉高加权均值
        sims = {
            "tight leggings": 0.30,
            "yoga pants": 0.10,
            "loose pants": 0.30,
            "a skirt": 0.20,
        }
        score = score_frame(sims, self.POS, self.NEG)
        assert score is not None
        # (0.30*3 + 0.10*1) / 4 = 0.25 vs 负类均值 0.25 → 边界，构造偏一点的数
        assert score["pos_mean"] == pytest.approx(0.25)
        assert score["neg_mean"] == pytest.approx(0.25)

    def test_weighting_can_flip_frame_to_positive(self):
        sims = {
            "tight leggings": 0.30,
            "yoga pants": 0.10,
            "loose pants": 0.28,
            "a skirt": 0.20,
        }
        # 非加权 pos 均值 0.20 < neg 均值 0.24 → 负；
        # 加权 pos (0.30*3+0.10)/4 = 0.25 > 0.24 → 正
        score = score_frame(sims, self.POS, self.NEG)
        assert score is not None
        assert score["is_positive"] is True

    def test_tight_in_negative_also_weighted(self):
        # 规则对称：负类里的 tight 修饰 prompt 同样加权
        sims = {
            "tight leggings": 0.30,
            "yoga pants": 0.30,
            "not tight baggy pants": 0.10,
            "a skirt": 0.30,
        }
        score = score_frame(sims, ["tight leggings", "yoga pants"], ["not tight baggy pants", "a skirt"])
        # neg 加权均值 (0.10*3 + 0.30)/4 = 0.15 < pos 0.30
        assert score is not None
        assert score["neg_mean"] == pytest.approx(0.15)
        assert score["is_positive"] is True


# ── 视频级聚合 ────────────────────────────────────────────


class TestAggregateVerdict:
    def test_pass_when_ratio_meets_threshold(self):
        frames = [{"is_positive": True}] * 3 + [{"is_positive": False}] * 1
        verdict = aggregate_verdict(frames, min_positive_ratio=0.5)
        assert verdict["verdict"] == "pass"
        assert verdict["classified"] == 4
        assert verdict["positive"] == 3
        assert verdict["ratio"] == 0.75

    def test_reject_when_below_threshold(self):
        frames = [{"is_positive": True}] * 1 + [{"is_positive": False}] * 3
        verdict = aggregate_verdict(frames, min_positive_ratio=0.5)
        assert verdict["verdict"] == "reject"

    def test_no_frames_insufficient(self):
        verdict = aggregate_verdict([], min_positive_ratio=0.5)
        assert verdict["verdict"] == "insufficient"
        assert verdict["ratio"] is None

    def test_none_entries_skipped(self):
        # classify 失败返回 None 的帧不参与统计
        frames = [{"is_positive": True}, None, {"is_positive": True}]
        verdict = aggregate_verdict(frames, min_positive_ratio=0.5)
        assert verdict["classified"] == 2
        assert verdict["positive"] == 2
        assert verdict["verdict"] == "pass"

    def test_boundary_ratio_equal_threshold_passes(self):
        frames = [{"is_positive": True}, {"is_positive": False}]
        verdict = aggregate_verdict(frames, min_positive_ratio=0.5)
        assert verdict["verdict"] == "pass"  # >= 阈值即通过


# ── 参数与配置接线 ────────────────────────────────────────


class TestPoseParams:
    def test_clip_defaults_disabled(self):
        params = PoseParams()
        assert params.clip_filter_enabled is False
        assert params.clip_sample_seconds == 30.0
        assert params.clip_min_positive_ratio == 0.5
        # 报告默认关闭：分类明细只用于人工核对，生产不落盘
        assert params.clip_save_reports is False

    def test_from_user_config_reads_clip_keys(self):
        params = PoseParams.from_user_config(
            {
                "clip_filter_enabled": True,
                "clip_min_positive_ratio": 0.8,
                "clip_sample_seconds": 15.0,
                "clip_positive_prompts": ["custom positive"],
                "clip_negative_prompts": [],
            }
        )
        assert params.clip_filter_enabled is True
        assert params.clip_min_positive_ratio == 0.8
        assert params.clip_sample_seconds == 15.0
        assert params.clip_positive_prompts == ["custom positive"]
        # 空负类回退默认组
        assert len(params.clip_negative_prompts) == len(DEFAULTS["clip_negative_prompts"])

    def test_defaults_mirror_settings_json(self):
        """pose_params.DEFAULTS 与 config/default_settings.json 的 pose_detection 段必须一致。"""
        import json

        path = os.path.join(os.path.dirname(__file__), "..", "config", "default_settings.json")
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)["pose_detection"]
        for key, value in DEFAULTS.items():
            if key in (
                "enabled",
                "model_path",
                "pose_model_path",
                "decode_backend",
                "clip_model_name",
                "clip_pretrained",
            ):
                continue  # 模型名/路径/后端选择是机器内部值，只存在于 Python 侧
            assert key in settings, f"缺键: {key}"
            assert settings[key] == value, f"默认值不一致: {key}"
