from sqlalchemy.orm import Session


def logout_user(db: Session, user_id: int) -> None:
    """
    Perform server-side logout cleanup for a user.

    This app currently uses stateless JWT access tokens, so logout is primarily
    implemented by the client clearing storage and the server clearing auth cookies.
    """
    _ = (db, user_id)
