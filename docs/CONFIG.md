# Шарувата конфігурація (`config/`)

Статус: механізм і тести є, на залізі нічого не перевірялось (UNVERIFIED для HW). Теги: REPO = перевірено командою в цьому репозиторії, INF = висновок.
Мета: усі нетривіальні константи нового інструментарію (`bench/`, `gs/mavlink/`, `tests/sim/*`) керуються з одного місця **без зміни типової поведінки**.
Перевірка: `tests/static/config.sh` (golden `tests/golden/static/config.out`, фікстура `tests/fixtures/config/legacy-defaults.txt`).

## 1. Пріоритет (від вищого до нижчого)

| # | Шар | Де | Мітка в `show` |
|---|---|---|---|
| 1 | прапорець CLI | `--deadman-ms 250` тощо (лише Python-інструменти) | `[cli]` у помилках |
| 2 | змінна середовища | `SBC_GS_<KEY>`; для ключів стенда й `SMOKE_*`/`WFB_NG_REF` лишилось «голе» ім'я (`VIDEO_CODEC`, `FC_SYSID`); порожня змінна = не задана | `env:<ІМ'Я>` |
| 3 | додатковий файл скрипта | лише `gs-mavlink`: `$GS_MAVLINK_CONF` (`/config/gs-mavlink.conf`); стенд: `bench/env` | `file:<шлях>` |
| 4 | файл для хоста | `$SBC_GS_CONFIG`, типово `/config/sbc-gs.env` | `host:<шлях>` |
| 5 | профіль | `config/profiles/<ім'я>.env`, ім'я з `SBC_GS_PROFILE` (каталог: `SBC_GS_PROFILE_DIR`) | `profile:<ім'я>` |
| 6 | вбудоване типове значення | `config/registry.tsv` | `default` |

Зміна проти старого `bench/lib.sh` (REPO): раніше `. bench/env` **перезаписував** змінні середовища (файл вигравав), тепер середовище виграває, як у всіх інших шарах.

## 2. Реєстр `config/registry.tsv`

Один рядок = один ключ, 10 полів через TAB, порожніх полів немає (`~` = порожнє типове, `-` = немає):
`KEY, default, type, min, max, unit, safety(y/n), owner(и через кому), env, description`.
Типи: `int`, `float`, `port` (1..65535), `ip` (IPv4), `path` (абсолютний, без `..`), `bool` (0/1), `str`, `enum:a|b`; суфікс `?` дозволяє порожнє значення.
Поточний обсяг: 145 ключів, з них SAFETY: 14 (`awk -F'\t' '$7=="y"' config/registry.tsv`).
Власники: `tx12`, `gs_mav`, `fake_fc`, `gs-mavlink`, `bench`, `sim`.

## 3. Завантажувачі

- Shell: `. config/load.sh; sbc_cfg_load [--i-know] [--no-value-check] [--extra-file F --extra-owner O] OWNER...` виставляє змінні `<KEY>` і `SBC_CFG_SRC_<KEY>`.
- Python (лише stdlib): `config/load.py`, `cfg = load.load(["tx12"], argv=...)`; `cfg["TX12_DEADMAN_MS"]` типізоване, `cfg.src[...]` шар, `cfg.check_cli(key, value)` перевіряє значення з CLI.
- Інспекція: `config/sbc-gs-config show [--defaults] [--owner O] [--i-know]`, `get KEY`, `check FILE [OWNER]`, `keys`. Те саме: `python3 config/load.py ...`; вивід обох збігається побайтово (перевіряє тест).
- Помилка конфігурації = вихід 2 з повідомленням; скрипти не продовжують із частково прочитаною конфігурацією (fail-closed).

### Файли читаються як дані (виправлення S1)
Рядок має бути `KEY=значення`, `KEY='значення'` або `KEY="значення"`, потім за потреби ` # коментар`. Заборонено: `$`, зворотні лапки, `\`, `;`, дужки, `&`, `|`, `export`, невідомі ключі, повторення ключа, керівні символи. `\r` (FAT/Windows) і BOM дозволені. Файл ніколи не виконується (`source`/`eval` немає).
Було (REPO, аудит): `gs-mavlink.sh` робив `. "$CONF"`, а `/config` доступний анонімно через Samba від root. Тепер ін'єкції `$(...)`, `` `...` ``, `; cmd`, `export`, `. файл` відхиляються (exit 2), маркер-файл не створюється: 13 пейлоадів у `tests/static/config.sh`.
Завантажувач `config/load.sh` **ніколи не береться з `/config`**: `gs-mavlink.sh` шукає його в `$SBC_GS_CONFIG_DIR`, `<скрипт>/../../config`, `<скрипт>/../config` і пропускає будь-який каталог, чий реальний шлях `/config` або `/config/*` (інакше `/gs/mavlink/../../config` дало б код з записуваного розділу).

## 4. SAFETY-ключі та жорсткі межі

Для ключів з `safety=y` поля `min`/`max` це **жорсткі межі**: профіль, файл, середовище й CLI не можуть їх перейти; перевищення = помилка. Лише `SBC_GS_I_KNOW=1` або прапорець `--i-know` (у `tx12_bridge.py`, `gs_mav.py`, `sbc-gs-config`) приймає значення поза межами, друкуючи в stderr `WARNING: SAFETY OVERRIDE ... YOU own the consequences`; помилку типу (`abc`) і межі не-SAFETY ключів `--i-know` не послаблює.

| Ключ | Типово | Жорсткі межі | Що захищає |
|---|---|---|---|
| `TX12_DEADMAN_MS` | 300 | 50..1000 | не можна «вимкнути» dead-man завеликим значенням |
| `TX12_MAX_RATE_HZ` | 50.0 | 1..100 | стеля частоти кадрів RC |
| `TX12_FAILSAFE_THROTTLE_US` | 1000 | 1000..1100 | газ у failsafe-кадрах |
| `TX12_THROTTLE_FS_FRAMES`, `TX12_EXIT_RELEASE_FRAMES` | 3, 5 | 1..20 | кадри failsafe/release (0 вимкнуло б їх; мутація t2 аудиту) |
| `TX12_RELEASE_HOLD_S` | 1.0 | 0.5..5 | тривалість release |
| `TX12_CLAMP_LO_US` / `_HI_US` | 1000 / 2000 | 900..1100 / 1900..2100 | діапазон стіків |
| `TX12_SANE_MIN_US` / `_MAX_US` | 0 / 4000 | 0..1000 / 2000..8000 | відсів абсурдних зразків |
| `GSMAV_RATE_HZ` | 20.0 | 0.1..50 | закриває зауваження аудиту «немає обмеження `--rate`» |
| `GSMAV_NEUTRAL_US`, `GSMAV_SWEEP_AMP_US` | 1500, 400 | 1400..1600, 0..500 | амплітуда sweep |
| `GSMAV_RELEASE_REPEATS` | 5 | 1..20 | release на виході |

Зміна проти старої поведінки (INF): `--failsafe-throttle` раніше приймав 1000..2000, тепер 1000..1100 (без `--i-know`); `--max-rate` вище 100 раніше відхилявся безумовно, тепер лише без `--i-know`. Типові значення не змінились.

## 5. Профілі (`config/profiles/`)
`gs-hardened` (`LISTEN_ADDR` і `UPSTREAM_BIND` = 127.0.0.1), `bench-tight` (суворіший міст), `contour-pi5` (стартові значення для контуру: AIR = OpenIPC WiFiLink2/RTL8812EU 720p, GS = Pi 5 + RTL8812AU, ELRS 900 МГц, FC Matek H743 SLIM, MAVLink з UART WiFiLink2 через wfb-ng; усе UNVERIFIED на залізі, регіон і канал навмисно не задано). Запуск: `SBC_GS_PROFILE=gs-hardened gs/mavlink/gs-mavlink.sh --print`.
Файли `config/contour/*.env` (інші агенти) мають власні ключі `AIR_*`/`GS_*` і цим завантажувачем **не** читаються.

## 6. `LISTEN_ADDR` (рекомендація, рішення за лідом)
Новий ключ `gs-mavlink`, типово `0.0.0.0` (стара поведінка, вивід `--print` не змінився). Він задає адресу GCS UDP/TCP-серверів (`udps:ADDR:порт`, `tcps:ADDR:порт`); `UPSTREAM_BIND` лишається окремим ключем. Рекомендація аудиту: `127.0.0.1`, якщо QGC/MAVProxy на самій GS, бо без автентифікації й signing будь-хто в hotspot (пароль `12345678`) отримує MAVLink-доступ. Вмикається профілем `gs-hardened`; змінювати типове значення чи ні вирішує лід.

## 7. Встановлення
`gs/install.sh` копіює `gs/lib` і `gs/boards`, але **не** `gs/mavlink` (служба ставиться вручну, `docs/GS-MAVLINK.md`), тому `config/` у `install.sh` **не додано**. Під час ручної установки `gs-mavlink` скопіюйте `config/` разом з `gs/mavlink/` так, щоб вийшло `/gs/config/{load.sh,registry.tsv,...}` і `/gs/mavlink/gs-mavlink.sh`. Коли `install.sh` почне ставити `gs/mavlink`, додайте туди `cp -r ../config ${install_dir}/` (зараз це лише рекомендація).

## 8. Храповик жорстких констант
`tests/static/config_scan.py` рахує за файлами числові літерали, порти, IPv4 й абсолютні шляхи поза реєстром (Python: `ast`; shell: регулярка без коментарів, ANSI-кодів і `$N`); 0, 1, 2 ігноруються, рядок із `cfg-ok` звільнений (протокольні константи: 8 каналів, 65535 = ignore, модуль токена RTT). Базовий рівень: розділ `== hardcode counts` у golden (зараз `total 374`); новий літерал у файлі більше за базу = FAIL, зменшення змінює golden (`tests/run.sh --update static/config`). `--list` показує кожну знахідку.

## 9. Свідомо НЕ винесено в реєстр
- Протокол MAVLink: 8 каналів, 65535 (ignore), 0 (release), msgid, `MAV_TYPE_*`, модуль токена RTT 60000 (парний на обох кінцях у `gs_mav.py`).
- Значення «підробленої» телеметрії `fake_fc.py` (батарея 15800 мВ, 87 %, GPS-координати, ефемерна геометрія; близько 28 літералів): це дані емулятора, не налаштування.
- `tests/sim/apm_model.py`: параметри моделі ArduPilot з тегами SRC/SITL; керуються через `apm_fc.py`/`APM_*`, самі значення моделі не змінюються.
- `tests/sim/sitl_probe.py` (58): очікування, виміряні на ArduCopter 4.7.1 SITL, порти 5760/5762 і сценарії; це еталон, а не налаштування.
- Тривалості та `sleep` усередині `tx12-bridge-test.sh` (113), `smoke.sh`: тайминги сценаріїв пов'язані між собою (наприклад, `sleep` після `--duration`); у реєстр винесено лише пороги й порти (`TXT_*`, `SMOKE_*`, `LB_*`).
- Шлях/імена бінарників роутера (`mavp2p`, `mavlink-routerd`), їх прапорці, межі перевірок у `gs-mavlink.sh` (65535, 254, 1200..3000000): 8 літералів, дублюють межі реєстру; джерело істини для повідомлень валідації лишається в скрипті, бо його golden не можна міняти (`--no-value-check`).
- `release.sh` (9): розміри/номери розділів збирача образу, поза темою цього механізму.
- `video_latency.py`: порти 15610+len/15620 у `smoke.sh` обчислюються арифметикою.
- Конфіг-шаблони, які `configure-wfb.sh` пише в `/etc/wifibroadcast.cfg` (`127.0.0.1`, `0.0.0.0`): це зміст файлу wfb-ng; керуються `GS_FORWARD_IP`, `MAV_PORT`, `AIR_VIDEO_PORT`.
- Рядки `conn` (`udpout:127.0.0.1:14550`) не виводяться з `MAV_PORT`: це окремі ключі (`TX12_CONN`, `GSMAV_CONN`, `FAKEFC_CONN`, `APM_CONN`); при зміні порту задайте і `MAV_PORT`, і потрібні `*_CONN`.

## 10. Відомі межі
- `SBC_GS_PROFILE` і `/config/sbc-gs.env` діють на всі інструменти разом: невалідний ключ у файлі зупиняє будь-який, що його читає (`exit 2`), навмисно.
- Значення `str` не валідуються далі за алфавіт файлового парсера (у середовищі можна задати довільний рядок).
- CI (`.github/workflows/ci.yml`, не мій файл): додайте `config/*.sh` до `shellcheck -x -S warning` і `config/*.py`, `tests/static/*.py` до `py_compile`.
