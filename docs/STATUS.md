# Знімок стану проєкту (оновлено 2026-10-03) і план фізичної перевірки

Фіксація перед початком перевірки на залізі (з понеділка). Усе нижче станом на коміт `6790f8b` у гілці `claude/hopeful-brown-k5mmbm` (PR #1, CI зелений: `lint`, `golden`, `loopback`).
Позначки: **REPO** перевірено в репозиторії/CI, **HW** перевіряється лише на залізі, **UNVERIFIED** не підтверджено.

## 0. Оновлення 2026-10-03 (поверх знімка нижче; голова гілки `6525332`)

Позначки: **REPO** перевірено мною (тести, nobody, мутації); **WIP** у робочому дереві, ще не перевірено/не закомічено; **HW** лише на залізі.

| Напрям | Стан | Доказ |
|---|---|---|
| CI | `lint`, `golden`, `loopback`, `sim-smoke` (блокуючий) | REPO, run 37043623138 |
| Віртуальний DKMS (4 драйвери × Pi-ядра) | 16 крос-збірок, завантаження в QEMU | REPO, `docs/SIM-DKMS.md` |
| Моделі RF/живлення/затримки + стохастичний рушій сценаріїв | 112 тестів, 14/14 мутацій вбито, nobody OK, каталог 37 сценаріїв; UNMEASURED 48, SYNTH 68 (храповики) | REPO, `docs/SIM-SCENARIOS.md`. **Усі числа SYNTH/UNMEASURED, не виміри** |
| Біомімітичний аналіз контуру | 17 тестів | REPO, `docs/SIM-BIOMIMETIC.md` |
| Динамічна конфігурація (реєстр 145 ключів, 14 safety з межами, профілі) | **WIP** (агент ще працює); виправлення S1 залежить від непакетованого `config/load.sh` | не коміт |
| Віртуальні GPIO/USB/udev | **WIP** (агент ще працює) | не коміт |

Контур (див. `docs/CONTOUR-AIR.md`, `CONTOUR-GS.md`, `MEMORY.md` §2): AIR радіо WiFiLink2 = RTL8812EU (не AU); GS-донгл 8812AU; RX ELRS 900 МГц, модель не підтверджена (ES900RX чи Xrossband Gemini); TX12 з вбудованим Multi і зовнішнім TX; Matek SLIM (H743). HW не торкались.

Що першим міряти за чутливістю моделі (SYNTH-рейтинг): струм TX адаптера, `misc_loss`, просадка USB, нулі антени, коефіцієнт RX-антени, пік TX.

## 1. Що зроблено й перевірено без заліза (REPO)

| Віха | Що саме | Доказ |
|---|---|---|
| M0 CI | `lint` (shellcheck, py_compile, ratchet), `golden`, `loopback` + тест моста TX12 | GitHub Actions зелений |
| M1 тестова сітка | 200 golden-сценаріїв спадкових скриптів + 12 статичних перевірок; golden знято з **незміненого** коду; перевірено під root і під `nobody` | `tests/run.sh` → 212 ok |
| M2 контракт плати | `gs/boards/radxa-zero3`, `validate.sh`, `gs/lib/board.sh` | `static/boards` |
| M3a/b/d | GPIO (`gpio.sh`), OTG і udev-шаблони (`otg.sh`, `render-udev.sh`), шляхи й датчики (`hw.sh`, `board_conf.py`); усі golden «до» байт-в-байт незмінні | `static/{gpio,otg,udev-render,paths}` |
| M4 | `gs/mavlink/` (генератор команди `mavp2p`/`mavlink-router`, unit), `bench/tx12_bridge.py` (один писач RC, dead-man, clamp, lock) | `static/gs-mavlink`, `bench/tx12-bridge-test.sh` (27 перевірок) |
| M5 (частково) | безпечний root-вхід у `build.sh` (opt-in `GS_LEGACY_ROOT_LOGIN=1`), ratchet-и пінування й небезпечних дефолтів, `build/lib/fetch.sh` (sha256, git SHA) | `static/{pins,security-defaults,fetch}` |
| M6 крок 1 | чернетка `gs/boards/rpi4`, сентинел OTG `none`, `GPIO_PIN_NUMBERING`/`GPIO_PIN_MAP`, `DTBO_MODE`, `PART_TABLE` (на рівні бібліотек) | `static/{rpi4-draft,contract-ext}` |

Продуктовий код `gs/`, `build/` змінено лише для M3a/b/d (5+ підстановок літералів на виклики бібліотек з Radxa-запасними значеннями) і блоку root-login у `build.sh`; решта додано новими файлами.

## 2. Що НЕ зроблено і чому

- **M3c** (оверлеї, завантажувач, RO-root у `gs-applyconf.sh`/`gs-init.sh`): помилка може зламати завантаження Radxa, потрібна HW-перевірка.
- **M6 спайк Pi-образу**, **M7 стенд**: потребують заліза.
- **Реальні `*_PIN`** у `build/versions.env` порожні: GitHub API/raw з сесії недоступні (403), обходу не робилось. `fetch`-хелпери до `build.sh` не підключені.
- **SITL** замість `fake_fc.py` не інтегрований.
- Не покриті тестами: безкінечні цикли `button.sh` (gpiomon) і запису `stream.sh`; `set air presets` у `gsmenu.sh`.
- **Рішення власника** (розділ 4 `ROADMAP-EXECUTION.md`): база ОС, збирач образу, `/config` на Pi, обсяг стеків.

## 3. Відомі дефекти спадкового коду (не виправлені)

Деталі: `docs/KNOWLEDGE.md` §4a (`gs-applyconf.sh`), §4b (`gsmenu.sh`). Найважливіші: `gsmenu.sh:655` `ifname wifi` без `0`; get/set `.records.enabled` проти `.enable`; `gs.sh` `rec_dev …p4` проти `p5` у `gs-init.sh`; ділення на нуль для невідомого типу карти. Виправляти лише після підтвердження на залізі.

## 4. План фізичної перевірки (з понеділка)

Основний документ: `docs/GUIDE.md` (етапи 0-10). Нижче порядок і що дивитись першим. **Безпека на кожному кроці: гвинти знято, батарея й ESC від'єднані, FC лише від USB; адаптери не вмикати без антен; мінімальна потужність на столі.**

### 4.1 Перед виїздом (на ноутбуці, без заліза, ≈10 хв)
1. `git checkout claude/hopeful-brown-k5mmbm && git pull`.
2. `tests/run.sh` (очікується 212 ok); також як `nobody` на копії (див. `CLAUDE.md`).
3. `cd bench && PY=<python з pymavlink> ./bench.sh loopback` (3 PASS) і `./tx12-bridge-test.sh` (27 PASS).

### 4.2 Порядок на стенді (мінімум до максимуму ризику)
| № | Що | Етап гайда | Що записати в `bench/RESULTS.md` |
|---|---|---|---|
| 1 | Образи microSD, перший запуск Pi 4 (AIR) і Pi 5 (GS), мережа, SSH | 2-3 | версія ОС/ядра (`uname -r`), `getconf PAGESIZE` (Pi 5: 16K), блок живлення |
| 2 | `bench/check.sh` на обох | 3 | вивід цілком |
| 3 | Драйвер RTL8812 (DKMS, `install-driver.sh`) на 6.12 | 4 | успіх/збій збірки, `iw dev`, режим monitor (закриває D1) |
| 4 | wfb-ng, ключі (**не перезапускати `wfb_keygen`**), канал, мінімальна потужність | 4-5 | `wfb-cli`/логи, RSSI, FEC |
| 5 | Емуляційний контур відео + MAVLink (без FC/камери) | 6 | PASS/FAIL T1-T8, RTT |
| 6 | Хост: GStreamer, QGC/клієнт | 7 | затримка (грубо), втрати |
| 7 | Реальний FC по USB (**без гвинтів**), `MAV_GCS_SYSID`, `FS_GCS_ENABLE=1`, `RC_OVERRIDE_TIME` | 8-9 | параметри, телеметрія |
| 8 | Міст TX12 → MAVLink (`tx12_bridge.py --input evdev`, спершу `--input sweep`) | 0, 9 (R4b) | назви осей evdev, діапазони, dead-man (чи відпускає FC), ArduPilot #32862 |
| 9 | Служба `gs-mavlink` з реальним `mavp2p` (розкласти бінарник, `--print` → запуск) | 7 | прапорці прийняті? два клієнти? |

### 4.3 Що перевірити першим як найціннішу невідомість (знімає найбільше HW/UNVERIFIED)
1. DKMS-збірка `svpcom` рівно на `6.12` (і `16K` сторінки Pi 5) та ін'єкція в monitor-режимі (D1).
2. Dead-man моста: після зупинки входу FC має відпустити RC (`RC_OVERRIDE_TIME` 3 с) без «залипання» (ArduPilot #32862).
3. `gpioinfo` на Pi 4: імена `gpiochipN` і ліній `GPIO<BCM>` (для `rpi4/pinmap.conf`).
4. Чи доходить `MAV_GCS_SYSID` (моста) до FC і чи спрацьовує GCS-failsafe.

### 4.4 Що принести/мати
Два Pi з БЖ (Pi 5 — 5 А), 2 адаптери RTL8812 з антенами, microSD, Ethernet+комутатор, ноутбук, FC з USB-кабелем, TX12 MKII + ES900TX (і ES900RX, якщо перевіряєте основний канал), мультиметр, якісні USB-кабелі.

## 5. Куди записувати результати й що оновити після
- Числа й вивід: `bench/RESULTS.md` (шаблон має T1-T9).
- Після прогону: зняти позначки HW у `docs/` за фактичними результатами, оновити `docs/KNOWLEDGE.md` (§3 факти, §6 відкриті питання), перерахувати пріоритети в `docs/ROADMAP-EXECUTION.md` (кроки 8 розділу 3), заповнити 8 UNVERIFIED у `gs/boards/rpi4/board.conf` (ratchet лише зменшується).
- Якщо щось падає: зберегти вивід команд цілком і повідомити, я виправлю й додам регресійний тест.

## 6. Повторення стану
Відновити контекст у новій сесії: `CLAUDE.md` → `docs/KNOWLEDGE.md` → `docs/ROADMAP-EXECUTION.md` → цей файл. PR #1 (https://github.com/dscodetesla/SBC-GS/pull/1) ще відкритий; його гілка містить усе описане.

## 7. Шари симуляції на 2026-10-03 (REPO, усе пройшло `tests/sim/layers.sh` та, де можливо, як `nobody`)

| Шар | Тести | Мутації | Доків |
|---|---|---|---|
| models (RF/живлення/затримки/рушій сценаріїв) | 118 | 14/14 | `SIM-SCENARIOS.md` |
| validate (інваріанти, збіжність, back-test) | 70 | 22/22 | `SIM-VALIDATION.md` |
| twin (події рушія -> реальні міст/реле/модель FC) | 66 | 20/20 | `SIM-TWIN.md` |
| fuzz (диф. fuzz, відмови реальних скриптів) | 77 | 23/23 | `SIM-FUZZ.md` |
| virt (QEMU: gpio-sim, usbip, hwsim; опційно) | 96 PASS | 3/3 | `SIM-VIRT-DEVICES.md` |
| bio, dkms | 17, 21 пунктів | | `SIM-BIOMIMETIC.md`, `SIM-DKMS.md` |

Знайдено й НЕ виправлено: 6 дефектів у віртуальному шарі (udev-імена, `OTG_MODE_FILE=none` на rpi4, `mavp2p` не завершується при втраті FC тощо), 22 дефекти fuzz (D14 виконання `custom.conf` як shell від root, D17 `fan.sh` падає на порожній температурі), 13 невідповідностей моделей. Рішення про виправлення за власником. Усі числа моделей SYNTH/UNMEASURED.

## 8. Стан після закриття дефектів і нових шарів (2026-10-03, REPO; усе нижче перевірено на чистій копії, як `nobody`, із мутаціями)

| Шар | Тести | Мутації | Що доводить / НЕ доводить |
|---|---|---|---|
| `tests/run.sh` (golden + static) | 220 ok | дані в підшарах | логіка скриптів у пісочниці; не залізо |
| models | 134 | усі вбито | SYNTH-модель; помилки D1, D2, D3, D4, D5-D12 виправлено (`SIM-VALIDATION.md`) |
| validate | 89 | 51/51 | інваріанти, збіжність, back-test |
| twin | 66 | 20/20 | події рушія -> справжні міст, реле, модель FC |
| fuzz | 91 | 59/59 | 22 дефекти реального коду виправлено й закріплено |
| netfetch | 74 | 27/27 | `fetch.sh` із реальним `curl` на loopback; не реальний GitHub |
| powerlab | 51 | усі вбито | парсери й конвеєр доктор -> оверлей -> what-if; реальні струми HW |
| evdev | 36 офлайн, 61 у QEMU | 16/16 | справжній `EvdevSource` на ядерному `uinput`/`uhid`; реальний TX12 HW |
| virt (QEMU) | gpio 53, roconf 177, usb 31, radio 27 | на копії | gpio-sim (в т.ч. чіп Pi 5 за міткою `pinctrl-rp1`), реальний overlayfs/ext4/vfat, hwsim, usbip |
| dkms | 21 пункт `--check`, 16 крос-збірок | | збірка проти справжніх заголовків Pi; патчі 0001/0002 застосовуються до справжніх джерел для 6.12 |

CI (`lint`, `golden`, `loopback`, `sim-smoke`, `sim-layers`) блокуючий. Рішення: Pi 5 = Bookworm (R4), `LISTEN_ADDR` 0.0.0.0 із захистом (R1), RX/TX = V-900 (R2) у `docs/DECISIONS.md`.

Виправлено в продуктовому коді цією серією: D1-D22 fuzz (виконання `custom.conf` від root, `fan.sh` на непрочитаній температурі, валідація профілів/плат/udev/`fetch.sh`, міст), атомарний запис `gs.conf` (`gs/lib/gsconf.sh`), guard OTG/вентилятора для плат без них (`gs.sh`, `otg-gadget.sh`, `button.sh`), `install-driver.sh` (патчі за ядром, чорний список `rtw88_*`), VA-API на хості Ubuntu 26 (`video-rx.sh`), приклад мапи TX12 (0..2047, центр 1024: старі placeholder давали повне відхилення на центрі стіка).

Лишається на залізі (окремий pipeline, `docs/STATUS.md` §4): реальні probe/monitor/ін'єкція RTL, RF, затримка HEVC, реальні струми RTL8812 і поведінка автомата Pi 5, реальний TX12 MKII (дескриптор, ім'я, таймінги), `gpiodetect` на Pi 5 (мітка й номер RP1), роль USB-C, реальний образ (`gs.conf` на флеші, втрата живлення), `fetch.sh` проти реального GitHub, QGC на Ubuntu 26 Wayland, VA-API на реальному GPU.
