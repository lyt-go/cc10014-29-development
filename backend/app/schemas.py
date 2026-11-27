"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class BatchReturnPayload(BaseModel):
    """监理批量退回整改：同一现场多份验收报告及其连带缺陷。"""
    acceptance_ids: list[int] = Field(default_factory=list)
    # 每份报告勾选的连带缺陷，key 为验收报告 id（字符串形式）
    defects_by_report: dict[str, str] = Field(default_factory=dict)
    site: str = ""
    deadline: str | None = None
    remark: str | None = None
