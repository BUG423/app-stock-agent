"""系统健康巡检任务。"""

import json
import logging
from pathlib import Path

from stockagent.core.health import collect_health

logger = logging.getLogger(__name__)
_STATE = Path(__file__).parents[3] / "data" / "health_alert_state.json"


async def health_check_job(job_name: str = "health_check") -> dict:
    health = await collect_health()
    signature = "|".join(sorted(i["code"] for i in health["issues"]))
    previous = ""
    try:
        previous = json.loads(_STATE.read_text(encoding="utf-8")).get("signature", "")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    sent = False
    if signature and signature != previous:
        from stockagent.notification.email_notifier import EmailNotifier

        detail = "\n".join(i["message"] for i in health["issues"])
        sent = await EmailNotifier().send_health_alert("系统健康巡检", "检测到降级状态", detail)
    saved_signature = signature if not signature or signature == previous or sent else previous
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(
        json.dumps({"signature": saved_signature}, ensure_ascii=False), encoding="utf-8"
    )
    issue_detail = "; ".join(issue["message"] for issue in health["issues"])
    log = logger.warning if health["issues"] else logger.info
    log(
        "健康巡检: %s issues=%d%s",
        health["status"],
        len(health["issues"]),
        f" detail={issue_detail}" if issue_detail else "",
    )
    return health
