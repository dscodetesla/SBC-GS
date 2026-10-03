# Контур AIR: WiFiLink2 + ArduPilot (Matek H743) + ELRS Gemini Xrossband

> Рішення 2026-10-03 (`docs/DECISIONS.md` R2): робочий контур AIR/RC = **V-900** (приймач 900 МГц + зовнішній ELRS-900 TX), Xrossband лише якщо наклейка приймача це підтвердить; нижче збережено повний аналіз обох варіантів.

Статус: **дослідницький документ, станом на 2026-10-03.** На залізі нічого не перевірялося. Стартові значення лежать у `config/contour/air.example.env` (усе там «стартова точка, перевірити на стенді»).
Позначки: **SRC** прочитано з першоджерела (файл/сторінку отримано напряму), **REPO** перевірено в цьому репозиторії, **SNIP** зі сніпета пошуку/роздрібної сторінки (не підстава для рішень), **INF** наш висновок, **HW** потребує заліза, **UNVERIFIED** не вдалося підтвердити.

Цільовий контур AIR (з формулювання користувача, не змінюю): камера + «WiFiLink2» (радіо RTL8812A/AU, за словами користувача), відео оптимізоване під 720p, OpenIPC оптимізований, FC ArduPilot на Matek H743 v3, ELRS «Dual Band Xross Gemini RX» з 4 антенами.

## 1. Ключові розбіжності з тим, що вважалося раніше

1. **Радіо в самому WiFiLink2 не RTL8812AU, а «Runcam custom RTL8812EU»** (5/10/20 МГц; 40 МГц лише прийом). RTL8812AU у комплекті «WiFiLink2-G» є **наземним** адаптером для Android (SRC: [docs.openipc.org/hardware/runcam/vtx/runcam-wifilink-v2](https://docs.openipc.org/hardware/runcam/vtx/runcam-wifilink-v2/), [docs.openipc.org/use-cases/fpv/net-cards/rtl8812eu](https://docs.openipc.org/use-cases/fpv/net-cards/rtl8812eu/)). Тобто «8812AU на борту» **не підтверджується**: AIR = EU, GS = AU або EU. Висновок для wfb-ng: обидва сімейства підтримані (SRC, wiki WiFi-hardware, цитовано в `docs/BENCH-HARDWARE.md:41`), але 40 МГц на AIR-передачі недоступні.
2. **Приймач ELRS не 900 МГц-only ES900RX**, а двохрадіо-приймач на LR1121 (2.4 ГГц + 900 МГц). Це суперечить таким місцям попередніх документів:

| Файл:рядок | Що там сказано |
|---|---|
| `docs/BENCH-HARDWARE.md:18, 21` | схема «ES900TX ~~~ 900 МГц (ELRS)», «два незалежні канали: wfb-ng і ELRS 900 МГц» |
| `docs/BENCH-HARDWARE.md:27` | «Приймач ES900RX (Happymodel) ESP8285 + SX1276» |
| `docs/BENCH-HARDWARE.md:41` | вибір приймача ES900RX «не впливає» на вибір GS-адаптера (для 900 МГц так; для 2.4 ГГц див. розділ 8) |
| `docs/BENCH-HARDWARE.md:58-73` (розділ 5) | усе про ES900TX/ES900RX, пара за варіантом 868/915 |
| `docs/BENCH-HARDWARE.md:73` | «на 900 МГц радять 200 Гц, співвідношення 1:2» для нативного MAVLink |
| `docs/BENCH-HARDWARE.md:82, 91, 112` | антени/регіон «на ES900TX», 868/915 МГц |
| `docs/CHAINS.md:54, 71` | «ELRS (ES900, 868/915 МГц)», «канал ELRS для ES900 це 868/915 МГц» |
| `docs/GUIDE.md:3, 12, 21, 24, 28` | «TX12 MKII + ES900TX», «ES900RX на UART FC», вимога збігу 868/915 |
| `docs/GUIDE.md:157-158` | «ES900RX... Живлення 5 В (fpvua.org)», прошивки ES900TX/RX |
| `docs/KNOWLEDGE.md:56, 71-73` | ES900TX/RX як факти стенду; рядки таблиці виправлень |
| `docs/GAPS.md:64` (E2) | співіснування «ELRS 900 МГц, wfb-ng 5 ГГц» |
| `docs/SIM-MODELS.md:160` | «ELRS ES900 в 868/915 МГц... не є завадою для wfb-ng» |
| `docs/STATUS.md:62` | перелік обладнання «TX12 MKII + ES900TX (і ES900RX)» |

Окремо: `docs/GUIDE.md:21` описує AIR як Raspberry Pi 4. Це **емуляція стенду** (`bench/`), реальний AIR тепер камера на SigmaStar, не Pi (розділ 10).
Ці документи я не редагував (поза моїм обсягом); це перелік для виправлення.

## 2. Компоненти: що перевірено

### 2.1 «WiFiLink2» = RunCam WiFiLink 2 (OpenIPC) (SRC)

Усе нижче з [docs.openipc.org/hardware/runcam/vtx/runcam-wifilink-v2](https://docs.openipc.org/hardware/runcam/vtx/runcam-wifilink-v2/) (прочитано), дубль: [openfpv.com.ua](https://openfpv.com.ua/en/hardware/vtx/runcamwifilinkv2).

| Параметр | Значення |
|---|---|
| SoC | SigmaStar SSC338Q |
| Сенсор | Sony IMX415, FOV 160°; роздрібний опис: 1080p60/1080p90/720p120 (SNIP, магазини) |
| Wi-Fi | Runcam custom RTL8812EU, 5/10/20 МГц; PA 28 дБм FCC / 20 дБм CE (630 / 100 мВт) |
| Живлення | DC 9-22 В, «BEC recommended, прямо від LiPo не рекомендовано» |
| Інтерфейси | 1 UART (до FC), Ethernet (кабель у комплекті), microSD |
| Антени | IPEX; модуль «IPEX x2» |
| Прошивка | `ssc338q_fpv_openipc_urllc_aio_nor.tgz`; адреса в камері `192.168.1.10`, `root`/`12345` (SRC docs.openipc.org advanced-setup) |
| Розміри/маса | 30.6×33 мм, 30 г з вентилятором |
| Індикація | синій швидко блимає = помилка Wi-Fi; синій+зелений по черзі = перегрів > 90 °C |
| GS у документації RunCam | Android PixelPilot + RTL8812AU, канал **161**, ширина 20 |

Що **не** знайдено: офіційна документація RunCam (не читалась), споживаний струм, USB ID саме RunCam-варіанта (скрипт прошивки знає `0bda:a81a` для BL-M8812EU2; чи збігається ID RunCam-модуля, UNVERIFIED), точний набір режимів сенсора для 720p (UNVERIFIED).

**Драйвер на камері** вибирає скрипт прошивки за USB ID: `88XXau` (`0bda:8812`, `0bda:881a`...), `8812eu` (`0bda:a81a`), `8733bu` (SRC: [OpenIPC/firmware wifibroadcast](https://raw.githubusercontent.com/OpenIPC/firmware/master/general/package/wifibroadcast-ng/files/wifibroadcast)). Для користувача це означає: **на AIR драйвер не ставимо**, він у прошивці. Драйвер потрібен лише на GS (`DRIVER=8812au` у `bench/env.example` або `8812eu`).

### 2.2 FC: «Matek H743 v3» (SRC hwdef + ardupilot.org)

Плата належить сім'ї **H743-WING / SLIM / MINI / WLITE**, що збирається з одного каталогу `MatekH743` ([hwdef.dat](https://raw.githubusercontent.com/ardupilot/ardupilot/master/libraries/AP_HAL_ChibiOS/hwdef/MatekH743/hwdef.dat), прочитано; сторінка [ardupilot.org common-matekh743-wing](https://ardupilot.org/copter/docs/common-matekh743-wing.html), прочитано). У hwdef коментар: «H743-V3: ICM42688P, ICM42605» (дві IMU), барометр DPS310 на I2C, вбудованого компаса немає (зовнішній, автопошук на I2C). **Який саме варіант (WING V3, SLIM V3, MINI V3)** під назвою «v3»: UNVERIFIED, піни різняться за варіантами. Є окрема прошивка `MatekH743-bdshot` (SRC hwdef: `include ../MatekH743/hwdef.dat`, USART6 як чистий UART).

| Порт ArduPilot | UART | Типове призначення (SRC) | Примітка |
|---|---|---|---|
| SERIAL0 | USB (OTG1) | консоль | |
| SERIAL1 | UART7 | Telemetry1, підтримує CTS/RTS | кандидат для WiFiLink2 |
| SERIAL2 | USART1 | Telemetry2 | кандидат для WiFiLink2 |
| SERIAL3 | USART2 | GPS1 | |
| SERIAL4 | USART3 | GPS2 | |
| SERIAL5 | UART8 | user | вільний |
| SERIAL6 | UART4 | user | вільний |
| SERIAL7 | USART6 | user/RC (TX only, поки не `BRD_ALT_CONFIG=1`) | RC-вхід |

- Порядок `SERIAL_ORDER OTG1 UART7 USART1 USART2 USART3 UART8 UART4 USART6 OTG2` (SRC hwdef.dat) збігається з таблицею документації.
- **CRSF/ELRS** потребує справжнього UART із RX+TX (SRC [common-rc-systems](https://ardupilot.org/copter/docs/common-rc-systems.html), [common-matekh743-wing](https://ardupilot.org/copter/docs/common-matekh743-wing.html)). Pin RX6 типово таймерний вхід і CRSF не тримає: або `BRD_ALT_CONFIG=1` (тоді USART6 = SERIAL7, `SERIAL7_PROTOCOL=23`, `SERIAL7_OPTIONS=0`; PPM не працює), або вільний UART (SERIAL5/6) з `SERIALx_PROTOCOL=23`. Швидкість порту виставляється прошивкою автоматично (SRC common-rc-systems).
- На `bdshot`-прошивці USART6 RX уже чистий UART і вимагає `SERIAL7_PROTOCOL=23` (SRC common-matekh743-wing).
- **Живлення (SRC common-matekh743-wing):** вхід 9-36 В (отже мінімум 3S), BEC 5 В 2 А (периферія), 9/12 В 2 А «для відео», 5/6/7.2 В 8 А (сервo); струмовий датчик у WING V2/V3 і WLITE (`BATT_AMP_PERVLT` 66.7 для WING V2/V3). Для WiFiLink2 (9-22 В): **вхід 9-22 В обмежує батарею 3S-5S**, якщо бортову 12 В шину не беруть від BEC (INF із двох специфікацій). Чи 12 В BEC в варіанті v3 потужніший за 2 А: UNVERIFIED.
- Датчик струму чутливий до шуму ESC, потрібен поставлений конденсатор (SRC там само).

### 2.3 Приймач «Dual Band Xross Gemini» з 4 антенами (частково SRC, модель UNVERIFIED)

**Що таке Gemini (SRC [expresslrs.org/software/gemini](https://www.expresslrs.org/software/gemini/), прочитано):**
- Gemini (одна смуга): TX передає пакет одночасно на двох частотах (2.4 ГГц: рознесені на 40 МГц; 900 МГц: ~10 МГц), true-diversity RX слухає обидва. Дальності **не** збільшує («No»): тримає вищий LQ до самого failsafe.
- **Gemini Xrossband (GemX):** два LR1121; один тракт 900 МГц (Ant1), другий 2.4 ГГц (Ant2), **одночасна** передача в обох смугах (режими X150Hz, X100Hz Full). Потрібен GemX-сумісний приймач; однобандовий RX синхронізації не отримає.
- Gemini-RX із не-Gemini TX: слухає обома антенами, синхронізується лише на одній.

**Кандидати за ExpressLRS/targets** ([targets.json](https://raw.githubusercontent.com/ExpressLRS/targets/master/targets.json), прочитано): назва «Gemini Xrossband» є в багатьох: RadioMaster XR4 (`Unified_ESP32_LR1121_RX`, мін. ELRS 3.5.0, прошивка через uart/wifi/betaflight), DBR4-TD, ER3pro/ER12/ER16; GEPRC «900/2400 Gemini Xrossband RX», «Gemini XrossBand PA 2.4/900 RX», BAYCKRC, BrotherHobby, DAKEFPV. **Модель користувача не встановлена** (UNVERIFIED). Найімовірніший XR4 (INF): його роздрібний опис «dual LR1121, ESP32, 2 дводіапазонні T-антени, 5-12.6 В, 100 мВт телеметрії, 22×18×4 мм, 1.7 г» (SNIP, [oscarliang](https://oscarliang.com/radiomaster-xr1-xr2-xr3-xr4-elrs-receivers/) прочитано як огляд, не специфікація виробника). «4 антени» = 2 T-антени по 2 елементи (INF; UNVERIFIED), якщо це XR4.

**Вихід:** CRSF по UART (SRC ardupilot.org common-crsf-telemetry: «ELRS systems use the CRSF protocol and connect identically»). Є також нативний MAVLink з `3.5.0` (SRC expresslrs.org/software/mavlink): у нашому контурі його не вмикати (дубль телеметрії із wfb-ng).

**Пакетні рейти (SRC [lua-howto](https://www.expresslrs.org/quick-start/transmitters/lua-howto/), прочитано):** 2.4 ГГц: 50/150/250/500 Hz (LoRa), F500/F1000, D250/D500, 100/333 Full, K1000, DK250/DK500; 900 МГц: 25/50/100/200 Hz, 100 Full, D50, 250 Hz і 200 Full (лише GemX), K1000 Full; GemX: **X150Hz** і **X100Hz Full**. Телеметрія: Off, 1:128 ... 1:2, Std/Race. Чутливості по рейтах Lua показує в дужках (числа не переписую).

**Яка пара TX потрібна (SRC lua-howto + targets.json):** GemX працює лише з TX із двома LR1121 (Nomad, GX12 внутрішній, TX15, TX16S MK3 внутрішній, Bayck тощо). **TX12 MKII не входить** до `tx_dual` у списку RadioMaster targets (SRC, негативний доказ в одному вендорі). Внутрішній модуль TX12 MKII ELRS-версії: SX1280, 2.4 ГГц, 10-250 мВт (SNIP, роздріб); CC2500-версія ELRS не підтримує (SNIP). Висновок (INF): із TX12 MKII доступні лише
- (a) внутрішній модуль: приймач працює як 2.4 ГГц true-diversity, **без GemX**;
- (b) зовнішній модуль у JR-слоті: GemX лише для двохтрактового LR1121-модуля; **ES900TX із попередніх документів (SX127x, 900 МГц) не дасть GemX**, а чи зв'яжеться він із LR1121-приймачем у режимі 900 МГц, не підтверджено (UNVERIFIED).

**Dynamic power (SRC [dynamic-transmit-power](https://www.expresslrs.org/software/dynamic-transmit-power/)):** потребує телеметрії (Telem Ratio ≠ Off/Race); без телеметрії при армінгу потужність піднімається до максимуму; пороги за SNR залежать від рейту; мінімум телеметрії для 250 Hz: 1:64, 150 Hz: 1:32.

**Failsafe і RSSI/LQ:**
- Опція «No Pulses / Last Pos» в документації ELRS стосується **SBUS** (SRC lua-howto). Для CRSF очікується, що RX перестає слати RC-кадри (INF), а ArduPilot оголошує RC failsafe після `RC_FS_TIMEOUT` (типово 1 с; SRC [radio-failsafe](https://ardupilot.org/copter/docs/radio-failsafe.html)). Сторінки ELRS `software/failsafe` не існує (404): поведінку **перевірити на стенді** (розділ 9, M9).
- LQ: `RC_OPTIONS` біт 11 «Use Link Quality for RSSI with CRSF»; біт 8 passthrough-телеметрія CRSF; біт 10 multi-receiver; біт 13 420 кбод для ELRS (SRC [parameters](https://ardupilot.org/copter/docs/parameters.html), прочитано).

## 3. Рекомендації: відео (OpenIPC/majestic)

Усі значення: стартові, перевірити на стенді. «Ризик» = що буде, якщо значення хибне.

| Параметр | Значення / діапазон | Джерело | Впевненість | Ризик, якщо хибно |
|---|---|---|---|---|
| Роздільність | `1280x720` | заявлена 720p120 (SNIP/SRC RunCam), прошивка за замовч. 1080p (SRC wifibroadcast) | середня | не всі режими сенсора доступні для sensor.bin: немає картинки/зміщений кадр |
| FPS | 60 (фолбек 30) | SRC wifibroadcast `.video0.fps 60` | середня | 60 к/с підвищує піки IDR і навантаження декодера GS |
| Кодек | **h265** | SRC wifibroadcast `.video0.codec h265` (типове у FPV-прошивці) | середня | див. нижче про GS |
| RC-режим | `cbr` | SRC wifibroadcast | висока як типове | VBR дає піки, що перевищують ефір (SNIP, OpenIPC/majestic#141: сторінка 403, не прочитано) |
| Бітрейт | 4000 кбіт/с (діапазон alink 2000-8000) | SRC adaptive-link README | середня | завеликий -> втрата пакетів/«розсипання»; замалий -> блоки |
| GOP | `1.0` початково; alink-приклади 10-25 і 5.0 | SRC sandbox-fpv gkrcparams, adaptive-link README | **низька** | одиниця виміру неузгоджена (UNVERIFIED); довгий GOP = довге відновлення |
| Експозиція | `isp.exposure 16` | SRC wifibroadcast | низька | розмиття/шум на швидкому польоті; одиниця UNVERIFIED |
| 3DNR | `noiseLevel 0` | SRC (коментар у скрипті: вимикає 3DNR заради затримки) | середня | більше шуму в кадрі |
| qpDelta | `-12` | SRC adaptive-link README | середня | -12 піднімає якість і розмір кадрів |
| Згладжування піків | `gkrcparams --MaxQp 30 --MaxI 2` | SRC sandbox-fpv gkrcparams.md (рос.; для 1080p30) | **низька** | зроблено для інших SoC; на SSC338Q UNVERIFIED |

**Кодек і GS на Pi 5 (REPO + SNIP, `docs/PI-PORT.md:95-101`):** Pi 5 має апаратний декодер **лише HEVC**, апаратного H.264 немає; Pi 4: HEVC так, H.264 не підтверджено. Наслідок: **h265 на AIR узгоджується з GS на Pi 5** (`v4l2slh265dec`), а h264 означає програмний декодер на Pi 5, навантаження якого не виміряне (T8 у `bench/README.md` саме це міряє). H.264 також потребує більшого бітрейту для тієї ж якості (загальновідоме, не читав джерела: INF). Ризик HEVC: відкриті проблеми декодера Pi при втратах UDP (SNIP: raspberrypi/linux#7609/#7612, не перечитано).

## 4. Рекомендації: радіоканал wfb-ng

| Параметр | Значення | Джерело | Впевн. | Ризик |
|---|---|---|---|---|
| Ширина | **20 МГц** | SRC rtl8812eu: TX лише 5/10/20, 40 лише RX | висока | 40 МГц: немає зображення (SRC: окрема стаття про збій) |
| MCS | 1 (старт), 2 за сильного сигналу | SRC README alink (MCS1 типове), SRC wfb.yaml (`mcs_index: 2`): **джерела розходяться** | середня | вище MCS = менша чутливість, дальність падає |
| GI | long | SRC alink профілі (short лише у верхньому) | середня | short GI менш стійкий |
| STBC / LDPC | 1 / 1 | SRC wifibroadcast (для 8812au і eu), SRC master.cfg (LDPC потрібен на обох кінцях; коментар «наразі лише 8812au») | середня | STBC потребує 2 TX-антен; неузгодженість LDPC між кінцями = втрати |
| FEC k/n | 8/12 | SRC master.cfg, SRC wfb.yaml | висока як типове | менше надлишковості = менш стійко; більше = нижчий корисний бітрейт |
| Канал | 161 (RunCam) або 165 (wfb-ng) | SRC; **регіон/канал за вами** | n/a | незаконна частота/потужність |
| Потужність | мінімум на стенді; максимум RunCam 28/20 дБм | SRC specs | низька | перегрів (>90 °C), перевантаження приймача; регуляторика |
| MTU `mlink` | 3994 у FPV-прошивці | SRC wifibroadcast `.wireless.mlink 3994` | середня | лише між камерою й WFB-TX; не змінювати без потреби |

**Орієнтир бітрейту (INF з SRC-профілів):** PHY для 802.11n, 1 потік, 20 МГц, long GI: MCS0 6.5, MCS1 13, MCS2 19.5 Мбіт/с (стандартна таблиця, не читав першоджерела; INF). Профілі alink дають бітрейт ≈ 46% (MCS0 2000, MCS1 4000) і 62% (MCS2 8000) від PHY×k/n (8/12), тобто 720p при 4 Мбіт/с вміщається в MCS1 із запасом, 8 Мбіт/с лише з MCS2. Калькулятор OpenIPC існує ([wfb-ng-calculator](https://docs.openipc.org/use-cases/fpv/wfb-ng/wfb-ng-calculator/)), його формула не читалась (JS). Бюджет лінії (чутливість, втрати кабелів, дальність): **UNKNOWN, міряти** (розділ 9). `docs/SIM-MODELS.md` дає лише модель із заглушками, не факти.

## 5. alink (adaptive link)

SRC: [OpenIPC/adaptive-link README, alink.conf, alink_drone.c](https://raw.githubusercontent.com/OpenIPC/adaptive-link/main/README.md), [docs.openipc.org install-adaptive-link](https://docs.openipc.org/use-cases/fpv/wfb-ng/install-adaptive-link/), прочитано.
- Механізм: `alink_gs` (на GS) рахує оцінку 1000-2000 з RSSI і SNR (мін/макс із `alink_gs.conf`, приклад RSSI -80..-40, SNR 12..36, ваги 0.5/0.5), шле на дрон кожні ~100 мс; `alink_drone` обирає рядок з `/etc/txprofiles.conf` (діапазон, GI, MCS, FEC k/n, бітрейт, GOP, потужність, roiQP, ширина, qpDelta) і застосовує через `wfb_tx_cmd set_radio/set_fec`, `iw ... txpower`, `curl .../api/v1/set?video0.bitrate=...`. Втрата heartbeat > `fallback_ms` (1000) -> профіль 999 (MCS0).
- Типові профілі: 999 MCS0 2000; 1000-1050 MCS0 2000; 1051-1500 MCS1 4000; 1501-1950 MCS2 8000; 1951-2001 short GI MCS2 9000 (8/12, ширина 20, qpDelta -12).
- `alink.conf` типово: `min_between_changes_ms=200`, `hold_modes_down_s=3`, `hysteresis_percent=5`, `allow_request_keyframe=1`, `allow_dynamic_fec=0`, `allow_xtx_reduce_bitrate=1` (x0.8).
- Для GS потрібен `log_interval = 200` у wfb-ng (типово 1000) (SRC README).
- **Застереження:** README каже, що потужність у профілях за замовчуванням консервативна (30), а «20-40 для AU-карт» підібрано автором; для RTL8812EU на AIR та AU на GS **числа перемірювати** (UNVERIFIED). Інсталятор `alink_gs` розрахований на Radxa/Android/PC; на Pi 5 не перевірено (UNVERIFIED). У заводській прошивці WiFiLink alink може бути не встановлений (SRC docs.openipc.org, станом на квітень 2025).
- Рекомендація: перший прогін з фіксованим профілем (`AIR_ALINK_ENABLE=0`), потім alink і порівняння втрат.
- Ризик: при несинхронізованих порогах система «гойдається» між профілями (SRC: збільшувати `min_between_changes_ms`/`hysteresis_percent`); при втраті зворотного каналу падає в MCS0.

## 6. Телеметрія і ArduPilot

**Шлях:** FC UART <-> UART камери (єдиний) <-> `mavfwd` <-> wfb-ng (порти 14550/14551 на камері) <-> GS (SRC скрипт wifibroadcast: `mavfwd -b 115200 -c 8 -p 100 -a 15 -t -m /dev/<serial> -o 127.0.0.1:14551 -i 127.0.0.1:14550`; SRC [OpenIPC/mavfwd README](https://raw.githubusercontent.com/OpenIPC/mavfwd/master/README.md)).
- Агрегація: mavfwd збирає пакети і скидає буфер при ATTITUDE (README), тож **ATTITUDE має стрімитись щонайменше ~10 Гц** (`MAVn_EXTRA1`). wfb-ng агрегує MAVLink до 100 мс (`mavlink_agg_timeout`, SRC master.cfg).
- `-c 8` в скрипті = канал RC, який mavfwd слухає і викликає `channels.sh`: потрібен потік `RC_CHANNELS` (`MAVn_RC_CHAN`). Це не керування апаратом.
- **Конфлікт OSD:** msposd (MSP DisplayPort OSD) і MAVLink **не можна на одній UART** (SRC OpenIPC/msposd README: «Do not enable mavlink and msp on a single UART»), а в WiFiLink2 UART одна (SRC). Для ArduPilot OSD через msposd потрібні `SERIALx_PROTOCOL=42`, `OSD_TYPE=5` (SRC msposd). Отже: **або MAVLink-телеметрія, або OSD від msposd**; рішення за користувачем (розділ 8).

| Параметр | Значення | Джерело | Впевн. | Ризик |
|---|---|---|---|---|
| `SERIAL1_PROTOCOL` (або SERIAL2) | 2 (MAVLink2) | SRC parameters (список) | висока | |
| `SERIAL1_BAUD` | 115 (=115200) | SRC (значення 115 = 115200) + 115200 у скрипті прошивки | висока | розбіжність швидкостей = немає зв'язку |
| Потоки (нові доки `MAVn_*`, у старих `SRn_*`) | EXTRA1 10, EXT_STAT 2, POSITION 3, EXTRA2 2, EXTRA3 1, RC_CHAN 2, RAW_SENS 0, PARAMS 0 | імена SRC (parameters, mavlink-requesting-data); **числа INF** | низька | 115200 бод ≈ 11.5 КБ/с: забагато потоків = втрата; мало = «заморожений» OSD |
| `MAV_GCS_SYSID` | 255 (типово); збігатися з міст/GCS | SRC parameters, `docs/MAVLINK-ROUTER.md` §6 | висока | override від чужого sysid ігнорується |
| `MAV_SYSID` | 1, **не 3** | SRC master.cfg (`mavlink_sys_id=3` ін'єктується) | висока | колізія sysid |
| `FS_GCS_ENABLE` | 0 на стенді, потім свідомо (Copter: 1 RTL...) | SRC parameters, [gcs-failsafe](https://ardupilot.org/copter/docs/gcs-failsafe.html) | середня | при 1 втрата відео-каналу wfb-ng спричинить RTL навіть за справного RC; без жодного GCS failsafe взагалі неактивний |
| `FS_OPTIONS` біт 4 (16) | «Continue in pilot controlled modes on GCS failsafe» | SRC parameters | середня | дозволяє ігнорувати GCS-failsafe в ручних режимах: оцінити ризик |
| `FS_GCS_TIMEOUT` | 5 | SRC | висока | |
| `FS_THR_ENABLE`, `FS_THR_VALUE`, `RC_FS_TIMEOUT` | 1 (RTL), типово, 1 с | SRC parameters, radio-failsafe | середня | CRSF не «низький газ»: тригер за таймаутом (INF) |
| `RC_OVERRIDE_TIME` | 3 с типово | SRC, `docs/KNOWLEDGE.md` | висока | #32862 (застарілі значення RC) |
| `RC_OPTIONS` | 2048 (LQ як RSSI); +2 «ігнорувати MAVLink override» у польоті | SRC bits; рішення INF | середня | +2 вимикає `gs_mav.py --rc` і `tx12_bridge.py` |
| `RC_PROTOCOLS` | 512 (CRSF) | SRC bit-таблиця | середня | |
| `SERIALx_PROTOCOL` для ELRS | 23 | SRC common-rc-systems | висока | |
| `RSSI_TYPE` | 3 (ReceiverProtocol); 5 лише для нативного MAVLink-ELRS | SRC; для CRSF UNVERIFIED | низька | |
| Логування | `LOG_BACKEND_TYPE`, швидкість: не досліджено | UNVERIFIED | | |

**Тип апарата невідомий** (Matek H743 це політні контролери і для Copter, і для Plane). Імена перевірено на **Copter**-параметрах; у Plane failsafe називаються інакше (UNVERIFIED).

## 7. ELRS-параметри (стартові)

| Параметр | Значення | Джерело | Впевн. | Ризик |
|---|---|---|---|---|
| Пакетний рейт | X150Hz (GemX), інакше 150/250 Hz LoRa на 2.4 ГГц | SRC lua-howto (опції); вибір INF | низька | вище = нижча чутливість; чутливості читати в Lua |
| Telem ratio | Std | SRC lua-howto | середня | Dynamic power потребує телеметрії |
| Dynamic power | Dyn із розумним Max Power | SRC dynamic-transmit-power | середня | без телеметрії при армінгу = максимум |
| Failsafe | CRSF: RX припиняє кадри (INF); `FS_THR_ENABLE` в FC | SRC radio-failsafe | середня | зміна рейту в польоті **розриває** зв'язок (SRC) |
| Потужність телеметрії RX | MatchTX (потребує Wide або FullRes) | SRC lua-howto | середня | |
| Link mode | Normal (CRSF), нативний MAVLink вимкнено | `docs/BENCH-HARDWARE.md:73` | середня | дубль `sysid` |
| Мін. прошивка | 3.5.0 (XR4) | SRC targets.json | висока | |

## 8. Конфлікти і відкриті питання

**Конфлікти між компонентами**

| № | Що | Чому проблема | Теги |
|---|---|---|---|
| C1 | **ELRS 2.4 ГГц/GemX і wfb-ng 5.8 ГГц на одній рамі** | Різні смуги, співпадіння каналів немає (INF); ризик: блокування/десенсибілізація приймача сусіднім потужним передавачем через малу відстань і гармоніки. Чисельно не оцінюю | INF |
| C2 | Приймач ELRS 900 МГц + 2.4 ГГц одночасно, 5.8 ГГц відео, GPS 1.5 ГГц | GPS L1 може втрачати супутники від шуму WiFiLink2; не вимірювано | INF/HW |
| C3 | **Одна UART камери:** MAVLink (mavfwd) проти MSP OSD (msposd) | не можна обидва на одній UART | SRC |
| C4 | Живлення: WiFiLink2 9-22 В, Matek 9-36 В; радіо EU 5 В до 1.8 А пікове (SRC для BL-M8812EU2; для RunCam-варіанта UNVERIFIED) ≈ 0.75 А на 12 В лише радіо (INF, без ККД); BEC Matek 9/12 В 2 А «для відео» | споживання всієї камери невідоме; перегрів >90 °C | SRC/INF |
| C5 | `RC_OPTIONS` +2 (ігнор override) проти стендового моста | вмикання захисту вимикає тести | INF |
| C6 | TX12 MKII (SX1280) проти GemX | без TX на двох LR1121 Gemini-X недоступний | SRC/SNIP |
| C7 | ES900TX з попередніх документів 900 МГц-only | не GemX; сумісність з LR1121 RX у 900 МГц UNVERIFIED | UNVERIFIED |
| C8 | Різні довідкові значення MCS за замовч.: README alink MCS1, wfb.yaml MCS2 | джерела розходяться | SRC |
| C9 | USB3-шум біля 2.4 ГГц на **GS Pi** | стосується приймача GS лише для 2.4 ГГц-пристроїв поруч (bench GAP E2) | INF |
| C10 | Регіон: канал 161/165, 5 ГГц і 868/915/2.4 ГГц, потужність 28 дБм | залежить від країни | SRC |

**Що треба від користувача:**
1. Точна модель приймача ELRS (XR4? DBR4? інше) і регіон 868 чи 915.
2. TX: яку саме зовнішню JR-модуль планується (Nomad/ES900TX/інше) і варіант TX12 MKII (ELRS чи CC2500).
3. Який варіант Matek H743 «v3» (WING/SLIM/MINI), яка прошивка (звичайна чи bdshot), яка версія ArduPilot і **тип апарата** (Copter/Plane).
4. Кількість комірок батареї (визначає, чи можна живити WiFiLink2 від BEC).
5. WiFiLink2 «-G» (з AU-адаптером для GS) чи без; яке GS-радіо (AU чи EU).
6. Дозволені вам канал і потужність.
7. Чи потрібен OSD (msposd) чи лише MAVLink-телеметрія.
8. Підтвердити: «RTL8812A/AU» стосується GS-адаптера, а не борту.

## 9. Невідомо до вимірювання (HW)

| № | Що | Як міряти |
|---|---|---|
| M1 | Реальний струм/температура WiFiLink2 | амперметр у лінії живлення 9-22 В; LED перегріву; температура з логу камери |
| M2 | Реальний бітрейт і піки IDR на 720p60/30 | `wfb-cli gs` (байти, FEC-відновлення, втрати), `bench/RESULTS.md` |
| M3 | Пропускна MCS/FEC: коли починаються втрати | ступінчасто знижувати потужність/збільшувати відстань, фіксувати втрати/FEC з `wfb-cli gs` |
| M4 | RSSI/SNR на дальності; чутливість | `wfb-cli gs`, лог alink_gs |
| M5 | Затримка від скла до скла | зовнішній метод (камера телефона + таймер на екрані); `bench/README.md` визнає, що скрипти її не міряють |
| M6 | Десенсибілізація: LQ ELRS з увімкненим і вимкненим wfb TX, GPS супутники/CN0 з/без радіо | A/B, LQ у EdgeTX/Lua, GPS у GCS |
| M7 | Навантаження і стабільність HEVC-декодера Pi 5 при втратах | `bench/video-rx.sh` + `top`, T8 з `bench/README.md` |
| M8 | Помилки UART mavfwd, затримка телеметрії | `gs_mav.py --rc off` (лише читання), `journalctl`, лічильники пакетів |
| M9 | Поведінка CRSF failsafe і час спрацювання FC | вимкнути TX (гвинти зняті, батареї немає), дивитись режим/статус у GCS |
| M10 | USB ID та драйвер RunCam-варіанта RTL8812EU, режими сенсора для 720p | `lsusb` в камері, `cli -g .isp...`, список sensor.bin |
| M11 | Поведінка alink на вашому GS (Pi) | запуск `alink_gs` поза польотом; перевірити, що профілі міняються |

## 10. Передпольотний чек-лист AIR (стенд, без гвинтів, без батареї на ESC)

Реальний AIR (камера на SigmaStar) **не запускає** скрипти з `bench/` (вони для Pi, `bench/README.md`). Для нього скрипти застосовні лише на боці GS/хоста; сторона AIR налаштовується через веб/SSH камери.

1. **Безпека:** гвинти зняті, ESC від'єднані від батареї, FC лише від USB (`docs/BENCH-HARDWARE.md` розділ 6). Антени на WiFiLink2 і на приймачі ELRS **до** живлення (SRC Setup-HOWTO).
2. **Без заліза:** `cd bench && PY=<python з pymavlink> ./bench.sh loopback`; `shellcheck -x *.sh`; `tests/run.sh`.
3. Живлення WiFiLink2 від BEC (9-22 В), амперметр у лінії; фіксувати струм і температуру (M1).
4. SSH на `192.168.1.10`; задати регіон/канал/потужність (мін.), `AIR_*` з `config/contour/air.example.env` (вручну, автоматичного застосування немає).
5. На GS: `sudo ./bench.sh check gs` (`bench/check.sh`): драйвер, служба, ключ `/etc/gs.key` (з карти камери, SRC docs.openipc.org: перший запуск створює `gs.key`).
6. `ethtool -i <wlan>` на GS: порожня `version` (T1 з `bench/README.md`); `wfb-cli gs`: видно пакети й RSSI (T2).
7. Відео: `sudo systemctl start bench-video-rx` або `bench/video-rx.sh` на GS; перевірити 720p, відсутність розсипу (T3). Зафіксувати бітрейт (M2).
8. FC: підключити Matek до UART камери (TX-RX хрест, GND, **не** підключати 5 В від FC, якщо камера від BEC: INF). Налаштувати `SERIALn_PROTOCOL=2`, швидкість 115, потоки.
9. На хості: `gs_mav.py --conn udpin:0.0.0.0:14550 --rc off` (лише читання): HEARTBEAT ~1 Гц, ATTITUDE ~10 Гц, `sysid` FC (T4). **`--rc sweep` не запускати**; `tx12_bridge.py` лише за `docs/BENCH-HARDWARE.md` розділ 5 і з `--confirm-props-off`.
10. RC: зв'язати TX12 із приймачем (binding phrase), CRSF на вибраний UART (`SERIALx_PROTOCOL=23`); перевірити канали в GCS і відображення LQ.
11. Тест втрати: вимкнути TX12 -> RC failsafe FC у межах `RC_FS_TIMEOUT` (M9); зупинити читача GCS -> GCS failsafe через `FS_GCS_TIMEOUT`, якщо ввімкнений (T6 `bench/README.md` для емулятора; для реального FC лише спостерігати).
12. Тест співіснування (M6): LQ ELRS при увімкненому/вимкненому wfb TX; GPS.
13. Перезапуск wfb (T7): служба `wifibroadcast` на камері: відео й MAVLink відновлюються без втручання.
14. Результати записати в `bench/RESULTS.md`.

## 11. Що не вдалося прочитати

| Ціль | Результат |
|---|---|
| `github.com/OpenIPC/firmware/issues/651`, `OpenIPC/majestic#141` | HTTP 403 (проксі); не прочитано; через MCP github доступ лише до `dscodetesla/sbc-gs` («Access denied: repository ... not configured») |
| `api.github.com/repos/OpenIPC/wiki/git/trees/...` | 403 |
| `raw.githubusercontent.com/OpenIPC/wiki/...fpv-wifilink2.md` та подібні | 404 (неправильні імена файлів; обійшов сторінками docs.openipc.org) |
| `expresslrs.org/software/failsafe`, `/rf-modes`, `/telemetry`, `/quick-start/receivers/`, `/hardware/lr1121/` | 404 |
| `ardupilot.org/copter/docs/common-expresslrs.html` | 404 |
| `raw.githubusercontent.com/ardupilot/ardupilot/master/.../MatekH743-wing|SLIM|Mini/hwdef.dat` | 404 (каталогів немає: усі варіанти в `MatekH743`) |
| Офіційні специфікації RunCam, RadioMaster (XR4, TX12 MKII) | не читались; відомості з роздрібних сторінок (SNIP) |
| Сторінка GS-кодеків Pi 5, `linux#7609/#7612` | не перечитувались (SNIP з `docs/PI-PORT.md`) |

## Додаток 2026-10-03: уточнення користувача (авторитетно) і їхній вплив на цей документ
| № | Відповідь користувача | Вплив |
|---|---|---|
| 1 | Приймач ELRS «на 900 МГц», модель не пам'ятає | Конфлікт C1/C2 цього документа лишається **відкритим**: якщо це не Xrossband, зворотний висновок про ES900RX з ранніх документів знову чинний; якщо Xrossband, потрібен Gemini-TX. Перевірити наклейку/Lua-сторінку приймача (model, band). |
| 2 | TX12 MKII із внутрішнім **Multi**-модулем | Внутрішній Multi не дає ELRS 2.4 ГГц; ELRS-лінк іде через **зовнішній** модуль; висновок цього документа про «внутрішній SX1280» для цього варіанта не застосовується (UNVERIFIED). |
| 3 | **Matek SLIM** | Родина `MatekH743`; SERIAL-мапінг цього документа лишається чинним для варіанта SLIM лише в межах підтвердженого hwdef (UNVERIFIED для SLIM-специфіки). |
| 4 | «8812A» = GS-донгл | Підтверджує, що на борту RTL8812EU (WiFiLink2), на GS RTL8812AU. |
| 5 | WiFiLink2: **MAVLink** на єдиній UART | Конфлікт C3 (msposd проти MAVLink) вирішено на користь MAVLink; OSD лише від GS/на екрані ПЗ. |
Додатково: TX12 + кастомна прошивка для одночасної роботи TX-модуля й USB: гіпотеза, потребує перевірки на залізі (див. `MEMORY.md` §2).

