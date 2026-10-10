"""真人博主固定画像和邮件提醒规则测试。"""

from stockagent.analysis.blogger_profiles import (
    get_blogger_profile,
    verified_direct_mention,
)
from stockagent.analysis.pipeline import _build_new_content_email, _build_raw_content_email
from stockagent.analysis.pipeline import (
    _is_explicitly_cleared,
    _is_holdings_only_update,
    _suppress_holdings_only_mentions,
)


def _verified_metadata() -> dict:
    return {
        "verified_stock": {
            "code": "605028",
            "name": "世茂能源",
            "confidence": "high",
            "evidence": [
                "名称片段 世茂/源 与世茂能源匹配",
                "OCR市值÷持仓数与发布时间可见行情价误差不超过0.8%",
            ],
        }
    }


def test_profiles_match_all_three_real_bloggers_by_uid():
    assert get_blogger_profile(user_id="3707010912618582").key == "early_retirement"
    assert get_blogger_profile(user_id="21119247").key == "tiaotiaohu"
    assert get_blogger_profile(user_id="3546957274417333").key == "yaoban"


def test_direct_mention_requires_high_confidence_verified_stock():
    profile = get_blogger_profile(user_id="3707010912618582")

    assert verified_direct_mention(profile, _verified_metadata()) == {
        "code": "605028",
        "name": "世茂能源",
        "direction": "bullish",
        "signal_type": "follow_now",
        "reason": "实盘持仓画面经名称与价格多证据确认",
        "conditions": [],
        "resolution_confidence": "high",
        "resolution_evidence": [
            "名称片段 世茂/源 与世茂能源匹配",
            "OCR市值÷持仓数与发布时间可见行情价误差不超过0.8%",
        ],
    }
    assert verified_direct_mention(profile, {"verified_stock": {"confidence": "medium"}}) is None


def test_early_retirement_email_shows_verified_direct_rule():
    body = _build_raw_content_email(
        "提前炒股早退休",
        "实盘记录",
        "https://example.test/video",
        "OCR原文",
        blogger_user_id="3707010912618582",
        metadata=_verified_metadata(),
    )

    assert "提前早退休 · 直接购入型" in body
    assert "本次确认标的" in body
    assert "世茂能源(605028)" in body
    assert "直接购入/持有倾向，无前置条件" in body
    assert "系统只提醒，不自动下单" in body


def test_early_retirement_email_refuses_unverified_target():
    body = _build_raw_content_email(
        "提前炒股早退休",
        "实盘记录",
        "https://example.test/video",
        "只看见两个残缺汉字",
        blogger_user_id="3707010912618582",
    )

    assert "标的未通过名称、持仓数字与行情的多证据核验" in body
    assert "不生成买入提醒" in body


def test_conditional_bloggers_email_explains_missing_current_conditions():
    for name, uid, title in (
            ("跳跳舞复盘", "21119247", "跳跳舞 · 条件触发型"),
        ("妖板选手", "3546957274417333", "妖板选手 · 条件触发型"),
    ):
        body = _build_new_content_email(
            blogger_name=name,
            title="盘前观点",
            url="https://example.test/post",
            stock_mentions=[{"name": "测试股", "code": "000001", "conditions": []}],
            analysis_dir="bullish",
            watch_count=0,
            text="原文没有明确价位",
            blogger_user_id=uid,
        )

        assert title in body
        assert "本条未识别到明确条件，不触发买入" in body
        assert "三位博主当前总览" in body
        assert "跳跳舞复盘" in body
        assert "小火怪神" in body


def test_holdings_only_update_drops_generic_new_watch_candidate():
    text = "明天处理持仓为主，暂时不新开，科技股回踩5日线承接住再考虑加仓。"

    assert _is_holdings_only_update(text) is True
    assert _suppress_holdings_only_mentions(
        [{"name": "科技股", "code": "unknown", "signal_type": "conditional"}]
    ) == []


def test_explicit_clearance_is_a_terminal_position_update():
    assert _is_explicitly_cleared("今天把手里的票全部卖出，暂时空仓") is True
    assert _is_explicitly_cleared("明天观察支撑") is False
