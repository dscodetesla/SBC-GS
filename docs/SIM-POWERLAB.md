# Віртуальна лабораторія живлення: USB, Pi 5, RTL8812 (що доводиться без струмів і як виміри дня заліза потрапляють у модель)

Станом на 2026-10-03. Код: `bench/ingest.py` (+ `bench/ingest-rules.json`), `bench/doctor.sh` (розширено), `tests/sim/powerlab/` (`gen.py`, `shims/`, `scenarios/`, `pipeline.sh`, `test_powerlab.py`, `sensitivity.py`, `run.sh`, `mutate.sh`). Запуск: `tests/sim/powerlab/run.sh --check` (менше 15 с, лише stdlib, без мережі й root). Позначки достовірності як у `CLAUDE.md`: **SRC** (прочитано першоджерело, є URL), **REPO** (перевірено в репозиторії), **SNIP** (приклад виводу з чужого файлу, який я прочитав напряму, але це не першоджерело), **INF** (мій висновок), **HW** (можливо лише на залізі), **UNVERIFIED**, **SYNTH** (синтезоване значення, ніколи не факт).

## 1. Що це і чого це не доводить

Мета: у день вимірювань нічого не вигадувати. Уся дорога від сирого виводу приладів до оновленої моделі (`calib.py whatif`) пройдена наперед на синтетичних даних у **справжніх форматах**, тож у день заліза лишається лише зібрати файли.

Що доводить ця лабораторія (REPO, `run.sh --check`): парсери читають формати, джерела яких підтверджено (розділ 3); `bench/doctor.sh` із шимами віддає очікуване JSON і лишається лише читанням; `bench/ingest.py` з нормованого сирого виводу відновлює задані «істинні» значення (опір кабелю, струми, пороги) у межах допуску; кожен ключ оверлею має `source` і мітку часу, решта `null`; суперечності між заявленим і виміряним (БЖ 5 А проти undervoltage) знаходяться; оверлей приймає `calib.py`, а `whatif` змінює вихід у очікуваний бік.

Чого вона **не** доводить (**HW**, див. розділ 10): реальні струми RTL8812AU/EU, реальну поведінку обмежувача Pi 5 (чи він відключає порт, чи спільний запобіжник, на якому рівні), реальну точність АЦП PMIC, реальну енумерацію/повернення адаптера, реальний тепловий режим. Усі числа генератора **SYNTH**: вони перевіряють інструменти, а не залізо.

## 2. Конвеєр

```
сценарій (JSON)            віртуальні прилади               зразок "дня заліза"
scenarios/*.json ──► gen.py ──► pmic.log  dmesg.txt  meter_*.csv  windows.txt  wfb.jsonl
                              vcgencmd/*  lsusb*.txt  sysroot/{proc,sys}  now_utc  truth.json
                                   │ (шими vcgencmd/dmesg/lsusb/date + DOCTOR_ROOT)
                                   ▼
                      bench/doctor.sh ──► doctor.json (sbc-gs-doctor/1, лише додані поля)
                                   │
        pmic.log + dmesg + meter CSV + windows + wfb.jsonl
                                   ▼
                      bench/ingest.py ──► звіт (текст + JSON) ──► overlay.json
                                                                       │
                              tests/sim/models/calib.py whatif overlay.json --scenario S
```

Команди (усе без заліза):

```
tests/sim/powerlab/run.sh --check          # тести, <15 с
tests/sim/powerlab/run.sh --demo           # конвеєр на сценарії calib_pi5_hot_adapter: звіт, істина, whatif
tests/sim/powerlab/pipeline.sh SCEN.json OUT --whatif nominal_pi5_5a_150m [-- додаткові аргументи ingest]
tests/sim/powerlab/mutate.sh               # 15 мутацій на КОПІЇ, усі мають бути вбиті
tests/sim/powerlab/run.sh --sensitivity    # дослідження чутливості (хвилини)
```

У день заліза те саме без генератора: зібрати файли (розділ 9), потім

```
bench/ingest.py --doctor bench/out/doctor-*.json --instr-log pmic.log --dmesg dmesg.txt \
    --meter psu:meter_psu.csv --meter dongle:meter_dongle.csv --windows windows.txt --wfb-json wfb.jsonl \
    --claim-psu-a 5 --claim-usb-max-current 1 --ambient-c 24 --overlay overlay.json --report-json report.json
python3 tests/sim/models/calib.py whatif overlay.json --scenario nominal_pi5_5a_150m
```

`ingest.py` лише читає файли. Вихід 0; `--strict` дає 1 при знайденій СУПЕРЕЧНОСТІ; 2 при поганому вході (невідомий ключ оверлею, неправильна схема doctor, нерозібраний CSV).

## 3. Формати виводу: що підтверджено джерелом, що ні

Правило проєкту: незвірене не видається за факт. «Парсер» нижче означає функцію в `bench/ingest.py`; регулярні вирази й константи лежать у `bench/ingest-rules.json` разом із тегом і джерелом.

| Вихід | Формат у парсері | Достовірність | Джерело |
|---|---|---|---|
| біти `vcgencmd get_throttled` | 0: undervoltage now, 1: ARM frequency capped, 2: currently throttled, 3: soft temperature limit active, 16/17/18/19: відповідні «has occurred» | **SRC** | `raspberrypi/documentation` `documentation/asciidoc/computers/os/graphics-utilities.adoc` (розділ `get_throttled`), прочитано напряму (raw) |
| вигляд відповіді `get_throttled` | `throttled=0x50005` | **SNIP** | приклади в `geerlingguy/jeffgeerling-com` (блог), `ebaauw/homebridge-rpi` README; `vcgencmd` лише друкує відповідь прошивки (SRC `raspberrypi/utils` `vcgencmd/vcgencmd.c`: `printf("%s\n", result)`), сам текст формує закрита прошивка VideoCore |
| `vcgencmd measure_temp` | `temp=39.5'C`; команда існує й описана як температура SoC | команда **SRC** (graphics-utilities.adoc); вигляд рядка **SNIP** (ті самі файли) | |
| `vcgencmd pmic_read_adc` (Pi 5) | рядки `<ІМ'Я> current(N)=0.00780744A` і `<ІМ'Я> volt(N)=5.10540000V`, напр. `EXT5V_V volt(24)=...` | існування команди та рейки `EXT5V_V` **SRC** (`power-supplies.adoc`, `config_txt/overclocking.adoc`); текстовий формат рядків **SNIP** (блог Gerling, кілька сторонніх репозиторіїв із зразками виводу) | USB-струм PMIC **не бачить** (SRC power-supplies.adoc: «You can't see USB current…»), точність АЦП не задокументована (**UNVERIFIED**) |
| `vcgencmd get_config usb_max_current_enable` | `usb_max_current_enable=1` | команда **SRC** (power-supplies.adoc); вигляд відповіді `ключ=значення` **UNVERIFIED** | у парсері допускається, невідомий вигляд = `null` |
| `/proc/device-tree/chosen/power/*` | файли `max_current` (мА), `usb_max_current_enable`, `usb_over_current_detected`, `reset_event`; читаються як big-endian u32 | назви й зміст **SRC** (power-supplies.adoc, «Power supplies and Raspberry Pi OS»); двійкове кодування **UNVERIFIED** (INF: клітинки device tree великі-endian) | довжина не 4 байти: значення не інтерпретується |
| рядки dmesg про undervoltage | `Undervoltage detected!` (dev_crit) і `Voltage normalised` (dev_info), із префіксом пристрою `hwmon hwmonN:` | текст **SRC** (`torvalds/linux` та `raspberrypi/linux` `drivers/hwmon/raspberrypi-hwmon.c`); префікс — конвенція dev_printk (**INF**); старіший текст `Under-voltage detected!` приймається (**UNVERIFIED**, на яких ядрах друкується) | драйвер опитує прошивку кожні 2 с і просить її **очистити липкі біти** (SRC той самий файл, коментар `Request firmware to clear sticky bits`): див. розділ 5 |
| dmesg: USB disconnect / new / повернення | `usb 1-1: USB disconnect, device number N`; `usb 1-1: new high-speed USB device number N using xhci-hcd`; `usb 1-1: New USB device found, idVendor=0bda, idProduct=8812, bcdDevice= 0.00` | **SRC** (`drivers/usb/core/hub.c`: рядки `dev_info`) | назва HCD на Pi 5 (`xhci-hcd`) **UNVERIFIED** |
| dmesg: over-current | `over-current condition` (порт `usbN-portM:` або хаб `1-0:1.0`), лічильник `over_current_count` у sysfs | **SRC** (hub.c; ABI `sysfs-bus-usb`) | чи RP1/xHCI Pi 5 повідомляє це при спрацюванні обмежувача: **UNVERIFIED** (генератор використовує слова hub.c) |
| dmesg: збої енумерації | `device descriptor read/64, error -71`, `unable to enumerate USB device`, `Cannot enable. Maybe the USB cable is bad?`, `connect-debounce failed`, `disabled by hub (EMI?), re-enabling...` | **SRC** (hub.c) | |
| форми часу | `[ 1234.567890]` (годинник ядра), `dmesg -T` (локальний час, береться як UTC з попередженням), `journalctl -k -o short-iso` | `[секунди]` — стандартний вигляд util-linux; `-T`/ISO — парсер «за можливості» (**UNVERIFIED** на реальних файлах); годинник ядра переводиться в UTC через `--boot-utc` або `utc − uptime` із doctor | |
| `lsusb -t` | `/:  Bus 001.Port 001: Dev 001, Class=root_hub, Driver=xhci_hcd/4p, 480M`; `    \|__ Port 001: Dev 002, If 0, Class=..., Driver=..., 480M` | **SRC** (`gregkh/usbutils` `lsusb-t.c`, формати `printf`) | |
| `hwmon`: `in*_input`, `curr*_input`, `temp*_input`, `power*_input`, `fan*_input` | одиниці: мВ, мА, мград. C, мкВт, об/хв; `in0_lcrit_alarm` 0/1 | **SRC** (ABI `Documentation/ABI/testing/sysfs-class-hwmon`) | **важливо:** на Pi драйвер `rpi_volt` дає лише `in0_lcrit_alarm` (ядра 6.12 raspberrypi/linux) або (master, 2026) `in0..in3_input` = **core/sdram_c/sdram_i/sdram_p**, тобто **не** 5 В входу (SRC raspberrypi-hwmon.c). Рейка 5 В у hwmon з'явиться лише від зовнішнього I²C-монітора (напр. INA3221); його підписи/мітки **UNVERIFIED** (SYNTH у тесті) |
| `/sys/class/thermal/thermal_zone0/temp` | міліградуси C | **SRC** (`overclocking.adoc`: «Divide the result by 1000») | |
| пороги | undervoltage 4.63 В (±5 %); м'яке обмеження ARM між 80 і 85 °C; Pi 3B+ м'який ліміт 60 °C | **SRC** (power-supplies.adoc; overclocking.adoc) | |
| бюджет USB Pi 5 | 1.6 А з БЖ 5 А (25 Вт), інакше 600 мА | **SRC** (power-supplies.adoc) | |
| wfb-ng статистика | `wfb-cli` — це curses-екран над msgpack-потоком (`stats_port`), його вивід парсити не можна; придатний **JSON-потік `api_port`**: один об'єкт на рядок; `settings`, потім `rx` (`packets{all,out,session,fec_rec,lost,dec_err,bad,data,uniq,all_bytes,out_bytes}=[приріст,усього]`, `rx_ant_stats[{ant,freq,mcs,bw,pkt_recv,rssi_min/avg/max,snr_min/avg/max}]`) і `tx` (`packets{injected,incoming,fec_timeouts,dropped,truncated}`, `tx_ant_stats`) | **SRC** (`svpcom/wfb-ng` `wfb_ng/protocols.py`, `wfb_ng/cli.py`, `wfb_ng/conf/master.cfg`: `[gs] stats_port=8003, api_port=8103` за замовчуванням) | реальні порти на образі можуть відрізнятися; часової мітки в об'єктах немає, час = номер рядка × `log_interval` (1000 мс за замовчуванням) від `--wfb-start-utc` |
| температура RF-модуля | `/proc/net/rtl88x2eu/<wlan>/thermal_state`: `rf_path:N, temperature:T` | **SRC** (`protocols.py`, `RFTempMeter`) | лише драйвери `rtl88x2eu/cu` (AIR, WiFiLink2); для RTL8812AU (GS) **UNVERIFIED**; парсер не реалізовано, поле для майбутнього |
| CSV USB-вимірників | **загальна** форма: рядок заголовка з колонками часу, напруги, струму; одиниці з заголовка (`(mA)`, `(mV)`) або `--meter-i-unit`; роздільник `,` `;` або таб; рядки `#` пропускаються; колонки задаються `--meter-cols t=..,v=..,i=..`. Друга форма: `device,state,amps` як у `power_model.py ingest` | **не прив'язано до жодної моделі приладу** (конкретних форматів не вигадую) | кожен реальний експорт треба один раз звірити з `--meter-cols`; будь-який CSV, де парсер не знайшов колонки, дає помилку, а не мовчки нуль |

## 4. `bench/doctor.sh`: що додано

Лише безпечні читання (як було: без передачі, без завантаження модулів, без запису поза вихідним файлом). Структура `sbc-gs-doctor/1` не змінена, **лише додані поля**; тест перевіряє, що всі старі поля на місці, і що скрипт не містить `modprobe`, `insmod`, `rmmod`, `iw ... set`, `ip link set`, `sudo`, запису в `/sys` чи `/proc`. Нове: `DOCTOR_ROOT=<dir>` додає префікс до читань `/proc` і `/sys` (для відтворення фальшивого дерева; порожній = справжня система).

| Нове поле | Що це | Навіщо |
|---|---|---|
| `dmesg_rc` | код завершення `dmesg` | старий лічильник `dmesg_usb_power_events` давав `0` і при **нечитабельному** журналі (`kernel.dmesg_restrict`); тепер нуль без `dmesg_rc=0` не є доказом (правило K7) |
| `dmesg_power_lines` | до 200 останніх рядків про undervoltage, over-current, USB disconnect/new/found, збої енумерації | сирі рядки з часовими мітками для `ingest.py` |
| `pi5_pmic_adc_full` | повний вивід `vcgencmd pmic_read_adc` | усі рейки, не лише `EXT5V_V` |
| `arm_clock` | `vcgencmd measure_clock arm` | дроселювання видно по частоті |
| `dt_chosen_power` | `ім'я=hex` для кожного файлу `/proc/device-tree/chosen/power/*` | `max_current`, `usb_max_current_enable`, `usb_over_current_detected`, `reset_event` (SRC назви) |
| `uptime_s` | перше число `/proc/uptime` | годинник ядра → UTC (`utc − uptime`) |
| `cpu0_cur_freq_khz`, `thermal_zones` | cpufreq і всі `thermal_zone*` | |
| `hwmon` | `hwmonN/name`, `in*_input/_label/_lcrit_alarm`, `curr*`, `power*`, `temp*`, `fan*` | рейка 5 В від зовнішнього I²C-монітора, якщо є; `rpi_volt` |
| `usb_sysfs` | `ім'я vid:pid bMaxPower speed product` для кожного USB-пристрою | **заявлений** струм конфігурації (SRC sysfs.c: `%dmA`), не виміряний |
| `usb_port_over_current_count` | `over_current_count` кожного порту (SRC ABI) | незалежний від dmesg лічильник спрацювань |

## 5. `bench/ingest.py`

**Входи:** `--doctor`, `--instr-log` (журнал `=== <ISO UTC>` + сирий вивід `vcgencmd get_throttled / measure_temp / pmic_read_adc EXT5V_V`; повторюється), `--dmesg`, `--lsusb-t`, `--meter ПОЗИЦІЯ:ФАЙЛ` (`psu` між БЖ і Pi, `host` біля порту Pi, `dongle` біля адаптера), `--windows` (рядки `початок,кінець,мітка` у ISO UTC, секундах epoch або відносних до `--boot-utc`/`--t0-utc`; мітка `пристрій:стан`: `rtl8812:tx`, `board_pi5:load`, `soc:load`), `--wfb-json`, ручні факти (`--claim-psu-a`, `--claim-usb-max-current`, `--ambient-c`, `--board`, `--soak-window`, `--radio-path`, `--meter-offset-s`).

**Оверлей** (`--overlay`): усі ключі моделі, які лабораторія вміє вивести, у форматі `calib.py`; те, що з цих входів не виведено, лишається `null` (calib ігнорує `null`). Кожен виведений ключ: `{"value", "min", "max", "source", "measured_utc", "tag": "HW", "note"}`; `source` містить прилад, спосіб і мітку часу. Ключі перевіряються за `params.json` і `params.degrade.json` (невідомий ключ = вихід 2, а не мовчазна втрата).

| Ключ моделі | Як виводиться | Умови | Застереження |
|---|---|---|---|
| `power.devices.rtl8812_idle_a`, `_rx_a`, `_tx_a`, `fc_usb_a`, `webcam_a`, `fan_a` | медіана струму в вікнах `пристрій:стан` із вимірника біля адаптера; діапазон p5..p95; **відрізки, коли радіо було відключене** (dmesg), виключаються | ≥ 5 відліків у вікні | для `tx_a` медіана = «статичний» струм моделі (імпульси ×пік не входять) |
| `power.boards.<плата>.board_{idle,active,load}_a` | вікна `board_pi5:idle` тощо з вимірника `psu` | мітка стверджує, що підключена лише плата | з-під БЖ видно й вентилятор/кулер |
| `power.tx_peak_factor` | p99/медіана у вікні `rtl8812:tx` | частота відліків ≥ 40 Гц | **нижня межа** (вимірник фільтрує імпульс), для достовірного значення потрібен осцилограф |
| `power.cable_resistance_ohm` | (V вимірника `psu` − `EXT5V_V`)/I методом найменших квадратів через нуль; без напруги вимірника — регресія `EXT5V_V` від I (≥ 3 вікна, розкид струму ≥ 0.3 А) | PMIC+вимірник у тих самих вікнах | вимірник на виході БЖ не бачить власного просідання БЖ; точність АЦП **UNVERIFIED** |
| `power.psu_nominal_v` | перетин регресії (лише коли немає напруги вимірника) | | екстраполяція до нульового струму |
| `usb.cable_r_ohm` | (`EXT5V_V` − V вимірника `dongle`)/I у вікнах з адаптером | вимірник біля **адаптера** | містить внутрішній USB-шлях Pi: **верхня межа** для самого кабелю |
| `power.undervolt_threshold_v` | обгортка `EXT5V_V` навколо переходу біту 0 `get_throttled` 0→1 | ≥ 5 відліків навколо, ширина ≤ 0.3 В | обгортка, не довірчий інтервал; зв'язок АЦП з детектором **UNVERIFIED** |
| `power.usb_dropout_v` | обгортка `EXT5V_V` навколо `USB disconnect` радіо, поки діє undervoltage | повільна рампа лабораторного БЖ | рейка PMIC це вхід плати, адаптер може бачити менше |
| `power.usb_reenum_s` | `USB disconnect` → наступний `new ... USB device` після рядка over-current | автоматичні спрацювання; `--reenum-all` додає решту | ручне вимкнення людиною і відновлення після undervoltage **виключені** (затримка містить відновлення живлення/людину) |
| `usb.drop_rate_per_h` | кількість відключень радіо у `--soak-window` / години | ≥ 1 год і ≥ 1 подія | причини не розділяються; діапазон — нормальне наближення Пуассона; при 0 подій лише верхня межа, ключ `null` |
| `hw.soc_rise_c` | медіана `measure_temp` у вікні `soc:*` мінус `--ambient-c` | перевірка усталеності (різниця медіан половин ≤ 1 °C), інакше нижня межа | |
| `hw.soc_soft_limit_c` | обгортка температури навколо переходу біту 3 | SoC справді перетнув поріг | |
| `ext.ambient_c` | значення `--ambient-c` | введено вручну | |

**Суперечності** (`findings`; рівні `CONTRADICTION`, `WARN`, `INFO`; `--strict` реагує на перший):

| Код | Правило |
|---|---|
| K1 | заявлений БЖ ≥ рекомендованого (Pi 5: 5 А), а є undervoltage (біти 0/16, рядок ядра або `EXT5V_V` < 4.63 В): **CONTRADICTION**. Без заяви: `WARN`; заява нижча за рекомендацію: `INFO` |
| K2 | `max_current` з device tree < заявлений БЖ (кодування UNVERIFIED) |
| K3 | Pi 5, `usb_max_current_enable = 0` при заявленому БЖ 5 А чи `--claim-usb-max-current 1` (ліміт 0.6 А, SRC) |
| K4/K4b | прапор м'якого обмеження при `measure_temp` нижче 80 °C (60 °C для 3B+); SoC ≥ 85 °C без жодного прапора дроселювання |
| K5 | рядки ядра про undervoltage, а `get_throttled` жодного разу не показав біти: **не суперечність**, очікувано, бо драйвер hwmon очищає липкі біти кожні 2 с (SRC): вірити рядкам ядра. K5b: біт 16 є, рядка ядра немає в читабельному журналі |
| K6 | `EXT5V_V` нижче смуги детектора (4.63 В −5 %) без жодного прапора й рядка |
| K7 | `dmesg_rc ≠ 0`: нулі не є доказом |
| K8 | струм p99 у вікні вище бюджету USB (за станом `usb_max_current_enable`/заявою), а ні over-current, ні відключення не було: **«жорсткий бюджет» моделі тут не спрацював** (модель `power_model.py` вважає бюджет жорстким; на залізі UNVERIFIED) |
| K9 | напруга вимірника біля порту/адаптера вища за `EXT5V_V`: позиція вимірника чи зсув АЦП |
| K10 | `--board` не збігається з `board_model` із doctor |
| K11 | немає жодного `EXT5V_V`: опір і пороги не виводяться |

Додатково звіт друкує підказку `scenario cfg hint: psu_a=…, usb_max_current=…` (з `max_current` DT) для `cfg` сценаріїв моделі; оверлей містить лише параметри, не `cfg`.

**Нюанс липких бітів (SRC).** Драйвер `raspberrypi-hwmon.c` кожні 2 с питає прошивку з маскою очищення липких бітів. Отже `vcgencmd get_throttled` може не показати біт 16/18/19 для події, яку ядро вже забрало, і відсутність біту 16 **не доводить** відсутності undervoltage; надійний слід це рядки ядра `Undervoltage detected!`. Тому K5 — `INFO`, а не суперечність; генератор відтворює цю поведінку (`kernel_clears_sticky`), і є тест.

## 6. Віртуальні прилади (`tests/sim/powerlab/`)

`gen.py СЦЕНАРІЙ --out DIR`: покроковий (20 мс) електричний мінімум GS на тих самих формулах і файлах параметрів, що й моделі (`power_model.budget/device_a/usb_budget_a`, `degrade_model.Pi5UsbLimiter`), але з «істинними» значеннями, що відрізняються від пріорів (`truth` у сценарії). Виходи: `pmic.log`, `dmesg.txt`, `meter_psu.csv`, `meter_dongle.csv`, `windows.txt`, `wfb.jsonl`, дерево для шимів (`vcgencmd/*`, `lsusb*.txt`, `sysroot/proc|sys`, `now_utc`), `truth.json` (що відновлювати), `ingest.args` (що передав би оператор). Детермінований за `--seed`.

Подій немає з повітря: просідання БЖ (`v_ramp`, `dips`), автомат Pi 5 (trip, повернення, latch за `Pi5UsbLimiter`), перегрів SoC (RC-модель), скидання USB при падінні напруги нижче `usb_dropout_v`, липкі біти й опитування ядром кожні 2 с. Шими (`shims/vcgencmd`, `dmesg`, `lsusb`, `date`) відтворюють записані відповіді з `POWERLAB_FIXTURE`; `POWERLAB_DMESG_RC=1` імітує заборонений журнал.

Сценарії: `calib_pi5_5a` (здоровий протокол дня заліза), `calib_pi5_hot_adapter` (адаптер 1.5 А проти пріора 0.9 А), `uv_ramp_pi5` (рампа лабораторного БЖ), `claim_5a_actual_3a` (заява 5 А, фактично 3 А і ліміт 0.6 А), `hot_soc` (перетин м'якого ліміту). Усе **SYNTH**.

Межі віртуальних приладів (чесно): повторна енумерація, вигляд повідомлення over-current на Pi 5, поведінка обмежувача при перевищенні, шум АЦП, форма TX-імпульсу, стала часу SoC (у тестових сценаріях 8 с, щоб усталювалось швидко) — припущення генератора (SYNTH/INF), не вимірювання.

## 7. Наскрізний тест і мутації

`test_powerlab.py` (stdlib `unittest`, 51 тест, ~11 с): формати (біти зіставляються з таблицею `power_model.THROTTLED_BITS`), генератор (детермінізм, збіг статичного стану з `power_model.budget`, послідовність подій, липкі біти), doctor із шимами (старі поля на місці, нові додані, лише читання, нечитабельний журнал → K7), конвеєр на п'яти сценаріях (відновлення істини в допуску; обгортки порогів містять істину; суперечності K1–K3 знайдено; усі ключі оверлею мають `source`, `measured_utc`, `tag`; `calib.split_overlay` без попереджень; `whatif` змінює вихід у очікуваний бік), правила суперечностей на ручних входах, «нуль нових літералів у `ingest.py`» (храповик `config_scan.py`), `shellcheck`.

`mutate.sh`: 15 мутацій на копії (біт `uv_now` зсунуто; оверлей без `source`; без мітки часу; суперечність K1 не виявлена; мА читаються як А; знак опору; британське `normalised` не впізнано; `dmesg_rc` зник із doctor; генератор поміняв порядок відключення/повернення; відрізки без радіо рахуються струмом пристрою; липкі біти не очищаються; undervoltage-дропи йдуть у `usb_reenum_s`; K8 не спрацьовує; генератор ігнорує істинний опір; doctor «завантажує модуль»). Результат: усі 15 вбиті (числа в звіті виконання).

## 8. Дослідження чутливості: який канал вимірювань найбільше звужує модель живлення

Метод (REPO, `tests/sim/powerlab/sensitivity.py`, `run.sh --sensitivity`). Для кожного параметра живлення/USB/температури рушій сценаріїв (`scenario_engine.py`, `docs/SIM-SCENARIOS.md`) ганяється з параметром, закріпленим на 5 % і 95 % **його пріора** (того ж розподілу, що рушій семплює; для параметрів, яких рушій не семплює, діапазон INF вказано нижче), зі спільними випадковими числами (однаковий seed, антитетичні пари, n = 200, seed 1). Розмах кожного виходу (середня доступність, середні залишкові втрати, ймовірності `usb_trip`, `usb_latched`, `usb_dropout`, `undervoltage`, `soc_throttle`) між двома прогонами є внеском параметра; частки нормуються на кожен вихід і сценарій (`nominal_pi5_5a_150m`, `pi5_3a_weak_psu`, `hot_day_closed_case`) і усереднюються. Другий набір прогонів закріплює параметр на медіані пріора ± **похибка приладу** (припущення, SYNTH) і показує, яка частка розмаху лишилась. Шум Монте-Карло (seed 1 проти 2 на медіанах) надрукований у кінці виводу; розмах нижче шуму позначено `within_mc_noise`.

**Це дослідження за моделлю (SYNTH/UNMEASURED пріори), а не за залізом**: воно каже, де вимірювання найсильніше стискає невизначеність *моделі*, а не що реально відбувається. Взаємодії параметрів ігноруються (по одному). Двоє з найвищих параметрів рушій не семплює (див. нижче), їх діапазон заданий у скрипті як INF.

Підсумок по каналах (`share` = внесок каналу у сумарний розмах, %; `після` = що лишається при вказаній похибці приладу; прогін 2026-10-03):

| Канал вимірювання | Параметри моделі | Внесок, % | Лишається після виміру, % | Зниження |
|---|---|---|---|---|
| **Iusb**: струм адаптера idle/RX/TX (вимірник у розриві живлення, окреме 5 В) | `rtl8812_tx_a` 19.7, `fc_usb_a` 6.0\*, `rtl8812_rx_a` 2.3, `fan_a` 1.0\*, `rtl8812_idle_a` 0 | **29.0** | 1.7 | 94 % (похибка 3..5 %) |
| **limiter**: реакція обмежувача USB Pi 5 на струм (HW, електронне навантаження) | `pi5_trip_tx_weight` 21.2, `pi5_trip_tol` 2.5, `pi5_trip_off_s` 0 | **23.7** | 2.6 | 89 % (похибка 30 %, SYNTH) |
| **T**: температура SoC `measure_temp` + біт 3 `get_throttled` | `soc_rise_c` 11.4, `soc_soft_limit_c` 4.4 | **15.9** | 0.5 | 97 % |
| **V5**: `EXT5V_V` (`pmic_read_adc`) + струм БЖ у розриві | `cable_resistance_ohm` 8.1, `psu_nominal_v` 6.6\* | **14.8** | 2.9 | 80 % |
| **events**: рампа лабораторного БЖ + dmesg | `undervolt_threshold_v` 4.1\*, `drop_rate_per_h` 2.2, `usb_dropout_v` 1.9, `usb_reenum_s` 0.1 | **8.3** | 1.7 | 80 % |
| **Ipeak**: пікове значення імпульсу TX (осцилограф) | `tx_peak_factor` 6.8 | **6.8** | 2.9 | 58 % (похибка 10 %) |
| **Vdongle**: напруга на адаптері | `usb.cable_r_ohm` 1.6 | 1.6 | 0.3 | 83 % |
| **Iboard**: струм плати | `board_load_a`, `board_idle_a` | 0.0 | 0.0 | (у цих сценаріях навантаження «active», тому не відчутно) |

\* рушій параметр **не семплює** (закріплений значенням файлу): діапазон INF із скрипта (`psu_nominal_v` 4.9..5.25 В, `undervolt_threshold_v` 4.40..4.86 В = 4.63 В ±5 % SRC, `fc_usb_a` 0.05..0.3 А, `fan_a` 0.05..0.4 А). Якщо їх виміряти, вихід моделі змінився б, але зараз рушій цього не вміє показати; це пропозиція власнику моделей додати їх як виміри (`base_sampled`), не зміна, яку зроблено тут.

Прочитання (INF з цих даних):

1. Найкраща віддача на вимірювану годину: **струм адаптера в TX** (внесок 19.7 %, зниження ≈ 95 %), далі **SoC-температура** (дешево, довго, не потребує приладів) і **опір шляху живлення** через `EXT5V_V` + струм БЖ. Усе це проводиться безпечно й без польотного обладнання.
2. Найбільший окремий параметр моделі, `usb.pi5_trip_tx_weight` (внесок 21.2 %), **жоден пасивний вимір не закриє**: це відповідь на питання «як обмежувач Pi 5 реагує на імпульсне навантаження» (середнє чи пік). Його можна визначити лише випробуванням обмежувача на реальній платі (розділ 9, крок 7): **HW only**. Не вигадувати значення з доків: SRC каже лише «1.6 А / 600 мА», поведінку запобіжника документ не описує (UNVERIFIED).
3. `power.tx_peak_factor` потребує осцилографа: USB-вимірник дає лише нижню межу (`ingest.py` так і позначає). Коефіцієнт 58 % зниження відображає похибку 10 % на пік.
4. `usb.cable_r_ohm` (1.6 %) і `usb_reenum_s` (0.1 %) у цій моделі майже не рухають вихід: їх вимірювати лише попутно (другий вимірник уже стоїть).
5. Шум Монте-Карло відносно малий (напр. `usb_dropout` 0.01..0.025, `availability` 0.004..0.043); параметри з часткою < 1 % у таблиці параметрів позначено `within_mc_noise`.

Повний вивід (параметри, діапазони, шум) відтворюється `tests/sim/powerlab/run.sh --sensitivity`; дані 2026-10-03: `usb.pi5_trip_tx_weight` 21.2 %, `rtl8812_tx_a` 19.7, `soc_rise_c` 11.4, `cable_resistance_ohm` 8.1, `tx_peak_factor` 6.8, `psu_nominal_v` 6.6\*, `fc_usb_a` 6.0\*, `soc_soft_limit_c` 4.4, `undervolt_threshold_v` 4.1\*, `pi5_trip_tol` 2.5, `rtl8812_rx_a` 2.3, `drop_rate_per_h` 2.2, `usb_dropout_v` 1.9, `usb.cable_r_ohm` 1.6, `fan_a` 1.0, решта ≈ 0. Модель змінюється (її правлять паралельно), тому рейтинг треба перезапускати перед днем заліза; числа тут це знімок.


## 9. ПЛАН вимірювань на день заліза (порядок, прилади, формат запису)

Порядок враховує розділ 8 (віддача), ризик (безпечне першим) і залежності. Чого саме не вистачає моделі: Iusb, T, V5 дають ~60 % внеску, бюджет часу на них найвигідніший.

**Передумови й безпека (AGENTS §3, BENCH-HARDWARE §6).** Без літального апарата, FC/ESC/батарея від'єднані; адаптер із підключеними антенами (SRC wfb-ng Setup-HOWTO: без антен не вмикати), потужність мінімальна або через атенюатор на столі (INF, BENCH-HARDWARE §6); тести з undervoltage робити на запасній SD-карті; лабораторний БЖ не підключати до апарата; `bench/gs_mav.py` і `bench/fake_fc.py` не використовувати. Pi 5 має працювати з активним кулером (рекомендація `docs/CONTOUR-GS.md`, INF).

**Прилади.** Два USB-вимірники з журналом у CSV, ≥ 40 Гц (нижче цього `tx_peak_factor` не виводиться): один між БЖ і Pi (`psu`), один у розриві кабелю біля адаптера (`dongle`); мультиметр; регульований лабораторний БЖ з обмеженням струму (рампа); окремий БЖ 5 В для живлення адаптера під час вимірювання його струму; термометр кімнатної температури; осцилограф (за наявності, для піку). Для кроку 7 потрібне програмоване навантаження (імпульсне).

**Єдиний каталог запису** `bench/out/ГГГГ-ММ-ДД/` і файли (формати з розділу 3):

| Файл | Як зібрати |
|---|---|
| `notes.md` | марка/номінал БЖ, довжина й переріз кабелів, розташування вимірників, ambient (°C), txpower, `uname -r`, чи стоїть кулер |
| `doctor-<крок>.json` | `bench/doctor.sh bench/out/.../doctor-krok1.json` на початку кожного кроку |
| `pmic.log` | `while :; do echo "=== $(date -u +%FT%T.%3NZ)"; vcgencmd get_throttled; vcgencmd measure_temp; vcgencmd pmic_read_adc EXT5V_V; sleep 0.2; done >> pmic.log` (лише читання; реальна частота опитування UNVERIFIED, дивитись інтервали міток) |
| `dmesg.txt` | `dmesg -w > dmesg.txt` (годинник ядра; зсув до UTC береться з doctor: `utc − uptime`) або `journalctl -k -f -o short-iso` |
| `meter_psu.csv`, `meter_dongle.csv` | експорт ПЗ вимірників; перед першим запуском один раз звірити заголовок із `--meter-cols`; зсув годинника вимірника й Pi записати в `notes.md` і дати `--meter-offset-s` |
| `windows.txt` | рядки `початок,кінець,мітка` в UTC; ставити позначки командою `date -u +%FT%T.%3NZ` на початку/кінці кожної фази (мітка `rtl8812:tx`, `board_pi5:idle`, `soc:load` …) |
| `wfb.jsonl` | `nc 127.0.0.1 8103 > wfb.jsonl` (порт `api_port` GS-профілю за замовчуванням, SRC `wfb_ng/conf/master.cfg`; реальний порт перевірити в конфігу образу); `wfb-cli` для цього непридатний (curses) |

**Кроки.**

0. **Базовий знімок** (5 хв). `bench/doctor.sh`; `vcgencmd get_config usb_max_current_enable`; фото етикетки БЖ. Критерій: `dt_chosen_power.max_current` і `usb_max_current_enable` відомі; `ingest.py --doctor ... --claim-psu-a ...` не дає K2/K3. Оновлює: `scenario cfg hint`.
1. **Струм адаптера** (канал Iusb, 20 хв; найвища віддача). Адаптер від окремого БЖ 5 В через вимірник `dongle` (не через Pi: обмежувач Pi 5 не заважає вимірюванню). Вікна `rtl8812:idle`, `rtl8812:rx`, `rtl8812:tx` по 60 с, ≥ 3 повтори, TX на txpower, що буде в польоті, і на мінімальному. Те саме для FC (`fc:`) і вентилятора (`fan:`) окремо. Критерій: ≥ 5 відліків у кожному вікні, розкид медіан між повторами < 3 %. Оновлює `power.devices.*_a`; `tx_peak_factor` (нижня межа) при ≥ 40 Гц.
2. **Піковий коефіцієнт** (канал Ipeak, 10 хв, осцилограф за наявності). Пік струму TX на осцилографі / середнє вимірника. Записати значення в оверлей вручну як `{"value": x, "source": "scope ..."}` (ingest його не виводить). Оновлює `power.tx_peak_factor`.
3. **Шлях живлення Pi** (канал V5, 25 хв). Вимірник `psu` між БЖ і Pi, `pmic.log` увімкнено, **без адаптера**: `board_pi5:idle` 60 с, далі три рівні навантаження ядер (`stress-ng --cpu 1/2/4`; наявність `stress-ng` UNVERIFIED) по 60 с з мітками `board_pi5:load`; розкид струму ≥ 0.3 А для регресії, а з напругою вимірника достатньо одного рівня. Критерій: ≥ 3 вікна. Оновлює `power.cable_resistance_ohm`, `power.psu_nominal_v`, `power.boards.pi5.board_*_a`. Змінити кабель/БЖ і повторити, якщо опір > 0.2 Ом.
4. **Адаптер через Pi** (канал Vdongle, 10 хв). Вимірник `dongle` біля адаптера, живлення від Pi, `rtl8812:rx` і `rtl8812:tx`, `pmic.log` увімкнено. Оновлює `usb.cable_r_ohm` (верхня межа). Тут же перевірити K8: чи Pi 5 при струмі вище 0.6 А без `usb_max_current_enable` взагалі щось робить (рядок over-current, відключення, `over_current_count`).
5. **Температура** (канал T, 15 хв, паралельно кроку 4/6). Реальне навантаження декодування, закритий корпус, кулер за планом; `soc:load` ≥ 5 хв до усталення + `--ambient-c`. Якщо SoC дійде до 80 °C, біт 3 дасть `hw.soc_soft_limit_c`; не доводити до небезпечного перегріву штучно. Оновлює `hw.soc_rise_c`.
6. **Рампа лабораторного БЖ** (канал events, 20 хв, **запасна SD, адаптер без FC**). Напругу зменшувати повільно (≲ 10 мВ/с) 5.1 → 4.2 В при увімкненому `pmic.log` (≥ 5 Гц) і `dmesg -w`, потім відновити. Не з підключеним апаратом. Оновлює `power.undervolt_threshold_v`, `power.usb_dropout_v` (обгортки). `usb_reenum_s` береться з автоматичних спрацювань (крок 7), не з ручного відключення.
7. **Обмежувач USB Pi 5** (HW, 30 хв, найбільший окремий невідомий параметр). На порт USB Pi 5 (не з адаптером) підключити програмоване навантаження: сходинки 0.3 → 2.0 А з витримкою 2 с та імпульсний режим (5 Гц, 30 %, піки до 2 А); записати dmesg, `usb_over_current_detected`, `over_current_count`, чи відключається порт, час повернення. Це єдиний спосіб визначити `usb.pi5_trip_*`; результат вносити в оверлей **вручну** з поясненням, ingest цього не виводить.
8. **Soak** (за часом, 1..24 год, за потреби). Радіо в роботі на мінімальній потужності, `dmesg -w`; `--soak-window`. Оновлює `usb.drop_rate_per_h` лише при ≥ 1 події.
9. **Підсумок.** `ingest.py` по всіх файлах → `overlay.json` (null лишаються null) → `calib.py whatif overlay.json --scenario nominal_pi5_5a_150m` і `pi5_3a_weak_psu`; прочитати `findings` (K1..K11) до того, як довіряти оверлею; оновити golden свідомо, не підганяти.

**Критерій готовності дня:** кроки 0, 1, 3, 4 завершені (ключі `power.devices.rtl8812_*_a`, `power.cable_resistance_ohm` отримали `source` і мітку часу), немає непояснених CONTRADICTION. Решта кроків підвищують точність і закривають `usb.pi5_trip_*` та пороги.


## 10. Чого лабораторія не доводить (HW only, чесно)

1. **Реальні струми RTL8812AU/EU** (idle/RX/TX, пік імпульсу). Єдиний знайдений текст (SRC wfb-ng Setup-HOWTO) дає лише оцінку автора «~1.6 А в імпульсі» для AWUS036ACH; даташита з числами немає. Усі `rtl8812_*_a` у моделі UNMEASURED; генератор обирає «істину» довільно (SYNTH).
2. **Реальна поведінка обмежувача Pi 5**: чи він відключає порт, чи відсікає струм, спільний він чи на порт, порогове значення й толеранс, час вимкнення, умови `latch`. Документ каже лише 1.6 А / 600 мА (SRC); автомат `Pi5UsbLimiter` у моделі це INF. Повідомлення ядра при спрацюванні на RP1/xHCI може відрізнятися від `over-current condition` (UNVERIFIED).
3. **Точність і сенс АЦП PMIC** (`EXT5V_V`): чи збігається з порогом детектора 4.63 В, частота й шум опитування (UNVERIFIED). `power.undervolt_threshold_v` з обгортки це не довірчий інтервал.
4. **Поведінку ядра/драйвера при brown-out** (чи адаптер піднімається, як довго працює `wfb-ng` після відновлення), реальний час повторної енумерації (`power.usb_reenum_s` вимірює лише видиме ядром remove→add).
5. **Тепловий режим**: стала часу SoC (у тестах 8 с, SYNTH), реальний вплив кулера й корпусу.
6. **Кодування `/proc/device-tree/chosen/power/*`** (big-endian u32 це припущення), вигляд відповіді `get_config usb_max_current_enable`, точні рядки `dmesg -T`/`journalctl` на цільовій системі.
7. **Точні формати конкретних USB-вимірників**: парсер загальний, перший реальний експорт може вимагати `--meter-cols`.
8. **Мережевий бік**: `wfb.jsonl` лише описує втрати/RSSI у момент випробування; корелювати їх з USB-подіями можна, але лабораторія не доводить, що втрата кадрів спричинена живленням.
9. Усе, що тут помічено SYNTH (числа сценаріїв, сигма шумів, імпульс TX, сталі часу): це планування, не факт.

## 11. UNVERIFIED (повний список)

1. Двійкове кодування `chosen/power/*`; відповідь `vcgencmd get_config usb_max_current_enable`.
2. Точність і частота опитування `pmic_read_adc`; наявність `EXT5V_V` на Pi 4/3B+ (команда описана для Pi 5; для Pi 4 PMIC-функції згадані в whitepaper RP-004340-WP, текст не прочитано: PDF не вдалося розібрати інструментами сесії).
3. Рядки ядра: префікси `hwmon hwmonN:`/`usb usbN-portM:` (INF із dev_printk), `Under-voltage detected!` у старих ядрах, формат `dmesg -T`/`journalctl` на цільовому образі, повідомлення over-current на Pi 5.
4. Назва `xhci-hcd` і форма `lsusb -t` на Pi 5 (формат рядка SRC, значення драйверів ні).
5. Мітки/імена hwmon зовнішнього I²C-монітора (INA3221 і т. п.) на цільовій системі; формат `curr*`/`power*` залежить від драйвера.
6. Порти `stats_port`/`api_port` у встановленому `wfb-ng` (SRC лише за замовчуванням master.cfg); `/proc/net/rtl88x2eu/.../thermal_state` для RTL8812AU.
7. Чи `kernel.dmesg_restrict` блокує `dmesg` без root на цільовому образі (тому `dmesg_rc`).
8. Наявність `stress-ng`, `nc`, `chrony` на образі.
9. Що саме очищає маска `0xffff` у `RPI_FIRMWARE_GET_THROTTLED` (усі липкі біти 16..19 чи лише біт 16): генератор очищає всі чотири (INF).
10. Чи читання `vcgencmd get_throttled` саме по собі очищає липкі біти (у документації не сказано).

