"""ShareGPT 对话格式导出。

输出格式兼容 LLaMA-Factory 的 ShareGPT DPO 格式：
{
  "conversations": [
    {"from": "human", "value": "<prompt>"}
  ],
  "chosen": {"from": "gpt", "value": "<chosen_response>"},
  "rejected": {"from": "gpt", "value": "<rejected_response>"}
}

配套生成 dataset_info.json 以便 LLaMA-Factory 直接加载。
"""

from __future__ import annotations

import json
from pathlib import Path

from ecoalign_forge.export.trl_format import prompt_messages
from ecoalign_forge.schemas.dpo import DPO_Pair


def to_sharegpt_dict(pair: DPO_Pair) -> dict:
    """将单条 DPO_Pair 转换为 ShareGPT 对话格式。"""
    return {
        "conversations": [
            {
                "from": {"system": "system", "user": "human", "assistant": "gpt"}[m["role"]],
                "value": m["content"],
            }
            for m in prompt_messages(pair)
        ],
        "chosen": {"from": "gpt", "value": pair.chosen},
        "rejected": {"from": "gpt", "value": pair.rejected},
    }


def export_sharegpt(
    pairs: list[DPO_Pair],
    output_path: str | Path,
) -> Path:
    """将 DPO 数据集导出为 ShareGPT 格式 JSON 文件。

    Args:
        pairs: DPO 偏好对列表
        output_path: 输出文件路径

    Returns:
        实际写入的文件路径
    """
    from ecoalign_forge.runtime.journal import atomic_write

    path = Path(output_path)
    records = [to_sharegpt_dict(pair) for pair in pairs]
    atomic_write(path, json.dumps(records, ensure_ascii=False, indent=2))

    return path


def export_dataset_info(
    output_dir: str | Path,
    *,
    dataset_name: str = "ecoalign_forge_dpo",
    file_name: str = "train_sharegpt.json",
) -> Path:
    """生成 LLaMA-Factory 的 dataset_info.json。

    Args:
        output_dir: 输出目录
        dataset_name: 数据集注册名
        file_name: 数据文件名

    Returns:
        dataset_info.json 文件路径
    """
    from ecoalign_forge.runtime.journal import atomic_write

    output_dir = Path(output_dir)

    info = {
        dataset_name: {
            "file_name": file_name,
            "formatting": "sharegpt",
            "ranking": True,
            "columns": {
                "messages": "conversations",
                "chosen": "chosen",
                "rejected": "rejected",
            },
        }
    }

    info_path = output_dir / "dataset_info.json"
    atomic_write(info_path, json.dumps(info, ensure_ascii=False, indent=2))

    return info_path
