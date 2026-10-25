"""Private on-demand Telegram stats PNG: daily bars, substances, risk patterns."""
from __future__ import annotations

import io
import math
from PIL import Image, ImageDraw, ImageFont

from meme_studio import font

W, H = 1100, 1470
BG, PANEL, INK, MUTED = "#10141C", "#1B2330", "#F5F6F8", "#A8B4C4"
RED, AMBER, GREEN = "#FB5564", "#FFCB6E", "#55D7B0"
RISK_LABELS = {
    "more": "Хотел продолжить", "contacts": "Написать всем", "ex": "Написать бывшей",
    "money": "Импульсивные траты", "travel": "Куда-то ехать",
    "paranoia": "Тревога / паранойя", "sleep": "Проблемы со сном",
}


def t(draw, xy, value, size=32, color=INK, weight="normal", anchor=None):
    draw.text(xy, str(value), font=font(size), fill=color, anchor=anchor)


def card(draw, coords):
    draw.rounded_rectangle(coords, radius=25, fill=PANEL)


def bar(draw, x0, y0, x1, y1, color):
    if x1 > x0 and y1 > y0:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=5, fill=color)


def make_dashboard(summary):
    """Return in-memory JPEG bytes; personal stats never written to public disk."""
    picture = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 0, W, 13), fill=RED)
    t(draw, (65, 54), "НЕ НАТВОРИ ДЕЛОВ", 53)
    t(draw, (65, 126), f"ТВОИ РАСКЛАДЫ  •  {summary['month']}  •  UTC", 28, MUTED)
    data = [
        (summary["episodes"], "Запусков сопровождения", RED),
        (summary["checkins"], "Проверок самочувствия", AMBER),
        (summary["resolved"], "Рисков отмечено как пройденные", GREEN),
    ]
    for i, (value, label, shade) in enumerate(data):
        left = 65 + i * 326
        card(draw, (left, 202, left + 304, 373))
        t(draw, (left + 22, 226), value, 65, shade)
        # Short labels at this size, with manual split for the longest.
        if i == 2:
            t(draw, (left + 21, 310), "Опасных порывов", 22, MUTED)
            t(draw, (left + 21, 335), "переждал", 22, MUTED)
        elif i == 1:
            t(draw, (left + 21, 316), "Проверок себя", 21, MUTED)
        else:
            t(draw, (left + 21, 316), label, 22, MUTED)

    card(draw, (65, 403, 1034, 771))
    t(draw, (93, 428), "КАК ПРОШЁЛ МЕСЯЦ", 34)
    t(draw, (94, 478), "Сколько раз включал сопровождение по дням", 21, MUTED)
    counts = summary["days"]
    maximum = max(1, max(counts, default=0))
    x0, chart_width = 108, 871
    plot_bottom = 694
    for line in range(4):
        y = 548 + line * 48
        draw.line((x0, y, x0 + chart_width, y), fill="#343D4B", width=2)
    number = max(1, len(counts))
    step = chart_width / number
    col_w = max(5, int(step * .58))
    for index, value in enumerate(counts):
        h = (value / maximum) * 141 if value else 0
        xx = round(x0 + index * step + (step - col_w) / 2)
        if value:
            bar(draw, xx, plot_bottom - h, xx + col_w, plot_bottom, RED)
        if index % 5 == 0 or index + 1 == len(counts):
            t(draw, (xx + 2, 711), str(index + 1), 20, MUTED)

    card(draw, (65, 797, 1034, 1099))
    t(draw, (93, 823), "ЧТО ТЫ УКАЗЫВАЛ", 32)
    t(draw, (93, 867), "Твои отметки по веществам • не оценка доз", 20, MUTED)
    top_substances = summary["substances"].most_common(4)
    if not top_substances:
        t(draw, (95, 949), "Пока нет записей за этот месяц", 30, MUTED)
    else:
        total = sum(summary["substances"].values()) or 1
        for index, (label, count) in enumerate(top_substances):
            y = 918 + index * 43
            t(draw, (95, y), str(label)[:25], 24)
            xx = 535
            width = 378
            draw.rounded_rectangle((xx, y + 4, xx + width, y + 20), radius=8, fill="#3A4351")
            draw.rounded_rectangle((xx, y + 4, xx + max(8, width * count / total), y + 20),
                                   radius=8, fill=AMBER)
            t(draw, (967, y - 2), count, 25, AMBER, anchor="ra")

    card(draw, (65, 1123, 1034, 1343))
    t(draw, (93, 1152), "КАКИЕ БЫЛИ ПОЗЫВЫ", 30)
    risks = summary["risks"].most_common(3)
    if not risks:
        t(draw, (95, 1236), "Пока нет отметок о рискованных желаниях", 24, MUTED)
    else:
        mx = max(1, risks[0][1])
        for idx, (key, count) in enumerate(risks):
            y = 1206 + idx * 41
            t(draw, (95, y), RISK_LABELS.get(key, key)[:27], 21)
            draw.rounded_rectangle((590, y + 7, 917, y + 22), radius=7, fill="#3A4351")
            draw.rounded_rectangle((590, y + 7, 590 + 327 * count / mx, y + 22),
                                   radius=7, fill=GREEN)
            t(draw, (970, y), count, 21, GREEN, anchor="ra")
    t(draw, (65, 1382), "Э, брат, это только твои отметки, не диагноз и не оценка доз.", 23, MUTED)
    t(draw, (65, 1421), "Твои данные личные • Картинка не сохраняется на сервере", 20, MUTED)
    output = io.BytesIO()
    picture.save(output, "JPEG", quality=89, optimize=True)
    return output.getvalue()


def summary_caption(summary):
    """Explain each number in everyday language, without overstating accuracy."""
    month = summary["month"]
    episodes = summary["episodes"]
    checkins = summary["checkins"]
    resolved = summary["resolved"]
    lines = [
        f"🦅 Э, вацок, вот твои расклады за {month}!",
        f"💀 {episodes} — столько раз ты включал сопровождение.",
        f"🫀 {checkins} — столько раз отмечал своё самочувствие.",
        f"🛡 {resolved} — столько рискованных порывов отметил как пережданные.",
    ]
    top = summary["substances"].most_common(1)
    if top:
        lines.append(f"📝 Чаще всего указывал: {top[0][0]}.")
    risk = summary["risks"].most_common(1)
    if risk:
        lines.append(f"⚡ Чаще отмечал желание: {RISK_LABELS.get(risk[0][0], risk[0][0]).lower()}.")
    if not episodes and not summary["event_count"]:
        lines.append("Пока пустовато, брат. Делай отметки — появятся графики.")
    lines.append("Жи есть, это только твои записи, не подсчёт фактического употребления и не медицинский диагноз.")
    return "\n".join(lines)
