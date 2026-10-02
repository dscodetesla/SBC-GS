# Що можна налагодити, віртуалізувати й змоделювати без заліза

Станом на 2026-10-02. Усе нижче **запущено** в контейнері сесії (Ubuntu 24.04.4, ядро `6.18.44-fc-v51`, root, чотири ядра, apt/pip/go/git через проксі політики; TLS не вимикався, проксі не обходився). Позначки: **RUN** виконано в цій сесії, вивід процитовано; **SRC/REPO** як у `CLAUDE.md`; **INF** висновок; **UNVERIFIED** не запускалось; **HW** потребує заліза. Вердикти: **WORKS тут**, **WORKS на runner** (лише там, де є підстава, інакше UNVERIFIED), **NEEDS HARDWARE**, **NOT FEASIBLE**.

Код симуляції: `tests/sim/` (див. `tests/README.md`). Єдина точка входу: `tests/sim/smoke.sh`.

## 1. Середовище контейнера (RUN)

| Факт | Вивід |
|---|---|
| PID 1 не systemd | `cat /proc/1/comm` → `process_api` |
| Модулів ядра немає | `zcat /proc/config.gz`: `# CONFIG_MODULES is not set`; `/lib/modules` відсутній; `modprobe mac80211_hwsim` → `FATAL: Module mac80211_hwsim not found in directory /lib/modules/6.18.44-fc-v51` |
| Є netns і veth | `ip netns add t1; ip netns exec t1 ip link add vA type veth peer name vB` → `rc=0` (`CONFIG_VETH=y`, `CONFIG_NET_NS=y`) |
| Немає netem | `# CONFIG_NET_SCH_NETEM is not set`, `tc` є |
| Немає KVM | `ls /dev/kvm` → `No such file or directory` (QEMU йде через TCG) |
| binfmt_misc треба змонтувати вручну | `mount -t binfmt_misc none /proc/sys/fs/binfmt_misc` + `cat /usr/lib/binfmt.d/qemu-aarch64.conf > …/register` (пакет сам запис не зробив) |
| Контейнерів немає | `docker ps` → `failed to connect to the docker API`; `podman` не встановлено |
| apt працює | `apt-get update` → `Fetched 9755 kB in 2s`; встановлено `qemu-user-static qemu-system-arm qemu-system-x86 debootstrap systemd-container udev iproute2 iw shfmt kmod cpio zstd busybox-static` |

## 2. Матриця технік

| # | Техніка | Вердикт | Ключовий доказ (RUN) | Відтворення | Що доводить | Чого не доводить |
|---|---|---|---|---|---|---|
| 1a | `qemu-user` + chroot arm64 Debian trixie | **WORKS тут**; runner: UNVERIFIED (потрібен `sudo`, binfmt) | `debootstrap --foreign` 57,7 с, `--second-stage` 5 хв 16 с; `chroot rootfs uname -m` → `aarch64`; `dpkg --print-architecture` → `arm64`; `bash 5.2.37 (aarch64-unknown-linux-gnu)` | розділ 3.1 | `gs/*.sh` без змін виконуються в arm64-userspace: `tests/run.sh` дав **210 ok, 2 DIFF** за 20 хв 11 с (нативно 212 ok за 49,8 с, повільніше в 24 рази) | поведінки на справжньому ядрі Pi, `/sys`, GPIO, systemd |
| 1b | `systemd-nspawn` | **NOT FEASIBLE тут** (хост не systemd); runner: UNVERIFIED | `systemd-nspawn -q -D rootfs --as-pid2 uname -m` → `Failed to open system bus: No such file or directory` | — | — | — |
| 1c | `qemu-system-aarch64 -M raspi3b` + справжнє ядро Pi OS | **WORKS тут** (TCG, ≈35 с стіни) | `Machine model: Raspberry Pi 3 Model B`, ядро `6.18.50+rpt-rpi-v8`, `SIM-BOOT uname=aarch64`; `raspi4b` немає: `qemu-system-aarch64 -M help \| grep -c raspi4` → `0` (QEMU 8.2.2) | розділ 3.2 | що справжнє ядро Pi OS завантажується, модулі `mac80211_hwsim` вставляються, monitor і ін'єкція працюють на arm64 | VideoCore/V3D/rpivid/brcmfmac/USB-живлення: у логу `bcm2835_vchiq … Could not initialize vchiq platform`, `mmc1: Timeout` |
| 1d | `qemu-system-aarch64 -M virt`, Pi 4 (`raspi4b`) | **NOT FEASIBLE** з QEMU 8.2.2 (`raspi4b` відсутня); `virt` не пробувалось (UNVERIFIED) | див. 1c | — | — | — |
| 2a | `mac80211_hwsim` на хості контейнера | **NOT FEASIBLE** | `CONFIG_MODULES` не задано (розділ 1) | — | — | — |
| 2b | `mac80211_hwsim` на runner ubuntu-24.04 (ядро Azure) | **NOT FEASIBLE за вмістом пакетів**; версію ядра runner не перевіряв (UNVERIFIED) | `dpkg -c linux-modules-extra-6.17.0-1022-azure` і `…6.8.0-1007-azure`: `mac80211.ko.zst` є, `mac80211_hwsim.ko` **немає** | — | — | — |
| 2c | hwsim у **QEMU-гості** зі стандартним ядром Ubuntu generic | **WORKS тут** (без root); runner: UNVERIFIED | `linux-modules-extra-6.8.0-146-generic` містить `mac80211_hwsim.ko.zst`; `tests/sim/qemu_hwsim.sh`: 5 PASS, 20–31 с | `tests/sim/qemu_hwsim.sh` | стек mac80211, monitor-режим, ін'єкція, радіотап RX, розділення каналів, справжні `wfb_tx`/`wfb_rx` без шимів | будь-який справжній драйвер, RF, USB |
| 2d | wfb-ng на veth без радіо | **WORKS тут** (root); runner: UNVERIFIED | `wfb_rx` на veth: `Error: unknown encapsulation on wfb0`; з `LD_PRELOAD`-шимом і ретранслятором `air_relay.py` лінк працює; `tests/sim/wfb_veth.sh` 12–18 с | `sudo tests/sim/wfb_veth.sh` | FEC і шифрування wfb-ng, порти й ключі, втрати/затримка (штучні) | радіо, MCS, реальний radiotap, ін'єкцію |
| 3a | GStreamer `videotestsrc`→x264/x265→RTP→UDP→декодер | **WORKS тут і на runner** (вже у CI `loopback`) | `./bench.sh loopback` 24 с: `PASS video h264 … PASS video h265`; `tests/sim/video_latency.py` | `tests/sim/smoke.sh` (шлях `video`) | склад пайплайна, caps, RTP, програмний декодер, затримка ПЗ | декодери Pi (`v4l2*dec`), `kmssink`, реальну камеру/кодер, jitter радіо |
| 3b | `v4l2loopback`/`vivid` | **NOT FEASIBLE тут**; у пакеті ядра Azure `6.17.0-1022` є `v4l2loopback.ko.zst` і `vivid.ko.zst`, але потрібні root і `modprobe` (runner: UNVERIFIED); у ядрі Pi `CONFIG_VIDEO_VIVID=m` (SRC конфіг із `.deb`) | `dpkg -c` (розділ 2b) | — | віртуальна вебкамера для `SOURCE=webcam` | — |
| 4a | Prebuilt ArduCopter SITL | **WORKS тут і (мережево) на runner** | `https://firmware.ardupilot.org/Copter/stable/SITL_x86_64_linux_gnu/arducopter` 7 231 384 байт, статичний ELF; прошивка `4.7.1` | `python tests/sim/sitl_probe.py` (49 с, три сценарії) | семантику справжнього ArduPilot (розділ 4) | політ із залізом, EKF на реальних датчиках, ELRS |
| 4b | `mavp2p` | **WORKS тут** | `go install github.com/bluenviron/mavp2p@latest` 25,8 с; `@v1.3.3` потребує Go ≥ 1.25 (`switching to go1.26.8`) | `go install github.com/bluenviron/mavp2p@v1.3.3` | що `gs-mavlink.sh` запускає справжній роутер і телеметрія проходить `fake_fc → mavp2p → gs_mav` | поведінку на реальному FC, цільову маршрутизацію при двох клієнтах |
| 4c | `mavlink-routerd` | **WORKS тут** | `git clone --recurse-submodules` 4,2 с, `meson`+`ninja` 14,2 с, коміт `2362c62`; `gs-mavlink.sh --print` → `mavlink-routerd -e 127.0.0.1:14570 -t 0 0.0.0.0:14550`; роутер: `Opened UDP Client … Opened UDP Server [6]CLI: 0.0.0.0:14550`, `gs_mav` отримав телеметрію | розділ 5 | що згенерований командний рядок синтаксично правильний | фільтри `BlockMsgIdIn` (потрібен конфіг-файл) |
| 4d | `pip install MAVProxy` | **UNVERIFIED** (`pypi.org` відповідає 200, не встановлював; для задач не потрібен) | — | — | — | — |
| 5a | `systemd-analyze verify` | **WORKS тут і на runner** (systemd 255) | без правок: `gs.service: Command /gs/gs.sh is not executable: No such file or directory`; після підміни `/gs/` на шлях чекаута (без root): порожній вивід, `rc=0`; мутація `ProtectHom=yes` → `Unknown key name 'ProtectHom'` | `smoke.sh` (шлях `static`) | синтаксис юнітів, імена директив | запуск, залежності від `network.target`, sandbox-директиви на цільовому systemd |
| 5b | `udevadm verify` | **WORKS тут** | `2 udev rules files have been checked. Success: 2 Fail: 0` | `udevadm verify gs/*.rules` | синтаксис правил | `udevadm test` на реальному пристрої, імена `wifi0`/`rpi0` на залізі |
| 5c | `shellcheck`, `bash -n`, `shfmt` | shellcheck і `bash -n` **WORKS**; `shfmt -d` придатний лише як інформація | `shfmt 3.8.0`: `gs/*.sh` 61 hunk, `bench/*.sh` 27, `tests/sim/*.sh` 17 (репозиторій не відформатований, у CI не вмикати) | — | — | — |
| 5d | Запуск юнітів (`systemctl start`) | **NOT FEASIBLE тут** (`systemd --user` → `Failed to connect to bus`); runner: UNVERIFIED | — | — | — | — |
| 6 | `tests/sim/smoke.sh` | **WORKS тут**, під `nobody` теж | 20 PASS за 10,3 с (без `wfb`, `qemu`); 22 PASS за 23,5 с з ними | розділ 6 | шляхи відео, MAVLink, RC-міст, роутер, лінт | усе з розділу «чого не доводить» |

## 3. Команди й вивід

### 3.1 arm64 userspace

```bash
apt-get install -y qemu-user-static debootstrap
mount -t binfmt_misc none /proc/sys/fs/binfmt_misc
head -1 /usr/lib/binfmt.d/qemu-aarch64.conf > /proc/sys/fs/binfmt_misc/register   # root
debootstrap --arch=arm64 --variant=minbase --foreign --include=bash,coreutils trixie rootfs https://deb.debian.org/debian
cp /usr/bin/qemu-aarch64-static rootfs/usr/bin/
chroot rootfs /debootstrap/debootstrap --second-stage      # "Base system installed successfully."
cp -r gs tests build bench rootfs/repo/ && chroot rootfs bash -c 'cd /repo && tests/run.sh'
```

Результат: 210 `ok`, 2 `DIFF`: `static/paths` (немає `python3` у minbase: зникають рядки `ok py_compile …`) і `static/fetch` (`git: command not found`). Обидва зумовлені складом образу, а не архітектурою (INF: після `apt install python3 git` мали б зійтись; повторного прогону не робив, 20 хв). Висновок: логіка `gs/*.sh` у golden-тестах від архітектури не залежить.

### 3.2 Справжнє ядро Pi OS у QEMU (raspi3b) + hwsim

```bash
curl -sS https://archive.raspberrypi.com/debian/dists/trixie/main/binary-arm64/Packages.gz | gunzip > Packages   # Packages.xz дає 404
# Filename: pool/main/l/linux/linux-image-6.18.50+rpt-rpi-v8_6.18.50-1+rpt1_arm64.deb  (32 322 668 байт)
dpkg -x linux-image-6.18.50+rpt-rpi-v8_*.deb ex; zcat ex/boot/vmlinuz-6.18.50+rpt-rpi-v8 > Image
# initramfs: busybox-static + iw + libnl (arm64 .deb із deb.debian.org) + rfkill, libarc4, cfg80211, mac80211, mac80211_hwsim (xz -d)
# + статичний aarch64 інжектор (aarch64-linux-gnu-gcc -static tests/sim/guest/inj.c)
qemu-system-aarch64 -M raspi3b -m 1G -kernel Image -dtb bcm2710-rpi-3-b.dtb -initrd initrd.gz \
  -append "earlycon=pl011,0x3f201000 keep_bootcon rdinit=/init loglevel=7 printk.devkmsg=on" -display none -serial file:q1.log
```

Вивід гостя (канал 1, 2,4 ГГц):

```
SIM-BOOT uname=aarch64 kernel=6.18.50+rpt-rpi-v8
insmod mac80211_hwsim ok  ->  hwsim0 wlan0 wlan1
SIM-MONITOR-OK            ->  type monitor ; ip link: link/[803]
INJECT sent=20 received_on_peer=20 rx_radiotap_len=26
```

Нюанси (RUN): без `earlycon` консоль мовчить; запис у `/dev/kmsg` обмежується швидкістю без `printk.devkmsg=on`; на 5 ГГц (`iw dev wlan0 set channel 36`, без HT) той самий інжектор без поля швидкості в radiotap дав `received_on_peer=0`; на x86-гості з `channel 36 HT20` він же дав 20/20. Причина розбіжності (non-HT проти HT20, версія ядра, швидкість за замовчуванням): UNVERIFIED. Збірка initramfs для arm64 лишається ручною (скрипту немає). Вбудованих модулів `rtw88_8814au`, `brcmfmac`, `mac80211_hwsim` у цьому ядрі достатньо, щоб перевіряти **наявність** драйвера (REPO: `.deb` містить `rtw88_8814au.ko.xz`, `mac80211_hwsim.ko.xz`, `brcmfmac.ko.xz`; `CONFIG_RTW88_8814AU=m`, `CONFIG_VIDEO_RPI_HEVC_DEC=m`).

### 3.3 wfb-ng: veth + ретранслятор

`wfb_tx` пише кадри через `AF_PACKET`, а `wfb_rx` відкидає все, крім `DLT_IEEE802_11_RADIO`, і викидає кадри з `TX_FLAGS` як «власну ін'єкцію» (`src/rx.cpp`, SRC коміт `2fe252b2f451c1ccfb16968e064fe1cdb18baaa0`). Тому `tests/sim/air_relay.py` підміняє radiotap на приймальний, а `tests/sim/pcapshim.c` (LD_PRELOAD) змушує `wfb_rx` прийняти veth.

```
$ sudo tests/sim/wfb_veth.sh                       # клон+збірка wfb-ng за пін-комітом + 3 сценарії, 18 с
  PROBE sent=200 recv=200 lost=0 ... p50=0.5 p95=1.4 max=10.0 ms     RELAY forwarded=302 dropped=0
  PROBE sent=200 recv=197 lost=3 ... p50=0.6 p95=50.5 max=118.4 ms   RELAY forwarded=271 dropped=31   (10 % втрат, FEC 8/12)
  PROBE sent=200 recv=61  lost=139 ...                                (негативний контроль, 60 % втрат)
```

`make all_bin` у wfb-ng: 7,1 с.

### 3.4 wfb-ng на hwsim у QEMU x86_64 (без шимів і без root)

```
$ tests/sim/qemu_hwsim.sh
PASS  guest booted (6.8.0-146-generic, TCG)
PASS  mac80211_hwsim: two radios switched to monitor mode
PASS  raw 802.11 injection wlan0 -> wlan1 (radiotap RX header present)
PASS  wfb_tx -> hwsim -> wfb_rx: 100 UDP datagrams delivered (>=90%)     UDPT sent=100 received=100
PASS  negative control: receiver on another channel hears nothing        UDPT sent=30 received=0
PASS  negative control: receiver with an unrelated keypair accepts nothing  UDPT sent=30 received=0
```

Ядро й модулі: `apt-get download linux-image-unsigned-6.8.0-146-generic linux-modules-… linux-modules-extra-…` (≈170 МБ, без встановлення). Мутації (копія): без `insmod mac80211_hwsim` три перевірки FAIL. Примітка: підміна `gs.key`→`drone.key` на приймачі **не** ламає лінк (обмін ключами Діффі-Геллмана симетричний, RUN), тому негативний контроль зроблено з незв'язаною парою ключів.

Про hwsim у документації wfb-ng: у клоні `svpcom/wfb-ng@2fe252b` слово `hwsim` не зустрічається (`grep -ri hwsim` порожньо, RUN); `wfb_ng/tests/test_tuntap.py` використовує лише netns для `wfb_tun`. Що upstream офіційно підтримує hwsim: **не підтверджено**, наш прохід це не upstream-практика, а власна збірка.

## 4. ArduPilot SITL: що виміряно (RUN, прошивка 4.7.1 `flight_sw_version 0x40701ff`)

`python tests/sim/sitl_probe.py` (три сценарії, 49 с; `--check` робить очікування вимогами). Вивід:

```
OBS FS_GCS_ENABLE=0.0  FS_GCS_TIMEOUT=5.0  RC_OVERRIDE_TIME=3.0  MAV_GCS_SYSID=255.0  MAV_GCS_SYSID_HI=0.0  RC_FS_TIMEOUT=1.0
OBS SYSID_MYGCS_exists=False    OBS firmware=4.7.1
OBS chan1_while_sysid77_overrides=1500     (baseline 1500, override з sysid 77 проігноровано)
OBS chan1_while_sysid255_overrides=1700    OBS override_expiry_s=2.8     OBS chan1_after_expiry=1500
OBS failsafe_when_only_hb255_sends=False   OBS failsafe_when_only_hb125_sends=True after 1.1s
OBS failsafe_when_only_manual_sends=False  OBS failsafe_text_timeline=['GCS Failsafe', 'GCS Failsafe Cleared']
```

Закрито відкриті питання з `docs/MAVLINK-ROUTER.md` для цієї версії: типове `FS_GCS_ENABLE` = **0**; `SYSID_MYGCS` відсутній, є `MAV_GCS_SYSID` = 255; heartbeat від sysid 125 (mavp2p) **не** рахується як GCS (підтверджено), `MANUAL_CONTROL` від 255 утримує failsafe (підтверджено). Спостереження поза документацією: прапорець «GCS Failsafe» знімається самим повідомленням `GCS Failsafe Cleared` після відновлення heartbeat (дія над режимом, за SRC gcs-failsafe.html, лишається); при розброєному апараті прапорець і текст з'являються теж (дії немає); на землі при озброєнні було `GCS Failsafe - Disarming` (одноразовий прогін, у скрипт не включено). Справжній `tx12_bridge.py --input sweep` проти SITL (`tcp:127.0.0.1:5760`): `chan1` 1787→1249, після виходу моста `1500` (RUN, ручний прогін).

### Порівняння `bench/fake_fc.py` і `tests/sim/apm_fc.py` (модель `apm_model.py`)

| Поведінка ArduPilot | `fake_fc.py` | `apm_fc.py` |
|---|---|---|
| Лише GCS-sysid (`MAV_GCS_SYSID`/`_HI`) може робити override | ні, приймає від будь-кого | так (SITL) |
| heartbeat лише від GCS-sysid (125 не рахується) | жорстко 255 | так (SITL) |
| `MANUAL_CONTROL` як ознака GCS | ні | так (SITL) |
| `FS_GCS_ENABLE=0` за замовчуванням (failsafe відсутній) | ні, завжди увімкнено | так (SITL), `--fs-gcs-enable` |
| failsafe не активний, поки GCS жодного разу не був | так | так (SRC) |
| Дія failsafe і «липкий» режим | лише текст | режим RTL/LAND, не повертається (SRC) |
| Значення 0 звільняє канал, 65535 ігнорує | ні | так (SRC) |
| Проканальне закінчення override | ні | так |
| `RC_OVERRIDE_TIME` 0 і від'ємне | ні | так (SRC) |
| Повернення до застарілого значення звичайного RC після спливу (#32862) | ні | так, `rc_input` (INF) |
| Радіо-failsafe, коли міст єдине джерело RC | ні | так (INF з SRC radio-failsafe) |

Розбіжності моделі з SITL: протермінування override 3,0 с у моделі проти 2,8 с у SITL; відповідність моделі SITL перевірена лише за переліченими спостереженнями; режим після failsafe та радіо-failsafe у SITL не перевірялись (UNVERIFIED). Цільова маршрутизація `RC_CHANNELS_OVERRIDE` (`target_system`) у `apm_fc.py` реалізована спрощено.

## 5. Відеоланцюжок

- `bench/video-src.sh` (videotestsrc, `timeoverlay`, x264/x265, `rtph264pay`/`rtph265pay`, `udpsink`) і `bench/video-rx.sh` (`udpsrc`, depay, parse, декодер, `fakesink` + `progressreport`) вже відтворюють пайплайн `gs/stream.sh` з програмним декодером. Смоук перевіряє ≥ 3 `progressreport` за 6 с.
- `tests/sim/video_latency.py` (системний python з `python3-gi`; у venv з pymavlink `gi` немає) штампує кадр на вході кодера і на виході декодера в одному процесі: `LATENCY codec=h264 frames=90/90 lost=0 min=3 p50=5 p95=10 max=30 ms`, `h265 … min=6 p50=8 p95=15 max=41 ms` (x86, програмні кодек, лупбек). Це контроль регресій (поріг p95 ≤ 500 мс), **не** цифра для Pi.
- Мутація: `enc_name="H264"`→`"H265"` у копії `video-rx.sh` → `FAIL … 0 progress reports`. Не виявляється: інший `pt=` у `video-src.sh` (приймальні caps не містять `payload`, RUN, мутація пройшла) і відмінність `config-interval`.

## 6. `tests/sim/smoke.sh`: шляхи, fallback і налагоджувальні гачки

Запуск: `tests/sim/smoke.sh`; `SMOKE_STRICT=1` перетворює SKIP на FAIL (CI); `SMOKE_ONLY="video mavlink"`; `SMOKE_QEMU=1` вмикає `qemu_hwsim.sh`; `WFB_NG_DIR=<зібране дерево>` або `SMOKE_WFB=1` вмикає `wfb_veth.sh` (root). `PY` з `pymavlink` (за замовчуванням `.venv`), `GST_PY` із `gi`.

| Шлях | Перевірки (кількість) | Fallback (PROPOSAL, INF) | Гачок налагодження |
|---|---|---|---|
| static | `bash -n`, ast, shellcheck `-S warning`, `systemd-analyze verify`, `udevadm verify`, `validate.sh`, `gs-mavlink.sh --print` на 9 фікстурах (7) | — | кожне окремо з виводом у `$tmp`; `SMOKE_KEEP_LOGS=<dir>` зберігає логи при FAIL |
| model | 23 одиничні перевірки `apm_model` | — | `python tests/sim/test_apm_model.py` |
| mavlink | телеметрія й RC-луна, таймаути `fake_fc`, міст→`apm_fc`, dead-man (спливання override за 1,6 с, що < 3 с таймауту FC), чужий sysid ігнорується (8) | RC лише резерв; основний канал ELRS з апаратним failsafe (`docs/CHAINS.md`); міст має lock і dead-man | `tx12_bridge.py --duration`, події `EVENT <t>s …` у `apm_fc.py`, `gs_mav.py --selftest` |
| video | `video-src.sh`→`video-rx.sh` h264; затримка h264 і h265 (3) | якщо `v4l2*dec` не стартує: `DECODER=avdec_h264`; якщо немає дисплея: `SINK=fakesink` (вже у `video-rx.sh`) | `PROGRESS=1`, `GST_DEBUG`, `tests/sim/video_latency.py --codec h265` |
| router | `gs-mavlink.sh` запускає справжній `mavp2p`, телеметрія проходить (1) | пряма видача wfb-ng `udp:14550` на GCS без роутера | `gs-mavlink.sh --print`, `mavp2p --print` |
| wfb | `wfb_veth.sh`: чисте повітря, 10 % втрат, негативний контроль (1 рядок) | відмова на `standalone`-режим wfb-ng замість `cluster` (`gs.conf:77-89`) | рядки `PKT`/`RX_ANT` у логу `wfb_rx`, `KEEP_TMP=1` |
| qemu | `qemu_hwsim.sh` (1 рядок, 6 перевірок усередині) | — | серійний лог гостя (`tests/sim/guest/init.sh`) |

Стабільність (RUN): 5 запусків поспіль під root — `20 passed, 0 failed` у кожному, 10,33–10,36 с; 3 запуски під `nobody` (через `setpriv`, копія репозиторію) — `20 passed, 0 failed`; `qemu_hwsim.sh` під `nobody`: 3 з 3 `ALL PASS` (20–31 с, перший із завантаженням 170 МБ); `shellcheck -x -S warning tests/sim/*.sh` без зауважень. Під `nobody` шляхи `wfb` і `qemu` лишаються SKIP, якщо не передано кеш (`SIM_CACHE`), а `wfb` вимагає root.

Мутаційні перевірки на копії (кожна дала FAIL у відповідному шляху): `bash -n` (синтаксична помилка у `fan.sh`), `ProtectHom=yes` у `gs-mavlink.service`, зламане правило udev, порт `99999` приймається в `gs-mavlink.sh`, видалений ключ у `rpi4/board.conf`, `udps:`→`udpx:` у `gs-mavlink.sh` (роутер не стартує), dead-man ніколи не спрацьовує (`now - ts <= 3600`: `dead-man … 5.8 s after start`), модель приймає будь-який sysid (модель + міст), `video-rx.sh` з хибним кодеком, `fake_fc` не виявляє GCS-таймаут, `air_relay.py` відкидає все або не міняє radiotap, `mac80211_hwsim` не завантажено. Не виявлено: `pt=` у `video-src.sh` (див. розділ 5). Перша спроба мутації dead-man через значення за замовчуванням `--deadman-ms` не виявилась, бо смоук передає `--deadman-ms 300` явно (мутація була недійсною, замінена на логіку в `tx12_bridge.py`).

## 7. Що це доводить і чого не доводить

| Тема | Симуляція доводить | Симуляція **не** доводить |
|---|---|---|
| **Ін'єкція в радіо** | wfb-ng коректно збирає й розбирає кадри, FEC відновлює втрати, ключі автентифікують, канали розділені (hwsim у QEMU, veth) | Що **драйвер** (rtl88xxau/8812eu/rtw88_8814au) входить у monitor, ін'єкує з потрібними MCS/потужністю й не губить кадри на USB. hwsim — не драйвер: ін'єкція без швидкості в radiotap на 5 ГГц non-HT дала 0 кадрів, на HT20 20 з 20 (RUN, розділ 3.2), тож поведінка залежить від стека й драйвера. Регулювання потужності, `iw reg`, DFS, TX-PWR: HW |
| **DKMS на справжньому ядрі** | Лише наявність готових модулів у пакеті ядра (`rtw88_8814au.ko.xz` у `6.18.50+rpt-rpi-v8`) | Збірку `svpcom/rtl8812au` коміт `6e75916…` і `libc0607/rtl88x2eu` проти заголовків `linux-headers-rpi-v8`/`-2712` (можна було б спробувати збірку в arm64-chroot з заголовками, **не пробував**: UNVERIFIED), `dkms autoinstall` після оновлення ядра, Secure Boot. Хост-ядро x86 не містить заголовків ядра Pi |
| **Затримка відео** | Внесок ПЗ: пайплайн, кодування, RTP (`p95` 10–18 мс на x86 лупбеку) | Скляна затримка: декодер Pi (`rpivid`, `v4l2slh265dec`), `kmssink`/vsync, камера й кодер OpenIPC, jitter-буфер, FEC-блок 8/12 (при 10 % втрат p95 вже 50 мс, RUN), канал. Потрібен вимір «від фотона до пікселя» (HW) |
| **Поведінка RF** | Ні: канал в hwsim ідеальний (0 % втрат без `air_relay.py`), втрати й затримка в `air_relay.py` задані вручну, **не** модель Wi-Fi | Дальність, інтерференція, багатопроменевість, чутливість RSSI, діверсіті, поведінка при перевантаженні приймача на столі |
| **Живлення й USB** | Нічого | Просідання 5 В, `usb_max_current_enable`, throttling (`get_throttled`), лімити Pi 5 (1,6 А з БЖ 5 А), перегрів, шум USB 3 на 2,4 ГГц; кабелі й помилки FEC. QEMU не емулює живлення, VideoCore, USB-контролер dwc2/xhci Pi |
| **MAVLink/ArduPilot** | Семантика override/failsafe для 4.7.1 (SITL), що `gs-mavlink.sh` запускає справжні роутери, що міст має dead-man | Конкретну прошивку польотного контролера, UART/USB-з'єднання, `SERIALx_*`, ELRS, EKF на реальних датчиках, поведінку `mavp2p` на реальному FC при двох клієнтах |
| **systemd** | Синтаксис юнітів | Запуск сервісів, порядок завантаження, sandbox `DynamicUser`/`ProtectSystem` на цільовому systemd, `gs-init.service` на справжньому диску (GPT, `/boot/dtbo`, overlays) |
| **Скрипти Radxa/Pi** | `gs/*.sh` виконуються незмінно в arm64-userspace (210/212 golden) | GPIO (`gpiofind` v1/v2), `/sys/class/thermal`, PWM, OTG (`configfs`/`libcomposite`: у ядрах Azure немає `libcomposite`, у generic не перевіряв), `rk3566` DTS |

## 8. Пріоритетний список для CI

1. **Вже додано в `.github/workflows/ci.yml`:** `tests/sim/*.sh` у shellcheck, `tests/sim/*.py` у `py_compile`, job `sim-smoke` (`SMOKE_STRICT=1`, шляхи `static model mavlink video router`, ≈10 с тесту + встановлення). Пакети перевірені на Ubuntu 24.04 цього контейнера; **на справжньому GitHub runner job пройшов зеленим** (REPO, run 37043623138 на коміті `4c6069a`: `Install tools`, `setup-go`, збірка `mavp2p@v1.3.3`, venv, `Smoke test` ≈10 с, усі кроки success).
2. **Наступним (advisory, `continue-on-error: true`):** `sudo tests/sim/wfb_veth.sh` (apt: `libsodium-dev libpcap-dev libevent-dev g++ iproute2`; потрібен `veth` у ядрі runner; UNVERIFIED) і `SMOKE_QEMU=1 tests/sim/qemu_hwsim.sh` (apt: `qemu-system-x86 busybox-static iw cpio zstd file libc6-dev`, ≈170 МБ завантаження, кеш `~/.cache/sbc-gs-sim` через `actions/cache`; UNVERIFIED).
3. `python tests/sim/sitl_probe.py --check` раз на день або вручну: качає 7 МБ бінарник, ловить зміну семантики ArduPilot (`FS_GCS_ENABLE`, sysid), 49 с.
4. arm64 chroot із `tests/run.sh` (`docker/setup-qemu-action` або `qemu-user-static`): 20 хв, лише за розкладом (nightly), перед віхами M3/M6; додати `python3 git` до образу.
5. Не додавати: `shfmt -d` (репозиторій не відформатований), `udevadm test`, `systemctl start` (потребує systemd як PID 1).

## 9. Відкрите (UNVERIFIED)

- Чи видно `mac80211_hwsim` у ядрі конкретного runner (тут лише аналіз пакетів Azure; ймовірно ні).
- Запуск `sim-smoke`, `wfb_veth.sh`, `qemu_hwsim.sh` на справжньому GitHub runner (бездоганність тут ≠ на runner).
- `wfb-ng` для arm64 на hwsim під raspi3b (бінарники не збирались для arm64; перевірено лише inj-інжектор).
- DKMS-збірка драйверів проти заголовків ядра Pi в arm64-chroot.
- Поведінка SITL: режим після failsafe, радіо-failsafe, озброєний політ; MAVProxy.
- `-M virt` із ядром Pi; `raspi4b` (потрібен QEMU новіший за 8.2.2; з якої версії, не з'ясовано).

## 10. Файли

`tests/sim/smoke.sh`, `wfb_veth.sh`, `qemu_hwsim.sh`, `guest/{init.sh,inj.c,udpt.c}`, `air_relay.py`, `udp_probe.py`, `pcapshim.c`, `video_latency.py`, `apm_model.py`, `apm_fc.py`, `test_apm_model.py`, `sitl_probe.py`.

**Безпека:** `apm_fc.py`, `sitl_probe.py` і `fake_fc.py` лише для симулятора й столу без гвинтів; не підключати до реального апарата (`CLAUDE.md`). Примусове озброєння (`21196`) у `sitl_probe.py` виконується лише в SITL.
