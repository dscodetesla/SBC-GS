# Відтворювана збірка (M5, GAPS C3/C4)

Стан: **M5a** (інвентаризація, храповик, маніфест) + **M5 крок 1** (хелпери `build/lib/fetch.sh` з офлайн-тестами `static/fetch`); поведінку `build/build.sh` НЕ змінено, хелпери ним ще не підключені. Номери рядків — за `build/build.sh` на момент
написання (**REPO**, перевірено читанням); вони зсуваються при правках, тож джерело істини — `tests/golden/static/pins.out`.

## 1. План
1. **Храповик (зроблено):** `tests/static/pins.sh` рахує знахідки по категоріях у `build/*.sh` і падає, якщо будь-яка категорія
   зросла відносно бази в `tests/golden/static/pins.out` (секція `== counts`). Мережа не потрібна. Зменшення проходить, але міняє вивід:
   зафіксувати `tests/run.sh --update static/pins`. Також входить у `tests/run.sh` (`static/pins`).
2. **Маніфест (зроблено, не підключений):** `build/versions.env` — `NAME_REPO`/`NAME_URL`, `NAME_CUR` (що тягне збірка зараз),
   `NAME_PIN` (перевірений SHA коміту або sha256 файлу; порожньо = UNVERIFIED).
3. **Хелпери (зроблено, не підключені до `build.sh`):** `build/lib/fetch.sh` (source-ується; лише функції, опцій оболонки не змінює,
   сумісний із `set -e`/`set -x`). Деталі й приклади: розділ 5. Підключення до `build.sh` (читання `. build/versions.env`, `STRICT=1`
   для порожнього `*_PIN` у CI, попередження й стара поведінка в звичайному режимі) — наступний крок (a). Для `--recursive`
   сабмодулі треба закріпити окремо (хелпер їх не обробляє).
4. **Розкатка малими PR (по одному на пункт, після кожного `tests/run.sh --update static/pins` зі зменшеними числами):**
   (a) підключення хелперів і маніфесту без зміни джерел (самі хелпери вже є); (b) git-клони DKMS-драйверів; (c) wfb-ng, PixelPilot_rk, wfb-ng-osd, yaml-cli,
   ядро Radxa; (d) бінарні `latest` (msposd, snander, yq), alink (прибрати API-пошук тегу), шрифти msposd; (e) ttyd sha256;
   (f) `overlayroot` по https (або `snapshot.debian.org`) + sha256; (g) `pip` з `==`/`--require-hashes`; (h) `bench/` і `gs/install.sh` (див. п. 4).
5. Кожен пін ставити лише після прямої перевірки (`raw.githubusercontent.com`/API через проксі). Кожен PR на збірку потребує зібрати образ на залізі/CI
   і порівняти список пакетів/хеші бінарників з попереднім — це **не** перевірялось (INF).

## 2. Інвентаризація `build/*.sh` (REPO; evidence = прочитаний рядок)
| Рядок | Що | Категорія | Примітка |
|---|---|---|---|
| build.sh:48 | `svpcom/rtl8812au -b v5.2.20` | (тег, не знахідка) | тег змінний; SHA UNVERIFIED |
| build.sh:58, 66, 78, 89, 99 | клони rtl88x2bu, rtl88x2cu, rtl88x2eu (GAPS: «8812eu»), rtl8733bu, rtw88 | git-unpinned | HEAD гілки за замовчуванням; DKMS збирається від root |
| build.sh:113 | `radxa/kernel -b linux-5.10-gen-rkr4.1` | git-unpinned | гілка (рухома) |
| build.sh:153 | `svpcom/wfb-ng -b master` | git-unpinned | гілка (рухома) |
| build.sh:165 | `OpenIPC/PixelPilot_rk` (+ сабмодулі :172) | git-unpinned | сабмодулі теж рухомі |
| build.sh:194 | `svpcom/wfb-ng-osd --recursive` | git-unpinned | |
| build.sh:203 | `RubyFPV --branch $rubyfpv_version` (11.1) | (тег, не знахідка) | змінний тег |
| build.sh:260 | `OpenIPC/yaml-cli` | git-unpinned | |
| build.sh:184 | msposd `releases/download/latest/` | dl-latest, dl-nosum | |
| build.sh:186-191 | 6 шрифтів msposd з `/main/` | dl-latest, dl-nosum | по 1 знахідці на рядок |
| build.sh:218 | тег alink з `api.github.com/.../tags` (`.[0]`) | api-lookup | перший тег ≠ обов'язково найновіший семвер (INF) |
| build.sh:219 | alink_gs за тегом з :218 | dl-nosum | |
| build.sh:220 | `alink_gs.conf` з `refs/heads/main` | dl-latest, dl-nosum | |
| build.sh:225 | ttyd 1.7.7 | dl-nosum | версію вже зафіксовано, sha256 немає |
| build.sh:249 | `pip install evdev dotenv` | pip-unpinned | без `==`; ім'я `dotenv` ≠ `python-dotenv` (INF, перевірити) |
| build.sh:252 | snander-mstar `latest` | dl-latest, dl-nosum | |
| build.sh:257 | yq `releases/latest/download` | dl-latest, dl-nosum | |
| build.sh:378 | `overlayroot_*.deb` з `http://ftp.cn.debian.org` | http-plain, dl-latest*, dl-nosum | *шлях містить `/main/`: хибна спрацьовка категорії dl-latest, лишена в базі |
| build.sh:27 | apt-джерело radxa по https, `signed-by` | (ок) | ключ `radxa-archive-keyring.gpg` береться з пакета; відбиток не звірявся |
| release.sh:46 | `wget "$IMAGE_URL"` базового образу | не ловиться (змінна без `https://`) | без sha256 (REPO); потребує окремого правила |

Підсумок бази (`tests/golden/static/pins.out`): git-unpinned 10, dl-latest 11, dl-nosum 13, api-lookup 1, pipe-exec 0, pip-unpinned 1, http-plain 1.

## 3. Поза `build/*.sh` (не в храповику, REPO)
- `bench/install-wfb.sh:15`: `curl … apt.wfb-ng.org/public.asc | gpg --dearmor` без звірки відбитка (GAPS C3; тут це bench, не образ).
- `bench/setup-common.sh:18`: `pip install --upgrade pip pymavlink` без версій.
- `gs/install.sh:49,51`: `pip install -r requirements.txt`; `pip install luma.oled psutil python-dotenv smbus2` без версій.
Правило `pipe-exec` у `pins.sh` тому дає 0 у `build/`; розширити на `bench/` і `gs/` — окремим PR.

## 4. Що лишається UNVERIFIED
- **Усі SHA/sha256 у `build/versions.env` порожні.** Спроба `api.github.com/repos/<repo>/commits/HEAD` через проксі сесії для всіх 12 репозиторіїв
  відповіла: «GitHub access to this repository is not enabled for this session» (політика мережі не обходилась). Тому жодного піна не встановлено.
- Чи є теги `v5.2.20`/`11.1` незмінними; чи сумісні поточні HEAD з поточним образом (на яких комітах збирався останній робочий образ — невідомо).
- Відбиток ключа Radxa/wfb-ng; наявність офіційних `*.sha256` у релізах ttyd/yq/msposd/snander/alink.
- Чи працює `overlayroot` по https на `ftp.cn.debian.org` (INF: дзеркало Debian зазвичай підтримує https, не перевірено).
- Евристика `pins.sh` текстова (рядкова): багаторядкові команди й змінні URL не ловляться (див. `release.sh:46`).

## 5. Хелпери `build/lib/fetch.sh` (REPO; перевірено офлайн, `tests/static/fetch.sh`)
Підключення: `. build/lib/fetch.sh` (працює під `set -e; set -x`; помилки повертаються через `return`, діагностика в stderr).
| Функція | Що робить | Коди |
|---|---|---|
| `fetch_file URL DEST SHA256` | завантажує в `DEST.part.$$` (curl: `--fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3`, без `-k`), перевіряє `sha256sum -c`, тоді `mv` у `DEST`; при розбіжності видаляє тимчасовий файл і `DEST`, пише очікуване/отримане | 0 ок; 1 збій завантаження/розбіжність; 2 порожній чи некоректний sha256 |
| `git_pin REPO DEST SHA` | `git init` + `fetch --depth 1 origin SHA` + `checkout FETCH_HEAD`, звіряє `git rev-parse HEAD` з SHA; дозволені лише транспорти https і file (`GIT_ALLOW_PROTOCOL`); приймає локальні шляхи та `file://` (для тестів) | 0 ок; 1 збій/невідповідність; 2 не 40-hex (гілка, тег, скорочений SHA, порожньо) |
| `pin_from_manifest NAME DEST` | бере `NAME_PIN` та `NAME_REPO` (git) або `NAME_URL` (файл) зі змінних оболонки (після `. build/versions.env`) і викликає відповідну функцію; обидва `_REPO` і `_URL` разом або жодного = код 2 | як у викликаної |

Приклади:
```bash
. build/versions.env
. build/lib/fetch.sh
pin_from_manifest WFB_NG /tmp/wfb-ng                 # git: потрібен WFB_NG_PIN = 40-hex SHA
pin_from_manifest TTYD /usr/local/bin/ttyd           # файл: потрібен TTYD_PIN = sha256
fetch_file "$YQ_URL" /usr/local/bin/yq "$YQ_PIN" || exit 1
```
Змінні оточення: `GS_ALLOW_UNPINNED=1` дозволяє порожній/нестрогий пін (гучне попередження); `GS_FETCH_CMD` підміняє примітив
завантаження (`$GS_FETCH_CMD URL DEST`; у тестах це шим `cp`, бо `--proto '=https'` відкидає `file://`).

**Як отримати пін.** Запустіть з `GS_ALLOW_UNPINNED=1`: `fetch_file` надрукує `computed sha256: ...`, `git_pin` надрукує
`resolved commit: ...`; це значення треба вставити в `NAME_PIN` у `build/versions.env`. Значення, отримане цим способом, довіряє
тому, що віддав сервер у момент запуску (trust on first use); для сильнішої гарантії звірте його з незалежним джерелом (офіційний
`*.sha256` релізу, підпис, другий канал). Файли по `http://` (напр. `OVERLAYROOT_URL`) `fetch_file` відхилить: спершу потрібен https-URL
(п. 1.4 f).

**Що лишається (UNVERIFIED).** Нічого не запускалось проти реальної мережі: лише `cp`-шим і локальний git-репозиторій. Доступ до GitHub
API/raw у цій сесії заборонений проксі, тож реальні піни не заповнено (усі `*_PIN` порожні), а поведінку справжнього `curl` (TLS,
редиректи, `--proto-redir`) на живих серверах не перевірено. `git fetch --depth 1 origin <SHA>` вимагає, щоб сервер дозволяв
запит довільного SHA (GitHub дозволяє — INF, не перевірялось тут).
