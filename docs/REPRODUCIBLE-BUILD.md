# Відтворювана збірка (M5, GAPS C3/C4)

Стан: **M5a** — інвентаризація, храповик і маніфест; поведінку `build/build.sh` НЕ змінено. Номери рядків — за `build/build.sh` на момент
написання (**REPO**, перевірено читанням); вони зсуваються при правках, тож джерело істини — `tests/golden/static/pins.out`.

## 1. План
1. **Храповик (зроблено):** `tests/static/pins.sh` рахує знахідки по категоріях у `build/*.sh` і падає, якщо будь-яка категорія
   зросла відносно бази в `tests/golden/static/pins.out` (секція `== counts`). Мережа не потрібна. Зменшення проходить, але міняє вивід:
   зафіксувати `tests/run.sh --update static/pins`. Також входить у `tests/run.sh` (`static/pins`).
2. **Маніфест (зроблено, не підключений):** `build/versions.env` — `NAME_REPO`/`NAME_URL`, `NAME_CUR` (що тягне збірка зараз),
   `NAME_PIN` (перевірений SHA коміту або sha256 файлу; порожньо = UNVERIFIED).
3. **Хелпери (далі):** `build/lib/fetch.sh` (source-ується з `build.sh`): `fetch_file URL SHA256 DEST` (завантажити, `sha256sum -c`,
   інакше вихід з помилкою; без `latest`) і `git_pin NAME` (`git init` + `git fetch --depth=1 <repo> <SHA>` + `checkout FETCH_HEAD`;
   для `--recursive` — окремо закріпити сабмодулі). `build.sh` читає `. build/versions.env`; порожній `*_PIN` у режимі
   `STRICT=1` (CI) = помилка, у звичайному — попередження й стара поведінка.
4. **Розкатка малими PR (по одному на пункт, після кожного `tests/run.sh --update static/pins` зі зменшеними числами):**
   (a) хелпери + підключення маніфесту без зміни джерел; (b) git-клони DKMS-драйверів; (c) wfb-ng, PixelPilot_rk, wfb-ng-osd, yaml-cli,
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
