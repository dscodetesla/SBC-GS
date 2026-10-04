# Реєстр знань проєкту (Pi-порт SBC-GS)

Зведення перевірених фактів, виправлених помилок і досвіду роботи, щоб не втрачати контекст між сесіями.
Станом на 2026-10-01. Гілка `claude/hopeful-brown-k5mmbm`, PR [#1](https://github.com/dscodetesla/SBC-GS/pull/1).
Позначки: **SRC** прочитано з першоджерела, **REPO** перевірено в коді, **SNIP** зі сніпета/підсумку (не підстава для рішень), **INF** висновок, **HW** потребує заліза.

## 1. Карта матеріалів

| Файл | Зміст |
|---|---|
| `docs/PI-PORT.md` | архітектура, матриця стеків і драйверів, фази 0–6 |
| `docs/CHAINS.md` | ланцюжки відео та керування, безпека RC |
| `docs/GAPS.md` | реєстр прогалин і порядок робіт |
| `docs/ROADMAP-EXECUTION.md` | план виконання: віхи M0–M8, наступні кроки, критерії готовності |
| `docs/BENCH-HARDWARE.md` | топологія стенду, обладнання, 8814/TX12, розділи 9–11 |
| `bench/` | скрипти стенду (AIR/GS), `loopback-test.sh`, `RESULTS.md` для вимірювань |

## 2. Мета та межі

- Мета: GS на Raspberry Pi 3B+/4/5 з wfb-ng, OpenIPC-відео, MAVLink (ArduPilot), керування TX12.
- ArduPilot **не** замінює PixelPilot_rk: ArduPilot на дроні, на GS лише MAVLink-шар. PixelPilot_rk (Rockchip MPP) на Pi не працює.
- openwifi (FPGA Zynq) на Pi не запускається, лише мережевий партнер. OpenHD/Ruby/openwifi поза обсягом.
- Станція вузлів: AIR Pi 4, GS Pi 5 (RTL8812 на обох), хост декодує відео по LAN.

## 3. Перевірені факти

**Репозиторій (REPO)**
- Вшито під Radxa Zero 3W: PixelPilot_rk і `mppvideodec` (`stream.sh:100`, `build.sh:162-175`), ядро Radxa (`build.sh:113`), GPT і `p3/p4/p5` (`release.sh`, `gs-init.sh`), `/boot/dtbo` (`gs-applyconf.sh`), `gpiofind PIN_*` (libgpiod v1), `fcc00000.dwc3`, `/home/radxa`.
- `build.sh:16` опечатка `xface4`: блок очищення мертвий (видалити, не виправляти).
- Потоки wfb-ng: відео `0x00`, MAVLink `0x10/0x90`, тунель `0x20/0xa0`, MSP `0x11/0x91` (`wfb.sh:104-107`). Режими `standalone/aggregator/cluster` (`gs.conf:77-89`).
- Маршрутизатора MAVLink у репо немає.

**wfb-ng (SRC)**
- Peer-схеми лише `connect://IPv4:порт` (UDP), `connect_unix://@ім'я`, `listen://`, `serial:dev:baud`; TCP/multicast для відео немає (`services.py`). TCP лише для MAVLink (`mavlink_tcp_port`).
- Офіційно підтримує лише RTL8812AU/EU; 8814au «not supported by author» (wiki WiFi-hardware). Інсталятор автовизначає лише `rtl88xxau_wfb` і `rtl88x2eu`.
- GS-конфіг: `[gs_video] connect://…:5600`, `[gs_mavlink] connect://…:14550`; AIR: `[drone_video] listen://0.0.0.0:5602`, `[drone_mavlink] listen://…:14550` або `serial:ttyUSB0:115200`. Сервіси `wifibroadcast@gs` і `@drone`, ключі `wfb_keygen` (повторний запуск ламає парування).

**Відео на Pi**
- Pi 4: апаратний HEVC (`rpivid`, `VIDEO_RPI_HEVC_DEC`, увімкнено в `rpi-6.12.y`). Pi 5: лише HEVC. Pi 3B+: лише H.264 (SNIP). GStreamer: `v4l2slh265dec`, `v4l2h264dec`. Зв'язка з `rpivid` на заліз: HW.
- `h264parse` у `gstreamer1.0-plugins-bad`, `timeoverlay` у `gstreamer1.0-x`, `kmssink` у `plugins-bad`.

**Драйвери**
- `svpcom/rtl8812au` коміт `6e75916416de1dce5ecd37f824896bebf96aaf8f` «Fix build on Raspberry Pi with kernel 6.18 headers». `libc0607/rtl88x2eu` `c066ed1`: умова `>= 6.12.101`.
- `rtw88_8814au` у mainline з v6.15 (файл відсутній у v6.14). `aircrack-ng/rtl8812au` застарілий. Готового FPV-форка 8814 не знайдено.
- `morrownr/8814au` `main`: README належить форку `joseguzman1337` («Codex (GPT-5)»); походження неясне, не збирати без перевірки.
- Pi 5: ядро `kernel_2712` 16K; USB 1.6 А лише від БЖ 5 А, інакше 600 мА (SRC raspberrypi/documentation).
- Пакети заголовків ядра є в Bookworm і Trixie (`archive.raspberrypi.com`, перечитано мною): `linux-headers-rpi-v8` (Pi 4) і `linux-headers-rpi-2712` (Pi 5); версії за звітом агента: Bookworm 6.12.109, Trixie 6.18.50. Застарілий `raspberrypi-kernel-headers` є лише в Bookworm (версія 2023) і ядро 6.12 не супроводжує. Відповідність пакета моделі за суфіксом: INF.
- Pi OS: поточний реліз на Debian Trixie, попередній на Bookworm (SRC documentation `rpi-os-introduction.adoc`). Imager налаштовує hostname, користувача, Wi-Fi, SSH, часовий пояс (SRC `install.adoc`). EEPROM: `sudo rpi-eeprom-update`, `-a` для застосування (SRC `boot-eeprom.adoc`). `kernel_2712.img` типовий на Pi 5, інакше `kernel8.img` (SRC `boot.adoc`). `usb_max_current_enable`, `get_throttled`, `getconf PAGESIZE`, dtoverlay для вимкнення Wi-Fi: НЕПІДТВЕРДЖЕНО документацією.

**ArduPilot / MAVLink (SRC ardupilot.org)**
- `RC_OVERRIDE_TIME` типово 3 с; `RC_FS_TIMEOUT` 1 с; `FS_GCS_TIMEOUT` 5 с; «RC_OVERRIDES are lost if using a GCS only». Відкрита помилка безпеки ArduPilot#32862 (застарілі значення RC після override).
- `ARMING_CHECK` у поточній документації відсутній, є `ARMING_SKIPCHK`, `ARMING_NEED_LOC`. SERIAL0 = USB, MAVLink2.
- Резервний RC має лишатися активним (joystick-сторінка). RC через телеметрію не може бути єдиним каналом.

**ELRS / TX12 / EdgeTX (SRC)**
- ES900TX: Happymodel (не EMAX), JR-bay, ESP32+ESP8285. ES900RX: ESP8285+SX1276, 5 В, IPEX, CRSF (fpvua.org); діапазон «750/915» схожий на помилку: варіанти TX/RX мають збігатись.
- Нативний MAVLink ELRS: прошивка 3.5.0, `SERIALx_PROTOCOL=2`, `RSSI_TYPE=5`, співвідношення 1:2, потрібні ESP-передавач і ESP-приймач. Backpack віддає MAVLink по WiFi: `10.0.0.1`, UDP 14550.
- **Режим USB-джойстика EdgeTX вимагає вимкнених RF-модулів**: ELRS і USB-хост одночасно не працюють.
- EdgeTX v2.12.4 (2026-09-02, Latest); `UA` є в `RADIO_LANGUAGES` і `TTS_LANGUAGES`; CloudBuild `tx12mk2` (≥2.8.0). Відкритий PR EdgeTX#7832 (MAVLink2, `-DMAVLINK=ON`, 2026-09-26, не злитий). Lua: `crossfireTelemetryPush/Pop`, `serialWrite/Read`, `setSerialBaudrate`. AUX: Telem Mirror, SBUS Trainer, LUA.
- `joystick2crsf`: ліцензія personal-use-only, у репо не включати.

**OpenIPC**
- alink_drone: `wfb_tx_cmd 8000 set_radio/set_fec`, API majestic, IDR через `nc localhost 4000`, профіль 999 = MCS 0; `/etc/txprofiles.conf`. Реальний порт виходу majestic: UNVERIFIED.

## 4. Виправлені помилки (щоб не повторювати)

| Було хибно | Правильно |
|---|---|
| Ruby підтримує Pi 3B+/4/5 | README згадує лише облікові дані «Raspberry»; Pi 4/5 не підтверджено |
| HEVC на Pi 4 програмний | є апаратний `rpivid` |
| ES900TX — EMAX | Happymodel |
| ArduPilot замінює PixelPilot | ні: це MAVLink-шар, відео окремо |
| BLE-джойстик з ES900TX | належить внутрішньому ELRS-модулю |
| `README morrownr/8814au` справжній | це README чужого форка |
| Дата EdgeTX «2024» | 2026 (підсумок сторінки помилявся) |
| `num-buffers=0` = безкінечно | у GStreamer це нуль кадрів, потрібно `-1` |

## 4a. Знахідки golden-тестів `gs/gs-applyconf.sh` (поведінка «як є», воспроизведено в пісочниці `tests/`)

| Знахідка | Статус |
|---|---|
| Запуск **окремим процесом** (як кнопка `apply_conf`, `button.sh:138`) не бачить змінних `gs.conf`: умова рядка 16 істинна, і скрипт обнуляє всі `btn_*_pin` у `gs.conf`. При `source` з `gs.sh:11`/`gs-init.sh:186` піни не чіпаються | відтворено в пісочниці; впливу на реальній станції не підтверджено (INF) |
| Рядок 116: `sed 's#^\(/dev/[^\s]*\s*\)[^\s]*#…#'`: `[^\s]` у GNU sed означає «не `\` і не `s`»; на рядку fstab у форматі `gs-init.sh:45` зміна `rec_dir` дає зіпсовану точку монтування (`/Videos` → `/Video/mnt/recs`) | **перевірено** прямим запуском `sed` на цьому форматі |
| Рядки 121–131: умова `[[ $(grep -q …) && … ]]` завжди хибна (`grep -q` нічого не друкує), тож `/etc/default/gpsd` переписується при кожному запуску | відтворено в пісочниці |
| Останній рядок `[ -n "$need_restart_services" ] && …` дає статус 1, коли нема що перезапускати; викликач із `set -e` (як `gs.sh`) після `source` **завершується**. У продакшні це, імовірно, маскується тим, що `br0.network` існує завжди і `systemd-networkd` завжди потрапляє в перезапуск | механізм відтворено; маскування продакшну: INF. **Для Pi-плати без `br0` це пастка** |

| `fan.sh` на старті завжди ставить шпарність `period/5` (20 %), тож початковий стан PWM не впливає; після повідомлення «Need enale pwmchip…» скрипт **не виходить**, а продовжує цикл; `${date}` у рядку роздільника не визначена, тож дата не друкується | відтворено в golden (`tests/golden/fan/`) |
| `otg-gadget.sh` у `[ -b /dev/mmcblk1p4 ]` залежить від пристроїв хоста | відомо з коду |
| **`button.sh ummount_extdisk` ніколи не відмонтовує диск:** `grep "^/dev/sda1 /${rec_dir}"` при `rec_dir='/Videos'` дає шаблон з подвійною скісною рискою і не збігається з реальним рядком `/proc/mounts`, тож завжди «extdisk already umounted» | **перевірено** прямим запуском `grep` на продакшн-значенні |
| `button.sh change_otg_mode`: `local pid_led=$!` губиться після виходу з функції, тож у гілці `device→host` `[ -z "$pid_led" ] \|\| kill $pid_led` ніколи не вбиває блимання LED з попереднього виклику | з коду; `kill` вбудована команда, у golden не видно (INF) |

Ці дефекти зафіксовані в golden «як є»; виправлення мають бути свідомими оновленнями golden у окремих PR, а не побічним ефектом рефакторингу.

## 4b. Знахідки golden-тестів `gs/gsmenu.sh` (поведінка «як є», 2026-10-02; не виправлено, зафіксовано в goldens)

REPO, `tests/golden/gsmenu/`, номери рядків у `gs/gsmenu.sh` (два пункти перевірено мною напряму, решта зі звіту агента):

- `:655` ✔ `nmcli device wifi connect ... ifname wifi`: літерал `wifi` замість імені інтерфейсу (на Radxa це `wifi0`); гілку M3d не застосовано.
- `:253` ✔ vs `:377,:379`: get читає `.records.enabled`, set пише `.records.enable` (ключі розходяться).
- `:714,:760` (+get ~717): для невідомого типу карти `range`=0 і ділення на нуль у `$(( .../range ))`.
- `:732`: `[ "$GSMENU_VTX_DETECTED" -eq "1" ]` при незаданій змінній дає помилку `[` (статус 2 приймається).
- `:623,:628,:635,:642`: `grep "$WIFI_IFACE"` шукає підрядок у всьому рядку nmcli (назва підключення з іменем інтерфейсу дасть хибний збіг); `:650` і `:620` так само для `$6`/`hotspot`.
- `:643`: `ip -4 addr show "$WIFI_DEV"` запускається з порожнім аргументом, якщо `WIFI_DEV` порожній.
- Не покрито тестами: `set air presets <name>` (реальний `mktemp` у `/tmp`), реальна поведінка sshpass/ssh/scp/git, формат виходу iw/jq/drm_info/udevadm на залізі (фікстури вигадані).

## 5. Досвід роботи (процес)

- Підсумки `WebFetch` робить мала модель: ключові цитати перечитувати напряму (`curl` до `raw.githubusercontent.com`). Це вже виявило кілька помилок.
- `github.com` HTML і `api.github.com` дають 403; `raw.githubusercontent.com` працює. Політика мережі живе в середовищі, а не в сесії; після зміни потрібна нова сесія.
- `forums.raspberrypi.com`, `www.raspberrypi.com` досі 403.
- У контейнері можна ставити apt-пакети (shellcheck, GStreamer) і venv з pymavlink: тест без заліза через `./bench.sh loopback` (PY= шлях до python з pymavlink).
- shellcheck запускати з каталогу `bench/`, інакше `source` не знаходить `lib.sh`.
- **Golden-тести з фоновими процесами (`&`) без стабілізації флакають** (у `gs.sh` 12/12 прогонів із різним порядком логів; ще випадкове «command not found» від фонового `wfb_rx`): порівнювати множину викликів, додавати шими для всіх фонових команд, ганяти 25×300 разів перед комітом. Також у golden не має потрапляти `PATH` хоста.
- **Мутаційні перевірки робити лише на копії репозиторію:** тестова мутація в `gs/fan.sh` одного разу лишилась після перерваного прогону (відкочено, `gs/` і `build/` ідентичні HEAD). Рівень `-S warning` у shellcheck не бачить `SC2086` (це info).
- Тестувати «на місці» знаходить реальні помилки (пропущені плагіни, `num-buffers`); код без запуску вважати непевним.
- Скрипти установки мають бути **ідемпотентними при зміні конфігурації**: після `FC_SERIAL`/`GS_FORWARD_IP` застарілі systemd-сервіси продовжували працювати (`Restart=always`); тепер `install-services.sh` їх вимикає. Незалежний агент-рецензент гайда знайшов 7 суттєвих помилок (ключ під `root@`, T7 на не тому вузлі, `gs_mav.py` на GS після пересилання, RTT на реальному FC, небезпечне «відключити антену» тощо): рецензію чергувати з написанням.
- Для реального FC `gs_mav.py --real-fc` змінює лише канали 1–4 (`65535` = ігнорувати за документацією ArduPilot), RTT вимкнено.
- Субагентам давати: чітку межу, «кожне твердження з URL, інакше UNVERIFIED», без обходу блокувань.
- **Тести «зелені локально, червоні на CI»:** я працюю як root, а раннер GitHub — ні. `gs-init.sh` писав у реальний `/dev/tty1` (локально вдавалося, на CI — `Permission denied`). Причина: підстановка шляхів у `sb_rewrite` споживала роздільник і не заміняла сусідній збіг (`/dev/ttyFIQ0 /dev/tty1`). Виправлено подвійним проходом; після змін пісочниці прогонять `tests/run.sh` і під непривілейованим користувачем (`setpriv --reuid=65534 …` на копії). Побічно: пісочниця раніше торкалась реального `/dev/tty1` (SRC: CI-лог job golden, run 36917347380).

- **Делегування субагентам (досвід 2026-10-02, 12 агентів):** кожному давати неперетинні файли й заборону на `git commit/push`; коміт робить керівний агент лише після **власної** перевірки (тести під root і `nobody`, `shellcheck`, ratchet, діф `gs/`+`build/`, вибірково джерела). Пастка: `git add -A` під час роботи агента заносить в коміт його недописані файли: перед комітом дивитись `git status`, додавати лише перевірене, а `git show --stat` після коміту.
- **Не приймати «обхід» замість виправлення:** агент залишив застарілий текст маркера UNVERIFIED, щоб не змінювати golden; правильно виправити текст і свідомо оновити golden власного тесту, а golden спадкового коду не чіпати ніколи.
- **Регресії в надійності при абстракціях:** скрипти, що почали `source`-ити `gs/lib/*.sh`, ламаються без бібліотеки (раніше були самодостатні). `oled.py` отримав `try/except` для імпорту; для shell-скриптів гарантія лише в тому, що `install.sh` копіює `lib/`.
- **Тест-інфраструктура:** глобальний git-конфіг (`commit.gpgsign=true`) робить SHA тестових репозиторіїв нестабільними: у тестах `GIT_CONFIG_GLOBAL=/dev/null` і фіксовані автори/дати; шим `jq`, що не читає stdin, дає SIGPIPE у виробника (exit 141); `set -x` у `build.sh` виводить розкритий пароль, тож перед розкриттям `set +x`; `mktemp` усередині `/tmp/claude-0` недоступний для `nobody` (копію для перевірки класти в `/tmp/sbcn`).
- **Ratchet-и працюють:** `static/pins` (незакріплені завантаження не можуть зрости), `static/security-defaults`, `static/rpi4-draft` (UNVERIFIED лише зменшується), shellcheck-храповик; кожен ловить регресію до рев'ю.
- **Stop-хук `/goal`:** умова з однієї літери («а») не вимірювана й зациклює хук; формулювати вимірювану умову (наприклад, «CI зелений на останньому коміті») або знімати `/goal clear`.
- **CI GitHub:** новіший пуш скасовує попередній запуск (`cancelled`): це не збій. Підсумок дивитись по останньому коміту; повний прогін ≈2-3 хв (golden ≈10 с локально 65 с після `gsmenu`).

## 6a. 2026-10-02: інженерні знахідки

| Знахідка | Де |
|---|---|
| У Pi Linux `dwc2` немає debugfs `mode` (лише `params`, `hw_params`, `dr_mode`, 0444); `"mode"` 0644 є лише в `dwc3` (SRC `raspberrypi/linux` rpi-6.12.y `drivers/usb/dwc2/debugfs.c`, `dwc3/debugfs.c`) | `docs/BOARD-RPI4.md` (OTG, `OTG_CONTROLLER=none`); на залізі не підтверджено (HW) |
| Імена ліній GPIO на Pi: `GPIO<BCM>` (SRC `bcm2711-rpi-4-b.dts`), тож `gpiofind PIN_<n>` з контракту Radxa на Pi не працює | `docs/BOARD-RPI4.md`, `gs/boards/rpi4/` |
| `mavp2p` не має фільтрів за msgid/sysid і не переписує sysid; `mavlink-router` має `Allow/Block*` (SRC README обох) | `docs/MAVLINK-ROUTER.md` §2, §5 |
| ArduPilot приймає `RC_CHANNELS_OVERRIDE` лише від sysid з `MAV_GCS_SYSID`(`_HI`); `FS_GCS_ENABLE` типово 0 | `docs/MAVLINK-ROUTER.md` §6; міст `bench/tx12_bridge.py` |
| Імена активів `mavp2p` для arm64: суфікс `arm64v8` (SRC `scripts/binaries.mk`); твердження про glibc ≥ 2.42 для `mavlink-router` не знайдено в джерелах: UNVERIFIED | `docs/MAVLINK-ROUTER.md` §4, §8; `docs/PI-PORT.md` §6 |
| Тести були зеленими під root, але червоними на непривілейованому CI (запис у реальний `/dev/tty1`): набір завжди ганяти й під `nobody` | розділ 5 вище; `tests/run.sh` |
| У цій сесії `github.com`/`api.github.com`/raw інколи відмовляли (403), тому будь-які `*_PIN` у профілях лишаються порожніми, а непідтверджене позначено UNVERIFIED | `docs/BOARD-RPI4.md`, `docs/MAVLINK-ROUTER.md` §8 |
| Урок щодо інструментів: `pkill -f <шаблон>` може вбити власну оболонку (шаблон збігається з командним рядком самої оболонки) | процес (INF) |

## 6. Відкриті питання (потрібне залізо або доступ)

1. Ін'єкція RTL8812/8814 на Pi 4/5, 16K-ядро, DKMS на 6.12/6.18.
2. `v4l2slh265dec` + `rpivid` на Pi 4/5; HW-кодер H.264 Pi 4 і `v4l2h264enc`.
3. Затримка відео від скла до скла; ліміти USB-струму Pi 4.
4. EdgeTX: список підтримуваних радіо, AUX у TX12 MKII, рендер кирилиці на ч/б, налаштування зовнішнього модуля.
5. `RC_OVERRIDE_TIME` = 0/-1, типове `FS_GCS_ENABLE`, підпис MAVLink2.
6. Адаптивна лінія alink і msposd у стенді не відтворені.
- До п. 5 (2026-10-01, SRC, `docs/MAVLINK-ROUTER.md`): `RC_OVERRIDE_TIME` типово 3 с, `0` вимикає overrides, `-1` = без таймауту (`RC_Channels_VarInfo.h`, `RC_Channel.cpp`); `FS_GCS_ENABLE` у Copter master типово 0 = вимкнено (`ArduCopter/Parameters.cpp`).
- Override приймається лише від sysid з `MAV_GCS_SYSID` (типово 255, стара назва `SYSID_MYGCS`) / `MAV_GCS_SYSID_HI` (`GCS.cpp`); heartbeat інших sysid GCS-failsafe не живить. Лишається відкритим: підпис MAVLink2 і типові значення в конкретних стабільних релізах.

7. (2026-10-02) Нове, що вимагає заліза: міст `bench/tx12_bridge.py` з реальним TX12 (назви осей evdev, діапазони, «свіжість» нерухомого стіка) і реальним FC (прийом override при `MAV_GCS_SYSID`, відновлення RC після `RC_OVERRIDE_TIME`, ArduPilot #32862); служба `gs-mavlink` з реальним `mavp2p`/`mavlink-router`; безпечний root-вхід у справжньому образі (`GS_*` до `chroot` через `sudo -E`; чи є інший акаунт для SSH); реальна робота `otg-gadget.sh`/`change_otg_mode` і рефакторингів M3a/b/d на Radxa; імена `gpiochipN` і таблиця пінів Pi 4 (`gpioinfo`); 8 UNVERIFIED значень `gs/boards/rpi4/board.conf`.
8. (2026-10-02) Потрібне рішення/перевірка: дефекти `gsmenu.sh` (`:655` `ifname wifi`, `.records.enabled` проти `.records.enable`, див. §4b) і неоднозначності M3d (`gs.sh` `rec_dev …p4` проти `p5` у `gs-init.sh`); реальні `*_PIN` для `build/versions.env` (потрібен доступ до GitHub API або `GS_ALLOW_UNPINNED=1`).

## 7. Рішення власника проєкту, що очікують

Базова ОС (Bookworm із зафіксованим ядром рекомендовано), збирач образу (chroot із шаром плати чи pi-gen), `/config`, чи потрібен Ruby на Pi, старт Фаз 0–1.

## Знахідки 2026-10-04 (міграція Pi, REPO/INF)
- (REPO) На Pi нічого не виставляло `BOARD`: скрипти мовчки брали профіль Radxa. Тепер `BOARD` env > `/etc/gs-board` > `/proc/device-tree/model` > `radxa-zero3`; невідома плата все ще дає Radxa (B2: небезпечно для Pi 3B+).
- (REPO) `mount -o remount,rw /media/root-ro` безумовний у `gs-init.sh` і `gs-applyconf.sh` (B1): на Pi OS без overlayroot ламає старт під `set -e`.
- (REPO) У `stream.sh` гілка gstreamer жорстко використовує `mppvideodec` (Rockchip): відео на Pi ще не працює.
- (SRC) pi-gen@bookworm `stage2/02-net-tweaks` ставить `network-manager`: на Pi мережею володіє NM (R7).
- (REPO) Sandbox-тести: `LOGONLY` shim-и завжди успішні; для збою перезаписати shim у `case_setup`; `conf_set` лише для наявних ключів.
- (INF) Контейнер сесії досягає control plane/DERP Tailscale по HTTPS, але `tailscaled` не встановлений і не запускається (класифікатор); плата в tailnet недосяжна.
