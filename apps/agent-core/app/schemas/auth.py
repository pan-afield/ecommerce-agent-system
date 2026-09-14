from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

LoginEmail = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=255,
    ),
]

LoginPassword = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=256,
    ),
]


class AuthRequest(BaseModel):
    email: LoginEmail
    password: LoginPassword


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


RefreshTokenValue = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=512,
    ),
]


class RefreshRequest(BaseModel):
    refresh_token: RefreshTokenValue


class CurrentUserResponse(BaseModel):
    id: str
    email: str
    role: Literal["CUSTOMER", "SUPPORT", "ADMIN"]
