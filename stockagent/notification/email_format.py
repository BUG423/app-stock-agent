"""用户邮件的统一标题与页面骨架。"""

from html import escape


def standard_subject(category: str, day: str, summary: str = "") -> str:
    """统一主题格式，便于邮箱聚类与快速扫描。"""
    suffix = f"｜{summary}" if summary else ""
    return f"[StockAgent][{category}] {day}{suffix}"


def standard_email(
    *,
    category: str,
    day: str,
    headline: str,
    summary_html: str,
    content_html: str,
    note: str,
) -> str:
    """统一日报类邮件的版式；业务明细由调用方传入。"""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<div style="font-family:'Microsoft YaHei',sans-serif;max-width:680px;margin:0 auto;padding:20px;color:#222">
<div style="font-size:12px;color:#868e96;margin-bottom:6px">StockAgent · {escape(category)}</div>
<h2 style="font-size:20px;margin:0 0 4px">{escape(headline)}</h2>
<div style="font-size:13px;color:#868e96;margin-bottom:16px">{escape(day)} · 北京时间</div>
<div style="background:#f5f7fa;border-radius:8px;padding:12px 14px;margin-bottom:16px;line-height:1.7">
{summary_html}
</div>
{content_html}
<div style="border-top:1px solid #eee;margin-top:18px;padding-top:10px;color:#999;font-size:12px;line-height:1.6">
{escape(note)}<br>仅供信息跟踪，不构成投资建议。
</div>
</div></body></html>"""
