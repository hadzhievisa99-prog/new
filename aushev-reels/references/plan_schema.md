# plan.json — полная схема

Все времена — в секундах **выходного** таймлайна (после jump-cut'ов `video.keep`).
Координаты — пиксели кадра 1080×1920, `x`/`y` — центр текстового блока.
Пример со всеми эффектами: `assets/example_plan.json`.

```jsonc
{
  "video": {
    "work": "work",                 // папка prepare/transcribe/track (относительно plan.json)
    "keep": [[0, 3.3], [3.7, 9.1]], // необязательно: какие куски исходника оставить (jump-cut пауз)
    "eye_line": "auto",             // "auto" | доля высоты (0.3) | пиксели (560) — линия глаз на всё видео
    "max_reframe": 1.15,            // насколько можно увеличить кадр ради линии глаз (1.0 = не трогать крупность)
    "grade": {"contrast": 1.06, "saturation": 1.06, "vignette": 0.16},  // или false
    "allow_face_overlap": false     // true = не предупреждать о тексте на лице
  },
  "output": "out/reel.mp4",
  "elements": [ ... ],
  "zooms": [ ... ],
  "transitions": [ ... ],
  "sfx": [ ... ],                   // ручные звуки (в дополнение к авто-звукам элементов)
  "audio": {"sfx_gain_db": -3, "pitch_jitter": 1.2, "vol_jitter_db": 1.5, "seed": 7}
}
```

## elements[]

Общие поля для `type: text | card | counter | subs`:

| поле | по умолчанию | смысл |
|---|---|---|
| `id` | `el<N>` | имя (видно в ошибках --check) |
| `type` | `text` | `text` — заголовок; `card` — текст на стеклянной плашке (glass включён); `counter` — анимированное число; `subs` — субтитры по словам из words.json |
| `text` | — | строка (для text/card) |
| `start`, `end` | — | время появления/исчезновения |
| `x`, `y` | 540, 960 | центр блока |
| `size` | 84 | кегль, px |
| `weight` | `extrabold` | `black` / `extrabold` / `semibold` (Inter Display) |
| `color` | `#FFFFFF` | цвет текста |
| `accent` | [] | слова, которые красятся `accent_color` |
| `accent_color` | `#FFD23F` | цвет акцента / karaoke |
| `max_width` | 900 | ширина переноса (переносы балансируются) |
| `align` | `center` | `left` / `center` / `right` |
| `line_height` | 1.08 | |
| `tracking` | 0 | доп. трекинг, px |
| `upper` | false | В ВЕРХНИЙ РЕГИСТР |
| `stroke`, `stroke_color` | 0 | обводка |
| `scale`, `rotation`, `opacity` | 1, 0, 1 | статичная трансформация |
| `z` | 0 | порядок слоёв |
| `hook` | false | хук в начале: авто-звук `opening` |
| `anim` | см. ниже | анимация текста AUSHEV |
| `bounce` | false | `true` или `{"io": "in"|"out"|"both"}` — Bounce AUSHEV |
| `glass` | false (card: true) | `true` или объект Liquid Glass |
| `glow` | false | `true` или объект Deep Glow |
| `shadow` | true | `false`, `true` или объект Shadow Studio |
| `sfx` | `"auto"` | `"auto"`, `false`, роль (`"hit"`), файл (`"hit_03"`) или `{"in": ..., "out": ..., "gain": dB}` |

### anim — текстовые анимации (пресеты AUSHEV)

```json
"anim": {"mode": "word", "dir": "up", "io": "both", "dur": 0.45, "stagger": 0.07, "dist": 75, "ease": "out"}
```

| поле | значения | смысл |
|---|---|---|
| `mode` | `word` / `letter` / `word_bounce` | слова целиком · по буквам · слова с пружиной (cos-затухание 150%·cos(2.5·2πt)/e^{7t}) |
| `dir` | `up` / `down` / `left` / `right` | направление движения при появлении (up = выезжает снизу вверх) |
| `out_dir` | как dir | направление для OUT (по умолчанию = dir, т.е. зеркально) |
| `io` | `in` / `out` / `both` | IN — в начале, OUT — зеркальное отражение во времени, заканчивается в `end`; both — оба на одном элементе |
| `dur` | word 0.45, letter 0.32, word_bounce 0.6 | длительность одной единицы |
| `stagger` | word 0.07, letter 0.028, word_bounce 0.1 | задержка между словами/буквами (Speed = 0.1 с в пресете) |
| `dist` | 0.9·size / 0.7·size / 0.55·size | сдвиг, px |
| `ease` | `out` `inout` `expo` `smooth` `linear` | кривая (AE: Ease High −50 ≈ `out`) |
| `times` | — | абсолютные времена старта каждого слова (синхрон с речью; subs ставит сам) |

### bounce (AUSHEV Bounce)

Окно 15 кадров (не больше половины длительности элемента). IN: Scale 0 → 100%, Rotation +28° → 0;
OUT: наоборот, заканчивается в `end`. Пружина после ключа: `v·0.06·sin(1.8·t·2π)/e^{5t}`, для OUT — зеркальная раскачка перед ключом.

### glass (Liquid Glass)

```json
"glass": {"pad": [46, 30], "radius": 38, "blur": 20, "refraction": 1.0, "edge": 22, "tint": "#10141A",
          "tint_opacity": 0.24, "brightness": 0.03, "light_angle": -55, "highlight": 0.9, "chroma": 0.6,
          "shadow": 0.38, "shadow_dist": 18, "shadow_soft": 34, "w": null, "h": null}
```
`blur` = Thickness, `refraction`/`edge` = Refraction/Edge Width, `chroma` = Chromatic Aberration,
`light_angle`/`highlight` = Specular Highlights, `tint*` = Tint, `shadow*` = Shadow. `w`/`h` — фиксированный размер плашки.
Светлое стекло: `"tint": "#FFFFFF", "tint_opacity": 0.12, "brightness": 0.06` (тогда текст тёмный или с glow).

### glow (Deep Glow)

```json
"glow": {"radius": 26, "exposure": 0.85, "threshold": 0.0, "layers": 5, "falloff": 0.62,
         "color": null, "outer": null, "tint": 0.55, "opacity": 1.0}
```
Слои блюра с удвоением радиуса и затуханием энергии (`falloff`), `color` = Tint Inner, `outer` = Tint Outer,
тонмаппинг 1−e^{−x} → без пересвета.

### shadow (Shadow Studio)

```json
"shadow": {"angle": 90, "long": 0.22, "contact": 0.045, "softness": 1.0, "strength": 0.55, "steps": 10, "color": "#000000"}
```
`long` — длина длинной тени (доля кегля), `contact` — короткая контактная, `angle` — направление (90 = вниз).

### counter

```json
{"type": "counter", "from": 0, "to": 200, "prefix": "+", "suffix": "%", "decimals": 0,
 "count_dur": 1.4, "count_delay": 0.15, ...стиль как у text}
```

### subs — субтитры синхронно с речью

```json
{"type": "subs", "start": 3.2, "end": 14.9, "y": 1300, "size": 60, "max_width": 640,
 "max_words": 3, "max_chars": 22, "gap": 0.35, "karaoke": true, "sync": true,
 "modes": ["word", "word_bounce", "letter"], "dirs": ["up", "left", "right", "down"], "shadow": true}
```
Слова берутся из `work/words.json`, режутся на группы (≤ max_words, ≤ max_chars, по паузам и точкам),
каждое слово появляется в момент произнесения, `karaoke` подсвечивает текущее слово. Стили групп чередуются
по спискам `modes`/`dirs`. Звук по умолчанию — нет (иначе щёлкает каждую секунду); включить: `"sfx": "small"`.

## zooms[] — зумы по смыслу (центр — глаза, линия глаз сохраняется)

```json
{"start": 3.9, "end": 5.2, "scale": 1.1, "ease": "smooth", "in": 0.35, "out": 0.3, "riser": false, "sfx": "auto"}
```
`ease`: `smooth` (мягкий наезд), `punch` (резкий удар с пружиной AUSHEV, авто-звук hit), `creep` (медленный наезд на весь отрезок).
`out: 0` — держать до склейки. Масштаб ≤ 1.25 — без потери резкости (запас 1.25× из prepare).

## transitions[] — переходы (набор Animation Composer)

```json
{"at": 5.367, "type": "whip", "dir": "left", "dur": 0.34, "riser": true, "sfx": "auto", "strength": 1.0}
```

| type | что делает |
|---|---|
| `whip` | резкий уход/вход в сторону `dir` с motion blur и зеркальными краями |
| `slide` | то же, мягче (push) |
| `zoom_in` / `zoom_out` | наезд/отъезд с радиальным размытием |
| `spin` | поворот ±70° с размытием (`dir`: left/right) |
| `blur` | расфокус и обратно |
| `glitch` | RGB-сдвиг + сдвиг полос |
| `flash` | вспышка (`color`, `strength`) |
| `luma` | тёплая засветка (light leak) |
| `shake` | тряска камеры после точки |

Ставить на каждую склейку (`--check` предупредит о склейке без перехода); можно и без склейки — как акцент.
`riser: true` — нарастание, которое заканчивается ровно в `at`.

## sfx[] — ручные звуки

```json
{"t": 13.05, "role": "shine"}            // роль: сэмпл подберётся без повтора подряд
{"t": 7.2, "file": "hit_03", "gain": -2}  // конкретный файл
{"t": 9.0, "role": "riser", "align": "end"} // закончиться в t
```

## audio

`sfx_gain_db` — общий уровень эффектов относительно голоса (−3 = чуть ниже голоса), `pitch_jitter` — разброс высоты
в полутонах (±1.2), `vol_jitter_db` — разброс громкости, `seed` — повторяемость выбора сэмплов.
Голос не обрабатывается. Лимитер действует только на эффекты. Музыки нет (поле `music` — ошибка --check).
