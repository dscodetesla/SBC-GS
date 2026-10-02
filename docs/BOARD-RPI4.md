# Профіль плати Raspberry Pi 4B (чернетка, M6 крок 1)

Статус: **ЧЕРНЕТКА.** Файл `gs/boards/rpi4/board.conf` ніким не підключений (як і `radxa-zero3` на старті M2): жоден скрипт `gs/` не читає його для Pi, образ не збирався, на залізі нічого не запускалось.
Ціль: Raspberry Pi 4 Model B (BCM2711), Raspberry Pi OS Bookworm 64-bit (ядро 6.12, гілка `rpi-6.12.y`).
Позначки: **SRC** прочитано з першоджерела (отримано `curl` через проксі цієї сесії), **REPO** `файл:рядок` у цьому репо, **INF** висновок, **HW** потребує заліза, **UNVERIFIED** значення не підтверджене (у профілі стоїть коментар `# UNVERIFIED (причина)`).
Перевірка: `tests/static/rpi4-draft.sh` (golden `tests/golden/static/rpi4-draft.out`), `gs/boards/validate.sh gs/boards/rpi4`.

## 1. Прочитані джерела

Скорочення: `docs` = `raspberrypi/documentation@master`, `documentation/asciidoc/computers/`; `linux` = `raspberrypi/linux@rpi-6.12.y`; `firmware` = `raspberrypi/firmware@master`; `pi-gen` = `RPi-Distro/pi-gen@master`. Усе через `raw.githubusercontent.com`.

| Скорочення | Файл (прочитано повністю або потрібні рядки) |
|---|---|
| docs | `config_txt/what_is_config_txt.adoc`, `config_txt/boot.adoc`, `config_txt/gpio.adoc`, `configuration/interfaces.adoc`, `configuration/configuring-networking.adoc`, `configuration/boot-behaviour.adoc`, `raspberry-pi/usb-bus-on-raspberry-pi.adoc`, `raspberry-pi/rtc.adoc` |
| linux | `arch/arm/boot/dts/broadcom/bcm2711-rpi-4-b.dts`, `bcm2711.dtsi`, `bcm2711-rpi.dtsi`, `bcm283x.dtsi`, `bcm283x-rpi-wifi-bt.dtsi`; `overlays/dwc2-overlay.dts`; `drivers/usb/dwc2/debugfs.c`, `drivers/usb/dwc3/debugfs.c`; `drivers/net/wireless/broadcom/brcm80211/brcmfmac/of.c`; `Documentation/ABI/testing/sysfs-class-pwm`; `Documentation/driver-api/thermal/sysfs-api.rst` |
| firmware | `boot/overlays/README` |
| pi-gen | `export-image/prerun.sh`, `README.md` |
| libgpiod | `brgl/libgpiod@v1.6.x`: `tools/gpiofind.c`, `lib/ctxless.c` |

Недоступно (не використано як джерело): `www.raspberrypi.com/documentation/*` (HTML, 403 через проксі), GitHub API (`api.github.com`, 403: «access not enabled»), `tracker.debian.org`/`packages.debian.org` не читались. Шляхи `.adoc` на `develop` дали 404, працює гілка `master`.

## 2. Таблиця ключів

Усі 18 ключів з `gs/boards/REQUIRED_KEYS` + необов'язкові. Колонка «Док.» дає позначку достовірності значення; цитати дослівні в коментарях `# src:` самого профілю.

| Ключ | Значення | Док. | Джерело (коротко) |
|---|---|---|---|
| `BOARD_ID` | `rpi4` | REPO | `gs/boards/REQUIRED_KEYS:2` |
| `GPIO_PIN_PREFIX` | `GPIO` | SRC + **UNVERIFIED** | `linux bcm2711-rpi-4-b.dts:91-93` імена ліній `"ID_SDA","ID_SCL","GPIO2","GPIO3"…`; `firmware boot/overlays/README`: «GPIO numbering uses the hardware pin numbering scheme (aka BCM scheme) and not the physical pin numbers»; `libgpiod gpiofind.c`/`ctxless.c`: пошук лише за ІМЕНЕМ лінії. Контракт Radxa інший, див. розд. 4.1 |
| `OTG_CONTROLLER` | `none` (сентинел) | SRC | `linux drivers/usb/dwc2/debugfs.c:783-785`: лише `params`, `hw_params`, `dr_mode` (0444); `dwc3/debugfs.c:1029`: `"mode"` 0644 є лише в dwc3 (Radxa); `dwc2` overlay: `dr_mode` статичний |
| `OTG_MODE_FILE` | `none` | SRC | те саме |
| `DTBO_DIR` | `/boot/firmware/overlays` | SRC + INF | `docs what_is_config_txt.adoc:3`: плата завантаження в `/boot/firmware/`; `firmware boot/overlays/README:4` «This directory contains Device Tree overlays»; назва `overlays/` на розділі виведена з розкладки репо `firmware` (INF) |
| `DTBO_SOC_PREFIX` | `none` (сентинел) | SRC | оверлеї Pi без префікса SoC (`dtoverlay=i2c-rtc`, `pwm`, `dwc2`); у Radxa `rk3568-`, REPO `gs-applyconf.sh:65,92` |
| `DTBO_DIR_LOWER` | `/boot/firmware/overlays` | **UNVERIFIED** | `pi-gen prerun.sh:92`: `/boot/firmware` це окреме FAT-монтування; шлях нижнього шару overlayroot невідомий |
| `WIFI_ONBOARD_IFACE` | `wifi0` | REPO + INF | `gs/98-rename.rules.in:2-4`: ті ж правила; `docs configuring-networking.adoc:112`: штатне ім'я `wlan0`. `wifi0` обрано для паритету зі скриптами (`gs.sh:92`, `button.sh:15`), рішення, не факт Pi |
| `WIFI_ONBOARD_DRIVER` | `brcmfmac` | SRC | `linux bcm283x-rpi-wifi-bt.dtsi:20-23` `compatible = "brcm,bcm4329-fmac"`; `brcmfmac/of.c:116` перевіряє цей compatible. `ID_NET_DRIVER` у udev на `add`: HW |
| `GADGET_IFNAME` | `rpi0` | **UNVERIFIED** | заглушка: на цьому профілі гаджета немає, ім'я вигадане мною |
| `HOME_DIR` | `/home/pi` | SRC + INF | `pi-gen README.md:175` `FIRST_USER_NAME` за замовчуванням `pi`; реального користувача задасть образ |
| `CONSOLE_TTY` | `/dev/serial0` | SRC + **UNVERIFIED** | `docs interfaces.adoc:304-306` (`/dev/serial0` — символічне посилання на первинний UART), `:272-274` (Pi 3/4: первинний = `UART1` = mini UART), `:356` (mini UART за замовчуванням вимкнений без `enable_uart=1`); `gs-init.sh` під `set -e`, `tee` у відсутній вузол впаде |
| `PWM_SYSFS_BASE` | `/sys/class/pwm/pwmchip` | SRC | `linux sysfs-class-pwm`: `/sys/class/pwm/pwmchip<N>/`; `firmware README`: `dtoverlay=pwm` (піни 12/13/18/19) |
| `PART_SEP` | `p` | SRC | `docs boot-behaviour.adoc:186` `root=/dev/mmcblk0p2` |
| `PART_OVERLAY_NUM` | `3` | **UNVERIFIED** | задум для спайку M6: MBR, `p1` boot, `p2` root, `p3` overlay, `p4` розширений |
| `PART_VIDEOS_NUM` | `5` | **UNVERIFIED** | перший логічний розділ у розширеному `p4`; `gs-init.sh` цього не вміє |
| `PART_VIDEOS_LABEL` | `videos` | REPO | `gs/gs-init.sh:36-37` |
| `ROOTFS_LABEL` | `rootfs` | SRC | `pi-gen prerun.sh:87` `mke2fs … -L rootfs`; `:76` `mkdosfs -n bootfs`; `:31` `mklabel msdos` (MBR) |
| `OTG_MASS_STORAGE_DEFAULT` / `_ALT` | `none` | SRC | як `OTG_CONTROLLER` |
| `THERMAL_CPU_TEMP_FILE` | `/sys/class/thermal/thermal_zone0/temp` | SRC (формат) + **UNVERIFIED** (індекс) | `linux sysfs-api.rst:284` `thermal_zone[0-*]`; `bcm2711.dtsi:73-74,635-638` `cpu_thermal`/`brcm,bcm2711-thermal`; індекс 0 з джерела не читався |
| `RTC_I2C_BUS` | `1` | SRC (overlay) + **UNVERIFIED** (номер) | `firmware README`: `dtoverlay=i2c-rtc,ds3231`, `dtparam=i2c_arm=on`; `bcm2711.dtsi:1132` `&i2c1`; `bcm2711-rpi.dtsi:13-19`: у `aliases` немає i2c, тож номер `/dev/i2c-N` динамічний |
| `BOOT_FIRMWARE_DIR`, `CONFIG_TXT`, `CMDLINE_TXT` | `/boot/firmware`, `…/config.txt`, `…/cmdline.txt` | SRC | `docs what_is_config_txt.adoc:3`, `boot.adoc:18`. Ключі лише для Pi, нічим не читаються |

### 2.1. Список UNVERIFIED (8, храповик)

Тест `static/rpi4-draft` друкує кожен маркер і падає, якщо їх стало більше за `UNVERIFIED_MAX=8`. Мета: лише зменшувати (знімати HW-перевіркою й знижувати `UNVERIFIED_MAX` разом із golden).

1. `GPIO_PIN_PREFIX`: розбіжність контракту (фізичний пін проти BCM), розд. 4.1.
2. `DTBO_DIR_LOWER`: поведінка overlayroot на Pi не перевірена.
3. `GADGET_IFNAME`: заглушка без гаджета.
4. `CONSOLE_TTY`: вузол існує лише з `enable_uart=1`; образ цього ще не задає.
5. `PART_OVERLAY_NUM`: задум, образу немає.
6. `PART_VIDEOS_NUM`: задум; `gs-init.sh` без логічних розділів MBR.
7. `THERMAL_CPU_TEMP_FILE`: індекс зони (перевірка: `cat /sys/class/thermal/thermal_zone*/type`).
8. `RTC_I2C_BUS`: номер шини (перевірка: `ls /dev/i2c-*`).

Не оформлено маркером (бо це не значення профілю), але не перевірено: ім'я `gpiochipN` для Pi 4 і версія `gpiod` у Bookworm (libgpiod v1 `gpiofind` чи v2), чи несе USB-C Pi 4B дані до dwc2 (HW), робота `ID_NET_DRIVER=brcmfmac` у правилі udev на `add`, чи є `thermal_zone0` єдиною зоною.

## 3. Відмінності Pi від Radxa (що не лягає на значення)

- **GPIO.** Radxa: `gpiofind PIN_<n>` з фізичним номером виводу (REPO `docs/PI-PORT.md:36`). Pi: імена ліній `GPIO<BCM>` (SRC dts); `gpiofind` шукає лише за іменем (`ctxless.c`). Тому `gpio_find 7` на Pi дав би `gpiofind GPIO7` = BCM 7 = вивід 26, а не вивід 7. Значення `GPIO` коректне лише для BCM-нумерації.
- **OTG.** dwc3 Radxa має `debugfs mode` (0644) для перемикання хост/пристрій на льоту; dwc2 Pi має лише читання `dr_mode` (0444), режим задає `dtoverlay=dwc2,dr_mode=…` при завантаженні. Профіль `none`.
- **Оверлеї.** На Pi файли `.dtbo` лежать у `/boot/firmware/overlays/` завжди, вмикаються рядком `dtoverlay=` у `config.txt`. Radxa вмикає перейменуванням `*.dtbo.disabled` (REPO `gs-applyconf.sh:65-108`). Модель інша, не лише шлях.
- **Розмітка.** pi-gen: MBR, `p1` FAT `bootfs`, `p2` ext4 `rootfs`. Radxa: GPT, `p3` root, `p4` overlay, `p5` videos; `gs-init.sh` викликає `sgdisk -ge` (лише GPT) і `parted resizepart 4`.
- **Wi-Fi.** Правило 1 у `98-rename.rules.in` перейменовує кожен `wl*` на `wlan0`, правило 2 онборд-чип на `wifi0`: на Pi онборд `brcmfmac` піде в `wifi0`, зовнішній RTL лишиться `wlan0` (INF, HW).

## 4. Пропозиції зміни контракту (PROPOSAL; нічого не «зігнуто» мовчки)

### 4.1. Нумерація GPIO (НЕ реалізовано)
Поточний контракт не виражає «ім'я лінії = префікс + фізичний номер» на Pi. Варіанти:
- нове необов'язкове `GPIO_PIN_NUMBERING='physical'|'bcm'` (типово `physical` = сьогоднішня поведінка Radxa), у `gs/lib/gpio.sh` для Pi таблиця «фізичний вивід → BCM» (40-контактний роз'єм фіксований), після чого `gpio_find 7` → `gpiofind GPIO4`;
- або `GPIO_PIN_MAP` у профілі. Обидва потребують тестів `static/gpio` і зміни golden лише за свідомим рішенням. До цього `GPIO_PIN_PREFIX='GPIO'` лишається UNVERIFIED.

### 4.2. Плата без OTG: сентинел `none` (РЕАЛІЗОВАНО, мало й зворотно сумісно)
`validate.sh` вважає порожнє значення обов'язкового ключа помилкою (`FAIL … empty key`, фікстура `empty-key`), а `board_get` відмовляє на порожньому, тож `gs/lib/otg.sh` тихо підставив би **літерали Radxa** (`fcc00000.dwc3`) на плату без OTG: це небезпечно. Рішення: документований сентинел `none` (непорожній, `validate.sh` його приймає без змін) і функція `otg_supported` у `gs/lib/otg.sh` (статус 0, якщо `OTG_CONTROLLER` ≠ `none`; без профілю/ключа повертає «підтримується», тобто поведінка Radxa не змінилась). Покрито `tests/static/rpi4-draft.sh` (rpi4 → `none`/неактивний; radxa-zero3, невідома плата, відсутня `board.sh` → активний; безпечно під `set -e` усередині `if`). Існуючі golden не змінювались (тести sentinel не додавались у `static/otg.sh` саме щоб `tests/golden/static/otg.out` лишився побайтно тим самим).
**Не зроблено:** виклики `otg_supported` у `button.sh` (`change_otg_mode`), `otg-gadget.sh`, `gs.sh:31`: окремий PR із golden пісочниці. Та сама схема потрібна для `DTBO_SOC_PREFIX` (зараз `none`-заглушка).

### 4.3. Механізм оверлеїв (НЕ реалізовано)
Додати `DTBO_MODE='rename'|'config-txt'` і не вимагати `DTBO_SOC_PREFIX` для `config-txt` (зробити необов'язковим або сентинел `none`). `gs-applyconf.sh` для `config-txt` має редагувати `dtoverlay=` у `CONFIG_TXT`.

### 4.4. Розмітка диска (НЕ реалізовано)
Додати `PART_TABLE='gpt'|'mbr'` і для `mbr` логіку розширеного/логічного розділу (потрібен для `PART_VIDEOS_NUM=5`) або альтернативу з `ROADMAP-EXECUTION.md` розд. 4: третій FAT-розділ `/config`/`/boot/firmware`.

### 4.5. Консоль
`CONSOLE_TTY` для плат без гарантованого вузла: `gs-init.sh` має писати лише якщо вузол існує, або профіль має задавати `enable_uart=1` у `config.txt` образу.

## 5. Що потрібно від заліза для спайку M6

Усе нижче HW, на Pi 4B з Bookworm 64-bit:
1. `ls /sys/class/thermal/` і `cat thermal_zone*/type` (знімає пункт 7); `ls /dev/i2c-*` із `dtparam=i2c_arm=on` (пункт 8).
2. `ls -l /dev/serial0` з/без `enable_uart=1` (пункт 4); чи видно консоль на GPIO14/15.
3. `gpioinfo | head` (`gpiochip0`, імена `GPIO2…`), `gpiofind GPIO17`, версія пакета `gpiod` (v1/v2); перевірити, що `gpiofind PIN_*` на Pi не знаходить нічого.
4. Чи є дані на USB-C Pi 4B: `dtoverlay=dwc2,dr_mode=peripheral` + `modprobe g_ether` з хостом на USB-C; `ls /sys/kernel/debug/usb/*/` (підтвердити відсутність `mode`).
5. `udevadm info /sys/class/net/wlan0 | grep ID_NET_DRIVER` на `add` (чи спрацює правило `brcmfmac` → `wifi0`); `ip link` з зовнішнім RTL.
6. Завантаження образу з MBR `p1`/`p2`/`p3`/`p4`(розширений)/`p5`(логічний), `blkid` (мітки `bootfs`, `rootfs`, `videos`), робота `gs-init.sh` на ньому та overlayroot (шлях `DTBO_DIR_LOWER`).
7. Яке ім'я користувача фактично створює збирач (`HOME_DIR`).
8. `dtoverlay=pwm,pin=12,func=4` і `ls /sys/class/pwm/` (номер `pwmchip` для `gs.conf fan_pwm_chip`).
