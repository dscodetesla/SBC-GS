# SBC-GS: що крутиться, як стартує і чому не зупиняється

Стан: гілка `main` (`bb27ba0`), посилання `файл:рядок` саме на неї. Де гілка PR #1 (`claude/hopeful-brown-k5mmbm`) вже щось змінила, це позначено **[PR#1]**. Нічого не запускалося; усе нижче прочитано з коду. Висновки, які не перевірені запуском, позначено «(висновок)».

## 1. Порядок старту

```
boot
 ├─ gs-init.service  (лише перше завантаження, oneshot)  → розмітка, конфіги, вимикає себе, reboot
 └─ gs.service       (oneshot + RemainAfterExit, After=gs-init) → /gs/gs.sh
        └─ запускає все інше фоном (&) або як тимчасові юніти systemd-run
udev (будь-коли)
 ├─ wl* add/remove   → /gs/wfb.sh <iface>      (99-GS.rules:3,8)
 ├─ клавіатура       → systemd-run button-kbd.py (99-GS.rules:13)
 └─ sda1             → button.sh mount_extdisk  (99-GS.rules:16)
```

- `gs/gs.service:5-7`, `gs/gs-init.service:5-7`: обидва `Type=oneshot`, `RemainAfterExit=true`. Самі вони не «крутяться»: живуть процеси, які `gs.sh` лишив після себе.
- `gs/gs-init.sh`: `sleep 12` (:8), розширює p4 і створює `videos` p5 (:25-38), компілює dtbo Rockchip (:50-54), пише br0/eth/usb0 для systemd-networkd (:57-110), `radxa0` + dnsmasq (:116-126), samba (:129-147), стартовий `wifibroadcast.cfg` (:150-172), вимикає себе (:183), чекає, доки зникне `/config/before.txt` (:188), і перезавантажує (:190).
- `gs/gs.sh` має `set -e` (:3) і йде лінійно: конфіг → `gs-applyconf.sh` (:11) → RTC/GPS/OTG/fan/ttyd → монтування запису (:43-47) → відео-гілка (:50-115) → OLED/WebUI → гасить червоний LED (:127).

## 2. Довгоживучі процеси

| Процес | Хто запускає | Чому живе / чому петля | Перезапуск |
|---|---|---|---|
| `wifibroadcast@gs` (wfb-ng, standalone) | `wfb.sh` → `systemctl restart` (`gs/wfb.sh:120`) | штатна служба wfb-ng | так, юніт wfb-ng |
| `local_node.service` (cluster) | `wfb.sh` через `systemd-run` (`gs/wfb.sh:62-72`) | `wfb_rx`/`wfb_tx` фоном + `wait` | ні |
| `wfb_rx -a` (aggregator) | `gs.sh:90-91` (голі `&`) | агрегує потоки від віддалених RX | ні |
| `stream` (`stream.sh`) | `systemd-run --unit=stream` (`gs/gs.sh:99`) | **петля читання FIFO** `/run/record_button.fifo` (`gs/stream.sh:154-198`): кнопка запису перемикає play/rec | ні |
| ├ `pixelpilot` або `gst-launch` | `stream.sh:124-131` | сам плеєр, PID у `pid_player` | ні |
| ├ `msposd` / `wfb-ng-osd -p 14550` | `stream.sh:134-148` | OSD; msposd чекає `/dev/shm/msposd` і `gs-wfb` (:138) | ні |
| └ мигання LED під час запису | `stream.sh:173-179` | `while true; gpioset ...` | ні |
| `button` (`button.sh`) | `systemd-run --unit=button` (`gs/gs.sh:103`) | по одній петлі `gpiomon` на кнопку (`gs/button.sh:194,216,234-242`), далі `wait` | ні |
| `fan` (`fan.sh`) | `systemd-run --unit=fan` (`gs/gs.sh:33`) | `while true` з `sleep $temperature_monitor_cycle` (`gs/fan.sh:26-66`) | ні |
| `alink` | `systemd-run --unit=alink` (`gs/gs.sh:106`) | адаптивний лінк OpenIPC | ні |
| `oled.py` | `systemd-run --unit=oled` (`gs/gs.sh:118-121`) | дисплей | ні |
| `webui` | `systemctl start webui` (`gs/gs.sh:124`), юніт з `install.sh:52-69` | Flask WebUI | `Restart=on-failure` |
| `rtsp@<codec>`, `ttyd`, `chrony`/`gpsd`, `serial-getty@ttyGS0` | `gs.sh:113,35-40,25`, `otg-gadget.sh:116` | системні служби | їхні юніти |
| `gs-mavlink` (mavp2p) **[PR#1]** | окремий юніт, вручну | `exec mavp2p` | `Restart=on-failure` |

Зв'язки між процесами: `button.sh` пише `single` у FIFO, `stream.sh` його читає (`button.sh:11,91`); усі пишуть повідомлення для OSD у `/run/pixelpilot.msg`; `button.sh toggle_stream` зупиняє/запускає юніт `stream` (`button.sh:95-101`); `wfb.sh` виходить, якщо `gs.service` ще не стартував (`wfb.sh:7`), тож udev до старту нічого не робить.

`otg-gadget.sh` не довгоживучий: це **перемикач**. Другий виклик видаляє gadget (`gs/otg-gadget.sh:16-36`).

## 3. Що крихке для порту на Pi

1. **Ланцюг `set -e` у `gs.sh`.** Будь-яка помилка до :99 зупиняє старт відео, кнопок, alink. Найімовірніший кандидат на Pi: `ip ro add 224.0.0.0/4 dev br0` (`gs/gs.sh:74`, [PR#1] :87). br0 створює systemd-networkd з `gs-init.sh:57-110`, а на Pi OS за замовчуванням працює NetworkManager (висновок). Без br0 команда падає, і нічого з відео не стартує.
2. **Перевірка HDMI жорстко на `card0`.** `stream.sh:119-123` чекає `card0-HDMI-A-1` вічно, без тайм-ауту. На Pi 4/5 card0 зазвичай v3d, а HDMI на `card1` (висновок, HW). Плеєр просто ніколи не стартує. **[PR#1] не виправлено** (`stream.sh:121`).
3. **`button.sh` може крутитися на 100 % CPU.** Якщо `gpiofind`/`gpio_find` не знаходить пін, `gpiomon` одразу падає, `button_action` повертає порожньо, і `while true` (`button.sh:216-229`) повторює це без паузи (висновок з коду). На Pi імена `PIN_<n>` не існують; [PR#1] додав `gpio_find`, але петля без затримки лишилась.
4. **Кінцевий `gpioset ... gpiofind PIN_` (`gs.sh:127`).** На Pi падає, `gs.service` стає failed, хоча все вже запущено. [PR#1] замінив на обгортку.
5. **Порт 14550 двічі.** У режимі `video_player=gstreamer` (саме Pi-шлях) `stream.sh:145` запускає `wfb-ng-osd -p 14550`, а `gs-mavlink` [PR#1] слухає `udps:0.0.0.0:14550`. Типова адреса виходу wfb `224.0.0.1` (multicast, `gs.conf:112`); чи отримають обидва сокети копії, залежить від `SO_REUSEADDR` у кожного (висновок, HW).
6. **`stream.sh` + `set -e` + `kill`.** Якщо плеєр уже впав, натискання запису робить `kill $pid_player` по мертвому PID (`stream.sh:163,165,186,188`), і весь `stream.sh` виходить. Юніти `systemd-run` без `Restart=`, тож відео не повертається до `toggle_stream` або перезавантаження.
7. **txpower через udev ненадійний.** `wfb.sh:130-134` робить `sleep 20 && set_txpower &`. При запуску з udev (`99-GS.rules:3`) udev вбиває фонові процеси після обробки події (так описано в `man udev`), тож потужність після гарячого підключення може не виставитись (висновок).
8. **Розділ `p4` у запасному монтуванні.** `gs.sh:44` монтує `...p4` як exfat, а `gs-init.sh:36-37,45` створює запис на **p5** (p4 це overlay). Спрацьовує лише коли fstab не змонтував `/Videos`. На Pi MBR (`p1`/`p2`) обидва номери неправильні. `otg-gadget.sh:41-42` теж віддає `mmcblk*p4` як mass storage.
9. **Перше завантаження прив'язане до Radxa.** `gs-init.sh`: `sgdisk`/GPT (:27), dtbo rk3566 (:50-54), `/media/root-ro` overlayroot (:41), очікування `before.txt` з rsetup (:188), `/dev/ttyFIQ0` (:10). На Pi `parted resizepart 4` по MBR, найімовірніше, зламає розмітку (висновок).
10. **Імена інтерфейсів.** `98-rename.rules:2` перейменовує **кожен** `wl*` у `wlan0` (для Ruby), `:7` gadget у `radxa0`. Друга USB-карта отримає конфлікт імен (висновок). [PR#1] має шаблон `98-rename.rules.in`, але `install.sh` ще копіює старі файли.
11. **Голі фонові процеси без нагляду.** `wfb_rx` в aggregator (`gs.sh:90-91`) і `local_node` не перезапускаються; якщо зникне USB-карта, вони лишаються мертвими до нової udev-події.

## 4. Що вже закрито в PR#1

GPIO через `gs/lib/gpio.sh`; OTG пропускається на платах без перемикача (`gs/lib/otg.sh`); `fan.sh` не стартує, якщо вентилятором керує ядро (Pi 5), і йде на 100 %, якщо температура не читається; RTC-шина і домашній каталог з профілю плати. Пункти 1, 2, 3 (пауза), 5, 6, 7, 8, 9 там не зачеплені.
