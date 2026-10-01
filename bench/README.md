# Стендовий прототип: «повітряний» вузол + GS на двох Raspberry Pi 4

Мета: фізично зібрати на столі повний ланцюжок з `docs/CHAINS.md` і зняти вимірювання, які закриють відкриті питання.

```
Pi 4 «AIR» (емуляція дрона)                              Pi 4 «GS» (наземна станція)
 video-src.sh ─ RTP/UDP :5602 ─┐                    ┌─ UDP :5600 ─ video-rx.sh ─ декодер ─ HDMI
                               ├─ wfb_tx ~~~ RF ~~~ wfb_rx ─┤
 fake_fc.py ◄─► UDP :14550 ────┘   (USB-Wi-Fi RTL8812)     └─ UDP :14550 ◄─► gs_mav.py (телеметрія + RC)
```

Керування обома вузлами: SSH по Ethernet (окремо від радіоканалу).

## Що перевірено, а що ні

| Що | Статус |
|---|---|
| `fake_fc.py` + `gs_mav.py`: телеметрія, RC_CHANNELS_OVERRIDE, ехо, таймаути | **перевірено** на x86 через loopback (`./bench.sh loopback`), RTT ≈ 5–6 мс |
| `video-src.sh` → `video-rx.sh`, h264 і h265 (програмний декодер) | **перевірено** на x86 через loopback |
| Генерація `wifibroadcast.cfg`, сервісів, `DRY_RUN` | **перевірено** (збігається з прикладом wfb-ng Setup-HOWTO) |
| Збірка драйвера DKMS, apt-репозиторій wfb-ng, назви пакетів заголовків на Pi, `kmssink`, `v4l2*` декодери, радіо | **не перевірено**, потребує вашого заліза |
| Емуляція ELRS | **немає:** RC емулюється повідомленням `RC_CHANNELS_OVERRIDE`, не радіоканалом ELRS |
| `fake_fc` ≠ ArduPilot | це емулятор лише документованих таймаутів (`RC_OVERRIDE_TIME` 3 с, `FS_GCS_TIMEOUT` 5 с) |

## Що потрібно

- 2× Raspberry Pi 4 + якісні блоки живлення 5 В/3 А, microSD, Raspberry Pi OS Lite arm64.
- 2× USB-адаптер на RTL8812AU (або EU), **з антенами**. Живити адаптер без антени не можна (SRC: wfb-ng Setup-HOWTO).
- Якісні короткі екрановані USB-кабелі: дешеві/тонкі дають помилки FEC і втрати пакетів (SRC: там само).
- Ethernet-кабелі/комутатор для керування; HDMI-монітор на GS.

Радіочастина (**PROPOSAL**, не з документації): тримайте антени на відстані 1–2 м одна від одної й не впритул; потужність мінімальна (`TX_PWR_IDX=1` за замовчуванням), бо на столі приймач легко перевантажити.
**Регіон і канал обираєте ви** (`WFB_REGION` не має значення за замовчуванням: потрібно вказати дозволений вам домен).

## Розгортання

На обох Pi, з клоном репозиторію:

```bash
cd bench && cp env.example env && nano env      # обов'язково WFB_REGION
```

**1. GS** (спершу, бо він генерує ключі):

```bash
sudo ./bench.sh setup gs        # пакети, venv, драйвер DKMS → ПЕРЕЗАВАНТАЖЕННЯ
sudo reboot
sudo ./bench.sh finish gs       # wfb-ng, ключі, конфіг, сервіси, перевірка
scp /etc/drone.key root@<AIR-IP>:/etc/drone.key
```

**2. AIR**:

```bash
sudo ./bench.sh setup air
sudo reboot
sudo ./bench.sh finish air      # потребує /etc/drone.key з GS
sudo systemctl start bench-video-src bench-fc
```

**3. Перевірка кожного вузла**: `./bench.sh check <air|gs>`. Без радіо: `./bench.sh loopback`.

> Повторний запуск `wfb_keygen` на GS ламає парування: скрипт його не повторює, якщо `/etc/gs.key` існує.

## Приймальні тести (на GS)

| № | Дія | Критерій проходження |
|---|---|---|
| T0 | `./bench.sh loopback` на кожному Pi | усі перевірки PASS |
| T1 | `ethtool -i <wlan>` на обох | у драйвера **порожня** `version` (SRC: wfb-ng Setup-HOWTO) |
| T2 | `wfb-cli gs` | видно пакети й RSSI від AIR |
| T3 | `sudo systemctl start bench-video-rx` | на HDMI рухомий м'яч і таймер, без розсипу |
| T4 | `$VENV/bin/python gs_mav.py --rc off` | HEARTBEAT ~1 Гц, ATTITUDE ~10 Гц, `sysid=1` |
| T5 | `gs_mav.py --rc sweep --confirm-props-off` | на AIR (`journalctl -u bench-fc`) видно значення каналів; RTT у виводі |
| T6 | зупинити `gs_mav.py` | на AIR: «RC override lost» через ≈3 с і «GCS failsafe» через ≈5 с |
| T7 | `sudo systemctl stop wifibroadcast@drone` потім `start` | відео й MAVLink зникають і відновлюються без втручання |
| T8 | порівняти `DECODER=avdec_h264` і `DECODER=v4l2h264dec` (так само h265: `avdec_h265` / `v4l2slh265dec`), `top` | записати завантаження CPU і чи йде картинка |

Результати T1–T8 впишіть у `RESULTS.md` і передайте мені: за ними я зніму позначки HW/SNIP у `docs/`.

## Варіант з реальним обладнанням

Топологія, підключення й перелік упущеного обладнання: `../docs/BENCH-HARDWARE.md`. У `env`:

| Змінна | Ефект |
|---|---|
| `SOURCE=webcam`, `WEBCAM_DEV`, `WEBCAM_FORMAT` | відео з UVC-веб-камери замість тестового малюнка |
| `ENCODER=v4l2h264enc` | апаратне кодування (**експериментально**, не перевірено) |
| `FC_SERIAL=ttyACM0` | реальний FC по USB: `[drone_mavlink] peer = serial:ttyACM0:115200`, `fake_fc` не встановлюється |
| `GS_FORWARD_IP=<хост>` | GS пересилає відео та MAVLink на хост по LAN; декодує хост |
| `DRIVER=8814au` | RTL8814AU на GS: **не підтримується wfb-ng**, `WFB_NICS` вручну, не перевірено |

`gs_mav.py --rc ...` тепер вимагає `--confirm-props-off`: з реальним FC лише без гвинтів і без батареї/ESC.

## Що змінюється вручну

- Інший кодек: `VIDEO_CODEC=h265` в `env` на **обох** вузлах, потім `sudo systemctl restart bench-video-src bench-video-rx`. Програмний x265 на Pi 4 важкий: зменшіть `VIDEO_W/H/FPS`.
- Дивитися відео/телеметрію на ноутбуці з QGC: у `/etc/wifibroadcast.cfg` на GS поставте IP ноутбука в `[gs_mavlink]`/`[gs_video]` і не запускайте `gs_mav.py` одночасно (порт 14550 один).
- Декодер: `DECODER=...`, вивід: `SINK=...` (наприклад, `autovideosink` під X/Wayland, `kmssink` на консолі).

## Поширені проблеми

| Симптом | Імовірна причина | Що зробити |
|---|---|---|
| `install-driver.sh`: немає `/lib/modules/$(uname -r)/build` | заголовки не відповідають запущеному ядру | оновити систему, перезавантажити, повторити; назви пакетів заголовків на вашому релізі перевірити самому |
| `FAIL patched Realtek module loaded` | драйвер не зібрався/не завантажений | `dkms status`, `dmesg`; для 6.12.x і 6.18 див. матрицю в `docs/PI-PORT.md` |
| `wifibroadcast@*` не стартує | немає ключа/адаптера, у `[drone_mavlink]` UART недоступний | `journalctl -u wifibroadcast@gs -e` |
| Багато FEC-помилок, обриви | кабель/живлення адаптера | кабель коротший і якісніший, живлення стабільне |
| `v4l2*` декодер дає чорний екран | елемент є, а відповідного пристрою ядра немає | `DECODER=avdec_h264` (або `avdec_h265`) |
| `kmssink` не відкривається | запущено з X/Wayland-сесії або не з консолі | `SINK="autovideosink sync=false"` |

## Безпека

- Це стендове обладнання. Не підключайте `gs_mav.py` або `fake_fc.py` до реального апарата з гвинтами.
- За документацією ArduPilot резервний RC має лишатися активним: для реального польоту керування через телеметричний канал не може бути єдиним (див. `docs/CHAINS.md`).
- Дотримуйтеся місцевих правил щодо частот і потужності.

## Відкрите

- Вимірювання затримки відео «від скла до скла»: ці скрипти її не міряють (годинники вузлів не синхронізовані); потрібен окремий метод.
- Емуляція саме радіоканалу ELRS і підключення реального ArduPilot SITL (замість `fake_fc.py`): не реалізовано й не перевірено.
