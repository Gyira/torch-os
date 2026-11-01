"""恋爱系统 AI 脱敏：姓名、电话、证件、邮箱、链接、银行卡号。

只用于发送给 dsh 的文本；原始聊天、Markdown 与数据库都不改写。
"""
from __future__ import annotations

import re

# 中文紧贴数字时 \b 不会成立（「是138…」两边都是 \w）；用数字前后断言替代 \b。
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s,，。！？\n]+")
_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")


def desensitize(text: str, names: dict[str, str] | None = None) -> str:
    value = str(text or "")
    value = _PHONE.sub("[电话号码已隐藏]", value)
    value = _ID_CARD.sub("[身份证号已隐藏]", value)
    value = _EMAIL.sub("[邮箱地址已隐藏]", value)
    value = _URL.sub("[链接已隐藏]", value)
    value = _BANK_CARD.sub("[银行卡号已隐藏]", value)
    for real, alias in (names or {}).items():
        real = str(real or "").strip()
        if real and len(real) >= 2:
            value = value.replace(real, alias)
    return value


__all__ = ["desensitize"]
