# Віртуальний DKMS: драйвери Realtek проти справжніх заголовків ядра Pi OS

Станом на 2026-10-03. Код: `tests/sim/dkms/` (`run.sh`, `guest-init.sh`, `manifest.txt`, `expect.txt`, `patches/`). Позначки: **RUN** виконано в цій сесії, вивід процитовано; **REPO/SRC/INF/HW/UNVERIFIED** як у `CLAUDE.md`.

Пріоритет за поточною топологією (AIR = RTL8812EU WiFiLink2 з вбудованою прошивкою, GS = RTL8812AU на Pi 5): (1) `svpcom/rtl8812au` на `rpi-2712` (Pi 5, сторінки 16K) і `rpi-v8`; (2) 8812EU/88x2eu; (3) 8814au.

## 1. Що саме перевірено

| Що | Значення |
|---|---|
| Ядра (RUN, `archive.raspberrypi.com`) | bookworm `6.12.109+rpt` (пакет `1:6.12.109-1+rpt1`), trixie `6.18.50+rpt` (`1:6.18.50-1+rpt1`); смаки `rpi-v8` (Pi 4, `CONFIG_ARM64_4K_PAGES=y`) і `rpi-2712` (Pi 5, `CONFIG_ARM64_16K_PAGES=y`) |
| Пакети | `linux-headers-<rel>-{common-rpi,rpi-v8,rpi-2712}`, `linux-kbuild-<rel>`, `linux-image-<rel>-{rpi-v8,rpi-2712}`; усі sha256 і розміри у `manifest.txt` (збігаються з `Packages` архіву) |
| Драйвери (git, SHA у `run.sh` і `manifest.txt`) | `svpcom/rtl8812au` `6e75916416de1dce5ecd37f824896bebf96aaf8f` (пін `bench/lib.sh`, збігається); `morrownr/8814au` `1840d7b23bf2350a3e9e22448a93c251d2fec73c`; `svpcom/rtl8812eu` `48e6e449e089fa954e4e15079bd864039e2960da` (`install-driver.sh`, у репозиторії НЕ був закріплений); `libc0607/rtl88x2eu-20230815` `12977e4013ecafbae993b3b08d46bff87d6d7c67` (його клонує `build/build.sh`, теж був незакріплений) |
| Компілятор | `aarch64-linux-gnu-gcc-12` (Ubuntu 12.4.0) для 6.12 і `gcc-14` (14.2.0) для 6.18: та сама мажорна версія, що в `.kernelvariables` Pi OS (Debian 12.2 / 14.2); мінорна версія інша (INF, на результат не впливає) |
| Хости (RUN) | `archive.raspberrypi.com` 200, `deb.debian.org` 200, `git fetch` з `github.com` працює; сторінки `github.com` (400), `codeload.github.com` (403), `api.github.com` (403) заблоковані політикою проксі, не обходилось |

### Команда збірки (та сама, що виконує DKMS на Pi: `dkms.conf` svpcom, `dkms-make.sh` morrownr)

```
make -j4 ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
     KSRC=<WORK>/kt/6.18.50+rpt/usr/src/linux-headers-6.18.50+rpt-rpi-2712 KVER=6.18.50+rpt-rpi-2712
```
`uname -m` у Makefile драйверів впливає лише на значення `ARCH ?=`, яке перекрито командним рядком; платформні прапорці залежать від `CONFIG_PLATFORM_*`, не від `uname` (REPO, прочитано Makefile). Тобто крос-збірка еквівалентна рідній.

### Обхідні кроки для крос-збірки на x86_64 (не зміни драйверів)
1. Makefile смаку включає спільний за **абсолютним** шляхом `/usr/src/...` (а 6.18 ще й `KBUILD_OUTPUT=/usr/src/...`): `run.sh` переписує їх на розпаковане дерево (`sed`, лише в розпакованих заголовках, у `$WORK`).
2. Готові інструменти kbuild (`modpost`, `fixdep`, ...) у `linux-kbuild` це бінарники **aarch64**: `run.sh` обгортає кожен ELF aarch64 скриптом `qemu-aarch64-static -0 "$0" -L <sysroot> "$0.aarch64"`. `-0` обов'язковий: `modpost` складає ім'я `modpost.real-lsb-64` з `argv[0]`; без нього збірка падала на кроці MODPOST з кодом 255 без повідомлення (RUN). Sysroot: glibc з `libc6-arm64-cross` + шість arm64-бібліотек (`libelf1t64`, `libssl3t64`, `zlib1g`, `libzstd1`, `liblzma5`, `libbz2`) з `deb.debian.org`. binfmt_misc і root не потрібні.

## 2. Матриця збірки (RUN, чистий прогін `run.sh --build`, 16 з 16 збігаються з `expect.txt`)

`imports` = символи, які модуль імпортує (`modprobe --dump-modversions`); `crc≠` = скільки CRC не збігаються з `Module.symvers` заголовків (0 = усі збігаються). Vermagic скрізь `<rel>-<flavour> SMP preempt mod_unload modversions aarch64` і **збігається** з vermagic модуля `cfg80211.ko`, узятого з `linux-image` того ж ядра.

| Драйвер | 6.12.109 v8 (4K) | 6.12.109 2712 (16K) | 6.18.50 v8 (4K) | 6.18.50 2712 (16K) | Патчі |
|---|---|---|---|---|---|
| **svpcom rtl8812au** (`88XXau_wfb.ko`) | PASS, 4 warn, imports 171, crc≠ 0 | **PASS**, 4 warn, 171, 0 | PASS, 25 warn, 174, 0 | **PASS**, 25 warn, 174, 0 | 0001 (лише 6.12) |
| svpcom rtl8812eu (`8812eu.ko`) | PASS (з 0002), 6 warn, 198, 0 | PASS (з 0002), 6, 198, 0 | PASS, 6, 200, 0 | PASS, 6, 200, 0 | 0002 (лише 6.12) |
| libc0607 rtl88x2eu (`8812eu.ko`) | PASS, 6, 197, 0 | PASS, 6, 197, 0 | PASS, 6, 199, 0 | PASS, 6, 199, 0 | немає |
| morrownr 8814au (`8814au.ko`) | PASS, 0, 177, 0 | PASS, 0, 177, 0 | PASS, 0, 179, 0 | PASS, 0, 179, 0 | немає |

Розмір (6.18 v8, після `strip --strip-debug`): 88XXau_wfb 3 638 656 Б (58 USB-alias, `depends: cfg80211`), svpcom 8812eu 3 109 360 Б (3 alias), rtl88x2eu 3 109 192 Б (3 alias), 8814au 5 942 144 Б (14 alias). `version`: `v5.2.20.2_28373.20190919`, `v5.15.0.1-249-g9245f8bd9...`, `v5.8.5.1_35583.20191029`.

Підсумок попередження за видом (RUN, логи): rtl8812au 6.18: `-Wvla-larger-than=` ×20, `-Wrestrict` ×4, `-Wenum-int-mismatch` ×1; 88x2eu/8812eu: `-Wempty-body` ×3, `-Wenum-conversion`, `-Wmisleading-indentation`, `-Wstringop-overread` по ×1. `-Werror` ядро Pi не вмикає (`# CONFIG_WERROR is not set`); `FORTIFY_SOURCE` вимкнено в обох смаках. 16K-сторінки не дали жодної помилки чи попередження, пов'язаної з `PAGE_SIZE` (RUN: порівняння логів 2712 з v8, після нормалізації шляхів відрізняється лише порядок рядків паралельної збірки).

## 3. Знахідки

### 3.1 svpcom/rtl8812au на 6.12.109: невірна сигнатура `set_monitor_channel` (REPO + RUN)
Збірка проходить, але з попередженням:
```
ioctl_cfg80211.c:7871:32: warning: initialization of 'int (*)(struct wiphy *, struct net_device *, struct cfg80211_chan_def *)' from incompatible pointer type ...
  7871 |         .set_monitor_channel = cfg80211_rtw_set_monitor_channel,
```
Заголовки Pi 6.12.109 (`include/net/cfg80211.h:4701`) уже мають форму `(wiphy, net_device *, chandef)` (бекпорт), а драйвер перемикає сигнатуру лише з `6.13`. Модуль **лінкується і завантажується**, але під час `iw dev ... set channel` cfg80211 передасть `net_device` на місце `chandef` (INF з коду; наслідок на залізі UNVERIFIED/HW). Це саме те, що виправив libc0607 (`6.12.101`, `docs/PI-PORT.md` розд. 4). Патч `patches/0001-...` змінює умову на `>= 6.12.101`; після нього `warning` зникає (5 → 4 попереджень). На 6.18 патч не потрібен.

### 3.2 svpcom/rtl8812eu на 6.12.109: збірка ПАДАЄ без патча (RUN)
```
ioctl_cfg80211.c:11095:32: error: initialization of 'int (*)(struct wiphy *, struct net_device *, struct cfg80211_chan_def *)' from incompatible pointer type 'int (*)(struct wiphy *, struct cfg80211_chan_def *)' [-Werror=incompatible-pointer-types]
make: *** [Makefile:2629: modules] Error 2
```
Тут `-Werror=incompatible-pointer-types` ядра перетворює це на помилку. З `0002` збірка проходить. Це стосується незакріпленого `install-driver.sh DRIVER=8812eu` на bookworm: **без патча не збереться**. libc0607/rtl88x2eu (`12977e4`) цього не має.

### 3.3 Вбудований rtw88 у ядрах Pi (REPO, `config-*` у `linux-image` і `.config` заголовків)
| Ядро | `RTW88_8814AU` | `RTW88_8812AU` | `RTW88_8821AU` | Модулі `rtw88_*` в образі |
|---|---|---|---|---|
| 6.12.109 (v8 і 2712) | **немає** | **немає** | немає | `8723d/de/du/x, 8821c/ce/cu, 8822b/be/bu, 8822c/ce/cu, core, pci, usb` (16) |
| 6.18.50 (v8 і 2712) | `=m` | `=m` | `=m` | додатково `8812a, 8812au, 8814a, 8814au, 8821a, 8821au, 88xxa` (22) |

Тобто твердження «mainline з v6.15» підтверджене для Pi: на bookworm 6.12.109 вбудованого 8814au **немає**, на trixie 6.18.50 є (і 8812au теж).

### 3.4 Конфлікт ідентифікаторів USB на 6.18 (RUN, `modinfo -F alias`, порівняння VID:PID)
- `rtw88_8812au` заявляє 34 VID:PID, і **усі 34** є серед 58 alias `88XXau_wfb` (svpcom). `rtw88_8821au`: 23 з 26. `rtw88_8814au`: 14 з 14 збігаються з morrownr `8814au`.
- `bench/install-driver.sh` чорним списком блокує `88XXau 8812au rtl8812au rtl88x2bs` і `rtw88_8814au` лише для `8814au-morrownr`; `rtw88_8812au` і `rtw88_8821au` **не блокує**. На Pi з Trixie обидва драйвери претендують на один адаптер; який виграє, залежить від порядку (UNVERIFIED, потребує адаптера). Рекомендація: додати `blacklist rtw88_8812au` і `rtw88_8821au` у `/etc/modprobe.d/wfb.conf` для Trixie (PROPOSAL; я цей файл не чіпав).
- svpcom `8812eu`/`88x2eu` мають 3 alias, з `rtw88_8812au` не перетинаються.

## 4. Тест завантаження у справжньому ядрі Pi (QEMU raspi3b, RUN)

`run.sh --load`: ядро `vmlinuz` із `linux-image-<rel>-rpi-v8`, `bcm2710-rpi-3-b.dtb` з того ж пакета, initramfs із arm64 busybox, залежності (`rfkill`, `cfg80211`) з того ж образу, потім `insmod` кожного зібраного модуля, `rmmod`, негативний контроль. Вивід (скорочено):
```
[k612v8] SIM-DKMS-BOOT uname=aarch64 kernel=6.12.109+rpt-rpi-v8
[k612v8] SIM-DKMS-DEP rfkill ok / cfg80211 ok
[k612v8] SIM-DKMS-INSMOD 8814au rc=0   | rtl8812au rc=0 | rtl8812eu rc=0 | rtl88x2eu rc=0
[k612v8] SIM-DKMS-NEG-OK refused: insmod: can't insert '/mods/WRONGKERNEL.ko': invalid module format
[k618v8] SIM-DKMS-BOOT uname=aarch64 kernel=6.18.50+rpt-rpi-v8
[k618v8] SIM-DKMS-INSMOD 8814au rc=0   | rtl8812au rc=0 | rtl8812eu rc=0 | rtl88x2eu rc=0
[k618v8] SIM-DKMS-NEG-OK ...
```
dmesg: `88XXau_wfb: loading out-of-tree module taints kernel.`, `usbcore: registered new interface driver rtl88xxau_wfb`, при `rmmod` `deregistering interface driver`; 8814au: `RTW: module init ret=0`. Негативний контроль (модуль, зібраний для іншого релізу): `88XXau_wfb: disagrees about version of symbol module_layout` → відмовлено. Два `WARNING ... ioremap.c:27` у логу виникають на 2,5-2,8 с, до запуску init, це особливість QEMU raspi3b (не від модулів).
Цей же прогін показує: svpcom8812eu і rtl88x2eu реєструють один і той самий USB-драйвер `rtl88x2eu`, тому одночасно їх завантажити не можна (різні варіанти одного коду).

**rpi-2712 (16K) під raspi3b не завантажується** (QEMU raspi3b лише 4K, смак 2712 для BCM2712): для Pi 5 доведено тільки збірку, лінкування й CRC, **не** виконання. Це UNVERIFIED на 16K-рантаймі.

## 5. Що це доводить і чого не доводить

**Доводить (RUN):** код компілюється і лінкується проти справжніх заголовків Pi (4K і 16K, 6.12.109 і 6.18.50); `modpost` розв'язав усі символи; vermagic збігається з пакетом `linux-image`; CRC `modversions` усіх імпортованих символів збігаються з `Module.symvers` (і CRC `cfg80211` з образу збігаються з тим же `Module.symvers`: 195 з 195); справжнє ядро Pi 4K приймає модулі та проходить init до `usb_register()`; факти конфігурації ядра (розд. 3.3); помилки збірки, яких не видно без таких заголовків (3.2).

**Не доводить:** USB-probe, прошивку/EEPROM, monitor mode, ін'єкцію, таймінги, потужність, поведінку на 16K-сторінках під час виконання, поведінку `set_monitor_channel` (3.1) на залізі, DKMS-обгортку (`dkms-install.sh`, автозбірку при оновленні ядра, `update-initramfs`), сумісність із прошивкою WiFiLink2, а також що модуль на Pi 5 не впаде при зверненні до пристрою. Збірку виконано gcc Ubuntu, не Debian (мінорна версія відрізняється).

## 6. Що це означає для понеділка

**Уже не залежить від заліза:** (а) пін `svpcom/rtl8812au 6e75916` збирається під Pi 5 `rpi-2712` 6.12.109 і 6.18.50 і під Pi 4 `rpi-v8` (GS-адаптер, пріоритет); (б) 6.18.50 `v8` приймає модуль; (в) без патча 0001 на bookworm-6.12 збірка проходить із хибною сигнатурою (застосувати до першого запуску на bookworm); (г) `svpcom/rtl8812eu` на bookworm без 0002 не збирається; (д) на Trixie з'являється вбудований `rtw88_8812au` з повним перетином ID: чорний список.

**Лишається підтвердити на Pi 4/5:** `insmod` на справжньому 16K-ядрі Pi 5 (`dmesg`, `ethtool -i` без версії драйвера), `iw ... set channel` у monitor на 6.12.109 з патчем 0001 і без, ін'єкція, хто виграє в конфлікті `rtw88_8812au`/`88XXau_wfb` на Trixie, RTL8812EU WiFiLink2 на AIR (прошивка вбудована: цей шар її не торкається), точна версія ядра після `apt upgrade` (перезапустіть `--fetch` з новими версіями в `run.sh`, зараз пін 6.12.109/6.18.50).

## 7. UNVERIFIED
1. Рантайм на 16K (Pi 5) та на реальному Pi 4.
2. Наслідок невірної сигнатури `set_monitor_channel` на 6.12.109 (оцінка з коду, не виміряна).
3. Який драйвер займе адаптер при двох кандидатах на Trixie.
4. Походження коду `morrownr/8814au` (`CLAUDE.md`/`docs/BENCH-HARDWARE.md` розд. 9): SHA записано, зміст не аудитовано; `8814au` wfb-ng не підтримує.
5. `libc0607/rtl88x2eu-20230815` і `svpcom/rtl8812eu` у `build.sh`/`install-driver.sh` не закріплені: цей пін у `run.sh` лише для відтворюваності тесту, репозиторій скриптів не змінено.
6. Збірка Debian-gcc замість Ubuntu-gcc.

## 8. Як відтворити

```bash
tests/sim/dkms/run.sh --check          # офлайн, <1 с: manifest, expect.txt, patches (можна з smoke.sh)
tests/sim/dkms/run.sh --fetch          # мережа, ~152 МБ у $WORK, 26 с
tests/sim/dkms/run.sh --build          # 16 збірок; ~35 хв на 4 ядрах (ONLY=rtl8812au для одного; NOPATCH=1 для чистих пінів)
tests/sim/dkms/run.sh --load           # ~1 хв, лише rpi-v8
```
Root потрібен лише для одноразової `apt-get install make file dpkg kmod xz-utils python3 git qemu-user-static qemu-system-arm cpio gcc-12-aarch64-linux-gnu gcc-14-aarch64-linux-gnu`. `$WORK` = `SIM_DKMS_WORK` (типово `${TMPDIR:-/tmp}/sim-dkms`, ~0,85 ГБ після збірки, ~1,9 ГБ з деревами `KEEP=1`). Перевірено від `nobody` (`setpriv`): `--check` і `ONLY=8814au --build` (4 хв 32 с) проходять. Код виходу: 0, 1 (розбіжність із `expect.txt`), 2 (використання), 77 (немає передумови).
`expect.txt` змінювати лише з доказом у цьому документі. Патчі: `patches/series` (ім'я, драйвер, glob id ядра), у заголовку кожного `# Target:` з SHA.
