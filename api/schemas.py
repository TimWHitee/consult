from typing import Any, Literal

from pydantic import BaseModel, Field


AccessMethod = Literal["qr", "card", "face"]
Direction = Literal["entry", "exit"]
EmployeeStatus = Literal["active", "suspended", "fired"]
EmployeeRole = Literal["employee", "hr", "security", "pass_office", "cleaner"]
PassStatus = Literal["active", "blocked"]


class CompanySetupIn(BaseModel):
    name: str
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
    admin_key_name: str = "Default admin key"
    owner_full_name: str | None = None
    owner_login: str | None = None
    owner_password: str | None = Field(default=None, min_length=6)
    owner_role: EmployeeRole = "hr"


class EmployeeIn(BaseModel):
    full_name: str
    external_id: str | None = None
    position: str | None = None
    email: str | None = None
    phone: str | None = None
    status: EmployeeStatus = "active"
    role: EmployeeRole = "employee"
    access_level: int = Field(default=1, ge=1, le=3)
    pass_status: PassStatus = "active"
    login: str | None = None
    password: str | None = None


class EmployeePatch(BaseModel):
    full_name: str | None = None
    external_id: str | None = None
    position: str | None = None
    email: str | None = None
    phone: str | None = None
    status: EmployeeStatus | None = None
    role: EmployeeRole | None = None
    access_level: int | None = Field(default=None, ge=1, le=3)
    pass_status: PassStatus | None = None
    pass_block_reason: str | None = None


class RoomIn(BaseModel):
    name: str
    code: str
    description: str | None = None
    capacity: int | None = None
    access_level: int = Field(default=1, ge=1, le=3)
    allowed_methods: list[AccessMethod] = Field(default_factory=lambda: ["qr", "card", "face"])
    biometric_only: bool = False
    status: Literal["active", "inactive"] = "active"


class RoomPatch(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    capacity: int | None = None
    access_level: int | None = Field(default=None, ge=1, le=3)
    allowed_methods: list[AccessMethod] | None = None
    biometric_only: bool | None = None
    status: Literal["active", "inactive"] | None = None


class AccessRuleIn(BaseModel):
    employee_id: int
    room_id: int
    allowed_methods: list[AccessMethod] = Field(default_factory=lambda: ["qr", "card", "face"])
    valid_from: str | None = None
    valid_until: str | None = None
    schedule: dict[str, Any] | None = None
    is_active: bool = True


class AccessRulePatch(BaseModel):
    allowed_methods: list[AccessMethod] | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    schedule: dict[str, Any] | None = None
    is_active: bool | None = None


class ScannerIn(BaseModel):
    name: str
    room_id: int
    direction: Direction = "entry"
    allowed_methods: list[AccessMethod] = Field(default_factory=lambda: ["qr", "card", "face"])
    status: Literal["active", "inactive"] = "active"


class ScannerPatch(BaseModel):
    name: str | None = None
    room_id: int | None = None
    direction: Direction | None = None
    allowed_methods: list[AccessMethod] | None = None
    status: Literal["active", "inactive"] | None = None


class QrPassIn(BaseModel):
    expires_at: str | None = None
    ttl_hours: int = 12


class GuestPassIn(BaseModel):
    host_employee_id: int
    room_id: int
    full_name: str
    document_number: str | None = None
    visit_starts_at: str
    visit_ends_at: str


class EmployeeGuestPassIn(BaseModel):
    room_id: int
    full_name: str
    document_number: str | None = None
    visit_starts_at: str
    visit_ends_at: str


class EmployeeLoginIn(BaseModel):
    company_slug: str
    login: str
    password: str


class EmployeePasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class EmployeeCredentialIn(BaseModel):
    login: str
    password: str = Field(min_length=6)


class ScannerVerifyIn(BaseModel):
    method: AccessMethod
    room_id: int | None = None
    qr_payload: str | None = None
    face_image_base64: str | None = None
    raw_subject: str | None = None


class ScannerQrImageIn(BaseModel):
    image_base64: str


class QrPassRevokeIn(BaseModel):
    reason: str = "revoked"


class EmployeePassStatusIn(BaseModel):
    pass_status: PassStatus
    reason: str | None = None


class RoomAllowlistIn(BaseModel):
    employee_id: int


class RoomLimitOverrideIn(BaseModel):
    limit_value: int = Field(ge=0)
    valid_from: str
    valid_until: str
    reason: str | None = None


class SecuritySettingsIn(BaseModel):
    anti_passback_minutes: int = Field(default=15, ge=0, le=1440)
