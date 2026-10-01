# Тести для `gs/*.sh` без заліза

Мета: перед рефакторингом (`docs/ROADMAP-EXECUTION.md`, віхи M2–M3) зафіксувати поведінку **незміненого** коду і потім
доводити, що рефакторинг не змінив вихід Radxa.

## Як це працює
- `tests/lib/sandbox.sh` копіює скрипт, переписує його абсолютні шляхи (`/etc`, `/boot`, `/media`, `/config`, `/sys`) у тимчасовий
  корінь і підміняє небезпечні команди (`chroot`, `mount`, `reboot`, `systemctl`, `nmcli`, `sleep`) шимами, що лише логують виклик.
- Сценарій у `tests/cases/<набір>/<ім'я>.sh` готує стан пісочниці. Вихід (stdout, виклики, змінені файли) нормалізується
  (тимчасовий шлях → `<ROOT>`) і порівнюється з `tests/golden/<набір>/<ім'я>.out`.
- `invocation=sourced` (типово) відтворює `gs.sh:11`/`gs-init.sh:186`, `invocation=standalone` відтворює `button.sh:138`.

## Команди
```bash
tests/run.sh                         # порівняти з golden
tests/run.sh applyconf/default       # один сценарій
tests/run.sh --update [сценарій]     # оновити golden ЛИШЕ при задуманій зміні поведінки
tests/shellcheck-ratchet.sh          # у gs/ і build/ кількість зауважень shellcheck може лише зменшуватись
tests/shellcheck-ratchet.sh --update # зафіксувати покращення
```

## Правила
- Golden «до» знято з коду без жодних змін. Зміна golden-файлу в PR = свідома зміна поведінки, пояснюйте її в описі.
- **Мутаційні перевірки робіть на копії репозиторію, не на місці.**
- Покрито (24 сценарії): `gs/gs-applyconf.sh` (13), `gs/fan.sh` (8), `gs/otg-gadget.sh` (3). Лишаються: GPIO (`button.sh`, `stream.sh`), `gs-init.sh`, `gs.sh`, правила udev.
- Для демонів (`fan.sh`) шим `sleep` завершує скрипт після `sleep_limit` викликів (`exit=143` у golden).
- `otg-gadget.sh` перевіряє `[ -b /dev/mmcblk1p4 ]`: на машині з eMMC/SD `mmcblk1` golden може відрізнятись (у CI й у типовому контейнері таких пристроїв немає).
- Сценарій `otg/delete_existing_controlflow` перевіряє лише потік керування: дерево фейкове, не справжній configfs.
