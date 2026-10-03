# Контур GS: Raspberry Pi 5 (4 ГБ) · RTL8812AU · TX12 MKII · хост Ubuntu 26.04+

Станом на 2026-10-03. Файл-супутник зі стартовими значеннями: `config/contour/gs.example.env` (проходить `config/sbc-gs-config check`, 22 ключі, і `source`). Бічний документ: `docs/CONTOUR-AIR.md` (борт). Нічого тут не запускалося на залізі: усе, що потребує заліза, має позначку **HW**.

Позначки: **SRC** (прочитано з першоджерела, вказано URL/файл), **REPO** (файл:рядок у цьому репо), **SNIP** (сніпет/підсумок пошуку чи малої моделі, не підстава для рішення), **INF** (мій висновок з прочитаного), **HW** (потрібне залізо), **UNVERIFIED** (не вдалося прочитати/перевірити), **UNMEASURED** (виміру не існує, число не вигадуємо).
Мережа: `raw.githubusercontent.com`, `archive.ubuntu.com`, `deb.debian.org`, `archive.raspberrypi.com`, `apt.wfb-ng.org`, `manual.edgetx.org`, `expresslrs.org`, `radiomasterrc.com`, PyPI відповідали 200; `www.raspberrypi.com` через `curl` дає 403 (через `WebFetch` два сторінкові підсумки отримано, це SNIP); `api.github.com` і `github.com` через `gh`/MCP: 403 («repository is not configured for this session»).

> Рішення 2026-10-03 (`docs/DECISIONS.md`): R2 (перегляд) приймач Xross Gemini, перший пуск 900 МГц одно-діапазонний, зовнішній TX для GemX має бути на двох LR1121; R1 `LISTEN_ADDR` лишається `0.0.0.0` із захистом профілем/екраном.

## 0. Контур і коротка відповідь

```
 Камера+WiFiLink2 (RTL8812EU) ~~ wfb-ng 5 ГГц ~~ RTL8812AU (USB) ── Pi 5 4 ГБ ── LAN ── хост Ubuntu 26.04+ (QGC/декодер)
 FC Matek H743 SLIM <-UART- WiFiLink2 (MAVLink)                                          ▲ USB-C
                                                                                          │ HID (джойстик) — НЕ основний RC
 TX12 MKII: внутрішній Multi (CC2500) + зовнішній TX-модуль (JR) ~~ ELRS 900 МГц ~~ RX ──CRSF──> FC   ← ОСНОВНИЙ RC, минає GS і хост
```

| Питання | Відповідь | Тег |
|---|---|---|
| Pi 5: HEVC апаратно? | Так: драйвер `rpi-hevc-dec` (`VIDEO_RPI_HEVC_DEC=m` у `bcm2712_defconfig`) | SRC |
| Pi 5: H.264 апаратно? | Ні: сторінка виробу згадує лише «4Kp60 HEVC decoder»; відсутність H.264-декодера в офіційному тексті не прочитано (403), підтверджують форумні сніпети | SNIP + INF |
| Де декодувати відео | Базово на **хості** (x86, будь-який кодек); Pi 5 лишається радіошлюзом wfb-ng + маршрутизатор MAVLink. Декод на самому Pi 5 лише для HEVC і лише після HW-перевірки формату COL128 (п. 2.1) | INF |
| H.264 чи H.265 з OpenIPC | Хост x86 декодує обидва, тож вибір диктує AIR/канал: стартово H.265 (типово в прошивці OpenIPC, `config/contour/air.example.env`, і єдиний апаратний кодек Pi 5), H.264 лишається запасним. Якщо відео декодує Pi 5, H.265 обов'язково (H.264 лише ПЗ) | INF |
| ОС для Pi 5 | Залежить від того, де декодуємо (п. 2.8). Для «хост декодує» лишається рішення MEMORY.md (Bookworm 6.12) | INF |
| Чи треба кастомна прошивка TX12 для USB + RF одночасно | **Ні, за вихідним кодом EdgeTX:** жоден код не вимикає RF у режимі USB-джойстика; «модулі мають бути вимкнені» це рекомендація заради швидкості мікшера (п. 4.4). Спершу перевірити на стоковій прошивці | SRC |

## 1. Pi 5, 4 ГБ як GS

### 1.1 Апаратне декодування відео

- **HEVC апаратно: так.** `drivers/media/platform/raspberrypi/hevc_dec/Kconfig`: «Support for the Raspberry Pi HEVC / H265 H/W decoder as a stateless V4L2 decoder device», модуль `rpi-hevc-dec`; `arch/arm64/configs/bcm2712_defconfig:1034` `CONFIG_VIDEO_RPI_HEVC_DEC=m`; сумісність `raspberrypi,hevc-dec` у `hevc_d.c:442` (SRC: `raw.githubusercontent.com/raspberrypi/linux/rpi-6.12.y/...`).
- **H.264 апаратно: ні.** Сторінка виробу Pi 5 (через `WebFetch`, SNIP) у специфікації має лише «4Kp60 HEVC decoder». Офіційну документацію про відсутність H.264-декодера прочитати не вдалося (`curl www.raspberrypi.com` → 403): UNVERIFIED. `VIDEO_CODEC_BCM2835` (VideoCore V4L2-кодек) у `bcm2712_defconfig:1542` увімкнено, але це службовий шлях через VCHIQ, не доказ H.264-декодера на Pi 5; `rpicam-vid` документує, що Pi 5 «uses software video encoders» (SRC `rpicam_vid.adoc`, стосується кодування).
- **Наслідок для вибору кодека (INF):** програмний H.264 на 4 ядрах Cortex-A76 лягає на CPU, а H.265 має апаратний шлях. Для базової схеми декод іде на хості, тому Pi 5 цей вибір не обмежує.
- **Навантаження CPU для 720p ПЗ-H.264 на Pi 5: UNMEASURED.** Єдина знайдена цифра: форум «ffmpeg + SDL2, 1080p H.264 ≈ 70 % CPU, VLC ≈ 10 %» (SNIP, forums.raspberrypi.com t=365656 у видачі пошуку; не відкривалось, невідомо, відсотки від одного ядра чи від усіх). Масштабувати її на 720p не буду. Міряти: `tests/sim/video_latency.py --codec h264` на Pi 5 (SIM-MODELS.md:288), CPU через `top -H`/`pidstat`.
- **Формат виходу апаратного HEVC: тільки COL128 (SAND).** `hevc_d_video.c:287-290`: допустимі лише `V4L2_PIX_FMT_NV12_COL128` і `NV12_10_COL128` (рядки `NV12MT_*` закоментовано). У GStreamer мапінг `V4L2_PIX_FMT_NC12 → DRM_FORMAT_NV12 + DRM_FORMAT_MOD_BROADCOM_SAND128` є в `gst-plugins-bad/sys/v4l2codecs/gstv4l2format.c` для тегів **1.26.2 і 1.28.2** (по 2 збіги `SAND128`) і **відсутній** у **1.22.0 і 1.22.12** (SRC, перечитано). Версії в дистрибутивах: Bookworm gst-tools 1.22.0, Trixie 1.26.2 (SRC `deb.debian.org` Packages), Ubuntu 26.04 1.28.2 (SRC `archive.ubuntu.com`). **Висновок (INF, runtime HW):** `v4l2slh265dec ! kmssink` у `docs/CHAINS.md:46` і `bench/video-rx.sh` на Bookworm (1.22) імовірно не зможе узгодити формат; на Trixie (1.26) шанс є, але теж не перевірено.

### 1.2 Дисплейний шлях (kmssink/DRM)

- Ядро Pi 5: `CONFIG_DRM_VC4=m`, `DRM_V3D=m` (SRC `bcm2712_defconfig:1098-1099`), отже DRM/KMS є. `kmssink` потребує DRM master: працює з консолі, не під запущеним Wayland-композитором (REPO `bench/README.md`: «`kmssink` не відкривається… запущено з X/Wayland»). Під робочим столом: `autovideosink`/`waylandsink` (REPO `bench/video-rx.sh:26-28`).
- У базовій схемі (декод на хості) HDMI Pi 5 не потрібен.

### 1.3 Пам'ять 4 ГБ: GStreamer + wfb-ng + маршрутизатор MAVLink

- **Виміряних цифр немає: UNMEASURED.** Відомі лише налаштування: wfb-ng резервує UDP-буфери `tx_rcv_buf_size = 2097152` і `rx_snd_buf_size = 2097152` (по 2 МіБ, SRC `wfb_ng/conf/master.cfg:31,34`). `mavp2p`: статичний бінарник Go, споживання не мірялось. CMA за замовчуванням у `bcm2712_defconfig:1771` `CONFIG_CMA_SIZE_MBYTES=5`; реальний розмір CMA задає прошивка/оверлей, невідомо (HW).
- Міряти: `free -m`, `smem`/`ps -o rss` для `wfb_rx`, `python3` (wifibroadcast), `mavp2p`, `gst-launch-1.0`, на 30 хв з відео. Критерій: вільно ≥ 1 ГБ без свопу (мій поріг, PROPOSAL).

### 1.4 Сторінка пам'яті 16 КіБ

- Прошивка Pi 5 за замовчуванням вантажить `kernel_2712.img` «(for example, 16K page-size)»; інакше `kernel8.img` (SRC `config_txt/boot.adoc:24`). `bcm2712_defconfig:1` `CONFIG_LOCALVERSION="-v8-16k"`, `:50` `CONFIG_ARM64_16K_PAGES=y` (SRC).
- **DKMS:** модулі збираються проти заголовків ядра `linux-headers-rpi-2712` (16K) або `rpi-v8` (4K); тест-план `tests/sim/dkms/` (маніфест заголовків 6.12.109 і 6.18.50 для обох типів, патчі `0001-rtl8812au-6.12-set_monitor_channel-signature.patch` і `0002-rtl8812eu-…` для `k612*`, REPO `tests/sim/dkms/patches/series`). `tests/sim/dkms/expect.txt` містить лише **очікування** («edit only with evidence in docs/SIM-DKMS.md»); `docs/SIM-DKMS.md` на момент написання не існує (`test -e` = ні), тому результат збірки на 16K я не дублюю й не стверджую. Те, що збирається, не доводить роботу ін'єкції на 16K: HW.
- **Userland:** програми з жорстким припущенням «сторінка = 4 КіБ» (деякі аллокатори/JIT, бінарники, зібрані для 4K-вирівнювання) можуть падати на 16K; конкретних випадків для нашого стека (wfb-ng, GStreamer, mavp2p, Python) не знайдено: UNVERIFIED. Запасний шлях: `kernel=kernel8.img` у `config.txt` (4K), але тоді втрачаються оптимізації 2712 (SRC boot.adoc:24).

### 1.5 Живлення і USB

- Джерело: `power-supplies.adoc` / `usb-bus-on-raspberry-pi.adoc` (SRC): Pi 5 дає **1.6 А** на USB лише від БЖ 5 А/25 Вт, інакше **600 мА**; бюджет «shared between the USB ports and the fan header» (вентилятор Active Cooler теж з нього: «The fan connector pulls from the same current limit as USB peripherals», `frequency-management.adoc`). Перевірка: `vcgencmd get_config usb_max_current_enable`.
- Для нашого набору: донгл RTL8812AU + активний кулер (+ клавіатура/SSD при потребі). Струм донгла TX/RX не виміряний (SIM-MODELS.md:182-198: «домінантний ризик не БЖ, а бюджет USB»): UNMEASURED. Радіо TX12 підключається до **хоста**, а не до Pi 5 (контур користувача), тож його заряд USB Pi не навантажує.
- Рекомендація: БЖ 5.1 В/5 А USB-PD (SRC), донгл через короткий екранований кабель, у разі FEC-помилок живлення з окремого хаба (SRC wfb-ng Setup-HOWTO про кабелі й живлення; там вимоги для дрона, на столі м'якші: INF).

### 1.6 RP1 і GPIO

- GPIO Pi 5 живляться з RP1 (`rp1_gpio: gpio@d0000`, `compatible = "raspberrypi,rp1-gpio"`, SRC `rp1.dtsi:482-486`). У `bcm2712-rpi.dtsi` задано псевдоніми `gpio0 = &gpio; … gpiochip0 = &gpio; gpiochip10 = &gio;` (SRC, гілки `rpi-6.6.y`, `rpi-6.12.y`, `rpi-6.18.y`, `rpi-7.1.y`; `rpi-6.1.y` файла не має). **Висновок (INF):** на ядрах 6.6+ RP1 очікується як `gpiochip0`, а не `gpiochip4` (так було на ранніх 6.6 до цього псевдоніма: з пам'яті, UNVERIFIED). Номер у рантаймі не покладати в код: знаходити чип за міткою (`gpiodetect`/`gpioinfo`): HW (потрібне, бо в профілі Pi 5 `gs/boards/` досі немає).
- libgpiod: Bookworm `gpiod 1.6.3-1`, `libgpiod2`; Trixie `gpiod 2.2.1-2`, `libgpiod3`; Ubuntu 26.04 `gpiod 2.2.1-3` (SRC Packages). Код із `gpiofind PIN_<n>` (Radxa, v1) на Trixie/Ubuntu потребує обгортки v1/v2 (PI-PORT.md фаза 1).

### 1.7 Температура і охолодження

- «When the core temperature is between 80°C and 85°C, the Arm cores will be progressively throttled back. If the temperature reaches 85°C, both the Arm cores and the GPU will be throttled back» (SRC `frequency-management.adoc:5`). Pi 5 вентилятор: 0 % до 50 °C, 30 % при 50, 50 % при 60, 70 % при 67.5, 100 % при 75 °C, гістерезис 5 °C (SRC там само).
- Active Cooler: алюмінієвий радіатор + керований вентилятор, 5 В, 4-pin JST-SH (SRC: `WebFetch` сторінки виробу, SNIP; роз'єм і живлення підтверджує `frequency-management.adoc`). **Рекомендація:** ставити Active Cooler. Температура під потоком відео і CPU-декодом: UNMEASURED (`vcgencmd measure_temp`, `vcgencmd get_throttled`).

### 1.8 Яка ОС

| Критерій | Bookworm (6.12) | Trixie (6.18) | Тег |
|---|---|---|---|
| Ядро Pi 5 у `archive.raspberrypi.com` | `linux-image-rpi-2712 6.12.109` | `6.18.50` (у гілці також `6.12.34`) | SRC |
| Найновіші гілки `raspberrypi/linux` | `rpi-6.12.y` = 6.12.111 | `rpi-6.18.y` = 6.18.54; є також `6.19.14`, `7.0.14`, `7.1.13` | SRC |
| GStreamer на Pi | 1.22.0 (без SAND128) | 1.26.2 (SAND128 є) | SRC |
| libgpiod | 1.6.3 | 2.2.1 | SRC |
| Python | 3.11.2 | 3.13.5 | SRC |
| wfb-ng apt (`apt.wfb-ng.org`) | `26.9.29.44301-0~bookworm` (arm64, amd64) | `26.9.29.44301-0~trixie` (arm64, amd64) | SRC |
| DKMS svpcom@6e75916 («Fix build … 6.18 headers») | потребує локального патча `k612` | коміт спеціально під 6.18 | REPO/SRC (`docs/PI-PORT.md` §4, `tests/sim/dkms/patches/series`) |

Рекомендація (INF): «декодує хост» → Bookworm лишається прийнятною (решта рішень MEMORY.md §3 не змінюється); якщо хочемо HEVC-декод на Pi 5 через GStreamer, потрібен Trixie. Остаточний вибір: після `getconf PAGESIZE`, `uname -r` і DKMS-збірки на залізі. Ubuntu Server для Pi (`linux-image-raspi 7.0.0-1009`, SRC `archive.ubuntu.com` arm64) існує як альтернатива, розмір сторінки і драйвери не перевірені: UNVERIFIED.

## 2. RTL8812AU на Pi 5 з wfb-ng

- Підтримка: «we officially support only cards on Realtek RTL8812au and RTL8812eu» (SRC wiki WiFi-hardware). Драйвер: `svpcom/rtl8812au` v5.2.20, **не** aircrack v5.6.4.2 («low output power»), стоковий модуль у blacklist (SRC wiki Setup-HOWTO). Пін: `6e75916416de1dce5ecd37f824896bebf96aaf8f` (REPO `bench/env.example:8`, `tests/sim/dkms/manifest.txt`). Збірка на 6.12/6.18 і 16K: у роботі, див. п. 1.4.
- Рекомендовані параметри GS (SRC wiki Setup-HOWTO і `master.cfg`; значення за замовчуванням wfb-ng, ми їх не змінюємо без виміру):
  - режим: один адаптер, `wifibroadcast@gs`; diversity не потрібне (кілька NIC перелічуються в `/etc/default/wifibroadcast`, TX-адаптер обирається за RSSI, Setup-HOWTO п. 11).
  - канал/ширина: `wifi_channel` має збігатися з бортом; `bandwidth = 20`, `mcs_index = 1`, `stbc = 1`, `ldpc = 1` (лише 8812au, «must be supported both on TX and RX», `master.cfg:206-213`). **Увага:** борт має RTL8812**EU**, а GS RTL8812**AU**: чи узгоджені LDPC/STBC між EU (TX) і AU (RX) у нашому парі: HW; якщо пакетів нема, Setup-HOWTO радить вимкнути LDPC на TX-стороні (WiFi-hardware: «Some cards doesn't support LDPC and you can try to disable it on TX side»).
  - FEC: відео `fec_k = 8`, `fec_n = 12` (`master.cfg:250-251`); MAVLink `fec_k = 1`, `fec_n = 2`; RX бере FEC із session-пакетів, тож параметри GS не критичні, критичні на борту. Типові alink-профілі OpenIPC (4 Мбіт/с, MCS1, FEC 8/12): SRC `docs/BENCH-HARDWARE.md` §7.
  - MAVLink на GS: `[gs_mavlink] peer = 'connect://127.0.0.1:14550'`; відео `connect://127.0.0.1:5600` (SRC). Для пересилання на хост — `GS_FORWARD_IP` (bench) або `wfb_outgoing_ip` (gs.conf).
  - `net.core.bpf_jit_enable = 1`; вимкнути NetworkManager/wpa_supplicant на `wlanX`; `rfkill unblock all` (SRC Setup-HOWTO п. 4, 7, 12).
  - Потужність і регіон: поза цим документом (правила вашої країни). У `gs/gs.conf:98` стоїть `wfb_region='00'`; в `bench/env.example` порожнє на вимогу.
- **USB 2 чи USB 3:** на Pi 5 два контролери `snps,dwc3` (`rp1_usb0`, `rp1_usb1`, SRC `rp1.dtsi:1118,1135`); яка фізична пара портів на якому контролері: UNVERIFIED. wfb-ng-потік (~4 Мбіт/с відео + FEC + MAVLink, MCS1/20 МГц) далеко від 480 Мбіт/с USB 2.0 (INF). **Рекомендація:** чорний USB 2.0-порт, подалі від USB 3.0 пристроїв (SSD, хаб). Причина: шум USB 3.0 у діапазоні 2.4–2.5 ГГц погіршує приймач поруч з роз'ємом (SRC: Intel 327216-001 «USB 3.0 Radio Frequency Interference Impact on 2.4 GHz Wireless Devices», usb.org/sites/default/files/327216.pdf, прочитано Summary і зміст; рекомендовано екранування й відстань). Це стосується 2.4 ГГц; наш wfb-ng на 5 ГГц, тож ризик для відео нижчий (INF), але **2.4 ГГц ELRS TX12 і Multi-модуль** (їх немає біля Pi, але біля хоста з USB 3.0) цей ефект зачепити може: HW.
- Антена: дві антени на донглі (STBC використовує обидві при TX, Setup-HOWTO п. 11), вертикально, далі від USB 3.0 і від кулера; не вмикати адаптер без антен (SRC Setup-HOWTO).

## 3. TX12 MKII

### 3.1 Апаратні факти (SRC radiomasterrc.com, сторінка виробу, перечитано `curl -L`)

- Варіанти: **ELRS або CC2500**, регіон **FCC або LBT** (чотири SKU). Діапазон RF 2.400-2.480 ГГц; чип «ExpressLRS (ELRS) / CC2500»; MCU STM32F407 (раніше F207); екран 128×64 ч/б; JR-сумісний слот; USB-C з QC3 для зарядки; прошивка EdgeTX з заводу; «CC2500 / MPM (RF module)».
- Користувач: внутрішній **Multi** (отже CC2500-варіант) + зовнішній TX-модуль у JR зі **стоковим EdgeTX**.
- **Внутрішній модуль 2.4 ГГц-only** (специфікація 2.400-2.480). Він не здатен випромінювати 868/915 МГц. 900-МГц приймач ELRS може живитись **лише зовнішнім** TX-модулем, що вміє ISM 868/915 (SX127x або LR1121), з однаковою bind-фразою, регіоном і версією ELRS (SRC `docs/BENCH-HARDWARE.md:60`, ES900TX-приклад; модуль користувач ще не назвав).
- **Xrossband (GemX):** потрібен TX із двома LR1121 і GemX-сумісний RX; «any single-band receiver will not get a sync» в X-режимах; «Available only to modules with Dual LR1121 RF chips like the Nomad, Internal GX12 GemX module…» (SRC expresslrs.org/software/gemini і /quick-start/transmitters/lua-howto). Два різних модулі (внутрішній + зовнішній) **не** утворюють GemX. Якщо RX виявиться Xrossband, зовнішній модуль мусить бути двобандовим Gemini (приклад RadioMaster Nomad: два LR1121, XT30 6-16.8 В, «Micro/Nano» адаптери; механічна й струмова сумісність зі слотом TX12 MKII: UNVERIFIED). Якщо RX простий 900-МГц, GemX не потрібен.
- Внутрішній Multi і ELRS-Backpack/BLE-джойстик: BLE-джойстик є у Lua ELRS «ESP32 TXes only» (SRC lua-howto), тож на CC2500/Multi-варіанті його немає (INF).

### 3.2 Режими USB EdgeTX (SRC manual.edgetx.org, v2.12, перечитано)

- «USB Mode» (Radio Settings → Setup): **Ask** (типово), **Joyst**, **SDCard**, **Serial** (`bw-radios/radio-settings/radio-setup.md:158`). У коді одночасно реєструється лише один клас: `usb_driver.cpp:171-205` (`USB_MASS_STORAGE_MODE` / `USB_JOYSTICK_MODE` / `USB_SERIAL_MODE`, serial лише з `USB_SERIAL`; чи є в збірці TX12 MKII: UNVERIFIED).
- Джойстик: Classic (Ch1-8 осі, Ch9-32 кнопки) і Advanced (Joystick/Gamepad/MultiAxis). Advanced недоступний на ч/б радіо з флешем < 1 МБ (manual); у TX12 MKII STM32F407 (флеш, імовірно, 1 МБ; INF, не перевірено).

### 3.3 Як радіо видно в Linux (SRC manual `joystick-mapping-information-for-game-developers` і `usb_joystick.cpp`)

- VID:PID `1209:4F54`. HID-usage «Game Pad». Classic: 8 осей + 24 кнопки, звіт 19 байт (`usb_joystick.cpp:85-92`).
- evdev: CH1 `ABS_X`, CH2 `ABS_Y`, CH3 `ABS_Z`, CH4 `ABS_RX`, CH5 `ABS_RY`, CH6 `ABS_RZ`, CH7 `ABS_THROTTLE`, CH8 `ABS_RUDDER`; CH9.. `BTN_SOUTH`(0x130)…; joydev: осі 0-7, кнопки 0-23. Лише ці 8 осей: «Linux maps … sim Thr + axis Slider → ABS_THROTTLE» (дублі осей мапляться на найнижчий канал).
- **Діапазон осі: 0…2047, центр 1024.** `usb_joystick.cpp:114-115` `LOGICAL_MINIMUM (0) / LOGICAL_MAXIMUM (2047)`, значення `limit(0, channelOutputs[i] + 1024, 2047)` (`:617`); manual: «Analog axis have 11 bit resolution». Отже `bench/tx12_map.example.json` (рядки 5-8: `min -1024 … max 1024 … center 0`) **не відповідає** прочитаному; у тому файлі це «placeholder», але для реального TX12 мають бути `min 0`, `max 2047`, центр ≈ 1024. Не мій файл: передати лідові. Підтвердити `evtest` на залізі (HW).
- Режим Advanced використовує дескриптор з max 2048 для частини осей (`:148`): тому Classic спершу («try the classic mode first», manual).

### 3.4 Тренер, PPM, CRSF, SBUS (SRC manual)

- Тренерські режими: Master/Jack, Slave/Jack, Master/SBUS Module, Master/CPPM Module, Master/Serial, Master/Bluetooth, Slave/Bluetooth, Master/Multi, Master/CRSF (BENCH-HARDWARE.md §10, SRC).
- Внутрішній модуль у Hardware: Multi/XJT/ISRM/CRSF (+ швидкість CRSF); ELRS-приклад: 400K для ≤250 Гц, 921K для ≤500 Гц, 1.87M для F1000, 5.25M максимум для «RadioMaster TX12» (SRC expresslrs.org/quick-start/transmitters/tx-prep; чи рядок «TX12» охоплює MKII: UNVERIFIED). AUX-порт: Telem Mirror, Telemetry In, SBUS Trainer, LUA, GPS, CLI (SRC hardware.md; наявність AUX на TX12 MKII: UNVERIFIED).

### 3.5 Одночасно USB-HID і зовнішній RF-модуль: що каже вихідний код (SRC, `EdgeTX/edgetx@main`, `radio/src/`)

1. **Рішення про USB-режим:** `main.cpp:handleUsbConnection()` (рядки ~166-220). Для **Mass storage** викликає `edgeTxClose(false)` (зупиняє систему); для **Serial** `serialInit(SP_VCP…)`; для **Joystick** лише `usbStart()`. Там `usbStart()` у `targets/common/arm/stm32/usb_driver.cpp:168` реєструє `USBD_HID` (рядок 192-196). `pulsesStop()`/вимкнення модулів у цьому шляху **немає** (`pulsesStop()` викликається лише в `edgeTxClose(shutdown=true)`, `edgetx.cpp:1174`).
2. **Мікшер у режимі джойстика:** `mixer_task.cpp:195-197` після кожної ітерації мікшера й `pulsesSendChannels()` викликає `usbJoystickUpdate()` лише якщо `getSelectedUsbMode() == USB_JOYSTICK_MODE`. `mixer_scheduler.cpp:70-86` `getMixerSchedulerPeriod()`: беремо період **внутрішнього** модуля, якщо його задано; інакше **зовнішнього**; інакше, якщо USB-джойстик, `MIXER_SCHEDULER_JOYSTICK_PERIOD_US = 1000` мкс (`mixer_scheduler.h:27`); інакше 4000 мкс. Тобто «RF вимкнено» дає 1 кГц, а з увімкненим RF **частоту звітів HID задає період модуля**: ELRS через `mixerSchedulerSetPeriod(module, status.getAdjustedRefreshRate()/CROSSFIRE_PERIOD)` (`pulses/crossfire.cpp:209-211,445`), Multi через `MULTIMODULE_PERIOD`/sync (`pulses/multi.cpp:74-78,238`). Діапазон періоду: 850..50000 мкс (`MIN/MAX_REFRESH_RATE`).
3. **Висновок:** фраза manual «If using the radio as a USB Joystick, both internal and external RF modules *should* be turned off… will result in increased performance» це **рекомендація про швидкість** (1 кГц, F-Sim), не заборона. У `docs/BENCH-HARDWARE.md:62` («мають бути вимкнені»), `docs/KNOWLEDGE.md:58` («вимагає вимкнених RF-модулів»), `docs/GUIDE.md:185` та `MEMORY.md` §2 п. 2 слово «мають/вимагає» перебільшує (див. п. 6). Апаратного конфлікту нема: USB OTG-FS STM32F4 незалежний від UART/таймерних виводів JR-слота (INF; HW-підтвердження потрібне).
4. **Нюанси для одночасного режиму (INF з коду, HW для чисел):**
   - Якщо **внутрішній Multi увімкнений** у моделі, `getMixerSchedulerPeriod()` бере його період **раніше** за зовнішній ELRS: швидкість HID і *всього мікшера* підлаштується під Multi (нижча частота). Рекомендація: у моделі для польоту внутрішній RF = OFF, зовнішній = ELRS (CRSF).
   - При `usbPlugged()` `pwrCheck()` повертає `e_power_usb` (радіо не вимикається, живиться з USB): `edgetx.cpp:2007`; наслідок для RF (передавач лишається в ефірі при «вимкненні»?): UNVERIFIED, HW.
   - Клас USB лише один: HID + serial/CLI одночасно в стоковій збірці неможливі.
   - Заряд від USB хоста: параметр `usbChargeDisabled` у `main.cpp:176-180` існує лише під `USB_CHARGE_CONTROL`; для TX12 MKII UNVERIFIED.
5. **Що потрібно від кастомної збірки, якщо стокова поведінка не влаштує (PROPOSAL, не перевірялось):** (a) для HID-частоти незалежно від RF: у `getMixerSchedulerPeriod()` брати `min` ненульових періодів і джойстикового; або викликати `usbJoystickUpdate()` з окремого таймера; (b) вибір «пріоритетного» модуля для періоду; (c) для `MAVLINK` з PR #7832 (`docs/BENCH-HARDWARE.md` §11) на TX12 mk2 треба вимкнути GHOST/DSMP, бо не вміщається. Не змінюйте безпеку: основний RC-канал лишається ELRS.
6. **Чого не вдалося прочитати:** поведінки реального TX12 MKII (чи HID-звіти йдуть при увімкнених обох модулях, реальна частота звітів), списку збірки TX12 MKII (`USBJ_EX`, `USB_SERIAL`), версії EdgeTX на вашому радіо: UNVERIFIED/HW. `github.com` HTML і `api.github.com` недоступні (403), читалось лише `raw.githubusercontent.com`.

### 3.6 Основний RC лишається незалежним

- Основний канал: радіо → ELRS-модуль (JR) → ELRS RX → CRSF → FC (`SERIALx_PROTOCOL=23`), failsafe приймача, **минає** Pi 5, wfb-ng і хост (REPO `docs/CHAINS.md` §2, `docs/BENCH-HARDWARE.md` §5).
- Резерв «радіо → USB → хост → `RC_CHANNELS_OVERRIDE` → wfb-ng → FC»: лише `bench/tx12_bridge.py` (відмовляється стартувати без `--confirm-props-off`; dead-man; єдиний писач; `--sysid` = `MAV_GCS_SYSID`; REPO `bench/tx12_bridge.py:1-24`). Не запускати на апараті з гвинтами; ArduPilot#32862 (`docs/CHAINS.md`).
- Практичне правило контуру: одночасне USB+RF потрібне для **хост-калібрування/джойстика/симулятора**, а не для керування апаратом. Для польоту міст вимкнений (`systemctl` не вмикати), хост-USB радіо від'єднати або режим USB = SDCard/Ask.

## 4. Хост Ubuntu 26.04 (SRC `archive.ubuntu.com/ubuntu/dists/resolute`, Packages.xz на 2026-10-03)

| Що | Значення | Тег |
|---|---|---|
| Реліз | 26.04.1 LTS «Resolute Raccoon» (`releases.ubuntu.com`, Release `Date: 23 Apr 2026`) | SRC |
| Ядро | `linux-image-generic 7.0.0-14` (updates `7.0.0-38`) | SRC |
| Робочий стіл | GNOME Shell 50.1; `ubuntu-session` залежить від `xwayland`, пакета `gnome-session-x11` в архіві немає: **сеанс лише Wayland** (INF); `xorg`/`xserver-xorg-core` пакети в архіві є | SRC+INF |
| Python | 3.14.3 (`python3`), `python3.14 3.14.4-1`, `python3-venv`, `pip 25.1.1` | SRC |
| GStreamer | 1.28.2: `gstreamer1.0-tools/plugins-base/good` (main), `-plugins-bad`, `-plugins-ugly`, `-libav`, `-vaapi 1.26.8` (universe), `-gl`, `-gtk3`, `-qt6` | SRC |
| libgpiod | `gpiod`, `libgpiod3`, `python3-libgpiod` 2.2.1 (v2 API) | SRC |
| systemd/udev | 259.5 | SRC |
| evdev | `python3-evdev 1.9.3` (universe), `evtest 1.36`, `joystick 1.8.1` | SRC |
| pymavlink/mavproxy | **немає в архіві** (`python3-pymavlink`, `mavproxy`: відсутні): ставити з PyPI у venv | SRC |
| QGroundControl | немає в архіві; офіційно AppImage `QGroundControl-x86_64.AppImage` (README mavlink/qgroundcontrol); для AppImage потрібен `libfuse2t64` (є в universe). Запуск на 26.04/Wayland: UNVERIFIED | SRC+UNVERIFIED |
| Mission Planner | немає пакета; `mono-runtime/mono-devel 6.14.1` є, `mono-complete` нема; `dotnet-runtime-10.0` є. Linux-запуск: UNVERIFIED | SRC+UNVERIFIED |
| Інше корисне | `dkms 3.2.2`, `shellcheck 0.11`, `stress-ng 0.20`, `chrony`, `iw 6.17`, `ethtool`, `tcpdump`, `wireshark 4.6.4`, `mpv`, `vlc 3.0.23`, `ffmpeg 8.0.1`, `intel-media-va-driver 26.1.2` | SRC |
| wfb-ng для Ubuntu | `apt.wfb-ng.org/dists/resolute` (amd64, arm64) `26.9.29.44301-0~resolute`; на хосту він потрібен лише для `wfb-cli`/тестів, не обов'язково | SRC |

**pymavlink 2.4.50 під найновішим Python (виконано):** `uv venv --python 3.14` → CPython **3.14.0rc2** (uv не мав 3.14.3; Ubuntu постачає 3.14.3/3.14.4, отже це не той самий інтерпретатор). `uv pip install -r bench/requirements.txt` → `fastcrc 0.5.0`, `lxml 6.1.3`, `pymavlink 2.4.50` встановились; `import pymavlink`, `mavutil`, `dialects.v20.ardupilotmega` і `heartbeat_encode().pack()` працюють (21 байт). Контрольно те саме на 3.13.12. `bench/tx12-bridge-test.sh` з `PY=<venv 3.14>` дає **«ALL TX12 BRIDGE CHECKS PASSED»** (evdev у тесті не використовується). `evdev 2.0.0` з PyPI зібрався зі sdist на 3.14.0rc2 (`import` працює; `evdev.__version__` не існує — це не помилка). Venv, створений **без доступу nobody** до інтерпретатора, не працює: `Permission denied`, бо uv ставить Python у `/root/.local/share/uv`; повторено з інтерпретатором і venv у світло-читабельному каталозі під `setpriv --reuid=65534`: `3.14.0rc2 pymavlink 2.4.50 uid 65534` (SRC власний запуск). Висновок: пін `pymavlink==2.4.50` сумісний із Python 3.14 (rc2); на 3.14.3 з Ubuntu: UNVERIFIED (інтерпретатор недоступний).
Досяжність: `archive.ubuntu.com`, `deb.debian.org`, `archive.raspberrypi.com`, `apt.wfb-ng.org`, PyPI (через uv) відповіли 200; `packages.ubuntu.com` 200 (не використовувалось).

## 5. Рекомендовані початкові значення і невідоме

Значення: `config/contour/gs.example.env` (кожен ключ із походженням). Таблиця невідомого до виміру на залізі:

| Невідоме | Як міряти | Тег |
|---|---|---|
| `getconf PAGESIZE` на Pi 5, збірка й робота `88XXau_wfb` на 16K і 4K | `uname -r`, `dkms status`, `iw dev`, monitor+ін'єкція (`bench.sh check gs`) | HW |
| Назва gpiochip RP1 і лінії | `gpiodetect`, `gpioinfo` | HW |
| Струм Pi 5 + донгл TX/RX, просідання 5 В | мультиметр, `vcgencmd pmic_read_adc`, `vcgencmd get_throttled` | UNMEASURED |
| CPU 720p ПЗ-H.264 на Pi 5 | `tests/sim/video_latency.py`, `top -H` | UNMEASURED |
| Робота `v4l2slh265dec` + COL128 на Pi 5 (Bookworm 1.22 / Trixie 1.26) | `GST_DEBUG=v4l2codecs*:5 gst-launch-1.0 …`, `gst-inspect-1.0 v4l2slh265dec` | HW |
| Пам'ять GStreamer+wfb-ng+router | `free -m`, `ps -o rss` | UNMEASURED |
| Температура Pi 5 з Active Cooler | `vcgencmd measure_temp` | UNMEASURED |
| Затримка скло-до-скла | окремий метод (годинники не синхронні) | UNMEASURED |
| LDPC/STBC між EU (борт) і AU (GS) | `wfb-cli gs`, втрати 10 хв | HW |
| Реальна частота HID-звітів TX12 при RF on/off | `evtest`, `evemu`/`python-evdev` таймстемпи | HW |
| Діапазон осей evdev (очікується 0..2047) | `evtest /dev/input/eventN` | HW |
| Поведінка радіо при USB + увімкнених двох модулях | спостереження | HW |
| QGC AppImage/Mission Planner на Ubuntu 26.04 Wayland | запуск | UNVERIFIED |

## 6. Конфлікти і відкриті питання

### 6.1 Суперечності з попередніми документами

| Де | Що там | Що виявилось |
|---|---|---|
| `docs/BENCH-HARDWARE.md:62`, `docs/KNOWLEDGE.md:58`, `docs/GUIDE.md:185` | «У режимі USB-джойстика обидва RF-модулі мають бути вимкнені / вимагає вимкнених» | manual: «should be turned off… increased performance»; код: RF не вимикається, змінюється лише період мікшера (п. 3.5). Рекомендую переписати як «рекомендовано» |
| `docs/BENCH-HARDWARE.md:18,60,66`, `docs/CHAINS.md:54,71`, `docs/GUIDE.md:24` | TX12 + **ES900TX** (868/915) як приклад RC | Це приклад. Користувач: зовнішній модуль не названий, внутрішній Multi, приймач 900 МГц модель невідома. ES900TX не є підтвердженою частиною контуру |
| `docs/CHAINS.md:46` (`v4l2slh265dec ! kmssink` для Pi 5) | вважається робочим | Декодер видає лише COL128; у GStreamer 1.22 (Bookworm) мапінгу SAND128 нема (п. 1.1). Не перевірено |
| `docs/PI-PORT.md:155` / `MEMORY.md` §3 «Bookworm» | рекомендація Bookworm | Для HEVC-декоду на Pi 5 краще Trixie; для «хост декодує» лишається |
| `bench/tx12_map.example.json:5-8` | `min -1024 / max 1024 / center 0` | EdgeTX Classic дає 0..2047, центр 1024 (п. 3.3) |
| `gs/gs.conf:97` `wfb_channel='161'` vs `bench/env.example:5` `WFB_CHANNEL="165"` | два різні канали за замовчуванням | Має збігатися з бортом (AIR). Користувач не повідомив |
| `docs/BENCH-HARDWARE.md:70` «BLE-джойстик належить внутрішньому ELRS-модулю» | | На CC2500/Multi-варіанті TX12 внутрішнього ELRS нема, BLE-джойстика нема (п. 3.1) |
| `docs/PI-PORT.md` §5 «Pi 5 лише HEVC», KNOWLEDGE.md:39 | | Підтверджено для HEVC (SRC код); «H.264 нема» лишається SNIP+INF |

### 6.2 Що має повідомити користувач

1. Варіант TX12 MKII: SKU (CC2500/ELRS, FCC/LBT) і версія EdgeTX на радіо (`Version` у меню).
2. Тип зовнішнього TX-модуля (модель, 900 МГц чи двобандовий LR1121) і його версія ELRS.
3. Модель ELRS-приймача (наклейка/Lua: одно- чи двобандовий, GemX?) і версія ELRS.
4. Хост: GPU, версія Ubuntu і сеанс (Wayland/XWayland), чи потрібен QGC AppImage.
5. Де декодувати відео (хост чи Pi 5), codec на борту (H.264/H.265), FPS, канал wfb-ng, ширина.
6. Версія і образ Pi OS (Bookworm/Trixie), БЖ (чи 5 А), чи вентилятор.
7. Чи потрібен міст `tx12_bridge.py` взагалі (за замовчуванням ні).

### 6.3 Передпольотний чек-лист GS (стенд, **без гвинтів, батарея/ESC від'єднані**)

1. На Pi 5: `uname -r`, `getconf PAGESIZE`, `vcgencmd get_throttled`, `vcgencmd get_config usb_max_current_enable`; зафіксувати в `bench/RESULTS.md`.
2. Без радіо: `cd bench && PY=<python з pymavlink> ./bench.sh loopback`; `PY=… ./tx12-bridge-test.sh`; `shellcheck -x *.sh` з `bench/`; `tests/run.sh`.
3. Встановлення: `sudo ./bench.sh setup gs`, перезавантаження, `sudo ./bench.sh finish gs`, далі `./bench.sh check gs` (перевіряє бінарники wfb-ng, ключ, модуль Realtek, службу).
4. Антени на донглі до подачі живлення; донгл на USB 2.0 через короткий кабель; без антен адаптер не вмикати.
5. Радіоканали: перевірити, що RC (ELRS) і wfb-ng незалежні; **нічого не змішувати в одній моделі EdgeTX**: модель «полет»: внутрішній RF OFF, зовнішній ELRS ON, USB не підключений до апарата.
6. MAVLink: `bench/gs_mav.py` лише на столі з fake_fc/FC без гвинтів; `MAV_GCS_SYSID` на FC = `TX12_SYSID`; перед польотом міст вимкнений.
7. RC-міст (тільки стенд): `tx12_bridge.py --input sweep --confirm-props-off`, потім `--input evdev:/dev/input/eventN` з виправленою мапою (0..2047); перевірити dead-man (відключити USB під час руху: газ failsafe, release, тиша).
8. Відео: `bench/video-rx.sh` (`DECODER=avdec_h264` на хості; `kmssink` лише з консолі).
9. Перед будь-яким польотом реальний RC (ELRS) перевірено окремо (failsafe приймача: `FS_THR_ENABLE`), RC через wfb-ng не єдиний канал (CLAUDE.md).

## 7. Що не вдалося прочитати (точні помилки)

- `www.raspberrypi.com` через `curl`: HTTP 403 (прочитано лише `WebFetch`-підсумками: SNIP). Форумні теми Pi (`forums.raspberrypi.com`): не відкривались, лише рядки видачі пошуку.
- `gh api repos/raspberrypi/documentation/…` і MCP `get_file_contents`: «GitHub access to this repository is not enabled for this session» / «repository … is not configured for this session. Allowed repositories: dscodetesla/sbc-gs».
- `raw.githubusercontent.com/raspberrypi/documentation/.../raspberry-pi/{processors,hardware,raspberry-pi-5,bcm2712,rp1}.adoc`: 404; сторінки про декодери Pi 5 в цьому репо не знайдено.
- `docs/SIM-DKMS.md`: не існує (`test -e`).
- Python 3.14.3/3.14.4 (Ubuntu): недоступні, перевірено на 3.14.0rc2.
