"""EvoPaw ASR 子包 —— 飞书语音转写基础设施.

本子包仅在主进程内使用，不暴露到 Skill / Sub-Agent 工具层。
"""

from evopaw.asr.models import AsrFailure, AsrResult

__all__ = ["AsrResult", "AsrFailure"]
