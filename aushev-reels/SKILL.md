---
name: aushev-reels
description: >
  Монтаж вертикальных видео (Reels / TikTok / Shorts) без After Effects — в песочнице на Python + ffmpeg.
  Повторяет в коде инструменты AUSHEV (анимации текста word / letter / word_bounce, 4 направления, IN/OUT,
  Bounce с пружиной), Liquid Glass (стеклянные плашки), Deep Glow (свечение), Shadow Studio (мягкие тени),
  переходы Mister Horse / Animation Composer (whip, zoom, slide, spin, glitch, flash…), трекинг глаз спикера
  на одной линии, субтитры синхронно с речью (офлайн Whisper) и звуки из пака пользователя (без музыки).
  Используй, когда прислали видео с телефона и просят: «смонтируй», «монтаж», «сделай рилс», «рилс»,
  «AUSHEV», «анимации текста», «стекло», «свечение», «переходы», «субтитры», «сделай динамичнее».
  English: edit a vertical phone video into a finished Reel/TikTok/Short MP4 — AUSHEV-style animated text,
  liquid glass cards, deep glow, soft shadows, transitions, eye-line lock, speech-synced captions, SFX only.
  Triggers: "edit my reel", "montage", "make a reel", "animate text", "glass", "glow", "AUSHEV".
---

# AUSHEV Reels — монтаж вертикальных видео кодом

Пользователь присылает видео с телефона → ты монтируешь → отдаёшь готовый MP4 1080×1920, 30 fps.
Всё делается скриптами из `scripts/`. Каждая тяжёлая команда работает не дольше `--budget` секунд
(по умолчанию 240 — в песочнице одна команда живёт ~300 с): если она напечатала `BUDGET: ... Re-run`,
просто запусти **ту же команду ещё раз** — она продолжит с места остановки. Успех = строка `DONE`.

## Правила пользователя (соблюдать всегда)

1. **Темп.** Много анимаций, эффектов и переходов: зритель Reels не держит фокус дольше ~2 с —
   что-то новое (текст, зум, переход, акцент) минимум каждые 2 с. **Сильный анимированный хук в первые 0.5 с**
   (крупный заголовок word_bounce + bounce + glow, punch-зум, звук opening).
2. **Безопасная зона Instagram 1080×1920:** весь текст и анимации внутри y 225–1552, x 48–1032;
   ниже y 1097 правый край x ≤ 948. `render.py --check` обязателен перед рендером (`--fit-safe` поправит сам).
3. **Глаза спикера всё видео на одной горизонтальной линии**; исходная крупность сохраняется
   (один постоянный масштаб), зумы — только по смыслу (акцент, панчлайн, цифра), центр зума — глаза.
4. **Никакой фоновой музыки** — только звуковые эффекты (нашид пользователь добавляет сам).
   Один звук на одну визуальную идею; один сэмпл никогда не звучит дважды подряд; разброс высоты ±1.2 полутона
   и громкости — всё это делает `audio.py` автоматически.
5. **Голос не трогать**, эффекты чуть ниже голоса (`audio.sfx_gain_db` ≈ −3).
6. Текст не кладём на лицо (`--check` предупреждает) — заголовки над головой или под подбородком.

## Пайплайн

```bash
S=<путь к скиллу>/scripts            # например /mnt/skills/user/aushev-reels/scripts
pip install -q -r $S/requirements.txt   # numpy, pillow, opencv-python-headless<5, faster-whisper

python3 $S/prepare.py  input.mp4 --work work          # 30 fps, запас 1.25x, склейки, голос → work/
python3 $S/transcribe.py --work work --lang ru        # слова с таймингами → work/words.json
python3 $S/track.py    --work work                    # лицо/глаза → work/track.json
python3 $S/autoplan.py --work work --out plan.json [--trim-pauses] [--hook "ТЕКСТ ХУКА"]
#   ↑ черновик. ДАЛЬШЕ ОБЯЗАТЕЛЬНО отредактируй plan.json по смыслу (см. «Как писать план»)
python3 $S/render.py plan.json --check                # ошибки = стоп; предупреждения — исправить
python3 $S/render.py plan.json --fit-safe --check     # (если вылезло за безопасную зону)
python3 $S/render.py plan.json --preview auto --safe  # кадры + work/preview/sheet.jpg — ПОСМОТРИ их
python3 $S/render.py plan.json --render               # повторять до DONE → out/reel.mp4
```

Детали шагов:
- **prepare** — любое видео (вертикальное, горизонтальное, с поворотом) → `work/src.mp4` (вертикаль 2400 px),
  `work/voice.wav` (нетронутый звук), `work/meta.json` (длительность, склейки `cuts`).
- **transcribe** — faster-whisper `small`, int8, CPU, слова с таймингами. Прочитай распознанный текст:
  если есть ошибки (имена, термины), запиши правильный текст в файл и выполни
  `transcribe.py --work work --fix fixed.txt` — слова заменятся, тайминги сохранятся.
  Если модель не скачивается (нет сети) — спроси у пользователя текст и выполни `--text script.txt`
  (выравнивание по паузам, приблизительно).
- **track** — OpenCV Haar (лицо + глаза), сглаживание внутри плана. Если `face found` < 50 % — проверь превью,
  при необходимости задай `video.eye_line` вручную.
- **autoplan** — хук, заголовок/стеклянная плашка на каждую мысль, субтитры по словам, зумы, переход на каждую склейку,
  позиции вне лица и внутри безопасной зоны. `--trim-pauses` вырезает паузы > 0.45 с (jump-cut) через `video.keep`.
- **render** — рендер чанками по 2 с в 4 процесса; повторный запуск перерисовывает только чанки, которых коснулись
  правки. В конце сводит звук и кладёт `out/*.mp4` (+ `work/sfx_only.wav` — дорожка одних эффектов,
  `work/sfx_log.json` — какие звуки где стоят).

## Как писать план (plan.json)

Полная схема со всеми полями — `references/plan_schema.md`. Пример со всеми эффектами — `assets/example_plan.json`.
Коротко:

```json
{
 "video": {"work": "work", "eye_line": "auto", "keep": [[0, 4.2], [4.8, 15]], "grade": {"contrast": 1.06}},
 "output": "out/reel.mp4",
 "elements": [
  {"id": "hook", "type": "text", "hook": true, "text": "3 ОШИБКИ", "start": 0, "end": 2.8,
   "y": 360, "size": 130, "weight": "black", "accent": ["3"],
   "anim": {"mode": "word_bounce", "dir": "up", "io": "both"}, "bounce": {"io": "in"}, "glow": true, "shadow": true},
  {"id": "c1", "type": "card", "text": "01 · Слабый хук", "start": 3.3, "end": 5.3, "y": 380,
   "anim": {"mode": "word", "dir": "left", "io": "both"}},
  {"id": "num", "type": "counter", "from": 0, "to": 200, "prefix": "+", "suffix": "%", "start": 11.6, "end": 14.9,
   "y": 380, "size": 150, "glow": true, "bounce": {"io": "in"}},
  {"id": "subs", "type": "subs", "start": 2.8, "end": 15, "y": 1300, "karaoke": true}
 ],
 "zooms": [{"start": 0, "end": 0.6, "scale": 1.12, "ease": "punch"}],
 "transitions": [{"at": 5.37, "type": "whip", "dir": "left", "riser": true}],
 "sfx": [{"t": 13.05, "role": "shine"}]
}
```

| поле | значения |
|---|---|
| `type` | `text` (заголовок), `card` (текст на стеклянной плашке), `counter` (число считает), `subs` (субтитры по словам) |
| `anim.mode` | `word` — слова целиком · `letter` — по буквам · `word_bounce` — слова с пружинным отскоком |
| `anim.dir` | `up` / `down` / `left` / `right` (направление движения при появлении) |
| `anim.io` | `in` / `out` / `both` — OUT это зеркало IN во времени и заканчивается в `end` |
| `bounce` | `true` / `{"io": "in"|"out"|"both"}` — Scale 0↔100 %, Rotation +28°, 15 кадров, пружина AUSHEV |
| `glass` | `true` / объект Liquid Glass (blur, refraction, edge, tint, light_angle, chroma, shadow) |
| `glow` | `true` / объект Deep Glow (radius, exposure, threshold, color, outer, layers) |
| `shadow` | `true` (по умолч.) / `false` / объект Shadow Studio (angle, long, contact, softness, strength) |
| `transitions[].type` | `whip slide zoom_in zoom_out spin blur glitch flash luma shake` (+ `dir`, `dur`, `riser`) |
| `zooms[].ease` | `punch` (удар с пружиной, звук hit) · `smooth` · `creep` (медленный наезд) |
| `sfx` у элемента | `"auto"` (по умолчанию) · `false` · роль `"hit"` · файл `"hit_03"` · `{"in": "...", "out": "..."}` |

Роли звуков (`assets/sounds`, 59 шт., список — `references/sounds.md`): `opening` хук · `title` заголовок (свиш) ·
`small` мелкий текст (клик) · `card` стеклянная плашка · `trans` переход · `hit` удар/акцент · `shine` блеск ·
`key` печать по буквам · `riser` нарастание (заканчивается в точке) · `snap` щелчок/bounce · `down` спад.
Авто-звуки: hook → opening, card/glass → card, word_bounce/bounce → snap, letter → key, glow → shine,
крупный текст → title, мелкий → small, переходы → trans (glitch → глитч-сэмплы, flash/shake → hit),
punch-зум → hit, `riser: true` → нарастание к точке. Субтитры по умолчанию без звука.

### Режиссура (что делает ролик живым)

- 0–0.5 с: хук — 2–4 слова КРУПНО (110–150 px, black), word_bounce + bounce + glow + punch-зум + opening.
- Каждая мысль = свой визуальный блок: заголовок или стеклянная карточка с ключевой фразой (не дублируй субтитры
  слово в слово — сожми смысл), акцент-слово цветом (`accent`).
- Чередуй `mode`/`dir` у соседних элементов; IN+OUT (`io: both`) у всего, что исчезает не по склейке.
- Цифры → `counter` + glow + punch-зум. Панчлайн → zoom `punch` + `hit`. Перечисление → карточки 01/02/03.
- На каждой склейке — переход (`--check` предупредит); в середине длинного плана — акцент-переход (flash, luma,
  shake) или зум, чтобы не было «мёртвых» 2 с.
- Субтитры: внизу безопасной зоны (y ≈ 1250–1450), 56–66 px, `max_width` ≤ 640, `karaoke: true`.
- Заголовки: над головой (y ≈ 300–420), если над головой есть место, иначе под подбородком.
  Ниже y 1097 центр по x лучше 498 (правая граница там 948) — `--fit-safe` сделает это сам.
- Не больше 2 текстовых блоков одновременно (кроме субтитров).

## Проверка перед сдачей (обязательно)

1. `--check` → `CHECK OK` и без предупреждений (или каждое осознанно оставлено).
2. `--preview auto --safe` → открой `work/preview/sheet.jpg` и пару кадров в полном размере: текст читается,
   не на лице, внутри красной рамки, стекло/свечение/тени выглядят аккуратно.
3. После `--render` достань кадры из готового MP4 (переходы, хук, конец) и посмотри их:
   `ffmpeg -i out/reel.mp4 -vf "select='not(mod(n\,45))',scale=270:480,tile=6x3" -frames:v 1 check.png`
4. `work/sfx_log.json` — нет одинаковых сэмплов подряд, звуков не больше, чем визуальных идей.
5. Отдай пользователю MP4 (и при желании `work/sfx_only.wav`). Не говори «готово», пока не посмотрел кадры.

## Файлы скилла

- `scripts/prepare.py transcribe.py track.py autoplan.py render.py` — команды;
  `common.py` (константы, пружина и Bounce AUSHEV), `text.py` (раскладка и анимации текста), `fx.py` (glow, тени,
  стекло), `camera.py` (линия глаз, зумы, переходы, грейд), `audio.py` (звуки и сведение).
- `references/plan_schema.md` — все поля плана; `references/effects_from_ae.md` — как разобраны AUSHEV-пресеты,
  Liquid Glass, Deep Glow, Shadow Studio, Animation Composer и как они повторены; `references/sounds.md` — каталог звуков.
- `assets/sounds/` — 59 SFX (Opus), `assets/fonts/` — Inter Display (Latin + Cyrillic, OFL),
  `assets/example_plan.json` — пример со всеми эффектами.

## Если что-то пошло не так

- `WHISPER UNAVAILABLE` — нет доступа к huggingface.co: попроси текст у пользователя → `transcribe.py --text`.
- OpenCV 5 без Haar-каскадов — `track.py` сам поставит `opencv-python-headless<5` и перезапустится.
- Долгий рендер — это нормально: повторяй `--render` до `DONE`; меньше нагрузка: `--workers 2`.
- Текст на лице / вне зоны — двигай `y`, уменьшай `size`/`max_width`, или `--fit-safe`.
- Линия глаз «не держится» (`--check` WARN) — увеличь `video.max_reframe` (до 1.25) или задай `eye_line` вручную.
