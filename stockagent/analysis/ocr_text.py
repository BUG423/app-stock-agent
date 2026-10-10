"""把 OCR 的碎片行整理成适合邮件阅读的单段文本。"""

import re


def compact_ocr_text(text: str, *, max_chars: int = 6000) -> str:
    """去掉 OCR 产生的空行/重复行，但不补全或猜测任何被遮挡文字。"""
    lines: list[str] = []
    previous = ""
    for raw_line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line == previous:
            continue
        lines.append(line)
        previous = line
    compact = " · ".join(lines)
    return compact[:max_chars]
