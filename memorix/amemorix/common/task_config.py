"""插件本地 TaskConfig 垫片。

替代上游 ``src.config.model_configs.TaskConfig``。MaiBot 主线的 TaskConfig 承载
多模型路由（model_list / selection_strategy / slow_threshold 等）；本插件 LLM 统一走
``amemorix.llm_client.LLMClient``（OpenAI 兼容）或 ``AstrBotLLMClient``（astrbot
provider 桥），不需要多模型路由，仅保留被剥离后的 core 代码用 ``getattr`` 读取的字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TaskConfig:
    """轻量任务配置，字段与上游 TaskConfig 对齐（仅取插件实际用到的子集）。

    被剥离的 ``model_routing`` / ``summary_importer`` / ``episode_segmentation``
    通过 ``getattr(task_config, "temperature", None)`` 等方式读取；本类保证这些
    访问不会因属性缺失而崩溃，真实 LLM 调度由 ``LLMClient`` 用 model_name +
    temperature + max_tokens 完成。
    """

    model_name: str = ""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # 以下字段仅用于兼容上游 ``getattr`` 读取，插件路径不参与多模型路由。
    model_list: List[str] = field(default_factory=list)
    slow_threshold: float = 0.0
    selection_strategy: str = ""
    hard_timeout: float = 0.0


def build_task_config(
    *,
    model_name: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> TaskConfig:
    """便捷工厂：从插件配置构造一个最小 TaskConfig。"""

    return TaskConfig(
        model_name=str(model_name or "").strip(),
        temperature=temperature,
        max_tokens=max_tokens,
    )
