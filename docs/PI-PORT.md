# SBC-GS → Raspberry Pi 3B+/4/5: архітектура та дорожня карта

Статус: **проєктний документ, код не змінювався.** Складено за результатами чотирьох дослідницьких проходів
(збірка, runtime-скрипти, стеки/меш, драйвери Realtek, готові репозиторії) станом на 2026-10-01.

## 0. Позначки достовірності

| Позначка | Значення |
|---|---|
| **REPO** | перевірено в цьому репозиторії (`file:line`) |
| **SRC** | прочитано з першоджерела (сторінка/коміт/файл) |
| **SNIP** | лише зі сніпета пошуку або підсумку малої моделі, не рішення на цьому ґрунті |
| **PROPOSAL** | наша пропозиція, не факт |
| **HW** | потребує перевірки на реальному залізі |

Принцип: у код потрапляє лише REPO/SRC. Усе інше проходить через HW-перевірку.

## 1. Уточнення задачі

1. **ArduPilot не замінює PixelPilot_rk.** PixelPilot_rk декодує відео; ArduPilot — прошивка польотного контролера.
   На наземній станції ArduPilot означає **шар MAVLink** (телеметрія, керування, маршрутизація). Відео лишається окремим модулем.
2. **«RT8012/8214»** прочитано як RTL8812AU/EU/BU/CU та RTL8814AU.
3. **openwifi** — FPGA-реалізація 802.11 на Xilinx Zynq (SRC: open-sdr/openwifi). На Pi не запускається, лише мережевий партнер.
4. Робота йде в гілці `claude/hopeful-brown-k5mmbm`, окрема від `main`.

## 2. Що не підтримується (REPO)

Усе привʼязано до Radxa Zero 3W. Жорсткі блокери:

| Область | Де | Суть |
|---|---|---|
| Відеоплеєр | `build/build.sh:162-175`, `gs/stream.sh:82-101` | PixelPilot_rk і `mppvideodec` — Rockchip MPP |
| Ядро/модулі | `build/build.sh:113-146` | гілка Radxa `linux-5.10-gen-rkr4.1`, `/boot/System.map` |
| Розмітка диска | `build/release.sh:86-196`, `gs/gs-init.sh:25-38` | GPT, `p3`=root, `p4`=overlay, `p5`=videos; Pi OS — MBR (`p1` boot, `p2` root) |
| Overlays | `gs/gs-applyconf.sh:63-108`, `gs/gs-init.sh:48-54` | перейменування `/boot/dtbo/*.dtbo`, `u-boot-update`; на Pi — `config.txt` |
| GPIO | `gs/gs.sh:127`, `gs/button.sh:51,193`, `gs/stream.sh:76` | `gpiofind PIN_<n>` (libgpiod v1, фізичні номери) |
| OTG | `gs/otg-gadget.sh:4`, `gs/button.sh:52` | `fcc00000.dwc3` debugfs |
| Користувач | `gs/gs.sh:57-71`, `build/build.sh:207-209` | `/home/radxa` |

Побічно: у `build/build.sh:16` опечатка `xface4`, тож блок очищення мертвий (не «виправляти», а видалити).

## 3. Шарова архітектура (PROPOSAL)

```
┌ Образ/збірка ──── pi-gen (+ власна stage) або поточний chroot-збирач для Radxa
├ Профіль плати ─── gs/boards/<id>/board.conf (єдине місце, де живе «яка це плата»)
├ Радіо/драйвери ── Realtek DKMS, закріплені за SHA, матриця по ядрах
├ Транспорт ─────── wfb-ng (standalone | aggregator | cluster), alink
├ MAVLink-шар ───── gs-mavlink (маршрутизатор) ↔ ArduPilot, QGC/MAVProxy
├ Відео ─────────── GStreamer: v4l2 + kmssink (Pi 4/5), MPP (Radxa)
└ Меш/LoRa ──────── необовʼязкові модулі (batman-adv, ELRS/serial-радіо)
```

### Стеки: що перший клас, що опційно

| Стек | Рішення | Підстава |
|---|---|---|
| wfb-ng | **перший клас** | Debian-пакет, активний до 2026-09-30 (SRC) |
| OpenIPC (alink) | **перший клас** | працює поверх wfb-ng (SRC: OpenIPC/adaptive-link) |
| ArduPilot/MAVLink | **перший клас** | wfb-ng несе MAVLink; роутера в репо немає (REPO) |
| batman-adv | опційний модуль | потребує окремого радіо (IBSS/802.11s), не працює поверх monitor-режиму |
| LoRa/ELRS | опційний модуль | через serial/UDP-роутер; Meshtastic — експериментальний |
| OpenWrt | лише зовнішній RX-партнер | режим `aggregator` (REPO: `gs.conf:84`) |
| OpenHD | поза обсягом | свій wifibroadcast; сумісність на ефірі не підтверджена; Pi 5 не підтримується (SNIP) |
| RubyFPV | **опційний, лише Radxa** | свій протокол; підтримка Pi 4/5 у README не підтверджена (SRC: відсутня) |
| openwifi | поза обсягом | потребує FPGA-платформу Zynq |

## 4. Драйвери Realtek (PROPOSAL, закріплення за SHA; HW — валідація)

Факти (SRC, підтверджено прямим читанням комітів):
- `svpcom/rtl8812au` `6e75916`: «Fix build on Raspberry Pi with kernel 6.18 headers» (змінює `Makefile` і `core/rtw_br_ext.c`). Гілку коміту сторінка не показала — тому **пін за SHA**.
- `libc0607/rtl88x2eu-20230815` `c066ed1`: умова змінена з `6.12.0` на `6.12.101` для сигнатури `cfg80211_rtw_set_monitor_channel`.
  Наслідок: звичайна умова `>= 6.13` ламається на Pi 6.12.x (бекпорт в LTS).
- `aircrack-ng/rtl8812au` позначений застарілим; не використовувати.

Матриця (**не перевірена на залізі**):

| Ядро | 8812AU | 8814AU | 88x2BU | 88x2CU | 88x2EU |
|---|---|---|---|---|---|
| 6.12 (Bookworm) | svpcom, SHA ≥ `20bcaf5` | morrownr/8814au | OpenHD/rtl88x2bu | libc0607 | libc0607 ≥ `c066ed1` |
| 6.18 (Trixie) | svpcom ≥ `6e75916` | morrownr/8814au (SNIP: діапазон до 6.18) | OpenHD | libc0607, коміт «6.18 compat» | libc0607 |

Обовʼязково: CI-матриця компіляції для 6.12.x і 6.18; чорний список `rtw88_*` там, де вжито вендорний драйвер.
Pi 5: перевірити ядро 16K сторінок (`kernel_2712`) проти `kernel8.img` (4K) — **HW, невідомо**.

**Пакет модифікацій драйвера** (SRC для svpcom/rtl8812au; тіло `rtw_monitor_xmit_entry` не читалось):
- експонувати `rtw_ldpc_cap`, `rtw_stbc_cap` як `module_param` (зараз статичні `int`, `os_intfs.c`);
- перевірити відображення radiotap TX-прапорців у дескриптор (`rtw_xmit.c: update_attrib_phy_info()`) — HW;
- перенести 5/10 МГц і `txpower fixed` з libc0607 (їх немає в дереві svpcom) — ліцензії GPL-2.0/GPL-3.0 перевірити по SPDX-заголовках файлів, бо змішування може не бути дозволене.

## 5. Відео (PROPOSAL + HW)

| Плата | HW-декодер | Примітка |
|---|---|---|
| Pi 3B+ | H.264 | немає HEVC |
| Pi 4 | HEVC (stateless `rpivid`, `VIDEO_RPI_HEVC_DEC`, SRC: `raspberrypi/linux` `rpi-6.12.y`, увімкнено за замовчуванням); H.264 (stateful) не підтверджено для поточної Pi OS | `h264_v4l2m2m` зависав на 6.6.63 (SNIP, linux#6554) |
| Pi 5 | лише HEVC | відкриті проблеми декодера на втратах UDP (SNIP, linux#7609/#7612) |

Конвеєр (власна пропозиція, джерела для OpenIPC на Pi немає): `udpsrc ! rtph265depay ! h265parse ! v4l2slh265dec ! kmssink`.
Вже існує gstreamer-гілка в `gs/stream.sh:100`; органічний крок — `GST_DECODER` у профілі плати. Затримка на Pi **не виміряна ніким** — вимірювати.

## 6. MAVLink-шар `gs-mavlink` (PROPOSAL)

Поточний стан (REPO): `wfb_rx … -u $wfb_outgoing_port_mavlink` (`gs/gs.sh:91`), `[gs_mavlink] peer = connect://…` (`gs/wfb.sh:87`). Маршрутизатора немає.

- Компонент: `mavp2p` (статичний бінарник, SNIP) або `mavlink-router` зі збіркою з джерел (SNIP: готовий бінарник вимагає glibc ≥ 2.42, Bookworm має 2.36).
- Вхід: wfb-ng UDP-пір; виходи: TCP для QGC/MAVProxy, список UDP-GCS замість multicast.
- Окремий `sysid` GCS (wfb-ng за замовчуванням ін’єктує `sysid 3`, SRC: `master.cfg`; можлива колізія з апаратом).
- Підпис MAVLink не обовʼязковий (канал wfb-ng уже шифрований), рішення відкладене.
- RTK: `GPS_RTCM_DATA` (SNIP); `gpsd`/`chrony` у репо покривають лише час (REPO).
- QGC на Pi: офіційний arm64 AppImage **не підтверджений** (SNIP, суперечливі дані). Практично: Pi пересилає UDP, GCS запущено на ноутбуці/телефоні.
- Відео в wfb-ng: peer-схеми лише UDP та unix-сокет, TCP/multicast немає (SRC: `services.py`); подробиці й джерела в `docs/CHAINS.md`.

## 7. Меш і LoRa (PROPOSAL, опційно)

- batman-adv у ядрі; `batctl` з apt (SNIP). Працює лише на IBSS/802.11s/managed інтерфейсі, тобто **другий радіомодуль**. 802.11s на Pi — не підтверджено.
- Багаторадійний wfb-ng: режими `aggregator`/`cluster` (REPO) та «distributed operation» (SRC: wfb-ng wiki).
- LoRa: ELRS — нативний MAVLink (SRC: expresslrs.org); Meshtastic (~1 кбіт/с, 238 байт на пакет, SNIP) придатний лише для сильно відфільтрованого MAVLink; Pi 5 RP1 — відкриті проблеми (SNIP).
- Сучасні шари модуляції wfb-ng (SRC: `tx.cpp`, `master.cfg`): FEC `-k/-n`, ширина `-B 20/40`, `-S` STBC, `-L` LDPC (лише 8812au), `-M` MCS, VHT. 5/10 МГц у wfb-ng **не задокументовано**.

## 8. Збірка образу (PROPOSAL)

Два шляхи, рішення за прототипом:
1. **Залишити chroot-збирач, додати шар плати.** Мінімум змін для Radxa; розмітку диска винести в модуль `gpt|mbr`.
2. **pi-gen** (BSD-3, активний 2026-09-29, SNIP/дата з API) з власною stage, CI через `usimd/pi-gen-action` (MIT).
   `rpi-image-gen` — молодший офіційний інструмент, окремий кандидат.

Reference (не форкати): `OpenHD/OpenHD-ImageBuilder` (хрут-шаблон), `OpenIPC/sbc-groundstations`.

## 9. Фази

| Фаза | Зміст | Критерій завершення |
|---|---|---|
| 0 | shellcheck, CI-перевірка збірки Radxa, видалити мертвий блок `build.sh:16-24` | збірка Radxa відтворюється |
| 1 | `board.conf` для Radxa; обгортки `gpio.sh` (v1/v2), `board_overlay_*`; прибрати `radxa` з коду | Radxa працює ідентично, у скриптах немає `radxa`, `rk35`, `fcc00000` |
| 2 | розділити збірку на `common` і `boards/<id>`; модуль розмітки `gpt|mbr`; пін драйверів за SHA; CI-матриця 6.12/6.18 | `BOARD=radxa-zero3` ідентичний, `BOARD=rpi4` збирається |
| 3 | образ Pi 4: wfb-ng + Realtek, GStreamer v4l2+kmssink, `gs-mavlink` | **HW:** відео та MAVLink від реального апарата |
| 4 | Pi 3B+ і Pi 5 як варіанти (Pi 5 — експериментальний) | HW-тест кожної плати |
| 5 | кнопки/OLED/INA226, вентилятор, overlayroot, firstboot, `/config` | HW |
| 6 | модифікації драйвера, меш-модуль, LoRa-модуль, вимірювання затримки | HW, повторювані виміри |

## 10. Відкриті ризики

1. Монітор/ін’єкція Realtek на Pi 5 і ядрі 16K — не перевірено (SNIP-повідомлення про збій морроунра, невідома причина).
2. Затримка відео на Pi — не виміряна.
3. Помилки HEVC-декодера на Pi 5 при втратах UDP (SNIP).
4. Сумісність OpenHD/Ruby з wfb-ng на ефірі — не підтверджена.
5. Ліцензійна сумісність при злитті патчів між форками драйверів (GPL-2.0 vs GPL-3.0).
6. overlayroot на Pi 5 (відомий обхід `initramfs8`, SNIP).

## 11. Рішення для власника проєкту

1. Базова ОС: Bookworm (ядро 6.12) чи Trixie (6.18). Рекомендація: Bookworm із зафіксованим ядром — драйвери стабільніші (PROPOSAL).
2. Образ: chroot-збирач із шаром плати чи pi-gen.
3. `/config`: окремий FAT-розділ чи `/boot/firmware`.
4. Чи потрібен Ruby на Pi, чи лишаємо його лише для Radxa.
