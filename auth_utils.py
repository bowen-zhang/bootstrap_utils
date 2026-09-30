from connectrpc.interceptor import MetadataInterceptor
from connectrpc.request import RequestContext


class AuthInterceptor(MetadataInterceptor):
    def __init__(self, token: str):
        self.token = token

    async def on_start(self, ctx: RequestContext):
        # Mutate the request headers in place before the call goes out
        ctx.request_headers["authorization"] = f"Bearer {self.token}"
