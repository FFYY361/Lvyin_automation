"""Secret-safe, actionable diagnostics for auto_preview CLI failures."""

from __future__ import annotations

import builtins
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from preview import (
    PreviewError,
    PreviewValidationError,
    TemplateContractError,
    UnsafeHtml,
)
from thufootball import (
    AuthenticationError,
    BatchQueryError,
    ConfigurationError,
    DataConflict,
    InvalidResponse,
    QueryValidationError,
    RateLimited,
    SchemaError,
    THUFootballError,
    Timeout,
)
from thufootball import (
    PermissionError as THUFootballPermissionError,
)
from wechat_official import (
    DraftValidationError,
    DraftWriteError,
    MediaUploadError,
    WechatArticleError,
    WechatAuthenticationError,
    WechatConfigurationError,
    WechatPermissionError,
    WechatRateLimited,
    WechatTimeout,
)

from .errors import ArtifactValidationError, NoGamesForDate, PipelineError


@dataclass(frozen=True, slots=True)
class _Diagnosis:
    category: str
    suggestions: tuple[str, ...]


_SECRET_NAMES = (
    r"openid|session[_-]?key|access[_-]?token|app[_-]?secret|"
    r"authorization|token|secret"
)
_SECRET_ASSIGNMENT = re.compile(rf"(?i)(\b(?:{_SECRET_NAMES})\b\s*=\s*)[^&\s,;]+")
_SECRET_MAPPING_VALUE = re.compile(
    rf"""(?i)((?:["'](?:{_SECRET_NAMES})["']|\b(?:{_SECRET_NAMES})\b)"""
    r"""\s*:\s*["']?)[^"'\s,;}}]+"""
)


def _safe_message(error: BaseException) -> str:
    message = str(error) or "<无错误信息>"
    message = _SECRET_ASSIGNMENT.sub(r"\1<redacted>", message)
    return _SECRET_MAPPING_VALUE.sub(r"\1<redacted>", message)


_DIAGNOSIS_RULES: tuple[
    tuple[type[BaseException] | tuple[type[BaseException], ...], _Diagnosis], ...
] = (
    (
        QueryValidationError,
        _Diagnosis(
            "本地查询校验错误",
            ("检查日期、赛事类型、赛事 ID 范围及命令行参数。",),
        ),
    ),
    (
        ConfigurationError,
        _Diagnosis(
            "本地配置错误",
            (
                "确认 THUFOOTBALL_OPENID 与 THUFOOTBALL_SESSION_KEY 已同时配置。",
                "不要把凭据值粘贴到日志；修改环境变量后重新打开终端再试。",
            ),
        ),
    ),
    (
        AuthenticationError,
        _Diagnosis(
            "认证错误",
            (
                "当前 THUFootball 凭据已失效或被服务端拒绝，请重新获取并更新凭据。",
                "确认 openid 与 session_key 来自同一次登录会话。",
            ),
        ),
    ),
    (
        THUFootballPermissionError,
        _Diagnosis(
            "权限错误",
            (
                "确认当前 THUFootball 账号能够查看失败赛事，并已获得相应赛事读取权限。",
                "若账号权限刚变更，请刷新本地 THUFootball 凭据后重试。",
            ),
        ),
    ),
    (
        Timeout,
        _Diagnosis(
            "网络超时",
            (
                "检查网络、DNS、代理和防火墙后重试；该请求未完成写操作。",
                "若持续超时，稍后重试并确认 THUFootball 服务是否可访问。",
            ),
        ),
    ),
    (
        RateLimited,
        _Diagnosis(
            "赛事接口限流",
            ("等待一段时间后重试，避免短时间内重复运行同一批查询。",),
        ),
    ),
    (
        (SchemaError, DataConflict),
        _Diagnosis(
            "远端赛事数据校验错误",
            (
                "服务端数据结构或同一比赛记录存在冲突；保留日志并核对对应赛事/比赛 ID。",
                "不要使用 --override 绕过数据校验。",
            ),
        ),
    ),
    (
        THUFootballError,
        _Diagnosis(
            "赛事数据错误",
            ("根据阶段、赛事 ID 和原始信息核对查询范围后重试。",),
        ),
    ),
    (
        ArtifactValidationError,
        _Diagnosis(
            "本地产物校验错误",
            (
                "根据失败阶段检查 source.json、previews/*.md、weather.json、config.json 和 run.json 的结构与字段路径。",
                "article/ 属于可重建产物，会在 source、正文 Markdown、当前天气、人员配置、模板或封面变化时自动覆盖；--override 会重新查询 source，并保留已填写的标题、作者和正文。",
            ),
        ),
    ),
    (
        NoGamesForDate,
        _Diagnosis(
            "赛事数据为空",
            ("确认日期、赛事类型和当前赛事配置；当天所有状态的比赛都会被接受。",),
        ),
    ),
    (
        PipelineError,
        _Diagnosis(
            "Pipeline 本地错误",
            ("根据失败阶段检查对应运行目录中的 source、article、run 和 draft 文件。",),
        ),
    ),
    (
        PreviewValidationError,
        _Diagnosis(
            "文章源数据校验错误",
            (
                "按错误路径检查 source.json、正文 Markdown、weather.json 或 config.json；已有文件不会被自动修复或覆盖。",
            ),
        ),
    ),
    (
        (TemplateContractError, UnsafeHtml),
        _Diagnosis(
            "文章模板或 HTML 安全校验错误",
            ("检查模板结构和渲染内容；不要通过关闭校验来继续发布。",),
        ),
    ),
    (
        PreviewError,
        _Diagnosis(
            "文章生成错误",
            ("根据失败阶段检查 source.json 和所选模板。",),
        ),
    ),
    (
        WechatConfigurationError,
        _Diagnosis(
            "公众号本地配置错误",
            ("确认公众号 AppID/AppSecret 等必需环境变量已配置，但不要输出其值。",),
        ),
    ),
    (
        WechatAuthenticationError,
        _Diagnosis(
            "公众号认证错误",
            ("检查公众号凭据是否有效，并确认服务器出口 IP 已加入公众号白名单。",),
        ),
    ),
    (
        WechatPermissionError,
        _Diagnosis(
            "公众号权限错误",
            ("确认公众号类型及账号已开通素材上传和草稿箱接口权限。",),
        ),
    ),
    (
        WechatRateLimited,
        _Diagnosis(
            "公众号接口限流",
            ("等待限流窗口恢复后再试，避免使用 --override 重复创建草稿。",),
        ),
    ),
    (
        WechatTimeout,
        _Diagnosis(
            "公众号网络超时",
            ("检查到微信 API 的网络连通性后重试，并先核对 draft.json 避免重复草稿。",),
        ),
    ),
    (
        DraftValidationError,
        _Diagnosis(
            "公众号草稿本地校验错误",
            ("检查文章标题、正文、作者、封面和媒体引用是否符合草稿接口要求。",),
        ),
    ),
    (
        (MediaUploadError, DraftWriteError),
        _Diagnosis(
            "公众号接口写入错误",
            ("根据微信错误码检查素材、封面、正文和接口权限，再核对 draft.json。",),
        ),
    ),
    (
        WechatArticleError,
        _Diagnosis(
            "公众号接口错误",
            ("根据微信错误码、失败阶段和 draft.json 判断是否可以安全重试。",),
        ),
    ),
    (
        builtins.PermissionError,
        _Diagnosis(
            "本地文件权限错误",
            (
                "确认运行目录和目标文件可读写，且文件未被 Excel、编辑器或同步软件锁定。",
                "不要使用管理员权限绕过目录归属问题；优先修正文件权限。",
            ),
        ),
    ),
    (
        FileNotFoundError,
        _Diagnosis(
            "本地文件缺失",
            ("检查报错路径、当前参数及仓库文件是否完整。",),
        ),
    ),
    (
        OSError,
        _Diagnosis(
            "本地文件系统错误",
            ("检查磁盘空间、路径长度、文件锁和运行目录读写权限。",),
        ),
    ),
    (
        ValueError,
        _Diagnosis(
            "本地数据校验错误",
            ("检查命令行参数和本地产物内容是否符合要求。",),
        ),
    ),
)

_UNCLASSIFIED_DIAGNOSIS = _Diagnosis(
    "内部未分类错误",
    ("保留终端中的完整错误输出，并根据异常类型定位代码或提交问题。",),
)


def _diagnose(error: BaseException) -> _Diagnosis:
    if isinstance(error, InvalidResponse):
        if error.retryable or error.stage == "http":
            return _Diagnosis(
                "网络或远端接口错误",
                (
                    "检查网络、DNS、代理和防火墙后重试。",
                    "若持续失败，确认 THUFootball 接口状态并查看下方底层异常类型。",
                ),
            )
        return _Diagnosis(
            "远端响应错误",
            (
                "THUFootball 返回了无法安全解析的响应；保留日志并稍后重试。",
                "如重复出现，请根据赛事 ID 联系数据源维护者。",
            ),
        )
    for error_types, diagnosis in _DIAGNOSIS_RULES:
        if isinstance(error, error_types):
            return diagnosis
    return _UNCLASSIFIED_DIAGNOSIS


def _stage(error: BaseException, fallback: str | None = None) -> str:
    value = getattr(error, "stage", None)
    return value if isinstance(value, str) and value else fallback or "unknown"


def _context(error: BaseException) -> tuple[str, ...]:
    values: list[str] = []
    for label, attribute in (
        ("tournament_id", "tournament_id"),
        ("game_id", "game_id"),
        ("error_code", "error_code"),
        ("observed_ip", "observed_ip"),
        ("errno", "errno"),
        ("path", "filename"),
    ):
        value = getattr(error, attribute, None)
        if value is not None:
            values.append(f"{label}={value}")
    cause = error.__cause__
    if cause is not None:
        values.append(f"底层异常={type(cause).__name__}")
    return tuple(values)


def _retryable(error: BaseException) -> bool | None:
    value = getattr(error, "retryable", None)
    return value if isinstance(value, bool) else None


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def failure_lines(
    error: BaseException,
    *,
    stage: str | None = None,
    log_path: Path | None = None,
) -> tuple[str, ...]:
    """Return a multi-line, secret-safe diagnostic suitable for CLI logs."""

    if isinstance(error, BatchQueryError):
        child_diagnoses = [_diagnose(child) for child in error.failures.values()]
        categories = _unique([item.category for item in child_diagnoses])
        category_detail = "、".join(categories)
        lines = [
            "✗ auto_preview 失败",
            f"  类别：批量赛事查询错误（{category_detail}）",
            f"  阶段：{_stage(error, stage)}",
            f"  异常：{type(error).__name__}",
            (
                "  原因："
                f"{len(error.failures)} 个赛事查询失败，"
                f"赛事 IDs={error.failed_tournament_ids}"
            ),
            "  子错误：",
        ]
        for tournament_id, child in error.failures.items():
            diagnosis = _diagnose(child)
            context = _context(child)
            context_text = f"；上下文={', '.join(context)}" if context else ""
            lines.append(
                "    - "
                f"赛事 {tournament_id}：{diagnosis.category}；"
                f"error={type(child).__name__}；stage={_stage(child)}；"
                f"message={_safe_message(child)}{context_text}"
            )
        retryable = _retryable(error)
        if retryable is not None:
            lines.append(f"  可重试：{'是' if retryable else '否'}")
        suggestions = _unique(
            [
                suggestion
                for diagnosis in child_diagnoses
                for suggestion in diagnosis.suggestions
            ]
        )
    else:
        diagnosis = _diagnose(error)
        lines = [
            "✗ auto_preview 失败",
            f"  类别：{diagnosis.category}",
            f"  阶段：{_stage(error, stage)}",
            f"  异常：{type(error).__name__}",
            f"  原因：{_safe_message(error)}",
        ]
        context = _context(error)
        if context:
            lines.append(f"  上下文：{', '.join(context)}")
        retryable = _retryable(error)
        if retryable is not None:
            lines.append(f"  可重试：{'是' if retryable else '否'}")
        suggestions = diagnosis.suggestions

    if suggestions:
        lines.append("  建议：")
        lines.extend(
            f"    {index}. {value}" for index, value in enumerate(suggestions, 1)
        )
    if log_path is not None:
        lines.append(f"  完整日志：{log_path}")
    return tuple(lines)


def log_failure(
    logger: logging.Logger,
    error: BaseException,
    *,
    stage: str | None = None,
    log_path: Path | None = None,
) -> None:
    logger.error("%s", "\n".join(failure_lines(error, stage=stage, log_path=log_path)))
