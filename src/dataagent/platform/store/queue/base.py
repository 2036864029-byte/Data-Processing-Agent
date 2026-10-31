"""队列最小语义抽象接口（research D6）。

至少一次：消息未确认前不丢失，消费方可被接管；手动确认：处理成功后显式 ACK；
死信：毒丸 / 多次失败的消息移入死信流。应用层用 visible_at 实现延迟与优先级，
本层不做延迟消息与优先级队列。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ReceivedMessage:
    """从队列接收到的消息（未 ACK 前保留在待确认列表）。"""

    id: str
    payload: bytes


class Queue(ABC):
    """队列最小语义抽象：enqueue / receive / ack / dead_letter。"""

    @abstractmethod
    def enqueue(self, payload: bytes) -> str:
        """入队，返回消息 id。"""

    @abstractmethod
    def receive(self, max_count: int = 1) -> list[ReceivedMessage]:
        """接收一批新消息（至少一次：未确认不丢失）。"""

    @abstractmethod
    def ack(self, message_id: str) -> None:
        """手动确认已处理（成功后调用；重复确认幂等）。"""

    @abstractmethod
    def dead_letter(self, message_id: str) -> None:
        """将消息移入死信流（毒丸 / 多次失败后调用）。"""
