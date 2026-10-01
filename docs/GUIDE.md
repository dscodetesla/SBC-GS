# Покроковий гайд: від нуля до випробування контуру

Лінійний шлях для стенду: **AIR** (Pi 4, емуляція дрона) · **GS** (Pi 5) · **хост** (ноутбук) · **TX12 MKII + ES900TX** · FC з ELRS-приймачем.
Тут лише послідовність і контрольні точки; деталі й обґрунтування: `bench/README.md`, `docs/BENCH-HARDWARE.md`, `docs/CHAINS.md`, `docs/KNOWLEDGE.md`.
Позначки: **SRC** прочитано з першоджерела, **INF** мій висновок, **HW** перевіряється лише на залізі, **НЕПЕРЕВІРЕНО** не підтверджено ніким.
Нічого з радіо й заліза мною не запускалося: кожен етап має контрольну точку, яку ви підтверджуєте самі. **Не переходьте до наступного етапу, поки не пройдена точка поточного.**

## Етап 0. Правила, що діють завжди

- **Безпека:** гвинти знято, батарея й ESC від'єднані, FC живиться лише від USB. `gs_mav.py --rc` вимагає прапор `--confirm-props-off` і ніколи не підключається до апарата з гвинтами.
- **RC через wfb-ng не є єдиним каналом керування.** Основний канал: TX12 → ES900TX → ES900RX → FC. Резервний RC через хост — окремий експеримент (етап 9).
- **Адаптери не вмикати без антен** (SRC: wfb-ng Setup-HOWTO). На столі мінімальна потужність (`TX_PWR_IDX=1`), антени на відстані 1–2 м (INF).
- **Регіон і частоти обираєте ви** і несете відповідальність за дотримання місцевих правил. Скрипти не мають регіону за замовчуванням.

## Етап 1. Пре-підготовка

### 1.1 Що потрібно
| Вузол | Склад |
|---|---|
| AIR | Raspberry Pi 4, БЖ 5 В/3 А, microSD, USB-адаптер RTL8812AU або EU **з антенами**, UVC-веб-камера (необов'язково), FC з ArduPilot по USB, ELRS-приймач ES900RX на UART FC |
| GS | Raspberry Pi 5, **БЖ 5 А (25 Вт)** (інакше USB лише 600 мА, SRC raspberrypi/documentation `power-supplies.adoc`), microSD, RTL8812 **з антенами** |
| Хост | ноутбук Linux або Windows (Python 3, GStreamer), Ethernet |
| RC | TX12 MKII + Happymodel ES900TX у JR-слоті, варіант (868/915) як у ES900RX |
| Мережа | комутатор/роутер і Ethernet для обох Pi та хоста; HDMI-монітор на GS (необов'язково, відео дивимось на хості) |
| Інструменти | мультиметр, мікро-USB/USB-C кабелі гарної якості (дешеві дають помилки FEC, SRC Setup-HOWTO) |

Варіант ES900RX/ES900TX (868 чи 915) має збігатися: сторінка приймача пише «750MHz/915MHz», схоже на помилку (НЕПЕРЕВІРЕНО), тож звіряйте маркування.

### 1.2 План адрес (приклад, змініть під вашу мережу)
| Вузол | Ім'я | IP |
|---|---|---|
| AIR | `air` | 192.168.50.10 |
| GS | `gs` | 192.168.50.11 |
| Хост | | 192.168.50.20 |

### 1.3 Репозиторій і нульовий тест на ноутбуці (без заліза)
```bash
git clone https://github.com/dscodetesla/SBC-GS && cd SBC-GS
git checkout claude/hopeful-brown-k5mmbm
# Linux: GStreamer і python
sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-{base,good,bad,ugly} gstreamer1.0-libav gstreamer1.0-x python3-venv
python3 -m venv ~/gsb && ~/gsb/bin/pip install pymavlink
cd bench && PY=~/gsb/bin/python ./bench.sh loopback
```
**Контрольна точка 1:** `ALL CHECKS PASSED` (телеметрія, RC-ехо, таймаути, відео h264/h265). Це перевіряє лише програмну частину, не радіо.

## Етап 2. Образи microSD

1. **Raspberry Pi Imager** → пристрій Pi 4 (для AIR) та Pi 5 (для GS) → **Raspberry Pi OS Lite (64-bit)**.
2. ОС: поточний реліз Pi OS побудований на Debian Trixie, попередній на Bookworm (SRC documentation `rpi-os-introduction.adoc`). Для першого стенду рекомендовано **Bookworm** (ядро 6.12.109 за індексом пакетів, драйвери там досліджені краще: INF); Trixie дає 6.18.50 і теж підтримується пінованим драйвером.
3. У «Налаштування ОС» (SRC `install.adoc`): ім'я хоста `air` / `gs`, користувач і пароль, **Enable SSH**, часовий пояс і розкладка. Wi-Fi не потрібен (LAN).
4. Записати, вставити, увімкнути, дочекатись першого завантаження.

**Контрольна точка 2:** `ssh <user>@air.local` і `ssh <user>@gs.local` працюють (або за IP з роутера).

## Етап 3. Перший запуск обох Pi (повторити на `air` і `gs`)

```bash
sudo apt update && sudo apt full-upgrade -y
sudo rpi-eeprom-update         # покаже стан; застосувати: sudo rpi-eeprom-update -a  (SRC boot-eeprom.adoc)
sudo reboot
```
Після перезавантаження:
```bash
uname -r              # має містити «-rpi-v8» (Pi 4) або «-rpi-2712» (Pi 5)
getconf PAGESIZE      # Pi 5 з ядром 2712: очікується 16384 (INF; SRC: kernel_2712 має 16K сторінки)
vcgencmd get_throttled   # 0x0 = жодних проблем з живленням/нагрівом
```
Клонувати репозиторій і створити налаштування:
```bash
git clone https://github.com/dscodetesla/SBC-GS && cd SBC-GS && git checkout claude/hopeful-brown-k5mmbm
cd bench && cp env.example env && nano env
```
У `env` **обов'язково** `WFB_REGION` (дозволений вам домен). Для першого проходу решту лишити типовою: `DRIVER=8812au`, `SOURCE=test`, `FC_SERIAL=` порожньо.

**Контрольна точка 3:** `uname -r` містить `-rpi-`; `get_throttled` = `0x0` (якщо ні, виправити БЖ/кабель до продовження).

## Етап 4. Радіо: драйвер і wfb-ng

Підключіть адаптер **з антеною** (на AIR і на GS). Потім на кожному вузлі:
```bash
cd ~/SBC-GS/bench
sudo ./bench.sh setup air     # на GS: sudo ./bench.sh setup gs
sudo reboot
```
Скрипт ставить пакети, venv з pymavlink, заголовки ядра (`linux-headers-rpi-v8` на Pi 4, `linux-headers-rpi-2712` на Pi 5, SRC Packages index обох релізів) і збирає драйвер svpcom/rtl8812au із закріпленого коміту через DKMS.
Після перезавантаження:
```bash
ethtool -i <wlan-інтерфейс>    # поле version має бути ПОРОЖНІМ (SRC wfb-ng Setup-HOWTO)
```
**Контрольна точка 4:** `lsmod | grep 88XXau_wfb` бачить модуль; `ethtool -i` показує порожню `version` на обох вузлах. Якщо збірка DKMS падає: `dkms status`, `dmesg`, див. `docs/PI-PORT.md` (матриця драйверів). **HW:** збірка на Pi 4/5 не перевірена мною.

## Етап 5. wfb-ng і ключі (спершу GS)

На **GS**:
```bash
sudo ./bench.sh finish gs
scp /etc/drone.key root@<IP AIR>:/etc/drone.key     # або через звичайного користувача і sudo mv
```
На **AIR**:
```bash
sudo ./bench.sh finish air      # потребує /etc/drone.key
```
Ключі не генерувати повторно: це ламає парування.
**Контрольна точка 5:** `./bench.sh check gs` і `./bench.sh check air` без `FAIL` по wfb-ng, ключах, конфігу, модулі та сервісі; на GS `wfb-cli gs` показує пакети й RSSI (дрон має працювати, див. етап 6).

## Етап 6. Емуляційний контур (без реального FC і камери)

На AIR: `sudo systemctl start bench-video-src bench-fc`. Далі тести з `bench/README.md` на GS (з `GS_FORWARD_IP` порожнім відео йде на GS):

| № | Дія | Критерій |
|---|---|---|
| T1 | `ethtool -i` на обох | `version` порожня |
| T2 | `wfb-cli gs` | пакети й RSSI від AIR |
| T3 | `sudo systemctl start bench-video-rx` (потрібен HDMI) | рухомий м'яч і таймер |
| T4 | `/opt/gs-bench/venv/bin/python bench/gs_mav.py --rc off` | HEARTBEAT ≈1 Гц, ATTITUDE ≈10 Гц, `sysid=1` |
| T5 | `gs_mav.py --rc sweep --confirm-props-off` | RTT у виводі, на AIR (`journalctl -u bench-fc`) видно канали |
| T6 | зупинити `gs_mav.py` | на AIR «RC override lost» через ≈3 с, «GCS failsafe» через ≈5 с |
| T7 | `sudo systemctl stop wifibroadcast@drone`, потім `start` | відео й MAVLink зникають і відновлюються самі |

**Контрольна точка 6:** T1–T7 пройдені (запишіть у `bench/RESULTS.md`). Python з pymavlink на вузлах: `/opt/gs-bench/venv/bin/python`.

## Етап 7. Хост

1. **Linux:** клон репозиторію (як у 1.3). **Windows:** Python 3 + `pip install pymavlink`, GStreamer (MSVC runtime) і команда з `docs/BENCH-HARDWARE.md` (приклад OpenIPC `gstlaunch_on_windows.md`).
2. На **GS**: у `env` задати `GS_FORWARD_IP=<IP хоста>`; потім
   ```bash
   cd ~/SBC-GS/bench && sudo ./configure-wfb.sh gs && sudo ./install-services.sh gs
   ```
   Конфіг матиме `connect://<IP хоста>:5600` (відео) і `:14550` (MAVLink). Схеми peer — лише UDP (SRC `services.py`).
3. На **хості** відкрити UDP 5600 і 14550 у брандмауері (INF) і запустити:
   ```bash
   ~/gsb/bin/python gs_mav.py --conn udpin:0.0.0.0:14550 --rc off       # телеметрія
   VIDEO_CODEC=h264 ./video-rx.sh                                       # відео (Linux)
   ```
   Не запускайте одночасно QGC і `gs_mav.py` на порту 14550.

**Контрольна точка 7:** на хості видно відео й телеметрію з `sysid=1`; відео декодує хост, Pi 5 лише пересилає (INF).

## Етап 8. Реальне обладнання на AIR

**8.1 FC (ArduPilot) — лише на USB-живленні, без гвинтів і батареї**
- FC підключити до Pi 4 по USB. Порт USB у ArduPilot це SERIAL0, MAVLink2 за замовчуванням (SRC ardupilot.org common-serial-options). Ім'я пристрою (`/dev/ttyACM0` чи інше) перевірити `ls /dev/ttyACM*` і `dmesg` (НЕПЕРЕВІРЕНО документацією).
- На `air` у `env`: `FC_SERIAL=ttyACM0` (змінити за потреби), далі
  ```bash
  sudo ./configure-wfb.sh air && sudo ./install-services.sh air && sudo systemctl restart wifibroadcast@drone
  ```
  Peer буде `serial:ttyACM0:115200` (SRC Setup-HOWTO + `services.py`); **без пристрою wfb-ng не стартує**.
- **Очікування:** на GS `gs_mav.py --rc off` показує HEARTBEAT від реального FC (його `sysid` зазвичай 1, не 3: wfb-ng сам ін'єктує повідомлення з `sysid 3`).
- Для індикації RC у телеметрії ArduPilot має стрімити `RC_CHANNELS`: параметр групи `SRn_RC_CHAN` (INF, перевірити за вашою версією).

**8.2 ELRS-приймач → FC (основний RC-канал)**
- ES900RX на вільний UART FC; для нього `SERIALx_PROTOCOL = 23` (SRC ardupilot.org common-crsf-telemetry: «ExpressLRS systems use the CRSF protocol and connect identically»). Живлення приймача 5 В (SRC fpvua.org).
- Прошивки ES900TX/RX і binding phrase мають збігатися (SRC expresslrs.org ES900TX). Для ES900TX у JR-слоті TX12: зовнішній модуль у налаштуваннях моделі EdgeTX, протокол CRSF: **конкретні пункти меню НЕПЕРЕВІРЕНО** (сторінки дали 404), звіряйте з документацією ELRS/EdgeTX. Bind і параметри через ELRS Lua-скрипт (SRC expresslrs.org lua-howto).
- Не вмикайте нативний MAVLink ELRS: у стенді лишаємо CRSF (RC) і MAVLink лише через wfb-ng, щоб не було дублікатів з тим самим `sysid`.
- **Радіо-failsafe (SRC ardupilot.org radio-failsafe):** спрацьовує після `RC_FS_TIMEOUT` (типово 1 с); значення `FS_THR_ENABLE` задати свідомо.

**8.3 Веб-камера (за потреби)**
```bash
v4l2-ctl --list-devices
# у env на air: SOURCE=webcam WEBCAM_DEV=/dev/video0 WEBCAM_FORMAT=jpeg
sudo systemctl restart bench-video-src
```
**HW:** MJPEG-режим камери й `x264enc` перевірте за навантаженням `top`; апаратний `ENCODER=v4l2h264enc` експериментальний.

**Контрольна точка 8:** у `gs_mav.py` видно живий FC і (за налаштування стрімів) значення каналів зі зміною стіків TX12; відео з камери на хості.

## Етап 9. Випробування контуру (R-тести)

| № | Дія | Критерій проходження |
|---|---|---|
| R1 | рухати стіки TX12 (режим A, ELRS) | `RC_CHANNELS` у телеметрії міняються (`gs_mav.py`, лічильник повідомлень) |
| R2 | вимкнути TX12 | радіо-failsafe FC за `RC_FS_TIMEOUT`; фіксувати STATUSTEXT. Без гвинтів. НЕПЕРЕВІРЕНО конкретна реакція |
| R3 | відключити антену/зупинити `wifibroadcast@drone` на 10 с | відео й MAVLink зникають і **самі** відновлюються |
| R4 | `gs_mav.py --rc sweep --confirm-props-off` (резервний шлях, режим B) | ехо RC, RTT. **Узгодити з FC:** `RC_OVERRIDE_TIME` типово 3 с; ризик ArduPilot#32862 (SRC) |
| R5 | порівняти декодери `DECODER=avdec_h264` і `v4l2h264dec`, `top` | записати CPU і чи йде картинка (T8) |
| R6 | 10 хв безперервної роботи | `get_throttled` = `0x0`, помилки FEC/втрати не зростають |

Режим B вимагає USB-джойстика EdgeTX, а в цьому режимі **обидва RF-модулі вимкнені** («both internal and external RF modules should be turned off», SRC manual.edgetx.org). Тому режими A і B не змішувати в одній моделі.

**Контрольна точка 9 (завершення):** усі T1–T7, R1, R3, R5, R6 пройдені; R2 і R4 виконані й описані.

## Етап 10. Результати і наступні кроки

1. Заповнити `bench/RESULTS.md` і додати `vcgencmd get_throttled`, `ethtool -i`, `dmesg | tail`, версії ядра/EdgeTX/ELRS.
2. Передати результати: за ними знімаються позначки HW/НЕПЕРЕВІРЕНО в `docs/`.
3. Рішення, що чекають на вас: базова ОС, збирач образу, `/config`, Ruby (див. `docs/KNOWLEDGE.md`, розділ 7).
4. **Ще не покрито цим гайдом:** адаптивна лінія alink, msposd, затримка від скла до скла, RTL8814 (оцінка A/B у `docs/BENCH-HARDWARE.md`, розділ 9).

## Додаток: типові проблеми
| Симптом | Що робити |
|---|---|
| `bench.sh setup`: немає `/lib/modules/$(uname -r)/build` | `sudo apt full-upgrade`, перезавантажити (запущене ядро має збігатися із заголовками), повторити |
| `FAIL patched Realtek module loaded` | `dkms status`, `dmesg`; перевірити версію ядра проти матриці в `docs/PI-PORT.md` |
| `wifibroadcast@*` не стартує | `journalctl -u wifibroadcast@gs -e`: немає ключа/адаптера/пристрою FC |
| Багато FEC-помилок | коротший екранований USB-кабель, стабільне живлення, адаптер подалі від USB 3 (INF) |
| `v4l2*` декодер: чорний екран | `DECODER=avdec_h264` (або `avdec_h265`) |
| `kmssink` не відкривається | запуск з X/Wayland-сесії: `SINK="autovideosink sync=false"` |
| Pi 5 USB-пристрої відключаються | БЖ не 5 А: обмеження 600 мА (SRC) |
