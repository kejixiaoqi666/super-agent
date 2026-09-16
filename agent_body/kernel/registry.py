"""Brain Registry —— 大脑内核注册表（插件机制）。

大脑 = 可替换内核。任何实现 BrainPort 契约的适配器类都可注册；
agent 主体只与注册表拿到的 BrainPort 交互，不关心具体内核。

设计要点：
  - Registry 只做「内核名 → 适配器类」的解析（插件表），
    实例化参数（data_dir/session/llm/kernel_path 等）由调用方 body 显式传入
    —— 因此不同 session 各自拿到独立实例，隔离不会被闭包工厂破坏。
  - 契约版本感知：适配器声明实现的 PORT_VERSION，与 agent 要求不符时拒绝注册。
  - 换内核 = 注册一个新适配器类；agent 主体代码零改动。
"""
from __future__ import annotations

import inspect
from typing import Dict

from .port import BrainPort, BRAIN_PORT_VERSION


class BrainRegistry:
    def __init__(self):
        self._classes: Dict[str, type] = {}

    def register(self, name: str, adapter_cls: type,
                 port_version: int = BRAIN_PORT_VERSION) -> None:
        """注册一个内核适配器类。

        name: 内核标识（如 "superbrain"）。
        adapter_cls: 实现 BrainPort 契约的类；其实例可用 build() 创建。
        port_version: 该内核实现的契约版本。不匹配 BRAIN_PORT_VERSION 时拒绝注册。
        """
        if not issubclass(adapter_cls, BrainPort):
            raise TypeError(
                f"kernel '{name}' must implement BrainPort, got {adapter_cls}")
        if inspect.isabstract(adapter_cls):
            raise TypeError(
                f"kernel '{name}' is an abstract BrainPort (incomplete contract "
                f"implementation); implement all abstract methods before registering")
        if port_version != BRAIN_PORT_VERSION:
            raise ValueError(
                f"kernel '{name}' implements port v{port_version}, "
                f"but agent requires v{BRAIN_PORT_VERSION}; update the adapter")
        self._classes[name] = adapter_cls

    def names(self) -> list:
        return list(self._classes)

    def build(self, name: str, **kwargs) -> BrainPort:
        """按名实例化一个适配器，kwargs 原样传给 __init__（含 data_dir/session/llm 等）。"""
        if name not in self._classes:
            raise KeyError(
                f"brain kernel '{name}' not registered; available: {self.names()}")
        return self._classes[name](**kwargs)

    def has(self, name: str) -> bool:
        return name in self._classes


_default_registry = BrainRegistry()


def default_registry() -> BrainRegistry:
    return _default_registry