from __future__ import annotations

import datetime
import jwt
import logging
import typing

from . import rpc_utils
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.interceptor import MetadataInterceptor
from connectrpc.request import RequestContext


_logger = logging.getLogger(__name__)

# Utilities for account

_CONTEXT_ACCOUNT_ID_NAME = "_account_id"
_CONTEXT_API_KEY_NAME = "_api_key"


def _set_account_id(ctx: RequestContext, account_id: str) -> None:
    setattr(ctx, _CONTEXT_ACCOUNT_ID_NAME, account_id)

def get_account_id(ctx: RequestContext) -> str:
    if hasattr(ctx, _CONTEXT_ACCOUNT_ID_NAME):
        account_id = getattr(ctx, _CONTEXT_ACCOUNT_ID_NAME)
    else:
        account_id = None
    if not account_id:
        raise ConnectError(code=Code.UNAUTHENTICATED, message="Missing account_id in context")
    return account_id


# Utilities for access and refresh token

ApiKeyLookupCallback = typing.Callable[[str], typing.Awaitable[str]]

class ServerAuthInterceptor(MetadataInterceptor):
    def __init__(
            self,
            auth_reader: ServerAuthReader,
            api_key_lookup: ApiKeyLookupCallback,
            token_refresh_method: str,
            api_key_methods: list[str] | None = None,
            whitelist_methods: list[str] | None = None,
        ) -> None:
        self._auth_reader = auth_reader
        self._token_refresh_method = token_refresh_method
        self._api_key_methods = set(api_key_methods or [])
        self._api_key_lookup = api_key_lookup
        self._whitelist_methods = set(whitelist_methods or [])

    async def on_start(self, ctx: RequestContext) -> None:
        method = f"{ctx.method.service_name}/{ctx.method.name}"
        if method == self._token_refresh_method:
            self._on_token_refresh_method(ctx)
        elif method in self._whitelist_methods:
            self._on_whitelist_method(ctx)
        elif method in self._api_key_methods:
            await self._on_api_key_method(ctx)
        else:
            self._on_regular_method(ctx)

    def _on_token_refresh_method(self, ctx: RequestContext) -> None:
        account_id = self._auth_reader.read_refresh_token(ctx)
        _set_account_id(ctx, account_id)

    def _on_whitelist_method(self, ctx: RequestContext) -> None:
        method = f"{ctx.method.service_name}/{ctx.method.name}"
        _logger.info(f"Skipping auth check for whitelisted method: {method}")

    async def _on_api_key_method(self, ctx: RequestContext) -> None:
        api_key = self._read_api_key(ctx)
        if api_key:
            account_id = await self._api_key_lookup(api_key)
            _set_account_id(ctx, account_id)
        else:
            self._on_regular_method(ctx)

    def _on_regular_method(self, ctx: RequestContext) -> None:
        account_id = self._auth_reader.read_access_token(ctx)
        _set_account_id(ctx, account_id)

    def _read_api_key(self, ctx: RequestContext) -> str | None:
        auth_header = ctx.request_headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[len("Bearer "):]
        return None


class ServerAuthAccessor:
    _ACCESS_TOKEN_COOKIE_NAME = "access_token"
    _REFRESH_TOKEN_COOKIE_NAME = "refresh_token"

    def __init__(self, secret: str, algorithm: str) -> None:
        self._secret = secret
        self._algorithm = algorithm


class ServerAuthWriter(ServerAuthAccessor):
    def __init__(
            self,
            secret: str,
            algorithm: str,
            access_token_expiration_seconds: int,
            refresh_token_expiration_seconds: int,
            refresh_token_path: str,
        ) -> None:
        super().__init__(secret, algorithm)
        self._access_token_expiration_seconds = access_token_expiration_seconds
        self._refresh_token_expiration_seconds = refresh_token_expiration_seconds
        self._refresh_token_path = refresh_token_path
        
    def grant(self, ctx: RequestContext, user_id: str) -> None:
        rpc_utils.set_cookie(
            ctx,
            name=self._ACCESS_TOKEN_COOKIE_NAME,
            value=self._create_access_token(user_id),
            path="/",
            httponly=True,
            secure=True,
            samesite="Strict",
            max_age=self._access_token_expiration_seconds,
        )
        rpc_utils.set_cookie(
            ctx,
            name=self._REFRESH_TOKEN_COOKIE_NAME,
            value=self._create_refresh_token(user_id),
            path=self._refresh_token_path,
            httponly=True,
            secure=True,
            samesite="Strict",
            max_age=self._refresh_token_expiration_seconds,
        )

    def reset(self, ctx: RequestContext) -> None:
        rpc_utils.set_cookie(
            ctx,
            name=self._ACCESS_TOKEN_COOKIE_NAME,
            value="",
            path="/",
            httponly=True,
            secure=True,
            samesite="Strict",
            max_age=0,
        )
        rpc_utils.set_cookie(
            ctx,
            name=self._REFRESH_TOKEN_COOKIE_NAME,
            value="",
            path=self._refresh_token_path,
            httponly=True,
            secure=True,
            samesite="Strict",
            max_age=0,
        )

    def _create_access_token(self, account_id: str, extra_claims: dict | None = None) -> str:
        payload = {
            "sub": account_id,  # Subject (User ID)
            "type": "access",
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=self._access_token_expiration_seconds),
            "iat": datetime.datetime.now(datetime.timezone.utc),
        }
        if extra_claims:
            payload.update(extra_claims)
            
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)


    def _create_refresh_token(self, account_id: str) -> str:
        payload = {
            "sub": account_id,
            "type": "refresh",
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=self._refresh_token_expiration_seconds),
            "iat": datetime.datetime.now(datetime.timezone.utc),
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)


class ServerAuthReader(ServerAuthAccessor):
    def read_access_token(self, ctx: RequestContext) -> str:
        token = rpc_utils.get_cookie(ctx, self._ACCESS_TOKEN_COOKIE_NAME)
        if not token:
            raise ConnectError(code=Code.UNAUTHENTICATED, message="Access token missing")

        try:
            account_id = self._validate_access_token(token)
        except Exception as ex:
            raise ConnectError(code=Code.UNAUTHENTICATED, message=str(ex))

        return account_id

    def read_refresh_token(self, ctx: RequestContext) -> str:
        token = rpc_utils.get_cookie(ctx, self._REFRESH_TOKEN_COOKIE_NAME)
        if not token:
            raise ConnectError(code=Code.UNAUTHENTICATED, message="Refresh token missing")

        try:
            account_id = self._validate_refresh_token(token)
        except Exception as ex:
            raise ConnectError(code=Code.UNAUTHENTICATED, message=str(ex))

        return account_id

    def _validate_access_token(self, token: str) -> str:
        return self._validate_token(token, expected_type="access")

    def _validate_refresh_token(self, token: str) -> str:
        return self._validate_token(token, expected_type="refresh")

    def _validate_token(self, token: str, expected_type: str) -> str:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
            if payload.get("type") != expected_type:
                raise ConnectError(code=Code.UNAUTHENTICATED, message=f"Invalid {expected_type} token type")
            sub = payload.get("sub")
            if not sub:
                raise ConnectError(code=Code.UNAUTHENTICATED, message=f"{expected_type} token missing subject")
            return sub
        except jwt.ExpiredSignatureError:
            raise ConnectError(code=Code.UNAUTHENTICATED, message=f"{expected_type.capitalize()} token has expired")
        except jwt.InvalidTokenError as e:
            raise ConnectError(code=Code.UNAUTHENTICATED, message=f"Invalid {expected_type} token")


# Utilities for API key

class ClientApiKeyInterceptor(MetadataInterceptor):
    def __init__(self, token: str):
        self.token = token

    async def on_start(self, ctx: RequestContext):
        # Mutate the request headers in place before the call goes out
        ctx.request_headers["authorization"] = f"Bearer {self.token}"

