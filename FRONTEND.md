# SKUD Frontend

## Latest functionality update

- Admin employees table now supports direct edit and delete actions.
- Employee `external_id` is a free-form string, not a numeric-only field.
- During employee edit, login/password are updated only when both fields are filled.
- API conflict errors are surfaced in the UI from JSON `detail`; duplicate employee IDs/logins no longer appear as generic `Internal Server Error`.
- Scanner QR camera no longer depends only on browser `BarcodeDetector`. If the browser does not support it, the scanner sends camera frames to `POST /api/v1/scanner/decode-qr`, then verifies the decoded payload through `POST /api/v1/scanner/verify`.
- Server QR fallback requires backend QR decoder dependencies: `Pillow`, `pyzbar`, and `libzbar`.

Интерфейсы приведены к единой Apple / iOS / macOS inspired glass-дизайн системе:

- Montserrat;
- soft gradient background;
- liquid glass panels;
- `rgba(255,255,255,0.65-0.86)` surfaces;
- `backdrop-filter: blur(...)`;
- radius 18-32px;
- мягкие layered shadows;
- спокойный blue/violet/cyan primary gradient;
- понятные hover/focus/active states;
- адаптивные desktop/tablet/mobile layouts.

## Адреса

После запуска API:

```bash
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000
```

Доступны:

- админ-панель: `http://localhost:8000/admin/`
- кабинет сотрудника: `http://localhost:8000/employee/`
- веб-сканер: `http://localhost:8000/scanner/`
- API docs: `http://localhost:8000/docs`

## Дизайн-система

Использованы ключевые правила из дизайн-файла:

- светлая тема;
- canvas `#f5f5f7`;
- основной текст `#1d1d1f`;
- основной action color `#0071e3`;
- secondary action `#0066cc`;
- radius кнопок `980px`;
- radius панелей и inputs `8px`;
- без теней на панелях и кнопках;
- иерархия через hairline borders и светлые surface shifts;
- крупные centered headings;
- верхняя sticky navigation вместо sidebar;
- системная SF Pro-подобная типографика через `system-ui`.

## Админ-панель

## Employee app

Файлы:

- `employee/index.html`
- `employee/styles.css`
- `employee/app.js`

Функции:

1. Вход сотрудника по `company_slug`, логину и паролю.
2. Собственный bearer token сотрудника без `X-API-Key`.
3. Профиль сотрудника с фото или fallback-avatar.
4. Информация о должности, email, телефоне, статусе и табельном ID.
5. Карточки доступных помещений и методов прохода.
6. Выпуск личного QR-пропуска.
7. Создание гостевого приглашения.
8. Просмотр списка своих гостей.
9. Просмотр собственных логов прохода.
10. Смена пароля.

Используемые API:

- `POST /api/v1/employee/login`
- `GET /api/v1/employee/me`
- `POST /api/v1/employee/qr-passes`
- `POST /api/v1/employee/guest-passes`
- `GET /api/v1/employee/guests`
- `GET /api/v1/employee/access-events`
- `POST /api/v1/employee/change-password`

## Админ-панель

Файлы:

- `admin/index.html`
- `admin/styles.css`
- `admin/app.js`

Функции:

1. Первичная настройка компании через bootstrap token.
2. Подключение к API по `X-API-Key`.
3. Добавление сотрудников.
4. Создание логина/пароля сотрудника при добавлении.
5. Поиск сотрудников.
6. Включение и отключение сотрудников через статус.
7. Загрузка фотографий лица для построения face embedding.
8. Выпуск QR-пропуска для сотрудника.
9. Добавление помещений.
10. Включение и отключение помещений.
11. Создание правил доступа сотрудник-помещение.
12. Настройка методов доступа: `qr`, `face`.
13. Включение и отключение правил доступа.
14. Создание сканеров.
15. Выбор направления сканера: `entry`, `exit`.
16. Получение scanner token после создания сканера.
17. Просмотр журнала проходов.
18. Фильтрация журнала по сотруднику, помещению и решению.
19. Просмотр статистики занятости помещений.
20. Просмотр проходимости по дням.
21. Просмотр базовой статистики времени в офисе.

## Веб-сканер

Файлы:

- `webapp/index.html`
- `webapp/styles.css`
- `webapp/app.js`

Функции:

1. Подключение к API по `X-Scanner-Token`.
2. Сохранение URL API и scanner token в `localStorage`.
3. Реальное сканирование QR с камеры через browser `BarcodeDetector`.
4. Fallback-проверка QR через ввод `skud1...` payload.
5. Проверка лица через камеру браузера.
6. Проверка лица через загрузку изображения.
7. Отправка запроса на `POST /api/v1/scanner/verify`.
8. Отображение результата `granted` или `denied`.
9. Отображение причины решения.
10. Отображение `event_id`, `employee_id`, `room_id`, `scanner_id`, `unlock_seconds`, `confidence`.

## Как проверить

1. В админке создать компанию.
2. Добавить помещение.
3. Добавить сотрудника.
4. Создать правило доступа.
5. Создать сканер и скопировать scanner token.
6. Выпустить QR-пропуск сотруднику.
7. Открыть `/scanner/`.
8. Вставить scanner token.
9. Вставить QR payload.
10. Нажать `Verify QR`.

Если доступ разрешен, веб-сканер покажет:

```json
{
  "decision": "granted",
  "reason": "access_granted"
}
```

## Ограничения

- QR-сканирование работает через `BarcodeDetector`. Если браузер не поддерживает этот API, используется ручной ввод QR payload.
- Face-проверка требует, чтобы backend видел `face_recognition`; иначе сервер вернет `face_engine_unavailable`.
- Scanner token показывается только один раз после создания сканера, потому что сервер хранит только хеш.
