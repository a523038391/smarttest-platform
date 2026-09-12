from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response, status

from .auth_domain import (
    UserRecord, hash_password, new_session_token, session_token_digest,
    verify_password,
)
from .auth_repository import AuthRepositoryLike, InvalidCredentials
from .auth_throttle import LoginThrottle
from .auth_schemas import (
    AuthStatusResponse, LoginRequest, SetupRequest, UserResponse,
)
from .config import Settings


SESSION_COOKIE_NAME = "smarttest_session"
auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def get_auth_repository(request: Request) -> AuthRepositoryLike:
    return request.app.state.auth_repository


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_login_throttle(request: Request) -> LoginThrottle:
    return request.app.state.login_throttle


def _resolve_user(request: Request, repository: AuthRepositoryLike) -> UserRecord:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise InvalidCredentials("authentication required")
    user = repository.resolve_session(session_token_digest(token))
    if user is None:
        raise InvalidCredentials("authentication required")
    return user


def _set_session(
    response: Response, user: UserRecord, repository: AuthRepositoryLike,
    settings: Settings,
) -> None:
    token = new_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.session_ttl_seconds
    )
    repository.create_session(user.id, session_token_digest(token), expires_at)
    response.set_cookie(
        SESSION_COOKIE_NAME, token, max_age=settings.session_ttl_seconds,
        httponly=True, secure=settings.session_cookie_secure, samesite="lax", path="/",
    )


@auth_router.get("/status", response_model=AuthStatusResponse)
def auth_status(
    request: Request,
    repository: AuthRepositoryLike = Depends(get_auth_repository),
    settings: Settings = Depends(get_settings),
) -> AuthStatusResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = repository.resolve_session(session_token_digest(token)) if token else None
    return AuthStatusResponse(
        setup_required=not repository.is_initialized(),
        auth_required=settings.auth_required,
        authenticated=user is not None,
        user=UserResponse.from_record(user) if user is not None else None,
    )


@auth_router.post(
    "/setup", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
def setup(
    body: SetupRequest,
    response: Response,
    repository: AuthRepositoryLike = Depends(get_auth_repository),
    settings: Settings = Depends(get_settings),
) -> UserResponse:
    user = repository.initialize_admin(
        body.username, body.display_name, hash_password(body.password)
    )
    _set_session(response, user, repository, settings)
    return UserResponse.from_record(user)


@auth_router.post("/login", response_model=UserResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    repository: AuthRepositoryLike = Depends(get_auth_repository),
    settings: Settings = Depends(get_settings),
    throttle: LoginThrottle = Depends(get_login_throttle),
) -> UserResponse:
    client_host = request.client.host if request.client is not None else "unknown"
    throttle_key = f"{client_host}:{body.username}"
    with throttle.attempt(throttle_key):
        user = repository.find_user(body.username)
        if user is None:
            hash_password(body.password)
            throttle.record_failure(throttle_key)
            raise InvalidCredentials("invalid username or password")
        password_valid = verify_password(body.password, user.password)
        if not user.is_active or not password_valid:
            throttle.record_failure(throttle_key)
            raise InvalidCredentials("invalid username or password")
        throttle.record_success(throttle_key)
    _set_session(response, user, repository, settings)
    return UserResponse.from_record(user)


@auth_router.get("/me", response_model=UserResponse)
def me(
    request: Request,
    repository: AuthRepositoryLike = Depends(get_auth_repository),
) -> UserResponse:
    return UserResponse.from_record(_resolve_user(request, repository))


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    repository: AuthRepositoryLike = Depends(get_auth_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        repository.revoke_session(session_token_digest(token))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE_NAME, path="/", httponly=True,
        secure=settings.session_cookie_secure, samesite="lax",
    )
    return response