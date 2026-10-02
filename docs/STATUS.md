# Знімок стану проєкту (2026-10-02) і план фізичної перевірки

Фіксація перед початком перевірки на залізі (з понеділка). Усе нижче станом на коміт `6790f8b` у гілці `claude/hopeful-brown-k5mmbm` (PR #1, CI зелений: `lint`, `golden`, `loopback`).
Позначки: **REPO** перевірено в репозиторії/CI, **HW** перевіряється лише на залізі, **UNVERIFIED** не підтверджено.

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
