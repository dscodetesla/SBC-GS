# MAVLink-маршрутизатор для M4: `mavp2p` і `mavlink-router`

Дата перевірки: 2026-10-01. Усі цитати перечитано напряму з `raw.githubusercontent.com` (не з підсумків). Позначки: SRC = первинне джерело (README/код/документація), INF = мій висновок із SRC, UNVERIFIED = не підтверджено.

Скорочення URL: `P2P` = `https://raw.githubusercontent.com/bluenviron/mavp2p/main`, `MR` = `https://raw.githubusercontent.com/mavlink-router/mavlink-router/master`, `AP` = `https://raw.githubusercontent.com/ArduPilot/ardupilot/master`.

## 1. mavp2p: синтаксис ендпоінтів

Формат: `mavp2p [<endpoints> ...] [flags]`, ендпоінти розділені пробілом, мінімум один.

| Ендпоінт | Синтаксис | Режим | Джерело |
|---|---|---|---|
| serial | `serial:/dev/ttyAMA0:57600` | `port:baudrate` | SRC `P2P/README.md` (Usage), `P2P/main.go` (`reSerial`) |
| udps | `udps:0.0.0.0:5600` | UDP-сервер (слухає; клієнти підключаються) | SRC `P2P/README.md`, `P2P/main.go` |
| udpc | `udpc:1.2.3.4:5600` | UDP-клієнт (шле на dest) | SRC те саме |
| udpb | `udpb:192.168.7.255:5601` | UDP broadcast | SRC те саме |
| tcps | `tcps:0.0.0.0:5601` | TCP-сервер | SRC те саме |
| tcpc | `tcpc:host:5600` | TCP-клієнт | SRC те саме |

Усі шість префіксів з наших припущень **підтверджені** (`P2P/main.go`, мапа `endpointTypes`).

## 2. mavp2p: прапорці

| Прапорець | Типово | Примітка | Джерело |
|---|---|---|---|
| `--hb-systemid` | 125 | «recommended to set a different system id for each router in the network» | SRC `P2P/README.md` (Full command-line usage) |
| `--hb-componentid` | 191 | | SRC те саме |
| `--hb-period` | 5 (с) | | SRC те саме |
| `--hb-version` | 1 | значення 1 або 2 (`enum:"1,2"`): типово heartbeat шлеться як MAVLink 1 | SRC `P2P/main.go` |
| `--hb-disable` | вимк. | | SRC README |
| `--streamreq-disable` | вимк. | типово mavp2p сам просить стріми в ArduPilot (`--streamreq-frequency=4`) | SRC README |
| `--idle-timeout` | 60s | відключає неактивні з'єднання | SRC README |
| `--read-timeout`/`--write-timeout` | 10s | | SRC README |
| `--dump`, `--dump-path`, `--dump-duration` | вимк., `dump/2006-01-02_15-04-05.tlog`, 1h | tlog | SRC README |
| `--print`, `-q` | | відладка | SRC README |

Чого **немає** в mavp2p (SRC `P2P/README.md`, повний список прапорців): жодних фільтрів за msgid/sysid, жодного переписування sysid чи полів, жодного «лише один писач». Прапорець для підміни sysid клієнта відсутній.

## 3. mavp2p: поведінка маршрутизації

Джерело: SRC `https://raw.githubusercontent.com/bluenviron/mavp2p/main/pkg/messageman/manager.go` (`ProcessFrame`).

| Факт | Позначка |
|---|---|
| Кадр пересилається **як є** (`evt.Frame`), без зміни sysid/полів | SRC manager.go |
| Повідомлення з `target_system > 0` (є поля TargetSystem+TargetComponent) йде лише на канал, де бачили цей вузол; якщо вузол невідомий, кадр іде на всі канали, крім вхідного | SRC manager.go |
| `RC_CHANNELS_OVERRIDE` має `target_system`/`target_component`, отже підпадає під цільову маршрутизацію (дедукція з коду: фільтр за полями у `generateDialect`) | INF |
| Вузол «зникає» після 30 с без трафіку; тоді цільове повідомлення піде broadcast | SRC manager.go (`nodeInactiveAfter`) |
| `REQUEST_DATA_STREAM` від наземних станцій відкидається (якщо не `--streamreq-disable`) | SRC manager.go, README |
| Кілька клієнтів: один `udps:0.0.0.0:5600` приймає багато UDP-клієнтів («links together all UDP endpoints that connect to it»); окремо можна додати `tcps:0.0.0.0:5601` | SRC README (Usage) |
| Неактивні UDP-клієнти видаляються | SRC README (Comparison) |
| Обмежити RC до одного писача **на рівні mavp2p неможливо** | INF (відсутність фільтрів, SRC §2) |

Приклад двох GCS-клієнтів (QGC по UDP, телефон/Mission Planner по TCP) та FC на serial; `--hb-systemid` залишаємо відмінним від 255 і від sysid апарата:

```
mavp2p serial:/dev/ttyAMA0:921600 udps:0.0.0.0:14550 tcps:0.0.0.0:5760 --hb-systemid=125
```

(Складено мною з синтаксису README; на залізі не запускалося: INF.)

## 4. mavp2p: релізи та ліцензія

| Факт | Позначка |
|---|---|
| Ліцензія MIT, «Copyright (c) 2019 aler9» | SRC `P2P/LICENSE` |
| README: збірки під «arm6, arm7, arm64, amd64», «independent from libc», сумісні з Alpine | SRC README (Features) |
| Імена артефактів за збірковим скриптом: `mavp2p_${VERSION}_linux_arm64v8.tar.gz`, `..._linux_armv7.tar.gz`, `..._linux_armv6.tar.gz`, `..._linux_amd64.tar.gz` (усередині один файл `mavp2p`). Увага: для arm64 суфікс **`arm64v8`**, не `arm64` | SRC `https://raw.githubusercontent.com/bluenviron/mavp2p/main/scripts/binaries.mk` |
| Реальні активи на сторінці релізу та номер останньої версії | UNVERIFIED (`github.com` і `api.github.com` повертають 403 політики проксі; MCP-GitHub не має доступу до репозиторію; не обходив) |
| Збірка з джерел без CGO: `CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build .` | SRC README (Cross compile) |

## 5. mavlink-router (запасний варіант)

| Тема | Факт | Позначка |
|---|---|---|
| Ліцензія | Apache 2.0 (`license: 'Apache 2.0'` у `meson.build`; файл `LICENSE` починається «Apache License Version 2.0») | SRC `MR/meson.build`, `MR/LICENSE` |
| Готові бінарники | у README лише збірка з джерел (meson+ninja, git submodule для mavlink); про релізні активи для arm64/armv7 нічого | SRC `MR/README.md`; активи релізів UNVERIFIED (403) |
| Конфіг | `/etc/mavlink-router/main.conf`, `-c`, каталог `config.d` (`-d`); CLI і файл зливаються | SRC README (Running) |
| CLI | `-e ip[:port]` UDP «normal» (клієнт; порт від 14550 і далі, якщо не вказано); `-p ip:port` TCP-клієнт; `-t port` TCP-сервер (типово 5760, `0` вимикає); останній аргумент без ключа: UART `dev[:baud]` або `ip:port` (UDP **server**); `-s/--sniffer-sysid`; `-l` лог, `-T` tlog | SRC `MR/src/main.cpp` (`long_options`, `help()`), README |
| Приклад | `mavlink-routerd -e 192.168.7.1:14550 -e 127.0.0.1:14550 /dev/ttyS1:1500000` | SRC README |
| Конфіг-ендпоінти | `[UartEndpoint n]` (Device, Baud, FlowControl), `[UdpEndpoint n]` (Mode=Normal/Server, Address, Port), `[TcpEndpoint n]` (Address, Port, RetryTimeout) | SRC `MR/examples/config.sample` |
| Фільтри | на кожному ендпоінті: `Allow/BlockMsgIdIn/Out`, `Allow/BlockSrcSysIn/Out`, `Allow/BlockSrcCompIn/Out`; для UART/UDP/TCP таблиці опцій у коді містять `AllowMsgIdIn`, `BlockMsgIdIn`, `AllowSrcSysIn`, `BlockSrcSysIn` | SRC `MR/README.md` (Message filters), `MR/src/endpoint.cpp` (таблиці опцій ~рр. 63-117) |
| Один писач RC | `BlockMsgIdIn = 70` на ендпоінтах QGC/телефона відкидає `RC_CHANNELS_OVERRIDE` (msgid 70) із цих клієнтів до маршрутизації. Номер 70 беру зі стандарту MAVLink | INF із SRC фільтрів; msgid 70 перевірити за `https://mavlink.io/en/messages/common.html` (сторінку завантажено, але рядок 70 окремо не перечитано: UNVERIFIED) |
| Два GCS | багато UDP-клієнтів через `-e` або кілька `[UdpEndpoint]`; динамічні TCP-клієнти на порту 5760 | SRC README |
| sysid | роутер не переписує sysid; маршрутизація за таблицею «які sysid бачили на ендпоінті»; є захист від петель (правило 1) | SRC README (Routing rules) |
| Власний heartbeat | у `mainloop.cpp` згадок heartbeat не знайдено; README не описує власний heartbeat роутера | INF (відсутність у коді; не гарантія) |
| Переписування RC/полів | відсутнє: лише фільтри й дедуплікація | SRC README |

## 6. ArduPilot: один писач RC

| Факт | Позначка / джерело |
|---|---|
| `RC_CHANNELS_OVERRIDE` приймається лише від «нашої GCS»: `if (!gcs().sysid_is_gcs(msg.sysid)) return;` | SRC `AP/libraries/GCS_MAVLink/GCS_Common.cpp`, `handle_rc_channels_override` |
| Стара назва `SYSID_MYGCS` у поточному master/док. це `MAV_GCS_SYSID` (типово **255**) плюс `MAV_GCS_SYSID_HI` (типово 0): якщо HI >= MAV_GCS_SYSID, всі sysid з діапазону вважаються GCS; опис: «accepted for GCS failsafe handling, RC overrides and manual control» | SRC `AP/libraries/GCS_MAVLink/GCS.cpp`, `GCS::sysid_is_gcs`; список параметрів `https://ardupilot.org/copter/docs/parameters.html` (є `MAV_GCS_SYSID`, немає `SYSID_MYGCS`). Для старішої прошивки назва `SYSID_MYGCS`: UNVERIFIED |
| Отже: два клієнти з однаковим sysid 255 обидва «наші»; для єдиного писача задати `MAV_GCS_SYSID` = sysid мосту TX12 (а QGC лишити 255) або фільтрувати msgid 70 на роутері | INF із SRC вище |
| Поріг `FS_GCS`/ heartbeat рахується лише від sysid, що проходить `sysid_is_gcs`: `handle_heartbeat` викликає `sysid_mygcs_seen`. Heartbeat самого mavp2p (sysid 125) за типових налаштувань не рахується; `MANUAL_CONTROL` теж рахується як heartbeat | SRC `GCS_Common.cpp` (`handle_heartbeat`, `handle_manual_control`) |
| `RC_OVERRIDE_TIME`: типово **3.0 с**, діапазон 0-120; `0` вимикає overrides, `-1` (будь-яке від'ємне) = ніколи не спливає | SRC `AP/libraries/RC_Channel/RC_Channels_VarInfo.h`; логіка `RC_Channel::has_override()` у `RC_Channel.cpp`, `get_override_timeout_ms()` у `RC_Channel.h`; док. `https://ardupilot.org/copter/docs/parameters.html` |
| Значення `UINT16_MAX` у каналі = «ігнорувати»; для каналів 9-16 також `0`; `UINT16_MAX-1` повертає канал до радіо | SRC `GCS_Common.cpp` |
| `RC_OPTIONS` біт 1 «Ignore MAVLink Overrides», біт 0 «Ignore RC Receiver» | SRC `https://ardupilot.org/copter/docs/parameters.html` |
| `FS_GCS_ENABLE` (Copter) типово **0 = Disabled**; значення 1 RTL, 3 SmartRTL/RTL, 4 SmartRTL/Land, 5 Land, 6 DO_LAND_START/RTL, 7 Brake/Land | SRC `AP/ArduCopter/Parameters.cpp` (master); док. parameters.html. Типове значення в конкретній стабільній версії UNVERIFIED |
| `FS_GCS_TIMEOUT` (Copter) 2-120 с (док.); у `gcs-failsafe` типово 5 с | SRC `https://ardupilot.org/copter/docs/gcs-failsafe.html` |
| GCS-failsafe рахує час від останнього heartbeat; «if no GCS is ever connected, the GCS failsafe will remain inactive» | SRC gcs-failsafe.html |
| Після відновлення зв'язку апарат лишається у failsafe-режимі, сам не повертається | SRC gcs-failsafe.html |

## 7. Висновки для M4 (INF)

1. mavp2p простіший (статичний Go-бінарник), але **не вміє ні фільтрувати, ні обмежувати писача**. Єдиний писач RC треба забезпечити: (а) `MAV_GCS_SYSID` на FC = sysid мосту; (б) мережею: QGC лише читає; (в) у мосту: єдине джерело `RC_CHANNELS_OVERRIDE`.
2. mavlink-router може відкидати msgid 70 на «читальних» ендпоінтах (`BlockMsgIdIn`), це реальна перевага як запасного. Ціна: збірка з джерел (README без бінарників), meson, залежність від glibc/ядра збірки.
3. `RC_OVERRIDE_TIME` лишити типовим або 1-2 с, не 0 і не -1; `FS_GCS_ENABLE` вручну виставити (типово 0), інакше жодного failsafe за heartbeat.
4. Heartbeat від GCS-мосту має мати sysid, який приймає FC (за `MAV_GCS_SYSID`), інакше failsafe за heartbeat не працює навіть при справних overrides.

## 8. Що лишилося UNVERIFIED

- Наявність і точні імена релізних активів mavp2p (за скриптом: `linux_arm64v8`, `linux_armv7`) і номер останньої версії: `github.com`/`api.github.com` дали 403, не обходив.
- Чи існують готові бінарники mavlink-router для arm64/armv7; твердження в `docs/PI-PORT.md:106` про «glibc ≥ 2.42» не перевірене.
- Поведінка mavp2p на реальному FC: чи тримається цільова маршрутизація `RC_CHANNELS_OVERRIDE` при двох клієнтах і при перепідключенні (потрібен SITL/залізо).
- Те, що mavlink-router приймає саме `BlockMsgIdIn` для кожного типу ендпоінтів у конкретній стабільній версії: фільтри є в master (SRC), але версія в Debian/релізах не перевірена; msgid 70 перечитати в `common.html`.
- Назва `SYSID_MYGCS` у старих прошивках; типовий `FS_GCS_ENABLE` в конкретних стабільних релізах Copter.
- Підпис MAVLink2 (відкрите питання №5 у KNOWLEDGE, не розглядалося).
