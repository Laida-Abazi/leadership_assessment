from os import getenv


DEFAULT_FRONTEND_URL = "https://leadership-assessment-front-app.vercel.app"


def get_frontend_base_url() -> str:
    return getenv("FRONTEND_URL", DEFAULT_FRONTEND_URL).rstrip("/")


def build_frontend_verification_url(token: str) -> str:
    return f"{get_frontend_base_url()}/verify/{token}"


def build_frontend_verification_url_template() -> str:
    return f"{get_frontend_base_url()}/verify/{{token}}"
