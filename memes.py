"""Мемные подписи для трекера. Не романтизируют употребление."""
from datetime import datetime, timezone

def streak_caption(days: int, substance: str) -> str:
    name = substance.upper()
    if days == 0:
        return f"🕶 {name}: СЧЁТЧИК ЗАПУЩЕН. ГЛАВНОЕ — БЕРЕЧЬ СЕБЯ."
    if days >= 30:
        return f"🏆 {days} ДНЕЙ БЕЗ {name}. ТЫ УЖЕ ФИНАЛЬНЫЙ БОСС."
    if days >= 14:
        return f"🗿 {days} ДНЕЙ БЕЗ {name}. ЛЕГЕНДА ВОЗВРАЩАЕТСЯ."
    if days >= 7:
        return f"🫡 {days} ДНЕЙ БЕЗ {name}. ОПЕРАЦИЯ «НЕ СЕГОДНЯ» ИДЁТ ПО ПЛАНУ."
    if days >= 3:
        return f"😎 {days} ДНЯ БЕЗ {name}. ТЯГА НЕ ПРОШЛА ФЕЙС-КОНТРОЛЬ."
    return f"🌱 {days} ДЕНЬ БЕЗ {name}. НАЧАЛО ПОЛОЖЕНО."

def session_caption() -> str:
    return "📝 ЖУРНАЛ ОТКРЫТ. НЕ ГЕРОЙСТВУЙ: ОТМЕЧАЙ САМОЧУВСТВИЕ."

def completed_goal_caption() -> str:
    return "🎯 МИССИЯ ВЫПОЛНЕНА. ЗДРАВЫЙ СМЫСЛ +100."

def ended_session_caption() -> str:
    return "🏁 ЗАПИСЬ ЗАВЕРШЕНА. НЕ ЗАБУДЬ ПРО ОТДЫХ И СВОЁ СОСТОЯНИЕ."
