# Профіль плати Raspberry Pi 5 (чернетка, база ОС Bookworm)

Статус: **ЧЕРНЕТКА.** `gs/boards/rpi5/board.conf` і `pinmap.conf` ніким не підключені (як `rpi4`): жоден скрипт `gs/` не читає їх для Pi, образ не збирався, на залізі нічого не запускалось. Станом на 2026-10-03.
Ціль: Raspberry Pi 5 (BCM2712 + південний міст RP1), 4 ГБ, Raspberry Pi OS **Bookworm** 64-bit, ядро 6.12.x, смак `rpi-2712`, сторінки 16 КіБ. Роль у контурі: радіошлюз wfb-ng + маршрутизатор MAVLink; відео базово декодує хост (`docs/CONTOUR-GS.md` п. 0, `docs/DECISIONS.md` R4).
Позначки: **SRC** прочитано з першоджерела (отримано `curl` з `raw.githubusercontent.com` 2026-10-03), **REPO** `файл:рядок` у цьому репо, **INF** висновок, **HW** потребує заліза, **UNVERIFIED** не підтверджено (у профілі `# UNVERIFIED (причина)`), **UNMEASURED** виміру немає.
Перевірка: `gs/boards/validate.sh` (усі плати), `tests/static/rpi5-draft.sh` (golden `tests/golden/static/rpi5-draft.out`, храповик UNVERIFIED).

## 1. Рішення власника: Pi 5 = Bookworm 6.12 (R4)

- Рішення: **USER** 2026-10-03; запис і умова перегляду в `docs/DECISIONS.md` R4, розділ у `docs/PI-PORT.md` §12.
- Версії в архівах (SRC, `Packages` прочитано 2026-10-03): `archive.raspberrypi.com/debian` `bookworm/main/binary-arm64`: `linux-image-rpi-2712` і `linux-headers-rpi-2712` `1:6.12.109-1+rpt1`, `linux-image-rpi-v8` (Pi 4, 4K) тієї самої версії. Дрібніші версії змінюватимуться після `apt upgrade`; тестовий пін заголовків у `tests/sim/dkms/manifest.txt` (6.12.109).
- Що з цього випливає (SRC): GStreamer на Bookworm 1.22.0 (див. п. 6, формат COL128), `gpiod 1.6.3` (є `gpiofind`, libgpiod v1: код `gs/` лишається як є), Python 3.11.2, DKMS 3.0.10. На Trixie (ядро 6.18.50, GStreamer 1.26.2, `gpiod 2.2.1`, API v2, потрібна обгортка v1/v2) усе інакше: `docs/CONTOUR-GS.md` п. 1.8.

## 2. Прочитані джерела

Скорочення: `linux` = `raspberrypi/linux@rpi-6.12.y`, `arch/arm64/boot/dts/broadcom/`; `docs` = `raspberrypi/documentation@master`, `documentation/asciidoc/computers/`; `firmware` = `raspberrypi/firmware@master`; `pi-gen` = `RPi-Distro/pi-gen@bookworm` (гілка, що збирає образи Bookworm; на `master` рядки зсунуті); `gpiozero` = `gpiozero/gpiozero@master`.

| Скорочення | Файл |
|---|---|
| linux | `bcm2712-rpi-5-b.dts`, `bcm2712-rpi.dtsi`, `bcm2712-ds.dtsi`, `rp1.dtsi`; `arch/arm64/configs/bcm2712_defconfig`; `drivers/pinctrl/pinctrl-rp1.c`; `drivers/gpio/gpiolib.c`; `drivers/usb/dwc3/debugfs.c`, `drivers/usb/dwc2/debugfs.c`; `drivers/i2c/i2c-core-base.c`; `drivers/net/wireless/broadcom/brcm80211/brcmfmac/of.c`; `drivers/media/platform/raspberrypi/hevc_dec/{Kconfig,hevc_d.c,hevc_d_video.c}`; `arch/arm/boot/dts/overlays/dwc2-overlay.dts`; `Documentation/ABI/testing/sysfs-class-pwm`, `Documentation/driver-api/thermal/sysfs-api.rst` |
| docs | `config_txt/what_is_config_txt.adoc`, `config_txt/boot.adoc`, `raspberry-pi/gpio-on-raspberry-pi.adoc`, `raspberry-pi/usb-bus-on-raspberry-pi.adoc`, `raspberry-pi/power-supplies.adoc`, `raspberry-pi/frequency-management.adoc`, `raspberry-pi/pcie.adoc`, `configuration/interfaces.adoc`, `configuration/boot-behaviour.adoc` |
| firmware | `boot/overlays/README` |
| pi-gen | `export-image/prerun.sh`, `README.md` |
| gpiozero | `gpiozero/pins/data.py` |
| GStreamer | `GStreamer/gstreamer@{1.22.0,1.22.12,1.26.2,1.28.2}` `subprojects/gst-plugins-bad/sys/v4l2codecs/gstv4l2format.c` |
| архіви | `archive.raspberrypi.com/debian/dists/bookworm/main/binary-arm64/Packages.gz`; `deb.debian.org/debian/dists/bookworm/main/binary-arm64/Packages.xz`; `apt.wfb-ng.org/dists/bookworm/master/binary-arm64/Packages.gz` |

Недоступно: `www.raspberrypi.com/documentation/*` (HTML, 403 через проксі), `github.com`/`api.github.com` (403), сторінка про декодер Pi 5 на raspberrypi.com не читалась. Шляхи `.adoc` `computers/raspberry-pi/{raspberry-pi-5,rp1,hardware,processors}.adoc` дали 404.

## 3. Таблиця ключів

Усі 18 ключів `gs/boards/REQUIRED_KEYS` + необов'язкові. Цитати дослівні в коментарях `# src:` самого профілю.

| Ключ | Значення | Док. | Джерело (коротко) |
|---|---|---|---|
| `BOARD_ID` | `rpi5` | REPO | `gs/boards/REQUIRED_KEYS:2` |
| `GPIO_PIN_PREFIX` | `GPIO` | SRC + **UNVERIFIED** | `linux bcm2712-rpi-5-b.dts:633-662`: чип RP1, імена ліній `ID_SDA, ID_SCL, GPIO2 … GPIO27`; `gpiozero data.py:530` `PI5_J8 = PI4_J8 # This is slightly wrong…`; `docs gpio-on-raspberry-pi.adoc:4` |
| `GPIO_PIN_NUMBERING` | `physical` | REPO | `gs/lib/gpio.sh`; `docs …gpio-on-raspberry-pi.adoc:14` (піни 27/28 = GPIO0/1) |
| `GPIO_PIN_MAP` | `pinmap.conf` | SRC | `gpiozero data.py:499-530`; піни 27/28 у таблиці це мітки `ID_SDA`/`ID_SCL` (п. 4.1) |
| `GPIO_CHIP_LABEL` | `pinctrl-rp1` (новий необов'язковий, нічим не читається) | SRC | `pinctrl-rp1.c:39,790` `.label = MODULE_NAME`; `:40` 54 лінії |
| `OTG_CONTROLLER`, `OTG_MODE_FILE` | `none` | SRC + INF | `rp1.dtsi:1118-1138` обидва dwc3 `dr_mode = "host"`; `dwc3/debugfs.c:450-451` запис у `mode` ігнорується не в режимі otg; `dwc2/debugfs.c:783-785` лише читання; `bcm2712-ds.dtsi:359-362` dwc2 `usb@480000` `status = "disabled"` (п. 4.2) |
| `DTBO_DIR` | `/boot/firmware/overlays` | SRC + INF | `docs what_is_config_txt.adoc:3`; назва `overlays/` з розкладки репо `firmware` (INF) |
| `DTBO_MODE` | `config-txt` | SRC | `firmware boot/overlays/README` `Load: dtoverlay=…` |
| `DTBO_SOC_PREFIX` | `none` (сентинел) | SRC | оверлеї Pi без префікса SoC |
| `DTBO_DIR_LOWER` | **не визначено свідомо** | — | необов'язковий ключ, нічого його не читає (`gs-init.sh:50` має літерал Radxa); значення залежить від образу з overlayroot, якого немає (п. 7, п. 10). Тест перевіряє, що ключа немає |
| `WIFI_ONBOARD_IFACE` | `wifi0` | REPO + INF | `gs/98-rename.rules.in:2-4`; рішення для паритету зі скриптами, не факт Pi |
| `WIFI_ONBOARD_DRIVER` | `brcmfmac` | SRC | `bcm2712-rpi-5-b.dts:424-440` `&sdio2 { … wifi@1 { compatible = "brcm,bcm4329-fmac"`; `brcmfmac/of.c:116`; `bcm2712_defconfig:635` `CONFIG_BRCMFMAC=m`. `ID_NET_DRIVER` у udev на `add`: HW |
| `GADGET_IFNAME` | `rpi0` | REPO (вибір, не факт) | те саме ім'я, що в `rpi4`; гаджета на профілі немає, правило `KERNELS=="gadget"` не спрацьовує. **Не маркер**, див. п. 5 |
| `HOME_DIR` | `/home/pi` | SRC + INF | `pi-gen README.md:170` `FIRST_USER_NAME (Default: pi)`; реального користувача задасть збирач |
| `CONSOLE_TTY` | `/dev/ttyAMA10` | SRC | `docs interfaces.adoc:276-277,300-306`: на Pi 5 основний UART це `UART10`, `/dev/ttyAMA10` «Debug UART on Raspberry Pi 5»; `bcm2712-rpi-5-b.dts:187-189` `&uart10 { status = "okay"; }`; `bcm2712-rpi.dtsi:124,149` `console = &uart10`. Відмінність від `rpi4` (`/dev/serial0`, потребує `enable_uart=1`): тут вузол налагоджувального роз'єму |
| `PWM_SYSFS_BASE` | `/sys/class/pwm/pwmchip` | SRC | `sysfs-class-pwm:10`; `bcm2712_defconfig:1599` `CONFIG_PWM_RP1=y`; `rp1.dtsi:397-415`. Офіційний вентилятор не вільний канал, п. 4.3 |
| `PART_SEP` | `p` | SRC | `pi-gen prerun.sh:49-50`; `docs boot-behaviour.adoc:186` |
| `PART_TABLE` | `mbr` | SRC | `pi-gen prerun.sh:31` `mklabel msdos` |
| `PART_OVERLAY_NUM` | `3` | **UNVERIFIED** | задум M6: MBR `p1` boot, `p2` root, `p3` overlay, `p4` розширений |
| `PART_VIDEOS_NUM` | `5` | **UNVERIFIED** | перший логічний розділ у `p4`; `gs-init.sh` цього не вміє |
| `PART_VIDEOS_LABEL` | `videos` | REPO | `gs/gs-init.sh:36-37` |
| `ROOTFS_LABEL` | `rootfs` | SRC | `pi-gen prerun.sh:66` `mkfs.ext4 -L rootfs`, `:65` `mkdosfs -n bootfs` |
| `OTG_MASS_STORAGE_DEFAULT/_ALT` | `none` | SRC | як `OTG_CONTROLLER`; без цього `otg.sh` підставив би `/dev/mmcblk0p4` Radxa |
| `THERMAL_CPU_TEMP_FILE` | `/sys/class/thermal/thermal_zone0/temp` | SRC (формат) + **UNVERIFIED** (індекс) | `bcm2712-ds.dtsi:48-53,275-277` одна зона `cpu-thermal` з `brcm,bcm2711-thermal`; `sysfs-api.rst:284`; індекс 0 з джерела не читався |
| `RTC_I2C_BUS` | `1` | SRC | `bcm2712-rpi.dtsi:138` `i2c1 = &i2c1`, `:379` `i2c1: &rp1_i2c1`, `:427-428` `i2c_arm: &i2c1` на GPIO2/3 (`rp1.dtsi:713-715`); `i2c-core-base.c:1664-1667` номер шини береться з псевдоніма: `/dev/i2c-1`. Увімкнення: `dtparam=i2c_arm=on` (`bcm2712-rpi.dtsi:207-208`), `dtoverlay=i2c-rtc,ds3231` (`firmware README`). Запуск на залізі: п. 7 |
| `BOOT_FIRMWARE_DIR`, `CONFIG_TXT`, `CMDLINE_TXT` | `/boot/firmware`, `…/config.txt`, `…/cmdline.txt` | SRC | `docs what_is_config_txt.adoc:3`, `boot.adoc:18` |

### 3.1. Список UNVERIFIED (4, храповик)

Тест `static/rpi5-draft` друкує кожен маркер, перевіряє, що кожен ключ із маркером згаданий у цьому документі, і падає, якщо маркерів стало більше за `UNVERIFIED_MAX=4`. Мета: лише зменшувати (знімати перевіркою на залізі й знижувати `UNVERIFIED_MAX` разом із golden).

1. `GPIO_PIN_PREFIX`: таблиця «фізичний вивід → GPIO» стороння (gpiozero, із застереженням автора для Pi 5); імена ліній прочитано з dts, не з `gpioinfo` реальної Pi 5. Знімається п. 7.2.
2. `PART_OVERLAY_NUM`: задум, образу немає.
3. `PART_VIDEOS_NUM`: задум; `gs-init.sh` без логічних розділів MBR.
4. `THERMAL_CPU_TEMP_FILE`: індекс зони (`cat /sys/class/thermal/thermal_zone*/type`).

Порівняння з `rpi4` (8 маркерів): знято `CONSOLE_TTY` (є вузол налагоджувального UART у dts і документація), `RTC_I2C_BUS` (номер шини фіксує псевдонім, п. 3), `DTBO_DIR_LOWER` (ключ не визначено), `GADGET_IFNAME` (вибір замість факту, п. 5).

### 3.2. Не маркер, але не перевірено

- Номер `gpiochipN` RP1 (п. 4.1): не значення профілю (`gpiofind` сам друкує чип), знімається `gpiodetect`.
- Чи доходить SoC-dwc2 до роз'єму USB-C на Pi 5 (HW; профіль це не змінює).
- `ID_NET_DRIVER=brcmfmac` на `add` у правилі udev, ім'я `wifi0` онборд-чипа (HW).
- Яке ім'я користувача створює образ (`HOME_DIR`).
- Конфігураційна опція `usb_max_current_enable=1` у `config.txt`: у прочитаній документації є лише читання `vcgencmd get_config usb_max_current_enable` і поле `/proc/device-tree/chosen/power/usb_max_current_enable` (`power-supplies.adoc:124,175-180`); запис опції в `config.txt` документація (прочитані файли) не описує: **UNVERIFIED**, пункт 7.3.

## 4. Знахідки

### 4.1. GPIO через RP1

- **Лінії (SRC):** 40-пінова шина сидить на чипі RP1 (`rp1_gpio: gpio@d0000`, `compatible = "raspberrypi,rp1-gpio"`, `rp1.dtsi:482-486`), 54 лінії (`pinctrl-rp1.c:40`), мітка чипа `pinctrl-rp1` (`:39,790`). Імена: `ID_SDA` (GPIO0), `ID_SCL` (GPIO1), `GPIO2 … GPIO27`, далі службові (`FAN_PWM` GPIO45 тощо), `bcm2712-rpi-5-b.dts:633-690`. Тобто той самий контракт імен, що на Pi 4: `gpiofind GPIO<n>`, а не `PIN_<n>` Radxa. Інші чипи Pi 5 (`gio`, `gio_aon`) мають імена на кшталт `2712_BOOT_CS_N`, `RP1_SDA` (`:556-630`): колізій з `GPIO<n>` немає (INF із прочитаних списків).
- **Номер чипа: СУПЕРЕЧНІСТЬ, HW.** Власник/INF у завданні: `gpiochip4` (`pinctrl-rp1`); `docs/CONTOUR-GS.md` п. 1.6: на ядрах 6.6+ очікується `gpiochip0`. Що прочитано: `bcm2712-rpi.dtsi:134-135` `gpiochip0 = &gpio; gpiochip10 = &gio;` (SRC), `gpiolib.c:994-1001` вибирає номер за псевдонімом `of_alias_get_id(gdev->dev.of_node, "gpiochip")` (SRC). Але в тому ж файлі `dev.of_node` встановлюється лише `:1030` `device_set_node(...)`, тобто після виклику на `:999`; `of_alias_get_id` (`drivers/of/base.c:1852-1870`) шукає збіг за `np`, тож за самим читанням коду псевдонім міг би не спрацьовувати (INF, мій висновок із читання, **не перевірено**; польові повідомлення, що RP1 став `gpiochip0` на ядрах 6.6.4x+, мені не доступні як джерело). Рішення профілю: **номер у коді не покладати**. `gpiofind` друкує `gpiochipN <лінія>`, а `gpiodetect | grep pinctrl-rp1` дає чип за міткою (ключ `GPIO_CHIP_LABEL`). Перевірка: п. 7.2.
- **Піни 27/28 (знахідка віртуального шару, `docs/SIM-VIRT-DEVICES.md` п. 4 №6):** на `rpi4` `pinmap.conf` має `27 0`, `28 1`, тож `gpio_find 27` дає `gpiofind GPIO0`, а лінія 0 зветься `ID_SDA`: не знайдеться. На Pi 5 те саме (`bcm2712-rpi-5-b.dts:635-636`). У `rpi5/pinmap.conf` ці виводи записані мітками `ID_SDA`/`ID_SCL`: `gpio_find 27` повертає 1 з повідомленням `gpio.sh: physical pin 27 is ID_SDA, not a GPIO line` і не викликає `gpiofind` (без змін у `gs/lib/gpio.sh`: нечислові значення вже відхиляються `_gpio_map_lookup`). Тест фіксує обидва виклики. **Пропозиція для `rpi4` (не моя територія):** замінити рядки `27 0`, `28 1` у `gs/boards/rpi4/pinmap.conf` на `27 ID_SDA`, `28 ID_SCL`; це змінить `tests/static/contract-ext.sh` (рядки про pin 27/28 і підрахунок «GPIO rows 28», «BCM 0..27») і golden `contract-ext.out`, `tests/sim/virt/guest/t_gpio.sh`.
- Таблиця «вивід → GPIO» для Pi 5: офіційні `.adoc` дають її лише картинкою; у `gpiozero` `PI5_J8 = PI4_J8` із коментарем автора «slightly wrong» (стосується альтернативних функцій, `data.py:389` TODO: INF). Лишається в маркері 1.

### 4.2. OTG, USB-C і кнопка (`OTG_MODE_FILE='none'`)

- **Профіль:** обидва контролери RP1 (чотири порти USB-A) мають `dr_mode = "host"` (`rp1.dtsi:1121,1138`), `dwc3 debugfs.c:450-451` ігнорує запис у `mode`, якщо `dr_mode != otg`; SoC-dwc2 (`bcm2712-ds.dtsi:359-362`) вимкнений і вмикається лише `dtoverlay=dwc2` (`dwc2-overlay.dts`: `target = <&usb>`, статичний `dr_mode`), без debugfs `mode` (`dwc2/debugfs.c:783-785`). Отже перемикання хост/пристрій на льоту, як у Radxa (`fcc00000.dwc3/mode`), на Pi 5 немає; профіль `none`. Чи виведений SoC-dwc2 на USB-C Pi 5: HW (`dts` цього не каже, INF: у dts немає вузла, що вмикає його за замовчуванням).
- **Знахідка віртуального шару (`SIM-VIRT-DEVICES` п. 4 №5), що було:** `change_otg_mode` (`gs/button.sh`) при `OTG_MODE_FILE='none'` виконував `cat none` (файл у поточному каталозі), друкував `cat: none: No such file or directory`, потім `otg mode is unkonw`. Це довге натискання кнопки, прив'язаної до `change_otg_mode`; у `gs/gs.conf:163` це `btn_q2_long_press` (q2; у віртуальному тесті прив'язано q3). Файл, випадково названий `none`, зробив би гірше: `echo device > none`.
- **Правка (мінімальна, `gs/button.sh`, початок `change_otg_mode`):** `if ! otg_supported; then echo "otg mode switch is not supported on this board"; return 0; fi` до першого `gpio_find`. Для Radxa (і невідомої плати, і без `board.sh`) `otg_supported` істинна: нуль зміни поведінки; golden `tests/golden/button/otg_*` лишились побайтно тими самими (прогнано, п. 8). Покрито `tests/static/rpi5-draft.sh` (функцію вирізано з `button.sh`; rpi5: повідомлення, `gpio_find` не викликається, навіть із файлом `none`; Radxa/невідома плата: старий шлях).
- **Не зроблено, поза моїми файлами (опис для лідера):** (а) `gs/gs.sh:31` `[ "$otg_mode" == "device" ] && /gs/otg-gadget.sh &`: типове `otg_mode='device'` у `gs/gs.conf:211`; на Pi 5/4 `otg-gadget.sh` читає `cat none`, не бачить `host`, далі `modprobe libcomposite`, створює `g1` і падає на відсутньому UDC; потрібне або `otg_mode='host'` у `gs.conf` образу Pi, або `otg_supported` у `gs.sh:31` і на початку `otg-gadget.sh`. (б) `gs/gs-init.sh:52` збирає `rk3566-dwc3-otg-role-switch.dtbo` для Radxa (на Pi не потрібне).

### 4.3. Вентилятор і температура

- Офіційний вентилятор Pi 5 (Active Cooler/кейс) підключається до 4-контактного JST-SH роз'єму (`docs frequency-management.adoc:68-75`); firmware керує офіційними вентиляторами (`:62`); на завантаженні вмикає `cooling_fan` (у dtb за замовчуванням `status = "disabled"`, `:89`; `firmware README`: «cooling_fan Enables the Pi 5 cooling fan (enabled automatically by the firmware)»).
- Керує драйвер `pwm-fan` на RP1 PWM1 канал 3, GPIO45 `FAN_PWM` (`bcm2712-rpi-5-b.dts:390-398,492-494`, `CONFIG_SENSORS_PWM_FAN=m`, `bcm2712_defconfig:839`), як cooling-пристрій теплової зони. Пороги: 0 % до 50 °C, 30 % при 50, 50 % при 60, 70 % при 67,5, 100 % при 75 °C, гістерезис 5 °C (`frequency-management.adoc:77-85`); змінюються `dtparam=fan_temp0…3`, `fan_temp0_hyst…`, `fan_temp0_speed…` (`:87`, `bcm2712-rpi.dtsi:195-206`).
- **Наслідок для `gs/fan.sh`:** не запускати його для офіційного вентилятора (`fan_service_enable='no'` у `gs.conf` образу Pi 5): канал PWM1 уже в руках ядра, а `fan.sh` пише в `/sys/class/pwm/pwmchipN`. `PWM_SYSFS_BASE` у профілі лише формат шляху для зовнішнього вентилятора на інших виводах (`dtoverlay=pwm,pin=…`: `firmware README` у Pi 5-варіанті не читався: UNVERIFIED).
- Температура SoC: одна зона `cpu-thermal` (`bcm2712-ds.dtsi:48-53`); `THERMAL_CPU_TEMP_FILE=…/thermal_zone0/temp`, індекс HW (маркер 4). Обмеження: 80-85 °C плавне, 85 °C жорстке (`frequency-management.adoc:5`).

### 4.4. USB і живлення

- 600 мА для периферії від БЖ 3 А, 1,6 А від БЖ 5 А/25 Вт USB-PD; бюджет ділиться з роз'ємом вентилятора (`usb-bus-on-raspberry-pi.adoc:7-30`, `power-supplies.adoc:114`). Для донгла RTL8812AU + вентилятора + клавіатури/SSD брати БЖ 5 А (INF; струм донгла UNMEASURED).
- Читання стану: `vcgencmd get_config usb_max_current_enable`, `vcgencmd get_throttled`, `/proc/device-tree/chosen/power/{max_current,usb_max_current_enable}` (`power-supplies.adoc:124,175-180`), `vcgencmd pmic_read_adc` (рейка `EXT5V_V`, `:184-188`). Рядок `usb_max_current_enable=1` у `config.txt`: UNVERIFIED (п. 3.2); спершу правильне БЖ, а не примусове ввімкнення.
- Лінії `EN_MAX_USB_CUR` (GPIO49), `USB_VBUS_EN`, `USB_OC_N` в dts (`bcm2712-rpi-5-b.dts:686`, `rp1_usb0 pinctrl usb_vbus_pins`) показують, що обмежувач керується апаратно; ключ профілю для цього не потрібен.
- Порти: два контролери dwc3 (`rp1_usb0`, `rp1_usb1`, `rp1.dtsi:1118,1135`); яка пара фізичних портів на якому: UNVERIFIED (`docs/CONTOUR-GS.md` п. 2). Донгл на чорний USB 2.0 порт, подалі від USB 3.0 пристроїв.

### 4.5. PCIe

- Роз'єм PCIe FPC на Pi 5 за замовчуванням **вимкнений**, якщо не підключено HAT+ (`docs pcie.adoc`, «By default, the PCIe connector is not enabled unless connected to a HAT+ device»); вмикається `dtparam=pciex1` (псевдонім `nvme`), швидкість `dtparam=pciex1_gen=3` (там само). У нашому контурі NVMe/HAT не потрібні: **у `config.txt` нічого не додавати**. Внутрішній PCIe x4 до RP1 окремий: `boot.adoc:177-182` `pciex4_reset` (за замовчуванням скидається перед ОС; не чіпати).
- `dvfs=…`/`PCIe stability`: лише для серії 4, на Pi 5 не застосовується (`frequency-management.adoc:13`: «applies to 4-series devices only»).

### 4.6. Сторінка пам'яті 16 КіБ

- Firmware Pi 5 за замовчуванням вантажить `kernel_2712.img` («for example, 16K page-size»), інакше `kernel8.img` (`boot.adoc:24`); `bcm2712_defconfig:1,50` `CONFIG_LOCALVERSION="-v8-16k"`, `CONFIG_ARM64_16K_PAGES=y`.
- Збірка DKMS на 16K (RUN у `docs/SIM-DKMS.md` §2): `svpcom/rtl8812au@6e75916` PASS на `rpi-2712` 6.12.109, vermagic і CRC збігаються, жодної помилки через `PAGE_SIZE`. **Виконання на 16K: UNVERIFIED** (`SIM-DKMS` §4: QEMU не має 16K-смаку). Запасний шлях: `kernel=kernel8.img` у `config.txt` (4K; губиться оптимізація 2712).

### 4.7. HEVC-декодер і GStreamer: висновок «декодувати на хості»

- **Апаратний HEVC є (SRC):** `bcm2712-ds.dtsi:372-383` вузол `hevc_dec: codec@800000` `compatible = "brcm,bcm2712-hevc-dec", "raspberrypi,hevc-dec"` без `status = "disabled"` (отже ввімкнений: INF); `Kconfig` `VIDEO_RPI_HEVC_DEC` «stateless V4L2 decoder»; `bcm2712_defconfig:1034` `=m` (модуль `rpi-hevc-dec`). H.264 апаратно на Pi 5 немає (SNIP+INF, `docs/CONTOUR-GS.md` п. 1.1).
- **Формат виходу лише COL128 (SRC):** `hevc_d_video.c:287-290` допустимі `V4L2_PIX_FMT_NV12_COL128` і `NV12_10_COL128` (варіанти `NV12MT_*` закоментовано).
- **GStreamer 1.22.0 (Bookworm), 1.22.12: мапінгу немає (SRC, перечитано):** у `gstv4l2format.c` для тегів 1.22.0 і 1.22.12 нуль збігів `SAND128`/`NC12`; для 1.26.2 і 1.28.2 по 2 збіги `SAND128` і 3 `NC12` (рядок `V4L2_PIX_FMT_NC12 → DRM_FORMAT_NV12 + DRM_FORMAT_MOD_BROADCOM_SAND128`). Bookworm має `gstreamer1.0-plugins-bad 1.22.0-4+deb12u7` (SRC `deb.debian.org` Packages).
- **Висновок (INF; виконання HW/UNVERIFIED):** `v4l2slh265dec ! kmssink` (`docs/CHAINS.md:46`, `bench/video-rx.sh`) на Bookworm 1.22 імовірно не домовиться про формат; тож **основний декод на хості** (x86, будь-який кодек), Pi 5 лише радіошлюз + MAVLink. Відкриті проблеми декодера на втратах UDP (SNIP, linux#7609/#7612: `docs/PI-PORT.md` §10).
- **Умова перегляду (R4):** якщо потрібне HEVC-декодування саме на Pi 5 і GStreamer на Bookworm не працює (або сумісний запасний шлях не знайдено), перейти на Trixie (ядро 6.18, GStreamer 1.26.2). Запасні шляхи на Bookworm, що вимагають окремої перевірки (UNVERIFIED): збірка GStreamer ≥ 1.26 з джерел; `ffmpeg` з V4L2 request API. Жоден із них не перевірено.

## 5. Чому `GADGET_IFNAME` не маркер, а `DTBO_DIR_LOWER` не визначено

Маркер `UNVERIFIED` описує твердження про світ, яке може бути хибним. `GADGET_IFNAME='rpi0'` не твердить нічого: ім'я виключно проєктне, на профілі без гаджета правило `KERNELS=="gadget"` ніколи не збігається (`OTG_CONTROLLER='none'`), а дивний вибір імені перевірити нема чим. Тому джерело в профілі `REPO` (те саме ім'я, що в `rpi4`), без маркера. У `rpi4` те саме значення мало маркер; різницю свідомо лишено, щоб храповик відбивав справді невідоме. Якщо лідер вважає, що узгодженість важливіша, достатньо додати рядок `# UNVERIFIED (…)` над ключем і підняти `UNVERIFIED_MAX` до 5 (golden зміниться).
`DTBO_DIR_LOWER` не визначено, бо ключ необов'язковий, ніхто його не читає, а значення (шлях нижнього шару overlayroot для окремого FAT-монтування, `pi-gen prerun.sh:70` `mount … /boot/firmware -t vfat`) без зібраного образу невідоме; визначення з вигаданим значенням було б гіршим за відсутність. Коли образ з overlayroot з'явиться, ключ додають із джерелом.

## 6. Рекомендовані пакети й версії (SRC, Bookworm arm64, прочитано 2026-10-03)

| Пакет | Версія | Архів | Навіщо |
|---|---|---|---|
| `linux-image-rpi-2712`, `linux-headers-rpi-2712` | `1:6.12.109-1+rpt1` | `archive.raspberrypi.com` | ядро 16K і заголовки для DKMS (`SIM-DKMS` §1); **не** `linux-headers-rpi-v8` (це 4K, Pi 4) |
| `dkms` | `3.0.10-8+deb12u1` | `deb.debian.org` | збірка `88XXau_wfb` |
| `gpiod` | `1.6.3-1+b3` (`libgpiod2`, `libgpiod-dev`) | `deb.debian.org` | `gpiofind`/`gpiomon`/`gpioset` v1: код `gs/` без обгортки v2 |
| `gstreamer1.0-tools`, `-plugins-base` `1.22.0-3+deb12u6`, `-plugins-good` `1.22.0-5+deb12u3`, `-plugins-bad` `1.22.0-4+deb12u7`, `-libav` `1.22.0-2` | 1.22.0 | `deb.debian.org` | UDP-прийом, ПЗ-декод при потребі (на Pi 5 лише для налагодження) |
| `v4l-utils` | `1.22.1-5+b2` | `deb.debian.org` | `v4l2-ctl --list-devices` для перевірки `rpi-hevc-dec` |
| `wfb-ng` | `26.9.29.44301-0~bookworm` | `apt.wfb-ng.org` (`dists/bookworm`, компонент `master`; є також `release-25.01` `25.1.117.73439-0~bookworm`) | транспорт |
| `python3` | `3.11.2-1+b1` | `deb.debian.org` | pymavlink 2.4.50 пін |
| `iw` `5.19-1`, `rfkill` `2.38.1-5+deb12u3` | | `deb.debian.org` | monitor, `rfkill unblock all` |
| `rpi-eeprom` | `28.33-1` | `archive.raspberrypi.com` | `sudo rpi-eeprom-update` (bootloader Pi 5) |

Драйвер: `svpcom/rtl8812au@6e75916416de1dce5ecd37f824896bebf96aaf8f` (REPO `bench/env.example`, `tests/sim/dkms/manifest.txt`) **з патчем** `tests/sim/dkms/patches/0001-rtl8812au-6.12-set_monitor_channel-signature.patch` для ядра 6.12 (`SIM-DKMS` §3.1: без нього збірка проходить, але сигнатура `set_monitor_channel` хибна; наслідок на залізі HW). Для `svpcom/rtl8812eu` (AIR-сторона не на цьому вузлі) потрібен `0002` (`SIM-DKMS` §3.2, без нього збірка падає). Версія пакета ядра після `apt upgrade` зміниться: перезапустити `tests/sim/dkms/run.sh --fetch` з новими версіями.
Чорний список у `/etc/modprobe.d/wfb.conf`: `88XXau 8812au rtl8812au rtl88x2bs` (як у `bench/install-driver.sh`) **плюс** `rtw88_8812au` і `rtw88_8821au` (PROPOSAL, `SIM-DKMS` §3.4): на Bookworm 6.12.109 цих модулів **немає** (SRC, `SIM-DKMS` §3.3: `RTW88_8812AU`/`8821AU` не зібрані), тож запис безпечний і потрібен лише на Trixie 6.18.50, де вони є й перекривають 34 з 58 ідентифікаторів `88XXau_wfb`. Також `rtw88_8814au`, якщо колись ставитиметься `morrownr/8814au`.

## 7. Чек-лист першого запуску на Pi 5 Bookworm (HW; усе нижче не виконано)

Порядок: спершу read-only факти (`bench/doctor.sh`), потім DKMS, потім GPIO/USB, потім радіо; кожен крок записати в `bench/RESULTS.md`. Без гвинтів, батарея й ESC від'єднані; TX не вмикати без антен.

1. **ОС і ядро.** Imager: Raspberry Pi OS Lite 64-bit **Bookworm** (не Trixie). `sudo apt update && sudo apt full-upgrade`, перезавантаження. `uname -r` очікуємо `6.12.x+rpt-rpi-2712`; `getconf PAGESIZE` очікуємо `16384`; `dpkg -l linux-image-rpi-2712`; `sudo rpi-eeprom-update` (`-a` для застосування). Якщо `PAGESIZE=4096`, ядро не 2712 (перевірити `kernel=` у `config.txt`). Знімає: 16K-припущення (п. 4.6).
2. **Read-only знімок:** `bench/doctor.sh` (пише JSON: ядро, `pagesize`, `board_model`, `get_throttled`, `usb_max_current_enable`, `thermal_zone0_mC`, `lsusb -t`, `gpioinfo` голова, `iw`; нічого не передає й не вантажить). Додатково вручну: `gpiodetect` (шукати `[pinctrl-rp1] (54 lines)` і записати номер: знімає розбіжність п. 4.1), `gpioinfo | grep -E 'ID_SDA|GPIO4\b|GPIO27'`, `gpiofind GPIO17`, `gpiofind PIN_7` (очікуємо «не знайдено»), `cat /sys/class/thermal/thermal_zone*/type` (знімає маркер 4: `cpu-thermal` у зоні 0), `ls /dev/i2c-*` після `dtparam=i2c_arm=on`.
3. **`config.txt` (`/boot/firmware/config.txt`; зміни по одній, перезавантаження після кожної):**
   - нічого про PCIe: роз'єм лишається вимкненим (п. 4.5); `dtparam=pciex1` лише якщо з'явиться NVMe/HAT+;
   - вентилятор: нічого, поки Active Cooler працює (`cat /sys/class/thermal/cooling_device*/type` і `cat /sys/class/hwmon/*/pwm1` показують `pwm-fan`; при потребі `dtparam=fan_temp0=55000` тощо, `frequency-management.adoc:87`);
   - USB-струм: `vcgencmd get_config usb_max_current_enable` і `/proc/device-tree/chosen/power/max_current`; якщо видно 600 мА, замінити БЖ на 5 А; запис `usb_max_current_enable=1` у `config.txt` **UNVERIFIED**, не робити без підтвердження з першоджерела;
   - `dtparam=i2c_arm=on` і `dtoverlay=i2c-rtc,ds3231` лише коли підключено DS3231;
   - `dtoverlay=dwc2,dr_mode=peripheral` НЕ додавати (п. 4.2: профіль `OTG=none`; експеримент із USB-C окремо, п. 10);
   - для 4K-ядра (діагностика 16K-проблем): `kernel=kernel8.img`.
4. **DKMS `rtl8812au` на 6.12/16K** (за `docs/SIM-DKMS.md`): `sudo apt install dkms linux-headers-rpi-2712 git build-essential bc`; `/lib/modules/$(uname -r)/build` має існувати; клон `svpcom/rtl8812au` на пін `6e75916416de1dce5ecd37f824896bebf96aaf8f`, **застосувати `0001-…set_monitor_channel-signature.patch`**, далі `./dkms-install.sh`; `/etc/modprobe.d/wfb.conf` з чорним списком `88XXau 8812au rtl8812au rtl88x2bs rtw88_8812au rtw88_8821au` і `options 88XXau_wfb rtw_tx_pwr_idx_override=<мінімум>`; перезавантаження; `dkms status`, `lsmod | grep 88XXau_wfb`, `dmesg | grep -iE '88XXau|rtl|usb'`, `ethtool -i wlanX` (версія драйвера має бути порожньою для патченого модуля). Знімає UNVERIFIED рантайму на 16K (`SIM-DKMS` §7 №1) і наслідок хибної сигнатури (§7 №2: `iw dev wlanX set channel <n>` у monitor з патчем і без). Для цього кроку `bench/install-driver.sh` НЕ чіпав, див. п. 9.
5. **Радіо:** `bench/bench.sh setup gs`, `finish gs`, `check gs`; `iw dev wlanX set type monitor`, `set channel`, ін'єкція (`bench/RESULTS.md`); `rfkill unblock all`; NetworkManager/wpa_supplicant не керують `wlanX`. Донгл на чорний USB 2.0 порт.
6. **Відео-ланка:** лише хост декодує: `DECODER=avdec_h265` (або h264) на Ubuntu; на Pi 5 перевірка HEVC-декодера для довідки: `lsmod | grep rpi_hevc_dec` (або `rpi-hevc-dec`), `v4l2-ctl --list-devices`, `gst-inspect-1.0 v4l2slh265dec`, `GST_DEBUG=v4l2codecs*:5 gst-launch-1.0 …` з файлом HEVC (очікуємо відмову узгодження COL128/`SAND128` на 1.22: п. 4.7). Зафіксувати результат: це визначає, чи переглядати R4.
7. **Живлення й тепло під навантаженням (UNMEASURED):** `vcgencmd get_throttled` (`0x0` = без проблем), `vcgencmd measure_temp`, `vcgencmd pmic_read_adc | grep EXT5V_V`, `free -m`, 30 хвилин із відео.
8. **OTG-перевірка (опційно):** `ls /sys/kernel/debug/usb/*/` (підтвердити, що `mode` або відсутній, або записи ігноруються: `dr_mode=host`).

Для знімання маркерів: п. 1-2 → 4 (`THERMAL_CPU_TEMP_FILE`), п. 2 → 1 (`GPIO_PIN_PREFIX`: порівняти `gpioinfo` із `pinmap.conf` для 40 виводів, окремо вивід 7=GPIO4, 11=GPIO17, 12=GPIO18); `PART_OVERLAY_NUM`/`PART_VIDEOS_NUM` знімаються лише образом з розміткою MBR `p1/p2/p3/p4(ext)/p5` і запуском `gs-init.sh` на ньому.

## 8. Що виконано в цій сесії (REPO/RUN)

- `gs/boards/validate.sh`: `ok radxa-zero3`, `ok rpi4`, `ok rpi5`.
- `tests/static/rpi5-draft.sh` + golden `tests/golden/static/rpi5-draft.out`: валідація, мутації обов'язкових ключів (18×2), поганих значень, наявність `# src:` над кожним ключем, храповик UNVERIFIED (4), сентинел OTG, `change_otg_mode` із `none`, `gpio_find` для 17 виводів і відмови для 27/28/1/2/6/9/39, повнота `pinmap.conf`, рендер udev.
- Нічого з перевіреного на залізі.

## 9. Що треба змінити поза цим профілем (опис, файли не чіпались)

- `bench/install-driver.sh`: (1) застосовувати `tests/sim/dkms/patches/series` для ядра `6.12*` (зараз клон+checkout+`dkms-install.sh` без патча, тобто на Bookworm збере хибну сигнатуру `set_monitor_channel` для 8812au і **не збере** 8812eu); (2) додати `rtw88_8812au rtw88_8821au` у чорний список; (3) пін `svpcom/rtl8812eu` і `libc0607/rtl88x2eu` (`SIM-DKMS` §7 №5); (4) повідомлення для 16K-ядра (`getconf PAGESIZE`).
- `gs/gs.sh:31`, `gs/otg-gadget.sh`: `otg_supported` (п. 4.2).
- `gs/gs.conf` для образу Pi 5: `otg_mode='host'`, `fan_service_enable='no'`, піни кнопок/LED у фізичній нумерації 40-пінової шини (без 27/28).
- `gs/gs-init.sh`: ім'я консолі й `DTBO_*`/розмітка через профіль (M3c); MBR з логічним розділом.
- `build/`: вибір ОС/ядра для Pi 5 не конфігурований (`build/build.sh` це Radxa-збирач: `build/config` `IMAGE_URL` Radxa, ядро `radxa/kernel linux-5.10-gen-rkr4.1`, `build.sh:113`). Пропозиції ключів у п. 11.

## 10. Відкриті питання

1. Номер і мітка чипа RP1 на 6.12 (п. 4.1): `gpiodetect`.
2. Чи доходить SoC-dwc2 до USB-C (`dtoverlay=dwc2,dr_mode=peripheral` + `g_ether` із хостом на USB-C: HW; на профіль не впливає).
3. Виконання `88XXau_wfb` на 16K, ін'єкція, `set_monitor_channel` (п. 7.4).
4. HEVC-декод на Pi 5 через GStreamer 1.22 (п. 7.6) і R4.
5. Розмітка MBR та overlayroot на Pi 5 (відомий обхід `initramfs8`: SNIP, `docs/PI-PORT.md` §10).
6. Яка пара портів USB на якому контролері.

## 11. Пропозиції ключів конфігурації (PROPOSAL; реєстр `config/registry.tsv` і `build/` не змінювались)

Місця, де вибір ОС/ядра для Pi 5 мав би бути конфігурованим (REPO, перевірено читанням): `build/versions.env` (маніфест пінів, нічим не читається, лише Radxa-компоненти), `build/config` (`IMAGE_URL` Radxa), `build/build.sh` (Radxa-ядро, `:113`), `bench/install-driver.sh:39-42` (вибір заголовків за `uname -r`, без розрізнення Bookworm/Trixie), `docs/PI-PORT.md` §4 (матриця 6.12/6.18). Пропонуються:

| Ключ | Де | Значення | Примітка |
|---|---|---|---|
| `RPIOS_SUITE` | `build/versions.env`, `config/registry.tsv` (`enum:bookworm\|trixie`, не safety) | `bookworm` | вибір дистрибутива Pi OS; R4: умова переходу на `trixie` |
| `RPI_KERNEL_FLAVOUR` | те саме (`enum:rpi-2712\|rpi-v8`) | `rpi-2712` для Pi 5 | відповідає `uname -r` суфіксу й пакетам `linux-headers-rpi-<flavour>` |
| `RPI_KERNEL_PKG_VERSION` | `build/versions.env` (`NAME_PIN`-стиль, порожньо = UNVERIFIED) | `1:6.12.109-1+rpt1` (SRC, п. 1) | пін пакета ядра для відтворюваності; без піна `apt upgrade` змінює ядро й ламає DKMS |
| `RPI_EXPECT_PAGESIZE` | `config/registry.tsv` (`int`, `16384`) / перевірка в `bench/doctor.sh` | `16384` | захист від випадкового 4K-ядра (`kernel8.img`) |
| `RPIOS_IMAGE_URL`, `RPIOS_IMAGE_SHA256` | `build/config`, `build/versions.env` | не заповнювати без джерела | аналог `IMAGE_URL` для Pi OS Bookworm Lite arm64: URL і sha256 з офіційного каталогу образів **не читались** (UNVERIFIED) |
| `BOARD` | `config/registry.tsv`, `build/` | `rpi5` | вибір профілю `gs/boards/<id>`; зараз лише змінна середовища `gs/lib/board.sh` |
| `GST_DECODER` | профіль плати (PI-PORT §5 пропозиція) | `none` для rpi5 (декод на хості) | у профілі rpi5 не додано: нічого не читає; з'явиться з M3c |
| `DRIVER_PATCH_SERIES` | `bench/lib.sh`/`bench/env.example` | `tests/sim/dkms/patches/series` | застосування патчів `0001/0002` для `k612*` у `install-driver.sh` |

Пропозиції не вносились у реєстр: `tests/static/config.sh` і `config.out` ратчетять літерали, а реєстр веде інший власник.
