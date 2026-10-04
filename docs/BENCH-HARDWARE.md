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
 Хост (Linux/Win) ◄──LAN── UDP 5600 (відео) + UDP 14550 (MAVLink) ◄── Pi 5 «GS» ──USB── RTL8812
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
| Драйвери для 8814 | Див. розділ 9: готового FPV-форка не знайдено; найменш ризикований шлях — вбудований `rtw88_8814au` (ядро ≥ 6.15, не документовано монітор/ін'єкцію) |
| Рекомендація | Базовий стенд: AIR(8812) ↔ GS(8812). 8814 розглядати пізніше, якщо потрібно порівняння |
| Ядро Pi 5 | типово `kernel_2712.img` з 16K сторінками, інакше `kernel8.img` (SRC raspberrypi/documentation `boot.adoc`); DKMS збирається під запущене ядро: HW |
| Живлення USB | Pi 5 дає 1.6 А на USB лише від БЖ на 5 А (25 Вт); «any other compatible power supply… restricts downstream USB devices to 600mA»; або `usb_max_current_enable=1` (SRC power-supplies.adoc) |
| LAN → хост | `GS_FORWARD_IP=<IP хоста>`: wfb-ng шле UDP `connect://<host>:5600` (відео) та `:14550` (MAVLink). Схеми peer лише UDP IPv4 (SRC `services.py`). **Декодує хост**, тож відсутність апаратного H.264 на Pi 5 не заважає (INF) |

## 4. Хост

| Що | Статус / нотатки |
|---|---|
| Відео | на хості `video-rx.sh` (Linux) або еквівалентний `gst-launch`: `udpsrc port=5600 caps=application/x-rtp,...` → depay → `avdec_*` → sink. Windows: OpenIPC має приклад `gstlaunch_on_windows.md` (SRC sandbox-fpv) |
| MAVLink | `gs_mav.py --conn udpin:0.0.0.0:14550`: wfb-ng шле на хост, відповіді йдуть на адресу відправника (SRC Setup-HOWTO про QGC-режим). Один клієнт на порт 14550: QGC і `gs_mav.py` одночасно потребують маршрутизатора (запропоновано `gs-mavlink`, `docs/GS-MAVLINK.md`; UNVERIFIED на залізі) |
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

- **Міст для режиму B:** `bench/tx12_bridge.py` (`--input evdev|stdin|sweep`; без `--confirm-props-off` або `--real-fc-armed-ok` не стартує; dead-man `--deadman-ms`; lock; `--sysid` = `MAV_GCS_SYSID` на FC, SRC `docs/MAVLINK-ROUTER.md` §6). Перевірено лише на loopback (`bench/tx12-bridge-test.sh`); реальний TX12/evdev і FC: HW/UNVERIFIED. **Гвинти знято, батарею й ESC від'єднано, FC лише від USB.** Між wfb-ng і клієнтами може стояти служба `gs-mavlink` (`docs/GS-MAVLINK.md`; не запускалась на залізі, UNVERIFIED).
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

## 9. RTL8814AU: кастомні варіанти драйверів (результат дослідження)

GitHub-сторінки репозиторіїв і issues дали 403, тому прочитано лише `raw.githubusercontent.com`; дат останніх комітів немає (UNVERIFIED).

| Варіант | Що встановлено | Ризик |
|---|---|---|
| вбудований `rtw88_8814au` | у mainline є з **v6.15** (файл `rtw8814au.c`: 404 на v6.14, 200 на v6.15–v6.18); ліцензія GPL-2.0 OR BSD-3-Clause; монітор/ін'єкція не задокументовані (SRC Kconfig/файл) | чи ввімкнено в ядрі Pi OS: UNVERIFIED |
| `lwfinger/rtw88` | README: 8814AU «Testing is needed»; ядра ≥5.4; монітор/ін'єкція не згадані | молодий код |
| `aircrack-ng/rtl8812au` v5.6.4.2 | README: «THESE DRIVERS IS DEPRECATED. Use ... lwfinger/rtw88»; заголовок «RTL8812AU/21AU and RTL8814AU», значки «monitor mode working» / «frame injection working» | застарілий; на Pi 6.12.96–6.12.109 ламався (aircrack#1270, SRC із попереднього етапу) |
| `OpenHD/rtl8812au` | README ідентичний aircrack v5.6.4.2 (дзеркало) | патчів FPV на рівні README немає |
| `svpcom/rtl8812au` v5.2.20 | README: «Supports Realtek 8811, 8812, 8814 and 8821» і опис TX power; але в Makefile `CONFIG_RTL8814A = n`; wfb-ng README згадує лише 8812AU/EU | чи працюють wfb-патчі з 8814: UNVERIFIED |
| `morrownr/8814au` | **README у `main` належить форку `joseguzman1337/8814au`**, у ньому «Codex (GPT-5)», монітор у списку режимів, скрипт `tools/injection-selftest.sh`, явного «ін'єкція працює» немає | **походження коду неясне**: скрипт тепер відмовляється збирати його без `ALLOW_UNPINNED=1` |
| Окремий FPV-форк 8814 (wfb/OpenHD/OpenIPC) | **не знайдено** | |

Скрипт: `DRIVER=8814au` (вбудований, без збірки; завершується з помилкою, якщо модуля немає) або `DRIVER=8814au-morrownr` + `ALLOW_UNPINNED=1`. wfb-ng адаптер не бачить автоматично: `WFB_NICS` вручну.

### Тест A/B на ін'єкцію (PROPOSAL)
1. Еталон: AIR(8812) → GS(8812), фіксований канал, MCS, відстань; 10 хв, записати втрати/RSSI з `wfb-cli gs`.
2. Підставити 8814 лише як **RX** на GS (те саме), повторити.
3. Поміняти ролі: 8814 як **TX** на AIR, 8812 як RX, повторити (перевіряє саме ін'єкцію).
4. Записати `ethtool -i`, `dmesg`, реальну потужність/RSSI. Рішення про 8814 ухвалювати за різницею втрат відносно еталона.

## 10. TX12 MKII: прошивка для нестандартних задач (результат дослідження)

| Пункт | Статус |
|---|---|
| Поточний EdgeTX | **v2.12.4 «Queen Anne's Revenge» (2026-09-02, Latest)**; nightly 2026-09-30; v2.11.7 (2026-08-14) для серії 2.11. Для STM32 F2 розробка закінчилась на 2.11 (SRC github.com/EdgeTX/edgetx/releases, перечитано; підсумок сторінки) |
| TX12 MKII у списку підтримуваних, процедура прошивання | UNVERIFIED (список релізу не прочитано) |
| USB-джойстик | classic: Ch1–8 осі, Ch9–32 кнопки; advanced: Joystick / Gamepad / MultiAxis, режим на канал (SRC manual.edgetx.org/bw-radios/model-select/setup) |
| Тренерський вхід | Master/Jack, Slave/Jack, Master/SBUS Module, Master/CPPM Module, **Master/Serial**, **Master/Bluetooth**, **Slave/Bluetooth**, Master/Multi, **Master/CRSF** (SRC, там само); Master/Serial = вхід тренера через AUX-порт у режимі SBUS trainer |
| AUX serial, USB serial/CLI, Lua (API, обмеження пам'яті), Yaapu | UNVERIFIED (сторінки не дійшли) |
| Форки EdgeTX з MAVLink/ELRS, статус OpenTX | UNVERIFIED (не шукали) |
| Документований режим ELRS «USB-хост → TX-модуль» | **не знайдено** |
| Хост-утиліта `snokvist/joystick2crsf` | Linux evdev → CRSF по UDP, може віддавати канали як MAVLink, зразок з серійним пристроєм 420000 бод; остання зміна 2026-07-11, 1 зірка; **ліцензія «Autod Personal Use License»: лише особисте некомерційне використання, без розповсюдження і без розповсюдження похідних** (SRC, прочитано). У репозиторій включати не можна |

Висновок (PROPOSAL): «окремої сучасної прошивки» для TX12 MKII я не знайшов; практичний шлях — сам EdgeTX (Lua, трейнер, режими USB) та режими A/B із розділу 5. Підключення PC напряму до JR-слотового модуля потребує окремого дослідження обладнання (одно-провідний інтерфейс модуля), яке не виконано.

## 11. TX12 MKII: українська прошивка і розширення (результат другого дослідження)

**Висновок:** окремої «української прошивки з розширеним функціоналом» не існує. «Українська збірка» — це офіційний EdgeTX, зібраний із прапором мови `UA`. Розширення робляться через Lua або через локальну збірку з додатковими прапорами.

| Пункт | Статус |
|---|---|
| Українська мова меню | **є**: `RADIO_LANGUAGES ... UA`, `TTS_LANGUAGES ... UA` у `radio/src/CMakeLists.txt`, `TRANSLATIONS` обирає мову; файл `translations/i18n/ua.h` (SRC, перечитано). У `ua.h` 938 з 1283 `TR_`-рядків із кирилицею (≈73%, мій підрахунок): переклад здебільшого повний, решта англійською. Автор у заголовку не вказаний (заглушка) |
| Голосові пакети | у списку мов `edgetx-sdcard-sounds` є Ukrainian; встановлення: папка мови в `SDCARD/SOUNDS/` (SRC README) |
| CloudBuild для TX12 MKII | ціль `tx12mk2`, версія ≥ v2.8.0, теги `stdlcd` + `bluetooth`; прапор `language` → `TRANSLATIONS` зі значенням `UA` (SRC `EdgeTX/cloudbuild/targets.json`, перечитано) |
| Прошивання українською | за гайдом fpvua.org: buddy.edgetx.org → вкладка CloudBuild → `language: UA` → прошити по USB; потім на вкладці «SD Card content» обрати голосовий пакет. Гайд: [fpvua.org/threads/nalashtuvannya-ukrains-koho-interfeysu-na-edgetx.269/](https://fpvua.org/threads/nalashtuvannya-ukrains-koho-interfeysu-na-edgetx.269/) (SRC від агента) |
| Ризик | у сніпеті згадано, що UA-збірка для TX12MK2 могла не завантажуватись: UNVERIFIED. Спершу на запасному радіо, з резервною копією і знанням відновлення завантажувача |
| Рендеринг кирилиці на ч/б 128×64 | UNVERIFIED (файли шрифтів ч/б не знайдені); перевірити в Companion-симуляторі |
| Українські ресурси fpvua.org | розділ EdgeTX, українська інструкція TX12 MK II (`threads/…tx12-mk-ii-instruktsiya…36/`), Lua-скрипт телеметрії для 128×64 (`…vb-telemetry.1492/`); сторінки відкрито лише частково |

### Розширений функціонал: відкритий PR з MAVLink

**[EdgeTX#7832](https://github.com/EdgeTX/edgetx/pull/7832) «MAVLink2 external rf»** (автор slik, відкритий, 2026-09-26, база `main`; SRC, перечитано): прапор `-DMAVLINK=ON` (за замовчуванням вимкнений); RC передається як `RC_CHANNELS_OVERRIDE`; телеметрія з `HEARTBEAT`, `SYS_STATUS`, `GPS_RAW_INT`, `ATTITUDE`, `VFR_HUD`, `RADIO_STATUS`; Lua-прив'язки `elrs_mav.lua` для налаштування ELRS. Для TX12 mk2 «require something to be turned off to fit, for example `-DGHOST=NO -DDSMP=NO`». Рецензент просить зробити MAVLink `sysid` налаштовуваним. Не злитий: **експериментальний**. Стосується радіо із зовнішнім ELRS-модулем у режимі MAVLink; для ES900TX умови (ESP-пара, ELRS ≥ 3.5.0) див. розділ 5.

CloudBuild дає лише фіксований перелік прапорів, довільні CMake-опції (як `MAVLINK`) потребують локальної збірки (CMake, `-DPCB=X7 -DPCBREV=TX12MK2` за старою wiki: **перевірити за поточною документацією**).

### Без заміни прошивки (SRC; перечитано в коді EdgeTX та manual)

| Можливість | Деталі |
|---|---|
| Lua API (у коді `api_general.cpp`) | `crossfireTelemetryPush/Pop`, `sportTelemetryPush/Pop`, `serialWrite/Read`, `setSerialBaudrate`, `multiBuffer`; чи всі скомпільовані саме в TX12 MKII: UNVERIFIED. Довідника API в manual немає |
| Типи скриптів | міксові (`/SCRIPTS/MIXES/`), функціональні (`/SCRIPTS/FUNCTIONS/`); сторінки телеметрійних/one-time скриптів для ч/б у manual не знайдено; ліміти пам'яті не вказані |
| AUX-порт | варіанти: Off, **Telem Mirror**, **Telemetry In**, **SBUS Trainer**, SBUS Trn Inv., **LUA**, GPS, CLI; USB-VCP. Чи є AUX у TX12 MKII: UNVERIFIED |
| Спеціальні функції | **Play Track**, **SD Logs** (CSV, інтервал 0–25.5 с, не стартує при <50 МБ на карті), **SetFailsafe** (потрібен кастомний failsafe), Play Value, Screenshot |
| ELRS Lua-скрипт | Packet Rate, Telemetry Ratio, TX Power, Dynamic power, Switch Mode, Model Match, VTX Administrator, Bind Mode, WiFi Connectivity, Backpack; кнопки Bind, Set Failsafe Pos, BLE Joystick (SRC expresslrs.org/quick-start/transmitters/lua-howto/). Ім'я файлу `elrsV3.lua` не підтверджене |
| Телеметрія ArduPilot | нативний CRSF + розширення для Yaapu («limited number» нативних сенсорів); при `RC_OPTIONS` біт 8 (passthrough) жоден SERIAL-порт не має бути `SERIALx_PROTOCOL = 10` (SRC ardupilot.org). Yaapu має варіанти для ч/б 128×64 (скріншоти в README), працездатність passthrough на ч/б не підтверджена |

### Рекомендація (PROPOSAL)
1. Лишитись на офіційному EdgeTX v2.12.x; персональні форки не перевірені.
2. Українська: CloudBuild `language: UA` на запасному радіо; голосовий пакет `ua`; перевірити кирилицю в симуляторі.
3. MAVLink із радіо: окрема експериментальна локальна збірка PR #7832, прив'язана до тега релізу.
4. Решту розширень — через Lua/спецфункції, а не патчі прошивки.
