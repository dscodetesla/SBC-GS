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
| TCP-маршрут | у прочитаних документах не знайдено | не підтверджено |
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
| Pi 4 | `v4l2h264dec`; HEVC: `v4l2slh265dec` | `kmssink` | **суперечність джерел:** один сніпет каже, що HEVC на Pi 4 лише програмно (`avdec_h265`); перевірити на залізі |
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
| ELRS → FC | CRSF: `SERIALx_PROTOCOL=23`, `RSSI_TYPE=3` | SNIP (expresslrs.org заблоковано) |
| ELRS нативний MAVLink | `SERIALx_PROTOCOL=2`, baud 460, `RSSI_TYPE=5`; на форумах скарги на частоту оновлення; для RC брати CRSF | SNIP |
| TX12 MKII | варіанти ELRS 2.4 ГГц **або** мультипротокол (окремі SKU); BLE-джойстик на ELRS-версії; USB HID/SD/serial за стандартом EdgeTX | SNIP (сторінки продавців) |
| WiFi/Bluetooth-trainer на TX12 | не підтверджено | UNVERIFIED |
| Джойстик → FC | `MANUAL_CONTROL` або `RC_CHANNELS_OVERRIDE`, 20-50 Гц + heartbeat GCS 1 Гц | SNIP; прецедент: OpenIPC `rcjoystick` (SRC: sandbox-fpv), OpenHD |
| ELRS-backpack → GCS по MAVLink | шлях не підтверджено | UNVERIFIED |

### Незалежність каналів і відмови (SNIP, не з документації ArduPilot напряму)

- Канал ELRS (2.4 ГГц) і канал wfb-ng (WiFi у монітор-режимі) не ділять ні апаратуру, ні ефір, доки обидва не заходять в один UART/хост.
- Якщо керування йде через wfb-ng, а лінк впав: пакети override зникають; FC повертається на фізичний RC через `RC_OVERRIDE_TIME` (типово 3 с), а без фізичного RC спрацьовує RC-failsafe.
- `FS_GCS_ENABLE` працює лише якщо хост шле heartbeat.
- Відома проблема при поверненні з override на звичайний RC: [ArduPilot#32862](https://github.com/ArduPilot/ardupilot/issues/32862) (SNIP).

### БЕЗПЕКА

1. **Керування через відео/телеметричний лінк не може бути єдиним каналом.** Основним лишається ELRS з апаратним failsafe (`FS_THR_ENABLE`, RTL/Land).
2. `RC_OVERRIDE_TIME` не ставити в `-1`; рекомендація 1-2 с разом із `FS_GCS_ENABLE=1`.
3. Застарілий джойстик або завислий процес хоста продовжує слати застарілі значення: потрібні dead-man і обнулення при втраті.
4. Wfb-ng має буферизацію та FEC, тож затримка й джитер для керування не виміряні. Перші випробування без гвинтів.
5. Версії ELRS і можливості BT/WiFi TX12 звіряти з expresslrs.org та edgetx.org, коли вони стануть доступні.

### Мінімальна референсна схема (PROPOSAL)

- ELRS CRSF — основний канал керування, wfb-ng MAVLink — телеметрія, параметри, місії.
- На GS-хості `mavp2p` або `mavlink-router`: UDP 14550 на вході, TCP/UDP на виходах для QGC і телефону.
- TX12 по USB/BLE → `RC_CHANNELS_OVERRIDE` — лише необов'язковий резерв, окремою фазою після Фази 3 з `docs/PI-PORT.md`.

## 3. Що відкрито

1. HEVC-декодування на Pi 4: апаратне чи програмне (суперечність джерел).
2. TCP-маршрутизація відео в wfb-ng; чи потрібен jitterbuffer.
3. Шлях ELRS-backpack → GCS і TX12 WiFi/trainer.
4. Затримка й втрати керування через wfb-ng, жодних цифр немає.
