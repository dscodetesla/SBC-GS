# Fuzz, property-тести та fault-injection для справжніх скриптів (`tests/sim/fuzz/`)

Статус: шар готовий і проходить у цьому контейнері (без заліза, без мережі, лише stdlib). Теги: **REPO** = відтворено командою в цьому репозиторії, **SYNTH** = згенерований вхід або вивід моделі (не вимір), **INF** = висновок, **UNVERIFIED** = не перевірялось. Нічого з `HW` тут не перевірено.
Межа роботи: `tests/sim/fuzz/**` і цей файл. Наявні файли проєкту **не змінювались**; знайдені дефекти описані в §4 і **не виправлені**.

## 1. Що перевіряється

Усі випадкові входи детерміновані (`random.Random` із seed, похідним від `FUZZ_SEED` і назви тесту), тож збій відтворюється тим самим seed.

| Шар | Об'єкт (справжній код) | Інваріанти | Клас тестів |
|---|---|---|---|
| Differential fuzz | `config/load.sh` проти `config/load.py` на випадкових файлах/значеннях/реєстрах | однаковий вердикт (rc, пари KEY=VALUE, повідомлення, шар, попередження SAFETY) у локалях `C` і `C.UTF-8`; весь `resolve` (env, default, `--i-know`) на випадкових синтетичних реєстрах | `TestConfigDifferential`, `TestConfigResolveDifferential` |
| Межі safety | 14 SAFETY-ключів реєстру | жорсткі межі діють в обох завантажувачах; лише `--i-know` послаблює межі (не тип); набір ключів і межі збігаються з незалежною копією таблиці `docs/CONFIG.md` §4 | `TestConfigDifferential` |
| Ін'єкції | завантажувачі, `sbc-gs-config check`, `load.py check`, `gs-mavlink.sh --print` (файл і середовище) | 18 пейлоадів × 11 шаблонів: маркер-файл не створюється (код з даних не виконується), код виходу лише 0/2 | `TestConfigInjection` |
| Оракул | `gs/mavlink/gs-mavlink.sh --print` | незалежний Python-оракул (вердикт і точний командний рядок) на випадкових конфігах + 23 явні межові випадки; порти в межах 1..65535; детермінізм | `TestGsMavlink` |
| Профіль плати | `gs/boards/validate.sh`, `gs/lib/board.sh`, `gs/lib/board_conf.py`, `gs/lib/gpio.sh` | профіль, який схвалив `validate.sh`, читається однаково shell-ом і Python-ом; невідомий `BOARD` дає rc 0/1 без виконання; `_gpio_map_lookup` дає лише цифри або rc 1 | `TestBoard` |
| udev | `gs/boards/render-udev.sh` | точна підстановка `@KEY@`, детермінізм, незакритий `@KEY@` = rc 1, пейлоади не виконуються | `TestUdevRender` |
| Збірка | `build/lib/fetch.sh` (`fetch_file`, `git_pin`, `pin_from_manifest`) | приймається лише відповідний sha256; невідповідність = rc 1 і немає файлу; формат = rc 2; нема `*.part.*`; `GS_ALLOW_UNPINNED` лише `1` | `TestFetch` |
| Пісочниця | `gs/gs-applyconf.sh`, `gs/fan.sh`, `gs/stream.sh` у `tests/lib/sandbox.sh` | виміряна поведінка при зіпсованих/порожніх конфігах, нульових і від'ємних значеннях, відсутньому `jq`, завислих `nmcli`/`jq` | `TestApplyconfFaults`, `TestFanFaults`, `TestStreamMissingTools` |
| Процеси | `bench/tx12_bridge.py` через UDP loopback з FC-заглушкою (`fc_sniff.py`) | SIGTERM/SIGINT: 3 кадри throttle-failsafe, потім 5 кадрів release (0), rc 0, замок вільний, жодного кадру після виходу, `--max-rate` у серії; зникнення входу (закритий pipe, закритий master pty): dead-man у межах 150..800 мс, release, потім тиша, міст живий; сміттєвий потік 2 МБ без `\n` не заважає сигналам; SIGKILL (межа) | `TestBridgeFaults` |
| Чисті функції | `sanitize`, `load_map`, `map_axis` мосту | вихід лише `int` у `[LO,HI]` або `65535`; абсурдне/нечислове/надто довге відкидається | `TestBridgeInputValidation` |
| Моделі (SYNTH) | `tests/sim/models`: rf, power, latency, degrade (чисті функції), gpio_bounce, scenario_engine | монотонність (дистанція: rx/SNR не зростають, PER/residual не спадають; більший струм: запас не зростає, вердикт не кращий; більший PSU: вердикт не гірший; вищий MCS: потрібен більший SNR, діапазон не більший, ефіру не більше), одиниці (+x дБ потужності = +x дБ SNR; подвоєння відстані = `10·n·log10 2`), відсутність NaN/inf на випадкових допустимих параметрах (кожен параметр із `min`/`max` береться всередині меж), впорядкованість мін/тип/макс, відтворюваність за seed, ймовірності в `[0,1]` | `TestModelProperties` |

Обсяг (базові лічильники з коду, множаться на `FUZZ_ITERS`): 300 випадкових файлів × 2 локалі; 60 файлів × 3 власники; ≈ 440 трійок (тип, межі, значення); 48 пар (SAFETY-ключ, значення, `--i-know`); 70 випадкових реєстрів; 40 + 23 + 10 конфігів gs-mavlink; 28 профілів плати; 14 + 13 пінів fetch; 150 бюджетів живлення; 40 + 20 + 8 RF-сценаріїв; 4000 + 3000 зразків для мосту; 7 процесних сценаріїв моста.

## 2. Як запускати

```
tests/sim/fuzz/run.sh --check          # швидко: FUZZ_ITERS=1, 5 паралельних шардів (7-8 с на 4 ядрах без навантаження)
tests/sim/fuzz/run.sh --long           # FUZZ_ITERS=8
tests/sim/fuzz/run.sh --check --strict-defects   # закріплені дефекти як ВИПРАВЛЕНІ: сьогодні дає 22 FAIL (по одному на дефект)
tests/sim/fuzz/run.sh --check -v       # докладний вивід
python3 tests/sim/fuzz/test_fuzz.py [-v] [Клас ...]        # unittest напряму
tests/sim/fuzz/mutate.sh [M1 M8 ...]   # мутації на КОПІЇ, ~1 хв
```
Середовище: `FUZZ_SEED` (типово 20261003), `FUZZ_ITERS` (масштаб ітерацій), `FUZZ_PY` (Python з pymavlink для тестів моста, типово `/opt/sbcvenv/bin/python`; без нього тести моста пропускаються), `FUZZ_REPO` (корінь для перевірки; `mutate.sh` вказує копію), `PY`, `FUZZ_SHARDS=0` (без шардів), `FUZZ_REPORT=<файл.json>` (дефекти та виміри в JSON, його пише `test_fuzz.py`).
Вихід: 0 pass, 1 fail, 77 skip (нема python3). Перевірки статичної якості: `shellcheck -x -S warning tests/sim/fuzz/*.sh` і компіляція `*.py` чисті (REPO).
Не зачеплено (за межею роботи): `tests/run.sh`, `tests/sim/smoke.sh`, `tests/README.md`, CI. Пропозиція підключення (не застосовано): у `tests/sim/smoke.sh` додати виклик `tests/sim/fuzz/run.sh --check`, у CI `shellcheck -x -S warning tests/sim/fuzz/*.sh` і `py_compile tests/sim/fuzz/*.py`.

Закріплені дефекти (`test_DEFECT_Dn_*`): тест проходить, поки дефект відтворюється, і **падає, коли його виправили** («no longer reproduces»); виправлення має перевернути закріплення й оновити цей файл. Це спосіб зафіксувати поведінку «як є», не ховаючи її.

## 3. Результати (цей контейнер, Python 3.11.15, bash 5.2.21, GNU sed 4.9, grep 3.11, 4 ядра)

REPO, виміряно:
- `--check`: **77 тестів, 0 пропущено, 7-8 с** (CPU ≈ 26 с; при завантаженні машини, load average ≈ 10, траплялось до 14-15 с, тобто бюджет 15 с тримається не завжди); seed 20261003.
- `--long` (×8, seed 7): 77 тестів, 34 с, усе зелено.
- Інші seed: 1, 2, 3, 4 (×2) і 11..16 (×3, без процесних тестів): усе зелено; нових розбіжностей не знайдено.
- Як `nobody` на записуваній копії (`setpriv --reuid=65534 --regid=65534 --clear-groups env HOME=/tmp TMPDIR=/tmp`, `chown 65534`): 77 тестів зелені, 7-9 с; мутації M6, M10, M23 також убиті.
- Мутації на копії: **23 з 23 убито, 0 вцілілих** (§5). Перша версія генератора дала 4 вцілілі (M1, M2, M17, M23); їх закрито доробкою генератора (односимвольні вставки, токени `$(…)`, відступи в лапках) і виправленням M23, див. §5.
- `--strict-defects`: 22 FAIL = 22 закріплені дефекти (D1..D22).

Виміряна поведінка при відмовах (REPO, одноразовий запуск; числа залежать від навантаження):

| Сценарій | Виміряно |
|---|---|
| міст, SIGTERM / SIGINT (pipe і pty) | rc 0; до першого failsafe-кадру 2-6 мс; серія 3 failsafe + 5 release за ≈ 141 мс (50 Гц guard); замок вільний; після виходу кадрів немає |
| міст, закритий stdin-pipe | dead-man ≈ 227 мс при налаштуванні 200 мс; 17 release-кадрів; потім тиша; міст живий; rc 0 при SIGTERM (у тиші) |
| міст, закритий master pty | те саме (≈ 208 мс), але reader-потік падає з `OSError` (EIO), у логу **Traceback**; dead-man працює |
| міст, 2 МБ сміття без `\n` | не стає зразком, SIGTERM обробляється, rc 0 |
| міст, SIGKILL | rc −9; останній кадр = стіки `[1500,1500,1000,1500,65535…]`, release не надсилається (межа: захист лише RC_OVERRIDE_TIME на FC, INF); flock знімається ядром |
| `gs-applyconf.sh`, `custom.conf` порожній / лише коментарі / CRLF / без кінцевого `\n` | зливається без втрат; без маркера |
| `gs-applyconf.sh`, порожній/усічений `gs.conf` | exit 1 з повідомленням, до будь-яких змін, без `reboot` (D16 виправлено; до виправлення викликав `reboot`) |
| `gs-applyconf.sh`, `nmcli` завис | exit 124 (немає таймаута в скрипті) |
| `fan.sh`, температура порожня/відсутня/`99` | exit 1 на першій ітерації, ШІМ лишається 8000/40000 = 20 % (D17) |
| `fan.sh`, температура `abc` або `fan_overheat_temperature='x'` | цикл живий, але без захисту від перегріву: відсутнє порівняння дає помилку `[`; шпарність 12000 при 90° і поганому порозі |
| `fan.sh`, `fan_pwm_frequency=0` | exit 1 (ділення на нуль), цикл не доходить далі першої ітерації |
| `stream.sh`, `jq` відсутній | exit 127 до запуску програвача (`osd_widgets_osmon='yes'` типове); `jq` завис: exit 124 |
| `tx12_bridge` `map_axis`/`load_map` | див. D18, D20 |
| `power_model` NaN/від'ємні вхідні | див. D21 |

## 4. Знайдені дефекти реального коду (не виправлено)

Усі: тег **REPO** для відтворення, **INF** для оцінки наслідків. Команди з кореня репозиторію; `W` = будь-який тимчасовий каталог. Серйозність — моя оцінка (INF).
Відтворення D14..D17 запускає `tests/sim/fuzz/sandbox_driver.sh` (змінні `OVERLAY`, `SLEEP_LIMIT`, `HIDE`, `HANG`, `TMO`, `ARGS`, `REWRITE_EXTRA`; результат у `<outdir>/{exit,stdout,stderr,shim.log,root/}`).

### Конфіг-завантажувач (`config/`)

**D1. ВИПРАВЛЕНО (тест `test_FIXED_D1_*`). Shell мовчки відкидає NUL, Python відхиляє** (низька). Файл `TX12_DEADMAN_MS=3<NUL>00`:
```
printf 'TX12_DEADMAN_MS=3\x0000\n' > $W/nul.env
config/sbc-gs-config check $W/nul.env     ->  ok: $W/nul.env (1 keys)         rc 0   (значення читається як 300)
python3 config/load.py check $W/nul.env   ->  error: ...:1: control character in line   rc 2
```
Причина: `read` у bash відкидає NUL до перевірки `[[:cntrl:]]`. Пропозиція: перед розбором перевіряти `tr -d '\000' < "$file" | cmp -s - "$file"` і відхиляти файл із NUL.

**D2. ВИПРАВЛЕНО (тест `test_FIXED_D2_*`). Вердикт залежить від локалі: U+2028 у значенні** (низька). `TX12_CONN="a<U+2028>b"`:
```
LC_ALL=C.UTF-8 config/sbc-gs-config check --syntax $W/u.env   ->  error: ...:1: control character in line   rc 2
LC_ALL=C       config/sbc-gs-config check --syntax $W/u.env   ->  ok                                       rc 0
python3 config/load.py check $W/u.env                          ->  ok                                       rc 0
```
Причина: `[[:cntrl:]]` залежить від локалі (U+2028 керівний у `C.UTF-8`). Пропозиція: `local LC_ALL=C` на початку `sbc_cfg_parse` і (за потреби) у Python відхиляти ті самі Unicode-керівні символи.

**D3. ВИПРАВЛЕНО (тест `test_FIXED_D3_*`). Shell-перевірка enum приймає «склеєні» варіанти** (низька). Реєстр `enum:x|y`, значення `x|y`:
```
SBC_GS_KE='a|b' SBC_CFG_REGISTRY=$W/reg.tsv config/sbc-gs-config show  ->  KE=a|b  # env:SBC_GS_KE      rc 0
SBC_GS_KE='a|b' SBC_CFG_REGISTRY=$W/reg.tsv python3 config/load.py show ->  error: KE='a|b' must be one of a|b   rc 2
```
(`reg.tsv`: `KE<TAB>a<TAB>enum:a|b<TAB>-<TAB>-<TAB>u<TAB>n<TAB>o<TAB>-<TAB>d`.) Причина: `case "|a|b|" in *"|$v|"*`. Пропозиція: порівнювати з кожним варіантом окремо (`IFS='|' read -ra alts`, цикл з `==`).

### `gs/mavlink/gs-mavlink.sh` (власні валідатори діють через `--no-value-check`; суворіші типи реєстру обходяться)

**D4. ВИПРАВЛЕНО (тест `test_FIXED_D4_*`). IPv4 з крапкою в кінці приймається** (низька): `SBC_GS_LISTEN_ADDR=1.2.3.4. gs/mavlink/gs-mavlink.sh --print | tail -1` -> `mavp2p udps:0.0.0.0:14550 udps:1.2.3.4.:14560 --hb-systemid=125` (rc 0). Причина: `set -- $2` з `IFS=.` не дає порожнього останнього поля. Пропозиція: regex `^[0-9]{1,3}(\.[0-9]{1,3}){3}$` як у `load.sh`.

**D5. ВИПРАВЛЕНО (тест `test_FIXED_D5_*`). Дубль порту з ведучим нулем не виявляється** (низька): `SBC_GS_GCS_UDP_PORTS="4560 04560"` -> `mavp2p udps:0.0.0.0:14550 udps:0.0.0.0:4560 udps:0.0.0.0:04560 --hb-systemid=125` (rc 0). Перевірка унікальності порівнює рядки. Пропозиція: нормалізувати `p=$((10#$p))` перед порівнянням або відхиляти ведучі нулі.

**D6. ВИПРАВЛЕНО (тест `test_FIXED_D6_*`). `..` у `SERIAL_DEV` і `DUMP_PATH` приймається** (середня для `DUMP_PATH`: служба від root, `/config` доступний анонімно за `docs/CONFIG.md` §3):
```
SBC_GS_SERIAL_DEV=/dev/../tmp/x SBC_GS_DUMP_ENABLE=1 SBC_GS_DUMP_PATH=/var/log/../../etc/x gs/mavlink/gs-mavlink.sh --print | tail -1
-> mavp2p serial:/dev/../tmp/x:115200 udps:0.0.0.0:14550 udps:0.0.0.0:14560 --hb-systemid=125 --dump --dump-path=/var/log/../../etc/x
```
Реєстровий тип `path` це відхиляє (`L.check_value(ROWS["DUMP_PATH"], "/var/log/../../etc/x")` -> `ConfigError`), але скрипт дає перевазі власним валідаторам. Пропозиція: відхиляти `*/../*` і `*/..` у `case`, або перевіряти значення реєстровим типом.

### Профіль плати (`gs/boards`, `gs/lib`)

**D7. ВИПРАВЛЕНО (тест `test_FIXED_D7_*`). `validate.sh` схвалює `#`, приклеєний до значення; shell і Python читають по-різному** (низька):
```
cp -r gs/boards/radxa-zero3 $W/b; printf "ZZ=abc#c\nZQ='q'#c\n" >> $W/b/board.conf
bash gs/boards/validate.sh $W/b   ->  ok b
bash -c '. $W/b/board.conf; echo "$ZZ $ZQ"'                -> abc#c q#c     (shell: # не коментар усередині слова)
python3 -c '...board_conf.parse(...)'                      -> abc q         (Python: коментар)
```
Пропозиція: у `validate.sh` і `board_conf.py` вимагати пробіл перед `#`.

**D8. ВИПРАВЛЕНО (тест `test_FIXED_D8_*`). `validate.sh` схвалює `"…\"`, а `source` ламається** (середня: профіль, який «пройшов валідацію», з'їдає наступні ключі):
```
printf 'ZZ="abc\\"\nZEND=1\n' >> $W/b/board.conf
bash gs/boards/validate.sh $W/b   ->  ok b
bash -c '. $W/b/board.conf; echo "${ZEND-unset}"'  ->  board.conf: line 69: unexpected EOF while looking for matching `"'   (ZEND=unset)
board_conf.parse(...)["ZEND"]                         ->  1
```
Пропозиція: виключити `\` із класу `[^"$`]` у regex `validate.sh` і `board_conf.py`.

**D9. ВИПРАВЛЕНО (тест `test_FIXED_D9_*`). `BOARD` не валідується: `../`-шлях підключає чужий `board.conf` і виконує його** (низька: `BOARD` — змінна середовища):
```
BOARD=../../../../../tmp/.../evil bash -c '. gs/lib/board.sh && board_get BOARD_ID'   ->  evil   (і створено маркер PWNED9)
```
Пропозиція: у `_board_load` вимагати `[[ $BOARD =~ ^[a-z0-9][a-z0-9-]*$ ]]`.

**D10. ВИПРАВЛЕНО (тест `test_FIXED_D10_*`). Ключ `board_get` обчислюється як арифметичний індекс** (низька: ключі літеральні, але `board_get` читає будь-яку змінну, зокрема `PATH`):
```
bash -c '. gs/lib/board.sh; board_get "a[\$(touch $1)]"' _ $W/PWNED10   ->  board.sh: key 'a[$(touch $W/PWNED10)]' is not defined ...  (файл створено)
```
Пропозиція: перед `${!key}` перевіряти `[[ $key =~ ^[A-Z][A-Z0-9_]*$ ]]`.

**D11. ВИПРАВЛЕНО (тест `test_FIXED_D11_*`). Значення в `render-udev.sh` не екрануються для синтаксису udev** (низька: `board.conf` довірений, INF): `WIFI_ONBOARD_IFACE='x", RUN+="/tmp/evil'` -> `rc=0`, у `99-GS.rules`: `... ENV{ID_NET_NAME}!="x", RUN+="/tmp/evil", RUN+="/gs/wfb.sh $name"`. Пропозиція: перевіряти значення за `^[A-Za-z0-9_.:-]+$`.

### `build/lib/fetch.sh`

**D12. ВИПРАВЛЕНО (тест `test_FIXED_D12_*`). Багаторядковий «pin» проходить формат-перевірку** (низька: pin береться з `versions.env`). `grep -E '^[0-9a-f]{64}$'` працює по рядках:
```
GS_FETCH_CMD=cp bash -c '. build/lib/fetch.sh; fetch_file src out "not-a-hash
<sha256 файла>"'   ->  fetch: ok out sha256=...   rc=0   (очікувано rc 2)
git_pin <repo> <dest> $'zzz\n'<40 hex>    ->  rc 1 після спроби git fetch (очікувано rc 2 до будь-якої дії)
```
Пропозиція: `[[ $want =~ ^[0-9a-f]{64}$ ]]` (весь рядок) замість `grep`; те саме для `ref` і `name`.

**D13. ВИПРАВЛЕНО (тест `test_FIXED_D13_*`). SIGTERM під час завантаження лишає `$dest.part.<pid>`** (низька):
```
GS_FETCH_CMD=slow.sh (пише 5 байт і спить)  setsid bash -c '. build/lib/fetch.sh; fetch_file src d13 <sha>' ; kill -TERM -- -<pgid>
-> залишився $W/d13.part.20849, rc 143
```
Пропозиція: `trap 'rm -f "$tmp"' EXIT INT TERM` у `fetch_file` (із відновленням попереднього trap, бо файл source-ується) або завантаження в `mktemp -d`.

### `gs/gs-applyconf.sh`, `gs/fan.sh`

**D14. ВИПРАВЛЕНО (2026-10-03; тест `test_FIXED_D14_*`). Значення з `/config/custom.conf` виконувалися як shell від root** (ВИСОКА, INF: `/config` анонімно записуваний за `docs/CONFIG.md` §3; це той самий клас, що й виправлена в `gs-mavlink` знахідка S1). Два незалежні вектори; обидва виміряні:
```
mkdir -p $O/config; printf 'wifi_mode=$(touch PWNED)\n' > $O/config/custom.conf
OVERLAY=$O bash tests/sim/fuzz/sandbox_driver.sh "$PWD" gs/gs-applyconf.sh $W/out
ls $W/out/root/PWNED      ->  існує      (рядок потрапив у gs.conf: `wifi_mode=$(touch PWNED)`, далі `source /etc/gs.conf`)
# вектор 2 (без `$`): custom.conf = `wifi_mode=;touch PWNED;x/e;#`  -> sed 's/^wifi_mode=.*/wifi_mode=;touch PWNED;x/e;#/' : прапорець `e` GNU sed виконує рядок; PWNED існує
```
Пропозиція: розбирати `custom.conf` як дані (білий список `^[a-z_]+='[^'$`\\]*'$`, як `config/load.sh`), записувати значення без `sed` (awk з `-v`, або `printf` у новий файл і `mv`), ніколи не `source`-ити неперевірене; захищати від `/`, `&`, `\`.

**D15. ВИПРАВЛЕНО разом з D14 (тест `test_FIXED_D15_*`). `/` у значенні `custom.conf` обривало злиття, файл не перейменовувався, збій повторювався щоразу** (середня):
```
custom.conf = "wifi_mode=a/b\nwifi_ssid=zz"  ->  exit=1; config/ містить лише custom.conf (custom-merged.conf немає); у gs.conf zz відсутнє
stderr: sed: -e expression #1, char 29: unknown option to `s'
```
Пропозиція: екранувати розділювач (або `awk`), опрацьовувати ключі незалежно, при помилці переносити файл у `custom-rejected.conf` із повідомленням.

**D16. ВИПРАВЛЕНО (тест `test_FIXED_D16_*`). Порожній/усічений `gs.conf` не виявляється, `gs-applyconf.sh` просить перезавантаження** (середня: усічення при втраті живлення під час запису → `reboot` при кожному запуску, INF):
```
OVERLAY (etc/gs.conf порожній) ... gs-applyconf.sh  ->  exit=1; stdout: `[info]: Update rec_dir in fstab and need reboot`; shim.log: `reboot`
```
Пропозиція: після `source` перевіряти обов'язкові змінні (`rec_dir`, `wifi_mode`, …) і виходити з помилкою до будь-яких змін; записувати `gs.conf` атомарно (`mktemp` + `mv`).

**D17. ВИПРАВЛЕНО (тест `test_FIXED_D17_*`). `fan.sh` завершувався, якщо температура не читається, і лишав вентилятор на 20 %** (ВИСОКА для надійності, INF: нема перезапуску й відновлення; поріг перегріву більше не контролюється):
```
(sys/class/thermal/thermal_zone0/temp порожній, відсутній або `99`)  SLEEP_LIMIT=3 ... gs/fan.sh
-> exit=1; <ROOT>/script.sh: line 29: -3: substring expression < 0; duty_cycle=8000 period=40000
```
Причина: `temp_max=${temp_cpu:0:-3}` у нефатальному для циклу, але фатальному для shell виразі. Пропозиція: `temp_cpu=$(cat ... 2>/dev/null)`, перевіряти `[[ $temp_cpu =~ ^[0-9]+$ ]]`, інакше fail-safe (100 % шпарності) і `continue`; додати `Restart=` для сервісу вентилятора.

### Міст (`bench/tx12_bridge.py`), моделі

**D18. ВИПРАВЛЕНО (тест `test_FIXED_D18_*`, див. «Виправлення D18–D22»). `load_map` падає з `AttributeError`, а не `ValueError`, якщо значення осі не об'єкт** (низька): `{"axes": {"ABS_X": 5}}` -> `AttributeError: 'int' object has no attribute 'get'`; `main` ловить лише `(OSError, ValueError)`, тож замість `rc 2` отримуємо traceback. Пропозиція: `if not isinstance(cfg, dict): raise ValueError(...)`.

**D19. ВИПРАВЛЕНО (тест `test_FIXED_D19_*`). Обрізаний рядок stdin є дійсним зразком, і мале число піднімається до 1000** (середня для бенч-входу `--input stdin`, INF): `sanitize([1500.0, 15.0])` -> `[1500, 1000, 65535, 65535, 65535, 65535, 65535, 65535]`. Рядок `1500 1500 1000 1500`, обрізаний після `1500 15`, дає канал 2 = 1000 (крайнє відхилення). Причина: `TX12_SANE_MIN_US` типово 0, вікно 0..4000, значення нижче `LO` піднімаються до `LO`. Пропозиція: вимагати очікувану кількість полів у рядку і/або відкидати, а не піднімати значення нижче `LO`; або підняти типовий `TX12_SANE_MIN_US` (межа SAFETY до 1000).

**D20. ВИПРАВЛЕНО (тест `test_FIXED_D20_*`). `map_axis` ділить на нуль при `center == max` і стіку в максимумі** (низька): `map_axis(100, {"min":0,"max":100,"center":100})` -> `ZeroDivisionError: float division by zero`; помилка виникає в основному циклі (`finally` ще встигає надіслати release). `load_map` не перевіряє `center`/`deadband`. Пропозиція: у `load_map` вимагати `min < center < max` і `0 <= deadband < 1`.

**D21. ВИПРАВЛЕНО (тест `test_FIXED_D21_*`). `power_model.budget` не відхиляє NaN-PSU і від'ємне число адаптерів** (низька, SYNTH): `budget(P,"pi4",psu_a=nan,adapters=1,state="tx",with_=("fc",))` -> `verdict OK`, `flags []`, `psu_margin_a nan`; `adapters=-3` -> `total_a −2.1 A`, `verdict OK`. Порівняння з NaN хибні, тож жоден прапорець не піднімається. Пропозиція: `ParamError` для нескінченних/NaN/неположних `psu_a` і від'ємних `adapters`; `type=` у argparse.

**D22. ВИПРАВЛЕНО (тест `test_FIXED_D22_*`). Оголошені діапазони `params.json` дозволяють струм idle > rx і rx > tx** (низька, SYNTH): `power.devices.rtl8812_idle_a` 0.15..0.5, `_rx_a` 0.25..0.7, `_tx_a` 0.5..1.6 перекриваються; при допустимих значеннях idle=0.5, rx=0.25 бюджет Pi 5: `idle total 1.30 A > rx total 1.05 A`. Вибірка рушія (`base_sampled` для `tx_a`, `rx_a`) теж не накладає порядок. Пропозиція: обмеження порядку в `common.validate` і в `priors.Space`, або непересічні діапазони.

## 5. Мутації (на КОПІЇ, `tests/sim/fuzz/mutate.sh`, ніколи in-place)

Таблиця в `mutations.py`: точна заміна тексту в одному файлі копії; мутація «вбита», якщо вказані класи тестів падають. Перед запуском перевіряється, що незмінена копія зелена.

| # | Мутація | Убито тестом |
|---|---|---|
| M1 | shell-парсер приймає `$` у подвійних лапках | `TestConfigDifferential` (розбіжність із Python) |
| M2 | Python-парсер приймає `;` у bare-значенні | `TestConfigDifferential` |
| M3 | межу `TX12_DEADMAN_MS` розширено 1000 → 100000 | незалежна таблиця `docs/CONFIG.md` §4 |
| M4 | Python більше не тримає SAFETY-межі | `test_resolve_safety_bounds_both_loaders` |
| M5 | shell-валідатор float приймає `5.` | `test_check_values_diff` |
| M6 | `sbc_cfg_load` виконує значення (`eval`) | маркер у `TestConfigInjection` |
| M7 | gs-mavlink приймає порт 0 | `test_explicit_boundary_regressions` |
| M8 | `HB_SYSID` до 255 (sysid GCS) | `TestGsMavlink` |
| M9 | дублікати портів не відхиляються | `TestGsMavlink` |
| M10 | міст без серії failsafe+release при виході (trap/finally) | `TestBridgeFaults` (SIGTERM/SIGINT) |
| M11 | міст без обробника SIGTERM/SIGINT | `TestBridgeFaults` |
| M12 | dead-man ніколи не спрацьовує | `TestBridgeFaults` (закритий pipe/pty) |
| M13 | абсурдні зразки обрізаються, а не відкидаються | `TestBridgeInputValidation` |
| M14 | `clamp_us` не обмежує | `TestBridgeInputValidation` |
| M15 | порівняння sha256 обійдено | `TestFetch` |
| M16 | часткове завантаження не видаляється | `TestFetch` |
| M17 | `board_conf.py` обрізає пробіли в лапках | `TestBoard` |
| M18 | `render-udev.sh` не відхиляє незакритий `@KEY@` | `TestUdevRender` |
| M19 | `rf_model`: знак path loss перевернуто | `TestModelProperties` |
| M20 | `power_model`: знак падіння на кабелі | `TestModelProperties` |
| M21 | `degrade_model`: прибрано стелю EVM | `TestModelProperties` |
| M22 | `priors.Rng` ігнорує seed | `TestModelProperties` |
| M23 | «виправлення» D14 застосовано (злиття `custom.conf` знешкоджене) | закріплення D14 падає з «no longer reproduces» (перевірка самого механізму закріплень) |

Підсумок: **23 убито, 0 вцілілих** (REPO, `mutate.sh`, 1 хв).
Корисний урок: перша версія генератора пропускала M1/M2 (спільний вердикт файлу з кількох рядків майже завжди «помилка», тому дифференціал рідко бачив валідний рядок із ворожим символом) і M17; тому генератор тепер змішує односимвольні вставки у валідні рядки, ворожі токени й пробіли всередині лапок, а таблиця безпеки реєстру має незалежну копію (інакше розширення межі в `registry.tsv` було б «самоузгодженим»).

## 6. Що fuzz не доводить

- **Не доводить відсутності дефектів**: скінченна вибірка з seed; відсутність розбіжності = «не знайдено», а не «немає». Різні seed (10 перевірено) нових розбіжностей не дали, але покриття не виміряно.
- **Differential лише порівнює дві реалізації**: якщо shell і Python помиляються однаково (наприклад, обидва приймають небезпечне значення), розбіжності не буде. Оракул `gs-mavlink` — моя незалежна переформульовка правил зі скрипта й документації; він може ділити з ними хибне розуміння. Тому додано 23 явні межові випадки.
- **Пісочниця не є цільовою системою**: `tests/lib/sandbox.sh` переписує шляхи й підміняє `nmcli`, `systemctl`, `jq` (логувальні шими) тощо; реальні `NetworkManager`, `chroot`, `u-boot-update`, `mount` не запускались. `fan.sh` перевірено на фіктивному sysfs, не на реальному PWM.
- **Міст перевірено лише з `--input stdin` і UDP loopback** (pipe/pty замість `/dev/input/event*`; python-evdev не задіяно, EdgeTX/TX12, реального FC і RC-ефіру немає). `UNVERIFIED`: поведінка при зникненні справжнього evdev-пристрою (`EvdevSource`: `OSError` у потоці), реальні затримки Wi-Fi. Часові пороги (dead-man 150..800 мс) широкі через навантаження; точні межі часу на залізі не перевірено. SIGKILL і втрата живлення release не надсилають: це межа дизайну, не тест.
- **Моделі (SYNTH)**: перевіряється внутрішня узгодженість (монотонність, одиниці, скінченність, відтворюваність), а не відповідність фізиці; жоден параметр `UNMEASURED` не став виміряним. Властивості перевірені на значеннях усередині декларованих `min`/`max`; поведінка за межами не гарантується.
- **Версії**: GNU bash 5.2, sed 4.9, grep 3.11, локалі лише `C` і `C.UTF-8`; BusyBox/dash/інші локалі не перевірялись. Ураховано лише root і `nobody` у цьому контейнері, не CI GitHub.
- **Не покрито**: `gs/gsmenu.sh` (маніпуляція JSON через `jq`, `iw`, `drm_info`), `gs/button.sh`, `gs/otg-gadget.sh`, `gs/wfb.sh`, `gs/channel-scan.sh`, `bench/gs_mav.py`, `bench/fake_fc.py`, `build/build.sh` (повна збірка), `oled.py`. Закріплені дефекти фіксують поведінку «як є» і не доводять, що інших немає.
- Тести з паралельними підпроцесами й коротким сном (мост, `HANG`) чутливі до сильного навантаження хоста: запуск при load average ≈ 10 давав час до 15 с, а перша версія процесних тестів хибно падала (виправлено очікуванням ACTIVE і ширшими порогами); зафіксовано в §3.

## 7. Файли

| Файл | Призначення |
|---|---|
| `tests/sim/fuzz/run.sh` | запуск `--check` / `--long` / `--strict-defects`, шарди, підсумок, перелік закріплених дефектів |
| `tests/sim/fuzz/test_fuzz.py` | 77 тестів (unittest), 14 класів; `FUZZ_REPORT` пише JSON |
| `tests/sim/fuzz/fuzzlib.py` | seed, масштаб, `run`, `pinned()` (закріплення дефектів), `measure()`, прибирання тимчасових каталогів |
| `tests/sim/fuzz/cfg_harness.sh` | пакетна shell-сторона differential: розбір, перевірка типу, `resolve` (справжній `config/load.sh` через `source`) |
| `tests/sim/fuzz/sandbox_driver.sh` | один скрипт у пісочниці з `OVERLAY`, `HIDE`, `HANG`, `TMO`, `SLEEP_LIMIT` |
| `tests/sim/fuzz/fc_sniff.py` | FC-заглушка на UDP для тестів моста (один процес на всі сценарії) |
| `tests/sim/fuzz/mutate.sh`, `mutations.py` | 23 мутації на копії |
| `docs/SIM-FUZZ.md` | цей файл |

Як додати перевірку: новий випадковий тест — використовувати `F.rng("назва")` і `F.n(база)`; нове закріплення дефекту — `F.pinned(self, "Dn", спостерігається_дефект, деталі)` і запис у §4; нова мутація — рядок у `MUTS` з єдиним входженням шаблону.


## Виправлення 2026-10-03 (D14, D15, D17)

- `gs/gs-applyconf.sh`: злиття `custom.conf` через `gs_conf_merge_line`: ключ лише `^[A-Za-z_][A-Za-z0-9_]*$`; значення без лапок записується як є лише з безпечних символів (`A-Za-z0-9_.,:/@%+=-`), інакше в одинарних лапках (значення з `'` відхиляється); заміна через `awk` з `ENVIRON`, не `sed s///`; файл у ту ж теку й `mv`; відхилені рядки друкуються в stderr, файл усе одно споживається (`custom-merged.conf`). Змінюються лише наявні ключі (як і раніше). Усі наявні golden лишилися без змін.
- `gs/fan.sh`: температура не з `^[0-9]{4,}$` (порожня, відсутня, `99`, `abc`, від'ємна) дає 100 % шпарності, повідомлення і наступну ітерацію, без завершення. Нормальні значення дають той самий вивід.
- Тести: D14, D15, D17 з «закріплень дефектів» стали постійними регресійними (`test_FIXED_*`), плюс `test_hostile_values_are_stored_inert_and_bad_lines_rejected`. Нові мутації M23-M25 (старий `sed`, без охорони температури, без лапок) убиті.
- Не змінено: D16 (порожній `gs.conf` -> `reboot`) та решта D1..D13, D18..D22.
- Не перевірено на залізі (HW): поведінка на реальному `/etc/gs.conf` як симлінку на RO-root і наявність `awk`/`chmod --reference` в образі (BusyBox-варіант `awk` не перевірявся).

## Виправлення D18–D22

- D18 (`bench/tx12_bridge.py`, `load_map`): документ не об'єкт, елемент осі не об'єкт, нечислові/нескінченні `min`/`max`/`center`/`deadband`, `max <= min`, `center` поза `min..max`, `deadband` поза `[0,1)` дають `ValueError`; `main` перетворює його на `rc 2` і повідомлення `bad mapping`. Приклад `tx12_map.example.json` проходить без змін.
- D19 (`parse_stdin_line`, `StdinSource`): рядок stdin коротший за 8 полів, у якому є значення нижче `LO` (крім 65535), відкидається (лише лог), а не піднімається до `LO`; неповний останній рядок без `\n` (EOF посеред запису) не береться. INF: це евристика, а не точний контроль довжини, бо короткі рядки `ch1..chN` задокументовані й їх використовують тести (`1100 1900`). Повний рядок із 8 полів і надалі обрізає `500 -> 1000` (поведінка не змінена); обрізаний 8-польний рядок, що завершився на межі поля, відрізнити неможливо. Коректні дані поводяться як раніше.
- D20 (`map_axis`): коли сторона осі має нульову довжину (`center == max` чи `center == min`, стік на межі), `n = 0` (нейтраль), не `ZeroDivisionError`. Інша сторона мапиться як раніше. `clamp`, dead-man, single writer, sysid не змінювалися; вивід не став менш обмеженим.
- D21 (`power_model.budget`): `psu_a` не скінченне число `> 0` (NaN, inf, 0, від'ємне, bool, рядок) і `adapters` не ціле `>= 0` дають `ParamError`; CLI повертає `rc 2` і `power_model: error: ...`. `psu_a=None` (рекомендований БЖ) і `adapters=0` працюють як раніше.
- D22 (`params.json`): діапазони струму RTL8812 більше не перетинаються: `idle` 0.15..0.3 (було ..0.5), `rx` 0.3..0.5 (було 0.25..0.7), `tx` 0.5..1.6 (без змін); типові значення 0.3/0.45/0.9 і теги UNMEASURED лишилися. Це звуження INF-діапазонів, а не вимір; діапазони `rx` і `idle` не вигадані з нових даних, а лише зведені до спільних меж. Наслідок: вибірки рушія (priors/scenario) змінилися, тому оновлено golden `scenario_nominal_pi5_5a_150m`, `scenario_hot_day_closed_case`, `sensitivity_nominal_pi5_5a_150m` (зсуви в 3-4 значущій цифрі; правила не змінено).
- Тести: D18-D22 з закріплень стали `test_FIXED_D18..D22_*`; мутації M26-M33 (кожна на копії) убиті; нові перевірки в `bench/tx12-bridge-test.sh` (D18 rc 2, D19 обрізаний рядок, D20 край центру).
- Не перевірено на залізі (HW): справжній TX12/EdgeTX evdev (діапазони осей і центр лишаються placeholder UNVERIFIED); реальні струми RTL8812 (діапазони SYNTH/UNMEASURED).

## Виправлення D1–D13, D16

Теги: REPO (перевірено командами цього репозиторію). Усі 14 дефектів виправлено мінімально, за пропозиціями §4; поведінка для коректних вхідних даних не змінилась (наявні golden без змін).

- D1 (`config/load.sh`, `config/load.py`): `sbc_cfg_parse` відхиляє файл, у якому є байт NUL (`tr -d '\000' | cmp`); Python відхиляє NUL і в рядках-коментарях, тож вердикт однаковий.
- D2 (`config/load.sh`): `local LC_ALL=C` у `sbc_cfg_parse`: `[[:cntrl:]]` і `[[:space:]]` не залежать від локалі; U+2028 приймається в обох завантажувачах (як у Python).
- D3 (`config/load.sh`): enum порівнюється з кожним варіантом окремо (`IFS='|' read -ra`), «склеєне» `a|b` відхиляється, як у Python.
- D4, D5, D6 (`gs/mavlink/gs-mavlink.sh`): IPv4 лише повним збігом `^[0-9]{1,3}(\.[0-9]{1,3}){3}$` (+ кожен октет <= 255); унікальність портів за числовим значенням (`10#`); `..` як компонент шляху в `SERIAL_DEV` і `DUMP_PATH` відхиляється (`..` усередині імені, `a..b`, лишається дозволеним). Повідомлення для раніше відхилюваних значень не змінились.
- D7, D8 (`gs/boards/validate.sh`, `gs/lib/board_conf.py`): коментар `#` вимагає пропуск перед собою; у `"..."` заборонено `\`. Обидва читачі мають однакові правила; профілі radxa-zero3 і rpi4 проходять.
- D9, D10 (`gs/lib/board.sh`): `BOARD` має відповідати `^[a-z0-9][a-z0-9-]*$` (інакше `invalid board id`, `return 1`); ключ `board_get` має відповідати `^[A-Z][A-Z0-9_]*$` (інакше `invalid key`, `return 1`) до `${!key}`. `hw.sh`/`gpio.sh`/`otg.sh` викликають `board_get` лише з літеральними ключами й мають fallback, тож їхня поведінка не змінилась.
- D11 (`gs/boards/render-udev.sh`): значення ключів шаблону мають відповідати `^[A-Za-z0-9_.:-]+$`, інакше exit 1 (замість екранування). Значення обох профілів (`wifi0`, `aicwf_sdio`, `radxa0`, `brcmfmac`, `rpi0`) проходять, вихід radxa-zero3 побайтово той самий.
- D12 (`build/lib/fetch.sh`): формат sha256, 40-hex ref і імені `pin_from_manifest` перевіряється `[[ =~ ^...$ ]]` по всьому рядку, не `grep -E` по рядках; багаторядковий pin дає rc 2 до будь-якої дії.
- D13 (`build/lib/fetch.sh`): `fetch_file` на час виклику ставить обробники INT/TERM (EXIT лише якщо в користувача нема свого), які видаляють `$dest.part.<pid>`, відновлюють trap-и викликача й повторно надсилають сигнал (виклик так само помирає від сигналу). Реалізація розбита на `fetch_file` (trap) і `_gs_fetch_file_impl` (колишнє тіло). Межа: якщо сигнал надіслано лише bash-процесу, а не групі, обробник чекає завершення дочірнього `curl`.
- D16 (`gs/gs-applyconf.sh`): на початку (до злиття `custom.conf`, до будь-яких змін) у підоболонці `source /etc/gs.conf` і перевірка, що `wifi_mode`, `rec_dir`, `gps_uart`, `gps_uart_baudrate` непорожні (ключі з різних частин файлу, тож обрив будь-де помітний); інакше exit 1 і повідомлення `not applying any change`, `custom.conf` лишається для наступного запуску. Атомарний запис `gs.conf` (друга половина пропозиції) не робився: `gs.conf` пишуть й інші скрипти (`gs-init.sh`, `gsmenu`), це окрема зміна.
- Тести: `test_DEFECT_D1..D13, D16` стали постійними `test_FIXED_*` (D13 тепер три тести: SIGTERM/SIGINT, caller з власним EXIT trap, відновлення trap-ів). Оракул `gs-mavlink` і генератори розширені (числові порти з нулями, `1.2.3.4.`, `..`, `#` приклеєний, `\` у `"..."`, `a|b` в enum). Нові мутації M40-M55 (кожна відновлює один дефект) усі убиті.
- Не перевірено на залізі (HW): `gs-applyconf.sh` на реальному `/etc/gs.conf` (симлінк на RO-root, `gs-init.sh` первинне створення) і порядок записів у образі; `fetch.sh` з реальним `curl` (лише `cp`-шим і `sleep`-шим); `board.sh` на Pi 5 (профілю `rpi5` ще немає, лише `rpi4`).
