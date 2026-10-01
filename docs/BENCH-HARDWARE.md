# Стенд з реальним обладнанням: Pi 4 (AIR) · Pi 5 (GS) · хост

Доповнює `bench/README.md` і `docs/CHAINS.md`. Позначки: **SRC** (прочитано з першоджерела), **SNIP** (з підсумку/сніпета), **INF** (мій висновок), **UNVERIFIED**, **HW** (потрібна перевірка на залізі).
Станом на 2026-10-01; жодне радіо чи пристрій тут не запускалися.

## 1. Топологія

```
                       ┌──────────── Pi 4 «AIR» ────────────┐
 UVC-вебкамера ──USB──►│ video-src.sh (SOURCE=webcam)       │
 FC (ArduPilot) ─USB──►│ wfb-ng drone_mavlink = serial:ttyACM0│──USB── RTL8812 ~~~ wfb-ng RF
 (UART◄─ ELRS RX)      │ [OpenIPC-емуляція]                  │
                       └─────────────────────────────────────┘                  ~~~
                                                                                 ▼
 Хост (Linux/Win) ◄──LAN── UDP 5600 (відео) + UDP 14550 (MAVLink) ◄── Pi 5 «GS» ──USB── RTL8814
   ├─ декодування відео (gst-launch / VLC / QGC)
   ├─ gs_mav.py / QGC / MAVProxy
   └─ TX12 MKII + ES900TX ~~~ 900 МГц (ELRS) ~~~► ELRS RX ──UART(CRSF)──► FC   ← основний RC, минає GS
```

Два **незалежні** радіоканали: wfb-ng (5 ГГц-діапазон адаптера, відео + MAVLink) і ELRS 900 МГц (RC).

## 2. Вузол AIR (Pi 4)

| Підключення | Статус / нотатки |
|---|---|
| Приймач ES900RX (Happymodel) | ESP8285 + SX1276, живлення 5 В (~100 мА), антена IPEX/U.FL, потужність телеметрії <17 dBm, 12×12×3 мм, 0.6 г, вихід CRSF; прошивка через pass-through або WiFi (SRC: [fpvua.org](https://fpvua.org/resources/happymodel-expresslrs-es900rx.57/), прочитано). Діапазон на сторінці вказано як «750MHz/915MHz», схоже на помилку: **варіант (868/915) приймача й ES900TX мають збігатися** (UNVERIFIED). ESP-чип дає змогу в принципі використати нативний MAVLink ELRS (вимога: ESP-передавач і ESP-приймач, SRC expresslrs.org); саме для ES900RX це INF. Сторінка приймача на expresslrs.org дала 404 |
| ELRS RX → UART FC (CRSF) | ArduPilot: `SERIALx_PROTOCOL=23` для порту приймача; «ExpressLRS systems use the CRSF protocol and connect identically» (SRC ardupilot.org/copter/docs/common-crsf-telemetry.html) |
| FC → USB Pi | У ArduPilot «Serial Port 0 is always assigned to the USB port», MAVLink2 за замовчуванням (SRC common-serial-options). Ім'я пристрою `/dev/ttyACM0` не підтверджене документацією ArduPilot: UNVERIFIED, перевірте `dmesg` |
| wfb-ng ↔ FC | `peer = 'serial:ttyACM0:115200'` (форма з Setup-HOWTO; синтаксис підтверджує `serial_re` у `services.py`). Без пристрою wfb-ng **не стартує** (SRC) |
| RTL8812 → USB | драйвер svpcom (див. `docs/PI-PORT.md`), `DRIVER=8812au` |
| Вебкамера UVC → USB | `SOURCE=webcam`; `v4l2src` має `device`, `extra-controls` (SRC GStreamer). MJPEG-режим камери (`WEBCAM_FORMAT=jpeg`) типовий для UVC: INF |
| Апаратне кодування `v4l2h264enc` | елемент існує як «Hardware» (SRC), але наявність HW-кодера на Pi 4 і його контролі не підтверджені (www.raspberrypi.com — 403): **EXPERIMENTAL**, типово `x264enc` |

Скрипти: у `bench/env` задайте `SOURCE=webcam`, `FC_SERIAL=ttyACM0`; тоді `fake_fc.py` не встановлюється.

## 3. Вузол GS (Pi 5)

| Підключення | Статус / нотатки |
|---|---|
| **Рішення: на GS ставимо RTL8812 (AU або EU)** | Те саме сімейство, що на AIR, і єдине, що wfb-ng офіційно підтримує («we officially support only cards on Realtek RTL8812au and RTL8812eu», SRC wiki WiFi-hardware). Автор радить BL-M8812EU2 (SRC Setup-HOWTO). Вибір приймача ES900RX на це не впливає: радіо ELRS 900 МГц і wfb-ng незалежні. `DRIVER=8812au` (закріплений) або `8812eu` (не закріплений) |
| RTL8814AU → USB (лише альтернатива) | **Найбільший ризик.** wfb-ng: «8811*, 8812bu, 8812cu or 8814au are different cards and not supported by author. They may work but it at your own risk.» (SRC, прочитано мною: wfb-ng wiki WiFi-hardware, рядок 5). `install_gs.sh` автовизначає лише `rtl88xxau_wfb` та `rtl88x2eu`, тож `WFB_NICS` задається вручну (SRC, `install_gs.sh`) |
| Драйвери для 8814 | (a) `svpcom/rtl8812au` v5.2.20: `CONFIG_RTL8814A = n` у Makefile, код є, але вимкнений; чи працюють wfb-патчі з 8814 — UNVERIFIED. (b) `morrownr/8814au` (скрипт `DRIVER=8814au`): діапазон ядер за README 5.4–6.18.x, монітор є, ін'єкцію README не заявляє; автентичність README перевірити (агент бачив посилання на інший форк). (c) вбудований `rtw88_8814au`: існує в ядрі (коміт 2025-03), чи є в ядрі Pi OS — UNVERIFIED |
| Рекомендація | Базовий стенд: AIR(8812) ↔ GS(8812). 8814 розглядати пізніше, якщо потрібно порівняння |
| Ядро Pi 5 | типово `kernel_2712.img` з 16K сторінками, інакше `kernel8.img` (SRC raspberrypi/documentation `boot.adoc`); DKMS збирається під запущене ядро: HW |
| Живлення USB | Pi 5 дає 1.6 А на USB лише від БЖ на 5 А (25 Вт); «any other compatible power supply… restricts downstream USB devices to 600mA»; або `usb_max_current_enable=1` (SRC power-supplies.adoc) |
| LAN → хост | `GS_FORWARD_IP=<IP хоста>`: wfb-ng шле UDP `connect://<host>:5600` (відео) та `:14550` (MAVLink). Схеми peer лише UDP IPv4 (SRC `services.py`). **Декодує хост**, тож відсутність апаратного H.264 на Pi 5 не заважає (INF) |

## 4. Хост

| Що | Статус / нотатки |
|---|---|
| Відео | на хості `video-rx.sh` (Linux) або еквівалентний `gst-launch`: `udpsrc port=5600 caps=application/x-rtp,...` → depay → `avdec_*` → sink. Windows: OpenIPC має приклад `gstlaunch_on_windows.md` (SRC sandbox-fpv) |
| MAVLink | `gs_mav.py --conn udpin:0.0.0.0:14550`: wfb-ng шле на хост, відповіді йдуть на адресу відправника (SRC Setup-HOWTO про QGC-режим). Один клієнт на порт 14550: QGC і `gs_mav.py` одночасно потребують маршрутизатора |
| Брандмауер | дозволити UDP 5600 і 14550 (INF) |
| RC-мікс: див. розділ 5 | |

## 5. TX12 MKII + ES900TX: два режими, які НЕ працюють одночасно

- **ES900TX** — це Happymodel, не EMAX (SRC expresslrs.org/quick-start/transmitters/es900tx). ESP32+ESP8285, 55×39×11 мм, SMA, до 5–13 В живлення, потужність <33 dBm, вентилятор (SNIP, роздріб). Пара — ES900RX, та сама binding phrase (SRC). USB — для прошивки через UART (SRC), не канал керування. Backpack/Bluetooth на ES900TX: UNVERIFIED.
- TX12 MKII має JR-сумісний слот (SRC radiomasterrc.com).
- **У режимі USB-джойстика EdgeTX обидва RF-модулі мають бути вимкнені** («both internal and external RF modules should be turned off», SRC manual.edgetx.org, перечитано). Тому:

| Режим | Канал RC | Для чого |
|---|---|---|
| **A, основний** | TX12 → ES900TX → ES900RX → FC (радіо ELRS), USB до хоста не потрібен | реалістичний RC із failsafe приймача |
| **B, експериментальний резерв** | TX12 USB-джойстик → хост → `RC_CHANNELS_OVERRIDE` → GS → wfb-ng → Pi 4 → FC USB; ELRS мовчить | перевірка RC-через-MAVLink; ризик ArduPilot#32862 |

- Не змішуйте A і B в одній моделі EdgeTX. BLE-джойстик належить внутрішньому ELRS-модулю: з зовнішнім ES900TX його наявність UNVERIFIED (INF: ні).
- Windows: радіо може визначатись лише як джойстик незалежно від вибору; на Linux/macOS/Android працює правильно (SRC).
- Налаштування зовнішнього модуля в EdgeTX (CRSF, baud, power, Lua): сторінки дали 404, UNVERIFIED.
- **Дублікати телеметрії.** Якщо ввімкнути нативний MAVLink ELRS (потрібні ESP-приймач і ESP-передавач, на 900 МГц радять 200 Гц, співвідношення телеметрії 1:2; SRC expresslrs.org/software/mavlink), хост отримає ті самі `sysid` двома шляхами. Для стенду просто лишити ELRS у режимі CRSF (лише RC), MAVLink тільки через wfb-ng.

## 6. Що упущено (перелік обладнання)

| Вузол | Що додати | Підстава |
|---|---|---|
| AIR | Живлення адаптера окремо від USB Pi: wfb-ng радить 5 В ≥5 А BEC, конденсатор ≥470 мкФ, радіатор із вентилятором | SRC Setup-HOWTO (там для дрона/BL-M8812EU2; на столі з мінімальною потужністю вимоги м'якші: INF) |
| AIR | Живлений USB-хаб або БЖ 5 В/3 А для Pi 4 з камерою, FC і адаптером | загальна практика; ліміт USB Pi 4: UNVERIFIED |
| AIR | Короткі екрановані USB-кабелі | SRC: тонкі кабелі дають FEC-помилки |
| AIR | Антени на адаптері й на ES900TX **до** увімкнення | SRC (wfb-ng: не вмикати адаптер без антен); для ES900TX: INF |
| AIR | FC: без гвинтів, без батареї/ESC; живлення від USB | SRC arming_the_motors / raspberry-pi-via-mavlink (прибрати гвинти) |
| AIR | GPS немає в приміщенні: `ARMING_NEED_LOC`, «waiting for home» ; параметр `ARMING_CHECK` у поточній документації відсутній, є `ARMING_SKIPCHK` | SRC ardupilot.org |
| AIR | Альтернатива реальному FC: ArduPilot SITL | сторінка SITL існує (SRC), інтеграція не перевірена |
| AIR | Консоль (USB-UART) і мікро-SD гарної якості | INF |
| GS | БЖ 5 А (25 Вт) для Pi 5 | SRC (див. вище) |
| GS | Охолодження | INF; Pi 5 не перевірено |
| Усі | Окремий Ethernet для керування (SSH) | INF |
| Радіо | Антени на відстані 1–2 м, мінімальна потужність; атенюатори за потреби | INF (PROPOSAL) |
| Радіо | Дозволений регіон і потужність 5 ГГц та 868/915 МГц (на ES900TX регіон обирається у прошивці) | SRC (прошивка вимагає Regulatory Domain); правила вашої країни — ваша відповідальність |
| Хост | GStreamer (Linux/Win), QGC/Mission Planner за потреби | INF |
| Хост | Синхронізація часу NTP на всіх вузлах для логів | INF |

## 7. Чого справжній OpenIPC має, а емуляція ні (SRC: OpenIPC/adaptive-link, mavfwd, msposd, wiki fpv.md)

| Компонент | Що робить у справжньому OpenIPC | Емуляція |
|---|---|---|
| majestic | RTP H.264/H.265 у wfb-ng; GS приймає на `5600`, `payload=96` | `video-src.sh`; реальний порт виходу majestic UNVERIFIED (ми беремо `5602` з wfb-ng Setup-HOWTO) |
| alink_drone | змінює MCS/FEC командами `wfb_tx_cmd 8000 set_radio -B 20 -G .. -S 1 -L 1 -M N` та `set_fec`; бітрейт/GOP через `curl http://localhost/api/v1/set?...`, IDR через `nc localhost 4000`, потужність через `iw`; при втраті heartbeat профіль 999 (MCS 0); профілі в `/etc/txprofiles.conf`, типово 4 Мбіт/с, MCS1, FEC 8/12, 20 МГц | **немає**. Окремий wfb_tx на `link id 7669207`, UDP `9999`/`9998` |
| mavfwd | UART↔UDP, у т.ч. текст у GCS та інжекція температури SoC | замінено на `serial:` peer wfb-ng |
| msposd | MSP з UART → UDP `14555`, малює OSD | **немає** |
| конфіги | `/etc/wifibroadcast.cfg`, `datalink.conf` (`telemetry=true`), `telemetry.conf` (`one_way`), `wfb.conf` (канал) | повний вміст UNVERIFIED |

Висновок: стенд перевіряє транспорт, драйвери й ланцюжок MAVLink/RC, але **не адаптивну лінію** OpenIPC. Для неї потрібна окрема фаза.

## 8. Відкрите і ризики

1. RTL8814AU на GS поза підтримкою wfb-ng, ін'єкція не підтверджена (ризик 1).
2. Драйвери Realtek на 16K-сторінковому ядрі Pi 5: HW.
3. HW-кодер H.264 на Pi 4, `extra-controls` для `v4l2h264enc`: UNVERIFIED.
4. Налаштування зовнішнього модуля EdgeTX, Backpack/Bluetooth ES900TX.
5. Чи є `rtw88_8814au` у ядрі Pi OS і з якої версії ядра.
6. Ліміти USB-струму Pi 4, interference USB3 ↔ 2.4 ГГц: не підтверджено джерелами (raspberrypi.com — 403).
