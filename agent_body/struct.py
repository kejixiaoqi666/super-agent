"""结构化输出 Struct —— JSON Schema 校验工具入参 / LLM 结构化结果。

成熟 agent 普遍支持"结构化输出"：工具调用入参按 schema 校验、LLM 返回按 schema 强约束。
这里实现一个轻量 JSON Schema 子集校验器（无外部依赖）：
  - validate(data, schema) -> (ok, errors)
  - 支持 type / required / enum / properties / items / const / min/max
  - 与 jsonschema 常用子集语义对齐，够覆盖 agent 工具与 LLM 输出校验

Schema 示例：
  {"type":"object", "required":["path"],
   "properties":{"path":{"type":"string"}, "recursive":{"type":"boolean"}}}
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# 支持的 JSON Schema 关键字（子集，够 agent 用）
_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


class SchemaError(ValueError):
    pass


def _type_matches(value: Any, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    return False


def _validate(value: Any, schema: Dict, path: str, errors: List[str]) -> None:
    if not isinstance(schema, dict):
        errors.append(f"{path}: schema 必须是对象")
        return
    # type 检查
    stype = schema.get("type")
    if stype is not None:
        if stype not in _TYPES:
            errors.append(f"{path}: 不支持的 type {stype!r}")
        elif not _type_matches(value, stype):
            errors.append(f"{path}: 期望 {stype}，得到 {type(value).__name__}")
            return  # 类型错就停，避免误判后续字段
    # const
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: 期望 const {schema['const']!r}，得到 {value!r}")
    # enum
    if "enum" in schema and isinstance(schema["enum"], list) and value not in schema["enum"]:
        errors.append(f"{path}: 值 {value!r} 不在 enum {schema['enum']}")
    # 数值范围
    for key, cmp_op, label in (("minimum", lambda v, c: v < c, "minimum"),
                               ("maximum", lambda v, c: v > c, "maximum"),
                               ("exclusiveMinimum", lambda v, c: v <= c, "exclusiveMinimum"),
                               ("exclusiveMaximum", lambda v, c: v >= c, "exclusiveMaximum")):
        if key in schema and isinstance(value, (int, float)):
            try:
                if cmp_op(value, schema[key]):
                    errors.append(f"{path}: 值 {value} 违反 {label}={schema[key]}")
            except TypeError:
                pass
    # 字符串长度
    for key, cmp in (("minLength", lambda v, c: len(v) < c),
                     ("maxLength", lambda v, c: len(v) > c)):
        if key in schema and isinstance(value, str):
            if cmp(value, schema[key]):
                errors.append(f"{path}: 字符串长度违反 {key}={schema[key]}")
    # object 属性
    if isinstance(value, dict):
        for req in schema.get("required", []) or []:
            if req not in value:
                errors.append(f"{path}: 缺少必填字段 {req!r}")
        for k, sub in (schema.get("properties", {}) or {}).items():
            if k in value:
                _validate(value[k], sub, f"{path}.{k}", errors)
        # additionalProperties=False 时禁止多余字段
        if schema.get("additionalProperties") is False:
            allowed = set((schema.get("properties", {}) or {}).keys())
            for k in value:
                if k not in allowed:
                    errors.append(f"{path}: 额外字段 {k!r} 不被允许")
    # array items
    if isinstance(value, list):
        items = schema.get("items")
        if items is not None:
            for i, item in enumerate(value):
                _validate(item, items, f"{path}[{i}]", errors)
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: 数组长度 {len(value)} 小于 minItems={schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: 数组长度 {len(value)} 大于 maxItems={schema['maxItems']}")


def validate(data: Any, schema: Dict) -> Tuple[bool, List[str]]:
    """校验 data 是否符合 schema。返回 (是否通过, 错误列表)。"""
    errors: List[str] = []
    _validate(data, schema, "$", errors)
    return (len(errors) == 0, errors)


def ensure(data: Any, schema: Dict, context: str = "value") -> Any:
    """校验并返回原值；不通过抛 SchemaError（含全部错误）。"""
    ok, errors = validate(data, schema)
    if not ok:
        raise SchemaError(f"{context} 校验失败: {'; '.join(errors)}")
    return data


# ---- 便捷构造：描述一个工具/schema，便于给 MCP tools/list 或 LLM 用 ----
def tool_schema(name: str, description: str, properties: Dict,
                required: Optional[List[str]] = None) -> Dict:
    """构造 MCP 风格工具 schema。"""
    return {
        "name": name, "description": description,
        "inputSchema": {"type": "object", "required": list(required or []),
                        "properties": properties},
    }
