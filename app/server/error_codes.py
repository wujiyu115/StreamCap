"""API 错误码：后端 detail 返回 "错误码|附加信息"，前端按码查 i18n 翻译。

附加信息直接拼在码后（如文件名列表），展示时原样带出。
"""

# 通用
ACCESS_DENIED = "err.accessDenied"
NOT_FOUND = "err.notFound"
AUTH_REQUIRED = "err.authRequired"
INVALID_CREDENTIALS = "err.invalidCredentials"
WRONG_OLD_PASSWORD = "err.wrongOldPassword"

# 媒体
FILE_NOT_FOUND = "err.fileNotFound"
BAD_RANGE = "err.badRange"

# 录制任务
RECORDING_NOT_FOUND = "err.recordingNotFound"
URL_REQUIRED = "err.urlRequired"
UNSUPPORTED_URL = "err.unsupportedUrl"
NO_FIELDS_TO_UPDATE = "err.noFieldsToUpdate"

# 人体识别
POSE_MANAGER_UNAVAILABLE = "err.poseUnavailable"
POSE_NO_VIDEOS = "err.poseNoVideos"
POSE_FILES_WRITING = "err.poseFilesWriting"
POSE_TASK_RUNNING = "err.poseTaskRunning"
POSE_QUEUE_FULL = "err.poseQueueFull"
POSE_NO_RUNNING_TASK = "err.poseNoRunningTask"
