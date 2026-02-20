# sotomat

## CI / автосборка

В репо добавлен GitHub Actions workflow `.github/workflows/ci.yml`:

- триггеры: push / PR в `main` или `master`
- шаги: checkout → Python 3.11 + cache pip → `pip install -r sotopong/requirements.txt` → быстрый sanity-check (`python -m compileall`) → сборка Docker-образа `sotopong:ci`

### Автодеплой (пример с Railway)

1. Подключите репозиторий к Railway.
2. В настройках Railway включите автодеплой по push/PR в нужную ветку.
3. Если нужен образ из GitHub Actions, добавьте шаг `docker build`/`docker push` в workflow и подключите регистр (GHCR или Docker Hub) через секреты (`CR_PAT`, `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`).

### Автодеплой на свой сервер (GitHub Actions → SSH)

Workflow `.github/workflows/deploy.yml` делает:

- собирает и пушит образ в GHCR (`ghcr.io/<repo>:latest`);
- по SSH заходит на сервер и применяет `docker-compose.prod.yml` (образ + volume для БД).

Необходимые секреты в репозитории GitHub:

- `SSH_HOST` — хост сервера
- `SSH_USER` — пользователь
- `SSH_PORT` — порт SSH (опционально, по умолчанию 22)
- `SSH_KEY` — приватный ключ (PEM) для подключения

Как подготовить сервер (один раз):

1. Установить Docker + Docker Compose.
2. Создать пользователя, выдать ему доступ к docker (например, добавить в группу docker).
3. Добавить публичный ключ (парный `SSH_KEY`) в `~/.ssh/authorized_keys` этого пользователя.
4. Открыть порт 8000 (или настроить reverse-proxy на 80/443).
