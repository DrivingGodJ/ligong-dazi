"""Colleges shown in NJUST's undergraduate admissions college overview."""

import re
import unicodedata

COLLEGES = (
    "机械工程学院",
    "化学与化工学院",
    "电子工程与光电技术学院",
    "计算机科学与工程学院",
    "经济管理学院",
    "能源与动力工程学院",
    "自动化学院",
    "物理学院",
    "外国语学院",
    "公共事务学院",
    "马克思主义学院",
    "材料科学与工程学院",
    "环境与生物工程学院",
    "设计科学与艺术学院",
    "钱学森学院",
    "知识产权学院",
    "中法工程师学院",
    "数学与统计学院",
    "集成电路学院（微电子学院）",
    "网络空间安全学院",
    "智能科学与技术学院",
    "新能源学院",
    "安全科学与工程学院（应急管理学院）",
)

COLLEGE_SET = frozenset(COLLEGES)


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", "", value).casefold()


_BY_KEY = {_key(college): college for college in COLLEGES}
_ALIASES = {
    "设计艺术与传媒学院": "设计科学与艺术学院",
    "设计艺术学院": "设计科学与艺术学院",
    "微电子学院(集成电路学院)": "集成电路学院（微电子学院）",
    "微电子学院": "集成电路学院（微电子学院）",
    "集成电路学院": "集成电路学院（微电子学院）",
    "安全科学与工程学院": "安全科学与工程学院（应急管理学院）",
    "公共事务学院(国防教育与国防动员研究院)": "公共事务学院",
}
_BY_KEY.update({_key(alias): college for alias, college in _ALIASES.items()})


def match_college(value: str | None) -> str | None:
    """Match only exact names or known, unambiguous historical aliases."""
    if not value or not value.strip():
        return None
    return _BY_KEY.get(_key(value))
