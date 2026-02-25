# 🏓 SotoPong

Рейтинговая система для настольного тенниса в офисе.  
**FastAPI + PostgreSQL + React** — всё в одном, без лишних зависимостей.

---

## Быстрый старт

### 1. Установи Python 3.9+

Скачай с [python.org](https://python.org) если не установлен.

### 2. Переменные окружения

Обязательно:

- `DATABASE_URL` — строка подключения к PostgreSQL, например: `postgresql://user:pass@host:5432/dbname`

Опционально:

- `ADMIN_TOKEN` — секрет для админ-импорта SQLite (см. ниже)

### 3. Установи зависимости

```bash
pip install -r requirements.txt
```

### 4. Запусти сервер

```bash
python server.py
```

Открой в браузере: **http://localhost:8000**

---

## Как расшарить коллегам (локальная сеть)

Если все в одной Wi-Fi сети (офис), коллегам не нужно ничего устанавливать.

1. Узнай свой локальный IP-адрес:
   - **Windows:** `ipconfig` в cmd → IPv4 Address
   - **Mac/Linux:** `ifconfig` или `ip addr` → inet

2. Запусти сервер (он уже слушает `0.0.0.0`):

   ```bash
   python server.py
   ```

3. Скинь коллегам ссылку: `http://ТВО_IP:8000`
   - Например: `http://192.168.1.42:8000`

Все данные хранятся в PostgreSQL (переменная `DATABASE_URL`).

---

## Деплой в интернет (чтобы работало не только в офисе)

### Вариант A — Railway (бесплатно, 5 минут)

1. Зарегистрируйся на [railway.app](https://railway.app)
2. Создай новый проект → Deploy from GitHub
3. Залей папку `sotopong` в GitHub репозиторий
4. Railway автоматически запустит сервер (использует `DATABASE_URL` и `ADMIN_TOKEN` из Variables)
5. Получишь ссылку вида `sotopong.up.railway.app`

### Вариант B — VPS (любой хостинг с Python)

```bash
# На сервере:
git clone ...  # или залей файлы через FTP
cd sotopong
pip install -r requirements.txt
python server.py
```

### Вариант C — Docker Compose (локально или на VPS)

```bash
# собрать и запустить
docker compose up -d --build

# логи
docker compose logs -f

# остановить
docker compose down
```

По умолчанию сервис будет доступен на `http://localhost:8000`. База (`sotopong.db`) хранится в Docker volume `sotopong-data`.

Или через systemd для автозапуска:

```ini
# /etc/systemd/system/sotopong.service
[Unit]
Description=SotoPong
After=network.target

[Service]
WorkingDirectory=/path/to/sotopong
ExecStart=/usr/bin/python3 server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable sotopong
systemctl start sotopong
```

---

## Структура проекта

```
sotopong/
├── server.py          # FastAPI бэкенд
├── requirements.txt   # Зависимости Python
├── sotopong.db        # Legacy SQLite база (для импорта, не используется на Postgres)
├── README.md
└── static/
    └── index.html     # Фронтенд (React)
```

## API endpoints

| Метод  | Путь                | Описание        |
| ------ | ------------------- | --------------- |
| GET    | `/api/players`      | Список игроков  |
| POST   | `/api/players`      | Добавить игрока |
| DELETE | `/api/players/{id}` | Удалить игрока  |
| GET    | `/api/matches`      | История матчей  |
| POST   | `/api/matches`      | Записать матч   |
| DELETE | `/api/matches/{id}` | Удалить матч    |

### Admin: импорт SQLite → Postgres

Только с `ADMIN_TOKEN` в Variables.

```
curl -X POST \
  -F "file=@/path/to/sotopong.db" \
  "https://<host>/admin/import_sqlite?token=<ADMIN_TOKEN>"
```

Импортирует таблицы `players` и `matches` в PostgreSQL и обновит последовательности.
