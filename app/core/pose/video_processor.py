"""视频处理：采样检测 → 人物区间合并 → ffmpeg 无重编码切割合并。

移植自 video_pose/app/video_processor.py。改造点：
- 参数由 PoseParams 注入，不依赖全局配置；
- 删除 debug 落帧（save_result）与 move_output_to_input（本平台输出固定在
  <媒体根>/pose_output/<相对路径>/）。
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime

import cv2
import ffmpeg

try:
    import av
except ImportError:  # PyAV 为可选依赖，缺失时退回 OpenCV 精确 seek
    av = None

from .pose_params import PoseParams

logger = logging.getLogger("video_pose")


def format_duration(duration) -> str:
    if isinstance(duration, (int, float)):
        total_seconds = int(duration)
    else:
        total_seconds = int(duration.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}小时")
    if minutes > 0:
        parts.append(f"{minutes}分钟")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}秒")
    return "".join(parts)


def safe_remove_file(file_path: str) -> bool:
    try:
        if not os.path.exists(file_path):
            logger.warning(f"要删除的文件不存在: {file_path}")
            return False
        if not os.access(file_path, os.W_OK):
            logger.error(f"没有删除文件的权限: {file_path}")
            return False
        os.remove(file_path)
        return True
    except Exception as e:
        logger.error(f"删除文件失败: {file_path}, 错误: {e}")
        return False


def get_output_subdir(file_path: str, media_root: str | None, sub_output_dir: str) -> str:
    """输出子目录保持原视频相对 media_root 的目录结构。"""
    if not media_root:
        return sub_output_dir
    abs_input_dir = os.path.abspath(media_root)
    abs_file_path = os.path.abspath(file_path)
    if abs_file_path.startswith(abs_input_dir + os.sep):
        rel_dir = os.path.dirname(os.path.relpath(abs_file_path, abs_input_dir))
        if rel_dir:
            sub_sub_output_dir = os.path.join(sub_output_dir, rel_dir)
            os.makedirs(sub_sub_output_dir, exist_ok=True)
            return sub_sub_output_dir
    return sub_output_dir


class VideoProcessor:
    def __init__(self, detector, params: PoseParams, media_root: str | None = None):
        self.detector = detector
        self.params = params
        self.media_root = media_root
        self.frame_seconds = float(params.frame_seconds)
        self.merge_threshold_seconds = float(params.merge_threshold_seconds)
        self.min_segment_seconds = float(params.min_segment_seconds)
        self.merge_clips = bool(params.merge_clips)
        self.delete_original_video = bool(params.delete_original_video)
        self.move_output_to_input = bool(params.move_output_to_input)
        self.merged_suffix = params.merged_suffix or "_merged"
        self.decode_backend = params.decode_backend

    def process_video_file(self, video_path, video_idx=0, total_videos=1, progress_cb=None, stop_check=None):
        """处理单个视频，返回 (处理帧数, 采样帧数, 原始区间数, 合并区间数, 片段数)。"""
        model = self.detector.model

        frames, saved, person_segments = self.process_video(
            video_path,
            model,
            person_cls=0,
            video_idx=video_idx,
            total_videos=total_videos,
            batch_size=self.detector.batch_size,
            frame_seconds=self.frame_seconds,
            progress_cb=progress_cb,
            stop_check=stop_check,
        )

        merged_segments = self.merge_segments(
            person_segments, self.merge_threshold_seconds, self.min_segment_seconds
        )
        logger.info(
            f"时序聚合完成，原始区间数: {len(person_segments)}，合并后区间数: {len(merged_segments)}"
        )

        clip_paths = []
        if merged_segments:
            clip_paths = self.clip_video(video_path, merged_segments)

        moved_paths = []
        if self.move_output_to_input and clip_paths:
            moved_paths = self._move_output_to_input(video_path)

        if self.delete_original_video:
            safe_remove_file(video_path)

        return frames, saved, len(person_segments), len(merged_segments), len(clip_paths)

    def _move_output_to_input(self, video_path: str) -> list[str]:
        """把当前视频的剪辑产物按原目录结构移回视频所在目录。

        与 delete_original_video 配合：原视频删除、产物与录制文件同目录，
        便于媒体库直接浏览。
        """
        moved_paths = []
        output_dir = self.output_dir_for(video_path)
        video_src = get_output_subdir(video_path, self.media_root, output_dir)
        if not os.path.isdir(video_src) or not self.media_root:
            return moved_paths

        abs_media_root = os.path.abspath(self.media_root)
        rel_dir = os.path.dirname(os.path.relpath(os.path.abspath(video_path), abs_media_root))
        video_dst = os.path.join(abs_media_root, rel_dir) if rel_dir else abs_media_root

        for item in os.listdir(video_src):
            src = os.path.join(video_src, item)
            dst = os.path.join(video_dst, item)
            if os.path.isfile(src):
                shutil.move(src, dst)
                moved_paths.append(os.path.abspath(dst))
                logger.info(f"移动剪辑产物到视频目录: {dst}")

        # 产物全部移走后清理空的输出目录树
        try:
            for root, dirs, files in os.walk(video_src, topdown=False):
                if not os.listdir(root):
                    os.rmdir(root)
            os.rmdir(output_dir)
        except OSError:
            pass

        return moved_paths

    def output_dir_for(self, video_path: str) -> str:
        """产物输出根目录。

        固定在应用内部工作目录（容器内 /app/pose_output），不放媒体根——
        避免 pose_output 出现在宿主机媒体库目录里；产物随后仍会按
        move_output_to_input 移回视频所在目录。
        """
        from ...core.runtime.paths import user_data_dir

        return os.path.join(str(user_data_dir), self.params.video_output_dir or "pose_output")

    def merge_segments(self, segments, merge_threshold_seconds, min_segment_seconds):
        """合并相邻的人物区间，过滤过短区间。"""
        if not segments:
            return []

        sorted_segments = sorted(segments, key=lambda x: x["start_frame"])

        merged_segments = [sorted_segments[0]]
        for current in sorted_segments[1:]:
            previous = merged_segments[-1]
            fps = previous.get("fps", 30)
            merge_threshold_frames = int(merge_threshold_seconds * fps)
            if current["start_frame"] - previous["end_frame"] <= merge_threshold_frames:
                previous["end_frame"] = max(previous["end_frame"], current["end_frame"])
                previous["end_time"] = previous["end_frame"] / fps
            else:
                merged_segments.append(current)

        filtered_segments = []
        for segment in merged_segments:
            fps = segment.get("fps", 30)
            min_segment_frames = int(min_segment_seconds * fps)
            segment_length = segment["end_frame"] - segment["start_frame"]
            if segment_length >= min_segment_frames:
                filtered_segments.append(segment)
                logger.info(
                    f"保留人物区间: {segment['start_time']:.2f}s - {segment['end_time']:.2f}s "
                    f"(长度: {segment_length / fps:.2f} 秒)"
                )
            else:
                logger.info(
                    f"过滤短人物区间: {segment['start_time']:.2f}s - {segment['end_time']:.2f}s "
                    f"(长度: {segment_length / fps:.2f} 秒)"
                )

        return filtered_segments

    def _open_av_container(self, video_path):
        if self.decode_backend == "opencv":
            return None
        if av is None:
            if self.decode_backend == "pyav":
                raise RuntimeError("decode_backend=pyav 但未安装 av")
            return None
        try:
            container = av.open(video_path)
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            return (container, stream)
        except Exception as e:
            if self.decode_backend == "pyav":
                raise
            logger.warning(f"PyAV 打开失败，本次回退 OpenCV 精确 seek: {e}")
            return None

    def process_video(
        self,
        video_path,
        model,
        person_cls=0,
        video_idx=0,
        total_videos=1,
        batch_size=4,
        frame_seconds=1.0,
        progress_cb=None,
        stop_check=None,
    ):
        start_time = datetime.now()
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error(f"无法打开视频: {video_path}")
            return 0, 0, []

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        video_name = os.path.basename(video_path)
        logger.info(f"开始处理视频 [{video_idx + 1}/{total_videos}]: {video_name}")
        if fps > 0:
            logger.info(f"视频FPS: {fps:.2f}, 视频时长: {format_duration(total_frames / fps)}")

        frame_idx = 0
        saved_count = 0
        person_segments = []
        current_segment = None
        last_person_frame = None

        av_ctx = self._open_av_container(video_path)

        if fps <= 0:
            logger.error(f"无法获取视频FPS，跳过: {video_name}")
            cap.release()
            if av_ctx is not None:
                av_ctx[0].close()
            return 0, 0, []

        frame_interval = max(1, int(frame_seconds * fps))
        frame_indices = list(range(0, total_frames, frame_interval))

        for i in range(0, len(frame_indices), batch_size):
            if stop_check is not None and stop_check():
                logger.info(f"收到停止请求，中止处理视频: {video_name}")
                if current_segment is not None:
                    current_segment["end_frame"] = max(frame_idx, current_segment["start_frame"])
                    current_segment["end_time"] = current_segment["end_frame"] / fps
                    person_segments.append(current_segment)
                    current_segment = None
                break

            batch_indices = frame_indices[i : i + batch_size]
            batch_frames = []
            batch_frame_idxs = []

            for idx in batch_indices:
                if av_ctx is not None:
                    frame = _av_grab_frame(av_ctx, idx / fps)
                    if frame is None:
                        continue
                else:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                    ret, frame = cap.read()
                    if not ret:
                        continue
                batch_frames.append(frame)
                batch_frame_idxs.append(idx)

            if not batch_frames:
                continue

            current_max_idx = batch_indices[-1]
            video_progress = current_max_idx / total_frames * 100 if total_frames > 0 else 0
            total_progress = (video_idx + video_progress / 100) / total_videos * 100
            if progress_cb is not None:
                progress_cb(
                    {
                        "video_name": video_name,
                        "video_idx": video_idx,
                        "total_videos": total_videos,
                        "video_percent": round(video_progress, 1),
                        "total_percent": round(total_progress, 1),
                    }
                )

            try:
                results = model(
                    batch_frames,
                    classes=[person_cls],
                    verbose=False,
                    imgsz=self.detector.imgsz,
                )

                for j, result in enumerate(results):
                    frame_idx = batch_frame_idxs[j]
                    has_person, _img, max_box_ratio, _coords, _kp = self.detector.check_person(result)

                    if has_person is False:
                        if current_segment is not None:
                            # 关闭区间：结束点用已记录的候选（上一有人采样点 + 一个采样间隔，
                            # 人物消失的精确边界由 merge_threshold 兜底），保底不早于起点
                            end_frame = max(
                                current_segment["start_frame"],
                                current_segment["end_frame"] or frame_idx,
                            )
                            current_segment["end_frame"] = end_frame
                            current_segment["end_time"] = end_frame / fps
                            person_segments.append(current_segment)
                            current_segment = None
                            last_person_frame = None
                    else:
                        if current_segment is None:
                            current_segment = {
                                "start_frame": frame_idx,
                                "start_time": frame_idx / fps,
                                "end_frame": None,
                                "end_time": None,
                                "fps": fps,
                            }
                        # 有人区间的结束候选：本采样点到下一采样点之间仍可能有人
                        end_candidate = min(frame_idx + frame_interval, total_frames - 1)
                        current_segment["end_frame"] = end_candidate
                        current_segment["end_time"] = end_candidate / fps
                        last_person_frame = frame_idx
                        logger.debug(f"  - 检测到人，最大边界框占比: {max_box_ratio:.2%}")

                    saved_count += 1
            except Exception as e:
                logger.error(f"批处理帧时发生错误: {e}")

        frame_idx = total_frames - 1 if total_frames > 0 else 0

        if current_segment is not None:
            current_segment["end_frame"] = frame_idx
            current_segment["end_time"] = current_segment["end_frame"] / fps
            person_segments.append(current_segment)

        cap.release()
        if av_ctx is not None:
            av_ctx[0].close()

        process_duration = datetime.now() - start_time
        logger.info(
            f"视频 {video_name} 处理完成，处理了 {saved_count} 个采样帧，"
            f"检测到 {len(person_segments)} 个人物区间，处理时长: {format_duration(process_duration)}"
        )
        return frame_idx, saved_count, person_segments

    def clip_video(self, video_path, segments, video_format="mp4"):
        """根据人物区间切割视频。输出保持相对 media_root 的目录结构。"""
        if not segments:
            logger.info("没有检测到有效的人物区间，跳过视频切割")
            return []

        output_dir = self.output_dir_for(video_path)
        os.makedirs(output_dir, exist_ok=True)
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        sub_output_dir = get_output_subdir(video_path, self.media_root, output_dir)

        logger.info(f"开始切割视频: {video_path} 输出目录: {sub_output_dir}")

        # concat demuxer 的 inpoint/outpoint 一次完成「切 + 合」，中间片段不落盘。
        # 失败回退到逐段切割后合并。单区间也走此路径，产物统一命名 *_merged。
        if self.merge_clips and segments:
            merged_paths = self._clip_and_merge(
                video_path,
                segments,
                sub_output_dir,
                video_name,
                video_format,
                self.delete_original_video,
            )
            if merged_paths:
                return merged_paths
            logger.warning("一次性切割合并失败，回退到逐段切割后合并")

        clip_paths = []
        for i, segment in enumerate(segments):
            output_filename = f"{video_name}_clip_{i + 1:03d}.{video_format}"
            output_path = os.path.join(sub_output_dir, output_filename)

            start_time = segment["start_time"]
            duration = segment["end_time"] - segment["start_time"]

            try:
                (
                    ffmpeg.input(video_path, ss=start_time, t=duration)
                    .output(output_path, codec="copy")
                    .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
                )
                logger.info(
                    f"成功切割视频片段 {i + 1}: 第 {format_duration(start_time)} - "
                    f"{format_duration(segment['end_time'])}, 保存至 {output_path}"
                )
                clip_paths.append(output_path)
            except ffmpeg.Error as e:
                logger.error(f"切割视频片段 {i + 1} 失败: {e.stderr.decode() if e.stderr else e}")

        if self.merge_clips and len(clip_paths) > 1:
            try:
                merged_filename = f"{video_name}{self.merged_suffix}.{video_format}"
                merged_output_path = os.path.join(sub_output_dir, merged_filename)

                temp_list_path = os.path.join(sub_output_dir, f"temp_list_{video_name}.txt")
                with open(temp_list_path, "w") as f:
                    for clip_path in clip_paths:
                        f.write(f"file '{os.path.abspath(clip_path)}'\n")

                (
                    ffmpeg.input(temp_list_path, format="concat", safe=0)
                    .output(merged_output_path, codec="copy")
                    .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
                )
                os.remove(temp_list_path)

                logger.info(f"成功合并 {len(clip_paths)} 个视频片段，保存至 {merged_output_path}")

                merged_clip_path = merged_output_path
                original_clips = clip_paths[:]
                clip_paths = [merged_clip_path]
                for clip_path in original_clips:
                    if safe_remove_file(clip_path):
                        logger.info(f"已删除原始视频片段: {clip_path}")

                if self.delete_original_video:
                    if safe_remove_file(video_path):
                        logger.info(f"已删除原始视频: {video_path}")
            except ffmpeg.Error as e:
                logger.error(f"合并视频片段失败: {e.stderr.decode() if e.stderr else e}")
            except Exception as e:
                logger.error(f"合并视频片段时发生错误: {e}")

        return clip_paths

    def _clip_and_merge(
        self,
        video_path,
        segments,
        sub_output_dir,
        video_name,
        video_format,
        delete_original_video,
    ):
        """concat demuxer 的 inpoint/outpoint 一次完成切割与合并。

        成功时返回 [合并后视频路径]，失败返回 [] 由调用方回退。
        """
        merged_filename = f"{video_name}{self.merged_suffix}.{video_format}"
        merged_output_path = os.path.join(sub_output_dir, merged_filename)
        temp_list_path = os.path.join(sub_output_dir, f"temp_list_{video_name}.txt")

        abs_video_path = os.path.abspath(video_path)
        try:
            with open(temp_list_path, "w") as f:
                for segment in segments:
                    f.write(f"file '{abs_video_path}'\n")
                    f.write(f"inpoint {segment['start_time']}\n")
                    f.write(f"outpoint {segment['end_time']}\n")

            (
                ffmpeg.input(temp_list_path, format="concat", safe=0)
                .output(merged_output_path, codec="copy", avoid_negative_ts="make_zero")
                .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
            )
        except ffmpeg.Error as e:
            logger.error(f"一次性切割合并失败: {e.stderr.decode() if e.stderr else e}")
            return []
        except Exception as e:
            logger.error(f"一次性切割合并时发生错误: {e}")
            return []
        finally:
            safe_remove_file(temp_list_path)

        total_seconds = sum(s["end_time"] - s["start_time"] for s in segments)
        logger.info(
            f"成功将 {len(segments)} 个人物区间一次切割合并（共 {format_duration(total_seconds)}），"
            f"保存至 {merged_output_path}"
        )

        if delete_original_video:
            if safe_remove_file(video_path):
                logger.info(f"已删除原始视频: {video_path}")

        return [merged_output_path]


def _av_grab_frame(ctx, target_sec):
    """取 target_sec 处的帧（BGR ndarray），失败返回 None。"""
    container, stream = ctx
    try:
        target_pts = int(round(target_sec / stream.time_base))
        container.seek(target_pts, stream=stream)
        prev = None
        for frame in container.decode(stream):
            if frame.pts is None:
                return frame.to_ndarray(format="bgr24")
            if frame.pts >= target_pts:
                if prev is not None and (target_pts - prev.pts) < (frame.pts - target_pts):
                    return prev.to_ndarray(format="bgr24")
                return frame.to_ndarray(format="bgr24")
            prev = frame
    except Exception as e:
        logger.warning(f"PyAV 取帧失败（{target_sec:.2f}s），跳过该采样点: {e}")
    return None
