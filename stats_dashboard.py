"""Minimal private Telegram stats card with self-reported records only."""
from __future__ import annotations

import io
from PIL import Image, ImageDraw

from meme_studio import font

W, H = 900, 920
BG = "#111519"
PANEL = "#1E2429"
WHITE = "#F7F4EE"
MUTED = "#B1B9B8"
RED = "#E44D48"
GREEN = "#88C5A0"


def draw_text(draw, xy, value, size, color=WHITE):
    draw.text(xy, str(value), font=font(size), fill=color)


def fit_text(draw, value, max_width, size=47):
    value = str(value or "Не указано")
    while size > 23 and draw.textbbox((0, 0), value, font=font(size))[2] > max_width:
        size -= 2
    if draw.textbbox((0, 0), value, font=font(size))[2] > max_width:
        while value and draw.textbbox((0, 0), value + "…", font=font(size))[2] > max_width:
            value = value[:-1]
        value += "…"
    return value, size


def make_dashboard(summary):
    """Render a restrained mobile-first card; no image is saved on disk."""
    picture = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 0, W, 13), fill=GREEN)
    draw.rectangle((0, 13, W, 26), fill=RED)

    draw_text(draw, (56, 66), "РАСКЛАД МАГИ", 72)
    draw_text(draw, (58, 164), f"🦅  {summary['month']}  •  ЖИ ЕСТЬ, БРАТ", 29, MUTED)
    draw.rounded_rectangle((48, 248, 852, 714), radius=30, fill=PANEL)

    last = summary.get("last_record")
    draw_text(draw, (85, 292), "ПОСЛЕДНЯЯ ЗАПИСЬ", 30, GREEN)
    substance = last["substance"] if last else "Пока нет записей"
    substance, size = fit_text(draw, substance, 710, 60)
    draw_text(draw, (85, 367), substance, size)
    draw_text(draw, (85, 478), "ДАТА ОТМЕТКИ", 27, MUTED)
    draw_text(draw, (85, 524), last["date"] if last else "—", 56)

    draw.line((85, 630, 816, 630), fill="#394249", width=2)
    draw_text(draw, (85, 658), "ЗАПУСКОВ В ЭТОМ МЕСЯЦЕ", 24, MUTED)
    draw_text(draw, (750, 640), str(summary["episodes"]), 52, RED)

    draw_text(draw, (52, 775), "Мага записал, вацок. Без лишней суеты.", 29)
    draw_text(draw, (52, 827), "Это запуски сопровождения, не число срывов.", 23, MUTED)
    draw_text(draw, (52, 861), "Данные личные  •  Не медицинский отчёт", 22, MUTED)
    output = io.BytesIO()
    picture.save(output, "JPEG", quality=90, optimize=True)
    return output.getvalue()


def summary_caption(summary):
    last = summary.get("last_record")
    lines = ["🦅 Э, брат, Мага поднял твой расклад!"]
    if last:
        lines += [
            f"📝 Последняя запись: {last['date']}",
            f"💊 Указано вещество: {last['substance']}",
        ]
    else:
        lines.append("📂 Записей пока нет, вацок.")
    lines.append(f"📊 Запусков сопровождения за {summary['month']}: {summary['episodes']}")
    lines.append("Жи есть! Запуск сопровождения не означает срыв; причина срыва отдельно не фиксируется.")
    return "\n".join(lines)
