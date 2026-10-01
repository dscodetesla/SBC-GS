# Ланцюжки: відео та керування

Доповнює `docs/PI-PORT.md`. Позначки: **REPO** (перевірено в коді), **SRC** (прочитано з першоджерела),
**SNIP** (сніпет/підсумок, не підстава для рішень), **PROPOSAL**, **HW** (потрібна перевірка на залізі).
Станом на 2026-10-01; нічого не перевірено на реальному залізі.

## 1. Відео: камера → дрон → wfb-ng → GS → хост → екран

```
камера → majestic (SoC) → RTP H.264/H.265 → UDP 127.0.0.1:5602 (дрон)   [SRC: wfb-ng Setup-HOWTO]
   → wfb_tx (потік 0x00, FEC, ін'єкція 802.11) → RF
   → GS: wfb_rx -p <video_id> -c 127.0.0.1 -u 10000                     [REPO: gs/wfb.sh:64]
   → маршрут: UDP 127.0.0.1:5600 (той самий хост)                       [REPO: gs/stream.sh:100]
              або UDP wfb_outgoing_ip:wfb_outgoing_port_video            [REPO: gs/wfb.sh:77]
   → хост: udpsrc ! rtpjitterbuffer? ! rtp<h26x>depay ! parse ! декодер ! sink
```

| Етап | Деталі | Статус |
|---|---|---|
| Таблиця потоків | відео `0x00` (`udp_direct_rx`), MAVLink `0x10/0x90`, тунель `0x20/0xa0`, MSP `0x11/0x91` | REPO `gs/wfb.sh:104-107` |
| Формат | RTP по UDP, до 3993 байт, 1 RTP-пакет = 1 кадр 802.11 | SRC wfb-ng README |
| Caps | `application/x-rtp, media=video, clock-rate=90000, encoding-name=H264\|H265` | REPO `gs/stream.sh:100` |
| Схеми peer | лише `connect://IPv4:порт` (UDP) та `connect_unix://@ім'я` (unix datagram); `tcp://` і multicast-схеми немає, інші значення відкидаються (`unsupported peer address`) | SRC `svpcom/wfb-ng` `wfb_ng/services.py:36,40,115,179` (перечитано) |
| TCP | існує лише для MAVLink (`mavlink_tcp_port`) та дзеркала OSD (`osd_tcp`); для відео TCP/multicast потребує зовнішнього релея (`udp_proxy` або локальний форвардер) | SRC `master.cfg`, `services.py`; multicast через `connect://` як звичайний UDP — не перевірено |
| Jitterbuffer | у прикладах OpenIPC його немає; `latency=0..20` це загальна практика | не підтверджено |

### Готові рішення (оцінка)

| Рішення | Покриває | Платформи | Вердикт |
|---|---|---|---|
| wfb-ng `connect://` + gst-launch (шаблон `stream.sh`) | GS → хост → екран | будь-який gstreamer-хост | **adopt**, вже в репо |
| [PixelPilot (Android)](https://github.com/OpenIPC/PixelPilot) | увесь ланцюжок, приймає радіо сам (SNIP) | Android, Quest | adopt для телефону |
| [Aviateur](https://github.com/OpenIPC/aviateur) | прийом → FFmpeg → GPU-рендер | Linux, Windows, macOS | reference; транспорт у README не вказано |
| `wfb-ng-osd` | OSD поверх gstreamer | Pi, gstreamer+GL | reference, вже використовується (`stream.sh:145`) |
| fpv4win, QGC UDP-відео | не читано | | не підтверджено |

### Мінімальна реалізація (PROPOSAL, HW)

Хост-скрипт `host_rx.sh <h264|h265>`:
`udpsrc port=5600 caps="application/x-rtp,media=video,clock-rate=90000,encoding-name=H26x" ! rtpjitterbuffer latency=10 ! rtph26xdepay ! h26xparse ! <decoder> ! queue max-size-buffers=5 ! <sink> sync=false`.

| Плата | Декодер | Приймач | Примітка |
|---|---|---|---|
| Pi 3B+ | `v4l2h264dec` | `kmssink` | лише H.264 |
| Pi 4 | HEVC: `v4l2slh265dec` (апаратний декодер `rpivid`); H.264 на поточній Pi OS не підтверджено | `kmssink` | **суперечність вирішено для HEVC:** драйвер `VIDEO_RPI_HEVC_DEC` є в `raspberrypi/linux` гілка `rpi-6.12.y`, оверлей `rpivid-v4l2` видалений, бо драйвер увімкнений за замовчуванням (SRC). Зв'язка саме `v4l2slh265dec` + `rpivid` не перевірена: HW |
| Pi 5 | `v4l2slh265dec` | `kmssink` | H.264 лише програмно |
| довільний хост | `avdec_*` | `autovideosink` | програмний резерв |

Прийом: спершу виміряти затримку від скла до скла на кожній платі (H.264/H.265, з jitterbuffer і без).

## 2. Керування: TX12 MKII ↔ ELRS ↔ FC ↔ MAVLink ↔ wfb-ng ↔ GS ↔ хост

```
TX12 MKII (EdgeTX) ──ELRS 2.4 ГГц (RF)──► ELRS RX ──CRSF──► FC (ArduPilot)      ← ОСНОВНИЙ канал керування
FC (MAVLink2 UART) ◄──► wfb_tx/rx (0x10/0x90) ◄──RF──► GS ◄──UDP 14550──► маршрутизатор ◄──► QGC / MAVProxy / pymavlink
хост ◄──USB HID / BLE-джойстик──► TX12                                           ← необов'язковий експериментальний резерв
```

| Етап | Деталі | Статус |
|---|---|---|
| MAVLink через wfb-ng | потік `0x10` (rx) / `0x90` (tx), UDP 14550 | REPO `gs/wfb.sh:104-107`, `gs.conf` |
| ELRS → FC | CRSF: `SERIALx_PROTOCOL=23`, `RSSI_TYPE=3` | SNIP (окрема сторінка CRSF не читалась) |
| ELRS нативний MAVLink | `SERIALx_PROTOCOL=2`, `SERIALx_BAUD=460` (460800), `RSSI_TYPE=5`, усі `SRx_` = 1 крім `SRx_ADSB/PARAMS/RAW_CTRL` = 0; прошивка TX/RX `3.5.0`, Backpack `1.5.0`; примусове співвідношення телеметрії 1:2 і режим Hybrid/16ch/2 | SRC (агент, підсумок сторінки) [expresslrs.org/software/mavlink](https://www.expresslrs.org/software/mavlink/) |
| ELRS Backpack → GCS | MAVLink по WiFi: AP `ExpressLRS TX Backpack XXXXXX`, `10.0.0.1`, UDP `14550`, дальність 5-10 м; **не вмикати WiFi вручну** (це режим оновлення прошивки), а через Lua: Backpack → Telemetry → WiFi. Bluetooth для MAVLink не задокументований | SRC (агент) та сторінка вище |
| TX12 MKII | два варіанти внутрішнього RF: ELRS або CC2500 (FCC/LBT); BLE-джойстик лише на ELRS-версії; вбудованого WiFi у переліку немає; USB: заряджання та «USB Simulator» | SRC [radiomasterrc.com](https://www.radiomasterrc.com/products/tx12-mark-ii-radio-controller) |
| EdgeTX USB-джойстик | клас. режим: Ch1-8 → осі/диск/слайдер, Ch9-32 → кнопки; розширений: на кожен канал; мікшер 1000 Гц. Сторінка стосується кольорових радіо, для TX12 MKII не підтверджено | SRC (агент) [manual.edgetx.org](https://manual.edgetx.org/color-radios/model-settings/model-setup/usb-joystick.md) |
| Джойстик → FC | `MANUAL_CONTROL` (осі −1000…1000) або `RC_CHANNELS_OVERRIDE` (0 звільняє канал, 65535 ігнорує поле); прецедент: OpenIPC `rcjoystick`, OpenHD | SRC mavlink.io, ardupilot.org/dev `mavlink-rcinput` |

### Незалежність каналів і відмови (SRC з ardupilot.org; цитати перечитано)

- Канал ELRS (2.4 ГГц) і канал wfb-ng (WiFi у монітор-режимі) не ділять ні апаратуру, ні ефір, доки обидва не заходять в один UART/хост (висновок з архітектури).
- `RC_OVERRIDE_TIME`: «Timeout in seconds after which RC overrides will no longer be used, and regular RC input will resume. Default is 3 seconds.» ([joystick](https://ardupilot.org/copter/docs/common-joystick.html)). Значення `0` і `-1` не підтверджені.
- Радіо-failsafe: спрацьовує після `RC_FS_TIMEOUT` (типово 1 с); «RC_OVERRIDES are lost if using a GCS only is being used» ([radio-failsafe](https://ardupilot.org/copter/docs/radio-failsafe.html)). Для лагу телеметрії документація радить збільшити `RC_FS_TIMEOUT` ([mavlink-rcinput](https://ardupilot.org/dev/docs/mavlink-rcinput.html)).
- GCS-failsafe: за heartbeat, `FS_GCS_TIMEOUT` типово 5 с; після відновлення зв'язку апарат лишається у failsafe і не повертається в попередній режим ([gcs-failsafe](https://ardupilot.org/copter/docs/gcs-failsafe.html)). Типове значення `FS_GCS_ENABLE` не підтверджене.
- Документація джойстика: «you should keep a regular transmitter/receiver connected and ready for use as a backup».
- **Відкрита помилка безпеки** [ArduPilot#32862](https://github.com/ArduPilot/ardupilot/issues/32862) (2026-04-21, мітки BUG, Safety): коли override закінчується після втрати GCS, автопілот може підхопити застарілі значення справжнього RC; приклад: може змінитися режим польоту.

### БЕЗПЕКА

1. **Керування через відео/телеметричний лінк не може бути єдиним каналом.** Основним лишається ELRS з апаратним failsafe (`FS_THR_ENABLE`, RTL/Land).
2. `RC_OVERRIDE_TIME` не ставити в `-1`; рекомендація 1-2 с разом із `FS_GCS_ENABLE=1`.
3. Застарілий джойстик або завислий процес хоста продовжує слати застарілі значення: потрібні dead-man і обнулення при втраті.
4. Wfb-ng має буферизацію та FEC, тож затримка й джитер для керування не виміряні. Перші випробування без гвинтів.
5. Резервний RC має бути ввімкнений і готовий (ArduPilot: joystick-сторінка). Пам'ятати про #32862 при будь-яких частково-канальних override.

### Мінімальна референсна схема (PROPOSAL)

- ELRS CRSF — основний канал керування, wfb-ng MAVLink — телеметрія, параметри, місії.
- На GS-хості `mavp2p` або `mavlink-router`: UDP 14550 на вході, TCP/UDP на виходах для QGC і телефону.
- TX12 по USB/BLE → `RC_CHANNELS_OVERRIDE` — лише необов'язковий резерв, окремою фазою після Фази 3 з `docs/PI-PORT.md`.

## 3. Що відкрито

1. Зв'язка `v4l2slh265dec` + `rpivid` на Pi 4/5 і H.264 на поточній Pi OS (форум і docs raspberrypi.com недоступні, 403).
2. Чи потрібен jitterbuffer; multicast через `connect://`.
3. TX12 MKII: Bluetooth-trainer, WiFi, застосовність сторінки EdgeTX USB-джойстика до B/W-радіо.
4. `RC_OVERRIDE_TIME` = 0/-1, типове `FS_GCS_ENABLE`, документація підпису MAVLink2.
5. Затримка й втрати керування через wfb-ng, жодних цифр немає.
6. Конфлікт портів: UDP `14550` використовують і wfb-ng MAVLink на GS (REPO), і Backpack ELRS як AP (`10.0.0.1`); на одному хості розвести порти.
