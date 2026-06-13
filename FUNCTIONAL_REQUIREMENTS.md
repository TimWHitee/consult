# Functional Requirements Coverage

## Access and HR workflows

- Rooms define available physical entry methods: `qr`, `card`, `face`.
- Employee access rules grant access to rooms; room methods decide how entry is allowed.
- Admin panel supports bulk grants: all rooms, all QR rooms, all card rooms and all face rooms.
- HR/security can view any employee profile and permissions through `GET /api/v1/employees/{employee_id}/profile`.
- Employee access can be changed through `POST /api/v1/access-rules`, `PATCH /api/v1/access-rules/{rule_id}` and the admin panel access section.
- Level 1 rooms are available to every active employee automatically, even without a manual access rule.
- Access rule changes are written immediately to `access_change_logs`; view through `GET /api/v1/access-change-logs`.
- Employees receive access change notifications in `notifications`; view through `GET /api/v1/employee/notifications` or `GET /api/v1/employee/me`.
- Cleaner/time-based access is supported through access rule `schedule`: `weekdays`, `start_time`, `end_time`.

## Passes and employee status

- Lost/broken passes can be blocked through `POST /api/v1/employees/{employee_id}/pass-status`.
- Erroneous blocking can be reverted by setting `pass_status` back to `active`.
- Security/pass office can reissue a permanent QR pass through `POST /api/v1/employees/{employee_id}/passes/reissue`.
- Individual QR passes can be revoked through `POST /api/v1/qr-passes/{qr_pass_id}/revoke`.
- When employee status becomes `fired`, employee pass status is set to `blocked`, employee QR passes are revoked, active guest invitations from that employee are revoked, and this is logged.

## Guests

- Employees can create guest QR passes only for rooms where they have active access.
- Guest QR passes expire by `expires_at`; expired passes are denied and can be marked revoked by `POST /api/v1/qr-passes/revoke-expired`.
- Guest QR scans are logged in `access_events` with `subject_type = guest` and `guest_id`.
- Host employee is notified when a guest passes or is denied.
- Pass office/security can reissue a guest QR from an existing invitation through `POST /api/v1/guests/{guest_id}/qr-passes`.

## Level 3 and biometric rooms

- Rooms have `access_level` and `biometric_only` fields.
- Level 3 rooms require allowlist membership and face access; QR is rejected.
- The allowlist is managed through:
  - `GET /api/v1/rooms/{room_id}/level3-allowlist`
  - `POST /api/v1/rooms/{room_id}/level3-allowlist`
  - `DELETE /api/v1/rooms/{room_id}/level3-allowlist/{employee_id}`
- Every denied attempt to a level 3 room creates a security alert in `security_alerts`.
- Security alerts include the attempted employee permissions snapshot; view through `GET /api/v1/security-alerts`.
- Face photo enrollment stores photos and embeddings when the face engine is available; endpoint: `POST /api/v1/employees/{employee_id}/face-photos`.

## Monitoring, limits and logs

- Room capacity is enforced during scanner verification.
- Temporary capacity overrides are managed through `POST /api/v1/rooms/{room_id}/limit-overrides`.
- Current occupancy is available through `GET /api/v1/stats/occupancy`.
- Access logs support filters by employee, room, decision, subject type and date range through `GET /api/v1/access-events`.
- Admin panel includes filters for employee, room, decision and date range.
- Employee attendance report is available through `GET /api/v1/reports/employee-attendance`.
- Room utilization report is available through `GET /api/v1/reports/room-utilization`.
- Admin panel has the `Отчеты` section for attendance, utilization and anti-passback settings.

## US-15 Attendance Analytics

- Every access point trigger is logged in `access_events` with employee, room, time, direction, decision and identification method.
- The employee attendance report contains employee ID, full name, visit day, visited room, first entry time, last exit time and used identification methods.
- The room utilization report contains room ID, room name, day, people count, first entry time and last exit time.
- HR can create a temporary QR code for an employee through `POST /api/v1/employees/{employee_id}/qr-passes` if the card does not work.

## US-21 Anti-Passback

- The system keeps office presence state in `employee_presence`: `in_office` or `out_office`.
- On granted entry, the employee is marked `in_office`; on granted exit, the employee is marked `out_office`.
- QR/card entry is blocked if the employee is already `in_office`.
- QR/card re-entry is blocked until the anti-passback interval elapses after the last exit.
- Default interval is 15 minutes.
- Security can configure the interval through:
  - `GET /api/v1/settings/security`
  - `PATCH /api/v1/settings/security`
- When anti-passback blocks access, security employees receive a notification containing employee ID, name, timestamp and reason.

## Scanner and QR camera

- Scanner verification endpoint: `POST /api/v1/scanner/verify`.
- Scanner can load room methods through `GET /api/v1/scanner/rooms/{room_id}/methods`.
- Scanner card mode sends `method = card` and resolves `raw_subject` by employee `external_id` or internal ID.
- Response includes `decision`, `reason`, `signal_color`, `unlock_seconds` and event metadata.
- UI shows green/red decision state as the required light signal equivalent.
- Camera QR scanning uses native `BarcodeDetector` when available.
- If `BarcodeDetector` is not available, scanner sends frames to `POST /api/v1/scanner/decode-qr`.
- Server QR fallback uses OpenCV `QRCodeDetector`; `pyzbar` remains a secondary fallback.
