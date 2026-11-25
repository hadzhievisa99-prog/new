# Как AE-инструменты повторены в коде

Плагины (.aex/.plugin/.msi) в скилл не входят — они работают только в After Effects. Ниже — что из них
извлечено и где это живёт в коде.

## AUSHEV v1.7.0 (aushev.ae_v1.jsx) → `scripts/text.py`, `scripts/common.py`

В .jsx в Base64 зашиты 13 пресетов .ffx и LiquidGlass.aex. Пресеты декодированы (RIFX-контейнер:
`tdmn` = match name свойства, `cdat` = значения double, `ldat` = ключи, `Utf8` = выражения).

| пресет | что внутри | в коде |
|---|---|---|
| `Word_<Up/Down/Left/Right>` | Text Animator, Range Selector: Units = %, Based On = Words, Shape = Ramp Up, Ease High −50, Offset ключ −100 → 100. Свойства: Position (0,100) для Up (вбок/вниз для других), Opacity 0 | `anim.mode = "word"`: каждое слово едет из `dist` в 0 с ease-out и проявляется, задержка `stagger` |
| `Letter-by-letter_<Dir>` | то же, Based On = Characters | `mode = "letter"` |
| `Word_<Dir>_Bounce` | Expression Selector: `t = (time-inPoint) - Speed*textIndex; amount = 150*cos(2.5*t*2π)/exp(7t)`, Speed = 0.1; Position из слайдеров (≈44 px), Opacity 0 | `mode = "word_bounce"` — та же формула: сдвиг = dist·amount, прозрачность = 1−amount |
| `Bounce` | Scale 0→100, Rotation 28→0 на 15 кадрах + выражение пружины на Position/Scale/Rotation | `bounce` (см. ниже) |

**IN/OUT (`convertToOut`, `mirrorKeys`)**: для OUT ключи зеркалятся во времени (последний ключ становится
первым, самый первый встаёт на Out Point), в выражениях `(time - inPoint)` → `(outPoint - time)`.
В коде OUT-состояние элемента в момент t = IN-состояние в момент `start + (end − t)`. IN и OUT складываются
(смещения суммируются, прозрачности перемножаются) — как два аниматора на одном слое.

**Bounce (`applyBounceToLayer`, `bounceExpression`)**: `BOUNCE_FRAMES = 15`, `BOUNCE_ROT = 28`,
окно ≤ половины длительности слоя (IN и OUT не перекрываются). IN: Scale 0 → исходный, Rotation исходный+28° →
исходный (линейные ключи), затем пружина `value + v·amp·sin(freq·t·2π)/exp(decay·t)`, `amp=0.06, freq=1.8, decay=5`,
v — скорость перед ключом. OUT: исходный → 0 и обратный доворот, раскачка **перед** первым OUT-ключом
(`r - v2·amp·sin(...)` с t2 = key − time). При IN+OUT ключи до середины считаются IN, после — OUT (`split`).
Функция `bounce_state()` в common.py — построчный перенос этого выражения.

## Liquid Glass (LiquidGlass.aex, «TFM LiquidGlass» by ToolsForMotion) → `fx.glass()`

Параметры плагина (из строк бинарника): Source Layer, Glass Body, Refraction (Amount/Strength), Thickness (Blur),
Edge Width, Edge Blur, Feather, Chromatic Aberration, Specular Highlights (Light Angle, Light Color, Highlight
Distance, Intensity), Tint Color / Tint Opacity, Shadow (Distance, Softness, Opacity), Scale.
Реализация: SDF скруглённого прямоугольника (с поворотом/масштабом от Bounce) → маска; фон под плашкой
размывается (Thickness), у края пиксели берутся со сдвигом по нормали (преломление линзы), отдельно по каналам R/G/B
(хроматическая аберрация); тонировка; блик по краю со стороны `light_angle` + тонкая внутренняя линия + слабый
обратный блик; мягкая тень под плашкой. Геометрия кэшируется — статичная плашка почти бесплатна.

## Deep Glow 2 → `fx.glow()`

Параметры из бинарника: Radius, Exposure, Threshold (+Smooth, Mode), Inner/Outer Bias, Tint Color Inner/Outer,
Tint Mode/Strength, Chromatic Aberration, Aspect Ratio, Gamma Correction, Glow Iterations, Quality, Unmult.
Реализация: N слоёв гауссова блюра (радиус удваивается от слоя к слою, вес падает в `falloff` раз) — это и есть
«физичное» затухание Deep Glow; цвет каждого слоя смешивается от `color` (inner) к `outer`; `exposure`; итог
тонмаппится `1 − e^{−x}` и кладётся экраном (screen) — ярко, но без пересвета. Считается в 1/4 разрешения.

## Shadow Studio 3 → `fx.shadow()`

20 пресетов (Long Shadow, Style Drop, Ambient Occlusion, Fake 3D, Illuminate…) с параметрами Distance, Softness,
Strength, Gradient, Renderer (JFA). Реализация: **длинная тень** — ступенчатое «выдавливание» альфы вдоль угла
света с градиентным затуханием (как Gradient) + большой блюр; **контактная тень** — короткий плотный сдвиг с малым
блюром. Берётся максимум двух, умножается на `strength`, затемняет кадр под текстом. Считается в 1/2 разрешения.

## Mister Horse / Animation Composer → `camera.py`

.msi — только менеджер продуктов (пресеты он скачивает из сети), поэтому набор переходов взят по библиотеке
Animation Composer (Transitions: Zoom, Whip/Swipe, Slide/Push, Spin, Blur, Glitch, Flash, Light leak, Shake)
и их приёмам: мотион-блюр за счёт субкадров, зеркальные края (Mirror Edges) вместо чёрных полей, expo-ускорение
уходящего кадра и expo-торможение входящего. Типы: `whip slide zoom_in zoom_out spin blur glitch flash luma shake`.

## Камера и линия глаз → `camera.py`, `track.py`

prepare делает исходник 1.25× от 1080×1920 (2400 px по вертикали). Трекер (OpenCV Haar: лицо + глаза) даёт
центр глаз и межзрачковое расстояние, сглаживает внутри каждого плана (не через склейку). Камера выбирает одну
линию глаз на всё видео (такую, чтобы понадобился минимальный постоянный кроп), один постоянный масштаб
(крупность не скачет) и сдвигает кадр так, чтобы глаза всё время были на линии. Зумы центрированы в глазах, поэтому
линия держится и при наездах.
