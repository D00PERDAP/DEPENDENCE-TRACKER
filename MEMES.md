# Настоящие русские мемы: каталог и обновления

`meme_collector.py` теперь **не ищет случайные фотографии на Wikimedia Commons**. Он собирает ссылки на страницы с мемами из проверяемых RSS/Atom-источников и двух начальных примеров. Изображения не скачиваются и не публикуются без редакторского подтверждения.

## Первый запуск

```bash
cd /opt/dependence-tracker
.venv/bin/python meme_collector.py clean-old
.venv/bin/python meme_collector.py scan
.venv/bin/python meme_collector.py list
```

Ссылки ведут на оригинальные публикации; открой подходящую, проверь разрешение на распространение, скачай конкретную картинку сам. Затем:

```bash
.venv/bin/python meme_collector.py approve 100 /path/to/image.jpg --category general
```

Вместо 100 подставь ID из списка. Команда проверяет формат и SHA-256, не добавляет точные дубликаты, сохраняет картинку в `assets/memes/general`. Для неподходящей страницы: `python3 meme_collector.py reject 100`.

## Ежедневное пополнение

В `data/meme_feeds.txt` добавь одну строку на разрешённую RSS/Atom-ленту:

```text
general https://example.org/memes/rss.xml
ex https://example.org/relationships/feed.xml
```

Это **примеры формата**, не реальные ленты. Используй только доступные RSS/Atom источники, допускающие такой опрос. Пока файл пуст или отсутствует, `scan` добавит лишь две исходные ссылки.

После проверки можно включить cron:

```cron
20 4 * * * cd /opt/dependence-tracker && /opt/dependence-tracker/.venv/bin/python meme_collector.py scan >> /opt/dependence-tracker/meme_scan.log 2>&1
```

Cron обновляет только очередь ссылок; **не** автоматически одобренную коллекцию. Пользовательские данные не отправляются на сторонние сайты.

Категории: `ex`, `calls`, `money`, `travel`, `general`. При отсутствии подходящего изображения бот отправляет текст.

Проверяй лицензию/разрешение автора и соответствие теме до добавления; само наличие картинки в интернете не даёт права на её распространение.
