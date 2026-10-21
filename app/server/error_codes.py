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
VALIDITY_CHECK_UNAVAILABLE = "err.validityUnavailable"

# 抖音关注列表
DOUYIN_COOKIE_MISSING = "err.douyinCookieMissing"
DOUYIN_LOGIN_REQUIRED = "err.douyinLoginRequired"
DOUYIN_UPSTREAM_ERROR = "err.douyinUpstreamError"
DOUYIN_FOLLOWING_UNAVAILABLE = "err.douyinFollowingUnavailable"
# 扫码登录：本部署没带浏览器组件 / 浏览器起来了但没拿到码
DOUYIN_LOGIN_UNAVAILABLE = "err.douyinLoginUnavailable"
DOUYIN_LOGIN_FAILED = "err.douyinLoginFailed"

# 人体识别
POSE_MANAGER_UNAVAILABLE = "err.poseUnavailable"
POSE_NO_VIDEOS = "err.poseNoVideos"
POSE_FILES_WRITING = "err.poseFilesWriting"
POSE_TASK_RUNNING = "err.poseTaskRunning"
POSE_QUEUE_FULL = "err.poseQueueFull"
POSE_NO_RUNNING_TASK = "err.poseNoRunningTask"

# 设置
SETTINGS_VERSION_MISMATCH = "err.settingsVersionMismatch"
SETTINGS_STALE_CLIENT = "err.settingsStaleClient"
