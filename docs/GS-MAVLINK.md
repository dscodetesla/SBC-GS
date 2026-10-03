# Служба `gs-mavlink` (M4, без заліза)

Статус: файли додано, **нічого не запускалося проти справжнього маршрутизатора чи FC** (UNVERIFIED). `gs/install.sh`, `gs/gs.conf` і `gs/*.sh` не змінено: служба ставиться вручну. Факти про синтаксис `mavp2p`/`mavlink-router` і правила ArduPilot беруться з `docs/MAVLINK-ROUTER.md` (SRC); тут нічого понад них не додано.

Файли: `gs/mavlink/gs-mavlink.sh` (збирає командний рядок), `gs/mavlink/gs-mavlink.conf.example`, `gs/mavlink/gs-mavlink.service`; тест `tests/static/gs-mavlink.sh` + golden `tests/golden/static/gs-mavlink.out`, фікстури `tests/fixtures/gs-mavlink/`.

## 1. Архітектура

```
FC (ArduPilot) ◄─ MAVLink ─► wfb-ng (0x10/0x90) ──UDP connect://127.0.0.1:14550──► gs-mavlink (mavp2p)
                                                                                    ├─ udps :14560 ◄── QGC/MAVProxy (UDP)
                                                                                    ├─ tcps :5760  ◄── телефон (за потреби)
                                                                                    └─ serial (за потреби, FC напряму)
```

Як wfb-ng підключається (REPO `gs/wfb.sh:87`, SRC `docs/CHAINS.md`): `[gs_mavlink] peer = 'connect://${wfb_outgoing_ip}:${wfb_outgoing_port_mavlink}'`, типово порт 14550 (`gs/gs.conf:114`). wfb-ng лише **шле UDP** на цю адресу, тому маршрутизатор слухає як UDP-сервер (`udps:0.0.0.0:14550`). Порт upstream має збігатися з `wfb_outgoing_port_mavlink`. Клієнтам GCS віддано **інший** порт (типово 14560), бо 14550 зайнятий upstream (див. також відкрите питання про конфлікт з ELRS Backpack у `docs/CHAINS.md`).

## 2. Правило єдиного писача RC

- `mavp2p` **не має фільтрів** і не переписує sysid (SRC `docs/MAVLINK-ROUTER.md` §2-3). Обмежити RC на маршрутизаторі неможливо.
- ArduPilot приймає `RC_CHANNELS_OVERRIDE` лише від sysid, який вважає «своєю GCS» (`MAV_GCS_SYSID`, типово 255). Отже: на FC задати `MAV_GCS_SYSID` = sysid мосту TX12, QGC лишити 255; QGC не повинен слати RC (джойстик у QGC вимкнути). Також не підключати `bench/gs_mav.py` і `fake_fc.py` до апарата з гвинтами.
- RC через wfb-ng не може бути єдиним каналом керування (основний: ELRS).
- Для `mavlink-router` є `BlockMsgIdIn = 70` у конфіг-файлі ендпоінта (SRC README), але скрипт такий файл **не генерує** і msgid 70 не перечитано в `common.html` (UNVERIFIED).

## 3. Таблиця sysid

| sysid | Хто | Джерело |
|---|---|---|
| 1 | апарат (типово) | INF |
| 3 | sysid, який wfb-ng ін'єктує за замовчуванням | SRC `master.cfg` (`docs/PI-PORT.md` §6); `HB_SYSID=3` відхиляється |
| 125 | heartbeat маршрутизатора `mavp2p` (`--hb-systemid`, типово 125) | SRC README mavp2p |
| 255 | QGC / типовий `MAV_GCS_SYSID`; `HB_SYSID=255` відхиляється | SRC `GCS.cpp` |
| вибирається | міст TX12 = значення `MAV_GCS_SYSID` на FC | INF |

Heartbeat sysid 125 за типових налаштувань failsafe-лічильником FC **не** рахується (SRC `docs/MAVLINK-ROUTER.md` §6): GCS-failsafe потребує heartbeat від sysid, що проходить `sysid_is_gcs`.

## 4. Довідник конфігурації `/config/gs-mavlink.conf`

Шлях можна перевизначити змінною `GS_MAVLINK_CONF`. Файл необов'язковий (тоді діють типові значення).

| Ключ | Типово | Перевірка / ефект |
|---|---|---|
| `ROUTER` | `mavp2p` | `mavp2p` або `mavlink-router` (бінарник `mavlink-routerd`), інше -> exit 2 |
| `UPSTREAM_BIND`, `UPSTREAM_PORT` | `0.0.0.0`, `14550` | IPv4; порт 1..65535; `udps:BIND:PORT` |
| `GCS_UDP_PORTS` | `14560` | список портів `udps:0.0.0.0:P` (лише mavp2p; для mavlink-router має бути порожнім) |
| `GCS_UDP_CLIENTS` | порожньо | список `ip:port`: `udpc:` (mavp2p) або `-e` (mavlink-router) |
| `TCP_ENABLE`, `TCP_PORT` | `0`, `5760` | `tcps:0.0.0.0:P`; для mavlink-router `-t P`, вимкнено = `-t 0` (SRC). Без автентифікації |
| `SERIAL_DEV`, `SERIAL_BAUD` | порожньо, `115200` | `serial:/dev/..:baud` (лише mavp2p); шлях лише `/dev/` і безпечні символи |
| `HB_SYSID` | `125` | 1..254, окрім 3; `--hb-systemid=` (лише mavp2p) |
| `HB_DISABLE` | `0` | `--hb-disable` замість `--hb-systemid` |
| `STREAMREQ_DISABLE` | `0` | `--streamreq-disable` |
| `DUMP_ENABLE`, `DUMP_PATH` | `0`, `/var/log/gs-mavlink/2006-01-02_15-04-05.tlog` | `--dump --dump-path=` (лише mavp2p), абсолютний шлях |

Скрипт відхиляє (exit 2): порт поза 1..65535, нечисловий порт, повторення порту між upstream/GCS/TCP, `HB_SYSID` 0/3/255/поза діапазоном, невідомий `ROUTER`, послідовний FC або `GCS_UDP_PORTS` з mavlink-router.

Режими: `gs-mavlink.sh --print` друкує нотатки (`# ...`) і командний рядок та виходить з 0 без запуску; без аргументів робить `exec`.

## 5. Установка (вручну)

```bash
# бінарник mavp2p в /usr/local/bin (імена активів: mavp2p_<ver>_linux_arm64v8.tar.gz, див. MAVLINK-ROUTER.md §4; UNVERIFIED)
sudo install -d /gs/mavlink && sudo install -m 755 gs/mavlink/gs-mavlink.sh /gs/mavlink/
sudo cp gs/mavlink/gs-mavlink.conf.example /config/gs-mavlink.conf   # відредагувати
sudo cp gs/mavlink/gs-mavlink.service /etc/systemd/system/ && sudo systemctl daemon-reload
/gs/mavlink/gs-mavlink.sh --print       # перевірити рядок
sudo systemctl enable --now gs-mavlink
```

Юніт працює без root (`DynamicUser=yes`, група `dialout` лише для послідовного порту), `Restart=on-failure`, без секретів. `ExecStart` без `PATH`-пошуку бінарника: `mavp2p`/`mavlink-routerd` мають бути в `PATH` служби (`/usr/local/bin`). Запис tlog іде у `LogsDirectory=gs-mavlink` (`/var/log/gs-mavlink`).

## 6. Що UNVERIFIED

- Жодного запуску з реальним `mavp2p`/`mavlink-routerd`, wfb-ng чи FC; тест лише порівнює рядок із golden.
- Наявність і назви релізних бінарників mavp2p; збірка mavlink-router.
- Чи `mavlink-routerd` приймає кілька позиційних аргументів (тому послідовний FC для нього заборонено), власний heartbeat і прапорці tlog для нього; msgid 70.
- Поведінка цільової маршрутизації `RC_CHANNELS_OVERRIDE` в mavp2p при кількох клієнтах (потрібен SITL/залізо).
- `systemd`-ізоляція (`ProtectSystem=strict`, `DynamicUser`) на образі Radxa/Pi і доступ DynamicUser до `/dev/ttyAMA0` через `dialout`.
- Інтеграція з `gs/install.sh` і `gs.sh` свідомо не робилася.
