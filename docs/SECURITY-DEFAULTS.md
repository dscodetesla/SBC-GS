# Небезпечні дефолти спадкового коду (C2)

Джерело вимоги: `docs/GAPS.md` (C2), `docs/ROADMAP-EXECUTION.md` (M5). Теги достовірності: REPO = перевірено читанням коду в цьому
репозиторії; INF = висновок; UNVERIFIED = не перевірено (немає реальної збірки/заліза).
Автоматична перевірка: `tests/static/security-defaults.sh` (golden `tests/golden/static/security-defaults.out`): рахунок лишаючихся
дефолтів може лише зменшуватись, а застарілі рядки root-логіну досяжні лише під `GS_LEGACY_ROOT_LOGIN=1`.

## Таблиця

| # | Дефолт | Де (file:line) | Доказ | Статус | Прапорець |
|---|--------|----------------|-------|--------|-----------|
| 1 | Пароль `root:root` | `build/build.sh` (блок `root-login`, гілка legacy) | REPO | **виправлено** (типово не задається) | `GS_LEGACY_ROOT_LOGIN=1` повертає старе; `GS_ROOT_PASSWORD=<пароль>` задає свій |
| 2 | `PermitRootLogin yes` | `build/build.sh` (той самий блок) | REPO | **виправлено** (типово `prohibit-password`) | `GS_LEGACY_ROOT_LOGIN=1` |
| 3 | `ttyd` (shell на порту 81, `login`) від root | `build/build.sh:238` (`User=root`) | REPO | задокументовано | немає; план: `User=` не-root + `login` потребує root, тож зміна нетривіальна, не вгадувалась |
| 4 | Анонімний samba `guest ok = yes` + `force user = root` (`/Videos`, `/config`) | `gs/gs-init.sh:137,146` | REPO | задокументовано (`gs/*.sh` не змінювались: пісочниця golden) | план: `GS_SMB_GUEST=0` або `force user` не-root + автентифікація |
| 5 | Публічний `FPVue.key` копіюється як робочий ключ wfb | `gs/install.sh:30` | REPO | задокументовано | план: `GS_KEEP_PUBLIC_KEY=1` для старого; типово генерувати ключ при першому запуску |
| 6 | Пароль AP `12345678` (hotspot і ap) | `gs/gs.conf:10,118` | REPO | задокументовано | змінити в `/config/gs.conf` |
| 7 | `hotspot` як режим за замовчуванням | `gs/gs.conf:4` | REPO | задокументовано | `wifi_mode='station'` у `/config/gs.conf` |

(Номери рядків ряду 1-2 стосуються блоку між маркерами `# >>> root-login` / `# <<< root-login` у `build/build.sh`.)

## Що змінено в збірці (1-2)
- Типово: пароль root не задається, у `sshd_config` рядок `PermitRootLogin prohibit-password` (вхід root лише за ключем).
- `GS_ROOT_PASSWORD=<пароль>`: задає пароль root під час збірки (лише консоль/`login`; ssh за паролем лишається заборонений).
  Під час запису пароля вимикається `set -x`, тож пароль не потрапляє в журнал збірки (перевірено симуляцією в статичному тесті).
- `GS_LEGACY_ROOT_LOGIN=1`: старі `root:root` + `PermitRootLogin yes`; має пріоритет над `GS_ROOT_PASSWORD`.
- Змінні мають дійти до `chroot` у `build/release.sh:140`: `chroot` успадковує середовище, тож запускайте `sudo -E` або
  `sudo GS_ROOT_PASSWORD=... ./release.sh`. Не перевірено на реальній збірці (UNVERIFIED).

## Міграція для тих, хто покладався на `root:root`
1. Збирайте з `GS_LEGACY_ROOT_LOGIN=1` (поведінка як раніше), або
2. додайте свій ключ у `/root/.ssh/authorized_keys` і заходьте за ключем, або
3. задайте `GS_ROOT_PASSWORD` для консолі.
UNVERIFIED: чи є в базовому образі Radxa інший обліковий запис (наприклад `radxa`) для ssh за паролем; без нього типовий образ
не має віддаленого входу за паролем, тож перевірте це до розгортання на залізі.

## Запропонований opt-in для FPVue.key (4-5, лише дизайн)
`install.sh` не змінено. Пропозиція: типово не копіювати публічний ключ, а генерувати пару (`wfb_keygen`) при першому запуску й
показувати відбиток; `GS_KEEP_PUBLIC_KEY=1` залишає поточну поведінку. Ризик (INF): будь-хто з публічним ключем може слухати/
впроваджувати кадри wfb. Зміна `install.sh` потребує окремого golden-узгодження (`tests/`), тому відкладена.

## Оновлення 2026-10-03: S2 (передача `GS_*` у chroot) виправлено в `build/release.sh`
Незалежний аудит (`docs/AUDIT-INDEPENDENT-2026-10-02.md`) виявив, що `GS_LEGACY_ROOT_LOGIN` і `GS_ROOT_PASSWORD` не доходили до `build.sh`: `release.sh` викликав `chroot` без явної передачі, а CI запускає `sudo ./release.sh`.
- **Зроблено (REPO, `tests/static/release-env.sh`):** блок `chroot-env` у `build/release.sh` передає лише ці дві змінні, лише якщо вони задані, через середовище (не через argv); xtrace на цей час вимкнений. Тест перевіряє передачу, відсутність пароля в xtrace/stdout/argv і відновлення xtrace; три мутації (немає передачі, немає `set +x`, пароль в argv) ловляться.
- **Не зроблено (рішення власника):** `sudo` скидає середовище, тож у `.github/workflows/autobuild.yml` команду треба змінити на `sudo --preserve-env=GS_LEGACY_ROOT_LOGIN,GS_ROOT_PASSWORD ./release.sh` (за потреби задати змінні в `env:` кроку). Я цей workflow не змінював: це збірка образу Radxa, який запускається вручну/на реліз.
- **UNVERIFIED:** реальна збірка образу з цими змінними не запускалась.
