# Стохастичний рушій сценаріїв: деградація, зовнішні фактори, нелінійність, шум, позанормальні сценарії

Станом на 2026-10-03. Код: `tests/sim/models/` (`priors.py`, `degrade_model.py`, `gpio_bounce.py`, `scenario_engine.py`, `scenarios/*.json`, `params.degrade.json`, `test_degrade.py`, `mutate.sh`). Запуск: `tests/sim/models/run.sh --check` (разом із тестами моделей `docs/SIM-MODELS.md`, менше 10 с, без мережі, без root, лише stdlib). Позначки достовірності як у `CLAUDE.md`: **SRC/REPO/INF/HW**, а також **SYNTH** (синтезований правдоподібний показник для планування, **не факт**) і **UNMEASURED** (заглушка, її треба виміряти на стенді).

**Коротко.** Рушій бере готові моделі (`rf_model`, `power_model`, `latency_budget`), додає до них нелінійність апаратури, випадкову деградацію, зовнішні впливи, збої запуску пристроїв, час і механічний дребезг кнопки, і ганяє детермінований (seed) Monte-Carlo по **розподілах параметрів** (пріорах). Вихід: перцентилі залишкових втрат відео, запасу лінка, дальності при втратах менше 1 %, затримки скло-до-скла, доступності, часу до першої відмови та ймовірності кожного режиму відмови; плюс рейтинг чутливості «що міряти першим». Усі цифри нижче похідні від **SYNTH/UNMEASURED пріорів**: це інструмент планування й проєктування тестів, а не прогноз польоту. Розділ «Чесність» (§10) обов'язковий до прочитання.

## 1. Контур, що моделюється, і що змінилося

Факти користувача (`MEMORY.md`, уточнені координатором 2026-10-03; **не перевірені залізом**):

| Вузол | Склад | Врахування в рушії |
|---|---|---|
| AIR | OpenIPC WiFiLink2 з радіо RunCam **RTL8812EU** (5/10/20 МГц, PA 28 дБм FCC за SRC `docs.openipc.org/hardware/runcam/vtx/runcam-wifilink-v2/`, прочитано в `docs/SIM-BLOCKERS.md`), 720p; живлення від BEC | нелінійний PA (Rapp), тепловий RC, провал живлення BEC: секція `hw` |
| FC | ArduPilot, Matek H743 SLIM; MAVLink на **єдиному UART** WiFiLink2; ELRS RX на **900 МГц** (модель невідома) | сценарії S08, S09, S32 каталогу (поза фізикою рушія) |
| GS | Raspberry Pi 5 4 ГБ + RTL8812AU донгл, TX12 MKII (внутрішній Multi-модуль 2,4 ГГц + зовнішній TX-модуль; кастомний EdgeTX для TX+USB у планах), хост Ubuntu 26.04+ | USB-бюджет і ліміт Pi 5, перешкоди від 900 МГц модуля (гармоніки/інтермод) і Multi 2,4 ГГц, USB3-шум, RP1 для кнопки |

Обмеження: `rf_model` знає лише 20/40 МГц (5/10 МГц WiFiLink2 не змодельовані, UNVERIFIED), AIR-радіо це EU, а в усій доказовій базі репозиторію AU (`docs/SIM-BLOCKERS.md`, K1/K2), тому всі значення `hw.*` для AIR лишаються заглушками.

## 2. Архітектура

```
params.json (rf/power/latency/video, мін/тип/макс)  ─┐
params.degrade.json (пріори: dist + походження)      ─┼─> priors.Space ──u∈[0,1]^k──> Theta (значення) ─┐
scenarios/<name>.json (set / dist / cfg)             ─┘                                                  │
                         Rng(seed): окремий потік на кожен процес (CRN)                                  v
degrade_model.run_session(Theta, cfg): bring-up -> N зрізів dt: середовище, AIR (тепло, провал, PA, EVM), канал, GS (USB, ліміт Pi 5, throttled),
                         затримка (черги, джитер, реордер) -> вихід за прохід -> scenario_engine.summarize: p5/p50/p95/p99, P(режим)
```

- **Детермінізм.** Лише `random.Random(seed)` (Mersenne Twister); кожному draw `i` відповідає `seed*1000003+i`; процеси (шум, пакетні завади, антена, провал, USB, затримка, bring-up) мають **власні підпотоки** (`Rng.child`), тож зміна параметра одного процесу не зсуває інші (спільні випадкові числа для аналізу чутливості).
- **Антитетичний режим** (`--antithetic`): draw `2j+1` бере `1-u` від draw `2j` для параметрів **і** шуму процесів; n має бути парним.
- **Пріори** (`priors.Dist`): `point`, `uniform`, `normal`, `lognormal`, `beta` (квантиль через неповну бета-функцію, таблиця по z), `triangular` (для параметрів `params.json` з мін/тип/макс). Походження: SRC, REPO, INF, SYNTH, UNMEASURED, MEASURED_SIM, MEASURED_HW.
- **Параметри з `params.json`**, що також семплюються (`base_sampled`, 27 штук) і контурні заміни їх пріорів (`base_dist`: шлях близький до вільного простору, потужність 20..27 дБм з модою 24, малий нерадіо-поріг втрат, Ріс K 5..15 дБ, пакетність 1..10 кадрів, кабель 5 А БЖ 0,05..0,25 Ом): усе SYNTH, у файлі видно.
- **Розмір.** 145 вимірів невизначеності в сценарії каналу (74 UNMEASURED, 67 SYNTH, 4 INF), 18 у сценарії кнопки.
- **Час прогону.** 60 draw по 600 с (зріз 20 с) ≈ 0,3 с; рейтинг чутливості Morris по 145 вимірах (r=3) ≈ 1,3 с.

## 3. Що моделюється (формули, INF; числа SYNTH/UNMEASURED)

| Явище | Модель | Властивості, що перевіряють тести |
|---|---|---|
| **Нелінійність PA (AM-AM)** | Rapp: `Pout = Plin / (1+(Plin/Psat)^p)^(1/p)`, `Psat` з виходу-віднесеної P1dB; `hw.pa_p1db_out_dbm`, `hw.pa_rapp_p` | `Pout` монотонно зростає, підсилення не зростає, 1 дБ стиснення точно в P1dB, лінійно при малому драйві |
| **EVM і стеля SNR** | `EVM² = floor + (k·похибка амплітуди)²`; `1/SNR_eff = 1/SNR + EVM²` | `SNR_eff ≤ -EVM` завжди; більше стиснення або вища температура дає гірший EVM |
| **Тепловий дрейф і дерейтинг** | `Tj' = (Tамб + Rth·P − Tj)/τ`, де `P = P_dc − P_rf + P_плати` (збереження енергії: `P_dc = P_простою + P_rf/η`, `η = 1 − hw.air_diss_frac` ККД PA за приростом, `P_rf` ВЧ-вихід після стиснення/дерейтингу/провалу; виміряний струм береться на номінальному виході P1dB; D1/D1b виправлено); вище `thermal_derate_start_c` потужність падає на `k` дБ/°C; вище `thermal_shutdown_c` TX вимикається, повернення після гістерезису | час до порогу збігається з аналітикою; вища температура дає раніший зупин |
| **Провал живлення AIR** | `sag = k1·ΔV + k2·ΔV²` нижче коліна, знижує P1dB (більше стиснення, гірший EVM) | монотонно в напрузі |
| **AGC/ADC поблизу передавача** | штраф SNR = `min(cap, (Rx − коліно)·нахил)` лише вище коліна; при нахилі > 1 (до 2 дБ/дБ, перевантаження АЦП/IMD3) SNR(відстань) **унімодальний**: максимум на коліні, у зоні AGC зростає з відстанню (за задумом, D8: `link_eval()["agc_zone"]` позначає зону) | штраф 0 нижче коліна; поза зоною margin(d) строго спадає; при нахилі ≤ 1 монотонно всюди; тільки на ближній відстані |
| **Десенсибілізація** | підйом шуму `10·lg(1+ΣI/N)`; джерела: модуль ELRS 900 МГц і Multi 2,4 ГГц (ефективні коефіцієнти зв'язку, робочий цикл), USB3-шум (ймовірність присутності) | нуль без завад, монотонно в потужності завади |
| **Антена** | нуль діаграми (ймовірність, експоненційна глибина) + поляризація (рівномірний кут, `-20·lg cos`) + тіло/планер; стан тримається ~десятки секунд | втрата ≥ 0, хвіст, обмеження кута |
| **Шум і шоки** | дрейф Орнштейна-Уленбека (точна AR(1) для будь-якого кроку) + шоки як **неперервний у часі** пуассонівський процес (моменти приходу з власного підпотоку, не залежать від `dt`; шок, що прийшов усередині зрізу, вже спав на `exp(−(t_кінця−t_приходу)/τ)`; логнормальний розмір) + старіння (години·dB/кгод) | без шоків при rate=0, лічильник зростає з rate, **та сама часова лінія й середній надлишок `rate·E[розмір]·τ` для будь-якого `dt`** |
| **Багатопроменевість, пакетні втрати** | усереднення PER по завмиранню Райса/Релея **інтегруванням щільності** (вагові коефіцієнти на сітці 0,25 дБ, згортка з таблицею PER, `degrade_model.fading_weights/fading_per_table`; хвіст до ~1e-6, D9 виправлено; раніше 32 квантилі) і `rf_model.residual_ge/iid` (Gilbert-Elliott, FEC k/n) | при вимкнених деградаціях PER збігається з `rf_model.frame_per` (2 %), діапазон з `rf_model.max_range` (3 %) |
| **Епізоди завад (клаш каналу)** | двостанова ланка в **неперервному часі**: тихі періоди Exp(інтенсивність), сплески Exp(середня); початковий стан стаціонарний; зріз отримує **частку часу в сплеску**, лінк рахується для тихого й сплескового стану й змішується за часткою (D6) | P(burst_outage) зростає з навантаженням; та сама часова лінія для будь-якого `dt`; сплеск коротший за зріз не губиться |
| **USB/живлення GS** | `power_model.budget` (стан rx/tx/пік, ліміт 0,6 або 1,6 А Pi 5), **один поріг перевантаження** `ліміт·(1+usb.pi5_trip_tol)` для бюджету (`USB_OVER`; смуга допуску `USB_TOL_BAND`), автомата тріпу й запасу небезпеки (D2); автомат Pi 5 (OK → TRIPPED → OK → LATCHED) реагує на `i_lim = i_rx + w·(i_tx − i_rx)`, `w = usb.pi5_trip_tx_weight` (UNMEASURED: вікно відгуку обмежувача не задокументоване, D12); епізод тріпу й спонтанні відвали `base·exp(−запас_V/Vs)·exp(−запас_I/Is)` (запас струму від того ж порога, для піку TX) генеруються **в неперервному часі** (експоненційні часи роботи, перелічування, bring-up), слово `get_throttled` з липкими бітами | небезпека спадає із запасом; автомат за таблицею станів; бюджет, автомат і небезпека узгоджені одним порогом; ті самі події й час відключення для будь-якого `dt`; слово розкодовується `power_model.decode_throttled` |
| **Збої запуску** | USB probe, завантаження прошивки, monitor, перша ін'єкція: Bernoulli на спробу, таймаут + експоненційний backoff, `max_attempts` | MC збігається з `Π(1−p^n)`; більше спроб краще |
| **Ін'єкція** | випадковий EBUSY + **M/D/1/K** за замовчуванням (детермінована служба: ефірний час кадру; точне розв'язання вкладеного ланцюга, `injection_block_prob_det`; `cfg.queue_service: "exp"` дає M/M/1/K, D10); довжина черги, ρ = пакети/мін(ефір, ліміт); переповнення черги I-кадром | блокування монотонне в ρ, спадає з K; K = 1 → ρ/(1+ρ); M/D/1/K ≤ M/M/1/K; `ρ=1, K=5`: 0,103 проти 0,167 |
| **Заморожування** | втрачений IDR заморожує весь GOP, втрачений P-кадр пів-GOP | монотонне в втратах |
| **Час** | ppm-розбіжність → ріст черги до витоку (скидання), зупинки як **неперервний у часі** пуассонівський процес (будь-яке число за зріз, кожна у свій момент, бэклог спадає з `catchup` між ними), джитер планувальника (логнормаль + рідкісні сплески), фаза vsync, реордер пакетів, кроки NTP | ріст затримки з ppm; сплески збільшують хвіст; середня затримка не залежить від `dt` |
| **GPIO** | дребезг контакту, EMI-голчики, вібраційне розмикання, повторення **справжнього алгоритму** `gs/button.sh` (див. §6), параметри RP1 | див. §6 |

## 4. Параметри з походженням

Джерело істини: `tests/sim/models/params.degrade.json` (124 параметри, 9 секцій: `hw`, `proc`, `ext`, `vid`, `inj`, `usb`, `bringup`, `timing`, `gpio`). Кожен параметр має `value`, `unit`, `provenance`, `dist`, `note` (для UNMEASURED/SYNTH обов'язкова), для SRC/REPO/MEASURED ще `source`; кожен UNMEASURED/SYNTH закріплений за записом калібрування (`calibration`). **Храповики** (`test_degrade.py`): UNMEASURED у цьому файлі = **48** (`DEGRADE_UNMEASURED_MAX`), SYNTH = **68** (`DEGRADE_SYNTH_MAX`); обидва можуть лише зменшуватись. UNMEASURED у `params.json` лишається окремо (48, `UNMEASURED_MAX`).

### Як перекрити без зміни коду

| Спосіб | Приклад | Походження в результаті |
|---|---|---|
| `DEGRADE_PARAMS=<файл>` | цілий альтернативний файл пріорів | як у файлі |
| `DEGRADE_MEASURED=<накладка.json>` | `{"hw.pa_p1db_out_dbm": 24.5}` або `{"hw.thermal_shutdown_c": {"value": 108, "dist": {"kind":"normal","mu":108,"sigma":2}, "source": "термопара 2026-10-05"}}` | `MEASURED_HW`: параметр стає точкою (або заданим розподілом) і зникає з вимірів невизначеності |
| `DEGRADE_SET='k=v;k=v'` | `DEGRADE_SET='ext.ambient_c=38'` | `OVERRIDE`, точка |
| `MODEL_PARAMS`/`MODEL_MEASURED`/`MODEL_SET` | як у `docs/SIM-MODELS.md` | діють на базові параметри |
| `--set k=v` / `--dist k='{"kind":"normal",...}'` (CLI рушія) | `scenario_engine.py run nominal_pi5_5a_150m --set rf.tx_power_dbm=25` | зафіксувати параметр / замінити пріор лише в цьому запуску |
| файл сценарію `scenarios/<name>.json` | `{"name","description","kind","cfg","set","dist"}` | `set` закріплює, `dist` замінює пріор, `cfg` задає відстань/тривалість/БЖ/плату |

### Таблиця параметрів `params.degrade.json` (згенеровано `scenario_engine.py doc-tables`; істина в JSON)

| Ключ | Одиниця | Розподіл (prior) | Походження | Примітка |
|---|---|---|---|---|
| `bringup.backoff_base_s` | s | uniform(lo=1.0, hi=4.0) | SYNTH | first backoff delay |
| `bringup.backoff_factor` | x | uniform(lo=1.5, hi=3.0) | SYNTH | backoff growth |
| `bringup.fw_fail_p` |  | beta(a=1, b=99) | SYNTH | per-attempt failure probability: firmware/driver init |
| `bringup.fw_ok_s` | s | lognormal(median=1.0, sigma=0.3, lo=0.2, hi=5) | UNMEASURED | time of a successful attempt: firmware/driver init |
| `bringup.fw_timeout_s` | s | lognormal(median=2.0, sigma=0.3, lo=0.5, hi=10) | UNMEASURED | time lost by a failed attempt: firmware/driver init |
| `bringup.inj_start_fail_p` |  | beta(a=1, b=99) | SYNTH | per-attempt failure probability: first injection test (EBUSY at start) |
| `bringup.inj_start_ok_s` | s | lognormal(median=0.5, sigma=0.3, lo=0.1, hi=3) | UNMEASURED | time of a successful attempt: first injection test (EBUSY at start) |
| `bringup.inj_start_timeout_s` | s | lognormal(median=1.0, sigma=0.3, lo=0.2, hi=5) | UNMEASURED | time lost by a failed attempt: first injection test (EBUSY at start) |
| `bringup.max_attempts` |  | point(3) | SYNTH | retry count assumed for the bring-up service (a design choice; the repo has no retry loop yet) |
| `bringup.monitor_fail_p` |  | beta(a=2, b=48) | SYNTH | per-attempt failure probability: iw set type monitor (busy: NetworkManager, rfkill) |
| `bringup.monitor_ok_s` | s | lognormal(median=0.5, sigma=0.3, lo=0.1, hi=3) | UNMEASURED | time of a successful attempt: iw set type monitor (busy: NetworkManager, rfkill) |
| `bringup.monitor_timeout_s` | s | lognormal(median=1.0, sigma=0.3, lo=0.2, hi=5) | UNMEASURED | time lost by a failed attempt: iw set type monitor (busy: NetworkManager, rfkill) |
| `bringup.usb_probe_fail_p` |  | beta(a=2, b=98) | SYNTH | per-attempt failure probability: USB enumeration (descriptor read timeouts, error -71) |
| `bringup.usb_probe_ok_s` | s | lognormal(median=1.5, sigma=0.3, lo=0.3, hi=6) | UNMEASURED | time of a successful attempt: USB enumeration (descriptor read timeouts, error -71) |
| `bringup.usb_probe_timeout_s` | s | lognormal(median=5.0, sigma=0.3, lo=1, hi=20) | UNMEASURED | time lost by a failed attempt: USB enumeration (descriptor read timeouts, error -71) |
| `ext.ambient_c` | C | normal(mu=24.0, sigma=8.0, lo=-10, hi=45) | SYNTH | outside air temperature of the session |
| `ext.clash_inr_db` | dB | normal(mu=12.0, sigma=4.0, lo=3, hi=30) | SYNTH | interference-to-noise ratio during an episode |
| `ext.clash_mean_s` | s | lognormal(median=8.0, sigma=0.7, lo=1, hi=120) | SYNTH | mean episode duration |
| `ext.clash_rate_per_h` | 1/h | lognormal(median=6.0, sigma=0.8, lo=0.2, hi=60) | SYNTH | start rate of interference episodes on the channel (neighbour FPV/AP) |
| `ext.solar_rise_c` | C | uniform(lo=0.0, hi=15.0) | SYNTH | extra heating of the enclosures by direct sun |
| `gpio.action_ms` | ms | lognormal(median=30.0, sigma=0.6, lo=3, hi=500) | UNMEASURED | execution time of the dispatched function before the loop re-arms |
| `gpio.bounce_count_mean` |  | lognormal(median=3.0, sigma=0.5, lo=1, hi=15) | SYNTH | mean number of bounce pairs on a press (tact switch: a few) |
| `gpio.bounce_total_ms` | ms | lognormal(median=2.0, sigma=0.8, lo=0.1, hi=40) | SYNTH | mean total bounce duration of a press (typical tact switch 1..5 ms, worn: tens of ms) |
| `gpio.chatter_ms` | ms | lognormal(median=3.0, sigma=0.7, lo=0.2, hi=60) | SYNTH | duration of that opening |
| `gpio.chatter_prob` |  | beta(a=1, b=49) | SYNTH | probability that vibration briefly opens the contact during a long hold |
| `gpio.debounce_ms` | ms | point(0.0) | REPO | kernel/libgpiod debounce period in use: 0 = none (gpiomon is called without a debounce option) |
| `gpio.exit_ms` | ms | lognormal(median=2.0, sigma=0.4, lo=0.3, hi=20) | UNMEASURED | gpiomon event delivery and exit latency |
| `gpio.get_ms` | ms | lognormal(median=8.0, sigma=0.4, lo=2, hi=60) | UNMEASURED | latency of gpioget until the level is sampled |
| `gpio.glitch_rate_per_s` | 1/s | lognormal(median=0.02, sigma=1.0, lo=0.0005, hi=2) | SYNTH | EMI glitch rate on the line (long wires near ESC/motors) |
| `gpio.glitch_width_us` | us | lognormal(median=40.0, sigma=0.8, lo=2, hi=5000) | SYNTH | median glitch width |
| `gpio.long_hold_mu_s` | s | uniform(lo=2.2, hi=3.0) | SYNTH | mean hold of an intended long press (operator) |
| `gpio.long_hold_sigma_s` | s | uniform(lo=0.2, hi=0.5) | SYNTH | spread of the intended long hold |
| `gpio.long_threshold_cs` | cs | point(200) | REPO | long-press threshold in /proc/uptime hundredths |
| `gpio.mon_start_ms` | ms | lognormal(median=12.0, sigma=0.4, lo=3, hi=80) | UNMEASURED | fork+exec+open latency of gpiomon (edges inside it are not seen) |
| `gpio.release_bounce_scale` | x | uniform(lo=0.8, hi=3.0) | SYNTH | release bounce vs press bounce |
| `gpio.rp1_edge_loss_prob` |  | beta(a=1, b=999) | UNMEASURED | probability that an RP1 edge event is lost (coalescing) |
| `gpio.rp1_filter_us` | us | lognormal(median=10.0, sigma=0.7, lo=0.0, hi=500) | UNMEASURED | RP1 hardware input glitch filter (pulses shorter are ignored); 0 = off. Datasheet value not read |
| `gpio.rp1_irq_latency_us` | us | lognormal(median=40.0, sigma=0.6, lo=5, hi=2000) | UNMEASURED | RP1 interrupt latency over PCIe added to every edge |
| `gpio.settle_s` | s | point(0.05) | REPO | sleep between the first rising edge and the level check |
| `gpio.single_hold_s` | s | lognormal(median=0.12, sigma=0.35, lo=0.03, hi=1.0) | SYNTH | hold time of an intended short press |
| `gpio.uptime_ms` | ms | lognormal(median=5.0, sigma=0.4, lo=1, hi=40) | UNMEASURED | latency of the cut/tr pipeline reading /proc/uptime |
| `hw.air_ambient_rise_c` | C | uniform(lo=2.0, hi=15.0) | SYNTH | airframe interior temperature rise above outside air (SYNTH) |
| `hw.air_bec_v` | V | normal(mu=5.0, sigma=0.12, lo=4.5, hi=5.5) | UNMEASURED | voltage the BEC delivers to the VTX (DC 9-22 V input, BEC recommended: SRC docs.openipc.org) |
| `hw.air_board_heat_w` | W | uniform(lo=0.5, hi=2.5) | UNMEASURED | SoC + sensor heat sharing the radio's heat path (SSC338Q + IMX415) |
| `hw.air_diss_frac` |  | uniform(lo=0.7, hi=0.95) | INF | 1 - eta, eta = incremental drain efficiency of the AIR PA: DC power the PA draws per radiated watt is 1/eta (degrade_model.air_tx_power_w); the heat is P_dc - P_rf, not diss_frac * P_dc any more (D1/D1b fixed). INF range from PA efficiency 5..30 %; the real value is UNMEASURED (calibration hw-pa-efficiency) |
| `hw.air_rth_c_per_w` | C/W | lognormal(median=7.0, sigma=0.35, lo=3, hi=30) | UNMEASURED | case-to-ambient thermal resistance of the WiFiLink2 radio incl. heatsink and fan (fan 25/30 g is SRC-listed per docs/SIM-BLOCKERS.md, Rth unknown) |
| `hw.air_supply_r_ohm` | ohm | lognormal(median=0.05, sigma=0.5, lo=0.01, hi=0.3) | UNMEASURED | AIR supply path resistance (BEC output, wire, connector) seen by the TX current pulse |
| `hw.air_supply_ripple_v` | V | lognormal(median=0.04, sigma=0.5, lo=0.005, hi=0.3) | UNMEASURED | BEC ripple / noise std per slice (ESC and motor load) |
| `hw.air_tau_s` | s | lognormal(median=150.0, sigma=0.4, lo=30, hi=900) | UNMEASURED | thermal time constant of the radio + heatsink |
| `hw.ant_body_mean_db` | dB | lognormal(median=12.0, sigma=0.3, lo=4, hi=25) | SYNTH | mean body/airframe shadow depth |
| `hw.ant_body_prob` |  | beta(a=2, b=38) | SYNTH | share of time the operator's body/airframe shadows an antenna |
| `hw.ant_body_sigma_db` | dB | uniform(lo=2.0, hi=6.0) | SYNTH | spread of the shadow depth |
| `hw.ant_null_mean_db` | dB | lognormal(median=10.0, sigma=0.4, lo=3, hi=30) | SYNTH | mean null depth (exponential) |
| `hw.ant_null_prob` |  | beta(a=2, b=18) | SYNTH | share of time the antenna pattern is in a null |
| `hw.ant_persist_s` | s | lognormal(median=20.0, sigma=0.5, lo=2, hi=120) | SYNTH | how long an orientation state persists |
| `hw.ant_pol_cap_db` | dB | uniform(lo=15.0, hi=30.0) | INF | cap of the cross-polar loss |
| `hw.ant_pol_max_deg` | deg | uniform(lo=10.0, hi=60.0) | SYNTH | maximum polarisation tilt between the AIR and GS antennas |
| `hw.elrs900_coupling_db` | dB | normal(mu=135.0, sigma=12.0, lo=90, hi=160) | UNMEASURED | EFFECTIVE in-band-equivalent coupling of the 900 MHz module into the 5.8 GHz receiver: harmonics/intermod, out-of-band rejection and antenna isolation lumped (very high loss expected; the tail is the risk) |
| `hw.elrs900_duty` |  | uniform(lo=0.05, hi=0.5) | SYNTH | share of time the module radiates (packet rate x airtime), SYNTH |
| `hw.elrs900_present` | bool | point(1) | INF | ELRS RX on 900 MHz is in the AIR contour (user fact); at the GS the external TX module is the source |
| `hw.elrs900_tx_dbm` | dBm | uniform(lo=20.0, hi=30.0) | UNMEASURED | ELRS 900 MHz TX module power at the GS (100 mW..1 W); model of the module unknown |
| `hw.evm_comp_coeff` | x | uniform(lo=1.0, hi=2.5) | SYNTH | scale of the compression amplitude error in EVM (SYNTH) |
| `hw.evm_floor_db` | dB | normal(mu=-33.0, sigma=2.0, lo=-40, hi=-26) | UNMEASURED | linear EVM floor (phase noise, IQ imbalance, DAC); 802.11 mandates only a ceiling per MCS |
| `hw.evm_temp_db_per_c` | dB/C | uniform(lo=0.02, hi=0.1) | SYNTH | EVM floor degradation per degree above 25 C (SYNTH) |
| `hw.multi24_coupling_db` | dB | normal(mu=120.0, sigma=12.0, lo=70, hi=150) | UNMEASURED | EFFECTIVE in-band-equivalent coupling of the 2.4 GHz Multi module into the 5.8 GHz receiver incl. antenna proximity and out-of-band rejection |
| `hw.multi24_duty` |  | uniform(lo=0.02, hi=0.4) | SYNTH | share of time the Multi module radiates |
| `hw.multi24_present` | bool | point(1) | INF | TX12 MKII internal Multi module: 2.4 GHz may be switched on at the GS (user fact; protocol unknown) |
| `hw.multi24_tx_dbm` | dBm | uniform(lo=10.0, hi=20.0) | UNMEASURED | Multi module output power (CC2500/ELRS 2.4); the exact module is unknown |
| `hw.pa_p1db_out_dbm` | dBm | normal(mu=27.5, sigma=1.2, lo=23, hi=31) | UNMEASURED | output-referred 1 dB compression point of the AIR PA. SRC docs.openipc.org lists 28 dBm (FCC) as the rated power of WiFiLink2; P1dB itself is not published |
| `hw.pa_rapp_p` |  | uniform(lo=1.5, hi=4.0) | SYNTH | Rapp smoothness: 1.5 soft knee .. 4 hard knee (shape prior, SYNTH) |
| `hw.rx_agc_cap_db` | dB | uniform(lo=25.0, hi=45.0) | SYNTH | maximum SNR loss from saturation |
| `hw.rx_agc_knee_dbm` | dBm | normal(mu=-28.0, sigma=3.0, lo=-40, hi=-15) | UNMEASURED | received level where the GS ADC/AGC starts to saturate (too close to the AIR TX) |
| `hw.rx_agc_slope` | dB/dB | uniform(lo=0.7, hi=2.0) | SYNTH | SNR loss per dB above the knee (SYNTH) |
| `hw.soc_rise_c` | C | normal(mu=30.0, sigma=6.0, lo=10, hi=55) | UNMEASURED | Pi 5 SoC temperature rise above ambient with decode load and the cooler fitted (no cooler is worse) |
| `hw.soc_soft_limit_c` | C | uniform(lo=80.0, hi=85.0) | UNMEASURED | D5: effective temperature at which the binary 'soft limit / throttled' state of the model switches on. SRC (raspberrypi/documentation frequency-management.adoc, re-read 2026-10-03): the Arm cores are PROGRESSIVELY throttled between 80 and 85 degC and the hard limit is 85 degC on all models, so the support is exactly [80, 85] (the old N(80; 3) clipped to [60; 90] put 4.8 % of the mass above the hard limit); the point inside the band where a binary model should switch is UNMEASURED (uniform = no information). UNVERIFIED: whether vcgencmd get_throttled bit 3 (soft temperature limit active) ever sets on a Pi 5: the doc defines a soft limit (60 degC, temp_soft_limit) only for the 3A+/3B+ and says the Pi 4 has none |
| `hw.thermal_derate_db_per_c` | dB/C | uniform(lo=0.05, hi=0.3) | SYNTH | TX power reduction per degree above the start (SYNTH) |
| `hw.thermal_derate_start_c` | C | normal(mu=85.0, sigma=5.0, lo=65, hi=100) | UNMEASURED | junction temperature where the driver starts to reduce TX power (RTL thermal tracking, threshold unknown) |
| `hw.thermal_hyst_c` | C | uniform(lo=8.0, hi=25.0) | SYNTH | hysteresis before TX resumes after shutdown (SYNTH) |
| `hw.thermal_shutdown_c` | C | normal(mu=110.0, sigma=5.0, lo=95, hi=130) | UNMEASURED | thermal shutdown threshold (parameter; datasheet value not read) |
| `hw.throttle_decode_factor` | x | uniform(lo=1.2, hi=2.0) | SYNTH | decode-time multiplier while the clock is capped |
| `hw.tx_sag_k1_db_per_v` | dB/V | uniform(lo=2.0, hi=8.0) | SYNTH | linear sag coefficient (power ~ V^2 gives ~3.5 dB/V near 5 V) |
| `hw.tx_sag_k2_db_per_v2` | dB/V2 | uniform(lo=4.0, hi=25.0) | SYNTH | quadratic sag coefficient (hard droop near brown-out) |
| `hw.tx_sag_knee_v` | V | normal(mu=4.75, sigma=0.1, lo=4.4, hi=5.0) | UNMEASURED | supply below which the PA output droops |
| `hw.usb3_noise_dbm` | dBm | normal(mu=-98.0, sigma=5.0, lo=-110, hi=-80) | UNMEASURED | broadband USB3 noise reaching the 5.8 GHz input (USB3 noise is mainly 2.4 GHz; effect on 5.8 is unverified) |
| `hw.usb3_present_prob` |  | beta(a=2, b=8) | SYNTH | probability that a noisy USB3 path (cable/port/SSD) is active during a session |
| `inj.ebusy_prob` |  | beta(a=1, b=4999) | SYNTH | per-frame random EBUSY/ENOBUFS probability even without overload |
| `inj.queue_pkts` | pkts | uniform(lo=64.0, hi=512.0) | UNMEASURED | driver/socket TX queue length in packets |
| `inj.rate_cap_pps` | pps | lognormal(median=5000.0, sigma=0.5, lo=800, hi=20000) | UNMEASURED | maximum injection rate the driver/USB path sustains (packets per second) |
| `proc.age_hours` | h | lognormal(median=150.0, sigma=1.0, lo=1, hi=5000) | SYNTH | operating hours of the radios at the time of the flight |
| `proc.age_nf_db_per_kh` | dB/kh | uniform(lo=0.0, hi=0.2) | SYNTH | noise-figure creep per 1000 h |
| `proc.age_pa_db_per_kh` | dB/kh | uniform(lo=0.0, hi=0.4) | SYNTH | PA output loss per 1000 h |
| `proc.nf_ou_sigma_db` | dB | lognormal(median=1.0, sigma=0.4, lo=0.2, hi=4) | SYNTH | stationary std of the slow noise-floor drift |
| `proc.nf_ou_tau_s` | s | lognormal(median=120.0, sigma=0.5, lo=10, hi=1800) | SYNTH | correlation time of the drift |
| `proc.shadow_sigma_db` | dB | uniform(lo=1.0, hi=4.0) | INF | log-normal shadowing sigma (3..8 dB outdoors is a textbook range) |
| `proc.shadow_tau_s` | s | lognormal(median=30.0, sigma=0.6, lo=3, hi=300) | SYNTH | shadowing correlation time (speed dependent) |
| `proc.shock_mag_db` | dB | lognormal(median=6.0, sigma=0.5, lo=1, hi=20) | SYNTH | median shock size |
| `proc.shock_rate_per_h` | 1/h | lognormal(median=2.0, sigma=0.8, lo=0.1, hi=30) | SYNTH | Poisson rate of interference shocks (ESC, neighbour transmitters) |
| `proc.shock_tau_s` | s | lognormal(median=20.0, sigma=0.5, lo=2, hi=200) | SYNTH | shock decay time |
| `timing.catchup_ms_per_s` | ms/s | uniform(lo=0.5, hi=5.0) | UNMEASURED | backlog drain rate (sink catch-up) |
| `timing.clock_ppm` | ppm | normal(mu=0.0, sigma=25.0, lo=-100, hi=100) | UNMEASURED | frame-clock mismatch between the VTX encoder and the GS display/sink |
| `timing.ntp_step_ms` | ms | lognormal(median=150.0, sigma=1.0, lo=1, hi=5000) | SYNTH | median NTP step size |
| `timing.ntp_step_rate_per_h` | 1/h | lognormal(median=0.2, sigma=0.8, lo=0.01, hi=5) | SYNTH | rate of wall-clock steps (NTP/chrony) on the GS or host |
| `timing.queue_max_ms` | ms | uniform(lo=100.0, hi=400.0) | UNMEASURED | size of the leaky queue/jitter buffer where frames are dropped |
| `timing.reorder_delay_ms` | ms | lognormal(median=8.0, sigma=0.6, lo=1, hi=60) | SYNTH | mean extra wait for a reordered packet |
| `timing.reorder_prob` |  | beta(a=1, b=499) | SYNTH | per-packet reordering probability |
| `timing.sched_jitter_median_ms` | ms | lognormal(median=1.5, sigma=0.5, lo=0.2, hi=10) | UNMEASURED | median wake-up latency of the GS pipeline threads |
| `timing.sched_jitter_sigma` |  | uniform(lo=0.4, hi=0.9) | SYNTH | log-std of the jitter |
| `timing.sched_spike_mean_ms` | ms | lognormal(median=40.0, sigma=0.5, lo=5, hi=300) | UNMEASURED | mean spike length (CPU contention, decode load) |
| `timing.sched_spike_prob` |  | beta(a=1, b=199) | SYNTH | probability of a scheduling spike per sample |
| `timing.stall_backlog_ms` | ms | lognormal(median=60.0, sigma=0.5, lo=10, hi=300) | SYNTH | mean backlog added by a stall |
| `timing.stall_rate_per_h` | 1/h | lognormal(median=3.0, sigma=0.8, lo=0.1, hi=40) | SYNTH | pipeline stall (decoder hiccup, GC, USB) rate |
| `usb.cable_r_ohm` | ohm | lognormal(median=0.08, sigma=0.5, lo=0.02, hi=0.4) | UNMEASURED | extra resistance of the dongle USB cable and connector (thin cables, SRC wfb-ng warns about them) |
| `usb.drop_i_scale_a` | A | uniform(lo=0.08, hi=0.3) | SYNTH | e-fold current margin of the drop hazard |
| `usb.drop_rate_per_h` | 1/h | lognormal(median=0.05, sigma=0.9, lo=0.001, hi=2) | SYNTH | spontaneous USB disconnect rate at ample margins |
| `usb.drop_v_scale` | V | uniform(lo=0.05, hi=0.15) | SYNTH | e-fold voltage margin of the drop hazard |
| `usb.pi5_trip_latch_n` |  | uniform(lo=2.0, hi=6.0) | UNMEASURED | consecutive trips after which the port stays off until a power cycle (assumption) |
| `usb.pi5_trip_off_s` | s | lognormal(median=2.0, sigma=0.4, lo=0.2, hi=10) | UNMEASURED | port-off time after a current-limit trip |
| `usb.pi5_trip_tol` |  | uniform(lo=0.0, hi=0.15) | UNMEASURED | tolerance above the documented limit before the Pi 5 trips |
| `usb.pi5_trip_tx_weight` |  | beta(a=0.5, b=0.5) | UNMEASURED | D2/D12: share of the TX-state current excess (i_tx - i_rx) that the Pi 5 port limiter reacts to: it trips on i_lim = i_rx + w (i_tx - i_rx). w = 0: the limiter averages over a window much longer than the GS TX bursts (the GS dongle is in the RX state almost all the time: uplink MAVLink/RC bursts a few times per second), w = 1: an instantaneous limiter that counts every TX burst as a sustained overload. SRC (raspberrypi/documentation power-supplies.adoc) gives only the 600 mA / 1.6 A budget; the response window is not documented (UNVERIFIED). Prior = Jeffreys Beta(1/2, 1/2) (no information about a proportion). Replaces usb.gs_tx_burst_hz, whose per-slice coin flip p = 1 - exp(-hz*dt) made the limiter exposure depend on dt (all-TX for dt >= 5 s) |
| `usb.psu_v_sigma` | V | uniform(lo=0.02, hi=0.12) | SYNTH | unit-to-unit PSU voltage offset std |
| `usb.reenum_fail_prob` |  | beta(a=2, b=38) | SYNTH | probability that the adapter does not come back without a replug |
| `usb.reenum_sigma` |  | uniform(lo=0.2, hi=0.6) | SYNTH | log-std of the re-enumeration time |
| `vid.bitrate_overshoot` | x | lognormal(median=1.08, sigma=0.12, lo=0.9, hi=2.0) | SYNTH | mean encoded bitrate / configured bitrate (rate control overshoot) |
| `vid.gop_s` | s | uniform(lo=0.5, hi=2.0) | UNMEASURED | IDR period in seconds (majestic default unknown) |


## 5. Калібрування: як вимірювання понеділка замінюють пріори

Порядок: (1) виміряти, (2) записати накладку `DEGRADE_MEASURED` (або `MODEL_MEASURED` для базових), (3) перезапустити `run` і `sensitivity`, (4) знизити храповик у `test_degrade.py`/`test_models.py` і оновити golden (`run.sh --update-golden`, зміна голдена в PR свідома). Виміряне значення стає `MEASURED_HW` і лишає список вимірів. Нижче всі записи калібрування з `params.degrade.json` (команда, інструмент, як перетворити вимір на параметр).

### Калібрування: який вимір понеділка замінює які пріори

| Запис | Що міряти | Параметри | Команда | Інструмент | Як |
|---|---|---|---|---|---|
| hw-tx-power-sweep | AIR conducted/radiated power vs commanded txpower (power meter or SDR), and EVM per MCS | `hw.pa_p1db_out_dbm`, `hw.pa_rapp_p`, `hw.evm_floor_db`, `hw.evm_comp_coeff` | iw dev wlan0 set txpower fixed <mBm> at 17..28 dBm; capture with a spectrum analyser/power meter | power meter or SDR + 30 dB attenuator | Fit the Rapp curve to Pout vs Pin: P1dB = the point where gain is 1 dB lower; EVM from the analyser at MCS 1 and 7 |
| hw-pa-efficiency | AIR input power (BEC current x voltage) against the radiated power at each commanded txpower step | `hw.air_diss_frac`, `hw.pa_p1db_out_dbm`, `hw.air_board_heat_w` | iw dev wlan0 set txpower fixed <mBm> at 17..28 dBm in 1 dB steps; at each step log the BEC V and A (inline meter or the FC power module) and the radiated power (power meter / SDR) | inline USB/DC power meter + RF power meter or SDR with a calibrated tap | Fit P_dc = P_idle + P_rf/eta by linear regression of the input power on the radiated power in mW: slope = 1/eta (hw.air_diss_frac = 1 - eta), intercept = P_idle (and, with the board off, hw.air_board_heat_w); take the current at the rated step as power.devices.rtl8812_tx_a. A slope below 1 means the meter or the power reading is wrong |
| hw-thermal | adapter case temperature vs time at flight TX power in the closed enclosure (thermal camera or NTC on the heatsink) | `hw.air_rth_c_per_w`, `hw.air_tau_s`, `hw.air_board_heat_w`, `hw.air_ambient_rise_c`, `hw.evm_temp_db_per_c`, `hw.thermal_derate_start_c`, `hw.thermal_derate_db_per_c`, `hw.thermal_shutdown_c`, `hw.thermal_hyst_c` | run TX at flight power 30 min; log vtx temperature (majestic/telemetry) every 5 s; repeat at 40 C ambient (warm box) | thermocouple/NTC, thermal camera | Rth = (Tss - Tamb)/P; tau = time to 63 % of the rise; derate/shutdown from the first power drop seen by the power meter |
| hw-supply-sag | VTX 5 V rail during TX bursts (scope on the BEC output) | `hw.air_bec_v`, `hw.air_supply_r_ohm`, `hw.air_supply_ripple_v`, `hw.tx_sag_knee_v`, `hw.tx_sag_k1_db_per_v`, `hw.tx_sag_k2_db_per_v2` | scope on the VTX 5 V pad while txpower steps; lab supply stepped 5.0->4.2 V to measure output power | scope, adjustable lab supply, power meter | R = dV/dI at the burst; sag coefficients from Pout vs rail voltage |
| hw-rx-agc | GS RX SNR/PER vs received level at very short range (conducted with attenuators) | `hw.rx_agc_knee_dbm`, `hw.rx_agc_slope`, `hw.rx_agc_cap_db` | AIR TX -> variable attenuator 0..60 dB -> GS RX; log PER in wfb-cli gs | RF attenuators | knee = level where PER starts to rise as attenuation decreases |
| hw-desense | GS PER with the ELRS 900 MHz module and the Multi 2.4 GHz module transmitting next to the dongle, and with/without USB3 devices | `hw.elrs900_tx_dbm`, `hw.elrs900_coupling_db`, `hw.elrs900_duty`, `hw.multi24_tx_dbm`, `hw.multi24_coupling_db`, `hw.multi24_duty`, `hw.usb3_present_prob`, `hw.usb3_noise_dbm` | fixed link at ~10 dB margin; switch each module/USB3 device on and off; wfb-cli gs loss counters | attenuators, the real TX12 and modules | dSNR (dB) at a known received level gives the noise rise: coupling = interferer power at the source minus the implied in-band-equivalent power |
| hw-antenna | RSSI distribution while rotating the airframe / walking (antenna nulls, polarisation, body shadow) | `hw.ant_null_prob`, `hw.ant_null_mean_db`, `hw.ant_pol_max_deg`, `hw.ant_pol_cap_db`, `hw.ant_body_prob`, `hw.ant_body_mean_db`, `hw.ant_body_sigma_db`, `hw.ant_persist_s` | log RSSI per antenna at fixed 100 m LOS while rotating through attitudes for 10 min | wfb-cli RX_ANT log | histogram of RSSI deficit vs the maximum; autocorrelation time = persistence |
| hw-pi5-thermal | Pi 5 SoC temperature and get_throttled during decode at 30 C ambient | `hw.soc_rise_c`, `hw.soc_soft_limit_c`, `hw.throttle_decode_factor` | vcgencmd measure_temp; vcgencmd get_throttled every 5 s for 30 min under the real GS load | vcgencmd | rise = SoC temp - ambient; decode factor from GStreamer latency before/after capping |
| proc-noise-log | noise floor over a 1 h log on the ground and in the air (iw survey dump / wfb-cli noise) | `proc.nf_ou_sigma_db`, `proc.nf_ou_tau_s`, `proc.shock_rate_per_h`, `proc.shock_mag_db`, `proc.shock_tau_s` | iw dev wlan0 survey dump every 2 s for 1 h; ESC on/off | iw | OU sigma/tau from the autocorrelation of the log; shocks = excursions > 3 sigma |
| proc-shadow | RSSI vs time for a moving aircraft on a fixed-range arc | `proc.shadow_sigma_db`, `proc.shadow_tau_s` | fly/drive a fixed-radius arc, log RSSI | wfb-cli | sigma = std of the detrended RSSI in dB; tau = decorrelation time |
| proc-ageing | same link measured with a new unit and an old one (batch of hours) | `proc.age_hours`, `proc.age_nf_db_per_kh`, `proc.age_pa_db_per_kh` | compare sensitivity (PER at fixed attenuation) and Pout of units with different logged hours | attenuators, power meter | slope per 1000 h; hours from the unit log |
| ext-survey | channel occupancy at the flying site (survey of the channel over 1 h, spectrum scan) | `ext.clash_rate_per_h`, `ext.clash_mean_s`, `ext.clash_inr_db` | iw dev wlan0 survey dump / channel-scan.sh repeatedly at the site | iw, channel-scan.sh | episodes = intervals where busy time rises above threshold; INR from the busy-noise level |
| ext-weather | ambient and sun load of the planned flight (thermometer in the sun/shade, enclosure surface) | `ext.ambient_c`, `ext.solar_rise_c` | thermometer log; IR thermometer on the closed enclosure after 10 min in the sun | thermometer | distribution over the planned season/location |
| vid-bitrate | actual encoder bitrate and IDR period from a capture (tcpdump on the wfb UDP port / majestic log) | `vid.bitrate_overshoot`, `vid.gop_s` | tcpdump -i lo udp port 5600 for 5 min; bitrate per 1 s; IDR spacing from the stream | tcpdump, ffprobe | overshoot = mean/config; GOP = IDR spacing |
| inj-rate | maximum injection rate and drop counters with an overloaded wfb_tx | `inj.queue_pkts`, `inj.rate_cap_pps`, `inj.ebusy_prob` | wfb_tx with bitrate above the air capacity; count send errors (strace -c / wfb_tx stats) | wfb_tx stats, strace | rate_cap = max sustained pps; ebusy = errors per frame at low load |
| usb-meter | USB current and 5 V rail at the Pi during the real GS load (TX bursts), with a 3 A and a 5 A PSU, usb_max_current_enable 0/1 | `usb.psu_v_sigma`, `usb.cable_r_ohm`, `usb.pi5_trip_tx_weight`, `usb.pi5_trip_tol`, `usb.pi5_trip_off_s`, `usb.pi5_trip_latch_n` | USB inline meter + scope on the 5 V; dmesg -w for over-current; vcgencmd get_throttled | USB meter, scope | trip tolerance/off time from dmesg timestamps; cable R from dV/dI; trip tx weight: with a 3 A PSU and usb_max_current_enable=0 log (dmesg over-current) whether the port trips on single uplink bursts (w ~ 1) or only under sustained TX, e.g. an iperf3 -u upload (w ~ 0) |
| usb-soak | USB disconnect events in a 24 h soak (dmesg 'USB disconnect', udev remove) | `usb.drop_rate_per_h`, `usb.drop_v_scale`, `usb.drop_i_scale_a`, `usb.reenum_sigma`, `usb.reenum_fail_prob` | journalctl -k -f / grep -i usb for 24 h at two margins (5 A PSU vs 3 A PSU) | journalctl | rate vs margin gives base and scales; time to return gives the re-enum distribution |
| bu-boot-loop | 50 cold boots / hot-plugs: stage outcomes and durations from the logs | `bringup.backoff_base_s`, `bringup.backoff_factor`, `bringup.usb_probe_fail_p`, `bringup.usb_probe_timeout_s`, `bringup.usb_probe_ok_s`, `bringup.fw_fail_p`, `bringup.fw_timeout_s`, `bringup.fw_ok_s`, `bringup.monitor_fail_p`, `bringup.monitor_timeout_s`, `bringup.monitor_ok_s`, `bringup.inj_start_fail_p`, `bringup.inj_start_timeout_s`, `bringup.inj_start_ok_s`, `bringup.max_attempts` | loop: power-cycle the dongle (uhubctl or relay) 50 times; log dmesg, iw set monitor exit codes, first wfb_tx send result | uhubctl or a relay, shell loop | failure fraction per stage = p; mean durations = timeouts/ok times |
| tm-latency-log | glass-to-glass latency over 30 min (LED + photodiode or phone slow motion), GStreamer queue levels, decoder stalls | `timing.clock_ppm`, `timing.queue_max_ms`, `timing.stall_rate_per_h`, `timing.stall_backlog_ms`, `timing.catchup_ms_per_s`, `timing.sched_jitter_median_ms`, `timing.sched_jitter_sigma`, `timing.sched_spike_prob`, `timing.sched_spike_mean_ms` | protocol: python3 tests/sim/models/latency_budget.py protocol; GST_DEBUG=GST_TRACER:7 latency tracer; watch latency drift over 30 min | photodiode + scope, GST tracers | creep slope = ppm; stalls from latency steps |
| tm-ntp-reorder | chrony/NTP step log of the host over a week; wfb_rx reorder counters | `timing.ntp_step_rate_per_h`, `timing.ntp_step_ms`, `timing.reorder_prob`, `timing.reorder_delay_ms` | chronyc tracking/journal; wfb-cli gs counters | chrony, wfb-cli | step rate and size from the journal; reorder from out-of-order seq counters |
| gp-scope | press/release bounce and glitches on the real button with a scope or logic analyser (>= 100 presses), and the edge timestamps libgpiod sees (gpiomon -r -f --format for 100 presses) | `gpio.bounce_count_mean`, `gpio.bounce_total_ms`, `gpio.release_bounce_scale`, `gpio.glitch_rate_per_s`, `gpio.glitch_width_us`, `gpio.chatter_prob`, `gpio.chatter_ms`, `gpio.rp1_irq_latency_us`, `gpio.rp1_edge_loss_prob`, `gpio.rp1_filter_us` | logic analyser on the pin; in parallel gpiomon --num-events=0 --format='%e %s.%n' for 100 presses on a Pi 5 and a Radxa | logic analyser, gpiomon | bounce stats from the analyser; RP1 latency = libgpiod timestamp - analyser edge; lost edges = analyser edges - gpiomon edges |
| gp-script-timing | real latencies of the button.sh loop: time between edge and uptime reading, and between exit and re-arm | `gpio.mon_start_ms`, `gpio.get_ms`, `gpio.uptime_ms`, `gpio.exit_ms`, `gpio.action_ms` | bash -x with timestamps (PS4='+ $EPOCHREALTIME ') on button.sh on the Pi 5; 50 presses | bash -x, EPOCHREALTIME | differences between consecutive trace lines |
| gp-human | hold times of single and long presses by 3 operators (20 each) | `gpio.single_hold_s`, `gpio.long_hold_mu_s`, `gpio.long_hold_sigma_s` | log press durations with a logic analyser | logic analyser | lognormal/normal fit |


## 6. Кнопка: справжній алгоритм `gs/button.sh` і дребезг

`gs/button.sh:button_action` (REPO): `gpiomon -r` чекає **перший** фронт, `sleep 0.05`, `gpioget` має дати `1` (єдина «дебаунс»-перевірка), `/proc/uptime` у сотих секунди (`cut -d ' ' -f1 | tr -d .`), `gpiomon -f` чекає **перший** спадний фронт (новий процес: фронти до його старту втрачено), знову uptime; `< 200` це `single`, інакше `long`. `gpio_bounce.replay_button_sh` повторює ці кроки по тракту фронтів із затримками процесів (`mon_start`, `get`, `uptime`, `exit`, `action`), фільтром дебаунсу (libgpiod/апаратний, 0 за замовчуванням: REPO) і RP1 (затримка IRQ, втрата фронтів, апаратний фільтр, усе UNMEASURED).

Що показує повтор (перевірено тестами `TestGpioBounce`):
- Вимір стартує **після** 50 мс очікування й затримок процесів, тож ефективний поріг довгого натискання **≈ 2,07 с**, а не 2,00 с: фізична витримка 2,03 с дає `single`.
- Дотик **коротший за ~60 мс** не бачиться зовсім (`gpioget` читає 0 → `continue`).
- Вібраційне розмикання під час довгого утримання закінчує вимір достроково (хибний `single`), наступний фронт стартує другий вимір.
- Імовірність хибної події падає з дебаунсом (тест: на «зношеному» вимикачі 80 мс дребезгу частка хибних падає щонайменше на 40 % при 40 мс дебаунсу) і з 50 мс очікуванням (без нього гірше).

**Формат подій дребезгу** `sbc-gs-gpio-bounce/1` (`scenario_engine.py bounce --kind long|single [--seed N]`; для агента `tests/sim/virt` → gpio-sim):

```
{"schema": "sbc-gs-gpio-bounce/1", "scenario": str, "seed": int,
 "line": {"pull": "down", "active_level": 1, "idle_level": 0},
 "intent": {"kind": "single"|"long", "press_at_us": int, "hold_us": int},
 "events": [{"t_us": int, "level": 0|1, "cause": "press"|"press_bounce"|"release"|"release_bounce"|"glitch"|"chatter"}],   // відсортовані, лише зміни рівня
 "algorithm": {"settle_s": 0.05, "long_threshold_cs": 200, "debounce_ms": 0.0},
 "expected_actions": ["single"|"long", ...]}    // що видасть алгоритм button.sh на цьому тракті
```
Писач gpio-sim виставляє рівень лінії в моменти `t_us`; очікувані дії порівнюються з виходом справжнього `button.sh`. Події сесії каналу (`scenario_engine.py events <сценарій>`) мають схему `sbc-gs-degrade-events/1`: форми `usb_drop`/`usb_return`/`undervoltage`/`voltage_ok`/`throttled` ідентичні `sbc-gs-usbfault/1` (`power_model.py`), додатково `thermal_*`, `burst_*`, `shock`, `stall`, `creep_reset`, `usb_trip`, `usb_latched`, `bringup`.

## 7. Еталонні сценарії (golden) і що з них видно

Усе: n = 60, seed = 1, зріз 20 с, сесія 600 с після bring-up. Стовпчики p5/p50/p95/p99 за draw; для «поганого малого» (запас, дальність, доступність) читати p5, для «поганого великого» (втрати, затримка) p95/p99. Для лінку, що не працює (bring-up не вдався або жодного зрізу з піднятим лінком), `margin_db` **не визначений**: у виході його немає (`dead`-прапорець, рядок `P(dead link ...)`; перцентилі `margin_db` лише по draw з лінком, у таблиці `nan`, якщо таких немає); `residual = 1` і `availability = 0` це фізичні межі, а не заглушка (D4 виправлено; стара заглушка `-60 дБ` лежала всередині фізичного діапазону).

**nominal_pi5_5a_150m**: 150 м, MCS1, FEC 8/12, 720p 4000 кбіт/с, Pi 5 з БЖ 5 А.

```
# scenario_engine: nominal_pi5_5a_150m kind=link n=60 seed=1 antithetic=0
# session: distance=150m duration=600s dt=20s board=pi5 psu=5.0A usb_max_current=0 codec=h265 spec: residual<=0.01 g2g<=250ms freeze<=1
# priors: 145 sampled dims (INF=4 SYNTH=67 UNMEASURED=74); pinned: rf.fading_model,rf.loss_model
output,unit,p5,p50,p95,p99,mean
residual,frac,0.001196,0.04374,0.5113,0.9774,0.1179
margin_db,dB,3.954,11.45,16.63,19.08,10.8
range_m,m,81.03,351.3,1240,1479,416.2
g2g_ms,ms,164,194.6,235.8,249.4,195.3
availability,frac,0,0.8667,1,1,0.75
ttff_s,s,0,120,600,600,215
P(no failure in session)=0.150  bring-up p50=3.6s
P(dead link: no up slice, margin_db undefined)=0.017; margin_db percentiles/mean are over the 59 of 60 draws with a link
failure_mode,probability
bringup_fail,0.033
thermal_derate,0.067
thermal_shutdown,0.017
usb_dropout,0.083
usb_trip,0.000
usb_latched,0.000
undervoltage,0.167
soc_throttle,0.033
agc_saturation,0.000
desense,0.183
burst_outage,0.300
injection_overload,0.083
idr_freeze,0.800
latency_creep,0.117
fec_exhaust,0.550
# SYNTH/UNMEASURED priors: planning numbers, NOT predictions; low-is-bad outputs (margin, range, availability) read p5, high-is-bad read p95/p99
```


**pi5_3a_weak_psu**: те саме з БЖ 3 А без `usb_max_current_enable` (ліміт USB 0,6 А).

```
# scenario_engine: pi5_3a_weak_psu kind=link n=60 seed=1 antithetic=0
# session: distance=150m duration=600s dt=20s board=pi5 psu=3.0A usb_max_current=0 codec=h265 spec: residual<=0.01 g2g<=250ms freeze<=1
# priors: 145 sampled dims (INF=4 SYNTH=67 UNMEASURED=74); pinned: rf.fading_model,rf.loss_model
output,unit,p5,p50,p95,p99,mean
residual,frac,1,1,1,1,1
margin_db,dB,nan,nan,nan,nan,nan
range_m,m,0,0,0,0,0
g2g_ms,ms,145.9,169.7,190.4,208.8,169.2
availability,frac,0,0,0,0,0
ttff_s,s,0,0,0,0,0
P(no failure in session)=0.000  bring-up p50=3.6s
P(dead link: no up slice, margin_db undefined)=1.000; margin_db percentiles/mean are over the 0 of 60 draws with a link
failure_mode,probability
bringup_fail,0.033
thermal_derate,0.067
thermal_shutdown,0.017
usb_dropout,0.200
usb_trip,1.000
usb_latched,1.000
undervoltage,0.167
soc_throttle,0.033
agc_saturation,0.000
desense,0.183
burst_outage,0.300
injection_overload,0.083
idr_freeze,0.000
latency_creep,0.000
fec_exhaust,0.000
# SYNTH/UNMEASURED priors: planning numbers, NOT predictions; low-is-bad outputs (margin, range, availability) read p5, high-is-bad read p95/p99
```


**hot_day_closed_case**: ~38 °C, сонце, закритий корпус AIR, потужність 25 дБм.

```
# scenario_engine: hot_day_closed_case kind=link n=60 seed=1 antithetic=0
# session: distance=150m duration=600s dt=20s board=pi5 psu=5.0A usb_max_current=0 codec=h265 spec: residual<=0.01 g2g<=250ms freeze<=1
# priors: 144 sampled dims (INF=4 SYNTH=67 UNMEASURED=73); pinned: rf.fading_model,rf.loss_model,rf.tx_power_dbm
output,unit,p5,p50,p95,p99,mean
residual,frac,0.002661,0.06552,1,1,0.197
margin_db,dB,0.6508,9.531,16.77,18.46,9.81
range_m,m,0,277.5,959.9,1212,355.4
g2g_ms,ms,161.8,197.6,243.3,266.7,198.5
availability,frac,0,0.8167,1,1,0.65
ttff_s,s,0,60,600,600,158.7
P(no failure in session)=0.100  bring-up p50=3.6s
P(dead link: no up slice, margin_db undefined)=0.100; margin_db percentiles/mean are over the 54 of 60 draws with a link
failure_mode,probability
bringup_fail,0.000
thermal_derate,0.450
thermal_shutdown,0.117
usb_dropout,0.033
usb_trip,0.000
usb_latched,0.000
undervoltage,0.083
soc_throttle,0.233
agc_saturation,0.000
desense,0.200
burst_outage,0.350
injection_overload,0.067
idr_freeze,0.800
latency_creep,0.150
fec_exhaust,0.633
# SYNTH/UNMEASURED priors: planning numbers, NOT predictions; low-is-bad outputs (margin, range, availability) read p5, high-is-bad read p95/p99
```


**button_bouncy_switch** (кнопка зі зношеним контактом, 30 коротких і 30 довгих натискань на draw).

```
# scenario_engine: button_bouncy_switch kind=button n=60 seed=1 antithetic=0
# priors: 18 sampled dims (SYNTH=10 UNMEASURED=8); pinned: rf.fading_model,rf.loss_model
output,unit,p5,p50,p95,p99,mean
false_event,frac,0.25,0.4167,0.5667,0.6615,0.4053
false_event_single,frac,0,0.1,0.3683,0.6137,0.1261
false_event_long,frac,0.2983,0.6833,0.935,1,0.6844
single_extra,frac,0,0,0.06833,0.1547,0.01667
long_as_single,frac,0.1,0.25,0.4333,0.5213,0.2617
long_missed,frac,0,0,0.03333,0.08033,0.005
failure_mode,probability
button_false_event,1.000
```


Інші сценарії: `elrs_usb3_desense_edge` (400 м, USB3-шум завжди, сильніший зв'язок модулів), `close_range_agc` (3 м, 27 дБм, MCS4: AGC і стиснення PA), `bitrate_overshoot_edge` (перевищення бітрейту ×1,6), `button_nominal`. Запуск: `tests/sim/models/scenario_engine.py run <сценарій> --n 200 [--seed N] [--antithetic]`.

Висновки, які ці числа **підтримують як гіпотези для стенду** (INF, не факти): (1) БЖ 3 А без `usb_max_current_enable` майже напевно валить лінк на першому ж TX-сплеску (`usb_trip` 1,0 за пріорами, бо піковий струм адаптера вище 0,6 А), це збігається з висновком `docs/SIM-MODELS.md` про домінантний ризик USB; (2) у спеку із закритим корпусом тепловий дерейтинг/зупин AIR стає головним режимом (`thermal_derate` ≈ 0,6); (3) на зношеному вимикачі більшість довгих натискань розпізнається хибно за дизайном (поріг ≈ 2,07 с, дребезг, вібрація), тож одного 50 мс-очікування замало.

## 8. Каталог позанормальних сценаріїв

`scenarios/catalog.json` (читається машиною; схема перевіряється `test_degrade.py`): 37 сценаріїв. Ймовірність `p` це **SYNTH-пріор «за 1 годину сесії»** з обґрунтуванням у JSON (`prior_prob.rationale`), а не виміряна частота. Стовпчик «Режим рушія» зв'язує сценарій із режимом відмови, який рахує `scenario_engine` (`-` = лише каталог), стовпчик F посилається на умови хибної впевненості `docs/SIM-BLOCKERS.md` §5 (F1-F19: наприклад F4 «hwsim має ідеальне радіо», F5 «втрати veth заднано вручну», F9 «модель із заглушками виглядає як прогноз», F11 «зелена збірка ≠ правильний рантайм»).

### Каталог позанормальних сценаріїв (`scenarios/catalog.json`)

| ID | Сценарій | Контур | p (SYNTH, /1 год) | Тяжкість | Режим рушія | Шари | F |
|---|---|---|---|---|---|---|---|
| S01 | AIR adapter thermal derate then shutdown | AIR | 0.08 | 4 | thermal_derate | models, HW | F9, F4 |
| S02 | USB3 / ELRS-900 harmonic / Multi 2.4 GHz desense of the GS receiver | GS | 0.10 | 3 | desense | models, HW | F9, F18 |
| S03 | Brown-out of the AIR supply on a TX burst | AIR | 0.05 | 4 | - | models, HW | F9 |
| S04 | GS USB re-enumeration mid-flight | GS | 0.06 | 5 | usb_dropout | models, virt.usb, HW | F3, F4 |
| S05 | Pi 5 USB current-limit trip (600 mA vs 1.6 A) | GS | 0.12 | 5 | usb_trip | models, virt.usb, HW | F9 |
| S06 | Wrong channel / channel clash with a neighbour | AIR+GS | 0.15 | 3 | burst_outage | models, qemu_hwsim, HW | F4, F5 |
| S07 | GCS heartbeat loss (MAVLink GCS timeout) | HOST | 0.05 | 5 | - | smoke.mavlink, smoke.model | F7, F8 |
| S08 | ELRS failsafe plus stale wfb RC override (ArduPilot #32862) | RC | 0.03 | 5 | - | smoke.model, smoke.mavlink, HW | F7, F8, F19 |
| S09 | FC UART overrun / baud mismatch on the WiFiLink2 single UART | AIR | 0.07 | 3 | - | smoke.mavlink, smoke.router, HW | F8 |
| S10 | majestic bitrate overshoot beyond the airtime | AIR | 0.12 | 4 | injection_overload | models, wfb_veth | F5, F10 |
| S11 | IDR loss causes a long freeze | AIR+GS | 0.10 | 3 | idr_freeze | models, smoke.video, wfb_veth | F4, F5 |
| S12 | Driver built for 4K pages breaks on Pi 5 16K pages | GS | 0.10 | 5 | - | dkms, HW | F11, F16 |
| S13 | DKMS module breaks after a kernel upgrade | GS | 0.15 | 4 | - | dkms, smoke.static | F11 |
| S14 | SD-card corruption / overlayroot absent | GS | 0.04 | 3 | - | HW | F2, F16 |
| S15 | Pi 5 SoC throttling | GS | 0.07 | 3 | soc_throttle | models, HW | F9, F12 |
| S16 | Host Wayland / GStreamer sink failure | HOST | 0.08 | 3 | - | smoke.video, HW | F17 |
| S17 | Clock step by NTP | HOST | 0.10 | 2 | - | models, smoke.static | F13 |
| S18 | TX12 enumerates as a different USB device node / event number | HOST | 0.12 | 4 | - | virt.usb, smoke.mavlink | F8, F3 |
| S19 | evdev axis reorder (EdgeTX mixes) | HOST | 0.08 | 4 | - | smoke.mavlink, HW | F19 |
| S20 | Button bounce causes a false long/short press | GS | 0.20 | 3 | button_false_event | models, virt.gpio, HW | F3, F1 |
| S21 | udev rename race for wifi0 / rpi0 | GS | 0.06 | 3 | - | virt.usb, virt.radio, smoke.static | F2, F3 |
| S22 | wfb key mismatch after reflash | AIR+GS | 0.08 | 3 | - | qemu_hwsim, wfb_veth | F6 |
| S23 | alink oscillation (adaptive bitrate flapping) | AIR+GS | 0.10 | 2 | fec_exhaust | models, wfb_veth | F5, F10 |
| S24 | Latency creep from GStreamer queue growth | HOST | 0.15 | 2 | latency_creep | models, smoke.video | F12 |
| S25 | Decoder stall / buffer backlog | GS | 0.12 | 2 | latency_creep | models, smoke.video | F12 |
| S26 | AGC saturation at very close range | GS | 0.05 | 3 | agc_saturation | models, HW | F4, F9 |
| S27 | USB probe failure / timeout at boot | GS | 0.05 | 3 | bringup_fail | models, virt.usb | F2 |
| S28 | Monitor-mode set fails (device busy) | GS | 0.06 | 3 | bringup_fail | virt.radio, smoke.static | F4 |
| S29 | Injection EBUSY / queue full | AIR | 0.05 | 3 | injection_overload | models, wfb_veth | F5 |
| S30 | FEC exhausted at the edge of range | AIR+GS | 0.20 | 3 | fec_exhaust | models, wfb_veth, qemu_hwsim | F4, F5, F10 |
| S31 | Noise-floor shock (nearby transmitter, ESC) | AIR+GS | 0.12 | 2 | burst_outage | models, HW | F9 |
| S32 | FC / VTX ground reference (UART) noise from ESC | AIR | 0.05 | 3 | - | HW | F18 |
| S33 | Wi-Fi hotspot / LAN loss between Pi and host | HOST | 0.10 | 3 | - | smoke.router, smoke.video | F2 |
| S34 | Wrong regdomain limits TX power / channels | AIR+GS | 0.06 | 2 | - | virt.radio, smoke.static | F4 |
| S35 | Pi 5 PSU undervoltage (throttled word) | GS | 0.10 | 4 | undervoltage | models, HW | F9 |
| S36 | Latency spec exceeded by scheduling spikes | HOST | 0.10 | 2 | latency_creep | models, smoke.video | F12, F13 |
| S37 | Bring-up never finishes (stage fails after retries) | GS | 0.04 | 4 | bringup_fail | models, virt.usb | F2 |


### Каталог: тригер, симптом, виявлення, пом'якшення, тест виявлення

| ID | Тригер | Симптом | Виявлення | Пом'якшення / fallback | Ідея тесту виявлення (шар) |
|---|---|---|---|---|---|
| S01 | ambient > 35 C, enclosure in the sun, TX at flight power for > 10 min | video loss rises gradually, then link drops for a cooldown; wfb-cli shows RSSI falling at the AIR side | VTX temperature telemetry (majestic), RSSI trend vs time; thermal alarm in OSD | fan, heatsink, lower txpower, shade; ground-test 30 min at flight power | engine scenario hot_day_closed_case: P(thermal_derate) and P(thermal_shutdown) must exceed the nominal scenario; on HW log Tj vs time |
| S02 | USB3 device active, or ELRS 900 / Multi 2.4 module transmitting next to the dongle | noise floor rises 2..10 dB, range shrinks, FEC exhausted at edge of range | compare noise floor/PER with modules and USB3 devices off vs on at fixed attenuation | dongle on a USB2 port or extension cable, ferrites, antenna spacing, module power down | engine scenario elrs_usb3_desense_edge at 900 m: P(desense) > nominal; HW: switch modules on/off while logging wfb-cli loss |
| S03 | txpower raised to 25+ dBm with a weak BEC, battery sag at full throttle | periodic dips in RSSI, EVM rise, VTX reboot at worst | scope on the 5 V pad; majestic log reboots | stronger BEC, LC filter, lower txpower | engine: P(thermal/sag) via hw.air_* priors; unit test compression + sag lowers Pout |
| S04 | undervoltage on the 5 V rail, over-current trip, spontaneous disconnect | video freezes 3-10 s then returns only if bring-up reruns; wfb_rx loses the interface | dmesg 'USB disconnect', udev remove/add, wfb-ng restart count | 5 A PSU + usb_max_current_enable, short thick cable, systemd restart policy for wfb-ng | virt usb: hot-unplug the dwc2/vudc device and check gs-mavlink + wfb restart; engine pi5_3a_weak_psu: P(usb_dropout|usb_trip) |
| S05 | 3 A PSU, adapter + FC + fan on USB, TX burst | adapter vanishes at every TX burst; repeated trips, latched port | dmesg over-current, get_throttled, lsusb after burst | 5 A PSU or usb_max_current_enable=1, bus-powered devices on a powered hub | engine pi5_3a_weak_psu: P(usb_trip)=1 for 3 A without the flag (power_model budget); virt usb gadget removal replays the events |
| S06 | two systems on the same channel, DFS/regdomain differences | bursty loss episodes, RSSI normal but SNR low | channel-scan.sh survey at the site; loss bursts in wfb-cli | channel scan before flight, move channel, regdomain check | qemu_hwsim negative control (other channel hears nothing) proves isolation; engine ext.clash_* gives burst_outage probability |
| S07 | GCS heartbeat stops (host sleep, router crash) with FS_GCS_ENABLE set | ArduPilot GCS failsafe (RTL/LAND) or, if FS_GCS_ENABLE=0, none | apm_fc/SITL timeline 'GCS Failsafe'; HEARTBEAT age in gs_mav | set FS_GCS_ENABLE deliberately, heartbeat source documented, never rely on the bridge heartbeat | smoke mavlink path: fake_fc/apm_fc timeouts; sitl_probe failsafe observations |
| S08 | ELRS link lost while a wfb RC_CHANNELS_OVERRIDE is still active or reverts to stale RC values | FC follows an old override or stale RC instead of failsafe | apm_fc rc_input model (#32862 behaviour); RCIN log on HIL | wfb RC strictly as a reserve with expiry; failsafe on no pulses; never the sole channel | apm_model test of override expiry and revert-to-stale; HIL: pull the ELRS antenna with the bridge running |
| S09 | baud differs between SERIALx_BAUD and the VTX, or two services read the port | MAVLink CRC errors, missing telemetry, heartbeats flicker | mavlink-router stats, link CRC counter, SERIAL port stats | pin baud in config, single reader, CRC check in smoke | gs_mav.py --selftest with corrupted frames; HW: change baud and watch CRC counters |
| S10 | bitrate x overshoot x n/k exceeds the air utilisation | queue drops, growing latency, I-frame loss, freezes | airtime utilisation in rf_model, injection drops, freeze fraction | bitrate with 30 % headroom, lower MCS cost awareness, alink | engine: rho > 1 sets injection_overload; wfb_veth with relay at the model's loss; rf_model utilisation > 1 flag |
| S11 | I-frame packets unrecoverable after FEC | video frozen/garbled until the next IDR (up to GOP seconds) | freeze fraction, decoder errors, missing IDR counter | shorter GOP, intra refresh, IDR request, FEC 8/12 tuned | engine freeze_fraction monotone in loss; smoke video with induced loss via air_relay and a keyframe-gap check |
| S12 | rtl8812au loaded on rpi-2712 16K | probe fails or memory corruption, no wlan interface | dkms status, dmesg, iw dev empty | use kernel8 (4K) on Pi 5 or a fixed driver; pin the kernel | tests/sim/dkms builds against the rpi-2712 headers; runtime proof only on HW |
| S13 | apt upgrade installs a newer kernel | no wlan interface after reboot | dkms status in check.sh, modinfo vermagic vs uname -r | apt-mark hold kernel, dkms autoinstall check, rollback image | dkms run.sh --build against two header sets; static check that the hold/dkms status step exists |
| S14 | power cut while writing recordings or logs | fsck on boot, services fail, read-only remount | journalctl, dmesg ext4 errors | overlayroot, separate recording partition, 20 power-cut test | HW: 20 power-cuts during recording; sandbox golden tests cannot show it |
| S15 | SoC temp above the soft limit | decode slows, latency rises, frames dropped | vcgencmd get_throttled bit 3/19, latency tracer | cooler/fan, HEVC hardware decode, lower resolution | engine hw.soc_* priors: P(soc_throttle) and latency impact; HW vcgencmd log |
| S16 | sink element missing or compositor refuses | black window, pipeline error, no video on the host | gst-launch exit code, pipeline bus errors | fallback sink chain (waylandsink -> glimagesink -> fakesink) selected by probe | smoke video with SINK=fakesink vs a forced bad sink: pipeline must fail loudly; HW on the 26.04 host |
| S17 | chrony/NTP steps the wall clock while logging or recording | log timestamps jump, recording file names collide, wall-clock latency probes break; /proc/uptime unaffected | journal 'time stepped', monotonic vs wall-clock diff | use CLOCK_MONOTONIC for intervals; button.sh uses /proc/uptime (immune) | clock_events() produces ntp_step events; unit test that button timing does not depend on wall time |
| S18 | replug, second HID, EdgeTX mode change | tx12_bridge opens the wrong device, no RC or wrong axes | by-id path check, device name check at start | open by /dev/input/by-id and name match, refuse if ambiguous | virt: create two uinput devices in swapped order and require the bridge to pick the TX12 by name |
| S19 | different model loaded on the TX12 | channels 1-4 land on the wrong RC channels | assert channel map against a known pattern at start | channel map in config with a start-up wiggle check | tx12_bridge --input sweep with permuted axes must be rejected by a map check |
| S20 | press on a bouncy switch, chatter during a long hold | single press fires twice or long press fires as single(s) | bounce trace JSON + algorithm replay (gpio_bounce.py) | debounce in libgpiod (debounce period), hardware RC filter, state machine | gpio_bounce.py trace JSON is fed to gpio-sim; test: false-event probability falls with debounce |
| S21 | adapter probe order changes | interface named wlan1 not wifiX; scripts act on the wrong iface | udevadm test, ip link after boot | match by MAC/driver, wait for rename before starting services | virt radio: create hwsim radios in swapped order and check rename; udevadm verify |
| S22 | VTX reflashed or SD replaced | wfb_rx accepts nothing, no video, 'no session key' errors | wfb-cli shows no sessions; key fingerprint compare | print/compare key fingerprints in bring-up, backup keys | qemu_hwsim negative control (unrelated keypair accepts nothing) is the detection |
| S23 | loss bursts near the link margin | bitrate and quality oscillate, latency saw-tooth | bitrate time series | hysteresis, longer averaging, floor on bitrate | engine: g2g tail and freeze under burst chain; veth ramp via relay_from_model with a loop that toggles bitrate |
| S24 | hours-long run with ppm drift | glass-to-glass grows by tens of ms until frames drop | latency tracer over 30 min | leaky queues, sync=false/low-latency sink, periodic reset | engine timing.clock_ppm: P(latency_creep); smoke video_latency.py p95 trend over a long run |
| S25 | hiccup in the decoder or sink | single frozen second, then latency raised until catch-up | stall counters, latency steps | queue limits, catch-up policy | engine stall process adds backlog; check that latency recovers |
| S26 | RX level above the AGC knee | high loss at short range, better at 20 m | PER vs attenuation sweep | attenuate on the bench, lower txpower at short range | engine scenario close_range_agc: P(agc_saturation) near 1; hw.rx_agc_* calibration sweep |
| S27 | cold boot or hot-plug | no wlan interface until retry | dmesg 'device descriptor read error -71' | retry with backoff, uhubctl power cycle | engine bringup(): analytic vs Monte-Carlo success; virt usb: delayed gadget attach |
| S28 | iw set type monitor with NM managing the device | wfb_rx cannot open the iface | exit code of iw, nmcli device status | unmanaged-devices in NM, retry | virt radio hwsim: set monitor while an NM-like process holds it; assert the script retries |
| S29 | I-frame burst larger than queue | dropped data and parity, FEC cannot cover | send errors counter in wfb_tx | bitrate cap, bigger queue, pacing | engine M/M/1/K block probability monotone in utilisation; wfb_veth with relay loss as the proxy |
| S30 | SNR margin < 0 for several seconds | bursty video loss | residual loss and margin in the engine; wfb-cli | lower MCS, FEC k/n, antenna diversity | engine fec_exhaust probability; veth relay at the model's loss for the same distance |
| S31 | motor spin-up, neighbour transmitter starts | short outage, retrain | noise floor log | shielding, routing, channel change | engine proc.shock_* : availability falls monotonically with the shock rate |
| S32 | motors at high throttle | CRC errors, dropped telemetry | UART error counters | twisted pair, ferrites, short wires | HW only: LQ and UART errors with motors spinning, props off |
| S33 | hotspot reconnects, cable unplugged | video freeze, GCS lost | ping loss, link state | wired link, restart policy | smoke router/video with the link down mid-run (veth down) must recover |
| S34 | wrong country code on the GS or VTX | txpower capped, channel unavailable | iw reg get, iw list | set regdomain in bring-up, verify channel/power | virt radio: iw reg set and assert the chosen channel is allowed; static check of the config |
| S35 | TX burst plus load on 5.1 V with a 0.15 ohm path | undervoltage flag, USB drops, throttling | get_throttled 0x50005, dmesg | 5 A PSU, thick cable | engine undervoltage flag from power_model budget; power_model throttled decode tests |
| S36 | CPU contention | occasional 100+ ms latency spikes | latency p95/p99 tracer | pin cores, priority, lighter host load | engine sched_spike_*: g2g tail percentile; smoke video latency p95 as a regression guard |
| S37 | several stages fail in sequence | service gives up, GS idle | bring-up log, systemd status | watchdog + escalating power-cycle of the adapter | engine bringup_fail probability vs max_attempts |


Шари тестів: `models` = цей рушій; `smoke.*` = шляхи `tests/sim/smoke.sh`; `wfb_veth`, `qemu_hwsim` = `tests/sim/`; `virt.gpio|usb|radio` = `tests/sim/virt/`; `dkms` = `tests/sim/dkms/`; `HW` = лише залізо (віртуального відтворення немає або воно не доводить).

## 9. Рейтинг чутливості: що міряти першим

Метод: **Morris** (елементарні ефекти) у просторі квантилів пріорів; рівні 0,02..0,98 з кроком 2/3; μ* = середнє |ефект| переміщення одного пріору через ~2/3 його діапазону **в одиницях виходу**; σ = нелінійність/взаємодії; частка = μ*/Σμ*. Усі проходи в траєкторії використовують **спільні випадкові числа** (підпотоки), тож ефект параметричний, а не шумовий. «Шум процесу» = std виходу за 24 seed при медіанних параметрах (що робить сама випадковість). Для виходів зі стрибками (відвал USB, виведення з ладу) μ* і σ великі: це клиф, а не плавний вплив. Мертвий лінк не має `margin_db`: ефекти `margin_db` рахуються лише з пар точок, де лінк працює в обох, а перехід живий/мертвий має власний рядок `dead` (0/1, не входить у «що міряти першим»); стара заглушка `-60 дБ` давала в `margin_db` μ* 34,8 дБ для `power.tx_peak_factor`: це стрибок заглушки, не фізика. Файли: `golden/sensitivity_nominal_pi5_5a_150m.txt` (r=3, сесія 300 с, зріз 30 с), `golden/sensitivity_button_nominal.txt` (r=6). **r малий, ранги наближені**: між сусідніми рангами різниця часто в межах шуму Morris; надійні лише лідери.

### 9.1 Канал: топ-5 по кожному виходу (nominal_pi5_5a_150m)


**residual** (шум процесу, std = 0.04219)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `power.devices.rtl8812_tx_a` | UNMEASURED | 0.3642 | 0.6308 | 13.7 |
| 2 | `usb.drop_v_scale` | SYNTH | 0.3121 | 0.5406 | 11.8 |
| 3 | `rf.noise_figure_db` | UNMEASURED | 0.2541 | 0.361 | 9.6 |
| 4 | `hw.ant_null_mean_db` | SYNTH | 0.2448 | 0.2191 | 9.2 |
| 5 | `rf.rx_antenna_gain_dbi` | UNMEASURED | 0.2089 | 0.08581 | 7.9 |

**margin_db** (шум процесу, std = 1.618)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `rf.path_loss_exponent` | INF | 12.69 | 2.427 | 17.0 |
| 2 | `hw.ant_null_mean_db` | SYNTH | 5.67 | 5.565 | 7.6 |
| 3 | `rf.tx_power_dbm` | UNMEASURED | 5.445 | 7.7 | 7.3 |
| 4 | `rf.rx_antenna_gain_dbi` | UNMEASURED | 5.431 | 1.094 | 7.3 |
| 5 | `rf.tx_antenna_gain_dbi` | UNMEASURED | 4.61 | 3.103 | 6.2 |

**range_m** (шум процесу, std = 48.67)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `rf.path_loss_exponent` | INF | 437.8 | 658.1 | 12.7 |
| 2 | `rf.tx_antenna_gain_dbi` | UNMEASURED | 382.1 | 468.8 | 11.1 |
| 3 | `rf.misc_loss_db` | UNMEASURED | 259.4 | 143.1 | 7.6 |
| 4 | `rf.rician_k_db` | UNMEASURED | 229.8 | 225.4 | 6.7 |
| 5 | `rf.rx_antenna_gain_dbi` | UNMEASURED | 213.2 | 132 | 6.2 |

**g2g_ms** (шум процесу, std = 4.242)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `latency.encode_frames` | UNMEASURED | 44.18 | 2.829 | 15.3 |
| 2 | `power.devices.rtl8812_tx_a` | UNMEASURED | 36.64 | 63.46 | 12.7 |
| 3 | `timing.catchup_ms_per_s` | UNMEASURED | 29.01 | 50.24 | 10.0 |
| 4 | `latency.capture_frames` | UNMEASURED | 25.4 | 0 | 8.8 |
| 5 | `latency.display_queue_frames` | UNMEASURED | 25.4 | 0 | 8.8 |

**availability** (шум процесу, std = 0.08065)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `usb.drop_v_scale` | SYNTH | 0.3125 | 0.5413 | 12.0 |
| 2 | `rf.path_loss_exponent` | INF | 0.2604 | 0.3253 | 10.0 |
| 3 | `power.tx_peak_factor` | UNMEASURED | 0.2083 | 0.3608 | 8.0 |
| 4 | `rf.implementation_loss_db` | UNMEASURED | 0.2083 | 0.2387 | 8.0 |
| 5 | `rf.misc_loss_db` | UNMEASURED | 0.2083 | 0.1804 | 8.0 |

**ttff_s** (шум процесу, std = 112.8)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `rf.misc_loss_db` | UNMEASURED | 203.1 | 189.4 | 21.0 |
| 2 | `hw.multi24_tx_dbm` | UNMEASURED | 156.2 | 270.6 | 16.1 |
| 3 | `usb.drop_v_scale` | SYNTH | 140.6 | 243.6 | 14.5 |
| 4 | `hw.ant_body_prob` | SYNTH | 125 | 216.5 | 12.9 |
| 5 | `hw.ant_null_mean_db` | SYNTH | 125 | 216.5 | 12.9 |

**dead** (0/1: лінк ніколи не працює; не входить у «що міряти першим»; шум процесу, std = 0)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `power.devices.rtl8812_tx_a` | UNMEASURED | 0.5208 | 0.9021 | 50.0 |
| 2 | `power.tx_peak_factor` | UNMEASURED | 0.5208 | 0.9021 | 50.0 |
| 3 | `bringup.backoff_base_s` | SYNTH | 0 | 0 | 0.0 |
| 4 | `bringup.backoff_factor` | SYNTH | 0 | 0 | 0.0 |
| 5 | `bringup.fw_fail_p` | SYNTH | 0 | 0 | 0.0 |

**Що міряти першим** (UNMEASURED/SYNTH, сума часток по виходах)

| № | Параметр | Походження | сума часток, % |
|---|---|---|---|
| 1 | `rf.misc_loss_db` | UNMEASURED | 45.7 |
| 2 | `usb.drop_v_scale` | SYNTH | 41.9 |
| 3 | `power.devices.rtl8812_tx_a` | UNMEASURED | 36.7 |
| 4 | `hw.ant_null_mean_db` | SYNTH | 36.4 |
| 5 | `rf.rx_antenna_gain_dbi` | UNMEASURED | 33.2 |
| 6 | `power.tx_peak_factor` | UNMEASURED | 24.1 |
| 7 | `rf.noise_figure_db` | UNMEASURED | 24.1 |
| 8 | `hw.multi24_tx_dbm` | UNMEASURED | 23.2 |
| 9 | `rf.tx_antenna_gain_dbi` | UNMEASURED | 22.7 |
| 10 | `latency.encode_frames` | UNMEASURED | 22.5 |


### 9.2 Кнопка (button_nominal)


**false_event** (шум процесу, std = 0.0397)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `gpio.single_hold_s` | SYNTH | 0.1606 | 0.1531 | 15.1 |
| 2 | `gpio.long_hold_mu_s` | SYNTH | 0.1519 | 0.09939 | 14.3 |
| 3 | `gpio.rp1_filter_us` | UNMEASURED | 0.08681 | 0.1251 | 8.2 |
| 4 | `gpio.bounce_total_ms` | SYNTH | 0.08247 | 0.115 | 7.8 |
| 5 | `gpio.bounce_count_mean` | SYNTH | 0.08247 | 0.1395 | 7.8 |

**false_event_single** (шум процесу, std = 0.04394)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `gpio.single_hold_s` | SYNTH | 0.3385 | 0.281 | 27.1 |
| 2 | `gpio.get_ms` | UNMEASURED | 0.1649 | 0.156 | 13.2 |
| 3 | `gpio.mon_start_ms` | UNMEASURED | 0.1302 | 0.197 | 10.4 |
| 4 | `gpio.uptime_ms` | UNMEASURED | 0.1215 | 0.1262 | 9.7 |
| 5 | `gpio.bounce_total_ms` | SYNTH | 0.1128 | 0.1787 | 9.0 |

**false_event_long** (шум процесу, std = 0.04913)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `gpio.long_hold_mu_s` | SYNTH | 0.3038 | 0.1988 | 23.2 |
| 2 | `gpio.bounce_count_mean` | SYNTH | 0.1476 | 0.2051 | 11.3 |
| 3 | `gpio.glitch_rate_per_s` | SYNTH | 0.1476 | 0.2145 | 11.3 |
| 4 | `gpio.bounce_total_ms` | SYNTH | 0.1215 | 0.1533 | 9.3 |
| 5 | `gpio.release_bounce_scale` | SYNTH | 0.1128 | 0.1594 | 8.6 |

**single_extra** (шум процесу, std = 0)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `gpio.glitch_rate_per_s` | SYNTH | 0.008681 | 0.02126 | 50.0 |
| 2 | `gpio.single_hold_s` | SYNTH | 0.008681 | 0.02126 | 50.0 |
| 3 | `gpio.action_ms` | UNMEASURED | 0 | 0 | 0.0 |
| 4 | `gpio.bounce_count_mean` | SYNTH | 0 | 0 | 0.0 |
| 5 | `gpio.bounce_total_ms` | SYNTH | 0 | 0 | 0.0 |

**long_as_single** (шум процесу, std = 0.04556)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `gpio.long_hold_mu_s` | SYNTH | 0.2865 | 0.1388 | 23.9 |
| 2 | `gpio.bounce_count_mean` | SYNTH | 0.1476 | 0.2051 | 12.3 |
| 3 | `gpio.release_bounce_scale` | SYNTH | 0.1042 | 0.1423 | 8.7 |
| 4 | `gpio.bounce_total_ms` | SYNTH | 0.09549 | 0.1307 | 8.0 |
| 5 | `gpio.glitch_rate_per_s` | SYNTH | 0.09549 | 0.1594 | 8.0 |

**long_missed** (шум процесу, std = 0.006804)

| № | Параметр | Походження | μ* | σ | частка, % |
|---|---|---|---|---|---|
| 1 | `gpio.rp1_filter_us` | UNMEASURED | 0.01736 | 0.0269 | 40.0 |
| 2 | `gpio.exit_ms` | UNMEASURED | 0.008681 | 0.02126 | 20.0 |
| 3 | `gpio.bounce_total_ms` | SYNTH | 0.008681 | 0.02126 | 20.0 |
| 4 | `gpio.mon_start_ms` | UNMEASURED | 0.008681 | 0.02126 | 20.0 |
| 5 | `gpio.action_ms` | UNMEASURED | 0 | 0 | 0.0 |

**Що міряти першим** (UNMEASURED/SYNTH, сума часток по виходах)

| № | Параметр | Походження | сума часток, % |
|---|---|---|---|
| 1 | `gpio.single_hold_s` | SYNTH | 99.2 |
| 2 | `gpio.glitch_rate_per_s` | SYNTH | 80.0 |
| 3 | `gpio.rp1_filter_us` | UNMEASURED | 70.3 |
| 4 | `gpio.long_hold_mu_s` | SYNTH | 61.4 |
| 5 | `gpio.bounce_total_ms` | SYNTH | 54.0 |
| 6 | `gpio.exit_ms` | UNMEASURED | 44.4 |
| 7 | `gpio.mon_start_ms` | UNMEASURED | 41.9 |
| 8 | `gpio.bounce_count_mean` | SYNTH | 38.3 |
| 9 | `gpio.get_ms` | UNMEASURED | 25.7 |
| 10 | `gpio.release_bounce_scale` | SYNTH | 24.6 |


Як читати для понеділка (таблиці вище це r=3 регресійний знімок, а не рейтинг; для рейтингу r≥60 і ≥3 seed, `docs/SIM-VALIDATION.md` §0): **першим міряти** `rf.misc_loss_db` і коефіцієнти антен (калібрований стенд з атенюаторами), `power.devices.rtl8812_tx_a` і `power.tx_peak_factor` (USB-вимірник + осцилограф: вони визначають відвал/ліміт і провал; їхня роль у `margin_db` після D4 менша, у відвалі USB лишається), крок `hw-pa-efficiency` (потужність BEC проти ВЧ-виходу: від нього залежить тепло AIR), потужність TX і `hw.pa_p1db_out_dbm` (зсув дальності в кілька разів), далі `hw.ant_null_*`/`ant_body_*` (розподіл RSSI), `rf.path_loss_exponent` та `rf.rician_k_db` (політ над відомим відрізком), потім `usb.drop_v_scale` (24-годинний soak на двох БЖ) і затримки кадрів `latency.encode_frames`/`capture_frames`/`display_queue_frames` (фотодіод). Для кнопки: розподіл тривалості натискань (`gpio.single_hold_s`, `long_hold_mu_s`), частота EMI-голчиків, фільтр RP1 і затримки gpiomon/gpioget на Pi 5. Це рангування **самої моделі**: воно каже, що модель найбільше залежить від чого, а не що найгірше в реальності.

## 10. Чесність: що означають синтезовані числа, що рушій може й чого не може

**Що це за числа.** Кожен пріор з позначкою **SYNTH** це синтезований правдоподібний показник, придуманий для того, щоб рушій мав що семплювати; **UNMEASURED** це заглушка з INF-діапазоном. Жодне з 116 значень (48 UNMEASURED + 68 SYNTH) не виміряне на цьому контурі. Ймовірності каталогу (`prior_prob`) теж SYNTH. Сама форма розподілів (логнормальні, бета, трикутні) теж не є свідченням: її вибрано за зручність і підручникову логіку. Тому:

- перцентилі, «дальність за < 1 % втрат», «ймовірність відвалу USB 1,0» це **наслідки припущень**, а не вимірювання; їх **заборонено** використовувати як критерії приймання й цитувати як факт (`docs/SIM-BLOCKERS.md` F9);
- модель налаштована так, щоб еталонний сценарій був «здоровим, але не ідеальним» (контурні `base_dist`, відстань 150 м, поріг затримки 250 мс): це **робоча точка для порівнянь**, не твердження про реальну дальність (медіана дальності за пріорами сотні метрів, бо пріори свідомо консервативні щодо пакетності й антен);
- результати чутливі до структурних вибору (Rapp, перший порядок тепла, M/M/1/K, подвійна мішана завада): друга модель дала б інші числа.

**Що рушій здатен виявити.**
1. **Домінантні ризики за припущеннями**: які режими відмови найімовірніші за пріорами і яка комбінація (БЖ 3 А, спека, дребезг) переводить систему в зону відмови.
2. **Пріоритет тестів і вимірів**: рейтинг §9 каже, що міряти першим; каталог §8 пов'язує кожен позанормальний сценарій із шаром, що його може відтворити, і з тестом виявлення.
3. **Регресії логіки**: детерміновані golden (4 сценарії + 2 таблиці чутливості), 75 тестів властивостей і 22 мутації (§11) ловлять випадкову зміну логіки моделей (наприклад вимкнення стиснення, дебаунсу, ліміту USB).
4. **Дизайн-дефекти алгоритмів, що перевіряються детерміновано**: поріг ≈ 2,07 с замість 2,00 с, сліпа зона 60 мс і чутливість до вібрації в `gs/button.sh` випливають із коду, не з пріорів (тест на чистій трасі), їх можна виправити без заліза.

**Чого рушій не може.**
1. **Справжню поведінку кремнію**: RTL8812EU на AIR і RTL8812AU на GS (таблиці потужності, EEPROM, термозахист драйвера, швидкість у monitor/ін'єкції, USB-quirks, 5/10 МГц) невідомі; тепловий дерейтинг і поріг вимкнення тут лише параметри.
2. **Справжній ефір**: багатопроменевість, ЕМЗ ESC, сусідні мережі, ELRS 900 МГц і Multi 2,4 ГГц поблизу приймача 5,8 ГГц задані одним «ефективним» коефіцієнтом зв'язку, який ніхто не вимірював.
3. **Pi 5/RP1/USB-контролер, VideoCore, декодер**: поведінка автомата ліміту USB (3 спроби до затискання, час відключення) це припущення; RP1 GPIO має UNMEASURED параметри.
4. **Корельовані збої й невідомі невідомі**: рушій семплює незалежні пріори; два зв'язані режими (спека + слабка БЖ) моделюються лише там, де це закладено, а «чорні лебеді» (баг драйвера, версії прошивок) ним не породжуються (їх місце в каталозі).
5. **Валідацію**: збіг рушія з `rf_model`/`power_model` доводить узгодженість коду, а не істинність. Прогін veth/hwsim за моделлю не валідує модель (`docs/SIM-BLOCKERS.md` F10).
6. **Що на AIR працює 28 дБм саме так**, що UART WiFiLink2 ділиться між MAVLink і іншими службами тощо: це каталог (S09, S32), не фізика.

**Правило використання.** Після кожного вимірювання понеділка: перекрити пріор накладкою (§4), перезапустити сценарії, оновити golden свідомо, знизити храповик. Усе, що лишається SYNTH/UNMEASURED, має виглядати як таке в кожному виводі: рядок `# priors: ... SYNTH=.. UNMEASURED=..` друкується завжди.

## 11. Мутаційні перевірки та тести

`tests/sim/models/mutate.sh` застосовує 22 мутації до **копії** каталогу й вимагає, щоб `test_degrade.py` або `test_models.py` впали: вимкнення стиснення PA (M1), нульова інтенсивність Пуассона (M2), прибрання 50 мс очікування в `button.sh` (M3), прибрана стеля EVM (M4), тепловий зупин не спрацьовує (M5), черга ін'єкції не блокує (M6), перевищення храповика UNMEASURED (M7), ймовірність каталогу без SYNTH (M8), автомат Pi 5 не трипає (M9), небезпека відвалу росте із запасом (M10), seed ігнорується (M11), поріг довгого натискання 100 (M12), вимкнений фільтр дебаунсу (M13), bring-up не збоїть (M14), тепло AIR не залежить від ВЧ-потужності (M15, D1), простій AIR < 0 / енергія не зберігається (M16, D1b), виміряний струм не домінує над ККД (M17), мертвий лінк знову дає margin -60 (M18, D4), Morris змішує перехід живий/мертвий з margin (M19), усереднення завмирання без хвоста глибоких завмирань (M20) або знову 32 квантилі (M21, D9), прапорець `dead` втрачено (M22). Результат: усі 22 убиті (див. звіт виконання).

Тести (`test_degrade.py`, 75): схема й храповики, розподіли (монотонність квантилів, середні, бета-функція), перекриття вимірюванням, антитетичні пари, відтворюваність за seed і розбіжність за іншим seed, впорядковані перцентилі й ймовірності в [0,1], монотонність за відстанню/температурою/БЖ, нелінійність PA/EVM/тепла/AGC, узгодженість з `rf_model` при нейтральних деградаціях, автомат ліміту USB, bring-up (MC проти замкненої формули), M/M/1/K, час, дребезг і алгоритм кнопки, каталог (≥ 30, SYNTH, режими, шари, посилання F1-F19), енергетичний баланс AIR (D1/D1b), мертвий лінк без margin і Morris без стрибка заглушки (D4), інтегрування завмирання проти незалежного еталона в хвості (D9), ця документація, golden.

## 11. Міст до заліза: аркуш вимірювань, оверлей, what-if (`calib.py`, `bench/doctor.sh`)

Позначка: REPO (код і тести), числа моделі SYNTH/UNMEASURED.

1. `bench/doctor.sh [OUT.json]` на цільовому Pi збирає лише читанням факти HW (ядро, `PAGESIZE`, `get_throttled`, USB-дерево, monitor-режим, `gpioinfo`, лічильник USB/живлення в `dmesg`). Нічого не передає в ефір і не вантажить модулі. Відсутні інструменти дають `null`.
2. `tests/sim/models/calib.py sheet --top N` друкує рейтинг «міряти першим» із інструментом і способом вимірювання з секцій `calibration`; `template FILE` пише порожній оверлей `{ключ: null}`.
3. Після вимірювання заповнити оверлей (`{"value": x, "source": "..."}`; без `source` буде попередження) і запустити `calib.py whatif FILE --scenario S`: інструмент розкладає ключі між `MODEL_MEASURED` (params.json) і `DEGRADE_MEASURED` (params.degrade.json) і друкує вихід до/після. Виміряний ключ може як звузити, так і розширити розкид виходів.

## 12. Застереження після валідації (`docs/SIM-VALIDATION.md`)

Конкретне перше місце рейтингу §9 **не** стабільне між версіями моделі: після виправлень D1, D2, D4, D9, D12 воно змінилося (`power.devices.rtl8812_tx_a` був №1, тепер сума часток ставить на верх `rf.path_loss_exponent`, `ext.ambient_c`, `hw.air_board_heat_w`, `usb.drop_v_scale`, антенні коефіцієнти). Надійна лише група з ≈6 параметрів; місця всередині групи між seed не відтворюються (Спірмен 0,79 при r=6, 0,95 при r=60); для рейтингу брати r≥60 і середнє по ≥3 seed, p95 потребує n≈800..1600. Помилки моделі D1..D12 виправлено й закріплено тестами (`docs/SIM-VALIDATION.md`); усі числа лишаються SYNTH/UNMEASURED.
