# Параметризовані моделі: радіоканал, живлення/USB, затримка відео

Станом на 2026-10-03. Код: `tests/sim/models/`. Запуск: `tests/sim/models/run.sh --check` (менше 5 с, без мережі й root). Позначки достовірності як у `CLAUDE.md`: **SRC** (URL прочитано в цій сесії), **REPO** (шлях у репозиторії), **INF** (власний висновок або підручникова формула), **UNMEASURED** (заглушка, її треба виміряти на стенді), **MEASURED_SIM** (з прогону `tests/sim`), **MEASURED_HW** (накладка з виміру на залізі), **UNVERIFIED** (не перевірено).

## 1. Що це і чого це не доводить

Це **моделі**, а не докази. Вони дають змогу ще до заліза (а) порівняти конфігурації (MCS, FEC k/n, плата, БЖ, декодер, fps) і (б) знайти домінантний ризик (що першим «ламається»: ефір, USB-бюджет чи декодер). Вони **не** сертифікують ні дальність, ні затримку, ні стійкість живлення: більшість чисел апаратури тут заглушки (`UNMEASURED`, 48 штук), а діапазони (min/typ/max) є висновком INF, а не вимірами. Результат модельного запуску завжди друкує в кінці рядок `# depends on N UNMEASURED parameter(s): ...`: це перелік заглушок, від яких залежить саме цей вивід.

| Модель | Файл | Що дає |
|---|---|---|
| РЧ | `rf_model.py` | втрати на трасі, SNR, PER по MCS 802.11n/ac, залишкові втрати відео після wfb-ng FEC, таблиця «відстань → втрати», максимальна дальність при втратах < 1 % |
| Адаптер | `relay_from_model.py` | сценарій «відстань D, MCS m, FEC k/n» → аргументи `tests/sim/air_relay.py` |
| Живлення | `power_model.py` | бюджет 5 В і USB по платах Pi 3B+/4/5, ризик brown-out, JSON-події «USB-пристрій відвалився», декодування `vcgencmd get_throttled`, розбір логів, імпорт CSV з USB-вимірника |
| Затримка | `latency_budget.py` | бюджет скло-до-скла: min/typ/max по доданках, домінантний доданок, протокол виміру |
| Спільне | `common.py`, `params.json` | завантаження параметрів, перевизначення, перевірка схеми |

## 2. Параметри: один файл, походження для кожного

Усі параметри лежать у `tests/sim/models/params.json` (секції `video`, `rf`, `power`, `latency`). Кожен параметр має `value`, `unit`, `provenance`, для SRC/REPO/MEASURED ще `source` (URL, що його прочитано, або шлях), за потреби `min`/`max` і `note`. Кожна секція має `assumptions` (припущення) і `calibration` (який вимір на стенді фіксує який параметр, команда, інструмент). Тест перевіряє схему: параметр без `provenance` відхиляється, SRC без https-URL відхиляється, кожен UNMEASURED мусить бути в якомусь записі `calibration`.

**Виміряне значення перекриває модель без змін коду** (порядок, пізніше сильніше):

| Спосіб | Приклад | Походження в результаті |
|---|---|---|
| `MODEL_PARAMS=<файл>` | цілий альтернативний `params.json` | як у файлі |
| `MODEL_MEASURED=<накладка.json>` | `{"rf.tx_power_dbm": {"value": 17.5, "source": "вимірювач 2026-10-05"}}` | `MEASURED_HW`; параметр зникає зі списку `UNMEASURED` у виводі |
| `MODEL_SET='k=v;k=v'` | `MODEL_SET='rf.path_loss_exponent=3.0'` | `OVERRIDE` |
| `--set k=v` (повторюється) | `rf_model.py range --set rf.tx_power_dbm=23` | `OVERRIDE` |

`power_model.py ingest --csv meter.csv > measured.json` будує накладку з CSV USB-вимірника (`device,state,amps`), далі `MODEL_MEASURED=measured.json`.

### Таблиця параметрів (згенеровано з `params.json`; істина в JSON)

| Ключ | Значення (мін..макс) | Одиниця | Походження | Джерело |
|---|---|---|---|---|
| `video.bitrate_kbps` | 4000 | kbit/s | REPO | bench/lib.sh |
| `video.fps` | 30 | fps | REPO | bench/lib.sh |
| `video.width` | 1280 | px | REPO | bench/lib.sh |
| `video.height` | 720 | px | REPO | bench/lib.sh |
| `video.iframe_ratio` | 6.0 (3.0..10.0) | x | UNMEASURED |  |
| `rf.freq_mhz` | 5745 | MHz | INF |  |
| `rf.bandwidth_mhz` | 20 | MHz | SRC | gh:svpcom/wfb-ng/master/wfb_ng/conf/master.cfg |
| `rf.vht` | False | bool | SRC | gh:svpcom/wfb-ng/master/wfb_ng/conf/master.cfg |
| `rf.mcs_index` | 1 |  | SRC | gh:svpcom/wfb-ng/master/wfb_ng/conf/master.cfg |
| `rf.stbc` | 1 | streams | SRC | gh:svpcom/wfb-ng/master/wfb_ng/conf/master.cfg |
| `rf.ldpc` | 1 | bool | SRC | gh:svpcom/wfb-ng/master/wfb_ng/conf/master.cfg |
| `rf.short_gi` | False | bool | SRC | gh:svpcom/wfb-ng/master/wfb_ng/conf/master.cfg |
| `rf.fec_k` | 8 | packets | SRC | gh:svpcom/wfb-ng/master/wfb_ng/conf/master.cfg |
| `rf.fec_n` | 12 | packets | SRC | gh:svpcom/wfb-ng/master/wfb_ng/conf/master.cfg |
| `rf.tx_power_dbm` | 20.0 (10.0..27.0) | dBm | UNMEASURED |  |
| `rf.tx_antenna_gain_dbi` | 2.0 (0.0..8.0) | dBi | UNMEASURED |  |
| `rf.rx_antenna_gain_dbi` | 2.0 (0.0..8.0) | dBi | UNMEASURED |  |
| `rf.misc_loss_db` | 1.0 (0.0..6.0) | dB | UNMEASURED |  |
| `rf.noise_figure_db` | 6.0 (4.0..10.0) | dB | UNMEASURED |  |
| `rf.implementation_loss_db` | 3.0 (1.0..6.0) | dB | UNMEASURED |  |
| `rf.mcs_snr_offset_db` | [0]*10 | dB | UNMEASURED |  |
| `rf.ldpc_gain_db` | 0.0 (0.0..2.0) | dB | UNMEASURED |  |
| `rf.diversity_gain_db` | 0.0 (0.0..4.0) | dB | UNMEASURED |  |
| `rf.path_loss_exponent` | 2.0 (2.0..3.5) |  | INF |  |
| `rf.ref_distance_m` | 1.0 | m | INF |  |
| `rf.fading_model` | none |  | INF |  |
| `rf.rician_k_db` | 6.0 (0.0..15.0) | dB | UNMEASURED |  |
| `rf.fading_draws` | 400 |  | INF |  |
| `rf.fading_seed` | 1 |  | INF |  |
| `rf.interference_dbm` | None (-95.0..-60.0) | dBm | UNMEASURED |  |
| `rf.floor_per` | 0.0 (0.0..0.02) |  | UNMEASURED |  |
| `rf.loss_model` | iid |  | INF |  |
| `rf.ge_mean_burst_frames` | 4.0 (1.0..30.0) | frames | UNMEASURED |  |
| `rf.payload_bytes` | 1400 | bytes | REPO | bench/video-src.sh |
| `rf.wfb_overhead_bytes` | 56 | bytes | SRC | gh:svpcom/wfb-ng/2fe252b2f451c1ccfb16968e064fe1cdb18baaa0/src/wifibroadcast.hpp |
| `rf.mac_access_us` | 100.0 (20.0..250.0) | us | UNMEASURED |  |
| `rf.target_residual` | 0.01 |  | INF |  |
| `rf.max_search_m` | 30000.0 | m | INF |  |
| `rf.relay_jitter_ms` | 0.3 (0.0..3.0) | ms | UNMEASURED |  |
| `rf.relay_extra_delay_ms` | 0.0 (0.0..5.0) | ms | UNMEASURED |  |
| `power.psu_nominal_v` | 5.1 | V | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.undervolt_threshold_v` | 4.63 | V | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.cable_resistance_ohm` | 0.15 (0.05..0.35) | ohm | UNMEASURED |  |
| `power.margin_warn_frac` | 0.2 |  | INF |  |
| `power.usb_dropout_v` | 4.4 (4.2..4.6) | V | UNMEASURED |  |
| `power.usb_reenum_s` | 3.0 (1.0..10.0) | s | UNMEASURED |  |
| `power.tx_peak_factor` | 1.5 (1.0..2.0) | x | UNMEASURED |  |
| `power.boards.pi3bp.psu_recommended_a` | 2.5 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi3bp.usb_budget_a` | 1.2 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi3bp.usb_budget_weak_psu_a` | 1.2 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi3bp.board_idle_a` | 0.3 (0.3..0.5) | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi3bp.board_active_a` | 0.5 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi3bp.board_load_a` | 0.85 (0.85..1.34) | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi4.psu_recommended_a` | 3.0 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi4.usb_budget_a` | 1.2 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi4.usb_budget_weak_psu_a` | 1.2 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi4.board_idle_a` | 0.6 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi4.board_active_a` | 0.6 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi4.board_load_a` | 1.2 (1.2..1.25) | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi5.psu_recommended_a` | 5.0 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi5.usb_budget_a` | 1.6 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi5.usb_budget_weak_psu_a` | 0.6 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi5.board_idle_a` | 0.8 (0.5..0.8) | A | UNMEASURED |  |
| `power.boards.pi5.board_active_a` | 0.8 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `power.boards.pi5.board_load_a` | 1.6 (1.2..2.2) | A | UNMEASURED |  |
| `power.devices.rtl8812_idle_a` | 0.3 (0.15..0.5) | A | UNMEASURED |  |
| `power.devices.rtl8812_rx_a` | 0.45 (0.25..0.7) | A | UNMEASURED |  |
| `power.devices.rtl8812_tx_a` | 0.9 (0.5..1.6) | A | UNMEASURED |  |
| `power.devices.fc_usb_a` | 0.1 (0.05..0.3) | A | UNMEASURED |  |
| `power.devices.webcam_a` | 0.25 (0.1..0.5) | A | UNMEASURED |  |
| `power.devices.fan_a` | 0.2 (0.05..0.4) | A | UNMEASURED |  |
| `power.devices.csi_camera_a` | 0.25 | A | SRC | gh:raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc |
| `latency.capture_frames` | 1.0 (0.5..1.5) | frames | UNMEASURED |  |
| `latency.encode_frames` | 1.0 (0.3..2.0) | frames | UNMEASURED |  |
| `latency.usb_rx_ms` | 1.0 (0.1..8.0) | ms | UNMEASURED |  |
| `latency.network_ms` | 0.3 (0.05..2.0) | ms | UNMEASURED |  |
| `latency.jitterbuffer_ms` | 0.0 (0.0..20.0) | ms | REPO | bench/video-rx.sh |
| `latency.display_hz` | 60.0 | Hz | INF |  |
| `latency.vsync_wait_frac` | 0.5 (0.0..1.0) | refresh | INF |  |
| `latency.scanout_frac` | 0.5 (0.0..1.0) | refresh | INF |  |
| `latency.panel_ms` | 5.0 (1.0..20.0) | ms | UNMEASURED |  |
| `latency.display_queue_frames` | 1.0 (0.0..2.0) | frames | UNMEASURED |  |
| `latency.pixel_ref` | 2073600 | px | INF |  |
| `latency.sw_ref_h264_ms` | 5.0 (3.0..10.0) | ms | MEASURED_SIM | docs/TESTABILITY.md:148 |
| `latency.sw_ref_h265_ms` | 8.0 (6.0..15.0) | ms | MEASURED_SIM | docs/TESTABILITY.md:148 |
| `latency.sw_ref_pixels` | 230400 | px | MEASURED_SIM | tests/sim/video_latency.py |
| `latency.sw_decode_fraction` | 0.4 (0.2..0.6) |  | UNMEASURED |  |
| `latency.cpu_scale_vs_x86` | 2.0 (1.0..4.0) | x | UNMEASURED |  |
| `latency.decoders.pi4_h264_stateful.proc_ms_1080p` | 8 (3..20) | ms | UNMEASURED |  |
| `latency.decoders.pi4_h264_stateful.reorder_frames` | 0 (0..2) | frames | UNMEASURED |  |
| `latency.decoders.pi4_h264_stateful.queue_frames` | 1 (0..3) | frames | UNMEASURED |  |
| `latency.decoders.pi4_h265_stateless.proc_ms_1080p` | 8 (3..20) | ms | UNMEASURED |  |
| `latency.decoders.pi4_h265_stateless.reorder_frames` | 0 (0..2) | frames | UNMEASURED |  |
| `latency.decoders.pi4_h265_stateless.queue_frames` | 1 (0..3) | frames | UNMEASURED |  |
| `latency.decoders.pi5_h265_stateless.proc_ms_1080p` | 6 (2..15) | ms | UNMEASURED |  |
| `latency.decoders.pi5_h265_stateless.reorder_frames` | 0 (0..2) | frames | UNMEASURED |  |
| `latency.decoders.pi5_h265_stateless.queue_frames` | 1 (0..3) | frames | UNMEASURED |  |
| `latency.decoders.pi5_h264_sw.reorder_frames` | 0.0 (0.0..2.0) | frames | UNMEASURED |  |
| `latency.decoders.pi5_h264_sw.queue_frames` | 0.0 (0.0..3.0) | frames | UNMEASURED |  |

## 3. РЧ-модель (`rf_model.py`)

**Формули** (INF, підручникові): `FSPL(d0) = 20·log10(d0/1000) + 20·log10(f_МГц) + 32.44`; `PL(d) = FSPL(d0) + 10·n·log10(d/d0)`; `Rx = Ptx + Gtx + Grx − PL − втрати`; шум `−174 дБм/Гц + 10·log10(BW) + NF`; `SNR = Rx − 10·log10(10^(N/10) + 10^(I/10))` (завада I додається до теплового шуму, параметр `rf.interference_dbm`, `null` = немає). Шум для 20 і 40 МГц відрізняється на 3.01 дБ (перевіряє тест).

**PER за MCS.** SRC: `ns-3` `src/wifi/model/nist-error-rate-model.cc` (прочитано: `raw.githubusercontent.com/nsnam/ns-3-dev-git/master/src/wifi/model/nist-error-rate-model.cc`): BER для BPSK `0.5·erfc(√snr)`, QPSK `0.5·erfc(√(snr/2))`, M-QAM `((b−1)/(b·log2 b))·erfc(√(snr/((2(M−1))/3)))`, `b=√M`; оцінка `Pe` зверху за спектром ваг згорткового коду K=7 для швидкостей 1/2, 2/3, 3/4, 5/6; успіх кадру `(1−Pe)^(8·байтів)`. Я **перереалізував** формули й таблицю коефіцієнтів у `rf_model.py` (код ns-3 під GPL-2.0-only, наш код його не копіює дослівно; таблиці коефіцієнтів це опубліковані математичні дані, але ліцензійне питання для коефіцієнтів не вирішено юридично: UNVERIFIED). Таблицю мінімальної чутливості з IEEE 802.11n (Table 20-23) **не читано** (стандарт недоступний), тому абсолютні пороги чутливості з нього не використано; різницю між ідеальним приймачем і реальним чипом покриває параметр `rf.implementation_loss_db` (UNMEASURED, 3 дБ), а на понеділок його калібрують вимірюванням SNR при PER 10 % по кожному MCS.

Результат моделі (ідеальний приймач + 3 дБ втрат реалізації, кадр 1456 Б): SNR для PER 10 %: MCS0 6.95 дБ, MCS1 10.0, MCS2 12.9, MCS3 16.5, MCS4 19.6, MCS5 24.3, MCS6 25.6, MCS7 26.8 (`rf_model.py sweep`). Тест перевіряє опорні значення BER (BPSK `0.5·erfc(1)=0.0786496`) і те, що колін MCS0 для ідеального приймача лежить у 2..6 дБ.

**wfb-ng.** SRC (`wfb_ng/conf/master.cfg`, гілка master, HEAD був `2fe252b2`, прочитано): `bandwidth=20`, `mcs_index=1`, `stbc=1`, `ldpc=1` («лише 8812au, на обох кінцях»), `short_gi=False`, `force_vht=False`; відео `fec_k=8`, `fec_n=12`; MAVLink `fec_k=1`, `fec_n=2`; `fec_timeout=0`, `fec_delay=0`. README: FEC 8/12 відновлює 4 втрачені пакети з 12; UDP до 3993 байт; один RTP-пакет = один кадр 802.11. SRC `src/tx.cpp@2fe252b` (прочитано): інформаційні фрагменти відправляються **одразу** (`send_block_fragment` перед накопиченням), паритет шлеться після k-го; тому очікування FEC-блоку виникає лише при втраті (використано в моделі затримки). Накладні витрати кадру: заголовок 802.11 24 Б, `wblock_hdr` 9 Б (packed), `wpacket_hdr` 3 Б (SRC `wifibroadcast.hpp`), FCS 4 Б (INF), тег ChaCha20-Poly1305 16 Б (INF, не перечитувано), разом 56 Б.

**Залишкові втрати з FEC.** Блок із n фрагментів, k інформаційних; MDS-припущення (будь-які k із n відновлюють блок). iid-втрати: `residual = p · P(Bin(n−1, p) ≥ n−k)` (перевірено повним перебором для n=5, k=3). Пакетні втрати: модель Гілберта (добрий стан без втрат, поганий з повною втратою, середня довжина поганого пробігу `ge_mean_burst_frames`), точний динамічний розрахунок по позиціях блоку (перевірено перебором n=6, k=3). Завмирання: `none`, `rayleigh` (квантилі експоненційного розподілу), `rician` (K-фактор, фіксований seed, спільні випадкові числа для всіх відстаней, тому втрати монотонні по відстані).

**Еталонний сценарій 1** (MCS1, FEC 8/12, 20 МГц, 4000 кбіт/с, 12 пакетів на кадр; заглушки з таблиці вище; `golden/rf_mcs1_fec8_12.txt`):

| Відстань, м | SNR, дБ | PER кадру | Залишок після FEC | Втрата відеокадру |
|---|---|---|---|---|
| 700 | 13.5 | 3.05e-09 | 8.76e-41 | 0 |
| 1000 | 10.4 | 2.38e-02 | 2.18e-06 | 2.6e-05 |
| 1050 | 9.9 | 1.08e-01 | 2.59e-03 | 3.1e-02 |
| 1100 | 9.5 | 3.76e-01 | 2.42e-01 | 0.96 |
| 1300 | 8.1 | 1.00 | 1.00 | 1.00 |

Максимальна дальність при залишку < 1 %: **1062 м** для MCS1 8/12. Ті самі умови: без FEC (1/1) 974 м; Rayleigh 443 м; Rician (K=6 дБ) 738 м; Gilbert (серія 4 кадри) 992 м; показник втрат `n=3` замість 2 дає 104 м; 40 МГц MCS3 8/12 354 м. **Це число не дальність, а наслідок заглушок** (потужність 20 дБм, антени 2 дБі, NF 6 дБ, втрати реалізації 3 дБ, `n=2.0` вільний простір): міняти треба через калібрування (розділ 7). Корисне, що не залежить від абсолютних чисел: порядок MCS за дальністю (спадає), виграш від FEC (десятки метрів), і прапорець завантаження ефіру: при 4000 кбіт/с і FEC 8/12 на MCS0 завантаження 1.04 (`!`), тобто потік не вміщається в ефір, і діапазон для цього MCS беззмістовний.

**Завади й ELRS (лише примітка).** Завада з того ж каналу моделюється адитивним доданком `rf.interference_dbm` (її треба виміряти як підлогу шуму при вимкненому TX). ELRS ES900 працює в діапазоні 868/915 МГц, wfb-ng у 5 ГГц: частотно рознесені, тож у цій моделі ELRS не є завадою для wfb-ng (INF; регуляторні питання поза межами документа).

**Межі застосовності.** Один канал, пряма видимість із логарифмічним показником; немає рельєфу, зон Френеля, діаграм антен, частотної вибірковості, адаптації швидкості, перепередач. Завмирання незалежні від кадру до кадру; блоки FEC незалежні один від одного. 5 ГГц, 20/40 МГц, 1 просторовий потік; VHT MCS8/9 є в таблиці, MCS9 для 20 МГц відхиляється.

## 4. Адаптер до `air_relay.py` (`relay_from_model.py`)

`air_relay.py` (не змінювався) приймає лише сталі `--loss P --delay-ms D --jitter-ms J --seed S [--duration T]` (REPO, `tests/sim/air_relay.py:30-37`); файла розкладу і пакетної моделі в нього немає. Адаптер тому видає:

- `--format args`: один рядок аргументів: `air_relay.py --src air0 --dst air1 $(relay_from_model.py --distance 1050 --mcs 1 --fec 8/12)`; для еталонного сценарію це `--loss 0.107942 --delay-ms 1.0400 --jitter-ms 0.3000 --seed 7`;
- `--format cmds`: повні команди по сегментах з `--duration` (послідовний запуск), для рампи `--ramp D0:D1:СЕК:КРОКІВ`;
- `--format json`: схема `sbc-gs-relay-schedule/1` із сегментами й очікуваним залишком моделі.

Затримка кадру = час в ефірі + `rf.mac_access_us` + `rf.relay_extra_delay_ms`; джитер = `rf.relay_jitter_ms`. Реле губить **окремі кадри 802.11** (і дані, і паритет) незалежно; FEC виконує справжній `wfb_tx` (запускати з `-k`/`-n` зі сценарію, адаптер друкує підказку в stderr). Для `--loss-model ge` пакетність відтворити неможливо: адаптер видає **iid-еквівалент**, що дає той самий залишок після FEC (`rf_model.iid_equivalent`); отже veth-прогін відтворює втрати відео з моделі, а не її статистику серій. Невідтворюване: тривалість серій, залежність від MCS (реле не знає про радіо), реальний `radiotap`.

## 5. Модель живлення й USB (`power_model.py`)

**Офіційні числа** (SRC: `raspberrypi/documentation`, `documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc`, гілка master, прочитано повністю):

| Плата | Рекомендована БЖ | Макс. сумарний струм USB | Типовий струм голої плати |
|---|---|---|---|
| Pi 3 Model B+ | 2.5 A | 1.2 A | 500 мА |
| Pi 4 Model B | 3.0 A | 1.2 A | 600 мА |
| Pi 5 | 5.0 A | «1.6A (600mA if using a 3A power supply)» | 800 мА |

Цитати: «The Raspberry Pi 5 provides 1.6A of power to downstream USB peripherals when connected to a power supply capable of 5A at +5V (25W). When connected to any other compatible power supply, the Raspberry Pi 5 restricts downstream USB devices to 600mA of power.» «The Camera Module requires 250mA.» «All models require a 5.1V supply». Детектор низької напруги: «will detect if the supply voltage drops below 4.63 V (±5%). This will result in an entry being added to the kernel log.» Таблиця споживання за навантаження (Pi 3B / Pi 4B): очікування 0.30 / 0.6 A, стрес (макс.) 1.34 / 1.25 A (стовпця для 3B+ немає, тому 3B+ idle/load у `params.json` це значення 3B з приміткою). Для Pi 5 в документі є лише «typical bare-board active 800 mA»; idle і load Pi 5 це UNMEASURED.

**Струм адаптера RTL8812AU/EU.** Єдиний знайдений текст (SRC, wfb-ng wiki `Setup-HOWTO.md`, прочитано): автор не має РЧ-вимірювача і за споживанням AWUS036ACH оцінив максимум «~1.6А in pulse» при `rtw_tx_pwr_idx_override=63`; радить живити адаптер від BEC ≥ 5 А з конденсатором ≥ 470 мкФ і писати, що «cheap / thin / unshielded USB cables» дають помилки FEC. Даташита RTL8812AU/EU з цифрами idle/RX/TX не знайдено, тому `rtl8812_idle/rx/tx_a` це UNMEASURED із діапазонами INF (TX 0.5..1.6 А, де верхня межа з тієї цитати). Імпульс 1.6 А перевищує бюджет USB Pi 4 (1.2 А) сам по собі: це головний висновок моделі, який треба перевірити вимірюванням.

**Модель.** `total = плата + Σ пристроїв`; `usb = Σ USB-пристроїв` (адаптер, FC, вебкамера); вентилятор і CSI-камера рахуються проти БЖ, але не проти USB-бюджету; `V_плати = 5.1 − total·R_кабелю` (одне зосереджене R, UNMEASURED 0.15 Ом). Прапорці: `PSU_OVER` (total > БЖ), `LOW_PSU_MARGIN` (запас < 20 %), `USB_OVER` (usb > бюджет), `UNDERVOLT` (V < 4.63). Вердикт `FAIL`, якщо є `PSU_OVER`, `USB_OVER` або `UNDERVOLT`; `WARN` при `LOW_PSU_MARGIN`. Бюджет USB Pi 5: 1.6 А для БЖ ≥ 5 А або `usb_max_current_enable=1`, інакше 0.6 А. Чи справді плата обмежує/відключає порт при перевищенні й як саме (спільний запобіжник чи на порт), документ не каже: **UNVERIFIED**; модель трактує бюджет як жорсткий.

**Еталонний сценарій 2** (`golden/power_pi5_psu.txt`, адаптер TX, FC, вебкамера, вентилятор):

| Сценарій | Усього, А | Запас БЖ, А | USB, А | Бюджет USB, А | Запас USB, А | V плати | Вердикт |
|---|---|---|---|---|---|---|---|
| Pi 5, БЖ 3 А | 2.250 | +0.750 | 1.250 | 0.60 | −0.650 | 4.762 В | FAIL (`USB_OVER`) |
| Pi 5, БЖ 5 А, пік TX (×1.5) | 2.700 | +2.300 | 1.700 | 1.60 | −0.100 | 4.695 В | FAIL (`USB_OVER`) |
| Pi 4, БЖ 3 А, пік TX, FC, вентилятор | 2.250 | +0.750 | 1.450 | 1.20 | −0.250 | 4.762 В | FAIL (`USB_OVER`) |

Для Pi 5 із БЖ 5 А без піку (середній TX 0.9 А) запас USB +0.350 А, вердикт OK. Тобто модель вказує домінантний ризик: не ємність БЖ, а **бюджет USB**, і особливо імпульс адаптера; усе залежить від трьох заглушок (`rtl8812_tx_a`, `tx_peak_factor`, `cable_resistance_ohm`), які калібруються USB-вимірником і осцилографом.

**Таблиця бітів `vcgencmd get_throttled`** (SRC: `documentation/asciidoc/computers/os/graphics-utilities.adoc`, розділ `get_throttled`, прочитано): біт 0 `0x1` Undervoltage detected; 1 `0x2` Arm frequency capped; 2 `0x4` Currently throttled; 3 `0x8` Soft temperature limit active; 16 `0x10000` Undervoltage has occurred; 17 `0x20000` Arm frequency capping has occurred; 18 `0x40000` Throttling has occurred; 19 `0x80000` Soft temperature limit has occurred. `power_model.py throttled 0x50005` друкує «bit0, bit2, bit16, bit18». Парсер логу (`throttled --file`, `report --throttled-log`) бере рядки `throttled=0x…`, голі hex-рядки та рядки ядра «Undervoltage detected» / «Voltage normalised» (точний текст повідомлень ядра: INF, регістр і дефіс нечутливі); `report --throttled-log` порівнює вердикт моделі з виміром: `MODEL_OPTIMISTIC` (виміряно просідання, модель каже не FAIL), `MODEL_PESSIMISTIC`, `CONSISTENT`.

**Схема подій для віртуальних USB-тестів** (`power_model.py brownout --scenario pi5_3a_tx|pi4_3a_dip|pi5_5a_ok` або `--scenario-file F.json`; детерміновано від `--seed`):

```json
{"schema": "sbc-gs-usbfault/1", "scenario": "pi4_3a_dip", "board": "pi4", "seed": 1, "duration_s": 30.0,
 "model": {"psu_a": 3.0, "usb_budget_a": 1.2, "undervolt_threshold_v": 4.63, "usb_dropout_v": 4.4, "usb_reenum_s": 3.0},
 "events": [
  {"t_s": 15.0, "kind": "undervoltage", "v": 4.39},
  {"t_s": 15.0, "kind": "throttled", "value": "0x50005", "bits": ["Undervoltage detected", "Currently throttled", "Undervoltage has occurred", "Throttling has occurred"]},
  {"t_s": 15.0, "kind": "usb_drop", "device": "rtl8812", "bus_port": "1-1.1", "reason": "undervoltage", "v": 4.39, "i_usb_a": 1.0},
  {"t_s": 15.4, "kind": "voltage_ok", "v": 4.875},
  {"t_s": 18.0, "kind": "usb_return", "device": "rtl8812", "bus_port": "1-1.1"} ]}
```

Види подій: `undervoltage` / `voltage_ok` (`v`), `throttled` (`value` hex і `bits` назви з таблиці вище, біти 16/18 липкі), `usb_drop` (`device`, `bus_port`, `reason` ∈ `undervoltage`/`overcurrent`, `v`, `i_usb_a`), `usb_return` (повторна енумерація через `usb_reenum_s`). Гарантії (перевіряє тест): події впорядковані за `t_s`; `usb_return` лише після `usb_drop` того ж пристрою; пристрій не падає двічі підряд без повернення; значення `throttled` збігається з розшифровкою біт. Файл сценарію: `{"name","board","psu_a","usb_max_current","duration_s","load","devices":[{"id","kind":"rtl8812|fc|webcam","bus_port"}],"adapter_profile":[{"t_s","state":"idle|rx|tx"}],"dips":[{"t_s","depth_v","duration_s"}],"extra_5v":["fan"],"drop_order":[id]}`. Політика «при перевищенні USB-бюджету скидається пристрій із найбільшим струмом, при просіданні напруги нижче `usb_dropout_v` скидається перший у `drop_order`» це **INF**, а не відоме поводження заліза (`usb_dropout_v` UNMEASURED).

## 6. Бюджет затримки скло-до-скла (`latency_budget.py`)

`скло-до-скла = камера + кодування + ефір + очікування FEC + USB/rx + мережа + jitter-буфер + декодування + дисплей`. Кожен доданок має min/typ/max; `TOTAL` це суми по стовпцях (max це межа, а не перцентиль).

| Доданок | Формула | Походження |
|---|---|---|
| камера, кодування | кадри × період кадру | UNMEASURED (камера й SoC OpenIPC, діапазони INF) |
| ефір | пакетів на кадр × (ефір + доступ); min без паритету, typ з половиною паритету, max I-кадр × n/k | формули `rf_model.py`; `rf.mac_access_us` UNMEASURED |
| очікування FEC | 0 без втрат; з `--lossy`: typ половина, max повний блок `(k−1)·інтервал + (n−k)·t_пакета` | SRC `tx.cpp` (дані йдуть одразу, паритет після k-го) |
| USB/rx, мережа | мс | UNMEASURED |
| jitter-буфер | 0 | REPO `bench/video-rx.sh` (jitterbuffer немає); 10 з `docs/CHAINS.md`, 20 типова практика (не підтверджено) |
| декодування (апаратне) | `proc_ms_1080p × пікселі/1080p + (reorder + queue) × період кадру` | числа UNMEASURED; механізми SRC нижче |
| декодування (ПЗ, Pi 5 H.264) | `sw_ref × частка декодування × пікселі/пікселі_еталона × CPU-коефіцієнт` | `sw_ref` MEASURED_SIM; решта UNMEASURED |
| дисплей | (очікування vblank + розгортка + черга кадрів) × період + панель | `vsync_wait`, `scanout` INF; панель, черга UNMEASURED |

**Що кажуть джерела про декодери** (усе прочитано в цій сесії):

- Pi 4/5, HEVC, stateless: SRC `raspberrypi/linux` гілка `rpi-6.12.y`, `drivers/media/platform/raspberrypi/hevc_dec/` (комміт `a553c46`). **Файли називаються `hevc_d*.c`, не `rpivid*`** (запити на `rpivid.c` дають 404; ім'я драйвера в `Kconfig`: `VIDEO_RPI_HEVC_DEC`, модуль `rpi-hevc-dec`, описано як stateless V4L2 декодер). `hevc_d.h`: `HEVC_D_DEC_ENV_COUNT 6` (шість середовищ декодування одночасно), `HEVC_D_P1BUF_COUNT 3`, `HEVC_D_P2BUF_COUNT 3`; `hevc_d_video.c`: `HEVC_D_MAX_WIDTH/HEIGHT 4096`, `dst_vq->min_queued_buffers = 1`. Часових характеристик на кадр у коді немає.
- GStreamer (`gst-plugins-bad`, `sys/v4l2codecs/gstv4l2codech265dec.c` і `gst-libs/gst/codecs/gsth265decoder.c`, гілка main): `get_preferred_output_delay`: для live-конвеєра **0**, інакше 1 кадр; кількість буферів входу `1 + MAX(1, render_delay)`; мінімальна затримка HEVC-декодера це `sps_max_num_reorder_pics` кадрів (+ вихідна затримка), максимальна `(max_dpb_size + вихідна затримка)` кадрів; для H.264 `frames_delay = max_dpb_size`, якщо рівень «bump» не низький. Звідси `reorder_frames`: 0 для P-кадрового low-delay потоку, більше при B-кадрах (залежить від кодера majestic, UNMEASURED).
- Pi 4, H.264, stateful: SRC `drivers/staging/vc04_services/bcm2835-codec/bcm2835-v4l2-codec.c` (`rpi-6.12.y`): роль `DECODE` із форматом `V4L2_PIX_FMT_H264`, `V4L2_CID_MIN_BUFFERS_FOR_CAPTURE` = 1. Чи цей вузол видно як `v4l2h264dec` на поточній Pi OS, і яка його затримка: **UNVERIFIED** (відкрите питання `docs/CHAINS.md` п. 1; сторінки raspberrypi.com повертають 403, форум теж).
- Pi 5 H.264: апаратного декодера в Pi 5 немає за `docs/PI-PORT.md` (SNIP/INF, офіційну сторінку не прочитано: 403), тому ПЗ-декодування.

**ПЗ-доданок, MEASURED_SIM.** `docs/TESTABILITY.md:148`: `tests/sim/video_latency.py` на x86, лупбек, 640×360@30: h264 `min=3 p50=5 p95=10 max=30 мс`, h265 `min=6 p50=8 p95=15 max=41 мс`; **цей час включає програмне кодування x264/x265**, тому для Pi це верхня довідкова оцінка, масштабується на частку декодування (0.4, діапазон 0.2..0.6), відношення пікселів і CPU-коефіцієнт (2.0, діапазон 1..4), обидва UNMEASURED. Параметри `sw_ref_h264_ms=5.0` (3..10), `sw_ref_h265_ms=8.0` (6..15) взято як p50 і p95.

**Еталонний сценарій 3** (Pi 4, HEVC, 1920×1080@30, 8000 кбіт/с, MCS3, FEC 8/12, `--lossy`; `golden/latency_pi4_h265_1080p.txt`): ефірне завантаження 0.63.

| Доданок | min, мс | typ, мс | max, мс |
|---|---|---|---|
| capture | 16.7 | 33.3 | 50.0 |
| encode | 10.0 | 33.3 | 66.7 |
| radio | 14.2 | 17.8 | 127.0 |
| fec_wait | 0.0 | 6.1 | 12.2 |
| usb_rx | 0.1 | 1.0 | 8.0 |
| network | 0.1 | 0.3 | 2.0 |
| jitter_buffer | 0.0 | 0.0 | 20.0 |
| decode | 3.0 | 41.3 | 186.7 |
| display | 1.0 | 38.3 | 86.7 |
| **TOTAL** | **45.0** | **171.5** | **559.2** |

Домінантний доданок: `decode` (typ і max). Це **не передбачення затримки Pi 4**: 171.5 мс це сума заглушок. `latency_budget.py matrix` дає той самий розріз для Pi 4/5 × h264/h265 × 30/60 fps; на 60 fps домінантним стає `display` (typ), бо період кадру короткий, а `panel_ms` і черга дисплея не залежать від fps. Висновок, який не залежить від точних чисел: порядок величини вирішують декодер, дисплей і кодек камери, а не ефір і FEC (при завантаженні ефіру < 1).

**Протокол виміру скло-до-скла на стенді (HW, `latency_budget.py protocol`):**

1. Зафіксувати кодек, роздільну здатність, fps, бітрейт, MCS, FEC, елемент декодера, sink (`kmssink`/`waylandsink`), режим дисплея (`modetest -M vc4 -c`).
2. Метод A (кращий, ~1 мс): світлодіод (1-2 Гц, меандр від GPIO або генератора) перед об'єктивом камери AIR; фотодіод/фототранзистор на екрані GS у місці зображення світлодіода; осцилограф або логічний аналізатор: канал 1 живлення світлодіода, канал 2 вихід фотодіода; затримка = фронт(2) − фронт(1). ≥ 100 фронтів на конфігурацію, звітувати p50, p95, max, три прогони з перезавантаженням.
3. Розгортка дисплея: повторити з фотодіодом угорі й унизу екрана; різниця це `scanout`.
4. Метод B (без обладнання; крок = кадр телефона, 4 мс при 240 к/с): на хості показувати мілісекундний лічильник, камера AIR дивиться на нього; телефон у slow-motion знімає в одному кадрі екран хоста й екран GS; затримка = лічильник(джерело) − лічильник(GS); ≥ 50 вибірок; похибку читання (півперіоду оновлення лічильника) записати.
5. Метод C (відокремити Pi-декодер): `bench/video-src.sh` (`SOURCE=test`, `timeoverlay`) прямо в `bench/video-rx.sh` без радіо; `tests/sim/video_latency.py --codec h264|h265` на самій Pi (системний python3 з `python3-gi`).
6. Під час прогону знімати `wfb-cli gs`: прогін зараховується, лише якщо залишкові втрати ~0 (інакше очікування FEC псує число).
7. `latency_budget.py budget ... --measured-g2g-ms <p50>` друкує нев'язку проти моделі й попереджає, якщо виміряне поза [min, max]; підібрані доданки записати в `measured.json` і запускати з `MODEL_MEASURED`.

## 7. Протокол калібрування на понеділок: що, чим, який параметр

Повний машиночитний перелік у `params.json` → `sections.<секція>.calibration`. Команди, помічені UNVERIFIED, не перевірялися на реальному залізі.

| Вимір | Параметри | Команда / інструмент |
|---|---|---|
| Кондуктивна потужність TX | `rf.tx_power_dbm` | вимірювач РЧ-потужності й атенюатор; `iw dev wlan0 info \| grep txpower` показує лише запитане значення; без вимірювача: підбір за RSSI на 3 відстанях |
| Підсилення антен, втрати кабелів | `rf.tx/rx_antenna_gain_dbi`, `rf.misc_loss_db` | даташит антени; порівняння RSSI по кабелю з фіксованим атенюатором і по повітрю на 1 м |
| Підлога шуму, завада | `rf.noise_figure_db`, `rf.interference_dbm` | `iw dev wlan0 survey dump` (поле `noise`; підтримка драйвером 8812au UNVERIFIED); `tshark -i wlan0 -T fields -e radiotap.dbm_antnoise`; `wfb-cli gs` |
| RSSI проти відстані (≥ 5 точок, відкрите поле) | `rf.path_loss_exponent`, `rf.ref_distance_m` | `wfb-cli gs` (RSSI по антенах); `tshark -i wlan0 -T fields -e radiotap.dbm_antsignal -e radiotap.mcs.index`; лазерний далекомір; МНК `rx_dbm = A − 10·n·log10(d)` |
| SNR при PER 10 % по MCS | `rf.implementation_loss_db`, `rf.mcs_snr_offset_db`, `rf.ldpc_gain_db`, `rf.diversity_gain_db` | ступінчастий атенюатор; `iperf3 -u -b <швидкість>` через тунель wfb-ng (потоки 0x20/0xa0) і лічильники `wfb-cli gs` (втрачено/відновлено = втрати до FEC); зміщення MCS = виміряний SNR@10 % − модельний (`rf_model.py snr --mcs m`) |
| Серії втрат, підлога | `rf.ge_mean_burst_frames`, `rf.floor_per`, `rf.fading_model`, `rf.rician_k_db` | `wfb-cli gs` або `tcpdump -i wlan0 -w cap.pcap` і скрипт по розривах номерів |
| Інтервал між кадрами в ефірі | `rf.mac_access_us`, `rf.relay_jitter_ms`, `rf.relay_extra_delay_ms` | другий адаптер у monitor-режимі: `tshark -i wlan0 -T fields -e frame.time_delta_displayed -e frame.len` |
| Бітрейт, відношення I-кадру | `video.iframe_ratio` | `ffprobe -show_frames -select_streams v capture.h265` (розміри пакетів) або `wfb-cli gs` |
| Струм пристроїв (idle/RX/TX) | `power.devices.*`, `power.tx_peak_factor` | USB-вимірник з журналом у CSV, `power_model.py ingest --csv meter.csv > measured.json`; осцилограф для піку |
| Струм плати | `power.boards.pi5.board_idle_a`, `board_load_a` | вимірник між БЖ і Pi; навантаження `stress-ng --cpu 4 --timeout 60` (наявність `stress-ng` UNVERIFIED) |
| Просідання, опір кабелю, поріг скидання USB | `power.cable_resistance_ohm`, `power.usb_dropout_v` | мультиметр на тест-точках 5 В; `vcgencmd get_throttled`; `vcgencmd pmic_read_adc` (`EXT5V_V`, лише Pi з PMIC, формат виводу UNVERIFIED); `dmesg \| grep -i voltage`; поріг скидання: повільно знижувати напругу лабораторного БЖ (не з підключеним апаратом) |
| Час повернення адаптера | `power.usb_reenum_s` | `udevadm monitor --subsystem-match=usb --property`, `dmesg -w` при примусовому відключенні |
| Скло-до-скла (розділ 6) | `latency.capture_frames`, `latency.encode_frames`, `latency.panel_ms`, `latency.display_queue_frames` | фотодіод + осцилограф або телефон 240 к/с |
| Декодер на Pi | `latency.decoders.*.proc_ms_1080p`, `reorder_frames`, `queue_frames` | `GST_DEBUG=GST_TRACER:7 GST_TRACERS='latency(flags=element)' gst-launch-1.0 -e udpsrc port=5600 caps=… ! rtph265depay ! h265parse ! v4l2slh265dec ! kmssink sync=false`; чи трасувальник дає затримку по елементу для v4l2-декодерів: UNVERIFIED; глибина reorder: `ffprobe -show_streams` (`has_b_frames`) |
| ПЗ-декодування Pi 5 | `latency.sw_decode_fraction`, `latency.cpu_scale_vs_x86` | `tests/sim/video_latency.py --codec h264` на Pi 5 (потрібен `python3-gi`), CPU-коефіцієнт = p50 Pi / p50 x86 |
| Лінія USB-приймання, мережа | `latency.usb_rx_ms`, `latency.network_ms` | `tests/sim/udp_probe.py` крізь лінк, `wfb-cli gs` |
| Розгортка/vsync | `latency.vsync_wait_frac`, `latency.scanout_frac` | `modetest -M vc4 -c`, фотодіод угорі й унизу |

Примітка про `iw dev wlan0 station dump`: показує станції лише в managed/AP-режимі, на лінку wfb-ng у monitor-режимі станцій немає; команду варто використати для контролю тракту «адаптер-AP» (RSSI, швидкість, retries), а для wfb-ng брати `wfb-cli gs` і radiotap.

## 8. Тести, храповик, підключення до смоуку

`tests/sim/models/run.sh --check`: компіляція всіх `.py`, потім `test_models.py` (stdlib `unittest`, 52 тести, ~2 с, без мережі). Покриття: схема параметрів (походження обов'язкове, SRC лише https-URL, UNMEASURED мусить мати запис калібрування, кількість UNMEASURED друкується й обмежена константою `UNMEASURED_MAX` у `test_models.py`, яка **може лише зменшуватись**; зараз 48: `video` 1, `rf` 16, `power` 12, `latency` 19); властивості (PER монотонно не зростає по SNR для всіх MCS, поріг SNR зростає з номером MCS, втрати монотонні по відстані для iid/Gilbert × none/Rayleigh/Rician, FEC ніколи не погіршує, паритет більше не гірше, Gilbert не краще iid, формули звірено з повним перебором, дальність справді є порогом, доданки затримки сумуються в підсумок, `min ≤ typ ≤ max`, знак запасу живлення збігається з прапорцями, додавання пристрою зменшує запас, ємність USB Pi 5 залежить від БЖ, схема й детермінованість подій brown-out, перекриття виміром, розбір логів `throttled`); три golden (`golden/rf_mcs1_fec8_12.txt`, `golden/power_pi5_psu.txt`, `golden/latency_pi4_h265_1080p.txt`). `run.sh --update-golden` лише при задуманій зміні моделі. Мутаційні перевірки робити на копії.

**Як підключити до `tests/sim/smoke.sh`** (файл не змінювався; кроки для того, хто його править):

1. Додати функцію поруч з `p_model`:
```bash
# ---------------------------------------------------------------- models (RF / power / latency, no hardware)
p_models() {
	[ -x "$HERE/models/run.sh" ] || { skip models "tests/sim/models/run.sh missing"; return; }
	"$HERE/models/run.sh" --check >"$tmp/models.log" 2>&1; rc=$?
	if [ "$rc" = 77 ]; then skip models "no python3"; else ok "$rc" models "calibratable models: $(tail -1 "$tmp/models.log")"; fi
}
```
2. Додати `models` у `ONLY` за замовчуванням (рядок `ONLY="${SMOKE_ONLY:-static model mavlink video router wfb qemu}"` → `static model models mavlink ...`) і в `order="static model models mavlink video router wfb qemu"`; у коментар шапки: «models (RF/power/latency model unit+golden tests)».
3. `p_static` уже перевіряє `bash -n` для `tests/sim/*.sh`, але не для підкаталогів: за потреби додати `"$ROOT"/tests/sim/models/*.sh` у цикл `bash -n` і `shellcheck -x tests/sim/models/run.sh`.

## 9. Що модель може й чого не може

Може: ранжувати конфігурації; показати, що при 4000 кбіт/с FEC 8/12 не вміщується в MCS0 (завантаження 1.04); показати, що бюджет USB, а не ємність БЖ, першим перевищується для обох Pi (за заглушками); показати, що відстань між MCS і FEC-схемами дає порядок, а не метри; надати події «USB відвалився на t» для віртуальних тестів; дати чек-лист вимірів і автоматично перенести виміряне значення в результат. Не може: сертифікувати дальність (рельєф, антени, завади, реальний приймач), затримку (заглушки декодера й дисплея), стійкість живлення (поведінка запобіжників USB, піки), поведінку ядра/драйвера при brown-out.

## 10. UNVERIFIED (повний список)

1. IEEE 802.11n Table 20-23 (мінімальна чутливість приймача) і сам стандарт не читано; константи OFDM (52/108 піднесучих, преамбули HT/VHT, 4.0/3.6 мкс символ) це INF.
2. Ліцензійний статус перенесення коефіцієнтів спектра ваг із ns-3 (GPL-2.0-only): формули перереалізовано, чи достатньо цього юридично, не з'ясовано.
3. Чи 16 Б тега ChaCha20-Poly1305 і 4 Б FCS входять у `WIFI_MTU`-розрахунок так, як у `rf.wfb_overhead_bytes` (SRC лише розміри структур у `wifibroadcast.hpp`).
4. Чи ін'єкція wfb-ng проходить через звичайний EDCA (DIFS+backoff): `rf.mac_access_us`.
5. Поведінка USB-обмежувача Pi 3B+/4/5 при перевищенні бюджету (документ не каже) і поріг скидання пристрою; усе в `brownout` це політика INF.
6. Струми RTL8812AU/EU в idle/RX/TX, FC, вебкамери, вентилятора, `tx_peak_factor`; даташита не знайдено (SRC лише оцінка автора wfb-ng «~1.6А in pulse» для AWUS036ACH).
7. Idle і навантаження Pi 5, значення idle/load для 3B+ (беруться з 3B).
8. Вигляд виводу `vcgencmd pmic_read_adc` і чи є він на кожній платі; точний текст повідомлень ядра про просідання.
9. Чи апаратний H.264-декодер Pi 4 видно як `v4l2h264dec` на поточній Pi OS; відсутність апаратного H.264 у Pi 5 (офіційні сторінки raspberrypi.com і форум повертають 403: UNVERIFIED); затримки всіх апаратних декодерів; поведінка HEVC-декодера Pi 5 при втратах UDP (SNIP: linux#7609/#7612, не перечитувано).
10. Чи трасувальник `latency` GStreamer показує час v4l2-декодерів по елементу.
11. Глибина reorder потоку з OpenIPC majestic (B-кадри, GOP) і відношення I-кадру.
12. Поріг `usb_dropout_v`, `usb_reenum_s`, `cable_resistance_ohm`.
13. Підтримка `iw survey dump` (поле `noise`) драйвером RTL8812AU/EU; наявність `stress-ng` на образі.
