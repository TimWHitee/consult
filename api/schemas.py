from typing import Any, Literal

from pydantic import BaseModel, Field


AccessMethod = Literal["qr", "face"]
Direction = Literal["entry", "exit"]


class CompanySetupIn(BaseModel):
    name: str
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
    admin_key_name: str = "Default admin key"


class EmployeeIn(BaseModel):
    full_name: str
    external_id: str | None = None
    position: str | None = None
    email: str | None = None
    phone: str | None = None
    status: Literal["active", "suspended"] = "active"


class EmployeePatch(BaseModel):
    full_name: str | None = None
    external_id: str | None = None
    position: str | None = None
    email: str | None = None
    phone: str | None = None
    status: Literal["active", "suspended"] | None = None


class RoomIn(BaseModel):
    name: str
    code: str
    description: str | None = None
    capacity: int | None = None
    status: Literal["active", "inactive"] = "active"


class RoomPatch(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    capacity: int | None = None
    status: Literal["active", "inactive"] | None = None


class AccessRuleIn(BaseModel):
    employee_id: int
    room_id: int
    allowed_methods: list[AccessMethod] = Field(default_factory=lambda: ["qr", "face"])
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
    allowed_methods: list[AccessMethod] = Field(default_factory=lambda: ["qr", "face"])
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


class ScannerVerifyIn(BaseModel):
    method: AccessMethod
    qr_payload: str | None = None
    face_image_base64: str | None = None
    raw_subject: str | None = None
