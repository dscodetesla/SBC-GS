# Віртуальні пристрої ядра у QEMU-госта (GPIO, USB, радіо)

Статус: прогнано 2026-10-03, `run.sh all`: **96 PASS, 0 FAIL, 1 SKIP** (gpio 38, usb 31, radio 27; SKIP = опційна перевірка oops, розд. 4). Код: `tests/sim/virt/` (`run.sh --check|gpio|usb|radio|all`).
Мітки: **RUN** виконано в цій сесії (вивід процитовано), **REPO** з коду репозиторію, **INF** висновок, **UNVERIFIED** не запускалось, **HW** потребує заліза.

## 1. Що це і навіщо
Гість QEMU x86_64 (TCG, без root на хості) із стандартним ядром Ubuntu `6.8.0-146-generic` (пакети в `~/.cache/sbc-gs-sim`, ті самі, що в `tests/sim/qemu_hwsim.sh`).
У гості працюють **справжні** `bash`, coreutils, util-linux, libgpiod **1.6.3** (Ubuntu 24.04 `gpiod`; репозиторій орієнтований на libgpiod v1, `docs/PI-PORT.md`), `systemd-udevd` 255 і **незмінені** `gs/*.sh`, `gs/lib/gpio.sh`, `gs/*.rules`. Пристрої віртуальні, але інтерфейси ядра справжні: configfs/sysfs `gpio-sim`, gadget configfs + `usbip-vudc`/`vhci_hcd`, `mac80211_hwsim`.

## 2. Які модулі ядра знайдено, а яких немає (RUN)
| Модуль / опція | Результат | Доказ |
|---|---|---|
| `gpio-sim` | **є** (`CONFIG_GPIO_SIM=m`) | `drivers/gpio/gpio-sim.ko.zst` у `linux-modules-extra-6.8.0-146-generic`; у гості `gpiodetect`: `gpiochip0 [gpio-sim.0-node0] (40 lines)` |
| `gpio-mockup` | не потрібен (gpio-sim є) | — |
| `dummy_hcd` | **ВІДСУТНІЙ** | `boot/config-6.8.0-146-generic`: `# CONFIG_USB_DUMMY_HCD is not set`; у `modules.dep` немає |
| `usbip-vudc` + `vhci-hcd` + `usbip-core` | **є**, використано замість `dummy_hcd` | `kernel/drivers/usb/usbip/*.ko.zst`; UDC `usbip-vudc.0`, enumeration `usb 1-1: New USB device found, idVendor=1d6b, idProduct=0104` |
| `libcomposite`, `usb_f_acm/ncm/ecm/mass_storage`, `g_serial/g_ether/g_mass_storage` | є | `drivers/usb/gadget/**` |
| `cdc-acm`, `cdc_ncm`, `cdc_ether`, `usb-storage`, `sd_mod` | є (sd_mod, usbcore, ehci вбудовані) | `modules.builtin`, `cdc_acm 1-1:1.2: ttyACM0: USB ACM device`, `sda: sda1` |
| `mac80211_hwsim` | є | як у `qemu_hwsim.sh` |

## 3. Як зв'язано gadget і хост без dummy_hcd
`tests/sim/virt/guest/usbip_link.c`: пара TCP-сокетів на loopback, рукостискання USBIP `OP_REQ_IMPORT/OP_REP_IMPORT` (те, що роблять `usbipd`/`usbip`), далі fd у `usbip-vudc.0/usbip_sockfd` і `vhci_hcd.0/attach`. Відключення: `vhci_hcd.0/detach`.
Лог гостя: `vhci_hcd: Device attached`, `usb 1-1: new high-speed USB device number 2 using vhci_hcd`.

## 4. Знахідки ядра (RUN)
1. **Oops у `usbip-vudc` при відключенні gadget з функцією serial/acm.** `usbip_link unplug` (або `echo '' > UDC`) з прив'язаною `acm.gs0`: `BUG: kernel NULL pointer dereference, address: 00000000000003b0`, `RIP: vep_dequeue+0x2a/0xe0 [usbip_vudc]`, стек `usb_ep_dequeue` ← `gs_console_disconnect [u_serial]`; наступний запис у `UDC` зависає. Те саме з FunctionFS-функцією (userspace CDC-ACM): oops у `vep_dequeue`. Без acm/ffs (ncm + mass_storage) відключення, `UDC=""`, перепідключення працюють чисто (`usbip_status` повертається в `1`).
   Наслідок: **гаряче відключення USB-FC (ttyACM0) через справжній USB-шлях НЕ можливе** на цьому ядрі; сценарій відключення/повернення FC для `gs-mavlink` емульовано через pty (розд. 6, це НЕ USB).
2. **Правило `gs/98-rename.rules` `KERNELS=="gadget"` не збігається**: батьківський пристрій gadget-NIC на ядрі 6.8 називається `gadget.0` (`/sys/devices/platform/usbip-vudc.0/gadget.0/net/gsnic0`). Копія правила з `KERNELS=="gadget.*"` перейменовує NIC у `radxa0`/`rpi0` (профіль rpi4). На ядрі Radxa BSP (5.10) ім'я могло бути `gadget` (INF, не перевірено).
3. **`ENV{ID_USB_DRIVER}=="cdc_ncm"` → `usb0` не спрацьовує**: хостовий NIC cdc_ncm отримує ім'я `eth1` (правило `KERNEL=="eth*", ID_BUS=="usb"`), див. результати USB.
4. gpio-sim: споживач `-B pull-down` застосовує зсув до того самого змодельованого `pull`; натискання кнопки тому «утримується» повторним записом `pull-up` кожні 20 мс (розд. 5).
5. **Дефект коду, знайдений тестом:** на `rpi4` `OTG_MODE_FILE='none'`, тож довге натискання з `change_otg_mode` дає `cat: none: No such file or directory` і `otg mode is unkonw`, LED не торкається (відповідає сентинелю `none`, але обробник усе одно викликається).
6. `gpio_find 27` на rpi4 дає `GPIO0`, а на справжньому Pi 4 рядок 0 називається `ID_SDA` (`gpiofind GPIO0` не знаходить) — піни 27/28 (ID EEPROM) не можна використовувати через `gpio_find`.

## 5. GPIO (RUN, `run.sh gpio`: 38 перевірок)
Чіпи gpio-sim: `radxa` (40 ліній `PIN_1..PIN_40`, зсув = пін−1) і `pi` (28 ліній: `ID_SDA`, `ID_SCL`, `GPIO2..GPIO27`). Перевірено: `gpiofind PIN_32` → `gpiochip0 31`; `gpio_find` (справжній `gs/lib/gpio.sh`): radxa 32 → `gpiochip0 31`, rpi4 32 → `gpiochip1 12`, 38 → 20, 40 → 21, 15 → 22, 11 → 17, 7 → 4, пін 6 (GND) відхилено.
`button_action` (текст функції вирізано з `gs/button.sh` без змін): коротке натискання → `single`, довге → `long`, для обох плат; повний демон `gs/button.sh` (безкінечний цикл `while true` + `gpiomon`/`gpioget`) запущено з обмеженням `timeout`: q2 коротке → `single` у FIFO, q2 довге → `no record file found!`, q3 довге на Radxa → `change otg mode to host!`, файл режиму `host`, LED PIN_15 `0,1,0`.
Таймінги: `button.sh` міряє `/proc/uptime` у сотих секунди між кінцем першого і другого `gpiomon`, поріг `-lt 200` (2 с); у TCG гості накладні витрати запуску процесів входять у виміряний час, тому «коротке» тримається 0,3 с після озброєння `gpiomon -f`, «довге» 2,5 с.

## 6. USB (RUN, `run.sh usb`: 31 PASS)
Gadget через configfs (`g1`: ncm+mass_storage; потім ecm+mass_storage; `g2`: acm+ncm+mass_storage; макет як у `gs/otg-gadget.sh`), UDC `usbip-vudc.0`, хост `vhci_hcd`; реальний `systemd-udevd 255` із правилами `gs/98-rename.rules`, `gs/99-GS.rules`.
- **mass storage + `mount_extdisk`:** `sda1` з'являється, udev запускає `Running command "/gs/button.sh mount_extdisk /dev/sda1"`, справжній `button.sh` монтує образ (MBR+ext4, файл-образ як backing LUN, не loop-пристрій): `/Videos/MARKER.TXT` видно, `pixelpilot.msg`: `/dev/sda1 ext4 11M 28K 9.0M 1% /Videos`. Відключення (`usbip detach`): `sda1` зникає, NIC зникає, `usbip_status` знову `1`; повторне підключення: правило спрацьовує вдруге, диск змонтовано знову.
- **імена NIC:** хостовий NIC cdc_ncm стає `eth1`, не `usb0` (правило `ID_USB_DRIVER=="cdc_ncm"` не спрацьовує, знахідка 3); cdc_ether (ecm) → `eth1` (правило `eth*`+`ID_BUS=usb` працює); gadget-NIC лишається `gsnic0` (правило `KERNELS=="gadget"` не збігається з `gadget.0`, знахідка 2); копії правил з `KERNELS=="gadget.*"` дають `radxa0` (профіль Radxa) і `rpi0` (`render-udev.sh rpi4`).
- **FC як USB-serial:** `mavhb gen /dev/ttyGS0` (gadget-сторона) → USB bulk → `/dev/ttyACM0` → справжній `gs/mavlink/gs-mavlink.sh` (`SERIAL_DEV=/dev/ttyACM0`) з `mavp2p v1.3.3` → UDP; `usb.fc.usb-serial.heartbeats`: 60 heartbeat (sysid 1) за 6 с при 10 Гц (в окремому прогоні 49-61).
- **Відключення/повернення FC (емуляція, НЕ USB):** через oops (знахідка 1) справжній USB-шлях для serial не використано; pty-пристрій `/dev/ttyFC0` (`mavhb ptygen`), «відключення» = закриття master. Спостережено: heartbeat зупиняються (0 за 2 с), `mavp2p` **не завершується** (лог `node disappeared: chan=serial:/dev/ttyFC0`), завершень, видимих наглядачу, 0 (тому `Restart=on-failure` не спрацьовує), після повернення пристрою heartbeat відновлюються (100 за 10 с). Наглядач `Restart=on-failure` емульовано циклом `while` (**systemd у госта немає**).
- Опційно `VIRT_OOPS_PROBE=1` відтворює oops (може підвісити гостя), типово SKIP.

Реальне ядро: перерахування usbcore, drivers cdc_acm/cdc_ncm/cdc_ether/usb-storage/sd, події відключення, uevents, configfs gadget. Емульоване: немає живлення, сигналів, таймінгів; транспорт USBIP через TCP loopback; обидві сторони (gadget і хост) в одному ядрі.

## 7. Радіо hotplug (RUN, `run.sh radio`: 27 PASS)
`mac80211_hwsim radios=0`, радіо створюються/видаляються під час роботи через generic netlink (`hwsimctl new`, `hwsimctl del <id>`, `HWSIM_CMD_NEW_RADIO=4`, `DEL_RADIO=5`). `wfb.sh` справжній; шими лише `systemd-run`/`systemctl` (логують; `systemd-run` запускає корисне навантаження, тож справжній `wfb_rx` працює). Умова `invocation:gs.service` виконана символьним посиланням.
- add `wlan0`: `Running command "/gs/wfb.sh wlan0"`, `WFB_NICS="wlan0"`, `systemctl restart wifibroadcast@gs`; `hwsim0` правило не запускає (0 викликів).
- **Знахідка:** друге радіо `wlan1`: `wlan1: Failed to rename network interface 4 from 'wlan1' to 'wlan0': File exists` / `Failed to process device, ignoring` — udev відкидає подію, **`wfb.sh` для другого адаптера не запускається** (правило `98-rename.rules:2` перейменовує всі `wl*` на `wlan0`).
- remove: `RUN /gs/wfb.sh` без аргументів, `WFB_NICS` перераховано. Видалення **останнього** адаптера: `wfb.sh` виходить рано (`[ -z "$wfb_nics" ] && exit 0`), `WFB_NICS` лишається застарілим `wlan0`, перезапуску служби немає.
- `wfb_mode=aggregator`: нове `wlan0` → `monitor_wnic` (справжні `ip`/`iw`): `type monitor`, `channel 161`; `systemd-run /usr/bin/wfb_rx -f -p 0 -c 127.0.0.1 -u 10000 -i 7669206 wlan0` і `-p 16 … -u 10001`; процеси `wfb_rx` живі (2). Після видалення радіо `wfb_rx` не завершується протягом 10 с (спостереження, чистки немає).

## 8. Мутаційна перевірка (RUN, на копії `gs/`+`config/` через `GS_ROOT`)
| Мутація | Результат |
|---|---|
| `button.sh`: `-lt 200`/`-ge 200` → `20` | gpio: 5 FAIL (`expected 'single', got 'long'` для radxa і rpi4, демон без `single` у FIFO) |
| `pinmap.conf`: `32 12` → `32 13` | gpio: 3 FAIL (`gpio_find.rpi4.32 expected 'gpiochip1 12', got 'gpiochip1 13'`) |
| `99-GS.rules`: `/gs/wfb.sh $name` → `/gs/wfb2.sh $name` | radio: 11 FAIL (`udevd never ran /gs/wfb.sh wlan0`, `WFB_NICS=''`, нема `wfb_rx`) |

## 9. Що це доводить і чого ні
Доводить: справжні libgpiod 1.6.3, `gs/lib/gpio.sh`, `gs/button.sh` (**безкінечний цикл демона вперше покрито**, раніше `tests/README.md` називав його непокритим), udev і справжні інтерфейси ядра працюють проти емульованих GPIO/USB/радіо; контракти імен рядків `PIN_<n>` (Radxa) і `GPIO<bcm>` + `pinmap.conf` (Pi) узгоджені; знахідки розд. 4, 6, 7 — реальна поведінка коду на цих ядрі й udev.
Не доводить: електричні рівні, дребезг/пружини справжніх кнопок (`sleep 0.05; gpioget` без реального дребезгу), таймінги й живлення USB, перерахування реальних контролерів (DWC2/DWC3), перемикання ролей dwc2, особливості RP1 GPIO на Pi 5 (на Pi 5 чіп/імена ліній інші, INF: `gpiochip4`/`pinctrl-rp1`, **не перевірено**; імена `GPIO<n>` на Pi 5 — HW), поведінку справжніх драйверів Wi-Fi (rtl88xxau, brcmfmac), UART-канал WiFiLink2 і справжній Matek H743 (USB VCP), справжній systemd (`Restart=`, `RestartSec`).

## 10. UNVERIFIED
- Запуск `run.sh` без root на хості (тут запускалось як root у контейнері; QEMU не потребує root, `apt-get download` виводить попередження про `_apt`).
- Імена ліній Radxa Zero 3W `PIN_<n>`: форма береться з коду (`gs/button.sh`), справжній `gpioinfo` Radxa не знято (HW).
- Поведінка `usbip-vudc` на новіших ядрах (oops може бути виправлено), `KERNELS=="gadget"` на ядрі Radxa BSP.
- KVM-прогін (тут TCG; таймінги кнопки в TCG зсунуті накладними витратами процесів, див. розд. 5).
- Повний час `run.sh all`: не вимірювався окремо (INF 5-7 хв; `gpio` 2 хв 01 с).

## 11. Запуск і інтеграція
```
tests/sim/virt/run.sh --check          # статика, без VM, менше 2 с
tests/sim/virt/run.sh gpio|usb|radio|all
VIRT_OOPS_PROBE=1 tests/sim/virt/run.sh usb   # опційно: відтворити oops vudc
GS_ROOT=<копія репо> tests/sim/virt/run.sh gpio   # мутаційні перевірки на копії
```
Потрібні: `qemu-system-x86_64 busybox(static) cpio gzip zstd gcc kmod iw ip udevadm systemd-udevd mke2fs python3`; мережа лише першого разу (пакети ядра ~170 МБ у `~/.cache/sbc-gs-sim`, `gpiod` 1.6.3, для radio ще wfb-ng; `mavp2p` з PATH або `$SIM_CACHE/bin`, інакше частину FC пропущено). Коди виходу: 0, 1, 77 (немає передумови).
Реєстрація в `tests/sim/smoke.sh` (опт-ін, ще не додано): шлях `virt` за `SMOKE_VIRT=1`: `[ "${SMOKE_VIRT:-0}" = 1 ] || { skip virt "opt-in: SMOKE_VIRT=1"; return; }; "$HERE/virt/run.sh" all >"$tmp/virt.log" 2>&1; rc=$?; if [ "$rc" = 77 ]; then skip virt "$(tail -1 "$tmp/virt.log")"; else ok "$rc" virt "virtual GPIO/USB/radio devices"; fi`.

## 12. `run.sh roconf`: розкладка образу для `gs.conf` (RUN: 177 PASS, 0 FAIL)
Справжні ядерні файлові системи в QEMU-гості: корінь ext4 змонтовано лише для читання (нижній шар overlay `/etc`), `/etc/gs.conf` симлінк на `/config/gs.conf` на окремому записуваному розділі (ext4, а також vfat, бо на Pi `/config` може бути на FAT). Справжні `gs/lib/gsconf.sh`, `gs/gs-applyconf.sh` (sourced, як у `gs-init.sh`/`gs.sh`), шляхи запису `gs/gsmenu.sh`; шими лише `reboot`, `systemctl`, `nmcli`, `killall`. `gs-init.sh` НЕ запускається (перерозмічує диски).
Перевірено для кожного варіанту `/config`: запис через симлінк (ціль змінена, симлінк цілий, без копіювання в верхній шар overlay, режим/власник збережені, без залишків, відрізняється один рядок); вбивство писача посеред запису `SIGKILL` (ціль лишається повним старим файлом, залишок тимчасового файлу прибирається наступним оновленням); `EROFS` через `remount,ro` і `EACCES` для непривілейованого писача (повідомлення називає причину, ціль не змінена); порожній чи обрізаний файл відхиляється; злиття `custom.conf` через справжній `gs-applyconf.sh` (ворожі значення зберігаються інертними, не виконуються), вбивство посеред злиття (файл цілий, `custom.conf` збережено, повторний запуск довершує); `gsmenu` у тих самих умовах; перемонтування RO-кореня справжнім скриптом; контроль: наївний `sed -i /etc/gs.conf` без `readlink -f` на цій розкладці замінює симлінк у верхньому шарі overlay (чому потрібен `readlink -f`).
Знахідка стенду: процес, вбитий `kill -9`, у новій сесії (`setsid`) з успадкованим stdin терміналу ламав подальший вивід гостя; фікс у тесті `</dev/null` для таких процесів.
Не доводить: реальний образ OpenIPC/Radxa і Pi (його розкладка змодельована), реальну втрату живлення (`SIGKILL` не те саме, що обрив живлення з кешем флеш), BusyBox-утиліти, ядро Radxa BSP. Запуск: `tests/sim/virt/run.sh roconf` (≈3 хв під TCG), входить у `all`.
