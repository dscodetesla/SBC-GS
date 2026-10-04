# Стан плану правок для Pi 3/4/5 (2026-10-03)

Перевірено на гілці `claude/hopeful-brown-k5mmbm` (`ffefcfa`, коміт «Pi 3/4/5 migration»). Посилання `файл:рядок` вказують на неї. Тести репозиторію `tests/run.sh` проходять: 228 із 228. Код читався, а не запускався на Pi. Тести підміняють `mount`, тому нові блокери з розділу 2 вони не бачать. Відкритого PR на цю гілку зараз немає.

## 1. Одинадцять пунктів плану

| # | Пункт | Стан | Де |
|---|---|---|---|
| 1 | Маршрут multicast на br0 | зроблено | `gs/gs.sh:93` більше не фатальний; br0 через nmcli `gs/gs-init.sh:71-73`, `gs/lib/net.sh` |
| 2 | Розділ запису на p4 | зроблено | `gs/gs.sh:59-64`: пошук за міткою `videos`, збій fsck/mount не зупиняє старт |
| 3 | Вбудований WiFi Pi у wfb | зроблено в коді | `gs/98-rename.rules.in:4` + `gs/install.sh:30` рендерить правила з профілю; що udev бачить `brcmfmac` у момент add, не перевірено на залозі |
| 4 | Перше завантаження | **частково** | розмітку і dtbo пропущено для MBR (`gs/gs-init.sh:33,58`), але `mount -o remount,rw /media/root-ro` (`:50`) лишився безумовним, див. розділ 2 |
| 5 | HDMI на card0 | зроблено | `gs/stream.sh:119-135`: будь-який `card*-HDMI-A-*`, необов'язковий `hdmi_wait_timeout` |
| 6 | 100 % CPU у `button.sh` | зроблено | `gs/button.sh:224-228`: кнопку без GPIO-лінії вимкнено |
| 7 | Порт 14550 двічі | **частково** | `gs/stream.sh:160` бере `osd_mavlink_port`, але типово 14550, ключа немає в `gs.conf`, а `gs-mavlink` не підключений у `gs.sh`/`install.sh` |
| 8 | `stream.sh` і мертвий плеєр | зроблено | `gs/stream.sh:172-176` + `Restart=on-failure` (`gs/gs.sh:119`) |
| 9 | txpower через udev | зроблено | `gs/99-GS.rules.in:4`: `systemd-run --no-block` |
| 10 | Процеси без перезапуску | **частково** | юніти `gs.sh` мають `Restart=` (`gs/gs.sh:9`), але `local_node` (`gs/wfb.sh:62`) і `wfb_rx` при hotplug (`gs/wfb.sh:123-124`) досі без нього |
| 11 | Останній `gpioset` | зроблено | `gs/gs.sh:147` через `gpio_find` |

## 2. Нові блокери, яких не було в плані

1. **`/media/root-ro` на Pi OS без overlayroot зупиняє весь старт.** `gs-applyconf.sh:138-141`: на Pi файлу `/media/root-ro/etc/fstab` немає, тому `grep` повертає помилку, `if !` заходить у блок, і `mount -o remount,rw /media/root-ro` падає під `set -e`. `gs.sh` підключає цей файл одразу на початку (`gs/gs.sh:15`), тож на чистій Pi OS не стартує нічого. Те саме в `gs/gs-init.sh:50`, тому `gs-init` не доходить до `systemctl disable` (`:206`) і запускається при кожному завантаженні. Висновок з коду; overlayroot для Pi в профілях позначений UNVERIFIED (`gs/boards/rpi4/board.conf:102`).
2. **Pi 3B+ отримує не той профіль і може стати незавантажуваним.** Профілю для Pi 3B+ немає (`gs/boards/`: лише `rpi4`, `rpi5`, `radxa-zero3`). `gs/lib/board.sh:35` за замовчуванням бере `radxa-zero3`, а там `PART_TABLE` не задано, і `hw_part_table` повертає `gpt` (`gs/lib/hw.sh:33`). У результаті `gs-init.sh:36` виконає `sgdisk -ge`, тобто перетворить MBR на GPT, а Pi 3B+ з GPT не завантажується. Висновок з коду. Поки профілю немає, на Pi 3B+ `install.sh` запускати не можна.

## 3. Що лишається для відео на Pi

Відео на Pi ще немає. `video_player='pixelpilot'` типово (`gs/gs.conf:28`), а гілка gstreamer жорстко використовує `mppvideodec` (`gs/stream.sh:101-102`), якого на Pi немає. Рішення R7 у `docs/DECISIONS.md` залишає це відкритим.

## 4. Що далі (пропонований порядок)

1. Прибрати два нові блокери: `/media/root-ro` лише коли overlayroot справді є (`gs-applyconf.sh:138-141,222`, `gs-init.sh:50`); безпечне значення за замовчуванням для невідомої плати, яке ніколи не переписує таблицю розділів.
2. Профіль `rpi3bp` (Pi 3B+): MBR, `brcmfmac`, без OTG, лише H.264.
3. Відео на Pi: декодер у профілі плати замість `mppvideodec` (Pi 4: `v4l2slh265dec`, Pi 3B+: `v4l2h264dec`, Pi 5: декод на хості за R4), типово `video_player='gstreamer'` на Pi.
4. MAVLink: підключити `gs-mavlink` у `install.sh`/`gs.sh`, віддати OSD окремий порт (`osd_mavlink_port`, напр. 14551 через `GCS_UDP_CLIENTS`), додати обидва ключі в `gs.conf`.
5. `Restart=` для `local_node` і hotplug-`wfb_rx` у `wfb.sh`.
6. Перший запуск на залізі, спершу Pi 4: `systemctl --failed` порожній, `journalctl -b -u gs` закінчується на `gs service start completed`, `ip link show br0`, `iw dev`, відео на екрані.
