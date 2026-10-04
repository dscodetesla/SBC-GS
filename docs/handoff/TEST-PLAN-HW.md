# План перевірки плану: що саме міряємо на залізі й у якому порядку

Станом на 2026-10-04. Це план **перевірки** `docs/ROADMAP-EXECUTION.md` + `docs/STATUS.md` §4, а не новий план розробки. Нових шарів симуляції й SYNTH-чисел не додає (`ROADMAP-EXECUTION.md`, доповнення 2026-10-03).
Позначки: **REPO** є в репозиторії, **HW** лише на залізі, **UNVERIFIED** не підтверджено, **GATE** крок, який виконує тільки людина.
Вхідні документи: `docs/ROADMAP-EXECUTION.md`, `docs/STATUS.md` §4 і три звіти проєкту (11 пунктів плану для Pi 3/4/5 станом на 2026-10-03; розбір «як органічно реалізувати», кроки 1-6; карта процесів). Проєктний документ claude.ai (`overviewDoc=output:cmsg_012DFg…`) із сесії недоступний (Claude Docs: `deny/access`).
Перевірено мною в коді на `ffefcfa` (REPO): блокер B1 (`gs-applyconf.sh:138-141`, `gs-init.sh:49-50` безумовний `mount -o remount,rw /media/root-ro`), блокер B2 (`gs/lib/board.sh:36` типово `radxa-zero3`, `gs/lib/hw.sh:33` типово `gpt`, профілів `rpi3bp` немає), `mppvideodec` у `stream.sh:101-102`, `local_node` без `Restart=` (`wfb.sh:62`), `tests/run.sh` = 228 ok. Не перечитано: `gs-init.sh:36` (`sgdisk -ge`) і номери рядків звітів поза переліченими.

## 1. Принцип

Кожне твердження плану, що тримається на HW/UNVERIFIED, має **один вимір, один критерій проходження й одне місце запису**. Пункт без критерію в таблицю не потрапляє. Результат = вивід команди цілком у `bench/out/` + рядок у `bench/RESULTS.md`.

## 2. Доступ до плати (стан сесії, REPO-факти з транскрипту)

- Плата `sbc-gs` = `100.72.78.99` у tailnet користувача. Яка саме це модель (Pi 4 чи Pi 5) і що підключено (RTL8812, FC, TX12): **UNVERIFIED**, першим читається `bench/doctor.sh` (поле `board_model`).
- З хмарного контейнера порт 22 цієї адреси **недосяжний**; піднімати tailnet із контейнера автоматичний режим заборонив, тому без явного дозволу власника це не робиться (див. §6).
- До появи доступу діє режим «власник запускає, агент розбирає»: власник виконує блоки з §4 на платі й кладе вивід у `bench/out/`, агент прогоняє `bench/ingest.py` і `tests/sim/models/calib.py`.

## 3. Матриця: твердження плану -> перевірка

| ID | Твердження плану (джерело) | Вимір на залізі | Критерій проходження | Запис |
|---|---|---|---|---|
| H0 | Плата й ОС такі, як у плані (`STATUS.md` §4.2 №1) | `bench/doctor.sh`: `kernel`, `pagesize`, `board_model`, `get_throttled` | Pi 5: `pagesize=16384`, `get_throttled=0x0`; Bookworm (рішення R4) | `RESULTS.md`, шапка |
| H1 | DKMS `svpcom` збирається на 6.12 і 16K-сторінках (D1, `STATUS.md` §4.3.1) | `./bench.sh setup gs`, `dkms status`, `lsmod`, `ethtool -i <wlan>` | модуль завантажено; `version` **порожня** (T1) | T1, `dmesg` |
| H2 | Monitor-режим і ін'єкція працюють (D1) | `iw dev`, `iw list` (monitor), короткий `wfb_tx` у ефір лише **з антеною** | інтерфейс у monitor; пакети йдуть без помилок драйвера | T2 |
| H3 | `gpiodetect` на Pi: мітки ліній/RP1 (K12, `STATUS.md` §4.3.3) | `doctor.sh`: `gpiodetect`, `gpioinfo_head` | RP1-чип знайдено за міткою `pinctrl-rp1`; імена `GPIO<BCM>` збігаються з `rpi4/pinmap.conf` | `board.conf` (знімає UNVERIFIED) |
| H4 | Живлення: струм TX адаптера, просадка USB (модель SYNTH, найчутливіше) | `doctor.sh` (`pi5_pmic_*`, `hwmon`, `usb_sysfs`, `dmesg_usb_power_events`) під TX і без | `get_throttled=0x0`, 0 подій undervoltage/disconnect за 10 хв (R6); число струму йде в `calib.py sheet` | `RESULTS.md`, `calib` |
| H5 | Відео на хості: декодери (T8, `GUIDE.md` етап 6-7) | `DECODER=...` + `top` | зафіксовано CPU й чи йде картинка; Pi 5: лише HEVC апаратно (SNIP) | T8 |
| H6 | `MAV_GCS_SYSID` моста доходить до FC (`STATUS.md` §4.3.4) | `tx12_bridge.py --input sweep --confirm-props-off --real-fc-armed-ok --sysid <MAV_GCS_SYSID>` | канали 1-4 у телеметрії FC змінюються; з іншим `--sysid` FC їх ігнорує | T9 |
| H7 | Dead-man моста: FC відпускає RC (ArduPilot #32862, `STATUS.md` §4.3.2) | Ctrl-C під час `sweep` | газ 1000, далі звільнення; FC повертається до RC-приймача за ≈3 с (`RC_OVERRIDE_TIME`), без «залипання» | T9 |
| H8 | GCS-failsafe реального ArduPilot (E3) | зупинити міст/`gs_mav.py` | STATUSTEXT про failsafe за `FS_GCS_TIMEOUT`; реакція записана (БЕЗ гвинтів) | T6/R2 |
| H9 | `gs-mavlink` з реальним `mavp2p` (M4) | `gs/mavlink/gs-mavlink.sh --print`, запуск, два клієнти (QGC + міст) | прапорці прийняті; обидва клієнти бачать телеметрію; єдиний писач RC за `MAV_GCS_SYSID` | нове поле в `RESULTS.md` |
| H10 | Відновлення після обриву (T7/R3) | `systemctl stop/start wifibroadcast@*` | відео й MAVLink зникають і відновлюються самі | T7 |
| H11 | Реальний TX12 MKII (evdev) (R4b) | `tx12_bridge.py --input evdev:/dev/input/eventN` після `sweep` | імена осей/діапазони збігаються з `tx12_map.example.json` (0..2047, центр 1024) | T9 |

Кожен вирок H1-H11 одразу знімає позначку HW/UNVERIFIED у відповідному доку (`STATUS.md` §5).

### 3b. Колія S: перевірка GS-софту на Pi (кроки rollout, кожен = окремий коміт і окрема перевірка)

Базова перевірка після **кожного** кроку: перезавантаження; `systemctl --failed` порожній; `journalctl -b -u gs` закінчується на `gs service start completed`; `ip link show br0`; `iw dev`. Тести репозиторію (`tests/run.sh`, 228 ok) підміняють `mount`/`systemctl`, тож ловлять регресії, але не доводять роботу на Pi (INF).

| ID | Що доводимо | Перевірка на Pi | Критерій | Статус у коді (REPO) |
|---|---|---|---|---|
| B1/S1 | `/media/root-ro` потрібен лише коли overlayroot справді змонтований (блокер: на чистій Pi OS не стартує нічого) | Pi 4 з чистою Pi OS: старт `gs`; `systemctl is-enabled gs-init` | `gs` доходить до кінця; `gs-init` = disabled; одне перезавантаження, а не цикл | **не виправлено**, блокер підтверджено читанням |
