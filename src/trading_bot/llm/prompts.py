from __future__ import annotations

import json

from trading_bot.llm.contracts import AdvisorContext


def render_system_prompt(*, workflow: str, language: str) -> str:
    language_hint = {
        "en": "Write narrative in English.",
        "ru": "Write narrative in Russian.",
        "bi": "Write a bilingual narrative: English + Russian.",
    }.get(language, "Write narrative in English.")
    return (
        "You are an advisory market analyst for a scalping futures bot. "
        "You must return only valid JSON without markdown or extra keys. "
        "Never provide executable trading commands and never claim order execution authority. "
        f"Workflow: {workflow}. "
        f"{language_hint} "
        "Required JSON keys: action, confidence_pct, market_regime, smart_money_signal, "
        "trade_idea, warnings, evidence, narrative, recommended_focus_symbols, market_bias, setup_quality_score."
    )


def render_user_prompt(context: AdvisorContext) -> str:
    payload = {
        "workflow": context.workflow,
        "run_session_id": context.run_session_id,
        "symbol": context.symbol,
        "requested_at": context.requested_at.isoformat(),
        "operator_prompt": context.operator_prompt,
        "playbook": context.playbook.model_dump(mode="json") if context.playbook is not None else None,
        "payload": context.payload,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
