"""Small bounded HTTPS transport. No redirects with bearer credentials."""

from __future__ import annotations

import json
from urllib import error, parse, request

from .base import AIError

MAX_BODY = 4 * 1024 * 1024


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def response_error(data: dict, status: int | None = None) -> AIError:
    detail = (data.get("error") or {}) if isinstance(data, dict) else {}
    code = detail.get("code", "") if isinstance(detail, dict) else detail
    if not isinstance(code, str):
        code = ""
    # Only known codes enter diagnostics; a server message may repeat private input.
    known = {
        "subscription_sharing_usage_limit_exceeded": "ChatGPT plan usage is limited. Check ChatGPT Settings > Usage, then retry from AI settings.",
        "subscription_sharing_user_not_eligible": "ChatGPT plan usage is unavailable for this account or workspace.",
        "subscription_sharing_usage_unavailable": "ChatGPT cannot check plan usage right now. Try again later.",
        "subscription_sharing_user_unavailable": "ChatGPT account information is temporarily unavailable.",
        "subscription_sharing_unsupported_capability": "The selected model or request capability is not supported by ChatGPT plan usage.",
        "subscription_sharing_route_not_supported": "This request route is not available for ChatGPT plan usage.",
        "subscription_sharing_invalid_user": "The ChatGPT account could not be verified. Reconnect in AI settings.",
        "chatpass_v2_scope_not_authorized": "This ChatGPT connection does not authorize plan usage. Reconnect in AI settings.",
        "chatpass_v2_invalid_authorization_context": "The ChatGPT authorization context is invalid. Reconnect in AI settings.",
    }
    terminal_refresh = {
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_invalid",
        "refresh_token_reused",
    }
    if code in terminal_refresh:
        return AIError(
            "The ChatGPT session expired or was revoked. Sign in again.",
            status=status,
            code=code,
            blocked=True,
        )
    if code == "invalid_client":
        return AIError(
            "ChatGPT rejected the client registration. Check the sign-in configuration.",
            status=status,
            code=code,
            blocked=True,
        )
    if code in known:
        temporary = code in {
            "subscription_sharing_usage_unavailable",
            "subscription_sharing_user_unavailable",
        }
        return AIError(known[code], status=status, code=code, retryable=temporary, blocked=not temporary)
    messages = {
        400: "The AI service rejected the request format or selected model.",
        401: "AI authentication was rejected. Reconnect or check your API key in settings.",
        403: "The account, workspace or region does not permit this request.",
        404: "The selected AI model or endpoint was not found.",
        429: "The AI service's usage limit was reached. Retry later from AI settings.",
    }
    return AIError(
        messages.get(status, "The AI service is temporarily unavailable."),
        status=status,
        code="http_error",
        retryable=bool(status and status >= 500),
        blocked=status in (401, 403, 429),
    )


def open_response(url: str, *, data=None, headers=None, timeout=30, form=False):
    headers = {"Accept": "application/json", "User-Agent": "WhispersInTheCourt/0.7.5", **(headers or {})}
    body = None
    if data is not None:
        body = (parse.urlencode(data) if form else json.dumps(data)).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = request.Request(url, data=body, headers=headers)
    try:
        return request.build_opener(NoRedirect()).open(req, timeout=timeout)
    except error.HTTPError as exc:
        try:
            body = json.loads(exc.read(MAX_BODY))
        except (ValueError, OSError):
            body = {}
        finally:
            exc.close()
        raise response_error(body if isinstance(body, dict) else {}, exc.code) from None
    except (error.URLError, TimeoutError, OSError):
        raise AIError(
            "Cannot reach the AI service. Check the connection and try again.", retryable=True
        ) from None


def request_json(url: str, **kwargs) -> dict:
    try:
        with open_response(url, **kwargs) as response:
            body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ValueError()
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError):
        raise AIError("The AI service returned an invalid response.", code="invalid_response") from None
    except (OSError, TimeoutError):
        raise AIError("The AI connection ended before its response completed.", retryable=True) from None
