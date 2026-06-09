# SKUD API

Новый API-слой делает систему независимой от Telegram. Боты, веб-сканер и будущая админ-панель могут работать как клиенты одного сервера.

## Запуск

```bash
pip install -r requirements.txt
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000
```

Документация OpenAPI:

- `http://localhost:8000/docs`
- `http://localhost:8000/redoc`

Переменные окружения:

```env
SKUD_API_DB_PATH=skud_api.db
SKUD_STORAGE_DIR=storage
SKUD_BOOTSTRAP_TOKEN=bootstrap-change-me
SKUD_QR_SECRET=change-me-in-production
SKUD_FACE_THRESHOLD=0.55
SKUD_UNLOCK_SECONDS=5
```

В реальной установке нужно заменить `SKUD_BOOTSTRAP_TOKEN` и `SKUD_QR_SECRET`.

## Общая модель

В системе есть:

- компания;
- сотрудники;
- помещения;
- правила доступа сотрудника в помещение;
- сканеры входа/выхода;
- QR-пропуска;
- фотографии и face embeddings;
- события прохода;
- текущая занятость помещений и статистика.

Авторизация:

- `X-API-Key` - админский ключ компании;
- `X-Scanner-Token` - токен конкретного сканера.

Базовая настройка с нуля:

1. Создать компанию через bootstrap endpoint.
2. Создать помещения.
3. Создать сотрудников.
4. Добавить сотрудникам правила доступа.
5. Зарегистрировать сканеры.
6. Загрузить фотографии лиц и/или выпустить QR-пропуска.
7. Сканеры вызывают `/api/v1/scanner/verify` и получают `granted` или `denied`.

## Bootstrap компании

```bash
curl -X POST http://localhost:8000/api/v1/setup/company \
  -H "Content-Type: application/json" \
  -H "X-Bootstrap-Token: bootstrap-change-me" \
  -d '{"name":"ACME Office","slug":"acme"}'
```

Ответ содержит `admin_api_key`. Его нужно сохранить: повторно получить этот ключ нельзя.

## Сотрудники

Создать сотрудника:

```bash
curl -X POST http://localhost:8000/api/v1/employees \
  -H "X-API-Key: skud_admin_..." \
  -H "Content-Type: application/json" \
  -d '{"full_name":"Иванов Иван","external_id":"EMP-001","position":"Engineer","email":"ivanov@example.com"}'
```

Изменить сотрудника:

```bash
curl -X PATCH http://localhost:8000/api/v1/employees/1 \
  -H "X-API-Key: skud_admin_..." \
  -H "Content-Type: application/json" \
  -d '{"status":"suspended"}'
```

Загрузить фото для дообучения распознавания:

```bash
curl -X POST http://localhost:8000/api/v1/employees/1/face-photos \
  -H "X-API-Key: skud_admin_..." \
  -F "file=@face.jpg"
```

API сохраняет фото и, если доступен `face_recognition`, сразу строит embedding. Профиль лица пополняется инкрементально без ручного пересоздания `model.pkl`.

Выпустить QR-пропуск:

```bash
curl -X POST http://localhost:8000/api/v1/employees/1/qr-passes \
  -H "X-API-Key: skud_admin_..." \
  -H "Content-Type: application/json" \
  -d '{"ttl_hours":12}'
```

Ответ содержит `payload` для QR и путь к PNG-файлу.

## Помещения

Создать помещение:

```bash
curl -X POST http://localhost:8000/api/v1/rooms \
  -H "X-API-Key: skud_admin_..." \
  -H "Content-Type: application/json" \
  -d '{"name":"Переговорная 1","code":"meeting-1","capacity":8}'
```

Изменить помещение:

```bash
curl -X PATCH http://localhost:8000/api/v1/rooms/1 \
  -H "X-API-Key: skud_admin_..." \
  -H "Content-Type: application/json" \
  -d '{"capacity":10,"status":"active"}'
```

## Правила доступа

Дать сотруднику доступ в помещение по QR и лицу:

```bash
curl -X POST http://localhost:8000/api/v1/access-rules \
  -H "X-API-Key: skud_admin_..." \
  -H "Content-Type: application/json" \
  -d '{"employee_id":1,"room_id":1,"allowed_methods":["qr","face"],"is_active":true}'
```

Ограничить метод только лицом:

```bash
curl -X PATCH http://localhost:8000/api/v1/access-rules/1 \
  -H "X-API-Key: skud_admin_..." \
  -H "Content-Type: application/json" \
  -d '{"allowed_methods":["face"]}'
```

Поля `valid_from`, `valid_until` уже проверяются. Поле `schedule` хранится в базе для следующего этапа, где можно добавить проверку рабочих дней и часов.

## Сканеры

Создать сканер входа:

```bash
curl -X POST http://localhost:8000/api/v1/scanners \
  -H "X-API-Key: skud_admin_..." \
  -H "Content-Type: application/json" \
  -d '{"name":"Main door QR+Face","room_id":1,"direction":"entry","allowed_methods":["qr","face"]}'
```

Ответ содержит `scanner_token`. Его нужно записать в конфигурацию устройства или веб-сканера.

Создать сканер выхода:

```bash
curl -X POST http://localhost:8000/api/v1/scanners \
  -H "X-API-Key: skud_admin_..." \
  -H "Content-Type: application/json" \
  -d '{"name":"Main door exit","room_id":1,"direction":"exit","allowed_methods":["qr","face"]}'
```

## Проверка прохода сканером

Проверка QR:

```bash
curl -X POST http://localhost:8000/api/v1/scanner/verify \
  -H "X-Scanner-Token: skud_scanner_..." \
  -H "Content-Type: application/json" \
  -d '{"method":"qr","qr_payload":"skud1..."}'
```

Проверка лица:

```bash
curl -X POST http://localhost:8000/api/v1/scanner/verify \
  -H "X-Scanner-Token: skud_scanner_..." \
  -H "Content-Type: application/json" \
  -d '{"method":"face","face_image_base64":"/9j/4AAQSkZJRg..."}'
```

Успешный ответ:

```json
{
  "decision": "granted",
  "reason": "access_granted",
  "event_id": 42,
  "unlock_seconds": 5,
  "employee_id": 1,
  "room_id": 1,
  "scanner_id": 1
}
```

При отказе `decision` будет `denied`, а `reason` объяснит причину: нет правила доступа, метод запрещен, QR истек, лицо не распознано и т.д.

## Логи и статистика

Все события прохода:

```bash
curl http://localhost:8000/api/v1/access-events \
  -H "X-API-Key: skud_admin_..."
```

Логи сотрудника:

```bash
curl http://localhost:8000/api/v1/employees/1/access-events \
  -H "X-API-Key: skud_admin_..."
```

Логи помещения:

```bash
curl http://localhost:8000/api/v1/rooms/1/access-events \
  -H "X-API-Key: skud_admin_..."
```

Кто сейчас в помещениях:

```bash
curl http://localhost:8000/api/v1/stats/occupancy \
  -H "X-API-Key: skud_admin_..."
```

Проходимость по дням:

```bash
curl http://localhost:8000/api/v1/stats/throughput \
  -H "X-API-Key: skud_admin_..."
```

Время в офисе:

```bash
curl http://localhost:8000/api/v1/stats/office-time \
  -H "X-API-Key: skud_admin_..."
```

Время считается по парам `entry`/`exit`. Если человек вошел и еще не вышел, API считает время до текущего момента.

## Что важно для веб-приложения

- веб-админка использует `X-API-Key`;
- веб-сканер использует `X-Scanner-Token`;
- QR можно распознавать в браузере и отправлять строку payload на сервер;
- лицо можно фотографировать через браузер, отправлять base64 и получать решение;
- статистика и журналы уже доступны отдельными endpoint-ами.

## Обновления: сотрудники, ошибки и QR fallback

`external_id` сотрудника является строкой. Его можно задавать как `EMP-001`, `ivanov`, `HR-A-7` или в любом другом корпоративном формате. Числовым остается только внутренний `id` записи, который используется в URL вида `/api/v1/employees/{employee_id}`.

Удалить сотрудника:

```bash
curl -X DELETE http://localhost:8000/api/v1/employees/1 \
  -H "X-API-Key: skud_admin_..."
```

Ответ:

```json
{
  "status": "deleted",
  "employee_id": 1
}
```

При удалении сотрудника удаляются его правила доступа, фото и учетные данные. История проходов остается в журнале, но ссылка на удаленного сотрудника очищается.

Обновить логин и пароль сотрудника:

```bash
curl -X POST http://localhost:8000/api/v1/employees/1/credentials \
  -H "X-API-Key: skud_admin_..." \
  -H "Content-Type: application/json" \
  -d '{"login":"ivanov","password":"new-secret"}'
```

Сервер теперь возвращает понятные `409 Conflict` для конфликтов уникальности, например:

```json
{ "detail": "Employee with this external_id already exists" }
```

Для браузеров без `BarcodeDetector` добавлен серверный endpoint декодирования QR из кадра камеры:

```bash
curl -X POST http://localhost:8000/api/v1/scanner/decode-qr \
  -H "X-Scanner-Token: skud_scanner_..." \
  -H "Content-Type: application/json" \
  -d '{"image_base64":"data:image/jpeg;base64,..."}'
```

Ответ:

```json
{ "qr_payload": "skud1..." }
```

Если серверный QR decoder недоступен, API вернет `503` с понятной причиной. Для этого endpoint нужны `Pillow`, `pyzbar` и системная библиотека `libzbar`; Dockerfile уже рассчитан на такой сценарий.
